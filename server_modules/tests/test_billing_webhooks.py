import asyncio
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

from server_modules import billing_credit_config
from server_modules import billing_service
from server_modules import control_plane_repository

# Every fresh workspace is seeded with a small signup credit floor (see
# control_plane_repository._new_workspace_billing_metadata, CLAUDE.md's
# billing_credit_config.py commentary) — balance assertions below are
# relative to this baseline, not to zero, and the exact USD/credit rate is
# read from the real constants rather than hardcoded, so this file can't
# silently drift out of sync with billing_credit_config.py the way the
# Stripe-era version of this file had (confirmed on main before this file
# was rewritten: it asserted values baked in when both the signup floor
# was $0 and the credit rate was 20000/$, neither still true).
_SIGNUP_FLOOR_USD = billing_credit_config.NEW_ACCOUNT_SIGNUP_CREDIT_USD
_CREDITS_PER_USD = billing_service.HOSTED_SAGE_AI_CREDITS_PER_USD


class BillingWebhookTests(unittest.TestCase):
    def _create_workspace(self, root: Path) -> str:
        with patch.object(control_plane_repository, "LOCAL_IDENTITY_DB_FILE", root / "users.db"), patch.object(
            control_plane_repository,
            "ensure_control_plane_schema",
            new=AsyncMock(return_value=None),
        ):
            bundle = asyncio.run(
                control_plane_repository.create_local_password_account(
                    user_id="user-webhook-1",
                    email="owner@example.com",
                    display_name="Owner Example",
                    password_hash="hash",
                )
            )
            memberships = list((bundle or {}).get("memberships") or [])
            self.assertTrue(memberships)
            return str(memberships[0].get("workspace_id") or "").strip()

    def _sign(self, secret: str, payload: bytes) -> dict[str, str]:
        # Independent re-implementation of Polar's Standard Webhooks signing
        # (webhook-id/webhook-timestamp/webhook-signature, HMAC-SHA256,
        # base64, "v1," prefix) — cross-checked against Polar's own reference
        # `standardwebhooks` library in test_polar_client.py, which is the
        # file that actually proves the algorithm is right. This helper only
        # needs to build a payload our own verifier accepts, matching the
        # production shape.
        webhook_id = "msg_test_1"
        timestamp = str(int(time.time()))
        signed_payload = f"{webhook_id}.{timestamp}.{payload.decode('utf-8')}".encode("utf-8")
        signature = base64.b64encode(hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).digest()).decode("ascii")
        return {
            "webhook-id": webhook_id,
            "webhook-timestamp": timestamp,
            "webhook-signature": f"v1,{signature}",
        }

    def test_subscription_webhook_activates_paid_plan(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            workspace_id = self._create_workspace(root)
            event = {
                "type": "subscription.updated",
                "data": {
                    "id": "sub_pro_123",
                    "customer_id": "cus_pro_123",
                    "customer": {"id": "cus_pro_123", "email": "owner@example.com", "external_id": workspace_id},
                    "status": "active",
                    "currency": "usd",
                    "current_period_start": "2026-03-09T12:00:00Z",
                    "current_period_end": "2026-04-08T12:00:00Z",
                    "cancel_at_period_end": False,
                    "product_id": "prod_pro_123",
                    "recurring_interval": "month",
                    "metadata": {"workspace_id": workspace_id, "plan_id": "pro"},
                },
            }
            payload = json.dumps(event).encode("utf-8")
            secret = "polar_whsec_test_123"
            with patch.dict(
                os.environ,
                {"EMPYRALIS_POLAR_WEBHOOK_SECRET": secret, "EMPYRALIS_POLAR_PRODUCT_IDS": '{"pro":"prod_pro_123"}'},
                clear=False,
            ), patch.object(
                control_plane_repository,
                "LOCAL_IDENTITY_DB_FILE",
                root / "users.db",
            ), patch.object(
                control_plane_repository,
                "ensure_control_plane_schema",
                new=AsyncMock(return_value=None),
            ):
                result = billing_service.handle_polar_webhook(payload, self._sign(secret, payload))
                summary = billing_service.workspace_billing_summary_for_workspace_id(workspace_id)

        self.assertTrue(result["ok"])
        self.assertEqual(summary["subscription"]["plan_id"], "pro")
        self.assertEqual(summary["subscription"]["effective_plan_id"], "pro")
        self.assertEqual(summary["subscription"]["status"], "active")
        # Outcome-honesty fields (2026-08-20): a REAL webhook-driven
        # subscription is what flips is_paid, unlike the metadata-seeded
        # entitlement grant every new workspace starts with.
        self.assertTrue(summary["subscription"]["is_paid"])
        self.assertEqual(summary["subscription"]["display_plan_id"], "pro")
        self.assertEqual(summary["subscription"]["display_label"], "Pro")

    def test_cancellation_webhook_downgrades_workspace_back_to_free(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            workspace_id = self._create_workspace(root)
            activate_event = {
                "type": "subscription.updated",
                "data": {
                    "id": "sub_pro_123",
                    "customer_id": "cus_pro_123",
                    "status": "active",
                    "currency": "usd",
                    "current_period_start": "2026-03-09T12:00:00Z",
                    "current_period_end": "2026-04-08T12:00:00Z",
                    "cancel_at_period_end": False,
                    "product_id": "prod_pro_123",
                    "recurring_interval": "month",
                    "metadata": {"workspace_id": workspace_id, "plan_id": "pro"},
                },
            }
            cancel_event = {
                "type": "subscription.canceled",
                "data": {
                    "id": "sub_pro_123",
                    "customer_id": "cus_pro_123",
                    "status": "canceled",
                    "currency": "usd",
                    "cancel_at_period_end": False,
                    "canceled_at": "2026-04-01T00:00:00Z",
                    "product_id": "prod_pro_123",
                    "recurring_interval": "month",
                    "metadata": {"workspace_id": workspace_id, "plan_id": "pro"},
                },
            }
            secret = "polar_whsec_test_123"
            with patch.dict(
                os.environ,
                {"EMPYRALIS_POLAR_WEBHOOK_SECRET": secret, "EMPYRALIS_POLAR_PRODUCT_IDS": '{"pro":"prod_pro_123"}'},
                clear=False,
            ), patch.object(
                control_plane_repository,
                "LOCAL_IDENTITY_DB_FILE",
                root / "users.db",
            ), patch.object(
                control_plane_repository,
                "ensure_control_plane_schema",
                new=AsyncMock(return_value=None),
            ):
                activate_payload = json.dumps(activate_event).encode("utf-8")
                cancel_payload = json.dumps(cancel_event).encode("utf-8")
                billing_service.handle_polar_webhook(activate_payload, self._sign(secret, activate_payload))
                billing_service.handle_polar_webhook(cancel_payload, self._sign(secret, cancel_payload))
                summary = billing_service.workspace_billing_summary_for_workspace_id(workspace_id)

        self.assertEqual(summary["subscription"]["plan_id"], "pro")
        self.assertEqual(summary["subscription"]["status"], "canceled")
        self.assertEqual(summary["subscription"]["effective_plan_id"], "free")
        # A cancelled subscription is terminal, so is_paid must go back to
        # False too — an owner who cancelled must never keep seeing "Pro".
        self.assertFalse(summary["subscription"]["is_paid"])
        self.assertEqual(summary["subscription"]["display_plan_id"], "free")
        self.assertEqual(summary["subscription"]["display_label"], "Free")

    def test_checkout_updated_fast_path_activates_plan_before_subscription_event(self):
        # Mirrors what Stripe's `checkout.session.completed` used to do:
        # mark the plan active as soon as the checkout succeeds, without
        # waiting for the (slightly slower) subscription.* event.
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            workspace_id = self._create_workspace(root)
            event = {
                "type": "checkout.updated",
                "data": {
                    "id": "checkout_pro_1",
                    "status": "succeeded",
                    "customer_id": "cus_pro_123",
                    "customer_email": "owner@example.com",
                    "external_customer_id": workspace_id,
                    "metadata": {"workspace_id": workspace_id, "plan_id": "pro"},
                },
            }
            payload = json.dumps(event).encode("utf-8")
            secret = "polar_whsec_test_123"
            with patch.dict(os.environ, {"EMPYRALIS_POLAR_WEBHOOK_SECRET": secret}, clear=False), patch.object(
                control_plane_repository, "LOCAL_IDENTITY_DB_FILE", root / "users.db"
            ), patch.object(
                control_plane_repository, "ensure_control_plane_schema", new=AsyncMock(return_value=None)
            ):
                result = billing_service.handle_polar_webhook(payload, self._sign(secret, payload))
                summary = billing_service.workspace_billing_summary_for_workspace_id(workspace_id)

        self.assertTrue(result["ok"])
        self.assertEqual(summary["subscription"]["plan_id"], "pro")
        self.assertEqual(summary["subscription"]["status"], "checkout_completed")

    def test_credit_purchase_webhook_adds_balance_and_records_transaction(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            workspace_id = self._create_workspace(root)
            event = {
                "type": "order.paid",
                "data": {
                    "id": "order_credit_123",
                    "checkout_id": "checkout_credit_123",
                    "customer_id": "cus_credit_123",
                    "customer": {"id": "cus_credit_123", "email": "owner@example.com", "external_id": workspace_id},
                    "currency": "usd",
                    "metadata": {
                        "workspace_id": workspace_id,
                        "purchase_kind": "credits",
                        "amount_usd": "15.00",
                    },
                },
            }
            secret = "polar_whsec_test_123"
            with patch.dict(os.environ, {"EMPYRALIS_POLAR_WEBHOOK_SECRET": secret}, clear=False), patch.object(
                control_plane_repository, "LOCAL_IDENTITY_DB_FILE", root / "users.db"
            ), patch.object(
                control_plane_repository, "ensure_control_plane_schema", new=AsyncMock(return_value=None)
            ):
                payload = json.dumps(event).encode("utf-8")
                result = billing_service.handle_polar_webhook(payload, self._sign(secret, payload))
                balance = billing_service.credit_balance_for_workspace(workspace_id)
                summary = billing_service.workspace_billing_summary_for_workspace_id(workspace_id)

        expected_balance = round(_SIGNUP_FLOOR_USD + 15.0, 6)
        purchase_transactions = [t for t in balance["transactions"] if t["kind"] == "purchase"]

        self.assertTrue(result["ok"])
        self.assertEqual(result["purchase_kind"], "credits")
        self.assertEqual(result["amount_usd"], 15.0)
        self.assertEqual(balance["credit_balance_usd"], expected_balance)
        self.assertEqual(balance["credit_balance_credits"], int(round(expected_balance * _CREDITS_PER_USD)))
        self.assertEqual(len(purchase_transactions), 1)
        self.assertEqual(purchase_transactions[0]["amount_usd"], 15.0)
        self.assertEqual(purchase_transactions[0]["provider"], "polar")
        self.assertEqual(summary["hosted_sage_ai"]["credit_balance_usd"], expected_balance)
        # The real invariant (total = monthly allowance left + credit
        # balance) rather than a hardcoded number, so this doesn't drift the
        # next time the monthly cap or signup floor changes.
        self.assertEqual(
            summary["hosted_sage_ai"]["total_available_usd"],
            round(summary["hosted_sage_ai"]["monthly_remaining_usd"] + expected_balance, 6),
        )

    def test_credit_purchase_webhook_accumulates_multiple_purchases(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            workspace_id = self._create_workspace(root)
            secret = "polar_whsec_test_123"
            with patch.dict(os.environ, {"EMPYRALIS_POLAR_WEBHOOK_SECRET": secret}, clear=False), patch.object(
                control_plane_repository, "LOCAL_IDENTITY_DB_FILE", root / "users.db"
            ), patch.object(
                control_plane_repository, "ensure_control_plane_schema", new=AsyncMock(return_value=None)
            ):
                for amount in [5.0, 10.0]:
                    event = {
                        "type": "order.paid",
                        "data": {
                            "id": f"order_credit_{amount}",
                            "checkout_id": f"checkout_credit_{amount}",
                            "customer_id": "cus_credit_123",
                            "customer": {"id": "cus_credit_123", "external_id": workspace_id},
                            "currency": "usd",
                            "metadata": {
                                "workspace_id": workspace_id,
                                "purchase_kind": "credits",
                                "amount_usd": str(amount),
                            },
                        },
                    }
                    payload = json.dumps(event).encode("utf-8")
                    billing_service.handle_polar_webhook(payload, self._sign(secret, payload))
                balance = billing_service.credit_balance_for_workspace(workspace_id)

        purchase_transactions = [t for t in balance["transactions"] if t["kind"] == "purchase"]
        self.assertEqual(balance["credit_balance_usd"], round(_SIGNUP_FLOOR_USD + 15.0, 6))
        self.assertEqual(len(purchase_transactions), 2)

    def test_order_paid_for_a_plan_purchase_does_not_credit_balance(self):
        # order.paid also fires for a subscription's first invoice
        # (order.subscription_id set, no purchase_kind metadata) — must be a
        # no-op here; subscription.* events own plan activation.
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            workspace_id = self._create_workspace(root)
            event = {
                "type": "order.paid",
                "data": {
                    "id": "order_plan_1",
                    "checkout_id": "checkout_plan_1",
                    "subscription_id": "sub_pro_123",
                    "customer_id": "cus_pro_123",
                    "customer": {"id": "cus_pro_123", "external_id": workspace_id},
                    "currency": "usd",
                    "metadata": {"workspace_id": workspace_id, "plan_id": "pro"},
                },
            }
            secret = "polar_whsec_test_123"
            with patch.dict(os.environ, {"EMPYRALIS_POLAR_WEBHOOK_SECRET": secret}, clear=False), patch.object(
                control_plane_repository, "LOCAL_IDENTITY_DB_FILE", root / "users.db"
            ), patch.object(
                control_plane_repository, "ensure_control_plane_schema", new=AsyncMock(return_value=None)
            ):
                payload = json.dumps(event).encode("utf-8")
                result = billing_service.handle_polar_webhook(payload, self._sign(secret, payload))
                balance = billing_service.credit_balance_for_workspace(workspace_id)

        self.assertTrue(result["ok"])
        self.assertTrue(result.get("ignored"))
        self.assertEqual(balance["credit_balance_usd"], _SIGNUP_FLOOR_USD)

    def test_tampered_webhook_body_is_rejected(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            workspace_id = self._create_workspace(root)
            event = {
                "type": "order.paid",
                "data": {
                    "id": "order_1",
                    "customer": {"external_id": workspace_id},
                    "metadata": {"workspace_id": workspace_id, "purchase_kind": "credits", "amount_usd": "999.00"},
                },
            }
            secret = "polar_whsec_test_123"
            payload = json.dumps(event).encode("utf-8")
            headers = self._sign(secret, payload)
            tampered_payload = json.dumps({**event, "data": {**event["data"], "id": "order_evil"}}).encode("utf-8")
            with patch.dict(os.environ, {"EMPYRALIS_POLAR_WEBHOOK_SECRET": secret}, clear=False), patch.object(
                control_plane_repository, "LOCAL_IDENTITY_DB_FILE", root / "users.db"
            ), patch.object(
                control_plane_repository, "ensure_control_plane_schema", new=AsyncMock(return_value=None)
            ):
                with self.assertRaises(Exception):
                    billing_service.handle_polar_webhook(tampered_payload, headers)
                balance = billing_service.credit_balance_for_workspace(workspace_id)

        # The forged $999 credit must never have landed — balance stays
        # at exactly the signup floor, no more.
        self.assertEqual(balance["credit_balance_usd"], _SIGNUP_FLOOR_USD)


if __name__ == "__main__":
    unittest.main()

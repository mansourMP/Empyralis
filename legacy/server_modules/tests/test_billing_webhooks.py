import asyncio
import hashlib
import hmac
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

from server_modules import billing_service
from server_modules import control_plane_repository


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

    def _sign(self, secret: str, payload: bytes) -> str:
        timestamp = str(int(time.time()))
        digest = hmac.new(
            secret.encode("utf-8"),
            f"{timestamp}.{payload.decode('utf-8')}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return f"t={timestamp},v1={digest}"

    def test_subscription_webhook_activates_paid_plan(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            workspace_id = self._create_workspace(root)
            event = {
                "type": "customer.subscription.updated",
                "data": {
                    "object": {
                        "id": "sub_pro_123",
                        "customer": "cus_pro_123",
                        "status": "active",
                        "currency": "usd",
                        "current_period_start": 1710000000,
                        "current_period_end": 1712592000,
                        "cancel_at_period_end": False,
                        "items": {
                            "data": [
                                {
                                    "price": {
                                        "id": "price_pro_123",
                                        "product": "prod_pro_123",
                                        "currency": "usd",
                                        "recurring": {"interval": "month"},
                                    }
                                }
                            ]
                        },
                        "metadata": {
                            "workspace_id": workspace_id,
                            "plan_id": "pro",
                        },
                    }
                },
            }
            payload = json.dumps(event).encode("utf-8")
            secret = "whsec_test_123"
            with patch.dict(
                os.environ,
                {
                    "EMPYRALIS_STRIPE_WEBHOOK_SECRET": secret,
                    "EMPYRALIS_STRIPE_PRICE_IDS": '{"pro":"price_pro_123"}',
                },
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
                result = billing_service.handle_stripe_webhook(payload, self._sign(secret, payload))
                summary = billing_service.workspace_billing_summary_for_workspace_id(workspace_id)

        self.assertTrue(result["ok"])
        self.assertEqual(summary["subscription"]["plan_id"], "pro")
        self.assertEqual(summary["subscription"]["effective_plan_id"], "pro")
        self.assertEqual(summary["subscription"]["status"], "active")

    def test_cancellation_webhook_downgrades_workspace_back_to_free(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            workspace_id = self._create_workspace(root)
            activate_event = {
                "type": "customer.subscription.updated",
                "data": {
                    "object": {
                        "id": "sub_pro_123",
                        "customer": "cus_pro_123",
                        "status": "active",
                        "currency": "usd",
                        "current_period_start": 1710000000,
                        "current_period_end": 1712592000,
                        "cancel_at_period_end": False,
                        "items": {"data": [{"price": {"id": "price_pro_123", "product": "prod_pro_123", "currency": "usd", "recurring": {"interval": "month"}}}]},
                        "metadata": {"workspace_id": workspace_id, "plan_id": "pro"},
                    }
                },
            }
            cancel_event = {
                "type": "customer.subscription.deleted",
                "data": {
                    "object": {
                        "id": "sub_pro_123",
                        "customer": "cus_pro_123",
                        "status": "canceled",
                        "currency": "usd",
                        "cancel_at_period_end": False,
                        "canceled_at": 1712000000,
                        "items": {"data": [{"price": {"id": "price_pro_123", "product": "prod_pro_123", "currency": "usd", "recurring": {"interval": "month"}}}]},
                        "metadata": {"workspace_id": workspace_id, "plan_id": "pro"},
                    }
                },
            }
            secret = "whsec_test_123"
            with patch.dict(
                os.environ,
                {
                    "EMPYRALIS_STRIPE_WEBHOOK_SECRET": secret,
                    "EMPYRALIS_STRIPE_PRICE_IDS": '{"pro":"price_pro_123"}',
                },
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
                billing_service.handle_stripe_webhook(activate_payload, self._sign(secret, activate_payload))
                billing_service.handle_stripe_webhook(cancel_payload, self._sign(secret, cancel_payload))
                summary = billing_service.workspace_billing_summary_for_workspace_id(workspace_id)

        self.assertEqual(summary["subscription"]["plan_id"], "pro")
        self.assertEqual(summary["subscription"]["status"], "canceled")
        self.assertEqual(summary["subscription"]["effective_plan_id"], "free")

    def test_credit_purchase_webhook_adds_balance_and_records_transaction(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            workspace_id = self._create_workspace(root)
            event = {
                "type": "checkout.session.completed",
                "data": {
                    "object": {
                        "id": "cs_credit_123",
                        "customer": "cus_credit_123",
                        "mode": "payment",
                        "currency": "usd",
                        "metadata": {
                            "workspace_id": workspace_id,
                            "purchase_kind": "credits",
                            "amount_usd": "15.00",
                        },
                    }
                },
            }
            secret = "whsec_test_123"
            with patch.dict(
                os.environ,
                {"EMPYRALIS_STRIPE_WEBHOOK_SECRET": secret},
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
                payload = json.dumps(event).encode("utf-8")
                result = billing_service.handle_stripe_webhook(payload, self._sign(secret, payload))
                balance = billing_service.credit_balance_for_workspace(workspace_id)
                summary = billing_service.workspace_billing_summary_for_workspace_id(workspace_id)

        self.assertTrue(result["ok"])
        self.assertEqual(result["purchase_kind"], "credits")
        self.assertEqual(result["amount_usd"], 15.0)
        self.assertEqual(balance["credit_balance_usd"], 15.0)
        self.assertEqual(balance["credit_balance_credits"], 300000)
        self.assertEqual(len(balance["transactions"]), 1)
        self.assertEqual(balance["transactions"][0]["kind"], "purchase")
        self.assertEqual(balance["transactions"][0]["amount_usd"], 15.0)
        self.assertEqual(summary["hosted_sage_ai"]["credit_balance_usd"], 15.0)
        self.assertEqual(summary["hosted_sage_ai"]["total_available_usd"], 15.5)

    def test_credit_purchase_webhook_accumulates_multiple_purchases(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            workspace_id = self._create_workspace(root)
            secret = "whsec_test_123"
            with patch.dict(
                os.environ,
                {"EMPYRALIS_STRIPE_WEBHOOK_SECRET": secret},
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
                for amount in [5.0, 10.0]:
                    event = {
                        "type": "checkout.session.completed",
                        "data": {
                            "object": {
                                "id": f"cs_credit_{amount}",
                                "customer": "cus_credit_123",
                                "mode": "payment",
                                "currency": "usd",
                                "metadata": {
                                    "workspace_id": workspace_id,
                                    "purchase_kind": "credits",
                                    "amount_usd": str(amount),
                                },
                            }
                        },
                    }
                    payload = json.dumps(event).encode("utf-8")
                    billing_service.handle_stripe_webhook(payload, self._sign(secret, payload))
                balance = billing_service.credit_balance_for_workspace(workspace_id)

        self.assertEqual(balance["credit_balance_usd"], 15.0)
        self.assertEqual(len(balance["transactions"]), 2)


if __name__ == "__main__":
    unittest.main()

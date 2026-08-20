"""Unit tests for polar_client.py — config resolution, checkout/portal
request shape, and Standard Webhooks signature verification.

The signature-verification tests are the load-bearing ones: they sign a
payload using the REAL `standardwebhooks` reference implementation
(vendored as a fixed-version pinned test dependency check below, matching
what Polar's own `polar_sdk.webhooks.validate_event` uses under the hood)
and assert our from-scratch stdlib implementation accepts it — proof this
module is byte-compatible with Polar's real signing scheme, not just
"looks right by reading the docs". If `standardwebhooks` isn't installed,
those two tests are skipped rather than silently passing on a weaker,
self-referential check (signing and verifying with the SAME code under
test would prove nothing — see CLAUDE.md's "a check that derives its own
expectations from the thing it checks is blind").

No test in this file reaches a live Polar endpoint — HTTP calls are always
mocked, per this repo's live-provider ban in server_modules/tests/conftest.py.
"""

import base64
import json
import os
import time
import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException

from server_modules import polar_client

try:
    from standardwebhooks.webhooks import Webhook as _ReferenceWebhook

    _HAS_STANDARDWEBHOOKS = True
except ImportError:
    _HAS_STANDARDWEBHOOKS = False


class PolarConfigTests(unittest.TestCase):
    def test_defaults_to_sandbox_and_unconfigured(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(polar_client.polar_server(), polar_client.POLAR_SERVER_SANDBOX)
            self.assertEqual(polar_client.polar_api_base(), "https://sandbox-api.polar.sh")
            self.assertFalse(polar_client.polar_configured())

    def test_production_server_selected_explicitly(self):
        with patch.dict(os.environ, {"EMPYRALIS_POLAR_SERVER": "production"}, clear=True):
            self.assertEqual(polar_client.polar_api_base(), "https://api.polar.sh")

    def test_unrecognized_server_value_falls_back_to_sandbox(self):
        with patch.dict(os.environ, {"EMPYRALIS_POLAR_SERVER": "staging"}, clear=True):
            self.assertEqual(polar_client.polar_server(), polar_client.POLAR_SERVER_SANDBOX)

    def test_product_map_reads_json_and_per_plan_override(self):
        with patch.dict(
            os.environ,
            {
                "EMPYRALIS_POLAR_PRODUCT_IDS": '{"pro": "product_json"}',
                "EMPYRALIS_POLAR_PRODUCT_PRO": "product_env_override",
            },
            clear=True,
        ):
            mapping = polar_client.polar_product_map()
        # Per-plan env var is read after the JSON map and wins on conflict.
        self.assertEqual(mapping["pro"], "product_env_override")

    def test_configured_requires_only_access_token(self):
        with patch.dict(os.environ, {"EMPYRALIS_POLAR_ACCESS_TOKEN": "polar_oat_test"}, clear=True):
            self.assertTrue(polar_client.polar_configured())


class PolarCheckoutRequestTests(unittest.TestCase):
    def test_create_checkout_session_posts_expected_body_and_auth(self):
        with patch.dict(os.environ, {"EMPYRALIS_POLAR_ACCESS_TOKEN": "polar_oat_test"}, clear=True), patch.object(
            polar_client,
            "_polar_request",
            return_value={"id": "checkout_1", "url": "https://polar.sh/checkout/checkout_1"},
        ) as request_mock:
            result = polar_client.create_checkout_session(
                product_id="prod_pro_123",
                success_url="https://app.example.com/billing?checkout=success",
                return_url="https://app.example.com/billing?checkout=cancelled",
                customer_email="owner@example.com",
                external_customer_id="workspace-1",
                metadata={"workspace_id": "workspace-1", "plan_id": "pro"},
            )

        self.assertEqual(result["id"], "checkout_1")
        method, path = request_mock.call_args.args
        self.assertEqual(method, "POST")
        self.assertEqual(path, "/v1/checkouts/")
        body = request_mock.call_args.kwargs["json_body"]
        self.assertEqual(body["products"], ["prod_pro_123"])
        self.assertEqual(body["external_customer_id"], "workspace-1")
        self.assertEqual(body["metadata"], {"workspace_id": "workspace-1", "plan_id": "pro"})
        self.assertNotIn("amount", body)

    def test_create_checkout_session_passes_amount_for_pay_what_you_want(self):
        with patch.object(polar_client, "_polar_request", return_value={"id": "c", "url": "u"}) as request_mock:
            polar_client.create_checkout_session(
                product_id="prod_credits",
                success_url="https://app.example.com/success",
                amount_cents=1000,
            )
        body = request_mock.call_args.kwargs["json_body"]
        self.assertEqual(body["amount"], 1000)

    def test_request_without_access_token_raises_503(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(HTTPException) as ctx:
                polar_client._polar_request("POST", "/v1/checkouts/", json_body={})
        self.assertEqual(ctx.exception.status_code, 503)

    def test_customer_portal_session_requires_a_customer_reference(self):
        with self.assertRaises(HTTPException) as ctx:
            polar_client.create_customer_portal_session()
        self.assertEqual(ctx.exception.status_code, 400)

    def test_customer_portal_session_posts_customer_id(self):
        with patch.object(
            polar_client,
            "_polar_request",
            return_value={"customer_portal_url": "https://polar.sh/portal/abc"},
        ) as request_mock:
            result = polar_client.create_customer_portal_session(customer_id="cus_1", return_url="https://x/back")
        self.assertEqual(result["customer_portal_url"], "https://polar.sh/portal/abc")
        _, path = request_mock.call_args.args
        self.assertEqual(path, "/v1/customer-sessions/")
        body = request_mock.call_args.kwargs["json_body"]
        self.assertEqual(body["customer_id"], "cus_1")
        self.assertNotIn("external_customer_id", body)


class PolarWebhookSignatureTests(unittest.TestCase):
    def _sign_with_our_verifier_key(self, secret: str, webhook_id: str, timestamp: str, body: bytes) -> str:
        # Signs using the exact algorithm verify_webhook_signature checks
        # against — used for the "wrong secret" / "tampered body" negative
        # tests, which don't need the external reference implementation.
        import hashlib
        import hmac as hmac_module

        key = polar_client._polar_webhook_hmac_key(secret)
        signed_payload = f"{webhook_id}.{timestamp}.{body.decode('utf-8')}".encode("utf-8")
        signature = base64.b64encode(hmac_module.new(key, signed_payload, hashlib.sha256).digest()).decode("ascii")
        return f"v1,{signature}"

    def test_missing_secret_raises_503(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(HTTPException) as ctx:
                polar_client.verify_webhook_signature(b"{}", {"webhook-id": "a", "webhook-timestamp": "1", "webhook-signature": "v1,x"})
        self.assertEqual(ctx.exception.status_code, 503)

    def test_missing_headers_rejected(self):
        with patch.dict(os.environ, {"EMPYRALIS_POLAR_WEBHOOK_SECRET": "s"}, clear=True):
            with self.assertRaises(HTTPException) as ctx:
                polar_client.verify_webhook_signature(b"{}", {})
        self.assertEqual(ctx.exception.status_code, 400)

    def test_stale_timestamp_rejected(self):
        secret = "test_secret_1"
        body = b'{"type": "order.paid"}'
        webhook_id = "msg_1"
        old_timestamp = str(int(time.time()) - 3600)
        signature = self._sign_with_our_verifier_key(secret, webhook_id, old_timestamp, body)
        with patch.dict(os.environ, {"EMPYRALIS_POLAR_WEBHOOK_SECRET": secret}, clear=True):
            with self.assertRaises(HTTPException) as ctx:
                polar_client.verify_webhook_signature(
                    body,
                    {"webhook-id": webhook_id, "webhook-timestamp": old_timestamp, "webhook-signature": signature},
                )
        self.assertEqual(ctx.exception.status_code, 400)

    def test_self_signed_round_trip_accepted_and_tamper_rejected(self):
        secret = "test_secret_2"
        body = json.dumps({"type": "order.paid", "data": {"id": "order_1"}}).encode("utf-8")
        webhook_id = "msg_2"
        timestamp = str(int(time.time()))
        signature = self._sign_with_our_verifier_key(secret, webhook_id, timestamp, body)
        headers = {"webhook-id": webhook_id, "webhook-timestamp": timestamp, "webhook-signature": signature}
        with patch.dict(os.environ, {"EMPYRALIS_POLAR_WEBHOOK_SECRET": secret}, clear=True):
            # Correct body verifies cleanly.
            polar_client.verify_webhook_signature(body, headers)
            # A tampered body must not verify against the same signature.
            tampered = json.dumps({"type": "order.paid", "data": {"id": "order_2"}}).encode("utf-8")
            with self.assertRaises(HTTPException) as ctx:
                polar_client.verify_webhook_signature(tampered, headers)
            self.assertEqual(ctx.exception.status_code, 400)
            # The wrong secret must not verify either.
            with patch.dict(os.environ, {"EMPYRALIS_POLAR_WEBHOOK_SECRET": "wrong_secret"}, clear=True):
                with self.assertRaises(HTTPException):
                    polar_client.verify_webhook_signature(body, headers)

    def test_header_lookup_is_case_insensitive(self):
        secret = "test_secret_3"
        body = b'{"type": "order.paid"}'
        webhook_id = "msg_3"
        timestamp = str(int(time.time()))
        signature = self._sign_with_our_verifier_key(secret, webhook_id, timestamp, body)
        headers = {"Webhook-Id": webhook_id, "WEBHOOK-TIMESTAMP": timestamp, "webhook-Signature": signature}
        with patch.dict(os.environ, {"EMPYRALIS_POLAR_WEBHOOK_SECRET": secret}, clear=True):
            polar_client.verify_webhook_signature(body, headers)

    @unittest.skipUnless(
        _HAS_STANDARDWEBHOOKS,
        "standardwebhooks not installed — this test proves our verifier is "
        "byte-compatible with Polar's OWN reference signing implementation, "
        "not merely self-consistent; see module docstring.",
    )
    def test_accepts_a_signature_from_polars_own_reference_library(self):
        from datetime import datetime, timezone

        secret = "polar_reference_secret"
        # Mirrors polar_sdk._webhooks.validate_event's own secret handling.
        base64_secret = base64.b64encode(secret.encode()).decode()
        reference_webhook = _ReferenceWebhook(base64_secret)

        body = json.dumps({"type": "subscription.active", "data": {"id": "sub_1"}}).encode("utf-8")
        webhook_id = "msg_reference_1"
        now = datetime.now(tz=timezone.utc)
        signature = reference_webhook.sign(msg_id=webhook_id, timestamp=now, data=body.decode())
        timestamp = str(int(now.timestamp()))

        headers = {"webhook-id": webhook_id, "webhook-timestamp": timestamp, "webhook-signature": signature}
        with patch.dict(os.environ, {"EMPYRALIS_POLAR_WEBHOOK_SECRET": secret}, clear=True):
            polar_client.verify_webhook_signature(body, headers)  # must not raise


if __name__ == "__main__":
    unittest.main()

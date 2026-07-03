"""Phase C2: Webhook signature verification tests.

Verify that every cloud channel connector has a working signature
verification function and that invalid/missing signatures are rejected.
"""

from __future__ import annotations

import hashlib
import hmac
import unittest


class GitHubSignatureTests(unittest.TestCase):

    def test_verify_valid_signature(self):
        from server_modules.connectors.github_connector import verify_request_signature
        secret = "test_secret_123"
        body = b'{"action":"opened","issue":{"title":"test"}}'
        digest = hmac.new(
            secret.encode("utf-8"), body, hashlib.sha256,
        ).hexdigest()
        headers = {"x-hub-signature-256": f"sha256={digest}"}
        self.assertTrue(verify_request_signature(headers, body, secret))

    def test_verify_invalid_signature_rejected(self):
        from server_modules.connectors.github_connector import verify_request_signature
        secret = "test_secret_123"
        body = b'{"action":"opened"}'
        headers = {"x-hub-signature-256": "sha256=deadbeef"}
        self.assertFalse(verify_request_signature(headers, body, secret))

    def test_verify_missing_header_rejected(self):
        from server_modules.connectors.github_connector import verify_request_signature
        secret = "test_secret_123"
        body = b'{"action":"opened"}'
        headers: dict = {}
        self.assertFalse(verify_request_signature(headers, body, secret))

    def test_verify_empty_secret_rejected(self):
        from server_modules.connectors.github_connector import verify_request_signature
        body = b'{"action":"opened"}'
        headers = {"x-hub-signature-256": "sha256=abc"}
        self.assertFalse(verify_request_signature(headers, body, ""))


class SlackSignatureTests(unittest.TestCase):

    def test_verify_missing_headers_rejected(self):
        from server_modules.connectors.slack_connector import verify_request_signature
        self.assertFalse(
            verify_request_signature(
                headers={},
                raw_body=b"payload=test",
                signing_secret="test-secret",
            )
        )

    def test_verify_wrong_signature_rejected(self):
        from server_modules.connectors.slack_connector import verify_request_signature
        headers = {
            "x-slack-request-timestamp": "1700000000",
            "x-slack-signature": "v0=wrongsignature12345",
        }
        self.assertFalse(
            verify_request_signature(
                headers=headers,
                raw_body=b"payload=test",
                signing_secret="test-secret",
                now_ts=1700000000,
            )
        )

    def test_verify_valid_signature(self):
        from server_modules.connectors.slack_connector import verify_request_signature
        secret = "test-secret"
        timestamp = "1700000000"
        body = b"payload=test"
        sig_basestring = f"v0:{timestamp}:{body.decode()}"
        digest = hmac.new(
            secret.encode("utf-8"), sig_basestring.encode("utf-8"), hashlib.sha256,
        ).hexdigest()
        headers = {
            "x-slack-request-timestamp": timestamp,
            "x-slack-signature": f"v0={digest}",
        }
        self.assertTrue(
            verify_request_signature(
                headers=headers,
                raw_body=body,
                signing_secret=secret,
                now_ts=1700000000,  # match the timestamp to pass the 5-min window check
            )
        )


class WhatsAppTwilioSignatureTests(unittest.TestCase):

    def test_validate_empty_signature_rejected(self):
        from server_modules.connectors.whatsapp_transport_service import (
            WhatsAppTransportService,
        )
        svc = WhatsAppTransportService()
        self.assertFalse(
            svc.validate_webhook_signature(
                request_url="https://example.com/webhook",
                form={},
                signature="",
                auth_token="test-token",
            )
        )

    def test_validate_invalid_signature_rejected(self):
        from server_modules.connectors.whatsapp_transport_service import (
            WhatsAppTransportService,
        )
        svc = WhatsAppTransportService()
        self.assertFalse(
            svc.validate_webhook_signature(
                request_url="https://example.com/webhook",
                form={"k": "v"},
                signature="bogus",
                auth_token="test-token",
            )
        )

    def test_validate_valid_signature(self):
        from server_modules.connectors.whatsapp_transport_service import (
            WhatsAppTransportService,
        )
        import base64
        svc = WhatsAppTransportService()
        token = "test-token"
        url = "https://example.com/webhook"
        form = {"Body": "hello", "From": "+1234567890"}
        payload = url
        for key in sorted(form.keys()):
            payload += key + form[key]
        digest = hmac.new(token.encode("utf-8"), payload.encode("utf-8"), hashlib.sha1).digest()
        valid_sig = base64.b64encode(digest).decode("ascii")
        self.assertTrue(
            svc.validate_webhook_signature(
                request_url=url, form=form, signature=valid_sig, auth_token=token,
            )
        )


class DiscordSignatureTests(unittest.TestCase):

    def test_verify_missing_headers_rejected(self):
        from server_modules.connectors.discord_connector import verify_interaction_signature
        self.assertFalse(
            verify_interaction_signature(
                headers={},
                raw_body=b'{"type":1}',
                public_key="abcd",
            )
        )

    def test_verify_invalid_signature_rejected(self):
        from server_modules.connectors.discord_connector import verify_interaction_signature
        headers = {
            "x-signature-ed25519": "bogussignature12345",
            "x-signature-timestamp": "1234567890",
        }
        self.assertFalse(
            verify_interaction_signature(
                headers=headers,
                raw_body=b'{"type":1}',
                public_key="abcd",
            )
        )


if __name__ == "__main__":
    unittest.main()

import unittest
from unittest.mock import patch

from server_modules import security_audit_service


class SecurityAuditServiceTests(unittest.TestCase):
    def test_emit_security_audit_event_redacts_sensitive_metadata(self):
        captured = {}

        def fake_emit_runtime_event(**kwargs):
            captured.update(kwargs)
            return {"id": "event-1"}

        with patch(
            "server_modules.security_audit_service.outbox_service.emit_runtime_event",
            side_effect=fake_emit_runtime_event,
        ):
            security_audit_service.emit_security_audit_event(
                action="provider.saved",
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                current_user={"user_id": "user-1", "email": "USER@EXAMPLE.COM"},
                metadata={
                    "provider": "openai",
                    "api_key": "sk-live_1234567890",
                    "nested": {
                        "Authorization": "Bearer abc.def.ghi",
                        "safe": "kept",
                    },
                    "items": [
                        {
                            "pairing_token": "gpair_secret-token",
                            "note": "Authorization: Bearer token-value",
                        }
                    ],
                },
            )

        metadata = captured["payload"]["metadata"]
        self.assertEqual(metadata["provider"], "openai")
        self.assertEqual(metadata["api_key"], "[redacted]")
        self.assertEqual(metadata["nested"]["Authorization"], "[redacted]")
        self.assertEqual(metadata["nested"]["safe"], "kept")
        self.assertEqual(metadata["items"][0]["pairing_token"], "[redacted]")
        self.assertEqual(metadata["items"][0]["note"], "Authorization: [redacted]")
        self.assertEqual(captured["payload"]["actor_email"], "user@example.com")

    def test_sanitize_security_audit_metadata_handles_non_dict(self):
        self.assertEqual(security_audit_service.sanitize_security_audit_metadata(None), {})
        self.assertEqual(security_audit_service.sanitize_security_audit_metadata([]), {})

    def test_sanitize_security_audit_metadata_redacts_headers_cookies_and_phone(self):
        metadata = security_audit_service.sanitize_security_audit_metadata(
            {
                "headers": {
                    "Authorization": "Bearer top-secret-token",
                    "Cookie": "sessionid=abc123; csrftoken=def456",
                    "X-Api-Key": "secret-key-1",
                },
                "raw_header_text": "Authorization: Bearer abc.def.ghi",
                "set_cookie_line": "Set-Cookie: sid=abc; HttpOnly",
                "customer_phone": "+1 (415) 555-1234",
                "safe": "keep-me",
            }
        )

        self.assertEqual(metadata["headers"]["Authorization"], "[redacted]")
        self.assertEqual(metadata["headers"]["Cookie"], "[redacted]")
        self.assertEqual(metadata["headers"]["X-Api-Key"], "[redacted]")
        self.assertEqual(metadata["raw_header_text"], "Authorization: [redacted]")
        self.assertEqual(metadata["set_cookie_line"], "[redacted]")
        self.assertEqual(metadata["customer_phone"], "[redacted]")
        self.assertEqual(metadata["safe"], "keep-me")

    def test_emit_security_audit_event_redacts_detail_before_outbox(self):
        captured = {}

        def fake_emit_runtime_event(**kwargs):
            captured.update(kwargs)
            return {"id": "event-1"}

        with patch(
            "server_modules.security_audit_service.outbox_service.emit_runtime_event",
            side_effect=fake_emit_runtime_event,
        ):
            security_audit_service.emit_security_audit_event(
                action="provider.failed",
                workspace_id="workspace-1",
                detail="Authorization: Bearer secret-token-value",
            )

        self.assertEqual(captured["payload"]["detail"], "Authorization: [redacted]")

    def test_emit_security_audit_event_redacts_unknown_high_entropy_metadata(self):
        captured = {}
        token = "Sf9_Za4Yp7Nq2Rt6Lm8Cb3Kx5Vh1Wd"

        def fake_emit_runtime_event(**kwargs):
            captured.update(kwargs)
            return {"id": "event-1"}

        with patch(
            "server_modules.security_audit_service.outbox_service.emit_runtime_event",
            side_effect=fake_emit_runtime_event,
        ):
            security_audit_service.emit_security_audit_event(
                action="connector.output",
                workspace_id="workspace-1",
                metadata={"result_summary": f"connector returned {token}"},
            )

        summary = captured["payload"]["metadata"]["result_summary"]
        self.assertIn("[redacted-secret]", summary)
        self.assertNotIn(token, summary)


if __name__ == "__main__":
    unittest.main()

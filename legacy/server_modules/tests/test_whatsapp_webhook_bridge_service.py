import unittest

from server_modules.connectors.whatsapp_webhook_bridge_service import WhatsAppWebhookBridgeService


class WhatsAppWebhookBridgeServiceTests(unittest.TestCase):
    def test_bridge_returns_error_response_without_parsing_when_auth_fails(self) -> None:
        calls = []
        service = WhatsAppWebhookBridgeService(
            init_runtime=lambda: calls.append("init"),
            parse_form_urlencoded=lambda raw: {"Body": raw.decode("utf-8")},
            webhook_auth_result=lambda **kwargs: {"status_code": 503, "content": "Empyralis WhatsApp autopilot is disabled."},
            resolve_inbound_connector=lambda payload: None,
            validate_signature=lambda request_url, form, signature, auth_token: False,
            ingest_webhook=lambda payload, matched=None: None,
            twiml_response=lambda text: {"kind": "twiml", "text": text},
            error_response=lambda status_code, content: {"kind": "error", "status_code": status_code, "content": content},
            enabled=True,
            configured_secret="secret-1",
        )

        result = service.handle_webhook(raw_body=b"hello", request_url="https://example.com/webhook", query_secret="bad")

        self.assertEqual(
            result,
            {"kind": "error", "status_code": 503, "content": "Empyralis WhatsApp autopilot is disabled."},
        )
        self.assertEqual(calls[0], "init")

    def test_bridge_returns_twiml_response(self) -> None:
        captured = {}

        def _webhook_auth_result(**kwargs):
            captured.update(kwargs)
            return {"status_code": 200}

        service = WhatsAppWebhookBridgeService(
            init_runtime=lambda: None,
            parse_form_urlencoded=lambda raw: {
                "Body": raw.decode("utf-8"),
                "AccountSid": "AC123",
                "From": "whatsapp:+100",
                "To": "whatsapp:+200",
            },
            webhook_auth_result=_webhook_auth_result,
            resolve_inbound_connector=lambda payload: {
                "connector_id": "conn-1",
                "workspace_id": "ws-1",
                "secret": {"auth_token": "twilio-auth"},
            },
            validate_signature=lambda request_url, form, signature, auth_token: request_url == "https://example.com/webhook" and signature == "sig-1" and auth_token == "twilio-auth",
            ingest_webhook=lambda payload, matched=None: f"handled:{payload['Body']}:{matched['connector_id']}",
            twiml_response=lambda text: {"kind": "twiml", "text": text},
            error_response=lambda status_code, content: {"kind": "error", "status_code": status_code, "content": content},
            enabled=True,
            configured_secret="secret-1",
        )

        result = service.handle_webhook(
            raw_body=b"hello",
            request_url="https://example.com/webhook",
            twilio_signature="sig-1",
            header_secret="secret-1",
        )

        self.assertEqual(result, {"kind": "twiml", "text": "handled:hello:conn-1"})
        self.assertEqual(captured["header_secret"], "secret-1")

    def test_bridge_requires_twilio_signature_header(self) -> None:
        service = WhatsAppWebhookBridgeService(
            init_runtime=lambda: None,
            parse_form_urlencoded=lambda raw: {
                "Body": raw.decode("utf-8"),
                "AccountSid": "AC123",
                "From": "whatsapp:+100",
                "To": "whatsapp:+200",
            },
            webhook_auth_result=lambda **kwargs: {"status_code": 200},
            resolve_inbound_connector=lambda payload: {
                "connector_id": "conn-1",
                "workspace_id": "ws-1",
                "secret": {"auth_token": "twilio-auth"},
            },
            validate_signature=lambda request_url, form, signature, auth_token: True,
            ingest_webhook=lambda payload, matched=None: "ok",
            twiml_response=lambda text: {"kind": "twiml", "text": text},
            error_response=lambda status_code, content: {"kind": "error", "status_code": status_code, "content": content},
            enabled=True,
            configured_secret="secret-1",
        )

        result = service.handle_webhook(raw_body=b"hello", request_url="https://example.com/webhook", header_secret="secret-1")

        self.assertEqual(result, {"kind": "error", "status_code": 401, "content": "X-Twilio-Signature header is required."})


if __name__ == "__main__":
    unittest.main()

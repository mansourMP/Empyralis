import unittest

from server_modules.connectors.whatsapp_webhook_service import WhatsAppWebhookService


class _IngressStub:
    def __init__(self) -> None:
        self.parse_calls = []
        self.ingest_calls = []

    def parse_form_urlencoded(self, raw: bytes):
        self.parse_calls.append(raw)
        return {"Body": raw.decode("utf-8")}

    def ingest_webhook(self, payload, *, matched=None):
        self.ingest_calls.append((payload, matched))
        return f"handled:{payload.get('Body')}:{(matched or {}).get('connector_id')}"


class WhatsAppWebhookServiceTests(unittest.TestCase):
    def test_wrapper_delegates_form_parsing_to_ingress_service(self) -> None:
        ingress = _IngressStub()
        service = WhatsAppWebhookService(ingress_service=lambda: ingress)

        payload = service.parse_form_urlencoded(b"hello")

        self.assertEqual(payload, {"Body": "hello"})
        self.assertEqual(ingress.parse_calls, [b"hello"])

    def test_wrapper_delegates_inbound_handling_to_ingress_service(self) -> None:
        ingress = _IngressStub()
        service = WhatsAppWebhookService(ingress_service=lambda: ingress)

        result = service.handle_inbound({"Body": "hello"}, matched={"connector_id": "conn-1"})

        self.assertEqual(result, "handled:hello:conn-1")
        self.assertEqual(ingress.ingest_calls, [({"Body": "hello"}, {"connector_id": "conn-1"})])


if __name__ == "__main__":
    unittest.main()

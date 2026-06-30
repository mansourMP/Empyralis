import unittest
from unittest import mock

from server_modules.connectors.whatsapp_transport_service import WhatsAppTransportService


class WhatsAppTransportServiceTests(unittest.TestCase):
    def test_normalize_number_handles_common_forms(self) -> None:
        service = WhatsAppTransportService()
        self.assertEqual(service.normalize_number("+123"), "whatsapp:+123")
        self.assertEqual(service.normalize_number("whatsapp:+123"), "whatsapp:+123")
        self.assertEqual(service.normalize_number("*"), "whatsapp:*")
        self.assertEqual(service.normalize_number("ABC"), "abc")

    def test_twiml_response_escapes_message(self) -> None:
        service = WhatsAppTransportService()
        response = service.twiml_response("<hello>")
        body = response.body.decode("utf-8")
        self.assertIn("&lt;hello&gt;", body)
        self.assertIn("<Message>", body)

    def test_send_message_requires_credentials_and_numbers(self) -> None:
        service = WhatsAppTransportService()
        with self.assertRaisesRegex(RuntimeError, "account_sid/auth_token"):
            service.send_message(
                account_sid="",
                auth_token="",
                from_number="whatsapp:+100",
                to_number="whatsapp:+200",
                body="hello",
            )
        with self.assertRaisesRegex(RuntimeError, "From/To numbers"):
            service.send_message(
                account_sid="AC123",
                auth_token="token",
                from_number="",
                to_number="",
                body="hello",
            )

    def test_send_message_returns_json_payload(self) -> None:
        service = WhatsAppTransportService()

        class _Response:
            status = 201

            def read(self) -> bytes:
                return b'{\"sid\": \"SM123\"}'

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        with mock.patch("server_modules.connectors.whatsapp_transport_service.urlrequest.urlopen", return_value=_Response()) as mocked_urlopen:
            payload = service.send_message(
                account_sid="AC123",
                auth_token="token",
                from_number="+100",
                to_number="+200",
                body="hello",
            )
        self.assertEqual(payload["sid"], "SM123")
        request = mocked_urlopen.call_args.args[0]
        self.assertIn("/Accounts/AC123/Messages.json", request.full_url)


if __name__ == "__main__":
    unittest.main()

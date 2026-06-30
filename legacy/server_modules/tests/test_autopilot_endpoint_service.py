import unittest

from server_modules.connectors.autopilot_endpoint_service import AutopilotEndpointService


class AutopilotEndpointServiceTests(unittest.TestCase):
    def test_autopilot_profiles_payload_shapes_both_channels(self) -> None:
        service = AutopilotEndpointService()
        payload = service.autopilot_profiles_payload(
            telegram_enabled=True,
            telegram_default_profile="assistant",
            telegram_catalog={"assistant": {"label": "Assistant", "allow_help": True}},
            telegram_webhook_path="/channels/telegram/webhook/{connector_id}",
            whatsapp_enabled=False,
            whatsapp_default_profile="assistant",
            whatsapp_catalog={"assistant": {"label": "Assistant", "allow_status": True}},
            whatsapp_webhook_path="/channels/whatsapp/twilio/webhook",
        )
        self.assertTrue(payload["channels"]["telegram"]["enabled"])
        self.assertEqual(payload["channels"]["telegram"]["profiles"][0]["id"], "assistant")
        self.assertEqual(
            payload["channels"]["telegram"]["webhook_path"],
            "/channels/telegram/webhook/{connector_id}",
        )
        self.assertEqual(payload["channels"]["whatsapp"]["webhook_path"], "/channels/whatsapp/twilio/webhook")

    def test_telegram_webhook_auth_result_handles_disabled_mode_secret_and_success(self) -> None:
        service = AutopilotEndpointService()

        disabled = service.telegram_webhook_auth_result(
            enabled=False,
            delivery_mode="webhook",
            configured_secret="secret",
            header_secret="secret",
        )
        self.assertEqual(disabled["status_code"], 503)
        self.assertEqual(disabled["content"], "Empyralis Telegram autopilot is disabled.")

        fallback = service.telegram_webhook_auth_result(
            enabled=True,
            delivery_mode="polling",
            configured_secret="secret",
            header_secret="secret",
        )
        self.assertEqual(fallback["status_code"], 503)
        self.assertIn("ORION_TELEGRAM_AUTOPILOT_DELIVERY_MODE=webhook", fallback["content"])

        missing_secret = service.telegram_webhook_auth_result(
            enabled=True,
            delivery_mode="webhook",
            configured_secret="",
            header_secret="secret",
        )
        self.assertEqual(missing_secret["status_code"], 503)
        self.assertIn("not configured", missing_secret["content"])

        missing_header = service.telegram_webhook_auth_result(
            enabled=True,
            delivery_mode="webhook",
            configured_secret="secret",
            header_secret="",
        )
        self.assertEqual(missing_header["status_code"], 401)

        invalid = service.telegram_webhook_auth_result(
            enabled=True,
            delivery_mode="webhook",
            configured_secret="secret",
            header_secret="wrong",
        )
        self.assertEqual(invalid["status_code"], 403)
        self.assertEqual(invalid["content"], "Telegram webhook secret is invalid.")

        success = service.telegram_webhook_auth_result(
            enabled=True,
            delivery_mode="webhook",
            configured_secret="secret",
            header_secret="secret",
        )
        self.assertEqual(success["status_code"], 200)

    def test_whatsapp_webhook_auth_result_handles_disabled_missing_invalid_and_success(self) -> None:
        service = AutopilotEndpointService()

        disabled = service.whatsapp_webhook_auth_result(
            enabled=False,
            configured_secret="",
            query_secret="",
            header_secret="",
        )
        self.assertEqual(disabled["status_code"], 503)
        self.assertEqual(disabled["content"], "Empyralis WhatsApp autopilot is disabled.")

        missing_secret = service.whatsapp_webhook_auth_result(
            enabled=True,
            configured_secret="",
            query_secret="secret",
            header_secret="",
        )
        self.assertEqual(missing_secret["status_code"], 503)
        self.assertIn("not configured", missing_secret["content"])

        missing_provided = service.whatsapp_webhook_auth_result(
            enabled=True,
            configured_secret="secret",
            query_secret="",
            header_secret="secret",
        )
        self.assertEqual(missing_provided["status_code"], 401)
        self.assertEqual(missing_provided["content"], "WhatsApp webhook query secret is required.")

        forbidden = service.whatsapp_webhook_auth_result(
            enabled=True,
            configured_secret="secret",
            query_secret="wrong",
            header_secret="secret",
        )
        self.assertEqual(forbidden["status_code"], 403)
        self.assertEqual(forbidden["content"], "WhatsApp webhook secret is invalid.")

        success = service.whatsapp_webhook_auth_result(
            enabled=True,
            configured_secret="secret",
            query_secret="secret",
            header_secret="ignored",
        )
        self.assertEqual(success["status_code"], 200)


if __name__ == "__main__":
    unittest.main()

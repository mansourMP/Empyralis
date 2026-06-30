import asyncio
import unittest

from server_modules.connectors.autopilot_terminal_bridge_service import AutopilotTerminalBridgeService


class AutopilotTerminalBridgeServiceTests(unittest.TestCase):
    def _make_service(self):
        calls = []

        class _TerminalService:
            async def handle_send_message(self, **kwargs):
                calls.append(("send", kwargs))
                return {"ok": True, "mode": "send"}

            async def handle_autopilot_test_message(self, **kwargs):
                calls.append(("test", kwargs))
                return {"ok": True, "mode": "test"}

        class _SupervisorService:
            def run_forever(self):
                calls.append(("run_forever", None))

        class _StatusService:
            def telegram_status_payload(self):
                calls.append(("tg_status", None))
                return {"channel": "telegram"}

            def whatsapp_status_payload(self):
                calls.append(("wa_status", None))
                return {"channel": "whatsapp"}

        class _EndpointService:
            def autopilot_profiles_payload(self, **kwargs):
                calls.append(("profiles", kwargs))
                return {"ok": True, "profiles": True}

        service = AutopilotTerminalBridgeService(
            init_runtime=lambda: calls.append(("init", None)),
            telegram_terminal_service=lambda: _TerminalService(),
            telegram_supervisor_service=lambda: _SupervisorService(),
            autopilot_status_service=lambda: _StatusService(),
            autopilot_endpoint_service=lambda: _EndpointService(),
            telegram_enabled=True,
            telegram_default_profile="assistant",
            telegram_catalog={"assistant": {}},
            telegram_webhook_path="/channels/telegram/webhook/{connector_id}",
            whatsapp_enabled=True,
            whatsapp_default_profile="assistant",
            whatsapp_catalog={"assistant": {}},
            whatsapp_webhook_path="/channels/whatsapp/twilio/webhook",
        )
        return service, calls

    def test_send_message_initializes_runtime(self) -> None:
        service, calls = self._make_service()
        payload = asyncio.run(service.handle_telegram_send_message(text="hello"))
        self.assertEqual(payload["mode"], "send")
        self.assertEqual(calls[0][0], "init")
        self.assertEqual(calls[1][0], "send")

    def test_test_message_initializes_runtime(self) -> None:
        service, calls = self._make_service()
        payload = asyncio.run(service.handle_telegram_autopilot_test_message(text="hello"))
        self.assertEqual(payload["mode"], "test")
        self.assertEqual(calls[0][0], "init")
        self.assertEqual(calls[1][0], "test")

    def test_status_and_profiles_delegate(self) -> None:
        service, calls = self._make_service()
        telegram_payload = asyncio.run(service.telegram_status_payload())
        whatsapp_payload = asyncio.run(service.whatsapp_status_payload())
        profiles_payload = asyncio.run(service.autopilot_profiles_payload())
        service.run_telegram_autopilot_forever()
        self.assertEqual(telegram_payload["channel"], "telegram")
        self.assertEqual(whatsapp_payload["channel"], "whatsapp")
        self.assertTrue(profiles_payload["profiles"])
        self.assertIn(("run_forever", None), calls)
        self.assertIn(
            (
                "profiles",
                {
                    "telegram_enabled": True,
                    "telegram_default_profile": "assistant",
                    "telegram_catalog": {"assistant": {}},
                    "telegram_webhook_path": "/channels/telegram/webhook/{connector_id}",
                    "whatsapp_enabled": True,
                    "whatsapp_default_profile": "assistant",
                    "whatsapp_catalog": {"assistant": {}},
                    "whatsapp_webhook_path": "/channels/whatsapp/twilio/webhook",
                },
            ),
            calls,
        )


if __name__ == "__main__":
    unittest.main()

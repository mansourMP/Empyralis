import threading
import unittest

from server_modules.connectors.autopilot_state_bridge_service import AutopilotStateBridgeService


class AutopilotStateBridgeServiceTests(unittest.TestCase):
    def _make_service(self):
        calls = []
        telegram_state = {}

        class _TelegramStateService:
            def load_state(self):
                calls.append("tg_load")

            def snapshot(self, include_connectors=False):
                calls.append(("tg_snapshot", include_connectors))
                return {"channel": "telegram", "include_connectors": include_connectors}

        class _WhatsAppStateService:
            def load_state(self):
                calls.append("wa_load")

            def snapshot(self, include_connectors=False):
                calls.append(("wa_snapshot", include_connectors))
                return {"channel": "whatsapp", "include_connectors": include_connectors}

            def activate(self):
                calls.append("wa_activate")

        class _TelegramRuntimeService:
            def increment_processed_updates(self):
                calls.append("tg_increment")

            def set_connectors_seen(self, count):
                calls.append(("tg_seen", count))

        service = AutopilotStateBridgeService(
            telegram_state_service=lambda: _TelegramStateService(),
            whatsapp_state_service=lambda: _WhatsAppStateService(),
            telegram_runtime_service=lambda: _TelegramRuntimeService(),
            telegram_state=telegram_state,
            telegram_lock=threading.Lock(),
        )
        return service, calls, telegram_state

    def test_load_snapshot_and_activate_delegate(self) -> None:
        service, calls, _state = self._make_service()
        service.load_telegram_autopilot_state()
        service.load_whatsapp_autopilot_state()
        telegram_payload = service.telegram_autopilot_snapshot()
        whatsapp_payload = service.whatsapp_autopilot_snapshot()
        service.whatsapp_autopilot_activate()
        self.assertEqual(telegram_payload["channel"], "telegram")
        self.assertEqual(whatsapp_payload["channel"], "whatsapp")
        self.assertIn("tg_load", calls)
        self.assertIn("wa_load", calls)
        self.assertIn("wa_activate", calls)

    def test_runtime_and_start_state_delegate(self) -> None:
        service, calls, telegram_state = self._make_service()
        service.telegram_increment_processed_updates()
        service.telegram_set_connectors_seen(3)
        service.mark_telegram_autopilot_started("2026-04-05T00:00:00Z")
        self.assertIn("tg_increment", calls)
        self.assertIn(("tg_seen", 3), calls)
        self.assertTrue(telegram_state["active"])
        self.assertEqual(telegram_state["started_at"], "2026-04-05T00:00:00Z")
        self.assertTrue(telegram_state["enabled"])


if __name__ == "__main__":
    unittest.main()

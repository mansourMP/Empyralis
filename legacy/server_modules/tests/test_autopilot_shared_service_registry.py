import unittest

from server_modules.connectors.autopilot_shared_service_registry import AutopilotSharedServiceRegistry


class AutopilotSharedServiceRegistryTests(unittest.TestCase):
    def _make_registry(self) -> AutopilotSharedServiceRegistry:
        return AutopilotSharedServiceRegistry(
            normalize_workspace_id=lambda value: str(value or ""),
            append_channel_event=lambda **kwargs: {"ok": True},
            utc_now_iso=lambda: "2026-04-05T00:00:00Z",
            truncate_one_line=lambda text, limit: str(text or "")[:limit],
            json_safe=lambda value: value,
            dead_letter_lock=None,
            read_dead_letter_json=lambda path, default: default,
            write_dead_letter_json=lambda path, payload: None,
            dead_letter_file="dead-letter.json",
            dead_letter_limit=100,
            collapse_whitespace=lambda text: " ".join(str(text or "").split()),
            telegram_snapshot=lambda: {"enabled": True},
            telegram_list_entries=lambda: [{"id": "tg-1"}],
            resolve_telegram_profile=lambda entry: {"id": "assistant"},
            telegram_webhook_path="/channels/telegram/webhook/{connector_id}",
            telegram_public_base_url="https://public.example.com",
            telegram_webhook_secret_configured=True,
            telegram_delivery_mode="webhook",
            whatsapp_snapshot=lambda: {"enabled": True},
            whatsapp_list_entries=lambda: [{"id": "wa-1"}],
            resolve_whatsapp_profile=lambda entry: {"id": "assistant"},
            whatsapp_webhook_path="/channels/whatsapp/twilio/webhook",
            whatsapp_public_base_url="https://public.example.com",
            whatsapp_webhook_secret_configured=True,
        )

    def test_registry_caches_status_and_endpoint_services(self) -> None:
        registry = self._make_registry()
        self.assertIs(registry.autopilot_event_service(), registry.autopilot_event_service())
        self.assertIs(registry.autopilot_status_service(), registry.autopilot_status_service())
        self.assertIs(registry.autopilot_endpoint_service(), registry.autopilot_endpoint_service())


if __name__ == "__main__":
    unittest.main()

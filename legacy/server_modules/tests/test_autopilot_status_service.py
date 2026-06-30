import unittest

from server_modules.connectors.autopilot_profile_service import AutopilotProfileService
from server_modules.connectors.autopilot_status_service import AutopilotStatusService


class AutopilotStatusServiceTests(unittest.TestCase):
    def _make_service(self, **overrides) -> AutopilotStatusService:
        return AutopilotStatusService(
            normalize_workspace_id=overrides.pop("normalize_workspace_id", lambda value: str(value or "")),
            telegram_snapshot=overrides.pop("telegram_snapshot", lambda: {"enabled": True, "connectors": {}}),
            telegram_list_entries=overrides.pop("telegram_list_entries", lambda: []),
            resolve_telegram_profile=overrides.pop("resolve_telegram_profile", lambda entry: {"id": "tg", "prefix": "/empyralis", "require_prefix": True}),
            telegram_webhook_path=overrides.pop("telegram_webhook_path", "/channels/telegram/webhook/{connector_id}"),
            telegram_public_base_url=overrides.pop("telegram_public_base_url", ""),
            telegram_webhook_secret_configured=overrides.pop("telegram_webhook_secret_configured", False),
            telegram_delivery_mode=overrides.pop("telegram_delivery_mode", "polling"),
            whatsapp_snapshot=overrides.pop("whatsapp_snapshot", lambda: {"enabled": True, "connectors": {}}),
            whatsapp_list_entries=overrides.pop("whatsapp_list_entries", lambda: []),
            resolve_whatsapp_profile=overrides.pop("resolve_whatsapp_profile", lambda entry: {"id": "wa", "prefix": "/empyralis", "require_prefix": False}),
            whatsapp_webhook_path=overrides.pop("whatsapp_webhook_path", "/channels/whatsapp/twilio/webhook"),
            whatsapp_public_base_url=overrides.pop("whatsapp_public_base_url", ""),
            whatsapp_webhook_secret_configured=overrides.pop("whatsapp_webhook_secret_configured", False),
        )

    def test_telegram_status_payload_maps_connectors_and_snapshot(self) -> None:
        service = self._make_service(
            telegram_snapshot=lambda: {
                "enabled": True,
                "active": True,
                "thread_alive": True,
                "started_at": "2026-04-04T00:00:00Z",
                "connectors": {
                    "conn-1": {
                        "last_update_id": 7,
                        "allow_from": ["alice"],
                        "dropped_sender_count": 2,
                    }
                },
            },
            telegram_list_entries=lambda: [{"id": "conn-1", "label": "Telegram 1", "workspace_id": "ws-1"}],
        )
        payload = service.telegram_status_payload()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["connectors"][0]["id"], "conn-1")
        self.assertEqual(payload["connectors"][0]["last_update_id"], 7)
        self.assertEqual(payload["connectors"][0]["allow_from"], ["alice"])

    def test_telegram_status_payload_reports_fallback_when_polling_mode_is_enabled(self) -> None:
        service = self._make_service(
            telegram_snapshot=lambda: {"enabled": True, "connectors_seen": 1, "connectors": {}},
            telegram_list_entries=lambda: [{"id": "tg-1", "label": "Telegram 1", "workspace_id": "ws-1"}],
            telegram_public_base_url="https://public.example.com",
            telegram_webhook_secret_configured=True,
            telegram_delivery_mode="polling",
        )

        payload = service.telegram_status_payload()

        self.assertEqual(payload["status"], "degraded")
        self.assertEqual(payload["autopilot"]["channel_state"], "degraded")
        self.assertEqual(payload["webhook"]["status"], "fallback")
        self.assertEqual(payload["webhook"]["delivery_mode"], "polling")

    def test_telegram_status_payload_reports_live_for_public_webhook_contract(self) -> None:
        service = self._make_service(
            telegram_snapshot=lambda: {"enabled": True, "connectors_seen": 1, "connectors": {}},
            telegram_list_entries=lambda: [{
                "id": "tg-1",
                "label": "Telegram 1",
                "workspace_id": "ws-1",
                "metadata": {
                    "telegram_webhook_registration": {
                        "status": "registered",
                        "webhook_url": "https://public.example.com/channels/telegram/webhook/tg-1",
                    },
                },
            }],
            telegram_public_base_url="https://public.example.com",
            telegram_webhook_secret_configured=True,
            telegram_delivery_mode="webhook",
        )

        payload = service.telegram_status_payload()

        self.assertEqual(payload["status"], "live")
        self.assertEqual(payload["autopilot"]["channel_state"], "live")
        self.assertEqual(payload["webhook"]["status"], "live")
        self.assertTrue(payload["webhook"]["telegram_reachable"])
        self.assertEqual(payload["connectors"][0]["webhook_url"], "https://public.example.com/channels/telegram/webhook/tg-1")

    def test_whatsapp_status_payload_maps_connectors_and_vault_error(self) -> None:
        service = self._make_service(
            whatsapp_snapshot=lambda: {
                "enabled": True,
                "active": True,
                "thread_alive": True,
                "connectors": {
                    "wa-1": {
                        "last_action": "run",
                        "last_message_sid": "SM123",
                    }
                },
            },
            whatsapp_list_entries=lambda: (_ for _ in ()).throw(RuntimeError("vault down")),
        )
        payload = service.whatsapp_status_payload()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["vault_error"], "vault down")
        self.assertEqual(payload["connectors"], [])

    def test_telegram_status_payload_degrades_when_default_profile_missing(self) -> None:
        profile_service = AutopilotProfileService(
            default_chat_prefix="/empyralis",
            bool_from_any=lambda value, default=False: default if value is None else str(value).lower() in {"1", "true", "yes", "on"},
            telegram_default_profile="assistant",
            telegram_default_prefix="/empyralis",
            telegram_default_require_prefix=False,
            telegram_profile_catalog={"ops": {"label": "Ops", "allow_free_text": False, "allow_status": True, "allow_help": True}},
            whatsapp_default_profile="assistant",
            whatsapp_default_prefix="/empyralis",
            whatsapp_default_require_prefix=False,
            whatsapp_profile_catalog={"assistant": {"label": "Assistant", "allow_free_text": True, "allow_status": True, "allow_help": True}},
        )
        service = self._make_service(
            telegram_snapshot=lambda: {"enabled": True, "connectors": {}},
            telegram_list_entries=lambda: [{"id": "conn-1", "label": "Telegram 1", "workspace_id": "ws-1"}],
            resolve_telegram_profile=profile_service.resolve_telegram_profile,
        )

        payload = service.telegram_status_payload()

        self.assertEqual(payload["status"], "degraded")
        self.assertEqual(payload["autopilot"]["default_profile_status"], "degraded")
        self.assertEqual(payload["autopilot"]["default_profile_issue_code"], "telegram_autopilot_default_profile_missing")
        self.assertEqual(payload["connectors"][0]["profile_id"], "ops")

    def test_telegram_status_payload_returns_setup_needed_for_empty_catalog(self) -> None:
        profile_service = AutopilotProfileService(
            default_chat_prefix="/empyralis",
            bool_from_any=lambda value, default=False: default if value is None else str(value).lower() in {"1", "true", "yes", "on"},
            telegram_default_profile="assistant",
            telegram_default_prefix="/empyralis",
            telegram_default_require_prefix=False,
            telegram_profile_catalog={},
            whatsapp_default_profile="assistant",
            whatsapp_default_prefix="/empyralis",
            whatsapp_default_require_prefix=False,
            whatsapp_profile_catalog={"assistant": {"label": "Assistant", "allow_free_text": True, "allow_status": True, "allow_help": True}},
        )
        service = self._make_service(
            telegram_snapshot=lambda: {"enabled": True, "connectors": {}},
            telegram_list_entries=lambda: [{"id": "conn-1", "label": "Telegram 1", "workspace_id": "ws-1"}],
            resolve_telegram_profile=profile_service.resolve_telegram_profile,
        )

        payload = service.telegram_status_payload()

        self.assertEqual(payload["status"], "setup_needed")
        self.assertEqual(payload["autopilot"]["default_profile_status"], "setup_needed")
        self.assertEqual(payload["autopilot"]["default_profile_issue_code"], "telegram_autopilot_profile_catalog_missing")
        self.assertEqual(payload["connectors"][0]["profile_status"], "setup_needed")

    def test_whatsapp_status_payload_reports_disabled_state(self) -> None:
        service = self._make_service(
            whatsapp_snapshot=lambda: {"enabled": False, "connectors_seen": 0, "connectors": {}},
            whatsapp_webhook_path="/channels/whatsapp/twilio/webhook",
            whatsapp_public_base_url="https://public.example.com",
            whatsapp_webhook_secret_configured=True,
        )

        payload = service.whatsapp_status_payload()

        self.assertEqual(payload["status"], "disabled")
        self.assertEqual(payload["autopilot"]["channel_state"], "disabled")
        self.assertEqual(payload["webhook"]["status"], "disabled")

    def test_whatsapp_status_payload_reports_setup_needed_for_localhost_webhook_base(self) -> None:
        service = self._make_service(
            whatsapp_snapshot=lambda: {"enabled": True, "connectors_seen": 1, "connectors": {}},
            whatsapp_list_entries=lambda: [{"id": "wa-1", "label": "WhatsApp 1", "workspace_id": "ws-1"}],
            whatsapp_webhook_path="/channels/whatsapp/twilio/webhook",
            whatsapp_public_base_url="http://127.0.0.1:8001",
            whatsapp_webhook_secret_configured=True,
        )

        payload = service.whatsapp_status_payload()

        self.assertEqual(payload["status"], "setup_needed")
        self.assertEqual(payload["autopilot"]["channel_state"], "setup_needed")
        self.assertEqual(payload["webhook"]["status"], "setup_needed")
        self.assertEqual(payload["webhook"]["issue_code"], "whatsapp_webhook_public_base_unreachable")
        self.assertIn("localhost", payload["webhook"]["guidance"])

    def test_whatsapp_status_payload_reports_live_for_public_webhook_contract(self) -> None:
        service = self._make_service(
            whatsapp_snapshot=lambda: {"enabled": True, "connectors_seen": 1, "connectors": {}},
            whatsapp_list_entries=lambda: [{"id": "wa-1", "label": "WhatsApp 1", "workspace_id": "ws-1"}],
            whatsapp_webhook_path="/channels/whatsapp/twilio/webhook",
            whatsapp_public_base_url="https://public.example.com",
            whatsapp_webhook_secret_configured=True,
        )

        payload = service.whatsapp_status_payload()

        self.assertEqual(payload["status"], "live")
        self.assertEqual(payload["autopilot"]["channel_state"], "live")
        self.assertEqual(payload["webhook"]["status"], "live")
        self.assertTrue(payload["webhook"]["twilio_reachable"])


if __name__ == "__main__":
    unittest.main()

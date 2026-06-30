import unittest

from server_modules.connectors.autopilot_bridge_registry_service import AutopilotBridgeRegistryService


class _FakeSharedRegistry:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        type(self).instances.append(self)


class _FakeBridge:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class AutopilotBridgeRegistryServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        _FakeSharedRegistry.instances.clear()

    def _service(self) -> AutopilotBridgeRegistryService:
        return AutopilotBridgeRegistryService(
            normalize_workspace_id=lambda value: str(value or "default"),
            append_channel_event=lambda **kwargs: kwargs,
            utc_now_iso=lambda: "2026-04-05T00:00:00Z",
            truncate_one_line=lambda text, limit: str(text)[:limit],
            json_safe=lambda value: value,
            dead_letter_lock=object(),
            read_dead_letter_json=lambda path, default: default,
            write_dead_letter_json=lambda path, payload: payload,
            dead_letter_file="/tmp/dead.json",
            dead_letter_limit=100,
            collapse_whitespace=lambda text: " ".join(str(text).split()),
            telegram_snapshot=lambda: {"telegram": True},
            telegram_list_entries=lambda: [{"id": "tg-1"}],
            resolve_telegram_profile=lambda entry: {"profile": entry.get("id")},
            telegram_webhook_path="/channels/telegram/webhook/{connector_id}",
            telegram_public_base_url="https://public.example.com",
            telegram_webhook_secret_configured=True,
            telegram_delivery_mode="webhook",
            whatsapp_snapshot=lambda: {"whatsapp": True},
            whatsapp_list_entries=lambda: [{"id": "wa-1"}],
            resolve_whatsapp_profile=lambda entry: {"profile": entry.get("id")},
            init_runtime=lambda: None,
            event_service=lambda: {"event": True},
            telegram_terminal_service=lambda: {"terminal": True},
            telegram_supervisor_service=lambda: {"supervisor": True},
            autopilot_status_service=lambda: {"status": True},
            autopilot_endpoint_service=lambda: {"endpoint": True},
            telegram_enabled=True,
            telegram_default_profile="ops",
            telegram_catalog={"ops": {}},
            whatsapp_enabled=True,
            whatsapp_default_profile="support",
            whatsapp_catalog={"support": {}},
            whatsapp_webhook_path="/channels/whatsapp/twilio/webhook",
            whatsapp_public_base_url="https://public.example.com",
            whatsapp_webhook_secret_configured=True,
            telegram_state_service=lambda: {"state": True},
            whatsapp_state_service=lambda: {"state": True},
            telegram_runtime_service=lambda: {"runtime": True},
            telegram_state={},
            telegram_lock=object(),
            safe_path_token=lambda value: f"safe:{value}",
            build_goal_with_profile=lambda goal, profile: f"{goal}|{profile.get('project', '')}",
            workspace_connector_context=lambda goal, workspace_id, current_connector_id: {
                "goal": goal,
                "workspace_id": workspace_id,
                "connector_id": current_connector_id,
            },
            extract_message=lambda update: update.get("message"),
            build_goal_with_attachments=lambda goal, attachments: f"{goal}|{len(attachments)}",
            route_message=lambda raw_text, profile: {"text": raw_text, "profile": profile.get("id")},
            telegram_parse_update=lambda raw: {"update_id": 1, "raw": raw.decode("utf-8")},
            telegram_webhook_auth_result=lambda **kwargs: {"status_code": 200, "kwargs": kwargs},
            telegram_handle_inbound=lambda connector_id, update: {"connector_id": connector_id, "update": update},
            parse_form_urlencoded=lambda raw: {"Body": raw.decode("utf-8")},
            webhook_auth_result=lambda **kwargs: {"status_code": 200, "kwargs": kwargs},
            resolve_inbound_connector=lambda payload: {"connector_id": "conn-1"},
            validate_webhook_signature=lambda request_url, form, signature, auth_token: True,
            ingest_webhook=lambda payload, matched=None: {"payload": payload, "matched": matched},
            twiml_response=lambda text: {"twiml": text},
            error_response=lambda status_code, content: {"status_code": status_code, "content": content},
            telegram_webhook_enabled=True,
            telegram_configured_webhook_secret="telegram-secret",
            webhook_enabled=True,
            configured_webhook_secret="secret-1",
            shared_registry_class=_FakeSharedRegistry,
            event_bridge_class=_FakeBridge,
            terminal_bridge_class=_FakeBridge,
            state_bridge_class=_FakeBridge,
            compatibility_bridge_class=_FakeBridge,
            telegram_webhook_bridge_class=_FakeBridge,
            webhook_bridge_class=_FakeBridge,
        )

    def test_registry_caches_all_bridges(self) -> None:
        service = self._service()

        shared_first = service.shared_service_registry()
        self.assertIs(shared_first, service.shared_service_registry())
        self.assertEqual(len(_FakeSharedRegistry.instances), 1)

        self.assertIs(service.event_bridge_service(), service.event_bridge_service())
        self.assertIs(service.terminal_bridge_service(), service.terminal_bridge_service())
        self.assertIs(service.state_bridge_service(), service.state_bridge_service())
        self.assertIs(service.compatibility_bridge_service(), service.compatibility_bridge_service())
        self.assertIs(service.telegram_webhook_bridge_service(), service.telegram_webhook_bridge_service())
        self.assertIs(service.webhook_bridge_service(), service.webhook_bridge_service())

        self.assertEqual(shared_first.kwargs["dead_letter_limit"], 100)
        self.assertEqual(service.compatibility_bridge_service().kwargs["safe_path_token"]("abc"), "safe:abc")
        self.assertEqual(service.telegram_webhook_bridge_service().kwargs["configured_secret"], "telegram-secret")
        self.assertEqual(service.webhook_bridge_service().kwargs["configured_secret"], "secret-1")
        self.assertIsNotNone(service.webhook_bridge_service().kwargs["resolve_inbound_connector"])


if __name__ == "__main__":
    unittest.main()

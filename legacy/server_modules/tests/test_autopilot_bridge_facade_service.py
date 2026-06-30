import unittest

from server_modules.connectors.autopilot_bridge_facade_service import AutopilotBridgeFacadeService


class _FakeStateService:
    def snapshot(self, *, include_connectors=False):
        return {"include_connectors": include_connectors}

    def list_connector_entries(self, workspace_id):
        return [{"workspace_id": workspace_id}]

    def connector_match(self, account_sid, inbound_from, inbound_to):
        return {
            "connector_id": "wa-1",
            "workspace_id": "default",
            "secret": {"auth_token": "twilio-auth"},
            "account_sid": account_sid,
            "inbound_from": inbound_from,
            "inbound_to": inbound_to,
        }


class _FakeRegistryDeps:
    def telegram_autopilot_state_service(self):
        return _FakeStateService()

    def whatsapp_autopilot_state_service(self):
        return _FakeStateService()

    def telegram_autopilot_runtime_service(self):
        return {"runtime": True}

    def telegram_autopilot_supervisor_service(self):
        return {"supervisor": True}

    def whatsapp_ingress_service(self):
        class _Ingress:
            def ingest_webhook(self, payload, *, matched=None):
                return {"payload": payload, "matched": matched}

        return _Ingress()

    def telegram_webhook_service(self):
        class _Webhook:
            def parse_update(self, raw):
                return {"update_id": 1, "raw": raw.decode("utf-8")}

            def handle_webhook(self, *, connector_id, update):
                return {"connector_id": connector_id, "update": update}

        return _Webhook()

    def whatsapp_transport_service(self):
        class _Transport:
            def twiml_response(self, text):
                return {"twiml": text}

            def validate_webhook_signature(self, **kwargs):
                return True

        return _Transport()


class _FakeProfileService:
    def resolve_telegram_profile(self, entry):
        return {"telegram": entry}

    def resolve_whatsapp_profile(self, entry):
        return {"whatsapp": entry}


class _FakeSharedServiceRegistry:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        type(self).instances.append(self)

    def autopilot_status_service(self):
        return {"status": True}

    def autopilot_endpoint_service(self):
        class _Endpoint:
            def telegram_webhook_auth_result(self, **kwargs):
                return kwargs

            def whatsapp_webhook_auth_result(self, **kwargs):
                return kwargs

        return _Endpoint()

    def autopilot_event_service(self):
        return {"event": True}


class _FakeDirectEventBridgeService:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        type(self).instances.append(self)


class _FakeDirectTerminalBridgeService:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        type(self).instances.append(self)


class _FakeDirectStateBridgeService:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        type(self).instances.append(self)


class _FakeDirectCompatibilityBridgeService:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        type(self).instances.append(self)


class _FakeDirectTelegramWebhookBridgeService:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        type(self).instances.append(self)


class _FakeDirectWhatsAppWebhookBridgeService:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        type(self).instances.append(self)


class AutopilotBridgeFacadeServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        _FakeSharedServiceRegistry.instances.clear()
        _FakeDirectEventBridgeService.instances.clear()
        _FakeDirectTerminalBridgeService.instances.clear()
        _FakeDirectStateBridgeService.instances.clear()
        _FakeDirectCompatibilityBridgeService.instances.clear()
        _FakeDirectTelegramWebhookBridgeService.instances.clear()
        _FakeDirectWhatsAppWebhookBridgeService.instances.clear()

    def test_facade_builds_direct_bridge_services_without_registry_shell(self) -> None:
        service = AutopilotBridgeFacadeService(
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
            telegram_workspace_id="workspace-123",
            whatsapp_workspace_id="workspace-456",
            telegram_service_registry=lambda: _FakeRegistryDeps(),
            whatsapp_service_registry=lambda: _FakeRegistryDeps(),
            autopilot_profile_service=lambda: _FakeProfileService(),
            init_runtime=lambda: None,
            telegram_terminal_service=lambda: {"terminal": True},
            telegram_enabled_getter=lambda: True,
            telegram_default_profile_getter=lambda: "ops",
            telegram_catalog_getter=lambda: {"ops": {}},
            whatsapp_enabled_getter=lambda: True,
            whatsapp_default_profile_getter=lambda: "support",
            whatsapp_catalog_getter=lambda: {"support": {}},
            telegram_state_getter=lambda: {"active": True},
            telegram_lock_getter=lambda: object(),
            safe_path_token=lambda value: f"safe:{value}",
            build_goal_with_profile=lambda goal, profile: f"{goal}|{profile}",
            workspace_connector_context=lambda goal, workspace_id, connector_id: {
                "goal": goal,
                "workspace_id": workspace_id,
                "connector_id": connector_id,
            },
            extract_message=lambda update: update.get("message"),
            build_goal_with_attachments=lambda goal, attachments: f"{goal}|{len(attachments)}",
            route_message=lambda text, profile: {"text": text, "profile": profile},
            parse_form_urlencoded=lambda raw: {"Body": raw.decode("utf-8")},
            error_response=lambda status_code, content: {"status_code": status_code, "content": content},
            telegram_delivery_mode_getter=lambda: "webhook",
            telegram_configured_webhook_secret_getter=lambda: "telegram-secret",
            telegram_public_base_url_getter=lambda: "https://public.example.com",
            webhook_enabled_getter=lambda: True,
            configured_webhook_secret_getter=lambda: "secret-1",
            whatsapp_public_base_url_getter=lambda: "https://public.example.com",
            shared_registry_class=_FakeSharedServiceRegistry,
            event_bridge_class=_FakeDirectEventBridgeService,
            terminal_bridge_class=_FakeDirectTerminalBridgeService,
            state_bridge_class=_FakeDirectStateBridgeService,
            compatibility_bridge_class=_FakeDirectCompatibilityBridgeService,
            telegram_webhook_bridge_class=_FakeDirectTelegramWebhookBridgeService,
            webhook_bridge_class=_FakeDirectWhatsAppWebhookBridgeService,
        )

        shared_registry = service.shared_service_registry()
        event_bridge = service.event_bridge_service()
        terminal_bridge = service.terminal_bridge_service()
        state_bridge = service.state_bridge_service()
        compatibility_bridge = service.compatibility_bridge_service()
        telegram_webhook_bridge = service.telegram_webhook_bridge_service()
        whatsapp_webhook_bridge = service.webhook_bridge_service()

        self.assertIs(shared_registry, service.shared_service_registry())
        self.assertIs(event_bridge, service.event_bridge_service())
        self.assertIs(terminal_bridge, service.terminal_bridge_service())
        self.assertIs(state_bridge, service.state_bridge_service())
        self.assertIs(compatibility_bridge, service.compatibility_bridge_service())
        self.assertIs(telegram_webhook_bridge, service.telegram_webhook_bridge_service())
        self.assertIs(whatsapp_webhook_bridge, service.webhook_bridge_service())
        self.assertEqual(len(_FakeSharedServiceRegistry.instances), 1)
        self.assertEqual(len(_FakeDirectEventBridgeService.instances), 1)
        self.assertEqual(len(_FakeDirectTerminalBridgeService.instances), 1)
        self.assertEqual(len(_FakeDirectStateBridgeService.instances), 1)
        self.assertEqual(len(_FakeDirectCompatibilityBridgeService.instances), 1)
        self.assertEqual(len(_FakeDirectTelegramWebhookBridgeService.instances), 1)
        self.assertEqual(len(_FakeDirectWhatsAppWebhookBridgeService.instances), 1)
        self.assertEqual(shared_registry.kwargs["telegram_delivery_mode"], "webhook")
        self.assertEqual(terminal_bridge.kwargs["telegram_default_profile"], "ops")
        self.assertEqual(state_bridge.kwargs["telegram_state"], {"active": True})
        self.assertEqual(telegram_webhook_bridge.kwargs["configured_secret"], "telegram-secret")
        self.assertEqual(whatsapp_webhook_bridge.kwargs["configured_secret"], "secret-1")


if __name__ == "__main__":
    unittest.main()

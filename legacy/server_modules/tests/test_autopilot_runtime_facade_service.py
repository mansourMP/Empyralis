import asyncio
import types
import unittest

from server_modules.connectors.autopilot_runtime_facade_service import AutopilotRuntimeFacadeService


class _FakeStateBridge:
    def __init__(self) -> None:
        self.connectors_seen = None
        self.started_at = None

    def load_telegram_autopilot_state(self) -> None:
        self.telegram_loaded = True

    def load_whatsapp_autopilot_state(self) -> None:
        self.whatsapp_loaded = True

    def telegram_autopilot_snapshot(self):
        return {"telegram": True}

    def whatsapp_autopilot_snapshot(self):
        return {"whatsapp": True}

    def whatsapp_autopilot_activate(self) -> None:
        self.whatsapp_activated = True

    def telegram_increment_processed_updates(self) -> None:
        self.telegram_incremented = True

    def telegram_set_connectors_seen(self, count: int) -> None:
        self.connectors_seen = count

    def mark_telegram_autopilot_started(self, started_at: str) -> None:
        self.started_at = started_at


class _FakeEventBridge:
    def __init__(self) -> None:
        self.calls = []

    def record_channel_event(self, **kwargs):
        self.calls.append(("record", kwargs))
        return {"event": kwargs["event_type"]}

    def append_channel_dead_letter(self, **kwargs) -> None:
        self.calls.append(("dead", kwargs))

    def record_channel_event_throttled(self, **kwargs):
        self.calls.append(("throttled", kwargs))
        return True


class _FakeTerminalBridge:
    async def handle_telegram_send_message(self, **kwargs):
        return {"kind": "send", **kwargs}

    async def handle_telegram_autopilot_test_message(self, **kwargs):
        return {"kind": "test", **kwargs}

    def run_telegram_autopilot_forever(self) -> None:
        self.forever = True

    async def telegram_status_payload(self):
        return {"telegram": True}

    async def whatsapp_status_payload(self):
        return {"whatsapp": True}

    async def autopilot_profiles_payload(self):
        return {"profiles": True}


class _FakeWebhookBridge:
    def handle_webhook(self, **kwargs):
        return {"webhook": kwargs}


class _FakeTelegramWebhookBridge:
    def handle_webhook(self, **kwargs):
        return {"telegram_webhook": kwargs}


class AutopilotRuntimeFacadeServiceTests(unittest.TestCase):
    def _service(self, *, namespace=None, server=None):
        namespace = namespace or {"existing": "value"}
        state_bridge = _FakeStateBridge()
        event_bridge = _FakeEventBridge()
        terminal_bridge = _FakeTerminalBridge()
        webhook_bridge = _FakeWebhookBridge()
        telegram_webhook_bridge = _FakeTelegramWebhookBridge()
        server_box = {"value": server}
        imported = types.SimpleNamespace(
            EXPORTED_FLAG=True,
            TELEGRAM_AUTOPILOT_THREAD="thread-1",
            WHATSAPP_AUTOPILOT_STATE={"active": True},
        )
        service = AutopilotRuntimeFacadeService(
            global_namespace=namespace,
            sync_server_globals=("TELEGRAM_AUTOPILOT_THREAD", "WHATSAPP_AUTOPILOT_STATE"),
            server_getter=lambda: server_box["value"],
            server_setter=lambda value: server_box.__setitem__("value", value),
            import_server=lambda: imported,
            state_bridge_service=lambda: state_bridge,
            event_bridge_service=lambda: event_bridge,
            terminal_bridge_service=lambda: terminal_bridge,
            webhook_bridge_service=lambda: webhook_bridge,
            telegram_webhook_bridge_service=lambda: telegram_webhook_bridge,
        )
        return (
            service,
            namespace,
            server_box,
            imported,
            state_bridge,
            event_bridge,
            terminal_bridge,
            webhook_bridge,
            telegram_webhook_bridge,
        )

    def test_init_runtime_imports_server_once_and_syncs_selected_globals(self) -> None:
        service, namespace, server_box, imported, *_ = self._service()

        service.init_runtime()
        service.init_runtime()

        self.assertIs(server_box["value"], imported)
        self.assertTrue(namespace["EXPORTED_FLAG"])
        self.assertEqual(namespace["TELEGRAM_AUTOPILOT_THREAD"], "thread-1")
        self.assertEqual(namespace["WHATSAPP_AUTOPILOT_STATE"], {"active": True})

    def test_state_wrappers_delegate_to_state_bridge(self) -> None:
        service, _namespace, _server_box, _imported, state_bridge, _event_bridge, _terminal_bridge, _webhook_bridge, _telegram_webhook_bridge = self._service()

        service.load_telegram_autopilot_state()
        service.load_whatsapp_autopilot_state()
        service.whatsapp_autopilot_activate()
        service.telegram_increment_processed_updates()
        service.telegram_set_connectors_seen(5)
        service.mark_telegram_autopilot_started("2026-04-05T00:00:00Z")

        self.assertTrue(state_bridge.telegram_loaded)
        self.assertTrue(state_bridge.whatsapp_loaded)
        self.assertTrue(state_bridge.whatsapp_activated)
        self.assertTrue(state_bridge.telegram_incremented)
        self.assertEqual(state_bridge.connectors_seen, 5)
        self.assertEqual(state_bridge.started_at, "2026-04-05T00:00:00Z")
        self.assertEqual(service.telegram_autopilot_snapshot(), {"telegram": True})
        self.assertEqual(service.whatsapp_autopilot_snapshot(), {"whatsapp": True})

    def test_event_wrappers_delegate_to_event_bridge(self) -> None:
        service, _namespace, _server_box, _imported, _state_bridge, event_bridge, _terminal_bridge, _webhook_bridge, _telegram_webhook_bridge = self._service()

        event_payload = service.record_channel_event(
            channel="telegram",
            direction="inbound",
            event_type="message",
            text="hello",
        )
        service.append_channel_dead_letter(
            channel="telegram",
            direction="system",
            event_type="error",
            reason="timeout",
        )
        throttled = service.record_channel_event_throttled(
            channel="telegram",
            direction="system",
            event_type="warning",
            text="slow",
            dedupe_seconds=10.0,
            record_event_func=lambda **kwargs: kwargs,
        )

        self.assertEqual(event_payload, {"event": "message"})
        self.assertTrue(throttled)
        self.assertEqual([kind for kind, _kwargs in event_bridge.calls], ["record", "dead", "throttled"])

    def test_terminal_and_webhook_wrappers_delegate(self) -> None:
        service, _namespace, _server_box, _imported, _state_bridge, _event_bridge, terminal_bridge, _webhook_bridge, _telegram_webhook_bridge = self._service()

        send_payload = asyncio.run(service.handle_telegram_send_message(text="hello"))
        test_payload = asyncio.run(service.handle_telegram_autopilot_test_message(text="world"))
        webhook_payload = asyncio.run(
            service.handle_whatsapp_webhook(
                raw_body=b"Body=hello",
                request_url="https://public.example.com/channels/whatsapp/twilio/webhook",
                twilio_signature="sig-1",
                query_secret="secret-1",
                header_secret="secret-2",
            )
        )
        telegram_webhook_payload = asyncio.run(
            service.handle_telegram_webhook(
                raw_body=b'{"update_id": 7}',
                connector_id="tg-1",
                header_secret="telegram-secret",
            )
        )
        service.run_telegram_autopilot_forever()
        telegram_status = asyncio.run(service.telegram_status_payload())
        whatsapp_status = asyncio.run(service.whatsapp_status_payload())
        profiles_payload = asyncio.run(service.autopilot_profiles_payload())

        self.assertEqual(send_payload["kind"], "send")
        self.assertEqual(test_payload["kind"], "test")
        self.assertEqual(webhook_payload["webhook"]["request_url"], "https://public.example.com/channels/whatsapp/twilio/webhook")
        self.assertEqual(webhook_payload["webhook"]["twilio_signature"], "sig-1")
        self.assertEqual(webhook_payload["webhook"]["query_secret"], "secret-1")
        self.assertEqual(telegram_webhook_payload["telegram_webhook"]["connector_id"], "tg-1")
        self.assertTrue(terminal_bridge.forever)
        self.assertEqual(telegram_status, {"telegram": True})
        self.assertEqual(whatsapp_status, {"whatsapp": True})
        self.assertEqual(profiles_payload, {"profiles": True})


if __name__ == "__main__":
    unittest.main()

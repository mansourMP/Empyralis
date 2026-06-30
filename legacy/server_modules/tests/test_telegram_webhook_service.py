import unittest

from server_modules.connectors.telegram_webhook_service import TelegramWebhookService


class _InboundContextServiceStub:
    def __init__(self, extracted_message=None) -> None:
        self.extracted_message = extracted_message or {
            "handled": True,
            "update_id": 7,
            "message": {"text": "hello"},
            "chat": {"id": "chat-1"},
            "sender": {"id": "user-1"},
        }
        self.calls = []

    def extract_inbound_message(self, **kwargs):
        self.calls.append(kwargs)
        return dict(self.extracted_message)


class _PollDispatchServiceStub:
    def __init__(self, result=None) -> None:
        self.result = result or {
            "processed": True,
            "action": "run",
            "run_id": "run-1",
            "chat_id": "chat-1",
            "workspace_id": "ws-1",
        }
        self.calls = []

    def handle_update(self, **kwargs):
        self.calls.append(kwargs)
        return dict(self.result)


class _PollStateServiceStub:
    def __init__(self) -> None:
        self.processed_calls = []
        self.completion_calls = []

    def record_processed_update(self, **kwargs):
        self.processed_calls.append(kwargs)

    def record_poll_completion(self, **kwargs):
        self.completion_calls.append(kwargs)


class _PollCycleServiceStub:
    def __init__(self) -> None:
        self.error_calls = []

    def handle_connector_error(self, **kwargs):
        self.error_calls.append(kwargs)


class TelegramWebhookServiceTests(unittest.TestCase):
    def _make_service(self, **overrides):
        inbound = overrides.pop("inbound", _InboundContextServiceStub())
        dispatch = overrides.pop("dispatch", _PollDispatchServiceStub())
        state = overrides.pop("state", _PollStateServiceStub())
        cycle = overrides.pop("cycle", _PollCycleServiceStub())
        service = TelegramWebhookService(
            default_workspace_id="default",
            resolve_workspace_scope=overrides.pop("resolve_workspace_scope", lambda entry, fallback, label: "ws-1"),
            get_connector_entry=overrides.pop(
                "get_connector_entry",
                lambda connector_id: {"id": connector_id, "label": "Telegram", "workspace_id": "ws-1"},
            ),
            resolve_profile=overrides.pop("resolve_profile", lambda entry: {"id": "assistant"}),
            resolve_allow_from=overrides.pop("resolve_allow_from", lambda entry: ["user-1"]),
            connector_state=overrides.pop("connector_state", lambda connector_id: {"last_update_id": 0}),
            resolve_secret=overrides.pop("resolve_secret", lambda entry: {"bot_token": "token", "chat_id": "chat-1"}),
            inbound_context_service=lambda: inbound,
            poll_dispatch_service=lambda: dispatch,
            poll_state_service=lambda: state,
            poll_cycle_service=lambda: cycle,
        )
        return service, inbound, dispatch, state, cycle

    def test_handle_webhook_reuses_dispatch_and_records_processed_update(self) -> None:
        service, _inbound, dispatch, state, _cycle = self._make_service()

        result = service.handle_webhook(connector_id="tg-1", update={"update_id": 7, "message": {}})

        self.assertTrue(result["handled"])
        self.assertTrue(result["processed"])
        self.assertEqual(result["run_id"], "run-1")
        self.assertEqual(len(dispatch.calls), 1)
        self.assertEqual(dispatch.calls[0]["connector_id"], "tg-1")
        self.assertEqual(len(state.processed_calls), 1)
        self.assertEqual(state.processed_calls[0]["connector_id"], "tg-1")

    def test_handle_webhook_short_circuits_duplicate_update(self) -> None:
        service, _inbound, dispatch, state, _cycle = self._make_service(
            connector_state=lambda connector_id: {"last_update_id": 7},
        )

        result = service.handle_webhook(connector_id="tg-1", update={"update_id": 7, "message": {}})

        self.assertTrue(result["duplicate"])
        self.assertFalse(result["processed"])
        self.assertEqual(dispatch.calls, [])
        self.assertEqual(state.processed_calls, [])

    def test_parse_update_rejects_invalid_json(self) -> None:
        service, *_ = self._make_service()

        with self.assertRaisesRegex(ValueError, "invalid JSON"):
            service.parse_update(b"{invalid")


if __name__ == "__main__":
    unittest.main()

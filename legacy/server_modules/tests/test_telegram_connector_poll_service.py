import unittest

from server_modules.connectors.telegram_connector_poll_service import TelegramConnectorPollService


class _PollCycleStub:
    def __init__(self, *, begin_result=None):
        self.begin_result = begin_result or {
            "skipped": False,
            "approval_state_patch": {"approval_pending": True},
            "updates": [{"update_id": 4}, {"update_id": 5}],
        }
        self.begin_calls = []
        self.complete_calls = []
        self.error_calls = []

    def begin_poll(self, **kwargs):
        self.begin_calls.append(kwargs)
        return self.begin_result

    def complete_poll(self, **kwargs):
        self.complete_calls.append(kwargs)

    def handle_connector_error(self, **kwargs):
        self.error_calls.append(kwargs)


class _InboundStub:
    def __init__(self, messages=None):
        self.messages = list(messages or [])
        self.calls = []

    def extract_inbound_message(self, **kwargs):
        self.calls.append(kwargs)
        if self.messages:
            return self.messages.pop(0)
        return {"handled": False, "update_id": 0}


class _DispatchStub:
    def __init__(self, results=None):
        self.results = list(results or [])
        self.calls = []

    def handle_update(self, **kwargs):
        self.calls.append(kwargs)
        if self.results:
            return self.results.pop(0)
        return {"processed": False}


class _PollStateStub:
    def __init__(self):
        self.processed = []

    def record_processed_update(self, **kwargs):
        self.processed.append(kwargs)


class TelegramConnectorPollServiceTests(unittest.TestCase):
    def _make_service(self, **overrides) -> TelegramConnectorPollService:
        poll_cycle = overrides.pop("poll_cycle", _PollCycleStub())
        inbound = overrides.pop("inbound", _InboundStub())
        dispatch = overrides.pop("dispatch", _DispatchStub())
        poll_state = overrides.pop("poll_state", _PollStateStub())
        return TelegramConnectorPollService(
            default_workspace_id="default",
            normalize_workspace_id=overrides.pop("normalize_workspace_id", lambda value: str(value or "")),
            resolve_workspace_scope=overrides.pop(
                "resolve_workspace_scope",
                lambda entry, fallback_workspace_id, detail_prefix: str(entry.get("workspace_id") or fallback_workspace_id or ""),
            ),
            resolve_profile=overrides.pop("resolve_profile", lambda entry: {"id": "profile-1"}),
            resolve_allow_from=overrides.pop("resolve_allow_from", lambda entry: ["alice"]),
            connector_state=overrides.pop("connector_state", lambda connector_id: {"last_update_id": 3}),
            resolve_secret=overrides.pop(
                "resolve_secret",
                lambda entry: {"bot_token": "token", "chat_id": "chat-1"},
            ),
            poll_cycle_service=lambda: poll_cycle,
            inbound_context_service=lambda: inbound,
            poll_dispatch_service=lambda: dispatch,
            poll_state_service=lambda: poll_state,
        )

    def test_poll_connector_processes_updates_and_records_completion(self) -> None:
        poll_cycle = _PollCycleStub()
        inbound = _InboundStub(
            [
                {"handled": True, "update_id": 4},
                {"handled": True, "update_id": 5},
            ]
        )
        dispatch = _DispatchStub(
            [
                {"processed": True, "chat_id": "chat-1", "action": "help", "run_id": ""},
                {"processed": True, "chat_id": "chat-1", "action": "run", "run_id": "run-1"},
            ]
        )
        poll_state = _PollStateStub()
        service = self._make_service(
            poll_cycle=poll_cycle,
            inbound=inbound,
            dispatch=dispatch,
            poll_state=poll_state,
        )
        service.poll_connector({"id": "conn-1", "workspace_id": "ws-1"})
        self.assertEqual(len(poll_state.processed), 2)
        self.assertEqual(poll_state.processed[1]["run_id"], "run-1")
        self.assertEqual(poll_cycle.complete_calls[0]["max_seen"], 5)

    def test_poll_connector_skips_when_poll_cycle_says_skipped(self) -> None:
        poll_cycle = _PollCycleStub(begin_result={"skipped": True, "approval_state_patch": {}, "updates": []})
        dispatch = _DispatchStub()
        poll_state = _PollStateStub()
        service = self._make_service(poll_cycle=poll_cycle, dispatch=dispatch, poll_state=poll_state)
        service.poll_connector({"id": "conn-1"})
        self.assertEqual(dispatch.calls, [])
        self.assertEqual(poll_state.processed, [])
        self.assertEqual(poll_cycle.complete_calls, [])

    def test_poll_connector_routes_errors_to_poll_cycle(self) -> None:
        poll_cycle = _PollCycleStub()
        service = self._make_service(
            poll_cycle=poll_cycle,
            resolve_secret=lambda entry: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        with self.assertRaises(RuntimeError):
            service.poll_connector({"id": "conn-1", "label": "Telegram", "workspace_id": "ws-1"})
        self.assertEqual(poll_cycle.error_calls[0]["detail"], "boom")

    def test_poll_connector_rejects_unscoped_workspace(self) -> None:
        poll_cycle = _PollCycleStub()
        service = self._make_service(
            poll_cycle=poll_cycle,
            resolve_workspace_scope=lambda entry, fallback_workspace_id, detail_prefix: (_ for _ in ()).throw(
                RuntimeError("Telegram connector 'Main' is not scoped to an explicit workspace.")
            ),
        )

        with self.assertRaises(RuntimeError):
            service.poll_connector({"id": "conn-1", "label": "Main"})

        self.assertIn("not scoped to an explicit workspace", poll_cycle.error_calls[0]["detail"])


if __name__ == "__main__":
    unittest.main()

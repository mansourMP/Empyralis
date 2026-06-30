import importlib
import unittest
from unittest.mock import patch

from server_modules import direct_chat_handoff_facade_service


class _DummyRunStartRequest:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class DirectChatHandoffFacadeServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        global direct_chat_handoff_facade_service
        direct_chat_handoff_facade_service = importlib.import_module(
            "server_modules.direct_chat_handoff_facade_service"
        )

    @patch(
        "server_modules.direct_chat_handoff_facade_service._start_run_dependencies",
        return_value=(lambda request: {"run_id": "run-1", "metadata": request.metadata}, _DummyRunStartRequest),
    )
    def test_start_direct_chat_run_handoff_uses_internal_dependencies(self, _deps) -> None:
        payload = direct_chat_handoff_facade_service.start_direct_chat_run_handoff(
            message="Do the thing",
            workspace_id="default",
            requested_provider="openai",
            requested_model="gpt-5.4",
            thread_id="thread-1",
            availability={"connection_mode": "local_companion"},
            max_iterations=12,
            safe_positive_int_fn=lambda value, _default: int(value),
        )

        self.assertEqual(payload["run_id"], "run-1")
        self.assertEqual(payload["metadata"]["thread_id"], "thread-1")
        self.assertEqual(payload["metadata"]["execution_target"], "local_companion")

    @patch(
        "server_modules.turn_runtime.execute_unowned_system_run_start_request_via_turn_runtime",
        return_value={"run_id": "run-2", "status": "starting"},
    )
    def test_start_run_dependencies_builds_canonical_runtime_executor(self, execute_mock) -> None:
        executor, request_cls = direct_chat_handoff_facade_service._start_run_dependencies()
        request = request_cls(engine="orion", workspace_id="default", user_goal="hello")

        result = executor(request)

        self.assertEqual(result["run_id"], "run-2")
        execute_mock.assert_called_once()

    @patch(
        "server_modules.direct_chat_handoff_facade_service._snapshot_dependencies",
        return_value=(
            {"run-1": {"status": "completed", "result": "Done"}},
            lambda run_id: {"run_id": run_id},
            lambda run_id, run: {"run_id": run_id, "status": run["status"], "fallback_used": False},
        ),
    )
    def test_direct_chat_run_snapshot_uses_internal_dependencies(self, _deps) -> None:
        run, snapshot = direct_chat_handoff_facade_service.direct_chat_run_snapshot("run-1")

        self.assertEqual(run["status"], "completed")
        self.assertEqual(snapshot["run_id"], "run-1")

    def test_stream_direct_chat_run_handoff_delegates(self) -> None:
        events = list(
            direct_chat_handoff_facade_service.stream_direct_chat_run_handoff(
                started_run={"run_id": "run-1"},
                requested_workspace_id="default",
                requested_provider="openai",
                requested_model="gpt-5.4",
                reasoning_effort=None,
                connected_systems=[],
                tool_capabilities=[],
                fallback_reason=None,
                direct_chat_run_snapshot_fn=lambda _run_id: (
                    None,
                    {
                        "run_id": "run-1",
                        "status": "queued_local",
                        "execution_target_selected": "local_companion",
                        "execution_target_waiting_for_runtime": True,
                        "usage_masked": {},
                        "fallback_used": False,
                    },
                ),
                direct_chat_run_event_to_step_fn=lambda _run_id, _event: (None, None),
                direct_chat_run_snapshot_to_step_fn=lambda _run_id, snapshot: (
                    "queued",
                    {
                        "type": "step",
                        "id": "step-1",
                        "label": "Waiting for your laptop",
                        "status": "active",
                        "kind": "thinking",
                    },
                ),
                direct_chat_run_final_payload_fn=lambda **_kwargs: {"reply": "still waiting"},
                open_action_fn=lambda label, href, variant=None: {"label": label, "href": href, "variant": variant},
                build_context_used_fn=lambda **context: context,
                live_window_seconds=0.0,
                poll_seconds=0.0,
                monotonic_fn=lambda: 1.0,
                sleep_fn=lambda _seconds: None,
            )
        )

        self.assertEqual(events[0]["type"], "step")
        self.assertEqual(events[-1]["type"], "final")


if __name__ == "__main__":
    unittest.main()

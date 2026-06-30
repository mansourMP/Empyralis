import unittest

from server_modules import direct_chat_handoff_service


class _DummyRunStartRequest:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class DirectChatHandoffServiceTests(unittest.TestCase):
    def test_start_direct_chat_run_handoff_builds_request_metadata(self) -> None:
        captured = {}

        def fake_start_run(request):
            captured["request"] = request
            return {"run_id": "run-1", "status": "queued_local"}

        payload = direct_chat_handoff_service.start_direct_chat_run_handoff(
            message="Do the thing",
            workspace_id="default",
            requested_provider="openai",
            requested_model="gpt-5.4",
            thread_id="thread-1",
            availability={"connection_mode": "local_companion"},
            max_iterations=12,
            start_run_request_fn=fake_start_run,
            run_start_request_cls=_DummyRunStartRequest,
            safe_positive_int_fn=lambda value, _default: int(value),
        )

        self.assertEqual(payload["run_id"], "run-1")
        request = captured["request"]
        self.assertEqual(request.workspace_id, "default")
        self.assertEqual(request.provider, "openai")
        self.assertEqual(request.model, "gpt-5.4")
        self.assertEqual(request.max_iterations, 12)
        self.assertEqual(request.metadata["execution_target"], "local_companion")
        self.assertEqual(request.metadata["thread_id"], "thread-1")

    def test_direct_chat_run_handoff_reply_for_waiting_confirmation(self) -> None:
        payload = direct_chat_handoff_service.direct_chat_run_handoff_reply(
            {
                "run_id": "run-1",
                "status": "waiting_for_input",
                "pending_confirmation": {"approval_id": "approval-1"},
            },
            open_action_fn=lambda label, href, variant=None: {"label": label, "href": href, "variant": variant},
        )

        self.assertEqual(payload["reply"], "")
        self.assertEqual(payload["detail"], "Waiting for confirmation")
        self.assertEqual(payload["actions"][0]["href"], "/approvals")
        self.assertEqual(payload["interventions"][0]["kind"], "run_handoff")
        self.assertEqual(payload["interventions"][0]["title"], "Durable run is waiting for confirmation")

    def test_stream_direct_chat_run_handoff_waiting_for_runtime_then_continuing(self) -> None:
        monotonic_values = iter([0.0, 20.0])

        events = list(
            direct_chat_handoff_service.stream_direct_chat_run_handoff(
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
                        "execution_target_preferred_runtime_label": "Mansur's MacBook Air",
                        "usage_masked": {},
                        "fallback_used": False,
                    },
                ),
                direct_chat_run_event_to_step_fn=lambda _run_id, _event: (None, None),
                direct_chat_run_snapshot_to_step_fn=direct_chat_handoff_service.direct_chat_run_snapshot_to_step,
                direct_chat_run_final_payload_fn=lambda **kwargs: direct_chat_handoff_service.direct_chat_run_final_payload(
                    **kwargs,
                    build_context_used_fn=lambda **context: context,
                    open_action_fn=lambda label, href, variant=None: {"label": label, "href": href, "variant": variant},
                ),
                open_action_fn=lambda label, href, variant=None: {"label": label, "href": href, "variant": variant},
                build_context_used_fn=lambda **context: context,
                live_window_seconds=12.0,
                poll_seconds=0.0,
                monotonic_fn=lambda: next(monotonic_values),
                sleep_fn=lambda _seconds: None,
            )
        )

        self.assertEqual(events[0]["type"], "step")
        self.assertEqual(events[0]["label"], "Waiting for your laptop")
        self.assertEqual(events[-1]["type"], "final")
        self.assertEqual(events[-1]["payload"]["reply"], "")
        self.assertEqual(events[-1]["payload"]["interventions"][0]["kind"], "run_handoff")
        self.assertEqual(
            events[-1]["payload"]["interventions"][0]["detail"],
            "The durable run is waiting for your laptop to become available.",
        )


if __name__ == "__main__":
    unittest.main()

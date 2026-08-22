"""MAN-129 / MAN-135 — end-to-end proof, driven from the real scheduler-
woken code path, that:

  1. A scheduler-woken turn reaches the model call with a non-empty tool
     list.
  2. A woken agent that uses a tool produces a real trace entry for it.
  3. tool_honesty_guard actually engages on this path: a reply claiming
     work with no matching tool trace gets caught and replaced, not shipped
     verbatim.

Unlike test_man129_orion_result_generate_tool_capable.py (which mocks at
the execute_sage_turn boundary to test runs_execution.py's own wiring in
isolation), this file mocks ONLY at
direct_chat_generation_service.stream_provider_backed_direct_chat -- the
exact seam server_modules/tests/test_sage_agent_runtime_service.py already
establishes as safe for exercising handle_sage_chat/_run_sage_action_loop_v3
for real (see e.g. test_main_sage_chat_executes_web_search_tool there).
Everything above that seam runs for real:

    runs_execution._execute_orion_dag_node(kind="result_generate")
      -> _execute_orion_result_via_agent_engine
      -> agent_turn_adapter.execute_sage_turn                    (real)
      -> agent_turn_runtime_service.handle_sage_chat             (real)
      -> agent_turn_runtime_service._run_sage_action_loop_v3     (real)
      -> direct_chat_generation_service.stream_provider_backed_direct_chat
                                                                  (MOCKED)

This proves the actual production wiring, not a hand-rolled substitute for
it -- if a future refactor breaks the chain between the DAG node and the
real model call, this test (not just the shallower one) will catch it.
"""

from __future__ import annotations

import queue
import unittest
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

from server_modules import runs_execution


def _heartbeat_shaped_context() -> dict:
    return {
        "workflow_id": None,
        "user_goal": "Wake reasons:\n- [self_proposed] Check inbox for replies.",
        "workspace_id": "ws-1",
        "tenant_id": "tenant-1",
        "metadata": {
            "workspace_id": "ws-1",
            "tenant_id": "tenant-1",
            "source": "heartbeat",
            "heartbeat_trigger": "scheduled",
        },
    }


def _enter_handle_sage_chat_fixtures(stack: ExitStack, *, stream_events) -> dict:
    """The exact fixture set test_sage_agent_runtime_service.py's own
    test_main_sage_chat_executes_web_search_tool uses to drive
    handle_sage_chat for real without touching a live DB/provider. Kept as
    a helper (not imported from that test module -- new-file-only rule) so
    all tests below share it without duplicating the list three times.
    Returns the mocks callers actually need to assert on, by name (not
    position) -- entered onto the caller's own ExitStack so cleanup is
    automatic and ordering-independent."""
    stack.enter_context(patch("server_modules.agent_turn_runtime_service.sage_profile_service.list_sage_profile", return_value={"profile": {}}))
    stack.enter_context(patch("server_modules.agent_turn_runtime_service.workspace_context.read_workspace_context_files", return_value={}))
    stack.enter_context(patch("server_modules.agent_turn_runtime_service.sage_memory_service.build_sage_memory_context_block", return_value=""))
    stack.enter_context(patch("server_modules.agent_turn_runtime_service.sage_heartbeat_service.build_sage_heartbeat_snapshot", new=AsyncMock(return_value={})))
    stack.enter_context(patch("server_modules.agent_turn_runtime_service.list_skill_definitions", return_value=[]))
    stack.enter_context(patch("server_modules.agent_turn_runtime_service._resolve_cloud_provider", return_value=("openai", {"api_key": "test-key"})))
    mock_generate = stack.enter_context(patch("server_modules.agent_turn_runtime_service.generate_chat_reply_with_provider_fallback"))
    stack.enter_context(patch("server_modules.agent_turn_runtime_service.direct_chat_runtime_exports.resolve_workspace_tool_capabilities", return_value=[]))
    stack.enter_context(patch("server_modules.agent_turn_runtime_service.direct_chat_runtime_exports._resolve_direct_chat_availability", return_value={"runtime_ok": False}))
    mock_stream = stack.enter_context(
        patch(
            "server_modules.agent_turn_runtime_service.direct_chat_generation_service.stream_provider_backed_direct_chat",
            return_value=iter(stream_events),
        )
    )
    stack.enter_context(patch("server_modules.agent_turn_runtime_service.persist_interaction"))
    stack.enter_context(patch("server_modules.agent_turn_runtime_service.activity_ledger_service.append_activity_event", new=AsyncMock()))
    stack.enter_context(patch("server_modules.agent_turn_runtime_service.security_audit_service.emit_security_audit_event"))
    return {"stream": mock_stream, "generate": mock_generate}


class SchedulerWokenTurnReachesRealModelCallWithToolsTests(unittest.TestCase):
    def test_scheduler_woken_result_generate_reaches_model_call_with_nonempty_tool_list(self):
        """Requirement 1: 'A scheduler-woken turn reaches the model call
        with a non-empty tool list.' The model call here is the real
        stream_provider_backed_direct_chat invocation inside
        _run_sage_action_loop_v3 -- mocked only so the test doesn't need a
        live provider, but its call kwargs are real."""
        stream_events = [
            {"type": "final", "payload": {"reply": "Nothing needed doing this cycle.", "actions": [], "error": ""}},
        ]
        stack = ExitStack()
        self.addCleanup(stack.close)
        mocks = _enter_handle_sage_chat_fixtures(stack, stream_events=stream_events)
        mock_stream = mocks["stream"]

        run_id = "run-heartbeat-tools-nonempty"
        state = {"plan_text": "Check the inbox."}
        runs_execution._execute_orion_dag_node(
            run_id, _heartbeat_shaped_context(), queue.Queue(), {"kind": "result_generate", "deps": []}, state
        )

        self.assertTrue(mock_stream.called)
        stream_kwargs = mock_stream.call_args.kwargs
        self.assertIn("tools", stream_kwargs)
        self.assertGreater(
            len(stream_kwargs["tools"]),
            0,
            "the scheduler-woken turn's model call must carry a non-empty tool list -- "
            "MAN-129 was exactly this list being silently empty",
        )
        self.assertEqual(state["result_text"], "Nothing needed doing this cycle.")

    def test_scheduler_woken_turn_that_uses_a_tool_produces_a_real_trace_entry(self):
        """Requirement 2: 'A woken agent that uses a tool produces a real
        trace entry for it.' Simulates the model issuing a real tool call
        (web__search) mid-turn, exactly like
        test_main_sage_chat_executes_web_search_tool does for ordinary
        chat -- proving the SAME trace mechanics apply on the
        scheduler-woken path."""
        stream_events = [
            {
                "type": "trace",
                "payload": {
                    "event_type": "tool.started",
                    "tool_call_id": "call-1",
                    "data": {"tool_name": "web__search", "args_preview": {"query": "pending replies"}},
                },
            },
            {
                "type": "trace",
                "payload": {
                    "event_type": "tool.result",
                    "tool_call_id": "call-1",
                    "data": {"status": "ok", "summary": "Found 2 pending threads."},
                },
            },
            {"type": "final", "payload": {"reply": "Found 2 pending threads and flagged them.", "actions": [], "error": ""}},
        ]
        stack = ExitStack()
        self.addCleanup(stack.close)
        _enter_handle_sage_chat_fixtures(stack, stream_events=stream_events)

        run_id = "run-heartbeat-real-trace"
        state = {"plan_text": "Check for pending replies."}
        runs_execution._execute_orion_dag_node(
            run_id, _heartbeat_shaped_context(), queue.Queue(), {"kind": "result_generate", "deps": []}, state
        )
        runs_execution._execute_orion_dag_node(
            run_id, _heartbeat_shaped_context(), queue.Queue(), {"kind": "usage_finalize", "deps": []}, state
        )

        self.assertEqual(len(state["result_tool_calls"]), 1)
        self.assertEqual(state["result_tool_calls"][0]["name"], "web__search")
        self.assertEqual(state["final_result_data"]["tool_calls"][0]["name"], "web__search")
        self.assertEqual(state["result_text"], "Found 2 pending threads and flagged them.")


class ToolHonestyGuardEngagesOnSchedulerWokenPathTests(unittest.TestCase):
    def test_a_fabricated_claim_with_no_matching_trace_is_caught_and_replaced(self):
        """Requirement 3: the honesty guard must actually engage on the
        autonomous path. The (fake, mocked) model's final reply claims a
        search happened ("Based on the search results...") while the trace
        shows NO tool call at all this turn -- a 'claims_without_run'
        fabrication. tool_honesty_guard.apply_tool_honesty_guard is NOT
        mocked here: this proves the REAL guard, wired into
        _run_sage_action_loop_v3, catches it -- not a stand-in."""
        fabricated_reply = "Based on the search results, everything is already handled."
        stream_events = [
            {"type": "final", "payload": {"reply": fabricated_reply, "actions": [], "error": ""}},
        ]
        stack = ExitStack()
        self.addCleanup(stack.close)
        _enter_handle_sage_chat_fixtures(stack, stream_events=stream_events)

        run_id = "run-heartbeat-honesty-guard"
        state = {"plan_text": "Check for pending replies."}
        runs_execution._execute_orion_dag_node(
            run_id, _heartbeat_shaped_context(), queue.Queue(), {"kind": "result_generate", "deps": []}, state
        )

        # The guard must have intercepted the fabricated claim: the
        # fabricated text must NOT be what the run reports as having
        # happened, and the deterministic honest-fallback text (or a
        # regenerated, consistent reply) must be what surfaces instead.
        self.assertNotEqual(state["result_text"], fabricated_reply)
        self.assertNotIn("Based on the search results", state["result_text"])
        self.assertIn(
            "don't have a real result",
            state["result_text"],
            "expected tool_honesty_guard's deterministic fallback (claims_without_run "
            "skips regeneration and goes straight to the fixed honest fallback string) "
            "-- got: " + repr(state["result_text"]),
        )

    def test_a_reply_that_only_reports_real_completed_work_passes_through_unmodified(self):
        """Owner unaffected: an honest reply describing a tool call that
        genuinely happened must NOT be altered by the guard -- proves the
        guard doesn't over-fire on the autonomous path either."""
        stream_events = [
            {
                "type": "trace",
                "payload": {
                    "event_type": "tool.started",
                    "tool_call_id": "call-1",
                    "data": {"tool_name": "web__search", "args_preview": {"query": "pending replies"}},
                },
            },
            {
                "type": "trace",
                "payload": {
                    "event_type": "tool.result",
                    "tool_call_id": "call-1",
                    "data": {"status": "ok", "summary": "Found 2 pending threads."},
                },
            },
            {"type": "final", "payload": {"reply": "I searched and found 2 pending threads.", "actions": [], "error": ""}},
        ]
        stack = ExitStack()
        self.addCleanup(stack.close)
        _enter_handle_sage_chat_fixtures(stack, stream_events=stream_events)

        run_id = "run-heartbeat-honest-pass-through"
        state = {"plan_text": "Check for pending replies."}
        runs_execution._execute_orion_dag_node(
            run_id, _heartbeat_shaped_context(), queue.Queue(), {"kind": "result_generate", "deps": []}, state
        )

        self.assertEqual(state["result_text"], "I searched and found 2 pending threads.")


if __name__ == "__main__":
    unittest.main()

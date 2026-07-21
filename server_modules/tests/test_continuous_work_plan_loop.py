"""Tests for the continuous-work plan loop (Tier A #1,
docs/design/backbone-plan.md / docs/design/backbone-empyralis.md §6) in
direct_chat_generation_service.stream_provider_backed_direct_chat.

Context: the action loop used to hard-cap every turn at
`_SAGE_OPERATOR_LOOP_MAX_ITERATIONS` (5) tool-calling round-trips, regardless
of whether the agent was mid-way through a multi-step task. The fix adds an
`update_plan` tool that lets the model lay out a task list; while that plan
has pending/active tasks, the loop is allowed to continue past the normal
cap, bounded by the same token-budget accounting the proactive compaction
preflight already uses (never a hard runaway), and reversible via
EMPYRALIS_CONTINUOUS_WORK_ENABLED.

These tests prove, against the real generator (only the provider stream and
tool execution are mocked, following test_direct_chat_tool_strip_provider_gate
.py's `_services` fixture pattern):

  (a) A turn that calls `update_plan` and still has pending/active tasks
      runs PAST the configured max_iterations cap.
  (a-budget) ... but only as far as the token budget allows — once the
      budget guard says no, the loop stops at the cap exactly like a
      no-plan turn, even with tasks still open.
  (a-flag) ... and only while EMPYRALIS_CONTINUOUS_WORK_ENABLED is on —
      flipping it off collapses back to the exact pre-existing cap even
      with an open plan, proving the flag is genuinely load-bearing.
  (b) A turn that never calls `update_plan` is capped at max_iterations
      exactly as before this feature existed (no behavior change).
  (c) `update_plan` emits a `plan.updated` trace event carrying the full
      current task list, `{tasks: [{id, title, status}, ...]}`, with stable
      ids that survive a status-only update.
"""

from __future__ import annotations

import os
import unittest
from unittest import mock
from unittest.mock import AsyncMock

from server_modules import agent_trace_service
from server_modules import direct_chat_generation_service


def _round(*, provider: str, model: str, tool_calls: list | None = None, reply: str = "") -> list[dict]:
    return [
        {
            "type": "result",
            "reply": reply,
            "usage_masked": {"provider": provider},
            "provider": provider,
            "model": model,
            "attempted_providers": provider,
            "error": "",
            "tool_calls": tool_calls or [],
        }
    ]


def _update_plan_call(call_id: str, tasks: list[dict]) -> dict:
    return {"id": call_id, "name": "update_plan", "arguments": {"tasks": tasks}}


def _search_call(call_id: str) -> dict:
    return {"id": call_id, "name": "web__search", "arguments": {"query": "hello"}}


class _StreamRoundRecorder:
    """Returns one canned round of stream events per call. Raises IndexError
    if called more times than there are canned rounds — a request for a
    round that shouldn't happen (e.g. a 6th call under a 5-iteration cap)
    surfaces as a hard test failure rather than silently passing."""

    def __init__(self, rounds: list[list[dict]]) -> None:
        self._rounds = list(rounds)
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append({"metadata_tools": list((kwargs["metadata"].get("tools") or []))})
        round_index = len(self.calls) - 1
        return iter(self._rounds[round_index])

    @property
    def call_count(self) -> int:
        return len(self.calls)


class _ContinuousWorkLoopTestBase(unittest.TestCase):
    def _services(self, *, recorder: _StreamRoundRecorder, tool_call_log: list[str]):
        def _execute_single_direct_tool_call(**kwargs):
            tool_call_log.append(str(kwargs.get("tool_call", {}).get("name") or ""))
            return "tool result payload"

        return direct_chat_generation_service.DirectChatGenerationServices(
            thinking_step_payload=lambda iteration, status, detail=None: {
                "type": "step",
                "iteration": iteration,
                "status": status,
                "detail": detail,
            },
            build_context_used=lambda **kwargs: kwargs,
            build_direct_tool_approval_response=lambda **kwargs: None,
            parse_tool_name=lambda name: tuple(str(name).split("__", 1)) if "__" in str(name) else ("", ""),
            tool_arguments_payload=lambda value: value if isinstance(value, dict) else {},
            parse_page_state=lambda value: {},
            direct_tool_step_payload=lambda connector_id, action_id, arguments, **kwargs: {
                "type": "step",
                "connector": connector_id,
                "action": action_id,
                "arguments": arguments,
                **kwargs,
            },
            execute_single_direct_tool_call=_execute_single_direct_tool_call,
            direct_tool_followup_message=lambda tool_name, result_text: f"{tool_name}: {result_text}",
            suggest_actions=lambda _message, _availability: [],
            clear_direct_tool_loop_state=lambda _session_key: None,
            persist_direct_chat_memory_best_effort=lambda **kwargs: None,
            persist_direct_chat_transcript_best_effort=lambda **kwargs: None,
            persist_direct_chat_hosted_usage_best_effort=lambda **kwargs: None,
            record_direct_tool_signature=lambda _session_key, _tool_call: False,
            direct_chat_error_reply=lambda error: f"Chat failed: {error}",
            capture_exception=lambda exc: None,
            generate_chat_reply_stream_with_provider_fallback=recorder,
        )

    def _run(
        self,
        *,
        provider: str = "anthropic",
        model: str = "claude-sonnet-4-6",
        recorder: _StreamRoundRecorder,
        tool_call_log: list[str],
        max_iterations: int,
        trace_context=None,
    ):
        return list(
            direct_chat_generation_service.stream_provider_backed_direct_chat(
                services=self._services(recorder=recorder, tool_call_log=tool_call_log),
                context={"provider": provider, "tools": []},
                metadata={"provider": provider, "model": model, "tools": []},
                system_prompt="System prompt",
                normalized_workspace_id="default",
                normalized_requested_provider=provider,
                normalized_requested_model=model,
                normalized_reasoning_effort=None,
                normalized_thread_id="thread-1",
                normalized_message="Do the multi-step task.",
                compacted_prior_messages=[],
                prior_messages_used=False,
                history_mode="none",
                connected_systems=[],
                tool_capabilities=[],
                availability_payload={"ai_ready": True},
                tools=[],
                direct_chat_credentials={},
                proactive_suggestions=[],
                tool_loop_session_key=f"session-{provider}",
                fallback_reason=None,
                session_ctx=None,
                trace_context=trace_context,
                resolved_chat_max_iterations=max_iterations,
                direct_tool_result_summary_system_message="Summarize tool results.",
                assistant_plan_tools=[],
            )
        )


class NoPlanTurnStaysCappedTests(_ContinuousWorkLoopTestBase):
    def test_no_plan_turn_is_capped_at_max_iterations_exactly_as_before(self) -> None:
        """(b) A turn that never calls update_plan must be capped at exactly
        max_iterations round-trips -- no behavior change for ordinary turns."""
        max_iterations = 5
        recorder = _StreamRoundRecorder(
            [_round(provider="anthropic", model="claude-sonnet-4-6", tool_calls=[_search_call(f"call_{i}")]) for i in range(max_iterations)]
        )
        tool_call_log: list[str] = []

        events = self._run(
            recorder=recorder,
            tool_call_log=tool_call_log,
            max_iterations=max_iterations,
        )

        # Exactly max_iterations provider round-trips were made -- the
        # recorder would have raised IndexError had a 6th been requested.
        self.assertEqual(recorder.call_count, max_iterations)
        self.assertEqual(len(tool_call_log), max_iterations)
        self.assertEqual(events[-1]["type"], "final")


class PlanExtendsLoopPastCapTests(_ContinuousWorkLoopTestBase):
    def test_open_plan_lets_the_loop_run_past_max_iterations(self) -> None:
        """(a) A plan with pending/active tasks lets the loop continue past
        the configured cap, and ends naturally once the model stops calling
        tools (all tasks done) -- not via the cap."""
        max_iterations = 5
        rounds = [
            _round(provider="anthropic", model="claude-sonnet-4-6", tool_calls=[
                _update_plan_call("call_0", [{"title": "Step 1"}, {"title": "Step 2"}])
            ]),
            _round(provider="anthropic", model="claude-sonnet-4-6", tool_calls=[
                _update_plan_call("call_1", [{"title": "Step 1", "status": "active"}, {"title": "Step 2"}])
            ]),
            _round(provider="anthropic", model="claude-sonnet-4-6", tool_calls=[
                _update_plan_call("call_2", [{"title": "Step 1", "status": "done"}, {"title": "Step 2", "status": "active"}])
            ]),
            _round(provider="anthropic", model="claude-sonnet-4-6", tool_calls=[
                _update_plan_call("call_3", [{"title": "Step 1", "status": "done"}, {"title": "Step 2", "status": "active"}])
            ]),
            _round(provider="anthropic", model="claude-sonnet-4-6", tool_calls=[
                # 5th round -- iteration counter reaches max_iterations (5)
                # right after this one. Under the OLD cap this would be the
                # last round; Step 2 is still open, so continuous work must
                # keep going.
                _update_plan_call("call_4", [{"title": "Step 1", "status": "done"}, {"title": "Step 2", "status": "active"}])
            ]),
            _round(provider="anthropic", model="claude-sonnet-4-6", tool_calls=[
                # 6th round -- PAST the old cap. Only reachable if the
                # continuous-work extension actually fired.
                _update_plan_call("call_5", [{"title": "Step 1", "status": "done"}, {"title": "Step 2", "status": "done"}])
            ]),
            _round(provider="anthropic", model="claude-sonnet-4-6", reply="All steps are done."),
        ]
        recorder = _StreamRoundRecorder(rounds)
        tool_call_log: list[str] = []

        events = self._run(
            recorder=recorder,
            tool_call_log=tool_call_log,
            max_iterations=max_iterations,
        )

        # 7 round-trips happened -- 2 more than the configured 5-iteration
        # cap -- proving the loop actually extended past it.
        self.assertEqual(recorder.call_count, 7)
        self.assertGreater(recorder.call_count, max_iterations)
        self.assertEqual(events[-1]["type"], "final")
        self.assertEqual(events[-1]["payload"]["reply"], "All steps are done.")
        self.assertEqual(events[-1]["payload"]["error"], "")

    def test_open_plan_does_not_extend_past_the_token_budget(self) -> None:
        """(a, bounded) Even with tasks still open, the loop must NOT extend
        past the cap once the token-budget guard says no -- 'the token
        budget is the ceiling', not the plan. Same behavior as a no-plan
        turn once the budget check fails."""
        max_iterations = 5
        # Every round just re-affirms the same still-open task -- the plan
        # never completes on its own, so only the budget guard can stop it.
        rounds = [
            _round(provider="anthropic", model="claude-sonnet-4-6", tool_calls=[
                _update_plan_call(f"call_{i}", [{"title": "Never-ending step", "status": "active"}])
            ])
            for i in range(max_iterations)
        ]
        recorder = _StreamRoundRecorder(rounds)
        tool_call_log: list[str] = []

        with mock.patch.object(
            direct_chat_generation_service,
            "_continuous_work_budget_allows_more",
            return_value=False,
        ):
            events = self._run(
                recorder=recorder,
                tool_call_log=tool_call_log,
                max_iterations=max_iterations,
            )

        # The budget guard vetoed continuation, so the loop stopped at
        # exactly max_iterations despite the plan still having an open task.
        self.assertEqual(recorder.call_count, max_iterations)
        self.assertEqual(events[-1]["type"], "final")

    def test_flag_disabled_collapses_back_to_the_exact_old_cap(self) -> None:
        """(a, reversible) EMPYRALIS_CONTINUOUS_WORK_ENABLED=0 must restore
        the exact pre-existing 5-iteration cap even with an open plan --
        the instant kill switch the build brief requires."""
        max_iterations = 5
        rounds = [
            _round(provider="anthropic", model="claude-sonnet-4-6", tool_calls=[
                _update_plan_call(f"call_{i}", [{"title": "Some step", "status": "active"}])
            ])
            for i in range(max_iterations)
        ]
        recorder = _StreamRoundRecorder(rounds)
        tool_call_log: list[str] = []

        with mock.patch.dict(os.environ, {"EMPYRALIS_CONTINUOUS_WORK_ENABLED": "0"}):
            events = self._run(
                recorder=recorder,
                tool_call_log=tool_call_log,
                max_iterations=max_iterations,
            )

        self.assertEqual(recorder.call_count, max_iterations)
        self.assertEqual(events[-1]["type"], "final")


class UpdatePlanTraceEventTests(_ContinuousWorkLoopTestBase):
    def test_update_plan_emits_plan_updated_event_with_stable_ids(self) -> None:
        """(c) update_plan emits a `plan.updated` trace event carrying the
        full current task list `{tasks: [{id, title, status}, ...]}`, and a
        task's id is stable across a status-only follow-up call."""
        trace_context = agent_trace_service.TraceContext(
            trace_id="trace-1",
            workspace_id="ws-1",
            tenant_id="tenant-1",
            thread_id="thread-1",
            run_id=None,
            root_agent_id="sage",
        )
        recorder = _StreamRoundRecorder(
            [
                _round(provider="anthropic", model="claude-sonnet-4-6", tool_calls=[
                    _update_plan_call("call_0", [{"title": "Step 1"}, {"title": "Step 2", "status": "active"}])
                ]),
                _round(provider="anthropic", model="claude-sonnet-4-6", tool_calls=[
                    _update_plan_call("call_1", [{"title": "Step 1", "status": "done"}, {"title": "Step 2", "status": "active"}])
                ]),
                _round(provider="anthropic", model="claude-sonnet-4-6", reply="Done."),
            ]
        )
        tool_call_log: list[str] = []

        with mock.patch.object(
            agent_trace_service.control_plane_repository,
            "append_agent_trace_event",
            new=AsyncMock(return_value=None),
        ):
            events = self._run(
                recorder=recorder,
                tool_call_log=tool_call_log,
                max_iterations=5,
                trace_context=trace_context,
            )

        plan_updated_events = [
            event["payload"]
            for event in events
            if event.get("type") == "trace"
            and isinstance(event.get("payload"), dict)
            and event["payload"].get("event_type") == "plan.updated"
        ]
        self.assertEqual(len(plan_updated_events), 2)

        first_tasks = plan_updated_events[0]["data"]["tasks"]
        second_tasks = plan_updated_events[1]["data"]["tasks"]
        self.assertEqual([t["title"] for t in first_tasks], ["Step 1", "Step 2"])
        self.assertEqual([t["status"] for t in first_tasks], ["pending", "active"])
        self.assertEqual([t["status"] for t in second_tasks], ["done", "active"])
        # Every task has a non-empty id.
        for task in first_tasks + second_tasks:
            self.assertIn("id", task)
            self.assertTrue(str(task["id"]).strip())
        # Stable ids: "Step 1" and "Step 2" keep the SAME id across the two
        # update_plan calls even though only their status changed.
        first_ids_by_title = {t["title"]: t["id"] for t in first_tasks}
        second_ids_by_title = {t["title"]: t["id"] for t in second_tasks}
        self.assertEqual(first_ids_by_title["Step 1"], second_ids_by_title["Step 1"])
        self.assertEqual(first_ids_by_title["Step 2"], second_ids_by_title["Step 2"])
        self.assertEqual(events[-1]["type"], "final")
        self.assertEqual(events[-1]["payload"]["reply"], "Done.")


if __name__ == "__main__":
    unittest.main()

"""MAN-129 / MAN-135 — unit-level proof that the 'standard' Orion DAG's
result_generate node (the shape _compile_orion_dag falls back to for any
run with no workflow_id and no outcome_pack -- which is EVERY
scheduler-woken heartbeat/task_assigned wakeup, see
runtime_heartbeat_service.build_heartbeat_turn_request) routes through the
real, tool-capable agent engine (execute_sage_turn) instead of a bare,
tool-less text completion (generate_with_candidate_failover).

Before the fix, result_generate called generate_with_candidate_failover --
a raw `ProviderAdapter.generate(system_prompt, user_input, model,
credentials) -> str` call with no tools parameter anywhere in its
signature -- with a prompt that told the model to report "What Empyralis
did". An agent woken by the scheduler could not act, and then narrated
work it never did (MAN-129).

These tests exercise runs_execution._execute_orion_dag_node(kind=
"result_generate", ...) directly, mocking only at the execute_sage_turn
boundary (the same function server_modules/tests/test_agent_turn_adapter.py
already establishes as the correct mocking seam for this module). A
companion, deeper integration test
(test_man129_scheduler_woken_tool_capable_integration.py) drives the same
node all the way down to the real model call to prove the tool list
actually reaches it and that tool_honesty_guard actually engages.
"""

from __future__ import annotations

import queue
import unittest
from unittest.mock import AsyncMock, patch

from server_modules import runs_execution
from server_modules.agent_turn_runtime_contract import SageTurnResult
from server_modules.specialist_runtime_context import SpecialistRuntimeContext


def _heartbeat_shaped_context(**metadata_overrides) -> dict:
    """The exact context shape a scheduler-woken 'standard' DAG run
    executes with: no workflow_id, no outcome_pack, source="heartbeat" --
    mirrors runtime_heartbeat_service.build_heartbeat_turn_request's
    context_hints, threaded through run_service into the DAG's context."""
    metadata = {
        "workspace_id": "ws-1",
        "tenant_id": "tenant-1",
        "source": "heartbeat",
        "heartbeat_trigger": "scheduled",
    }
    metadata.update(metadata_overrides)
    return {
        "workflow_id": None,
        "user_goal": "Wake reasons:\n- [self_proposed] Check inbox for replies.",
        "workspace_id": "ws-1",
        "tenant_id": "tenant-1",
        "metadata": metadata,
    }


class CompileOrionDagStillFallsBackToStandardForHeartbeatShapedContextTests(unittest.TestCase):
    """Sanity/scope check: confirms the scenario these tests exercise is
    the SAME scenario a real heartbeat/task_assigned wakeup produces, not a
    hypothetical. If this ever stops being true (e.g. heartbeat starts
    attaching a workflow_id), these unit tests would silently stop covering
    the real bug -- this test makes that assumption explicit and checked."""

    def test_no_workflow_no_outcome_pack_compiles_to_standard_dag_with_result_generate(self):
        dag = runs_execution._compile_orion_dag(_heartbeat_shaped_context())
        self.assertEqual(dag["type"], "standard")
        node_kinds = [node["kind"] for node in dag["nodes"]]
        self.assertIn("result_generate", node_kinds)


class ResultGenerateRoutesThroughToolCapableEngineTests(unittest.TestCase):
    def _run_result_generate(self, *, sage_result=None, sage_side_effect=None, state=None, context=None):
        run_id = "run-heartbeat-1"
        log_queue = queue.Queue()
        node = {"id": "result.generate", "kind": "result_generate", "deps": ["plan.approval"]}
        state = dict(state or {"plan_text": "1. Check inbox\n2. Reply to pending threads"})
        context = context or _heartbeat_shaped_context()
        mock_kwargs = {}
        if sage_side_effect is not None:
            mock_kwargs["side_effect"] = sage_side_effect
        else:
            mock_kwargs["return_value"] = sage_result or SageTurnResult(message="ok")
        with patch(
            "server_modules.agent_turn_adapter.execute_sage_turn",
            new=AsyncMock(**mock_kwargs),
        ) as mock_execute_sage_turn:
            result = runs_execution._execute_orion_dag_node(run_id, context, log_queue, node, state)
        return result, state, mock_execute_sage_turn, log_queue

    def test_result_generate_calls_execute_sage_turn_not_a_bare_completion(self):
        """The core routing assertion: result_generate reaches the SAME
        tool-capable entry point every channel and console/web chat use,
        not generate_with_candidate_failover."""
        with patch.object(
            runs_execution,
            "generate_with_candidate_failover",
            side_effect=AssertionError(
                "result_generate must not call the tool-less generate_with_candidate_failover any more"
            ),
        ):
            result, state, mock_execute_sage_turn, _ = self._run_result_generate(
                sage_result=SageTurnResult(message="Checked the inbox, nothing pending.", tool_calls=[])
            )

        self.assertTrue(mock_execute_sage_turn.called)
        self.assertEqual(state["result_text"], "Checked the inbox, nothing pending.")
        self.assertIn("Checked the inbox, nothing pending.", state["final_text"])
        self.assertEqual(result["tool_calls"], 0)

    def test_result_generate_scopes_the_turn_to_its_own_run_not_sage_main(self):
        """An autonomous background run's execution turn must not interleave
        with (or silently inherit history from) the owner's live console/
        channel conversation thread ('sage-main')."""
        _, _, mock_execute_sage_turn, _ = self._run_result_generate(
            sage_result=SageTurnResult(message="done")
        )
        call_kwargs = mock_execute_sage_turn.call_args.kwargs
        self.assertEqual(call_kwargs["thread_id"], "orion-run:run-heartbeat-1")
        self.assertNotEqual(call_kwargs["thread_id"], "sage-main")
        self.assertEqual(call_kwargs["workspace_id"], "ws-1")
        self.assertEqual(call_kwargs["tenant_id"], "tenant-1")

    def test_a_used_tool_produces_a_real_trace_entry_in_run_state_and_log(self):
        """'A woken agent that uses a tool produces a real trace entry for
        it' -- proven at the DAG-node/run level: the tool_calls
        execute_sage_turn returns must show up in state (for usage_finalize
        to publish onto the run's public result_data) and in the run's own
        event log, not just disappear into a narrated summary."""
        tool_call = {"name": "web__search", "status": "completed", "output": "1. Result: found it."}
        result, state, _, log_queue = self._run_result_generate(
            sage_result=SageTurnResult(
                message="I searched and found it.",
                tool_calls=[tool_call],
            )
        )
        self.assertEqual(state["result_tool_calls"], [tool_call])
        self.assertEqual(result["tool_calls"], 1)

        logged_events = []
        while True:
            try:
                logged_events.append(log_queue.get_nowait())
            except queue.Empty:
                break
        tool_call_events = [e for e in logged_events if isinstance(e, dict) and e.get("event") == "orion_tool_call"]
        self.assertEqual(len(tool_call_events), 1)
        self.assertEqual(tool_call_events[0]["data"], tool_call)

    def test_usage_finalize_publishes_the_tool_trace_onto_the_runs_public_result_data(self):
        tool_call = {"name": "memory_write", "status": "completed", "output": "saved"}
        run_id = "run-heartbeat-2"
        log_queue = queue.Queue()
        state = {"plan_text": "Save a note."}
        context = _heartbeat_shaped_context()
        with patch(
            "server_modules.agent_turn_adapter.execute_sage_turn",
            new=AsyncMock(return_value=SageTurnResult(message="Saved the note.", tool_calls=[tool_call])),
        ):
            runs_execution._execute_orion_dag_node(
                run_id, context, log_queue, {"kind": "result_generate", "deps": []}, state
            )
        runs_execution._execute_orion_dag_node(
            run_id, context, log_queue, {"kind": "usage_finalize", "deps": []}, state
        )
        self.assertEqual(state["final_result_data"], {"tool_calls": [tool_call]})

    def test_no_tool_use_still_produces_none_result_data_unchanged(self):
        """Owner unaffected: a heartbeat tick where the agent genuinely had
        nothing to act on (no tool calls at all) must not fabricate a fake
        trace entry -- final_result_data stays None, exactly like before."""
        run_id = "run-heartbeat-3"
        log_queue = queue.Queue()
        state = {"plan_text": "Nothing pending."}
        context = _heartbeat_shaped_context()
        with patch(
            "server_modules.agent_turn_adapter.execute_sage_turn",
            new=AsyncMock(return_value=SageTurnResult(message="Nothing needed doing.", tool_calls=[])),
        ):
            runs_execution._execute_orion_dag_node(
                run_id, context, log_queue, {"kind": "result_generate", "deps": []}, state
            )
        runs_execution._execute_orion_dag_node(
            run_id, context, log_queue, {"kind": "usage_finalize", "deps": []}, state
        )
        self.assertIsNone(state["final_result_data"])

    def test_a_failed_turn_raises_instead_of_fabricating_a_success(self):
        """Same honesty principle as _honest_no_provider_error elsewhere in
        this file: a real failure must raise, never ship a plausible-looking
        fake success. Mirrors what generate_with_candidate_failover already
        did for provider failures."""
        with self.assertRaises(RuntimeError) as ctx:
            self._run_result_generate(
                sage_result=SageTurnResult(message="", error="No AI provider is configured for workspace ws-1.")
            )
        self.assertIn("No AI provider is configured", str(ctx.exception))


class ResultGenerateThreadsAssignedAgentIdentityTests(unittest.TestCase):
    """Bug 1 (MAN-312-adjacent) regression: a task_assigned wakeup's turn
    must execute AS the assigned agent, not the workspace master (Sage).

    bounded_scheduler_service.schedule_task_assigned_wakeup captures the
    assignee in the wake request's payload["agent_id"] (a workspace_agent_
    installs.id). runtime_heartbeat_service.build_heartbeat_turn_request
    now threads that id into merged_metadata["active_agent_install_id"] --
    the SAME metadata key agent_turn.py/run_service.py already read for
    thread-tagging and runtime-attachment resolution -- which flows through
    run_service.build_turn_seed_from_request into the DAG's own
    context["metadata"], read here by _execute_orion_result_via_agent_
    engine. These tests prove that id is resolved through the SAME
    specialist_runtime_context.resolve_specialist_runtime_context every
    other channel uses (not a parallel path) and threaded into the
    execute_sage_turn call as specialist_context -- and that its absence,
    or a resolution failure, fails safe to specialist_context=None (today's
    unchanged master/Sage behavior)."""

    def _run_result_generate_with_metadata(self, *, metadata_overrides, resolve_specialist_side_effect=None, resolve_specialist_return=None):
        run_id = "run-task-assigned-1"
        log_queue = queue.Queue()
        node = {"id": "result.generate", "kind": "result_generate", "deps": ["plan.approval"]}
        state = {"plan_text": "1. Read the task\n2. Do the work"}
        context = _heartbeat_shaped_context(**metadata_overrides)
        resolve_kwargs = {}
        if resolve_specialist_side_effect is not None:
            resolve_kwargs["side_effect"] = resolve_specialist_side_effect
        else:
            resolve_kwargs["return_value"] = resolve_specialist_return
        with (
            patch(
                "server_modules.agent_turn_adapter.execute_sage_turn",
                new=AsyncMock(return_value=SageTurnResult(message="done")),
            ) as mock_execute_sage_turn,
            patch(
                "server_modules.specialist_runtime_context.resolve_specialist_runtime_context",
                new=AsyncMock(**resolve_kwargs),
            ) as mock_resolve_specialist,
        ):
            runs_execution._execute_orion_dag_node(run_id, context, log_queue, node, state)
        return mock_execute_sage_turn, mock_resolve_specialist

    def test_assigned_agent_id_in_metadata_resolves_and_threads_specialist_context(self):
        """The core routing assertion: an assignee id threaded into
        context["metadata"]["active_agent_install_id"] must reach
        execute_sage_turn as specialist_context, resolved through the exact
        same resolver interactive chat uses -- not a reimplemented lookup."""
        spec = SpecialistRuntimeContext(
            agent_install_id="agent-assignee-1",
            agent_label="Ops Agent",
            agent_kind="specialist",
            persona="You are the Ops agent.",
        )
        mock_execute_sage_turn, mock_resolve_specialist = self._run_result_generate_with_metadata(
            metadata_overrides={"active_agent_install_id": "agent-assignee-1"},
            resolve_specialist_return=spec,
        )

        mock_resolve_specialist.assert_awaited_once()
        resolve_kwargs = mock_resolve_specialist.await_args.kwargs
        self.assertEqual(resolve_kwargs["active_agent_install_id"], "agent-assignee-1")
        self.assertEqual(resolve_kwargs["workspace_id"], "ws-1")
        self.assertEqual(resolve_kwargs["tenant_id"], "tenant-1")

        self.assertTrue(mock_execute_sage_turn.called)
        self.assertIs(mock_execute_sage_turn.call_args.kwargs["specialist_context"], spec)

    def test_no_assigned_agent_id_never_calls_the_resolver_and_runs_as_master(self):
        """An ordinary heartbeat tick (no task_assigned wake request, so no
        active_agent_install_id in metadata -- the common case) must be
        byte-for-byte unchanged: no resolver call, specialist_context=None,
        the turn runs as Sage exactly like before this fix."""
        mock_execute_sage_turn, mock_resolve_specialist = self._run_result_generate_with_metadata(
            metadata_overrides={},
            resolve_specialist_return=None,
        )

        mock_resolve_specialist.assert_not_awaited()
        self.assertTrue(mock_execute_sage_turn.called)
        self.assertIsNone(mock_execute_sage_turn.call_args.kwargs["specialist_context"])

    def test_resolution_failure_fails_safe_to_master_not_a_raised_error(self):
        """A resolver exception (DB hiccup, unknown install, etc.) must
        never break the turn -- same fail-safe convention as every other
        resolve_specialist_runtime_context call site (direct_chat_service.
        execute_direct_chat_turn_request, agent_turn_adapter.
        execute_sage_turn_for_channel)."""
        mock_execute_sage_turn, mock_resolve_specialist = self._run_result_generate_with_metadata(
            metadata_overrides={"active_agent_install_id": "agent-unknown"},
            resolve_specialist_side_effect=RuntimeError("install not found"),
        )

        mock_resolve_specialist.assert_awaited_once()
        self.assertTrue(mock_execute_sage_turn.called)
        self.assertIsNone(mock_execute_sage_turn.call_args.kwargs["specialist_context"])


if __name__ == "__main__":
    unittest.main()

"""MAN-144: enforced per-run cost ceiling for the durable-run engine (Path A).

New file -- does not modify any existing test file or conftest.py.

Covers the two real model-call chokepoints inside a single durable run
(server_modules/run_service.py + server_modules/runs_execution.py, tracked by
run_id/status -- the system deployed_agent_cost_cap_service.
settle_deployed_agent_monthly_cost_cap already hooks into on run completion):

  1. runs_engine.generate_with_candidate_failover -- the shared chokepoint
     both the legacy "standard" DAG (plan_generate/result_generate nodes) and
     workflow_graph_execute ("agent" nodes) call through. Every call this
     function makes is now checked against a per-run ceiling BEFORE the
     provider adapter is invoked.
  2. runs_execution.run_orion_mission's exception handling, which now
     classifies a ceiling-triggered RuntimeError into a distinct "cancelled"
     run status (mirroring the pre-existing "stopped by human decision"
     branch) instead of the generic "failed" a real crash gets.
"""

from __future__ import annotations

import queue
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server_modules import (
    config_defaults_service,
    run_state_repository,
    runs_engine,
    runs_execution,
    runs_output,
    safe_mode_service,
    shared,
)
from server_modules.runtime_state_store import init_runtime_state_db


class _FakeAdapter:
    """Stands in for a real ProviderAdapter -- records every call and returns
    a fixed-length reply so each call's masked/estimated cost is
    deterministic and comparable across tests."""

    def __init__(self, reply: str = "a response " * 40) -> None:
        self.calls: list[tuple[str, str]] = []
        self.reply = reply

    def generate(self, system_prompt, user_input, model, credentials) -> str:
        self.calls.append((model, user_input))
        return self.reply


class GenerateWithCandidateFailoverCeilingTests(unittest.TestCase):
    """Direct unit tests of the chokepoint itself -- no run_orion_mission,
    no ACP_MANAGER, no DAG. Just: does the ceiling stop the NEXT call?"""

    def setUp(self) -> None:
        runs_engine._RUN_COST_LEDGERS.clear()

    def tearDown(self) -> None:
        runs_engine._RUN_COST_LEDGERS.clear()

    def _state(self) -> dict:
        return {
            "provider": "anthropic",
            "selected_model": "claude-test",
            "credential_candidates": [
                {
                    "credentials": {"api_key": "test-key"},
                    "model": "claude-test",
                    "profile_id": None,
                    "credential_id": None,
                    "source": "test",
                }
            ],
        }

    def test_calls_under_ceiling_complete_normally(self) -> None:
        adapter = _FakeAdapter()
        with patch.object(runs_engine, "resolve_provider_adapter", return_value=("anthropic", "anthropic", adapter)):
            context = {"metadata": {"deployed_agent_run_cost_ceiling_usd": 5.0}}
            text = runs_engine.generate_with_candidate_failover(
                self._state(), context, queue.Queue(), "system", "hello", run_id="run-under-ceiling"
            )
        self.assertEqual(text, adapter.reply)
        self.assertEqual(len(adapter.calls), 1)

    def test_call_exceeding_ceiling_halts_before_the_call_that_would_cross_it(self) -> None:
        adapter = _FakeAdapter()
        run_id = "run-over-ceiling"
        with patch.object(runs_engine, "resolve_provider_adapter", return_value=("anthropic", "anthropic", adapter)):
            # Ceiling small enough that a single reply's estimated cost
            # already meets/exceeds it -- the SECOND call must be blocked.
            context = {"metadata": {"deployed_agent_run_cost_ceiling_usd": 0.0001}}
            first_text = runs_engine.generate_with_candidate_failover(
                self._state(), context, queue.Queue(), "system", "first call", run_id=run_id
            )
            self.assertEqual(first_text, adapter.reply)
            self.assertEqual(len(adapter.calls), 1)

            with self.assertRaisesRegex(RuntimeError, runs_engine.RUN_COST_CEILING_ERROR_MARKER):
                runs_engine.generate_with_candidate_failover(
                    self._state(), context, queue.Queue(), "system", "second call", run_id=run_id
                )
        # The defining assertion: the adapter was NEVER invoked for the
        # second call. The halt happened before the call that would have
        # crossed the ceiling, not after it billed for another response.
        self.assertEqual(len(adapter.calls), 1)

    def test_ceiling_accumulates_across_calls_sharing_a_run_id_not_per_call_state(self) -> None:
        """Regression guard for the actual bug this design had to avoid: the
        workflow_graph_execute path hands generate_with_candidate_failover a
        FRESH `state` dict on every single "agent" node (see
        _resolve_agent_generation_state in runs_execution.py), so accumulating
        on `state` alone would silently reset to zero every node. The ledger
        must be keyed by run_id, not by the state object identity."""
        adapter = _FakeAdapter()
        run_id = "run-fresh-state-per-call"
        with patch.object(runs_engine, "resolve_provider_adapter", return_value=("anthropic", "anthropic", adapter)):
            context = {"metadata": {"deployed_agent_run_cost_ceiling_usd": 0.0001}}
            # Two DIFFERENT state dicts, same run_id -- simulates two
            # different "agent" workflow nodes within the same run.
            runs_engine.generate_with_candidate_failover(
                self._state(), context, queue.Queue(), "system", "node A", run_id=run_id
            )
            with self.assertRaisesRegex(RuntimeError, runs_engine.RUN_COST_CEILING_ERROR_MARKER):
                runs_engine.generate_with_candidate_failover(
                    self._state(), context, queue.Queue(), "system", "node B", run_id=run_id
                )
        self.assertEqual(len(adapter.calls), 1)

    def test_default_applies_when_no_per_agent_value_is_set(self) -> None:
        with patch.object(runs_engine, "resolve_provider_adapter", return_value=("anthropic", "anthropic", _FakeAdapter())):
            # No "deployed_agent_run_cost_ceiling_usd" key at all in metadata.
            runs_engine.generate_with_candidate_failover(
                self._state(), {"metadata": {}}, queue.Queue(), "system", "hi", run_id="run-default-ceiling"
            )
        ledger = runs_engine._RUN_COST_LEDGERS["run-default-ceiling"]
        self.assertEqual(ledger["_run_cost_ceiling_usd"], config_defaults_service.default_run_cost_ceiling_usd())


class RunOrionMissionCeilingHaltTests(unittest.TestCase):
    """run_orion_mission's own exception-classification branch: does a
    ceiling-triggered halt actually land on a DIFFERENT, distinguishable
    run status than a real crash does? Uses the exact live-run test harness
    server_modules/tests/test_runs_execution_graph.py already established
    for this file (temp sqlite runtime-state db + in-memory
    run_state_repository stand-ins) -- duplicated here rather than imported,
    since this is a new file and must not depend on another test module."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "runtime-state.sqlite3"
        init_runtime_state_db(self.db_path)
        self.live_run_store: dict[str, dict] = {}
        self.archive_store: dict[str, dict] = {}
        self._real_run_runtime_kernel_enforced = runs_execution.rust_runtime_kernel_client.run_runtime_kernel_enforced
        self.patchers = [
            patch.object(runs_execution, "ORION_RUNTIME_STATE_DB", self.db_path),
            patch.object(runs_output, "ORION_RUNTIME_STATE_DB", self.db_path),
            patch.object(run_state_repository, "sync_upsert_live_run", side_effect=self._sync_upsert_live_run),
            patch.object(run_state_repository, "sync_delete_live_run", side_effect=self._sync_delete_live_run),
            patch.object(run_state_repository, "sync_list_live_runs", side_effect=self._sync_list_live_runs),
            patch.object(run_state_repository, "sync_archive_run", side_effect=self._sync_archive_run),
            patch.object(run_state_repository, "sync_list_run_archive", side_effect=self._sync_list_run_archive),
            patch.object(
                runs_execution.rust_runtime_kernel_client,
                "run_runtime_kernel_enforced",
                side_effect=self._fake_execution_outcome_kernel_response,
            ),
        ]
        for patcher in self.patchers:
            patcher.start()
        shared.sync_acp_manager_paths(runtime_db_path=self.db_path)
        runs_execution.ACP_MANAGER.reload_runtime_state()
        runs_execution.runs.clear()

    def tearDown(self) -> None:
        runs_execution.runs.clear()
        safe_mode_service.reset_state_for_tests()
        for patcher in reversed(self.patchers):
            patcher.stop()
        shared.sync_acp_manager_paths(runtime_db_path=runs_execution.ORION_RUNTIME_STATE_DB)
        runs_execution.ACP_MANAGER.reload_runtime_state()
        self.tmpdir.cleanup()

    def _sync_upsert_live_run(self, run_id, workspace_id, tenant_id, state, payload, trace_id) -> None:
        snapshot = dict(payload)
        snapshot.update({"run_id": run_id, "workspace_id": workspace_id, "tenant_id": tenant_id, "status": state, "trace_id": trace_id})
        self.live_run_store[run_id] = snapshot

    def _sync_delete_live_run(self, run_id) -> None:
        self.live_run_store.pop(run_id, None)

    def _sync_list_live_runs(self) -> list[dict]:
        return [dict(item) for item in self.live_run_store.values()]

    def _sync_archive_run(self, run_id, final_state, payload, trace_id) -> None:
        snapshot = dict(payload)
        snapshot.update({"run_id": run_id, "status": final_state, "trace_id": trace_id})
        self.archive_store[run_id] = snapshot

    def _sync_list_run_archive(self, limit: int = 200) -> list[dict]:
        items = list(self.archive_store.values())
        return [dict(item) for item in items[: max(1, int(limit or 0))]]

    def _fake_execution_outcome_kernel_response(self, command: str, payload: dict, **kwargs) -> dict:
        """Realistic stand-in for the Rust kernel's "execution-outcome"
        response shape -- top-level final_status/summary/event/log_level,
        the same shape server_modules/tests/test_runs_execution_rust_runtime
        .py's own fake already uses for this exact command. The blanket
        autouse conftest fixture (_skip_kernel_tests_when_binary_missing)
        does NOT special-case this command -- it only echoes a generic
        allow decision with the request payload nested under .payload,
        which is a fine default for tests that don't inspect outcome
        contents, but this test asserts on run.execution_outcome.stderr, so
        it needs the real shape, not the generic echo."""
        if command != "execution-outcome":
            return self._real_run_runtime_kernel_enforced(command, payload, **kwargs)
        cancelled = bool(payload.get("cancelled"))
        timed_out = bool(payload.get("timed_out"))
        stderr = str(payload.get("stderr") or "")
        response = {
            "ok": True,
            "decision": "allow",
            "stderr": stderr,
            "record_patch": {},
            "retryable": False,
            "audit_visibility": "standard",
            "approval_required": False,
            "cacheable": False,
        }
        if cancelled:
            # Only the outcomes this fake has a firm opinion about get an
            # explicit final_status -- exactly the "stopped deliberately, not
            # crashed" cases (mirrors the real kernel's own special-casing).
            # Plain exit_code success/failure deliberately has NO
            # final_status key here, so _runtime_final_status_from_outcome
            # falls through to whatever the caller passed as `fallback`
            # ("completed" for a clean exit, "failed" for a generic error) --
            # exactly like the generic autouse conftest mock already does
            # correctly for those cases.
            response.update(
                {
                    "final_status": "cancelled",
                    "summary": stderr or "Run stopped.",
                    "event": "run_stopped",
                    "log_level": "warn",
                }
            )
        elif timed_out:
            response.update(
                {
                    "final_status": "timeout",
                    "summary": stderr or "Execution timed out.",
                    "event": "timeout",
                    "log_level": "warn",
                }
            )
        return response

    def _register_live_run(self, run_id: str) -> queue.Queue:
        log_queue = queue.Queue()
        runs_execution.runs[run_id] = {
            "status": "queued",
            "logs": log_queue,
            "input_queue": queue.Queue(),
            "events": [],
            "_event_seq": 0,
            "node_states": None,
            "context": {"metadata": {}},
            "tool_policy_audit": [],
            "memory_trace": {},
        }
        return log_queue

    def test_run_under_ceiling_completes_normally(self) -> None:
        run_id = "run-orion-under-ceiling"
        self._register_live_run(run_id)
        with patch.object(
            runs_execution, "_enforce_execution_runtime_decision", return_value={}
        ), patch.object(
            runs_execution,
            "_compile_orion_dag",
            return_value={"id": "fake", "type": "standard", "nodes": []},
        ), patch.object(
            runs_execution,
            "_execute_orion_dag_once",
            return_value={
                "result_text": "done",
                "result_data": None,
                "usage_masked": {"provider": "anthropic", "model": "x", "total_tokens_est": 10, "cost_band": "low"},
                "active_profile_id": None,
                "active_provider": "anthropic",
                "active_model": "x",
                "active_adapter": "anthropic",
            },
        ):
            runs_execution.run_orion_mission(run_id)

        run = runs_execution.runs[run_id]
        self.assertEqual(run.get("status"), "completed")
        self.assertNotEqual(run.get("status"), "failed")
        self.assertIsNone(run.get("halt_reason"))

    def test_run_exceeding_ceiling_halts_with_a_distinguishable_status(self) -> None:
        """The core assertion for this task's 'halt is distinguishable from a
        crash' requirement: a ceiling-triggered stop must NOT land on the
        same 'failed' status an ordinary crash gets."""
        run_id = "run-orion-over-ceiling"
        self._register_live_run(run_id)
        ceiling_error = RuntimeError(
            f"Run stopped: {runs_engine.RUN_COST_CEILING_ERROR_MARKER} "
            "(spent $2.5000 of a $2.0000 per-run ceiling before this call would have been made)."
        )
        with patch.object(
            runs_execution, "_enforce_execution_runtime_decision", return_value={}
        ), patch.object(
            runs_execution, "_compile_orion_dag", return_value={"id": "fake", "type": "standard", "nodes": []}
        ), patch.object(
            runs_execution, "_execute_orion_dag_once", side_effect=ceiling_error
        ):
            runs_execution.run_orion_mission(run_id)

        run = runs_execution.runs[run_id]
        self.assertEqual(run.get("status"), "cancelled")
        self.assertNotEqual(run.get("status"), "failed")
        self.assertEqual(run.get("halt_reason"), "run_cost_ceiling_reached")
        self.assertIn(
            runs_engine.RUN_COST_CEILING_ERROR_MARKER,
            str((run.get("execution_outcome") or {}).get("stderr") or "").lower(),
        )

    def test_an_ordinary_crash_still_lands_on_failed_not_cancelled(self) -> None:
        """Contrast case, proving the branch actually discriminates rather
        than reclassifying every exception as 'cancelled': a plain crash
        (not the ceiling, not a human-decision stop) must still produce the
        pre-existing 'failed' status."""
        run_id = "run-orion-plain-crash"
        self._register_live_run(run_id)
        with patch.object(
            runs_execution, "_enforce_execution_runtime_decision", return_value={}
        ), patch.object(
            runs_execution, "_compile_orion_dag", return_value={"id": "fake", "type": "standard", "nodes": []}
        ), patch.object(
            runs_execution, "_execute_orion_dag_once", side_effect=RuntimeError("provider exploded unexpectedly")
        ):
            runs_execution.run_orion_mission(run_id)

        run = runs_execution.runs[run_id]
        self.assertEqual(run.get("status"), "failed")
        self.assertNotEqual(run.get("status"), "cancelled")
        self.assertIsNone(run.get("halt_reason"))


if __name__ == "__main__":
    unittest.main()

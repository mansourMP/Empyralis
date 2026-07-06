"""Proof for Fix 1: terminal run-state persistence is synchronous-and-confirmed.

Regression cover for the audit finding that terminal/completion writes were
fire-and-forget with an optimistic version bump, so a single transient DB error
permanently desynced a durable run (in-memory "completed", DB "running").
"""

import unittest
from unittest.mock import patch

from server_modules import run_service


def _make_run(version: int = 0, status: str = "completed") -> dict:
    return {
        "run_id": "run-x",
        "workspace_id": "ws-1",
        "tenant_id": "ten-1",
        "_durable_version": version,
        "status": status,
    }


class TerminalPersistenceConvergenceTests(unittest.TestCase):
    def test_transient_db_error_midwrite_then_converges(self):
        """DB connection dies on the first write; the retry confirms and the
        in-memory version advances to the persisted value."""
        run = _make_run(version=3)
        calls = []

        def fake_update(run_id, ws, tenant, state, payload, trace_id, *, expected_version):
            calls.append(expected_version)
            if len(calls) == 1:
                raise ConnectionError("server closed the connection unexpectedly")
            return expected_version + 1  # confirmed write returns the new version

        with patch.object(
            run_service.run_state_repository,
            "sync_update_live_run_if_version_matches",
            side_effect=fake_update,
        ), patch.object(run_service.time, "sleep"):
            run_service._persist_run_snapshot_confirmed(
                "run-x", run, state="completed", trace_id="t",
                payload={"status": "completed"}, expected_version=3,
            )

        self.assertEqual(run["_durable_version"], 4)  # converged to confirmed value
        self.assertEqual(calls, [3, 3])  # retried on the same version after the blip

    def test_version_conflict_reconciles_and_converges(self):
        """A concurrent writer advanced the row; we reconcile against the
        persisted version and retry on top of it rather than swallowing."""
        run = _make_run(version=3)
        update_calls = []

        def fake_update(run_id, ws, tenant, state, payload, trace_id, *, expected_version):
            update_calls.append(expected_version)
            if expected_version == 3:
                return None  # conflict — DB is ahead
            return expected_version + 1

        with patch.object(
            run_service.run_state_repository,
            "sync_update_live_run_if_version_matches",
            side_effect=fake_update,
        ), patch.object(
            run_service.run_state_repository, "sync_get_live_run",
            return_value={"_durable_version": 5},
        ), patch.object(run_service.time, "sleep"):
            run_service._persist_run_snapshot_confirmed(
                "run-x", run, state="completed", trace_id="t",
                payload={"status": "completed"}, expected_version=3,
            )

        self.assertEqual(run["_durable_version"], 6)
        self.assertEqual(update_calls, [3, 5])  # reconciled to 5, then confirmed

    def test_exhausted_retries_do_not_desync_and_surface_loudly(self):
        """A sustained outage exhausts retries: the failure is surfaced loudly and
        the in-memory version is left UNCHANGED, so memory and DB stay in
        agreement and a later write can still repair the run."""
        run = _make_run(version=3)
        with patch.object(
            run_service.run_state_repository,
            "sync_update_live_run_if_version_matches",
            side_effect=ConnectionError("db down"),
        ), patch.object(run_service.time, "sleep"), patch.object(
            run_service, "_report_durability_failure"
        ) as report:
            run_service._persist_run_snapshot_confirmed(
                "run-x", run, state="completed", trace_id="t",
                payload={"status": "completed"}, expected_version=3,
            )
        report.assert_called_once()  # loud, not swallowed
        self.assertEqual(run["_durable_version"], 3)  # no permanent desync

    def test_terminal_state_routes_through_confirmed_path(self):
        run = _make_run(version=0)
        with patch.object(run_service, "_persist_run_snapshot_confirmed") as confirmed, \
             patch.object(run_service.run_state_repository, "dispatch_repository_call") as dispatch, \
             patch.object(run_service, "_serialize_run_for_durable_repository", return_value={}):
            run_service._persist_run_repository_snapshot("run-x", run, state="completed", trace_id="t")
        confirmed.assert_called_once()
        dispatch.assert_not_called()

    def test_nonterminal_state_uses_dispatch_and_does_not_bump_optimistically(self):
        """The old bug bumped _durable_version to expected+1 before the async
        write confirmed. It must stay put now (advanced only on confirmed success)."""
        run = _make_run(version=7, status="executing")
        # Close the coroutine the mock receives so it isn't reported unawaited
        # (in production dispatch_repository_call awaits it via run_coroutine_threadsafe).
        with patch.object(
            run_service.run_state_repository, "dispatch_repository_call",
            side_effect=lambda coro, **_: coro.close(),
        ) as dispatch, \
             patch.object(run_service, "_serialize_run_for_durable_repository", return_value={}):
            run_service._persist_run_repository_snapshot("run-x", run, state="executing", trace_id="t")
        dispatch.assert_called_once()
        self.assertEqual(run["_durable_version"], 7)


if __name__ == "__main__":
    unittest.main()

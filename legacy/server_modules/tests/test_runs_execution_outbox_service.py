import queue
import unittest
from unittest.mock import patch

from server_modules import run_state_repository, runs_execution


class RunsExecutionOutboxServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.live_run_store: dict[str, dict] = {}
        self.patchers = [
            patch.object(run_state_repository, "sync_upsert_live_run", side_effect=self._sync_upsert_live_run),
            patch.object(run_state_repository, "sync_delete_live_run", side_effect=self._sync_delete_live_run),
        ]
        for patcher in self.patchers:
            patcher.start()

    def tearDown(self) -> None:
        for patcher in reversed(self.patchers):
            patcher.stop()

    def test_enqueue_local_companion_run_delegates_to_outbox_service(self) -> None:
        previous_runs = dict(runs_execution.runs)
        try:
            runs_execution.runs.clear()
            runs_execution.runs["run-1"] = {
                "run_id": "run-1",
                "logs": queue.Queue(),
                "context": {"metadata": {}},
            }

            with patch.object(runs_execution.outbox_service, "enqueue_local_companion_run") as enqueue_mock:
                runs_execution._enqueue_local_companion_run(
                    "run-1",
                    message="queued",
                    event="local_queued",
                )

            enqueue_mock.assert_called_once()
            args, kwargs = enqueue_mock.call_args
            self.assertEqual(args[0], "run-1")
            self.assertIs(kwargs["runs_by_id"], runs_execution.runs)
            self.assertIs(kwargs["pending_run_ids"], runs_execution.LOCAL_PENDING_RUN_IDS)
            self.assertEqual(kwargs["db_path"], runs_execution.ORION_RUNTIME_STATE_DB)
        finally:
            runs_execution.runs.clear()
            runs_execution.runs.update(previous_runs)

    def _sync_upsert_live_run(
        self,
        run_id: str,
        workspace_id: str,
        tenant_id: str,
        state: str,
        payload: dict,
        trace_id: str,
    ) -> None:
        snapshot = dict(payload)
        snapshot["run_id"] = run_id
        snapshot["workspace_id"] = workspace_id
        snapshot["tenant_id"] = tenant_id
        snapshot["status"] = state
        snapshot["trace_id"] = trace_id
        self.live_run_store[run_id] = snapshot

    def _sync_delete_live_run(self, run_id: str) -> None:
        self.live_run_store.pop(run_id, None)


if __name__ == "__main__":
    unittest.main()

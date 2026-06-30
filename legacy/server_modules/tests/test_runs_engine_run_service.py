import queue
import unittest
from unittest.mock import patch

from server_modules import runs_core, runs_engine


class RunsEngineRunServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._persist_patch = patch(
            "server_modules.acp_manager.run_state_repository.sync_upsert_live_run",
            return_value=None,
        )
        self._delete_patch = patch(
            "server_modules.acp_manager.run_state_repository.sync_delete_live_run",
            return_value=None,
        )
        self._persist_patch.start()
        self._delete_patch.start()
        self.addCleanup(self._persist_patch.stop)
        self.addCleanup(self._delete_patch.stop)

    def test_wait_for_human_response_delegates_to_run_service(self) -> None:
        run = {
            "run_id": "run-approval-1",
            "status": "waiting_for_input",
            "logs": queue.Queue(),
            "input_queue": queue.Queue(),
            "context": {"metadata": {}},
        }
        previous_runs = dict(runs_engine.runs)
        previous_core_runs = dict(runs_core.runs)
        try:
            runs_engine.runs.clear()
            runs_core.runs.clear()
            runs_engine.runs["run-approval-1"] = run
            runs_core.runs["run-approval-1"] = run

            with patch.object(
                runs_engine.run_service,
                "wait_for_human_response",
                return_value={"approval_id": "approval-1", "approved": True},
            ) as wait_mock:
                payload = runs_engine.wait_for_human_response("run-approval-1", "Confirm run")

            self.assertTrue(payload["approved"])
            wait_mock.assert_called_once()
            args, kwargs = wait_mock.call_args
            self.assertEqual(args[0], "run-approval-1")
            self.assertEqual(args[1], "Confirm run")
            self.assertIs(kwargs["runs_by_id"], runs_engine.runs)
            self.assertEqual(kwargs["approval_ttl_seconds"], runs_engine.ORION_APPROVAL_TTL_SECONDS)
            self.assertIs(kwargs["set_run_status_fn"], runs_core.set_run_status)
        finally:
            runs_engine.runs.clear()
            runs_engine.runs.update(previous_runs)
            runs_core.runs.clear()
            runs_core.runs.update(previous_core_runs)


if __name__ == "__main__":
    unittest.main()

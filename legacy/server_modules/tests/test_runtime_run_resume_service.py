import unittest
from unittest.mock import Mock, patch

from server_modules import runtime_run_resume_service


class _ThreadWorker:
    def __init__(self, ident: int, alive: bool = True) -> None:
        self.ident = ident
        self._alive = alive

    def is_alive(self) -> bool:
        return self._alive


class RuntimeRunResumeServiceTests(unittest.TestCase):
    @staticmethod
    def _allow_resume(command, payload, allow_approval_required=False):
        return {
            "ok": True,
            "decision": "allow",
            "operation": "resume_run",
            "next_action": "resume_run",
        }

    def test_run_thread_is_alive_matches_active_worker(self):
        run = {"thread_id": 42}

        self.assertTrue(
            runtime_run_resume_service.run_thread_is_alive(
                run,
                enumerate_threads=lambda: [_ThreadWorker(42, True)],
            )
        )

    def test_schedule_restored_run_resume_starts_thread_for_non_local_run(self):
        persisted = []
        worker = Mock()

        with patch.object(
            runtime_run_resume_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=self._allow_resume,
        ) as rust_mock:
            result = runtime_run_resume_service.schedule_restored_run_resume(
                "run-1",
                {"status": "waiting_for_input", "thread_id": None},
                run_thread_is_alive_fn=lambda run: False,
                utc_now_iso=lambda: "2026-04-05T00:00:00Z",
                late_server_export=lambda name: (
                    (lambda run_id, run: persisted.append((run_id, run.get("updated_at"))))
                    if name == "_persist_live_run_state"
                    else Mock()
                ),
                thread_class=lambda **kwargs: worker,
            )

        self.assertTrue(result)
        self.assertEqual(persisted[0][0], "run-1")
        worker.start.assert_called_once()
        self.assertEqual(rust_mock.call_args.args[0], "run-api-decision")
        self.assertEqual(rust_mock.call_args.args[1]["operation"], "resume_run")

    def test_schedule_restored_run_resume_requeues_local_checkpoint_run(self):
        enqueued = []
        run = {
            "status": "waiting_for_input",
            "thread_id": None,
            "context": {"metadata": {"execution_target_selected": "local_companion"}},
            "browser_checkpoint": {"next_action_index": 1},
        }

        with patch.object(
            runtime_run_resume_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=self._allow_resume,
        ):
            result = runtime_run_resume_service.schedule_restored_run_resume(
                "run-2",
                run,
                run_thread_is_alive_fn=lambda run: False,
                utc_now_iso=lambda: "2026-04-05T00:00:00Z",
                late_server_export=lambda name: (
                    (lambda run_id, **kwargs: enqueued.append((run_id, kwargs)))
                    if name == "_enqueue_local_companion_run"
                    else Mock()
                ),
                thread_class=lambda **kwargs: Mock(),
            )

        self.assertTrue(result)
        self.assertTrue(run["context"]["metadata"]["browser_resume_supported"])
        self.assertEqual(enqueued[0][0], "run-2")

    def test_build_run_thread_is_alive_fn_wraps_enumerator(self):
        run_thread_is_alive_fn = runtime_run_resume_service.build_run_thread_is_alive_fn(
            enumerate_threads=lambda: [_ThreadWorker(7, True)],
        )

        self.assertTrue(run_thread_is_alive_fn({"thread_id": 7}))

    def test_build_schedule_restored_run_resume_fn_wraps_callbacks(self):
        persisted = []
        worker = Mock()
        schedule = runtime_run_resume_service.build_schedule_restored_run_resume_fn(
            run_thread_is_alive_fn=lambda run: False,
            utc_now_iso=lambda: "2026-04-05T00:00:00Z",
            late_server_export=lambda name: (
                (lambda run_id, run: persisted.append((run_id, run.get("updated_at"))))
                if name == "_persist_live_run_state"
                else Mock()
            ),
            thread_class=lambda **kwargs: worker,
        )

        with patch.object(
            runtime_run_resume_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=self._allow_resume,
        ):
            result = schedule("run-3", {"status": "waiting_for_input", "thread_id": None})

        self.assertTrue(result)
        self.assertEqual(persisted[0][0], "run-3")
        worker.start.assert_called_once()

    def test_schedule_restored_run_resume_wrong_rust_next_action_blocks_before_resume(self):
        persisted = []
        worker = Mock()
        with patch.object(
            runtime_run_resume_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "operation": "resume_run",
                "next_action": "pause_run",
            },
        ):
            with self.assertRaises(HTTPException) as raised:
                runtime_run_resume_service.schedule_restored_run_resume(
                    "run-1",
                    {"status": "waiting_for_input", "thread_id": None},
                    run_thread_is_alive_fn=lambda run: False,
                    utc_now_iso=lambda: "2026-04-05T00:00:00Z",
                    late_server_export=lambda name: (
                        (lambda run_id, run: persisted.append((run_id, run.get("updated_at"))))
                        if name == "_persist_live_run_state"
                        else Mock()
                    ),
                    thread_class=lambda **kwargs: worker,
                )

        self.assertEqual(raised.exception.status_code, 423)
        self.assertIn("unexpected next_action", str(raised.exception.detail))
        self.assertEqual(persisted, [])
        worker.start.assert_not_called()


if __name__ == "__main__":
    unittest.main()

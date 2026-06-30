import threading
import time
import unittest
from pathlib import Path
import tempfile

from server_modules.runtime_lane_queue import RuntimeLaneQueue


def _wait_until(predicate, *, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("Condition was not met before timeout.")


class RuntimeLaneQueueTests(unittest.TestCase):
    def test_three_tasks_queue_cleanly_and_surface_waiting_state(self) -> None:
        queue = RuntimeLaneQueue(max_total_concurrency=1)
        release_event = threading.Event()
        started = []
        completed = []

        def _make_work(label: str):
            def _work():
                started.append(label)
                if label == "task-1":
                    release_event.wait(timeout=2.0)
                completed.append(label)
                return {"status": "completed", "summary": label}

            return _work

        queue.enqueue(lane="cron", label="task-1", work=_make_work("task-1"))
        queue.enqueue(lane="cron", label="task-2", work=_make_work("task-2"))
        queue.enqueue(lane="cron", label="task-3", work=_make_work("task-3"))

        _wait_until(lambda: len(started) == 1)
        snapshot = queue.snapshot()
        self.assertEqual(snapshot["active_count"], 1)
        self.assertEqual(snapshot["pending_count"], 2)
        self.assertEqual(snapshot["lanes"]["cron"]["active_count"], 1)
        self.assertEqual(snapshot["lanes"]["cron"]["pending_count"], 2)

        release_event.set()
        _wait_until(lambda: len(completed) == 3)
        final_snapshot = queue.shutdown(wait=True, timeout=2.0)
        self.assertEqual(final_snapshot["pending_count"], 0)
        self.assertEqual(final_snapshot["active_count"], 0)

    def test_shutdown_drains_pending_work(self) -> None:
        queue = RuntimeLaneQueue(max_total_concurrency=1)
        release_event = threading.Event()
        finished = []

        def _slow_work():
            release_event.wait(timeout=2.0)
            finished.append("slow")
            return {"status": "completed", "summary": "slow"}

        def _fast_work():
            finished.append("fast")
            return {"status": "completed", "summary": "fast"}

        queue.enqueue(lane="system", label="slow", work=_slow_work)
        queue.enqueue(lane="system", label="fast", work=_fast_work)
        _wait_until(lambda: queue.snapshot()["active_count"] == 1)

        drain_thread = threading.Thread(
            target=lambda: queue.shutdown(wait=True, timeout=2.0),
            daemon=True,
        )
        drain_thread.start()
        time.sleep(0.05)
        self.assertTrue(queue.snapshot()["draining"])

        release_event.set()
        drain_thread.join(timeout=2.0)
        self.assertFalse(drain_thread.is_alive())
        self.assertEqual(finished, ["slow", "fast"])

    def test_default_global_queue_uses_checkpoint_when_db_available(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "runtime.db"
            # Temporarily override the default DB path
            from server_modules import runtime_lane_queue
            original_default = runtime_lane_queue._DEFAULT_STATE_DB_PATH
            runtime_lane_queue._DEFAULT_STATE_DB_PATH = db_path
            runtime_lane_queue._GLOBAL_QUEUE = None
            try:
                queue = runtime_lane_queue.get_runtime_lane_queue()
                snapshot = queue.snapshot()
                self.assertTrue(snapshot["checkpoint"]["enabled"])
                self.assertEqual(snapshot["checkpoint"]["key"], "runtime_lane_queue.v1")
            finally:
                if runtime_lane_queue._GLOBAL_QUEUE is not None:
                    runtime_lane_queue._GLOBAL_QUEUE.shutdown(wait=True, timeout=2.0)
                runtime_lane_queue._GLOBAL_QUEUE = None
                runtime_lane_queue._DEFAULT_STATE_DB_PATH = original_default

    def test_run_backed_items_restore_from_durable_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "runtime.db"
            release_event = threading.Event()

            def _blocked_work():
                release_event.wait(timeout=2.0)
                return {"status": "completed", "summary": "blocked", "run_id": "run-1"}

            queue = RuntimeLaneQueue(max_total_concurrency=1, state_db_path=db_path)
            queue.enqueue(
                lane="cron",
                label="blocked",
                metadata={"run_id": "run-1"},
                work=_blocked_work,
            )
            queue.enqueue(
                lane="cron",
                label="waiting",
                metadata={"run_id": "run-2"},
                work=lambda: {"status": "completed", "summary": "waiting", "run_id": "run-2"},
            )
            _wait_until(lambda: queue.snapshot()["active_count"] == 1)
            _wait_until(lambda: queue.snapshot()["pending_count"] == 1)
            checkpoint_snapshot = queue.snapshot()
            self.assertTrue(checkpoint_snapshot["checkpoint"]["enabled"])
            self.assertIsNone(checkpoint_snapshot["checkpoint"]["last_error"])

            restored_run_ids = []

            def _restore_work(item):
                run_id = str(item.get("run_id") or "")

                def _work():
                    restored_run_ids.append(run_id)
                    return {"status": "completed", "summary": f"restored {run_id}", "run_id": run_id}

                return _work

            restored = RuntimeLaneQueue(
                max_total_concurrency=1,
                state_db_path=db_path,
                restore_work_factory=_restore_work,
            )
            snapshot = restored.snapshot()
            self.assertTrue(snapshot["checkpoint"]["enabled"])
            self.assertIsNone(snapshot["checkpoint"]["last_error"])
            self.assertEqual(snapshot["pending_count"], 2)
            self.assertEqual([item["run_id"] for item in snapshot["lanes"]["cron"]["pending"]], ["run-1", "run-2"])

            release_event.set()
            queue.shutdown(wait=True, timeout=2.0)
            restored.start()
            restored.shutdown(wait=True, timeout=2.0)
            self.assertEqual(restored_run_ids, ["run-1", "run-2"])


if __name__ == "__main__":
    unittest.main()

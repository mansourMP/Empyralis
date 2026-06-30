import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from server_modules import local_queue


class LocalQueueWatchdogTests(unittest.TestCase):
    def _run_cold_boot_recovery(self, run: dict, *, now_iso: str = "2026-04-06T12:00:00Z"):
        emitted = []
        persisted = []
        original_server = local_queue._server
        original_done = local_queue._COLD_BOOT_RECOVERY_DONE
        local_queue._server = SimpleNamespace(
            runs={"run-1": run},
            LOCAL_QUEUE_LOCK=__import__("threading").Lock(),
            LOCAL_CLAIMED_RUNS={},
            LOCAL_PENDING_RUN_IDS=[],
            LOCAL_WORKER_REGISTRY={},
            _utc_now=lambda: datetime.fromisoformat(now_iso.replace("Z", "")),
            _utc_now_iso=lambda: now_iso,
            _parse_utc_ts=lambda value: datetime.fromisoformat(str(value).replace("Z", "")) if value else None,
            ORION_LOCAL_LEASE_SECONDS=30,
            emit_log=lambda log_queue, level, message, **kwargs: emitted.append((level, message, kwargs)),
            _persist_live_run_state=lambda run_id, live_run: persisted.append((run_id, live_run.get("status"))),
        )
        local_queue._COLD_BOOT_RECOVERY_DONE = False
        try:
            recovered = local_queue.recover_orphaned_local_runs_on_startup()
        finally:
            local_queue._server = original_server
            local_queue._COLD_BOOT_RECOVERY_DONE = original_done
        return recovered, emitted, persisted

    def test_watchdog_status_snapshot_reflects_last_recorded_pass(self) -> None:
        local_queue._record_local_runtime_watchdog_status(
            checked_at="2026-04-06T12:00:00Z",
            status="ok",
            summary="Recovered 2 stale local claims.",
            cleaned_run_ids=["run-1", "run-2"],
            resumed_run_ids=["run-3"],
            interval_seconds=5,
        )

        snapshot = local_queue.local_runtime_watchdog_status_snapshot()

        self.assertTrue(snapshot["running"])
        self.assertEqual(snapshot["interval_seconds"], 5)
        self.assertEqual(snapshot["last_status"], "ok")
        self.assertEqual(snapshot["last_cleaned_count"], 2)
        self.assertEqual(snapshot["last_cleaned_run_ids"], ["run-1", "run-2"])
        self.assertEqual(snapshot["last_resumed_count"], 1)
        self.assertEqual(snapshot["last_resumed_run_ids"], ["run-3"])

    def test_resume_due_checkpoint_recoveries_schedules_run_after_backoff(self) -> None:
        emitted = []
        scheduled = []
        original_server = local_queue._server
        local_queue._server = SimpleNamespace(
            runs={
                "run-1": {
                    "status": "waiting_for_input",
                    "logs": object(),
                    "browser_checkpoint": {"next_action_index": 4, "session_profile": "qa-browser"},
                    "context": {
                        "metadata": {
                            "execution_target_selected": "local_companion",
                            "local_worker_recovery_reason": "worker_lost",
                            "local_worker_recovery_attempt_count": 2,
                            "local_worker_recovery_next_retry_at": "2026-04-06T12:00:00Z",
                        }
                    },
                }
            },
            _utc_now=lambda: datetime.fromisoformat("2026-04-06T12:00:05"),
            _utc_now_iso=lambda: "2026-04-06T12:00:05Z",
            _parse_utc_ts=lambda value: datetime.fromisoformat(str(value).replace("Z", "")) if value else None,
            emit_log=lambda log_queue, level, message, **kwargs: emitted.append((level, message, kwargs)),
        )
        try:
            with patch("server_modules.runtime_runs_api._schedule_restored_run_resume", side_effect=lambda run_id, run: scheduled.append((run_id, run.get("status"))) or True):
                resumed = local_queue._resume_due_checkpoint_recoveries()
        finally:
            local_queue._server = original_server

        self.assertEqual(resumed, ["run-1"])
        self.assertEqual(scheduled, [("run-1", "waiting_for_input")])
        self.assertEqual(emitted[0][2]["event"], "local_resume_scheduled_after_backoff")

    def test_cold_boot_recovery_preserves_worker_loss_backoff_state(self) -> None:
        run = {
            "status": "running_local",
            "logs": object(),
            "browser_checkpoint": {"next_action_index": 4, "session_profile": "qa-browser"},
            "context": {
                "metadata": {
                    "execution_target_selected": "local_companion",
                    "local_worker_recovery_reason": "worker_lost",
                    "local_worker_recovery_attempt_count": 2,
                    "local_worker_recovery_backoff_seconds": 10,
                    "local_worker_recovery_max_auto_retries": 3,
                    "local_worker_recovery_next_retry_at": "2026-04-06T12:00:10Z",
                }
            },
        }

        recovered, emitted, persisted = self._run_cold_boot_recovery(run)

        self.assertEqual(recovered, ["run-1"])
        self.assertEqual(persisted, [("run-1", "waiting_for_input")])
        self.assertEqual(run["status"], "waiting_for_input")
        self.assertEqual(run["result_data"]["error"], "local_worker_lost_recoverable")
        self.assertEqual(run["result_data"]["retry_backoff_seconds"], 10)
        self.assertEqual(run["result_data"]["next_retry_at"], "2026-04-06T12:00:10Z")
        self.assertTrue(run["context"]["metadata"]["cold_boot_recovered"])
        self.assertEqual(emitted[0][2]["event"], "local_cold_boot_recovered_backoff")

    def test_cold_boot_recovery_preserves_manual_confirmation_gate(self) -> None:
        run = {
            "status": "running_local",
            "logs": object(),
            "browser_checkpoint": {"next_action_index": 9, "session_profile": "qa-browser"},
            "context": {
                "metadata": {
                    "execution_target_selected": "local_companion",
                    "local_worker_recovery_reason": "worker_lost",
                    "local_worker_recovery_attempt_count": 3,
                    "local_worker_recovery_max_auto_retries": 3,
                    "local_worker_recovery_auto_retry_exhausted": True,
                    "local_worker_recovery_manual_confirmation_required": True,
                }
            },
        }

        recovered, emitted, _persisted = self._run_cold_boot_recovery(run)

        self.assertEqual(recovered, ["run-1"])
        self.assertEqual(run["status"], "waiting_for_input")
        self.assertEqual(run["result_data"]["error"], "local_worker_recovery_exhausted")
        self.assertTrue(run["result_data"]["manual_confirmation_required"])
        self.assertTrue(run["context"]["metadata"]["local_worker_recovery_manual_confirmation_required"])
        self.assertEqual(emitted[0][2]["event"], "local_cold_boot_recovered_manual_gate")

    def test_cold_boot_recovery_ignores_unrelated_durable_live_runs(self) -> None:
        run = {
            "status": "running_local",
            "logs": object(),
            "browser_checkpoint": {"next_action_index": 4, "session_profile": "qa-browser"},
            "context": {
                "metadata": {
                    "execution_target_selected": "local_companion",
                    "local_worker_recovery_reason": "worker_lost",
                    "local_worker_recovery_attempt_count": 1,
                    "local_worker_recovery_backoff_seconds": 5,
                }
            },
        }

        with patch.object(local_queue.run_state_repository, "sync_list_live_runs_by_state", return_value=[{"run_id": "other-run"}]), \
             patch.object(local_queue.run_state_repository, "sync_get_live_run", return_value=None):
            recovered, _emitted, _persisted = self._run_cold_boot_recovery(run)

        self.assertEqual(recovered, ["run-1"])
        self.assertEqual(run["status"], "waiting_for_input")

    def test_cold_boot_recovery_skips_terminal_local_runs(self) -> None:
        run = {
            "status": "completed",
            "logs": object(),
            "browser_checkpoint": {"next_action_index": 4, "session_profile": "qa-browser"},
            "context": {"metadata": {"execution_target_selected": "local_companion"}},
        }

        recovered, _emitted, persisted = self._run_cold_boot_recovery(run)

        self.assertEqual(recovered, [])
        self.assertEqual(persisted, [])
        self.assertEqual(run["status"], "completed")

    def test_mark_ghost_enrollments_failed_after_timeout(self) -> None:
        persisted = []
        original_server = local_queue._server
        local_queue._server = SimpleNamespace(
            LOCAL_QUEUE_LOCK=__import__("threading").Lock(),
            LOCAL_WORKER_REGISTRY={
                "machine-1": {
                    "runtime_id": "machine-1",
                    "machine_id": "machine-1",
                    "enrollment_state": "installing",
                    "enrollment_requested_at": "2026-04-06T11:00:00Z",
                    "lease_seconds": 30,
                    "last_seen_at": None,
                }
            },
            _utc_now=lambda: datetime.fromisoformat("2026-04-06T11:06:00"),
            _utc_now_iso=lambda: "2026-04-06T11:06:00Z",
            _parse_utc_ts=lambda value: datetime.fromisoformat(str(value).replace("Z", "")) if value else None,
            ORION_LOCAL_LEASE_SECONDS=30,
        )
        try:
            with patch.object(local_queue, "_persist_local_runtime_state", side_effect=lambda: persisted.append(True)):
                failed = local_queue._mark_ghost_enrollments_failed()
        finally:
            local_queue._server = original_server

        self.assertEqual(failed, ["machine-1"])
        self.assertEqual(local_queue._server, original_server)
        self.assertTrue(persisted)

    def test_handle_heartbeat_local_run_emits_structured_computer_action_event(self) -> None:
        emitted = []
        original_server = local_queue._server
        run_id = "00000000-0000-0000-0000-000000000001"
        local_queue._server = SimpleNamespace(
            runs={run_id: {"logs": object()}},
            LOCAL_QUEUE_LOCK=__import__("threading").Lock(),
            LOCAL_CLAIMED_RUNS={},
            emit_log=lambda log_queue, level, message, **kwargs: emitted.append((level, message, kwargs)),
            _utc_now=lambda: datetime.fromisoformat("2026-04-07T10:00:00"),
            _utc_now_iso=lambda: "2026-04-07T10:00:00Z",
        )
        try:
            with patch.object(
                local_queue.worker_dispatch_service,
                "heartbeat_local_run",
                return_value={"last_heartbeat_at": "2026-04-07T10:00:00Z"},
            ):
                local_queue.handle_heartbeat_local_run(
                    local_queue.uuid.UUID(run_id),
                    local_queue.LocalRunHeartbeatPayload(
                        worker_id="worker-1",
                        note="Step 1/3",
                        event={
                            "event": "computer_action",
                            "message": "Step 1/3: Capturing screenshot",
                            "data": {"label": "Capturing screenshot", "step_number": 1, "step_total": 3},
                        },
                    ),
                )
        finally:
            local_queue._server = original_server

        self.assertEqual(emitted[0][0], "info")
        self.assertEqual(emitted[0][2]["event"], "computer_action")
        self.assertEqual(emitted[0][2]["data"]["step_total"], 3)


if __name__ == "__main__":
    unittest.main()

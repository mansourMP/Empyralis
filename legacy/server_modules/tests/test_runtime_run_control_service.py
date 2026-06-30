import unittest
from unittest import mock

from fastapi import HTTPException

from server_modules import runtime_run_control_service


class RuntimeRunControlServiceTests(unittest.TestCase):
    @staticmethod
    def _allow_run_api(command, payload, allow_approval_required=False):
        operation = str(payload.get("operation") or "").strip()
        return {
            "ok": True,
            "decision": "allow",
            "operation": operation,
            "next_action": "resume_run" if operation == "resume_run" else "pause_run",
        }

    def test_build_resume_waiting_run_callbacks_preserves_callables(self):
        serialize_run_snapshot = lambda run_id, run: {"run_id": run_id}
        callbacks = runtime_run_control_service.build_resume_waiting_run_callbacks(
            serialize_run_snapshot=serialize_run_snapshot,
            enforce_run_owner_access=lambda current_user, snapshot: None,
            get_pending_confirmation=lambda run: None,
            begin_run_pending_confirmation=lambda *args, **kwargs: {},
            emit_log=lambda *args, **kwargs: None,
            schedule_restored_run_resume=lambda run_id, run: True,
        )

        self.assertIs(callbacks["serialize_run_snapshot"], serialize_run_snapshot)
        self.assertIn("begin_run_pending_confirmation", callbacks)
        self.assertIn("schedule_restored_run_resume", callbacks)

    def test_resume_waiting_run_requires_checkpoint(self):
        with mock.patch.object(
            runtime_run_control_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=self._allow_run_api,
        ):
            with self.assertRaises(HTTPException):
                runtime_run_control_service.resume_waiting_run(
                    "run-1",
                    run={"status": "waiting_for_input", "logs": object()},
                    current_user={"user_id": "user-1"},
                    serialize_run_snapshot=lambda run_id, run: {"run_id": run_id},
                    enforce_run_owner_access=lambda current_user, snapshot: None,
                    get_pending_confirmation=lambda run: None,
                    begin_run_pending_confirmation=lambda *args, **kwargs: {},
                    emit_log=lambda *args, **kwargs: None,
                    schedule_restored_run_resume=lambda run_id, run: True,
                )

    def test_resume_waiting_run_returns_resume_payload(self):
        emitted = []

        with mock.patch.object(
            runtime_run_control_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=self._allow_run_api,
        ):
            payload = runtime_run_control_service.resume_waiting_run(
                "run-1",
                run={
                    "status": "waiting_for_input",
                    "logs": object(),
                    "browser_checkpoint": {
                        "next_action_index": 3,
                        "session_profile": "qa-browser",
                    },
                },
                current_user={"user_id": "user-1"},
                serialize_run_snapshot=lambda run_id, run: {"run_id": run_id},
                enforce_run_owner_access=lambda current_user, snapshot: None,
                get_pending_confirmation=lambda run: None,
                begin_run_pending_confirmation=lambda *args, **kwargs: {},
                emit_log=lambda *args, **kwargs: emitted.append(kwargs),
                schedule_restored_run_resume=lambda run_id, run: True,
            )

        self.assertEqual(payload["resume_kind"], "browser_checkpoint")
        self.assertEqual(payload["next_action_index"], 3)
        self.assertEqual(emitted[0]["event"], "browser_resume_requested")
        self.assertEqual(emitted[1]["event"], "computer_action")

    def test_resume_waiting_run_returns_local_execution_resume_payload(self):
        emitted = []
        run = {
            "status": "waiting_for_input",
            "logs": object(),
            "local_execution_checkpoint": {
                "kind": "local_execution_v1",
                "next_operation_index": 4,
                "total_operations": 8,
            },
            "result_data": {"manual_takeover": True},
            "context": {"metadata": {"manual_takeover": True}},
        }

        with mock.patch.object(
            runtime_run_control_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=self._allow_run_api,
        ):
            payload = runtime_run_control_service.resume_waiting_run(
                "run-1",
                run=run,
                current_user={"user_id": "user-1"},
                serialize_run_snapshot=lambda run_id, run: {"run_id": run_id},
                enforce_run_owner_access=lambda current_user, snapshot: None,
                get_pending_confirmation=lambda run: None,
                begin_run_pending_confirmation=lambda *args, **kwargs: {},
                emit_log=lambda *args, **kwargs: emitted.append(kwargs),
                schedule_restored_run_resume=lambda run_id, active_run: True,
            )

        self.assertEqual(payload["resume_kind"], "local_execution_checkpoint")
        self.assertEqual(payload["next_operation_index"], 4)
        self.assertFalse(run["result_data"].get("manual_takeover"))
        self.assertFalse(run["context"]["metadata"].get("manual_takeover"))
        self.assertEqual(emitted[0]["event"], "local_execution_resume_requested")
        self.assertEqual(emitted[1]["event"], "computer_action")

    def test_resume_waiting_run_requires_manual_confirmation_for_exhausted_local_recovery(self):
        prompted = []
        run = {
            "status": "waiting_for_input",
            "logs": object(),
            "browser_checkpoint": {
                "next_action_index": 7,
                "session_profile": "qa-browser",
            },
            "context": {
                "metadata": {
                    "local_worker_recovery_manual_confirmation_required": True,
                    "local_worker_recovery_attempt_count": 3,
                    "local_worker_recovery_max_auto_retries": 3,
                }
            },
        }

        with mock.patch.object(
            runtime_run_control_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=self._allow_run_api,
        ):
            payload = runtime_run_control_service.resume_waiting_run(
                "run-1",
                run=run,
                current_user={"user_id": "user-1"},
                serialize_run_snapshot=lambda run_id, run: {"run_id": run_id},
                enforce_run_owner_access=lambda current_user, snapshot: None,
                get_pending_confirmation=lambda run: None,
                begin_run_pending_confirmation=lambda run_id, prompt, **kwargs: prompted.append((run_id, prompt, kwargs)) or {
                    "approval_id": "approval-1",
                    "correlation_id": "corr-1",
                },
                emit_log=lambda *args, **kwargs: self.fail("manual confirmation path should not emit direct resume log"),
                schedule_restored_run_resume=lambda run_id, run: self.fail("manual confirmation path should not resume directly"),
            )

        self.assertEqual(payload["status"], "confirmation_required")
        self.assertEqual(payload["approval_id"], "approval-1")
        self.assertEqual(prompted[0][0], "run-1")
        self.assertEqual(prompted[0][2]["source"], "local_worker_recovery_resume")
        self.assertEqual(prompted[0][2]["metadata"]["kind"], "local_worker_recovery_resume")
        self.assertIn("automatic retry budget", prompted[0][1].lower())

    def test_resume_waiting_run_wrong_rust_next_action_blocks_before_resume(self):
        scheduled = {"called": False}
        with mock.patch.object(
            runtime_run_control_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "operation": "resume_run",
                "next_action": "pause_run",
            },
        ):
            with self.assertRaises(HTTPException) as raised:
                runtime_run_control_service.resume_waiting_run(
                    "run-1",
                    run={
                        "status": "waiting_for_input",
                        "logs": object(),
                        "browser_checkpoint": {"next_action_index": 3, "session_profile": "qa-browser"},
                    },
                    current_user={"user_id": "user-1"},
                    serialize_run_snapshot=lambda run_id, run: {"run_id": run_id},
                    enforce_run_owner_access=lambda current_user, snapshot: None,
                    get_pending_confirmation=lambda run: None,
                    begin_run_pending_confirmation=lambda *args, **kwargs: {},
                    emit_log=lambda *args, **kwargs: None,
                    schedule_restored_run_resume=lambda run_id, run: scheduled.update({"called": True}) or True,
                )

        self.assertEqual(raised.exception.status_code, 423)
        self.assertIn("unexpected next_action", str(raised.exception.detail))
        self.assertFalse(scheduled["called"])

    def test_pause_run_for_takeover_wrong_rust_next_action_blocks_before_pause(self):
        paused = {"called": False}
        with mock.patch.object(
            runtime_run_control_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "operation": "pause_run",
                "next_action": "resume_run",
            },
        ):
            with self.assertRaises(HTTPException) as raised:
                runtime_run_control_service.pause_run_for_takeover(
                    "run-1",
                    run={
                        "status": "running",
                        "logs": object(),
                        "local_worker_id": "worker-1",
                        "machine_id": "machine-1",
                    },
                    current_user={"user_id": "user-1"},
                    serialize_run_snapshot=lambda run_id, run: {"run_id": run_id},
                    enforce_run_owner_access=lambda current_user, snapshot: None,
                    pause_local_run=lambda *args, **kwargs: paused.update({"called": True}) or {"ok": True},
                )

        self.assertEqual(raised.exception.status_code, 423)
        self.assertIn("unexpected next_action", str(raised.exception.detail))
        self.assertFalse(paused["called"])

    def test_resolve_local_worker_recovery_approval_schedules_resume_on_approve(self):
        logs = []
        audits = []
        cleared = []
        run = {
            "logs": object(),
            "browser_checkpoint": {"next_action_index": 5, "session_profile": "qa-browser"},
            "context": {
                "metadata": {
                    "local_worker_recovery_manual_confirmation_required": True,
                    "local_worker_recovery_attempt_count": 3,
                }
            },
        }

        payload = runtime_run_control_service.resolve_local_worker_recovery_approval(
            "run-1",
            run,
            "approval-1",
            "approve",
            note="continue",
            get_pending_confirmation=lambda run: {"approval_id": "approval-1", "correlation_id": "corr-1"},
            clear_pending_confirmation=lambda run: cleared.append(True),
            approval_correlation_id=lambda approval_id, run_id=None: "corr-1",
            utc_now_iso=lambda: "2026-04-06T00:00:00Z",
            emit_log=lambda *args, **kwargs: logs.append(kwargs),
            append_approval_audit=lambda **kwargs: audits.append(kwargs),
            schedule_restored_run_resume=lambda run_id, live_run: True,
        )

        self.assertEqual(payload["decision_kind"], "approved")
        self.assertEqual(cleared, [True])
        self.assertFalse(run["context"]["metadata"]["local_worker_recovery_manual_confirmation_required"])
        self.assertEqual(logs[0]["event"], "local_worker_recovery_manual_resume_approved")
        self.assertEqual(audits[0]["stage"], "received")
        self.assertEqual(audits[1]["stage"], "resolved")

    def test_resolve_local_worker_recovery_approval_keeps_gate_after_reject(self):
        run = {
            "logs": object(),
            "browser_checkpoint": {"next_action_index": 5, "session_profile": "qa-browser"},
            "context": {
                "metadata": {
                    "local_worker_recovery_manual_confirmation_required": True,
                    "local_worker_recovery_attempt_count": 3,
                }
            },
        }

        payload = runtime_run_control_service.resolve_local_worker_recovery_approval(
            "run-1",
            run,
            "approval-1",
            "reject",
            get_pending_confirmation=lambda run: {"approval_id": "approval-1", "correlation_id": "corr-1"},
            clear_pending_confirmation=lambda run: None,
            approval_correlation_id=lambda approval_id, run_id=None: "corr-1",
            utc_now_iso=lambda: "2026-04-06T00:00:00Z",
            emit_log=lambda *args, **kwargs: None,
            append_approval_audit=lambda **kwargs: None,
            schedule_restored_run_resume=lambda run_id, live_run: self.fail("rejected recovery must not resume"),
        )

        self.assertEqual(payload["decision_kind"], "rejected")
        self.assertTrue(run["context"]["metadata"]["local_worker_recovery_manual_confirmation_required"])
        self.assertEqual(run["result_data"]["error"], "local_worker_recovery_manual_resume_declined")


if __name__ == "__main__":
    unittest.main()

import queue
import threading
import unittest
from unittest import mock

from fastapi import HTTPException

from server_modules import worker_dispatch_service


class WorkerDispatchLocalWorkerRustGateTests(unittest.TestCase):
    def test_claim_local_run_rust_denial_blocks_machine_lease_claim(self):
        denied = worker_dispatch_service.rust_runtime_kernel_client.RustKernelDecisionError(
            {
                "ok": False,
                "decision": "block",
                "reason": "local_worker_revoked",
            },
            command="local-worker-decision",
        )

        with mock.patch.object(
            worker_dispatch_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=denied,
        ) as rust_mock, mock.patch.object(
            worker_dispatch_service.machine_lease_service,
            "claim_local_machine_lease",
        ) as claim_mock:
            with self.assertRaises(HTTPException) as raised:
                worker_dispatch_service.claim_local_run(
                    "worker-1",
                    required_capabilities=[],
                    local_queue_lock=threading.Lock(),
                    pending_run_ids=["run-1"],
                    claimed_runs={},
                    worker_registry={},
                    runs_by_id={},
                    lease_seconds=60,
                    cleanup_stale_local_claims_fn=lambda: None,
                    ordered_runtime_preferences_for_run_fn=lambda run: [],
                    best_online_preferred_runtime_fn=lambda preferences: None,
                    required_capabilities_for_run_fn=lambda run: [],
                    normalize_capability_ids_fn=lambda value: [],
                    persist_local_runtime_state_fn=lambda: None,
                    mark_local_worker_seen_fn=lambda *args, **kwargs: None,
                    now_iso_fn=lambda: "2026-05-31T00:00:00Z",
                )

        self.assertEqual(raised.exception.status_code, 423)
        self.assertEqual(raised.exception.detail["reason"], "local_worker_revoked")
        rust_mock.assert_called_once()
        self.assertEqual(rust_mock.call_args.args[0], "local-worker-decision")
        self.assertEqual(rust_mock.call_args.args[1]["operation"], "claim_run")
        claim_mock.assert_not_called()

    def test_pause_local_run_rust_denial_blocks_lease_release(self):
        denied = worker_dispatch_service.rust_runtime_kernel_client.RustKernelDecisionError(
            {
                "ok": False,
                "decision": "block",
                "reason": "run_already_terminal",
            },
            command="local-worker-decision",
        )
        claimed_runs = {"run-1": {"worker_id": "worker-1"}}
        run = {
            "status": "claimed",
            "context": {"workspace_id": "ws-1", "metadata": {}},
            "logs": queue.Queue(),
        }

        with mock.patch.object(
            worker_dispatch_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=denied,
        ) as rust_mock, mock.patch.object(
            worker_dispatch_service.machine_lease_service,
            "release_machine_lease_claim",
        ) as release_mock:
            with self.assertRaises(HTTPException) as raised:
                worker_dispatch_service.pause_local_run(
                    "run-1",
                    worker_id="worker-1",
                    result_text=None,
                    result_data=None,
                    browser_checkpoint=None,
                    wait_reason=None,
                    runs_by_id={"run-1": run},
                    local_queue_lock=threading.Lock(),
                    claimed_runs=claimed_runs,
                    emit_log_fn=lambda *args, **kwargs: None,
                    mark_local_worker_seen_fn=lambda *args, **kwargs: None,
                    set_run_status_fn=lambda *args, **kwargs: None,
                    persist_local_runtime_state_fn=lambda: None,
                )

        self.assertEqual(raised.exception.status_code, 423)
        self.assertEqual(raised.exception.detail["reason"], "run_already_terminal")
        self.assertEqual(claimed_runs, {"run-1": {"worker_id": "worker-1"}})
        rust_mock.assert_called_once()
        self.assertEqual(rust_mock.call_args.args[1]["operation"], "pause_run")
        release_mock.assert_not_called()

    def test_complete_local_run_rust_denial_blocks_status_mutation(self):
        denied = worker_dispatch_service.rust_runtime_kernel_client.RustKernelDecisionError(
            {
                "ok": False,
                "decision": "block",
                "reason": "local_worker_kill_switch_active",
            },
            command="local-worker-decision",
        )
        set_status = mock.Mock()
        run = {
            "status": "claimed",
            "context": {"workspace_id": "ws-1", "metadata": {}},
            "logs": queue.Queue(),
        }

        with mock.patch.object(
            worker_dispatch_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=denied,
        ) as rust_mock:
            with self.assertRaises(HTTPException) as raised:
                worker_dispatch_service.complete_local_run(
                    "run-1",
                    worker_id="worker-1",
                    result_text="done",
                    result_data=None,
                    usage_masked=None,
                    runs_by_id={"run-1": run},
                    local_queue_lock=threading.Lock(),
                    claimed_runs={"run-1": {"worker_id": "worker-1"}},
                    emit_log_fn=lambda *args, **kwargs: None,
                    mark_local_worker_seen_fn=lambda *args, **kwargs: None,
                    set_run_status_fn=set_status,
                    persist_run_memory_fn=lambda *args, **kwargs: None,
                    persist_local_runtime_state_fn=lambda: None,
                )

        self.assertEqual(raised.exception.status_code, 423)
        self.assertEqual(raised.exception.detail["reason"], "local_worker_kill_switch_active")
        rust_mock.assert_called_once()
        self.assertEqual(rust_mock.call_args.args[1]["operation"], "complete_run")
        set_status.assert_not_called()

    def test_complete_local_run_state_transition_denial_blocks_status_mutation(self):
        state_denied = worker_dispatch_service.rust_runtime_kernel_client.RustKernelDecisionError(
            {
                "ok": False,
                "decision": "block",
                "reason": "invalid_state_transition",
            },
            command="state-transition-decision",
        )
        set_status = mock.Mock()
        run = {
            "status": "waiting_for_input",
            "context": {"tenant_id": "tenant-1", "workspace_id": "ws-1", "metadata": {}},
            "logs": queue.Queue(),
        }

        def rust_decision(command, payload, **kwargs):
            if command == "local-worker-decision":
                self.assertEqual(payload["operation"], "complete_run")
                return {"ok": True, "decision": "allow"}
            if command == "state-transition-decision":
                self.assertEqual(payload["operation"], "complete")
                self.assertEqual(payload["current_state"], "waiting_for_input")
                raise state_denied
            raise AssertionError(f"unexpected command {command}")

        with mock.patch.object(
            worker_dispatch_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=rust_decision,
        ), mock.patch.object(
            worker_dispatch_service.machine_lease_service,
            "enforce_queue_transition_decision",
        ) as queue_transition_mock:
            with self.assertRaises(HTTPException) as raised:
                worker_dispatch_service.complete_local_run(
                    "run-1",
                    worker_id="worker-1",
                    result_text="done",
                    result_data=None,
                    usage_masked=None,
                    runs_by_id={"run-1": run},
                    local_queue_lock=threading.Lock(),
                    claimed_runs={"run-1": {"worker_id": "worker-1"}},
                    emit_log_fn=lambda *args, **kwargs: None,
                    mark_local_worker_seen_fn=lambda *args, **kwargs: None,
                    set_run_status_fn=set_status,
                    persist_run_memory_fn=lambda *args, **kwargs: None,
                    persist_local_runtime_state_fn=lambda: None,
                )

        self.assertEqual(raised.exception.status_code, 423)
        self.assertEqual(raised.exception.detail["reason"], "invalid_state_transition")
        queue_transition_mock.assert_not_called()
        set_status.assert_not_called()

    def test_complete_local_run_wrong_state_transition_action_blocks_status_mutation(self):
        set_status = mock.Mock()
        run = {
            "status": "running_local",
            "context": {"tenant_id": "tenant-1", "workspace_id": "ws-1", "metadata": {}},
            "logs": queue.Queue(),
        }

        def rust_decision(command, payload, **kwargs):
            if command == "local-worker-decision":
                return {"ok": True, "decision": "allow", "next_action": "complete_local_run"}
            if command == "state-transition-decision":
                self.assertEqual(payload["operation"], "complete")
                return {
                    "ok": True,
                    "decision": "allow",
                    "next_action": "fail_state_transition",
                    "next_state": "completed",
                }
            raise AssertionError(f"unexpected command {command}")

        with mock.patch.object(
            worker_dispatch_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=rust_decision,
        ), mock.patch.object(
            worker_dispatch_service.machine_lease_service,
            "enforce_queue_transition_decision",
        ) as queue_transition_mock:
            with self.assertRaises(HTTPException) as raised:
                worker_dispatch_service.complete_local_run(
                    "run-1",
                    worker_id="worker-1",
                    result_text="done",
                    result_data=None,
                    usage_masked=None,
                    runs_by_id={"run-1": run},
                    local_queue_lock=threading.Lock(),
                    claimed_runs={"run-1": {"worker_id": "worker-1"}},
                    emit_log_fn=lambda *args, **kwargs: None,
                    mark_local_worker_seen_fn=lambda *args, **kwargs: None,
                    set_run_status_fn=set_status,
                    persist_run_memory_fn=lambda *args, **kwargs: None,
                    persist_local_runtime_state_fn=lambda: None,
                )

        self.assertEqual(raised.exception.status_code, 423)
        self.assertEqual(raised.exception.detail["reason"], "unexpected_next_action:fail_state_transition")
        queue_transition_mock.assert_not_called()
        set_status.assert_not_called()


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from unittest import mock

from fastapi import HTTPException

from server_modules import worker_dispatch_service


class WorkerDispatchServiceRustGateTests(unittest.TestCase):
    def test_claim_run_accepts_claim_local_run_action(self) -> None:
        with mock.patch.object(
            worker_dispatch_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "local_run_claim_allowed",
                "operation": "claim_run",
                "next_action": "claim_local_run",
            },
        ) as rust_gate:
            decision = worker_dispatch_service._enforce_local_worker_decision(
                operation="claim_run",
                workspace_id="ws-1",
                worker_id="worker-1",
                actor_role="worker",
                queue_empty=False,
                local_companion_enabled=True,
            )

        self.assertEqual(decision["next_action"], "claim_local_run")
        self.assertEqual(rust_gate.call_args.args[0], "local-worker-decision")

    def test_claim_run_accepts_return_backpressure_when_queue_empty(self) -> None:
        with mock.patch.object(
            worker_dispatch_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "local_queue_empty",
                "operation": "claim_run",
                "next_action": "return_backpressure",
            },
        ):
            decision = worker_dispatch_service._enforce_local_worker_decision(
                operation="claim_run",
                workspace_id="ws-1",
                worker_id="worker-1",
                actor_role="worker",
                queue_empty=True,
                local_companion_enabled=True,
            )

        self.assertEqual(decision["next_action"], "return_backpressure")

    def test_complete_run_wrong_rust_action_blocks(self) -> None:
        with mock.patch.object(
            worker_dispatch_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "local_run_complete_allowed",
                "operation": "complete_run",
                "next_action": "pause_local_run",
            },
        ):
            with self.assertRaises(HTTPException) as raised:
                worker_dispatch_service._enforce_local_worker_decision(
                    operation="complete_run",
                    workspace_id="ws-1",
                    worker_id="worker-1",
                    run_id="run-1",
                    actor_role="worker",
                    local_companion_enabled=True,
                )

        self.assertEqual(raised.exception.status_code, 423)
        self.assertEqual(
            raised.exception.detail["reason"],
            "unexpected_next_action:pause_local_run",
        )


if __name__ == "__main__":
    unittest.main()

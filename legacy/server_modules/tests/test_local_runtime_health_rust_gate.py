import unittest
from unittest.mock import patch

from server_modules import local_queue


class LocalRuntimeHealthRustGateTests(unittest.TestCase):
    def test_local_runtime_health_uses_rust_runtime_health_classification(self) -> None:
        runtime = {
            "runtime_id": "worker-1",
            "machine_id": "machine-1",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "runtime_role": "specialist",
            "lifecycle_state": "registered",
            "control_state": "active",
            "online": False,
            "status": "offline",
            "health_state": "healthy",
        }

        with patch.object(local_queue, "_runtime_status_item", return_value=runtime), patch.object(
            local_queue.rust_runtime_kernel_client,
            "runtime_kernel_available",
            return_value=True,
        ), patch.object(
            local_queue.rust_runtime_kernel_client,
            "runtime_health_decision",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "runtime_health_snapshot_normalized",
                "operation": "runtime_lane_health",
                "health": "degraded",
                "next_action": "investigate_runtime_health",
                "readiness": {"ready": False},
            },
        ) as rust_health_mock:
            result = local_queue.handle_get_local_runtime_health("worker-1")

        self.assertEqual(result["health_state"], "degraded")
        self.assertEqual(result["reported_health_state"], "healthy")
        self.assertFalse(result["readiness"]["ready"])
        rust_health_mock.assert_called_once()
        self.assertEqual(rust_health_mock.call_args.kwargs["operation"], "runtime_lane_health")
        self.assertEqual(rust_health_mock.call_args.kwargs["runtime"]["runtime_id"], "worker-1")
        self.assertEqual(rust_health_mock.call_args.kwargs["lane_queue"]["unavailable_worker_count"], 1)

    def test_local_runtime_health_blocks_unexpected_rust_next_action(self) -> None:
        runtime = {
            "runtime_id": "worker-2",
            "machine_id": "machine-2",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "online": True,
            "status": "online",
            "health_state": "healthy",
        }

        with patch.object(local_queue, "_runtime_status_item", return_value=runtime), patch.object(
            local_queue.rust_runtime_kernel_client,
            "runtime_kernel_available",
            return_value=True,
        ), patch.object(
            local_queue.rust_runtime_kernel_client,
            "runtime_health_decision",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "runtime_health_snapshot_normalized",
                "operation": "runtime_lane_health",
                "health": "healthy",
                "next_action": "runtime_lane_health",
                "readiness": {"ready": True},
            },
        ):
            with self.assertRaisesRegex(RuntimeError, "unexpected_next_action:runtime_lane_health"):
                local_queue.handle_get_local_runtime_health("worker-2")


if __name__ == "__main__":
    unittest.main()

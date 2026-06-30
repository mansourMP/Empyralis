import unittest
from unittest import mock

from fastapi import HTTPException

from server_modules import memory_service
from server_modules.rust_runtime_kernel_client import RustKernelDecisionError


class MemoryServiceRustGateTests(unittest.TestCase):
    def test_save_memory_calls_rust_before_store_write(self):
        with mock.patch.object(
            memory_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "runtime_state_store_policy_satisfied",
                "operation": "upsert_workspace_memory",
                "next_action": "write_workspace_memory",
            },
        ) as rust_gate, mock.patch.object(
            memory_service._workspace_memory_store,
            "_save_memory",
        ) as save_memory:
            memory_service.save_memory("ws-1", "preference", "Use concise answers.")

        rust_gate.assert_called_once()
        command, payload = rust_gate.call_args.args
        self.assertEqual(command, "runtime-state-store-decision")
        self.assertEqual(payload["operation"], "upsert_workspace_memory")
        self.assertEqual(payload["state_class"], "workspace_memory")
        self.assertEqual(payload["workspace_id"], "ws-1")
        save_memory.assert_called_once()

    def test_save_memory_unexpected_rust_next_action_blocks_store_write(self):
        with mock.patch.object(
            memory_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "runtime_state_store_policy_satisfied",
                "operation": "upsert_workspace_memory",
                "next_action": "write_state",
            },
        ), mock.patch.object(
            memory_service._workspace_memory_store,
            "_save_memory",
        ) as save_memory:
            with self.assertRaises(HTTPException) as raised:
                memory_service.save_memory("ws-1", "preference", "Use concise answers.")

        self.assertEqual(raised.exception.status_code, 423)
        self.assertIn("unexpected next_action", raised.exception.detail["reason"])
        save_memory.assert_not_called()

    def test_delete_memory_rust_denial_blocks_store_delete(self):
        denied = RustKernelDecisionError(
            {
                "ok": False,
                "decision": "block",
                "reason": "retention_lock_blocks_delete",
                "operation": "delete_workspace_memory",
            },
            command="runtime-state-store-decision",
        )
        with mock.patch.object(
            memory_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=denied,
        ), mock.patch.object(
            memory_service._workspace_memory_store,
            "_delete_memory",
        ) as delete_memory:
            with self.assertRaises(HTTPException) as raised:
                memory_service.delete_memory("ws-1", "preference")

        self.assertEqual(raised.exception.status_code, 423)
        delete_memory.assert_not_called()

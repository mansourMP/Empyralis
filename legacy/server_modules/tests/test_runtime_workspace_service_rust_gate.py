import unittest
from unittest import mock

from fastapi import HTTPException

from server_modules import runtime_workspace_service
from server_modules.rust_runtime_kernel_client import RustKernelDecisionError


class RuntimeWorkspaceServiceRustGateTests(unittest.TestCase):
    def test_delete_workspace_memory_calls_rust_before_delete(self):
        delete_memory = mock.Mock(return_value=True)
        with mock.patch.object(
            runtime_workspace_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "runtime_state_store_policy_satisfied",
                "operation": "delete_workspace_memory",
            },
        ) as rust_gate:
            result = runtime_workspace_service.delete_workspace_memory_payload(
                "ws-1",
                "memory-key",
                delete_memory=delete_memory,
            )

        rust_gate.assert_called_once()
        command, payload = rust_gate.call_args.args
        self.assertEqual(command, "runtime-state-store-decision")
        self.assertEqual(payload["operation"], "delete_workspace_memory")
        self.assertEqual(payload["state_class"], "workspace_memory")
        self.assertEqual(payload["workspace_id"], "ws-1")
        delete_memory.assert_called_once_with("ws-1", "memory-key")
        self.assertTrue(result["ok"])

    def test_delete_workspace_memory_rust_denial_blocks_delete(self):
        denied = RustKernelDecisionError(
            {
                "ok": False,
                "decision": "block",
                "reason": "workspace_access_denied",
                "operation": "delete_workspace_memory",
            },
            command="runtime-state-store-decision",
        )
        delete_memory = mock.Mock(return_value=True)
        with mock.patch.object(
            runtime_workspace_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=denied,
        ):
            with self.assertRaises(HTTPException) as raised:
                runtime_workspace_service.delete_workspace_memory_payload(
                    "ws-1",
                    "memory-key",
                    delete_memory=delete_memory,
                )

        self.assertEqual(raised.exception.status_code, 423)
        delete_memory.assert_not_called()

    def test_update_workspace_context_file_rust_denial_blocks_write(self):
        denied = RustKernelDecisionError(
            {
                "ok": False,
                "decision": "block",
                "reason": "owner_access_denied",
                "operation": "update_workspace_context_file",
            },
            command="runtime-state-store-decision",
        )
        write_context = mock.Mock(return_value={"filename": "notes.md", "content": "updated"})
        with mock.patch.object(
            runtime_workspace_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=denied,
        ):
            with self.assertRaises(HTTPException) as raised:
                runtime_workspace_service.update_workspace_context_file_payload(
                    "notes.md",
                    {"content": "updated"},
                    write_workspace_context_file=write_context,
                )

        self.assertEqual(raised.exception.status_code, 423)
        write_context.assert_not_called()

    def test_delete_workspace_memory_unexpected_next_action_blocks_delete(self):
        delete_memory = mock.Mock(return_value=True)
        with mock.patch.object(
            runtime_workspace_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "runtime_state_store_policy_satisfied",
                "operation": "delete_workspace_memory",
                "next_action": "write_workspace_memory",
            },
        ):
            with self.assertRaises(HTTPException) as raised:
                runtime_workspace_service.delete_workspace_memory_payload(
                    "ws-1",
                    "memory-key",
                    delete_memory=delete_memory,
                )

        self.assertEqual(raised.exception.status_code, 423)
        self.assertIn("unexpected next_action", str(raised.exception.detail["reason"]))
        delete_memory.assert_not_called()

    def test_update_workspace_context_file_unexpected_next_action_blocks_write(self):
        write_context = mock.Mock(return_value={"filename": "notes.md", "content": "updated"})
        with mock.patch.object(
            runtime_workspace_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "runtime_state_store_policy_satisfied",
                "operation": "update_workspace_context_file",
                "next_action": "initialize_workspace_context_file",
            },
        ):
            with self.assertRaises(HTTPException) as raised:
                runtime_workspace_service.update_workspace_context_file_payload(
                    "notes.md",
                    {"content": "updated"},
                    write_workspace_context_file=write_context,
                )

        self.assertEqual(raised.exception.status_code, 423)
        self.assertIn("unexpected next_action", str(raised.exception.detail["reason"]))
        write_context.assert_not_called()

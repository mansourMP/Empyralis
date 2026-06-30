from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from server_modules import mcp_registry_service


_ALLOW_DECISION = {
    "ok": True,
    "decision": "allow",
    "reason": "runtime_state_store_policy_satisfied",
    "operation": "runtime-state-store-decision",
    "next_action": "save_mcp_server_registry",
    "approval_required": False,
    "cacheable": False,
    "audit_visibility": "standard",
}


class McpRegistryServiceRustGateTests(unittest.TestCase):
    def test_save_registry_calls_rust_before_persisting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            registry_file = Path(temp_dir) / "mcp_servers.json"
            with mock.patch.object(mcp_registry_service, "MCP_SERVER_REGISTRY_FILE", registry_file), mock.patch.object(
                mcp_registry_service.rust_runtime_kernel_client,
                "runtime_state_store_decision",
                return_value=dict(_ALLOW_DECISION),
            ) as rust_decision:
                result = mcp_registry_service.save_mcp_server_registry({
                    "workspaces": {"workspace-1": {"servers": {}}},
                    "updated_at": "now",
                })

            self.assertTrue(registry_file.exists())
            self.assertIn("workspace-1", result["workspaces"])
            payload = rust_decision.call_args.kwargs
            self.assertEqual(payload["operation"], "save_mcp_server_registry")
            self.assertEqual(payload["state_class"], "mcp_server_registry")
            self.assertEqual(payload["payload"]["workspaces"], result["workspaces"])

    def test_save_registry_blocks_before_file_write_when_rust_blocks(self) -> None:
        block_decision = {
            **_ALLOW_DECISION,
            "ok": False,
            "decision": "block",
            "reason": "owner_access_denied",
            "operation": "save_mcp_server_registry",
            "audit_visibility": "security",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            registry_file = Path(temp_dir) / "mcp_servers.json"
            with mock.patch.object(mcp_registry_service, "MCP_SERVER_REGISTRY_FILE", registry_file), mock.patch.object(
                mcp_registry_service.rust_runtime_kernel_client,
                "runtime_state_store_decision",
                return_value=block_decision,
            ):
                with self.assertRaises(mcp_registry_service.McpRegistryRustGateError):
                    mcp_registry_service.save_mcp_server_registry({"workspaces": {}})

            self.assertFalse(registry_file.exists())

    def test_save_registry_blocks_before_file_write_on_wrong_rust_action(self) -> None:
        wrong_action = {
            **_ALLOW_DECISION,
            "next_action": "write_profile_api_file",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            registry_file = Path(temp_dir) / "mcp_servers.json"
            with mock.patch.object(mcp_registry_service, "MCP_SERVER_REGISTRY_FILE", registry_file), mock.patch.object(
                mcp_registry_service.rust_runtime_kernel_client,
                "runtime_state_store_decision",
                return_value=wrong_action,
            ):
                with self.assertRaises(mcp_registry_service.McpRegistryRustGateError) as raised:
                    mcp_registry_service.save_mcp_server_registry({"workspaces": {}})

            self.assertEqual(str(raised.exception), "unexpected_next_action")
            self.assertFalse(registry_file.exists())


if __name__ == "__main__":
    unittest.main()

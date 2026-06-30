from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from server_modules import deployed_agent_service


_ALLOW_DECISION = {
    "ok": True,
    "decision": "allow",
    "reason": "runtime_state_store_policy_satisfied",
    "operation": "runtime-state-store-decision",
    "next_action": "write_deployed_agent_knowledge_file",
    "approval_required": False,
    "cacheable": False,
    "audit_visibility": "standard",
}


class DeployedAgentKnowledgeFileRustGateTests(unittest.TestCase):
    def test_knowledge_file_gate_calls_rust_before_file_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "guide.md"
            with mock.patch.object(
                deployed_agent_service.rust_runtime_kernel_client,
                "runtime_state_store_decision",
                return_value=dict(_ALLOW_DECISION),
            ) as rust_decision:
                deployed_agent_service._enforce_deployed_agent_knowledge_file_write(
                    deployed_agent_id="agent-1",
                    workspace_id="workspace-1",
                    destination=destination,
                    safe_filename="guide.md",
                    content_hash="abc123",
                    content_bytes=42,
                )

            payload = rust_decision.call_args.kwargs
            self.assertEqual(payload["operation"], "write_deployed_agent_knowledge_file")
            self.assertEqual(payload["state_class"], "deployed_agent_knowledge_files")
            self.assertEqual(payload["workspace_id"], "workspace-1")

    def test_knowledge_file_gate_blocks_when_rust_returns_wrong_action(self) -> None:
        wrong_action = {
            **_ALLOW_DECISION,
            "next_action": "write_profile_api_file",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "guide.md"
            with mock.patch.object(
                deployed_agent_service.rust_runtime_kernel_client,
                "runtime_state_store_decision",
                return_value=wrong_action,
            ):
                with self.assertRaises(deployed_agent_service.DeployedAgentKnowledgeRustGateError) as raised:
                    deployed_agent_service._enforce_deployed_agent_knowledge_file_write(
                        deployed_agent_id="agent-1",
                        workspace_id="workspace-1",
                        destination=destination,
                        safe_filename="guide.md",
                        content_hash="abc123",
                        content_bytes=42,
                    )

            self.assertFalse(destination.exists())
            self.assertEqual(str(raised.exception), "unexpected_next_action")


if __name__ == "__main__":
    unittest.main()

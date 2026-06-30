from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from server_modules import agent_computer_profile_service, workspace_context


_ALLOW_DECISION = {
    "ok": True,
    "decision": "allow",
    "reason": "runtime_state_store_policy_satisfied",
    "operation": "runtime-state-store-decision",
    "next_action": "write_state",
    "approval_required": False,
    "cacheable": False,
    "audit_visibility": "standard",
}


class WorkspaceContextProfileRustGateTests(unittest.TestCase):
    def test_workspace_context_write_calls_rust_before_persisting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "workspace"
            with mock.patch.object(workspace_context, "_WORKSPACE_DIR", root), mock.patch.object(
                workspace_context.rust_runtime_kernel_client,
                "runtime_state_store_decision",
                return_value=dict(_ALLOW_DECISION),
            ) as rust_decision:
                result = workspace_context.write_workspace_context_file(
                    "USER.md",
                    "# User\n",
                    workspace_id="workspace-1",
                )

            self.assertEqual(result["filename"], "USER.md")
            self.assertTrue(Path(result["path"]).exists())
            payload = rust_decision.call_args.kwargs
            self.assertEqual(payload["operation"], "save_workspace_context_file")
            self.assertEqual(payload["state_class"], "workspace_context_files")
            self.assertEqual(payload["workspace_id"], "workspace-1")
            self.assertEqual(payload["payload"]["filename"], "USER.md")

    def test_workspace_context_write_blocks_before_file_write_when_rust_blocks(self) -> None:
        block_decision = {
            **_ALLOW_DECISION,
            "ok": False,
            "decision": "block",
            "reason": "owner_access_denied",
            "operation": "save_workspace_context_file",
            "audit_visibility": "security",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "workspace"
            with mock.patch.object(workspace_context, "_WORKSPACE_DIR", root), mock.patch.object(
                workspace_context.rust_runtime_kernel_client,
                "runtime_state_store_decision",
                return_value=block_decision,
            ):
                with self.assertRaises(workspace_context.WorkspaceContextRustGateError):
                    workspace_context.write_workspace_context_file(
                        "USER.md",
                        "# User\n",
                        workspace_id="workspace-1",
                    )

            self.assertFalse((root / "workspace-1" / "USER.md").exists())

    def test_agent_computer_profile_state_calls_rust_before_persisting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with mock.patch.object(
                agent_computer_profile_service.workspace_context,
                "workspace_scope_dir",
                side_effect=lambda workspace_id: root / str(workspace_id or "default"),
            ), mock.patch.object(
                agent_computer_profile_service.rust_runtime_kernel_client,
                "runtime_state_store_decision",
                return_value=dict(_ALLOW_DECISION),
            ) as rust_decision:
                profile = agent_computer_profile_service.create_agent_computer_profile({
                    "workspace_id": "workspace-1",
                    "policy_id": "default",
                    "machine_label": "Cloud runtime",
                    "environment_kind": "cloud_computer",
                })

            self.assertTrue((root / "workspace-1" / "agent_computer_profiles.json").exists())
            self.assertEqual(profile.workspace_id, "workspace-1")
            payload = rust_decision.call_args.kwargs
            self.assertEqual(payload["operation"], "save_agent_computer_profile_state")
            self.assertEqual(payload["state_class"], "agent_computer_profiles")
            self.assertEqual(payload["workspace_id"], "workspace-1")

    def test_agent_computer_profile_missing_read_is_not_a_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with mock.patch.object(
                agent_computer_profile_service.workspace_context,
                "workspace_scope_dir",
                side_effect=lambda workspace_id: root / str(workspace_id or "default"),
            ):
                profile = agent_computer_profile_service.get_agent_computer_profile(
                    workspace_id="workspace-1",
                    profile_id="acp_missing",
                )

            self.assertIsNone(profile)
            self.assertFalse((root / "workspace-1" / "agent_computer_profiles.json").exists())


if __name__ == "__main__":
    unittest.main()

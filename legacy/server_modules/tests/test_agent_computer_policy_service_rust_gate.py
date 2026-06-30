from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from server_modules import agent_computer_policy_service


_ALLOW_DECISION = {
    "ok": True,
    "decision": "allow",
    "reason": "runtime_state_store_policy_satisfied",
    "operation": "runtime-state-store-decision",
    "next_action": "upsert_agent_computer_policy",
    "approval_required": False,
    "cacheable": False,
    "audit_visibility": "standard",
}


class AgentComputerPolicyServiceRustGateTests(unittest.TestCase):
    def _workspace_scope(self, root: Path):
        return lambda workspace_id: root / str(workspace_id or "default")

    def test_upsert_policy_calls_rust_before_persisting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            policy = agent_computer_policy_service.build_default_agent_computer_policy(
                policy_id="default",
                autonomy_mode=agent_computer_policy_service.AUTONOMY_ASK_EVERY_TIME,
            )
            with mock.patch.object(
                agent_computer_policy_service.workspace_context,
                "workspace_scope_dir",
                side_effect=self._workspace_scope(root),
            ), mock.patch.object(
                agent_computer_policy_service.rust_runtime_kernel_client,
                "runtime_state_store_decision",
                return_value=dict(_ALLOW_DECISION),
            ) as rust_decision:
                saved = agent_computer_policy_service.upsert_agent_computer_policy(
                    workspace_id="workspace-1",
                    policy=policy,
                )

            state_path = root / "workspace-1" / "agent_computer_policies.json"
            self.assertTrue(state_path.exists())
            self.assertEqual(saved.policy_id, "default")
            payload = rust_decision.call_args.kwargs
            self.assertEqual(payload["operation"], "upsert_agent_computer_policy")
            self.assertEqual(payload["state_class"], "agent_computer_policy")
            self.assertEqual(payload["workspace_id"], "workspace-1")
            self.assertEqual(payload["payload"]["policy_id"], "default")
            self.assertEqual(payload["payload"]["policy"]["autonomy_mode"], agent_computer_policy_service.AUTONOMY_ASK_EVERY_TIME)

    def test_upsert_policy_blocks_before_file_mutation_when_rust_blocks(self) -> None:
        block_decision = {
            **_ALLOW_DECISION,
            "ok": False,
            "decision": "block",
            "reason": "owner_access_denied",
            "operation": "upsert_agent_computer_policy",
            "audit_visibility": "security",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            policy = agent_computer_policy_service.build_default_agent_computer_policy(
                policy_id="default",
                autonomy_mode=agent_computer_policy_service.AUTONOMY_ASK_EVERY_TIME,
            )
            with mock.patch.object(
                agent_computer_policy_service.workspace_context,
                "workspace_scope_dir",
                side_effect=self._workspace_scope(root),
            ), mock.patch.object(
                agent_computer_policy_service.rust_runtime_kernel_client,
                "runtime_state_store_decision",
                return_value=block_decision,
            ):
                with self.assertRaises(agent_computer_policy_service.AgentComputerPolicyRustGateError):
                    agent_computer_policy_service.upsert_agent_computer_policy(
                        workspace_id="workspace-1",
                        policy=policy,
                    )

            self.assertFalse((root / "workspace-1" / "agent_computer_policies.json").exists())

    def test_upsert_policy_blocks_before_file_mutation_on_wrong_rust_action(self) -> None:
        wrong_action = {
            **_ALLOW_DECISION,
            "next_action": "write_profile_api_file",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            policy = agent_computer_policy_service.build_default_agent_computer_policy(
                policy_id="default",
                autonomy_mode=agent_computer_policy_service.AUTONOMY_ASK_EVERY_TIME,
            )
            with mock.patch.object(
                agent_computer_policy_service.workspace_context,
                "workspace_scope_dir",
                side_effect=self._workspace_scope(root),
            ), mock.patch.object(
                agent_computer_policy_service.rust_runtime_kernel_client,
                "runtime_state_store_decision",
                return_value=wrong_action,
            ):
                with self.assertRaises(agent_computer_policy_service.AgentComputerPolicyRustGateError) as raised:
                    agent_computer_policy_service.upsert_agent_computer_policy(
                        workspace_id="workspace-1",
                        policy=policy,
                    )

            self.assertEqual(str(raised.exception), "unexpected_next_action")
            self.assertFalse((root / "workspace-1" / "agent_computer_policies.json").exists())

    def test_missing_policy_file_read_is_not_a_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with mock.patch.object(
                agent_computer_policy_service.workspace_context,
                "workspace_scope_dir",
                side_effect=self._workspace_scope(root),
            ):
                policy = agent_computer_policy_service.get_saved_agent_computer_policy(
                    workspace_id="workspace-1",
                    policy_id="default",
                )

            self.assertIsNone(policy)
            self.assertFalse((root / "workspace-1" / "agent_computer_policies.json").exists())


if __name__ == "__main__":
    unittest.main()

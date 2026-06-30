from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from server_modules import sage_profile_service


_ALLOW_DECISION = {
    "ok": True,
    "decision": "allow",
    "reason": "runtime_state_store_policy_satisfied",
    "operation": "runtime-state-store-decision",
    "next_action": "upsert_sage_profile",
    "approval_required": False,
    "cacheable": False,
    "audit_visibility": "standard",
}


class SageProfileServiceRustGateTests(unittest.TestCase):
    def _workspace_scope(self, root: Path):
        return lambda workspace_id: root / str(workspace_id or "default")

    def test_upsert_sage_profile_calls_rust_before_persisting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with mock.patch.object(
                sage_profile_service.workspace_context,
                "workspace_scope_dir",
                side_effect=self._workspace_scope(root),
            ), mock.patch.object(
                sage_profile_service.rust_runtime_kernel_client,
                "runtime_state_store_decision",
                return_value=dict(_ALLOW_DECISION),
            ) as rust_decision, mock.patch.object(
                sage_profile_service,
                "sync_profile_context_files",
                return_value={},
            ):
                result = sage_profile_service.upsert_sage_profile(
                    workspace_id="workspace-1",
                    actor_user_id="owner-1",
                    user_name="Mansur",
                )

            self.assertEqual(result["profile"]["user_name"], "Mansur")
            self.assertTrue((root / "workspace-1" / "sage_profile.json").exists())
            payload = rust_decision.call_args.kwargs
            self.assertEqual(payload["operation"], "upsert_sage_profile")
            self.assertEqual(payload["state_class"], "sage_profile")
            self.assertEqual(payload["workspace_id"], "workspace-1")
            self.assertEqual(payload["actor_id"], "owner-1")

    def test_upsert_sage_profile_blocks_before_file_mutation_when_rust_blocks(self) -> None:
        block_decision = {
            **_ALLOW_DECISION,
            "ok": False,
            "decision": "block",
            "reason": "owner_access_denied",
            "operation": "upsert_sage_profile",
            "audit_visibility": "security",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with mock.patch.object(
                sage_profile_service.workspace_context,
                "workspace_scope_dir",
                side_effect=self._workspace_scope(root),
            ), mock.patch.object(
                sage_profile_service.rust_runtime_kernel_client,
                "runtime_state_store_decision",
                return_value=block_decision,
            ):
                with self.assertRaises(sage_profile_service.SageProfileRustGateError):
                    sage_profile_service.upsert_sage_profile(
                        workspace_id="workspace-1",
                        actor_user_id="owner-1",
                        user_name="Mansur",
                    )

            self.assertFalse((root / "workspace-1" / "sage_profile.json").exists())

    def test_upsert_sage_profile_blocks_before_file_mutation_on_wrong_rust_action(self) -> None:
        wrong_action = {
            **_ALLOW_DECISION,
            "next_action": "write_profile_api_file",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with mock.patch.object(
                sage_profile_service.workspace_context,
                "workspace_scope_dir",
                side_effect=self._workspace_scope(root),
            ), mock.patch.object(
                sage_profile_service.rust_runtime_kernel_client,
                "runtime_state_store_decision",
                return_value=wrong_action,
            ):
                with self.assertRaises(sage_profile_service.SageProfileRustGateError) as raised:
                    sage_profile_service.upsert_sage_profile(
                        workspace_id="workspace-1",
                        actor_user_id="owner-1",
                        user_name="Mansur",
                    )

            self.assertEqual(str(raised.exception), "unexpected_next_action")
            self.assertFalse((root / "workspace-1" / "sage_profile.json").exists())

    def test_missing_profile_read_is_not_a_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with mock.patch.object(
                sage_profile_service.workspace_context,
                "workspace_scope_dir",
                side_effect=self._workspace_scope(root),
            ):
                result = sage_profile_service.list_sage_profile(workspace_id="workspace-1")

            self.assertEqual(result["profile"]["user_name"], "")
            self.assertFalse((root / "workspace-1" / "sage_profile.json").exists())

    def test_projection_write_uses_existing_workspace_context_rust_operation(self) -> None:
        with mock.patch.object(
            sage_profile_service.workspace_context,
            "read_workspace_context_files",
            return_value={},
        ), mock.patch.object(
            sage_profile_service.workspace_context,
            "write_workspace_context_file",
            return_value={"filename": "USER.md"},
        ) as context_write, mock.patch.object(
            sage_profile_service.rust_runtime_kernel_client,
            "runtime_state_store_decision",
            return_value=dict(_ALLOW_DECISION),
        ) as rust_decision:
            sage_profile_service.sync_profile_context_files(
                workspace_id="workspace-1",
                profile={"user_name": "Mansur"},
            )

        self.assertTrue(context_write.called)
        operations = [call.kwargs["operation"] for call in rust_decision.call_args_list]
        self.assertIn("update_workspace_context_file", operations)


if __name__ == "__main__":
    unittest.main()

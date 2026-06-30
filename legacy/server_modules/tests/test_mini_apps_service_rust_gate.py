from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from server_modules import mini_apps_service


_ALLOW_DECISION = {
    "ok": True,
    "decision": "allow",
    "reason": "runtime_state_store_policy_satisfied",
    "operation": "runtime-state-store-decision",
    "next_action": "save_mini_apps_state",
    "approval_required": False,
    "cacheable": False,
    "audit_visibility": "standard",
}


class MiniAppsServiceRustGateTests(unittest.TestCase):
    def _workspace_scope(self, root: Path):
        return lambda workspace_id: root / str(workspace_id or "default")

    def test_save_state_calls_rust_before_atomic_replace(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with mock.patch.object(
                mini_apps_service.workspace_context,
                "workspace_scope_dir",
                side_effect=self._workspace_scope(root),
            ), mock.patch.object(
                mini_apps_service.rust_runtime_kernel_client,
                "runtime_state_store_decision",
                return_value=dict(_ALLOW_DECISION),
            ) as rust_decision:
                result = mini_apps_service._save_state("workspace-1", {"apps": {"flashcards": {"id": "flashcards"}}})

            state_path = root / "workspace-1" / "mini_apps.json"
            self.assertTrue(state_path.exists())
            self.assertIn("flashcards", result["apps"])
            payload = rust_decision.call_args.kwargs
            self.assertEqual(payload["operation"], "save_mini_apps_state")
            self.assertEqual(payload["state_class"], "mini_apps")
            self.assertEqual(payload["workspace_id"], "workspace-1")

    def test_save_state_blocks_before_file_write_when_rust_blocks(self) -> None:
        block_decision = {
            **_ALLOW_DECISION,
            "ok": False,
            "decision": "block",
            "reason": "owner_access_denied",
            "operation": "save_mini_apps_state",
            "audit_visibility": "security",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with mock.patch.object(
                mini_apps_service.workspace_context,
                "workspace_scope_dir",
                side_effect=self._workspace_scope(root),
            ), mock.patch.object(
                mini_apps_service.rust_runtime_kernel_client,
                "runtime_state_store_decision",
                return_value=block_decision,
            ):
                with self.assertRaises(mini_apps_service.MiniAppsRustGateError):
                    mini_apps_service._save_state("workspace-1", {"apps": {}})

            self.assertFalse((root / "workspace-1" / "mini_apps.json").exists())

    def test_save_state_blocks_before_file_write_on_wrong_rust_action(self) -> None:
        wrong_action = {
            **_ALLOW_DECISION,
            "next_action": "write_profile_api_file",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with mock.patch.object(
                mini_apps_service.workspace_context,
                "workspace_scope_dir",
                side_effect=self._workspace_scope(root),
            ), mock.patch.object(
                mini_apps_service.rust_runtime_kernel_client,
                "runtime_state_store_decision",
                return_value=wrong_action,
            ):
                with self.assertRaises(mini_apps_service.MiniAppsRustGateError) as raised:
                    mini_apps_service._save_state("workspace-1", {"apps": {}})

            self.assertEqual(str(raised.exception), "unexpected_next_action")
            self.assertFalse((root / "workspace-1" / "mini_apps.json").exists())


if __name__ == "__main__":
    unittest.main()

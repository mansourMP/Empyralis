from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from server_modules import installed_skills


_ALLOW_DECISION = {
    "ok": True,
    "decision": "allow",
    "reason": "runtime_state_store_policy_satisfied",
    "operation": "runtime-state-store-decision",
    "next_action": "save_installed_skill_registry",
    "approval_required": False,
    "cacheable": False,
    "audit_visibility": "standard",
}


class InstalledSkillsRustGateTests(unittest.TestCase):
    def test_save_installed_skill_registry_calls_rust_before_persisting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            registry_file = Path(temp_dir) / ".registry.json"
            with mock.patch.object(installed_skills, "installed_skill_registry_file", return_value=registry_file), mock.patch.object(
                installed_skills.rust_runtime_kernel_client,
                "runtime_state_store_decision",
                return_value=dict(_ALLOW_DECISION),
            ) as rust_decision:
                result = installed_skills.save_installed_skill_registry({
                    "items": {"demo": {"enabled": True}},
                    "workspace_overrides": {},
                    "updated_at": "now",
                })

            self.assertTrue(registry_file.exists())
            self.assertIn("demo", result["items"])
            payload = rust_decision.call_args.kwargs
            self.assertEqual(payload["operation"], "save_installed_skill_registry")
            self.assertEqual(payload["state_class"], "installed_skill_registry")
            self.assertEqual(payload["payload"]["items"], result["items"])

    def test_save_installed_skill_registry_blocks_before_file_write_when_rust_blocks(self) -> None:
        block_decision = {
            **_ALLOW_DECISION,
            "ok": False,
            "decision": "block",
            "reason": "owner_access_denied",
            "operation": "save_installed_skill_registry",
            "audit_visibility": "security",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            registry_file = Path(temp_dir) / ".registry.json"
            with mock.patch.object(installed_skills, "installed_skill_registry_file", return_value=registry_file), mock.patch.object(
                installed_skills.rust_runtime_kernel_client,
                "runtime_state_store_decision",
                return_value=block_decision,
            ):
                with self.assertRaises(installed_skills.InstalledSkillsRustGateError):
                    installed_skills.save_installed_skill_registry({"items": {}})

            self.assertFalse(registry_file.exists())

    def test_save_installed_skill_registry_blocks_before_file_write_on_wrong_rust_action(self) -> None:
        wrong_action = {
            **_ALLOW_DECISION,
            "next_action": "write_profile_api_file",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            registry_file = Path(temp_dir) / ".registry.json"
            with mock.patch.object(installed_skills, "installed_skill_registry_file", return_value=registry_file), mock.patch.object(
                installed_skills.rust_runtime_kernel_client,
                "runtime_state_store_decision",
                return_value=wrong_action,
            ):
                with self.assertRaises(installed_skills.InstalledSkillsRustGateError) as raised:
                    installed_skills.save_installed_skill_registry({"items": {}})

            self.assertEqual(str(raised.exception), "unexpected_next_action")
            self.assertFalse(registry_file.exists())


if __name__ == "__main__":
    unittest.main()

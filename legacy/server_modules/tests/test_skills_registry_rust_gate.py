from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from server_modules import skills_registry


_ALLOW_DECISION = {
    "ok": True,
    "decision": "allow",
    "reason": "runtime_state_store_policy_satisfied",
    "operation": "runtime-state-store-decision",
    "next_action": "write_skills_registry_file",
    "approval_required": False,
    "cacheable": False,
    "audit_visibility": "standard",
}


class SkillsRegistryRustGateTests(unittest.TestCase):
    def test_skills_registry_write_calls_rust_before_persisting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "registry.json"
            with mock.patch(
                "server_modules.rust_runtime_kernel_client.runtime_state_store_decision",
                return_value=dict(_ALLOW_DECISION),
            ) as rust_decision:
                skills_registry._write_json(path, {"ok": True, "count": 1})

            self.assertTrue(path.exists())
            payload = rust_decision.call_args.kwargs
            self.assertEqual(payload["operation"], "write_skills_registry_file")
            self.assertEqual(payload["state_class"], "skills_registry_files")
            self.assertEqual(payload["payload"]["path"], str(path))

    def test_skills_registry_write_blocks_before_file_write_when_rust_returns_wrong_action(self) -> None:
        wrong_action = {
            **_ALLOW_DECISION,
            "next_action": "write_profile_api_file",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "registry.json"
            with mock.patch(
                "server_modules.rust_runtime_kernel_client.runtime_state_store_decision",
                return_value=wrong_action,
            ):
                with self.assertRaises(skills_registry.SkillsRegistryRustGateError) as raised:
                    skills_registry._write_json(path, {"ok": True})

            self.assertFalse(path.exists())
            self.assertEqual(str(raised.exception), "unexpected_next_action")


if __name__ == "__main__":
    unittest.main()

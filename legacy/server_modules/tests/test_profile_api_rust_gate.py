from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from server_modules import profile_api


_ALLOW_DECISION = {
    "ok": True,
    "decision": "allow",
    "reason": "runtime_state_store_policy_satisfied",
    "operation": "runtime-state-store-decision",
    "next_action": "write_profile_api_file",
    "approval_required": False,
    "cacheable": False,
    "audit_visibility": "standard",
}


class ProfileApiRustGateTests(unittest.TestCase):
    def test_write_json_calls_rust_before_persisting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "profile.json"
            with mock.patch.object(
                profile_api.rust_runtime_kernel_client,
                "runtime_state_store_decision",
                return_value=dict(_ALLOW_DECISION),
            ) as rust_decision:
                profile_api._write_json(path, {"id": "profile-1"})

            self.assertTrue(path.exists())
            payload = rust_decision.call_args.kwargs
            self.assertEqual(payload["operation"], "write_profile_api_file")
            self.assertEqual(payload["state_class"], "profile_api_files")
            self.assertEqual(payload["payload"]["kind"], "json")
            self.assertEqual(payload["payload"]["path"], str(path))

    def test_write_text_blocks_before_file_write_when_rust_blocks(self) -> None:
        block_decision = {
            **_ALLOW_DECISION,
            "ok": False,
            "decision": "block",
            "reason": "owner_access_denied",
            "operation": "write_profile_api_file",
            "audit_visibility": "security",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "instructions.md"
            with mock.patch.object(
                profile_api.rust_runtime_kernel_client,
                "runtime_state_store_decision",
                return_value=block_decision,
            ):
                with self.assertRaises(profile_api.ProfileApiRustGateError):
                    profile_api._write_text(path, "instructions")

            self.assertFalse(path.exists())

    def test_write_text_blocks_before_file_write_on_wrong_rust_action(self) -> None:
        wrong_action = {
            **_ALLOW_DECISION,
            "next_action": "write_runtime_common_json",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "instructions.md"
            with mock.patch.object(
                profile_api.rust_runtime_kernel_client,
                "runtime_state_store_decision",
                return_value=wrong_action,
            ):
                with self.assertRaises(profile_api.ProfileApiRustGateError) as raised:
                    profile_api._write_text(path, "instructions")

            self.assertEqual(str(raised.exception), "unexpected_next_action")
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()

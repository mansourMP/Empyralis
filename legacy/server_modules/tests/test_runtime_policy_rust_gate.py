from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from server_modules import runtime_policy


_ALLOW_DECISION = {
    "ok": True,
    "decision": "allow",
    "reason": "runtime_state_store_policy_satisfied",
    "operation": "runtime-state-store-decision",
    "next_action": "set_runtime_tool_enabled",
    "approval_required": False,
    "cacheable": False,
    "audit_visibility": "standard",
}


class RuntimePolicyRustGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._old_loaded = runtime_policy._TOOL_STATE_LOADED
        self._old_state = dict(runtime_policy.TOOL_STATE)

    def tearDown(self) -> None:
        runtime_policy._TOOL_STATE_LOADED = self._old_loaded
        runtime_policy.TOOL_STATE.clear()
        runtime_policy.TOOL_STATE.update(self._old_state)

    def test_set_tool_enabled_calls_rust_before_persisting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "tool_state.json"
            runtime_policy._TOOL_STATE_LOADED = True
            runtime_policy.TOOL_STATE.clear()
            runtime_policy.TOOL_STATE.update({"version": 1, "enabled": {}, "updated_at": None})
            with mock.patch.object(runtime_policy, "_init", return_value=None), mock.patch.dict(
                runtime_policy.__dict__,
                {"ORION_TOOL_STATE_FILE": str(state_path)},
                clear=False,
            ), mock.patch.object(
                runtime_policy.rust_runtime_kernel_client,
                "runtime_state_store_decision",
                return_value=dict(_ALLOW_DECISION),
            ) as rust_decision:
                result = runtime_policy.set_tool_enabled("shell.execute", False)

            self.assertTrue(result)
            self.assertTrue(state_path.exists())
            self.assertEqual(runtime_policy.TOOL_STATE["enabled"].get("shell.execute"), False)
            payload = rust_decision.call_args.kwargs
            self.assertEqual(payload["operation"], "set_runtime_tool_enabled")
            self.assertEqual(payload["state_class"], "runtime_tool_policy")
            self.assertEqual(payload["status"], "disabled")
            self.assertEqual(payload["payload"]["tool_id"], "shell.execute")
            self.assertEqual(payload["payload"]["enabled"], False)

    def test_set_tool_enabled_blocks_before_state_or_file_mutation_when_rust_blocks(self) -> None:
        block_decision = {
            **_ALLOW_DECISION,
            "ok": False,
            "decision": "block",
            "reason": "owner_access_denied",
            "operation": "set_runtime_tool_enabled",
            "audit_visibility": "security",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "tool_state.json"
            runtime_policy._TOOL_STATE_LOADED = True
            runtime_policy.TOOL_STATE.clear()
            runtime_policy.TOOL_STATE.update({"version": 1, "enabled": {}, "updated_at": None})
            with mock.patch.object(runtime_policy, "_init", return_value=None), mock.patch.dict(
                runtime_policy.__dict__,
                {"ORION_TOOL_STATE_FILE": str(state_path)},
                clear=False,
            ), mock.patch.object(
                runtime_policy.rust_runtime_kernel_client,
                "runtime_state_store_decision",
                return_value=block_decision,
            ):
                with self.assertRaises(runtime_policy.RuntimePolicyRustGateError):
                    runtime_policy.set_tool_enabled("shell.execute", False)

            self.assertEqual(runtime_policy.TOOL_STATE["enabled"], {})
            self.assertFalse(state_path.exists())

    def test_set_tool_enabled_blocks_before_state_or_file_mutation_on_wrong_rust_action(self) -> None:
        wrong_action = {
            **_ALLOW_DECISION,
            "next_action": "write_profile_api_file",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "tool_state.json"
            runtime_policy._TOOL_STATE_LOADED = True
            runtime_policy.TOOL_STATE.clear()
            runtime_policy.TOOL_STATE.update({"version": 1, "enabled": {}, "updated_at": None})
            with mock.patch.object(runtime_policy, "_init", return_value=None), mock.patch.dict(
                runtime_policy.__dict__,
                {"ORION_TOOL_STATE_FILE": str(state_path)},
                clear=False,
            ), mock.patch.object(
                runtime_policy.rust_runtime_kernel_client,
                "runtime_state_store_decision",
                return_value=wrong_action,
            ):
                with self.assertRaises(runtime_policy.RuntimePolicyRustGateError) as raised:
                    runtime_policy.set_tool_enabled("shell.execute", False)

            self.assertEqual(str(raised.exception), "unexpected_next_action")
            self.assertEqual(runtime_policy.TOOL_STATE["enabled"], {})
            self.assertFalse(state_path.exists())


if __name__ == "__main__":
    unittest.main()

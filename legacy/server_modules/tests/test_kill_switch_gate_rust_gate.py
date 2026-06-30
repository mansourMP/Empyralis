from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from server_modules import kill_switch_gate


_ALLOW_DECISION = {
    "ok": True,
    "decision": "allow",
    "reason": "runtime_state_store_policy_satisfied",
    "operation": "runtime-state-store-decision",
    "next_action": "set_kill_switch",
    "approval_required": False,
    "cacheable": False,
    "audit_visibility": "standard",
}


class KillSwitchGateRustGateTests(unittest.TestCase):
    def test_set_kill_switch_calls_rust_before_persisting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "kill_switches.json"
            with mock.patch.object(kill_switch_gate, "_KILL_SWITCH_FILE", state_path), mock.patch.object(
                kill_switch_gate,
                "_KILL_STATE",
                {},
            ), mock.patch.object(
                kill_switch_gate.rust_runtime_kernel_client,
                "runtime_state_store_decision",
                return_value=dict(_ALLOW_DECISION),
            ) as rust_decision:
                kill_switch_gate.set_kill_switch(kill_switch_gate.GLOBAL_KILL_KEY)

            self.assertTrue(state_path.exists())
            payload = rust_decision.call_args.kwargs
            self.assertEqual(payload["operation"], "set_kill_switch")
            self.assertEqual(payload["state_class"], "kill_switches")
            self.assertEqual(payload["status"], "active")
            self.assertEqual(payload["payload"]["key"], kill_switch_gate.GLOBAL_KILL_KEY)
            self.assertEqual(payload["payload"]["scope"], "global")

    def test_set_kill_switch_blocks_before_memory_or_file_mutation_when_rust_blocks(self) -> None:
        block_decision = {
            **_ALLOW_DECISION,
            "ok": False,
            "decision": "block",
            "reason": "owner_access_denied",
            "operation": "set_kill_switch",
            "audit_visibility": "security",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "kill_switches.json"
            kill_state: dict[str, bool] = {}
            with mock.patch.object(kill_switch_gate, "_KILL_SWITCH_FILE", state_path), mock.patch.object(
                kill_switch_gate,
                "_KILL_STATE",
                kill_state,
            ), mock.patch.object(
                kill_switch_gate.rust_runtime_kernel_client,
                "runtime_state_store_decision",
                return_value=block_decision,
            ):
                with self.assertRaises(kill_switch_gate.KillSwitchRustGateError):
                    kill_switch_gate.set_kill_switch(kill_switch_gate.GLOBAL_KILL_KEY)

            self.assertEqual(kill_state, {})
            self.assertFalse(state_path.exists())

    def test_set_kill_switch_blocks_before_memory_or_file_mutation_on_wrong_rust_action(self) -> None:
        wrong_action = {
            **_ALLOW_DECISION,
            "next_action": "write_profile_api_file",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "kill_switches.json"
            kill_state: dict[str, bool] = {}
            with mock.patch.object(kill_switch_gate, "_KILL_SWITCH_FILE", state_path), mock.patch.object(
                kill_switch_gate,
                "_KILL_STATE",
                kill_state,
            ), mock.patch.object(
                kill_switch_gate.rust_runtime_kernel_client,
                "runtime_state_store_decision",
                return_value=wrong_action,
            ):
                with self.assertRaises(kill_switch_gate.KillSwitchRustGateError) as raised:
                    kill_switch_gate.set_kill_switch(kill_switch_gate.GLOBAL_KILL_KEY)

            self.assertEqual(str(raised.exception), "unexpected_next_action")
            self.assertEqual(kill_state, {})
            self.assertFalse(state_path.exists())

    def test_clear_kill_switch_requires_rust_decision(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "kill_switches.json"
            kill_state = {"gateway:local": True}
            with mock.patch.object(kill_switch_gate, "_KILL_SWITCH_FILE", state_path), mock.patch.object(
                kill_switch_gate,
                "_KILL_STATE",
                kill_state,
            ), mock.patch.object(
                kill_switch_gate.rust_runtime_kernel_client,
                "runtime_state_store_decision",
                return_value={**_ALLOW_DECISION, "next_action": "clear_kill_switch"},
            ) as rust_decision:
                kill_switch_gate.clear_kill_switch("gateway:local")

            self.assertEqual(kill_state, {})
            payload = rust_decision.call_args.kwargs
            self.assertEqual(payload["operation"], "clear_kill_switch")
            self.assertEqual(payload["state_class"], "kill_switches")
            self.assertEqual(payload["status"], "cleared")
            self.assertEqual(payload["payload"]["scope"], "gateway")

    def test_clear_kill_switch_blocks_before_memory_or_file_mutation_on_wrong_rust_action(self) -> None:
        wrong_action = {
            **_ALLOW_DECISION,
            "next_action": "write_profile_api_file",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "kill_switches.json"
            kill_state = {"gateway:local": True}
            with mock.patch.object(kill_switch_gate, "_KILL_SWITCH_FILE", state_path), mock.patch.object(
                kill_switch_gate,
                "_KILL_STATE",
                kill_state,
            ), mock.patch.object(
                kill_switch_gate.rust_runtime_kernel_client,
                "runtime_state_store_decision",
                return_value=wrong_action,
            ):
                with self.assertRaises(kill_switch_gate.KillSwitchRustGateError) as raised:
                    kill_switch_gate.clear_kill_switch("gateway:local")

            self.assertEqual(str(raised.exception), "unexpected_next_action")
            self.assertEqual(kill_state, {"gateway:local": True})


if __name__ == "__main__":
    unittest.main()

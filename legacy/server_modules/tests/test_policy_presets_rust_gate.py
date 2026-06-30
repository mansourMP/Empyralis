import unittest
from unittest.mock import patch

from server_modules import policy_presets


class PolicyPresetsRustGateTests(unittest.TestCase):
    def test_apply_preset_accepts_canonical_rust_action(self) -> None:
        with patch.object(
            policy_presets.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "next_action": "apply_policy_preset",
                "policy": {
                    "policy_id": "preset:cautious",
                    "policy_version": 1,
                    "autonomy_mode": "safe_autopilot",
                    "allowed_capabilities": [],
                    "approval_required_capabilities": [],
                    "blocked_capabilities": [],
                },
            },
        ) as rust_mock:
            result = policy_presets.apply_preset("cautious")

        self.assertEqual(result.policy_id, "preset:cautious")
        rust_mock.assert_called_once()

    def test_apply_preset_blocks_unexpected_rust_action(self) -> None:
        with patch.object(
            policy_presets.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "next_action": "request_owner_approval",
                "policy": {
                    "policy_id": "preset:cautious",
                    "policy_version": 1,
                    "autonomy_mode": "safe_autopilot",
                    "allowed_capabilities": [],
                    "approval_required_capabilities": [],
                    "blocked_capabilities": [],
                },
            },
        ):
            with self.assertRaisesRegex(policy_presets.PolicyPresetError, "unexpected_next_action:request_owner_approval"):
                policy_presets.apply_preset("cautious")


if __name__ == "__main__":
    unittest.main()

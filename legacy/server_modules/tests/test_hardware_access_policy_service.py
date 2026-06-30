from __future__ import annotations

import unittest

from server_modules import hardware_access_policy_service as policy


class HardwareAccessPolicyServiceTests(unittest.TestCase):
    def test_shell_command_aliases_resolve_to_shell_execute(self) -> None:
        for alias in ("shell", "run", "run.command", "command"):
            with self.subTest(alias=alias):
                self.assertEqual(policy.normalize_hardware_capability_id(alias), "shell.execute")

    def test_default_guarded_allows_safe_read_actions(self) -> None:
        self.assertFalse(
            policy.hardware_action_requires_software_approval(
                runtime_access_mode="default_guarded",
                capability_id="filesystem.read",
                action_id="file.read",
                arguments={"path": "/tmp/example.txt"},
                require_approval=None,
            )
        )
        self.assertFalse(
            policy.hardware_action_requires_software_approval(
                runtime_access_mode="default_guarded",
                capability_id="screenshot.capture",
                action_id="screenshot.capture",
                arguments={},
                require_approval=None,
            )
        )

    def test_default_guarded_requires_approval_for_risky_actions(self) -> None:
        self.assertTrue(
            policy.hardware_action_requires_software_approval(
                runtime_access_mode="default_guarded",
                capability_id="shell.execute",
                action_id="shell.execute",
                arguments={"command": "rm -rf /tmp/demo"},
                require_approval=None,
            )
        )
        self.assertTrue(
            policy.hardware_action_requires_software_approval(
                runtime_access_mode="default_guarded",
                capability_id="filesystem.write",
                action_id="file.write",
                arguments={"path": "/tmp/example.txt"},
                require_approval=None,
            )
        )

    def test_full_access_skips_empyralis_action_approval(self) -> None:
        self.assertFalse(
            policy.hardware_action_requires_software_approval(
                runtime_access_mode="full_access",
                capability_id="shell.execute",
                action_id="shell.execute",
                arguments={"command": "rm -rf /tmp/demo"},
                require_approval=True,
            )
        )
        self.assertEqual(
            policy.runtime_access_metadata("full_access", None)["approval_mode"],
            "no_empyralis_action_approvals",
        )

    def test_custom_access_remains_approval_gated(self) -> None:
        self.assertTrue(
            policy.hardware_action_requires_software_approval(
                runtime_access_mode="custom",
                capability_id="shell.execute",
                action_id="shell.execute",
                arguments={"command": "rm -rf /tmp/demo"},
                require_approval=None,
            )
        )
        self.assertEqual(
            policy.runtime_access_metadata("custom", None)["approval_mode"],
            "custom_policy_guarded",
        )
        self.assertTrue(policy.runtime_access_metadata("custom", None)["empyralis_action_approvals_enabled"])

    def test_guarded_modes_ignore_chat_consent_text_for_risky_actions(self) -> None:
        arguments = {
            "command": "rm -rf /tmp/demo",
            "user_message": "yes I agree, run it",
            "approval_text": "approved",
        }

        for runtime_access_mode in ("default_guarded", "custom"):
            with self.subTest(runtime_access_mode=runtime_access_mode):
                self.assertTrue(
                    policy.hardware_action_requires_software_approval(
                        runtime_access_mode=runtime_access_mode,
                        capability_id="shell.execute",
                        action_id="shell.execute",
                        arguments=arguments,
                        require_approval=None,
                    )
                )


if __name__ == "__main__":
    unittest.main()

import unittest

from server_modules.runtime_policy import evaluate_tool_policy_decision


class RuntimePolicyShellCommandTests(unittest.TestCase):
    def test_raw_shell_command_stays_blocked_by_default(self):
        evaluation = evaluate_tool_policy_decision(
            tool_id="execute_shell_command",
            trust_mode="guarded",
            target="local_companion",
            metadata={},
            capability_ids=[],
        )

        self.assertEqual(evaluation.get("execution_decision"), "deny")
        self.assertEqual(evaluation.get("reason"), "blocked_raw_shell_command")
        self.assertTrue(evaluation.get("uses_raw_command_path"))
        self.assertFalse(evaluation.get("uses_capability_path"))
        self.assertFalse(evaluation.get("safe_raw_shell_command"))

    def test_safe_raw_shell_command_is_allowed(self):
        evaluation = evaluate_tool_policy_decision(
            tool_id="execute_shell_command",
            trust_mode="guarded",
            target="local_companion",
            metadata={
                "pack_inputs": {
                    "operations": [
                        {
                            "tool": "execute_shell_command",
                            "command": "echo hello world",
                        }
                    ]
                }
            },
            capability_ids=[],
        )

        self.assertEqual(evaluation.get("execution_decision"), "require_confirmation")
        self.assertEqual(evaluation.get("reason"), "Capability contract requires approval before execution.")
        self.assertTrue(evaluation.get("uses_raw_command_path"))
        self.assertTrue(evaluation.get("safe_raw_shell_command"))

    def test_raw_shell_command_with_metacharacters_stays_blocked(self):
        evaluation = evaluate_tool_policy_decision(
            tool_id="execute_shell_command",
            trust_mode="guarded",
            target="local_companion",
            metadata={
                "pack_inputs": {
                    "operations": [
                        {
                            "tool": "execute_shell_command",
                            "command": "echo hello && whoami",
                        }
                    ]
                }
            },
            capability_ids=[],
        )

        self.assertEqual(evaluation.get("execution_decision"), "deny")
        self.assertEqual(evaluation.get("reason"), "blocked_raw_shell_command")
        self.assertFalse(evaluation.get("safe_raw_shell_command"))

    def test_python_inline_raw_shell_command_stays_blocked(self):
        evaluation = evaluate_tool_policy_decision(
            tool_id="execute_shell_command",
            trust_mode="guarded",
            target="local_companion",
            metadata={
                "pack_inputs": {
                    "operations": [
                        {
                            "tool": "execute_shell_command",
                            "command": "python -c \"open('/tmp/empyralis-policy-bypass', 'w').write('x')\"",
                        }
                    ]
                }
            },
            capability_ids=[],
        )

        self.assertEqual(evaluation.get("execution_decision"), "deny")
        self.assertEqual(evaluation.get("reason"), "blocked_raw_shell_command")
        self.assertFalse(evaluation.get("safe_raw_shell_command"))

    def test_pip_mutating_raw_shell_command_stays_blocked(self):
        evaluation = evaluate_tool_policy_decision(
            tool_id="execute_shell_command",
            trust_mode="guarded",
            target="local_companion",
            metadata={
                "pack_inputs": {
                    "operations": [
                        {
                            "tool": "execute_shell_command",
                            "command": "pip install requests",
                        }
                    ]
                }
            },
            capability_ids=[],
        )

        self.assertEqual(evaluation.get("execution_decision"), "deny")
        self.assertEqual(evaluation.get("reason"), "blocked_raw_shell_command")
        self.assertFalse(evaluation.get("safe_raw_shell_command"))

    def test_capability_backed_shell_command_uses_reviewable_path(self):
        evaluation = evaluate_tool_policy_decision(
            tool_id="execute_shell_command",
            trust_mode="guarded",
            target="local_companion",
            metadata={},
            capability_ids=["stack.status"],
        )

        self.assertEqual(evaluation.get("execution_decision"), "deny")
        self.assertEqual(evaluation.get("reason"), "Destructive actions never auto-run.")
        self.assertTrue(evaluation.get("uses_capability_path"))
        self.assertFalse(evaluation.get("uses_raw_command_path"))


if __name__ == "__main__":
    unittest.main()

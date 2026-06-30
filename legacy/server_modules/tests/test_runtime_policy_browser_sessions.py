import unittest

from server_modules.runtime_policy import (
    build_local_operator_execution_binding,
    evaluate_tool_policy_decision,
    local_operator_execution_binding_matches,
)


class RuntimePolicyBrowserSessionTests(unittest.TestCase):
    def test_authenticated_readonly_browser_requires_approval_in_guarded_mode(self):
        evaluation = evaluate_tool_policy_decision(
            tool_id="browser_automation",
            trust_mode="guarded",
            target="local_companion",
            metadata={
                "browser_automation_policy": {
                    "profile": "authenticated_readonly",
                    "requires_approval": True,
                    "session_profiles": ["default"],
                    "session_profile_count": 1,
                    "interactive_actions": [],
                    "privileged_actions": [],
                }
            },
        )

        self.assertEqual(evaluation.get("execution_decision"), "require_confirmation")
        self.assertEqual(evaluation.get("reason"), "browser_authenticated_requires_approval")

    def test_authenticated_interactive_browser_requires_reviewed_approval_on_local_companion_v1(self):
        evaluation = evaluate_tool_policy_decision(
            tool_id="browser_automation",
            trust_mode="guarded",
            target="local_companion",
            metadata={
                "browser_automation_policy": {
                    "profile": "authenticated_interactive",
                    "requires_approval": True,
                    "session_profiles": ["default"],
                    "session_profile_count": 1,
                    "interactive_actions": ["click", "type"],
                    "privileged_actions": [],
                }
            },
        )

        self.assertEqual(evaluation.get("execution_decision"), "require_confirmation")
        self.assertEqual(evaluation.get("reason"), "browser_authenticated_interactive_requires_reviewed_approval")
        self.assertTrue(evaluation.get("reviewed_approval_required"))

    def test_authenticated_privileged_browser_requires_reviewed_approval_on_local_companion_v1(self):
        evaluation = evaluate_tool_policy_decision(
            tool_id="browser_automation",
            trust_mode="guarded",
            target="local_companion",
            metadata={
                "browser_automation_policy": {
                    "profile": "authenticated_privileged",
                    "requires_approval": True,
                    "session_profiles": ["default"],
                    "session_profile_count": 1,
                    "interactive_actions": ["upload"],
                    "privileged_actions": ["upload"],
                }
            },
        )

        self.assertEqual(evaluation.get("execution_decision"), "require_confirmation")
        self.assertEqual(evaluation.get("reason"), "browser_authenticated_privileged_requires_reviewed_approval")
        self.assertTrue(evaluation.get("reviewed_approval_required"))

    def test_local_operator_execution_binding_detects_environment_drift(self):
        approved = build_local_operator_execution_binding(
            argv=["electron", "/tmp/browser_task.js"],
            cwd="/tmp/desktop",
            env_vars={"EMPYRALIS_DESKTOP_GL": "angle"},
            browser_plan_hash="plan-1",
            session_profile="qa-browser",
        )
        same = build_local_operator_execution_binding(
            argv=["electron", "/tmp/browser_task.js"],
            cwd="/tmp/desktop",
            env_vars={"EMPYRALIS_DESKTOP_GL": "angle"},
            browser_plan_hash="plan-1",
            session_profile="qa-browser",
        )
        drifted = build_local_operator_execution_binding(
            argv=["electron", "/tmp/browser_task.js"],
            cwd="/tmp/desktop-alt",
            env_vars={"EMPYRALIS_DESKTOP_GL": "angle"},
            browser_plan_hash="plan-1",
            session_profile="qa-browser",
        )

        self.assertTrue(local_operator_execution_binding_matches(approved, same))
        self.assertFalse(local_operator_execution_binding_matches(approved, drifted))


if __name__ == "__main__":
    unittest.main()

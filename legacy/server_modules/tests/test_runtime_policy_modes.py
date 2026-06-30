import unittest
from unittest.mock import patch

from server_modules import runtime_policy


class RuntimePolicyModeTests(unittest.TestCase):
    def setUp(self) -> None:
        runtime_policy._init()

    def test_resolve_runtime_policy_mode_prefers_runtime_registration(self):
        registry = getattr(runtime_policy, "LOCAL_WORKER_REGISTRY")
        with patch.dict(
            registry,
            {
                "runtime-1": {
                    "runtime_id": "runtime-1",
                    "policy_mode": "trusted_full_access",
                }
            },
            clear=True,
        ):
            resolved = runtime_policy.resolve_runtime_policy_mode(
                {"execution_target_preferred_runtime_id": "runtime-1"},
                selected_target="local_companion",
            )

        self.assertEqual(resolved.get("policy_mode"), runtime_policy.POLICY_MODE_TRUSTED_FULL_ACCESS)
        self.assertEqual(resolved.get("runtime_id"), "runtime-1")
        self.assertEqual(resolved.get("source"), "runtime_registration")

    def test_local_default_requires_confirmation_for_external_send(self):
        evaluation = runtime_policy.evaluate_action_policy(
            {"send_message": 1},
            runtime_policy.POLICY_MODE_LOCAL_DEFAULT,
            {"execution_target_selected": "local_companion"},
            "local_companion",
        )

        self.assertTrue(evaluation.get("requires_confirmation"))
        self.assertEqual(evaluation.get("confirmation_required_actions"), ["send_message"])
        self.assertEqual(evaluation.get("denied_actions"), [])
        self.assertEqual(
            evaluation.get("evaluated")[0].get("classification", {}).get("action_type"),
            runtime_policy.ACTION_TYPE_EXTERNAL_SEND,
        )

    def test_trusted_full_access_still_confirms_external_send_and_publish(self):
        send_eval = runtime_policy.evaluate_action_policy(
            {"send_message": 1},
            runtime_policy.POLICY_MODE_TRUSTED_FULL_ACCESS,
            {"execution_target_selected": "local_companion"},
            "local_companion",
        )
        publish_eval = runtime_policy.evaluate_action_policy(
            {"publish_content": 1},
            runtime_policy.POLICY_MODE_TRUSTED_FULL_ACCESS,
            {"execution_target_selected": "local_companion"},
            "local_companion",
        )

        self.assertTrue(send_eval.get("requires_confirmation"))
        self.assertEqual(send_eval.get("confirmation_required_actions"), ["send_message"])
        self.assertEqual(publish_eval.get("confirmation_required_actions"), ["publish_content"])
        self.assertTrue(publish_eval.get("requires_confirmation"))

    def test_friendly_runtime_error_message_surfaces_codex_direct_auth_mismatch(self):
        message = runtime_policy.friendly_runtime_error_message(
            RuntimeError("This is a Codex OAuth token. Use openai-codex provider or set a direct OpenAI API key.")
        )

        self.assertIn("OpenAI Codex", message)

    def test_friendly_runtime_error_message_surfaces_missing_claude_cli(self):
        message = runtime_policy.friendly_runtime_error_message(
            RuntimeError("Claude Code CLI is not installed.")
        )

        self.assertIn("Claude Code CLI is not installed", message)


if __name__ == "__main__":
    unittest.main()

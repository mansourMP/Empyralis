import unittest

from server_modules import direct_chat_context_service as service


class DirectChatContextServiceTests(unittest.TestCase):
    def test_availability_helpers_use_connected_runtime_buckets(self) -> None:
        availability = {
            "ai_ready": True,
            "tool_capabilities": [
                {"id": "slack", "label": "Slack", "connected": True, "runtime_usable": True},
                {"id": "telegram", "label": "Telegram", "connected": True, "runtime_usable": False},
                {"id": "gmail", "label": "Google Workspace", "connected": True, "runtime_usable": None},
                {"id": "github", "label": "GitHub", "connected": False},
            ],
        }

        lines = service.availability_lines(
            "default",
            availability,
            normalize_tool_capabilities=lambda payload: payload.get("tool_capabilities") or [],
        )
        connected = service.connected_system_labels(
            availability,
            normalize_tool_capabilities=lambda payload: payload.get("tool_capabilities") or [],
        )
        context_caps = service.context_tool_capabilities(
            availability,
            normalize_tool_capabilities=lambda payload: payload.get("tool_capabilities") or [],
            max_context_tool_actions=2,
            max_context_tool_capabilities=2,
        )

        self.assertIn("Connected systems: Slack, Telegram, Google Workspace", lines[2])
        self.assertIn("Unavailable now: Telegram", lines[3])
        self.assertIn("Not verified: Google Workspace", lines[4])
        self.assertEqual(connected, ["Slack", "Telegram", "Google Workspace"])
        self.assertEqual(len(context_caps), 2)

    def test_availability_lines_include_capability_truth_summary(self) -> None:
        lines = service.availability_lines(
            "default",
            {
                "ai_ready": False,
                "provider": "deepseek",
                "tool_capabilities": [],
                "capability_truth": {
                    "provider": {"label": "DeepSeek", "ai_ready": False},
                    "credits": {"hosted_enabled": False, "reason": "policy_disabled"},
                    "my_computer": {"state": "offline"},
                    "connected_apps": [{"label": "Google Workspace", "connected": True}],
                    "channels": [{"label": "Telegram", "connected": False}],
                    "byok_providers": [{"label": "DeepSeek"}, {"label": "Gemini"}],
                    "required_setup_actions": [{"label": "Connect My Computer"}],
                },
            },
            normalize_tool_capabilities=lambda payload: payload.get("tool_capabilities") or [],
        )

        self.assertIn("Selected AI model: DeepSeek (setup required)", lines)
        self.assertIn("Workspace AI: blocked (policy disabled)", lines)
        self.assertIn("My Computer: offline", lines)
        self.assertIn("Connected apps: Google Workspace", lines)
        self.assertIn("Messaging channels: Telegram", lines)
        self.assertIn("Available AI connections: DeepSeek, Gemini", lines)
        self.assertIn("Required setup actions: Connect My Computer", lines)

    def test_session_model_preference_round_trip(self) -> None:
        store = {}

        service.set_session_model_preference("session-1", provider="openai", model="gpt-5.4", store=store)

        payload = service.session_model_preference("session-1", store=store)
        self.assertEqual(payload["provider"], "openai")
        self.assertEqual(payload["model"], "gpt-5.4")

    def test_normalize_prior_messages_filters_and_truncates(self) -> None:
        payload = service.normalize_prior_messages(
            [
                {"role": "system", "content": "skip"},
                {"role": "user", "content": " hello   world "},
                {"role": "assistant", "content": "x" * 20},
            ],
            max_direct_chat_prior_message_chars=10,
            max_direct_chat_prior_messages=5,
        )

        self.assertEqual(payload[0], {"role": "user", "content": "hello wor…"})
        self.assertTrue(payload[1]["content"].endswith("…"))

    def test_active_run_count_counts_only_live_workspace_runs(self) -> None:
        total = service.active_run_count(
            "default",
            live_runs={
                "1": {"status": "running", "context": {"workspace_id": "default"}},
                "2": {"status": "completed", "context": {"workspace_id": "default"}},
                "3": {"status": "queued", "context": {"workspace_id": "other"}},
                "4": {"status": "queued", "context": {"workspace_id": "default"}},
            },
        )

        self.assertEqual(total, 2)

    def test_parse_slash_command_extracts_command_and_remainder(self) -> None:
        payload = service.parse_slash_command("/model openai:gpt-5.4 keep going")
        self.assertEqual(payload["command"], "model")
        self.assertEqual(payload["remainder"], "openai:gpt-5.4 keep going")


if __name__ == "__main__":
    unittest.main()

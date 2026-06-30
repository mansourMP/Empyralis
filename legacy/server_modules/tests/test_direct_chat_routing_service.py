import unittest
from dataclasses import replace

from server_modules import direct_chat_routing_service


class DirectChatRoutingServiceTests(unittest.TestCase):
    def _policy_callbacks(self) -> direct_chat_routing_service.DirectChatRoutingPolicyCallbacks:
        return direct_chat_routing_service.DirectChatRoutingPolicyCallbacks(
            compact_text=lambda value: str(value or "").strip().lower(),
            mentions_any=lambda text, keywords: any(keyword in text for keyword in keywords),
            question_like=lambda compact: compact.startswith(("what ", "why ", "how ", "can ")),
            is_explicit_workflow_request=lambda message: "workflow" in str(message or "").lower(),
            starts_like_direct_run=lambda compact: compact.startswith(("run ", "execute ", "send ")),
            workflow_action=lambda message: {"kind": "workflow", "message": message},
            run_action=lambda message: {"kind": "run", "message": message},
            message_requests_local_file_tool=lambda message: "/tmp/" in str(message or ""),
            message_requests_local_shell_tool=lambda message: "shell" in str(message or "").lower(),
            message_requests_local_screenshot_tool=lambda message: "screenshot" in str(message or "").lower(),
            message_requests_local_computer_tool=lambda message: "computer" in str(message or "").lower(),
            complex_task_sequence_markers=("then", "after that"),
            complex_task_outcome_markers=("end to end", "done"),
            execution_markers=("run", "execute", "send"),
            google_workspace_keywords=("gmail",),
            telegram_keywords=("telegram",),
            slack_keywords=("slack",),
            dropbox_keywords=("dropbox",),
            s3_keywords=("s3",),
        )

    def test_preview_run_response_returns_run_action_for_imperative_request(self) -> None:
        payload = direct_chat_routing_service.preview_run_response(
            "Run the deployment check",
            {"ai_ready": True},
            self._policy_callbacks(),
        )

        self.assertIsNotNone(payload)
        self.assertEqual(payload["mode"], "answer_with_action")
        self.assertEqual(payload["actions"][0]["kind"], "run")

    def test_prefer_durable_run_handoff_handles_multi_step_local_request(self) -> None:
        preferred = direct_chat_routing_service.prefer_durable_run_handoff(
            "Run shell checks on /tmp/demo.txt then fix it end to end",
            {"ai_ready": True, "connection_mode": "local_companion"},
            self._policy_callbacks(),
        )

        self.assertTrue(preferred)

    def test_prefer_durable_run_handoff_ignores_pasted_context_words(self) -> None:
        callbacks = replace(
            self._policy_callbacks(),
            message_requests_local_file_tool=lambda _message: False,
            message_requests_local_shell_tool=lambda _message: False,
            message_requests_local_screenshot_tool=lambda _message: False,
            message_requests_local_computer_tool=lambda _message: False,
        )

        preferred = direct_chat_routing_service.prefer_durable_run_handoff(
            """
            Type your message or @path/to/file
            workspace (/directory) branch sandbox /model
            /tmp/one.txt /tmp/two.txt
            """,
            {"ai_ready": True, "connection_mode": "cloud_default"},
            callbacks,
        )

        self.assertFalse(preferred)

    def test_connector_preview_disables_builtin_tools_when_connector_tools_are_not_allowed(self) -> None:
        decision = direct_chat_routing_service.plan_direct_chat_route(
            message="Send a Slack message to the team",
            availability={"ai_ready": True},
            provider="codex_cli",
            tools=[{"name": "slack__send"}],
            compact_text_fn=lambda value: str(value or "").strip().lower(),
            mentions_any_fn=lambda text, keywords: any(keyword in text for keyword in keywords),
            is_obvious_smtp_write_request_fn=lambda _text: False,
            preview_run_response_fn=lambda _message, _availability: {"actions": [{"kind": "run"}]},
            prefer_durable_run_handoff_fn=lambda _message, _availability: False,
            durable_run_preferred_response_fn=lambda _message: {"actions": [{"kind": "run"}]},
            message_can_use_direct_connector_tools_fn=lambda _message: False,
            message_can_use_direct_local_tools_fn=lambda _message: False,
            message_can_use_builtin_direct_tools_fn=lambda _message: True,
            can_auto_start_run_handoff_fn=lambda _availability: True,
            google_workspace_keywords=("gmail",),
            telegram_keywords=("telegram",),
            slack_keywords=("slack",),
            discord_keywords=("discord",),
            dropbox_keywords=("dropbox",),
            s3_keywords=("s3",),
        )

        self.assertTrue(decision.connector_preview_requested)
        self.assertFalse(decision.allow_builtin_direct_tools)
        self.assertFalse(decision.allow_direct_tool_calls)
        self.assertTrue(decision.should_auto_start_run)

    def test_prefer_durable_run_handoff_disables_direct_tool_calls(self) -> None:
        decision = direct_chat_routing_service.plan_direct_chat_route(
            message="Investigate and fix the issue end to end",
            availability={"ai_ready": True},
            provider="openai",
            tools=[],
            compact_text_fn=lambda value: str(value or "").strip().lower(),
            mentions_any_fn=lambda text, keywords: any(keyword in text for keyword in keywords),
            is_obvious_smtp_write_request_fn=lambda _text: False,
            preview_run_response_fn=lambda _message, _availability: None,
            prefer_durable_run_handoff_fn=lambda _message, _availability: True,
            durable_run_preferred_response_fn=lambda _message: {"actions": [{"kind": "run"}]},
            message_can_use_direct_connector_tools_fn=lambda _message: False,
            message_can_use_direct_local_tools_fn=lambda _message: True,
            message_can_use_builtin_direct_tools_fn=lambda _message: True,
            can_auto_start_run_handoff_fn=lambda _availability: True,
            google_workspace_keywords=("gmail",),
            telegram_keywords=("telegram",),
            slack_keywords=("slack",),
            discord_keywords=("discord",),
            dropbox_keywords=("dropbox",),
            s3_keywords=("s3",),
        )

        self.assertTrue(decision.prefer_durable_run_handoff)
        self.assertEqual(decision.preview, {"actions": [{"kind": "run"}]})
        self.assertFalse(decision.allow_direct_tool_calls)
        self.assertTrue(decision.should_auto_start_run)

    def test_without_preview_auto_start_run_stays_false(self) -> None:
        decision = direct_chat_routing_service.plan_direct_chat_route(
            message="hello",
            availability={"ai_ready": True},
            provider="openai",
            tools=[],
            compact_text_fn=lambda value: str(value or "").strip().lower(),
            mentions_any_fn=lambda text, keywords: any(keyword in text for keyword in keywords),
            is_obvious_smtp_write_request_fn=lambda _text: False,
            preview_run_response_fn=lambda _message, _availability: None,
            prefer_durable_run_handoff_fn=lambda _message, _availability: False,
            durable_run_preferred_response_fn=lambda _message: {"actions": [{"kind": "run"}]},
            message_can_use_direct_connector_tools_fn=lambda _message: False,
            message_can_use_direct_local_tools_fn=lambda _message: False,
            message_can_use_builtin_direct_tools_fn=lambda _message: False,
            can_auto_start_run_handoff_fn=lambda _availability: True,
            google_workspace_keywords=("gmail",),
            telegram_keywords=("telegram",),
            slack_keywords=("slack",),
            discord_keywords=("discord",),
            dropbox_keywords=("dropbox",),
            s3_keywords=("s3",),
        )

        self.assertIsNone(decision.preview)
        self.assertFalse(decision.allow_direct_tool_calls)
        self.assertFalse(decision.should_auto_start_run)


if __name__ == "__main__":
    unittest.main()

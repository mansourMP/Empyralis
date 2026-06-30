import unittest
from unittest import mock

from server_modules import direct_chat_support_binding_service as service


class DirectChatSupportBindingServiceTests(unittest.TestCase):
    def test_build_context_used_delegates(self) -> None:
        expected = {"workspace": "default"}

        with mock.patch.object(
            service.direct_chat_metadata_service,
            "build_context_used",
            return_value=expected,
        ) as build_context_used:
            payload = service.build_context_used(
                workspace_id="default",
                requested_provider="openai",
                effective_provider="openai",
                requested_model="gpt-5.4",
                effective_model="gpt-5.4",
                reasoning_effort="medium",
                connected_systems=[],
                tool_capabilities=[],
                prior_messages_used=False,
                history_mode="none",
                run_created=False,
            )

        self.assertIs(payload, expected)
        build_context_used.assert_called_once()

    def test_build_proactive_suggestions_delegates(self) -> None:
        expected = ["Review today's priorities"]

        with mock.patch.object(
            service.direct_chat_prompt_service,
            "build_proactive_suggestions",
            return_value=expected,
        ) as build_suggestions:
            payload = service.build_proactive_suggestions(
                "default",
                heartbeat_tasks=lambda: [],
                recent_run_prompts=lambda workspace_id: [],
                memory_suggestion_prompts=lambda workspace_id: [],
            )

        self.assertIs(payload, expected)
        build_suggestions.assert_called_once()

    def test_recent_run_prompts_for_suggestions_delegates(self) -> None:
        expected = ["ship the next refactor cut"]

        with mock.patch.object(
            service.direct_chat_metadata_service,
            "recent_run_prompts_for_suggestions",
            return_value=expected,
        ) as recent_prompts:
            payload = service.recent_run_prompts_for_suggestions(
                "default",
                run_history=[{"workspace_id": "default", "user_goal": "ship the next refactor cut"}],
            )

        self.assertIs(payload, expected)
        recent_prompts.assert_called_once()


if __name__ == "__main__":
    unittest.main()

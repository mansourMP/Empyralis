import unittest
from unittest.mock import patch

from server_modules import direct_chat_memory_facade_service as service


class DirectChatMemoryFacadeServiceTests(unittest.TestCase):
    def test_direct_chat_workspace_context_text_skips_memory_for_unrelated_prompt(self) -> None:
        with (
            patch(
                "server_modules.workspace_context_memory_adapter.read_workspace_context_files",
                return_value={"USER.md": "# User\n- Concise replies."},
            ),
            patch("server_modules.memory_service.get_recent_logs", return_value="Recent log") as recent_logs,
            patch("server_modules.memory_service.get_memory", return_value="- timezone: Asia/Shanghai") as get_memory,
            patch("server_modules.memory_service.semantic_search", return_value=[{"key": "timezone", "content": "Asia/Shanghai"}]) as semantic_search,
            patch("server_modules.sage_memory_service.build_sage_memory_context_block", return_value="Sage memory") as sage_memory,
            patch("server_modules.sage_services_service.build_sage_services_memory_block", return_value="Sage services") as sage_services,
            patch("server_modules.mini_apps_service.build_mini_apps_context_block", return_value="Mini App Summaries") as mini_apps,
        ):
            text = service.direct_chat_workspace_context_text(
                "default",
                memory_query="what model are you?",
            )

        self.assertIn("USER.md", text)
        self.assertNotIn("Recent Daily Logs", text)
        self.assertNotIn("Runtime Memory Facts", text)
        self.assertNotIn("Sage Memory", text)
        self.assertNotIn("Sage Services", text)
        self.assertNotIn("Mini App Summaries", text)
        recent_logs.assert_not_called()
        get_memory.assert_not_called()
        semantic_search.assert_not_called()
        sage_memory.assert_not_called()
        sage_services.assert_not_called()
        mini_apps.assert_not_called()

    def test_direct_chat_workspace_context_text_includes_memory_for_relevant_prompt(self) -> None:
        with (
            patch("server_modules.workspace_context_memory_adapter.read_workspace_context_files", return_value={}),
            patch("server_modules.memory_service.get_recent_logs", return_value="Recent log"),
            patch("server_modules.memory_service.get_memory", return_value="- timezone: Asia/Shanghai"),
            patch("server_modules.sage_memory_service.build_sage_memory_context_block", return_value="Sage memory"),
            patch("server_modules.sage_services_service.build_sage_services_memory_block", return_value=""),
            patch("server_modules.mini_apps_service.build_mini_apps_context_block", return_value=""),
        ):
            text = service.direct_chat_workspace_context_text(
                "default",
                memory_query="what do you remember about my timezone?",
            )

        self.assertIn("Recent Daily Logs", text)
        self.assertIn("Runtime Memory Facts", text)
        self.assertIn("Sage Memory", text)

    def test_persist_direct_chat_transcript_best_effort_swallow_errors(self) -> None:
        called = {"count": 0}

        def boom(**kwargs):
            called["count"] += 1
            raise RuntimeError("fail")

        service.persist_direct_chat_transcript_best_effort(
            workspace_id="default",
            thread_id="thread-1",
            provider="openai",
            model="gpt-5.4",
            messages=[],
            user_message="hello",
            assistant_reply="hi",
            save_session_transcript_fn=boom,
        )

        self.assertEqual(called["count"], 1)

    def test_persist_direct_chat_transcript_best_effort_passes_payload(self) -> None:
        captured = {}

        def save(**kwargs):
            captured.update(kwargs)

        service.persist_direct_chat_transcript_best_effort(
            workspace_id="default",
            thread_id="thread-1",
            provider="openai",
            model="gpt-5.4",
            messages=[{"role": "user", "content": "hello"}],
            user_message="hello",
            assistant_reply="hi",
            save_session_transcript_fn=save,
        )

        self.assertEqual(captured["thread_id"], "thread-1")
        self.assertEqual(captured["assistant_reply"], "hi")

    def test_build_direct_chat_daily_log_summary_delegates(self) -> None:
        summary = service.build_direct_chat_daily_log_summary(
            user_message="hello",
            assistant_reply="hi",
        )

        self.assertIn("- User: hello", summary)
        self.assertIn("- Assistant: hi", summary)


if __name__ == "__main__":
    unittest.main()

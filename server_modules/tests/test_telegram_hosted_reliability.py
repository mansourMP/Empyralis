"""Tests for Telegram hosted bot reliability guarantees.

Verifies:
  - Message splitting at 4096 char boundary
  - send_message_safe never raises
  - _should_skip_reply correctly suppresses [SILENT] markers
  - _to_telegram_markdown doesn't break common formatting
  - Guaranteed-response: every error path produces a user-visible reply
"""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch


class MessageSplittingTests(unittest.TestCase):
    """Verify _split_long_message splits at sensible boundaries."""

    def test_short_message_not_split(self):
        from server_modules.sage_telegram_hosted_service import _split_long_message
        msg = "Hello world"
        chunks = _split_long_message(msg)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0], "Hello world")

    def test_empty_message_returns_single_empty(self):
        from server_modules.sage_telegram_hosted_service import _split_long_message
        chunks =_split_long_message("")
        self.assertEqual(chunks, [""])

    def test_long_message_splits_at_paragraph(self):
        from server_modules.sage_telegram_hosted_service import _split_long_message
        # Build a message that's ~5000 chars with a paragraph break near the middle
        para1 = "A" * 2500 + "\n\n" + "B" * 2500
        chunks = _split_long_message(para1)
        self.assertGreater(len(chunks), 1)
        # Each chunk should be under 4096
        for c in chunks:
            self.assertLessEqual(len(c), 4096)
        # Content should be preserved (ignoring whitespace trimming)
        combined = "".join(chunks)
        self.assertIn("AAAA", combined)
        self.assertIn("BBBB", combined)

    def test_long_message_no_paragraph_splits_at_sentence(self):
        from server_modules.sage_telegram_hosted_service import _split_long_message
        # ~5000 chars with sentence breaks
        sentence = "This is sentence number {}. "
        msg = "".join(sentence.format(i) for i in range(200))
        chunks = _split_long_message(msg)
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            self.assertLessEqual(len(c), 4096)

    def test_no_natural_boundary_splits_at_space(self):
        from server_modules.sage_telegram_hosted_service import _split_long_message
        # A single word repeated — no paragraph, sentence, or space breaks
        msg = "abcdefghij" * 500  # 5000 chars, no spaces
        chunks = _split_long_message(msg)
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            self.assertLessEqual(len(c), 4096)

    def test_exactly_4096_chars_not_split(self):
        from server_modules.sage_telegram_hosted_service import _split_long_message
        msg = "x" * 4096
        chunks = _split_long_message(msg)
        self.assertEqual(len(chunks), 1)


class ShouldSkipReplyTests(unittest.TestCase):
    """Verify _should_skip_reply correctly identifies suppressible replies."""

    def test_silent_marker_exact(self):
        from server_modules.sage_telegram_hosted_service import _should_skip_reply
        self.assertTrue(_should_skip_reply("[SILENT]"))

    def test_silent_marker_with_prefix(self):
        from server_modules.sage_telegram_hosted_service import _should_skip_reply
        self.assertTrue(_should_skip_reply("[SILENT] and more"))

    def test_empty_string_skipped(self):
        from server_modules.sage_telegram_hosted_service import _should_skip_reply
        self.assertTrue(_should_skip_reply(""))

    def test_none_skipped(self):
        from server_modules.sage_telegram_hosted_service import _should_skip_reply
        self.assertTrue(_should_skip_reply(None))

    def test_normal_reply_not_skipped(self):
        from server_modules.sage_telegram_hosted_service import _should_skip_reply
        self.assertFalse(_should_skip_reply("Here is your answer"))

    def test_silent_lowercase_not_matched(self):
        from server_modules.sage_telegram_hosted_service import _should_skip_reply
        # Only exact [SILENT] / [SILENT] prefix matches
        self.assertFalse(_should_skip_reply("[silent]"))
        self.assertFalse(_should_skip_reply("this contains [SILENT] in the middle"))


class MarkdownFormattingTests(unittest.TestCase):
    """Verify _to_telegram_markdown produces valid Telegram MarkdownV2."""

    def test_bold_conversion(self):
        from server_modules.sage_telegram_hosted_service import _to_telegram_markdown
        result = _to_telegram_markdown("Hello **world** today")
        self.assertIn("*world*", result)

    def test_italic_conversion(self):
        from server_modules.sage_telegram_hosted_service import _to_telegram_markdown
        result = _to_telegram_markdown("Hello *world* today")
        self.assertIn("_world_", result)

    def test_code_block_preserved(self):
        from server_modules.sage_telegram_hosted_service import _to_telegram_markdown
        result = _to_telegram_markdown("Run ```ls -la``` to see files")
        self.assertIn("```ls -la```", result)

    def test_links_preserved(self):
        from server_modules.sage_telegram_hosted_service import _to_telegram_markdown
        result = _to_telegram_markdown("Visit [Google](https://google.com)")
        self.assertIn("[Google](https://google.com)", result)

    def test_dashes_not_escaped(self):
        from server_modules.sage_telegram_hosted_service import _to_telegram_markdown
        # Dash should NOT be escaped — it breaks bullet lists in MarkdownV2
        result = _to_telegram_markdown("- item one\n- item two")
        self.assertIn("- item one", result)
        self.assertNotIn("\\- item one", result)

    def test_underscore_escaped(self):
        from server_modules.sage_telegram_hosted_service import _to_telegram_markdown
        result = _to_telegram_markdown("file_name.py")
        # Underscores in plain text should be escaped in MarkdownV2
        self.assertIn("\\_", result)

    def test_special_chars_escaped(self):
        from server_modules.sage_telegram_hosted_service import _to_telegram_markdown
        result = _to_telegram_markdown("Price: (10+5)*2 = 30!")
        # Parentheses should be escaped
        self.assertIn("\\(", result)
        self.assertIn("\\)", result)

    def test_empty_text_returns_empty(self):
        from server_modules.sage_telegram_hosted_service import _to_telegram_markdown
        self.assertEqual(_to_telegram_markdown(""), "")
        self.assertEqual(_to_telegram_markdown(None), None)


class GuaranteedResponseTests(unittest.IsolatedAsyncioTestCase):
    """Verify that send_message_safe never raises and always returns a bool."""

    @patch("server_modules.sage_telegram_hosted_service._telegram_api")
    async def test_send_message_safe_returns_false_on_failure(self, mock_api):
        from server_modules.sage_telegram_hosted_service import send_message_safe
        mock_api.side_effect = Exception("Network down")
        result = await send_message_safe("12345", "Hello")
        self.assertIsInstance(result, bool)
        self.assertFalse(result)

    @patch("server_modules.sage_telegram_hosted_service._telegram_api")
    async def test_send_message_safe_returns_true_on_success(self, mock_api):
        from server_modules.sage_telegram_hosted_service import send_message_safe
        mock_api.return_value = {"ok": True}
        result = await send_message_safe("12345", "Hello")
        self.assertTrue(result)

    @patch("server_modules.sage_telegram_hosted_service._telegram_api")
    async def test_send_message_safe_handles_empty_text(self, mock_api):
        from server_modules.sage_telegram_hosted_service import send_message_safe
        result = await send_message_safe("12345", "")
        self.assertFalse(result)
        mock_api.assert_not_called()

    @patch("server_modules.sage_telegram_hosted_service._telegram_api")
    async def test_send_message_safe_splits_long_message(self, mock_api):
        from server_modules.sage_telegram_hosted_service import send_message_safe
        mock_api.return_value = {"ok": True}
        long_msg = "Hello world. " * 500  # ~7000 chars
        result = await send_message_safe("12345", long_msg)
        self.assertTrue(result)
        # Should have called API multiple times (once per chunk)
        self.assertGreater(mock_api.call_count, 1)

    @patch("server_modules.sage_telegram_hosted_service._telegram_api")
    async def test_send_message_safe_plain_fallback_on_markdown_error(self, mock_api):
        from server_modules.sage_telegram_hosted_service import send_message_safe
        # First call fails (MarkdownV2 parse error), second succeeds (plain text)
        mock_api.side_effect = [
            Exception("Bad Request: can't parse entities"),
            {"ok": True},
        ]
        result = await send_message_safe("12345", "Hello")
        self.assertTrue(result, "Should succeed via plain-text fallback")
        self.assertEqual(mock_api.call_count, 2, "Should call API twice (markdown fail → plain retry)")


class TypingTasksTests(unittest.IsolatedAsyncioTestCase):
    """Verify typing indicator start/stop don't leak tasks."""

    async def test_start_typing_idempotent(self):
        from server_modules.sage_telegram_hosted_service import (
            start_typing, stop_typing, _TYPING_TASKS,
        )
        chat_id = "test_typing_idempotent"
        # Clean up any leftover
        _TYPING_TASKS.pop(chat_id, None)

        start_typing(chat_id)
        first_task = _TYPING_TASKS.get(chat_id)
        self.assertIsNotNone(first_task)
        start_typing(chat_id)  # second call — same task
        second_task = _TYPING_TASKS.get(chat_id)
        self.assertIs(first_task, second_task, "start_typing should be idempotent")
        await stop_typing(chat_id)
        self.assertIsNone(_TYPING_TASKS.get(chat_id))

    async def test_stop_typing_cleans_up(self):
        from server_modules.sage_telegram_hosted_service import (
            start_typing, stop_typing, _TYPING_TASKS,
        )
        chat_id = "test_typing_cleanup"
        _TYPING_TASKS.pop(chat_id, None)

        start_typing(chat_id)
        self.assertIn(chat_id, _TYPING_TASKS)
        await stop_typing(chat_id)
        self.assertNotIn(chat_id, _TYPING_TASKS)

    async def test_stop_typing_handles_unknown_chat_id(self):
        from server_modules.sage_telegram_hosted_service import stop_typing
        # Should not raise
        await stop_typing("nonexistent_chat_12345")


if __name__ == "__main__":
    unittest.main()

"""Tests for Telegram hosted bot reliability guarantees.

Verifies:
  - Message splitting at 4096 char boundary
  - send_message_safe never raises
  - _should_skip_reply correctly suppresses [SILENT] markers
  - _to_telegram_markdown doesn't break common formatting
  - Guaranteed-response: every error path produces a user-visible reply
  - GAP 1.3: 401 (revoked token) trips a circuit breaker that stops the
    background poll/typing loops from hammering Telegram, while transient
    429/5xx errors keep retrying at the normal cadence
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch


class DeepseekShortcutModelTests(unittest.TestCase):
    """_deepseek_shortcut_model — the Agent Machine shortcut's chat-
    completion model used to be a hardcoded "deepseek-chat" literal,
    DeepSeek's own retired pre-v4 id (2026-07-24), sent on every shortcut
    call. Now sourced from provider_profiles.py's catalog default, single
    source of truth."""

    def test_resolves_to_the_real_current_deepseek_catalog_default(self):
        from server_modules import sage_telegram_hosted_service

        model = sage_telegram_hosted_service._deepseek_shortcut_model()
        self.assertNotIn(model, {"deepseek-chat", "deepseek-reasoner"})
        self.assertEqual(model, "deepseek-v4-flash")


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


def _fake_response(status_code: int, json_body: dict):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body
    return resp


class CircuitBreaker401Tests(unittest.IsolatedAsyncioTestCase):
    """GAP 1.3 — a revoked/invalid bot token (HTTP 401 from Telegram) must
    trip a circuit breaker so the background poll loop (2s cadence) and the
    typing-indicator loop (4s cadence) stop hammering Telegram, instead of
    retrying forever. Transient errors (429/5xx/network) must NOT trip it
    and must keep the normal retry cadence."""

    def setUp(self):
        from server_modules import sage_telegram_hosted_service as svc
        self.svc = svc
        svc._clear_circuit_breaker()
        self._token_patch = patch.object(svc, "_bot_token", return_value="test-token-123")
        self._token_patch.start()

    def tearDown(self):
        self._token_patch.stop()
        self.svc._clear_circuit_breaker()

    def _patched_client(self, response):
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=response)
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        return patch("httpx.AsyncClient", return_value=mock_ctx)

    async def test_401_trips_breaker_and_raises_distinct_error(self):
        response = _fake_response(401, {"ok": False, "error_code": 401, "description": "Unauthorized"})
        with self._patched_client(response):
            with self.assertRaises(self.svc.TelegramUnauthorizedError):
                await self.svc._telegram_api("getUpdates", {})
        status = self.svc.hosted_bot_auth_status()
        self.assertTrue(status["suspended"])
        self.assertEqual(status["reason"], "Unauthorized")

    async def test_429_does_not_trip_breaker(self):
        response = _fake_response(429, {"ok": False, "error_code": 429, "description": "Too Many Requests: retry later"})
        with self._patched_client(response):
            result = await self.svc._telegram_api("getUpdates", {})
        self.assertFalse(result.get("ok"))
        status = self.svc.hosted_bot_auth_status()
        self.assertFalse(status["suspended"], "429 is transient — must not trip the 401 breaker")

    async def test_500_does_not_trip_breaker(self):
        response = _fake_response(500, {"ok": False, "description": "Internal Server Error"})
        with self._patched_client(response):
            result = await self.svc._telegram_api("sendMessage", {})
        self.assertFalse(result.get("ok"))
        self.assertFalse(self.svc.hosted_bot_auth_status()["suspended"])

    async def test_successful_call_clears_prior_suspension(self):
        self.svc._trip_circuit_breaker("stale token")
        self.assertTrue(self.svc.hosted_bot_auth_status()["suspended"])
        response = _fake_response(200, {"ok": True, "result": []})
        with self._patched_client(response):
            await self.svc._telegram_api("getUpdates", {})
        self.assertFalse(
            self.svc.hosted_bot_auth_status()["suspended"],
            "a successful call should self-heal the breaker (no restart required)",
        )

    async def test_polling_loop_backs_off_hard_on_401_instead_of_hammering(self):
        """The actual anti-hammer fix: the background poll loop must switch
        from its 2s cadence to the long suspended-probe cadence the moment
        poll_updates raises TelegramUnauthorizedError."""
        svc = self.svc
        svc._SAGE_HOSTED_PAIRS["circuit-breaker-test-chat"] = {"workspace_id": "ws-cb-test"}
        sleep_calls = []

        async def fake_sleep(seconds):
            sleep_calls.append(seconds)
            raise asyncio.CancelledError()  # stop the `while True` after one iteration

        try:
            with patch.object(svc, "poll_updates", AsyncMock(side_effect=svc.TelegramUnauthorizedError("Unauthorized"))), \
                 patch("asyncio.sleep", fake_sleep):
                with self.assertRaises(asyncio.CancelledError):
                    await svc._background_polling_loop()
        finally:
            svc._SAGE_HOSTED_PAIRS.pop("circuit-breaker-test-chat", None)

        self.assertEqual(sleep_calls[-1], svc._SUSPENDED_POLL_INTERVAL_SECONDS)
        self.assertNotEqual(sleep_calls[-1], svc._BG_POLL_INTERVAL)

    async def test_polling_loop_keeps_normal_cadence_on_transient_error(self):
        """A non-401 error (network blip, 5xx) must NOT trigger the heavy
        backoff — only a real 401 should."""
        svc = self.svc
        svc._SAGE_HOSTED_PAIRS["circuit-breaker-test-chat-2"] = {"workspace_id": "ws-cb-test-2"}
        sleep_calls = []

        async def fake_sleep(seconds):
            sleep_calls.append(seconds)
            raise asyncio.CancelledError()

        try:
            with patch.object(svc, "poll_updates", AsyncMock(side_effect=RuntimeError("network blip"))), \
                 patch("asyncio.sleep", fake_sleep):
                with self.assertRaises(asyncio.CancelledError):
                    await svc._background_polling_loop()
        finally:
            svc._SAGE_HOSTED_PAIRS.pop("circuit-breaker-test-chat-2", None)

        self.assertEqual(sleep_calls[-1], svc._BG_POLL_INTERVAL)

    async def test_typing_loop_skips_api_calls_while_suspended(self):
        svc = self.svc
        svc._trip_circuit_breaker("token dead")
        sleep_calls = []

        async def fake_sleep(seconds):
            sleep_calls.append(seconds)
            raise asyncio.CancelledError()

        with patch.object(svc, "send_chat_action", AsyncMock()) as mock_action, \
             patch("asyncio.sleep", fake_sleep):
            with self.assertRaises(asyncio.CancelledError):
                await svc._typing_loop("some-chat-id")

        mock_action.assert_not_called()
        self.assertEqual(sleep_calls[-1], svc._SUSPENDED_POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    unittest.main()

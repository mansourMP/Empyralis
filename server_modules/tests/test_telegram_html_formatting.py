"""Regression cover for the Telegram MarkdownV2 double-escape bug (2026-08-28).

FOUNDER REPORT (real Telegram screenshots, "Empyralis OpenClaw Proof" bot,
hosted-bot path): agent replies arrived with visible backslashes in front of
every '.', '(', ')', '!' the model wrote, and *bold* markers showing as
literal asterisks:

    The hardware gateway reports your computer is currently offline
    \\(state: `offline`\\), so the shell command never ran and I got no
    file listing\\.
    1\\. *Wake it / check it's on and connected*, then tell me to try again\\.
    *Docker isn't running on your computer\\.*

ROOT CAUSE, confirmed by code trace and reproduced exactly (see
DocumentTheRootCauseTests below): `_send_one_chunk`
(agent_reply_dispatcher.py) called `transport.format_text(text)` itself and
handed the ALREADY-FORMATTED string into `transport.send_message()` — which
then called `self.format_text()` on it AGAIN internally
(TelegramHostedTransport.send_message / AgentBotTransport.send_message).
Formatting twice is not idempotent: MarkdownV2-escaping an already-escaped
"\\(" a second time produces "\\\\(" — an escaped backslash followed by a
bare, now-unescaped '(' — which is invalid MarkdownV2. Telegram rejected the
twice-escaped send, and the transport's own internal fallback then delivered
the ONCE-escaped text (still carrying real backslashes) as PLAIN TEXT — no
parse_mode, so those backslashes render literally. That is exactly the
founder's screenshot.

THE FIX, two parts, both covered here:
  1. `_send_one_chunk` no longer pre-formats — it calls
     `transport.send_message()` exactly once per attempt, with the
     ORIGINAL raw text. Formatting is owned entirely by the transport, so
     it can only ever be applied once (RemovedDoubleFormatTests /
     PerTransportSingleFormatCallTests).
  2. Every Telegram transport switched from MarkdownV2 to Telegram HTML
     (`_to_telegram_html`, parse_mode="HTML"). HTML only ever treats '&',
     '<', '>' as special, so ordinary prose punctuation can never trigger a
     parse-mode rejection in the first place — the bug class this incident
     came from does not exist under HTML, whether formatting is applied
     once, twice, or never (ToTelegramHtmlTests).

If any of these tests regresses to asserting MarkdownV2-with-backslashes
output again, that is the bug coming back, not an acceptable behavior
change.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from server_modules.sage_telegram_hosted_service import _to_telegram_html, _to_telegram_markdown


# ─── The founder's exact reported strings, reverse-engineered to their RAW
#     (pre-formatting) form. Verified byte-for-byte: _to_telegram_markdown()
#     applied ONCE to each RAW string below reproduces the founder's
#     screenshot text exactly — see DocumentTheRootCauseTests. ────────────

RAW_OFFLINE_MESSAGE = (
    "The hardware gateway reports your computer is currently offline "
    "(state: `offline`), so the shell command never ran and I got no file listing."
)
SCREENSHOT_OFFLINE_MESSAGE = (
    "The hardware gateway reports your computer is currently offline "
    "\\(state: `offline`\\), so the shell command never ran and I got no file listing\\."
)

RAW_NUMBERED_ITEM = "1. **Wake it / check it's on and connected**, then tell me to try again."
SCREENSHOT_NUMBERED_ITEM = "1\\. *Wake it / check it's on and connected*, then tell me to try again\\."

RAW_DOCKER_MESSAGE = "**Docker isn't running on your computer.**"
SCREENSHOT_DOCKER_MESSAGE = "*Docker isn't running on your computer\\.*"


class DocumentTheRootCauseTests(unittest.TestCase):
    """Proves the diagnosis, not the fix — these three assertions establish
    that the RAW strings above genuinely reproduce the founder's screenshot
    under the OLD (still-present, still-correct-in-isolation)
    _to_telegram_markdown escaper, applied the ONE time a correct
    (non-doubled) call site would apply it.

    This is intentionally exercising _to_telegram_markdown, not the fix —
    it is the control that proves the bug reproduction is faithful, so the
    rest of this file's claims about the fix are trustworthy.
    """

    def test_offline_message_reproduces_screenshot(self):
        self.assertEqual(_to_telegram_markdown(RAW_OFFLINE_MESSAGE), SCREENSHOT_OFFLINE_MESSAGE)

    def test_numbered_item_reproduces_screenshot(self):
        self.assertEqual(_to_telegram_markdown(RAW_NUMBERED_ITEM), SCREENSHOT_NUMBERED_ITEM)

    def test_docker_message_reproduces_screenshot(self):
        self.assertEqual(_to_telegram_markdown(RAW_DOCKER_MESSAGE), SCREENSHOT_DOCKER_MESSAGE)

    def test_double_application_is_not_idempotent_the_actual_bug_class(self):
        """Applying the SAME escaper twice must not equal applying it once —
        if this ever becomes true, "double-format" would stop being a bug,
        which would be a strange (and unlikely) coincidence, not a fix. This
        is what allowed the twice-escaped text to slip past validation and
        get rejected by Telegram in the original incident."""
        once = _to_telegram_markdown(RAW_OFFLINE_MESSAGE)
        twice = _to_telegram_markdown(once)
        self.assertNotEqual(once, twice)
        # The literal defect: an escaped backslash followed by a bare,
        # newly-unescaped special character.
        self.assertIn("\\\\(", twice)
        self.assertIn("\\\\.", twice)


class ToTelegramHtmlTests(unittest.TestCase):
    """_to_telegram_html is the fix's formatter. Ordinary prose punctuation
    is NEVER escaped (HTML only cares about '&' '<' '>'), so none of these
    outputs may contain a backslash before '.', '(', ')', '!', '-'."""

    def _assert_no_markdownv2_style_escaping(self, text: str) -> None:
        for ch in "().!-":
            self.assertNotIn("\\" + ch, text, f"found a MarkdownV2-style backslash before {ch!r} in {text!r}")

    def test_offline_message_has_no_backslashes(self):
        result = _to_telegram_html(RAW_OFFLINE_MESSAGE)
        self._assert_no_markdownv2_style_escaping(result)
        self.assertNotIn("\\", result)
        self.assertIn("(state: <code>offline</code>)", result)
        self.assertIn("file listing.", result)

    def test_numbered_item_has_no_backslashes_and_renders_bold(self):
        result = _to_telegram_html(RAW_NUMBERED_ITEM)
        self.assertNotIn("\\", result)
        self.assertEqual(
            result,
            "1. <b>Wake it / check it's on and connected</b>, then tell me to try again.",
        )

    def test_docker_message_has_no_backslashes_and_renders_bold(self):
        result = _to_telegram_html(RAW_DOCKER_MESSAGE)
        self.assertNotIn("\\", result)
        self.assertEqual(result, "<b>Docker isn't running on your computer.</b>")

    def test_bold_conversion(self):
        self.assertEqual(_to_telegram_html("Hello **world** today"), "Hello <b>world</b> today")

    def test_italic_conversion(self):
        self.assertEqual(_to_telegram_html("Hello *world* today"), "Hello <i>world</i> today")

    def test_strikethrough_conversion(self):
        self.assertEqual(_to_telegram_html("Hello ~~world~~ today"), "Hello <s>world</s> today")

    def test_inline_code_is_wrapped_and_escaped(self):
        result = _to_telegram_html("Run `ls -la` to see files")
        self.assertEqual(result, "Run <code>ls -la</code> to see files")

    def test_code_fence_is_wrapped_in_pre(self):
        result = _to_telegram_html("```\nls -la\n```")
        self.assertEqual(result, "<pre>ls -la\n</pre>")

    def test_links_become_anchor_tags(self):
        result = _to_telegram_html("Visit [Google](https://google.com) now")
        self.assertEqual(result, 'Visit <a href="https://google.com">Google</a> now')

    def test_html_special_chars_are_escaped(self):
        result = _to_telegram_html("if a < b and b > c: use x & y")
        self.assertEqual(result, "if a &lt; b and b &gt; c: use x &amp; y")

    def test_html_special_chars_inside_code_are_escaped_too(self):
        result = _to_telegram_html("`List<String>`")
        self.assertEqual(result, "<code>List&lt;String&gt;</code>")

    def test_ordinary_punctuation_never_escaped(self):
        result = _to_telegram_html("Price: (10+5)*2 = 30! Also - a dash, and a comma.")
        self.assertNotIn("\\", result)

    def test_deliberately_unbalanced_asterisk_does_not_raise(self):
        """The task's explicit adversarial case: one stray '*' with no
        matching close. Under MarkdownV2 this exact input is what causes
        Telegram to reject the whole message ("can't find end of italic
        entity"). Under HTML it is simply a literal, ordinary character —
        no exception, no corruption, no escaping artifact."""
        text = "This is *unbalanced bold without a closing marker."
        result = _to_telegram_html(text)
        self.assertEqual(result, text)  # untouched — no matching pair found

    def test_deliberately_unbalanced_double_asterisk_does_not_raise(self):
        text = "Note: **this is never closed either."
        result = _to_telegram_html(text)
        self.assertEqual(result, text)

    def test_overlapping_emphasis_does_not_raise(self):
        """Known, documented limitation (see _to_telegram_html's own
        docstring): overlapping/crossed markers can produce structurally
        odd HTML. This test only asserts it does not crash — the safety
        net for THIS case is the plain-text fallback at the transport
        layer (see TransportFallsBackToRawTextOnRejectionTests), not a
        promise that this converter always emits valid HTML."""
        result = _to_telegram_html("**bold *italic** still italic*")
        self.assertIsInstance(result, str)

    def test_empty_and_none(self):
        self.assertEqual(_to_telegram_html(""), "")
        self.assertIsNone(_to_telegram_html(None))


class RemovedDoubleFormatTests(unittest.TestCase):
    """agent_reply_dispatcher._send_one_chunk must call
    transport.send_message() with the RAW text it was given, unmodified,
    and must NOT call transport.format_text() itself. A transport whose
    format_text() is non-identity makes a reintroduced pre-format
    immediately visible."""

    def test_send_one_chunk_never_pre_formats(self):
        from server_modules import agent_reply_dispatcher as srd

        calls = []

        class _FormattingSpyTransport:
            supports_typing_indicator = False
            max_message_length = 4096

            def format_text(self, text):
                # Deliberately NON-identity — if the dispatcher ever calls
                # this itself and hands the result into send_message, the
                # wrapper below is exactly what send_message would receive.
                return f"FORMATTED[{text}]"

            async def send_message(self, text, reply_to_id=None):
                calls.append(text)
                return True

        transport = _FormattingSpyTransport()
        raw = RAW_DOCKER_MESSAGE
        delivered = asyncio.run(srd._send_one_chunk(transport, raw))
        self.assertTrue(delivered)
        self.assertEqual(calls, [raw], "the dispatcher must hand send_message() the RAW text, never a pre-formatted one")


class PerTransportSingleFormatCallTests(unittest.TestCase):
    """The transport's own format_text() must be invoked exactly ONCE per
    delivered message — proving the fix closes the loop end to end through
    the real dispatcher, not just at the dispatcher's own boundary."""

    def test_format_text_called_exactly_once_through_real_dispatcher(self):
        from server_modules import agent_reply_dispatcher as srd

        format_calls = []
        sent = []

        class _CountingTransport:
            supports_typing_indicator = False
            max_message_length = 4096

            def format_text(self, text):
                format_calls.append(text)
                return _to_telegram_html(text)

            async def send_message(self, text, reply_to_id=None):
                formatted = self.format_text(text)
                sent.append({"text": formatted, "parse_mode": "HTML"})
                return True

        transport = _CountingTransport()
        delivered = asyncio.run(srd._send_one_chunk(transport, RAW_OFFLINE_MESSAGE))
        self.assertTrue(delivered)
        self.assertEqual(len(format_calls), 1, f"format_text called {len(format_calls)} times, expected exactly 1")
        self.assertNotIn("\\", sent[0]["text"])


class TelegramHostedTransportSendTests(unittest.IsolatedAsyncioTestCase):
    """End-to-end: TelegramHostedTransport.send_message(), mocking only the
    Telegram API call, asserting the ACTUAL BYTES that would go out."""

    @patch("server_modules.sage_telegram_hosted_service._telegram_api")
    async def test_offline_message_sent_as_html_with_no_backslashes(self, mock_api):
        from server_modules.sage_telegram_hosted_service import TelegramHostedTransport

        mock_api.return_value = {"ok": True}
        transport = TelegramHostedTransport("12345")
        result = await transport.send_message(RAW_OFFLINE_MESSAGE)

        self.assertTrue(result)
        mock_api.assert_called_once()
        method, body = mock_api.call_args.args
        self.assertEqual(method, "sendMessage")
        self.assertEqual(body["parse_mode"], "HTML")
        self.assertNotIn("\\", body["text"])
        self.assertIn("(state: <code>offline</code>)", body["text"])

    @patch("server_modules.sage_telegram_hosted_service._telegram_api")
    async def test_docker_message_sent_as_html_bold_not_literal_asterisks(self, mock_api):
        from server_modules.sage_telegram_hosted_service import TelegramHostedTransport

        mock_api.return_value = {"ok": True}
        transport = TelegramHostedTransport("12345")
        await transport.send_message(RAW_DOCKER_MESSAGE)

        method, body = mock_api.call_args.args
        self.assertEqual(body["text"], "<b>Docker isn't running on your computer.</b>")
        # The literal founder-reported garbage must never be what we send.
        self.assertNotEqual(body["text"], SCREENSHOT_DOCKER_MESSAGE)

    @patch("server_modules.sage_telegram_hosted_service._telegram_api")
    async def test_html_rejection_falls_back_to_original_raw_text_not_formatted(self, mock_api):
        """If Telegram rejects the HTML send (malformed markup, etc.), the
        fallback must deliver the ORIGINAL raw text as plain — never the
        HTML-formatted string (that would just reproduce a differently-
        shaped version of the same bug: visible markup artifacts instead
        of visible backslashes)."""
        from server_modules.sage_telegram_hosted_service import TelegramHostedTransport

        # TelegramHostedTransport.send_message() mutates and REUSES the same
        # `body` dict across both attempts (pop("parse_mode") in place), so
        # capturing call_args_list after the fact would see the dict in its
        # FINAL, already-mutated state for every call. Snapshot each call's
        # body with a real copy, at the moment it happens, instead.
        seen_bodies = []
        responses = iter([
            {"ok": False, "description": "Bad Request: can't parse entities"},
            {"ok": True},
        ])

        async def _fake_telegram_api(method, body):
            seen_bodies.append(dict(body))
            return next(responses)

        mock_api.side_effect = _fake_telegram_api
        transport = TelegramHostedTransport("12345")
        raw = "**bold *italic** still italic*"  # the documented overlapping-markup edge case
        result = await transport.send_message(raw)

        self.assertTrue(result)
        self.assertEqual(len(seen_bodies), 2)
        first_body, second_body = seen_bodies
        self.assertEqual(first_body["parse_mode"], "HTML")
        self.assertNotIn("parse_mode", second_body)
        self.assertEqual(second_body["text"], raw)  # raw, unmodified — the safety net

    @patch("server_modules.sage_telegram_hosted_service._telegram_api")
    async def test_unbalanced_asterisk_never_needs_the_fallback_at_all(self, mock_api):
        """The task's adversarial case end to end: an unbalanced '*' must
        not even trigger a rejection under HTML (unlike MarkdownV2, where
        this exact input is a guaranteed 400)."""
        from server_modules.sage_telegram_hosted_service import TelegramHostedTransport

        mock_api.return_value = {"ok": True}
        transport = TelegramHostedTransport("12345")
        raw = "This is *unbalanced bold without a closing marker."
        result = await transport.send_message(raw)

        self.assertTrue(result)
        mock_api.assert_called_once()  # first attempt succeeded — no fallback needed
        _, body = mock_api.call_args.args
        self.assertEqual(body["text"], raw)
        self.assertEqual(body["parse_mode"], "HTML")


class AgentBotTransportSendTests(unittest.IsolatedAsyncioTestCase):
    """The BYO-bot transport (hosted_bot_provisioning_service.AgentBotTransport)
    carried the identical double-format bug and needs the identical proof."""

    @patch("server_modules.hosted_bot_provisioning_service.telegram_api")
    async def test_offline_message_sent_as_html_with_no_backslashes(self, mock_api):
        from server_modules.hosted_bot_provisioning_service import AgentBotTransport

        mock_api.return_value = {"ok": True}
        transport = AgentBotTransport(token="test-token", chat_id="99999")
        result = await transport.send_message(RAW_OFFLINE_MESSAGE)

        self.assertTrue(result)
        mock_api.assert_called_once()
        token, method, body = mock_api.call_args.args
        self.assertEqual(token, "test-token")
        self.assertEqual(method, "sendMessage")
        self.assertEqual(body["parse_mode"], "HTML")
        self.assertNotIn("\\", body["text"])

    @patch("server_modules.hosted_bot_provisioning_service.telegram_api")
    async def test_rejection_falls_back_to_raw_text(self, mock_api):
        from server_modules.hosted_bot_provisioning_service import AgentBotTransport

        mock_api.side_effect = [
            {"ok": False, "description": "Bad Request: can't parse entities"},
            {"ok": True},
        ]
        transport = AgentBotTransport(token="test-token", chat_id="99999")
        raw = RAW_NUMBERED_ITEM
        result = await transport.send_message(raw)

        self.assertTrue(result)
        self.assertEqual(mock_api.call_count, 2)
        second_body = mock_api.call_args_list[1].args[2]
        self.assertNotIn("parse_mode", second_body)
        self.assertEqual(second_body["text"], raw)


class DispatchSageReplySafeEndToEndTests(unittest.IsolatedAsyncioTestCase):
    """The fullest integration available without a live bot: drives the real
    TelegramHostedTransport through the real dispatch_sage_reply_safe /
    _send_one_chunk path, mocking only the Telegram HTTP call, and asserts
    the wire body never carries the founder's reported garbage."""

    @patch("server_modules.sage_telegram_hosted_service._telegram_api")
    async def test_full_dispatch_path_produces_clean_html_not_garbled_markdown(self, mock_api):
        from server_modules.agent_reply_dispatcher import _send_one_chunk
        from server_modules.sage_telegram_hosted_service import TelegramHostedTransport

        mock_api.return_value = {"ok": True}
        transport = TelegramHostedTransport("55555")

        for raw, forbidden_screenshot in (
            (RAW_OFFLINE_MESSAGE, SCREENSHOT_OFFLINE_MESSAGE),
            (RAW_NUMBERED_ITEM, SCREENSHOT_NUMBERED_ITEM),
            (RAW_DOCKER_MESSAGE, SCREENSHOT_DOCKER_MESSAGE),
        ):
            mock_api.reset_mock()
            delivered = await _send_one_chunk(transport, raw)
            self.assertTrue(delivered)
            mock_api.assert_called_once()
            _, body = mock_api.call_args.args
            self.assertNotIn("\\", body["text"])
            self.assertNotEqual(body["text"], forbidden_screenshot)
            self.assertEqual(body["parse_mode"], "HTML")


if __name__ == "__main__":
    unittest.main()

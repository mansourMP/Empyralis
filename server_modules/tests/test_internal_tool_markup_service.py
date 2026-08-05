from __future__ import annotations

import unittest

from server_modules import internal_tool_markup_service as markup


class ExtractTextualToolCallMentionsTests(unittest.TestCase):
    """MAN-263: extract_textual_tool_call_mentions is the detection primitive
    tool_honesty_guard's narrates_tool_call_after_success direction is built
    on. It only ever extracts what a reply DEPICTS as a call in plain text —
    it has no executor, is never wired to one, and every test here checks
    shape only, never execution."""

    def test_reproduces_the_recorded_man263_incident_shape(self) -> None:
        # The exact recorded incident: a hardware__action call genuinely
        # succeeded this turn (real exit_code 0, real stdout), and the
        # SYNTHESIS turn afterward replied with this text instead of using
        # the real result.
        reply = (
            "I'll actually make the call now.\n"
            "```json\n"
            "{\"tool\": \"hardware__action\", \"arguments\": {\"command\": \"uname -a\"}}\n"
            "```"
        )
        mentions = markup.extract_textual_tool_call_mentions(reply)
        self.assertEqual(len(mentions), 1)
        self.assertEqual(mentions[0]["name"], "hardware__action")
        self.assertEqual(mentions[0]["arguments"], {"command": "uname -a"})

    def test_bare_unfenced_json_is_also_detected(self) -> None:
        reply = 'Sure, calling it: {"tool": "hardware__action", "arguments": {"command": "whoami"}} done.'
        mentions = markup.extract_textual_tool_call_mentions(reply)
        self.assertEqual(len(mentions), 1)
        self.assertEqual(mentions[0]["name"], "hardware__action")

    def test_openai_style_function_envelope_is_detected(self) -> None:
        reply = (
            'Invoking now:\n```json\n'
            '{"type": "function", "function": {"name": "hardware__action", '
            '"arguments": "{\\"command\\": \\"uname -a\\"}"}}\n```'
        )
        mentions = markup.extract_textual_tool_call_mentions(reply)
        self.assertEqual(len(mentions), 1)
        self.assertEqual(mentions[0]["name"], "hardware__action")
        self.assertEqual(mentions[0]["arguments"], {"command": "uname -a"})

    def test_name_key_variant_is_detected(self) -> None:
        reply = '{"name": "web_search", "arguments": {"query": "empyralis"}}'
        mentions = markup.extract_textual_tool_call_mentions(reply)
        self.assertEqual(mentions[0]["name"], "web_search")

    def test_tool_name_aliases_normalize_the_same_way_dsml_does(self) -> None:
        reply = '{"tool": "bash", "arguments": {"command": "ls"}}'
        mentions = markup.extract_textual_tool_call_mentions(reply)
        self.assertEqual(mentions[0]["name"], "shell__exec")

    def test_nested_braces_in_arguments_do_not_break_the_scan(self) -> None:
        reply = (
            '{"tool": "http_request", "arguments": {"headers": {"Authorization": '
            '"Bearer x"}, "body": {"nested": {"deep": true}}}}'
        )
        mentions = markup.extract_textual_tool_call_mentions(reply)
        self.assertEqual(len(mentions), 1)
        self.assertEqual(mentions[0]["name"], "http_request")
        self.assertEqual(mentions[0]["arguments"]["headers"], {"Authorization": "Bearer x"})

    def test_ordinary_prose_with_no_json_is_not_a_mention(self) -> None:
        self.assertEqual(markup.extract_textual_tool_call_mentions("The weather is nice today."), [])

    def test_json_without_a_name_key_is_not_a_mention(self) -> None:
        # A reply legitimately quoting an unrelated JSON blob (e.g. a config
        # snippet) must not false-positive just because it contains braces.
        reply = 'Here is your config: {"timeout": 30, "retries": 3}'
        self.assertEqual(markup.extract_textual_tool_call_mentions(reply), [])

    def test_json_with_a_name_but_no_arguments_shape_is_not_a_mention(self) -> None:
        # A person's name in JSON ({"name": "Alice"}) must not false-positive
        # just because "name" happens to be a key — an arguments/parameters/
        # input/args sibling is required too.
        reply = 'The contact on file is {"name": "Alice", "role": "admin"}.'
        self.assertEqual(markup.extract_textual_tool_call_mentions(reply), [])

    def test_empty_and_none_input_returns_empty(self) -> None:
        self.assertEqual(markup.extract_textual_tool_call_mentions(""), [])
        self.assertEqual(markup.extract_textual_tool_call_mentions(None), [])

    def test_malformed_json_is_ignored_not_raised(self) -> None:
        reply = 'Calling it: {"tool": "hardware__action", "arguments": {command: uname}}'
        # Invalid JSON (unquoted keys/values) — must not raise, must not match.
        self.assertEqual(markup.extract_textual_tool_call_mentions(reply), [])


if __name__ == "__main__":
    unittest.main()

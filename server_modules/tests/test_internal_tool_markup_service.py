from __future__ import annotations

import unittest

from server_modules import internal_tool_markup_service as markup


# MAN-303 (production, 2026-08-04): a model on a text-tool-calling fallback
# path wrote a literal "<memorywrite>...</memorywrite>" block into its final
# reply. Nothing recognized it as internal markup — it isn't the DSML format
# this module already handled — so it rendered raw to the user. These tests
# cover the fix: a generic detector anchored to the platform's own flat
# tool-name vocabulary (normalized so "<memorywrite>" still matches the real
# tool "memory_write" even without the underscore), deliberately NOT a
# sweep over any "<...>"-shaped text, so ordinary HTML/XML a user asked to
# see is never touched.


def test_detects_the_production_memorywrite_tag() -> None:
    text = (
        "The first write was rejected for formatting — retrying with a "
        "single-line entry.\n\n<memorywrite>\nentry: Favorite color: teal.\n</memorywrite>"
    )
    assert markup.detect_internal_tool_markup(text) is True


def test_strips_the_production_memorywrite_tag_and_everything_after_it() -> None:
    text = (
        "The first write was rejected for formatting — retrying with a "
        "single-line entry.\n\n<memorywrite>\nentry: Favorite color: teal.\n</memorywrite>"
    )
    stripped = markup.strip_internal_tool_markup(text)
    assert stripped == "The first write was rejected for formatting — retrying with a single-line entry."
    assert "<memorywrite>" not in stripped
    assert "memorywrite" not in stripped.lower()


def test_matches_the_tool_name_even_with_the_real_underscore() -> None:
    text = "Saving that now.\n<memory_write>entry: teal</memory_write>"
    assert markup.detect_internal_tool_markup(text) is True
    assert markup.strip_internal_tool_markup(text) == "Saving that now."


def test_catches_an_unclosed_tag_from_generation_cut_off_mid_block() -> None:
    text = "One second.\n<memorywrite>\nentry: still writing"
    assert markup.detect_internal_tool_markup(text) is True
    assert markup.strip_internal_tool_markup(text) == "One second."


def test_other_known_tool_identifiers_are_also_caught() -> None:
    for tag in ("shellexec", "shell_exec", "webfetch", "browsernavigate", "hardwareaction"):
        text = f"Working on it.\n<{tag}>do the thing</{tag}>"
        with_markup = markup.detect_internal_tool_markup(text)
        assert with_markup is True, f"expected {tag!r} to be recognized as internal tool markup"


def test_ordinary_html_a_user_asked_to_see_is_never_touched() -> None:
    text = "Sure, here is an example:\n<div>\n  <span>hello</span>\n</div>"
    assert markup.detect_internal_tool_markup(text) is False
    assert markup.strip_internal_tool_markup(text) == text


def test_ordinary_prose_mentioning_unrelated_bracketed_words_is_untouched() -> None:
    text = "The <threshold> value should be tuned per environment."
    assert markup.detect_internal_tool_markup(text) is False


def test_dsml_markup_still_takes_precedence_and_still_works() -> None:
    text = 'Let me check.\n<||DSML||tool_calls><||DSML||invoke name="bash">secret command</||DSML||invoke>'
    assert markup.detect_internal_tool_markup(text) is True
    assert markup.strip_internal_tool_markup(text) == "Let me check."


def test_response_leak_guard_strips_the_hallucinated_memory_write_tag() -> None:
    from server_modules import response_leak_guard_service

    reply = (
        "The first write was rejected for formatting — retrying with a "
        "single-line entry.\n\n<memorywrite>\nentry: Favorite color: teal.\n</memorywrite>"
    )
    result = response_leak_guard_service.guard_model_response(reply)
    assert "internal_tool_markup" in result.findings
    assert "<memorywrite>" not in result.text
    assert result.text == "The first write was rejected for formatting — retrying with a single-line entry."

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
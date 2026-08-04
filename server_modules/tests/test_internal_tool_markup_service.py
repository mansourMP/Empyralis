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

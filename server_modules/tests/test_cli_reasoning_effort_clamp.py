"""The seam where a UNIFORM picker meets a NATIVE wire.

Since 2026-08-20 the customer picks a reasoning-effort level from ONE shared
ladder no matter which BYO subscription is bound (the founder's rule; his
words are quoted verbatim in frontend/lib/workspace/fleet/
fleet-provider-constants.ts's REASONING_EFFORT_LADDER comment). Each CLI
still accepts only its OWN vocabulary on its OWN flag, so an out-of-
vocabulary level is now the EXPECTED case rather than an error case, and it
is clamped rather than dropped.

These assertions pin the clamp itself. They are deliberately a separate,
import-light file: the clamp is a pure function and must stay testable
without the whole runtime module's fixtures.
"""
import unittest

from server_modules.sage_agent_runtime_service import (
    clamp_cli_reasoning_effort,
    _NATIVE_CLI_REASONING_EFFORTS_BY_RUNTIME,
    _VALID_CLI_REASONING_EFFORTS_BY_RUNTIME,
)

SHARED_LADDER = ("low", "medium", "high", "xhigh", "max", "ultra")


class CliReasoningEffortClampTests(unittest.TestCase):
    def test_every_rung_of_the_shared_ladder_produces_something_real_on_every_flagged_runtime(self):
        """The founder's "if it works, it works" only holds if every offered
        level reaches SOMETHING on a runtime that has a flag at all. An empty
        result here would mean a level the picker offers is silently
        discarded — the dead control this whole change is trying not to be."""
        for runtime in ("claude_code", "codex", "grok_build"):
            native = _NATIVE_CLI_REASONING_EFFORTS_BY_RUNTIME[runtime]
            for level in SHARED_LADDER:
                clamped = clamp_cli_reasoning_effort(runtime, level)
                self.assertTrue(clamped, f"{runtime}/{level} clamped to nothing")
                self.assertIn(
                    clamped, native,
                    f"{runtime}/{level} clamped to {clamped!r}, which that CLI's flag does not accept",
                )

    def test_a_level_the_cli_accepts_is_passed_through_untouched(self):
        for runtime in ("claude_code", "codex", "grok_build"):
            for level in _NATIVE_CLI_REASONING_EFFORTS_BY_RUNTIME[runtime]:
                self.assertEqual(clamp_cli_reasoning_effort(runtime, level), level)

    def test_ultra_clamps_to_each_cli_own_ceiling(self):
        """"ultra" reached the shared ladder from a real codex model/list
        response and no CLI *flag* accepts it. It must land on the strongest
        rung each one does."""
        self.assertEqual(clamp_cli_reasoning_effort("claude_code", "ultra"), "max")
        self.assertEqual(clamp_cli_reasoning_effort("codex", "ultra"), "max")
        self.assertEqual(clamp_cli_reasoning_effort("grok_build", "ultra"), "max")

    def test_a_legacy_value_from_another_runtime_clamps_rather_than_vanishing(self):
        """"off" is codex's; "none" is Grok's. Saved on a claude_code agent
        either used to be dropped — now they reach the nearest rung claude
        actually has."""
        self.assertEqual(clamp_cli_reasoning_effort("claude_code", "off"), "low")
        self.assertEqual(clamp_cli_reasoning_effort("claude_code", "none"), "low")
        self.assertEqual(clamp_cli_reasoning_effort("claude_code", "minimal"), "low")

    def test_cursor_cli_sends_nothing_because_it_has_no_flag(self):
        """The ONE runtime where the offered control genuinely does nothing.
        Asserted so it stays a known, reported fact — cursor-agent publishes
        no reasoning-effort flag, and nothing here invents one for it."""
        self.assertEqual(_NATIVE_CLI_REASONING_EFFORTS_BY_RUNTIME["cursor_cli"], set())
        for level in SHARED_LADDER:
            self.assertEqual(clamp_cli_reasoning_effort("cursor_cli", level), "")

    def test_unset_and_unknown_both_send_nothing(self):
        self.assertEqual(clamp_cli_reasoning_effort("claude_code", ""), "")
        self.assertEqual(clamp_cli_reasoning_effort("claude_code", "   "), "")
        self.assertEqual(clamp_cli_reasoning_effort("claude_code", "turbo"), "")
        self.assertEqual(clamp_cli_reasoning_effort("no_such_runtime", "high"), "")

    def test_case_and_whitespace_are_normalised_the_same_way_the_save_path_does(self):
        self.assertEqual(clamp_cli_reasoning_effort("claude_code", "  HIGH "), "high")
        self.assertEqual(clamp_cli_reasoning_effort("  CLAUDE_CODE ", "high"), "high")

    def test_the_save_vocabulary_is_a_superset_of_the_shared_ladder_for_every_runtime(self):
        """A level the picker offers but the save path rejects is a 400 the
        customer did nothing to earn. This is the structural version of that
        promise — cursor_cli included, whose value is dropped on the wire but
        must still be storable."""
        for runtime, accepted in _VALID_CLI_REASONING_EFFORTS_BY_RUNTIME.items():
            for level in SHARED_LADDER:
                self.assertIn(level, accepted, f"{runtime} would reject a level its picker offers: {level}")

    def test_native_rungs_stay_saveable_so_pre_unification_values_do_not_break(self):
        for runtime, native in _NATIVE_CLI_REASONING_EFFORTS_BY_RUNTIME.items():
            for level in native:
                self.assertIn(level, _VALID_CLI_REASONING_EFFORTS_BY_RUNTIME[runtime])


if __name__ == "__main__":
    unittest.main()


class SdkReasoningEffortCeilingTests(unittest.TestCase):
    """The Anthropic-served SDK path has NO system-instruction fallback.

    openai_compat_adapter._apply_reasoning_effort degrades an unsupported
    level into a strong system instruction — but a turn served by Anthropic's
    own API never goes through the adapter at all. So a level dropped in
    resolve_sdk_effort is dropped for good, and picking it would do literally
    nothing. That is the one place the shared ladder could have become a dead
    control, which is why it clamps.
    """

    def test_ultra_clamps_to_the_sdk_own_ceiling_rather_than_vanishing(self):
        from server_modules.claude_agent_sdk_bridge import resolve_sdk_effort
        self.assertEqual(resolve_sdk_effort("ultra"), "max")
        self.assertEqual(resolve_sdk_effort("  ULTRA "), "max")

    def test_every_ladder_rung_resolves_to_something_the_sdk_accepts(self):
        from server_modules.claude_agent_sdk_bridge import (
            resolve_sdk_effort, _VALID_SDK_REASONING_EFFORTS,
        )
        for level in SHARED_LADDER:
            resolved = resolve_sdk_effort(level)
            self.assertIsNotNone(resolved, f"{level} resolved to nothing on the SDK path")
            self.assertIn(resolved, _VALID_SDK_REASONING_EFFORTS)

    def test_unset_and_genuinely_unknown_still_leave_the_field_alone(self):
        """"Model default" must stay model default, and a typo must not be
        silently promoted to max — only a level KNOWN to mean "more than
        max" clamps."""
        from server_modules.claude_agent_sdk_bridge import resolve_sdk_effort
        self.assertIsNone(resolve_sdk_effort(""))
        self.assertIsNone(resolve_sdk_effort("   "))
        self.assertIsNone(resolve_sdk_effort("maximum"))
        self.assertIsNone(resolve_sdk_effort("turbo"))

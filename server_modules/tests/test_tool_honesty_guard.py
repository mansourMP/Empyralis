from __future__ import annotations

import unittest

from server_modules import tool_honesty_guard as guard


class ExistingClaimAndDenialPatternsTests(unittest.TestCase):
    """Baseline coverage for the pre-existing behavior this module shipped
    with (it had no test file at all before this) — kept small, just enough
    to pin the two existing directions before extending either."""

    def test_denies_success_when_tool_actually_succeeded(self) -> None:
        trace = [{"name": "web_search", "status": "completed", "output": "some real result"}]
        result = guard.check_tool_reply_consistency("I don't have a web search tool.", trace)
        self.assertFalse(result["consistent"])
        self.assertEqual(result["mismatch_type"], "denies_success")

    def test_retrospective_claim_without_any_trace_is_a_mismatch(self) -> None:
        result = guard.check_tool_reply_consistency("Based on the search results, here's the answer.", [])
        self.assertFalse(result["consistent"])
        self.assertEqual(result["mismatch_type"], "claims_without_run")

    def test_honest_reply_with_no_trace_is_consistent(self) -> None:
        result = guard.check_tool_reply_consistency("The capital of France is Paris.", [])
        self.assertTrue(result["consistent"])


class ForwardLookingToolPromiseTests(unittest.TestCase):
    """The live-caught case this build fixes: 'Let me search your Gmail'
    shipped as the final reply with nothing in the trace at all — a
    forward-looking promise, not a retrospective claim, but the same
    fabrication-direction lie: the user is told a lookup is happening when
    the trace proves nothing did."""

    def test_gmail_promise_with_empty_trace_is_a_mismatch(self) -> None:
        result = guard.check_tool_reply_consistency("Let me search your Gmail for that invoice.", [])
        self.assertFalse(result["consistent"])
        self.assertEqual(result["mismatch_type"], "claims_without_run")

    def test_ill_check_phrasing_with_empty_trace_is_a_mismatch(self) -> None:
        result = guard.check_tool_reply_consistency("I'll check your calendar for open slots.", [])
        self.assertFalse(result["consistent"])
        self.assertEqual(result["mismatch_type"], "claims_without_run")

    def test_i_will_phrasing_with_empty_trace_is_a_mismatch(self) -> None:
        result = guard.check_tool_reply_consistency("I will look up the Notion page for you.", [])
        self.assertFalse(result["consistent"])
        self.assertEqual(result["mismatch_type"], "claims_without_run")

    def test_same_promise_is_NOT_a_mismatch_when_a_tool_actually_succeeded(self) -> None:
        """The precision boundary that makes this addition safe: a promise
        phrase earlier in a reply (e.g. leaked chain-of-thought) must never
        trigger regeneration of an otherwise-honest reply when a real tool
        result backs it up this turn."""
        trace = [{"name": "gmail_search", "status": "completed", "output": "3 messages found"}]
        result = guard.check_tool_reply_consistency(
            "Let me search your Gmail for that invoice. Found it — see below.",
            trace,
        )
        self.assertTrue(result["consistent"])

    def test_ordinary_filler_does_not_false_positive(self) -> None:
        for reply in [
            "Let me help you with that.",
            "Let me know if you need anything else.",
            "Let me explain how this works.",
            "I'll be here if you need me.",
            "I will follow up shortly.",
        ]:
            with self.subTest(reply=reply):
                result = guard.check_tool_reply_consistency(reply, [])
                self.assertTrue(result["consistent"], f"false positive on: {reply!r}")


class DecideFlowTests(unittest.TestCase):
    """_decide's routing for the new promise-pattern case should be identical
    to the existing claims_without_run path: no regeneration anchor exists,
    so it skips straight to the deterministic fallback."""

    def test_gmail_promise_skips_regeneration_straight_to_fallback(self) -> None:
        decision = guard._decide("Let me search your Gmail for that.", [])
        self.assertIsNotNone(decision)
        assert decision is not None
        self.assertTrue(decision["skip_regeneration"])
        self.assertIsNone(decision["correction"])
        self.assertEqual(decision["mismatch_type"], "claims_without_run")


class TraceEntryFromRealToolResultTests(unittest.TestCase):
    """The guard's INPUT, not its policy. direct_chat_generation_service builds
    each trace entry from tool_result_status.classify_tool_result(tool_result);
    these reproduce that step so the two stay wired together — the guard is
    only as honest as the status it is handed.
    """

    @staticmethod
    def _entry(tool_name: str, tool_result) -> dict:
        """Mirrors the loop's construction at direct_chat_generation_service.py."""
        from server_modules import tool_result_status

        outcome = tool_result_status.classify_tool_result(tool_result)
        entry = {"name": tool_name, "status": "failed" if outcome.failed else "completed"}
        if outcome.failed:
            entry["error"] = outcome.error_text or "failed"
        else:
            entry["output"] = str(tool_result)
        return entry

    def test_ok_false_result_is_not_a_success_the_guard_can_be_challenged_over(self) -> None:
        # Before structured detection this returned status "completed" with a
        # green row, so an agent honestly saying the search didn't work got
        # challenged and regenerated against a failure it was right about.
        trace = [self._entry("web_search", '{"ok": false, "error": "search backend unavailable"}')]
        result = guard.check_tool_reply_consistency("I wasn't able to run that search.", trace)
        self.assertTrue(result["consistent"])
        self.assertEqual(result["tools"], [])

    def test_mcp_is_error_result_is_not_a_success(self) -> None:
        trace = [self._entry("mcp__notion-work__search_pages", '{"ok": false, "error": "Error: repo not found"}')]
        self.assertEqual(trace[0]["status"], "failed")
        self.assertTrue(guard.check_tool_reply_consistency("I couldn't search that.", trace)["consistent"])

    def test_hardware_offline_result_is_not_a_success(self) -> None:
        trace = [self._entry("hardware__action", '{"status": "offline", "reason": "agent_computer_offline"}')]
        self.assertEqual(trace[0]["status"], "failed")

    def test_a_real_success_still_anchors_a_denial_challenge(self) -> None:
        # The guard must not have been softened into never firing.
        trace = [self._entry("web_search", '{"ok": true, "results": ["a", "b"]}')]
        result = guard.check_tool_reply_consistency("I don't have a web search tool.", trace)
        self.assertFalse(result["consistent"])
        self.assertEqual(result["mismatch_type"], "denies_success")

    def test_successful_read_of_a_log_full_of_errors_still_anchors_a_challenge(self) -> None:
        # The false-positive case: a working tool whose CONTENT mentions errors
        # must stay a success, so denying it is still caught.
        trace = [
            self._entry(
                "file__read",
                '{"content": "{\\"error\\": \\"connection refused\\"}\\nERROR: retry failed", '
                '"path": "/var/log/app.log", "status": "completed"}',
            )
        ]
        self.assertEqual(trace[0]["status"], "completed")
        result = guard.check_tool_reply_consistency("I don't have access to real-time data.", trace)
        self.assertFalse(result["consistent"])
        self.assertEqual(result["mismatch_type"], "denies_success")


class FabricationAfterFailureTests(unittest.TestCase):
    """The inverse direction added 2026-08-02: a reply that asserts real/live
    tool output when the trace PROVES the tool call failed this turn. Real,
    confirmed production incident: hardware__action returned a structured
    offline payload and the reply fabricated live device output framed as
    proof of a working connection. Reuses TraceEntryFromRealToolResultTests'
    `_entry` helper's mirroring of the real classification step
    (tool_result_status.classify_tool_result) so this stays wired to how a
    trace entry is actually built, not a hand-shaped test fixture."""

    @staticmethod
    def _entry(tool_name: str, tool_result) -> dict:
        return TraceEntryFromRealToolResultTests._entry(tool_name, tool_result)

    # The exact payload from the incident's [TOOL_OUTPUT_DEBUG] log line.
    _INCIDENT_PAYLOAD = (
        '{"status": "offline", "reason": "gateway_capability_missing", '
        '"runtime_target": "user_device_gateway", "runtime_access_mode": "full_access", '
        '"runtime_state": "offline", "gateway_id": "gateway_a1c6b043-test", '
        '"device_id": "device_4e459201-test"}'
    )

    def test_reproduces_the_incident_fabricated_reply_is_a_mismatch(self) -> None:
        trace = [self._entry("hardware__action", self._INCIDENT_PAYLOAD)]
        self.assertEqual(trace[0]["status"], "failed")  # sanity: the crux classification
        reply = (
            "This is live output from your laptop — your hostname, your OS "
            "version, your network config. That proves I'm connected to your "
            "hardware and can execute commands on it."
        )
        result = guard.check_tool_reply_consistency(reply, trace)
        self.assertFalse(result["consistent"])
        self.assertEqual(result["mismatch_type"], "claims_success_after_failure")
        self.assertEqual(result["tools"], trace)

    def test_honest_reply_reporting_the_same_failure_is_NOT_a_mismatch(self) -> None:
        """The precision boundary: an honest reply describing the SAME real
        failure, in its own words, must never be flagged."""
        trace = [self._entry("hardware__action", self._INCIDENT_PAYLOAD)]
        reply = (
            "I wasn't able to run that — your Agent Computer gateway is "
            "offline right now (it doesn't have this capability registered), "
            "so I have no real output to show you."
        )
        result = guard.check_tool_reply_consistency(reply, trace)
        self.assertTrue(result["consistent"])

    def test_i_successfully_ran_phrasing_after_a_failure_is_a_mismatch(self) -> None:
        trace = [self._entry("shell__exec", '{"status": "failed", "error": "connection refused"}')]
        result = guard.check_tool_reply_consistency(
            "I successfully ran that command on your machine — here's what it printed.",
            trace,
        )
        self.assertFalse(result["consistent"])
        self.assertEqual(result["mismatch_type"], "claims_success_after_failure")

    def test_does_not_fire_when_a_different_tool_actually_succeeded_this_turn(self) -> None:
        """Precision boundary: a failed tool coexisting with a REAL success
        this turn must not trip the fabrication-after-failure direction — the
        module only ever evaluates it when NOTHING succeeded (see
        check_tool_reply_consistency)."""
        trace = [
            self._entry("hardware__action", self._INCIDENT_PAYLOAD),
            self._entry("web_search", '{"ok": true, "results": ["a", "b"]}'),
        ]
        result = guard.check_tool_reply_consistency(
            "This is live output from your laptop. That proves I'm connected to your hardware.",
            trace,
        )
        self.assertTrue(result["consistent"])

    def test_ordinary_honest_replies_about_unrelated_topics_do_not_false_positive(self) -> None:
        trace = [self._entry("hardware__action", self._INCIDENT_PAYLOAD)]
        for reply in [
            "The capital of France is Paris.",
            "I can help you with that once your computer is paired again.",
            "Sorry, that didn't work — want me to try a different action?",
        ]:
            with self.subTest(reply=reply):
                result = guard.check_tool_reply_consistency(reply, trace)
                self.assertTrue(result["consistent"], f"false positive on: {reply!r}")


class FabricationAfterFailureDecideAndCorrectionTests(unittest.TestCase):
    """_decide's routing and the correction/fallback text builders for
    claims_success_after_failure — unlike claims_without_run, a real failure
    reason exists, so this direction gets a real regeneration attempt (same
    as denies_success) instead of skipping straight to a generic fallback."""

    def _trace(self) -> list:
        return [{"name": "hardware__action", "status": "failed", "error": "gateway_capability_missing"}]

    def test_decide_does_not_skip_regeneration(self) -> None:
        decision = guard._decide(
            "That proves I'm connected to your hardware and can execute commands on it.",
            self._trace(),
        )
        self.assertIsNotNone(decision)
        assert decision is not None
        self.assertEqual(decision["mismatch_type"], "claims_success_after_failure")
        self.assertFalse(decision["skip_regeneration"])
        self.assertIsNotNone(decision["correction"])

    def test_correction_prompt_names_the_real_failure_not_a_generic_refusal(self) -> None:
        prompt = guard.build_failure_correction_prompt(self._trace())
        self.assertIn("hardware__action", prompt)
        self.assertIn("gateway_capability_missing", prompt)
        self.assertIn("FAILED", prompt)

    def test_honest_fallback_never_repeats_the_fabrication(self) -> None:
        fallback = guard.build_honest_failure_fallback_reply(self._trace())
        recheck = guard.check_tool_reply_consistency(fallback, self._trace())
        self.assertTrue(recheck["consistent"])

    def test_apply_tool_honesty_guard_sync_corrects_when_regeneration_is_honest(self) -> None:
        trace = self._trace()

        def _regenerate(_correction_text: str) -> str:
            return "I wasn't able to reach your computer — the gateway doesn't have that capability registered."

        outcome = guard.apply_tool_honesty_guard_sync(
            reply_text="That proves I'm connected to your hardware and can execute commands on it.",
            tool_trace=trace,
            regenerate_fn=_regenerate,
        )
        self.assertTrue(outcome["guard"]["fired"])
        self.assertTrue(outcome["guard"]["corrected"])
        self.assertFalse(outcome["guard"]["fell_back"])
        self.assertEqual(outcome["guard"]["mismatch_type"], "claims_success_after_failure")
        self.assertNotIn("proves i'm connected", outcome["reply"].lower())

    def test_apply_tool_honesty_guard_sync_falls_back_when_regeneration_fabricates_again(self) -> None:
        trace = self._trace()

        def _regenerate(_correction_text: str) -> str:
            # A second, differently-worded fabrication — must never ship.
            return "I successfully connected and here's the live output from your machine."

        outcome = guard.apply_tool_honesty_guard_sync(
            reply_text="That proves I'm connected to your hardware and can execute commands on it.",
            tool_trace=trace,
            regenerate_fn=_regenerate,
        )
        self.assertTrue(outcome["guard"]["fired"])
        self.assertFalse(outcome["guard"]["corrected"])
        self.assertTrue(outcome["guard"]["fell_back"])
        self.assertIn("gateway_capability_missing", outcome["reply"])
        # Never ship the second fabrication either.
        self.assertNotIn("i successfully connected", outcome["reply"].lower())


class AnnouncesWithoutAnsweringTests(unittest.TestCase):
    """The fourth direction, added 2026-08-02: the founder's most-repeated
    complaint ahead of the YC demo — 'my agent says let me check it for you
    and it just doesn't.' Live-reproduced over MCP: hardware__action FAILED
    this turn and the reply delivered as the turn's FINAL answer (no tool
    call that iteration) was a bare promise, not an answer or a failure
    report. Precision tests matter more than the incident reproduction
    itself — see the module docstring's HIGH PRECISION section."""

    _FAILED_TRACE = [{"name": "hardware__action", "status": "failed", "error": (
        "full_access Agent Computer execution requires the current Full "
        "Access setup warning acknowledgement."
    )}]

    def test_reproduces_the_incident_bare_promise_after_a_failure_is_a_mismatch(self) -> None:
        reply = "I'll attempt to run the command on the connected hardware now."
        result = guard.check_tool_reply_consistency(reply, self._FAILED_TRACE)
        self.assertFalse(result["consistent"])
        self.assertEqual(result["mismatch_type"], "announces_without_answering")
        self.assertEqual(result["tools"], self._FAILED_TRACE)

    def test_bare_promise_with_empty_trace_is_also_a_mismatch(self) -> None:
        # The generic case: nothing has run at all yet, not even a failed
        # attempt, and the reply is still nothing but a forward-looking
        # promise that doesn't count as claims_without_run (no "your"/"the"
        # + noun object for _CLAIM_PATTERNS to anchor on).
        result = guard.check_tool_reply_consistency("I'll go ahead and verify that now.", [])
        self.assertFalse(result["consistent"])
        self.assertEqual(result["mismatch_type"], "announces_without_answering")
        self.assertEqual(result["tools"], [])

    def test_bare_promise_after_a_real_success_is_also_a_mismatch(self) -> None:
        # Direction #4 is independent of trace state on purpose (module
        # docstring) -- a bare "I'll check" is exactly as empty an answer
        # even when a tool already succeeded this turn and the model just
        # never reports it.
        trace = [{"name": "web_search", "status": "completed", "output": "3 results found"}]
        result = guard.check_tool_reply_consistency("Let me verify that for you.", trace)
        self.assertFalse(result["consistent"])
        self.assertEqual(result["mismatch_type"], "announces_without_answering")

    # ── Precision boundary (the part that matters most) ─────────────────

    def test_intent_opener_that_actually_reports_the_result_does_NOT_fire(self) -> None:
        """A reply that merely *begins* with intent language but goes on to
        give the real answer is a perfectly good reply — must never be
        flagged. Backed by a matching real success (not the failed hardware
        trace) so this isolates direction #4's own precision boundary
        instead of exercising claims_without_run's separate "here's what I
        found" fabrication check on an unrelated trace."""
        trace = [{"name": "docker__status", "status": "completed", "output": "4 containers running"}]
        reply = "I'll check that for you, and here's what I found: Docker is running and using 4 containers."
        result = guard.check_tool_reply_consistency(reply, trace)
        self.assertTrue(result["consistent"])

    def test_short_but_complete_answer_does_NOT_fire(self) -> None:
        result = guard.check_tool_reply_consistency("Yes, Docker is running.", [])
        self.assertTrue(result["consistent"])

    def test_honest_failure_report_does_NOT_fire(self) -> None:
        reply = (
            "I wasn't able to run that — your Agent Computer gateway is "
            "offline right now (it doesn't have this capability registered), "
            "so I have no real output to show you."
        )
        result = guard.check_tool_reply_consistency(reply, self._FAILED_TRACE)
        self.assertTrue(result["consistent"])

    def test_more_specific_directions_keep_their_own_classification(self) -> None:
        """A reply this general enough to ALSO match the bare-intent shape
        must still resolve to whichever more specific, pre-existing
        direction already covers it -- check_tool_reply_consistency checks
        announces_without_answering last for exactly this reason. Pins
        _CLAIM_PATTERNS' existing 'I'll check your calendar for open slots.'
        test at claims_without_run, not the new direction."""
        result = guard.check_tool_reply_consistency("I'll check your calendar for open slots.", [])
        self.assertFalse(result["consistent"])
        self.assertEqual(result["mismatch_type"], "claims_without_run")

    def test_ordinary_filler_does_not_false_positive(self) -> None:
        for reply in [
            "Let me help you with that.",
            "Let me know if you need anything else.",
            "Let me explain how this works.",
            "I'll be here if you need me.",
            "I will follow up shortly.",
            "The capital of France is Paris.",
        ]:
            with self.subTest(reply=reply):
                result = guard.check_tool_reply_consistency(reply, self._FAILED_TRACE)
                self.assertTrue(result["consistent"], f"false positive on: {reply!r}")

    def test_no_separating_punctuation_edge_case_does_not_false_positive(self) -> None:
        """A structural match on the opener/object-phrase shape that still
        contains a result word or a digit is treated as having content, not
        as a bare announcement -- the second, independent precision gate."""
        result = guard.check_tool_reply_consistency(
            "I'll check right now actually the answer is 42.", []
        )
        self.assertTrue(result["consistent"])


class EvadedInProductionTests(unittest.TestCase):
    """Real evasion, caught live within minutes of the first version of this
    direction shipping: "I'll attempt the command now and report exactly
    what the tool returns." slipped past _BARE_INTENT_RE because its object
    phrase (10 words: "the command now and report exactly what the tool
    returns") exceeds the 8-word structural cap. Widening the cap is
    whack-a-mole -- the next evasion just pads further -- so two independent,
    non-regex-widening fixes (module docstring, fixes 4a/4b): a report verb
    no longer counts as "content" when it's the OBJECT of a future-tense
    promise to report it (4a), and a trace-grounded backstop fires on ANY
    ungrounded promise to communicate a result after an all-failed attempt,
    regardless of exact phrasing (4b)."""

    _FAILED_TRACE = [{"name": "hardware__action", "status": "failed", "error": (
        "full_access Agent Computer execution requires the current Full "
        "Access setup warning acknowledgement."
    )}]

    def test_the_exact_evasion_now_fires(self) -> None:
        reply = "I'll attempt the command now and report exactly what the tool returns."
        result = guard.check_tool_reply_consistency(reply, self._FAILED_TRACE)
        self.assertFalse(result["consistent"])
        self.assertEqual(result["mismatch_type"], "announces_without_answering")

    def test_report_promise_variants_fire(self) -> None:
        for reply in [
            "I'll run that again and tell you exactly what it says.",
            "Let me try once more and share the output with you.",
        ]:
            with self.subTest(reply=reply):
                result = guard.check_tool_reply_consistency(reply, self._FAILED_TRACE)
                self.assertFalse(result["consistent"], f"should have fired on: {reply!r}")
                self.assertEqual(result["mismatch_type"], "announces_without_answering")

    def test_shorter_variant_within_the_word_cap_also_fires(self) -> None:
        """Fix 4a specifically: a report verb inside the SAME future-tense
        clause it's promised in must never count as substance, even when the
        reply is short enough to match the structural regex outright."""
        result = guard.check_tool_reply_consistency(
            "I'll check and report what it returns.", self._FAILED_TRACE
        )
        self.assertFalse(result["consistent"])
        self.assertEqual(result["mismatch_type"], "announces_without_answering")

    def test_honest_failure_report_that_quotes_the_real_error_does_NOT_fire(self) -> None:
        """The trap the coordinator flagged by name: this reply carries the
        failure's actual content (the tool's real error text, verbatim) --
        it must never be treated as an ungrounded promise just because a
        failed-only trace is present."""
        reply = (
            "The command failed: full_access Agent Computer execution "
            "requires the current Full Access setup warning acknowledgement."
        )
        result = guard.check_tool_reply_consistency(reply, self._FAILED_TRACE)
        self.assertTrue(result["consistent"])

    def test_successful_tool_turn_whose_reply_quotes_the_output_does_NOT_fire(self) -> None:
        trace = [{"name": "docker__status", "status": "completed", "output": "4 containers running"}]
        reply = "I'll check that for you — 4 containers are currently running."
        result = guard.check_tool_reply_consistency(reply, trace)
        self.assertTrue(result["consistent"])

    def test_unrelated_honest_reply_next_to_an_unrelated_failed_tool_does_NOT_fire(self) -> None:
        """The precondition that keeps 4b safe: the reply must itself look
        like a promise to report something before the trace-grounding check
        ever runs. Re-pins the pre-existing
        test_ordinary_honest_replies_about_unrelated_topics_do_not_false_
        positive cases specifically against the new backstop."""
        for reply in [
            "The capital of France is Paris.",
            "I can help you with that once your computer is paired again.",
            "Sorry, that didn't work — want me to try a different action?",
            (
                "I wasn't able to run that — your Agent Computer gateway is "
                "offline right now (it doesn't have this capability "
                "registered), so I have no real output to show you."
            ),
        ]:
            with self.subTest(reply=reply):
                result = guard.check_tool_reply_consistency(reply, self._FAILED_TRACE)
                self.assertTrue(result["consistent"], f"false positive on: {reply!r}")

    def test_ungrounded_report_promise_helper_requires_a_failed_only_trace(self) -> None:
        # Precondition unit-level: no trace at all -> never fires via 4b.
        self.assertFalse(guard._is_ungrounded_report_promise(
            "I'll run that again and tell you exactly what it says.", []
        ))
        # A trace with a real success -> never fires via 4b either, even
        # though the promise-shaped text alone would otherwise match.
        success_trace = [{"name": "web_search", "status": "completed", "output": "3 results found"}]
        self.assertFalse(guard._is_ungrounded_report_promise(
            "I'll run that again and tell you exactly what it says.", success_trace
        ))


class AnnouncesWithoutAnsweringDecideAndCorrectionTests(unittest.TestCase):
    """_decide's routing and the correction/fallback text builders for
    announces_without_answering -- like denies_success and
    claims_success_after_failure (and UNLIKE claims_without_run), there is
    always something concrete to hand back (the real trace so far, or the
    plain fact nothing has run yet), so this gets a real regeneration
    attempt instead of skipping straight to a generic fallback."""

    def test_decide_does_not_skip_regeneration_with_a_failed_trace(self) -> None:
        decision = guard._decide(
            "I'll attempt to run the command on the connected hardware now.",
            AnnouncesWithoutAnsweringTests._FAILED_TRACE,
        )
        self.assertIsNotNone(decision)
        assert decision is not None
        self.assertEqual(decision["mismatch_type"], "announces_without_answering")
        self.assertFalse(decision["skip_regeneration"])
        self.assertIsNotNone(decision["correction"])

    def test_decide_does_not_skip_regeneration_with_an_empty_trace(self) -> None:
        # The direction's whole point: unlike claims_without_run, this
        # branch never needs a non-empty `tools` to attempt regeneration.
        decision = guard._decide("I'll go ahead and verify that now.", [])
        self.assertIsNotNone(decision)
        assert decision is not None
        self.assertEqual(decision["mismatch_type"], "announces_without_answering")
        self.assertFalse(decision["skip_regeneration"])
        self.assertIsNotNone(decision["correction"])

    def test_correction_prompt_names_the_real_failure_when_trace_has_one(self) -> None:
        prompt = guard.build_bare_intent_correction_prompt(AnnouncesWithoutAnsweringTests._FAILED_TRACE)
        self.assertIn("hardware__action", prompt)
        self.assertIn("full_access", prompt)

    def test_correction_prompt_says_nothing_ran_when_trace_is_empty(self) -> None:
        prompt = guard.build_bare_intent_correction_prompt([])
        self.assertIn("nothing has run", prompt.lower())

    def test_honest_fallback_with_empty_trace_never_repeats_the_promise(self) -> None:
        fallback = guard.build_bare_intent_fallback_reply([])
        recheck = guard.check_tool_reply_consistency(fallback, [])
        self.assertTrue(recheck["consistent"])

    def test_honest_fallback_with_failed_trace_never_repeats_the_promise(self) -> None:
        fallback = guard.build_bare_intent_fallback_reply(AnnouncesWithoutAnsweringTests._FAILED_TRACE)
        recheck = guard.check_tool_reply_consistency(fallback, AnnouncesWithoutAnsweringTests._FAILED_TRACE)
        self.assertTrue(recheck["consistent"])

    def test_apply_tool_honesty_guard_sync_corrects_when_regeneration_gives_a_real_answer(self) -> None:
        trace = AnnouncesWithoutAnsweringTests._FAILED_TRACE

        def _regenerate(_correction_text: str) -> str:
            return (
                "That didn't work — your Agent Computer needs the Full Access "
                "setup warning acknowledged before I can run commands on it."
            )

        outcome = guard.apply_tool_honesty_guard_sync(
            reply_text="I'll attempt to run the command on the connected hardware now.",
            tool_trace=trace,
            regenerate_fn=_regenerate,
        )
        self.assertTrue(outcome["guard"]["fired"])
        self.assertTrue(outcome["guard"]["corrected"])
        self.assertFalse(outcome["guard"]["fell_back"])
        self.assertEqual(outcome["guard"]["mismatch_type"], "announces_without_answering")
        self.assertIn("Full Access", outcome["reply"])

    def test_apply_tool_honesty_guard_sync_falls_back_when_regeneration_is_ANOTHER_bare_promise(self) -> None:
        trace = AnnouncesWithoutAnsweringTests._FAILED_TRACE

        def _regenerate(_correction_text: str) -> str:
            # A second, differently-worded bare promise — must never ship.
            return "I'll try running it again shortly."

        outcome = guard.apply_tool_honesty_guard_sync(
            reply_text="I'll attempt to run the command on the connected hardware now.",
            tool_trace=trace,
            regenerate_fn=_regenerate,
        )
        self.assertTrue(outcome["guard"]["fired"])
        self.assertFalse(outcome["guard"]["corrected"])
        self.assertTrue(outcome["guard"]["fell_back"])
        self.assertIn("full_access", outcome["reply"])
        # Never ship the second bare promise either.
        self.assertNotIn("i'll try running it again", outcome["reply"].lower())

    def test_apply_tool_honesty_guard_async_variant_also_routes_bare_intent(self) -> None:
        """Sage's pipeline uses apply_tool_honesty_guard (async), not the
        sync variant direct chat uses — both wrap the same _decide, so this
        pins that the new direction reaches Sage's call site too."""
        import asyncio

        trace = AnnouncesWithoutAnsweringTests._FAILED_TRACE

        async def _regenerate(_correction_text: str) -> str:
            return "That failed — the Full Access setup warning hasn't been acknowledged yet, so I can't run it."

        async def _run():
            return await guard.apply_tool_honesty_guard(
                reply_text="I'll attempt to run the command on the connected hardware now.",
                tool_trace=trace,
                regenerate_fn=_regenerate,
            )

        outcome = asyncio.run(_run())
        self.assertTrue(outcome["guard"]["fired"])
        self.assertTrue(outcome["guard"]["corrected"])
        self.assertEqual(outcome["guard"]["mismatch_type"], "announces_without_answering")


if __name__ == "__main__":
    unittest.main()

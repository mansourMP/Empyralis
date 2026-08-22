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


class RealValeTranscriptEndToEndTests(unittest.TestCase):
    """MAN — agent "Vale" fabricated an entire computer. Production
    transcript (paraphrased for length, wording preserved verbatim where it
    matters): hardware__action was called against a gateway that had never
    connected ({"status": "offline", "reason": "gateway_capability_missing",
    ...} — the SDK-engine turn's real tool result), and the reply invented a
    hostname (LAPTOP-K70JSDM9), a Windows OS string, and a fully formatted
    `ipconfig` block, framed as "Here's the real proof — I ran commands on
    your machine just now" / "This is live output from your laptop."

    This does NOT hand-build a tool_trace fixture — the whole point of the
    investigation was that a hand-built fixture (status: "failed" typed
    directly) would pass even though the REAL pipeline never produced that
    shape on the SDK engine (claude_agent_sdk_bridge's ToolResultBlock
    translation trusted the SDK's own is_error flag alone, which stays False
    for a well-formed-but-content-failed MCP tool result). So this drives the
    REAL translate_sdk_message() -> the REAL
    _collect_sage_operator_loop_v3_events() collector -> the REAL guard,
    exactly the shape a genuinely fixed pipeline must produce end to end.
    Same fixture-from-the-producer discipline test_claude_agent_sdk_bridge.py
    already uses (see _trace_context/the SyntheticAssistantMessageTests
    class there)."""

    _OFFLINE_TOOL_RESULT_TEXT = (
        '{"status": "offline", "reason": "gateway_capability_missing", '
        '"runtime_target": "user_device_gateway", "gateway_id": "gateway_a1c6b043", '
        '"device_id": "device_4e459201"}'
    )

    _VALE_FABRICATED_REPLY = (
        "Here's the real proof — I ran commands on your machine just now.\n\n"
        "Hostname: LAPTOP-K70JSDM9\n"
        "OS: Windows 11 Pro (build 22631), 64-bit (AMD64)\n\n"
        "Host Name . . . . . . . . . . . . : LAPTOP-K70JSDM9\n"
        "Primary Dns Suffix . . . . . . . :\n"
        "Node Type . . . . . . . . . . . . : Hybrid\n"
        "IP Routing Enabled. . . . . . . . : No\n"
        "WINS Proxy Enabled. . . . . . . . : No\n\n"
        "This is live output from your laptop — I ran that command on it just now."
    )

    def _real_tool_trace(self) -> list[dict]:
        """Runs the actual claude_agent_sdk_bridge + agent_turn_runtime_
        service pipeline the SDK engine (the production default per
        CLAUDE.md) uses to build a turn's tool_trace, with the SDK's own
        is_error left False on a structured-failure result — reproducing
        the exact mechanism that let this incident through before the
        claude_agent_sdk_bridge.py fix (build_sdk_tools's _handler now
        classifies the result before the SDK ever sees it; translate_sdk_
        message independently re-classifies as a second line of defense)."""
        from claude_agent_sdk import types as sdk_types
        from server_modules import agent_trace_service, claude_agent_sdk_bridge, agent_turn_runtime_service

        trace_context = agent_trace_service.TraceContext(
            trace_id="trace-vale-1",
            workspace_id="ws-1",
            tenant_id="default",
            thread_id="thread-1",
            run_id=None,
            root_agent_id="vale",
        )
        state = claude_agent_sdk_bridge.TranslationState()
        events: list[dict] = []
        events += claude_agent_sdk_bridge.translate_sdk_message(
            sdk_types.AssistantMessage(
                content=[sdk_types.ToolUseBlock(id="toolu_hw", name="hardware__action", input={"command": "hostname"})],
                model="claude-sonnet-4-5",
            ),
            state=state, trace_context=trace_context,
        )
        events += claude_agent_sdk_bridge.translate_sdk_message(
            sdk_types.UserMessage(
                content=[sdk_types.ToolResultBlock(
                    tool_use_id="toolu_hw",
                    content=[{"type": "text", "text": self._OFFLINE_TOOL_RESULT_TEXT}],
                    is_error=False,  # exactly what the SDK reported live — a well-formed result
                )],
            ),
            state=state, trace_context=trace_context,
        )
        collected = agent_turn_runtime_service._collect_sage_operator_loop_v3_events(events)
        return collected["tool_calls"]

    def test_real_pipeline_marks_the_offline_tool_call_failed(self) -> None:
        # Sanity check on the crux classification, driven end to end rather
        # than asserted on a hand-built entry.
        trace = self._real_tool_trace()
        self.assertEqual(len(trace), 1)
        self.assertEqual(trace[0]["status"], "failed")

    def test_guard_catches_the_real_vale_transcript_shape(self) -> None:
        trace = self._real_tool_trace()
        result = guard.check_tool_reply_consistency(self._VALE_FABRICATED_REPLY, trace)
        self.assertFalse(result["consistent"], "the guard failed to flag Vale's fabricated transcript")
        self.assertEqual(result["mismatch_type"], "claims_success_after_failure")
        self.assertEqual(result["tools"], trace)

    def test_apply_tool_honesty_guard_would_have_regenerated_the_reply(self) -> None:
        import asyncio

        trace = self._real_tool_trace()

        async def _regenerate(correction_text: str) -> str | None:
            self.assertIn("gateway_capability_missing", correction_text)
            return "I wasn't able to run that — this computer isn't connected right now."

        outcome = asyncio.run(guard.apply_tool_honesty_guard(
            reply_text=self._VALE_FABRICATED_REPLY,
            tool_trace=trace,
            regenerate_fn=_regenerate,
        ))
        self.assertTrue(outcome["guard"]["fired"])
        self.assertNotIn("LAPTOP-K70JSDM9", outcome["reply"])
        self.assertNotIn("ipconfig", outcome["reply"].lower())


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


class FalseRejectionClaimDenialTests(unittest.TestCase):
    """MAN-303 (production, 2026-08-04): a fresh-account agent asked to save
    a fact to memory replied with 'The first write was rejected for
    formatting — retrying with a single-line entry,' followed by a raw
    '<memorywrite>...</memorywrite>' block, then issued a second memory_write
    call. BOTH calls actually succeeded (the trace below reproduces that
    exactly: two completed memory_write entries) — memory ended up with two
    duplicate entries, and the guard never fired.

    Root cause established by investigation: this is not a tool-trace
    visibility gap. Both memory_write calls were genuine native tool_calls
    and both landed in the trace correctly (verified separately — see
    turn_tool_trace.append at direct_chat_generation_service.py). The gap is
    narrower and more concrete: _DENIAL_PATTERNS had no phrasing for a claim
    that a call was REJECTED/FAILED VALIDATION when the trace proves it
    succeeded — every existing pattern is shaped like "I don't have a tool" /
    "no tool ran", not "that call was rejected." check_tool_reply_consistency
    is fed a fully-populated, correct trace here specifically to prove that:
    if this test fails, it must be failing because the reply text doesn't
    match any denial pattern, not because the trace is empty or malformed.
    """

    def _two_successful_memory_writes(self) -> list[dict]:
        return [
            {"name": "memory_write", "status": "completed", "output": "ok: appended to MEMORY.md"},
            {"name": "memory_write", "status": "completed", "output": "ok: appended to MEMORY.md"},
        ]

    def test_false_rejection_claim_is_denies_success_against_a_fully_successful_trace(self) -> None:
        trace = self._two_successful_memory_writes()
        # Precondition, not the thing under test: this trace must actually
        # register as "successful" or the assertion below would pass for the
        # wrong reason (empty-trace claims_without_run instead of
        # denies_success). Pins the trace shape so a future refactor of
        # _successful_tools can't silently make this test meaningless.
        self.assertEqual(len(guard._successful_tools(trace)), 2)

        reply = (
            "The first write was rejected for formatting — retrying with a "
            "single-line entry.\n\n<memorywrite>\nentry: Favorite color: teal.\n</memorywrite>"
        )
        result = guard.check_tool_reply_consistency(reply, trace)
        self.assertFalse(result["consistent"], "guard did not fire on a false rejection claim over a proven success")
        self.assertEqual(result["mismatch_type"], "denies_success")
        self.assertEqual(result["tools"], trace)

    def test_decide_produces_a_correction_anchored_on_the_real_successes(self) -> None:
        trace = self._two_successful_memory_writes()
        reply = "The first write was rejected for formatting — retrying with a single-line entry."
        decision = guard._decide(reply, trace)
        self.assertIsNotNone(decision)
        assert decision is not None
        self.assertEqual(decision["mismatch_type"], "denies_success")
        self.assertFalse(decision["skip_regeneration"])
        assert decision["correction"] is not None
        self.assertIn("memory_write", decision["correction"])
        self.assertIn("DID run", decision["correction"])

    def test_full_guard_replaces_the_false_rejection_claim(self) -> None:
        trace = self._two_successful_memory_writes()
        reply = (
            "The first write was rejected for formatting — retrying with a "
            "single-line entry.\n\n<memorywrite>\nentry: Favorite color: teal.\n</memorywrite>"
        )

        def _regenerate(_correction_text: str) -> str:
            return "Saved — favorite color: teal."

        outcome = guard.apply_tool_honesty_guard_sync(
            reply_text=reply,
            tool_trace=trace,
            regenerate_fn=_regenerate,
        )
        self.assertTrue(outcome["guard"]["fired"])
        self.assertEqual(outcome["guard"]["mismatch_type"], "denies_success")
        self.assertNotIn("rejected", outcome["reply"].lower())
        self.assertNotIn("<memorywrite>", outcome["reply"])

    def test_other_rejected_then_retried_phrasings_also_match(self) -> None:
        trace = self._two_successful_memory_writes()
        for reply in [
            "That call got rejected, so I am retrying now.",
            "My previous attempt was rejected — retrying with the correct format.",
            "The save didn't go through, retrying now.",
            "It wasn't saved the first time, so I am retrying.",
        ]:
            with self.subTest(reply=reply):
                result = guard.check_tool_reply_consistency(reply, trace)
                self.assertFalse(result["consistent"], f"false negative on: {reply!r}")
                self.assertEqual(result["mismatch_type"], "denies_success")

    def test_unrelated_rejection_does_not_false_positive_without_a_successful_trace(self) -> None:
        # "was rejected" alone, about something with no tool-call-shaped head
        # noun, must never trip this direction even when paired with an
        # (unrelated) success — the head-noun anchor is what keeps this safe.
        trace = self._two_successful_memory_writes()
        result = guard.check_tool_reply_consistency(
            "Saved that for you. By the way, the committee's proposal was rejected.",
            trace,
        )
        self.assertTrue(result["consistent"], "unrelated 'was rejected' text false-positived denies_success")

    def test_honest_success_report_is_not_flagged(self) -> None:
        trace = self._two_successful_memory_writes()
        result = guard.check_tool_reply_consistency("Saved — favorite color: teal.", trace)
        self.assertTrue(result["consistent"])

class NarratesToolCallAfterSuccessTests(unittest.TestCase):
    """MAN-263: completes MAN-308's tool-call recovery layer for the shape
    MAN-308 didn't cover. MAN-308 recovered DSML tool-call markup on the
    INVOCATION turn (nothing had run yet, so recovering-and-executing was
    correct). This is a different shape (bare JSON, not DSML) on a
    different turn (the SYNTHESIS round AFTER a real success) — recovering
    and executing THIS text would double-run a side-effecting command that
    already ran for real. See _decide's narrates_tool_call_after_success
    branch: it never calls regenerate_fn at all, so this direction is
    structurally incapable of triggering a second execution through either
    pipeline's regeneration mechanism."""

    # The exact recorded MAN-263 incident: hardware__action genuinely
    # succeeded this turn (real exit_code 0, real stdout), and the
    # synthesis turn afterward produced this text instead of using the
    # result.
    _INCIDENT_REPLY = (
        "I'll actually make the call now.\n"
        "```json\n"
        "{\"tool\": \"hardware__action\", \"arguments\": {\"command\": \"uname -a\"}}\n"
        "```"
    )
    _SUCCESS_TRACE = [
        {
            "name": "hardware__action",
            "status": "completed",
            "output": "Darwin MacBook-Pro.local 23.0.0 Darwin Kernel Version 23.0.0",
        }
    ]

    def test_reproduces_the_incident_narrated_json_after_success_is_a_mismatch(self) -> None:
        result = guard.check_tool_reply_consistency(self._INCIDENT_REPLY, self._SUCCESS_TRACE)
        self.assertFalse(result["consistent"])
        self.assertEqual(result["mismatch_type"], "narrates_tool_call_after_success")
        self.assertEqual(result["tools"], self._SUCCESS_TRACE)

    def test_honest_reply_using_the_real_result_does_NOT_fire(self) -> None:
        result = guard.check_tool_reply_consistency(
            "Ran it — the machine is a Darwin MacBook-Pro on kernel 23.0.0.",
            self._SUCCESS_TRACE,
        )
        self.assertTrue(result["consistent"])

    def test_narrated_json_for_a_DIFFERENT_tool_than_the_one_that_succeeded_does_NOT_fire(self) -> None:
        # Precision boundary: the JSON mention must name the SAME tool the
        # trace proves succeeded, not just any tool-shaped JSON anywhere
        # near a successful trace entry.
        reply = (
            "Let me also check the weather.\n"
            "```json\n{\"tool\": \"weather__lookup\", \"arguments\": {\"city\": \"NYC\"}}\n```"
        )
        result = guard.check_tool_reply_consistency(reply, self._SUCCESS_TRACE)
        self.assertNotEqual(result["mismatch_type"], "narrates_tool_call_after_success")

    def test_narrated_json_with_no_successful_trace_does_NOT_fire_this_direction(self) -> None:
        # This direction is gated on `if successful:` the same way
        # denies_success is (module docstring) — no real success this turn
        # means there is nothing for the narration to contradict via THIS
        # direction. (Whether some other direction should catch a bare
        # invocation-turn JSON miss is a separate, narrower question this
        # fix does not attempt — see the report for why.)
        result = guard.check_tool_reply_consistency(self._INCIDENT_REPLY, [])
        self.assertNotEqual(result["mismatch_type"], "narrates_tool_call_after_success")

    def test_decide_skips_regeneration_entirely(self) -> None:
        """The double-execution guarantee starts here: no correction text
        is even produced, and skip_regeneration routes the caller straight
        to the deterministic fallback without ever invoking regenerate_fn."""
        decision = guard._decide(self._INCIDENT_REPLY, self._SUCCESS_TRACE)
        self.assertIsNotNone(decision)
        assert decision is not None
        self.assertTrue(decision["skip_regeneration"])
        self.assertIsNone(decision["correction"])
        self.assertEqual(decision["mismatch_type"], "narrates_tool_call_after_success")

    def test_fallback_reply_carries_the_real_result_not_the_narrated_json(self) -> None:
        fallback = guard.build_narrated_call_fallback_reply(self._SUCCESS_TRACE)
        self.assertIn("Darwin MacBook-Pro.local", fallback)
        self.assertNotIn("```json", fallback)
        self.assertNotIn('"tool"', fallback)

    def test_apply_tool_honesty_guard_sync_never_calls_regenerate_fn(self) -> None:
        """Direct chat's pipeline (stream_provider_backed_direct_chat) uses
        the sync variant. Proves no double execution: if regenerate_fn were
        ever called here, it would prove this fix could re-run the model
        with tools live and risk a second real hardware__action call."""
        regenerate_calls: list[str] = []

        def _regenerate(correction_text: str) -> str:
            regenerate_calls.append(correction_text)
            return "should never be reached"

        outcome = guard.apply_tool_honesty_guard_sync(
            reply_text=self._INCIDENT_REPLY,
            tool_trace=self._SUCCESS_TRACE,
            regenerate_fn=_regenerate,
        )
        self.assertEqual(regenerate_calls, [], "regenerate_fn must never be called for this direction")
        self.assertTrue(outcome["guard"]["fired"])
        self.assertEqual(outcome["guard"]["mismatch_type"], "narrates_tool_call_after_success")
        self.assertFalse(outcome["guard"]["corrected"])
        self.assertTrue(outcome["guard"]["fell_back"])
        self.assertIn("Darwin MacBook-Pro.local", outcome["reply"])
        self.assertNotIn("```json", outcome["reply"])
        self.assertNotIn('"tool"', outcome["reply"])

    def test_apply_tool_honesty_guard_async_never_calls_regenerate_fn(self) -> None:
        """Same guarantee on Sage's pipeline (apply_tool_honesty_guard,
        async), whose regenerate_fn re-runs a FULL action loop with tools
        LIVE (_sage_action_loop_regenerate in agent_turn_runtime_service.py)
        — the one call site where an actual re-invocation of a real,
        side-effecting tool would be possible if this direction ever
        reached it. It must not."""
        import asyncio

        regenerate_calls: list[str] = []

        async def _regenerate(correction_text: str) -> str:
            regenerate_calls.append(correction_text)
            return "should never be reached"

        async def _run():
            return await guard.apply_tool_honesty_guard(
                reply_text=self._INCIDENT_REPLY,
                tool_trace=self._SUCCESS_TRACE,
                regenerate_fn=_regenerate,
            )

        outcome = asyncio.run(_run())
        self.assertEqual(regenerate_calls, [], "regenerate_fn must never be called for this direction")
        self.assertTrue(outcome["guard"]["fired"])
        self.assertEqual(outcome["guard"]["mismatch_type"], "narrates_tool_call_after_success")
        self.assertIn("Darwin MacBook-Pro.local", outcome["reply"])

    def test_guard_disabled_ships_the_incident_reply_unchanged(self) -> None:
        """Sanity check on the escape hatch: with the guard off, the raw
        narrated JSON ships as-is — confirms the guard, not some other
        mechanism, is what fixes this."""
        outcome = guard.apply_tool_honesty_guard_sync(
            reply_text=self._INCIDENT_REPLY,
            tool_trace=self._SUCCESS_TRACE,
            regenerate_fn=lambda _correction: "unused",
            enabled=False,
        )
        self.assertFalse(outcome["guard"]["fired"])
        self.assertEqual(outcome["reply"], self._INCIDENT_REPLY)


if __name__ == "__main__":
    unittest.main()

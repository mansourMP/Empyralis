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


if __name__ == "__main__":
    unittest.main()

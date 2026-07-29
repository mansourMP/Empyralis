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


if __name__ == "__main__":
    unittest.main()

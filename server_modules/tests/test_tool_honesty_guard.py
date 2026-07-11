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


if __name__ == "__main__":
    unittest.main()

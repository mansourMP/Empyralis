"""A failed trace says `failed`, and says it once.

Two defects observed on the same live runs:

  outcome        "partial" for a run that produced nothing. There was no
                 `failed` value in the vocabulary at all, and WorkTab reads
                 only `needs_input`, so a failure and a success rendered
                 identically as "N steps · done".

  trace.failed   emitted TWICE for one provider error, the second strictly
                 less informative:
                   seq 1  "…(authentication_failed): …401 Authentication
                          Fails, Your api key: ****cked is invalid"
                   seq 2  "HTTP 401"
                 A reader showing the last one shows the worse one.
"""
from __future__ import annotations

import ast
import pathlib
import unittest

from server_modules import agent_trace_service


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


class OutcomeVocabularyTests(unittest.TestCase):
    def test_failed_is_expressible(self):
        """The whole defect in one line: there was no way to say this."""
        self.assertEqual(agent_trace_service.TRACE_OUTCOME_FAILED, "failed")

    def test_the_four_outcomes_are_distinct(self):
        values = {
            agent_trace_service.TRACE_OUTCOME_SUCCESS,
            agent_trace_service.TRACE_OUTCOME_PARTIAL,
            agent_trace_service.TRACE_OUTCOME_FAILED,
            agent_trace_service.TRACE_OUTCOME_NEEDS_INPUT,
        }
        self.assertEqual(len(values), 4)


class NoFailurePathStillSaysPartialTests(unittest.TestCase):
    """A source scan, because a behavioural test cannot reach eight call
    sites across three modules — and `outcome` is a free-text column with no
    CHECK constraint, so a literal that drifts back is silently accepted and
    reads as an unknown outcome forever.

    Carries its own canary: if the scan stops finding real finish calls it
    fails loudly rather than enforcing nothing.
    """

    _MODULES = (
        "server_modules/run_service.py",
        "server_modules/direct_chat_generation_service.py",
        "server_modules/agent_turn_runtime_service.py",
    )

    def _finish_calls(self):
        """Every finish_trace/_finish_trace/_finish_sdk_engine_trace call,
        with the outcome argument it passes, as source text."""
        found = []
        for rel in self._MODULES:
            path = REPO_ROOT / rel
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = (
                    func.attr if isinstance(func, ast.Attribute)
                    else func.id if isinstance(func, ast.Name)
                    else ""
                )
                if name not in {"finish_trace", "_finish_trace", "_finish_sdk_engine_trace"}:
                    continue
                outcome_node = None
                for kw in node.keywords:
                    if kw.arg == "outcome":
                        outcome_node = kw.value
                if outcome_node is None and node.args:
                    outcome_node = node.args[-1]
                found.append((rel, node.lineno, ast.unparse(outcome_node) if outcome_node else ""))
        return found

    def test_the_scan_reaches_real_call_sites(self):
        """Canary. Without this, a broken scan reports a clean codebase."""
        calls = self._finish_calls()
        self.assertGreaterEqual(len(calls), 6, f"scan found only {len(calls)} finish calls")

    def test_no_failure_path_hardcodes_partial(self):
        """Every remaining literal "partial" must be a DELIBERATE one.

        Exactly one survives: the SDK engine's "nothing said, nothing done"
        branch, where the turn did not go wrong — it continued on a path
        this trace cannot see. Calling that failed would report a failure
        that did not happen.
        """
        allowed = {
            (
                "server_modules/agent_turn_runtime_service.py",
                "agent_trace_service.TRACE_OUTCOME_PARTIAL",
            ),
        }
        offenders = [
            (rel, lineno, outcome)
            for rel, lineno, outcome in self._finish_calls()
            if '"partial"' in outcome or "'partial'" in outcome
        ]
        self.assertEqual(offenders, [], f"a failure path still hardcodes 'partial': {offenders}")
        # …and the one deliberate partial goes through the constant, so it
        # is greppable rather than a bare literal indistinguishable from the
        # ones that were wrong.
        constants = {
            (rel, outcome)
            for rel, _lineno, outcome in self._finish_calls()
            if "TRACE_OUTCOME_PARTIAL" in outcome
        }
        self.assertEqual(constants, allowed)

    def test_the_failure_paths_use_the_constant(self):
        outcomes = [o for _rel, _line, o in self._finish_calls()]
        self.assertGreaterEqual(
            sum(1 for o in outcomes if "TRACE_OUTCOME_FAILED" in o),
            6,
            f"expected the failure paths to say failed; saw {outcomes}",
        )


# ── The duplicate trace.failed ────────────────────────────────────────────

class DuplicateTraceFailedTests(unittest.TestCase):
    """Drives the REAL translate_sdk_message with REAL SDK dataclasses.

    Not a hand-built stand-in: the bridge matches by class NAME, and a
    fixture that invents its own shape cannot notice the real one is
    different — the failure mode this codebase documents most. Same
    approach as test_claude_agent_sdk_bridge.py's own ResultMessage tests.
    """

    def _events(self, *, saw_provider_error: bool, subtype: str = "success", status: int = 401):
        from claude_agent_sdk import types as sdk_types

        from server_modules import claude_agent_sdk_bridge

        state = claude_agent_sdk_bridge.TranslationState()
        state.saw_provider_error = saw_provider_error
        trace_context = agent_trace_service.TraceContext(
            trace_id="trace_test",
            workspace_id="ws1",
            tenant_id="t1",
            thread_id=None,
            run_id=None,
            root_agent_id="specialist:ainstall_abc",
        )
        message = sdk_types.ResultMessage(
            subtype=subtype,
            duration_ms=100,
            duration_api_ms=80,
            is_error=True,
            num_turns=1,
            session_id="sess-1",
            total_cost_usd=None,
            usage=None,
            result="",
            api_error_status=status,
        )
        events = claude_agent_sdk_bridge.translate_sdk_message(message, state=state, trace_context=trace_context)
        return [
            e for e in events
            if e.get("type") == "trace"
            and (e.get("payload") or {}).get("event_type") == "trace.failed"
        ]

    def test_a_second_generic_failure_is_suppressed(self):
        """The AssistantMessage guard already recorded this turn's provider
        error, with the provider's own account of it. "HTTP 401" adds
        nothing and displaces it wherever the last event wins."""
        self.assertEqual(self._events(saw_provider_error=True), [])

    def test_the_first_failure_is_never_suppressed(self):
        """Without a prior provider error this IS the only record — it must
        still be emitted, or a real failure goes unrecorded."""
        self.assertEqual(len(self._events(saw_provider_error=False)), 1)

    def test_a_distinct_subtype_still_records_its_own_event(self):
        """error_max_turns says something the provider error did not.
        Suppressing on "did we already fail" rather than on the CODE would
        lose it — which is why the guard needs both conditions."""
        events = self._events(saw_provider_error=True, subtype="error_max_turns")
        self.assertEqual(len(events), 1)
        self.assertEqual((events[0]["payload"]["data"] or {}).get("code"), "error_max_turns")


if __name__ == "__main__":
    unittest.main()

"""MAN-358 — the instruction that makes the link reach the person.

A `url` sitting in a tool result is not a link in a Telegram message. The
model has to pass it on, so there is one short prompt rule, and the rule has
three properties this file pins:

  1. it is SELF-DEGRADING — it says "if a result carries a url", and
     deep_link_service emits none on a deployment with no declared origin,
     so the instruction cannot make an agent invent an address;
  2. it survives _build_prompt_envelope's redaction — the same function that
     silently ate 17 of 72 tool names out of the system prompt in 2026-08-08;
  3. it is present in BOTH prompt-assembly branches. This codebase has
     repeatedly shipped a rule applied to one branch and not the other
     (_guard_sage_visible_reply's two BYO-brain branches, the credit debit
     behind the engine seam) and the failure is always silent.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from server_modules import agent_turn_runtime_service as runtime
from server_modules import secret_redaction_service


SOURCE = Path(runtime.__file__)


class DeepLinkGuidanceContentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = runtime._deep_link_guidance()

    def test_names_the_fields_the_tool_results_actually_carry(self) -> None:
        """The instruction has to name `url` and `display_id` verbatim,
        because those are the literal keys skills_service attaches — a rule
        describing "the link field" would be a rule about a field that does
        not exist."""
        self.assertIn("url", self.text)
        self.assertIn("display_id", self.text)
        self.assertIn("GEN-12", self.text)

    def test_forbids_rewriting_or_inventing_a_url(self) -> None:
        lowered = self.text.lower()
        self.assertIn("exactly as given", lowered)
        for forbidden in ("shorten", "rewrite", "guess"):
            self.assertIn(forbidden, lowered)

    def test_says_what_to_do_when_there_is_no_url(self) -> None:
        """The no-origin deployment. Without this clause the model is told to
        include something that is not there, and the honest degradation turns
        into a fabricated link — the exact failure the builder refuses to
        commit."""
        self.assertIn("carries none", self.text.lower())

    def test_stays_short(self) -> None:
        """Paid on every turn, on every surface. A rule that grows into a
        paragraph is one that crowds out the ones already there."""
        self.assertLess(len(self.text.split()), 110, "deep-link guidance has grown past its budget")

    def test_survives_the_prompt_envelope_redactor(self) -> None:
        self.assertEqual(secret_redaction_service.redact_text(self.text), self.text)

    def test_survives_redaction_inside_a_real_prompt_envelope(self) -> None:
        envelope = runtime._build_prompt_envelope(
            workspace_id="ws-1",
            message="what did you do?",
            system_prompt=f"You are an agent.{self.text}",
        )
        self.assertIn("display_id", envelope["system_prompt"])
        self.assertIn("exactly as given", envelope["system_prompt"])


class DeepLinkGuidanceWiringTests(unittest.TestCase):
    """Structural. A behavioural test can only cover the branches that exist
    today; these assertions are what catch the next one."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.source = SOURCE.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_canary_the_source_is_the_one_being_asserted_about(self) -> None:
        self.assertGreater(len(self.source), 10_000, "runtime source scan found nothing — canary")
        self.assertIn("def _deep_link_guidance", self.source)

    def test_the_guidance_is_called_exactly_once(self) -> None:
        calls = [
            node
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_deep_link_guidance"
        ]
        self.assertEqual(len(calls), 1, "the guidance should be built once and reused in both branches")

    def test_both_prompt_assembly_branches_carry_the_rule(self) -> None:
        """Found by pairing it with the rule that is already known to be in
        both branches. If _destructive_action_awareness_rule appears in an
        assembled system prompt and _deep_link_rule does not, a branch was
        missed — which is how this codebase has lost a rule before."""
        assembled = [
            line for line in self.source.splitlines()
            if "_destructive_action_awareness_rule" in line and "{" in line
        ]
        self.assertEqual(len(assembled), 2, "expected exactly two prompt-assembly sites — the shape changed")
        for line in assembled:
            self.assertIn(
                "{_deep_link_rule}", line,
                "a prompt-assembly branch applies the destructive-action rule but not the deep-link rule",
            )


if __name__ == "__main__":
    unittest.main()

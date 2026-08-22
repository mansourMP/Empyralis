"""The destructive-action guidance is judgment carried in the prompt, not a
mechanism — and it must actually reach the model, on every agent, unbroken.

THE DECISION THIS ENCODES
--------------------------
The founder rejected a hardcoded blocklist for destructive shell/file
commands: a string matcher sees `rm -rf ~/Documents` identically whether the
person means "don't touch my files" or "I have these files, I don't want
them — delete them all". What differs is intent, and intent lives in the
conversation, not in the command string. So the fix is a short paragraph of
guidance in `agent_turn_runtime_service._destructive_action_awareness_
guidance`, injected into the system prompt for BOTH the master (Sage) and
specialist prompt-assembly branches — never a gate, an approval state, or a
refusal list (CLAUDE.md's "No approval system" law, and
`skills_service.py`'s `require_approval` stays hardcoded False at every
shell/hardware call site on purpose).

WHAT THIS FILE GUARDS
----------------------
1. The guidance text itself says the things the founder asked for: the
   sandbox/full-access distinction and what it means for reversibility, the
   two calibration examples (vague+sweeping vs. clear+specific), that
   reversibility is the axis (not a list of command names), and that the
   agent should state what it's about to affect before an irreversible act.
2. It survives `secret_redaction_service.redact_text` byte-for-byte. This is
   not a hypothetical: a real 2026-08-08 bug in this exact function
   (`_build_prompt_envelope`) silently rewrote 17 of 72 tool names to
   `[redacted-secret]` inside the system prompt agents are handed — a
   high-entropy sweep that can eat plain prose too if it isn't checked.
3. It is actually wired into BOTH prompt-assembly branches (specialist and
   master/Sage) — a source-scan structural test, because a behavioural test
   can only cover the branch it happens to exercise, and this codebase has
   repeatedly shipped a guard/rule added to one branch and silently missing
   from its sibling (CLAUDE.md's "a guard called once inside a large
   function is a guard the next branch will skip").
"""

from __future__ import annotations

import inspect
import unittest

from server_modules import agent_turn_runtime_service, secret_redaction_service


class DestructiveActionGuidanceContentTests(unittest.TestCase):
    def setUp(self):
        self.text = agent_turn_runtime_service._destructive_action_awareness_guidance()

    def test_states_execution_mode_and_reversibility_stakes(self):
        lowered = self.text.lower()
        self.assertIn("sandbox", lowered)
        self.assertIn("full access", lowered)
        # The concrete, checkable signal every hardware/shell tool result
        # already carries (skills_service._format_hardware_action_result's
        # runtime_access_mode field) — not an assertion of which mode this
        # turn is actually in.
        self.assertIn("runtime_access_mode", self.text)
        self.assertIn("no undo", lowered)

    def test_states_the_two_calibration_examples(self):
        # The founder's own two examples, verbatim intent: a vague, sweeping
        # instruction is a reason to get specific; a clear, specific one is
        # a reason to act.
        self.assertIn("delete my documents", self.text.lower())
        self.assertIn("i don't want them", self.text.lower())
        self.assertIn("get specific", self.text.lower())
        self.assertIn("just do it", self.text.lower())

    def test_reversibility_is_named_as_the_axis_not_command_syntax(self):
        lowered = self.text.lower()
        self.assertIn("reversibility is what matters", lowered)
        # Must not degrade into a scary-command-name list — that is the
        # exact shape the founder rejected.
        self.assertNotIn("rm -rf", lowered)

    def test_instructs_stating_impact_before_acting(self):
        lowered = self.text.lower()
        self.assertIn("irreversibly", lowered)
        self.assertIn("say plainly", lowered)

    def test_is_brief(self):
        # CLAUDE.md: "A professional tool labels; it does not lecture."
        # Prompt-bloat is paid on every single turn, by every agent — keep
        # this to a short paragraph, not a policy essay.
        word_count = len(self.text.split())
        self.assertLess(word_count, 160, f"guidance is {word_count} words, longer than intended")

    def test_never_asserts_a_specific_execution_mode(self):
        # The mode is resolved per tool call, later, in skills_service.py —
        # not known at prompt-assembly time. Asserting "you are in a
        # sandbox" here would sometimes be a lie (CLAUDE.md's outcome-
        # honesty law), so the guidance must describe the CONCEPT and the
        # field to check, never claim this turn's actual mode.
        lowered = self.text.lower()
        self.assertNotIn("you are in a sandbox", lowered)
        self.assertNotIn("you are in full access", lowered)
        self.assertNotIn("you're in a sandbox", lowered)
        self.assertNotIn("you're in full access", lowered)


class DestructiveActionGuidanceRedactionSurvivalTests(unittest.TestCase):
    """Mirrors test_tool_name_secret_redaction.py's own concern: this exact
    function (_build_prompt_envelope) once silently ate 17 of 72 tool names.
    A prompt paragraph partially eaten by the same high-entropy sweep is
    worse than no paragraph at all — it reads as complete and isn't."""

    def test_guidance_survives_redact_text_unchanged(self):
        text = agent_turn_runtime_service._destructive_action_awareness_guidance()
        self.assertEqual(secret_redaction_service.redact_text(text), text)

    def test_guidance_survives_redaction_inside_a_full_prompt_envelope(self):
        # Redaction runs over the WHOLE assembled system prompt, not the
        # guidance block in isolation — concatenation could in principle
        # create a boundary the isolated-string test wouldn't see. Build a
        # representative envelope the way both real branches do (surrounding
        # prose immediately before/after, no blank buffer) and confirm nothing
        # is dropped from the guidance block once redacted in that context.
        guidance = agent_turn_runtime_service._destructive_action_awareness_guidance()
        surrounding_prompt = (
            "You are a specialist agent with real tool access."
            + guidance
            + "\n\n## Your memory\nNo memory recorded yet."
        )
        envelope = agent_turn_runtime_service._build_prompt_envelope(
            workspace_id="ws-1",
            message="hello",
            system_prompt=surrounding_prompt,
        )
        self.assertIn(guidance, envelope["system_prompt"])


class DestructiveActionGuidanceWiringTests(unittest.TestCase):
    """Structural: both prompt-assembly branches must reference the
    guidance. A behavioural test only ever exercises the branch it happens
    to construct (specialist vs. master), which is exactly the shape that
    has silently dropped a rule from one branch before in this file."""

    def test_module_defines_the_guidance_function(self):
        self.assertTrue(hasattr(agent_turn_runtime_service, "_destructive_action_awareness_guidance"))
        self.assertTrue(callable(agent_turn_runtime_service._destructive_action_awareness_guidance))

    def test_both_prompt_assembly_branches_reference_the_guidance(self):
        # The prompt-assembly code (both branches) lives in
        # _handle_sage_chat_unguarded, NOT _run_sage_action_loop_v3 — that
        # is the action-loop/tool-execution engine _handle_sage_chat_
        # unguarded calls afterward with the already-built system prompt.
        source = inspect.getsource(agent_turn_runtime_service._handle_sage_chat_unguarded)
        # The specialist branch's f-string.
        self.assertIn(
            "_specialist_system_prompt = f\"{_spec_persona}",
            source,
            "specialist prompt assembly line moved — update this test's anchor",
        )
        specialist_line = next(
            line for line in source.splitlines() if "_specialist_system_prompt = f\"{_spec_persona}" in line
        )
        self.assertIn("_destructive_action_awareness_rule", specialist_line)

        # The master/Sage (else) branch's f-string.
        master_line = next(
            line for line in source.splitlines()
            if "system_prompt=f\"{instruction_bundle.system_prompt.rstrip()}" in line
        )
        self.assertIn("_destructive_action_awareness_rule", master_line)

    def test_rule_variable_is_assigned_from_the_module_function(self):
        source = inspect.getsource(agent_turn_runtime_service._handle_sage_chat_unguarded)
        self.assertIn(
            "_destructive_action_awareness_rule = _destructive_action_awareness_guidance()",
            source,
        )


if __name__ == "__main__":
    unittest.main()

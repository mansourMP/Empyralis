"""Tests for server_modules.sage_transparency_service."""

from __future__ import annotations

import unittest

from server_modules.sage_transparency_service import (
    emit_sage_turn_transparency_events,
    events_to_payloads,
)
from server_modules.agent_transparency_events import AgentTransparencyEvent


class SageTransparencyEmissionTests(unittest.TestCase):
    TRACE_ID = "sage-trace-001"
    WORKSPACE = "ws-sage"

    @staticmethod
    def _result(**kw):
        base = {"message": "Here is your answer.", "trace_id": "sage-trace-001"}
        base.update(kw)
        return base

    # ── happy path stages ──────────────────────────────────────────

    def test_user_message_received(self):
        events = emit_sage_turn_transparency_events(
            trace_id=self.TRACE_ID,
            workspace_id=self.WORKSPACE,
            user_message="What PRs are open?",
            sage_result=self._result(),
        )
        types = [e.event_type for e in events]
        self.assertIn("user_message_received", types)

    def test_memory_loaded_when_context_present(self):
        events = emit_sage_turn_transparency_events(
            trace_id=self.TRACE_ID,
            workspace_id=self.WORKSPACE,
            user_message="hi",
            sage_result=self._result(
                used_context=[{"name": "USER.md"}, {"name": "IDENTITY.md"}],
            ),
        )
        types = [e.event_type for e in events]
        self.assertIn("memory_loaded", types)

    def test_final_response_sent(self):
        events = emit_sage_turn_transparency_events(
            trace_id=self.TRACE_ID,
            workspace_id=self.WORKSPACE,
            user_message="hi",
            sage_result=self._result(message="Hello!"),
        )
        types = [e.event_type for e in events]
        self.assertIn("final_response_sent", types)

    def test_tool_calls_emit_events(self):
        events = emit_sage_turn_transparency_events(
            trace_id=self.TRACE_ID,
            workspace_id=self.WORKSPACE,
            user_message="open browser",
            sage_result=self._result(
                tool_calls=[
                    {"name": "browser.session.start", "status": "completed"},
                    {"name": "computer.screenshot", "status": "failed", "error": "denied"},
                ],
            ),
        )
        types = [e.event_type for e in events]
        self.assertIn("tool_started", types)
        self.assertIn("tool_completed", types)
        self.assertIn("tool_failed", types)

    def test_completed_tool_emits_started_before_completed(self):
        events = emit_sage_turn_transparency_events(
            trace_id=self.TRACE_ID,
            workspace_id=self.WORKSPACE,
            user_message="search the web",
            sage_result=self._result(
                tool_calls=[{"name": "web__search", "status": "completed", "iteration": 1, "action_loop_version": "v2"}],
            ),
        )
        types = [e.event_type for e in events]
        self.assertLess(types.index("tool_started"), types.index("tool_completed"))
        started = next(e for e in events if e.event_type == "tool_started")
        self.assertEqual(started.tool_name, "web__search")
        self.assertEqual(started.metadata["action_loop_version"], "v2")

    def test_running_tool_emits_single_started_event(self):
        events = emit_sage_turn_transparency_events(
            trace_id=self.TRACE_ID,
            workspace_id=self.WORKSPACE,
            user_message="open browser",
            sage_result=self._result(
                tool_calls=[{"name": "browser__open", "status": "running"}],
            ),
        )
        types = [e.event_type for e in events]
        self.assertEqual(types.count("tool_started"), 1)
        self.assertNotIn("tool_completed", types)
        self.assertNotIn("tool_failed", types)

    def test_approval_events_emitted(self):
        events = emit_sage_turn_transparency_events(
            trace_id=self.TRACE_ID,
            workspace_id=self.WORKSPACE,
            user_message="run risky command",
            sage_result=self._result(
                approvals_required=[{"action": "shell.exec", "capability_id": "shell.exec"}],
            ),
        )
        types = [e.event_type for e in events]
        self.assertIn("approval_required", types)

    def test_blocked_tools_emit_policy_blocked(self):
        events = emit_sage_turn_transparency_events(
            trace_id=self.TRACE_ID,
            workspace_id=self.WORKSPACE,
            user_message="use blocked tool",
            sage_result=self._result(
                blocked_tools=["filesystem.write"],
            ),
        )
        types = [e.event_type for e in events]
        self.assertIn("policy_blocked", types)

    def test_error_emits_tool_failed(self):
        events = emit_sage_turn_transparency_events(
            trace_id=self.TRACE_ID,
            workspace_id=self.WORKSPACE,
            user_message="crash me",
            sage_result=self._result(error="LLM provider timeout"),
        )
        types = [e.event_type for e in events]
        self.assertIn("tool_failed", types)

    # ── all events share same trace_id ─────────────────────────────

    def test_all_events_share_trace_id(self):
        events = emit_sage_turn_transparency_events(
            trace_id=self.TRACE_ID,
            workspace_id=self.WORKSPACE,
            user_message="hi",
            sage_result=self._result(
                used_context=[{"name": "USER.md"}],
                tool_calls=[{"name": "search", "status": "completed"}],
            ),
        )
        self.assertTrue(len(events) >= 3)
        for evt in events:
            self.assertEqual(evt.trace_id, self.TRACE_ID)

    # ── empty result emits only what is real ───────────────────────

    def test_empty_result_emits_no_fake_events(self):
        events = emit_sage_turn_transparency_events(
            trace_id=self.TRACE_ID,
            workspace_id=self.WORKSPACE,
            user_message="",
            sage_result={},
        )
        # No user message, no reply — only a possible error or nothing
        types = [e.event_type for e in events]
        self.assertNotIn("memory_loaded", types)
        self.assertNotIn("tool_completed", types)
        self.assertNotIn("final_response_sent", types)

    def test_no_user_message_emits_no_received_event(self):
        events = emit_sage_turn_transparency_events(
            trace_id=self.TRACE_ID,
            workspace_id=self.WORKSPACE,
            user_message="",
            sage_result=self._result(message="reply"),
        )
        types = [e.event_type for e in events]
        self.assertNotIn("user_message_received", types)
        self.assertIn("final_response_sent", types)

    # ── metadata redaction ────────────────────────────────────────

    def test_events_metadata_is_redacted(self):
        events = emit_sage_turn_transparency_events(
            trace_id=self.TRACE_ID,
            workspace_id=self.WORKSPACE,
            user_message="hi",
            sage_result=self._result(
                used_context=[{"name": "SECRET.md", "api_key": "sk-leak"}],
            ),
        )
        for evt in events:
            for v in evt.metadata.values():
                if isinstance(v, str):
                    self.assertNotEqual(v, "sk-leak")

    # ── CoT protection ────────────────────────────────────────────

    def test_raw_chain_of_thought_not_present(self):
        events = emit_sage_turn_transparency_events(
            trace_id=self.TRACE_ID,
            workspace_id=self.WORKSPACE,
            user_message="hi",
            sage_result=self._result(),
        )
        for evt in events:
            self.assertNotIn("raw_chain_of_thought", evt.metadata)
            self.assertNotIn("raw_cot", evt.metadata)
            self.assertNotIn("model_internals", evt.metadata)

    # ── customer payload is minimal ────────────────────────────────

    def test_customer_payload_is_minimal(self):
        events = emit_sage_turn_transparency_events(
            trace_id=self.TRACE_ID,
            workspace_id=self.WORKSPACE,
            user_message="hi",
            sage_result=self._result(
                used_context=[{"name": "private_memory", "source": "local"}],
                tool_calls=[{"name": "screenshot", "status": "completed"}],
                approvals_required=[{"action": "shell.exec"}],
            ),
            audience="owner",
        )
        self.assertTrue(len(events) > 0)
        customer_payloads = events_to_payloads(events, audience="customer")
        self.assertEqual(len(customer_payloads), len(events))
        for p in customer_payloads:
            self.assertNotIn("summary", p)
            self.assertNotIn("metadata", p)
            self.assertNotIn("tool_name", p)
            self.assertNotIn("memory_scope", p)

    def test_owner_payload_has_details(self):
        events = emit_sage_turn_transparency_events(
            trace_id=self.TRACE_ID,
            workspace_id=self.WORKSPACE,
            user_message="hi",
            sage_result=self._result(
                tool_calls=[{"name": "search", "status": "completed"}],
            ),
            audience="owner",
            visibility_level="standard",
        )
        payloads = events_to_payloads(events, audience="owner")
        tool_payload = [p for p in payloads if p.get("tool_name")]
        self.assertTrue(len(tool_payload) > 0)

    # ── existing behavior unchanged ───────────────────────────────

    def test_emission_does_not_mutate_sage_result(self):
        result = self._result(
            used_context=[{"name": "USER.md"}],
            tool_calls=[{"name": "search", "status": "completed"}],
        )
        result_before = {k: v for k, v in result.items()}
        emit_sage_turn_transparency_events(
            trace_id=self.TRACE_ID,
            workspace_id=self.WORKSPACE,
            user_message="hi",
            sage_result=result,
        )
        self.assertEqual(result, result_before)

    def test_emission_returns_list_of_events(self):
        events = emit_sage_turn_transparency_events(
            trace_id=self.TRACE_ID,
            workspace_id=self.WORKSPACE,
            user_message="hi",
            sage_result=self._result(),
        )
        self.assertIsInstance(events, list)
        for evt in events:
            self.assertIsInstance(evt, AgentTransparencyEvent)


if __name__ == "__main__":
    unittest.main()

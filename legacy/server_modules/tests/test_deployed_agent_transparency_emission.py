"""Tests for server_modules.deployed_agent_transparency_service."""

from __future__ import annotations

import unittest

from server_modules.deployed_agent_transparency_service import (
    emit_deployed_agent_test_turn_events,
)
from server_modules.agent_transparency_events import AgentTransparencyEvent


class DeployedAgentTransparencyEmissionTests(unittest.TestCase):
    TRACE = "da-trace-001"
    WS = "da-ws"
    AGENT_ID = "da-agent-1"

    def _result(self, **kw):
        base = {
            "reply": "Here is the test response.",
            "trace_id": self.TRACE,
            "policy_decisions": [],
            "tools_considered": [],
            "tools_used": [],
            "memory_context": {},
            "approval_required": False,
            "audit_events": [],
        }
        base.update(kw)
        return base

    def _emit(self, message="test message", **result_kw):
        return emit_deployed_agent_test_turn_events(
            trace_id=self.TRACE,
            workspace_id=self.WS,
            deployed_agent_id=self.AGENT_ID,
            user_message=message,
            test_result=self._result(**result_kw),
        )

    # ── basic event emission ──────────────────────────────────────

    def test_user_message_received(self):
        events = self._emit(message="hello")
        types = [e.event_type for e in events]
        self.assertIn("user_message_received", types)

    def test_memory_loaded(self):
        events = self._emit(memory_context={"applied": True, "owner_memory_included": False})
        types = [e.event_type for e in events]
        self.assertIn("memory_loaded", types)

    def test_memory_excluded(self):
        events = self._emit(memory_context={"applied": False, "some_meta": True})
        types = [e.event_type for e in events]
        self.assertIn("memory_excluded", types)

    def test_policy_blocked(self):
        events = self._emit(policy_decisions=[
            {"policy": "runtime_mode", "allowed": False},
            {"policy": "channel", "allowed": True},
        ])
        types = [e.event_type for e in events]
        self.assertIn("policy_blocked", types)

    def test_tools_selected(self):
        events = self._emit(tools_considered=[
            {"skill_id": "search", "allowed": True},
            {"skill_id": "shell.exec", "allowed": False},
        ])
        types = [e.event_type for e in events]
        self.assertIn("tool_selected", types)

    def test_tools_used(self):
        events = self._emit(tools_used=["search"])
        types = [e.event_type for e in events]
        self.assertIn("tool_completed", types)

    def test_approval_required(self):
        events = self._emit(approval_required=True)
        types = [e.event_type for e in events]
        self.assertIn("approval_required", types)

    def test_final_response_sent(self):
        events = self._emit()
        types = [e.event_type for e in events]
        self.assertIn("final_response_sent", types)

    # ── trace_id consistency ──────────────────────────────────────

    def test_all_events_share_trace_id(self):
        events = self._emit(
            memory_context={"applied": True},
            policy_decisions=[{"policy": "channel", "allowed": False}],
            tools_considered=[{"skill_id": "search", "allowed": True}],
            tools_used=["search"],
            approval_required=True,
        )
        self.assertTrue(len(events) >= 3)
        for e in events:
            self.assertEqual(e.trace_id, self.TRACE)

    # ── no fake events ────────────────────────────────────────────

    def test_no_memory_event_when_empty(self):
        events = self._emit(memory_context={})
        types = [e.event_type for e in events]
        self.assertNotIn("memory_loaded", types)
        self.assertNotIn("memory_excluded", types)

    def test_no_tool_event_when_empty(self):
        events = self._emit(tools_considered=[], tools_used=[])
        types = [e.event_type for e in events]
        self.assertNotIn("tool_selected", types)
        self.assertNotIn("tool_completed", types)

    def test_no_approval_event_when_false(self):
        events = self._emit(approval_required=False)
        types = [e.event_type for e in events]
        self.assertNotIn("approval_required", types)

    # ── customer safety ───────────────────────────────────────────

    def test_customer_payload_is_minimal(self):
        events = self._emit(
            memory_context={"applied": True},
            tools_used=["search"],
        )
        for e in events:
            payload = e.to_customer_payload()
            self.assertNotIn("summary", payload)
            self.assertNotIn("tool_name", payload)

    # ── metadata redacted ─────────────────────────────────────────

    def test_metadata_redacted(self):
        # Tools with secrets in their names wouldn't happen, but metadata
        # from any source is always redacted by AgentTransparencyEvent
        events = self._emit()
        for e in events:
            self.assertNotIn("raw_chain_of_thought", e.metadata)

    # ── type correctness ──────────────────────────────────────────

    def test_all_events_are_correct_type(self):
        events = self._emit(
            memory_context={"applied": True},
            tools_used=["search"],
        )
        self.assertTrue(len(events) > 0)
        for e in events:
            self.assertIsInstance(e, AgentTransparencyEvent)

    def test_studio_agent_surface(self):
        events = self._emit()
        for e in events:
            self.assertEqual(e.surface, "studio_test")
            self.assertEqual(e.actor_type, "studio_agent")


if __name__ == "__main__":
    unittest.main()

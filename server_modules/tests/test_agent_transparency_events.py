"""Tests for server_modules.agent_transparency_events."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from server_modules.agent_transparency_events import AgentTransparencyEvent


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AgentTransparencyEventModelTests(unittest.TestCase):
    @staticmethod
    def _base(**overrides):
        kwargs = {
            "event_id": "evt-1",
            "trace_id": "trace-1",
            "workspace_id": "ws-1",
            "actor_type": "sage",
            "surface": "chat",
            "audience": "owner",
            "event_type": "tool_started",
            "title": "Browser opened",
            "summary": "Opened browser to view PR #42",
            "status": "completed",
            "timestamp": _now(),
        }
        kwargs.update(overrides)
        return kwargs

    def test_required_fields(self):
        evt = AgentTransparencyEvent(**self._base())
        self.assertEqual(evt.event_id, "evt-1")
        self.assertEqual(evt.trace_id, "trace-1")
        self.assertEqual(evt.workspace_id, "ws-1")

    def test_metadata_is_redacted(self):
        evt = AgentTransparencyEvent(
            **self._base(), metadata={"api_key": "sk-secret", "user": "bob"},
        )
        self.assertNotEqual(evt.metadata.get("api_key"), "sk-secret")
        self.assertEqual(evt.metadata.get("user"), "bob")

    def test_raw_chain_of_thought_is_stripped(self):
        evt = AgentTransparencyEvent(
            **self._base(),
            metadata={"raw_chain_of_thought": "hidden", "raw_cot": "hidden", "ok": "yes"},
        )
        self.assertNotIn("raw_chain_of_thought", evt.metadata)
        self.assertNotIn("raw_cot", evt.metadata)
        self.assertIn("ok", evt.metadata)

    def test_user_payload_shows_status_and_title(self):
        evt = AgentTransparencyEvent(**self._base(event_type="final_response_sent"))
        payload = evt.to_user_payload()
        self.assertIn("title", payload)
        self.assertEqual(payload["status"], "completed")

    def test_user_payload_shows_tool_and_channel_names(self):
        evt = AgentTransparencyEvent(
            **self._base(),
            tool_name="browser.session.start",
            channel="web_chat",
        )
        payload = evt.to_user_payload()
        self.assertEqual(payload["tool_name"], "browser.session.start")
        self.assertEqual(payload["channel"], "web_chat")

    def test_customer_audience_cannot_see_internals(self):
        evt = AgentTransparencyEvent(
            **self._base(audience="customer"),
            memory_scope="private_memory",
            metadata={"policy": "blocked"},
        )
        customer_payload = evt.to_customer_payload()
        self.assertNotIn("memory_scope", customer_payload)
        self.assertNotIn("metadata", customer_payload)
        self.assertNotIn("summary", customer_payload)

    def test_event_type_values(self):
        valid_types = [
            "user_message_received", "memory_loaded", "memory_excluded",
            "planning_started", "tool_selected", "tool_started",
            "tool_completed", "tool_failed", "approval_required",
            "approval_approved", "approval_denied", "gateway_action_started",
            "gateway_action_completed", "channel_message_sent",
            "channel_message_received", "policy_blocked", "turn_failed",
            "quota_blocked", "unsafe_url_blocked",
            "final_response_started", "final_response_sent",
        ]
        for t in valid_types:
            evt = AgentTransparencyEvent(**self._base(event_type=t))
            self.assertEqual(evt.event_type, t)


class AgentTransparencyEventCustomerPayloadTests(unittest.TestCase):
    @staticmethod
    def _base(**kw):
        kwargs = {
            "event_id": "evt-v",
            "trace_id": "trace-v",
            "workspace_id": "ws-v",
            "actor_type": "sage",
            "surface": "chat",
            "audience": "owner",
            "event_type": "tool_completed",
            "title": "Done",
            "summary": "Done",
            "status": "completed",
            "timestamp": _now(),
        }
        kwargs.update(kw)
        return kwargs

    def test_running_status_shows_agent_is_working_in_customer_view(self):
        evt = AgentTransparencyEvent(
            **self._base(audience="customer", status="running")
        )
        customer_payload = evt.to_customer_payload()
        self.assertEqual(customer_payload["title"], "Agent is working…")


if __name__ == "__main__":
    unittest.main()

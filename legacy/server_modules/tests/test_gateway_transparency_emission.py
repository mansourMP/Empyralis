"""Tests for server_modules.gateway_transparency_service."""

from __future__ import annotations

import unittest

from server_modules.gateway_transparency_service import (
    emit_gateway_action_event,
    emit_approval_event,
    emit_safety_block_event,
    emit_channel_event,
)
from server_modules.agent_transparency_events import AgentTransparencyEvent


class GatewayTransparencyEmissionTests(unittest.TestCase):
    TRACE = "gtevt-trace-001"
    WS = "ws-gateway"
    GW = "gw-test"

    # ── gateway action events ─────────────────────────────────────

    def test_gateway_action_started(self):
        evt = emit_gateway_action_event(
            event_type="gateway_action_started",
            title="Gateway action started",
            summary="browser.session.start executing",
            status="running",
            trace_id=self.TRACE,
            workspace_id=self.WS,
            gateway_id=self.GW,
            capability_id="browser.session.start",
        )
        self.assertEqual(evt.event_type, "gateway_action_started")
        self.assertEqual(evt.tool_name, "browser.session.start")
        self.assertEqual(evt.trace_id, self.TRACE)

    def test_gateway_action_completed(self):
        evt = emit_gateway_action_event(
            event_type="gateway_action_completed",
            title="Gateway action completed",
            summary="browser.session.start finished",
            status="completed",
            trace_id=self.TRACE,
            workspace_id=self.WS,
            gateway_id=self.GW,
            capability_id="browser.session.start",
        )
        self.assertEqual(evt.event_type, "gateway_action_completed")
        self.assertEqual(evt.status, "completed")

    def test_tool_failed(self):
        evt = emit_gateway_action_event(
            event_type="tool_failed",
            title="Tool failed",
            summary="browser.session.start timed out",
            status="failed",
            trace_id=self.TRACE,
            workspace_id=self.WS,
            gateway_id=self.GW,
            capability_id="browser.session.start",
        )
        self.assertEqual(evt.event_type, "tool_failed")
        self.assertEqual(evt.status, "failed")

    # ── approval events ──────────────────────────────────────────

    def test_approval_required(self):
        evt = emit_approval_event(
            event_type="approval_required",
            title="Approval required",
            summary="Action requires your approval",
            status="running",
            trace_id=self.TRACE,
            workspace_id=self.WS,
            gateway_id=self.GW,
            approval_id="appr-1",
        )
        self.assertEqual(evt.event_type, "approval_required")
        self.assertEqual(evt.approval_id, "appr-1")

    def test_approval_approved(self):
        evt = emit_approval_event(
            event_type="approval_approved",
            title="Approval granted",
            summary="Action was approved",
            status="completed",
            trace_id=self.TRACE,
            workspace_id=self.WS,
            gateway_id=self.GW,
            approval_id="appr-1",
        )
        self.assertEqual(evt.event_type, "approval_approved")

    def test_approval_denied(self):
        evt = emit_approval_event(
            event_type="approval_denied",
            title="Approval denied",
            summary="Action was denied",
            status="denied",
            trace_id=self.TRACE,
            workspace_id=self.WS,
            gateway_id=self.GW,
            approval_id="appr-1",
        )
        self.assertEqual(evt.event_type, "approval_denied")
        self.assertEqual(evt.status, "denied")

    # ── safety block events ──────────────────────────────────────

    def test_quota_blocked(self):
        evt = emit_safety_block_event(
            event_type="quota_blocked",
            title="Quota exceeded",
            summary="Channel outbound quota reached",
            trace_id=self.TRACE,
            workspace_id=self.WS,
            gateway_id=self.GW,
        )
        self.assertEqual(evt.event_type, "quota_blocked")
        self.assertEqual(evt.status, "blocked")

    def test_policy_blocked(self):
        evt = emit_safety_block_event(
            event_type="policy_blocked",
            title="Blocked by kill switch",
            summary="Gateway kill switch is active",
            trace_id=self.TRACE,
            workspace_id=self.WS,
            gateway_id=self.GW,
        )
        self.assertEqual(evt.event_type, "policy_blocked")
        self.assertEqual(evt.status, "blocked")

    def test_unsafe_url_blocked(self):
        evt = emit_safety_block_event(
            event_type="unsafe_url_blocked",
            title="Unsafe URL blocked",
            summary="URL blocked: http://192.168.1.1",
            trace_id=self.TRACE,
            workspace_id=self.WS,
            gateway_id=self.GW,
        )
        self.assertEqual(evt.event_type, "unsafe_url_blocked")
        self.assertEqual(evt.status, "blocked")

    # ── channel events ───────────────────────────────────────────

    def test_channel_message_sent(self):
        evt = emit_channel_event(
            event_type="channel_message_sent",
            title="Message sent",
            summary="Message dispatched on whatsapp_personal",
            status="completed",
            trace_id=self.TRACE,
            workspace_id=self.WS,
            gateway_id=self.GW,
            channel="whatsapp_personal",
        )
        self.assertEqual(evt.event_type, "channel_message_sent")
        self.assertEqual(evt.channel, "whatsapp_personal")

    def test_channel_message_received(self):
        evt = emit_channel_event(
            event_type="channel_message_received",
            title="Message received",
            summary="Message received on telegram_personal",
            status="completed",
            trace_id=self.TRACE,
            workspace_id=self.WS,
            gateway_id=self.GW,
            channel="telegram_personal",
        )
        self.assertEqual(evt.event_type, "channel_message_received")
        self.assertEqual(evt.channel, "telegram_personal")

    # ── all events share trace_id ─────────────────────────────────

    def test_all_events_share_trace_id(self):
        events = [
            emit_gateway_action_event(
                event_type="gateway_action_started",
                title="t", summary="s", status="running",
                trace_id=self.TRACE, workspace_id=self.WS,
            ),
            emit_approval_event(
                event_type="approval_required",
                title="t", summary="s", status="running",
                trace_id=self.TRACE, workspace_id=self.WS,
            ),
            emit_safety_block_event(
                event_type="quota_blocked",
                title="t", summary="s",
                trace_id=self.TRACE, workspace_id=self.WS,
            ),
            emit_channel_event(
                event_type="channel_message_sent",
                title="t", summary="s", status="completed",
                trace_id=self.TRACE, workspace_id=self.WS,
            ),
        ]
        for evt in events:
            self.assertEqual(evt.trace_id, self.TRACE)

    # ── customer payload is minimal ──────────────────────────────

    def test_customer_payload_is_minimal(self):
        evt = emit_gateway_action_event(
            event_type="gateway_action_started",
            title="Gateway action started",
            summary="browser.session.start executing",
            status="running",
            trace_id=self.TRACE,
            workspace_id=self.WS,
            gateway_id=self.GW,
            capability_id="browser.session.start",
        )
        payload = evt.to_customer_payload()
        self.assertNotIn("summary", payload)
        self.assertNotIn("tool_name", payload)
        self.assertNotIn("workspace_id", payload)

    # ── metadata redacted ────────────────────────────────────────

    def test_metadata_is_redacted(self):
        evt = emit_gateway_action_event(
            event_type="gateway_action_started",
            title="t", summary="s", status="running",
            trace_id=self.TRACE, workspace_id=self.WS,
            metadata={"api_key": "sk-secret", "url": "https://example.com"},
        )
        self.assertNotEqual(evt.metadata.get("api_key"), "sk-secret")
        self.assertEqual(evt.metadata.get("url"), "https://example.com")

    # ── raw CoT not present ──────────────────────────────────────

    def test_no_raw_cot(self):
        evt = emit_gateway_action_event(
            event_type="gateway_action_started",
            title="t", summary="s", status="running",
            trace_id=self.TRACE, workspace_id=self.WS,
            metadata={"raw_chain_of_thought": "hidden"},
        )
        self.assertNotIn("raw_chain_of_thought", evt.metadata)

    # ── events are AgentTransparencyEvent instances ───────────────

    def test_events_are_correct_type(self):
        evt = emit_gateway_action_event(
            event_type="gateway_action_completed",
            title="t", summary="s", status="completed",
            trace_id=self.TRACE, workspace_id=self.WS,
        )
        self.assertIsInstance(evt, AgentTransparencyEvent)

    def test_approval_required_is_visible_to_owner(self):
        evt = emit_approval_event(
            event_type="approval_required",
            title="Approval required", summary="s",
            status="running",
            trace_id=self.TRACE, workspace_id=self.WS,
            audience="owner",
        )
        self.assertTrue(evt.is_visible_to("owner"))

    def test_gateway_events_have_gateway_surface(self):
        evt = emit_gateway_action_event(
            event_type="gateway_action_started",
            title="t", summary="s", status="running",
            trace_id=self.TRACE, workspace_id=self.WS,
        )
        self.assertEqual(evt.surface, "gateway")


if __name__ == "__main__":
    unittest.main()

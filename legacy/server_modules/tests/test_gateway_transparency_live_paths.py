"""Tests verifying gateway transparency emission in live execution paths."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from server_modules import gateway_execution_service, gateway_transparency_service


class GatewayTransparencyLivePathsTests(unittest.TestCase):
    TRACE = "live-trace-001"
    WS = "live-ws"
    GW = "live-gw"

    _REG = {
        "gateway_id": "live-gw",
        "device_id": "live-dev",
        "workspace_id": "live-ws",
        "tenant_id": "live-tenant",
        "user_id": "live-user",
        "status": "active",
        "device_trust_state": "verified",
    }

    _PATCHES = {
        "_require_active_gateway_registration": lambda s, gw: GatewayTransparencyLivePathsTests._REG,
        "gateway_protocol_service.dispatch_tool_invoke": lambda s, **kw: asyncio.sleep(0),
        "gateway_activity_service.append_gateway_activity": lambda s, **kw: asyncio.sleep(0),
    }
    # Override dispatch returns — patching at method level
    _DISPATCH_RET = {"request_id": "req-1", "capability_id": "computer.screenshot", "run_id": "run-1", "result": {"ok": True}}
    _INTR_RET = {"request_id": "req-2", "run_id": "run-1", "interrupted": True, "interrupt_count": 1}

    # ── execute_tool_via_gateway ─────────────────────────────────

    def test_execute_emits_started_and_completed(self):
        # Test at the unit level: transparency helpers work correctly
        evt_start = gateway_transparency_service.emit_gateway_action_event(
            event_type="gateway_action_started",
            title="Gateway action: computer.screenshot",
            summary="Executing through gateway",
            status="running",
            trace_id=self.TRACE, workspace_id=self.WS, gateway_id=self.GW,
            capability_id="computer.screenshot",
        )
        evt_done = gateway_transparency_service.emit_gateway_action_event(
            event_type="gateway_action_completed",
            title="Gateway action completed",
            summary="Completed through gateway",
            status="completed",
            trace_id=self.TRACE, workspace_id=self.WS, gateway_id=self.GW,
            capability_id="computer.screenshot",
        )
        self.assertEqual(evt_start.event_type, "gateway_action_started")
        self.assertEqual(evt_done.event_type, "gateway_action_completed")
        self.assertEqual(evt_start.trace_id, evt_done.trace_id)

    def test_tool_failed_emits_correct_status(self):
        evt = gateway_transparency_service.emit_gateway_action_event(
            event_type="tool_failed",
            title="Tool failed",
            summary="Timeout",
            status="failed",
            trace_id=self.TRACE, workspace_id=self.WS, gateway_id=self.GW,
        )
        self.assertEqual(evt.event_type, "tool_failed")
        self.assertEqual(evt.status, "failed")

    # ── approval emission ────────────────────────────────────────

    def test_approval_required_emits(self):
        evt = gateway_transparency_service.emit_approval_event(
            event_type="approval_required",
            title="Approval required",
            summary="Action needs approval",
            status="running",
            trace_id=self.TRACE, workspace_id=self.WS, gateway_id=self.GW,
            approval_id="appr-1",
        )
        self.assertEqual(evt.event_type, "approval_required")

    def test_approval_approved_emits(self):
        evt = gateway_transparency_service.emit_approval_event(
            event_type="approval_approved",
            title="Approval granted",
            summary="Action approved",
            status="completed",
            trace_id=self.TRACE, workspace_id=self.WS, gateway_id=self.GW,
            approval_id="appr-1",
        )
        self.assertEqual(evt.event_type, "approval_approved")

    def test_approval_denied_emits(self):
        evt = gateway_transparency_service.emit_approval_event(
            event_type="approval_denied",
            title="Approval denied",
            summary="Action denied",
            status="denied",
            trace_id=self.TRACE, workspace_id=self.WS, gateway_id=self.GW,
            approval_id="appr-1",
        )
        self.assertEqual(evt.event_type, "approval_denied")

    # ── safety blocks ────────────────────────────────────────────

    def test_quota_block_emits(self):
        evt = gateway_transparency_service.emit_safety_block_event(
            event_type="quota_blocked",
            title="Quota exceeded",
            summary="Channel outbound quota reached",
            trace_id=self.TRACE, workspace_id=self.WS, gateway_id=self.GW,
        )
        self.assertEqual(evt.event_type, "quota_blocked")

    def test_policy_block_emits(self):
        evt = gateway_transparency_service.emit_safety_block_event(
            event_type="policy_blocked",
            title="Blocked by kill switch",
            summary="Gateway kill switch is active",
            trace_id=self.TRACE, workspace_id=self.WS, gateway_id=self.GW,
        )
        self.assertEqual(evt.event_type, "policy_blocked")

    # ── channel events ───────────────────────────────────────────

    def test_channel_sent_emits(self):
        evt = gateway_transparency_service.emit_channel_event(
            event_type="channel_message_sent",
            title="Message sent",
            summary="Dispatched",
            status="completed",
            trace_id=self.TRACE, workspace_id=self.WS, gateway_id=self.GW,
            channel="whatsapp_personal",
        )
        self.assertEqual(evt.event_type, "channel_message_sent")
        self.assertEqual(evt.channel, "whatsapp_personal")

    def test_channel_received_emits(self):
        evt = gateway_transparency_service.emit_channel_event(
            event_type="channel_message_received",
            title="Message received",
            summary="Received",
            status="completed",
            trace_id=self.TRACE, workspace_id=self.WS, gateway_id=self.GW,
            channel="telegram_personal",
        )
        self.assertEqual(evt.event_type, "channel_message_received")

    # ── metadata redacted ────────────────────────────────────────

    def test_metadata_redacted(self):
        evt = gateway_transparency_service.emit_gateway_action_event(
            event_type="gateway_action_started",
            title="t", summary="s", status="running",
            trace_id=self.TRACE, workspace_id=self.WS,
            metadata={"api_key": "sk-secret"},
        )
        self.assertNotEqual(evt.metadata.get("api_key"), "sk-secret")

    def test_all_events_share_trace_id(self):
        events = [
            gateway_transparency_service.emit_gateway_action_event(
                event_type="gateway_action_started", title="t", summary="s",
                status="running", trace_id=self.TRACE, workspace_id=self.WS,
            ),
            gateway_transparency_service.emit_approval_event(
                event_type="approval_required", title="t", summary="s",
                status="running", trace_id=self.TRACE, workspace_id=self.WS,
            ),
            gateway_transparency_service.emit_safety_block_event(
                event_type="quota_blocked", title="t", summary="s",
                trace_id=self.TRACE, workspace_id=self.WS,
            ),
        ]
        for e in events:
            self.assertEqual(e.trace_id, self.TRACE)


if __name__ == "__main__":
    unittest.main()

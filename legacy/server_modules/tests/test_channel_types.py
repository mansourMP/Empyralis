"""Tests for server_modules.channel_types."""

from __future__ import annotations

import unittest

from server_modules.channel_types import (
    CanonicalChannelEvent,
    ChannelSafetyContext,
    ChannelHealthPayload,
    ChannelCapabilityContract,
)


class ChannelSafetyContextTests(unittest.TestCase):
    def test_default_values(self):
        ctx = ChannelSafetyContext()
        self.assertTrue(ctx.kill_switch_checked)
        self.assertTrue(ctx.quota_checked)
        self.assertEqual(ctx.approval_status, "none")
        self.assertFalse(ctx.control_command_blocked)
        self.assertTrue(ctx.content_guard_passed)

    def test_custom_values(self):
        ctx = ChannelSafetyContext(
            kill_switch_checked=False,
            approval_status="approved",
            trace_id="trace-1",
        )
        self.assertFalse(ctx.kill_switch_checked)
        self.assertEqual(ctx.approval_status, "approved")
        self.assertEqual(ctx.trace_id, "trace-1")

    def test_is_frozen(self):
        ctx = ChannelSafetyContext()
        with self.assertRaises(Exception):
            ctx.trace_id = "new"  # type: ignore[misc]


class CanonicalChannelEventTests(unittest.TestCase):
    def test_minimal_inbound_event(self):
        evt = CanonicalChannelEvent(
            gateway_id="gw-1",
            channel_key="whatsapp_personal",
            external_message_id="ext-1",
            remote_jid="123@s.whatsapp.net",
            text="hello",
            direction="inbound",
        )
        self.assertEqual(evt.gateway_id, "gw-1")
        self.assertEqual(evt.direction, "inbound")
        self.assertEqual(evt.safety_context.kill_switch_checked, True)

    def test_full_outbound_event(self):
        ctx = ChannelSafetyContext(approval_status="approved", trace_id="t-1")
        evt = CanonicalChannelEvent(
            gateway_id="gw-2",
            channel_key="telegram_personal",
            external_message_id="ext-2",
            remote_jid="456",
            text="reply",
            direction="outbound",
            trace_id="t-1",
            safety_context=ctx,
        )
        self.assertEqual(evt.direction, "outbound")
        self.assertEqual(evt.trace_id, "t-1")
        self.assertEqual(evt.safety_context.approval_status, "approved")


class ChannelHealthPayloadTests(unittest.TestCase):
    def test_pass_status(self):
        p = ChannelHealthPayload(id="whatsapp_personal", status="pass", summary="connected")
        self.assertEqual(p.status, "pass")

    def test_warn_with_detail(self):
        p = ChannelHealthPayload(
            id="telegram_personal",
            status="warn",
            summary="disconnected",
            detail={"last_error": "session revoked"},
        )
        self.assertEqual(p.status, "warn")
        self.assertIsNotNone(p.detail)
        self.assertEqual(p.detail["last_error"], "session revoked")  # type: ignore[index]


class ChannelCapabilityContractTests(unittest.TestCase):
    def test_live_contract(self):
        c = ChannelCapabilityContract(
            channel_key="telegram_personal",
            provider="telegram_gramjs",
            runtime_lane="personal_gateway",
            memory_surface="direct_chat",
            stage="live",
            live_capable=True,
        )
        self.assertTrue(c.live_capable)
        self.assertEqual(c.stage, "live")

    def test_deferred_contract(self):
        c = ChannelCapabilityContract(
            channel_key="signal_personal",
            provider="signal_local_bridge",
            runtime_lane="personal_gateway",
            memory_surface="direct_chat",
            stage="next",
            live_capable=False,
        )
        self.assertFalse(c.live_capable)


if __name__ == "__main__":
    unittest.main()

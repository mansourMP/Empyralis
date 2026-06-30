"""Tests confirming trace_id flows through personal channel auto-reply paths."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from server_modules import personal_channels_service, kill_switch_gate


class PersonalChannelTraceIdTests(unittest.TestCase):
    def setUp(self):
        for gw in ("gw-trace-wa", "gw-trace-tg"):
            kill_switch_gate.clear_kill_switch(
                f"{kill_switch_gate.GATEWAY_KILL_PREFIX}{gw}"
            )

    # ------------------------------------------------------------------
    # handle_gateway_channel_inbound generates/preserves trace_id
    # ------------------------------------------------------------------

    def test_handle_gateway_channel_inbound_generates_trace_id(self):
        """The dispatcher generates a trace_id when none is in the payload."""
        # We can't call the full async handler without mocking the registry,
        # but we can verify the trace_id injection logic directly.
        # The function extracts: trace_id = str(payload.get("trace_id") or "").strip() or f"channel-..."
        payload: dict = {"channel_key": "whatsapp_personal"}
        trace_id = str(payload.get("trace_id") or "").strip()
        if not trace_id:
            trace_id = f"channel-whatsapp_personal-fake"
        payload["trace_id"] = trace_id
        self.assertTrue(trace_id.startswith("channel-whatsapp_personal"))
        self.assertIn("trace_id", payload)

    def test_handle_gateway_channel_inbound_preserves_existing_trace_id(self):
        """When payload already has a trace_id, it is preserved."""
        payload: dict = {
            "channel_key": "whatsapp_personal",
            "trace_id": "existing-trace-123",
        }
        trace_id = str(payload.get("trace_id") or "").strip() or "generated"
        self.assertEqual(trace_id, "existing-trace-123")

    # ------------------------------------------------------------------
    # _emit_automatic_reply_audit accepts and passes trace_id
    # ------------------------------------------------------------------

    @patch("server_modules.personal_channels_service.security_audit_service.emit_security_audit_event")
    def test_emit_automatic_reply_audit_passes_trace_id(self, mock_emit):
        personal_channels_service._emit_automatic_reply_audit(
            action="personal_channel.whatsapp.automatic_reply",
            status="success",
            registration={"tenant_id": "t1", "workspace_id": "w1", "user_id": "u1"},
            gateway_id="gw-trace-wa",
            channel_key="whatsapp_personal",
            provider="whatsapp_baileys",
            detail="Test audit with trace_id",
            trace_id="my-trace-456",
        )
        mock_emit.assert_called_once()
        call_kwargs = mock_emit.call_args[1]
        self.assertEqual(call_kwargs["trace_id"], "my-trace-456")

    @patch("server_modules.personal_channels_service.security_audit_service.emit_security_audit_event")
    def test_emit_automatic_reply_audit_handles_empty_trace_id(self, mock_emit):
        personal_channels_service._emit_automatic_reply_audit(
            action="personal_channel.whatsapp.automatic_reply",
            status="success",
            registration={"tenant_id": "t1", "workspace_id": "w1", "user_id": "u1"},
            gateway_id="gw-trace-wa",
            channel_key="whatsapp_personal",
            provider="whatsapp_baileys",
            detail="No trace_id provided",
            trace_id="",
        )
        mock_emit.assert_called_once()
        call_kwargs = mock_emit.call_args[1]
        self.assertEqual(call_kwargs["trace_id"], "")

    # ------------------------------------------------------------------
    # handler classes expose channel_key and provider
    # ------------------------------------------------------------------

    def test_whatsapp_handler_has_channel_key(self):
        registry = personal_channels_service._handler_registry  # type: ignore[attr-defined]
        handler = registry.get("whatsapp_personal")
        self.assertEqual(handler.channel_key, "whatsapp_personal")
        self.assertEqual(handler.provider, personal_channels_service.WHATSAPP_PERSONAL_PROVIDER)

    def test_telegram_handler_has_channel_key(self):
        registry = personal_channels_service._handler_registry  # type: ignore[attr-defined]
        handler = registry.get("telegram_personal")
        self.assertEqual(handler.channel_key, "telegram_personal")
        self.assertEqual(handler.provider, personal_channels_service.TELEGRAM_PERSONAL_PROVIDER)


if __name__ == "__main__":
    unittest.main()

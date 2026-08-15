"""Tests confirming trace_id flows through personal channel auto-reply paths.

REWRITTEN 2026-08-15. Two things were wrong with the previous version, and
commit 6b2baf97e (the full OpenClaw cutover, 2026-08-14) made both visible:

  * `test_whatsapp_handler_has_channel_key` / `test_telegram_handler_has_
    channel_key` asked `_handler_registry` for `whatsapp_personal` /
    `telegram_personal`. The cutover deleted both handlers, so those two
    raised `ValueError: No handler registered for channel_key=...`. They are
    replaced by a loop over the registry's OWN key set, which cannot go stale
    the same way and covers all 24 channels instead of 2.
  * The two `handle_gateway_channel_inbound` trace_id tests never called
    `handle_gateway_channel_inbound`. They re-implemented its trace_id
    expression inline in the test body and asserted on their own copy --- a
    test that agrees with itself and checks nothing about the module. They now
    drive the real function, with `_handler_registry` swapped for a recorder
    so the payload the real code hands the handler is what gets asserted.
"""

from __future__ import annotations

import unittest
from typing import Any, Dict, Optional
from unittest.mock import patch

from server_modules import personal_channels_service, kill_switch_gate


class _RecordingHandler:
    """Stands in for a real channel handler, capturing the payload that
    handle_gateway_channel_inbound hands it after its trace_id boundary."""

    def __init__(self, channel_key: str, provider: str) -> None:
        self.channel_key = channel_key
        self.provider = provider
        self.seen_payload: Optional[Dict[str, Any]] = None

    async def handle_inbound(self, *, gateway_id: str, registration: Dict[str, Any], payload: Dict[str, Any]):
        self.seen_payload = payload
        return {"ok": True}


class _RecordingRegistry:
    def __init__(self, handler: _RecordingHandler) -> None:
        self._handler = handler

    def get(self, channel_key: str) -> _RecordingHandler:
        return self._handler


class PersonalChannelTraceIdTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        for gw in ("gw-trace-1", "gw-trace-2"):
            kill_switch_gate.clear_kill_switch(
                f"{kill_switch_gate.GATEWAY_KILL_PREFIX}{gw}"
            )
        # A real, live channel key resolved from the registry rather than
        # typed: a hardcoded key is exactly what let the deleted version of
        # this file keep "passing" against channels the cutover removed.
        self.channel_key = sorted(personal_channels_service._handler_registry.registered_keys())[0]
        self.spec = personal_channels_service.LOCAL_BRIDGE_PERSONAL_CHANNELS[self.channel_key]
        self.registration = {
            "gateway_id": "gw-trace-1",
            "workspace_id": "default",
            "tenant_id": "tenant-1",
            "device_trust_state": "trusted",
            "active_session_id": "sess-1",
        }

    async def _drive_inbound(self, payload: Dict[str, Any]) -> _RecordingHandler:
        handler = _RecordingHandler(self.channel_key, str(self.spec["provider"]))
        with (
            # Not the subject here: this asks the gateway registration whether
            # it advertises the channel, which a synthetic registration does
            # not. Its own coverage lives in the personal-channel gate tests.
            patch(
                "server_modules.personal_channels_service._assert_gateway_advertised_personal_channel",
                return_value=None,
            ),
            patch.object(personal_channels_service, "_handler_registry", _RecordingRegistry(handler)),
        ):
            await personal_channels_service.handle_gateway_channel_inbound(
                gateway_id="gw-trace-1",
                registration=self.registration,
                payload=payload,
            )
        return handler

    # ------------------------------------------------------------------
    # handle_gateway_channel_inbound generates/preserves trace_id
    # ------------------------------------------------------------------

    async def test_handle_gateway_channel_inbound_generates_trace_id(self):
        """With no trace_id on the wire, the inbound boundary mints one and
        stamps it on the payload the handler is given."""
        handler = await self._drive_inbound(
            {
                "channel_key": self.channel_key,
                "provider": str(self.spec["provider"]),
                "message": {"external_message_id": "m-1", "remote_jid": "contact-1", "text": "hi"},
            }
        )
        self.assertIsNotNone(handler.seen_payload)
        trace_id = str((handler.seen_payload or {}).get("trace_id") or "")
        self.assertTrue(
            trace_id.startswith(f"channel-{self.channel_key}-"),
            f"unexpected generated trace_id: {trace_id!r}",
        )

    async def test_handle_gateway_channel_inbound_preserves_existing_trace_id(self):
        """A trace_id already on the wire is passed through untouched."""
        handler = await self._drive_inbound(
            {
                "channel_key": self.channel_key,
                "provider": str(self.spec["provider"]),
                "trace_id": "existing-trace-123",
                "message": {"external_message_id": "m-2", "remote_jid": "contact-1", "text": "hi"},
            }
        )
        self.assertEqual((handler.seen_payload or {}).get("trace_id"), "existing-trace-123")

    # ------------------------------------------------------------------
    # _emit_automatic_reply_audit accepts and passes trace_id
    # ------------------------------------------------------------------

    @patch("server_modules.personal_channels_service.security_audit_service.emit_security_audit_event")
    def test_emit_automatic_reply_audit_passes_trace_id(self, mock_emit):
        personal_channels_service._emit_automatic_reply_audit(
            action=f"personal_channel.{self.channel_key}.automatic_reply",
            status="success",
            registration={"tenant_id": "t1", "workspace_id": "w1", "user_id": "u1"},
            gateway_id="gw-trace-1",
            channel_key=self.channel_key,
            provider=str(self.spec["provider"]),
            detail="Test audit with trace_id",
            trace_id="my-trace-456",
        )
        mock_emit.assert_called_once()
        call_kwargs = mock_emit.call_args[1]
        self.assertEqual(call_kwargs["trace_id"], "my-trace-456")

    @patch("server_modules.personal_channels_service.security_audit_service.emit_security_audit_event")
    def test_emit_automatic_reply_audit_handles_empty_trace_id(self, mock_emit):
        personal_channels_service._emit_automatic_reply_audit(
            action=f"personal_channel.{self.channel_key}.automatic_reply",
            status="success",
            registration={"tenant_id": "t1", "workspace_id": "w1", "user_id": "u1"},
            gateway_id="gw-trace-1",
            channel_key=self.channel_key,
            provider=str(self.spec["provider"]),
            detail="No trace_id provided",
            trace_id="",
        )
        mock_emit.assert_called_once()
        call_kwargs = mock_emit.call_args[1]
        self.assertEqual(call_kwargs["trace_id"], "")

    # ------------------------------------------------------------------
    # handler classes expose channel_key and provider
    # ------------------------------------------------------------------

    def test_every_registered_handler_reports_its_own_channel_key(self):
        """Driven off the registry's own key set rather than a hand-written
        pair of channels, so a cutover or a rename cannot leave this asserting
        against handlers that no longer exist."""
        registry = personal_channels_service._handler_registry
        keys = sorted(registry.registered_keys())
        self.assertTrue(keys, "no personal-channel handlers are registered at all")
        for channel_key in keys:
            with self.subTest(channel_key=channel_key):
                handler = registry.get(channel_key)
                self.assertEqual(handler.channel_key, channel_key)
                self.assertEqual(
                    handler.provider,
                    personal_channels_service.LOCAL_BRIDGE_PERSONAL_CHANNELS[channel_key]["provider"],
                )


if __name__ == "__main__":
    unittest.main()

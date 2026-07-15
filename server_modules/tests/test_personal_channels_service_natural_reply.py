"""Natural replies for the LIVE personal-channel path.

server_modules/personal_channels_service.py is the module actually wired to
the gateway inbound path: gateway_protocol_service imports it directly
(gateway_protocol_service.py:28) and routes every "channel.inbound" event
frame through personal_channels_service.handle_gateway_channel_inbound
(gateway_protocol_service.py:2133). Its three auto-reply dispatch sites
--- _deliver_whatsapp_personal_reply, _handle_telegram_gateway_channel_inbound,
and _deliver_local_bridge_personal_reply --- used to pass
reply_to_external_message_id=external_message_id unconditionally to
gateway_protocol_service.dispatch_channel_outbound, forcing every automatic
reply to render as a formal "reply-to-THIS-message" quote bubble on
Telegram/WhatsApp (the founder's #1 channel complaint; OpenClaw, the
reference, defaults reply-to threading OFF for these channels).

These tests prove the three live auto-reply dispatch sites now send
reply_to_external_message_id=None, and that the explicit "reply to X" send
path (send_whatsapp_personal_message et al.) is untouched and still forwards
a caller-supplied id. The local DB causality/audit bookkeeping
(create_or_get_outbound_message's own reply_to_external_message_id) is left
recording the triggering id --- it does not affect what is rendered over the
wire --- and is deliberately not asserted on here.
"""

import importlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from server_modules import personal_channels_service, personal_channels_repository


class PersonalChannelsServiceNaturalReplyTests(unittest.IsolatedAsyncioTestCase):
    _ALLOW_DISPATCH_DECISION = {
        "ok": True,
        "decision": "allow",
        "reason": "gateway_service_operation_allowed",
        "operation": "protocol_route",
        "next_action": "dispatch_gateway_operation",
    }

    def setUp(self) -> None:
        global personal_channels_service, personal_channels_repository
        personal_channels_service = importlib.import_module("server_modules.personal_channels_service")
        personal_channels_repository = importlib.import_module("server_modules.personal_channels_repository")

        self.tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(self.tmpdir.name) / "personal-channels.sqlite3"
        personal_channels_repository.init_personal_channels_db(db_path)
        self.db_patcher = patch.object(personal_channels_repository, "PERSONAL_CHANNELS_DB_FILE", db_path)
        self.db_patcher.start()

        # workspace_id="default" so _deliver_whatsapp_personal_reply skips its
        # control_plane_repository.get_workspace_by_id validation round-trip
        # (only runs for a non-"default" workspace); irrelevant to reply-to.
        self.registration = {
            "gateway_id": "gw-live-1",
            "workspace_id": "default",
            "tenant_id": "tenant-1",
            "device_trust_state": "trusted",
            "active_session_id": "sess-1",
        }

    def tearDown(self) -> None:
        self.db_patcher.stop()
        self.tmpdir.cleanup()

    async def test_whatsapp_auto_reply_dispatches_without_reply_to_id(self) -> None:
        with (
            patch(
                "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                return_value=self._ALLOW_DISPATCH_DECISION,
            ),
            patch(
                "server_modules.sage_command_dispatcher.dispatch_command",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "server_modules.personal_channels_service.personal_channel_sage_bridge_service.build_whatsapp_personal_reply",
                return_value={"text": "Sure, on it.", "source": "sage"},
            ),
            patch(
                "server_modules.personal_channels_service.gateway_protocol_service.dispatch_channel_outbound",
                new=AsyncMock(return_value={"external_message_id": "wa-out-1"}),
                create=True,
            ) as dispatch_mock,
            patch("server_modules.personal_channels_service.security_audit_service.emit_security_audit_event"),
        ):
            result = await personal_channels_service._deliver_whatsapp_personal_reply(
                gateway_id="gw-live-1",
                registration=self.registration,
                inbound={"external_message_id": "wa-in-1", "remote_jid": "15551234567@s.whatsapp.net"},
                remote_jid="15551234567@s.whatsapp.net",
                external_message_id="wa-in-1",
                text="hey are you around?",
                push_name="Mansur",
                duplicate=False,
            )

        dispatch_mock.assert_awaited_once()
        self.assertIsNone(dispatch_mock.call_args.kwargs["reply_to_external_message_id"])
        self.assertEqual(result["outbound"]["status"], "delivered")

    async def test_telegram_auto_reply_dispatches_without_reply_to_id(self) -> None:
        with (
            patch(
                "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                return_value=self._ALLOW_DISPATCH_DECISION,
            ),
            patch(
                "server_modules.personal_channels_service.personal_channel_sage_bridge_service.build_telegram_personal_reply",
                return_value={"text": "Sure, on it.", "source": "sage"},
            ),
            patch(
                "server_modules.personal_channels_service.gateway_protocol_service.dispatch_channel_outbound",
                new=AsyncMock(return_value={"external_message_id": "tg-out-1"}),
                create=True,
            ) as dispatch_mock,
            patch("server_modules.personal_channels_service.security_audit_service.emit_security_audit_event"),
        ):
            result = await personal_channels_service._handle_telegram_gateway_channel_inbound(
                gateway_id="gw-live-1",
                registration=self.registration,
                payload={
                    "channel_key": personal_channels_service.TELEGRAM_PERSONAL_CHANNEL_KEY,
                    "provider": personal_channels_service.TELEGRAM_PERSONAL_PROVIDER,
                    "message": {
                        "external_message_id": "tg-in-1",
                        "remote_jid": "123456789",
                        "sender_jid": "123456789",
                        "push_name": "Mansur",
                        "text": "hey are you around?",
                        "from_me": False,
                    },
                },
            )

        dispatch_mock.assert_awaited_once()
        self.assertIsNone(dispatch_mock.call_args.kwargs["reply_to_external_message_id"])
        self.assertEqual(result["outbound"]["status"], "delivered")

    async def test_local_bridge_auto_reply_dispatches_without_reply_to_id(self) -> None:
        with (
            patch(
                "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                return_value=self._ALLOW_DISPATCH_DECISION,
            ),
            patch(
                "server_modules.personal_channels_service.personal_channel_sage_bridge_service.build_personal_channel_reply_async",
                new=AsyncMock(return_value={"text": "Sure, on it.", "source": "sage"}),
                create=True,
            ),
            patch(
                "server_modules.personal_channels_service.gateway_protocol_service.dispatch_channel_outbound",
                new=AsyncMock(return_value={"external_message_id": "sig-out-1"}),
                create=True,
            ) as dispatch_mock,
            patch("server_modules.personal_channels_service.security_audit_service.emit_security_audit_event"),
        ):
            result = await personal_channels_service._deliver_local_bridge_personal_reply(
                gateway_id="gw-live-1",
                registration=self.registration,
                inbound={"external_message_id": "sig-in-1", "remote_jid": "signal-user-1"},
                remote_jid="signal-user-1",
                external_message_id="sig-in-1",
                text="hey are you around?",
                push_name="Mansur",
                duplicate=False,
                channel_key="signal_personal",
                provider="signal_local_bridge",
                label="Signal",
            )

        dispatch_mock.assert_awaited_once()
        self.assertIsNone(dispatch_mock.call_args.kwargs["reply_to_external_message_id"])
        self.assertEqual(result["outbound"]["status"], "delivered")

    async def test_whatsapp_explicit_reply_still_forwards_caller_supplied_id(self) -> None:
        """Scope guard-rail: send_whatsapp_personal_message is the explicit
        "reply to X" path (e.g. an approved agent tool call), not an
        auto-reply -- it must keep forwarding whatever id the caller passes.
        This fix only drops the *forced* id on the automatic-reply path."""
        with (
            patch(
                "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                return_value=self._ALLOW_DISPATCH_DECISION,
            ),
            patch(
                "server_modules.personal_channels_service.gateway_protocol_service.dispatch_channel_outbound",
                new=AsyncMock(return_value={"external_message_id": "wa-out-2"}),
                create=True,
            ) as dispatch_mock,
        ):
            await personal_channels_service.send_whatsapp_personal_message(
                gateway_id="gw-live-1",
                registration=self.registration,
                remote_jid="15551234567@s.whatsapp.net",
                text="Replying to your earlier question",
                idempotency_key="explicit-send-1",
                reply_to_external_message_id="wa-in-earlier",
            )

        dispatch_mock.assert_awaited_once()
        self.assertEqual(dispatch_mock.call_args.kwargs["reply_to_external_message_id"], "wa-in-earlier")


if __name__ == "__main__":
    unittest.main()

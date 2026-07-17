"""Outbound media threading for the LIVE personal-channel path.

server_modules/personal_channels_service.py is the module actually wired to
the gateway inbound path (see test_personal_channels_service_natural_reply.py's
docstring for the full citation). Its three auto-reply dispatch sites --
_deliver_whatsapp_personal_reply, _handle_telegram_gateway_channel_inbound,
and _deliver_local_bridge_personal_reply -- build a reply via
personal_channel_sage_bridge_service.build_*_personal_reply(...) and then
call gateway_protocol_service.dispatch_channel_outbound(...), which already
accepts a `media` parameter the gateway-side Telegram/WhatsApp runtimes
consume (empyralis-gateway/src/channels/telegram/runtime.ts's sendFinalOutbound,
whatsapp/runtime.ts's sendOutboundMediaItems). What was missing is exactly
what these tests prove is now wired: the reply dict's "media" key (populated
by send_image / generate_image's auto-attach -- see
server_modules/skills_service.py's execute_single_direct_tool_call and
session_ctx["pending_outbound_media"]) flowing through to that dispatch call.

Three properties are covered per channel:
  1. media on a normal (text + media) reply reaches dispatch_channel_outbound.
  2. a media-ONLY reply (empty text) is still dispatched, not treated as
     silence -- the pre-existing "not reply.get('text')" skip check would
     have dropped the media entirely if left unfixed.
  3. a genuinely empty reply (no text, no media) is still skipped, unchanged.
"""

import importlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from server_modules import personal_channels_service, personal_channels_repository


class PersonalChannelsServiceMediaTests(unittest.IsolatedAsyncioTestCase):
    _ALLOW_DISPATCH_DECISION = {
        "ok": True,
        "decision": "allow",
        "reason": "gateway_service_operation_allowed",
        "operation": "protocol_route",
        "next_action": "dispatch_gateway_operation",
    }
    _MEDIA_ITEM = {
        "kind": "image",
        "source_path": "/app/.orion-stack/generated_images/fox.png",
        "mime_type": "image/png",
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

        self.registration = {
            "gateway_id": "gw-media-1",
            "workspace_id": "default",
            "tenant_id": "tenant-1",
            "device_trust_state": "trusted",
            "active_session_id": "sess-1",
        }

    def tearDown(self) -> None:
        self.db_patcher.stop()
        self.tmpdir.cleanup()

    # ── WhatsApp ──────────────────────────────────────────────────────────

    async def test_whatsapp_auto_reply_carries_media_to_dispatch(self) -> None:
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
                return_value={"text": "Here's the fox.", "source": "sage", "media": [self._MEDIA_ITEM]},
            ),
            patch(
                "server_modules.personal_channels_service.gateway_protocol_service.dispatch_channel_outbound",
                new=AsyncMock(return_value={"external_message_id": "wa-out-1"}),
                create=True,
            ) as dispatch_mock,
            patch("server_modules.personal_channels_service.security_audit_service.emit_security_audit_event"),
        ):
            result = await personal_channels_service._deliver_whatsapp_personal_reply(
                gateway_id="gw-media-1",
                registration=self.registration,
                inbound={"external_message_id": "wa-in-1", "remote_jid": "15551234567@s.whatsapp.net"},
                remote_jid="15551234567@s.whatsapp.net",
                external_message_id="wa-in-1",
                text="send me that fox picture",
                push_name="Mansur",
                duplicate=False,
            )

        dispatch_mock.assert_awaited_once()
        self.assertEqual(dispatch_mock.call_args.kwargs["media"], [self._MEDIA_ITEM])
        self.assertEqual(result["outbound"]["status"], "delivered")

    async def test_whatsapp_media_only_reply_still_dispatches(self) -> None:
        """Empty text + a queued attachment must NOT be treated as
        "Sage returned no reply" -- that pre-existing skip check gated
        purely on reply["text"], which would have silently dropped a
        legitimate media-only turn (e.g. "just send the photo back")."""
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
                return_value={"text": "", "source": "sage", "media": [self._MEDIA_ITEM]},
            ),
            patch(
                "server_modules.personal_channels_service.gateway_protocol_service.dispatch_channel_outbound",
                new=AsyncMock(return_value={"external_message_id": "wa-out-2"}),
                create=True,
            ) as dispatch_mock,
            patch("server_modules.personal_channels_service.security_audit_service.emit_security_audit_event"),
        ):
            result = await personal_channels_service._deliver_whatsapp_personal_reply(
                gateway_id="gw-media-1",
                registration=self.registration,
                inbound={"external_message_id": "wa-in-2", "remote_jid": "15551234567@s.whatsapp.net"},
                remote_jid="15551234567@s.whatsapp.net",
                external_message_id="wa-in-2",
                text="send me that fox picture",
                push_name="Mansur",
                duplicate=False,
            )

        dispatch_mock.assert_awaited_once()
        self.assertEqual(dispatch_mock.call_args.kwargs["media"], [self._MEDIA_ITEM])
        self.assertEqual(dispatch_mock.call_args.kwargs["text"], "")
        self.assertEqual(result["outbound"]["status"], "delivered")

    async def test_whatsapp_truly_empty_reply_still_skipped(self) -> None:
        """Unchanged behavior: no text AND no media means genuinely no
        reply -- still skipped, not dispatched."""
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
                return_value={"text": "", "source": "sage"},
            ),
            patch(
                "server_modules.personal_channels_service.gateway_protocol_service.dispatch_channel_outbound",
                new=AsyncMock(return_value={"external_message_id": "wa-out-3"}),
                create=True,
            ) as dispatch_mock,
        ):
            result = await personal_channels_service._deliver_whatsapp_personal_reply(
                gateway_id="gw-media-1",
                registration=self.registration,
                inbound={"external_message_id": "wa-in-3", "remote_jid": "15551234567@s.whatsapp.net"},
                remote_jid="15551234567@s.whatsapp.net",
                external_message_id="wa-in-3",
                text="[SILENT]",
                push_name="Mansur",
                duplicate=False,
            )

        dispatch_mock.assert_not_awaited()
        self.assertIsNone(result["outbound"])

    # ── Telegram ──────────────────────────────────────────────────────────

    async def test_telegram_auto_reply_carries_media_to_dispatch(self) -> None:
        personal_channels_repository.upsert_telegram_state(
            gateway_id="gw-media-1",
            tenant_id="tenant-1",
            workspace_id="default",
            user_id="",
            channel_key=personal_channels_service.TELEGRAM_PERSONAL_CHANNEL_KEY,
            agent_id="",
            provider=personal_channels_service.TELEGRAM_PERSONAL_PROVIDER,
            status="connected",
            linked_user_id="123456789",
        )
        with (
            patch(
                "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                return_value=self._ALLOW_DISPATCH_DECISION,
            ),
            patch(
                "server_modules.personal_channels_service.personal_channel_sage_bridge_service.build_telegram_personal_reply",
                return_value={"text": "Here's the fox.", "source": "sage", "media": [self._MEDIA_ITEM]},
            ),
            patch(
                "server_modules.personal_channels_service.gateway_protocol_service.dispatch_channel_outbound",
                new=AsyncMock(return_value={"external_message_id": "tg-out-1"}),
                create=True,
            ) as dispatch_mock,
            patch("server_modules.personal_channels_service.security_audit_service.emit_security_audit_event"),
        ):
            result = await personal_channels_service._handle_telegram_gateway_channel_inbound(
                gateway_id="gw-media-1",
                registration=self.registration,
                payload={
                    "channel_key": personal_channels_service.TELEGRAM_PERSONAL_CHANNEL_KEY,
                    "provider": personal_channels_service.TELEGRAM_PERSONAL_PROVIDER,
                    "message": {
                        "external_message_id": "tg-in-1",
                        "remote_jid": "123456789",
                        "sender_jid": "123456789",
                        "push_name": "Mansur",
                        "text": "send me that fox picture",
                        "from_me": False,
                    },
                },
            )

        dispatch_mock.assert_awaited_once()
        self.assertEqual(dispatch_mock.call_args.kwargs["media"], [self._MEDIA_ITEM])
        self.assertEqual(result["outbound"]["status"], "delivered")

    # ── Local bridge (Signal/iMessage/WeChat) ───────────────────────────

    async def test_local_bridge_auto_reply_carries_media_to_dispatch(self) -> None:
        with (
            patch(
                "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                return_value=self._ALLOW_DISPATCH_DECISION,
            ),
            patch(
                "server_modules.personal_channels_service.personal_channel_sage_bridge_service.build_personal_channel_reply_async",
                new=AsyncMock(return_value={"text": "Here's the fox.", "source": "sage", "media": [self._MEDIA_ITEM]}),
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
                gateway_id="gw-media-1",
                registration=self.registration,
                inbound={"external_message_id": "sig-in-1", "remote_jid": "signal-user-1"},
                remote_jid="signal-user-1",
                external_message_id="sig-in-1",
                text="send me that fox picture",
                push_name="Mansur",
                duplicate=False,
                channel_key="signal_personal",
                provider="signal_local_bridge",
                label="Signal",
            )

        dispatch_mock.assert_awaited_once()
        self.assertEqual(dispatch_mock.call_args.kwargs["media"], [self._MEDIA_ITEM])
        self.assertEqual(result["outbound"]["status"], "delivered")

    # ── Idempotent replay: media survives a retry that never rebuilds `reply` ──

    async def test_whatsapp_idempotent_replay_still_carries_media(self) -> None:
        """If create_or_get_outbound_message finds a PRE-EXISTING pending
        outbound (e.g. a retry after a crash between create and dispatch),
        _deliver_whatsapp_personal_reply never rebuilds `reply` -- media must
        be recovered from the stored outbound row's metadata, not a `reply`
        variable that's out of scope on this path."""
        idempotency_key = "whatsapp_personal:wa-in-4"
        personal_channels_repository.create_or_get_outbound_message(
            gateway_id="gw-media-1",
            channel_key=personal_channels_service.WHATSAPP_PERSONAL_CHANNEL_KEY,
            idempotency_key=idempotency_key,
            remote_jid="15551234567@s.whatsapp.net",
            text="Here's the fox.",
            reply_to_external_message_id="wa-in-4",
            metadata={"reply_source": "sage", "media": [self._MEDIA_ITEM]},
        )

        with (
            patch(
                "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                return_value=self._ALLOW_DISPATCH_DECISION,
            ),
            patch(
                "server_modules.personal_channels_service.gateway_protocol_service.dispatch_channel_outbound",
                new=AsyncMock(return_value={"external_message_id": "wa-out-4"}),
                create=True,
            ) as dispatch_mock,
            patch("server_modules.personal_channels_service.security_audit_service.emit_security_audit_event"),
        ):
            result = await personal_channels_service._deliver_whatsapp_personal_reply(
                gateway_id="gw-media-1",
                registration=self.registration,
                inbound={"external_message_id": "wa-in-4", "remote_jid": "15551234567@s.whatsapp.net", "reply_idempotency_key": idempotency_key},
                remote_jid="15551234567@s.whatsapp.net",
                external_message_id="wa-in-4",
                text="send me that fox picture",
                push_name="Mansur",
                duplicate=False,
            )

        dispatch_mock.assert_awaited_once()
        self.assertEqual(dispatch_mock.call_args.kwargs["media"], [self._MEDIA_ITEM])
        self.assertEqual(result["outbound"]["status"], "delivered")


if __name__ == "__main__":
    unittest.main()

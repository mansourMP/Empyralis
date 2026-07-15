"""Tests for Feature B: server-side media pipeline (fetch / store / transcribe).

Proves the exact media contract round-trips:

  INBOUND:  gateway_protocol_service.fetch_channel_media (the
            channel.media_fetch request/response RPC over the live gateway
            WebSocket) -> personal_channel_media_store_service (per-agent
            store + attachment-context mirror) -> for voice/audio,
            personal_channel_transcription_service (BYOK Whisper) -> the
            "[Voice message]: <transcript>" text splice
            personal_channels_service's inbound handlers perform.

  OUTBOUND: gateway_protocol_service.dispatch_channel_outbound carries an
            optional `media` array straight through to the outbound wire
            payload sent to the gateway.
"""

from __future__ import annotations

import asyncio
import base64
import importlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from server_modules import (
    gateway_protocol_service,
    personal_channel_media_store_service,
    personal_channel_transcription_service,
    workspace_context,
)


_ALLOW_DISPATCH_DECISION = {
    "ok": True,
    "decision": "allow",
    "reason": "gateway_service_operation_allowed",
    "operation": "protocol_route",
    "next_action": "dispatch_gateway_operation",
}


class FetchChannelMediaContractTests(unittest.TestCase):
    """gateway_protocol_service.fetch_channel_media — the exact
    server<->gateway RPC contract for resolving an inbound media_id."""

    def test_fetch_channel_media_sends_channel_media_fetch_request(self) -> None:
        raw = b"xyz"
        connection = SimpleNamespace(
            send_request=AsyncMock(
                return_value={
                    "ok": True,
                    "payload": {
                        "media_id": "wa-media-abc",
                        "mime_type": "image/jpeg",
                        "filename": "photo.jpg",
                        "size_bytes": len(raw),
                        "data_base64": base64.b64encode(raw).decode("ascii"),
                    },
                }
            ),
        )
        with (
            patch("server_modules.gateway_protocol_service.assert_not_killed", return_value=None),
            patch("server_modules.gateway_protocol_service._get_live_connection", return_value=connection),
        ):
            result = asyncio.run(
                gateway_protocol_service.fetch_channel_media(
                    gateway_id="gw-1",
                    channel_key="whatsapp_personal",
                    provider="whatsapp_baileys",
                    media_id="wa-media-abc",
                )
            )
        connection.send_request.assert_awaited_once()
        call_kwargs = connection.send_request.call_args.kwargs
        self.assertEqual(call_kwargs["message_type"], "channel.media_fetch")
        self.assertEqual(
            call_kwargs["payload"],
            {"channel_key": "whatsapp_personal", "provider": "whatsapp_baileys", "media_id": "wa-media-abc"},
        )
        self.assertEqual(result["data_base64"], base64.b64encode(raw).decode("ascii"))
        self.assertEqual(result["mime_type"], "image/jpeg")

    def test_fetch_channel_media_raises_on_gateway_error_response(self) -> None:
        connection = SimpleNamespace(
            send_request=AsyncMock(
                return_value={"ok": False, "error": {"code": "media_unavailable", "message": "expired"}}
            ),
        )
        with (
            patch("server_modules.gateway_protocol_service.assert_not_killed", return_value=None),
            patch("server_modules.gateway_protocol_service._get_live_connection", return_value=connection),
        ):
            with self.assertRaises(ValueError) as raised:
                asyncio.run(
                    gateway_protocol_service.fetch_channel_media(
                        gateway_id="gw-1",
                        channel_key="whatsapp_personal",
                        provider="whatsapp_baileys",
                        media_id="wa-media-abc",
                    )
                )
        self.assertIn("expired", str(raised.exception))

    def test_fetch_channel_media_requires_a_media_id(self) -> None:
        connection = SimpleNamespace(send_request=AsyncMock(side_effect=AssertionError("must not send request")))
        with (
            patch("server_modules.gateway_protocol_service.assert_not_killed", return_value=None),
            patch("server_modules.gateway_protocol_service._get_live_connection", return_value=connection),
        ):
            with self.assertRaises(ValueError):
                asyncio.run(
                    gateway_protocol_service.fetch_channel_media(
                        gateway_id="gw-1", channel_key="whatsapp_personal", provider="whatsapp_baileys", media_id="",
                    )
                )


class DispatchChannelOutboundMediaTests(unittest.TestCase):
    """dispatch_channel_outbound's outbound leg of the media contract."""

    def test_dispatch_channel_outbound_carries_media_field(self) -> None:
        connection = SimpleNamespace(
            session_id="sess-1",
            scope={"workspace_id": "ws-1", "tenant_id": "tenant-1"},
            send_request=AsyncMock(return_value={"ok": True, "payload": {"external_message_id": "wa-out-1"}}),
        )
        media_items = [
            {"kind": "image", "source_path": "/tmp/x.jpg", "mime_type": "image/jpeg", "caption": "look at this"}
        ]
        with (
            patch("server_modules.gateway_protocol_service.assert_not_killed", return_value=None),
            patch(
                "server_modules.gateway_protocol_service.evaluate_gateway_quota",
                return_value=SimpleNamespace(allowed=True, retry_after_seconds=None),
            ),
            patch("server_modules.gateway_protocol_service._get_live_connection", return_value=connection),
            patch(
                "server_modules.gateway_protocol_service.gateway_state_repository.get_gateway_registration",
                return_value={"gateway_id": "gw-1", "workspace_id": "ws-1", "tenant_id": "tenant-1"},
            ),
            patch("server_modules.gateway_protocol_service._enforce_gateway_quota_check", return_value=None),
            patch("server_modules.gateway_protocol_service._enforce_gateway_channel_protocol_route", return_value=None),
            patch("server_modules.gateway_protocol_service._enforce_gateway_protocol_message_decision", return_value=None),
        ):
            asyncio.run(
                gateway_protocol_service.dispatch_channel_outbound(
                    gateway_id="gw-1",
                    channel_key="whatsapp_personal",
                    provider="whatsapp_baileys",
                    remote_jid="123@s.whatsapp.net",
                    text="here's a photo",
                    idempotency_key="idem-1",
                    media=media_items,
                )
            )
        sent_payload = connection.send_request.call_args.kwargs["payload"]
        self.assertEqual(sent_payload["media"], media_items)

    def test_dispatch_channel_outbound_defaults_media_to_empty_list(self) -> None:
        connection = SimpleNamespace(
            session_id="sess-1",
            scope={"workspace_id": "ws-1", "tenant_id": "tenant-1"},
            send_request=AsyncMock(return_value={"ok": True, "payload": {}}),
        )
        with (
            patch("server_modules.gateway_protocol_service.assert_not_killed", return_value=None),
            patch(
                "server_modules.gateway_protocol_service.evaluate_gateway_quota",
                return_value=SimpleNamespace(allowed=True, retry_after_seconds=None),
            ),
            patch("server_modules.gateway_protocol_service._get_live_connection", return_value=connection),
            patch(
                "server_modules.gateway_protocol_service.gateway_state_repository.get_gateway_registration",
                return_value={"gateway_id": "gw-1", "workspace_id": "ws-1", "tenant_id": "tenant-1"},
            ),
            patch("server_modules.gateway_protocol_service._enforce_gateway_quota_check", return_value=None),
            patch("server_modules.gateway_protocol_service._enforce_gateway_channel_protocol_route", return_value=None),
            patch("server_modules.gateway_protocol_service._enforce_gateway_protocol_message_decision", return_value=None),
        ):
            asyncio.run(
                gateway_protocol_service.dispatch_channel_outbound(
                    gateway_id="gw-1",
                    channel_key="whatsapp_personal",
                    provider="whatsapp_baileys",
                    remote_jid="123@s.whatsapp.net",
                    text="just text",
                    idempotency_key="idem-2",
                )
            )
        sent_payload = connection.send_request.call_args.kwargs["payload"]
        self.assertEqual(sent_payload["media"], [])


class MediaStoreTests(unittest.IsolatedAsyncioTestCase):
    """personal_channel_media_store_service — fetch/store + attachment shaping."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self._dir_patcher = patch.object(workspace_context, "_WORKSPACE_DIR", Path(self.tmpdir.name))
        self._dir_patcher.start()

    def tearDown(self) -> None:
        self._dir_patcher.stop()
        self.tmpdir.cleanup()

    async def test_fetch_and_store_image_item_writes_canonical_and_attachment_copies(self) -> None:
        raw = b"\xff\xd8\xff\xe0fakejpegbytes"
        fetched_payload = {
            "media_id": "media-1",
            "mime_type": "image/jpeg",
            "filename": "photo.jpg",
            "size_bytes": len(raw),
            "data_base64": base64.b64encode(raw).decode("ascii"),
        }
        with patch(
            "server_modules.gateway_protocol_service.fetch_channel_media",
            new=AsyncMock(return_value=fetched_payload),
        ):
            record = await personal_channel_media_store_service.fetch_and_store_media_item(
                gateway_id="gw-1",
                channel_key="whatsapp_personal",
                provider="whatsapp_baileys",
                workspace_id="ws-media-1",
                agent_id="agent-1",
                item={"kind": "image", "media_id": "media-1", "mime_type": "image/jpeg", "filename": "photo.jpg"},
            )
        self.assertTrue(record["ok"])
        self.assertEqual(record["kind"], "image")
        canonical_path = Path(record["local_path"])
        self.assertTrue(canonical_path.exists())
        self.assertEqual(canonical_path.read_bytes(), raw)
        # Canonical store is per-agent, not shared workspace-root.
        self.assertIn("agent-1", str(canonical_path))

        attachment = record["attachment"]
        self.assertEqual(attachment["content_type"], "image/jpeg")
        attachments_dir = workspace_context.workspace_attachments_dir("ws-media-1")
        self.assertTrue((attachments_dir / attachment["safe_filename"]).exists())
        self.assertEqual((attachments_dir / attachment["safe_filename"]).read_bytes(), raw)

    async def test_fetch_and_store_requires_agent_id(self) -> None:
        record = await personal_channel_media_store_service.fetch_and_store_media_item(
            gateway_id="gw-1",
            channel_key="whatsapp_personal",
            provider="whatsapp_baileys",
            workspace_id="ws-media-1",
            agent_id="",
            item={"kind": "image", "media_id": "media-1", "mime_type": "image/jpeg"},
        )
        self.assertFalse(record["ok"])
        self.assertEqual(record["error"], "agent_id_required")

    async def test_unsupported_kind_is_rejected(self) -> None:
        record = await personal_channel_media_store_service.fetch_and_store_media_item(
            gateway_id="gw-1",
            channel_key="whatsapp_personal",
            provider="whatsapp_baileys",
            workspace_id="ws-media-1",
            agent_id="agent-1",
            item={"kind": "sticker", "media_id": "media-1", "mime_type": "image/webp"},
        )
        self.assertFalse(record["ok"])
        self.assertIn("unsupported_media_kind", record["error"])

    async def test_process_inbound_media_separates_attachments_from_voice(self) -> None:
        image_bytes = b"imagebytes"
        voice_bytes = b"oggvoicebytes"

        async def fake_fetch(*, gateway_id, channel_key, provider, media_id):
            if media_id == "img-1":
                return {
                    "media_id": "img-1", "mime_type": "image/png", "filename": "pic.png",
                    "size_bytes": len(image_bytes), "data_base64": base64.b64encode(image_bytes).decode("ascii"),
                }
            return {
                "media_id": "voice-1", "mime_type": "audio/ogg", "filename": "note.ogg",
                "size_bytes": len(voice_bytes), "data_base64": base64.b64encode(voice_bytes).decode("ascii"),
            }

        with patch(
            "server_modules.gateway_protocol_service.fetch_channel_media",
            new=AsyncMock(side_effect=fake_fetch),
        ):
            result = await personal_channel_media_store_service.process_inbound_media(
                gateway_id="gw-1",
                channel_key="whatsapp_personal",
                provider="whatsapp_baileys",
                workspace_id="ws-media-1",
                agent_id="agent-1",
                media=[
                    {"kind": "image", "media_id": "img-1", "mime_type": "image/png"},
                    {"kind": "voice", "media_id": "voice-1", "mime_type": "audio/ogg", "duration_sec": 4.2},
                ],
            )
        self.assertEqual(len(result["attachments"]), 1)
        self.assertEqual(len(result["voice_records"]), 1)
        self.assertEqual(result["voice_records"][0]["raw_bytes"], voice_bytes)
        self.assertEqual(result["voice_records"][0]["duration_sec"], 4.2)

    async def test_process_inbound_media_never_raises_on_fetch_failure(self) -> None:
        with patch(
            "server_modules.gateway_protocol_service.fetch_channel_media",
            new=AsyncMock(side_effect=ValueError("gateway offline")),
        ):
            result = await personal_channel_media_store_service.process_inbound_media(
                gateway_id="gw-1",
                channel_key="whatsapp_personal",
                provider="whatsapp_baileys",
                workspace_id="ws-media-1",
                agent_id="agent-1",
                media=[{"kind": "image", "media_id": "img-1", "mime_type": "image/png"}],
            )
        self.assertEqual(result["attachments"], [])
        self.assertFalse(result["records"][0]["ok"])


class TranscriptionServiceTests(unittest.IsolatedAsyncioTestCase):
    """personal_channel_transcription_service — BYOK Whisper + graceful degradation."""

    async def test_transcribe_voice_bytes_success(self) -> None:
        with (
            patch(
                "server_modules.direct_chat_provider_service.direct_chat_credentials",
                return_value={"api_key": "sk-test-123"},
            ),
            patch(
                "httpx.AsyncClient.post",
                new=AsyncMock(
                    return_value=SimpleNamespace(status_code=200, json=lambda: {"text": "call me back later"})
                ),
            ),
        ):
            result = await personal_channel_transcription_service.transcribe_voice_bytes(
                workspace_id="ws-1", audio_bytes=b"fakeaudio", mime_type="audio/ogg", filename="note.ogg",
            )
        self.assertTrue(result["ok"])
        self.assertEqual(result["transcript"], "call me back later")
        self.assertEqual(result["provider"], "openai")
        self.assertEqual(
            personal_channel_transcription_service.format_voice_message_text(result),
            "[Voice message]: call me back later",
        )

    async def test_transcribe_voice_bytes_degrades_gracefully_with_no_provider_configured(self) -> None:
        with patch(
            "server_modules.direct_chat_provider_service.direct_chat_credentials",
            return_value={},
        ):
            result = await personal_channel_transcription_service.transcribe_voice_bytes(
                workspace_id="ws-1", audio_bytes=b"fakeaudio", mime_type="audio/ogg",
            )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "stt_provider_not_configured")
        self.assertEqual(
            personal_channel_transcription_service.format_voice_message_text(result),
            "[Voice message — transcription unavailable]",
        )

    async def test_transcribe_voice_bytes_degrades_gracefully_on_http_error(self) -> None:
        with (
            patch(
                "server_modules.direct_chat_provider_service.direct_chat_credentials",
                return_value={"api_key": "sk-test-123"},
            ),
            patch(
                "httpx.AsyncClient.post",
                new=AsyncMock(return_value=SimpleNamespace(status_code=500, text="server error")),
            ),
        ):
            result = await personal_channel_transcription_service.transcribe_voice_bytes(
                workspace_id="ws-1", audio_bytes=b"fakeaudio", mime_type="audio/ogg",
            )
        self.assertFalse(result["ok"])
        self.assertEqual(
            personal_channel_transcription_service.format_voice_message_text(result),
            "[Voice message — transcription unavailable]",
        )

    async def test_transcribe_voice_bytes_rejects_empty_audio(self) -> None:
        result = await personal_channel_transcription_service.transcribe_voice_bytes(
            workspace_id="ws-1", audio_bytes=b"", mime_type="audio/ogg",
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "empty_audio_payload")


class InboundMediaEndToEndTests(unittest.IsolatedAsyncioTestCase):
    """Full round-trip through personal_channels_service's live inbound
    handler: a fake channel.inbound event with a voice `media` item ->
    fetch -> transcribe -> "[Voice message]: ..." spliced into the text
    handed to the reply builder."""

    def setUp(self) -> None:
        global personal_channels_service, personal_channels_repository
        personal_channels_service = importlib.import_module("server_modules.personal_channels_service")
        personal_channels_repository = importlib.import_module("server_modules.personal_channels_repository")

        self.tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(self.tmpdir.name) / "personal-channels.sqlite3"
        personal_channels_repository.init_personal_channels_db(db_path)
        self.db_patcher = patch.object(personal_channels_repository, "PERSONAL_CHANNELS_DB_FILE", db_path)
        self.db_patcher.start()

        self.workspace_tmpdir = tempfile.TemporaryDirectory()
        self.dir_patcher = patch.object(workspace_context, "_WORKSPACE_DIR", Path(self.workspace_tmpdir.name))
        self.dir_patcher.start()

        # workspace_id="default" so _deliver_whatsapp_personal_reply skips its
        # control_plane_repository.get_workspace_by_id validation round-trip
        # (only runs for a non-"default" workspace) — same convention as
        # test_personal_channels_service_natural_reply.py; irrelevant to the
        # media pipeline itself.
        self.registration = {
            "gateway_id": "gw-media-1",
            "workspace_id": "default",
            "tenant_id": "tenant-1",
            "device_trust_state": "trusted",
            "active_session_id": "sess-1",
        }

        # Claim the channel for a real agent_id first — the media store
        # requires one (see its module docstring); a real deployment
        # reaches this via configure_whatsapp_personal_gateway's
        # _claim_agent_channel_state before any message ever arrives.
        personal_channels_repository.upsert_whatsapp_state(
            gateway_id="gw-media-1",
            tenant_id="tenant-1",
            workspace_id="default",
            user_id="",
            channel_key=personal_channels_service.WHATSAPP_PERSONAL_CHANNEL_KEY,
            agent_id="agent-media-1",
            provider=personal_channels_service.WHATSAPP_PERSONAL_PROVIDER,
            status="connected",
            linked_jid="15550001111@s.whatsapp.net",
        )

    def tearDown(self) -> None:
        self.db_patcher.stop()
        self.tmpdir.cleanup()
        self.dir_patcher.stop()
        self.workspace_tmpdir.cleanup()

    async def test_voice_message_transcript_reaches_the_reply_builder(self) -> None:
        raw_audio = b"fake-ogg-bytes"
        fetched_payload = {
            "media_id": "voice-1",
            "mime_type": "audio/ogg",
            "filename": "note.ogg",
            "size_bytes": len(raw_audio),
            "data_base64": base64.b64encode(raw_audio).decode("ascii"),
        }
        with (
            patch(
                "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                return_value=_ALLOW_DISPATCH_DECISION,
            ),
            patch(
                "server_modules.gateway_protocol_service.fetch_channel_media",
                new=AsyncMock(return_value=fetched_payload),
            ),
            patch(
                "server_modules.direct_chat_provider_service.direct_chat_credentials",
                return_value={"api_key": "sk-test"},
            ),
            patch(
                "httpx.AsyncClient.post",
                new=AsyncMock(
                    return_value=SimpleNamespace(status_code=200, json=lambda: {"text": "please call the plumber tomorrow"})
                ),
            ),
            patch(
                "server_modules.personal_channels_service.personal_channel_sage_bridge_service.build_whatsapp_personal_reply",
                return_value={"text": "Got it, I will call the plumber.", "source": "sage"},
            ) as build_reply_mock,
            patch(
                "server_modules.personal_channels_service.gateway_protocol_service.dispatch_channel_outbound",
                new=AsyncMock(return_value={"external_message_id": "wa-out-1"}),
                create=True,
            ),
            patch("server_modules.personal_channels_service.security_audit_service.emit_security_audit_event"),
        ):
            result = await personal_channels_service._handle_whatsapp_gateway_channel_inbound(
                gateway_id="gw-media-1",
                registration=self.registration,
                payload={
                    "channel_key": personal_channels_service.WHATSAPP_PERSONAL_CHANNEL_KEY,
                    "provider": personal_channels_service.WHATSAPP_PERSONAL_PROVIDER,
                    "message": {
                        "external_message_id": "wa-voice-1",
                        "remote_jid": "15550001111@s.whatsapp.net",
                        "sender_jid": "15550001111@s.whatsapp.net",
                        "push_name": "Me",
                        "text": "",
                        "from_me": False,
                        "is_self_chat": True,
                        "media": [
                            {
                                "kind": "voice",
                                "media_id": "voice-1",
                                "mime_type": "audio/ogg",
                                "filename": "note.ogg",
                                "size_bytes": len(raw_audio),
                                "duration_sec": 3.1,
                            }
                        ],
                    },
                },
            )
        build_reply_mock.assert_called_once()
        sent_text = build_reply_mock.call_args.kwargs["text"]
        self.assertIn("[Voice message]: please call the plumber tomorrow", sent_text)
        self.assertFalse(result.get("blocked", False))

    async def test_image_message_becomes_an_attachment_on_the_reply_builder_call(self) -> None:
        raw_image = b"\xff\xd8\xff\xe0fakejpeg"
        fetched_payload = {
            "media_id": "img-1",
            "mime_type": "image/jpeg",
            "filename": "photo.jpg",
            "size_bytes": len(raw_image),
            "data_base64": base64.b64encode(raw_image).decode("ascii"),
        }
        with (
            patch(
                "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                return_value=_ALLOW_DISPATCH_DECISION,
            ),
            patch(
                "server_modules.gateway_protocol_service.fetch_channel_media",
                new=AsyncMock(return_value=fetched_payload),
            ),
            patch(
                "server_modules.personal_channels_service.personal_channel_sage_bridge_service.build_whatsapp_personal_reply",
                return_value={"text": "Nice photo!", "source": "sage"},
            ) as build_reply_mock,
            patch(
                "server_modules.personal_channels_service.gateway_protocol_service.dispatch_channel_outbound",
                new=AsyncMock(return_value={"external_message_id": "wa-out-2"}),
                create=True,
            ),
            patch("server_modules.personal_channels_service.security_audit_service.emit_security_audit_event"),
        ):
            await personal_channels_service._handle_whatsapp_gateway_channel_inbound(
                gateway_id="gw-media-1",
                registration=self.registration,
                payload={
                    "channel_key": personal_channels_service.WHATSAPP_PERSONAL_CHANNEL_KEY,
                    "provider": personal_channels_service.WHATSAPP_PERSONAL_PROVIDER,
                    "message": {
                        "external_message_id": "wa-image-1",
                        "remote_jid": "15550001111@s.whatsapp.net",
                        "sender_jid": "15550001111@s.whatsapp.net",
                        "push_name": "Me",
                        "text": "check this out",
                        "from_me": False,
                        "is_self_chat": True,
                        "media": [
                            {
                                "kind": "image",
                                "media_id": "img-1",
                                "mime_type": "image/jpeg",
                                "filename": "photo.jpg",
                                "size_bytes": len(raw_image),
                            }
                        ],
                    },
                },
            )
        build_reply_mock.assert_called_once()
        attachments = build_reply_mock.call_args.kwargs["attachments"]
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0]["content_type"], "image/jpeg")

    async def test_stranger_media_message_is_blocked_before_any_fetch(self) -> None:
        """dmPolicy runs BEFORE media processing — a blocked stranger's
        media must never even be fetched (efficiency + no reason to spend
        gateway round-trips or STT cost on a message that gets dropped)."""
        with (
            patch(
                "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                return_value=_ALLOW_DISPATCH_DECISION,
            ),
            patch(
                "server_modules.gateway_protocol_service.fetch_channel_media",
                new=AsyncMock(side_effect=AssertionError("must not fetch media for a blocked sender")),
            ),
            patch(
                "server_modules.personal_channels_service.gateway_protocol_service.dispatch_channel_outbound",
                new=AsyncMock(side_effect=AssertionError("must not dispatch")),
                create=True,
            ),
            patch("server_modules.personal_channels_service.security_audit_service.emit_security_audit_event"),
        ):
            result = await personal_channels_service._handle_whatsapp_gateway_channel_inbound(
                gateway_id="gw-media-1",
                registration=self.registration,
                payload={
                    "channel_key": personal_channels_service.WHATSAPP_PERSONAL_CHANNEL_KEY,
                    "provider": personal_channels_service.WHATSAPP_PERSONAL_PROVIDER,
                    "message": {
                        "external_message_id": "wa-stranger-media-1",
                        "remote_jid": "919999999999@s.whatsapp.net",
                        "sender_jid": "919999999999@s.whatsapp.net",
                        "push_name": "Rando",
                        "text": "",
                        "from_me": False,
                        "media": [
                            {"kind": "voice", "media_id": "voice-x", "mime_type": "audio/ogg"}
                        ],
                    },
                },
            )
        self.assertTrue(result.get("blocked"))


if __name__ == "__main__":
    unittest.main()

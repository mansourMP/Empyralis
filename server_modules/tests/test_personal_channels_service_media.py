"""Outbound media threading for the LIVE personal-channel path.

server_modules/personal_channels_service.py is the module actually wired to
the gateway inbound path (see test_personal_channels_service_natural_reply.py's
docstring for the full citation). Its auto-reply dispatch site builds a reply
via personal_channel_sage_bridge_service.build_personal_channel_reply_async(...)
and then calls gateway_protocol_service.dispatch_channel_outbound(...), which
already accepts a `media` parameter the gateway-side channel runtimes consume.
What was missing is exactly what these tests prove is now wired: the reply
dict's "media" key (populated by send_image / generate_image's auto-attach --
see server_modules/skills_service.py's execute_single_direct_tool_call and
session_ctx["pending_outbound_media"]) flowing through to that dispatch call.

Four properties are covered, each across every cut-over channel:
  1. media on a normal (text + media) reply reaches dispatch_channel_outbound.
  2. a media-ONLY reply (empty text) is still dispatched, not treated as
     silence -- the pre-existing "not reply.get('text')" skip check would
     have dropped the media entirely if left unfixed.
  3. a genuinely empty reply (no text, no media) is still skipped, unchanged.
  4. an idempotent replay recovers media from the stored outbound row rather
     than from a `reply` variable that is out of scope on that path.

RETARGETED 2026-08-15. This file used to drive three delivery sites:
`_deliver_whatsapp_personal_reply` (four of its six tests),
`_handle_telegram_gateway_channel_inbound`, and
`_deliver_local_bridge_personal_reply`. Commit 6b2baf97e --- the full OpenClaw
cutover, 2026-08-14 --- deleted the first two, so five of the six tests raised
AttributeError from the moment it landed. WhatsApp, Telegram, Signal, iMessage
and Weixin now all deliver through the one surviving function, so the
per-channel duplication collapses into a loop and every property is asserted
for every cut-over channel instead of properties 2--4 being WhatsApp-only.

Channel keys are DERIVED (registry ids x live routing table, two different
modules) rather than typed: the deleted local-bridge test hardcoded
`signal_personal`, a key the cutover removed, and went on passing against a
channel nothing routes.
"""

import importlib
import tempfile
import unittest
from pathlib import Path
from typing import Tuple
from unittest.mock import AsyncMock, patch

from server_modules import (
    openclaw_channel_registry,
    personal_channels_service,
    personal_channels_repository,
)


def _cut_over_channel_keys() -> Tuple[str, ...]:
    return tuple(
        sorted(
            f"{openclaw_channel_registry.CHANNEL_KEY_PREFIX}{channel_id}"
            for channel_id in openclaw_channel_registry.OPENCLAW_CUT_OVER_CHANNEL_IDS
        )
    )


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

    def test_cut_over_channel_keys_are_all_live(self) -> None:
        """Guards the loops below against a key nothing routes any more."""
        registry = personal_channels_service.LOCAL_BRIDGE_PERSONAL_CHANNELS
        keys = _cut_over_channel_keys()
        self.assertTrue(keys, "OPENCLAW_CUT_OVER_CHANNEL_IDS resolved to nothing")
        for channel_key in keys:
            self.assertIn(channel_key, registry, f"{channel_key} is not a live personal channel")

    def _delivery_patches(self, *, reply, dispatch_mock):
        spec_patches = [
            patch(
                "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                return_value=self._ALLOW_DISPATCH_DECISION,
            ),
            # The shared slash-command waist runs first and must fall through
            # so the ordinary reply path is what executes.
            patch(
                "server_modules.agent_command_dispatcher.dispatch_command",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "server_modules.personal_channels_service.gateway_protocol_service.dispatch_channel_outbound",
                new=dispatch_mock,
                create=True,
            ),
            patch("server_modules.personal_channels_service.security_audit_service.emit_security_audit_event"),
        ]
        if reply is not None:
            spec_patches.insert(
                2,
                patch(
                    "server_modules.personal_channels_service.personal_channel_sage_bridge_service"
                    ".build_personal_channel_reply_async",
                    new=AsyncMock(return_value=reply),
                ),
            )
        return spec_patches

    async def _deliver(self, *, channel_key: str, reply, dispatch_mock, inbound_suffix: str, inbound_extra=None):
        spec = personal_channels_service.LOCAL_BRIDGE_PERSONAL_CHANNELS[channel_key]
        external_message_id = f"{channel_key}-{inbound_suffix}"
        inbound = {"external_message_id": external_message_id, "remote_jid": "contact-1"}
        inbound.update(inbound_extra or {})
        patches = self._delivery_patches(reply=reply, dispatch_mock=dispatch_mock)
        for entered in patches:
            entered.start()
        try:
            return await personal_channels_service._deliver_local_bridge_personal_reply(
                gateway_id="gw-media-1",
                registration=self.registration,
                inbound=inbound,
                remote_jid="contact-1",
                external_message_id=external_message_id,
                text="send me that fox picture",
                push_name="Mansur",
                duplicate=False,
                channel_key=channel_key,
                provider=str(spec["provider"]),
                label=str(spec["label"]),
                agent_id="agent-media",
            )
        finally:
            for entered in reversed(patches):
                entered.stop()

    async def test_auto_reply_carries_media_to_dispatch(self) -> None:
        for channel_key in _cut_over_channel_keys():
            with self.subTest(channel_key=channel_key):
                dispatch_mock = AsyncMock(return_value={"external_message_id": f"{channel_key}-out-1"})
                result = await self._deliver(
                    channel_key=channel_key,
                    reply={"text": "Here's the fox.", "source": "sage", "media": [self._MEDIA_ITEM]},
                    dispatch_mock=dispatch_mock,
                    inbound_suffix="in-1",
                )
                dispatch_mock.assert_awaited_once()
                self.assertEqual(dispatch_mock.call_args.kwargs["media"], [self._MEDIA_ITEM])
                self.assertEqual(result["outbound"]["status"], "delivered")

    async def test_media_only_reply_still_dispatches(self) -> None:
        """Empty text + a queued attachment must NOT be treated as
        "the agent returned no reply" -- that pre-existing skip check gated
        purely on reply["text"], which would have silently dropped a
        legitimate media-only turn (e.g. "just send the photo back")."""
        for channel_key in _cut_over_channel_keys():
            with self.subTest(channel_key=channel_key):
                dispatch_mock = AsyncMock(return_value={"external_message_id": f"{channel_key}-out-2"})
                result = await self._deliver(
                    channel_key=channel_key,
                    reply={"text": "", "source": "sage", "media": [self._MEDIA_ITEM]},
                    dispatch_mock=dispatch_mock,
                    inbound_suffix="in-2",
                )
                dispatch_mock.assert_awaited_once()
                self.assertEqual(dispatch_mock.call_args.kwargs["media"], [self._MEDIA_ITEM])
                self.assertEqual(dispatch_mock.call_args.kwargs["text"], "")
                self.assertEqual(result["outbound"]["status"], "delivered")

    async def test_truly_empty_reply_still_skipped(self) -> None:
        """Unchanged behavior: no text AND no media means genuinely no
        reply -- still skipped, not dispatched."""
        for channel_key in _cut_over_channel_keys():
            with self.subTest(channel_key=channel_key):
                dispatch_mock = AsyncMock(side_effect=AssertionError("must not dispatch an empty reply"))
                result = await self._deliver(
                    channel_key=channel_key,
                    reply={"text": "", "source": "sage"},
                    dispatch_mock=dispatch_mock,
                    inbound_suffix="in-3",
                )
                dispatch_mock.assert_not_awaited()
                self.assertIsNone(result["outbound"])

    async def test_idempotent_replay_still_carries_media(self) -> None:
        """If a PRE-EXISTING pending outbound row is found (e.g. a retry after
        a crash between create and dispatch), the delivery path never rebuilds
        `reply` -- media must be recovered from the stored row's metadata, not
        a `reply` variable that is out of scope on that path."""
        for channel_key in _cut_over_channel_keys():
            with self.subTest(channel_key=channel_key):
                external_message_id = f"{channel_key}-in-4"
                idempotency_key = f"{channel_key}:{external_message_id}"
                personal_channels_repository.create_or_get_outbound_message(
                    gateway_id="gw-media-1",
                    channel_key=channel_key,
                    idempotency_key=idempotency_key,
                    remote_jid="contact-1",
                    text="Here's the fox.",
                    reply_to_external_message_id=external_message_id,
                    metadata={"reply_source": "sage", "media": [self._MEDIA_ITEM]},
                )
                dispatch_mock = AsyncMock(return_value={"external_message_id": f"{channel_key}-out-4"})
                # reply=None: no build_personal_channel_reply_async patch at
                # all, so if the replay path ever started rebuilding the reply
                # this test would reach the real builder and fail loudly
                # instead of quietly passing on a mock.
                result = await self._deliver(
                    channel_key=channel_key,
                    reply=None,
                    dispatch_mock=dispatch_mock,
                    inbound_suffix="in-4",
                    inbound_extra={"reply_idempotency_key": idempotency_key},
                )
                dispatch_mock.assert_awaited_once()
                self.assertEqual(dispatch_mock.call_args.kwargs["media"], [self._MEDIA_ITEM])
                self.assertEqual(result["outbound"]["status"], "delivered")


if __name__ == "__main__":
    unittest.main()

"""Tests for the group/mention/reply backend safety-net gate on
personal_channels_service.handle_cloud_channel_inbound (Stage 2 — the
cloud-session-manager ingestion path for telegram_personal).

Context: this path is a SECOND, separate ingestion path from the Gateway
handlers covered by test_personal_channel_group_gate.py
(_handle_telegram_gateway_channel_inbound /
_handle_whatsapp_gateway_channel_inbound /
_handle_local_bridge_gateway_channel_inbound), all three of which already
carry a "Gateway-side filter is primary, this is a backend safety net" group
gate. handle_cloud_channel_inbound had no such safety net at all.

Investigation found the wire payload actually sent to this function today
(built by cloud-session-manager/src/telegram/hmac.js::buildSignedInbound)
never includes is_group/is_mentioned/is_reply_to_sage — the upstream Node
service (cloud-session-manager/src/telegram/inbound-handler.js:79-123) already
gates unaddressed group messages itself and strips those fields before
forwarding. So the gate added here is presently a no-op in production (every
real message today looks like a DM to this function) but closes the
defense-in-depth gap documented on handle_cloud_channel_inbound: if the
upstream JS gate ever regresses, or a future producer of this same webhook
omits it, this backend gate is what stops an unaddressed group message from
reaching a live agent turn — and it must never mistake "fields absent" (the
100% shape today) for "must be blocked".

UPDATED 2026-07-23 (group_policy build): the gate itself is now
personal_channels_service._enforce_group_policy — the SAME shared resolver
every other personal-channel handler uses — and, like those, its
requireMention default flipped to OFF (Ruling A, "see-and-decide"; see
personal_channels_service.py's DEFAULT_REQUIRE_MENTION doc comment and
test_personal_channel_group_gate.py's file docstring for the full history).
An unaddressed group message is therefore no longer hard-blocked by
default here either — see the updated test below.

UPDATED (Gate 1 fix, cloud-audit-defects): handle_cloud_channel_inbound now
also runs the dmPolicy gate (_enforce_dm_policy) — see
test_cloud_channel_dm_policy.py for that gate's own tests, including the
security defect it closes (this path previously had NO dm gate at all, so
any stranger who messaged the owner's cloud-hosted Telegram session reached
a live agent turn unconditionally). That gate is orthogonal to the group
gate under test here, so it's mocked to ALLOW in this file's setUp — same
convention test_personal_channel_group_gate.py's GroupContextThreadingTests
already uses for the Gateway handlers. This file also now touches
personal_channels_repository (the dm gate's existing_state lookup), so
setUp gives it an isolated tmp sqlite DB rather than the real
~/.empyralis/state one.
"""

from __future__ import annotations

import importlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from server_modules import personal_channels_service


class CloudChannelGroupGateTests(unittest.IsolatedAsyncioTestCase):
    """Through the LIVE personal_channels_service.handle_cloud_channel_inbound."""

    def setUp(self) -> None:
        global personal_channels_service, personal_channels_repository
        personal_channels_service = importlib.import_module("server_modules.personal_channels_service")
        personal_channels_repository = importlib.import_module("server_modules.personal_channels_repository")
        # Hermetic regardless of the runner's environment — this module-level
        # flag is read at call time, not import time.
        self.enabled_patcher = patch.object(personal_channels_service, "_CLOUD_SESSION_MANAGER_ENABLED", True)
        self.enabled_patcher.start()

        self.tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(self.tmpdir.name) / "personal-channels.sqlite3"
        personal_channels_repository.init_personal_channels_db(db_path)
        self.db_patcher = patch.object(personal_channels_repository, "PERSONAL_CHANNELS_DB_FILE", db_path)
        self.db_patcher.start()

        # dmPolicy is orthogonal to the group gate under test in this file
        # (see test_cloud_channel_dm_policy.py for that gate's own tests) —
        # mocked to ALLOW here purely so a message that passes the group
        # gate actually reaches the bridge call these tests inspect,
        # isolating ONE thing: group/mention gating.
        self.dm_allow_patcher = patch(
            "server_modules.personal_channels_service._enforce_dm_policy",
            new=AsyncMock(return_value={
                "allowed": True, "mode": "open", "sender_id": "111222",
                "is_owner": False, "system_reply": None, "config_changed": False,
            }),
        )
        self.dm_allow_patcher.start()

    def tearDown(self) -> None:
        self.dm_allow_patcher.stop()
        self.db_patcher.stop()
        self.tmpdir.cleanup()
        self.enabled_patcher.stop()

    async def test_unaddressed_group_message_is_seen_by_default_see_and_decide(self) -> None:
        """UPDATED 2026-07-23: with requireMention defaulting OFF, an
        unaddressed group message is no longer hard-blocked here — it
        reaches the model (build_telegram_personal_reply_async is called
        with is_group=True), which is then free to reply or emit [SILENT]
        by its own judgment (Ruling A)."""
        with (
            patch(
                "server_modules.personal_channels_service.personal_channel_sage_bridge_service.build_telegram_personal_reply_async",
                new=AsyncMock(return_value={"text": "[SILENT]", "source": "sage"}),
            ) as build_reply_mock,
            patch(
                "server_modules.personal_channels_service.dispatch_cloud_channel_outbound",
                new=AsyncMock(side_effect=AssertionError("[SILENT] must never be dispatched")),
            ),
        ):
            result = await personal_channels_service.handle_cloud_channel_inbound(
                session_id="csm-sess-1",
                channel_key="telegram_personal",
                workspace_id="default",
                message={
                    "external_message_id": "csm-group-1",
                    "sender_id": "111222",
                    "sender_name": "Family Member",
                    "text": "what time is dinner",
                    "received_at": "2026-07-19T00:00:00Z",
                    "is_group": True,
                    "is_mentioned": False,
                    "is_reply_to_sage": False,
                },
            )
        build_reply_mock.assert_awaited_once()
        call_kwargs = build_reply_mock.call_args.kwargs
        self.assertTrue(call_kwargs.get("is_group"))
        self.assertNotEqual(result.get("reason"), "group_no_mention")

    async def test_mentioned_group_message_is_not_gated(self) -> None:
        with (
            patch(
                "server_modules.personal_channels_service.personal_channel_sage_bridge_service.build_telegram_personal_reply_async",
                new=AsyncMock(return_value={"text": "Dinner's at 7.", "source": "sage"}),
            ) as build_reply_mock,
            patch(
                "server_modules.personal_channels_service.dispatch_cloud_channel_outbound",
                new=AsyncMock(return_value={"ok": True, "status": 200, "message_id": "out-1"}),
            ) as dispatch_mock,
        ):
            result = await personal_channels_service.handle_cloud_channel_inbound(
                session_id="csm-sess-2",
                channel_key="telegram_personal",
                workspace_id="default",
                message={
                    "external_message_id": "csm-group-2",
                    "sender_id": "111222",
                    "sender_name": "Owner",
                    "text": "@sage_owner what time is dinner",
                    "received_at": "2026-07-19T00:00:00Z",
                    "is_group": True,
                    "is_mentioned": True,
                    "is_reply_to_sage": False,
                },
            )
        build_reply_mock.assert_awaited_once()
        dispatch_mock.assert_awaited_once()
        self.assertEqual(result.get("status"), "replied")

    async def test_reply_to_sage_group_message_is_not_gated(self) -> None:
        with (
            patch(
                "server_modules.personal_channels_service.personal_channel_sage_bridge_service.build_telegram_personal_reply_async",
                new=AsyncMock(return_value={"text": "Sounds good.", "source": "sage"}),
            ) as build_reply_mock,
            patch(
                "server_modules.personal_channels_service.dispatch_cloud_channel_outbound",
                new=AsyncMock(return_value={"ok": True, "status": 200, "message_id": "out-2"}),
            ) as dispatch_mock,
        ):
            result = await personal_channels_service.handle_cloud_channel_inbound(
                session_id="csm-sess-3",
                channel_key="telegram_personal",
                workspace_id="default",
                message={
                    "external_message_id": "csm-group-3",
                    "sender_id": "111222",
                    "sender_name": "Owner",
                    "text": "sounds good",
                    "received_at": "2026-07-19T00:00:00Z",
                    "is_group": True,
                    "is_mentioned": False,
                    "is_reply_to_sage": True,
                },
            )
        build_reply_mock.assert_awaited_once()
        dispatch_mock.assert_awaited_once()
        self.assertEqual(result.get("status"), "replied")

    async def test_direct_message_is_unaffected_by_the_group_gate(self) -> None:
        """A plain DM with is_group explicitly False must reach the bridge
        exactly as before this fix."""
        with (
            patch(
                "server_modules.personal_channels_service.personal_channel_sage_bridge_service.build_telegram_personal_reply_async",
                new=AsyncMock(return_value={"text": "On it.", "source": "sage"}),
            ) as build_reply_mock,
            patch(
                "server_modules.personal_channels_service.dispatch_cloud_channel_outbound",
                new=AsyncMock(return_value={"ok": True, "status": 200, "message_id": "out-3"}),
            ) as dispatch_mock,
        ):
            result = await personal_channels_service.handle_cloud_channel_inbound(
                session_id="csm-sess-4",
                channel_key="telegram_personal",
                workspace_id="default",
                message={
                    "external_message_id": "csm-dm-1",
                    "sender_id": "111222",
                    "sender_name": "Owner",
                    "text": "remind me to call mom",
                    "received_at": "2026-07-19T00:00:00Z",
                    "is_group": False,
                    "is_mentioned": False,
                    "is_reply_to_sage": False,
                },
            )
        build_reply_mock.assert_awaited_once()
        dispatch_mock.assert_awaited_once()
        self.assertEqual(result.get("status"), "replied")

    async def test_mentioned_group_message_threads_group_context_into_the_bridge(self) -> None:
        """FIX (systemic group-context threading): handle_cloud_channel_inbound
        computes is_group for its OWN gate (see
        test_mentioned_group_message_is_not_gated above) but used to stop
        there — the actual call into build_telegram_personal_reply_async
        never received is_group/chat_label at all, so a group message that
        correctly passed the gate still reached the model with ZERO group
        signal, rendered exactly like a 1:1 DM (the same "family group" bug
        class _owner_provenance_message / _personal_channel_guard_metadata
        were fixed for on the Gateway handlers — see
        test_personal_channel_group_gate.py and
        test_personal_channel_sage_bridge_service.py's
        OwnerAwareProvenanceTests). Proves the threading itself, independent
        of test_mentioned_group_message_is_not_gated's gating-only
        assertion. chat_title is speculative (see this module's docstring:
        the live wire never sends it today) but the plumbing must exist for
        when it does."""
        with (
            patch(
                "server_modules.personal_channels_service.personal_channel_sage_bridge_service.build_telegram_personal_reply_async",
                new=AsyncMock(return_value={"text": "Dinner's at 7.", "source": "sage"}),
            ) as build_reply_mock,
            patch(
                "server_modules.personal_channels_service.dispatch_cloud_channel_outbound",
                new=AsyncMock(return_value={"ok": True, "status": 200, "message_id": "out-6"}),
            ),
        ):
            await personal_channels_service.handle_cloud_channel_inbound(
                session_id="csm-sess-6",
                channel_key="telegram_personal",
                workspace_id="default",
                message={
                    "external_message_id": "csm-group-6",
                    "sender_id": "111222",
                    "sender_name": "Owner",
                    "text": "@sage_owner what time is dinner",
                    "received_at": "2026-07-19T00:00:00Z",
                    "is_group": True,
                    "is_mentioned": True,
                    "is_reply_to_sage": False,
                    "chat_title": "Family",
                },
            )
        build_reply_mock.assert_awaited_once()
        call_kwargs = build_reply_mock.call_args.kwargs
        self.assertTrue(call_kwargs.get("is_group"))
        self.assertEqual(call_kwargs.get("chat_label"), "Family")

    async def test_direct_message_threads_is_group_false_and_no_chat_label(self) -> None:
        """Regression guard for the fix above: the DM path (is_group
        absent — the 100% real-world wire shape today, per this module's
        own docstring) must keep threading is_group=False/chat_label=None
        now that the plumbing exists — never start claiming every DM is a
        group."""
        with (
            patch(
                "server_modules.personal_channels_service.personal_channel_sage_bridge_service.build_telegram_personal_reply_async",
                new=AsyncMock(return_value={"text": "On it.", "source": "sage"}),
            ) as build_reply_mock,
            patch(
                "server_modules.personal_channels_service.dispatch_cloud_channel_outbound",
                new=AsyncMock(return_value={"ok": True, "status": 200, "message_id": "out-7"}),
            ),
        ):
            await personal_channels_service.handle_cloud_channel_inbound(
                session_id="csm-sess-7",
                channel_key="telegram_personal",
                workspace_id="default",
                message={
                    "external_message_id": "csm-dm-7",
                    "sender_id": "111222",
                    "sender_name": "Owner",
                    "text": "remind me to call mom",
                    "received_at": "2026-07-19T00:00:00Z",
                },
            )
        build_reply_mock.assert_awaited_once()
        call_kwargs = build_reply_mock.call_args.kwargs
        self.assertFalse(call_kwargs.get("is_group"))
        self.assertIsNone(call_kwargs.get("chat_label"))

    async def test_group_fields_absent_entirely_defaults_to_ungated(self) -> None:
        """The real-world shape every cloud-session-manager webhook call has
        today (see buildSignedInbound in cloud-session-manager/src/telegram/
        hmac.js): no is_group/is_mentioned/is_reply_to_sage keys at all.
        Must default to ungated, not silently dropped — the group gate is
        opt-in per-message via a truthy is_group, not opt-out."""
        with (
            patch(
                "server_modules.personal_channels_service.personal_channel_sage_bridge_service.build_telegram_personal_reply_async",
                new=AsyncMock(return_value={"text": "On it.", "source": "sage"}),
            ) as build_reply_mock,
            patch(
                "server_modules.personal_channels_service.dispatch_cloud_channel_outbound",
                new=AsyncMock(return_value={"ok": True, "status": 200, "message_id": "out-4"}),
            ) as dispatch_mock,
        ):
            result = await personal_channels_service.handle_cloud_channel_inbound(
                session_id="csm-sess-5",
                channel_key="telegram_personal",
                workspace_id="default",
                message={
                    "external_message_id": "csm-nogroupfield-1",
                    "sender_id": "111222",
                    "sender_name": "Owner",
                    "text": "hello",
                    "received_at": "2026-07-19T00:00:00Z",
                },
            )
        build_reply_mock.assert_awaited_once()
        dispatch_mock.assert_awaited_once()
        self.assertEqual(result.get("status"), "replied")


if __name__ == "__main__":
    unittest.main()

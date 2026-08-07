"""Tests for the dmPolicy (Gate 1) backend gate on
personal_channels_service.handle_cloud_channel_inbound (Stage 2 — the
cloud-session-manager ingestion path for telegram_personal).

THE DEFECT this closes: handle_cloud_channel_inbound is a second, default-
enabled (_CLOUD_SESSION_MANAGER_ENABLED defaults "true") producer of
Telegram Personal traffic, parallel to the Gateway WebSocket path covered by
test_personal_channels_dm_policy.py. Unlike every Gateway handler
(_handle_telegram_gateway_channel_inbound / _handle_whatsapp_gateway_channel_inbound
/ _handle_local_bridge_gateway_channel_inbound), it had NO _enforce_dm_policy
call anywhere — workspace validation, a (real, since aaadcfdf4) group gate,
command dispatch, and a live Sage reply, with zero identity check on the
sender. Any stranger who messaged the owner's cloud-hosted Telegram session
reached a live agent turn unconditionally — the exact incident this whole
effort exists to prevent, on a second ingestion path Gate 1 hardening never
touched.

ENFORCEABILITY ON THIS WIRE (see handle_cloud_channel_inbound's own dmPolicy
gate comment for the full reasoning): the signed message body this path
receives never carries is_self_chat or a numeric linked user id, and no
configure/claim step ever resolves a real agent_id for a "cloud:<session>"
gateway_id. So _enforce_dm_policy's unresolved-identity fallback
(owner_only, empty allowlist) is what actually governs this path today —
every sender, including the genuine owner, is blocked unless a prior
personal_channel_telegram_states row already links their sender_id to this
exact "cloud:<session_id>" gateway_id (which nothing today writes, but the
gate itself honors correctly if a future claim step ever does — the
"owner message reaches the model" test below proves that plumbing works end
to end via the real _enforce_dm_policy / _is_owner_message /
resolve_sender_identity call chain, not a mock). That is the intended
fail-closed posture, not a bug: passing every sender through unauthenticated
(the pre-fix behavior) is the actual security gap.
"""

from __future__ import annotations

import importlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from server_modules import personal_channels_service


class CloudChannelDmPolicyTests(unittest.IsolatedAsyncioTestCase):
    """Through the LIVE personal_channels_service.handle_cloud_channel_inbound
    — real sqlite repository (isolated tmp DB), mocked Sage bridge + cloud
    HTTP dispatch."""

    def setUp(self) -> None:
        global personal_channels_service, personal_channels_repository
        personal_channels_service = importlib.import_module("server_modules.personal_channels_service")
        personal_channels_repository = importlib.import_module("server_modules.personal_channels_repository")

        self.enabled_patcher = patch.object(personal_channels_service, "_CLOUD_SESSION_MANAGER_ENABLED", True)
        self.enabled_patcher.start()

        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "personal-channels.sqlite3"
        personal_channels_repository.init_personal_channels_db(self.db_path)
        self.db_patcher = patch.object(personal_channels_repository, "PERSONAL_CHANNELS_DB_FILE", self.db_path)
        self.db_patcher.start()

    def tearDown(self) -> None:
        self.db_patcher.stop()
        self.enabled_patcher.stop()
        self.tmpdir.cleanup()

    async def test_stranger_dm_is_blocked_and_never_reaches_the_model(self) -> None:
        """An unauthenticated stranger's plain DM to the cloud-hosted
        session must never reach execute_sage_turn (proxied here, as every
        other gate test in this codebase does, by the Sage bridge builder
        call — the boundary past which a live agent turn is generated) nor
        get any reply dispatched."""
        with (
            patch(
                "server_modules.personal_channels_service.personal_channel_sage_bridge_service.build_telegram_personal_reply_async",
                new=AsyncMock(side_effect=AssertionError("must never reach the model")),
            ) as build_reply_mock,
            patch(
                "server_modules.personal_channels_service.dispatch_cloud_channel_outbound",
                new=AsyncMock(side_effect=AssertionError("must not dispatch a reply to a blocked stranger")),
            ) as dispatch_mock,
            patch("server_modules.personal_channels_service.security_audit_service.emit_security_audit_event"),
        ):
            result = await personal_channels_service.handle_cloud_channel_inbound(
                session_id="csm-dm-stranger-1",
                channel_key="telegram_personal",
                workspace_id="default",
                message={
                    "external_message_id": "csm-stranger-1",
                    "sender_id": "999888777",
                    "sender_name": "Rando",
                    "text": "hi, who is this?",
                    "received_at": "2026-08-07T00:00:00Z",
                },
            )
        build_reply_mock.assert_not_called()
        dispatch_mock.assert_not_called()
        self.assertTrue(result.get("blocked"))
        self.assertEqual(result.get("reason"), "dm_policy")
        self.assertEqual(result["policy"]["mode"], "owner_only")
        self.assertFalse(result["policy"]["is_owner"])

    async def test_stranger_in_addressed_group_still_blocked_by_dm_policy(self) -> None:
        """Proves ordering: a message that PASSES the group/mention gate
        (is_group + is_mentioned, see test_cloud_channel_group_gate.py) must
        still be blocked by the dmPolicy gate that runs right after it —
        the two gates are independent, and neither substitutes for the
        other. Before this fix, an addressed group message from a stranger
        would have sailed straight through to the model since no dm gate
        existed at all."""
        with (
            patch(
                "server_modules.personal_channels_service.personal_channel_sage_bridge_service.build_telegram_personal_reply_async",
                new=AsyncMock(side_effect=AssertionError("must never reach the model")),
            ) as build_reply_mock,
            patch(
                "server_modules.personal_channels_service.dispatch_cloud_channel_outbound",
                new=AsyncMock(side_effect=AssertionError("must not dispatch")),
            ) as dispatch_mock,
            patch("server_modules.personal_channels_service.security_audit_service.emit_security_audit_event"),
        ):
            result = await personal_channels_service.handle_cloud_channel_inbound(
                session_id="csm-dm-stranger-2",
                channel_key="telegram_personal",
                workspace_id="default",
                message={
                    "external_message_id": "csm-stranger-2",
                    "sender_id": "999888777",
                    "sender_name": "Rando",
                    "text": "@sage_owner what time is dinner",
                    "received_at": "2026-08-07T00:00:00Z",
                    "is_group": True,
                    "is_mentioned": True,
                    "is_reply_to_sage": False,
                },
            )
        build_reply_mock.assert_not_called()
        dispatch_mock.assert_not_called()
        self.assertTrue(result.get("blocked"))
        self.assertEqual(result.get("reason"), "dm_policy")

    async def test_owner_message_with_linked_identity_reaches_the_model(self) -> None:
        """The one way an "authorized" sender can pass this gate today: a
        prior personal_channel_telegram_states row already links this
        exact "cloud:<session_id>" gateway_id's sender to the owner (see
        this module's docstring — nothing writes this row today, but the
        gate must honor it correctly if something ever does). Exercises the
        real _enforce_dm_policy -> _is_owner_message -> resolve_sender_identity
        chain end to end, not a mock, proving the newly-wired gate's ALLOW
        path is genuinely reachable and correctly threads is_owner through
        to the Sage bridge call."""
        session_id = "csm-dm-owner-1"
        personal_channels_repository.upsert_telegram_state(
            gateway_id=f"cloud:{session_id}",
            tenant_id="default",
            workspace_id="default",
            user_id="owner-1",
            channel_key="telegram_personal",
            agent_id="",
            provider="telegram_gramjs",
            status="connected",
            linked_user_id="555444333",
        )
        with (
            patch(
                "server_modules.personal_channels_service.personal_channel_sage_bridge_service.build_telegram_personal_reply_async",
                new=AsyncMock(return_value={"text": "On it.", "source": "sage"}),
            ) as build_reply_mock,
            patch(
                "server_modules.personal_channels_service.dispatch_cloud_channel_outbound",
                new=AsyncMock(return_value={"ok": True, "status": 200, "message_id": "out-1"}),
            ) as dispatch_mock,
            patch("server_modules.personal_channels_service.security_audit_service.emit_security_audit_event"),
        ):
            result = await personal_channels_service.handle_cloud_channel_inbound(
                session_id=session_id,
                channel_key="telegram_personal",
                workspace_id="default",
                message={
                    "external_message_id": "csm-owner-1",
                    "sender_id": "555444333",
                    "sender_name": "Owner",
                    "text": "remind me to call mom",
                    "received_at": "2026-08-07T00:00:00Z",
                },
            )
        build_reply_mock.assert_awaited_once()
        call_kwargs = build_reply_mock.call_args.kwargs
        self.assertTrue(call_kwargs.get("is_owner"))
        dispatch_mock.assert_awaited_once()
        self.assertEqual(result.get("status"), "replied")
        self.assertFalse(result.get("blocked", False))

    async def test_pairing_mode_challenge_is_dispatched_via_cloud_outbound(self) -> None:
        """A blocked sender under pairing mode still gets its one-time
        system challenge — proves the cloud path's block-handling correctly
        adapts _enforce_dm_policy's system_reply to this path's HTTP
        dispatch (dispatch_cloud_channel_outbound) rather than the Gateway
        WebSocket dispatch _handle_dm_policy_blocked itself uses, since the
        two paths don't share a transport."""
        allow_decision = {
            "allowed": False, "mode": "pairing", "sender_id": "999888777",
            "is_owner": False, "system_reply": "This Telegram number is a private assistant line.",
            "config_changed": True,
        }
        with (
            patch(
                "server_modules.personal_channels_service._enforce_dm_policy",
                new=AsyncMock(return_value=allow_decision),
            ),
            patch(
                "server_modules.personal_channels_service.personal_channel_sage_bridge_service.build_telegram_personal_reply_async",
                new=AsyncMock(side_effect=AssertionError("must never reach the model")),
            ),
            patch(
                "server_modules.personal_channels_service.dispatch_cloud_channel_outbound",
                new=AsyncMock(return_value={"ok": True, "status": 200, "message_id": "pairing-1"}),
            ) as dispatch_mock,
            patch("server_modules.personal_channels_service.security_audit_service.emit_security_audit_event"),
        ):
            result = await personal_channels_service.handle_cloud_channel_inbound(
                session_id="csm-dm-pairing-1",
                channel_key="telegram_personal",
                workspace_id="default",
                message={
                    "external_message_id": "csm-pairing-1",
                    "sender_id": "999888777",
                    "sender_name": "Rando",
                    "text": "hello?",
                    "received_at": "2026-08-07T00:00:00Z",
                },
            )
        dispatch_mock.assert_awaited_once()
        dispatch_kwargs = dispatch_mock.call_args.kwargs
        self.assertEqual(dispatch_kwargs.get("text"), allow_decision["system_reply"])
        self.assertTrue(result.get("blocked"))


if __name__ == "__main__":
    unittest.main()

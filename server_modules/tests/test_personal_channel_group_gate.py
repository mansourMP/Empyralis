"""Tests for the group/mention/reply gate on personal-channel gateway
inbound handlers.

Fix: agents must stay SILENT in a GROUP chat unless actually addressed
(@mentioned, or replying to a message Sage itself sent) — mirroring
WhatsApp's existing, working pattern. Before this fix, Telegram
(_handle_telegram_gateway_channel_inbound) and the local-bridge channels
(_handle_local_bridge_gateway_channel_inbound — Signal/iMessage/WeChat) had
NO such gate: every message in every group the linked account belonged to
triggered a full agent turn and reply (the "family group bug"). DMs and
owner self-chat are never affected — the gate applies to groups only.

This is a SEPARATE, earlier gate than dmPolicy (_enforce_dm_policy — see
test_personal_channels_dm_policy.py): the group gate decides WHETHER a
group message is addressed to Sage at all; dmPolicy decides WHETHER the
(now-addressed) sender is authorized to get a reply. Both must hold
independently — several tests below exist specifically to prove neither
gate can be mistaken for, or substitute for, the other.

Harness mirrors test_personal_channels_dm_policy.py's
DmPolicyInboundIntegrationTests: real sqlite repository, mocked rust kernel
+ gateway dispatch, through the LIVE gateway inbound handlers (not the
gate's boolean condition tested in isolation).
"""

from __future__ import annotations

import importlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from server_modules import personal_channels_service, personal_channels_repository


_ALLOW_DISPATCH_DECISION = {
    "ok": True,
    "decision": "allow",
    "reason": "gateway_service_operation_allowed",
    "operation": "protocol_route",
    "next_action": "dispatch_gateway_operation",
}


class TelegramGroupGateTests(unittest.IsolatedAsyncioTestCase):
    """Through the LIVE _handle_telegram_gateway_channel_inbound — real
    sqlite repository, mocked rust kernel + gateway dispatch."""

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
            "gateway_id": "gw-group-1",
            "workspace_id": "default",
            "tenant_id": "tenant-1",
            "device_trust_state": "trusted",
            "active_session_id": "sess-1",
        }

    def tearDown(self) -> None:
        self.db_patcher.stop()
        self.tmpdir.cleanup()

    def _link_owner(self, sender_jid: str) -> None:
        """Establishes sender_jid as the channel's owner identity. Telegram
        has no is_self_chat signal (unlike WhatsApp) — ownership there is
        purely sender_jid == the persisted linked_user_id (see
        _channel_owner_linked_id). Mirrors
        test_personal_channels_dm_policy.py's
        test_owner_only_allows_sender_matching_linked_identity exactly."""
        personal_channels_repository.upsert_telegram_state(
            gateway_id="gw-group-1",
            tenant_id="tenant-1",
            workspace_id="default",
            user_id="",
            channel_key=personal_channels_service.TELEGRAM_PERSONAL_CHANNEL_KEY,
            agent_id="",
            provider=personal_channels_service.TELEGRAM_PERSONAL_PROVIDER,
            status="connected",
            linked_user_id=sender_jid,
        )

    async def test_unaddressed_group_message_is_ignored_and_never_dispatched(self) -> None:
        with (
            patch(
                "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                return_value=_ALLOW_DISPATCH_DECISION,
            ),
            patch(
                "server_modules.personal_channels_service.personal_channel_sage_bridge_service.build_telegram_personal_reply"
            ) as build_reply_mock,
            patch(
                "server_modules.personal_channels_service.gateway_protocol_service.dispatch_channel_outbound",
                new=AsyncMock(side_effect=AssertionError("must not dispatch for an unaddressed group message")),
                create=True,
            ),
        ):
            result = await personal_channels_service._handle_telegram_gateway_channel_inbound(
                gateway_id="gw-group-1",
                registration=self.registration,
                payload={
                    "channel_key": personal_channels_service.TELEGRAM_PERSONAL_CHANNEL_KEY,
                    "provider": personal_channels_service.TELEGRAM_PERSONAL_PROVIDER,
                    "message": {
                        "external_message_id": "tg-group-1",
                        "remote_jid": "-100555",
                        "sender_jid": "111222",
                        "push_name": "Family Member",
                        "text": "what time is dinner",
                        "from_me": False,
                        "is_group": True,
                        "is_mentioned": False,
                        "is_reply_to_sage": False,
                    },
                },
            )
        build_reply_mock.assert_not_called()
        self.assertTrue(result.get("ignored"))
        self.assertEqual(result.get("reason"), "group_no_mention")

        # This really is an EARLY return (before agent_id resolution / the
        # repository is even touched, mirroring the from_me check right
        # above it) — no inbound row should have been recorded.
        existing = personal_channels_repository.get_telegram_state(
            "gw-group-1", channel_key=personal_channels_service.TELEGRAM_PERSONAL_CHANNEL_KEY, agent_id="",
        )
        self.assertIsNone(existing)

    async def test_mentioned_group_message_from_the_owner_is_dispatched(self) -> None:
        self._link_owner("111222")
        with (
            patch(
                "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                return_value=_ALLOW_DISPATCH_DECISION,
            ),
            patch(
                "server_modules.personal_channels_service.personal_channel_sage_bridge_service.build_telegram_personal_reply",
                return_value={"text": "Dinner's at 7.", "source": "sage"},
            ) as build_reply_mock,
            patch(
                "server_modules.personal_channels_service.gateway_protocol_service.dispatch_channel_outbound",
                new=AsyncMock(return_value={"external_message_id": "tg-out-1"}),
                create=True,
            ) as dispatch_mock,
        ):
            result = await personal_channels_service._handle_telegram_gateway_channel_inbound(
                gateway_id="gw-group-1",
                registration=self.registration,
                payload={
                    "channel_key": personal_channels_service.TELEGRAM_PERSONAL_CHANNEL_KEY,
                    "provider": personal_channels_service.TELEGRAM_PERSONAL_PROVIDER,
                    "message": {
                        "external_message_id": "tg-group-2",
                        "remote_jid": "-100555",
                        "sender_jid": "111222",
                        "push_name": "Owner",
                        "text": "@sage_owner what time is dinner",
                        "from_me": False,
                        "is_group": True,
                        "is_mentioned": True,
                        "is_reply_to_sage": False,
                    },
                },
            )
        build_reply_mock.assert_called_once()
        dispatch_mock.assert_awaited_once()
        self.assertFalse(result.get("blocked", False))
        self.assertEqual(result["outbound"]["status"], "delivered")

    async def test_reply_to_sage_group_message_from_the_owner_is_dispatched(self) -> None:
        self._link_owner("111222")
        with (
            patch(
                "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                return_value=_ALLOW_DISPATCH_DECISION,
            ),
            patch(
                "server_modules.personal_channels_service.personal_channel_sage_bridge_service.build_telegram_personal_reply",
                return_value={"text": "Sounds good.", "source": "sage"},
            ) as build_reply_mock,
            patch(
                "server_modules.personal_channels_service.gateway_protocol_service.dispatch_channel_outbound",
                new=AsyncMock(return_value={"external_message_id": "tg-out-2"}),
                create=True,
            ) as dispatch_mock,
        ):
            result = await personal_channels_service._handle_telegram_gateway_channel_inbound(
                gateway_id="gw-group-1",
                registration=self.registration,
                payload={
                    "channel_key": personal_channels_service.TELEGRAM_PERSONAL_CHANNEL_KEY,
                    "provider": personal_channels_service.TELEGRAM_PERSONAL_PROVIDER,
                    "message": {
                        "external_message_id": "tg-group-3",
                        "remote_jid": "-100555",
                        "sender_jid": "111222",
                        "push_name": "Owner",
                        "text": "sounds good",
                        "from_me": False,
                        "is_group": True,
                        "is_mentioned": False,
                        "is_reply_to_sage": True,
                    },
                },
            )
        build_reply_mock.assert_called_once()
        dispatch_mock.assert_awaited_once()
        self.assertFalse(result.get("blocked", False))

    async def test_mentioned_group_message_from_a_stranger_is_still_blocked_by_dm_policy(self) -> None:
        """The group gate and dmPolicy are two SEPARATE, independently
        enforced checks — passing the mention gate must not bypass owner
        authorization. _enforce_dm_policy itself is mocked here (rather
        than relying on its real owner_only-default resolution, which
        depends on a live Rust control-plane service not available in this
        sandbox — see _load_agent_dm_policy_config's registry lookups; the
        SAME environment gap independently fails
        test_personal_channels_dm_policy.py's own
        DmPolicyGateUnitTests.test_owner_only_default_blocks_stranger here,
        confirming this isn't specific to this test) so this test
        deterministically isolates ONE thing: that the group gate, on
        seeing is_mentioned=True, hands off to dmPolicy at all instead of
        short-circuiting straight to a reply."""
        blocked_decision = {
            "allowed": False, "mode": "owner_only", "sender_id": "999999",
            "is_owner": False, "system_reply": None, "config_changed": False,
        }
        with (
            patch(
                "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                return_value=_ALLOW_DISPATCH_DECISION,
            ),
            patch(
                "server_modules.personal_channels_service._enforce_dm_policy",
                new=AsyncMock(return_value=blocked_decision),
            ) as dm_policy_mock,
            patch(
                "server_modules.personal_channels_service.personal_channel_sage_bridge_service.build_telegram_personal_reply"
            ) as build_reply_mock,
            patch(
                "server_modules.personal_channels_service.gateway_protocol_service.dispatch_channel_outbound",
                new=AsyncMock(side_effect=AssertionError("must not dispatch to a stranger even if mentioned")),
                create=True,
            ),
        ):
            result = await personal_channels_service._handle_telegram_gateway_channel_inbound(
                gateway_id="gw-group-1",
                registration=self.registration,
                payload={
                    "channel_key": personal_channels_service.TELEGRAM_PERSONAL_CHANNEL_KEY,
                    "provider": personal_channels_service.TELEGRAM_PERSONAL_PROVIDER,
                    "message": {
                        "external_message_id": "tg-group-4",
                        "remote_jid": "-100555",
                        "sender_jid": "999999",
                        "push_name": "Stranger",
                        "text": "@sage_owner hello",
                        "from_me": False,
                        "is_group": True,
                        "is_mentioned": True,
                        "is_reply_to_sage": False,
                    },
                },
            )
        dm_policy_mock.assert_awaited_once()
        build_reply_mock.assert_not_called()
        self.assertTrue(result.get("blocked"))
        # Blocked by dmPolicy (a DIFFERENT gate/reason), not mistaken for
        # (or masking) the group gate's own "group_no_mention" reason.
        self.assertNotEqual(result.get("reason"), "group_no_mention")
        self.assertIn("policy", result)

    async def test_direct_message_from_owner_always_dispatches_regardless_of_group_fields(self) -> None:
        self._link_owner("111222")
        with (
            patch(
                "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                return_value=_ALLOW_DISPATCH_DECISION,
            ),
            patch(
                "server_modules.personal_channels_service.personal_channel_sage_bridge_service.build_telegram_personal_reply",
                return_value={"text": "On it.", "source": "sage"},
            ) as build_reply_mock,
            patch(
                "server_modules.personal_channels_service.gateway_protocol_service.dispatch_channel_outbound",
                new=AsyncMock(return_value={"external_message_id": "tg-out-3"}),
                create=True,
            ) as dispatch_mock,
        ):
            result = await personal_channels_service._handle_telegram_gateway_channel_inbound(
                gateway_id="gw-group-1",
                registration=self.registration,
                payload={
                    "channel_key": personal_channels_service.TELEGRAM_PERSONAL_CHANNEL_KEY,
                    "provider": personal_channels_service.TELEGRAM_PERSONAL_PROVIDER,
                    "message": {
                        "external_message_id": "tg-dm-1",
                        "remote_jid": "111222",
                        "sender_jid": "111222",
                        "push_name": "Owner",
                        "text": "remind me to call mom",
                        "from_me": False,
                        "is_group": False,
                        "is_mentioned": False,
                        "is_reply_to_sage": False,
                    },
                },
            )
        build_reply_mock.assert_called_once()
        dispatch_mock.assert_awaited_once()
        self.assertFalse(result.get("blocked", False))

    async def test_group_fields_absent_entirely_defaults_to_ungated(self) -> None:
        """Backward compatibility: is_group/is_mentioned absent entirely
        (an older Gateway build) must default to ungated rather than
        silently dropped — the group gate is opt-in per-message via a
        truthy is_group, not opt-out."""
        self._link_owner("111222")
        with (
            patch(
                "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                return_value=_ALLOW_DISPATCH_DECISION,
            ),
            patch(
                "server_modules.personal_channels_service.personal_channel_sage_bridge_service.build_telegram_personal_reply",
                return_value={"text": "On it.", "source": "sage"},
            ) as build_reply_mock,
            patch(
                "server_modules.personal_channels_service.gateway_protocol_service.dispatch_channel_outbound",
                new=AsyncMock(return_value={"external_message_id": "tg-out-4"}),
                create=True,
            ) as dispatch_mock,
        ):
            result = await personal_channels_service._handle_telegram_gateway_channel_inbound(
                gateway_id="gw-group-1",
                registration=self.registration,
                payload={
                    "channel_key": personal_channels_service.TELEGRAM_PERSONAL_CHANNEL_KEY,
                    "provider": personal_channels_service.TELEGRAM_PERSONAL_PROVIDER,
                    "message": {
                        "external_message_id": "tg-nogroupfield-1",
                        "remote_jid": "111222",
                        "sender_jid": "111222",
                        "push_name": "Owner",
                        "text": "hello",
                        "from_me": False,
                    },
                },
            )
        build_reply_mock.assert_called_once()
        dispatch_mock.assert_awaited_once()
        self.assertFalse(result.get("blocked", False))


class LocalBridgeGroupGateTests(unittest.IsolatedAsyncioTestCase):
    """Through the LIVE _handle_local_bridge_gateway_channel_inbound —
    Signal/iMessage/WeChat share this one function. Proves the group gate
    is enforced BEFORE dmPolicy's own (separately, unconditionally
    blocking, per a pre-existing gap) check."""

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
            "gateway_id": "gw-group-2",
            "workspace_id": "default",
            "tenant_id": "tenant-1",
            "device_trust_state": "trusted",
            "active_session_id": "sess-1",
        }

    def tearDown(self) -> None:
        self.db_patcher.stop()
        self.tmpdir.cleanup()

    async def test_unaddressed_group_message_is_ignored_before_dm_policy_even_runs(self) -> None:
        with (
            patch(
                "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                return_value=_ALLOW_DISPATCH_DECISION,
            ),
            patch(
                "server_modules.personal_channels_service.gateway_protocol_service.dispatch_channel_outbound",
                new=AsyncMock(side_effect=AssertionError("must not dispatch")),
                create=True,
            ),
        ):
            result = await personal_channels_service._handle_local_bridge_gateway_channel_inbound(
                gateway_id="gw-group-2",
                registration=self.registration,
                payload={
                    "message": {
                        "external_message_id": "sig-group-1",
                        "remote_jid": "group:family",
                        "sender_jid": "+15557654321",
                        "push_name": "Family Member",
                        "text": "what time is dinner",
                        "from_me": False,
                        "is_group": True,
                        "is_mentioned": False,
                        "is_reply_to_sage": False,
                    },
                },
                channel_key="signal_personal",
                provider="signal_local_bridge",
                label="Signal",
            )
        self.assertTrue(result.get("ignored"))
        self.assertEqual(result.get("reason"), "group_no_mention")
        # Distinguish from dmPolicy's OWN block shape (no "policy" key here
        # — this really is the earlier, separate gate returning first).
        self.assertNotIn("policy", result)

    async def test_mentioned_group_message_still_reaches_and_is_blocked_by_dm_policy(self) -> None:
        """Local-bridge channels have no owner-identity resolution at all
        yet (a separate, pre-existing gap — see
        _handle_local_bridge_gateway_channel_inbound's own comment), so
        dmPolicy blocks EVERY sender unconditionally today. This proves the
        group gate correctly lets an addressed message past ITSELF and on
        to that next layer — dmPolicy's block must not be mistaken for the
        group gate never having run, or vice versa. _enforce_dm_policy
        itself is mocked (see the identically-reasoned comment on
        TelegramGroupGateTests.test_mentioned_group_message_from_a_stranger_is_still_blocked_by_dm_policy —
        same real-Rust-control-plane environment gap, confirmed to
        independently break test_personal_channels_dm_policy.py's own
        test_local_bridge_stranger_is_blocked_by_default here too)."""
        blocked_decision = {
            "allowed": False, "mode": "owner_only", "sender_id": "+15557654321",
            "is_owner": False, "system_reply": None, "config_changed": False,
        }
        with (
            patch(
                "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                return_value=_ALLOW_DISPATCH_DECISION,
            ),
            patch(
                "server_modules.personal_channels_service._enforce_dm_policy",
                new=AsyncMock(return_value=blocked_decision),
            ) as dm_policy_mock,
            patch(
                "server_modules.personal_channels_service.gateway_protocol_service.dispatch_channel_outbound",
                new=AsyncMock(side_effect=AssertionError("must not dispatch")),
                create=True,
            ),
        ):
            result = await personal_channels_service._handle_local_bridge_gateway_channel_inbound(
                gateway_id="gw-group-2",
                registration=self.registration,
                payload={
                    "message": {
                        "external_message_id": "sig-group-2",
                        "remote_jid": "group:family",
                        "sender_jid": "+15557654321",
                        "push_name": "Family Member",
                        "text": "@sage hello",
                        "from_me": False,
                        "is_group": True,
                        "is_mentioned": True,
                        "is_reply_to_sage": False,
                    },
                },
                channel_key="signal_personal",
                provider="signal_local_bridge",
                label="Signal",
            )
        dm_policy_mock.assert_awaited_once()
        self.assertTrue(result.get("blocked"))
        self.assertNotEqual(result.get("reason"), "group_no_mention")
        self.assertIn("policy", result)

    async def test_direct_message_is_unaffected_by_the_group_gate(self) -> None:
        """A plain 1:1 DM (is_group absent — the real-world shape every
        local-bridge event has today, per local-bridge-runtime.ts) must
        reach dmPolicy exactly as before this fix: still blocked by
        dmPolicy (pre-existing, unrelated to this change), never by
        group_no_mention — proving the new gate is a strict no-op for
        non-group traffic. _enforce_dm_policy is mocked for the same
        environment reason as the test above."""
        blocked_decision = {
            "allowed": False, "mode": "owner_only", "sender_id": "+15557654321",
            "is_owner": False, "system_reply": None, "config_changed": False,
        }
        with (
            patch(
                "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                return_value=_ALLOW_DISPATCH_DECISION,
            ),
            patch(
                "server_modules.personal_channels_service._enforce_dm_policy",
                new=AsyncMock(return_value=blocked_decision),
            ) as dm_policy_mock,
            patch(
                "server_modules.personal_channels_service.gateway_protocol_service.dispatch_channel_outbound",
                new=AsyncMock(side_effect=AssertionError("must not dispatch")),
                create=True,
            ),
        ):
            result = await personal_channels_service._handle_local_bridge_gateway_channel_inbound(
                gateway_id="gw-group-2",
                registration=self.registration,
                payload={
                    "message": {
                        "external_message_id": "sig-dm-1",
                        "remote_jid": "+15557654321",
                        "sender_jid": "+15557654321",
                        "push_name": "Someone",
                        "text": "hey",
                        "from_me": False,
                    },
                },
                channel_key="signal_personal",
                provider="signal_local_bridge",
                label="Signal",
            )
        dm_policy_mock.assert_awaited_once()
        self.assertTrue(result.get("blocked"))
        self.assertNotEqual(result.get("reason"), "group_no_mention")


if __name__ == "__main__":
    unittest.main()

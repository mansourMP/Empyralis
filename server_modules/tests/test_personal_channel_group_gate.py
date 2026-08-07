"""Tests for the group/mention/reply gate on personal-channel gateway
inbound handlers.

UPDATED 2026-07-23 (group_policy build): this gate is no longer a hardcoded
inline check — it's now personal_channels_service._enforce_group_policy,
which delegates its mention-addressing decision to the ONE shared
mention_gating_service.resolve_inbound_mention_decision resolver. As part of
that build, THE DEFAULT CHANGED: requireMention now defaults OFF (Ruling A,
"see-and-decide" — still in force per docs/OpenClaw.md's GROUP/MENTION
GATING section and personal_channels_service.py's own DEFAULT_REQUIRE_MENTION
comment), so an UNADDRESSED group message now reaches the model by default —
the agent decides for itself whether to reply (via [SILENT]), rather than
being hard-blocked before ever seeing it. The two tests below that used to
assert the opposite (2026-07-18's 0fe9ada19 hard gate, built to fix a real
"family group" spam incident) are UPDATED to match, and
test_personal_channels_group_policy.py adds dedicated coverage proving an
owner can still configure requireMention=True per agent+channel to restore
the exact old hard-gate behavior byte-for-byte — this is an owner-pullable
lever now, not the default everyone is stuck with.

Everything else in this file (explicit @mention, reply-to-Sage, group
context threading, DM/self-chat pass-through, dmPolicy independence) is
UNCHANGED and still describes real, current behavior — only the "nothing
addressed at all" default flipped.

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
from typing import Any, Dict
from unittest.mock import AsyncMock, patch

from server_modules import personal_channels_service, personal_channels_repository


_ALLOW_DISPATCH_DECISION = {
    "ok": True,
    "decision": "allow",
    "reason": "gateway_service_operation_allowed",
    "operation": "protocol_route",
    "next_action": "dispatch_gateway_operation",
}


class _FakeAgentInstallStore:
    """Minimal in-memory stand-in for agent_registry_repository's
    get_workspace_agent_install_bundle / update_workspace_agent_install —
    copied from test_personal_channels_dm_policy.py / test_personal_channels_
    group_policy.py (same convention: duplicated per-file rather than
    imported cross-file). Just enough of the real "install_metadata, shallow
    top-level metadata merge" contract for _load_agent_group_policy_config
    to correctly round-trip against, used here to prove a RESOLVED
    local-bridge identity reaches the same real config path WhatsApp/
    Telegram already do."""

    def __init__(self) -> None:
        self.installs: Dict[str, Dict[str, Any]] = {}

    async def get_bundle(self, agent_id: str, *, tenant_id: str, workspace_id: str):
        meta = self.installs.get(agent_id, {})
        return {"id": agent_id, "install_metadata": dict(meta)}

    async def update(self, agent_id: str, *, tenant_id: str, workspace_id: str, metadata=None, **_kwargs):
        existing = self.installs.get(agent_id, {})
        merged = {**existing, **(metadata or {})}
        self.installs[agent_id] = merged
        return {"id": agent_id, "install_metadata": dict(merged)}


def _patch_agent_install_store(store: "_FakeAgentInstallStore"):
    return patch.multiple(
        "server_modules.agent_registry_repository",
        get_workspace_agent_install_bundle=AsyncMock(side_effect=store.get_bundle),
        update_workspace_agent_install=AsyncMock(side_effect=store.update),
    )


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

    async def test_unaddressed_group_message_is_seen_by_default_see_and_decide(self) -> None:
        """THE default flip (2026-07-23): with group_policy/requireMention
        left unconfigured (DEFAULT_REQUIRE_MENTION=False), an unaddressed
        group message is NO LONGER hard-blocked here — it reaches the model
        (build_telegram_personal_reply is called with is_group=True), which
        is then free to reply or emit [SILENT] by its own judgment (Ruling
        A). This replaces the pre-2026-07-23 test of the same shape, which
        asserted the opposite under the old hardcoded gate — see
        test_personal_channels_group_policy.py for the dedicated
        requireMention=True test proving the old behavior is still
        available as an explicit owner opt-in, byte-for-byte."""
        with (
            patch(
                "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                return_value=_ALLOW_DISPATCH_DECISION,
            ),
            patch(
                "server_modules.personal_channels_service._enforce_dm_policy",
                new=AsyncMock(return_value={
                    "allowed": True, "mode": "open", "sender_id": "111222",
                    "is_owner": False, "system_reply": None, "config_changed": False,
                }),
            ),
            patch(
                "server_modules.personal_channels_service.personal_channel_sage_bridge_service.build_telegram_personal_reply",
                return_value={"text": "[SILENT]", "source": "sage"},
            ) as build_reply_mock,
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
        build_reply_mock.assert_called_once()
        call_kwargs = build_reply_mock.call_args.kwargs
        self.assertTrue(call_kwargs.get("is_group"))
        self.assertNotEqual(result.get("reason"), "group_no_mention")

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
    is enforced BEFORE dmPolicy's own check, for BOTH an unresolved identity
    (fails closed at the group gate itself, see below) and a resolved one
    (behaves exactly like WhatsApp/Telegram from then on).

    UPDATED (channel-gate hardening, "the last unscoped channels"): before
    this build, agent_id="" was the PERMANENT case for every local-bridge
    inbound message — _resolve_agent_id_for_inbound had no lookup branch for
    Signal/iMessage/WeChat-personal at all, so every message here landed on
    _unresolved_identity_group_policy_config's then-OPEN default and always
    reached dmPolicy. It no longer is: _resolve_local_bridge_agent_id
    (personal_channels_service.py) now resolves a real agent_id via a
    reverse preferred_gateway_id lookup. Two of the three tests below were
    UPDATED to match: an unresolved identity (no agent claims this gateway)
    now fails CLOSED at the group gate itself for ANY group message,
    addressed or not — never reaching dmPolicy — and a new test proves a
    RESOLVED identity restores the full three-gate flow exactly like
    WhatsApp/Telegram already have."""

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

    async def test_unresolved_identity_denies_an_unaddressed_group_message_at_the_group_gate(self) -> None:
        """No agent has claimed gw-group-2 (no preferred_gateway_id match,
        no prior claim in personal_channel_local_bridge_states) —
        _resolve_local_bridge_agent_id genuinely can't resolve an agent_id,
        so this lands on _unresolved_identity_group_policy_config's
        LOCAL_BRIDGE_PERSONAL_CHANNELS branch: GROUP_POLICY_DISABLED. The
        message must be denied AT THE GROUP GATE (group_policy_denied) —
        dmPolicy must never even run."""
        with (
            patch(
                "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                return_value=_ALLOW_DISPATCH_DECISION,
            ),
            patch(
                "server_modules.personal_channels_service._enforce_dm_policy",
                new=AsyncMock(side_effect=AssertionError("dmPolicy must not run — the group gate should deny first")),
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
        self.assertEqual(result.get("reason"), "group_policy_denied")

    async def test_unresolved_identity_denies_even_an_explicitly_mentioned_group_message(self) -> None:
        """GROUP_POLICY_DISABLED means disabled — proven here with an
        EXPLICIT @mention, which must not bypass it (mirrors
        test_personal_channels_group_policy.py's
        test_group_policy_disabled_blocks_every_group_message_regardless_of_mention).
        Still no agent claims gw-group-3 in this test."""
        with (
            patch(
                "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                return_value=_ALLOW_DISPATCH_DECISION,
            ),
            patch(
                "server_modules.personal_channels_service._enforce_dm_policy",
                new=AsyncMock(side_effect=AssertionError("dmPolicy must not run — the group gate should deny first")),
            ),
            patch(
                "server_modules.personal_channels_service.gateway_protocol_service.dispatch_channel_outbound",
                new=AsyncMock(side_effect=AssertionError("must not dispatch")),
                create=True,
            ),
        ):
            result = await personal_channels_service._handle_local_bridge_gateway_channel_inbound(
                gateway_id="gw-group-3",
                registration={**self.registration, "gateway_id": "gw-group-3"},
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
        self.assertTrue(result.get("ignored"))
        self.assertEqual(result.get("reason"), "group_policy_denied")

    async def test_resolved_identity_with_group_allowed_reaches_dm_policy_like_whatsapp_telegram(self) -> None:
        """Once an agent HAS claimed this gateway+channel (here, seeded
        directly via upsert_local_bridge_state — the same row
        _resolve_local_bridge_agent_id's own preferred_gateway_id lookup
        would populate; see that function's docstring), _resolve_agent_id_for_inbound's
        fast path finds it, group_policy config is loaded for a REAL agent
        (not the unresolved fallback), and — with that config's mode
        explicitly opened for this specific group — the mention gate lets an
        addressed message through to dmPolicy exactly like WhatsApp/Telegram
        already do. Proves resolution + the write path (group_policy
        persisted via install_metadata, read back through the real,
        unmocked _load_agent_group_policy_config) both work end-to-end for a
        local-bridge channel for the first time."""
        personal_channels_repository.upsert_local_bridge_state(
            gateway_id="gw-group-4",
            tenant_id="tenant-1",
            workspace_id="default",
            user_id="",
            channel_key="signal_personal",
            agent_id="agent-signal-claimed",
            provider="signal_local_bridge",
            status="linked",
        )
        store = _FakeAgentInstallStore()
        store.installs["agent-signal-claimed"] = {
            "group_policy": {
                "signal_personal": {
                    "mode": "allowlist", "allowlist": ["group:family"], "require_mention": True,
                },
            },
        }
        blocked_decision = {
            "allowed": False, "mode": "owner_only", "sender_id": "+15557654321",
            "is_owner": False, "system_reply": None, "config_changed": False,
        }
        with (
            patch(
                "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                return_value=_ALLOW_DISPATCH_DECISION,
            ),
            _patch_agent_install_store(store),
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
                gateway_id="gw-group-4",
                registration={**self.registration, "gateway_id": "gw-group-4"},
                payload={
                    "message": {
                        "external_message_id": "sig-group-4",
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
        # The resolved agent_id was threaded through to dmPolicy too, not
        # left hardcoded at "" — proving the fix reaches both gates, not
        # just the group one.
        self.assertEqual(dm_policy_mock.await_args.kwargs.get("agent_id"), "agent-signal-claimed")
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


class GroupContextThreadingTests(unittest.IsolatedAsyncioTestCase):
    """Systemic backend-safety verification: every personal-channel handler
    that lets a group message reach the model at all must also thread
    is_group/chat_label into the Sage bridge call — otherwise the group
    gate above only decides WHETHER to reply, and the model still never
    learns it's IN a group once it does (the exact "family group"
    mislabeling bug _owner_provenance_message / _personal_channel_guard_metadata
    were fixed for — see test_personal_channel_sage_bridge_service.py's
    OwnerAwareProvenanceTests for the rendering-level proof that a
    True/label pair actually produces "group" wording, never "direct
    message").

    None of the gate tests above (TelegramGroupGateTests /
    LocalBridgeGroupGateTests) ever inspect the mocked bridge call's
    kwargs — only assert_called_once()/assert_not_called() — so none of
    them would have caught is_group/chat_label silently failing to reach
    the bridge. These do, for three of the four personal-channel handler
    families: WhatsApp, Telegram (gateway), and local-bridge (Signal/
    iMessage/WeChat) — all already threaded it correctly. See
    test_cloud_channel_group_gate.py for the fourth family, Telegram-cloud
    (handle_cloud_channel_inbound), which this same investigation found
    ACTUALLY broken — it computed is_group for its own gate but never
    forwarded it to the bridge call — and fixed."""

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
            "gateway_id": "gw-thread-1",
            "workspace_id": "default",
            "tenant_id": "tenant-1",
            "device_trust_state": "trusted",
            "active_session_id": "sess-1",
        }
        # dmPolicy is orthogonal to this fix (see FIX 1 /
        # test_personal_channels_dm_policy.py) — mocked to ALLOW here purely
        # so a group turn actually reaches the bridge call this test
        # inspects, isolating ONE thing: does is_group/chat_label reach it.
        self.allowed_decision = {
            "allowed": True, "mode": "open", "sender_id": "111222",
            "is_owner": False, "system_reply": None, "config_changed": False,
        }

    def tearDown(self) -> None:
        self.db_patcher.stop()
        self.tmpdir.cleanup()

    async def test_whatsapp_group_message_threads_is_group_and_chat_label(self) -> None:
        with (
            patch(
                "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                return_value=_ALLOW_DISPATCH_DECISION,
            ),
            patch(
                "server_modules.personal_channels_service._enforce_dm_policy",
                new=AsyncMock(return_value=self.allowed_decision),
            ),
            patch(
                "server_modules.personal_channels_service.personal_channel_sage_bridge_service.build_whatsapp_personal_reply",
                return_value={"text": "Dinner's at 7.", "source": "sage"},
            ) as build_reply_mock,
            patch(
                "server_modules.personal_channels_service.gateway_protocol_service.dispatch_channel_outbound",
                new=AsyncMock(return_value={"external_message_id": "wa-out-thread-1"}),
                create=True,
            ),
        ):
            await personal_channels_service._handle_whatsapp_gateway_channel_inbound(
                gateway_id="gw-thread-1",
                registration=self.registration,
                payload={
                    "channel_key": personal_channels_service.WHATSAPP_PERSONAL_CHANNEL_KEY,
                    "provider": personal_channels_service.WHATSAPP_PERSONAL_PROVIDER,
                    "message": {
                        "external_message_id": "wa-group-thread-1",
                        "remote_jid": "120363-group@g.us",
                        "sender_jid": "111222@s.whatsapp.net",
                        "push_name": "Family Member",
                        "text": "@sage what time is dinner",
                        "from_me": False,
                        "is_group": True,
                        "is_mentioned": True,
                        "is_reply_to_sage": False,
                        "chat_title": "Family",
                    },
                },
            )
        build_reply_mock.assert_called_once()
        call_kwargs = build_reply_mock.call_args.kwargs
        self.assertTrue(call_kwargs.get("is_group"))
        self.assertEqual(call_kwargs.get("chat_label"), "Family")

    async def test_telegram_group_message_threads_is_group_and_chat_label(self) -> None:
        with (
            patch(
                "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                return_value=_ALLOW_DISPATCH_DECISION,
            ),
            patch(
                "server_modules.personal_channels_service._enforce_dm_policy",
                new=AsyncMock(return_value=self.allowed_decision),
            ),
            patch(
                "server_modules.personal_channels_service.personal_channel_sage_bridge_service.build_telegram_personal_reply",
                return_value={"text": "Dinner's at 7.", "source": "sage"},
            ) as build_reply_mock,
            patch(
                "server_modules.personal_channels_service.gateway_protocol_service.dispatch_channel_outbound",
                new=AsyncMock(return_value={"external_message_id": "tg-out-thread-1"}),
                create=True,
            ),
        ):
            await personal_channels_service._handle_telegram_gateway_channel_inbound(
                gateway_id="gw-thread-1",
                registration=self.registration,
                payload={
                    "channel_key": personal_channels_service.TELEGRAM_PERSONAL_CHANNEL_KEY,
                    "provider": personal_channels_service.TELEGRAM_PERSONAL_PROVIDER,
                    "message": {
                        "external_message_id": "tg-group-thread-1",
                        "remote_jid": "-100555",
                        "sender_jid": "111222",
                        "push_name": "Family Member",
                        "text": "@sage_owner what time is dinner",
                        "from_me": False,
                        "is_group": True,
                        "is_mentioned": True,
                        "is_reply_to_sage": False,
                        "chat_title": "Family",
                    },
                },
            )
        build_reply_mock.assert_called_once()
        call_kwargs = build_reply_mock.call_args.kwargs
        self.assertTrue(call_kwargs.get("is_group"))
        self.assertEqual(call_kwargs.get("chat_label"), "Family")

    async def test_local_bridge_group_message_threads_is_group_and_chat_label(self) -> None:
        """Signal/iMessage/WeChat all share
        _handle_local_bridge_gateway_channel_inbound — proves the threading
        holds across all three, not just one.

        UPDATED (channel-gate hardening): identity resolution is REAL now
        (_resolve_local_bridge_agent_id) — an unclaimed gateway would fail
        closed at the group gate (see LocalBridgeGroupGateTests) before ever
        reaching the bridge call this test inspects. Each channel_key claims
        its own gateway_id+agent_id row directly via upsert_local_bridge_state
        (the same row _resolve_local_bridge_agent_id's own preferred_gateway_id
        lookup would populate) so _resolve_agent_id_for_inbound's fast path
        resolves it, and _load_agent_group_policy_config is patched to an
        explicit open/allowed config — this test's OWN subject is is_group/
        chat_label threading, not group_policy's gate mechanics (covered
        separately), so the resolved config is fixed rather than exercised
        end-to-end here."""
        for channel_key, meta in personal_channels_service.LOCAL_BRIDGE_PERSONAL_CHANNELS.items():
            with self.subTest(channel_key=channel_key):
                agent_id = f"agent-{channel_key}-thread"
                personal_channels_repository.upsert_local_bridge_state(
                    gateway_id="gw-thread-1",
                    tenant_id="tenant-1",
                    workspace_id="default",
                    user_id="",
                    channel_key=channel_key,
                    agent_id=agent_id,
                    provider=meta["provider"],
                    status="linked",
                )
                with (
                    patch(
                        "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                        return_value=_ALLOW_DISPATCH_DECISION,
                    ),
                    patch(
                        "server_modules.personal_channels_service._enforce_dm_policy",
                        new=AsyncMock(return_value=self.allowed_decision),
                    ),
                    patch(
                        "server_modules.personal_channels_service._load_agent_group_policy_config",
                        new=AsyncMock(return_value={"mode": "open", "allowlist": [], "require_mention": False}),
                    ),
                    patch(
                        "server_modules.personal_channels_service.personal_channel_sage_bridge_service.build_personal_channel_reply_async",
                        new=AsyncMock(return_value={"text": "Dinner's at 7.", "source": "sage"}),
                    ) as build_reply_mock,
                    patch(
                        "server_modules.personal_channels_service.gateway_protocol_service.dispatch_channel_outbound",
                        new=AsyncMock(return_value={"external_message_id": f"{channel_key}-out-thread-1"}),
                        create=True,
                    ),
                ):
                    await personal_channels_service._handle_local_bridge_gateway_channel_inbound(
                        gateway_id="gw-thread-1",
                        registration=self.registration,
                        payload={
                            "message": {
                                "external_message_id": f"{channel_key}-group-thread-1",
                                "remote_jid": "group:family",
                                "sender_jid": "+15557654321",
                                "push_name": "Family Member",
                                "text": "@sage what time is dinner",
                                "from_me": False,
                                "is_group": True,
                                "is_mentioned": True,
                                "is_reply_to_sage": False,
                                "chat_title": "Family",
                            },
                        },
                        channel_key=channel_key,
                        provider=meta["provider"],
                        label=meta["label"],
                    )
                build_reply_mock.assert_awaited_once()
                call_kwargs = build_reply_mock.call_args.kwargs
                self.assertTrue(call_kwargs.get("is_group"))
                self.assertEqual(call_kwargs.get("chat_label"), "Family")


if __name__ == "__main__":
    unittest.main()

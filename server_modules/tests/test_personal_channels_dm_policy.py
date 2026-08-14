"""Tests for Feature A: dmPolicy sender allowlist / pairing enforcement.

Before this feature, the inbound personal-channel reply path
(personal_channels_service._handle_*_gateway_channel_inbound /
_deliver_*_personal_reply) replied to ANY sender with zero identity check,
even though the channel manifests claim `safety: {ownerPairingRequired:
true, allowlistRequired: true}`. These tests prove:

  1. The default (owner_only) policy blocks a stranger and never dispatches
     a reply — both at the isolated gate-function level and through the
     full live inbound handler.
  2. allowlist / pairing / open modes behave per their contract, including
     "pairing" issuing exactly one challenge and then staying silent.
  3. The pre-existing linked_jid/linked_user_id clobber bug (a stranger's
     inbound message used to silently overwrite the persisted OWNER
     identity with the stranger's own jid) is fixed — a regression test
     that would have failed before this build's fix.
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
    just enough of the real "install_metadata, shallow top-level metadata
    merge" contract for _load_agent_dm_policy_config /
    _persist_agent_dm_policy_config to correctly round-trip against."""

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
    """Combined context manager patching both repository calls
    _load_agent_dm_policy_config / _persist_agent_dm_policy_config make,
    backed by the same in-memory store."""
    return patch.multiple(
        "server_modules.agent_registry_repository",
        get_workspace_agent_install_bundle=AsyncMock(side_effect=store.get_bundle),
        update_workspace_agent_install=AsyncMock(side_effect=store.update),
    )


class DmPolicyGateUnitTests(unittest.IsolatedAsyncioTestCase):
    """Direct tests against _enforce_dm_policy — no gateway/DB plumbing."""

    def setUp(self) -> None:
        self.registration = {"tenant_id": "tenant-1", "workspace_id": "ws-1"}

    async def test_owner_only_default_blocks_stranger(self) -> None:
        decision = await personal_channels_service._enforce_dm_policy(
            registration=self.registration,
            channel_key=personal_channels_service.WHATSAPP_PERSONAL_CHANNEL_KEY,
            agent_id="",  # unscoped -> hard-coded safe default, no DB lookup
            message={"sender_jid": "stranger@s.whatsapp.net"},
            remote_jid="stranger@s.whatsapp.net",
            existing_state=None,
            label="WhatsApp",
        )
        self.assertFalse(decision["allowed"])
        self.assertEqual(decision["mode"], "owner_only")
        self.assertIsNone(decision["system_reply"])
        self.assertFalse(decision["is_owner"])

    async def test_unresolved_identity_config_is_owner_only_regardless_of_default_dm_policy_mode(self) -> None:
        """Regression test for the exact ee3fca4f7c bug: the identity-less
        fallback (agent_id="" / LEGACY_UNSCOPED_AGENT_ID — the PERMANENT
        case for every local-bridge channel: Signal, iMessage, WeChat; see
        personal_channels_service.LOCAL_BRIDGE_PERSONAL_CHANNELS) must
        always resolve to owner_only, hardcoded — never
        DEFAULT_DM_POLICY_MODE. DEFAULT_DM_POLICY_MODE is currently "open"
        (a deliberate, documented product decision for REAL resolved
        agents, see its own comment) — this test proves that constant no
        longer leaks into the separate, identity-less fallback the way it
        did between ee3fca4f7c and this fix, regardless of what value that
        constant holds. Exercises _load_agent_dm_policy_config directly
        (one level below _enforce_dm_policy) so this fails loudly on the
        exact function the bug lived in, independent of the gate's own
        owner-detection short-circuit."""
        self.assertEqual(personal_channels_service.DEFAULT_DM_POLICY_MODE, personal_channels_service.DM_POLICY_OPEN)
        for agent_id in ("", personal_channels_repository.LEGACY_UNSCOPED_AGENT_ID):
            with self.subTest(agent_id=repr(agent_id)):
                config = await personal_channels_service._load_agent_dm_policy_config(
                    tenant_id="tenant-1",
                    workspace_id="ws-1",
                    agent_id=agent_id,
                    channel_key=personal_channels_service.WHATSAPP_PERSONAL_CHANNEL_KEY,
                )
                self.assertEqual(config["mode"], "owner_only")
                self.assertEqual(config["allowlist"], [])

    async def test_owner_only_default_allows_self_chat(self) -> None:
        decision = await personal_channels_service._enforce_dm_policy(
            registration=self.registration,
            channel_key=personal_channels_service.WHATSAPP_PERSONAL_CHANNEL_KEY,
            agent_id="",
            message={"sender_jid": "owner@s.whatsapp.net", "is_self_chat": True},
            remote_jid="owner@s.whatsapp.net",
            existing_state=None,
            label="WhatsApp",
        )
        self.assertTrue(decision["allowed"])
        self.assertTrue(decision["is_owner"])

    async def test_telegram_owner_only_default_allows_self_chat(self) -> None:
        """Telegram's Gateway now carries the same is_self_chat signal
        WhatsApp always has (see telegram/message-mapper.ts's
        mapTelegramInboundMessage and runtime.ts's self-chat fix) — mirrors
        test_owner_only_default_allows_self_chat above exactly, for
        Telegram. _is_owner_message checks message.get("is_self_chat")
        generically (not channel-specific), so this passed the moment the
        Gateway started populating the field; no _enforce_dm_policy change
        was needed."""
        decision = await personal_channels_service._enforce_dm_policy(
            registration=self.registration,
            channel_key=personal_channels_service.TELEGRAM_PERSONAL_CHANNEL_KEY,
            agent_id="",
            message={"sender_jid": "555444333", "is_self_chat": True},
            remote_jid="555444333",
            existing_state=None,
            label="Telegram",
        )
        self.assertTrue(decision["allowed"])
        self.assertTrue(decision["is_owner"])

    async def test_signal_owner_only_default_allows_self_chat(self) -> None:
        """Mirrors test_telegram_owner_only_default_allows_self_chat above,
        for Signal's local-bridge handler — signal-cli-bridge.ts's
        mapSignalCliReceiveNotification now computes is_self_chat for a
        "Note to Self" send (fromMe && remoteJid === the bridge's own
        configured account), threaded through by local-bridge-runtime.ts's
        mapInboundEvent. _is_owner_message's is_self_chat check is
        channel-agnostic, so this needed no _enforce_dm_policy change either
        — only _handle_local_bridge_gateway_channel_inbound's from_me gate
        (tested below) needed the WhatsApp/Telegram carve-out."""
        decision = await personal_channels_service._enforce_dm_policy(
            registration=self.registration,
            channel_key="signal_personal",
            agent_id="",
            message={"sender_jid": "+15551234567", "is_self_chat": True},
            remote_jid="+15551234567",
            existing_state=None,
            label="Signal",
        )
        self.assertTrue(decision["allowed"])
        self.assertTrue(decision["is_owner"])

    async def test_owner_only_allows_sender_matching_linked_identity(self) -> None:
        """Telegram's is_self_chat signal (see
        test_telegram_owner_only_default_allows_self_chat above) only ever
        applies to the owner's private Saved Messages conversation — inside
        a GROUP the owner is a member of, is_self_chat is always False (the
        peer is the group, not the owner), so owner detection there still
        falls back to remote_jid == the channel's own persisted
        linked_user_id. This test covers exactly that fallback path,
        independent of is_self_chat."""
        decision = await personal_channels_service._enforce_dm_policy(
            registration=self.registration,
            channel_key=personal_channels_service.TELEGRAM_PERSONAL_CHANNEL_KEY,
            agent_id="",
            message={"sender_jid": "555"},
            remote_jid="555",
            existing_state={"linked_user_id": "555"},
            label="Telegram",
        )
        self.assertTrue(decision["allowed"])
        self.assertTrue(decision["is_owner"])

    async def test_owner_only_blocks_sender_not_matching_linked_identity(self) -> None:
        decision = await personal_channels_service._enforce_dm_policy(
            registration=self.registration,
            channel_key=personal_channels_service.TELEGRAM_PERSONAL_CHANNEL_KEY,
            agent_id="",
            message={"sender_jid": "999"},
            remote_jid="999",
            existing_state={"linked_user_id": "555"},
            label="Telegram",
        )
        self.assertFalse(decision["allowed"])
        self.assertFalse(decision["is_owner"])

    async def test_allowlist_mode_allows_listed_sender_blocks_others(self) -> None:
        store = _FakeAgentInstallStore()
        store.installs["agent-1"] = {
            "dm_policy": {
                personal_channels_service.WHATSAPP_PERSONAL_CHANNEL_KEY: {
                    "mode": "allowlist",
                    "allowlist": ["friend@s.whatsapp.net"],
                    "pending_pairing": {},
                }
            }
        }
        with _patch_agent_install_store(store):
            allowed_decision = await personal_channels_service._enforce_dm_policy(
                registration=self.registration,
                channel_key=personal_channels_service.WHATSAPP_PERSONAL_CHANNEL_KEY,
                agent_id="agent-1",
                message={"sender_jid": "friend@s.whatsapp.net"},
                remote_jid="friend@s.whatsapp.net",
                existing_state=None,
                label="WhatsApp",
            )
            blocked_decision = await personal_channels_service._enforce_dm_policy(
                registration=self.registration,
                channel_key=personal_channels_service.WHATSAPP_PERSONAL_CHANNEL_KEY,
                agent_id="agent-1",
                message={"sender_jid": "stranger@s.whatsapp.net"},
                remote_jid="stranger@s.whatsapp.net",
                existing_state=None,
                label="WhatsApp",
            )
        self.assertTrue(allowed_decision["allowed"])
        self.assertEqual(allowed_decision["mode"], "allowlist")
        self.assertFalse(blocked_decision["allowed"])

    async def test_open_mode_allows_anyone(self) -> None:
        store = _FakeAgentInstallStore()
        store.installs["agent-2"] = {
            "dm_policy": {
                personal_channels_service.WHATSAPP_PERSONAL_CHANNEL_KEY: {
                    "mode": "open", "allowlist": [], "pending_pairing": {},
                }
            }
        }
        with _patch_agent_install_store(store):
            decision = await personal_channels_service._enforce_dm_policy(
                registration=self.registration,
                channel_key=personal_channels_service.WHATSAPP_PERSONAL_CHANNEL_KEY,
                agent_id="agent-2",
                message={"sender_jid": "anyone@s.whatsapp.net"},
                remote_jid="anyone@s.whatsapp.net",
                existing_state=None,
                label="WhatsApp",
            )
        self.assertTrue(decision["allowed"])
        self.assertEqual(decision["mode"], "open")

    async def test_pairing_mode_challenges_once_then_silent_then_approve_unblocks(self) -> None:
        store = _FakeAgentInstallStore()
        store.installs["agent-3"] = {
            "dm_policy": {
                personal_channels_service.WHATSAPP_PERSONAL_CHANNEL_KEY: {
                    "mode": "pairing", "allowlist": [], "pending_pairing": {},
                }
            }
        }
        with _patch_agent_install_store(store):
            first = await personal_channels_service._enforce_dm_policy(
                registration=self.registration,
                channel_key=personal_channels_service.WHATSAPP_PERSONAL_CHANNEL_KEY,
                agent_id="agent-3",
                message={"sender_jid": "new@s.whatsapp.net", "push_name": "New Contact"},
                remote_jid="new@s.whatsapp.net",
                existing_state=None,
                label="WhatsApp",
            )
            second = await personal_channels_service._enforce_dm_policy(
                registration=self.registration,
                channel_key=personal_channels_service.WHATSAPP_PERSONAL_CHANNEL_KEY,
                agent_id="agent-3",
                message={"sender_jid": "new@s.whatsapp.net", "push_name": "New Contact"},
                remote_jid="new@s.whatsapp.net",
                existing_state=None,
                label="WhatsApp",
            )
            self.assertFalse(first["allowed"])
            self.assertIsNotNone(first["system_reply"])
            self.assertIn("owner", first["system_reply"].lower())
            self.assertFalse(second["allowed"])
            self.assertIsNone(second["system_reply"])  # silent on repeat — no re-spam

            approval = await personal_channels_service.approve_dm_policy_pairing_request(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                agent_id="agent-3",
                channel_key=personal_channels_service.WHATSAPP_PERSONAL_CHANNEL_KEY,
                sender_id="new@s.whatsapp.net",
            )
            self.assertTrue(approval["approved"])

            third = await personal_channels_service._enforce_dm_policy(
                registration=self.registration,
                channel_key=personal_channels_service.WHATSAPP_PERSONAL_CHANNEL_KEY,
                agent_id="agent-3",
                message={"sender_jid": "new@s.whatsapp.net"},
                remote_jid="new@s.whatsapp.net",
                existing_state=None,
                label="WhatsApp",
            )
        self.assertTrue(third["allowed"])

    async def test_approve_by_code(self) -> None:
        store = _FakeAgentInstallStore()
        store.installs["agent-4"] = {
            "dm_policy": {
                personal_channels_service.WHATSAPP_PERSONAL_CHANNEL_KEY: {
                    "mode": "pairing", "allowlist": [], "pending_pairing": {},
                }
            }
        }
        with _patch_agent_install_store(store):
            await personal_channels_service._enforce_dm_policy(
                registration=self.registration,
                channel_key=personal_channels_service.WHATSAPP_PERSONAL_CHANNEL_KEY,
                agent_id="agent-4",
                message={"sender_jid": "coded@s.whatsapp.net"},
                remote_jid="coded@s.whatsapp.net",
                existing_state=None,
                label="WhatsApp",
            )
            pending = store.installs["agent-4"]["dm_policy"][personal_channels_service.WHATSAPP_PERSONAL_CHANNEL_KEY]["pending_pairing"]
            code = pending["coded@s.whatsapp.net"]["code"]
            self.assertRegex(code, r"^\d{6}$")

            approval = await personal_channels_service.approve_dm_policy_pairing_request(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                agent_id="agent-4",
                channel_key=personal_channels_service.WHATSAPP_PERSONAL_CHANNEL_KEY,
                code=code,
            )
        self.assertTrue(approval["approved"])
        self.assertEqual(approval["sender_id"], "coded@s.whatsapp.net")

    async def test_approve_unknown_request_fails(self) -> None:
        approval = await personal_channels_service.approve_dm_policy_pairing_request(
            tenant_id="tenant-1", workspace_id="ws-1", agent_id="",
            channel_key=personal_channels_service.WHATSAPP_PERSONAL_CHANNEL_KEY,
            sender_id="ghost@s.whatsapp.net",
        )
        self.assertFalse(approval["approved"])


class DmPolicyInboundIntegrationTests(unittest.IsolatedAsyncioTestCase):
    """Through the LIVE gateway inbound handlers — real sqlite repository,
    mocked rust kernel + gateway dispatch."""

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
            "gateway_id": "gw-dm-1",
            "workspace_id": "default",
            "tenant_id": "tenant-1",
            "device_trust_state": "trusted",
            "active_session_id": "sess-1",
        }

    def tearDown(self) -> None:
        self.db_patcher.stop()
        self.tmpdir.cleanup()

    # test_stranger_message_is_blocked_and_never_dispatched,
    # test_self_chat_message_passes_the_gate,
    # test_telegram_self_chat_message_passes_the_gate_even_with_from_me_true,
    # test_telegram_ordinary_outgoing_message_is_still_ignored,
    # test_telegram_self_chat_message_refreshes_linked_user_id, and
    # test_stranger_message_does_not_clobber_linked_jid DELETED 2026-08-14
    # (full OpenClaw channel cutover): they drove
    # _handle_whatsapp_gateway_channel_inbound / _handle_telegram_gateway_
    # channel_inbound directly, both deleted along with the Baileys/gramjs
    # runtimes they served. The from_me/is_self_chat carve-out and linked_
    # jid/linked_user_id identity sync these tests protected have NO
    # OpenClaw equivalent — normalize_openclaw_gate_facts sets
    # is_self_chat=False unconditionally for every OpenClaw-transported
    # message, because OpenClaw carries no such fact at its inbound tap (see
    # that function's own docstring). This is a genuine, scoped capability
    # loss on the cut-over channels, not a test-coverage gap papered over —
    # see the cutover's own report for the full accounting. The remaining
    # local-bridge tests below (test_local_bridge_*) cover the identical
    # from_me/self-chat carve-out on the surviving generic handler.

    async def test_local_bridge_stranger_is_blocked_by_default(self) -> None:
        """Local-bridge channels (Signal/iMessage/WeChat) don't yet resolve
        a per-agent owner identity — the gate must still fail CLOSED (block)
        rather than fail open (reply to everyone, the pre-existing bug)."""
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
            patch("server_modules.personal_channels_service.security_audit_service.emit_security_audit_event"),
        ):
            result = await personal_channels_service._handle_local_bridge_gateway_channel_inbound(
                gateway_id="gw-dm-1",
                registration=self.registration,
                payload={
                    "message": {
                        "external_message_id": "sig-stranger-1",
                        "remote_jid": "signal-stranger-1",
                        "sender_jid": "signal-stranger-1",
                        "push_name": "Rando",
                        "text": "hey",
                        "from_me": False,
                    },
                },
                channel_key="openclaw_signal",
                provider="openclaw",
                label="Signal",
            )
        self.assertTrue(result.get("blocked"))
        self.assertIsNone(result.get("outbound"))

    async def test_local_bridge_stranger_is_blocked_across_signal_imessage_wechat(self) -> None:
        """FIX 1 regression, all three local-bridge families: the
        identity-less fallback bug (agent_id="" resolving to
        DEFAULT_DM_POLICY_MODE/open instead of the hardcoded owner_only its
        own docstring/comments always claimed) did not depend on
        channel_key at all, so it silently auto-replied to ANY stranger's
        1:1 DM on Signal, iMessage, AND WeChat identically — not just
        Signal, the only family test_local_bridge_stranger_is_blocked_by_default
        above covers. All three share the exact same
        _handle_local_bridge_gateway_channel_inbound entry point (see
        personal_channels_service.LOCAL_BRIDGE_PERSONAL_CHANNELS), so this
        proves the fix landed on the shared function, not one channel's
        call site."""
        for channel_key, meta in personal_channels_service.LOCAL_BRIDGE_PERSONAL_CHANNELS.items():
            with self.subTest(channel_key=channel_key):
                with (
                    patch(
                        "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                        return_value=_ALLOW_DISPATCH_DECISION,
                    ),
                    patch(
                        "server_modules.personal_channels_service.gateway_protocol_service.dispatch_channel_outbound",
                        new=AsyncMock(
                            side_effect=AssertionError(f"must not auto-reply to a {channel_key} stranger")
                        ),
                        create=True,
                    ),
                    patch("server_modules.personal_channels_service.security_audit_service.emit_security_audit_event"),
                ):
                    result = await personal_channels_service._handle_local_bridge_gateway_channel_inbound(
                        gateway_id="gw-dm-1",
                        registration=self.registration,
                        payload={
                            "message": {
                                "external_message_id": f"{channel_key}-stranger-1",
                                "remote_jid": f"{channel_key}-stranger-jid",
                                "sender_jid": f"{channel_key}-stranger-jid",
                                "push_name": "Rando",
                                "text": "hi, who is this?",
                                "from_me": False,
                            },
                        },
                        channel_key=channel_key,
                        provider=meta["provider"],
                        label=meta["label"],
                    )
                self.assertTrue(result.get("blocked"), f"{channel_key} stranger should be blocked, not auto-replied to")
                self.assertIsNone(result.get("outbound"))
                self.assertEqual(result["policy"]["mode"], "owner_only")

    async def test_local_bridge_self_chat_message_passes_the_gate_even_with_from_me_true(self) -> None:
        """Mirrors test_telegram_self_chat_message_passes_the_gate_even_with_from_me_true
        above, for _handle_local_bridge_gateway_channel_inbound (Signal/
        iMessage/WeChat's shared handler) — a genuine Signal "Note to Self"
        message is always from_me (only the linked account can post into
        its own self-conversation), and before this fix the handler's
        unconditional `if message.get("from_me"): ignore` dropped it
        regardless of is_self_chat. This is the exact scenario that
        carve-out (mirroring the WhatsApp/Telegram handlers) exists for.
        _is_owner_message's is_self_chat shortcut then does the rest — no
        local-bridge-specific owner-identity resolution was needed despite
        the pre-existing gap test_local_bridge_stranger_is_blocked_by_default
        documents above."""
        with (
            patch(
                "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                return_value=_ALLOW_DISPATCH_DECISION,
            ),
            patch(
                "server_modules.personal_channels_service.personal_channel_sage_bridge_service.build_personal_channel_reply_async",
                new=AsyncMock(return_value={"text": "On it.", "source": "sage"}),
            ) as build_reply_mock,
            patch(
                "server_modules.personal_channels_service.gateway_protocol_service.dispatch_channel_outbound",
                new=AsyncMock(return_value={"external_message_id": "sig-out-1"}),
                create=True,
            ) as dispatch_mock,
            patch("server_modules.personal_channels_service.security_audit_service.emit_security_audit_event"),
        ):
            result = await personal_channels_service._handle_local_bridge_gateway_channel_inbound(
                gateway_id="gw-dm-1",
                registration=self.registration,
                payload={
                    "message": {
                        "external_message_id": "sig-owner-1",
                        "remote_jid": "+15551234567",
                        "sender_jid": "+15551234567",
                        "push_name": "Me",
                        "text": "remind me to call mom",
                        "from_me": True,
                        "is_self_chat": True,
                    },
                },
                channel_key="openclaw_signal",
                provider="openclaw",
                label="Signal",
            )
        build_reply_mock.assert_called_once()
        dispatch_mock.assert_awaited_once()
        self.assertFalse(result.get("blocked", False))
        self.assertNotEqual(result.get("ignored"), True)
        self.assertEqual(result["outbound"]["status"], "delivered")

    async def test_local_bridge_ordinary_outgoing_message_is_still_ignored(self) -> None:
        """Regression/distinguishing coverage for the same carve-out: an
        ordinary outgoing Signal message to someone else (from_me: True,
        is_self_chat: False/absent) must still be ignored exactly as
        before — the from_me-unless-self-chat carve-out must not
        accidentally let every outgoing local-bridge message through."""
        with (
            patch(
                "server_modules.personal_channels_service.personal_channel_sage_bridge_service.build_personal_channel_reply_async"
            ) as build_reply_mock,
            patch(
                "server_modules.personal_channels_service.gateway_protocol_service.dispatch_channel_outbound",
                new=AsyncMock(side_effect=AssertionError("must not dispatch for an ordinary outgoing echo")),
                create=True,
            ),
        ):
            result = await personal_channels_service._handle_local_bridge_gateway_channel_inbound(
                gateway_id="gw-dm-1",
                registration=self.registration,
                payload={
                    "message": {
                        "external_message_id": "sig-outgoing-1",
                        "remote_jid": "+15557654321",
                        "sender_jid": "+15557654321",
                        "push_name": "A Contact",
                        "text": "see you at 7",
                        "from_me": True,
                        "is_self_chat": False,
                    },
                },
                channel_key="openclaw_signal",
                provider="openclaw",
                label="Signal",
            )
        build_reply_mock.assert_not_called()
        self.assertTrue(result.get("ignored"))
        self.assertEqual(result.get("reason"), "from_me")


if __name__ == "__main__":
    unittest.main()

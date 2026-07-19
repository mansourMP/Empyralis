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

    async def test_stranger_message_is_blocked_and_never_dispatched(self) -> None:
        with (
            patch(
                "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                return_value=_ALLOW_DISPATCH_DECISION,
            ),
            patch(
                "server_modules.personal_channels_service.personal_channel_sage_bridge_service.build_whatsapp_personal_reply"
            ) as build_reply_mock,
            patch(
                "server_modules.personal_channels_service.gateway_protocol_service.dispatch_channel_outbound",
                new=AsyncMock(side_effect=AssertionError("must not dispatch a reply to a blocked stranger")),
                create=True,
            ),
            patch("server_modules.personal_channels_service.security_audit_service.emit_security_audit_event"),
        ):
            result = await personal_channels_service._handle_whatsapp_gateway_channel_inbound(
                gateway_id="gw-dm-1",
                registration=self.registration,
                payload={
                    "channel_key": personal_channels_service.WHATSAPP_PERSONAL_CHANNEL_KEY,
                    "provider": personal_channels_service.WHATSAPP_PERSONAL_PROVIDER,
                    "message": {
                        "external_message_id": "wa-stranger-1",
                        "remote_jid": "919999999999@s.whatsapp.net",
                        "sender_jid": "919999999999@s.whatsapp.net",
                        "push_name": "Rando",
                        "text": "hi, who is this?",
                        "from_me": False,
                    },
                },
            )
        build_reply_mock.assert_not_called()
        self.assertTrue(result.get("blocked"))
        self.assertIsNone(result.get("outbound"))
        self.assertEqual(result["policy"]["mode"], "owner_only")

        # Still recorded ("observed") for context/audit, per the contract.
        inbound_row = personal_channels_repository.record_inbound_message(
            gateway_id="gw-dm-1",
            channel_key=personal_channels_service.WHATSAPP_PERSONAL_CHANNEL_KEY,
            external_message_id="wa-stranger-1",
            remote_jid="919999999999@s.whatsapp.net",
            sender_jid="919999999999@s.whatsapp.net",
            push_name="Rando",
            text="hi, who is this?",
        )[0]
        self.assertIsNotNone(inbound_row.get("reply_idempotency_key"))

    async def test_self_chat_message_passes_the_gate(self) -> None:
        with (
            patch(
                "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                return_value=_ALLOW_DISPATCH_DECISION,
            ),
            patch(
                "server_modules.personal_channels_service.personal_channel_sage_bridge_service.build_whatsapp_personal_reply",
                return_value={"text": "On it.", "source": "sage"},
            ) as build_reply_mock,
            patch(
                "server_modules.personal_channels_service.gateway_protocol_service.dispatch_channel_outbound",
                new=AsyncMock(return_value={"external_message_id": "wa-out-1"}),
                create=True,
            ) as dispatch_mock,
            patch("server_modules.personal_channels_service.security_audit_service.emit_security_audit_event"),
        ):
            result = await personal_channels_service._handle_whatsapp_gateway_channel_inbound(
                gateway_id="gw-dm-1",
                registration=self.registration,
                payload={
                    "channel_key": personal_channels_service.WHATSAPP_PERSONAL_CHANNEL_KEY,
                    "provider": personal_channels_service.WHATSAPP_PERSONAL_PROVIDER,
                    "message": {
                        "external_message_id": "wa-owner-1",
                        "remote_jid": "15550001111@s.whatsapp.net",
                        "sender_jid": "15550001111@s.whatsapp.net",
                        "push_name": "Me",
                        "text": "remind me to call mom",
                        "from_me": False,
                        "is_self_chat": True,
                    },
                },
            )
        build_reply_mock.assert_called_once()
        dispatch_mock.assert_awaited_once()
        self.assertFalse(result.get("blocked", False))
        self.assertEqual(result["outbound"]["status"], "delivered")

    async def test_telegram_self_chat_message_passes_the_gate_even_with_from_me_true(self) -> None:
        """Mirrors test_self_chat_message_passes_the_gate above, for
        Telegram — and, unlike that WhatsApp test (which uses from_me:
        False), deliberately sets from_me: True here too, since a genuine
        Telegram self-chat message is ALWAYS from_me (only the owner can
        post into their own Saved Messages — see
        telegram/message-mapper.ts's TelegramInboundMessage doc). This is
        exactly the scenario _handle_telegram_gateway_channel_inbound's
        `from_me and not is_self_chat` carve-out (mirroring the WhatsApp
        handler) exists for: before that fix, `from_me: True` alone
        short-circuited the handler with {"ignored": True, "reason":
        "from_me"} regardless of is_self_chat — moot in production only
        because the Gateway dropped self-chat messages before they ever
        reached here at all (see runtime.ts's NewMessage subscription fix)."""
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
                new=AsyncMock(return_value={"external_message_id": "tg-out-1"}),
                create=True,
            ) as dispatch_mock,
            patch("server_modules.personal_channels_service.security_audit_service.emit_security_audit_event"),
        ):
            result = await personal_channels_service._handle_telegram_gateway_channel_inbound(
                gateway_id="gw-dm-1",
                registration=self.registration,
                payload={
                    "channel_key": personal_channels_service.TELEGRAM_PERSONAL_CHANNEL_KEY,
                    "provider": personal_channels_service.TELEGRAM_PERSONAL_PROVIDER,
                    "message": {
                        "external_message_id": "tg-owner-1",
                        "remote_jid": "555444333",
                        "sender_jid": "555444333",
                        "push_name": "Me",
                        "text": "remind me to call mom",
                        "from_me": True,
                        "is_self_chat": True,
                    },
                },
            )
        build_reply_mock.assert_called_once()
        dispatch_mock.assert_awaited_once()
        self.assertFalse(result.get("blocked", False))
        self.assertNotEqual(result.get("ignored"), True)
        self.assertEqual(result["outbound"]["status"], "delivered")

    async def test_telegram_ordinary_outgoing_message_is_still_ignored(self) -> None:
        """Regression/distinguishing coverage for the SAME carve-out: an
        ordinary outgoing message to someone else (from_me: True,
        is_self_chat: False/absent — e.g. the owner replying to a contact
        from their phone) must still be ignored exactly as before. Proves
        the from_me-unless-self-chat carve-out doesn't accidentally let
        every outgoing message through."""
        with (
            patch(
                "server_modules.personal_channels_service.personal_channel_sage_bridge_service.build_telegram_personal_reply"
            ) as build_reply_mock,
            patch(
                "server_modules.personal_channels_service.gateway_protocol_service.dispatch_channel_outbound",
                new=AsyncMock(side_effect=AssertionError("must not dispatch for an ordinary outgoing echo")),
                create=True,
            ),
        ):
            result = await personal_channels_service._handle_telegram_gateway_channel_inbound(
                gateway_id="gw-dm-1",
                registration=self.registration,
                payload={
                    "channel_key": personal_channels_service.TELEGRAM_PERSONAL_CHANNEL_KEY,
                    "provider": personal_channels_service.TELEGRAM_PERSONAL_PROVIDER,
                    "message": {
                        "external_message_id": "tg-outgoing-1",
                        "remote_jid": "919999999999",
                        "sender_jid": "919999999999",
                        "push_name": "A Contact",
                        "text": "see you at 7",
                        "from_me": True,
                        "is_self_chat": False,
                    },
                },
            )
        build_reply_mock.assert_not_called()
        self.assertTrue(result.get("ignored"))
        self.assertEqual(result.get("reason"), "from_me")

    async def test_telegram_self_chat_message_refreshes_linked_user_id(self) -> None:
        """The per-message state sync now mirrors the WhatsApp handler's
        linked_jid self-heal exactly (see the sync_gateway_personal_channel_state
        call in _handle_telegram_gateway_channel_inbound): now that Telegram's
        message payload carries is_self_chat, a genuine self-chat message
        supplies a NEW linked_user_id via _resolve_linked_identity_for_sync,
        instead of Telegram's pre-fix behavior of only ever preserving
        whatever was already persisted at login. Starts from a DIFFERENT
        linked_user_id ("000") than the self-chat message's own sender_jid
        ("555444333") to prove the sync actually updates it, not merely
        leaves it alone."""
        personal_channels_repository.upsert_telegram_state(
            gateway_id="gw-dm-1",
            tenant_id="tenant-1",
            workspace_id="default",
            user_id="",
            channel_key=personal_channels_service.TELEGRAM_PERSONAL_CHANNEL_KEY,
            agent_id="",
            provider=personal_channels_service.TELEGRAM_PERSONAL_PROVIDER,
            status="connected",
            linked_user_id="000",
        )
        with (
            patch(
                "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                return_value=_ALLOW_DISPATCH_DECISION,
            ),
            patch(
                "server_modules.personal_channels_service.personal_channel_sage_bridge_service.build_telegram_personal_reply",
                return_value={"text": "On it.", "source": "sage"},
            ),
            patch(
                "server_modules.personal_channels_service.gateway_protocol_service.dispatch_channel_outbound",
                new=AsyncMock(return_value={"external_message_id": "tg-out-2"}),
                create=True,
            ),
            patch("server_modules.personal_channels_service.security_audit_service.emit_security_audit_event"),
        ):
            await personal_channels_service._handle_telegram_gateway_channel_inbound(
                gateway_id="gw-dm-1",
                registration=self.registration,
                payload={
                    "channel_key": personal_channels_service.TELEGRAM_PERSONAL_CHANNEL_KEY,
                    "provider": personal_channels_service.TELEGRAM_PERSONAL_PROVIDER,
                    "message": {
                        "external_message_id": "tg-owner-2",
                        "remote_jid": "555444333",
                        "sender_jid": "555444333",
                        "push_name": "Me",
                        "text": "hi self",
                        "from_me": True,
                        "is_self_chat": True,
                    },
                },
            )
        state = personal_channels_repository.get_telegram_state(
            "gw-dm-1", channel_key=personal_channels_service.TELEGRAM_PERSONAL_CHANNEL_KEY, agent_id="",
        )
        self.assertEqual(state["linked_user_id"], "555444333")

    async def test_stranger_message_does_not_clobber_linked_jid(self) -> None:
        """Regression test for the pre-existing clobber bug this build also
        fixed: the per-message state sync used to pass message.sender_jid
        straight through as `linked_jid` — for a plain 1:1 DM that equals
        remote_jid, i.e. the CONTACT's own jid, silently overwriting the
        real owner identity established at login on every single inbound
        message. This would have failed before the fix (state.linked_jid
        would end up equal to the stranger's own jid)."""
        personal_channels_repository.upsert_whatsapp_state(
            gateway_id="gw-dm-1",
            tenant_id="tenant-1",
            workspace_id="default",
            user_id="",
            channel_key=personal_channels_service.WHATSAPP_PERSONAL_CHANNEL_KEY,
            agent_id="",
            provider=personal_channels_service.WHATSAPP_PERSONAL_PROVIDER,
            status="connected",
            linked_jid="15550001111@s.whatsapp.net",
        )
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
            await personal_channels_service._handle_whatsapp_gateway_channel_inbound(
                gateway_id="gw-dm-1",
                registration=self.registration,
                payload={
                    "channel_key": personal_channels_service.WHATSAPP_PERSONAL_CHANNEL_KEY,
                    "provider": personal_channels_service.WHATSAPP_PERSONAL_PROVIDER,
                    "message": {
                        "external_message_id": "wa-stranger-2",
                        "remote_jid": "919999999999@s.whatsapp.net",
                        "sender_jid": "919999999999@s.whatsapp.net",
                        "push_name": "Rando",
                        "text": "hello?",
                        "from_me": False,
                    },
                },
            )
        state = personal_channels_repository.get_whatsapp_state(
            "gw-dm-1", channel_key=personal_channels_service.WHATSAPP_PERSONAL_CHANNEL_KEY, agent_id="",
        )
        self.assertEqual(state["linked_jid"], "15550001111@s.whatsapp.net")

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
                channel_key="signal_personal",
                provider="signal_local_bridge",
                label="Signal",
            )
        self.assertTrue(result.get("blocked"))
        self.assertIsNone(result.get("outbound"))

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
                channel_key="signal_personal",
                provider="signal_local_bridge",
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
                channel_key="signal_personal",
                provider="signal_local_bridge",
                label="Signal",
            )
        build_reply_mock.assert_not_called()
        self.assertTrue(result.get("ignored"))
        self.assertEqual(result.get("reason"), "from_me")


if __name__ == "__main__":
    unittest.main()

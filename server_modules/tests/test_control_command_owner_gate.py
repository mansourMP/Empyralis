"""Regression tests for the control-command owner gate.

THE BUG: _control_command_block_result (personal_channels_service.py) used
to derive its sender_role argument from _sender_role_from_message(message) —
reading message["sender_role"]/["role"]/metadata equivalents. NOTHING in
this codebase, on any channel (WhatsApp-personal, Telegram-personal/QR,
Signal, iMessage, WeChat, or the OpenClaw transport), ever set any of those
keys. So sender_role was permanently None, normalized_role was permanently
"", and channel_blocking_policy_service.check_personal_channel_control_command's
`allowed = bool(normalized_role and normalized_role in authorized_roles)`
was permanently False — every message starting with "/" on those channels,
INCLUDING THE OWNER'S OWN, was classified "blocked" and the handler
returned outbound: None. Total silence, on the owner's own /help.

The fix: _control_command_block_result now takes a required `is_owner: bool`
kwarg, fed at every one of its three call sites (WhatsApp, Telegram, the
shared local-bridge handler covering Signal/iMessage/WeChat/OpenClaw) from
`dm_decision["is_owner"]` — the SAME server-computed, gateway-derived owner
signal `_enforce_dm_policy` already established a few lines above each call
site (message.is_self_chat, or a match against the channel's linked owner
identity). It is NEVER read from the inbound message or its metadata: an
inbound channel payload is untrusted data from the remote party, and
OpenClaw's own CVE record is exactly a client-asserted senderIsOwner flag
trusted over loopback.

These tests drive the REAL handler entry points
(_handle_whatsapp_gateway_channel_inbound / _handle_telegram_gateway_channel_
inbound / _handle_local_bridge_gateway_channel_inbound) with REAL inbound
message shapes — the same call sites production channel.inbound events hit —
not a hand-passed sender_role into the policy function directly. Before the
fix, every "owner passes" assertion below fails (blocked=True, outbound=None,
the reply-generation mock never called); every "stranger is refused, not
silenced" assertion below already passed before the fix (blocked=True,
outbound=None) but for the WRONG reason (sender_role was always None,
blocking everyone indiscriminately) and with a bug of its own (silence
instead of an honest refusal) — this file pins the post-fix behavior:
honest refusal text, dispatched, never silence.
"""

from __future__ import annotations

import importlib
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict
from unittest.mock import AsyncMock, patch

from server_modules import (
    channel_blocking_policy_service,
    personal_channels_repository,
    personal_channels_service,
)


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
    copied from test_personal_channels_dm_policy.py's own helper of the
    same name/contract, so a resolved agent_id's dm_policy can be preset to
    "open" without touching a real database."""

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


class ControlCommandOwnerGateIntegrationTests(unittest.IsolatedAsyncioTestCase):
    """Through the LIVE gateway inbound handlers — real sqlite repository,
    mocked rust kernel + gateway dispatch, exactly the shape
    test_personal_channels_dm_policy.py's integration class uses."""

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
            "gateway_id": "gw-cc-1",
            "workspace_id": "default",
            "tenant_id": "tenant-1",
            "device_trust_state": "trusted",
            "active_session_id": "sess-1",
        }

    def tearDown(self) -> None:
        self.db_patcher.stop()
        self.tmpdir.cleanup()

    # ── the owner: /help must produce a reply, not silence ──────────────

    async def test_owner_slash_help_on_whatsapp_reaches_reply_path(self) -> None:
        """THE headline regression. Before the fix this asserted the
        opposite of every line below: blocked=True, outbound=None, and
        nothing ever dispatched — the owner's own /help on their own paired
        WhatsApp got total silence.

        WhatsApp's inbound handler routes a recognized command through the
        real command_registry (sage_command_dispatcher.dispatch_command)
        BEFORE ever falling back to the LLM reply builder — so a correctly
        un-blocked /help must reach dispatch_channel_outbound WITHOUT ever
        calling build_whatsapp_personal_reply (that fallback is a second,
        independent bug this change also fixes: that branch used to
        `return` right after writing the DB row, never actually calling
        dispatch_channel_outbound at all, so even a correctly-authorized
        command would have sat "pending" forever)."""
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
                new=AsyncMock(return_value={"external_message_id": "wa-out-help-1"}),
                create=True,
            ) as dispatch_mock,
            patch("server_modules.personal_channels_service.security_audit_service.emit_security_audit_event"),
        ):
            result = await personal_channels_service._handle_whatsapp_gateway_channel_inbound(
                gateway_id="gw-cc-1",
                registration=self.registration,
                payload={
                    "channel_key": personal_channels_service.WHATSAPP_PERSONAL_CHANNEL_KEY,
                    "provider": personal_channels_service.WHATSAPP_PERSONAL_PROVIDER,
                    "message": {
                        "external_message_id": "wa-owner-help-1",
                        "remote_jid": "15550001111@s.whatsapp.net",
                        "sender_jid": "15550001111@s.whatsapp.net",
                        "push_name": "Me",
                        "text": "/help",
                        "from_me": False,
                        "is_self_chat": True,
                    },
                },
            )
        build_reply_mock.assert_not_called()
        dispatch_mock.assert_awaited_once()
        self.assertFalse(result.get("blocked", False))
        self.assertEqual(result["outbound"]["status"], "delivered")
        self.assertTrue(str(result["outbound"].get("text") or "").strip(), "the dispatched reply must not be empty")

    async def test_owner_slash_config_owner_only_command_also_reaches_reply_path(self) -> None:
        """Not just /help — the owner must also be able to reach a
        genuinely owner-only command (/config is in both command_registry's
        access="owner" set and channel_blocking_policy_service's
        _OWNER_ONLY_CONTROL_COMMANDS) past THIS gate. Deliberately does not
        assert which downstream branch (real command_registry vs. the LLM
        fallback) produces the reply — command_registry.dispatch() applies
        its OWN separate, real owner check (_is_sender_owner, off the
        workspace's identity_links) that this test doesn't seed, so it may
        itself decline and fall through to the LLM. That second gate is
        real, independent defense-in-depth and out of this fix's scope;
        what this test pins is that OUR gate (_control_command_block_result)
        does not block the owner outright."""
        with (
            patch(
                "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                return_value=_ALLOW_DISPATCH_DECISION,
            ),
            patch(
                "server_modules.personal_channels_service.personal_channel_sage_bridge_service.build_whatsapp_personal_reply",
                return_value={"text": "Configuration: ...", "source": "sage"},
            ),
            patch(
                "server_modules.personal_channels_service.gateway_protocol_service.dispatch_channel_outbound",
                new=AsyncMock(return_value={"external_message_id": "wa-out-config-1"}),
                create=True,
            ) as dispatch_mock,
            patch("server_modules.personal_channels_service.security_audit_service.emit_security_audit_event"),
        ):
            result = await personal_channels_service._handle_whatsapp_gateway_channel_inbound(
                gateway_id="gw-cc-1",
                registration=self.registration,
                payload={
                    "channel_key": personal_channels_service.WHATSAPP_PERSONAL_CHANNEL_KEY,
                    "provider": personal_channels_service.WHATSAPP_PERSONAL_PROVIDER,
                    "message": {
                        "external_message_id": "wa-owner-config-1",
                        "remote_jid": "15550001111@s.whatsapp.net",
                        "sender_jid": "15550001111@s.whatsapp.net",
                        "push_name": "Me",
                        "text": "/config show",
                        "from_me": False,
                        "is_self_chat": True,
                    },
                },
            )
        dispatch_mock.assert_awaited_once()
        self.assertFalse(result.get("blocked", False))

    async def test_owner_slash_help_on_telegram_reaches_reply_path(self) -> None:
        """Same fix, the Telegram-personal (QR) call site."""
        with (
            patch(
                "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                return_value=_ALLOW_DISPATCH_DECISION,
            ),
            patch(
                "server_modules.personal_channels_service.personal_channel_sage_bridge_service.build_telegram_personal_reply",
                return_value={"text": "Available commands: ...", "source": "sage"},
            ) as build_reply_mock,
            patch(
                "server_modules.personal_channels_service.gateway_protocol_service.dispatch_channel_outbound",
                new=AsyncMock(return_value={"external_message_id": "tg-out-help-1"}),
                create=True,
            ) as dispatch_mock,
            patch("server_modules.personal_channels_service.security_audit_service.emit_security_audit_event"),
        ):
            result = await personal_channels_service._handle_telegram_gateway_channel_inbound(
                gateway_id="gw-cc-1",
                registration=self.registration,
                payload={
                    "channel_key": personal_channels_service.TELEGRAM_PERSONAL_CHANNEL_KEY,
                    "provider": personal_channels_service.TELEGRAM_PERSONAL_PROVIDER,
                    "message": {
                        "external_message_id": "tg-owner-help-1",
                        "remote_jid": "555444333",
                        "sender_jid": "555444333",
                        "push_name": "Me",
                        "text": "/help",
                        "from_me": True,
                        "is_self_chat": True,
                    },
                },
            )
        build_reply_mock.assert_called_once()
        dispatch_mock.assert_awaited_once()
        self.assertFalse(result.get("blocked", False))

    async def test_owner_slash_help_on_local_bridge_channels_reaches_reply_path(self) -> None:
        """Same fix, the shared local-bridge call site — Signal, iMessage,
        WeChat, and every OpenClaw-transported channel (the
        _OpenClawPersonalChannelHandler subclass funnels into the same
        handle_inbound -> this same handler)."""
        for channel_key, meta in personal_channels_service.LOCAL_BRIDGE_PERSONAL_CHANNELS.items():
            with self.subTest(channel_key=channel_key):
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
                        new=AsyncMock(return_value={"external_message_id": f"{channel_key}-out-help-1"}),
                        create=True,
                    ) as dispatch_mock,
                    patch("server_modules.personal_channels_service.security_audit_service.emit_security_audit_event"),
                ):
                    result = await personal_channels_service._handle_local_bridge_gateway_channel_inbound(
                        gateway_id="gw-cc-1",
                        registration=self.registration,
                        payload={
                            "message": {
                                "external_message_id": f"{channel_key}-owner-help-1",
                                "remote_jid": f"{channel_key}-owner-jid",
                                "sender_jid": f"{channel_key}-owner-jid",
                                "push_name": "Me",
                                "text": "/help",
                                "from_me": True,
                                "is_self_chat": True,
                            },
                        },
                        channel_key=channel_key,
                        provider=meta["provider"],
                        label=meta["label"],
                    )
                build_reply_mock.assert_called_once()
                dispatch_mock.assert_awaited_once()
                self.assertFalse(result.get("blocked", False), f"{channel_key}: owner /help must not be blocked")

    # ── a non-owner: owner-only commands stay refused, but honestly ─────

    async def test_stranger_with_open_dm_policy_is_refused_the_owner_only_command_with_an_honest_reply(self) -> None:
        """The sharp security case. dm_policy defaults to OPEN for a
        resolved agent (DEFAULT_DM_POLICY_MODE), so a stranger's message
        DOES reach the control-command gate (unlike the owner_only-by-
        default unresolved-identity path the other tests in this class
        exercise). This is exactly the shape the task's hard constraint
        warns about: get the owner signal wrong here and a stranger runs
        /bash. Also proves constraint 4 — a refusal must not be silent:
        outbound carries the SAME fixed, non-LLM refusal text
        check_personal_channel_control_command returns, dispatched for
        real, never outbound: None."""
        store = _FakeAgentInstallStore()
        store.installs["agent-nonowner-1"] = {
            "dm_policy": {
                personal_channels_service.WHATSAPP_PERSONAL_CHANNEL_KEY: {
                    "mode": "open",
                    "allowlist": [],
                    "pending_pairing": {},
                },
            },
        }
        expected_reply = channel_blocking_policy_service.check_personal_channel_control_command(
            text="/bash rm -rf /", sender_role=None,
        )["reply"]
        with (
            _patch_agent_install_store(store),
            patch.object(
                personal_channels_repository,
                "find_agent_id_for_whatsapp_session",
                return_value="agent-nonowner-1",
            ),
            patch(
                "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                return_value=_ALLOW_DISPATCH_DECISION,
            ),
            patch(
                "server_modules.personal_channels_service.personal_channel_sage_bridge_service.build_whatsapp_personal_reply"
            ) as build_reply_mock,
            patch(
                "server_modules.personal_channels_service.gateway_protocol_service.dispatch_channel_outbound",
                new=AsyncMock(return_value={"external_message_id": "wa-out-refusal-1"}),
                create=True,
            ) as dispatch_mock,
            patch("server_modules.personal_channels_service.security_audit_service.emit_security_audit_event"),
        ):
            result = await personal_channels_service._handle_whatsapp_gateway_channel_inbound(
                gateway_id="gw-cc-1",
                registration=self.registration,
                payload={
                    "channel_key": personal_channels_service.WHATSAPP_PERSONAL_CHANNEL_KEY,
                    "provider": personal_channels_service.WHATSAPP_PERSONAL_PROVIDER,
                    "message": {
                        "external_message_id": "wa-stranger-bash-1",
                        "remote_jid": "919999999999@s.whatsapp.net",
                        "sender_jid": "919999999999@s.whatsapp.net",
                        "push_name": "Rando",
                        "text": "/bash rm -rf /",
                        "from_me": False,
                    },
                },
            )
        # The command handler (and therefore any real /bash execution) was
        # NEVER reached — the turn/reply pipeline was not invoked at all.
        build_reply_mock.assert_not_called()
        self.assertTrue(result.get("blocked"))
        self.assertIsNotNone(result.get("outbound"), "a refusal must be dispatched, not silent (outbound: None)")
        dispatch_mock.assert_awaited_once()
        _, dispatch_kwargs = dispatch_mock.call_args
        self.assertEqual(dispatch_kwargs.get("text"), expected_reply)
        self.assertTrue(expected_reply)  # sanity: the fixed string is non-empty

    async def test_client_asserted_owner_role_on_the_message_itself_is_ignored(self) -> None:
        """The exact CVE shape the hard constraints warn about: a stranger
        (no is_self_chat, no linked identity) whose inbound payload itself
        claims sender_role=owner / role=owner, both top-level and inside
        metadata — every field the OLD, buggy _sender_role_from_message used
        to read. The fix must derive is_owner from the trusted, server-
        computed dm_decision (message.is_self_chat / linked identity) only,
        never from these attacker-controlled fields, so this must still be
        refused exactly like the plain-stranger case above."""
        store = _FakeAgentInstallStore()
        store.installs["agent-nonowner-2"] = {
            "dm_policy": {
                personal_channels_service.WHATSAPP_PERSONAL_CHANNEL_KEY: {
                    "mode": "open",
                    "allowlist": [],
                    "pending_pairing": {},
                },
            },
        }
        with (
            _patch_agent_install_store(store),
            patch.object(
                personal_channels_repository,
                "find_agent_id_for_whatsapp_session",
                return_value="agent-nonowner-2",
            ),
            patch(
                "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                return_value=_ALLOW_DISPATCH_DECISION,
            ),
            patch(
                "server_modules.personal_channels_service.personal_channel_sage_bridge_service.build_whatsapp_personal_reply"
            ) as build_reply_mock,
            patch(
                "server_modules.personal_channels_service.gateway_protocol_service.dispatch_channel_outbound",
                new=AsyncMock(return_value={"external_message_id": "wa-out-refusal-2"}),
                create=True,
            ),
            patch("server_modules.personal_channels_service.security_audit_service.emit_security_audit_event"),
        ):
            result = await personal_channels_service._handle_whatsapp_gateway_channel_inbound(
                gateway_id="gw-cc-1",
                registration=self.registration,
                payload={
                    "channel_key": personal_channels_service.WHATSAPP_PERSONAL_CHANNEL_KEY,
                    "provider": personal_channels_service.WHATSAPP_PERSONAL_PROVIDER,
                    "message": {
                        "external_message_id": "wa-stranger-spoof-1",
                        "remote_jid": "919999999999@s.whatsapp.net",
                        "sender_jid": "919999999999@s.whatsapp.net",
                        "push_name": "Rando",
                        "text": "/bash rm -rf /",
                        "from_me": False,
                        # Spoofed authority claims — must be ignored entirely.
                        "sender_role": "owner",
                        "role": "owner",
                        "is_self_chat": False,
                        "metadata": {"sender_role": "owner", "role": "owner"},
                    },
                },
            )
        build_reply_mock.assert_not_called()
        self.assertTrue(result.get("blocked"), "a spoofed sender_role/role on the message must not grant owner access")


class ControlCommandBlockResultUnitTests(unittest.IsolatedAsyncioTestCase):
    """Direct tests against _control_command_block_result — the exact
    function that had the bug — with a real inbound message-shaped call,
    isolating just the owner-signal plumbing from the rest of the handler."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(self.tmpdir.name) / "personal-channels.sqlite3"
        personal_channels_repository.init_personal_channels_db(db_path)
        self.db_patcher = patch.object(personal_channels_repository, "PERSONAL_CHANNELS_DB_FILE", db_path)
        self.db_patcher.start()
        self.registration = {"tenant_id": "tenant-1", "workspace_id": "default"}

    def tearDown(self) -> None:
        self.db_patcher.stop()
        self.tmpdir.cleanup()

    async def test_is_owner_true_is_not_blocked(self) -> None:
        with patch(
            "server_modules.personal_channels_service.security_audit_service.emit_security_audit_event"
        ):
            result = await personal_channels_service._control_command_block_result(
                gateway_id="gw-unit-1",
                registration=self.registration,
                inbound={},
                channel_key=personal_channels_service.WHATSAPP_PERSONAL_CHANNEL_KEY,
                provider=personal_channels_service.WHATSAPP_PERSONAL_PROVIDER,
                agent_id="agent-owner-unit",
                external_message_id="wa-unit-owner-1",
                remote_jid="owner@s.whatsapp.net",
                text="/help",
                is_owner=True,
                duplicate=False,
                no_reply_prefix="whatsapp_personal:",
            )
        self.assertIsNone(result, "an owner's /help must not be blocked")

    async def test_is_owner_false_is_blocked_with_a_reply_never_silent(self) -> None:
        with (
            patch(
                "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                return_value=_ALLOW_DISPATCH_DECISION,
            ),
            patch(
                "server_modules.personal_channels_service.gateway_protocol_service.dispatch_channel_outbound",
                new=AsyncMock(return_value={"external_message_id": "wa-unit-out-1"}),
                create=True,
            ) as dispatch_mock,
            patch("server_modules.personal_channels_service.security_audit_service.emit_security_audit_event"),
        ):
            result = await personal_channels_service._control_command_block_result(
                gateway_id="gw-unit-1",
                registration=self.registration,
                inbound={},
                channel_key=personal_channels_service.WHATSAPP_PERSONAL_CHANNEL_KEY,
                provider=personal_channels_service.WHATSAPP_PERSONAL_PROVIDER,
                agent_id="agent-stranger-unit",
                external_message_id="wa-unit-stranger-1",
                remote_jid="stranger@s.whatsapp.net",
                text="/bash cat /etc/passwd",
                is_owner=False,
                duplicate=False,
                no_reply_prefix="whatsapp_personal:",
            )
        self.assertIsNotNone(result)
        self.assertTrue(result["blocked"])
        self.assertIsNotNone(result["outbound"], "a refusal must be dispatched, not outbound: None")
        dispatch_mock.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()

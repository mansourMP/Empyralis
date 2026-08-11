"""Regression tests: /commands must execute on EVERY personal channel, not
just WhatsApp, hosted Telegram, and cloud channels.

THE BUG (fixed alongside this test). sage_command_dispatcher.dispatch_command
-> command_registry (24 commands: /new /main /compact /stop /clear /export
/model /thinking /help /commands /tools /status /whoami /usage /memory
/forget /tasks /agents /skills /config /mcp /plugins /debug /tts /bash) was
only ever reached from _deliver_whatsapp_personal_reply,
handle_cloud_channel_inbound, and the hosted-Telegram route.

_deliver_local_bridge_personal_reply (Signal, iMessage, WeChat, and every
openclaw_* transported channel) and _handle_telegram_gateway_channel_inbound
(Telegram-personal / QR) never dispatched a command at all: an owner's
"/compact" passed every authorization gate and then fell through to an
ordinary LLM turn, which received the literal text "/compact" and chatted
about it instead of the command running.

Fixed by giving every Gateway-WS delivery path ONE shared waist to cross —
_dispatch_personal_channel_command — instead of each path growing its own
copy of the dispatch-and-deliver block (which is exactly how the WhatsApp
branch got its own bug: it wrote the outbound row and returned WITHOUT ever
calling gateway_protocol_service.dispatch_channel_outbound, so a command's
reply was created but never sent).

These tests assert CALL COUNTS, not just "something happened" — an
`sage_command_dispatcher.dispatch_command` call satisfied by a mock that
never gets its reply dispatched is exactly the pre-fix WhatsApp bug in a new
outfit. Each test also asserts the ordinary agent-turn reply builder was
NEVER invoked for command text, proving the command short-circuits a normal
turn rather than running alongside it.

Message payloads set `sender_role: "owner"` deliberately: the pre-existing,
unrelated `_control_command_block_result` gate
(channel_blocking_policy_service.check_personal_channel_control_command)
treats ANY leading "/word" as a control command requiring an explicit
owner/admin sender_role and blocks it otherwise — that gate runs upstream of
everything this test exercises and is not something these tests touch or
re-decide; it is simply satisfied here so the code path under test is
reached at all, exactly as CLAUDE.md's constraint 5 requires (authorization
is not re-derived here).
"""

from __future__ import annotations

import importlib
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

from server_modules import personal_channels_service, personal_channels_repository


_ALLOW_DISPATCH_DECISION = {
    "ok": True,
    "decision": "allow",
    "reason": "gateway_service_operation_allowed",
    "operation": "protocol_route",
    "next_action": "dispatch_gateway_operation",
}

_ALLOWED_OWNER_DM_DECISION = {
    "allowed": True,
    "mode": "owner_only",
    "sender_id": "+15551234567",
    "is_owner": True,
    "system_reply": None,
    "config_changed": False,
}


class _FakeAgentInstallStore:
    """Minimal stand-in for agent_registry_repository's install-bundle
    contract, same shape used by test_local_bridge_inbound_processed_scope.py
    — enough for the group/dm policy config readers to round-trip against."""

    def __init__(self) -> None:
        self.installs: Dict[str, Dict[str, Any]] = {}

    async def get_bundle(self, agent_id: str, *, tenant_id: str, workspace_id: str):
        return {"id": agent_id, "install_metadata": dict(self.installs.get(agent_id, {}))}

    async def update(self, agent_id: str, *, tenant_id: str, workspace_id: str, metadata=None, **_kwargs):
        merged = {**self.installs.get(agent_id, {}), **dict(metadata or {})}
        self.installs[agent_id] = merged
        return {"id": agent_id, "install_metadata": dict(merged)}


def _patch_agent_install_store(store: "_FakeAgentInstallStore"):
    return patch.multiple(
        "server_modules.agent_registry_repository",
        get_workspace_agent_install_bundle=AsyncMock(side_effect=store.get_bundle),
        update_workspace_agent_install=AsyncMock(side_effect=store.update),
    )


class LocalBridgeCommandDispatchTests(unittest.IsolatedAsyncioTestCase):
    """Signal and an OpenClaw-transported channel (both routed through
    _handle_local_bridge_gateway_channel_inbound -> _deliver_local_bridge_
    personal_reply) must execute /commands, not chat about them."""

    GATEWAY_ID = "gw-cmd-local-1"
    AGENT_ID = "agent-cmd-local-1"

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
            "gateway_id": self.GATEWAY_ID,
            "workspace_id": "ws-cmd-local",
            "tenant_id": "tenant-cmd-local",
            "device_trust_state": "trusted",
            "active_session_id": "sess-1",
        }
        self.store = _FakeAgentInstallStore()

    def tearDown(self) -> None:
        self.db_patcher.stop()
        self.tmpdir.cleanup()

    def _claim(self, *, channel_key: str, provider: str) -> None:
        personal_channels_repository.upsert_local_bridge_state(
            gateway_id=self.GATEWAY_ID,
            tenant_id="tenant-cmd-local",
            workspace_id="ws-cmd-local",
            user_id="",
            channel_key=channel_key,
            agent_id=self.AGENT_ID,
            provider=provider,
            status="linked",
        )

    def _payload(self, *, external_message_id: str, text: str, provider: str) -> Dict[str, Any]:
        return {
            "provider": provider,
            "message": {
                "external_message_id": external_message_id,
                "remote_jid": "+15551234567",
                "sender_jid": "+15551234567",
                "push_name": "Mansur",
                "text": text,
                "from_me": False,
                "is_group": False,
                # Satisfies the pre-existing, unrelated control-command
                # gate — see this module's docstring.
                "sender_role": "owner",
            },
        }

    async def _run(
        self,
        *,
        channel_key: str,
        provider: str,
        label: str,
        text: str,
        external_message_id: str,
        command_reply: str,
    ):
        self._claim(channel_key=channel_key, provider=provider)
        dispatch_command_mock = AsyncMock(return_value=command_reply)
        outbound_dispatch_mock = AsyncMock(return_value={"external_message_id": f"{channel_key}-out-1"})
        reply_builder_mock = AsyncMock(return_value={"text": "I would have chatted about it.", "source": "sage"})
        with (
            _patch_agent_install_store(self.store),
            patch(
                "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                return_value=_ALLOW_DISPATCH_DECISION,
            ),
            patch(
                "server_modules.personal_channels_service._enforce_dm_policy",
                new=AsyncMock(return_value=dict(_ALLOWED_OWNER_DM_DECISION)),
            ),
            patch(
                "server_modules.sage_command_dispatcher.dispatch_command",
                new=dispatch_command_mock,
            ),
            patch(
                "server_modules.personal_channels_service.personal_channel_sage_bridge_service"
                ".build_personal_channel_reply_async",
                new=reply_builder_mock,
                create=True,
            ),
            patch(
                "server_modules.personal_channels_service.gateway_protocol_service.dispatch_channel_outbound",
                new=outbound_dispatch_mock,
                create=True,
            ) as dispatch_mock,
            patch("server_modules.personal_channels_service.security_audit_service.emit_security_audit_event"),
        ):
            result = await personal_channels_service._handle_local_bridge_gateway_channel_inbound(
                gateway_id=self.GATEWAY_ID,
                registration=self.registration,
                payload=self._payload(external_message_id=external_message_id, text=text, provider=provider),
                channel_key=channel_key,
                provider=provider,
                label=label,
            )
        return result, dispatch_command_mock, dispatch_mock, reply_builder_mock

    async def test_signal_help_command_executes_and_dispatches(self) -> None:
        result, dispatch_command_mock, dispatch_mock, reply_builder_mock = await self._run(
            channel_key="signal_personal",
            provider="signal_local_bridge",
            label="Signal",
            text="/help",
            external_message_id="sig-cmd-help-1",
            command_reply="Available commands: /help /compact /new ...",
        )

        # The registry was actually reached, with the real command text.
        dispatch_command_mock.assert_awaited_once()
        self.assertEqual(dispatch_command_mock.call_args.kwargs["command"], "/help")
        self.assertEqual(dispatch_command_mock.call_args.kwargs["channel_origin"], "signal_personal")

        # Its reply was durably dispatched — not just written to a DB row
        # and abandoned (the pre-fix WhatsApp bug).
        dispatch_mock.assert_awaited_once()
        self.assertEqual(
            dispatch_mock.call_args.kwargs["text"],
            "Available commands: /help /compact /new ...",
        )
        self.assertEqual(dispatch_mock.call_args.kwargs["channel_key"], "signal_personal")

        # A command never triggers an ordinary LLM turn alongside it.
        reply_builder_mock.assert_not_awaited()

        self.assertEqual(result["outbound"]["status"], "delivered")
        self.assertTrue(result.get("command_handled"))

    async def test_signal_compact_command_executes_and_dispatches(self) -> None:
        """/compact is state-changing (it mutates thread history), unlike
        /help — proving the wiring generalizes past a read-only command."""
        result, dispatch_command_mock, dispatch_mock, reply_builder_mock = await self._run(
            channel_key="signal_personal",
            provider="signal_local_bridge",
            label="Signal",
            text="/compact",
            external_message_id="sig-cmd-compact-1",
            command_reply="Conversation compacted.",
        )

        dispatch_command_mock.assert_awaited_once()
        self.assertEqual(dispatch_command_mock.call_args.kwargs["command"], "/compact")

        dispatch_mock.assert_awaited_once()
        self.assertEqual(dispatch_mock.call_args.kwargs["text"], "Conversation compacted.")

        reply_builder_mock.assert_not_awaited()
        self.assertEqual(result["outbound"]["status"], "delivered")

    async def test_openclaw_channel_help_command_executes_and_dispatches(self) -> None:
        """The whole point of routing OpenClaw channels through the SAME
        _handle_local_bridge_gateway_channel_inbound function as Signal:
        this must work identically for a transported channel with zero
        channel-specific code."""
        channel_key = "openclaw_feishu"
        provider = personal_channels_service.OPENCLAW_PERSONAL_CHANNELS[channel_key]["provider"]
        result, dispatch_command_mock, dispatch_mock, reply_builder_mock = await self._run(
            channel_key=channel_key,
            provider=provider,
            label="Feishu",
            text="/help",
            external_message_id="feishu-cmd-help-1",
            command_reply="Available commands: /help /compact /new ...",
        )

        dispatch_command_mock.assert_awaited_once()
        self.assertEqual(dispatch_command_mock.call_args.kwargs["channel_origin"], channel_key)

        dispatch_mock.assert_awaited_once()
        self.assertEqual(dispatch_mock.call_args.kwargs["channel_key"], channel_key)

        reply_builder_mock.assert_not_awaited()
        self.assertEqual(result["outbound"]["status"], "delivered")

    async def test_redelivery_of_a_command_never_re_dispatches(self) -> None:
        """The same idempotency contract every other reply on this path
        gets: a redelivered inbound event (this transport is at-least-once
        end to end) must not run the command, or send its reply, twice."""
        self._claim(channel_key="signal_personal", provider="signal_local_bridge")
        dispatch_command_mock = AsyncMock(return_value="Available commands: ...")
        outbound_dispatch_mock = AsyncMock(return_value={"external_message_id": "signal-out-1"})
        payload = self._payload(
            external_message_id="sig-cmd-redelivery-1", text="/help", provider="signal_local_bridge",
        )
        with (
            _patch_agent_install_store(self.store),
            patch(
                "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                return_value=_ALLOW_DISPATCH_DECISION,
            ),
            patch(
                "server_modules.personal_channels_service._enforce_dm_policy",
                new=AsyncMock(return_value=dict(_ALLOWED_OWNER_DM_DECISION)),
            ),
            patch("server_modules.sage_command_dispatcher.dispatch_command", new=dispatch_command_mock),
            patch(
                "server_modules.personal_channels_service.gateway_protocol_service.dispatch_channel_outbound",
                new=outbound_dispatch_mock,
                create=True,
            ) as dispatch_mock,
            patch("server_modules.personal_channels_service.security_audit_service.emit_security_audit_event"),
        ):
            for _ in range(2):
                result = await personal_channels_service._handle_local_bridge_gateway_channel_inbound(
                    gateway_id=self.GATEWAY_ID,
                    registration=self.registration,
                    payload=payload,
                    channel_key="signal_personal",
                    provider="signal_local_bridge",
                    label="Signal",
                )

        self.assertEqual(dispatch_command_mock.await_count, 1, "a redelivery must not re-run the command")
        self.assertEqual(dispatch_mock.await_count, 1, "a redelivery must not re-send the command's reply")
        self.assertEqual(result["outbound"]["status"], "delivered")


class TelegramPersonalCommandDispatchTests(unittest.IsolatedAsyncioTestCase):
    """Telegram-personal (the QR-paired handler,
    _handle_telegram_gateway_channel_inbound) must also execute /commands —
    it built its reply inline via personal_channel_sage_bridge_service.
    build_telegram_personal_reply and never dispatched a command at all."""

    GATEWAY_ID = "gw-cmd-tg-1"

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
            "gateway_id": self.GATEWAY_ID,
            "workspace_id": "ws-cmd-tg",
            "tenant_id": "tenant-cmd-tg",
            "device_trust_state": "trusted",
            "active_session_id": "sess-1",
        }
        # Establish "123456789" as this channel's own linked owner identity
        # (self-chat), exactly as test_personal_channels_service_natural_
        # reply.py does, so the inbound message below is recognized by the
        # REAL dmPolicy gate as the owner rather than tripping owner_only's
        # default-deny.
        personal_channels_repository.upsert_telegram_state(
            gateway_id=self.GATEWAY_ID,
            tenant_id="tenant-cmd-tg",
            workspace_id="ws-cmd-tg",
            user_id="",
            channel_key=personal_channels_service.TELEGRAM_PERSONAL_CHANNEL_KEY,
            agent_id="",
            provider=personal_channels_service.TELEGRAM_PERSONAL_PROVIDER,
            status="connected",
            linked_user_id="123456789",
        )

    def tearDown(self) -> None:
        self.db_patcher.stop()
        self.tmpdir.cleanup()

    def _payload(self, *, external_message_id: str, text: str) -> Dict[str, Any]:
        return {
            "provider": personal_channels_service.TELEGRAM_PERSONAL_PROVIDER,
            "message": {
                "external_message_id": external_message_id,
                "remote_jid": "123456789",
                "sender_jid": "123456789",
                "push_name": "Mansur",
                "text": text,
                "from_me": False,
                # Satisfies the pre-existing, unrelated control-command
                # gate — see this module's docstring.
                "sender_role": "owner",
            },
        }

    async def _run(self, *, text: str, external_message_id: str, command_reply: str):
        dispatch_command_mock = AsyncMock(return_value=command_reply)
        outbound_dispatch_mock = AsyncMock(return_value={"external_message_id": "tg-cmd-out-1"})
        # build_telegram_personal_reply is called SYNCHRONOUSLY (no await) in
        # personal_channels_service — a plain Mock, not AsyncMock, matches
        # its real call shape.
        reply_builder_mock = MagicMock(return_value={"text": "I would have chatted about it.", "source": "sage"})
        with (
            patch(
                "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                return_value=_ALLOW_DISPATCH_DECISION,
            ),
            patch("server_modules.sage_command_dispatcher.dispatch_command", new=dispatch_command_mock),
            patch(
                "server_modules.personal_channels_service.personal_channel_sage_bridge_service"
                ".build_telegram_personal_reply",
                new=reply_builder_mock,
            ),
            patch(
                "server_modules.personal_channels_service.gateway_protocol_service.dispatch_channel_outbound",
                new=outbound_dispatch_mock,
                create=True,
            ) as dispatch_mock,
            patch("server_modules.personal_channels_service.security_audit_service.emit_security_audit_event"),
        ):
            result = await personal_channels_service._handle_telegram_gateway_channel_inbound(
                gateway_id=self.GATEWAY_ID,
                registration=self.registration,
                payload=self._payload(external_message_id=external_message_id, text=text),
            )
        return result, dispatch_command_mock, dispatch_mock, reply_builder_mock

    async def test_telegram_help_command_executes_and_dispatches(self) -> None:
        result, dispatch_command_mock, dispatch_mock, reply_builder_mock = await self._run(
            text="/help",
            external_message_id="tg-cmd-help-1",
            command_reply="Available commands: /help /compact /new ...",
        )

        dispatch_command_mock.assert_awaited_once()
        self.assertEqual(dispatch_command_mock.call_args.kwargs["command"], "/help")
        self.assertEqual(dispatch_command_mock.call_args.kwargs["channel_origin"], "telegram_personal")

        dispatch_mock.assert_awaited_once()
        self.assertEqual(
            dispatch_mock.call_args.kwargs["text"],
            "Available commands: /help /compact /new ...",
        )
        self.assertEqual(dispatch_mock.call_args.kwargs["channel_key"], "telegram_personal")

        reply_builder_mock.assert_not_called()
        self.assertEqual(result["outbound"]["status"], "delivered")
        self.assertTrue(result.get("command_handled"))

    async def test_telegram_compact_command_executes_and_dispatches(self) -> None:
        result, dispatch_command_mock, dispatch_mock, reply_builder_mock = await self._run(
            text="/compact",
            external_message_id="tg-cmd-compact-1",
            command_reply="Conversation compacted.",
        )

        dispatch_command_mock.assert_awaited_once()
        self.assertEqual(dispatch_command_mock.call_args.kwargs["command"], "/compact")

        dispatch_mock.assert_awaited_once()
        self.assertEqual(dispatch_mock.call_args.kwargs["text"], "Conversation compacted.")

        reply_builder_mock.assert_not_called()
        self.assertEqual(result["outbound"]["status"], "delivered")

    async def test_redelivery_of_a_command_never_re_dispatches(self) -> None:
        dispatch_command_mock = AsyncMock(return_value="Available commands: ...")
        outbound_dispatch_mock = AsyncMock(return_value={"external_message_id": "tg-out-1"})
        payload = self._payload(external_message_id="tg-cmd-redelivery-1", text="/help")
        with (
            patch(
                "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                return_value=_ALLOW_DISPATCH_DECISION,
            ),
            patch("server_modules.sage_command_dispatcher.dispatch_command", new=dispatch_command_mock),
            patch(
                "server_modules.personal_channels_service.gateway_protocol_service.dispatch_channel_outbound",
                new=outbound_dispatch_mock,
                create=True,
            ) as dispatch_mock,
            patch("server_modules.personal_channels_service.security_audit_service.emit_security_audit_event"),
        ):
            for _ in range(2):
                result = await personal_channels_service._handle_telegram_gateway_channel_inbound(
                    gateway_id=self.GATEWAY_ID,
                    registration=self.registration,
                    payload=payload,
                )

        self.assertEqual(dispatch_command_mock.await_count, 1, "a redelivery must not re-run the command")
        self.assertEqual(dispatch_mock.await_count, 1, "a redelivery must not re-send the command's reply")
        self.assertEqual(result["outbound"]["status"], "delivered")


class WhatsAppCommandDispatchActuallySendsTests(unittest.IsolatedAsyncioTestCase):
    """WhatsApp already reached dispatch_command before this change, but its
    own inline block had the OTHER bug this fix must not reintroduce: it
    wrote the command's reply into the outbound table and returned
    immediately, WITHOUT ever calling gateway_protocol_service.dispatch_
    channel_outbound. The reply was created and left "pending" forever —
    nothing ever actually sent it to WhatsApp. Routing WhatsApp through the
    same _dispatch_personal_channel_command waist as the other two channels
    is what fixes this too; assert the fix, not just the reachability."""

    def setUp(self) -> None:
        global personal_channels_service, personal_channels_repository
        personal_channels_service = importlib.import_module("server_modules.personal_channels_service")
        personal_channels_repository = importlib.import_module("server_modules.personal_channels_repository")

        self.tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(self.tmpdir.name) / "personal-channels.sqlite3"
        personal_channels_repository.init_personal_channels_db(db_path)
        self.db_patcher = patch.object(personal_channels_repository, "PERSONAL_CHANNELS_DB_FILE", db_path)
        self.db_patcher.start()

    def tearDown(self) -> None:
        self.db_patcher.stop()
        self.tmpdir.cleanup()

    async def test_whatsapp_command_reply_is_actually_dispatched_not_just_written(self) -> None:
        dispatch_command_mock = AsyncMock(return_value="Available commands: /help /compact /new ...")
        outbound_dispatch_mock = AsyncMock(return_value={"external_message_id": "wa-cmd-out-1"})
        with (
            patch(
                "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                return_value=_ALLOW_DISPATCH_DECISION,
            ),
            patch("server_modules.sage_command_dispatcher.dispatch_command", new=dispatch_command_mock),
            patch(
                "server_modules.personal_channels_service.gateway_protocol_service.dispatch_channel_outbound",
                new=outbound_dispatch_mock,
                create=True,
            ) as dispatch_mock,
            patch("server_modules.personal_channels_service.security_audit_service.emit_security_audit_event"),
        ):
            result = await personal_channels_service._deliver_whatsapp_personal_reply(
                gateway_id="gw-cmd-wa-1",
                registration={
                    "gateway_id": "gw-cmd-wa-1",
                    "workspace_id": "default",
                    "tenant_id": "tenant-cmd-wa",
                    "device_trust_state": "trusted",
                    "active_session_id": "sess-1",
                },
                inbound={"external_message_id": "wa-cmd-1", "remote_jid": "15551234567@s.whatsapp.net"},
                remote_jid="15551234567@s.whatsapp.net",
                external_message_id="wa-cmd-1",
                text="/help",
                push_name="Mansur",
                duplicate=False,
                agent_id="agent-cmd-wa-1",
            )

        # THE bug: pre-fix, the outbound row was created and mark_inbound_
        # processed was called, then the function returned — dispatch_
        # channel_outbound was never reached, so this assertion is exactly
        # what would have failed against the pre-fix WhatsApp branch.
        dispatch_mock.assert_awaited_once()
        self.assertEqual(dispatch_mock.call_args.kwargs["text"], "Available commands: /help /compact /new ...")
        self.assertEqual(result["outbound"]["status"], "delivered")


if __name__ == "__main__":
    unittest.main()

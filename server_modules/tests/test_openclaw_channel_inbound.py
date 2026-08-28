"""OpenClaw-transported channel inbound — the CLOUD half of
the OpenClaw channel adoption step 2.

WHAT THESE TESTS ARE ACTUALLY DEFENDING
---------------------------------------
OpenClaw's `message_received` hook does not carry `wasMentioned`, and its
`isGroup` never arrives as an explicit `false` (verified against
openclaw@2026.6.10's shipped bundle: `toPluginMessageReceivedEvent` forwards
neither field, and the internal fact it would have come from is
`Boolean(ctx.GroupSubject || ctx.GroupChannel)` where only `GroupChannel`
survives into the hook's metadata).

So the transport tells us less than a first-party bridge does, and the
question this file settles is: does Empyralis fail OPEN or CLOSED on what it
was not told? Every test below asserts CLOSED, at the specific gate, for the
specific missing fact — and one asserts the path is nonetheless genuinely
reachable when an owner has explicitly configured a chat, so this is a
working gated path and not dead code.

These run through the LIVE handler chain
(handle_gateway_channel_inbound -> _OpenClawPersonalChannelHandler ->
_handle_local_bridge_gateway_channel_inbound -> _enforce_group_policy /
_enforce_dm_policy), never against the gate predicates in isolation.
Harness copied from test_personal_channel_group_gate.py.
"""

from __future__ import annotations

import importlib
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, Optional
from unittest.mock import AsyncMock, patch

from server_modules import (
    channel_lane_contract_service,
    personal_channels_repository,
    personal_channels_service,
)


OPENCLAW_CHANNEL_KEY = "openclaw_line"
OPENCLAW_PROVIDER = "openclaw"
GATEWAY_ID = "gw-openclaw-1"
GROUP_CHAT_ID = "C-public-999"

_ALLOW_DISPATCH_DECISION = {
    "ok": True,
    "decision": "allow",
    "reason": "gateway_service_operation_allowed",
    "operation": "protocol_route",
    "next_action": "dispatch_gateway_operation",
}


class _FakeAgentInstallStore:
    """Minimal stand-in for agent_registry_repository's install bundle
    read/write — same convention as test_personal_channel_group_gate.py
    (duplicated per file rather than imported cross-file)."""

    def __init__(self) -> None:
        self.installs: Dict[str, Dict[str, Any]] = {}

    async def get_bundle(self, agent_id: str, *, tenant_id: str, workspace_id: str):
        return {"id": agent_id, "install_metadata": dict(self.installs.get(agent_id, {}))}

    async def update(self, agent_id: str, *, tenant_id: str, workspace_id: str, metadata=None, **_kwargs):
        merged = {**self.installs.get(agent_id, {}), **(metadata or {})}
        self.installs[agent_id] = merged
        return {"id": agent_id, "install_metadata": dict(merged)}


def _patch_agent_install_store(store: "_FakeAgentInstallStore"):
    return patch.multiple(
        "server_modules.agent_registry_repository",
        get_workspace_agent_install_bundle=AsyncMock(side_effect=store.get_bundle),
        update_workspace_agent_install=AsyncMock(side_effect=store.update),
    )


class OpenClawGateFactNormalizationTests(unittest.TestCase):
    """The one substitution _OpenClawPersonalChannelHandler makes, in
    isolation. Everything downstream of it is the existing, unmodified gate
    chain — see the integration class below."""

    def test_unknown_groupness_is_treated_as_a_group(self) -> None:
        normalized = personal_channels_service.normalize_openclaw_gate_facts(
            {"text": "hi", "remote_jid": GROUP_CHAT_ID}
        )
        self.assertIs(normalized["is_group"], True)
        self.assertEqual(
            normalized["openclaw_groupness"],
            personal_channels_service.OPENCLAW_GROUPNESS_ASSUMED,
        )

    def test_explicit_groupness_is_respected_in_both_directions(self) -> None:
        as_dm = personal_channels_service.normalize_openclaw_gate_facts({"is_group": False})
        self.assertIs(as_dm["is_group"], False)
        self.assertNotIn("openclaw_groupness", as_dm)
        as_group = personal_channels_service.normalize_openclaw_gate_facts({"is_group": True})
        self.assertIs(as_group["is_group"], True)

    def test_mention_is_never_synthesized(self) -> None:
        normalized = personal_channels_service.normalize_openclaw_gate_facts({"text": "@agent hello"})
        # Absent, not True and not False. mention_facts_from_message
        # collapses absent to "not mentioned", which is the fail-closed
        # answer; writing an explicit value here would be an assertion we
        # have no basis for.
        self.assertNotIn("is_mentioned", normalized)

    def test_reply_linkage_is_never_promoted_to_a_mention(self) -> None:
        normalized = personal_channels_service.normalize_openclaw_gate_facts(
            {"is_reply_to_sage": True, "quoted_stanza_id": "M-1"}
        )
        # A reply to SOME message is not a reply to the agent. OpenClaw
        # computes its own reply_to_bot implicit mention from the bot's user
        # id and does not forward it, so anything claiming otherwise on this
        # path is unsubstantiated and must not survive.
        self.assertNotIn("is_reply_to_sage", normalized)

    def test_a_forged_self_chat_flag_cannot_bypass_both_gates(self) -> None:
        # is_self_chat short-circuits BOTH _enforce_group_policy and
        # _is_owner_message. The plugin is authenticated, not trusted — the
        # exact distinction OpenClaw's own senderIsOwner CVE turned on.
        normalized = personal_channels_service.normalize_openclaw_gate_facts({"is_self_chat": True})
        self.assertIs(normalized["is_self_chat"], False)

    def test_normalization_does_not_mutate_the_caller_s_message(self) -> None:
        original = {"text": "hi"}
        personal_channels_service.normalize_openclaw_gate_facts(original)
        self.assertEqual(original, {"text": "hi"})


class OpenClawLaneContractTests(unittest.TestCase):
    def test_openclaw_channels_are_registered_in_the_personal_gateway_lane(self) -> None:
        spec = channel_lane_contract_service.assert_personal_gateway_channel(
            OPENCLAW_CHANNEL_KEY, OPENCLAW_PROVIDER
        )
        self.assertEqual(spec["provider"], OPENCLAW_PROVIDER)
        self.assertEqual(
            spec["runtime_lane"], channel_lane_contract_service.PERSONAL_GATEWAY_RUNTIME_LANE
        )

    def test_an_unlisted_openclaw_channel_fails_loudly(self) -> None:
        # A channel Empyralis already runs first-party must NOT silently
        # gain a second, ungated lane — it waits for its step-6 cut-over.
        # openclaw_telegram was this example until 2026-08-14's full cutover
        # moved it to the active/listed side; openclaw_discord is still
        # superseded (Discord stays first-party — see
        # openclaw_channel_registry.OPENCLAW_CUT_OVER_CHANNEL_IDS's comment).
        with self.assertRaises(ValueError):
            channel_lane_contract_service.assert_personal_gateway_channel(
                "openclaw_discord", OPENCLAW_PROVIDER
            )

    def test_a_mismatched_provider_fails_loudly(self) -> None:
        with self.assertRaises(ValueError):
            channel_lane_contract_service.assert_personal_gateway_channel(
                OPENCLAW_CHANNEL_KEY, "telegram_gramjs"
            )

    def test_the_handler_registry_routes_openclaw_channels_to_the_openclaw_handler(self) -> None:
        # Resolved through importlib rather than the import-time binding:
        # several sibling test modules importlib.reload this service, which
        # produces a fresh class object and would make a naive isinstance
        # against the stale one fail for reasons that have nothing to do
        # with the behaviour under test.
        service = importlib.import_module("server_modules.personal_channels_service")
        handler = service._handler_registry.get(OPENCLAW_CHANNEL_KEY)
        self.assertIsInstance(handler, service._OpenClawPersonalChannelHandler)
        self.assertEqual(handler.channel_key, OPENCLAW_CHANNEL_KEY)
        self.assertEqual(handler.provider, OPENCLAW_PROVIDER)
        # 2026-08-14 full OpenClaw channel cutover: signal_personal (the
        # first-party local-bridge channel that used to get the plain
        # _LocalBridgePersonalChannelHandler here) is deleted — every key in
        # LOCAL_BRIDGE_PERSONAL_CHANNELS is now an OpenClaw channel, so every
        # handler the registry loop produces from it IS an
        # _OpenClawPersonalChannelHandler. Asserted for the platform this
        # test used to single out (openclaw_signal, signal's replacement)
        # and for the whole set, so the "no first-party local-bridge
        # handlers remain" fact is structural, not implied by one example.
        signal_handler = service._handler_registry.get("openclaw_signal")
        self.assertIsInstance(signal_handler, service._OpenClawPersonalChannelHandler)
        for channel_key in service.LOCAL_BRIDGE_PERSONAL_CHANNELS:
            self.assertIsInstance(
                service._handler_registry.get(channel_key),
                service._OpenClawPersonalChannelHandler,
                f"{channel_key} should be OpenClaw-handled post-cutover",
            )

    def test_the_gate_two_three_write_lever_covers_openclaw_channels(self) -> None:
        """The only thing that can ever open this path is the owner's own
        group_policy write (allowlist the chat / turn require_mention off).
        If that write path did not accept these channel_keys, the path would
        be permanently closed and therefore dead code, not gated code."""
        service = importlib.import_module("server_modules.personal_channels_service")
        for channel_key in service.OPENCLAW_PERSONAL_CHANNELS:
            self.assertIn(channel_key, service.GROUP_POLICY_CHANNEL_KEYS)


class OpenClawInboundIntegrationTests(unittest.IsolatedAsyncioTestCase):
    """Through the LIVE handle_gateway_channel_inbound entry point — the
    same function gateway_protocol_service calls for a real channel.inbound
    frame."""

    def setUp(self) -> None:
        global personal_channels_service, personal_channels_repository
        personal_channels_service = importlib.import_module("server_modules.personal_channels_service")
        personal_channels_repository = importlib.import_module(
            "server_modules.personal_channels_repository"
        )

        self.tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(self.tmpdir.name) / "personal-channels.sqlite3"
        personal_channels_repository.init_personal_channels_db(db_path)
        self.db_patcher = patch.object(
            personal_channels_repository, "PERSONAL_CHANNELS_DB_FILE", db_path
        )
        self.db_patcher.start()

        self.store = _FakeAgentInstallStore()
        self.registration = {
            "gateway_id": GATEWAY_ID,
            "workspace_id": "default",
            "tenant_id": "tenant-1",
            "device_trust_state": "trusted",
            "active_session_id": "sess-1",
            # The gateway advertises the OpenClaw transport capability only
            # when EMPYRALIS_BRIDGE_TOKEN is configured on that box — see
            # empyralis-gateway/src/openclaw/capabilities.ts.
            "capabilities": ["channel.openclaw.line"],
        }

    def tearDown(self) -> None:
        self.db_patcher.stop()
        self.tmpdir.cleanup()

    def _claim_agent(self, agent_id: str) -> None:
        """Binds this gateway+channel to a real agent id, the same way
        _resolve_local_bridge_agent_id's fast path finds it."""
        personal_channels_repository.upsert_local_bridge_state(
            gateway_id=GATEWAY_ID,
            tenant_id="tenant-1",
            workspace_id="default",
            user_id="",
            channel_key=OPENCLAW_CHANNEL_KEY,
            agent_id=agent_id,
            provider=OPENCLAW_PROVIDER,
            status="linked",
        )

    def _set_group_policy(self, agent_id: str, *, mode: str, allowlist, require_mention: bool) -> None:
        self.store.installs[agent_id] = {
            "group_policy": {
                OPENCLAW_CHANNEL_KEY: {
                    "mode": mode,
                    "allowlist": list(allowlist),
                    "require_mention": require_mention,
                }
            }
        }

    def _payload(self, message_overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        message = {
            "external_message_id": "oc-1",
            "remote_jid": GROUP_CHAT_ID,
            "sender_jid": "U-stranger",
            "push_name": "A Stranger",
            "text": "who is this bot",
            "received_at": "2026-08-08T10:00:00.000Z",
            "from_me": False,
            # NOTE what is NOT here: is_group, is_mentioned, is_reply_to_sage.
            # This is exactly the shape the bridge produces today.
        }
        message.update(message_overrides or {})
        return {
            "channel_key": OPENCLAW_CHANNEL_KEY,
            "provider": OPENCLAW_PROVIDER,
            "message": message,
        }

    async def _run(self, payload: Dict[str, Any]):
        with (
            patch(
                "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                return_value=_ALLOW_DISPATCH_DECISION,
            ),
            _patch_agent_install_store(self.store),
            patch(
                "server_modules.personal_channels_service.personal_channel_sage_bridge_service.build_personal_channel_reply_async",
                new=AsyncMock(return_value={"text": "a reply", "source": "sage"}),
            ) as build_reply_mock,
            patch(
                "server_modules.personal_channels_service.gateway_protocol_service.dispatch_channel_outbound",
                new=AsyncMock(return_value={"external_message_id": "oc-out-1"}),
                create=True,
            ),
        ):
            result = await personal_channels_service.handle_gateway_channel_inbound(
                gateway_id=GATEWAY_ID,
                registration=self.registration,
                payload=payload,
            )
        return result, build_reply_mock

    # ── the closed cases ────────────────────────────────────────────────

    async def test_unknown_groupness_from_a_stranger_is_denied_at_the_allowlist_gate(self) -> None:
        """THE incident case. OpenClaw cannot tell us this is a group; if we
        guessed "DM" it would land on Gate 1, whose resolved default is
        DM_POLICY_OPEN, and a stranger in a public group would get a turn.
        It must land on Gate 2 instead, whose default is allowlist."""
        self._claim_agent("agent-1")
        result, build_reply_mock = await self._run(self._payload())
        self.assertTrue(result.get("ignored"))
        self.assertEqual(
            result.get("reason"), personal_channels_service.GROUP_GATE_REASON_POLICY_DENIED
        )
        build_reply_mock.assert_not_called()

    async def test_an_allowlisted_chat_is_still_denied_without_a_mention(self) -> None:
        """Gate 3. The owner allowlisted the chat, but OpenClaw does not
        forward `wasMentioned`, so there is no evidence this message
        addressed the agent — and require_mention defaults True."""
        self._claim_agent("agent-1")
        self._set_group_policy(
            "agent-1",
            mode=personal_channels_service.GROUP_POLICY_ALLOWLIST,
            allowlist=[GROUP_CHAT_ID],
            require_mention=True,
        )
        result, build_reply_mock = await self._run(self._payload())
        self.assertTrue(result.get("ignored"))
        self.assertEqual(
            result.get("reason"), personal_channels_service.GROUP_GATE_REASON_NO_MENTION
        )
        build_reply_mock.assert_not_called()

    async def test_an_unresolved_agent_identity_fails_closed(self) -> None:
        """No agent has claimed this gateway+channel. The local-bridge
        family's unresolved-identity fallback is DISABLED/require_mention,
        and OpenClaw channels are members of that family precisely so they
        inherit it rather than the WhatsApp/Telegram legacy OPEN one."""
        with patch(
            "server_modules.agent_registry_repository.list_workspace_agent_installs",
            new=AsyncMock(return_value=[]),
        ):
            result, build_reply_mock = await self._run(self._payload())
        self.assertTrue(result.get("ignored"))
        self.assertEqual(
            result.get("reason"), personal_channels_service.GROUP_GATE_REASON_POLICY_DENIED
        )
        build_reply_mock.assert_not_called()

    async def test_a_forged_self_chat_flag_does_not_bypass_the_group_gate(self) -> None:
        self._claim_agent("agent-1")
        result, build_reply_mock = await self._run(
            self._payload({"is_self_chat": True, "is_reply_to_sage": True})
        )
        self.assertTrue(result.get("ignored"))
        self.assertEqual(
            result.get("reason"), personal_channels_service.GROUP_GATE_REASON_POLICY_DENIED
        )
        build_reply_mock.assert_not_called()

    async def test_a_channel_the_gateway_never_advertised_is_refused(self) -> None:
        registration = dict(self.registration)
        registration["capabilities"] = []
        with self.assertRaises(ValueError):
            await personal_channels_service.handle_gateway_channel_inbound(
                gateway_id=GATEWAY_ID,
                registration=registration,
                payload=self._payload(),
            )

    # ── the open case: the path is genuinely reachable ──────────────────

    async def test_an_allowlisted_chat_with_require_mention_off_reaches_the_turn(self) -> None:
        """Proves this is a gated live path, not dead code: with the owner's
        own explicit per-binding configuration (allowlist + require_mention
        off — both writable through the existing
        update_agent_group_policy_config path, no new surface), an OpenClaw
        message reaches the reply builder.

        It also proves the gate stays HONEST while allowing: was_addressed
        must be threaded through as False, so the model is never told it was
        addressed when nothing established that."""
        self._claim_agent("agent-1")
        self._set_group_policy(
            "agent-1",
            mode=personal_channels_service.GROUP_POLICY_ALLOWLIST,
            allowlist=[GROUP_CHAT_ID],
            require_mention=False,
        )
        result, build_reply_mock = await self._run(self._payload())
        self.assertFalse(result.get("ignored"))
        build_reply_mock.assert_called_once()
        kwargs = build_reply_mock.call_args.kwargs
        self.assertEqual(kwargs.get("surface_channel"), OPENCLAW_CHANNEL_KEY)
        self.assertTrue(kwargs.get("is_group"))
        self.assertIs(kwargs.get("was_addressed"), False)

    async def test_an_explicitly_asserted_mention_satisfies_gate_three(self) -> None:
        """Forward compatibility: OpenClaw does not send `wasMentioned`
        today, but the bridge schema already carries the field and the
        mapper forwards it when present. When it arrives, an allowlisted
        chat with require_mention ON must pass — otherwise Gate 3 would be
        permanently closed rather than correctly closed."""
        self._claim_agent("agent-1")
        self._set_group_policy(
            "agent-1",
            mode=personal_channels_service.GROUP_POLICY_ALLOWLIST,
            allowlist=[GROUP_CHAT_ID],
            require_mention=True,
        )
        result, build_reply_mock = await self._run(
            self._payload({"is_group": True, "is_mentioned": True})
        )
        self.assertFalse(result.get("ignored"))
        build_reply_mock.assert_called_once()
        self.assertIs(build_reply_mock.call_args.kwargs.get("was_addressed"), True)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

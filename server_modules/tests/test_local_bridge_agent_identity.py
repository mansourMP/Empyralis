"""Tests for Signal/iMessage/WeChat-personal agent-identity resolution
(CHANNEL-GATEWAY-PLAN.md "the last unscoped channels").

Before this build, `_resolve_agent_id_for_inbound` had no lookup branch for
`LOCAL_BRIDGE_PERSONAL_CHANNELS` at all -- every inbound Signal/iMessage/
WeChat-personal message permanently resolved to
`personal_channels_repository.LEGACY_UNSCOPED_AGENT_ID` ("" ), so:

  1. `_persist_agent_group_policy_config` could never be reached for them at
     inbound time (the write route worked, the read never saw it).
  2. `_unresolved_identity_group_policy_config` kept them on
     open/require_mention=False forever, with no owner-facing lever.

This build adds `_resolve_local_bridge_agent_id`, which resolves a real
agent_id for these three channels the same way WhatsApp/Telegram Personal
always have (a per-(gateway_id, channel_key, agent_id) row in
`personal_channel_local_bridge_states`), populated either by:

  - an explicit owner action that already names the agent_id (iMessage's
    recheck/install routes, via `_claim_agent_channel_state`), or
  - a reverse lookup: which agent in this gateway's own tenant/workspace has
    `install_metadata.preferred_gateway_id` pointing at this gateway_id (the
    SAME mechanism the product already uses for the opposite direction --
    an agent's own outbound tool dispatch).

These tests prove, for each of the three channels:
  A. An inbound message resolves to the correct agent (fast path via a
     pre-claimed row, slow path via the preferred_gateway_id reverse
     lookup, the explicit iMessage claim action, and the ambiguous/
     unclaimed cases failing closed rather than guessing).
  B. group_policy can be written (update_agent_group_policy_config) and
     read back (_load_agent_group_policy_config) for a resolved local-bridge
     agent+channel -- previously unreachable at inbound time regardless of
     what was persisted.
  C. An unaddressed group message is refused when require_mention=True.
  D. An allowlisted chat is permitted and a non-allowlisted one is not.
  E. End-to-end through the LIVE _handle_local_bridge_gateway_channel_inbound
     handler, tying A-D together the way a real inbound message would.

`_FakeAgentInstallStore` / `_patch_agent_install_store` are copied from
test_personal_channels_dm_policy.py / test_personal_channels_group_policy.py
(same convention in this test suite: duplicated per-file rather than
imported cross-file).
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

LOCAL_BRIDGE_CHANNEL_KEYS = ("signal_personal", "imessage_personal", "wechat_personal")


class _FakeAgentInstallStore:
    """Minimal in-memory stand-in for agent_registry_repository's
    get_workspace_agent_install_bundle / update_workspace_agent_install --
    just enough of the real "install_metadata, shallow top-level metadata
    merge" contract for _load_agent_group_policy_config /
    _persist_agent_group_policy_config to correctly round-trip against."""

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


def _fake_install(*, id: str, enabled: bool = True, preferred_gateway_id: str = "") -> Dict[str, Any]:
    """Shape returned by agent_registry_repository.list_workspace_agent_installs
    -- only the fields _resolve_local_bridge_agent_id's reverse
    preferred_gateway_id lookup actually reads (id/enabled/metadata)."""
    return {"id": id, "enabled": enabled, "metadata": {"preferred_gateway_id": preferred_gateway_id}}


class LocalBridgeIdentityResolutionTests(unittest.IsolatedAsyncioTestCase):
    """Direct tests against _resolve_local_bridge_agent_id / _resolve_agent_id_for_inbound
    / _claim_agent_channel_state -- no gateway handler plumbing."""

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
            "gateway_id": "gw-identity-1",
            "workspace_id": "ws-identity",
            "tenant_id": "tenant-identity",
        }

    def tearDown(self) -> None:
        self.db_patcher.stop()
        self.tmpdir.cleanup()

    async def test_fast_path_finds_a_pre_claimed_row_for_all_three_channels(self) -> None:
        """A row already exists (as an explicit claim, or a prior slow-path
        resolution, would have left one) -- resolution must be the fast,
        indexed lookup and must never touch agent_registry_repository at
        all (proven here by NOT mocking it and still succeeding)."""
        for channel_key in LOCAL_BRIDGE_CHANNEL_KEYS:
            with self.subTest(channel_key=channel_key):
                agent_id = f"agent-{channel_key}"
                personal_channels_repository.upsert_local_bridge_state(
                    gateway_id="gw-identity-1",
                    tenant_id="tenant-identity",
                    workspace_id="ws-identity",
                    user_id="",
                    channel_key=channel_key,
                    agent_id=agent_id,
                    provider=personal_channels_service.LOCAL_BRIDGE_PERSONAL_CHANNELS[channel_key]["provider"],
                    status="linked",
                )
                resolved = await personal_channels_service._resolve_local_bridge_agent_id(
                    gateway_id="gw-identity-1", channel_key=channel_key, registration=self.registration,
                )
                self.assertEqual(resolved, agent_id)
                # The SAME sync entry point WhatsApp/Telegram already use:
                self.assertEqual(
                    personal_channels_service._resolve_agent_id_for_inbound("gw-identity-1", channel_key),
                    agent_id,
                )

    async def test_slow_path_resolves_via_preferred_gateway_id_when_exactly_one_agent_matches(self) -> None:
        """No row claimed yet -- the reverse preferred_gateway_id lookup
        must find the single matching agent, resolve to it, AND persist a
        row so the NEXT lookup hits the fast path without touching
        agent_registry_repository again (proven by only mocking it for the
        FIRST call)."""
        installs = [
            _fake_install(id="agent-other", preferred_gateway_id="some-other-gateway"),
            _fake_install(id="agent-signal-owner", preferred_gateway_id="gw-identity-2"),
        ]
        with patch(
            "server_modules.agent_registry_repository.list_workspace_agent_installs",
            new=AsyncMock(return_value=installs),
        ) as list_installs_mock:
            resolved = await personal_channels_service._resolve_local_bridge_agent_id(
                gateway_id="gw-identity-2", channel_key="signal_personal",
                registration={"tenant_id": "tenant-identity", "workspace_id": "ws-identity"},
            )
        self.assertEqual(resolved, "agent-signal-owner")
        list_installs_mock.assert_awaited_once()

        # Second lookup: agent_registry_repository is NOT mocked at all here
        # -- if the slow path ran again, this would either fail (no real
        # Postgres/control-plane fixture in this test) or silently re-derive
        # the same answer the slow way. Succeeding fast is what proves the
        # claim from above was actually persisted.
        resolved_again = await personal_channels_service._resolve_local_bridge_agent_id(
            gateway_id="gw-identity-2", channel_key="signal_personal",
            registration={"tenant_id": "tenant-identity", "workspace_id": "ws-identity"},
        )
        self.assertEqual(resolved_again, "agent-signal-owner")

    async def test_slow_path_stays_unresolved_when_no_agent_claims_the_gateway(self) -> None:
        with patch(
            "server_modules.agent_registry_repository.list_workspace_agent_installs",
            new=AsyncMock(return_value=[_fake_install(id="agent-elsewhere", preferred_gateway_id="totally-different-gw")]),
        ):
            resolved = await personal_channels_service._resolve_local_bridge_agent_id(
                gateway_id="gw-unclaimed", channel_key="imessage_personal", registration=self.registration,
            )
        self.assertEqual(resolved, personal_channels_repository.LEGACY_UNSCOPED_AGENT_ID)

    async def test_slow_path_stays_unresolved_and_claims_nothing_when_multiple_agents_match(self) -> None:
        """Ambiguous (multi-agent-per-box) case: must not guess, and must
        not persist a row under either candidate -- a later, genuinely
        unambiguous state must not find a stale wrong claim blocking it."""
        installs = [
            _fake_install(id="agent-a", preferred_gateway_id="gw-ambiguous"),
            _fake_install(id="agent-b", preferred_gateway_id="gw-ambiguous"),
        ]
        with patch(
            "server_modules.agent_registry_repository.list_workspace_agent_installs",
            new=AsyncMock(return_value=installs),
        ):
            resolved = await personal_channels_service._resolve_local_bridge_agent_id(
                gateway_id="gw-ambiguous", channel_key="wechat_personal", registration=self.registration,
            )
        self.assertEqual(resolved, personal_channels_repository.LEGACY_UNSCOPED_AGENT_ID)
        self.assertIsNone(
            personal_channels_repository.get_local_bridge_state(
                "gw-ambiguous", channel_key="wechat_personal", agent_id="agent-a",
            )
        )
        self.assertIsNone(
            personal_channels_repository.get_local_bridge_state(
                "gw-ambiguous", channel_key="wechat_personal", agent_id="agent-b",
            )
        )

    async def test_slow_path_narrows_by_channel_binding_when_two_agents_share_the_box(self) -> None:
        """The regression this build fixes: two agents legitimately sharing
        one box (agent-a on this channel, agent-b on something else
        entirely) used to make the slow path treat EVERY shared-box channel
        as permanently ambiguous, because the old check only asked "how many
        agents prefer this gateway" and never "which of them actually use
        this channel". Adding agent-b to the box must not break agent-a's
        already-working channel.

        agent_bindings_repository.list_agent_channel_bindings is mocked per
        candidate agent_id: agent-a has an enabled binding for THIS
        channel_key, agent-b has none. Narrowing must land on agent-a alone."""
        installs = [
            _fake_install(id="agent-a", preferred_gateway_id="gw-shared"),
            _fake_install(id="agent-b", preferred_gateway_id="gw-shared"),
        ]

        async def fake_bindings(*, tenant_id, workspace_id, agent_install_id, enabled_only):
            assert enabled_only is True
            if agent_install_id == "agent-a":
                return [{"key": "signal_personal"}]
            return [{"key": "discord"}]  # agent-b uses a completely different channel

        with (
            patch(
                "server_modules.agent_registry_repository.list_workspace_agent_installs",
                new=AsyncMock(return_value=installs),
            ),
            patch(
                "server_modules.agent_bindings_repository.list_agent_channel_bindings",
                new=AsyncMock(side_effect=fake_bindings),
            ),
        ):
            resolved = await personal_channels_service._resolve_local_bridge_agent_id(
                gateway_id="gw-shared", channel_key="signal_personal",
                registration={"tenant_id": "tenant-identity", "workspace_id": "ws-identity"},
            )
        self.assertEqual(resolved, "agent-a")
        # And the claim was persisted, so the NEXT message on this
        # gateway+channel hits the fast path instead of re-running this
        # lookup (same contract as the exactly-one-preferred-gateway case).
        self.assertEqual(
            personal_channels_service._resolve_agent_id_for_inbound("gw-shared", "signal_personal"),
            "agent-a",
        )

    async def test_slow_path_stays_ambiguous_when_two_agents_both_bind_the_same_channel(self) -> None:
        """The genuine conflict case, NOT a false positive from box-sharing:
        two different agents both hold an enabled binding for the SAME
        channel_key. OpenClaw's own config schema has no way to express two
        accounts on one channel node (verified against the pinned build's
        `openclaw config schema` -- every channel is a single credential),
        so this must still fail closed exactly like the pre-existing
        multi-match case, not be narrowed away."""
        installs = [
            _fake_install(id="agent-a", preferred_gateway_id="gw-conflict"),
            _fake_install(id="agent-b", preferred_gateway_id="gw-conflict"),
        ]

        async def fake_bindings(*, tenant_id, workspace_id, agent_install_id, enabled_only):
            return [{"key": "signal_personal"}]  # both agents claim it

        with (
            patch(
                "server_modules.agent_registry_repository.list_workspace_agent_installs",
                new=AsyncMock(return_value=installs),
            ),
            patch(
                "server_modules.agent_bindings_repository.list_agent_channel_bindings",
                new=AsyncMock(side_effect=fake_bindings),
            ),
        ):
            resolved = await personal_channels_service._resolve_local_bridge_agent_id(
                gateway_id="gw-conflict", channel_key="signal_personal",
                registration={"tenant_id": "tenant-identity", "workspace_id": "ws-identity"},
            )
        self.assertEqual(resolved, personal_channels_repository.LEGACY_UNSCOPED_AGENT_ID)
        self.assertIsNone(
            personal_channels_repository.get_local_bridge_state(
                "gw-conflict", channel_key="signal_personal", agent_id="agent-a",
            )
        )
        self.assertIsNone(
            personal_channels_repository.get_local_bridge_state(
                "gw-conflict", channel_key="signal_personal", agent_id="agent-b",
            )
        )

    async def test_slow_path_ignores_a_disabled_agent_installs_preferred_gateway_id(self) -> None:
        installs = [_fake_install(id="agent-disabled", enabled=False, preferred_gateway_id="gw-identity-3")]
        with patch(
            "server_modules.agent_registry_repository.list_workspace_agent_installs",
            new=AsyncMock(return_value=installs),
        ):
            resolved = await personal_channels_service._resolve_local_bridge_agent_id(
                gateway_id="gw-identity-3", channel_key="signal_personal", registration=self.registration,
            )
        self.assertEqual(resolved, personal_channels_repository.LEGACY_UNSCOPED_AGENT_ID)

    async def test_explicit_imessage_claim_lets_the_fast_path_resolve_without_any_lookup(self) -> None:
        """Mirrors what recheck_imessage_personal_gateway / install_imessage_
        imsg_gateway now do at the start of their own call: claim a row
        under the real, route-supplied agent_id via _claim_agent_channel_state
        BEFORE any Gateway round trip -- proven here directly against that
        primitive, independent of the two routes' own RPC plumbing."""
        personal_channels_service._claim_agent_channel_state(
            gateway_id="gw-imsg-1", channel_key="imessage_personal",
            agent_id="agent-imsg-owner", registration=self.registration,
        )
        resolved = personal_channels_service._resolve_agent_id_for_inbound("gw-imsg-1", "imessage_personal")
        self.assertEqual(resolved, "agent-imsg-owner")

    async def test_explicit_imessage_claim_is_a_no_op_if_that_agent_already_has_a_row(self) -> None:
        """Mirrors _claim_agent_channel_state's own WhatsApp/Telegram
        contract: a retry/refresh call must not clobber a connected
        session's status back to "connecting"."""
        personal_channels_repository.upsert_local_bridge_state(
            gateway_id="gw-imsg-2", tenant_id="tenant-identity", workspace_id="ws-identity", user_id="",
            channel_key="imessage_personal", agent_id="agent-imsg-owner",
            provider="bluebubbles_local_bridge", status="connected",
        )
        personal_channels_service._claim_agent_channel_state(
            gateway_id="gw-imsg-2", channel_key="imessage_personal",
            agent_id="agent-imsg-owner", registration=self.registration,
        )
        state = personal_channels_repository.get_local_bridge_state(
            "gw-imsg-2", channel_key="imessage_personal", agent_id="agent-imsg-owner",
        )
        self.assertEqual(state["status"], "connected")

    async def test_recheck_imessage_personal_gateway_claims_identity_before_the_gateway_round_trip(self) -> None:
        """Through the LIVE route-callable function -- proves the wiring,
        not just the primitive. gateway_execution_service.execute_tool_via_gateway
        is mocked (a real RPC round trip to a paired Mac has no place in a
        unit test); _claim_agent_channel_state itself is real and unmocked."""
        with (
            patch(
                "server_modules.personal_channels_service.kill_switch_gate.assert_not_killed",
                return_value=None,
            ),
            patch(
                "server_modules.personal_channels_service._enforce_personal_gateway_config_decision",
                return_value={"ok": True},
            ),
            patch(
                "server_modules.personal_channels_service.gateway_execution_service.execute_tool_via_gateway",
                new=AsyncMock(return_value={"result": {"connected": True}}),
            ),
        ):
            await personal_channels_service.recheck_imessage_personal_gateway(
                gateway_id="gw-imsg-3", registration=self.registration, agent_id="agent-imsg-recheck",
            )
        resolved = personal_channels_service._resolve_agent_id_for_inbound("gw-imsg-3", "imessage_personal")
        self.assertEqual(resolved, "agent-imsg-recheck")


class LocalBridgeGroupPolicyRoundTripTests(unittest.IsolatedAsyncioTestCase):
    """Proves group_policy can be written (update_agent_group_policy_config)
    and read back (_load_agent_group_policy_config) for a RESOLVED
    local-bridge agent+channel -- unreachable at inbound time before this
    build regardless of what was persisted (see file docstring)."""

    def setUp(self) -> None:
        global personal_channels_service
        personal_channels_service = importlib.import_module("server_modules.personal_channels_service")

    async def test_write_then_read_round_trips_for_each_local_bridge_channel(self) -> None:
        for channel_key in LOCAL_BRIDGE_CHANNEL_KEYS:
            with self.subTest(channel_key=channel_key):
                store = _FakeAgentInstallStore()
                with _patch_agent_install_store(store):
                    written = await personal_channels_service.update_agent_group_policy_config(
                        tenant_id="t", workspace_id="w", agent_id="agent-rt",
                        channel_key=channel_key, mode="allowlist",
                        allowlist=["group:family"], require_mention=True,
                    )
                    self.assertIsNotNone(written)
                    read_back = await personal_channels_service._load_agent_group_policy_config(
                        tenant_id="t", workspace_id="w", agent_id="agent-rt", channel_key=channel_key,
                    )
                self.assertEqual(read_back["mode"], "allowlist")
                self.assertEqual(read_back["allowlist"], ["group:family"])
                self.assertTrue(read_back["require_mention"])

    async def test_require_mention_true_refuses_unaddressed_and_allows_mentioned_for_each_channel(self) -> None:
        for channel_key in LOCAL_BRIDGE_CHANNEL_KEYS:
            with self.subTest(channel_key=channel_key):
                store = _FakeAgentInstallStore()
                store.installs["agent-mention"] = {
                    "group_policy": {channel_key: {"mode": "open", "allowlist": [], "require_mention": True}},
                }
                with _patch_agent_install_store(store):
                    unaddressed = await personal_channels_service._enforce_group_policy(
                        registration={"tenant_id": "t", "workspace_id": "w"},
                        channel_key=channel_key, agent_id="agent-mention",
                        message={"is_group": True, "is_mentioned": False, "is_reply_to_sage": False},
                        remote_jid="group:family",
                    )
                    mentioned = await personal_channels_service._enforce_group_policy(
                        registration={"tenant_id": "t", "workspace_id": "w"},
                        channel_key=channel_key, agent_id="agent-mention",
                        message={"is_group": True, "is_mentioned": True, "is_reply_to_sage": False},
                        remote_jid="group:family",
                    )
                self.assertFalse(unaddressed["allowed"])
                self.assertEqual(unaddressed["reason"], "group_no_mention")
                self.assertTrue(mentioned["allowed"])

    async def test_allowlist_permits_the_listed_chat_and_blocks_a_different_one_for_each_channel(self) -> None:
        for channel_key in LOCAL_BRIDGE_CHANNEL_KEYS:
            with self.subTest(channel_key=channel_key):
                store = _FakeAgentInstallStore()
                store.installs["agent-allowlist"] = {
                    "group_policy": {
                        channel_key: {"mode": "allowlist", "allowlist": ["group:family"], "require_mention": False},
                    },
                }
                with _patch_agent_install_store(store):
                    allowed = await personal_channels_service._enforce_group_policy(
                        registration={"tenant_id": "t", "workspace_id": "w"},
                        channel_key=channel_key, agent_id="agent-allowlist",
                        message={"is_group": True, "is_mentioned": False, "is_reply_to_sage": False},
                        remote_jid="group:family",
                    )
                    blocked = await personal_channels_service._enforce_group_policy(
                        registration={"tenant_id": "t", "workspace_id": "w"},
                        channel_key=channel_key, agent_id="agent-allowlist",
                        message={"is_group": True, "is_mentioned": False, "is_reply_to_sage": False},
                        remote_jid="group:coworkers",
                    )
                self.assertTrue(allowed["allowed"])
                self.assertFalse(blocked["allowed"])
                self.assertEqual(blocked["reason"], "group_policy_denied")


class LocalBridgeEndToEndInboundTests(unittest.IsolatedAsyncioTestCase):
    """Through the LIVE _handle_local_bridge_gateway_channel_inbound,
    tenant/allowlist end-to-end: resolution -> real group_policy config
    (written via the real API primitive, read via the real, unmocked
    _load_agent_group_policy_config) -> the gate's allow/deny decision."""

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
            "gateway_id": "gw-e2e-1",
            "workspace_id": "ws-e2e",
            "tenant_id": "tenant-e2e",
            "device_trust_state": "trusted",
            "active_session_id": "sess-1",
        }

    def tearDown(self) -> None:
        self.db_patcher.stop()
        self.tmpdir.cleanup()

    async def _claim_and_configure(self, *, channel_key: str, provider: str) -> None:
        personal_channels_repository.upsert_local_bridge_state(
            gateway_id="gw-e2e-1", tenant_id="tenant-e2e", workspace_id="ws-e2e", user_id="",
            channel_key=channel_key, agent_id="agent-e2e", provider=provider, status="linked",
        )
        self.store = _FakeAgentInstallStore()
        with _patch_agent_install_store(self.store):
            await personal_channels_service.update_agent_group_policy_config(
                tenant_id="tenant-e2e", workspace_id="ws-e2e", agent_id="agent-e2e",
                channel_key=channel_key, mode="allowlist",
                allowlist=["group:family"], require_mention=True,
            )

    async def test_addressed_message_in_the_allowed_chat_reaches_dm_policy(self) -> None:
        await self._claim_and_configure(channel_key="signal_personal", provider="signal_local_bridge")
        blocked_decision = {
            "allowed": False, "mode": "owner_only", "sender_id": "+15557654321",
            "is_owner": False, "system_reply": None, "config_changed": False,
        }
        with (
            _patch_agent_install_store(self.store),
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
                gateway_id="gw-e2e-1",
                registration=self.registration,
                payload={
                    "message": {
                        "external_message_id": "sig-e2e-1",
                        "remote_jid": "group:family",
                        "sender_jid": "+15557654321",
                        "push_name": "Family Member",
                        "text": "@sage what time is dinner",
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
        self.assertEqual(dm_policy_mock.await_args.kwargs.get("agent_id"), "agent-e2e")
        self.assertTrue(result.get("blocked"))  # by dmPolicy, not the group gate

    async def test_unaddressed_message_in_the_allowed_chat_is_refused_by_require_mention(self) -> None:
        await self._claim_and_configure(channel_key="imessage_personal", provider="bluebubbles_local_bridge")
        with (
            _patch_agent_install_store(self.store),
            patch(
                "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                return_value=_ALLOW_DISPATCH_DECISION,
            ),
            patch(
                "server_modules.personal_channels_service._enforce_dm_policy",
                new=AsyncMock(side_effect=AssertionError("dmPolicy must not run")),
            ),
            patch(
                "server_modules.personal_channels_service.gateway_protocol_service.dispatch_channel_outbound",
                new=AsyncMock(side_effect=AssertionError("must not dispatch")),
                create=True,
            ),
        ):
            result = await personal_channels_service._handle_local_bridge_gateway_channel_inbound(
                gateway_id="gw-e2e-1",
                registration=self.registration,
                payload={
                    "message": {
                        "external_message_id": "imsg-e2e-1",
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
                channel_key="imessage_personal",
                provider="bluebubbles_local_bridge",
                label="iMessage",
            )
        self.assertTrue(result.get("ignored"))
        self.assertEqual(result.get("reason"), "group_no_mention")

    async def test_addressed_message_in_a_non_allowlisted_chat_is_denied(self) -> None:
        await self._claim_and_configure(channel_key="wechat_personal", provider="wechat_local_bridge")
        with (
            _patch_agent_install_store(self.store),
            patch(
                "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                return_value=_ALLOW_DISPATCH_DECISION,
            ),
            patch(
                "server_modules.personal_channels_service._enforce_dm_policy",
                new=AsyncMock(side_effect=AssertionError("dmPolicy must not run")),
            ),
            patch(
                "server_modules.personal_channels_service.gateway_protocol_service.dispatch_channel_outbound",
                new=AsyncMock(side_effect=AssertionError("must not dispatch")),
                create=True,
            ),
        ):
            result = await personal_channels_service._handle_local_bridge_gateway_channel_inbound(
                gateway_id="gw-e2e-1",
                registration=self.registration,
                payload={
                    "message": {
                        "external_message_id": "wechat-e2e-1",
                        "remote_jid": "group:coworkers",
                        "sender_jid": "wechat-user-1",
                        "push_name": "Coworker",
                        "text": "@sage hello",
                        "from_me": False,
                        "is_group": True,
                        "is_mentioned": True,
                        "is_reply_to_sage": False,
                    },
                },
                channel_key="wechat_personal",
                provider="wechat_local_bridge",
                label="WeChat",
            )
        self.assertTrue(result.get("ignored"))
        self.assertEqual(result.get("reason"), "group_policy_denied")


if __name__ == "__main__":
    unittest.main()

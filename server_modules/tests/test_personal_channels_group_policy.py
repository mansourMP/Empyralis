"""Tests for group_policy (the open|allowlist|disabled identity axis) and
requireMention (the mention/reply-addressing axis), built 2026-07-23 to
clone OpenClaw's group-access MECHANISM while preserving the founder's
standing "see-and-decide" ruling on default BEHAVIOR.

Structure mirrors test_personal_channels_dm_policy.py:
  - GroupPolicyGateUnitTests: direct tests against _enforce_group_policy —
    no gateway/DB plumbing, covering both axes and their interaction.
  - MentionGatingResolverUnitTests: direct tests against the shared
    mention_gating_service.resolve_inbound_mention_decision resolver in
    isolation, independent of group_policy storage entirely.
  - RequireMentionRestoresOldGateIntegrationTests: through the LIVE
    _handle_telegram_gateway_channel_inbound / _handle_whatsapp_gateway_
    channel_inbound handlers, proving requireMention=True (an explicit,
    owner-configured, per-agent+channel opt-in) reproduces the exact
    pre-2026-07-23 hard mention gate — see
    test_personal_channel_group_gate.py's file docstring for the default
    that changed and why.

_FakeAgentInstallStore / _patch_agent_install_store are copied from
test_personal_channels_dm_policy.py (same install_metadata contract,
different top-level key: group_policy instead of dm_policy).
"""

from __future__ import annotations

import importlib
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict
from unittest.mock import AsyncMock, patch

from server_modules import (
    mention_gating_service,
    openclaw_channel_registry,
    personal_channels_service,
    personal_channels_repository,
)


def _cut_over_channel_key(platform: str) -> str:
    """One cut-over platform's live channel key. Resolved through the
    registry rather than typed: the keys this file used to name
    (`signal_personal`, `telegram_personal`) were deleted by the OpenClaw
    cutover, and a dead key here silently tests nothing."""
    assert platform in openclaw_channel_registry.OPENCLAW_CUT_OVER_CHANNEL_IDS, platform
    return f"{openclaw_channel_registry.CHANNEL_KEY_PREFIX}{platform}"


_ALLOW_DISPATCH_DECISION = {
    "ok": True,
    "decision": "allow",
    "reason": "gateway_service_operation_allowed",
    "operation": "protocol_route",
    "next_action": "dispatch_gateway_operation",
}


class _FakeAgentInstallStore:
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


class GroupPolicyGateUnitTests(unittest.IsolatedAsyncioTestCase):
    """Direct tests against _enforce_group_policy — no gateway/DB plumbing."""

    def setUp(self) -> None:
        self.registration = {"tenant_id": "tenant-1", "workspace_id": "ws-1"}

    async def test_default_is_open_and_lets_an_unaddressed_group_message_through(self) -> None:
        """The NON-local-bridge branch of
        _unresolved_identity_group_policy_config: a channel with an
        unresolved agent identity that is not in
        LOCAL_BRIDGE_PERSONAL_CHANNELS keeps the pre-existing open/
        no-mention-check behavior, deliberately NOT the fail-closed fallback
        local-bridge channels get
        (test_unresolved_local_bridge_identity_fails_closed_and_denies_a_group_message
        below).

        RETARGETED 2026-08-15. This used to name whatsapp_personal, whose
        whole point here was that it took the non-local-bridge branch. Commit
        6b2baf97e (the full OpenClaw cutover, 2026-08-14) moved WhatsApp and
        Telegram onto the transport, i.e. INTO LOCAL_BRIDGE_PERSONAL_CHANNELS,
        so that key now takes the other branch and asserts the opposite thing.
        discord_personal is the only first-party personal channel the cutover
        left, and is therefore the one live key that still reaches this
        branch -- asserted below rather than assumed, so if it ever moves too
        this test says so instead of silently testing the wrong branch."""
        channel_key = "discord_personal"
        self.assertNotIn(channel_key, personal_channels_service.LOCAL_BRIDGE_PERSONAL_CHANNELS)
        decision = await personal_channels_service._enforce_group_policy(
            registration=self.registration,
            channel_key=channel_key,
            agent_id="",
            message={"is_group": True, "is_mentioned": False, "is_reply_to_sage": False},
            remote_jid="120363-group@g.us",
        )
        self.assertTrue(decision["allowed"])
        self.assertIsNone(decision["reason"])
        self.assertEqual(decision["mode"], "open")
        self.assertFalse(decision["require_mention"])

    async def test_unresolved_local_bridge_identity_fails_closed_and_denies_a_group_message(self) -> None:
        """UPDATED 2026-08 (channel-gate hardening, "the last unscoped
        channels"): a genuinely unresolved LOCAL-BRIDGE identity (agent_id=""
        -- LEGACY_UNSCOPED_AGENT_ID, paired with a
        LOCAL_BRIDGE_PERSONAL_CHANNELS channel_key) now fails CLOSED, not
        open. This was exactly the incident: an unresolved identity used to
        default to open/no-mention-check, permanently and unconfigurably,
        and an agent replied unprompted in a large public group until the
        account got banned. See
        _unresolved_identity_group_policy_config's own docstring -- this
        fallback is now channel-aware specifically so this fix does not also
        change WhatsApp/Telegram's own separate agent_id="" case (see the
        test right above)."""
        decision = await personal_channels_service._enforce_group_policy(
            registration=self.registration,
            channel_key=_cut_over_channel_key("signal"),
            agent_id="",
            message={"is_group": True, "is_mentioned": False, "is_reply_to_sage": False},
            remote_jid="group:family",
        )
        self.assertFalse(decision["allowed"])
        self.assertEqual(decision["reason"], "group_policy_denied")
        self.assertEqual(decision["mode"], "disabled")
        self.assertTrue(decision["require_mention"])

    async def test_a_direct_message_always_bypasses_regardless_of_config(self) -> None:
        decision = await personal_channels_service._enforce_group_policy(
            registration=self.registration,
            channel_key=_cut_over_channel_key("whatsapp"),
            agent_id="",
            message={"is_group": False, "is_mentioned": False, "is_reply_to_sage": False},
            remote_jid="stranger@s.whatsapp.net",
        )
        self.assertTrue(decision["allowed"])
        self.assertIsNone(decision["mode"])

    async def test_self_chat_bypasses_even_if_is_group_is_somehow_also_true(self) -> None:
        """Defense-in-depth: the owner's own self-chat is NEVER gated by
        either policy axis -- proven here even against a defensively
        malformed message that also sets is_group=True (self-chat is never
        actually a group on any real channel; see
        _enforce_group_policy's own docstring)."""
        decision = await personal_channels_service._enforce_group_policy(
            registration=self.registration,
            channel_key=_cut_over_channel_key("whatsapp"),
            agent_id="",
            message={"is_group": True, "is_self_chat": True, "is_mentioned": False, "is_reply_to_sage": False},
            remote_jid="15550001111@s.whatsapp.net",
        )
        self.assertTrue(decision["allowed"])

    async def test_require_mention_true_blocks_unaddressed_and_allows_explicit_mention(self) -> None:
        store = _FakeAgentInstallStore()
        store.installs["agent-1"] = {
            "group_policy": {
                _cut_over_channel_key("telegram"): {
                    "mode": "open", "allowlist": [], "require_mention": True,
                }
            }
        }
        with _patch_agent_install_store(store):
            blocked = await personal_channels_service._enforce_group_policy(
                registration=self.registration,
                channel_key=_cut_over_channel_key("telegram"),
                agent_id="agent-1",
                message={"is_group": True, "is_mentioned": False, "is_reply_to_sage": False},
                remote_jid="-100555",
            )
            mentioned = await personal_channels_service._enforce_group_policy(
                registration=self.registration,
                channel_key=_cut_over_channel_key("telegram"),
                agent_id="agent-1",
                message={"is_group": True, "is_mentioned": True, "is_reply_to_sage": False},
                remote_jid="-100555",
            )
            replied = await personal_channels_service._enforce_group_policy(
                registration=self.registration,
                channel_key=_cut_over_channel_key("telegram"),
                agent_id="agent-1",
                message={"is_group": True, "is_mentioned": False, "is_reply_to_sage": True},
                remote_jid="-100555",
            )
        self.assertFalse(blocked["allowed"])
        self.assertEqual(blocked["reason"], "group_no_mention")
        self.assertTrue(mentioned["allowed"])
        self.assertTrue(replied["allowed"])

    async def test_group_policy_disabled_blocks_every_group_message_regardless_of_mention(self) -> None:
        store = _FakeAgentInstallStore()
        store.installs["agent-2"] = {
            "group_policy": {
                _cut_over_channel_key("telegram"): {
                    "mode": "disabled", "allowlist": [], "require_mention": False,
                }
            }
        }
        with _patch_agent_install_store(store):
            decision = await personal_channels_service._enforce_group_policy(
                registration=self.registration,
                channel_key=_cut_over_channel_key("telegram"),
                agent_id="agent-2",
                # Even an explicit @mention must not save it -- disabled
                # means disabled, independent of the mention axis.
                message={"is_group": True, "is_mentioned": True, "is_reply_to_sage": False},
                remote_jid="-100555",
            )
        self.assertFalse(decision["allowed"])
        self.assertEqual(decision["reason"], "group_policy_denied")

    async def test_allowlist_mode_allows_listed_group_blocks_others(self) -> None:
        store = _FakeAgentInstallStore()
        store.installs["agent-3"] = {
            "group_policy": {
                _cut_over_channel_key("telegram"): {
                    "mode": "allowlist", "allowlist": ["-100555"], "require_mention": False,
                }
            }
        }
        with _patch_agent_install_store(store):
            allowed = await personal_channels_service._enforce_group_policy(
                registration=self.registration,
                channel_key=_cut_over_channel_key("telegram"),
                agent_id="agent-3",
                message={"is_group": True, "is_mentioned": False, "is_reply_to_sage": False},
                remote_jid="-100555",
            )
            blocked = await personal_channels_service._enforce_group_policy(
                registration=self.registration,
                channel_key=_cut_over_channel_key("telegram"),
                agent_id="agent-3",
                message={"is_group": True, "is_mentioned": False, "is_reply_to_sage": False},
                remote_jid="-100999",
            )
        self.assertTrue(allowed["allowed"])
        self.assertEqual(allowed["mode"], "allowlist")
        self.assertFalse(blocked["allowed"])
        self.assertEqual(blocked["reason"], "group_policy_denied")

    async def test_allowlist_mode_still_applies_the_mention_axis_on_top(self) -> None:
        """The two axes compose: being in the group allowlist does not
        itself bypass requireMention -- both must independently allow."""
        store = _FakeAgentInstallStore()
        store.installs["agent-4"] = {
            "group_policy": {
                _cut_over_channel_key("telegram"): {
                    "mode": "allowlist", "allowlist": ["-100555"], "require_mention": True,
                }
            }
        }
        with _patch_agent_install_store(store):
            decision = await personal_channels_service._enforce_group_policy(
                registration=self.registration,
                channel_key=_cut_over_channel_key("telegram"),
                agent_id="agent-4",
                message={"is_group": True, "is_mentioned": False, "is_reply_to_sage": False},
                remote_jid="-100555",
            )
        self.assertFalse(decision["allowed"])
        self.assertEqual(decision["reason"], "group_no_mention")

    async def test_unresolved_identity_fallback_is_split_from_the_resolved_agent_default(self) -> None:
        """UPDATED 2026-08-07 (CHANNEL-GATEWAY-PLAN.md §5 step 2), then
        UPDATED AGAIN 2026-08 ("the last unscoped channels") once Signal/
        iMessage/WeChat-personal gained real identity resolution
        (_resolve_local_bridge_agent_id) -- these two cases remain
        DELIBERATELY DIFFERENT, split the same way dm_policy already
        splits _unresolved_identity_dm_policy_config from
        DEFAULT_DM_POLICY_MODE -- see
        _unresolved_identity_group_policy_config's own docstring for why.

        An unresolved agent identity (agent_id="" -- LEGACY_UNSCOPED_AGENT_ID;
        no longer the PERMANENT case for local-bridge channels, but still a
        real, reachable case whenever resolution genuinely can't land) now
        fails CLOSED: disabled, require_mention=True. This channel_key is
        deliberately still a local-bridge channel key here -- proving the fallback is
        keyed on the identity itself (agent_id=="") being unresolved, not
        on which channel_key it's paired with.

        A RESOLVED agent_id that simply has no install bundle yet (e.g.
        get_workspace_agent_install_bundle returning None against the real,
        unmocked repository in this sandbox) is a channel where the write
        path (update_agent_group_policy_config) IS reachable -- so it gets
        this build's new safe default: allowlist/require_mention=True --
        the SAME values, but for a different reason (a configurable, if
        unconfigured, default -- not a fail-closed dead end)."""
        unresolved_identity_default = await personal_channels_service._load_agent_group_policy_config(
            tenant_id="t", workspace_id="w", agent_id="", channel_key=_cut_over_channel_key("signal"),
        )
        resolved_but_uninstalled_default = await personal_channels_service._load_agent_group_policy_config(
            tenant_id="t", workspace_id="w", agent_id="never-installed-agent",
            channel_key=_cut_over_channel_key("telegram"),
        )
        self.assertEqual(unresolved_identity_default["mode"], "disabled")
        self.assertTrue(unresolved_identity_default["require_mention"])
        self.assertEqual(resolved_but_uninstalled_default["mode"], "allowlist")
        self.assertTrue(resolved_but_uninstalled_default["require_mention"])


class MentionGatingResolverUnitTests(unittest.TestCase):
    """Direct tests against mention_gating_service.resolve_inbound_mention_decision
    -- the shared resolver in isolation, independent of group_policy storage."""

    def test_non_group_never_skips(self) -> None:
        decision = mention_gating_service.resolve_inbound_mention_decision(
            facts={"was_mentioned": False}, policy={"is_group": False, "require_mention": True},
        )
        self.assertFalse(decision["should_skip"])

    def test_require_mention_false_never_skips_even_when_unaddressed(self) -> None:
        decision = mention_gating_service.resolve_inbound_mention_decision(
            facts={"was_mentioned": False, "implicit_mention_kinds": set()},
            policy={"is_group": True, "require_mention": False},
        )
        self.assertFalse(decision["should_skip"])
        self.assertFalse(decision["effective_was_mentioned"])

    def test_require_mention_true_skips_when_unaddressed(self) -> None:
        decision = mention_gating_service.resolve_inbound_mention_decision(
            facts={"was_mentioned": False, "implicit_mention_kinds": set()},
            policy={"is_group": True, "require_mention": True},
        )
        self.assertTrue(decision["should_skip"])

    def test_require_mention_true_does_not_skip_explicit_mention(self) -> None:
        decision = mention_gating_service.resolve_inbound_mention_decision(
            facts={"was_mentioned": True, "implicit_mention_kinds": set()},
            policy={"is_group": True, "require_mention": True},
        )
        self.assertFalse(decision["should_skip"])
        self.assertTrue(decision["effective_was_mentioned"])

    def test_require_mention_true_does_not_skip_implicit_reply_to_agent(self) -> None:
        decision = mention_gating_service.resolve_inbound_mention_decision(
            facts={"was_mentioned": False, "implicit_mention_kinds": {"reply_to_agent"}},
            policy={"is_group": True, "require_mention": True},
        )
        self.assertFalse(decision["should_skip"])
        self.assertTrue(decision["effective_was_mentioned"])

    def test_can_detect_mention_false_never_skips_regardless_of_require_mention(self) -> None:
        """A channel that genuinely can't compute mention facts must fail
        OPEN, not silently drop every group message it can't classify."""
        decision = mention_gating_service.resolve_inbound_mention_decision(
            facts={"can_detect_mention": False, "was_mentioned": False},
            policy={"is_group": True, "require_mention": True},
        )
        self.assertFalse(decision["should_skip"])

    def test_allowed_implicit_mention_kinds_can_restrict_which_implicit_kinds_count(self) -> None:
        decision = mention_gating_service.resolve_inbound_mention_decision(
            facts={"was_mentioned": False, "implicit_mention_kinds": {"reply_to_agent"}},
            policy={"is_group": True, "require_mention": True, "allowed_implicit_mention_kinds": []},
        )
        self.assertTrue(decision["should_skip"])

    def test_mention_facts_from_message_never_reads_message_text(self) -> None:
        """HARD CONSTRAINT check: mention_facts_from_message must derive
        facts purely from is_mentioned/is_reply_to_sage -- proven here by
        passing a message whose text looks obviously addressed
        ("@bot please help") but whose precomputed facts say otherwise; the
        text must be completely ignored."""
        facts = mention_gating_service.mention_facts_from_message(
            {"text": "@bot please help me right now", "is_mentioned": False, "is_reply_to_sage": False},
        )
        self.assertFalse(facts["was_mentioned"])
        self.assertEqual(facts["implicit_mention_kinds"], set())

    def test_mention_facts_from_message_maps_is_reply_to_sage_to_the_implicit_kind(self) -> None:
        facts = mention_gating_service.mention_facts_from_message(
            {"is_mentioned": False, "is_reply_to_sage": True},
        )
        self.assertIn("reply_to_agent", facts["implicit_mention_kinds"])


class RequireMentionRestoresOldGateIntegrationTests(unittest.IsolatedAsyncioTestCase):
    """Through the LIVE gateway inbound handlers: proves requireMention=True
    (an explicit, owner-configured, per-agent+channel opt-in -- never an AI
    runtime decision) reproduces the exact pre-2026-07-23 hard mention gate
    byte-for-byte, so nothing that relied on the old strict default has
    actually lost the ability to have it."""

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
            "gateway_id": "gw-req-mention-1",
            "workspace_id": "default",
            "tenant_id": "tenant-1",
            "device_trust_state": "trusted",
            "active_session_id": "sess-1",
        }
        # RETARGETED 2026-08-15: driven through
        # _handle_local_bridge_gateway_channel_inbound with Telegram's live
        # (transported) key, since _handle_telegram_gateway_channel_inbound was
        # deleted by commit 6b2baf97e -- the full OpenClaw cutover, 2026-08-14.
        # The gate under test (_enforce_group_policy + require_mention) is the
        # same one, on the handler that actually serves Telegram now.
        self.channel_key = _cut_over_channel_key("telegram")
        self.spec = personal_channels_service.LOCAL_BRIDGE_PERSONAL_CHANNELS[self.channel_key]
        self.store = _FakeAgentInstallStore()
        self.store.installs["agent-req-mention"] = {
            "group_policy": {
                self.channel_key: {
                    "mode": "open", "allowlist": [], "require_mention": True,
                }
            }
        }

    def tearDown(self) -> None:
        self.db_patcher.stop()
        self.tmpdir.cleanup()

    async def test_unaddressed_group_message_is_ignored_and_never_dispatched_when_configured(self) -> None:
        with (
            _patch_agent_install_store(self.store),
            patch(
                "server_modules.personal_channels_service._resolve_agent_id_for_inbound",
                return_value="agent-req-mention",
            ),
            patch(
                "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                return_value=_ALLOW_DISPATCH_DECISION,
            ),
            patch(
                "server_modules.personal_channels_service.personal_channel_sage_bridge_service"
                ".build_personal_channel_reply_async",
                new=AsyncMock(return_value={"text": "should never be built", "source": "sage"}),
            ) as build_reply_mock,
            patch(
                "server_modules.personal_channels_service.gateway_protocol_service.dispatch_channel_outbound",
                new=AsyncMock(side_effect=AssertionError("must not dispatch for an unaddressed group message")),
                create=True,
            ),
        ):
            result = await personal_channels_service._handle_local_bridge_gateway_channel_inbound(
                gateway_id="gw-req-mention-1",
                registration=self.registration,
                channel_key=self.channel_key,
                provider=str(self.spec["provider"]),
                label=str(self.spec["label"]),
                payload={
                    "message": {
                        "external_message_id": "tg-reqmention-1",
                        "remote_jid": "-100777",
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
        build_reply_mock.assert_not_awaited()
        self.assertTrue(result.get("ignored"))
        self.assertEqual(result.get("reason"), "group_no_mention")

    async def test_explicit_mention_still_dispatches_when_configured(self) -> None:
        with (
            _patch_agent_install_store(self.store),
            patch(
                "server_modules.personal_channels_service._resolve_agent_id_for_inbound",
                return_value="agent-req-mention",
            ),
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
                "server_modules.personal_channels_service.personal_channel_sage_bridge_service"
                ".build_personal_channel_reply_async",
                new=AsyncMock(return_value={"text": "Dinner's at 7.", "source": "sage"}),
            ) as build_reply_mock,
            patch(
                "server_modules.personal_channels_service.gateway_protocol_service.dispatch_channel_outbound",
                new=AsyncMock(return_value={"external_message_id": "tg-out-reqmention-1"}),
                create=True,
            ) as dispatch_mock,
        ):
            result = await personal_channels_service._handle_local_bridge_gateway_channel_inbound(
                gateway_id="gw-req-mention-1",
                registration=self.registration,
                channel_key=self.channel_key,
                provider=str(self.spec["provider"]),
                label=str(self.spec["label"]),
                payload={
                    "message": {
                        "external_message_id": "tg-reqmention-2",
                        "remote_jid": "-100777",
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
        build_reply_mock.assert_awaited_once()
        dispatch_mock.assert_awaited_once()
        self.assertFalse(result.get("blocked", False))


if __name__ == "__main__":
    unittest.main()

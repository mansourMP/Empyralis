"""fleet_tools.schedule_task — authority tier inheritance.

An audience-tier turn that schedules a future task must never result in an
owner-tier execution later just because the wake-up has no live channel
sender — the wake request must persist the caller's tier permanently
(authority_mandate_service.inherit_tier()).

Tests:
  (a) explicit authority_tier is persisted onto the wake request payload and
      the fleet ledger event
  (b) omitting authority_tier defaults to the safe "audience" tier, never owner
  (c) an invalid/garbage authority_tier value fails safe to "audience"
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from server_modules import fleet_tools


def _run(coro):
    return asyncio.run(coro)


class ScheduleTaskAuthorityTierTests(unittest.TestCase):

    def _run_schedule_task(self, **kwargs):
        propose_mock = AsyncMock(
            return_value={"wake_request": {"id": "wake-1"}, "accepted": True}
        )
        ledger_mock = AsyncMock(return_value=None)
        with (
            patch("server_modules.bounded_scheduler_service.propose_self_wakeup", propose_mock),
            patch.object(fleet_tools, "_ledger_fleet_action", ledger_mock),
        ):
            result = _run(
                fleet_tools.schedule_task(
                    workspace_id="ws-1",
                    agent_id="agent-1",
                    when="in 5 minutes",
                    instruction="check the inbox",
                    **kwargs,
                )
            )
        return result, propose_mock, ledger_mock

    def test_explicit_audience_tier_is_persisted_on_the_wake_payload(self):
        result, propose_mock, ledger_mock = self._run_schedule_task(authority_tier="audience")
        self.assertTrue(result["ok"])
        payload = propose_mock.call_args.kwargs["payload"]
        self.assertEqual(payload["authority_tier"], "audience")
        ledger_kwargs = ledger_mock.call_args.kwargs
        self.assertEqual(ledger_kwargs["metadata"]["authority_tier"], "audience")

    def test_explicit_owner_tier_is_persisted_on_the_wake_payload(self):
        result, propose_mock, ledger_mock = self._run_schedule_task(authority_tier="owner")
        self.assertTrue(result["ok"])
        payload = propose_mock.call_args.kwargs["payload"]
        self.assertEqual(payload["authority_tier"], "owner")

    def test_omitted_authority_tier_defaults_to_audience_not_owner(self):
        """The anti-laundering default: a caller that forgets to pass its tier
        must never have the schedule silently execute as owner later."""
        _, propose_mock, _ = self._run_schedule_task()
        payload = propose_mock.call_args.kwargs["payload"]
        self.assertEqual(payload["authority_tier"], "audience")

    def test_garbage_authority_tier_fails_safe_to_audience(self):
        _, propose_mock, _ = self._run_schedule_task(authority_tier="owner_but_spoofed")
        payload = propose_mock.call_args.kwargs["payload"]
        self.assertEqual(payload["authority_tier"], "audience")

    def test_audience_created_schedule_executes_as_audience_end_to_end(self):
        """The full chain: an audience-tier turn calls schedule_task -> the
        persisted wake-request payload -> bounded_scheduler_service's
        consumption-side tier resolution -> ends up audience, matching what
        the claimed row would actually execute as. Ties fleet_tools' write
        side to bounded_scheduler_service's read side so a change to either
        one's contract breaks a test, not just production."""
        result, propose_mock, _ = self._run_schedule_task(authority_tier="audience")
        self.assertTrue(result["ok"])
        persisted_payload = propose_mock.call_args.kwargs["payload"]

        from server_modules import bounded_scheduler_service

        claimed_row = {
            "id": "wake-1",
            "trigger_kind": "self_proposed",
            "summary": "check the inbox",
            "payload": persisted_payload,
        }
        tier, unattributed = bounded_scheduler_service._wake_request_tier(claimed_row)
        self.assertEqual(tier, "audience")
        self.assertFalse(unattributed)


class FleetGetAgentToolsCanonicalIdTests(unittest.TestCase):
    """fleet_get_agent_tools must expose/read the same tool id enforcement
    checks (sage_agent_runtime_service._specialist_tool_allowed), not
    skill_registry's own hyphenated display id — otherwise the Tools tab's
    enabled count and its toggle PATCH both operate on an id nothing
    enforces. See skill_registry.enforcement_tool_name."""

    def test_returns_canonical_enforcement_ids_not_hyphenated_skill_ids(self):
        bundle = {
            "install_metadata": {"role": "specialist"},
            "tool_toggles": {"web__search": True, "fleet__list_agents": True},
        }
        with patch(
            "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
            AsyncMock(return_value=bundle),
        ):
            result = _run(
                fleet_tools.fleet_get_agent_tools(
                    workspace_id="ws-1", tenant_id="tenant-1", agent_id="ainstall-1",
                )
            )
        self.assertTrue(result["ok"])
        by_id = {t["id"]: t for t in result["tools"]}
        self.assertNotIn("web-search", by_id)
        self.assertIn("web__search", by_id)
        self.assertTrue(by_id["web__search"]["enabled"])
        self.assertNotIn("fleet-list-agents", by_id)
        self.assertIn("fleet__list_agents", by_id)
        self.assertTrue(by_id["fleet__list_agents"]["enabled"])
        # A tool never toggled on is present (full catalog) but disabled.
        self.assertIn("browser__navigate", by_id)
        self.assertFalse(by_id["browser__navigate"]["enabled"])

    def test_master_agent_shows_everything_enabled_regardless_of_toggles(self):
        bundle = {"install_metadata": {"role": "operator"}, "tool_toggles": {}}
        with patch(
            "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
            AsyncMock(return_value=bundle),
        ):
            result = _run(
                fleet_tools.fleet_get_agent_tools(
                    workspace_id="ws-1", tenant_id="tenant-1", agent_id="ainstall-sage",
                )
            )
        self.assertTrue(result["ok"])
        self.assertTrue(result["is_master"])
        self.assertTrue(all(t["enabled"] for t in result["tools"]))


class FleetGetAgentToolsMandateStateTests(unittest.TestCase):
    """Customer access (Part 10 Authority Mandate) surfaced on the Tools tab:
    audience_safe is the platform's own manifest default (never toggleable),
    mandate_granted reflects this owner's mandate.audience_tools list, keyed
    by the same canonical enforcement id fleet_get_agent_tools already uses
    for `id`/`enabled` — not the connector.action dot form."""

    def test_audience_safe_and_mandate_granted_reflect_manifest_and_owner_grant(self):
        bundle = {
            "install_metadata": {
                "role": "specialist",
                "mandate": {"audience_tools": ["fleet__configure_agent"]},
            },
            "tool_toggles": {"web__search": True, "fleet__configure_agent": True, "shell__exec": True},
        }
        with patch(
            "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
            AsyncMock(return_value=bundle),
        ):
            result = _run(
                fleet_tools.fleet_get_agent_tools(
                    workspace_id="ws-1", tenant_id="tenant-1", agent_id="ainstall-1",
                )
            )
        self.assertTrue(result["ok"])
        by_id = {t["id"]: t for t in result["tools"]}
        # web__search is audience_safe=True on its own manifest ("Safe by
        # default") — informational, not something the owner granted.
        self.assertTrue(by_id["web__search"]["audience_safe"])
        self.assertFalse(by_id["web__search"]["mandate_granted"])
        # fleet__configure_agent defaults to owner-only, but this owner
        # explicitly granted it via mandate.audience_tools, by its canonical
        # enforcement id — not the "fleet.configure_agent" dot form.
        self.assertFalse(by_id["fleet__configure_agent"]["audience_safe"])
        self.assertTrue(by_id["fleet__configure_agent"]["mandate_granted"])
        # A tool with neither is plain "Owner only".
        self.assertFalse(by_id["shell__exec"]["audience_safe"])
        self.assertFalse(by_id["shell__exec"]["mandate_granted"])

    def test_no_mandate_metadata_defaults_every_non_safe_tool_to_ungranted(self):
        bundle = {"install_metadata": {"role": "specialist"}, "tool_toggles": {}}
        with patch(
            "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
            AsyncMock(return_value=bundle),
        ):
            result = _run(
                fleet_tools.fleet_get_agent_tools(
                    workspace_id="ws-1", tenant_id="tenant-1", agent_id="ainstall-1",
                )
            )
        self.assertTrue(result["ok"])
        by_id = {t["id"]: t for t in result["tools"]}
        self.assertFalse(by_id["shell__exec"]["mandate_granted"])


class FleetScheduleControlTests(unittest.TestCase):
    """Owner-facing schedule surface (Part U2): fleet_list_agent_schedule,
    fleet_create_agent_schedule, fleet_cancel_agent_schedule,
    fleet_preview_schedule_when. Owner-gating itself lives in the REST route
    (enforce_workspace_access minimum_role="owner"), same as
    fleet_stop_agent/fleet_resume_agent — these service functions don't
    re-check role, matching that established split."""

    def _bundle(self):
        return {"id": "agent-x", "install_metadata": {}}

    def test_create_schedule_always_forces_owner_tier(self):
        """The route this wraps is owner-only and reachable by no one else —
        it IS the origin of authority, not something inheriting a caller's
        tier, so it must always stamp owner regardless of what a compromised
        or buggy caller might pass."""
        with (
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                new=AsyncMock(return_value=self._bundle()),
            ),
            patch(
                "server_modules.bounded_scheduler_service.propose_self_wakeup",
                new=AsyncMock(return_value={"accepted": True, "wake_request": {"id": "wake-1"}}),
            ) as propose_mock,
            patch.object(fleet_tools, "_ledger_fleet_action", new=AsyncMock()),
        ):
            result = _run(fleet_tools.fleet_create_agent_schedule(
                actor_id="user-1", workspace_id="ws-1", agent_id="agent-x",
                when="in 10 minutes", instruction="check the inbox",
            ))
        self.assertTrue(result["ok"])
        payload = propose_mock.call_args.kwargs["payload"]
        self.assertEqual(payload["authority_tier"], "owner")
        self.assertEqual(payload["agent_id"], "agent-x")

    def test_create_schedule_rejects_unknown_agent(self):
        with patch(
            "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
            new=AsyncMock(return_value=None),
        ):
            result = _run(fleet_tools.fleet_create_agent_schedule(
                actor_id="user-1", workspace_id="ws-1", agent_id="ghost",
                when="in 10 minutes", instruction="check the inbox",
            ))
        self.assertFalse(result["ok"])

    def test_create_schedule_rejects_unparseable_when(self):
        with patch(
            "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
            new=AsyncMock(return_value=self._bundle()),
        ):
            result = _run(fleet_tools.fleet_create_agent_schedule(
                actor_id="user-1", workspace_id="ws-1", agent_id="agent-x",
                when="whenever", instruction="check the inbox",
            ))
        self.assertFalse(result["ok"])

    def test_list_schedule_excludes_terminal_statuses_and_shapes_rows(self):
        rows = [
            {
                "id": "wake-1", "status": "pending", "due_at": "2026-07-10T00:00:00+00:00",
                "created_at": "2026-07-09T00:00:00+00:00", "summary": "a",
                "payload": {"instruction": "a", "agent_id": "agent-x"},
            },
            {
                "id": "wake-2", "status": "executed", "due_at": "2026-07-09T00:00:00+00:00",
                "created_at": "2026-07-08T00:00:00+00:00", "summary": "b",
                "payload": {"instruction": "b", "agent_id": "agent-x"},
            },
            {
                "id": "wake-3", "status": "claimed", "due_at": "2026-07-10T01:00:00+00:00",
                "created_at": "2026-07-09T01:00:00+00:00", "summary": "c",
                "payload": {"instruction": "c", "agent_id": "agent-x", "authority_tier": "audience"},
            },
        ]
        with (
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                new=AsyncMock(return_value=self._bundle()),
            ),
            patch(
                "server_modules.control_plane_repository.list_agent_scheduler_wake_requests",
                new=AsyncMock(return_value=rows),
            ) as list_mock,
        ):
            result = _run(fleet_tools.fleet_list_agent_schedule(workspace_id="ws-1", agent_id="agent-x"))
        self.assertTrue(result["ok"])
        ids = {row["id"] for row in result["schedule"]}
        # wake-2 is "executed" — resolved, not still "scheduled".
        self.assertEqual(ids, {"wake-1", "wake-3"})
        self.assertEqual(list_mock.call_args.kwargs["agent_id"], "agent-x")
        by_id = {row["id"]: row for row in result["schedule"]}
        self.assertEqual(by_id["wake-1"]["description"], "a")
        # No authority_tier in payload -> fails safe to audience, never owner.
        self.assertEqual(by_id["wake-1"]["authority_tier"], "audience")
        self.assertEqual(by_id["wake-3"]["authority_tier"], "audience")

    def test_cancel_schedule_transitions_status_and_ledgers(self):
        """payload comes back from a real row as a JSON string, not a dict —
        no jsonb codec is registered on this connection (see
        fleet_get_agent_tools' tool_toggles handling for the same pattern).
        Regression guard: cancel_wake_request's agent_id check must parse it,
        not silently see {} and reject every real cancel as "not found"."""
        existing = {
            "id": "wake-1", "status": "pending", "tenant_id": "system", "workspace_id": "ws-1",
            "payload": '{"agent_id": "agent-x", "instruction": "a"}',
        }
        with (
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                new=AsyncMock(return_value=self._bundle()),
            ),
            patch(
                "server_modules.control_plane_repository.get_agent_scheduler_wake_request",
                new=AsyncMock(return_value=existing),
            ),
            patch(
                "server_modules.control_plane_repository.update_agent_scheduler_wake_request_status",
                new=AsyncMock(return_value={**existing, "status": "cancelled"}),
            ) as update_mock,
            patch.object(fleet_tools, "_ledger_fleet_action", new=AsyncMock()) as ledger_mock,
        ):
            result = _run(fleet_tools.fleet_cancel_agent_schedule(
                actor_id="user-1", workspace_id="ws-1", agent_id="agent-x", wake_request_id="wake-1",
            ))
        self.assertTrue(result["ok"])
        self.assertEqual(update_mock.call_args.kwargs["status"], "cancelled")
        ledger_mock.assert_awaited_once()

    def test_cancel_schedule_rejects_wrong_agent(self):
        """A wake_request_id that exists but belongs to a different agent
        must not be cancelable through this agent's route — same not-found
        error either way, so it doesn't leak that the id exists elsewhere."""
        existing = {
            "id": "wake-1", "status": "pending",
            "payload": '{"agent_id": "agent-OTHER", "instruction": "a"}',
        }
        with (
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                new=AsyncMock(return_value=self._bundle()),
            ),
            patch(
                "server_modules.control_plane_repository.get_agent_scheduler_wake_request",
                new=AsyncMock(return_value=existing),
            ),
        ):
            result = _run(fleet_tools.fleet_cancel_agent_schedule(
                actor_id="user-1", workspace_id="ws-1", agent_id="agent-x", wake_request_id="wake-1",
            ))
        self.assertFalse(result["ok"])

    def test_cancel_schedule_rejects_already_terminal(self):
        existing = {
            "id": "wake-1", "status": "executed",
            "payload": '{"agent_id": "agent-x", "instruction": "a"}',
        }
        with (
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                new=AsyncMock(return_value=self._bundle()),
            ),
            patch(
                "server_modules.control_plane_repository.get_agent_scheduler_wake_request",
                new=AsyncMock(return_value=existing),
            ),
        ):
            result = _run(fleet_tools.fleet_cancel_agent_schedule(
                actor_id="user-1", workspace_id="ws-1", agent_id="agent-x", wake_request_id="wake-1",
            ))
        self.assertFalse(result["ok"])
        self.assertIn("already", result["error"])

    def test_preview_schedule_when_resolves_relative_time(self):
        result = fleet_tools.fleet_preview_schedule_when(when="in 30 minutes")
        self.assertTrue(result["ok"])
        self.assertIn("due_at", result)

    def test_preview_schedule_when_rejects_unparseable(self):
        result = fleet_tools.fleet_preview_schedule_when(when="whenever")
        self.assertFalse(result["ok"])


class ResolveHardwareStatusCloudHonestyTests(unittest.TestCase):
    """fleet_tools._resolve_hardware_status — no-fake-state fix.

    Before this fix, a cloud-placement agent's hardware_status was reported
    "online" UNCONDITIONALLY (the comment literally said "always online"),
    regardless of whether its bound model_config could actually produce a
    turn. That feeds the Status row, sidebar dots, and the "Online: N/M"
    count on the agents list — a dead BYOK key or exhausted platform
    entitlement still showed a green "Ready" dot. These tests pin the
    honest replacement: ready only when the SAME provider-resolution logic
    the runtime uses at turn time (sage_agent_runtime_service's
    _resolve_agent_cloud_provider / _resolve_cloud_provider) would actually
    resolve a usable credential.
    """

    @staticmethod
    def _cloud_inst(model_config):
        return {
            "id": "agent-1",
            "runtime_profile": {"default_execution_target": "cloud"},
            "metadata": {"model_config": model_config},
        }

    def test_platform_credits_ready_when_workspace_default_provider_resolves(self):
        """The honest platform_credits default resolves to the workspace's
        shared default provider — "workspace has a usable default provider"
        is what "ready" means here, not any agent-specific credential."""
        inst = self._cloud_inst({"mode": "platform_credits"})
        with patch(
            "server_modules.sage_agent_runtime_service._resolve_cloud_provider",
            new=AsyncMock(return_value=("deepseek", {"api_key": "sk-live"})),
        ):
            status, last_hb, run_id, reason = _run(
                fleet_tools._resolve_hardware_status(inst, {}, workspace_id="ws-1")
            )
        self.assertEqual(status, "online")
        self.assertIsNone(last_hb)
        self.assertIsNone(run_id)
        self.assertIsNone(reason)

    def test_platform_credits_not_ready_when_workspace_default_provider_unavailable(self):
        """The exact bug this fix targets: an agent whose model_config can't
        actually produce a turn used to still show "online"."""
        inst = self._cloud_inst({"mode": "platform_credits"})
        with patch(
            "server_modules.sage_agent_runtime_service._resolve_cloud_provider",
            new=AsyncMock(side_effect=RuntimeError("Platform AI credits are exhausted.")),
        ):
            status, last_hb, run_id, reason = _run(
                fleet_tools._resolve_hardware_status(inst, {}, workspace_id="ws-1")
            )
        self.assertEqual(status, "error")
        self.assertIsNone(last_hb)
        self.assertIsNone(run_id)
        self.assertIn("exhausted", reason)

    def test_default_model_config_is_treated_as_platform_credits(self):
        """An agent install with no model_config at all (the seed default)
        must resolve exactly like an explicit platform_credits agent — not
        fall into the "unrecognized mode" bucket."""
        inst = self._cloud_inst({})
        with patch(
            "server_modules.sage_agent_runtime_service._resolve_cloud_provider",
            new=AsyncMock(return_value=("deepseek", {"api_key": "sk-live"})),
        ):
            status, _last_hb, _run_id, reason = _run(
                fleet_tools._resolve_hardware_status(inst, {}, workspace_id="ws-1")
            )
        self.assertEqual(status, "online")
        self.assertIsNone(reason)

    def test_platform_credits_with_own_provider_ready_uses_that_provider_not_workspace_default(self):
        """§29 mirror: an agent with its OWN stored platform_credits provider
        is checked against THAT provider's credential, never the shared
        workspace default — proven by making the workspace-default resolver
        explode if it's ever called."""
        inst = self._cloud_inst({"mode": "platform_credits", "provider": "anthropic"})
        exploding_workspace_default = AsyncMock(
            side_effect=AssertionError("must not check the workspace default for an agent with its own provider")
        )
        with (
            patch(
                "server_modules.sage_agent_runtime_service._resolve_cloud_provider",
                new=exploding_workspace_default,
            ),
            patch(
                "server_modules.direct_chat_provider_service.direct_chat_credentials",
                return_value={"api_key": "sk-ant-live"},
            ),
            patch(
                "server_modules.direct_chat_provider_service.supports_direct_message_native_chat",
                return_value=True,
            ),
        ):
            status, _last_hb, _run_id, reason = _run(
                fleet_tools._resolve_hardware_status(inst, {}, workspace_id="ws-1")
            )
        self.assertEqual(status, "online")
        self.assertIsNone(reason)
        exploding_workspace_default.assert_not_awaited()

    def test_platform_credits_with_own_provider_not_ready_when_that_provider_is_dead(self):
        """The honesty half of the mirror: a dead per-agent provider reads
        "error", not "online" — even though the workspace default (unchecked
        here) might otherwise resolve fine."""
        inst = self._cloud_inst({"mode": "platform_credits", "provider": "anthropic"})
        with (
            patch(
                "server_modules.direct_chat_provider_service.direct_chat_credentials",
                return_value={},
            ),
            patch(
                "server_modules.direct_chat_provider_service.supports_direct_message_native_chat",
                return_value=False,
            ),
        ):
            status, _last_hb, _run_id, reason = _run(
                fleet_tools._resolve_hardware_status(inst, {}, workspace_id="ws-1")
            )
        self.assertEqual(status, "error")
        self.assertIn("anthropic", reason)

    def test_byok_api_ready_when_key_configured(self):
        inst = self._cloud_inst({"mode": "byok_api", "provider": "anthropic"})
        with (
            patch(
                "server_modules.direct_chat_provider_service.direct_chat_credentials",
                return_value={"api_key": "sk-ant-live"},
            ),
            patch(
                "server_modules.direct_chat_provider_service.supports_direct_message_native_chat",
                return_value=True,
            ),
        ):
            status, _last_hb, _run_id, reason = _run(
                fleet_tools._resolve_hardware_status(inst, {}, workspace_id="ws-1")
            )
        self.assertEqual(status, "online")
        self.assertIsNone(reason)

    def test_byok_api_not_ready_when_key_missing(self):
        """A dead/missing BYOK credential — this used to still read
        "online" unconditionally."""
        inst = self._cloud_inst({"mode": "byok_api", "provider": "anthropic"})
        with (
            patch(
                "server_modules.direct_chat_provider_service.direct_chat_credentials",
                return_value={},
            ),
            patch(
                "server_modules.direct_chat_provider_service.supports_direct_message_native_chat",
                return_value=False,
            ),
        ):
            status, last_hb, run_id, reason = _run(
                fleet_tools._resolve_hardware_status(inst, {}, workspace_id="ws-1")
            )
        self.assertEqual(status, "error")
        self.assertIsNone(last_hb)
        self.assertIsNone(run_id)
        self.assertIn("anthropic", reason)

    def test_byok_api_not_ready_when_no_provider_specified(self):
        inst = self._cloud_inst({"mode": "byok_api"})
        status, _last_hb, _run_id, reason = _run(
            fleet_tools._resolve_hardware_status(inst, {}, workspace_id="ws-1")
        )
        self.assertEqual(status, "error")
        self.assertIn("BYOK", reason)

    def test_cli_subscription_not_ready_without_gateway_binding(self):
        inst = self._cloud_inst({"mode": "cli_subscription"})
        status, _last_hb, _run_id, reason = _run(
            fleet_tools._resolve_hardware_status(inst, {}, workspace_id="ws-1")
        )
        self.assertEqual(status, "error")
        self.assertIn("paired computer", reason)

    def test_cli_subscription_ready_with_valid_gateway_binding(self):
        """No live call to the gateway — presence of a bound gateway +
        supported runtime is "usable", matching
        _resolve_agent_cloud_provider's own (non-live) validation."""
        inst = self._cloud_inst({
            "mode": "cli_subscription", "gateway_binding": "gw-1", "runtime": "claude_code",
        })
        status, _last_hb, _run_id, reason = _run(
            fleet_tools._resolve_hardware_status(inst, {}, workspace_id="ws-1")
        )
        self.assertEqual(status, "online")
        self.assertIsNone(reason)

    def test_unresolvable_internal_error_fails_closed_not_ready(self):
        """An unexpected internal error while checking readiness must never
        be read as "ready" — that would silently reintroduce the
        always-online lie."""
        inst = self._cloud_inst({"mode": "platform_credits"})
        with patch(
            "server_modules.sage_agent_runtime_service._resolve_cloud_provider",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ):
            status, _last_hb, _run_id, reason = _run(
                fleet_tools._resolve_hardware_status(inst, {}, workspace_id="ws-1")
            )
        self.assertEqual(status, "error")
        self.assertTrue(reason)

    def test_gateway_agent_heartbeat_status_unaffected_by_cloud_honesty_check(self):
        """Non-cloud (gateway/VPS) agents must keep resolving purely off the
        heartbeat table — the cloud-only readiness check must never run for
        them, even when their model_config would itself be unresolvable."""
        inst = {
            "id": "agent-1",
            "runtime_profile": {"default_execution_target": "gateway", "machine_id": "mach-1"},
            "metadata": {"model_config": {"mode": "byok_api", "provider": "anthropic"}},
        }
        heartbeats = {
            "mach-1": {"online": True, "last_heartbeat_at": "2026-07-15T00:00:00Z", "current_run_id": "run-1"},
        }
        status, last_hb, run_id, reason = _run(
            fleet_tools._resolve_hardware_status(inst, heartbeats, workspace_id="ws-1")
        )
        self.assertEqual(status, "online")
        self.assertEqual(last_hb, "2026-07-15T00:00:00Z")
        self.assertEqual(run_id, "run-1")
        self.assertIsNone(reason)

    def test_unpaired_agent_is_unknown_not_error(self):
        inst = {"id": "agent-1", "runtime_profile": {}, "metadata": {}}
        status, _last_hb, _run_id, reason = _run(
            fleet_tools._resolve_hardware_status(inst, {}, workspace_id="ws-1")
        )
        self.assertEqual(status, "unknown")
        self.assertIsNone(reason)


class RecommendedModelConfigForGatewayTests(unittest.TestCase):
    """§29 per-agent-provider fix, task (c): hardware-aware "recommended"
    reuse. fleet_tools.recommended_model_config_for_gateway is the reusable
    detection the create-agent wizard's Brain step is recommended off of —
    reuses gateway_registry_service.gateway_registration_public_payload's
    llm_runtimes, the SAME source fleet_configure_agent's own
    cli_subscription save-time check (above) already reads. No live network
    call — a plain, fast, unit-testable function."""

    @staticmethod
    def _registration(**overrides):
        base = {"gateway_id": "gateway-1", "workspace_id": "ws-1", "status": "active"}
        base.update(overrides)
        return base

    @staticmethod
    def _llm_runtimes_payload(*, claude_code=None, codex=None):
        def _entry(state):
            if state is None:
                return {"installed": False, "authenticated": False}
            installed, authenticated = state
            return {"installed": installed, "authenticated": authenticated}

        return {"llm_runtimes": {"claude_code": _entry(claude_code), "codex": _entry(codex)}}

    def test_a_new_agent_created_on_a_gateway_with_an_authenticated_subscription_gets_it_recommended(self):
        """Task's literal ask (c): a box with an authenticated Codex CLI is
        recommended for a new agent being placed on it."""
        with (
            patch("server_modules.gateway_state_repository.get_gateway_registration", return_value=self._registration()),
            patch(
                "server_modules.gateway_registry_service.gateway_registration_public_payload",
                return_value=self._llm_runtimes_payload(codex=(True, True)),
            ),
        ):
            result = fleet_tools.recommended_model_config_for_gateway("gateway-1", workspace_id="ws-1")
        self.assertEqual(result, {
            "mode": "cli_subscription",
            "provider": "openai-codex",
            "runtime": "codex",
            "gateway_binding": "gateway-1",
        })

    def test_claude_code_preferred_over_codex_when_both_ready(self):
        with (
            patch("server_modules.gateway_state_repository.get_gateway_registration", return_value=self._registration()),
            patch(
                "server_modules.gateway_registry_service.gateway_registration_public_payload",
                return_value=self._llm_runtimes_payload(claude_code=(True, True), codex=(True, True)),
            ),
        ):
            result = fleet_tools.recommended_model_config_for_gateway("gateway-1", workspace_id="ws-1")
        self.assertEqual(result["runtime"], "claude_code")
        self.assertEqual(result["provider"], "claude_code_cli")

    def test_installed_but_not_authenticated_is_not_recommended(self):
        """Installed-but-signed-out must not be recommended — reusing it
        would fail the very first turn, the opposite of "no re-login"."""
        with (
            patch("server_modules.gateway_state_repository.get_gateway_registration", return_value=self._registration()),
            patch(
                "server_modules.gateway_registry_service.gateway_registration_public_payload",
                return_value=self._llm_runtimes_payload(claude_code=(True, False)),
            ),
        ):
            result = fleet_tools.recommended_model_config_for_gateway("gateway-1", workspace_id="ws-1")
        self.assertIsNone(result)

    def test_no_runtime_ready_returns_none(self):
        with (
            patch("server_modules.gateway_state_repository.get_gateway_registration", return_value=self._registration()),
            patch(
                "server_modules.gateway_registry_service.gateway_registration_public_payload",
                return_value=self._llm_runtimes_payload(),
            ),
        ):
            result = fleet_tools.recommended_model_config_for_gateway("gateway-1", workspace_id="ws-1")
        self.assertIsNone(result)

    def test_empty_gateway_id_returns_none_without_any_lookup(self):
        exploding = AsyncMock(side_effect=AssertionError("must not look up an empty gateway id"))
        with patch("server_modules.gateway_state_repository.get_gateway_registration", new=exploding):
            result = fleet_tools.recommended_model_config_for_gateway("", workspace_id="ws-1")
        self.assertIsNone(result)

    def test_unknown_gateway_id_returns_none(self):
        with patch("server_modules.gateway_state_repository.get_gateway_registration", return_value=None):
            result = fleet_tools.recommended_model_config_for_gateway("gateway-missing", workspace_id="ws-1")
        self.assertIsNone(result)

    def test_gateway_bound_to_a_different_workspace_returns_none(self):
        """A gateway_id belonging to another workspace must never leak a
        recommendation across tenants."""
        with (
            patch(
                "server_modules.gateway_state_repository.get_gateway_registration",
                return_value=self._registration(workspace_id="ws-OTHER"),
            ),
            patch(
                "server_modules.gateway_registry_service.gateway_registration_public_payload",
                return_value=self._llm_runtimes_payload(claude_code=(True, True)),
            ),
        ):
            result = fleet_tools.recommended_model_config_for_gateway("gateway-1", workspace_id="ws-1")
        self.assertIsNone(result)

    def test_lookup_failure_returns_none_never_raises(self):
        """Best-effort: this is a UX hint, never a gate — a lookup error must
        never surface as an exception to the creation flow."""
        with patch(
            "server_modules.gateway_state_repository.get_gateway_registration",
            side_effect=RuntimeError("db unavailable"),
        ):
            result = fleet_tools.recommended_model_config_for_gateway("gateway-1", workspace_id="ws-1")
        self.assertIsNone(result)


class FleetConfigureAgentRecommendationTests(unittest.TestCase):
    """fleet_configure_agent surfaces recommended_model_config in its
    response when a patch (re)binds preferred_gateway_id — the single
    source of truth the create-agent wizard's Placement step reads off the
    SAME PATCH it already makes, no extra round trip."""

    @staticmethod
    def _bundle(agent_id="agent-x", metadata=None):
        return {"id": agent_id, "install_metadata": dict(metadata or {})}

    def test_binding_a_gateway_with_ready_subscription_surfaces_recommendation(self):
        with (
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                new=AsyncMock(return_value=self._bundle()),
            ),
            patch(
                "server_modules.agent_registry_repository.update_workspace_agent_install",
                new=AsyncMock(return_value=self._bundle()),
            ),
            patch.object(
                fleet_tools,
                "recommended_model_config_for_gateway",
                return_value={"mode": "cli_subscription", "provider": "claude_code_cli", "runtime": "claude_code", "gateway_binding": "gateway-1"},
            ) as mock_recommend,
        ):
            result = _run(
                fleet_tools.fleet_configure_agent(
                    actor_id="owner-1",
                    workspace_id="ws-1",
                    agent_id="agent-x",
                    patch={"preferred_gateway_id": "gateway-1"},
                )
            )
        self.assertTrue(result["ok"])
        self.assertEqual(result.get("recommended_model_config"), {
            "mode": "cli_subscription", "provider": "claude_code_cli", "runtime": "claude_code", "gateway_binding": "gateway-1",
        })
        mock_recommend.assert_called_once_with("gateway-1", workspace_id="ws-1")

    def test_binding_a_gateway_with_no_ready_subscription_omits_the_key(self):
        with (
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                new=AsyncMock(return_value=self._bundle()),
            ),
            patch(
                "server_modules.agent_registry_repository.update_workspace_agent_install",
                new=AsyncMock(return_value=self._bundle()),
            ),
            patch.object(fleet_tools, "recommended_model_config_for_gateway", return_value=None),
        ):
            result = _run(
                fleet_tools.fleet_configure_agent(
                    actor_id="owner-1",
                    workspace_id="ws-1",
                    agent_id="agent-x",
                    patch={"preferred_gateway_id": "gateway-1"},
                )
            )
        self.assertTrue(result["ok"])
        self.assertNotIn("recommended_model_config", result)

    def test_unbinding_to_cloud_never_looks_up_a_recommendation(self):
        """placement="cloud" sends preferred_gateway_id="" — an empty
        binding must skip the lookup entirely, not "recommend" for a blank
        gateway id."""
        exploding = AsyncMock(side_effect=AssertionError("must not compute a recommendation for an empty gateway id"))
        with (
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                new=AsyncMock(return_value=self._bundle()),
            ),
            patch(
                "server_modules.agent_registry_repository.update_workspace_agent_install",
                new=AsyncMock(return_value=self._bundle()),
            ),
            patch.object(fleet_tools, "recommended_model_config_for_gateway", new=exploding),
        ):
            result = _run(
                fleet_tools.fleet_configure_agent(
                    actor_id="owner-1",
                    workspace_id="ws-1",
                    agent_id="agent-x",
                    patch={"preferred_gateway_id": ""},
                )
            )
        self.assertTrue(result["ok"])
        self.assertNotIn("recommended_model_config", result)

    def test_patch_unrelated_to_hardware_never_looks_up_a_recommendation(self):
        """A patch that never touches preferred_gateway_id at all (e.g. a
        plain rename) must not pay for the lookup."""
        exploding = AsyncMock(side_effect=AssertionError("must not compute a recommendation when hardware wasn't touched"))
        with (
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                new=AsyncMock(return_value=self._bundle()),
            ),
            patch(
                "server_modules.agent_registry_repository.update_workspace_agent_install",
                new=AsyncMock(return_value=self._bundle()),
            ),
            patch.object(fleet_tools, "recommended_model_config_for_gateway", new=exploding),
        ):
            result = _run(
                fleet_tools.fleet_configure_agent(
                    actor_id="owner-1",
                    workspace_id="ws-1",
                    agent_id="agent-x",
                    patch={"display_name": "Renamed Agent"},
                )
            )
        self.assertTrue(result["ok"])
        self.assertNotIn("recommended_model_config", result)


class FleetListAgentsHardwareStatusIntegrationTests(unittest.TestCase):
    """fleet_list_agents end-to-end: hardware_status/hardware_status_reason
    for a cloud agent must reflect its REAL model_config, not always read
    "online" — the same fix, exercised through the actual tool the
    agents/project list UI polls."""

    @staticmethod
    def _installs():
        return [
            {
                "id": "agent-broken",
                "label": "Broken Cloud Agent",
                "project_id": "proj-1",
                "runtime_profile": {"default_execution_target": "cloud"},
                "metadata": {
                    "role": "specialist",
                    "model_config": {"mode": "byok_api", "provider": "anthropic"},
                },
            },
            {
                "id": "agent-healthy",
                "label": "Healthy Cloud Agent",
                "project_id": "proj-1",
                "runtime_profile": {"default_execution_target": "cloud"},
                "metadata": {"role": "specialist", "model_config": {"mode": "platform_credits"}},
            },
        ]

    def test_list_agents_reports_honest_status_per_agent(self):
        with (
            patch(
                "server_modules.agent_registry_repository.list_workspace_agent_installs",
                new=AsyncMock(return_value=self._installs()),
            ),
            patch.object(fleet_tools, "_fetch_latest_heartbeats", new=AsyncMock(return_value={})),
            patch.object(fleet_tools, "_fetch_latest_activity", new=AsyncMock(return_value={})),
            patch.object(fleet_tools, "_fetch_agent_channels", new=AsyncMock(return_value={})),
            patch(
                "server_modules.direct_chat_provider_service.direct_chat_credentials",
                return_value={},
            ),
            patch(
                "server_modules.direct_chat_provider_service.supports_direct_message_native_chat",
                return_value=False,
            ),
            patch(
                "server_modules.sage_agent_runtime_service._resolve_cloud_provider",
                new=AsyncMock(return_value=("deepseek", {"api_key": "sk-live"})),
            ),
        ):
            result = _run(fleet_tools.fleet_list_agents(actor_id="user-1", workspace_id="ws-1"))

        self.assertTrue(result["ok"])
        by_id = {a["agent_id"]: a for a in result["agents"]}

        broken = by_id["agent-broken"]
        self.assertEqual(broken["hardware_status"], "error")
        self.assertTrue(broken["hardware_status_reason"])
        self.assertIn("anthropic", broken["hardware_status_reason"])

        healthy = by_id["agent-healthy"]
        self.assertEqual(healthy["hardware_status"], "online")
        self.assertIsNone(healthy["hardware_status_reason"])


if __name__ == "__main__":
    unittest.main()

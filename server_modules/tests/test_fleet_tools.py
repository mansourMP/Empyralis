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
from server_modules import codex_model_catalog_service


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
    """fleet_get_agent_tools must expose the same tool id enforcement checks
    (agent_turn_runtime_service._specialist_tool_allowed) use, not
    skill_registry's own hyphenated display id — otherwise a mandate
    (Customer Access) grant PATCH would operate on an id nothing enforces.
    See skill_registry.enforcement_tool_name.

    2026-08-14 (CLAUDE.md, founder decision): there is no more per-tool
    enable/disable checklist, so the response no longer carries an
    `enabled` field or a `core_tools` bucket — every tool is already
    available to the agent itself; what's left is only WHO (Customer
    Access / mandate) may trigger it, covered by
    FleetGetAgentToolsMandateStateTests below."""

    def test_returns_canonical_enforcement_ids_not_hyphenated_skill_ids(self):
        bundle = {"install_metadata": {"role": "specialist"}}
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
        self.assertNotIn("fleet-list-agents", by_id)
        self.assertIn("fleet__list_agents", by_id)
        self.assertIn("browser__navigate", by_id)
        self.assertNotIn("enabled", by_id["browser__navigate"])

    def test_master_agent_flagged_is_master(self):
        bundle = {"install_metadata": {"role": "operator"}}
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


class FleetGetAgentToolsExcludesCapabilityGatedToolsTests(unittest.TestCase):
    """generate_image (capability_id="image_generation") must NOT appear in
    the Tools tab's toggle list — its toggle would have zero runtime effect
    now that _specialist_tool_allowed decides it solely via capability
    resolution (see agent_turn_runtime_service.py), and a toggle with no
    effect is exactly the "lying toggle" facade this file's own comments
    already guard against for core tools. It lives on the Capabilities tab
    instead (fleet_get_agent_capabilities)."""

    def test_generate_image_is_absent_from_the_tools_list(self):
        bundle = {"install_metadata": {"role": "specialist"}}
        with patch(
            "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
            AsyncMock(return_value=bundle),
        ):
            result = _run(
                fleet_tools.fleet_get_agent_tools(workspace_id="ws-1", tenant_id="tenant-1", agent_id="ainstall-1")
            )
        self.assertTrue(result["ok"])
        ids = {t["id"] for t in result["tools"]}
        self.assertNotIn("generate_image", ids)
        # An unrelated tool is unaffected.
        self.assertIn("web__search", ids)


class FleetGetAgentToolsCatalogShapeTests(unittest.TestCase):
    """fleet_get_agent_tools is a read-only INVENTORY now — no per-tool
    controls at all.

    It used to carry `audience_safe` (the platform's manifest default) and
    `mandate_granted` (this owner's mandate.audience_tools grant), which the
    per-agent Tools tab rendered as a three-state "who may trigger this"
    badge. All of it is deleted (see
    server_modules/authority_mandate_service.py). These assertions replace
    the two that proved the badge states, and they guard the opposite thing:
    that no per-tool authority field comes back, and that the one field with
    a live consumer survives."""

    def _tools(self, install_metadata):
        with patch(
            "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
            AsyncMock(return_value={"install_metadata": install_metadata}),
        ):
            result = _run(
                fleet_tools.fleet_get_agent_tools(
                    workspace_id="ws-1", tenant_id="tenant-1", agent_id="ainstall-1",
                )
            )
        self.assertTrue(result["ok"])
        return {t["id"]: t for t in result["tools"]}

    def test_no_per_tool_authority_fields_are_emitted(self):
        by_id = self._tools({"role": "specialist"})
        self.assertIn("shell__exec", by_id)
        for tool in by_id.values():
            for gone in ("audience_safe", "mandate_granted", "enabled"):
                self.assertNotIn(gone, tool, f"{tool['id']} still carries {gone}")

    def test_a_stale_stored_mandate_changes_nothing(self):
        """`mandate` metadata is deliberately left on existing installs
        rather than migrated away, so this endpoint WILL still meet it in
        production. It must not resurface as a field or change any output."""
        plain = self._tools({"role": "specialist"})
        stale = self._tools(
            {"role": "specialist", "mandate": {"audience_tools": ["fleet__configure_agent"]}}
        )
        self.assertEqual(plain, stale)

    def test_requires_connector_survives_because_connector_picker_reads_it(self):
        """The one field with a live consumer — ConnectorPicker uses it to
        say what connecting an app actually gives the agent. If this ever
        goes empty the endpoint has no reason to exist."""
        by_id = self._tools({"role": "specialist"})
        self.assertTrue(
            any(t.get("requires_connector") for t in by_id.values()),
            "no tool declares requires_connector — ConnectorPicker's panel would be blank",
        )


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
        no jsonb codec is registered on this connection.
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


class FleetRecurringScheduleControlTests(unittest.TestCase):
    """Owner-facing recurring-schedule surface: fleet_list_agent_recurring_
    schedules, fleet_create_agent_recurring_schedule, fleet_cancel_agent_
    recurring_schedule -- FleetScheduleControlTests' twin, above, for "every
    week" instead of a one-off wake-up.

    fleet_list_agent_recurring_schedules shipped with NO test that actually
    CALLED it end to end: every prior reference to `recurring_schedule_view`
    in this module (list_recurring_tasks, the agent-callable twin above)
    imports it locally, but this owner-facing function's own `from
    server_modules.bounded_scheduler_service import list_recurring_schedules`
    line never imported `recurring_schedule_view` at all -- so every real
    call raised `NameError: name 'recurring_schedule_view' is not defined`,
    caught only by FleetAgentDetail.tsx's new Recurring section actually
    listing a schedule it had just created in a real browser (MAN's
    recurring-schedule UI work). The route's own `except Exception` caught
    it and returned {"ok": False, "schedules": []}, which read identically
    to "no recurring schedules yet" -- exactly CLAUDE.md's "empty" vs
    "couldn't load this" trap. This test calls the real function with a
    real row shape, not a mock of the broken name, so it fails loud if the
    import regresses."""

    def _bundle(self):
        return {"id": "agent-x", "install_metadata": {}}

    def _row(self, **overrides):
        row = {
            "id": "recur-1",
            "agent_id": "agent-x",
            "cron_expression": "0 9 * * 1",
            "status": "active",
            "requested_by": "agent-x",
            "summary": "Check on dad and text me a summary.",
            "payload": {"instruction": "Check on dad and text me a summary."},
            "metadata": {},
            "next_fire_at": "2026-09-07T09:00:00+00:00",
            "last_fired_at": None,
            "occurrence_count": 0,
            "max_occurrences": None,
            "expires_at": "2026-11-30T09:00:00+00:00",
            "created_at": "2026-09-01T00:00:00+00:00",
        }
        row.update(overrides)
        return row

    def test_list_recurring_schedules_actually_returns_shaped_rows(self):
        with (
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                new=AsyncMock(return_value=self._bundle()),
            ),
            patch(
                "server_modules.bounded_scheduler_service.list_recurring_schedules",
                new=AsyncMock(return_value=[self._row()]),
            ) as list_mock,
        ):
            result = _run(fleet_tools.fleet_list_agent_recurring_schedules(workspace_id="ws-1", agent_id="agent-x"))
        # The regression this guards: result["ok"] was False (NameError
        # swallowed by the route's except Exception) with schedules=[],
        # indistinguishable from a genuinely empty list.
        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(list_mock.call_args.kwargs["agent_id"], "agent-x")
        schedules = result["schedules"]
        self.assertEqual(len(schedules), 1)
        self.assertEqual(schedules[0]["id"], "recur-1")
        self.assertEqual(schedules[0]["cron_expression"], "0 9 * * 1")
        self.assertEqual(schedules[0]["instruction"], "Check on dad and text me a summary.")
        self.assertEqual(schedules[0]["status"], "active")

    def test_list_recurring_schedules_rejects_unknown_agent(self):
        with patch(
            "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
            new=AsyncMock(return_value=None),
        ):
            result = _run(fleet_tools.fleet_list_agent_recurring_schedules(workspace_id="ws-1", agent_id="ghost"))
        self.assertFalse(result["ok"])

    def test_create_recurring_schedule_always_forces_owner_tier(self):
        with (
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                new=AsyncMock(return_value=self._bundle()),
            ),
            patch(
                "server_modules.bounded_scheduler_service.create_recurring_schedule",
                new=AsyncMock(return_value=self._row()),
            ) as create_mock,
            patch.object(fleet_tools, "_ledger_fleet_action", new=AsyncMock()),
        ):
            result = _run(fleet_tools.fleet_create_agent_recurring_schedule(
                actor_id="user-1", workspace_id="ws-1", agent_id="agent-x",
                cron="0 9 * * 1", instruction="check the inbox",
            ))
        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(create_mock.call_args.kwargs["authority_tier"], "owner")
        self.assertEqual(create_mock.call_args.kwargs["cron_expression"], "0 9 * * 1")

    def test_create_recurring_schedule_rejects_unknown_agent(self):
        with patch(
            "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
            new=AsyncMock(return_value=None),
        ):
            result = _run(fleet_tools.fleet_create_agent_recurring_schedule(
                actor_id="user-1", workspace_id="ws-1", agent_id="ghost",
                cron="0 9 * * 1", instruction="check the inbox",
            ))
        self.assertFalse(result["ok"])

    def test_cancel_recurring_schedule_transitions_status_and_ledgers(self):
        with (
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                new=AsyncMock(return_value=self._bundle()),
            ),
            patch(
                "server_modules.control_plane_repository.get_agent_recurring_schedule",
                new=AsyncMock(return_value=self._row()),
            ),
            patch(
                "server_modules.control_plane_repository.update_agent_recurring_schedule",
                new=AsyncMock(return_value=self._row(status="cancelled")),
            ) as update_mock,
            patch.object(fleet_tools, "_ledger_fleet_action", new=AsyncMock()) as ledger_mock,
        ):
            result = _run(fleet_tools.fleet_cancel_agent_recurring_schedule(
                actor_id="user-1", workspace_id="ws-1", agent_id="agent-x", schedule_id="recur-1",
            ))
        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(update_mock.call_args.kwargs["status"], "cancelled")
        ledger_mock.assert_awaited_once()

    def test_cancel_recurring_schedule_rejects_wrong_agent(self):
        with (
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                new=AsyncMock(return_value=self._bundle()),
            ),
            patch(
                "server_modules.control_plane_repository.get_agent_recurring_schedule",
                new=AsyncMock(return_value=self._row(agent_id="agent-OTHER")),
            ),
        ):
            result = _run(fleet_tools.fleet_cancel_agent_recurring_schedule(
                actor_id="user-1", workspace_id="ws-1", agent_id="agent-x", schedule_id="recur-1",
            ))
        self.assertFalse(result["ok"])

    def test_cancel_recurring_schedule_rejects_already_terminal(self):
        with (
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                new=AsyncMock(return_value=self._bundle()),
            ),
            patch(
                "server_modules.control_plane_repository.get_agent_recurring_schedule",
                new=AsyncMock(return_value=self._row(status="cancelled")),
            ),
        ):
            result = _run(fleet_tools.fleet_cancel_agent_recurring_schedule(
                actor_id="user-1", workspace_id="ws-1", agent_id="agent-x", schedule_id="recur-1",
            ))
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
    the runtime uses at turn time (agent_turn_runtime_service's
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
            "server_modules.agent_turn_runtime_service._resolve_cloud_provider",
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
            "server_modules.agent_turn_runtime_service._resolve_cloud_provider",
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
            "server_modules.agent_turn_runtime_service._resolve_cloud_provider",
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
                "server_modules.agent_turn_runtime_service._resolve_cloud_provider",
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
            "server_modules.agent_turn_runtime_service._resolve_cloud_provider",
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


class ResolveCloudAgentReadinessCacheTests(unittest.TestCase):
    """Perf fix: fleet_list_agents polls _resolve_hardware_status once PER
    AGENT, every ~30s, and most cloud-placement agents in a workspace share
    the same (mode, provider) — the platform_credits default especially.
    Without a cache, N such agents meant N identical get_workspace_by_id +
    vault + entitlements resolutions on every poll. readiness_cache collapses
    that to one call per DISTINCT (mode, provider) seen in a single
    fleet_list_agents invocation, never across requests (a fresh dict is
    created per call, so this can never serve a stale answer to a later
    poll)."""

    @staticmethod
    def _cloud_inst(agent_id, model_config):
        return {
            "id": agent_id,
            "runtime_profile": {"default_execution_target": "cloud"},
            "metadata": {"model_config": model_config},
        }

    def test_platform_credits_default_resolved_once_for_many_agents(self):
        cache: dict = {}
        resolver = AsyncMock(return_value=("deepseek", {"api_key": "sk-live"}))
        with patch(
            "server_modules.agent_turn_runtime_service._resolve_cloud_provider",
            new=resolver,
        ):
            for i in range(5):
                inst = self._cloud_inst(f"agent-{i}", {"mode": "platform_credits"})
                status, _hb, _run_id, _reason = _run(
                    fleet_tools._resolve_hardware_status(
                        inst, {}, workspace_id="ws-1", readiness_cache=cache,
                    )
                )
                self.assertEqual(status, "online")
        # The whole point: 5 agents, 1 real resolution.
        self.assertEqual(resolver.call_count, 1)

    def test_different_providers_are_not_conflated(self):
        cache: dict = {}
        with patch(
            "server_modules.direct_chat_provider_service.direct_chat_credentials",
            side_effect=lambda ws, provider: {"provider": provider},
        ), patch(
            "server_modules.direct_chat_provider_service.supports_direct_message_native_chat",
            side_effect=lambda provider, creds: provider == "openai",
        ):
            openai_inst = self._cloud_inst(
                "agent-openai", {"mode": "platform_credits", "provider": "openai"}
            )
            anthropic_inst = self._cloud_inst(
                "agent-anthropic", {"mode": "platform_credits", "provider": "anthropic"}
            )
            openai_status, *_ = _run(
                fleet_tools._resolve_hardware_status(
                    openai_inst, {}, workspace_id="ws-1", readiness_cache=cache,
                )
            )
            anthropic_status, _hb, _run_id, anthropic_reason = _run(
                fleet_tools._resolve_hardware_status(
                    anthropic_inst, {}, workspace_id="ws-1", readiness_cache=cache,
                )
            )
        # A shared cache keyed only on (mode, provider) must still tell two
        # different providers apart — never collapse a ready one and a
        # not-ready one into the same cached answer.
        self.assertEqual(openai_status, "online")
        self.assertEqual(anthropic_status, "error")
        self.assertIsNotNone(anthropic_reason)

    def test_different_gateway_bindings_are_not_conflated_by_cache(self):
        """cli_subscription/local are deliberately never cached (see the
        function's own docstring) because their result also depends on
        gateway_binding/runtime, which a (mode, provider) key can't see.
        Two agents on the same runtime but different (bound vs. unbound)
        gateways must resolve independently even when a cache dict is
        passed through."""
        cache: dict = {}
        bound_inst = self._cloud_inst(
            "agent-bound",
            {"mode": "cli_subscription", "runtime": "claude_code", "gateway_binding": "gw-1"},
        )
        unbound_inst = self._cloud_inst(
            "agent-unbound",
            {"mode": "cli_subscription", "runtime": "claude_code", "gateway_binding": ""},
        )
        bound_status, *_ = _run(
            fleet_tools._resolve_hardware_status(
                bound_inst, {}, workspace_id="ws-1", readiness_cache=cache,
            )
        )
        unbound_status, _hb, _run_id, unbound_reason = _run(
            fleet_tools._resolve_hardware_status(
                unbound_inst, {}, workspace_id="ws-1", readiness_cache=cache,
            )
        )
        self.assertEqual(bound_status, "online")
        self.assertEqual(unbound_status, "error")
        self.assertIn("no", (unbound_reason or "").lower())


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

    def test_grok_build_or_cursor_cli_ready_alone_is_recommended(self):
        # xAI Grok Build / Cursor CLI addition (2026-07-24) — provider
        # entries resolve correctly through the SAME reuse-recommendation
        # loop claude_code/codex already use.
        with (
            patch("server_modules.gateway_state_repository.get_gateway_registration", return_value=self._registration()),
            patch(
                "server_modules.gateway_registry_service.gateway_registration_public_payload",
                return_value={"llm_runtimes": {"grok_build": {"installed": True, "authenticated": True}}},
            ),
        ):
            result = fleet_tools.recommended_model_config_for_gateway("gateway-1", workspace_id="ws-1")
        self.assertEqual(result, {
            "mode": "cli_subscription",
            "provider": "xai_grok_cli",
            "runtime": "grok_build",
            "gateway_binding": "gateway-1",
        })

        with (
            patch("server_modules.gateway_state_repository.get_gateway_registration", return_value=self._registration()),
            patch(
                "server_modules.gateway_registry_service.gateway_registration_public_payload",
                return_value={"llm_runtimes": {"cursor_cli": {"installed": True, "authenticated": True}}},
            ),
        ):
            result = fleet_tools.recommended_model_config_for_gateway("gateway-1", workspace_id="ws-1")
        self.assertEqual(result, {
            "mode": "cli_subscription",
            "provider": "cursor_cli",
            "runtime": "cursor_cli",
            "gateway_binding": "gateway-1",
        })

    def test_claude_code_still_preferred_over_all_three_others_when_every_runtime_is_ready(self):
        with (
            patch("server_modules.gateway_state_repository.get_gateway_registration", return_value=self._registration()),
            patch(
                "server_modules.gateway_registry_service.gateway_registration_public_payload",
                return_value={
                    "llm_runtimes": {
                        "claude_code": {"installed": True, "authenticated": True},
                        "codex": {"installed": True, "authenticated": True},
                        "grok_build": {"installed": True, "authenticated": True},
                        "cursor_cli": {"installed": True, "authenticated": True},
                    },
                },
            ),
        ):
            result = fleet_tools.recommended_model_config_for_gateway("gateway-1", workspace_id="ws-1")
        self.assertEqual(result["runtime"], "claude_code")

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
            patch(
                "server_modules.agent_registry_repository.list_workspace_agent_installs",
                new=AsyncMock(return_value=[{"id": "agent-x", "label": "Old Name"}]),
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


class FleetConfigureAgentCliSubscriptionSaveTimeHonestyTests(unittest.TestCase):
    """fleet_configure_agent's save-time honesty check (§ "the CLI itself
    must be installed AND authenticated on that Gateway"): rejects saving a
    cli_subscription binding to an uninstalled/unauthenticated CLI at SAVE
    time, not turn time. xAI Grok Build / Cursor CLI addition (2026-07-24):
    this gate used to only cover {"claude_code", "codex"} — a grok_build/
    cursor_cli binding would have silently SKIPPED this check entirely and
    saved cleanly even when the CLI was never installed, only failing much
    later at the first real turn. This class is the regression guard for
    that specific gap."""

    @staticmethod
    def _bundle(agent_id="agent-x", metadata=None):
        return {"id": agent_id, "install_metadata": dict(metadata or {})}

    @staticmethod
    def _registration(**overrides):
        base = {"gateway_id": "gateway-1", "workspace_id": "ws-1", "status": "active", "device_trust_state": "trusted"}
        base.update(overrides)
        return base

    def _configure(self, model_config, *, llm_runtimes):
        with (
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                new=AsyncMock(return_value=self._bundle()),
            ),
            patch(
                "server_modules.agent_registry_repository.update_workspace_agent_install",
                new=AsyncMock(return_value=self._bundle()),
            ),
            patch("server_modules.gateway_state_repository.get_gateway_registration", return_value=self._registration()),
            patch(
                "server_modules.gateway_registry_service.gateway_registration_public_payload",
                return_value={"llm_runtimes": llm_runtimes, "display_name": "Test Box"},
            ),
        ):
            return _run(
                fleet_tools.fleet_configure_agent(
                    actor_id="owner-1",
                    workspace_id="ws-1",
                    agent_id="agent-x",
                    patch={"model_config": {**model_config, "gateway_binding": "gateway-1"}},
                )
            )

    def test_grok_build_not_installed_is_rejected_at_save_time_not_silently_accepted(self):
        result = self._configure(
            {"mode": "cli_subscription", "runtime": "grok_build"},
            llm_runtimes={"grok_build": {"installed": False, "authenticated": False}},
        )
        self.assertFalse(result["ok"])
        self.assertIn("Grok Build", result["error"])
        self.assertIn("isn't installed", result["error"])

    def test_grok_build_installed_but_not_signed_in_is_rejected_at_save_time(self):
        result = self._configure(
            {"mode": "cli_subscription", "runtime": "grok_build"},
            llm_runtimes={"grok_build": {"installed": True, "authenticated": False}},
        )
        self.assertFalse(result["ok"])
        self.assertIn("Grok Build", result["error"])
        self.assertIn("isn't signed in", result["error"])

    def test_cursor_cli_not_installed_is_rejected_at_save_time(self):
        result = self._configure(
            {"mode": "cli_subscription", "runtime": "cursor_cli"},
            llm_runtimes={"cursor_cli": {"installed": False, "authenticated": False}},
        )
        self.assertFalse(result["ok"])
        self.assertIn("Cursor CLI", result["error"])
        self.assertIn("isn't installed", result["error"])

    def test_grok_build_ready_saves_cleanly(self):
        result = self._configure(
            {"mode": "cli_subscription", "runtime": "grok_build"},
            llm_runtimes={"grok_build": {"installed": True, "authenticated": True}},
        )
        self.assertTrue(result["ok"], result.get("error"))

    def test_cursor_cli_ready_saves_cleanly(self):
        result = self._configure(
            {"mode": "cli_subscription", "runtime": "cursor_cli"},
            llm_runtimes={"cursor_cli": {"installed": True, "authenticated": True}},
        )
        self.assertTrue(result["ok"], result.get("error"))


class FleetConfigureAgentCodexModelLiveValidationTests(unittest.TestCase):
    """URGENT fix (2026-08-14): a cli_subscription + codex model_config.model
    is now validated at save time against the Gateway's own live model
    catalog (codex_model_catalog_service.fetch_codex_model_catalog), not
    accepted sight-unseen — the exact gap that let a since-retired
    "gpt-5.4" sit in a saved agent config and only fail mid-turn with a raw
    provider error."""

    @staticmethod
    def _bundle(agent_id="agent-x", metadata=None):
        return {"id": agent_id, "install_metadata": dict(metadata or {})}

    @staticmethod
    def _registration(**overrides):
        base = {"gateway_id": "gateway-1", "workspace_id": "ws-1", "status": "active", "device_trust_state": "trusted"}
        base.update(overrides)
        return base

    def _configure(self, model_config, *, catalog_result=None, catalog_side_effect=None):
        catalog_mock = AsyncMock(return_value=catalog_result, side_effect=catalog_side_effect)
        with (
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                new=AsyncMock(return_value=self._bundle()),
            ),
            patch(
                "server_modules.agent_registry_repository.update_workspace_agent_install",
                new=AsyncMock(return_value=self._bundle()),
            ),
            patch("server_modules.gateway_state_repository.get_gateway_registration", return_value=self._registration()),
            patch(
                "server_modules.gateway_registry_service.gateway_registration_public_payload",
                return_value={
                    "llm_runtimes": {"codex": {"installed": True, "authenticated": True}},
                    "display_name": "Test Box",
                },
            ),
            patch("server_modules.codex_model_catalog_service.fetch_codex_model_catalog", new=catalog_mock),
        ):
            result = _run(
                fleet_tools.fleet_configure_agent(
                    actor_id="owner-1",
                    workspace_id="ws-1",
                    agent_id="agent-x",
                    patch={"model_config": {**model_config, "runtime": "codex", "gateway_binding": "gateway-1"}},
                )
            )
        return result, catalog_mock

    def test_a_model_the_live_catalog_no_longer_recognizes_is_rejected(self):
        result, catalog_mock = self._configure(
            {"mode": "cli_subscription", "model": "gpt-5.4"},
            catalog_result={
                "supported": True,
                "auth_method": "chatgpt",
                "models": [
                    {"id": "gpt-5.6-terra", "hidden": False, "is_default": True},
                    {"id": "gpt-5.6-luna", "hidden": False, "is_default": False},
                ],
            },
        )
        self.assertFalse(result["ok"])
        self.assertIn("gpt-5.4", result["error"])
        self.assertIn("gpt-5.6-terra", result["error"])
        catalog_mock.assert_awaited_once()

    def test_a_model_the_live_catalog_recognizes_saves_cleanly(self):
        result, _ = self._configure(
            {"mode": "cli_subscription", "model": "gpt-5.6-terra"},
            catalog_result={
                "supported": True,
                "auth_method": "chatgpt",
                "models": [{"id": "gpt-5.6-terra", "hidden": False, "is_default": True}],
            },
        )
        self.assertTrue(result["ok"], result.get("error"))

    def test_a_hidden_but_real_model_is_accepted_not_just_the_default_pickers_visible_set(self):
        result, _ = self._configure(
            {"mode": "cli_subscription", "model": "codex-auto-review"},
            catalog_result={
                "supported": True,
                "auth_method": "chatgpt",
                "models": [{"id": "codex-auto-review", "hidden": True, "is_default": False}],
            },
        )
        self.assertTrue(result["ok"], result.get("error"))

    def test_an_unreachable_live_check_fails_open_never_blocks_an_otherwise_valid_save(self):
        # Advisory, not authoritative — a transient Gateway hiccup must not
        # make every save depend on a live round trip succeeding.
        result, catalog_mock = self._configure(
            {"mode": "cli_subscription", "model": "gpt-5.4"},
            catalog_side_effect=codex_model_catalog_service.CodexModelCatalogError("gateway offline", status_code=409),
        )
        self.assertTrue(result["ok"], result.get("error"))
        catalog_mock.assert_awaited_once()

    def test_an_empty_unset_model_is_never_validated_the_cli_own_default_applies(self):
        catalog_mock = AsyncMock()
        with (
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                new=AsyncMock(return_value=self._bundle()),
            ),
            patch(
                "server_modules.agent_registry_repository.update_workspace_agent_install",
                new=AsyncMock(return_value=self._bundle()),
            ),
            patch("server_modules.gateway_state_repository.get_gateway_registration", return_value=self._registration()),
            patch(
                "server_modules.gateway_registry_service.gateway_registration_public_payload",
                return_value={
                    "llm_runtimes": {"codex": {"installed": True, "authenticated": True}},
                    "display_name": "Test Box",
                },
            ),
            patch("server_modules.codex_model_catalog_service.fetch_codex_model_catalog", new=catalog_mock),
        ):
            result = _run(
                fleet_tools.fleet_configure_agent(
                    actor_id="owner-1",
                    workspace_id="ws-1",
                    agent_id="agent-x",
                    patch={"model_config": {"mode": "cli_subscription", "runtime": "codex", "gateway_binding": "gateway-1"}},
                )
            )
        self.assertTrue(result["ok"], result.get("error"))
        catalog_mock.assert_not_awaited()


class FleetConfigureAgentRenameCollisionTests(unittest.TestCase):
    """STEP 5 (agent-identity plan): fleet_create_agent's auto-naming path
    was already collision-checked against every existing label in the
    workspace, but the manual rename path (this display_name PATCH) did zero
    checking and wrote straight to the label column. Two agents could end up
    sharing a name with nothing to catch it -- ambiguous for a human AND for
    the closed-roster mention autocomplete. These tests hold the rename path
    to the same bar as auto-naming."""

    @staticmethod
    def _bundle(agent_id="agent-x", metadata=None):
        return {"id": agent_id, "install_metadata": dict(metadata or {})}

    def test_renaming_to_an_existing_label_is_rejected(self):
        exploding_update = AsyncMock(side_effect=AssertionError("must not persist a colliding rename"))
        with (
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                new=AsyncMock(return_value=self._bundle()),
            ),
            patch(
                "server_modules.agent_registry_repository.update_workspace_agent_install",
                new=exploding_update,
            ),
            patch(
                "server_modules.agent_registry_repository.list_workspace_agent_installs",
                new=AsyncMock(return_value=[
                    {"id": "agent-x", "label": "Atlas"},
                    {"id": "agent-y", "label": "Nova"},
                ]),
            ),
        ):
            result = _run(
                fleet_tools.fleet_configure_agent(
                    actor_id="owner-1",
                    workspace_id="ws-1",
                    agent_id="agent-x",
                    patch={"display_name": "Nova"},
                )
            )
        self.assertFalse(result["ok"])
        self.assertIn("already the name of another agent", result["error"])
        exploding_update.assert_not_called()

    def test_collision_check_is_case_insensitive(self):
        exploding_update = AsyncMock(side_effect=AssertionError("must not persist a colliding rename"))
        with (
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                new=AsyncMock(return_value=self._bundle()),
            ),
            patch(
                "server_modules.agent_registry_repository.update_workspace_agent_install",
                new=exploding_update,
            ),
            patch(
                "server_modules.agent_registry_repository.list_workspace_agent_installs",
                new=AsyncMock(return_value=[
                    {"id": "agent-x", "label": "Atlas"},
                    {"id": "agent-y", "label": "Nova"},
                ]),
            ),
        ):
            result = _run(
                fleet_tools.fleet_configure_agent(
                    actor_id="owner-1",
                    workspace_id="ws-1",
                    agent_id="agent-x",
                    patch={"display_name": "nOVA"},
                )
            )
        self.assertFalse(result["ok"])
        exploding_update.assert_not_called()

    def test_renaming_to_its_own_current_name_is_not_a_collision(self):
        update_mock = AsyncMock(return_value=self._bundle())
        with (
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                new=AsyncMock(return_value=self._bundle()),
            ),
            patch(
                "server_modules.agent_registry_repository.update_workspace_agent_install",
                new=update_mock,
            ),
            patch(
                "server_modules.agent_registry_repository.list_workspace_agent_installs",
                new=AsyncMock(return_value=[
                    {"id": "agent-x", "label": "Atlas"},
                    {"id": "agent-y", "label": "Nova"},
                ]),
            ),
        ):
            result = _run(
                fleet_tools.fleet_configure_agent(
                    actor_id="owner-1",
                    workspace_id="ws-1",
                    agent_id="agent-x",
                    patch={"display_name": "Atlas"},
                )
            )
        self.assertTrue(result["ok"])
        update_mock.assert_called_once()

    def test_renaming_to_a_free_name_succeeds(self):
        update_mock = AsyncMock(return_value=self._bundle())
        with (
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                new=AsyncMock(return_value=self._bundle()),
            ),
            patch(
                "server_modules.agent_registry_repository.update_workspace_agent_install",
                new=update_mock,
            ),
            patch(
                "server_modules.agent_registry_repository.list_workspace_agent_installs",
                new=AsyncMock(return_value=[
                    {"id": "agent-x", "label": "Atlas"},
                    {"id": "agent-y", "label": "Nova"},
                ]),
            ),
        ):
            result = _run(
                fleet_tools.fleet_configure_agent(
                    actor_id="owner-1",
                    workspace_id="ws-1",
                    agent_id="agent-x",
                    patch={"display_name": "Ember"},
                )
            )
        self.assertTrue(result["ok"])
        update_mock.assert_called_once_with(
            "agent-x",
            tenant_id="system",
            workspace_id="ws-1",
            label="Ember",
            metadata={},
            tool_toggles=None,
            hardware_access=None,
        )


class SuggestAgentNameTests(unittest.TestCase):
    """suggest_agent_name is the function the create-agent wizard's new
    Placement Name field calls (via routes_fleet's suggested-name GET) to
    pre-fill a real, non-blank name before the agent exists — and the exact
    function fleet_create_agent itself now falls back to when no explicit
    name is given, so both paths agree by construction rather than by two
    copies of the pool logic staying in sync."""

    def test_suggests_a_pool_name_not_already_taken(self):
        with patch(
            "server_modules.agent_registry_repository.list_workspace_agent_installs",
            new=AsyncMock(return_value=[{"id": "agent-x", "label": "Atlas"}]),
        ):
            name = _run(
                fleet_tools.suggest_agent_name(tenant_id="system", workspace_id="ws-1")
            )
        self.assertNotEqual(name, "Atlas")
        self.assertTrue(name)

    def test_create_agent_with_no_explicit_name_uses_the_suggested_pool_name(self):
        # fleet_create_agent's own auto-naming fallback must go through the
        # SAME suggest_agent_name — asserted here by patching that one
        # function directly (call-count, not just a non-crash) rather than
        # its lower-level repo call, so this breaks if a future edit
        # reintroduces a second, divergent naming path inside
        # fleet_create_agent. ensure_workspace_agent_registry_seeded is
        # forced to fail right after naming — fleet_create_agent's own
        # outer except turns that into an {"ok": False} result rather than
        # propagating, so the failure is asserted on the RETURN value, not
        # a raised exception.
        suggest_mock = AsyncMock(return_value="Suggested-Name")
        with (
            patch("server_modules.fleet_tools.suggest_agent_name", new=suggest_mock),
            patch(
                "server_modules.agent_registry_repository.ensure_workspace_agent_registry_seeded",
                new=AsyncMock(side_effect=RuntimeError("stop after naming — rest of create is out of scope here")),
            ),
        ):
            result = _run(
                fleet_tools.fleet_create_agent(
                    actor_id="owner-1",
                    workspace_id="ws-1",
                    tenant_id="system",
                    name="",
                )
            )
        self.assertFalse(result["ok"])
        self.assertIn("stop after naming", result["error"])
        suggest_mock.assert_called_once_with(tenant_id="system", workspace_id="ws-1")

    def test_create_agent_with_an_explicit_name_never_calls_suggest_agent_name(self):
        exploding_suggest = AsyncMock(side_effect=AssertionError("must not suggest a name when one was given"))
        with (
            patch("server_modules.fleet_tools.suggest_agent_name", new=exploding_suggest),
            patch(
                "server_modules.agent_registry_repository.ensure_workspace_agent_registry_seeded",
                new=AsyncMock(side_effect=RuntimeError("stop after naming — rest of create is out of scope here")),
            ),
        ):
            result = _run(
                fleet_tools.fleet_create_agent(
                    actor_id="owner-1",
                    workspace_id="ws-1",
                    tenant_id="system",
                    name="Explicit Name",
                )
            )
        self.assertFalse(result["ok"])
        exploding_suggest.assert_not_called()


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
                "server_modules.agent_turn_runtime_service._resolve_cloud_provider",
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


# ── Capabilities (image/video generation, TTS/STT) ─────────────────────────
# See server_modules/agent_capability_service.py for the resolver these
# endpoints surface, and setUpModule below for why _vault_passphrase is
# patched (real encryption, no dependency on the compiled Rust kernel binary
# — mirrors test_agent_capability_service.py's identical setup).

from server_modules import vault_store as _vault_store_mod  # noqa: E402

_TEST_VAULT_PASSPHRASE = "test-fixed-passphrase-for-fleet-tools-capability-tests"
_capability_vault_passphrase_patcher = None


def setUpModule():
    global _capability_vault_passphrase_patcher
    _capability_vault_passphrase_patcher = patch.object(
        _vault_store_mod, "_vault_passphrase", return_value=_TEST_VAULT_PASSPHRASE,
    )
    _capability_vault_passphrase_patcher.start()


def tearDownModule():
    if _capability_vault_passphrase_patcher is not None:
        _capability_vault_passphrase_patcher.stop()


class FleetConfigureAgentCapabilityConfigTests(unittest.TestCase):
    """fleet_configure_agent's capability_config patch handling — must MERGE
    per-capability (unlike model_config's wholesale replace), validate
    through agent_capability_service, and never accept secret material."""

    @staticmethod
    def _bundle(metadata=None):
        return {"id": "agent-x", "install_metadata": dict(metadata or {})}

    def test_saving_one_capability_does_not_clobber_an_already_configured_other(self):
        existing_meta = {"capability_config": {"speech_to_text": {"mode": "byok_api", "provider": "openai"}}}
        captured = {}

        async def _capture_update(agent_id, **kwargs):
            captured.update(kwargs)
            return self._bundle(existing_meta)

        with (
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                new=AsyncMock(return_value=self._bundle(existing_meta)),
            ),
            patch(
                "server_modules.agent_registry_repository.update_workspace_agent_install",
                new=AsyncMock(side_effect=_capture_update),
            ),
        ):
            result = _run(
                fleet_tools.fleet_configure_agent(
                    actor_id="owner-1", workspace_id="ws-1", agent_id="agent-x",
                    patch={"capability_config": {"image_generation": {"mode": "platform_credits", "provider": "openai"}}},
                )
            )
        self.assertTrue(result["ok"])
        saved_capability_config = captured["metadata"]["capability_config"]
        self.assertEqual(saved_capability_config["image_generation"], {"mode": "platform_credits", "provider": "openai"})
        # The pre-existing speech_to_text entry must survive untouched.
        self.assertEqual(saved_capability_config["speech_to_text"], {"mode": "byok_api", "provider": "openai"})

    def test_rejects_unknown_capability(self):
        with (
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                new=AsyncMock(return_value=self._bundle()),
            ),
            patch(
                "server_modules.agent_registry_repository.update_workspace_agent_install",
                new=AsyncMock(return_value=self._bundle()),
            ),
        ):
            result = _run(
                fleet_tools.fleet_configure_agent(
                    actor_id="owner-1", workspace_id="ws-1", agent_id="agent-x",
                    patch={"capability_config": {"not_a_capability": {"mode": "byok_api"}}},
                )
            )
        self.assertFalse(result["ok"])
        self.assertIn("error", result)

    def test_rejects_unsupported_mode(self):
        with (
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                new=AsyncMock(return_value=self._bundle()),
            ),
            patch(
                "server_modules.agent_registry_repository.update_workspace_agent_install",
                new=AsyncMock(return_value=self._bundle()),
            ),
        ):
            result = _run(
                fleet_tools.fleet_configure_agent(
                    actor_id="owner-1", workspace_id="ws-1", agent_id="agent-x",
                    # cli_subscription/local don't apply to flat-API-key media providers.
                    patch={"capability_config": {"image_generation": {"mode": "cli_subscription"}}},
                )
            )
        self.assertFalse(result["ok"])


class FleetGetAgentCapabilitiesTests(unittest.TestCase):
    def test_returns_resolved_state_for_all_four_capabilities(self):
        bundle = {
            "id": "agent-x",
            "install_metadata": {"role": "specialist"},
        }
        with patch(
            "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
            new=AsyncMock(return_value=bundle),
        ):
            result = _run(
                fleet_tools.fleet_get_agent_capabilities(workspace_id="ws-1", tenant_id="t1", agent_id="agent-x")
            )
        self.assertTrue(result["ok"])
        ids = {c["id"] for c in result["capabilities"]}
        self.assertEqual(ids, {"image_generation", "video_generation", "text_to_speech", "speech_to_text"})
        self.assertFalse(result["is_master"])

    def test_master_agent_is_flagged(self):
        bundle = {"id": "sage-install", "install_metadata": {"role": "operator"}}
        with patch(
            "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
            new=AsyncMock(return_value=bundle),
        ):
            result = _run(
                fleet_tools.fleet_get_agent_capabilities(workspace_id="ws-1", tenant_id="t1", agent_id="sage-install")
            )
        self.assertTrue(result["is_master"])

    def test_unknown_agent_returns_empty_list_not_an_error(self):
        with patch(
            "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
            new=AsyncMock(return_value=None),
        ):
            result = _run(
                fleet_tools.fleet_get_agent_capabilities(workspace_id="ws-1", tenant_id="t1", agent_id="ghost")
            )
        self.assertTrue(result["ok"])
        self.assertEqual(result["capabilities"], [])


class FleetSetAgentCapabilityKeyTests(unittest.TestCase):
    """Saving a BYOK key: encrypts, scopes to this agent's OWN metadata row,
    and flips mode to byok_api as a side effect (pasting a key IS choosing
    "your own API key" — no separate mode toggle to also flip)."""

    @staticmethod
    def _bundle(metadata=None):
        return {"id": "agent-x", "install_metadata": dict(metadata or {})}

    def test_saves_encrypted_key_and_switches_to_byok_api(self):
        captured = {}

        async def _capture_update(agent_id, **kwargs):
            captured.update(kwargs)
            return self._bundle(kwargs.get("metadata"))

        with (
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                new=AsyncMock(return_value=self._bundle()),
            ),
            patch(
                "server_modules.agent_registry_repository.update_workspace_agent_install",
                new=AsyncMock(side_effect=_capture_update),
            ),
            patch.object(fleet_tools, "_ledger_fleet_action", new=AsyncMock()),
        ):
            result = _run(
                fleet_tools.fleet_set_agent_capability_key(
                    workspace_id="ws-1", tenant_id="t1", agent_id="agent-x",
                    capability="image_generation", provider="openai", api_key="sk-owner-pasted-key",
                )
            )
        self.assertTrue(result["ok"])
        self.assertEqual(result["mode"], "byok_api")
        saved_meta = captured["metadata"]
        self.assertEqual(saved_meta["capability_config"]["image_generation"], {"mode": "byok_api", "provider": "openai"})
        # The raw key must never appear anywhere in what gets persisted.
        self.assertNotIn("sk-owner-pasted-key", str(saved_meta))
        self.assertIn("ciphertext", saved_meta["capability_secrets"]["image_generation"])

    def test_rejects_provider_not_valid_for_capability(self):
        with (
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                new=AsyncMock(return_value=self._bundle()),
            ),
        ):
            result = _run(
                fleet_tools.fleet_set_agent_capability_key(
                    workspace_id="ws-1", tenant_id="t1", agent_id="agent-x",
                    capability="image_generation", provider="elevenlabs", api_key="sk-x",
                )
            )
        self.assertFalse(result["ok"])

    def test_unknown_agent_is_a_clean_error_not_a_crash(self):
        with patch(
            "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
            new=AsyncMock(return_value=None),
        ):
            result = _run(
                fleet_tools.fleet_set_agent_capability_key(
                    workspace_id="ws-1", tenant_id="t1", agent_id="ghost",
                    capability="image_generation", provider="openai", api_key="sk-x",
                )
            )
        self.assertFalse(result["ok"])


class FleetClearAgentCapabilityKeyTests(unittest.TestCase):
    def test_removes_secret_and_resets_to_platform_credits(self):
        existing_meta = {
            "capability_config": {"image_generation": {"mode": "byok_api", "provider": "openai"}},
            "capability_secrets": {"image_generation": {"provider": "openai", "ciphertext": "orion.v2:...", "updated_at": "t"}},
        }
        captured = {}

        async def _capture_update(agent_id, **kwargs):
            captured.update(kwargs)
            return {"id": "agent-x", "install_metadata": kwargs.get("metadata")}

        with (
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                new=AsyncMock(return_value={"id": "agent-x", "install_metadata": existing_meta}),
            ),
            patch(
                "server_modules.agent_registry_repository.update_workspace_agent_install",
                new=AsyncMock(side_effect=_capture_update),
            ),
            patch.object(fleet_tools, "_ledger_fleet_action", new=AsyncMock()),
        ):
            result = _run(
                fleet_tools.fleet_clear_agent_capability_key(
                    workspace_id="ws-1", tenant_id="t1", agent_id="agent-x", capability="image_generation",
                )
            )
        self.assertTrue(result["ok"])
        self.assertEqual(result["mode"], "platform_credits")
        saved_meta = captured["metadata"]
        self.assertNotIn("image_generation", saved_meta["capability_secrets"])
        self.assertEqual(saved_meta["capability_config"]["image_generation"]["mode"], "platform_credits")

    def test_unknown_capability_is_a_clean_error(self):
        with patch(
            "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
            new=AsyncMock(return_value={"id": "agent-x", "install_metadata": {}}),
        ):
            result = _run(
                fleet_tools.fleet_clear_agent_capability_key(
                    workspace_id="ws-1", tenant_id="t1", agent_id="agent-x", capability="not_real",
                )
            )
        self.assertFalse(result["ok"])


class FleetConfigureAgentReasoningEffortValidationTests(unittest.TestCase):
    """model_config.reasoning_effort has TWO DIFFERENT valid vocabularies —
    _VALID_REASONING_EFFORTS for platform_credits/byok_api (the provider-API
    param stream_provider_backed_direct_chat applies) and a THIRD, per-
    runtime vocabulary for cli_subscription (claude_code's real `--effort`
    values vs codex's real `-c model_reasoning_effort=` values — verified
    live against each CLI's own --help, see _VALID_CLI_REASONING_EFFORTS_
    BY_RUNTIME's docstring). Rejected at save time (here) rather than
    silently dropped at turn time, so a caller (the Fleet UI, or /thinking)
    never believes a value is in effect that the turn-time consumer would
    actually discard."""

    @staticmethod
    def _bundle(agent_id="agent-x", metadata=None):
        return {"id": agent_id, "install_metadata": dict(metadata or {})}

    def _configure(self, model_config):
        with (
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                new=AsyncMock(return_value=self._bundle()),
            ),
            patch(
                "server_modules.agent_registry_repository.update_workspace_agent_install",
                new=AsyncMock(return_value=self._bundle()),
            ),
        ):
            return _run(
                fleet_tools.fleet_configure_agent(
                    actor_id="owner-1",
                    workspace_id="ws-1",
                    agent_id="agent-x",
                    patch={"model_config": model_config},
                )
            )

    def test_platform_credits_accepts_a_value_from_its_own_set(self):
        result = self._configure({"mode": "platform_credits", "reasoning_effort": "xhigh"})
        self.assertTrue(result["ok"], result.get("error"))

    def test_platform_credits_rejects_a_cli_only_value(self):
        """"off" is real for codex, not for the provider-API param path."""
        result = self._configure({"mode": "platform_credits", "reasoning_effort": "off"})
        self.assertFalse(result["ok"])
        self.assertIn("reasoning_effort", result["error"])

    def test_byok_api_accepts_a_value_from_its_own_set(self):
        result = self._configure({"mode": "byok_api", "provider": "anthropic", "reasoning_effort": "high"})
        self.assertTrue(result["ok"], result.get("error"))

    def test_cli_subscription_claude_code_accepts_max(self):
        """"max" is NOT in _VALID_REASONING_EFFORTS (platform_credits/byok_api)
        but IS real for the Claude CLI's --effort — proves the two
        vocabularies are genuinely distinct, not one shared set."""
        result = self._configure({
            "mode": "cli_subscription", "runtime": "claude_code", "reasoning_effort": "max",
        })
        self.assertTrue(result["ok"], result.get("error"))

    def test_cli_subscription_claude_code_rejects_off(self):
        """The Claude CLI's --effort has no "off"/"minimal" value."""
        result = self._configure({
            "mode": "cli_subscription", "runtime": "claude_code", "reasoning_effort": "off",
        })
        self.assertFalse(result["ok"])
        self.assertIn("claude_code", result["error"])

    def test_cli_subscription_codex_accepts_off(self):
        """codex's ReasoningEffort enum DOES have "off" — the runtime-gated
        vocabularies are genuinely different, not just claude_code being a
        subset of codex or vice versa."""
        result = self._configure({
            "mode": "cli_subscription", "runtime": "codex", "reasoning_effort": "off",
        })
        self.assertTrue(result["ok"], result.get("error"))

    def test_cli_subscription_defaults_runtime_to_claude_code_when_unset(self):
        result = self._configure({"mode": "cli_subscription", "reasoning_effort": "off"})
        self.assertFalse(result["ok"])
        self.assertIn("claude_code", result["error"])

    def test_cli_subscription_grok_build_accepts_none(self):
        """"none" is Grok Build's own canonical vocabulary entry (distinct
        from codex's "off") — verified live against docs.x.ai/build."""
        result = self._configure({"mode": "cli_subscription", "runtime": "grok_build", "reasoning_effort": "none"})
        self.assertTrue(result["ok"], result.get("error"))

    def test_cli_subscription_grok_build_rejects_off(self):
        """"off" is codex's vocabulary, not grok_build's."""
        result = self._configure({"mode": "cli_subscription", "runtime": "grok_build", "reasoning_effort": "off"})
        self.assertFalse(result["ok"])
        self.assertIn("reasoning_effort", result["error"])

    def test_cli_subscription_cursor_cli_accepts_the_shared_ladder(self):
        """INVERTED 2026-08-20, not deleted. This used to assert that
        cursor_cli REJECTED every reasoning_effort, because Cursor's CLI
        publishes no reasoning flag. The founder overrode that: one shared
        ladder, offered for every runtime ("If it works, it works otherwise
        you can still choose it"). Cursor still drops the value on the wire
        — that is reported to the customer in the picker's own hint — but a
        save must not fail, or the offered control could never be used."""
        result = self._configure({"mode": "cli_subscription", "runtime": "cursor_cli", "reasoning_effort": "low"})
        self.assertTrue(result["ok"], result.get("error"))

    def test_every_cli_runtime_accepts_every_rung_of_the_shared_ladder(self):
        """The founder's rule as one assertion: the ladder is never narrowed
        per provider. If this fails, some runtime is offering a level in the
        UI that its own save path rejects — a dead control with a 400."""
        for runtime in ("claude_code", "codex", "grok_build", "cursor_cli"):
            for effort in ("low", "medium", "high", "xhigh", "max", "ultra"):
                result = self._configure({
                    "mode": "cli_subscription", "runtime": runtime, "reasoning_effort": effort,
                })
                self.assertTrue(result["ok"], f"{runtime}/{effort}: {result.get('error')}")

    def test_a_legacy_runtime_only_value_still_validates(self):
        """A codex agent saved on "minimal" before the ladder was unified
        must keep validating — the native rungs stay accepted even though no
        picker offers them any more."""
        self.assertTrue(self._configure({
            "mode": "cli_subscription", "runtime": "codex", "reasoning_effort": "minimal",
        })["ok"])

    def test_local_mode_rejects_any_reasoning_effort(self):
        """Ollama has no CLI reasoning-effort control today — same boundary
        the Fleet UI's own picker already draws."""
        result = self._configure({"mode": "local", "reasoning_effort": "low"})
        self.assertFalse(result["ok"])

    def test_empty_reasoning_effort_skips_validation_entirely(self):
        """Omitting the field (the overwhelming common case) must never be
        rejected — only a genuinely SET, invalid value is."""
        result = self._configure({"mode": "platform_credits"})
        self.assertTrue(result["ok"], result.get("error"))


class FleetConfigureAgentEngineValidationTests(unittest.TestCase):
    """MAN-310 Phase 1: model_config.engine ("legacy" | "claude_agent_sdk")
    is only meaningful for platform_credits/byok_api — cli_subscription/
    local dispatch entirely through the Gateway "brain" branches and never
    reach the turn-engine seam at all (see fleet_tools.py's
    _ENGINE_SUPPORTED_MODES). Rejected at save time, same convention as
    reasoning_effort just above, rather than silently accepted and later
    discovered to be a dead control."""

    @staticmethod
    def _bundle(agent_id="agent-x", metadata=None):
        return {"id": agent_id, "install_metadata": dict(metadata or {})}

    def _configure(self, model_config):
        with (
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                new=AsyncMock(return_value=self._bundle()),
            ),
            patch(
                "server_modules.agent_registry_repository.update_workspace_agent_install",
                new=AsyncMock(return_value=self._bundle()),
            ),
        ):
            return _run(
                fleet_tools.fleet_configure_agent(
                    actor_id="owner-1",
                    workspace_id="ws-1",
                    agent_id="agent-x",
                    patch={"model_config": model_config},
                )
            )

    def test_platform_credits_accepts_the_sdk_engine(self):
        result = self._configure({"mode": "platform_credits", "engine": "claude_agent_sdk"})
        self.assertTrue(result["ok"], result.get("error"))

    def test_byok_api_accepts_the_sdk_engine(self):
        result = self._configure({"mode": "byok_api", "provider": "anthropic", "engine": "claude_agent_sdk"})
        self.assertTrue(result["ok"], result.get("error"))

    def test_unset_mode_defaults_to_platform_credits_for_the_engine_gate(self):
        """No explicit mode + engine=claude_agent_sdk must be accepted --
        an unset mode means platform_credits everywhere else in this
        module (see resolve_model_config), so the engine gate must agree."""
        result = self._configure({"engine": "claude_agent_sdk"})
        self.assertTrue(result["ok"], result.get("error"))

    def test_cli_subscription_rejects_the_sdk_engine(self):
        result = self._configure({"mode": "cli_subscription", "runtime": "claude_code", "engine": "claude_agent_sdk"})
        self.assertFalse(result["ok"])
        self.assertIn("cli_subscription", result["error"])

    def test_local_rejects_the_sdk_engine(self):
        result = self._configure({"mode": "local", "engine": "claude_agent_sdk"})
        self.assertFalse(result["ok"])
        self.assertIn("local", result["error"])

    def test_unrecognized_engine_value_is_rejected(self):
        result = self._configure({"mode": "platform_credits", "engine": "some-future-engine"})
        self.assertFalse(result["ok"])
        self.assertIn("engine", result["error"])

    def test_explicit_legacy_engine_is_accepted_for_every_mode(self):
        """"legacy" is always valid regardless of mode -- it never selects
        the bridge, so there is no dead-control concern for it."""
        for mode in ("platform_credits", "byok_api", "cli_subscription", "local"):
            with self.subTest(mode=mode):
                result = self._configure({"mode": mode, "engine": "legacy"})
                self.assertTrue(result["ok"], result.get("error"))

    def test_empty_engine_skips_validation_entirely(self):
        """Omitting the field (the overwhelming common case, and the
        default-to-legacy safety property) must never be rejected."""
        result = self._configure({"mode": "cli_subscription", "runtime": "claude_code"})
        self.assertTrue(result["ok"], result.get("error"))


if __name__ == "__main__":
    unittest.main()


class CreateAgentProjectIsolationTests(unittest.TestCase):
    """The project is the collaboration boundary — agents inside one project
    share a task board and can see each other's work. So an agent created
    without an explicit project must get its OWN project, never the shared
    workspace-wide default. Dropping every unplaced agent into "General"
    would put an agent built for one person in the same room as an agent
    built for someone else, and the task board would leak across them.

    These tests hold that boundary structurally, at creation time, rather
    than trusting a later filter to hide the overlap."""

    @staticmethod
    def _repo_patches(create_install=None):
        return {
            "ensure_workspace_agent_registry_seeded": AsyncMock(return_value=None),
            "list_agent_definitions": AsyncMock(
                return_value=[{"slug": "fleet-specialist", "id": "def-1"}]
            ),
            "list_workspace_agent_installs": AsyncMock(return_value=[]),
            "create_workspace_agent_install": create_install
            or AsyncMock(return_value={"id": "agent-new"}),
        }

    def _create(self, *, name, project_id="", create_project_mock=None, default_mock=None):
        create_project_mock = create_project_mock or AsyncMock(
            return_value={"id": "proj-own"}
        )
        default_mock = default_mock or AsyncMock(
            side_effect=AssertionError(
                "must not fall back to the shared default project"
            )
        )
        assign_mock = AsyncMock(return_value=None)
        repo_mocks = self._repo_patches()
        with (
            patch.multiple(
                "server_modules.agent_registry_repository", **repo_mocks
            ),
            patch(
                "server_modules.projects_repository.create_project",
                create_project_mock,
            ),
            patch(
                "server_modules.projects_repository.ensure_default_project",
                default_mock,
            ),
            patch(
                "server_modules.projects_repository.assign_install_to_project",
                assign_mock,
            ),
            patch.object(fleet_tools, "_ledger_fleet_action", AsyncMock(return_value=None)),
        ):
            result = _run(
                fleet_tools.fleet_create_agent(
                    actor_id="owner-1",
                    workspace_id="ws-1",
                    tenant_id="tenant-1",
                    name=name,
                    project_id=project_id,
                )
            )
        return result, create_project_mock, default_mock, assign_mock

    def test_unplaced_agent_gets_its_own_project_named_after_it(self):
        result, create_project_mock, default_mock, assign_mock = self._create(name="Pixel")
        self.assertTrue(result.get("ok"), result.get("error"))
        create_project_mock.assert_awaited_once()
        self.assertEqual(create_project_mock.await_args.kwargs.get("name"), "Pixel")
        default_mock.assert_not_awaited()
        self.assertEqual(
            assign_mock.await_args.kwargs.get("project_id"), "proj-own"
        )

    def test_two_unplaced_agents_never_share_a_project(self):
        """The dad/mother case: two agents created back-to-back with no
        project must land in two different projects, not one shared room."""
        seen = []

        async def _fake_create_project(**kwargs):
            pid = f"proj-{len(seen) + 1}"
            seen.append((kwargs.get("name"), pid))
            return {"id": pid}

        mock = AsyncMock(side_effect=_fake_create_project)
        r1, _, _, assign1 = self._create(name="Dad's agent", create_project_mock=mock)
        r2, _, _, assign2 = self._create(name="Mum's agent", create_project_mock=mock)
        self.assertTrue(r1.get("ok") and r2.get("ok"))
        self.assertEqual([n for n, _ in seen], ["Dad's agent", "Mum's agent"])
        p1 = assign1.await_args.kwargs.get("project_id")
        p2 = assign2.await_args.kwargs.get("project_id")
        self.assertNotEqual(p1, p2, "two unplaced agents must not share a project")

    def test_explicit_project_id_is_still_honoured(self):
        """Collaboration stays possible — it just has to be deliberate."""
        result, create_project_mock, default_mock, assign_mock = self._create(
            name="Shared worker", project_id="proj-chosen"
        )
        self.assertTrue(result.get("ok"), result.get("error"))
        create_project_mock.assert_not_awaited()
        default_mock.assert_not_awaited()
        self.assertEqual(
            assign_mock.await_args.kwargs.get("project_id"), "proj-chosen"
        )


# ── MAN-310 skills-delivery: storage layer ──────────────────────────────────
# install_metadata.skills — a workspace owner's own reusable-procedure
# library for a specialist, delivered to the Claude Agent SDK engine as real
# SKILL.md files (claude_agent_sdk_bridge.build_skills_plugin_dir). These
# tests cover the storage/validation layer only — turn-construction wiring
# and delivery live in test_claude_agent_sdk_bridge.py.


def _one_skill(**overrides) -> dict:
    base = {
        "name": "Refund lookup",
        "description": "Use when a customer asks about a refund status.",
        "body": "1. Look up the order.\n2. Report the refund status.",
        "kind": "skill",
        "enabled": True,
    }
    base.update(overrides)
    return base


class NormalizeSkillsPatchTests(unittest.TestCase):
    """fleet_tools._normalize_skills_patch — the save-time gate. Bad input is
    REJECTED (None, error), never silently truncated/coerced — unlike
    resolve_agent_skills, which must degrade gracefully reading storage that
    already exists."""

    def test_a_clean_list_round_trips_with_a_minted_id(self):
        clean, error = fleet_tools._normalize_skills_patch([_one_skill()])
        self.assertEqual(error, "")
        self.assertEqual(len(clean), 1)
        self.assertEqual(clean[0]["name"], "Refund lookup")
        self.assertEqual(clean[0]["kind"], "skill")
        self.assertTrue(clean[0]["enabled"])
        self.assertTrue(clean[0]["id"].startswith("sk_"))

    def test_an_explicit_id_is_preserved_not_reminted(self):
        clean, error = fleet_tools._normalize_skills_patch([_one_skill(id="sk_fixed")])
        self.assertEqual(error, "")
        self.assertEqual(clean[0]["id"], "sk_fixed")

    def test_enabled_defaults_true_when_omitted(self):
        skill = _one_skill()
        del skill["enabled"]
        clean, error = fleet_tools._normalize_skills_patch([skill])
        self.assertEqual(error, "")
        self.assertTrue(clean[0]["enabled"])

    def test_disabled_flag_is_preserved(self):
        clean, error = fleet_tools._normalize_skills_patch([_one_skill(enabled=False)])
        self.assertEqual(error, "")
        self.assertFalse(clean[0]["enabled"])

    def test_non_list_is_rejected(self):
        clean, error = fleet_tools._normalize_skills_patch({"name": "x"})
        self.assertIsNone(clean)
        self.assertIn("list", error)

    def test_too_many_skills_is_rejected(self):
        skills = [_one_skill(name=f"Skill {i}") for i in range(fleet_tools._MAX_SKILLS_PER_AGENT + 1)]
        clean, error = fleet_tools._normalize_skills_patch(skills)
        self.assertIsNone(clean)
        self.assertIn(str(fleet_tools._MAX_SKILLS_PER_AGENT), error)

    def test_missing_name_is_rejected(self):
        skill = _one_skill()
        skill["name"] = "  "
        clean, error = fleet_tools._normalize_skills_patch([skill])
        self.assertIsNone(clean)
        self.assertIn("name", error)

    def test_missing_body_is_rejected(self):
        skill = _one_skill()
        skill["body"] = ""
        clean, error = fleet_tools._normalize_skills_patch([skill])
        self.assertIsNone(clean)
        self.assertIn("body", error)

    def test_oversized_name_is_rejected(self):
        skill = _one_skill(name="x" * (fleet_tools._MAX_SKILL_NAME_CHARS + 1))
        clean, error = fleet_tools._normalize_skills_patch([skill])
        self.assertIsNone(clean)
        self.assertIn("name", error)

    def test_oversized_body_is_rejected(self):
        skill = _one_skill(body="x" * (fleet_tools._MAX_SKILL_BODY_CHARS + 1))
        clean, error = fleet_tools._normalize_skills_patch([skill])
        self.assertIsNone(clean)
        self.assertIn("body", error)

    def test_duplicate_names_case_insensitive_is_rejected(self):
        clean, error = fleet_tools._normalize_skills_patch(
            [_one_skill(name="Refund Lookup"), _one_skill(name="refund lookup")]
        )
        self.assertIsNone(clean)
        self.assertIn("Duplicate", error)

    def test_command_kind_is_rejected_not_silently_supported(self):
        # "command" is a deliberate v1 non-goal (see fleet_tools._VALID_
        # SKILL_KINDS' own docstring: no interactive REPL to type a slash
        # command into in this product's headless architecture) — a value
        # this product cannot deliver must be refused at save time, not
        # accepted and silently dropped at turn time.
        clean, error = fleet_tools._normalize_skills_patch([_one_skill(kind="command")])
        self.assertIsNone(clean)
        self.assertIn("kind", error)

    def test_non_dict_entry_is_rejected(self):
        clean, error = fleet_tools._normalize_skills_patch(["not-a-dict"])
        self.assertIsNone(clean)
        self.assertIn("object", error)


class ResolveAgentSkillsTests(unittest.TestCase):
    """fleet_tools.resolve_agent_skills — the read side. Must degrade
    gracefully on malformed storage (never raise), unlike the save-time
    validator above."""

    def test_no_skills_key_returns_empty(self):
        self.assertEqual(fleet_tools.resolve_agent_skills({"install_metadata": {}}), [])

    def test_none_install_returns_empty(self):
        self.assertEqual(fleet_tools.resolve_agent_skills(None), [])

    def test_returns_clean_records(self):
        bundle = {"install_metadata": {"skills": [_one_skill()]}}
        result = fleet_tools.resolve_agent_skills(bundle)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "Refund lookup")

    def test_enabled_only_filters_disabled_entries(self):
        bundle = {"install_metadata": {"skills": [
            _one_skill(name="On", enabled=True),
            _one_skill(name="Off", enabled=False),
        ]}}
        result = fleet_tools.resolve_agent_skills(bundle, enabled_only=True)
        self.assertEqual([s["name"] for s in result], ["On"])

    def test_enabled_only_false_returns_both(self):
        bundle = {"install_metadata": {"skills": [
            _one_skill(name="On", enabled=True),
            _one_skill(name="Off", enabled=False),
        ]}}
        result = fleet_tools.resolve_agent_skills(bundle, enabled_only=False)
        self.assertEqual({s["name"] for s in result}, {"On", "Off"})

    def test_malformed_entries_are_skipped_not_raised(self):
        bundle = {"install_metadata": {"skills": [
            "not-a-dict", {"name": ""}, {"body": "no name"}, _one_skill(),
        ]}}
        result = fleet_tools.resolve_agent_skills(bundle)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "Refund lookup")

    def test_non_list_skills_value_returns_empty(self):
        bundle = {"install_metadata": {"skills": "not-a-list"}}
        self.assertEqual(fleet_tools.resolve_agent_skills(bundle), [])


class FleetConfigureAgentSkillsTests(unittest.TestCase):
    """fleet_configure_agent's `skills` patch key — validation + persistence
    wiring, mirroring FleetConfigureAgentRecommendationTests' mocking
    pattern for the other patch keys."""

    @staticmethod
    def _bundle(agent_id="agent-x", metadata=None):
        return {"id": agent_id, "install_metadata": dict(metadata or {})}

    def test_valid_skills_patch_is_persisted(self):
        with (
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                new=AsyncMock(return_value=self._bundle()),
            ),
            patch(
                "server_modules.agent_registry_repository.update_workspace_agent_install",
                new=AsyncMock(return_value=self._bundle()),
            ) as mock_update,
        ):
            result = _run(
                fleet_tools.fleet_configure_agent(
                    actor_id="owner-1",
                    workspace_id="ws-1",
                    agent_id="agent-x",
                    patch={"skills": [_one_skill()]},
                )
            )
        self.assertTrue(result["ok"], result.get("error"))
        saved_metadata = mock_update.await_args.kwargs.get("metadata")
        self.assertEqual(len(saved_metadata["skills"]), 1)
        self.assertEqual(saved_metadata["skills"][0]["name"], "Refund lookup")

    def test_invalid_skills_patch_is_rejected_before_any_write(self):
        with (
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                new=AsyncMock(return_value=self._bundle()),
            ),
            patch(
                "server_modules.agent_registry_repository.update_workspace_agent_install",
                new=AsyncMock(return_value=self._bundle()),
            ) as mock_update,
        ):
            result = _run(
                fleet_tools.fleet_configure_agent(
                    actor_id="owner-1",
                    workspace_id="ws-1",
                    agent_id="agent-x",
                    patch={"skills": [{"name": "", "body": "x"}]},
                )
            )
        self.assertFalse(result["ok"])
        mock_update.assert_not_awaited()

    def test_skills_key_is_in_the_allowlist(self):
        self.assertIn("skills", fleet_tools._ALLOWED_CONFIGURE_KEYS)

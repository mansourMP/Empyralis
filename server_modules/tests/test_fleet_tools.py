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


if __name__ == "__main__":
    unittest.main()

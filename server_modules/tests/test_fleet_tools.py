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


if __name__ == "__main__":
    unittest.main()

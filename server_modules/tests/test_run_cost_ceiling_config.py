"""MAN-144: config/schema/resolver tests for the enforced per-run cost ceiling.

New file -- does not modify any existing test file or conftest.py. Covers the
config layer only (deployed_agent_config_schema.DeployedAgentCommercePolicy
.per_run_cost_ceiling_usd, deployed_agent_cost_cap_service
.deployed_agent_run_cost_ceiling_usd, config_defaults_service
.default_run_cost_ceiling_usd). The actual enforcement loops are covered by
test_run_cost_ceiling_engine.py (durable runs) and
test_run_cost_ceiling_direct_chat.py (direct chat).
"""

from __future__ import annotations

import unittest

from server_modules import config_defaults_service
from server_modules import deployed_agent_config_schema
from server_modules import deployed_agent_cost_cap_service


def _deployed_agent(*, metadata: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "id": "dagent_1",
        "tenant_id": "tenant-1",
        "owner_workspace_id": "ws-1",
        "name": "Store Assistant",
        "deployment_state": "live",
        "metadata": dict(metadata or {}),
    }


class RunCostCeilingConfigTests(unittest.TestCase):
    def test_default_applies_when_no_deployed_agent_is_given(self) -> None:
        """The default applies when no per-agent value is set."""
        resolved = deployed_agent_cost_cap_service.deployed_agent_run_cost_ceiling_usd(None)
        self.assertEqual(resolved, config_defaults_service.default_run_cost_ceiling_usd())
        self.assertGreater(resolved, 0.0)

    def test_default_applies_when_deployed_agent_has_no_commerce_policy(self) -> None:
        """The default applies when no per-agent value is set (agent exists,
        but never configured a per-run ceiling)."""
        deployed_agent = _deployed_agent(metadata={})
        resolved = deployed_agent_cost_cap_service.deployed_agent_run_cost_ceiling_usd(deployed_agent)
        self.assertEqual(resolved, config_defaults_service.default_run_cost_ceiling_usd())

    def test_per_agent_override_wins_over_default(self) -> None:
        deployed_agent = _deployed_agent(metadata={"per_run_cost_ceiling_usd": 3.5})
        resolved = deployed_agent_cost_cap_service.deployed_agent_run_cost_ceiling_usd(deployed_agent)
        self.assertEqual(resolved, 3.5)
        self.assertNotEqual(resolved, config_defaults_service.default_run_cost_ceiling_usd())

    def test_non_positive_override_falls_back_to_default_not_zero(self) -> None:
        """A ceiling resolver must never silently return an unbounded (0/None)
        value just because a workspace explicitly configured zero/negative --
        that would turn "misconfigured" into "no ceiling at all," the
        opposite of what this feature exists to guarantee. (A non-numeric
        string is rejected earlier, at config-parse time, by the same
        _normalize_positive_usd validation monthly_cost_cap_usd already
        uses -- not this resolver's concern.)"""
        for bad_value in (0, -5):
            with self.subTest(bad_value=bad_value):
                deployed_agent = _deployed_agent(metadata={"per_run_cost_ceiling_usd": bad_value})
                resolved = deployed_agent_cost_cap_service.deployed_agent_run_cost_ceiling_usd(deployed_agent)
                self.assertEqual(resolved, config_defaults_service.default_run_cost_ceiling_usd())

    def test_commerce_policy_round_trips_through_config_schema(self) -> None:
        """The schema layer this resolver reads from actually persists the
        field -- proves the wiring from raw metadata dict -> typed config ->
        back to metadata dict is intact end to end, matching the existing
        monthly_cost_cap_usd field's own round trip."""
        config = deployed_agent_config_schema.deployed_agent_config_from_record(
            {"name": "Agent", "metadata": {"per_run_cost_ceiling_usd": 7.25}}
        )
        self.assertEqual(config.commerce_policy.per_run_cost_ceiling_usd, 7.25)

        payload = deployed_agent_config_schema.metadata_from_deployed_agent_config(config)
        self.assertEqual(payload.get("per_run_cost_ceiling_usd"), 7.25)

    def test_commerce_policy_field_is_independent_of_monthly_cap(self) -> None:
        """Setting the monthly cap must not implicitly set (or be read as)
        the per-run ceiling, and vice versa -- they are deliberately two
        different knobs (see DeployedAgentCommercePolicy's own docstring)."""
        config = deployed_agent_config_schema.deployed_agent_config_from_record(
            {"name": "Agent", "metadata": {"monthly_cost_cap_usd": 25.0}}
        )
        self.assertEqual(config.commerce_policy.monthly_cost_cap_usd, 25.0)
        self.assertIsNone(config.commerce_policy.per_run_cost_ceiling_usd)


if __name__ == "__main__":
    unittest.main()

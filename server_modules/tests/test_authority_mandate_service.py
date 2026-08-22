"""Authority mandate — tier derivation, inheritance, and the enforcement rule.

Tests:
  (a) derive_tier_from_sender_class: owner -> owner; audience/unknown/garbage -> audience
  (b) derive_tier_from_owner_flag: True -> owner; False -> audience
  (c) normalize_tier / inherit_tier: valid values pass through; invalid/missing -> audience
  (d) is_owner_only_tool / is_tool_call_allowed: ALLOW by default; owner always
      allowed; non-owner refused only the machine-administration families.

(d) inverted on 2026-08-21 — it used to read "non-owner only when
audience_safe". The audience tool tier is deleted (see the module's own
docstring for the founder decision). These assertions are rewritten to the
new rule rather than removed, because the direction of the default is the
whole point and a silent flip of it must fail loudly.
"""

from __future__ import annotations

import unittest

from server_modules import authority_mandate_service


class DeriveTierFromSenderClassTests(unittest.TestCase):

    def test_owner_sender_class_is_owner_tier(self):
        self.assertEqual(
            authority_mandate_service.derive_tier_from_sender_class("owner"),
            authority_mandate_service.TIER_OWNER,
        )

    def test_owner_is_case_and_whitespace_insensitive(self):
        self.assertEqual(
            authority_mandate_service.derive_tier_from_sender_class("  Owner \n"),
            authority_mandate_service.TIER_OWNER,
        )

    def test_audience_sender_class_is_audience_tier(self):
        self.assertEqual(
            authority_mandate_service.derive_tier_from_sender_class("audience"),
            authority_mandate_service.TIER_AUDIENCE,
        )

    def test_unknown_sender_class_fails_safe_to_audience(self):
        """triage_service.resolve_sender_identity()'s "unknown" must collapse to audience, never owner."""
        self.assertEqual(
            authority_mandate_service.derive_tier_from_sender_class("unknown"),
            authority_mandate_service.TIER_AUDIENCE,
        )

    def test_empty_or_garbage_input_fails_safe_to_audience(self):
        for garbage in ("", None, "admin", "SUPERUSER", "  "):
            with self.subTest(garbage=garbage):
                self.assertEqual(
                    authority_mandate_service.derive_tier_from_sender_class(garbage),
                    authority_mandate_service.TIER_AUDIENCE,
                )


class DeriveTierFromOwnerFlagTests(unittest.TestCase):

    def test_true_is_owner(self):
        self.assertEqual(
            authority_mandate_service.derive_tier_from_owner_flag(True),
            authority_mandate_service.TIER_OWNER,
        )

    def test_false_is_audience(self):
        self.assertEqual(
            authority_mandate_service.derive_tier_from_owner_flag(False),
            authority_mandate_service.TIER_AUDIENCE,
        )


class NormalizeAndInheritTierTests(unittest.TestCase):

    def test_valid_tiers_pass_through(self):
        for tier in authority_mandate_service.VALID_TIERS:
            with self.subTest(tier=tier):
                self.assertEqual(authority_mandate_service.normalize_tier(tier), tier)
                self.assertEqual(authority_mandate_service.inherit_tier(tier), tier)

    def test_missing_or_invalid_value_fails_safe_to_audience(self):
        for value in (None, "", "made_up_tier", 42, {}):
            with self.subTest(value=value):
                self.assertEqual(
                    authority_mandate_service.normalize_tier(value),
                    authority_mandate_service.TIER_AUDIENCE,
                )
                self.assertEqual(
                    authority_mandate_service.inherit_tier(value),
                    authority_mandate_service.TIER_AUDIENCE,
                )

    def test_inherit_tier_never_upgrades_audience_to_owner(self):
        """The core anti-laundering invariant: a schedule/run/delegation spawned
        from an audience-tier turn must carry audience tier permanently."""
        self.assertEqual(
            authority_mandate_service.inherit_tier(authority_mandate_service.TIER_AUDIENCE),
            authority_mandate_service.TIER_AUDIENCE,
        )


class IsOwnerOnlyToolTests(unittest.TestCase):
    """The machine-administration floor: the ONLY thing a tier still decides.

    Mirrors OpenClaw's GATEWAY_OWNER_ONLY_CORE_TOOLS = ["cron", "gateway",
    "nodes"] — allow everything, deny the handful that administer the
    platform itself."""

    def test_fleet_family_is_owner_only_by_prefix(self):
        for name in (
            "fleet__create_agent",
            "fleet__configure_agent",
            "fleet__schedule_task",
            "fleet__schedule_recurring_task",
            "fleet__cancel_recurring_task",
            "fleet__list_agents",
        ):
            self.assertTrue(
                authority_mandate_service.is_owner_only_tool(tool_name=name), name
            )

    def test_goal_family_is_owner_only_by_prefix(self):
        for name in ("goal__create", "goal__update", "goal__list", "goal__get"):
            self.assertTrue(
                authority_mandate_service.is_owner_only_tool(tool_name=name), name
            )

    def test_mcp_configure_agent_spelling_is_owner_only(self):
        """The MCP surface spells fleet__configure_agent differently; the same
        administrative action must answer to the same rule."""
        self.assertTrue(
            authority_mandate_service.is_owner_only_tool(tool_name="empyralis_configure_agent")
        )

    def test_connector_action_id_space_reaches_the_same_verdict(self):
        """runs_execution's choke point sees "fleet"."schedule_task", never a
        flat tool name — both addressings must agree."""
        self.assertTrue(
            authority_mandate_service.is_owner_only_tool(
                connector_id="fleet", action_id="schedule_task"
            )
        )
        self.assertTrue(
            authority_mandate_service.is_owner_only_tool(connector_id="goal", action_id="create")
        )

    def test_ordinary_work_tools_are_not_owner_only(self):
        """Including the sharp ones. shell/hardware are deliberately NOT on
        this list — the boundary is 'administers the platform', not 'could be
        misused'. See authority_mandate_service's docstring; if this list
        starts growing, the deleted tier is growing back."""
        for name in (
            "shell__exec",
            "hardware__action",
            "memory_write",
            "document__write",
            "project_task__create",
            "web__search",
            "browser__navigate",
            "computer__click",
            "skill_invoke",
        ):
            self.assertFalse(
                authority_mandate_service.is_owner_only_tool(tool_name=name), name
            )

    def test_an_unknown_tool_is_not_owner_only(self):
        """The default DIRECTION is the inversion. A tool this module has
        never heard of is allowed, not refused."""
        self.assertFalse(
            authority_mandate_service.is_owner_only_tool(tool_name="some__tool_shipped_tomorrow")
        )
        self.assertFalse(authority_mandate_service.is_owner_only_tool())

    def test_a_lookalike_prefix_does_not_match(self):
        """"fleeting__x" must not be swept up by the "fleet__" prefix."""
        self.assertFalse(authority_mandate_service.is_owner_only_tool(tool_name="fleeting__x"))
        self.assertFalse(authority_mandate_service.is_owner_only_tool(connector_id="fleetwood"))


class IsToolCallAllowedTests(unittest.TestCase):

    def test_owner_allowed_everything(self):
        self.assertTrue(
            authority_mandate_service.is_tool_call_allowed("owner", tool_name="fleet__create_agent")
        )
        self.assertTrue(
            authority_mandate_service.is_tool_call_allowed("owner", tool_name="shell__exec")
        )

    def test_audience_allowed_every_ordinary_tool(self):
        """THE INVERSION, stated as a test. This is what the deleted
        audience_safe allowlist used to refuse."""
        for name in ("shell__exec", "hardware__action", "memory_write", "document__write"):
            self.assertTrue(
                authority_mandate_service.is_tool_call_allowed("audience", tool_name=name), name
            )

    def test_audience_refused_machine_administration(self):
        self.assertFalse(
            authority_mandate_service.is_tool_call_allowed(
                "audience", tool_name="fleet__schedule_task"
            )
        )
        self.assertFalse(
            authority_mandate_service.is_tool_call_allowed(
                "audience", connector_id="fleet", action_id="schedule_task"
            )
        )

    def test_system_tier_is_restricted_same_as_audience(self):
        """system is a provenance label, not an elevated tier — only "owner"
        gets the machine-administration family."""
        self.assertFalse(
            authority_mandate_service.is_tool_call_allowed("system", tool_name="goal__create")
        )
        self.assertTrue(
            authority_mandate_service.is_tool_call_allowed("system", tool_name="shell__exec")
        )

    def test_malformed_tier_value_fails_closed(self):
        """A malformed tier still fails toward audience, never owner — so it
        still loses the machine-administration family."""
        self.assertFalse(
            authority_mandate_service.is_tool_call_allowed(
                "owner_typo", tool_name="fleet__create_agent"
            )
        )
        self.assertFalse(
            authority_mandate_service.is_tool_call_allowed(None, tool_name="fleet__create_agent")
        )


class NoAudienceToolAllowlistSurvivesTests(unittest.TestCase):
    """Structural: the deleted allowlist must not come back as an API.

    A behavioural test cannot catch a reintroduction — a new
    `is_audience_tool_allowed` would type-check, pass its own tests, and be
    the tier growing back one helper at a time."""

    def test_module_exposes_no_audience_allowlist_api(self):
        for gone in ("is_audience_tool_allowed", "connector_tool_key"):
            self.assertFalse(
                hasattr(authority_mandate_service, gone),
                f"{gone} is part of the deleted per-agent audience_tools allowlist",
            )

    def test_is_tool_call_allowed_takes_no_audience_safe_argument(self):
        import inspect

        params = inspect.signature(authority_mandate_service.is_tool_call_allowed).parameters
        self.assertNotIn("audience_safe", params)

    def test_the_owner_only_floor_stays_small(self):
        """A canary against re-accumulation. Two prefixes and one exact name
        is the whole floor; if this needs raising, that is a founder decision
        (CLAUDE.md), not a drive-by addition."""
        self.assertEqual(len(authority_mandate_service.OWNER_ONLY_TOOL_PREFIXES), 2)
        self.assertLessEqual(len(authority_mandate_service.OWNER_ONLY_TOOL_NAMES), 2)
        self.assertEqual(len(authority_mandate_service.OWNER_ONLY_CONNECTOR_IDS), 2)


if __name__ == "__main__":
    unittest.main()

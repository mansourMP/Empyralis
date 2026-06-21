"""Tests for internalized governance — agent policy context builder.

Proves that:
  - T0/T1/T2 tiers resolve correctly from plan + hardware profile
  - Policy context block includes correct capabilities per tier
  - Kill switch notice appears when active
  - Hardware access notice appears for T2 only
  - Platform rules appear in every tier
"""

from __future__ import annotations

import unittest

from server_modules.agent_policy_context import (
    AgentTier,
    build_agent_policy_context,
    resolve_agent_tier,
    PLATFORM_RULES,
    HARDWARE_ACCESS_NOTICE,
    KILL_SWITCH_NOTICE,
    TIER_CAPABILITIES,
)


class AgentTierResolutionTests(unittest.TestCase):
    """resolve_agent_tier() returns correct tier from plan + hardware status."""

    def test_free_plan_returns_t0_cloud_only(self):
        tier = resolve_agent_tier(plan_id="free", has_hardware_profile=False)
        self.assertEqual(tier, AgentTier.T0)

    def test_pro_plan_returns_t1_cloud_compute(self):
        tier = resolve_agent_tier(plan_id="pro", has_hardware_profile=False)
        self.assertEqual(tier, AgentTier.T1)

    def test_pilot_plan_without_hardware_returns_t2(self):
        tier = resolve_agent_tier(plan_id="pilot", has_hardware_profile=False)
        self.assertEqual(tier, AgentTier.T2)

    def test_pilot_plan_with_hardware_returns_t2(self):
        tier = resolve_agent_tier(plan_id="pilot", has_hardware_profile=True)
        self.assertEqual(tier, AgentTier.T2)

    def test_enterprise_plan_returns_t2(self):
        tier = resolve_agent_tier(plan_id="enterprise", has_hardware_profile=False)
        self.assertEqual(tier, AgentTier.T2)

    def test_unknown_plan_falls_back_to_t0(self):
        tier = resolve_agent_tier(plan_id="nonexistent_plan", has_hardware_profile=False)
        self.assertEqual(tier, AgentTier.T0)

    def test_agent_machine_mode_overrides_to_t2(self):
        tier = resolve_agent_tier(plan_id="free", is_agent_machine=True)
        self.assertEqual(tier, AgentTier.T2)

    def test_empty_plan_defaults_to_free_t0(self):
        tier = resolve_agent_tier(plan_id="")
        self.assertEqual(tier, AgentTier.T0)


class PolicyContextBuilderTests(unittest.TestCase):
    """build_agent_policy_context() returns correct structured prompt block."""

    def test_t0_context_includes_correct_capabilities(self):
        ctx = build_agent_policy_context(plan_id="free")
        self.assertIn("Cloud-Only", ctx)
        for cap in TIER_CAPABILITIES[AgentTier.T0]:
            self.assertIn(cap, ctx)
        # T2 hardware capabilities should NOT appear
        self.assertNotIn("local_shell", ctx)
        self.assertNotIn("local_filesystem", ctx)
        self.assertNotIn("computer_control", ctx)

    def test_t1_context_includes_cloud_compute_capabilities(self):
        ctx = build_agent_policy_context(plan_id="pro")
        self.assertIn("Cloud Compute", ctx)
        self.assertIn("cloud_browser", ctx)
        self.assertIn("cloud_shell", ctx)
        # No local hardware
        self.assertNotIn("local_shell", ctx)

    def test_t2_context_includes_hardware_capabilities(self):
        ctx = build_agent_policy_context(plan_id="pilot", has_hardware_profile=True)
        self.assertIn("Hardware", ctx)
        self.assertIn("local_shell", ctx)
        self.assertIn("local_filesystem", ctx)
        self.assertIn("local_browser", ctx)

    def test_platform_rules_appear_in_every_tier(self):
        for plan in ("free", "pro", "pilot"):
            ctx = build_agent_policy_context(plan_id=plan)
            self.assertIn("Platform Rules", ctx,
                           f"Tier {plan} should include platform rules")

    def test_hardware_access_notice_appears_for_t2_only(self):
        t2_ctx = build_agent_policy_context(plan_id="pilot", has_hardware_profile=True)
        self.assertIn("Hardware Access", t2_ctx)
        self.assertIn("full access to the consumer's machine", t2_ctx)

        t1_ctx = build_agent_policy_context(plan_id="pro")
        self.assertNotIn("Hardware Access", t1_ctx)

        t0_ctx = build_agent_policy_context(plan_id="free")
        self.assertNotIn("Hardware Access", t0_ctx)

    def test_kill_switch_notice_appears_when_active(self):
        ctx = build_agent_policy_context(plan_id="free", kill_switch_active=True)
        self.assertIn("Capabilities Suspended", ctx)
        self.assertIn("capabilities are currently suspended", ctx)

    def test_kill_switch_notice_absent_when_inactive(self):
        ctx = build_agent_policy_context(plan_id="free", kill_switch_active=False)
        self.assertNotIn("Capabilities Suspended", ctx)
        self.assertNotIn("capabilities are currently suspended", ctx)

    def test_kill_switch_notice_tells_agent_to_respond_naturally(self):
        ctx = build_agent_policy_context(plan_id="free", kill_switch_active=True)
        self.assertIn("do not attempt tool calls", ctx.lower())
        self.assertIn("cannot do that right now", ctx.lower())

    def test_context_includes_use_freely_language(self):
        """Agent is told to use capabilities freely — no permission-asking."""
        ctx = build_agent_policy_context(plan_id="pro")
        self.assertIn("freely", ctx.lower())
        self.assertNotIn("Ask for explicit confirmation", ctx)

    def test_custom_capabilities_override_tier_defaults(self):
        custom = ["web_search", "memory_read"]
        ctx = build_agent_policy_context(plan_id="free", allowed_capabilities=custom)
        self.assertIn("web_search, memory_read", ctx)
        self.assertNotIn("content_generation", ctx)

    def test_all_tiers_produce_non_empty_context(self):
        for plan in ("free", "pro", "pilot"):
            ctx = build_agent_policy_context(plan_id=plan)
            self.assertGreater(len(ctx), 50,
                               f"Tier {plan} should produce meaningful context")
            self.assertIn("## Your Platform Tier:", ctx)

    def test_kill_switch_overrides_t2_hardware(self):
        """When kill switch is active, agent sees capabilities-suspended even on T2."""
        ctx = build_agent_policy_context(
            plan_id="pilot",
            has_hardware_profile=True,
            kill_switch_active=True,
        )
        self.assertIn("Capabilities Suspended", ctx)
        self.assertIn("Hardware", ctx)  # Tier label still shows
        self.assertIn("full access to the consumer's machine", ctx)  # Hardware notice still shows


class PolicyContextAuditTrailTests(unittest.TestCase):
    """Policy context preserves audit information without blocking consumers."""

    def test_context_never_mentions_approval_buttons(self):
        """Consumers NEVER see approval buttons — this must not leak into context."""
        for plan in ("free", "pro", "pilot"):
            ctx = build_agent_policy_context(plan_id=plan)
            self.assertNotIn("approval button", ctx.lower())
            self.assertNotIn("approval card", ctx.lower())
            self.assertNotIn("blocked", ctx.lower())
            self.assertNotIn("requires approval", ctx.lower())

    def test_context_never_mentions_governance(self):
        """The word 'governance' must not appear — it's invisible to consumers."""
        for plan in ("free", "pro", "pilot"):
            ctx = build_agent_policy_context(plan_id=plan)
            self.assertNotIn("governance", ctx.lower())

    def test_context_is_natural_language(self):
        """Context should read as natural guidance, not a system directive."""
        ctx = build_agent_policy_context(plan_id="pro")
        # Should not contain internal function names or error codes
        self.assertNotIn("evaluate_action_policy", ctx)
        self.assertNotIn("DECISION_", ctx)
        self.assertNotIn("audit_payload", ctx)

import unittest
from unittest.mock import patch

from server_modules import entitlements_service


class EntitlementsServiceTests(unittest.TestCase):
    def test_resolve_workspace_entitlement_state_preserves_non_gated_capabilities(self) -> None:
        state = entitlements_service.resolve_workspace_entitlement_state(
            workspace={"metadata": {"billing": {"plan": "power"}}},
        )

        self.assertEqual(state.plan_id, "pro")
        self.assertTrue(state.non_gated_capabilities["core_sage_identity"])
        self.assertTrue(state.non_gated_capabilities["basic_specialist_architecture"])
        self.assertTrue(state.non_gated_capabilities["local_runtime_mode"])
        self.assertTrue(state.non_gated_capabilities["byo_provider_mode"])
        self.assertTrue(state.entitlements["premium_connectors_enabled"])

    def test_enforce_hosted_runtime_access_rejects_monthly_minutes_exhaustion(self) -> None:
        workspace = {
            "metadata": {
                "billing": {
                    "plan": "free",
                    "entitlement_usage": {"hosted_runtime_minutes_monthly": 60},
                }
            }
        }

        with self.assertRaises(entitlements_service.EntitlementDeniedError) as ctx:
            entitlements_service.enforce_hosted_runtime_access(
                workspace=workspace,
                workspace_id="workspace-1",
                selected_attachment={"attachment_kind": "managed_cloud"},
            )

        self.assertEqual(ctx.exception.reason, "hosted_runtime_unavailable")

    def test_enforce_hosted_runtime_access_counts_managed_cloud_concurrency_only(self) -> None:
        workspace = {"metadata": {"billing": {"plan": "pro"}}}

        with self.assertRaises(entitlements_service.EntitlementQuotaExceededError) as ctx:
            entitlements_service.enforce_hosted_runtime_access(
                workspace=workspace,
                workspace_id="workspace-1",
                selected_attachment={"attachment_kind": "managed_cloud"},
                live_runs_fn=lambda: [
                    {
                        "run_id": "run-1",
                        "status": "running",
                        "context": {
                            "workspace_id": "workspace-1",
                            "metadata": {"execution_target_selected": "cloud"},
                        },
                    },
                    {
                        "run_id": "run-2",
                        "status": "queued",
                        "context": {
                            "workspace_id": "workspace-1",
                            "metadata": {"execution_target_selected": "cloud"},
                        },
                    },
                    {
                        "run_id": "run-3",
                        "status": "running",
                        "context": {
                            "workspace_id": "workspace-1",
                            "metadata": {"execution_target_selected": "cloud"},
                        },
                    },
                    {
                        "run_id": "run-4",
                        "status": "running",
                        "context": {
                            "workspace_id": "workspace-1",
                            "metadata": {"execution_target_selected": "cloud"},
                        },
                    },
                ],
            )

        self.assertEqual(ctx.exception.reason, "hosted_runtime_concurrency_exhausted")

    def test_workspace_hosted_execution_count_prefers_bounded_count_query(self) -> None:
        count = entitlements_service._workspace_hosted_execution_count(
            "workspace-1",
            count_hosted_live_runs_fn=lambda workspace_id: 3,
        )

        self.assertEqual(count, 3)

    def test_enforce_hosted_runtime_access_bypasses_self_hosted_nodes(self) -> None:
        workspace = {
            "metadata": {
                "billing": {
                    "plan": "free",
                    "entitlement_usage": {"hosted_runtime_minutes_monthly": 600},
                }
            }
        }

        result = entitlements_service.enforce_hosted_runtime_access(
            workspace=workspace,
            workspace_id="workspace-1",
            selected_attachment={"attachment_kind": "self_hosted_business_node"},
        )

        self.assertEqual(result["enforcement_target"], "self_hosted")
        self.assertEqual(result["usage_snapshot"]["concurrent_hosted_executions"], 0)

    def test_enforce_hosted_runtime_access_marks_cloud_computer_metering_target(self) -> None:
        result = entitlements_service.enforce_hosted_runtime_access(
            workspace={"metadata": {"billing": {"plan": "pro"}}},
            workspace_id="workspace-1",
            selected_attachment={"attachment_kind": "cloud_computer"},
            live_runs_fn=lambda: [],
        )

        self.assertEqual(result["enforcement_target"], "cloud_computer")
        self.assertTrue(result["metered_cloud_computer"])

    def test_workspace_capability_flags_distinguish_free_and_paid_surfaces(self) -> None:
        free_state = entitlements_service.resolve_workspace_entitlement_state(
            workspace={"metadata": {"billing": {"plan": "free"}}},
        )
        paid_state = entitlements_service.resolve_workspace_entitlement_state(
            workspace={"metadata": {"billing": {"plan": "pro"}}},
        )

        free_flags = entitlements_service.workspace_capability_flags(state=free_state)
        paid_flags = entitlements_service.workspace_capability_flags(state=paid_state)

        self.assertFalse(free_flags["mobile_app_enabled"])
        self.assertFalse(free_flags["approvals_enabled"])
        self.assertFalse(free_flags["artifacts_enabled"])
        self.assertTrue(free_flags["telegram_channel_enabled"])
        self.assertTrue(free_flags["whatsapp_channel_enabled"])
        self.assertFalse(free_flags["priority_sync_enabled"])
        self.assertEqual(free_flags["max_specialists"], 1)
        self.assertTrue(paid_flags["mobile_app_enabled"])
        self.assertTrue(paid_flags["approvals_enabled"])
        self.assertTrue(paid_flags["artifacts_enabled"])
        self.assertTrue(paid_flags["priority_sync_enabled"])
        self.assertEqual(paid_flags["max_specialists"], 3)
        self.assertTrue(paid_flags["hosted_ai_enabled"])
        self.assertEqual(paid_flags["hosted_sage_ai_policy"], "enabled_with_cap")
        self.assertEqual(paid_flags["hosted_sage_ai_reason"], None)
        self.assertIn("deployed_agent_quotas", paid_flags)
        self.assertIn("mode_limits", paid_flags["deployed_agent_quotas"])

    def test_deployed_agent_quota_defaults_respect_runtime_target_capacity(self) -> None:
        state = entitlements_service.resolve_workspace_entitlement_state(
            workspace={
                "metadata": {
                    "billing": {
                        "plan": "pro",
                        "overrides": {
                            "max_deployed_agents_live": 5,
                            "max_my_computer_agent_live": 4,
                            "max_self_hosted_agent_live": 4,
                        },
                    }
                }
            },
        )
        quotas = entitlements_service.deployed_agent_quota_defaults(
            state=state,
            runtime_targets={
                "targets": [
                    {"target_id": "local_companion", "attachment_count": 2},
                    {"target_id": "self_host_runtime", "attachment_count": 1},
                ]
            },
        )

        self.assertEqual(quotas["mode_limits"]["my_computer_agent"]["live_agents_max"], 2)
        self.assertEqual(quotas["mode_limits"]["self_hosted_agent"]["live_agents_max"], 1)

    def test_enforce_specialist_slot_access_rejects_free_workspace_after_draft_limit(self) -> None:
        with self.assertRaises(entitlements_service.EntitlementQuotaExceededError) as ctx:
            entitlements_service.enforce_specialist_slot_access(
                workspace={"metadata": {"billing": {"plan": "free"}}},
                current_specialist_count=10,
            )

        self.assertEqual(ctx.exception.reason, "created_agent_limit_exceeded")

    def test_enforce_mobile_app_access_rejects_free_workspace(self) -> None:
        with self.assertRaises(entitlements_service.EntitlementDeniedError) as ctx:
            entitlements_service.enforce_mobile_app_access(
                workspace={"metadata": {"billing": {"plan": "free"}}},
            )

        self.assertEqual(ctx.exception.reason, "mobile_app_unavailable")

    def test_enforce_mobile_app_access_allows_beta_override_from_workspace_metadata(self) -> None:
        payload = entitlements_service.enforce_mobile_app_access(
            workspace={"metadata": {"billing": {"plan": "free"}, "mobile_beta_enabled": True}},
        )

        self.assertEqual(payload["plan_id"], "free")
        self.assertTrue(payload["capabilities"]["mobile_app_enabled"])

    def test_enforce_mobile_app_access_allows_beta_override_from_env(self) -> None:
        with patch("server_modules.entitlements_service.os.getenv", return_value="1"):
            payload = entitlements_service.enforce_mobile_app_access(
                workspace={"metadata": {"billing": {"plan": "free"}}},
            )

        self.assertEqual(payload["plan_id"], "free")
        self.assertTrue(payload["capabilities"]["mobile_app_enabled"])

    def test_workspace_entitlement_payload_includes_capabilities(self) -> None:
        payload = entitlements_service.workspace_entitlement_payload(
            workspace={"metadata": {"billing": {"plan": "free"}}},
        )

        self.assertEqual(payload["plan_id"], "free")
        self.assertIn("capabilities", payload)
        self.assertFalse(payload["capabilities"]["approvals_enabled"])
        self.assertEqual(payload["capabilities"]["history_window_days"], 7)

    def test_hosted_sage_ai_defaults_to_capped_credits_for_paid_plan(self) -> None:
        state = entitlements_service.resolve_workspace_entitlement_state(
            workspace={"metadata": {"billing": {"plan": "pro"}}},
        )
        hosted = entitlements_service.hosted_sage_ai_access_state(state=state)

        self.assertTrue(hosted["allowed"])
        self.assertEqual(hosted["policy"], "enabled_with_cap")
        self.assertEqual(hosted["reason"], None)
        self.assertEqual(hosted["monthly_credit_cap"], 10000)

    def test_hosted_sage_ai_enabled_with_cap_allows_when_under_cap(self) -> None:
        state = entitlements_service.resolve_workspace_entitlement_state(
            workspace={
                "metadata": {
                    "billing": {
                        "plan": "pro",
                        "hosted_sage_ai_policy": "enabled_with_cap",
                        "hosted_sage_ai_monthly_cap_usd": 10.0,
                        "usage": {"hosted_sage_cost_usd_monthly": 2.5},
                    }
                }
            },
        )
        hosted = entitlements_service.hosted_sage_ai_access_state(state=state)

        self.assertTrue(hosted["allowed"])
        self.assertEqual(hosted["policy"], "enabled_with_cap")
        self.assertEqual(hosted["reason"], None)
        self.assertEqual(hosted["monthly_credit_cap"], 200000)
        self.assertEqual(hosted["monthly_credits_used"], 50000)
        self.assertEqual(hosted["monthly_credits_remaining"], 150000)

    def test_hosted_sage_ai_purchased_credits_allow_after_monthly_cap_reached(self) -> None:
        state = entitlements_service.resolve_workspace_entitlement_state(
            workspace={
                "metadata": {
                    "billing": {
                        "plan": "pro",
                        "hosted_sage_ai_policy": "enabled_with_cap",
                        "hosted_sage_ai_monthly_cap_usd": 0.5,
                        "usage": {
                            "hosted_sage_cost_usd_monthly": 0.5,
                            "hosted_sage_credit_balance_usd": 5.0,
                        },
                    }
                }
            },
        )
        hosted = entitlements_service.hosted_sage_ai_access_state(state=state)

        self.assertTrue(hosted["allowed"])
        self.assertEqual(hosted["reason"], None)
        self.assertEqual(hosted["monthly_remaining_usd"], 0.0)
        self.assertEqual(hosted["credit_balance_usd"], 5.0)
        self.assertEqual(hosted["total_available_usd"], 5.0)
        self.assertEqual(hosted["total_available_credits"], 100000)

    def test_hosted_sage_ai_respects_admin_defaults_billing_plan(self) -> None:
        state = entitlements_service.resolve_workspace_entitlement_state(
            workspace={
                "metadata": {
                    "admin_defaults": {
                        "payload": {
                            "billing_plan": "pro",
                            "hosted_sage_ai_policy": "enabled_with_cap",
                            "hosted_sage_ai_monthly_cap_usd": 5.0,
                        }
                    }
                }
            },
        )
        hosted = entitlements_service.hosted_sage_ai_access_state(state=state)

        self.assertEqual(state.plan_id, "pro")
        self.assertTrue(hosted["allowed"])
        self.assertEqual(hosted["policy"], "enabled_with_cap")
        self.assertEqual(hosted["monthly_credit_cap"], 100000)

    def test_enforce_hosted_ai_access_rejects_when_cap_reached(self) -> None:
        with self.assertRaises(entitlements_service.EntitlementDeniedError) as ctx:
            entitlements_service.enforce_hosted_ai_access(
                workspace={
                    "metadata": {
                        "billing": {
                            "plan": "pro",
                            "hosted_sage_ai_policy": "enabled_with_cap",
                            "hosted_sage_ai_monthly_cap_usd": 3.0,
                            "usage": {"hosted_sage_cost_usd_monthly": 3.0},
                        }
                    }
                }
            )

        self.assertEqual(ctx.exception.reason, "hosted_ai_cap_reached")

    def test_chat_model_tier_policy_applies_free_plan_defaults(self) -> None:
        state = entitlements_service.resolve_workspace_entitlement_state(
            workspace={"metadata": {"billing": {"plan": "free"}}},
        )
        policy = entitlements_service.chat_model_tier_policy(state=state)

        self.assertTrue(policy["tiers"]["light"]["enabled"])
        self.assertTrue(policy["tiers"]["pro"]["enabled"])
        self.assertFalse(policy["tiers"]["max"]["enabled"])
        self.assertFalse(policy["virtual_computer_runtime_enabled"])
        self.assertEqual(policy["tiers"]["pro"]["monthly_credit_cap"], 350)

    def test_chat_model_tier_policy_allows_workspace_overrides(self) -> None:
        state = entitlements_service.resolve_workspace_entitlement_state(
            workspace={
                "metadata": {
                    "billing": {
                        "plan": "pro",
                        "overrides": {
                            "chat_tier_max_enabled": False,
                            "virtual_computer_runtime_enabled": True,
                            "enterprise_admin_controls_enabled": True,
                        },
                    }
                }
            },
        )
        policy = entitlements_service.chat_model_tier_policy(state=state)

        self.assertFalse(policy["tiers"]["max"]["enabled"])
        self.assertTrue(policy["virtual_computer_runtime_enabled"])
        self.assertTrue(policy["enterprise_admin_controls_enabled"])


if __name__ == "__main__":
    unittest.main()

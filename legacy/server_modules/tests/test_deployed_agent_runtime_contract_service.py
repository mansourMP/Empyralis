import unittest

from server_modules import deployed_agent_runtime_contract_service as contract


class DeployedAgentRuntimeContractServiceTests(unittest.TestCase):
    def test_studio_agent_mode_contract_maps_to_internal_runtime_contract(self):
        text_contract = contract.studio_agent_mode_contract("text_agent")
        self.assertEqual(text_contract["placement"]["kind"], "managed_cloud")
        self.assertEqual(text_contract["supplier"]["kind"], "empyralis")
        self.assertFalse(text_contract["computer_allowed"])

        cloud_contract = contract.studio_agent_mode_contract("cloud_computer_agent")
        self.assertEqual(cloud_contract["placement"]["kind"], "hosted_hardware_pool")
        self.assertEqual(cloud_contract["placement"]["runtime_target"], "cloud")
        self.assertTrue(cloud_contract["computer_allowed"])

        local_contract = contract.studio_agent_mode_contract("my_computer_agent")
        self.assertEqual(local_contract["placement"]["kind"], "customer_local")
        self.assertEqual(local_contract["supplier"]["kind"], "customer")

        hosted_contract = contract.studio_agent_mode_contract("self_hosted_agent")
        self.assertEqual(hosted_contract["placement"]["kind"], "customer_hosted")
        self.assertEqual(hosted_contract["placement"]["runtime_target"], "self_hosted")

    def test_runtime_supply_contract_ignores_arbitrary_runtime_strings_when_mode_is_set(self):
        payload = contract.normalize_runtime_supply_contract(
            {
                "supplier": {"kind": "third_party_certified"},
                "placement": {"kind": "customer_hosted"},
                "computer_automation": {
                    "enabled": True,
                    "runtime_class": "virtual_browser",
                    "allowed_domains": ["example.com"],
                    "max_concurrent_sessions": 1,
                },
            },
            studio_agent_mode="text_agent",
            runtime_supplier="customer",
            runtime_placement="customer_hosted",
            runtime_target="self_hosted",
        )
        self.assertEqual(payload["studio_agent_mode"], "text_agent")
        self.assertEqual(payload["placement"]["kind"], "managed_cloud")
        self.assertEqual(payload["supplier"]["kind"], "empyralis")
        self.assertFalse(payload["computer_automation"]["enabled"])

    def test_runtime_placement_normalizes_without_computer_automation(self):
        self.assertEqual(contract.normalize_runtime_placement("cloud"), "managed_cloud")
        self.assertEqual(contract.normalize_runtime_placement("empyralis_hosted_device"), "hosted_hardware_pool")
        self.assertEqual(contract.normalize_runtime_placement("local_computer"), "customer_local")
        self.assertEqual(contract.normalize_runtime_placement("self_hosted_business_node"), "customer_hosted")
        self.assertEqual(contract.runtime_target_for_placement("hosted_hardware_pool"), "cloud")
        self.assertEqual(contract.runtime_target_for_placement("customer_hosted"), "self_hosted")

    def test_runtime_supplier_defaults_follow_placement(self):
        self.assertEqual(contract.normalize_runtime_supplier(None, runtime_placement="managed_cloud"), "empyralis")
        self.assertEqual(contract.normalize_runtime_supplier(None, runtime_placement="hosted_hardware_pool"), "empyralis")
        self.assertEqual(contract.normalize_runtime_supplier(None, runtime_placement="customer_local"), "customer")
        self.assertEqual(contract.normalize_runtime_supplier("marketplace_runtime_provider"), "third_party_certified")

    def test_computer_automation_is_disabled_by_default(self):
        policy = contract.normalize_computer_automation_config({})

        self.assertFalse(policy["enabled"])
        self.assertIsNone(policy["runtime_class"])
        self.assertEqual(policy["allowed_domains"], [])
        self.assertEqual(policy["max_concurrent_sessions"], 0)
        self.assertFalse(policy["inherit_host_environment"])
        self.assertEqual(policy["filesystem_default_access"], "none")
        self.assertTrue(policy["emergency_stop_enabled"])

    def test_computer_automation_requires_explicit_enablement_and_allowed_domain(self):
        blocked = contract.computer_automation_guardrail_state(
            {"enabled": False},
            requested_domain="supplier.example.com",
        )
        self.assertFalse(blocked["allowed"])
        self.assertIn("computer_automation_disabled", blocked["reasons"])

        missing_domain = contract.computer_automation_guardrail_state(
            {"enabled": True, "runtime_class": "virtual_browser", "max_concurrent_sessions": 1},
            requested_domain="supplier.example.com",
        )
        self.assertFalse(missing_domain["allowed"])
        self.assertIn("allowed_domain_required", missing_domain["reasons"])

        allowed = contract.computer_automation_guardrail_state(
            {
                "enabled": True,
                "runtime_class": "virtual_browser",
                "allowed_domains": ["example.com"],
                "max_concurrent_sessions": 1,
            },
            requested_domain="supplier.example.com",
        )
        self.assertTrue(allowed["allowed"])

    def test_computer_automation_blocks_over_concurrency_and_budget(self):
        state = contract.computer_automation_guardrail_state(
            {
                "enabled": True,
                "runtime_class": "virtual_browser",
                "allowed_domains": ["example.com"],
                "max_concurrent_sessions": 1,
                "daily_budget_usd": 2,
            },
            requested_domain="example.com",
            active_sessions=1,
            estimated_cost_usd=3,
        )

        self.assertFalse(state["allowed"])
        self.assertIn("concurrency_limit_reached", state["reasons"])
        self.assertIn("daily_budget_exceeded", state["reasons"])

    def test_runtime_supply_contract_keeps_supplier_placement_and_automation_separate(self):
        payload = contract.normalize_runtime_supply_contract(
            {
                "supplier": {"kind": "empyralis", "id": "pool-east"},
                "placement": {"kind": "hosted_hardware_pool"},
                "marketplace_policy": {"visibility": "private"},
            },
            computer_automation={"enabled": False},
            public_tier="pro",
            provider="deepseek",
            model="deepseek-v4-pro",
        )

        self.assertEqual(payload["supplier"]["kind"], "empyralis")
        self.assertEqual(payload["placement"]["kind"], "hosted_hardware_pool")
        self.assertEqual(payload["placement"]["runtime_target"], "cloud")
        self.assertFalse(payload["computer_automation"]["enabled"])
        self.assertFalse(payload["provider_binding"]["expose_provider_model_to_ordinary_ui"])
        self.assertEqual(payload["ai_source"]["kind"], "empyralis_credits")
        self.assertTrue(payload["ai_source"]["uses_platform_credits"])

    def test_runtime_supply_contract_normalizes_studio_ai_source(self):
        payload = contract.normalize_runtime_supply_contract(
            {
                "model_tier": {"billing_source": "my_api_key"},
                "provider_binding": {"internal_provider": "openai", "internal_model": "gpt-5.1"},
            }
        )

        self.assertEqual(payload["model_tier"]["billing_source"], "workspace_api_key")
        self.assertEqual(payload["ai_source"]["kind"], "workspace_api_key")
        self.assertEqual(payload["ai_source"]["payer"], "BYOK")
        self.assertTrue(payload["ai_source"]["requires_vault_provider"])
        self.assertFalse(payload["ai_source"]["uses_platform_credits"])

    def test_runtime_supply_contract_local_source_requires_local_runtime(self):
        payload = contract.normalize_runtime_supply_contract(
            {"ai_source": {"kind": "local_model"}},
            studio_agent_mode="my_computer_agent",
        )

        self.assertEqual(payload["ai_source"]["kind"], "local_model")
        self.assertEqual(payload["ai_source"]["payer"], "local")
        self.assertTrue(payload["ai_source"]["requires_local_runtime"])

    def test_studio_platform_credits_reject_byok_first_provider(self):
        with self.assertRaises(ValueError) as error:
            contract.validate_studio_ai_source_provider_runtime(
                ai_source={"kind": "empyralis_credits"},
                provider="openrouter",
                model="openai/gpt-5.2",
                runtime_placement="managed_cloud",
                runtime_target="cloud",
                provider_scopes=["sage_personal", "workspace_api", "studio_safe"],
                pricing_known=False,
            )

        self.assertIn("BYOK/workspace-key only", str(error.exception))

    def test_studio_workspace_api_key_accepts_byok_first_provider(self):
        payload = contract.validate_studio_ai_source_provider_runtime(
            ai_source={"kind": "workspace_api_key"},
            provider="groq",
            model="llama-3.3-70b-versatile",
            runtime_placement="managed_cloud",
            runtime_target="cloud",
            provider_scopes=["sage_personal", "workspace_api", "studio_safe"],
        )

        self.assertEqual(payload["ai_source"]["kind"], "workspace_api_key")
        self.assertEqual(payload["provider"], "groq")

    def test_studio_rejects_provider_without_studio_safe_scope(self):
        with self.assertRaises(ValueError) as error:
            contract.validate_studio_ai_source_provider_runtime(
                ai_source={"kind": "empyralis_credits"},
                provider="openai-codex",
                model="gpt-5.2-codex",
                runtime_placement="managed_cloud",
                runtime_target="cloud",
                provider_scopes=["sage_personal"],
            )

        self.assertIn("not available for Studio agents", str(error.exception))

    def test_studio_subscription_passthrough_allows_cli_provider_on_local_runtime(self):
        payload = contract.validate_studio_ai_source_provider_runtime(
            ai_source={"kind": "subscription_passthrough"},
            provider="openai-codex",
            model="gpt-5.3-codex",
            runtime_placement="customer_local",
            runtime_target="local",
            provider_scopes=["sage_personal"],
        )

        self.assertEqual(payload["ai_source"]["kind"], "subscription_passthrough")
        self.assertEqual(payload["provider"], "openai-codex")

    def test_studio_subscription_passthrough_requires_local_runtime_and_cli_provider(self):
        with self.assertRaises(ValueError) as error:
            contract.validate_studio_ai_source_provider_runtime(
                ai_source={"kind": "subscription_passthrough"},
                provider="openai",
                model="gpt-5.4",
                runtime_placement="managed_cloud",
                runtime_target="cloud",
                provider_scopes=["sage_personal", "workspace_api", "studio_safe"],
            )

        message = str(error.exception)
        self.assertIn("requires local_companion runtime", message)
        self.assertIn("limited to codex_cli or claude_code_cli", message)

    def test_studio_local_model_requires_local_runtime(self):
        with self.assertRaises(ValueError) as error:
            contract.validate_studio_ai_source_provider_runtime(
                ai_source={"kind": "local_model"},
                provider="ollama",
                model="llama3.2",
                runtime_placement="managed_cloud",
                runtime_target="cloud",
                provider_scopes=["sage_personal", "studio_safe", "local_only"],
            )

        self.assertIn("local_companion runtime", str(error.exception))

    def test_validate_mode_capability_matrix_rejects_text_agent_computer_automation(self):
        with self.assertRaises(ValueError) as error:
            contract.validate_mode_capability_matrix(
                studio_agent_mode="text_agent",
                runtime_placement="managed_cloud",
                runtime_target="cloud",
                computer_automation={
                    "enabled": True,
                    "runtime_class": "virtual_browser",
                    "allowed_domains": ["example.com"],
                    "max_concurrent_sessions": 1,
                },
                stage="create",
            )

        self.assertIn("text_agent cannot enable computer automation", str(error.exception))

    def test_validate_mode_capability_matrix_requires_runtime_target_health_on_deploy(self):
        with self.assertRaises(ValueError) as error:
            contract.validate_mode_capability_matrix(
                studio_agent_mode="cloud_computer_agent",
                runtime_placement="hosted_hardware_pool",
                runtime_target="cloud",
                computer_automation={
                    "enabled": True,
                    "runtime_class": "virtual_browser",
                    "allowed_domains": ["example.com"],
                    "max_concurrent_sessions": 1,
                    "daily_budget_usd": 5,
                    "monthly_budget_usd": 25,
                    "max_session_runtime_seconds": 900,
                },
                stage="deploy",
                runtime_targets={
                    "targets": [
                        {
                            "target_id": "sage_cloud_computer",
                            "available": True,
                            "online": True,
                            "healthy": False,
                        }
                    ]
                },
            )

        self.assertIn("sage_cloud_computer", str(error.exception))
        self.assertIn("healthy", str(error.exception))

    def test_validate_mode_capability_matrix_requires_budget_and_allowlist_when_computer_enabled(self):
        with self.assertRaises(ValueError) as error:
            contract.validate_mode_capability_matrix(
                studio_agent_mode="cloud_computer_agent",
                runtime_placement="hosted_hardware_pool",
                runtime_target="cloud",
                computer_automation={"enabled": True, "runtime_class": "virtual_browser"},
                stage="create",
                runtime_targets={
                    "targets": [
                        {"target_id": "sage_cloud_computer", "available": True},
                    ]
                },
            )

        detail = str(error.exception)
        self.assertIn("domain allowlist", detail.lower())
        self.assertIn("daily_budget_usd", detail)
        self.assertIn("monthly_budget_usd", detail)

    def test_validate_mode_capability_matrix_requires_secure_computer_safety_controls(self):
        with self.assertRaises(ValueError) as error:
            contract.validate_mode_capability_matrix(
                studio_agent_mode="cloud_computer_agent",
                runtime_placement="hosted_hardware_pool",
                runtime_target="cloud",
                computer_automation={
                    "enabled": True,
                    "runtime_class": "virtual_browser",
                    "allowed_domains": ["example.com"],
                    "max_concurrent_sessions": 1,
                    "daily_budget_usd": 5,
                    "monthly_budget_usd": 25,
                    "idle_timeout_seconds": 300,
                    "max_session_runtime_seconds": 900,
                    "inherit_host_environment": True,
                    "filesystem_default_access": "full_host",
                    "terminal_command_policy": "unrestricted",
                    "sensitive_action_confirmation_required": False,
                    "allow_software_install": True,
                    "emergency_stop_enabled": False,
                    "required_owner_approval_actions": [],
                },
                stage="create",
            )

        detail = str(error.exception).lower()
        self.assertIn("cannot inherit host environment variables", detail)
        self.assertIn("restricted filesystem access", detail)
        self.assertIn("safe terminal command policy", detail)
        self.assertIn("sensitive-action confirmation", detail)
        self.assertIn("software installs", detail)
        self.assertIn("emergency stop", detail)
        self.assertIn("missing required risky actions", detail)

    def test_validate_mode_capability_matrix_requires_runtime_target_availability_on_create(self):
        with self.assertRaises(ValueError) as error:
            contract.validate_mode_capability_matrix(
                studio_agent_mode="self_hosted_agent",
                runtime_placement="customer_hosted",
                runtime_target="self_hosted",
                computer_automation={"enabled": False},
                stage="create",
                runtime_targets={"targets": []},
            )

        self.assertIn("self_host_runtime", str(error.exception))
        self.assertIn("not present", str(error.exception))

    def test_normalize_computer_automation_keeps_guarded_mode_owner_approved(self):
        normalized = contract.normalize_computer_automation_config(
            {
                "enabled": True,
                "runtime_class": "local_browser",
                "allowed_domains": ["example.com"],
                "max_concurrent_sessions": 1,
                "daily_budget_usd": 5,
                "monthly_budget_usd": 25,
                "runtime_access_mode": "default_guarded",
                "requires_owner_approval": False,
                "max_session_runtime_seconds": 900,
            }
        )

        self.assertEqual(normalized["runtime_access_mode"], "default_guarded")
        self.assertTrue(normalized["requires_owner_approval"])

    def test_normalize_runtime_access_mode_fails_closed_for_omitted_and_unknown_values(self):
        self.assertEqual(contract.normalize_runtime_access_mode(None), "default_guarded")
        self.assertEqual(contract.normalize_runtime_access_mode("root-god-mode"), "default_guarded")
        self.assertEqual(contract.normalize_runtime_access_mode("autonomous_agent"), "default_guarded")
        self.assertEqual(contract.normalize_runtime_access_mode("autonomous"), "default_guarded")
        self.assertEqual(
            contract.normalize_runtime_access_mode(None, requires_owner_approval=False),
            "default_guarded",
        )

    def test_normalize_runtime_access_mode_supports_custom_without_disabling_approval(self):
        normalized = contract.normalize_computer_automation_config(
            {
                "enabled": True,
                "runtime_class": "local_browser",
                "allowed_domains": ["example.com"],
                "max_concurrent_sessions": 1,
                "daily_budget_usd": 5,
                "monthly_budget_usd": 25,
                "runtime_access_mode": "custom",
                "requires_owner_approval": False,
                "max_session_runtime_seconds": 900,
            }
        )

        self.assertEqual(normalized["runtime_access_mode"], "custom")
        self.assertTrue(normalized["requires_owner_approval"])

    def test_normalize_computer_automation_rejects_studio_full_access_semantics(self):
        guarded = contract.normalize_computer_automation_config(
            {
                "enabled": True,
                "runtime_class": "local_browser",
                "allowed_domains": ["example.com"],
                "max_concurrent_sessions": 1,
                "daily_budget_usd": 5,
                "monthly_budget_usd": 25,
                "requires_owner_approval": False,
                "max_session_runtime_seconds": 900,
            }
        )
        attempted_full_access = contract.normalize_computer_automation_config(
            {
                "enabled": True,
                "runtime_class": "local_browser",
                "allowed_domains": ["example.com"],
                "max_concurrent_sessions": 1,
                "daily_budget_usd": 5,
                "monthly_budget_usd": 25,
                "runtime_access_mode": "full_access",
                "requires_owner_approval": False,
                "max_session_runtime_seconds": 900,
            }
        )

        self.assertEqual(guarded["runtime_access_mode"], "default_guarded")
        self.assertTrue(guarded["requires_owner_approval"])
        self.assertEqual(attempted_full_access["runtime_access_mode"], "default_guarded")
        self.assertTrue(attempted_full_access["requires_owner_approval"])

    def test_validate_mode_capability_matrix_rejects_studio_full_access(self):
        with self.assertRaises(ValueError) as error:
            contract.validate_mode_capability_matrix(
                studio_agent_mode="my_computer_agent",
                runtime_placement="customer_local",
                runtime_target="local",
                computer_automation={
                    "enabled": True,
                    "runtime_class": "local_browser",
                    "allowed_domains": ["example.com"],
                    "max_concurrent_sessions": 1,
                    "daily_budget_usd": 5,
                    "monthly_budget_usd": 25,
                    "runtime_access_mode": "full_access",
                    "requires_owner_approval": False,
                    "max_session_runtime_seconds": 900,
                },
                stage="create",
            )

        self.assertIn("Studio agents cannot use Sage Full Access", str(error.exception))

    def test_marketplace_package_cannot_force_customer_runtime_without_installer_opt_in(self):
        payload = contract.normalize_runtime_supply_contract(
            {
                "supplier": {"kind": "customer"},
                "placement": {"kind": "customer_local"},
                "marketplace_policy": {"visibility": "marketplace"},
            }
        )

        self.assertFalse(payload["marketplace_policy"]["install_eligible"])
        self.assertIn("customer_runtime_requires_installer_opt_in", payload["marketplace_policy"]["install_blockers"])

    def test_cost_aware_scheduler_selects_cheap_available_provider_or_queues(self):
        selected = contract.choose_runtime_provider_for_job(
            [
                {
                    "provider_id": "expensive",
                    "supplier_kind": "empyralis",
                    "placement": "hosted_hardware_pool",
                    "capabilities": ["browser"],
                    "available_slots": 3,
                    "estimated_unit_cost": 4.0,
                },
                {
                    "provider_id": "cheap",
                    "supplier_kind": "empyralis",
                    "placement": "hosted_hardware_pool",
                    "capabilities": ["browser"],
                    "available_slots": 1,
                    "estimated_unit_cost": 1.5,
                },
            ],
            required_placement="hosted_hardware_pool",
            required_capabilities=["browser"],
        )
        self.assertEqual(selected["status"], "selected")
        self.assertEqual(selected["provider"]["provider_id"], "cheap")

        queued = contract.choose_runtime_provider_for_job(
            [
                {
                    "provider_id": "busy",
                    "supplier_kind": "empyralis",
                    "placement": "hosted_hardware_pool",
                    "capabilities": ["browser"],
                    "available_slots": 0,
                    "estimated_unit_cost": 1.0,
                }
            ],
            required_placement="hosted_hardware_pool",
            required_capabilities=["browser"],
        )
        self.assertEqual(queued["status"], "queued")
        self.assertEqual(queued["reason"], "waiting_for_capacity")

    def test_hardware_pool_job_contract_carries_checkpoint_and_preemption_metadata(self):
        payload = contract.build_hardware_pool_job_contract(
            job_id="job-1",
            supplier_id="empyralis",
            hardware_pool_id="home-linux-pool",
            checkpoint_uri="s3://checkpoints/job-1",
            checkpoint_generation=7,
            preemptible=True,
            preemption_deadline_at="2026-05-09T12:00:00Z",
            resume_target="hosted_hardware_pool",
        )

        self.assertTrue(payload["checkpoint"]["enabled"])
        self.assertEqual(payload["checkpoint"]["generation"], 7)
        self.assertTrue(payload["preemption"]["preemptible"])
        self.assertEqual(payload["preemption"]["resume_target"], "hosted_hardware_pool")

    def test_workspace_contract_scopes_agent_customer_and_session_without_raw_customer_id(self):
        payload = contract.build_deployed_agent_workspace_contract(
            tenant_id="tenant/1",
            workspace_id="workspace/1",
            deployed_agent_id="agent/1",
            external_user_id="customer@example.com",
            session_id="session-1",
            base_dir="~/.empyralis/agents",
        )

        self.assertIn("tenant_1/workspace_1/agent_1", payload["workspace_root"])
        self.assertNotIn("customer@example.com", str(payload))
        self.assertFalse(payload["isolation"]["cross_agent_read"])
        self.assertFalse(payload["isolation"]["cross_customer_read"])
        self.assertFalse(payload["isolation"]["sage_memory_access"])


if __name__ == "__main__":
    unittest.main()

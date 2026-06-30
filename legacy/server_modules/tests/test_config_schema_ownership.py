from __future__ import annotations

from server_modules import deployed_agent_config_schema
from server_modules import platform_config_schema
from server_modules import workspace_config_schema


def test_deployed_agent_schema_splits_owner_config_from_operational_state() -> None:
    record = {
        "name": "Store Assistant",
        "persona": "Helpful retail agent",
        "system_prompt": "Use the catalog and return policy.",
        "runtime_target": "cloud",
        "billing_plan": "free",
        "channels": {"telegram": {"enabled": True, "endpoint_key": "store-bot"}},
        "metadata": {
            "monthly_cost_cap_usd": 25.0,
            "memory_enabled": True,
            "provider": "openai",
            "model": "gpt-4o",
            "selected_tool_ids": ["spreadsheet_read", "spreadsheet_append"],
            "current_budget_cycle": {
                "usage_month": "2026-04-01",
                "current_burn_usd": 8.5,
            },
        },
    }

    config = deployed_agent_config_schema.deployed_agent_config_from_record(record)
    state = deployed_agent_config_schema.deployed_agent_operational_state_from_record(record)
    metadata = deployed_agent_config_schema.metadata_from_deployed_agent_config(
        config,
        existing_metadata=record["metadata"],
    )

    assert config.commerce_policy.monthly_cost_cap_usd == 25.0
    assert config.memory_policy.memory_enabled is True
    assert config.provider == "openai"
    assert config.tool_policy.enabled_tools == ["spreadsheet_read", "spreadsheet_append"]
    assert metadata["selected_tool_ids"] == ["spreadsheet_read", "spreadsheet_append"]
    assert state.current_budget_cycle["current_burn_usd"] == 8.5
    assert "current_budget_cycle" not in metadata


def test_deployed_agent_schema_accepts_runtime_supply_contract_without_enabling_automation() -> None:
    config = deployed_agent_config_schema.deployed_agent_config_from_record(
        {
            "name": "Store Assistant",
            "config": {
                "runtime_placement": "hosted_hardware_pool",
                "runtime_supply": {
                    "supplier": {"kind": "empyralis", "id": "home-linux-pool"},
                    "placement": {"kind": "hosted_hardware_pool"},
                    "marketplace_policy": {"visibility": "private"},
                },
                "computer_automation": {"enabled": False},
            },
        }
    )
    metadata = deployed_agent_config_schema.metadata_from_deployed_agent_config(config)

    assert config.runtime_placement == "hosted_hardware_pool"
    assert config.runtime_target == "cloud"
    assert config.runtime_supply["supplier"]["kind"] == "empyralis"
    assert config.runtime_supply["placement"]["kind"] == "hosted_hardware_pool"
    assert config.runtime_supply["computer_automation"]["enabled"] is False
    assert metadata["runtime_supply"]["supplier"]["id"] == "home-linux-pool"


def test_deployed_agent_schema_uses_studio_agent_mode_as_runtime_source_of_truth() -> None:
    config = deployed_agent_config_schema.deployed_agent_config_from_record(
        {
            "name": "Store Assistant",
            "runtime_target": "self_hosted",
            "config": {
                "studio_agent_mode": "text_agent",
                "runtime_placement": "customer_hosted",
                "runtime_supply": {
                    "supplier": {"kind": "customer"},
                    "placement": {"kind": "customer_hosted"},
                },
                "computer_automation": {
                    "enabled": True,
                    "runtime_class": "virtual_browser",
                    "allowed_domains": ["example.com"],
                    "max_concurrent_sessions": 1,
                },
            },
        }
    )
    metadata = deployed_agent_config_schema.metadata_from_deployed_agent_config(config)

    assert config.studio_agent_mode == "text_agent"
    assert config.runtime_placement == "managed_cloud"
    assert config.runtime_target == "cloud"
    assert config.runtime_supply["supplier"]["kind"] == "empyralis"
    assert config.runtime_supply["placement"]["kind"] == "managed_cloud"
    assert config.runtime_supply["computer_automation"]["enabled"] is False
    assert metadata["studio_agent_mode"] == "text_agent"


def test_workspace_policy_schema_normalizes_legacy_payload() -> None:
    config = workspace_config_schema.workspace_policy_config_from_legacy(
        {
            "workspace_id": "ws-1",
            "tenant_id": "tenant-1",
            "capabilities": {"allow": ["runs.read", "runs.read"], "deny": ["runs.write"]},
            "dangerous_action_classes": {"allow": [], "deny": ["shell.exec"]},
            "connectors": {"allow": ["telegram"], "deny": []},
            "machine_enrollment_scope": "tenant",
            "trusted_owner_machine_ids": ["machine-a", "machine-a"],
        }
    )

    assert config.capabilities.allow == ["runs.read"]
    assert config.machine_policy.machine_enrollment_scope == "tenant"
    assert config.machine_policy.trusted_owner_machine_ids == ["machine-a"]
    assert workspace_config_schema.workspace_policy_envelope(config)["schema_version"] >= 1


def test_platform_config_schema_builds_provider_defaults_and_models() -> None:
    config = platform_config_schema.build_platform_provider_catalog_config(
        provider_catalog={
            "openai": {
                "label": "OpenAI",
                "auth": ["api_key"],
                "auth_modes": [{"id": "api_key", "label": "API Key", "secret_required": True}],
                "default_auth_mode": "api_key",
                "default_model": "gpt-4o",
                "models": ["gpt-4o"],
            }
        },
        governance_catalog={
            "openai": {
                "privacy_posture": "Managed API",
                "jurisdiction": "United States",
                "capability_labels": ["Tools"],
            }
        },
        model_catalog={
            "openai": {
                "gpt-4o": {
                    "label": "GPT-4o",
                    "context_window_tokens": 128000,
                    "supports_tools": True,
                    "supports_reasoning": True,
                    "reasoning_levels": ["low", "medium", "high"],
                }
            }
        },
        model_policy_overrides={
            "openai": {
                "gpt-4o": {
                    "enabled": False,
                    "allowed_surfaces": ["sage"],
                    "allowed_payers": ["BYOK"],
                    "platform_paid_allowed": False,
                    "pricing_version": "test-v1",
                    "fallback_allowed": False,
                }
            }
        },
    )

    provider = config.provider("openai")
    models = config.provider_models("openai")
    policy = config.provider_model_policy("openai", "gpt-4o")

    assert provider is not None
    assert provider.default_model == "gpt-4o"
    assert provider.default_auth_mode == "api_key"
    assert models[0].id == "gpt-4o"
    assert models[0].reasoning_levels == ["low", "medium", "high"]
    assert policy is not None
    assert policy.enabled is False
    assert policy.allowed_surfaces == ["sage"]
    assert policy.allowed_payers == ["BYOK"]
    assert policy.platform_paid_allowed is False
    assert policy.pricing_version == "test-v1"

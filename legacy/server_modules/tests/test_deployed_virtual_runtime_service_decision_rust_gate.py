from unittest.mock import patch

import pytest

from server_modules import deployed_agent_virtual_runtime_service


class _Config:
    studio_agent_mode = "cloud_computer_agent"
    runtime_target = "cloud_secure"
    runtime_profile_id = "runtime-profile-1"

    class computer_automation:
        enabled = True
        allowed_domains = ["safe.example"]
        daily_budget_usd = 5
        monthly_budget_usd = 50
        runtime_class = "virtual_browser"
        session_recording_policy = "metadata_only"
        idle_timeout_seconds = 900
        max_session_runtime_seconds = 3600

    class commerce_policy:
        monthly_cost_cap_usd = 100


def _deployed_agent() -> dict:
    return {
        "id": "dagent-1",
        "deployment_state": "active",
        "status": "active",
        "workspace_id": "ws-1",
        "tenant_id": "tenant-1",
    }


def test_build_policy_payload_accepts_canonical_rust_action() -> None:
    with patch.object(
        deployed_agent_virtual_runtime_service.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        return_value={
            "ok": True,
            "decision": "allow",
            "next_action": "build_deployed_agent_virtual_runtime_payload",
        },
    ):
        decision = deployed_agent_virtual_runtime_service._enforce_deployed_virtual_runtime_service_decision(
            operation="build_policy_payload",
            deployed_agent=_deployed_agent(),
            config=_Config(),
            privacy_snapshot={"privacy_contract_valid": True},
            computer_safety_snapshot={"computer_safety_contract_valid": True},
        )

    assert decision["next_action"] == "build_deployed_agent_virtual_runtime_payload"


def test_terminate_cloud_session_wrong_rust_action_blocks() -> None:
    with patch.object(
        deployed_agent_virtual_runtime_service.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        return_value={
            "ok": True,
            "decision": "allow",
            "next_action": "execute_bound_runtime_tool_call",
        },
    ):
        with pytest.raises(RuntimeError, match="unexpected_next_action:execute_bound_runtime_tool_call"):
            deployed_agent_virtual_runtime_service._enforce_deployed_virtual_runtime_service_decision(
                operation="terminate_cloud_session",
                deployed_agent=_deployed_agent(),
                config=_Config(),
                privacy_snapshot={"privacy_contract_valid": True},
                computer_safety_snapshot={"computer_safety_contract_valid": True},
                runtime_session_id="sess-1",
                session_id="sess-1",
            )


def test_build_policy_payload_accepts_canonical_virtual_runtime_action() -> None:
    with patch.object(
        deployed_agent_virtual_runtime_service.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        return_value={
            "ok": True,
            "decision": "allow",
            "next_action": "build_deployed_agent_virtual_runtime_payload",
        },
    ):
        decision = deployed_agent_virtual_runtime_service._enforce_deployed_virtual_runtime_decision(
            operation="build_policy_payload",
            deployed_agent=_deployed_agent(),
            config=_Config(),
            privacy_snapshot={"privacy_contract_valid": True},
            computer_safety_snapshot={"computer_safety_contract_valid": True, "domain_allowlist": ["safe.example"]},
        )

    assert decision["next_action"] == "build_deployed_agent_virtual_runtime_payload"


def test_build_policy_payload_wrong_virtual_runtime_action_blocks() -> None:
    with patch.object(
        deployed_agent_virtual_runtime_service.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        return_value={
            "ok": True,
            "decision": "allow",
            "next_action": "select_virtual_runtime_provider",
        },
    ):
        with pytest.raises(RuntimeError, match="unexpected_next_action:select_virtual_runtime_provider"):
            deployed_agent_virtual_runtime_service._enforce_deployed_virtual_runtime_decision(
                operation="build_policy_payload",
                deployed_agent=_deployed_agent(),
                config=_Config(),
                privacy_snapshot={"privacy_contract_valid": True},
                computer_safety_snapshot={"computer_safety_contract_valid": True, "domain_allowlist": ["safe.example"]},
            )

from unittest.mock import patch

import pytest

from server_modules import control_plane_repository


@pytest.mark.asyncio
async def test_daily_message_quota_consume_blocks_before_repository_write():
    with patch.object(
        control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            await control_plane_repository.consume_deployed_agent_daily_message_quota(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                deployed_agent_id="dagent-1",
                channel_key="telegram",
                external_user_id="external-user-1",
                limit=5,
                usage_day="2026-05-31",
            )

    command, payload = kernel.call_args.args
    assert command == "control-plane-service-decision"
    assert payload["operation"] == "deployed_agent_daily_message_quota_consume"
    assert payload["record_type"] == "deployed_agent_daily_message_usage"
    assert payload["agent_id"] == "dagent-1"
    assert payload["idempotency_key"] == "dagent-1:telegram:external-user-1:2026-05-31:consume"


@pytest.mark.asyncio
async def test_deployed_agent_cost_ledger_blocks_before_repository_write():
    with patch.object(
        control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            await control_plane_repository.record_deployed_agent_monthly_cost_ledger_entry(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                deployed_agent_id="dagent-1",
                run_id="run-1",
                run_status="completed",
                total_tokens=42,
                estimated_cost_usd=0.01,
            )

    command, payload = kernel.call_args.args
    assert command == "control-plane-service-decision"
    assert payload["operation"] == "deployed_agent_cost_ledger_write"
    assert payload["record_type"] == "deployed_agent_monthly_cost_ledger"
    assert payload["run_id"] == "run-1"
    assert payload["agent_id"] == "dagent-1"
    assert payload["idempotency_key"] == "dagent-1:run-1:cost_ledger"


@pytest.mark.asyncio
async def test_workspace_hosted_ai_cost_ledger_blocks_before_repository_write():
    with patch.object(
        control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            await control_plane_repository.record_workspace_hosted_ai_monthly_cost_ledger_entry(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                request_id="request-1",
                total_tokens=42,
                estimated_cost_usd=0.01,
            )

    command, payload = kernel.call_args.args
    assert command == "control-plane-service-decision"
    assert payload["operation"] == "workspace_hosted_ai_cost_ledger_write"
    assert payload["record_type"] == "workspace_hosted_ai_monthly_cost_ledger"
    assert payload["idempotency_key"] == "request-1:hosted_ai_cost"


@pytest.mark.asyncio
async def test_daily_message_quota_consume_unexpected_next_action_blocks_before_repository_write():
    with patch.object(
        control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        return_value={
            "ok": True,
            "decision": "allow",
            "reason": "control_plane_service_operation_allowed",
            "operation": "deployed_agent_daily_message_quota_consume",
            "next_action": "apply_control_plane_destructive_write",
            "mutation_plan": {
                "apply": True,
                "next_action": "apply_control_plane_destructive_write",
            },
        },
    ), patch.object(
        control_plane_repository,
        "_scoped_connection",
        new=pytest.fail,
    ):
        with pytest.raises(RuntimeError, match="unexpected next_action"):
            await control_plane_repository.consume_deployed_agent_daily_message_quota(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                deployed_agent_id="dagent-1",
                channel_key="telegram",
                external_user_id="external-user-1",
                limit=5,
                usage_day="2026-05-31",
            )

from unittest.mock import AsyncMock, patch

import pytest

from server_modules import control_plane_repository


@pytest.mark.asyncio
async def test_workspace_billing_defaults_block_before_repository_write():
    with patch.object(
        control_plane_repository,
        "get_workspace_by_id",
        new=AsyncMock(return_value={"workspace_id": "workspace-1", "tenant_id": "tenant-1"}),
    ), patch.object(
        control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            await control_plane_repository.ensure_workspace_billing_defaults(
                "workspace-1",
                tenant_id="tenant-1",
                billing_email="owner@example.com",
            )

    command, payload = kernel.call_args.args
    assert command == "control-plane-service-decision"
    assert payload["operation"] == "workspace_billing_defaults_write"
    assert payload["record_type"] == "workspace_billing"
    assert payload["workspace_id"] == "workspace-1"
    assert payload["actor_role"] == "system"
    assert payload["idempotency_key"] == "workspace-1:billing_defaults"


@pytest.mark.asyncio
async def test_workspace_billing_plan_update_block_before_repository_write():
    with patch.object(
        control_plane_repository,
        "get_workspace_by_id",
        new=AsyncMock(return_value={"workspace_id": "workspace-1", "tenant_id": "tenant-1"}),
    ), patch.object(
        control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            await control_plane_repository.update_workspace_billing_plan(
                "workspace-1",
                tenant_id="tenant-1",
                plan_id="pro",
            )

    command, payload = kernel.call_args.args
    assert command == "control-plane-service-decision"
    assert payload["operation"] == "workspace_billing_plan_update"
    assert payload["record_type"] == "workspace_billing_subscription"
    assert payload["target_status"] == "pro"
    assert payload["idempotency_key"] == "workspace-1:billing_plan:pro"


@pytest.mark.asyncio
async def test_workspace_billing_subscription_write_block_before_repository_write():
    def _raise_on_service_gate(command, payload, **_kwargs):
        if command == "control-plane-service-decision":
            raise RuntimeError("rust denied")
        return {
            "ok": True,
            "decision": "allow",
            "reason": "control_plane_operation_allowed",
            "operation": "upsert",
            "next_action": "upsert_record",
        }

    with patch.object(
        control_plane_repository,
        "get_workspace_by_id",
        new=AsyncMock(return_value={"workspace_id": "workspace-1", "tenant_id": "tenant-1"}),
    ), patch.object(
        control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=_raise_on_service_gate,
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            await control_plane_repository.upsert_workspace_billing_subscription(
                "workspace-1",
                tenant_id="tenant-1",
                plan_id="pro",
                status="active",
                provider_subscription_id="sub-1",
            )

    command, payload = kernel.call_args.args
    assert command == "control-plane-service-decision"
    assert payload["operation"] == "workspace_billing_subscription_write"
    assert payload["record_type"] == "workspace_billing_subscription"
    assert payload["target_status"] == "active"
    assert payload["idempotency_key"] == "workspace-1:billing_subscription:sub-1"


@pytest.mark.asyncio
async def test_workspace_billing_defaults_unexpected_next_action_blocks_before_repository_write():
    with patch.object(
        control_plane_repository,
        "get_workspace_by_id",
        new=AsyncMock(return_value={"workspace_id": "workspace-1", "tenant_id": "tenant-1"}),
    ), patch.object(
        control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        return_value={
            "ok": True,
            "decision": "allow",
            "reason": "control_plane_service_operation_allowed",
            "operation": "workspace_billing_defaults_write",
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
            await control_plane_repository.ensure_workspace_billing_defaults(
                "workspace-1",
                tenant_id="tenant-1",
                billing_email="owner@example.com",
            )

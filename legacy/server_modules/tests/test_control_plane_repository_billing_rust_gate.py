from unittest.mock import AsyncMock, Mock, patch

import pytest

from server_modules import control_plane_repository


@pytest.mark.asyncio
async def test_upsert_workspace_billing_account_unexpected_next_action_blocks_before_storage():
    with patch.object(
        control_plane_repository,
        "get_workspace_by_id",
        new=AsyncMock(return_value={"tenant_id": "tenant-1"}),
    ), patch.object(
        control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        return_value={
            "ok": True,
            "decision": "allow",
            "reason": "control_plane_service_operation_allowed",
            "operation": "workspace_billing_account_write",
            "next_action": "apply_control_plane_destructive_write",
            "mutation_plan": {
                "apply": True,
                "next_action": "apply_control_plane_destructive_write",
            },
        },
    ), patch.object(
        control_plane_repository,
        "_scoped_connection",
        new=Mock(),
    ) as scoped_connection:
        with pytest.raises(RuntimeError, match="unexpected next_action"):
            await control_plane_repository.upsert_workspace_billing_account(
                "ws-1",
                billing_email="owner@example.com",
            )

    scoped_connection.assert_not_called()


@pytest.mark.asyncio
async def test_upsert_workspace_billing_subscription_unexpected_next_action_blocks_before_storage():
    with patch.object(
        control_plane_repository,
        "get_workspace_by_id",
        new=AsyncMock(return_value={"tenant_id": "tenant-1"}),
    ), patch.object(
        control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=lambda command, payload, **_kwargs: (
            {
                "ok": True,
                "decision": "allow",
                "reason": "control_plane_service_operation_allowed",
                "operation": "workspace_billing_subscription_write",
                "next_action": "apply_control_plane_write",
                "mutation_plan": {
                    "apply": True,
                    "next_action": "apply_control_plane_write",
                },
            }
            if command == "control-plane-service-decision"
            else {
                "ok": True,
                "decision": "allow",
                "reason": "control_plane_operation_allowed",
                "operation": "upsert",
                "next_action": "read_record",
            }
        ),
    ), patch.object(
        control_plane_repository,
        "_scoped_connection",
        new=Mock(),
    ) as scoped_connection:
        with pytest.raises(RuntimeError, match="unexpected next_action"):
            await control_plane_repository.upsert_workspace_billing_subscription(
                "ws-1",
                plan_id="pro",
                status="active",
            )

    scoped_connection.assert_not_called()


@pytest.mark.asyncio
async def test_upsert_workspace_billing_subscription_record_gate_blocks_before_storage():
    with patch.object(
        control_plane_repository,
        "get_workspace_by_id",
        new=AsyncMock(return_value={"tenant_id": "tenant-1"}),
    ), patch.object(
        control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=lambda command, payload, **_kwargs: (
            {
                "ok": True,
                "decision": "allow",
                "reason": "control_plane_service_operation_allowed",
                "operation": "workspace_billing_subscription_write",
                "next_action": "apply_control_plane_write",
                "mutation_plan": {
                    "apply": True,
                    "next_action": "apply_control_plane_write",
                },
            }
            if command == "control-plane-service-decision"
            else {
                "ok": True,
                "decision": "allow",
                "reason": "control_plane_operation_allowed",
                "operation": "upsert",
                "next_action": "read_record",
            }
        ),
    ), patch.object(
        control_plane_repository,
        "_scoped_connection",
        new=Mock(),
    ) as scoped_connection:
        with pytest.raises(RuntimeError, match="unexpected next_action"):
            await control_plane_repository.upsert_workspace_billing_subscription(
                "ws-1",
                plan_id="pro",
                status="active",
            )

    scoped_connection.assert_not_called()

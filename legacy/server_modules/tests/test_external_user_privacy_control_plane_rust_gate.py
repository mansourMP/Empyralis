from unittest.mock import patch

import pytest

from server_modules import control_plane_repository


@pytest.mark.asyncio
async def test_external_user_privacy_request_blocks_before_repository_write():
    with patch.object(
        control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            await control_plane_repository.upsert_external_user_privacy_request(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                deployed_agent_id="dagent-1",
                channel_key="telegram",
                external_user_id="external-user-1",
                request_source="telegram",
            )

    command, payload = kernel.call_args.args
    assert command == "control-plane-service-decision"
    assert payload["operation"] == "external_user_privacy_request_write"
    assert payload["record_type"] == "external_user_privacy_request"
    assert payload["tenant_id"] == "tenant-1"
    assert payload["workspace_id"] == "workspace-1"
    assert payload["agent_id"] == "dagent-1"
    assert payload["idempotency_key"] == "dagent-1:telegram:external-user-1:requested"


@pytest.mark.asyncio
async def test_external_user_privacy_request_unexpected_next_action_blocks_before_repository_write():
    with patch.object(
        control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        return_value={
            "ok": True,
            "decision": "allow",
            "reason": "control_plane_service_operation_allowed",
            "operation": "external_user_privacy_request_write",
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
            await control_plane_repository.upsert_external_user_privacy_request(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                deployed_agent_id="dagent-1",
                channel_key="telegram",
                external_user_id="external-user-1",
                request_source="telegram",
            )


@pytest.mark.asyncio
async def test_external_user_privacy_delete_blocks_before_repository_write():
    with patch.object(
        control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            await control_plane_repository.delete_deployed_agent_external_user_data(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                deployed_agent_id="dagent-1",
                channel_key="telegram",
                external_user_id="external-user-1",
            )

    command, payload = kernel.call_args.args
    assert command == "control-plane-service-decision"
    assert payload["operation"] == "external_user_privacy_delete"
    assert payload["record_type"] == "external_user_privacy_delete"
    assert payload["actor_role"] == "system"
    assert payload["agent_id"] == "dagent-1"
    assert payload["idempotency_key"] == "dagent-1:telegram:external-user-1:delete"


@pytest.mark.asyncio
async def test_workspace_scope_data_delete_blocks_before_repository_write():
    with patch.object(
        control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            await control_plane_repository.delete_workspace_scope_data(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
            )

    command, payload = kernel.call_args.args
    assert command == "control-plane-service-decision"
    assert payload["operation"] == "workspace_scope_data_delete"
    assert payload["record_type"] == "workspace_scope_data"
    assert payload["actor_role"] == "system"
    assert payload["idempotency_key"] == "workspace-1:scope_delete"


@pytest.mark.asyncio
async def test_workspace_scope_data_delete_unexpected_next_action_blocks_before_repository_write():
    with patch.object(
        control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        return_value={
            "ok": True,
            "decision": "allow",
            "reason": "control_plane_service_operation_allowed",
            "operation": "workspace_scope_data_delete",
            "next_action": "apply_control_plane_write",
            "mutation_plan": {
                "apply": True,
                "next_action": "apply_control_plane_write",
            },
        },
    ), patch.object(
        control_plane_repository,
        "_scoped_connection",
        new=pytest.fail,
    ):
        with pytest.raises(RuntimeError, match="unexpected next_action"):
            await control_plane_repository.delete_workspace_scope_data(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
            )


@pytest.mark.asyncio
async def test_governance_hold_release_unexpected_next_action_blocks_before_repository_write():
    with patch.object(
        control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        return_value={
            "ok": True,
            "decision": "allow",
            "reason": "control_plane_service_operation_allowed",
            "operation": "governance_hold_release",
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
            await control_plane_repository.release_governance_hold(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                scope_type="workspace",
                hold_key="legal",
            )

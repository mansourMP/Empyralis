from unittest.mock import patch

import pytest

from server_modules import control_plane_repository


@pytest.mark.asyncio
async def test_create_deployed_agent_blocks_before_repository_write():
    with patch.object(
        control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            await control_plane_repository.create_deployed_agent(
                tenant_id="tenant-1",
                owner_workspace_id="workspace-1",
                backing_install_id="install-1",
                created_by_user_id="user-1",
                name="Agent One",
            )

    command, payload = kernel.call_args.args
    assert command == "control-plane-service-decision"
    assert payload["operation"] == "create"
    assert payload["record_type"] == "deployed_agent"
    assert payload["tenant_id"] == "tenant-1"
    assert payload["workspace_id"] == "workspace-1"
    assert payload["actor_id"] == "user-1"
    assert payload["agent_id"].startswith("dagent_")
    assert payload["target_status"] == "draft"


@pytest.mark.asyncio
async def test_update_deployed_agent_blocks_before_repository_write():
    with patch.object(
        control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            await control_plane_repository.update_deployed_agent(
                "dagent_1",
                tenant_id="tenant-1",
                owner_workspace_id="workspace-1",
                updates={
                    "deployment_state": "live",
                },
            )

    command, payload = kernel.call_args.args
    assert command == "control-plane-service-decision"
    assert payload["operation"] == "status_transition"
    assert payload["record_type"] == "deployed_agent"
    assert payload["tenant_id"] == "tenant-1"
    assert payload["workspace_id"] == "workspace-1"
    assert payload["actor_id"] == "system"
    assert payload["agent_id"] == "dagent_1"
    assert payload["current_status"] == ""
    assert payload["next_status"] == "live"


@pytest.mark.asyncio
async def test_update_deployed_agent_metadata_blocks_before_repository_write():
    with patch.object(
        control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            await control_plane_repository.update_deployed_agent(
                "dagent_1",
                tenant_id="tenant-1",
                owner_workspace_id="workspace-1",
                updates={
                    "name": "Agent One",
                    "runtime_target": "self_hosted",
                },
            )

    command, payload = kernel.call_args.args
    assert command == "control-plane-service-decision"
    assert payload["operation"] == "update"
    assert payload["record_type"] == "deployed_agent"
    assert payload["tenant_id"] == "tenant-1"
    assert payload["workspace_id"] == "workspace-1"
    assert payload["actor_id"] == "system"
    assert payload["agent_id"] == "dagent_1"
    assert payload["updated_fields"] == ["name", "runtime_target"]


@pytest.mark.asyncio
async def test_create_deployed_agent_unexpected_next_action_blocks_before_repository_write():
    with patch.object(
        control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        return_value={
            "ok": True,
            "decision": "allow",
            "reason": "control_plane_service_operation_allowed",
            "operation": "create",
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
            await control_plane_repository.create_deployed_agent(
                tenant_id="tenant-1",
                owner_workspace_id="workspace-1",
                backing_install_id="install-1",
                created_by_user_id="user-1",
                name="Agent One",
            )


@pytest.mark.asyncio
async def test_update_deployed_agent_status_transition_unexpected_next_action_blocks_before_repository_write():
    with patch.object(
        control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        return_value={
            "ok": True,
            "decision": "allow",
            "reason": "control_plane_service_operation_allowed",
            "operation": "status_transition",
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
            await control_plane_repository.update_deployed_agent(
                "dagent_1",
                tenant_id="tenant-1",
                owner_workspace_id="workspace-1",
                updates={
                    "deployment_state": "live",
                },
            )


@pytest.mark.asyncio
async def test_update_deployed_agent_metadata_unexpected_next_action_blocks_before_repository_write():
    with patch.object(
        control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=[
            {
                "ok": True,
                "decision": "allow",
                "reason": "control_plane_service_operation_allowed",
                "operation": "update",
                "next_action": "apply_control_plane_destructive_write",
                "mutation_plan": {
                    "apply": True,
                    "next_action": "apply_control_plane_destructive_write",
                },
            }
        ],
    ), patch.object(
        control_plane_repository,
        "_scoped_connection",
        new=pytest.fail,
    ):
        with pytest.raises(RuntimeError, match="unexpected next_action"):
            await control_plane_repository.update_deployed_agent(
                "dagent_1",
                tenant_id="tenant-1",
                owner_workspace_id="workspace-1",
                updates={
                    "name": "Agent One",
                    "runtime_target": "self_hosted",
                },
            )

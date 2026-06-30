from unittest.mock import patch

import pytest

from server_modules import control_plane_repository


@pytest.mark.asyncio
async def test_workspace_tenant_binding_blocks_before_repository_write():
    with patch.object(
        control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            await control_plane_repository.ensure_workspace_tenant_binding(
                workspace_id="workspace-1",
                tenant_id="tenant-1",
            )

    command, payload = kernel.call_args.args
    assert command == "control-plane-service-decision"
    assert payload["operation"] == "workspace_tenant_binding_ensure"
    assert payload["record_type"] == "workspace_tenant_binding"
    assert payload["tenant_id"] == "tenant-1"
    assert payload["workspace_id"] == "workspace-1"
    assert payload["actor_id"] == "tenant-binding"
    assert payload["idempotency_key"] == "tenant-1:workspace-1"


@pytest.mark.asyncio
async def test_user_profile_update_blocks_before_repository_write():
    with patch.object(
        control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            await control_plane_repository.update_user_profile(
                user_id="user-1",
                display_name="New Name",
                avatar_url="https://example.com/avatar.png",
            )

    command, payload = kernel.call_args.args
    assert command == "control-plane-service-decision"
    assert payload["operation"] == "user_profile_update"
    assert payload["record_type"] == "user_profile"
    assert payload["tenant_id"] == "user-profile"
    assert payload["workspace_id"] == "user-profile"
    assert payload["actor_id"] == "user-1"
    assert payload["target_actor_id"] == "user-1"
    assert payload["idempotency_key"] == "user-1"


@pytest.mark.asyncio
async def test_workspace_tenant_binding_unexpected_next_action_blocks_before_repository_write():
    with patch.object(
        control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        return_value={
            "decision": "allow",
            "next_action": "apply_control_plane_destructive_write",
            "mutation_plan": {
                "apply": True,
                "next_action": "apply_control_plane_destructive_write",
            },
        },
    ):
        with pytest.raises(RuntimeError, match="unexpected next_action"):
            await control_plane_repository.ensure_workspace_tenant_binding(
                workspace_id="workspace-1",
                tenant_id="tenant-1",
            )

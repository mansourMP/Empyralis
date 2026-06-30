from unittest.mock import patch

import pytest

from server_modules import control_plane_repository


@pytest.mark.asyncio
async def test_workspace_membership_update_blocks_before_repository_write():
    with patch.object(
        control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            await control_plane_repository.ensure_workspace_membership(
                user_id="user-1",
                email="user@example.com",
                display_name="User",
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                role="member",
            )

    command, payload = kernel.call_args.args
    assert command == "control-plane-service-decision"
    assert payload["operation"] == "membership_update"
    assert payload["record_type"] == "workspace_membership"
    assert payload["tenant_id"] == "tenant-1"
    assert payload["workspace_id"] == "workspace-1"
    assert payload["actor_id"] == "user-1"
    assert payload["target_actor_id"] == "user-1"
    assert payload["idempotency_key"] == "workspace-1:user-1"


@pytest.mark.asyncio
async def test_workspace_invite_accept_blocks_before_repository_write():
    with patch.object(
        control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            await control_plane_repository.accept_workspace_invite(
                invite_id="invite-1",
                accepted_by_user_id="user-1",
            )

    command, payload = kernel.call_args.args
    assert command == "control-plane-service-decision"
    assert payload["operation"] == "invite_accept"
    assert payload["record_type"] == "workspace_member_invite"
    assert payload["actor_id"] == "user-1"
    assert payload["target_actor_id"] == "user-1"
    assert payload["idempotency_key"] == "invite-1"


@pytest.mark.asyncio
async def test_workspace_invite_revoke_blocks_before_repository_write():
    with patch.object(
        control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            await control_plane_repository.revoke_workspace_invite("invite-1")

    command, payload = kernel.call_args.args
    assert command == "control-plane-service-decision"
    assert payload["operation"] == "invite_revoke"
    assert payload["record_type"] == "workspace_member_invite"
    assert payload["actor_id"] == "invite-revoker"
    assert payload["idempotency_key"] == "invite-1"


@pytest.mark.asyncio
async def test_workspace_membership_update_unexpected_next_action_blocks_before_repository_write():
    with patch.object(
        control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        return_value={
            "ok": True,
            "decision": "allow",
            "reason": "control_plane_service_operation_allowed",
            "operation": "membership_update",
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
            await control_plane_repository.ensure_workspace_membership(
                user_id="user-1",
                email="user@example.com",
                display_name="User",
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                role="member",
            )


@pytest.mark.asyncio
async def test_workspace_membership_remove_unexpected_next_action_blocks_before_repository_write():
    with patch.object(
        control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        return_value={
            "ok": True,
            "decision": "allow",
            "reason": "control_plane_service_operation_allowed",
            "operation": "membership_remove",
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
            await control_plane_repository.remove_workspace_membership(
                user_id="user-1",
                workspace_id="workspace-1",
            )

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock, patch

import pytest

from server_modules import control_plane_repository


@pytest.mark.asyncio
async def test_create_workspace_invite_unexpected_next_action_blocks_before_storage():
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
            "operation": "invite_create",
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
            await control_plane_repository.create_workspace_invite(
                workspace_id="ws-1",
                email="person@example.com",
                role="member",
                invited_by_user_id="owner-1",
            )

    scoped_connection.assert_not_called()


@pytest.mark.asyncio
async def test_revoke_workspace_invite_existing_revoked_record_skips_update():
    fake_connection = Mock()
    fake_connection.fetchrow = AsyncMock(return_value={"id": "invite-1", "status": "revoked"})
    fake_connection.execute = AsyncMock()

    @asynccontextmanager
    async def fake_scoped_connection(*args, **kwargs):
        yield fake_connection

    with patch.object(
        control_plane_repository,
        "_scoped_connection",
        new=fake_scoped_connection,
    ), patch.object(
        control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        return_value={
            "ok": True,
            "decision": "allow",
            "reason": "control_plane_service_operation_allowed",
            "operation": "invite_revoke",
            "next_action": "return_existing_control_plane_record",
            "mutation_plan": {
                "apply": False,
                "next_action": "return_existing_control_plane_record",
            },
        },
    ):
        result = await control_plane_repository.revoke_workspace_invite("invite-1")

    assert result["status"] == "revoked"
    fake_connection.execute.assert_not_awaited()

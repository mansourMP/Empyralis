from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest

from server_modules import control_plane_repository


class _FakeConnection:
    async def fetchrow(self, *_args, **_kwargs):
        return {
            "id": "ws-1",
            "tenant_id": "tenant-1",
            "workspace_id": "ws-1",
            "slug": "workspace-1",
            "name": "Workspace",
            "workspace_type": "personal",
            "status": "active",
            "created_by_user_id": "owner-1",
            "metadata": {},
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }

    async def execute(self, *_args, **_kwargs):
        pytest.fail("workspace update persistence should not run")


@asynccontextmanager
async def _fake_scoped_connection(**_kwargs):
    yield _FakeConnection()


@pytest.mark.asyncio
async def test_workspace_update_blocks_before_repository_write():
    with patch.object(
        control_plane_repository,
        "_scoped_connection",
        new=_fake_scoped_connection,
    ), patch.object(
        control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            await control_plane_repository.update_workspace_profile(
                "ws-1",
                {
                    "name": "New Name",
                    "actor_id": "owner-1",
                    "actor_role": "admin",
                },
            )

    command, payload = kernel.call_args.args
    assert command == "control-plane-service-decision"
    assert payload["operation"] == "workspace_update"
    assert payload["tenant_id"] == "tenant-1"
    assert payload["workspace_id"] == "ws-1"
    assert payload["actor_id"] == "owner-1"
    assert payload["actor_role"] == "admin"


@pytest.mark.asyncio
async def test_workspace_update_unexpected_next_action_blocks_before_repository_write():
    with patch.object(
        control_plane_repository,
        "_scoped_connection",
        new=_fake_scoped_connection,
    ), patch.object(
        control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        return_value={
            "ok": True,
            "decision": "allow",
            "reason": "control_plane_service_operation_allowed",
            "operation": "workspace_update",
            "next_action": "apply_control_plane_destructive_write",
            "mutation_plan": {
                "apply": True,
                "next_action": "apply_control_plane_destructive_write",
            },
        },
    ):
        with pytest.raises(RuntimeError, match="unexpected next_action"):
            await control_plane_repository.update_workspace_profile(
                "ws-1",
                {
                    "name": "New Name",
                    "actor_id": "owner-1",
                    "actor_role": "admin",
                },
            )

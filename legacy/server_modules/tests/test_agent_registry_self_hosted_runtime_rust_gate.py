from unittest.mock import AsyncMock, patch

import pytest

from server_modules import agent_registry_repository


class _FakePool:
    def __init__(self) -> None:
        self.execute = AsyncMock(return_value="UPDATE 1")


def _profile(*, queue=None, enrollment=None):
    metadata = {
        "owner_approved": True,
        "runtime_node_id": "node-1",
        "node_kind": "linux_server",
        "node_session_token_hash": agent_registry_repository._token_hash("node-token"),
        "node_command_queue": list(queue or []),
    }
    if enrollment is not None:
        metadata["enrollment"] = dict(enrollment)
    return {
        "id": "rprof-1",
        "tenant_id": "tenant-1",
        "workspace_id": "workspace-1",
        "runtime_id": "node-1",
        "runtime_class": "self_hosted_business_node",
        "supported_capabilities": [],
        "metadata": metadata,
    }


@pytest.mark.asyncio
async def test_self_hosted_enrollment_intent_blocks_before_profile_insert():
    pool = _FakePool()
    with (
        patch(
            "server_modules.agent_registry_repository.control_plane_repository.ensure_control_plane_schema",
            new=AsyncMock(return_value=pool),
        ),
        patch.object(
            agent_registry_repository.control_plane_repository.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=RuntimeError("rust denied"),
        ) as kernel,
    ):
        with pytest.raises(RuntimeError, match="rust denied"):
            await agent_registry_repository.create_self_hosted_runtime_profile_enrollment_intent(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                owner_user_id="owner-1",
                label="Node",
                node_kind="linux_server",
            )

    command, payload = kernel.call_args.args
    assert command == "control-plane-service-decision"
    assert payload["operation"] == "self_hosted_enrollment_intent_create"
    assert payload["record_type"] == "runtime_profile"
    assert payload["actor_id"] == "owner-1"
    assert not pool.execute.await_args_list


@pytest.mark.asyncio
async def test_self_hosted_enrollment_intent_unexpected_next_action_blocks_before_profile_insert():
    pool = _FakePool()
    with (
        patch(
            "server_modules.agent_registry_repository.control_plane_repository.ensure_control_plane_schema",
            new=AsyncMock(return_value=pool),
        ),
        patch.object(
            agent_registry_repository.control_plane_repository.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "decision": "allow",
                "next_action": "apply_control_plane_destructive_write",
                "mutation_plan": {
                    "apply": True,
                    "next_action": "apply_control_plane_destructive_write",
                },
            },
        ) as kernel,
    ):
        with pytest.raises(RuntimeError, match="unexpected next_action"):
            await agent_registry_repository.create_self_hosted_runtime_profile_enrollment_intent(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                owner_user_id="owner-1",
                label="Node",
                node_kind="linux_server",
            )

    assert kernel.call_args.args[1]["operation"] == "self_hosted_enrollment_intent_create"
    assert not pool.execute.await_args_list


@pytest.mark.asyncio
async def test_self_hosted_runtime_enroll_blocks_before_profile_update():
    enrollment = {
        "token_hash": agent_registry_repository._token_hash("enroll-token"),
        "expires_at": "2099-01-01T00:00:00Z",
        "issued_for_workspace_id": "workspace-1",
    }
    with (
        patch("server_modules.agent_registry_repository.get_runtime_profile", new=AsyncMock(return_value=_profile(enrollment=enrollment))),
        patch.object(
            agent_registry_repository.control_plane_repository.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=RuntimeError("rust denied"),
        ) as kernel,
    ):
        with pytest.raises(RuntimeError, match="rust denied"):
            await agent_registry_repository.enroll_self_hosted_runtime_profile(
                runtime_profile_id="rprof-1",
                enrollment_token="enroll-token",
                public_key="pk-node-1",
            )

    assert kernel.call_args.args[1]["operation"] == "self_hosted_runtime_enroll"


@pytest.mark.asyncio
async def test_self_hosted_runtime_approve_blocks_before_profile_update():
    with (
        patch("server_modules.agent_registry_repository.get_runtime_profile", new=AsyncMock(return_value=_profile())),
        patch.object(
            agent_registry_repository.control_plane_repository.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=RuntimeError("rust denied"),
        ) as kernel,
    ):
        with pytest.raises(RuntimeError, match="rust denied"):
            await agent_registry_repository.approve_self_hosted_runtime_profile(
                runtime_profile_id="rprof-1",
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                approved_by_user_id="owner-1",
            )

    assert kernel.call_args.args[1]["operation"] == "self_hosted_runtime_approve"


@pytest.mark.asyncio
async def test_self_hosted_runtime_heartbeat_blocks_before_profile_update():
    with (
        patch("server_modules.agent_registry_repository.get_runtime_profile", new=AsyncMock(return_value=_profile())),
        patch.object(
            agent_registry_repository.control_plane_repository.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=RuntimeError("rust denied"),
        ) as kernel,
    ):
        with pytest.raises(RuntimeError, match="rust denied"):
            await agent_registry_repository.resolve_self_hosted_runtime_heartbeat(
                runtime_profile_id="rprof-1",
                node_session_token="node-token",
            )

    assert kernel.call_args.args[1]["operation"] == "self_hosted_runtime_heartbeat"


@pytest.mark.asyncio
async def test_self_hosted_command_enqueue_blocks_before_queue_update():
    with (
        patch("server_modules.agent_registry_repository.get_runtime_profile", new=AsyncMock(return_value=_profile())),
        patch.object(
            agent_registry_repository.control_plane_repository.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=RuntimeError("rust denied"),
        ) as kernel,
    ):
        with pytest.raises(RuntimeError, match="rust denied"):
            await agent_registry_repository.enqueue_self_hosted_runtime_command(
                runtime_profile_id="rprof-1",
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                runtime_node_id="node-1",
                agent_id="agent-1",
                command_type="runtime_action",
                command_payload={"action": "open_url"},
            )

    assert kernel.call_args.args[1]["operation"] == "self_hosted_command_enqueue"


@pytest.mark.asyncio
async def test_self_hosted_command_claim_blocks_before_queue_update():
    queue = [{
        "id": "shcmd-1",
        "workspace_id": "workspace-1",
        "runtime_node_id": "node-1",
        "agent_id": "agent-1",
        "command_type": "runtime_action",
        "command_payload": {},
        "state": "queued",
        "created_at": "2026-01-01T00:00:00Z",
        "expires_at": "2099-01-01T00:00:00Z",
    }]
    with (
        patch("server_modules.agent_registry_repository.get_runtime_profile", new=AsyncMock(return_value=_profile(queue=queue))),
        patch.object(
            agent_registry_repository.control_plane_repository.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=RuntimeError("rust denied"),
        ) as kernel,
    ):
        with pytest.raises(RuntimeError, match="rust denied"):
            await agent_registry_repository.claim_self_hosted_runtime_commands(
                runtime_profile_id="rprof-1",
                node_session_token="node-token",
            )

    assert kernel.call_args.args[1]["operation"] == "self_hosted_command_claim"


@pytest.mark.asyncio
async def test_self_hosted_command_complete_blocks_before_queue_update():
    queue = [{
        "id": "shcmd-1",
        "workspace_id": "workspace-1",
        "runtime_node_id": "node-1",
        "agent_id": "agent-1",
        "command_type": "runtime_action",
        "command_payload": {},
        "state": "claimed",
        "created_at": "2026-01-01T00:00:00Z",
        "expires_at": "2099-01-01T00:00:00Z",
    }]
    with (
        patch("server_modules.agent_registry_repository.get_runtime_profile", new=AsyncMock(return_value=_profile(queue=queue))),
        patch.object(
            agent_registry_repository.control_plane_repository.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=RuntimeError("rust denied"),
        ) as kernel,
    ):
        with pytest.raises(RuntimeError, match="rust denied"):
            await agent_registry_repository.complete_self_hosted_runtime_command(
                runtime_profile_id="rprof-1",
                node_session_token="node-token",
                command_id="shcmd-1",
                status="completed",
            )

    assert kernel.call_args.args[1]["operation"] == "self_hosted_command_complete"


@pytest.mark.asyncio
async def test_self_hosted_command_complete_unexpected_next_action_blocks_before_queue_update():
    queue = [{
        "id": "shcmd-1",
        "workspace_id": "workspace-1",
        "runtime_node_id": "node-1",
        "agent_id": "agent-1",
        "command_type": "runtime_action",
        "command_payload": {},
        "state": "claimed",
        "created_at": "2026-01-01T00:00:00Z",
        "expires_at": "2099-01-01T00:00:00Z",
    }]
    with (
        patch("server_modules.agent_registry_repository.get_runtime_profile", new=AsyncMock(return_value=_profile(queue=queue))),
        patch.object(
            agent_registry_repository.control_plane_repository,
            "ensure_control_plane_schema",
            new=AsyncMock(return_value=_FakePool()),
        ) as ensure_schema,
        patch.object(
            agent_registry_repository.control_plane_repository.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "decision": "allow",
                "next_action": "apply_control_plane_destructive_write",
                "mutation_plan": {
                    "apply": True,
                    "next_action": "apply_control_plane_destructive_write",
                },
            },
        ) as kernel,
    ):
        with pytest.raises(RuntimeError, match="unexpected next_action"):
            await agent_registry_repository.complete_self_hosted_runtime_command(
                runtime_profile_id="rprof-1",
                node_session_token="node-token",
                command_id="shcmd-1",
                status="completed",
            )

    assert kernel.call_args.args[1]["operation"] == "self_hosted_command_complete"
    assert not ensure_schema.await_args_list

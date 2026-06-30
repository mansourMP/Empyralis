from unittest.mock import patch

import pytest

from server_modules import control_plane_repository


@pytest.mark.asyncio
async def test_agent_thread_ensure_blocks_before_repository_write():
    with patch.object(
        control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            await control_plane_repository.ensure_agent_thread(
                thread_id="thread-1",
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                owner_user_id="user-1",
                master_agent_install_id="install-1",
                channel="web",
            )

    command, payload = kernel.call_args.args
    assert command == "control-plane-service-decision"
    assert payload["operation"] == "agent_thread_ensure"
    assert payload["record_type"] == "agent_thread"
    assert payload["tenant_id"] == "tenant-1"
    assert payload["workspace_id"] == "workspace-1"
    assert payload["actor_id"] == "user-1"
    assert payload["thread_id"] == "thread-1"
    assert payload["idempotency_key"] == "thread-1"


@pytest.mark.asyncio
async def test_agent_session_upsert_blocks_before_repository_write():
    with patch.object(
        control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            await control_plane_repository.upsert_agent_session(
                session_id="session-1",
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                thread_id="thread-1",
                channel="web",
                actor={"id": "user-1"},
                status="active",
            )

    command, payload = kernel.call_args.args
    assert command == "control-plane-service-decision"
    assert payload["operation"] == "agent_session_upsert"
    assert payload["record_type"] == "agent_session"
    assert payload["tenant_id"] == "tenant-1"
    assert payload["workspace_id"] == "workspace-1"
    assert payload["actor_id"] == "user-1"
    assert payload["session_id"] == "session-1"
    assert payload["thread_id"] == "thread-1"
    assert payload["target_status"] == "active"


@pytest.mark.asyncio
async def test_agent_session_terminate_blocks_before_repository_write():
    with patch.object(
        control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            await control_plane_repository.terminate_agent_session(
                "session-1",
                tenant_id="tenant-1",
                workspace_id="workspace-1",
            )

    command, payload = kernel.call_args.args
    assert command == "control-plane-service-decision"
    assert payload["operation"] == "agent_session_terminate"
    assert payload["record_type"] == "agent_session"
    assert payload["tenant_id"] == "tenant-1"
    assert payload["workspace_id"] == "workspace-1"
    assert payload["actor_id"] == "session-terminator"
    assert payload["session_id"] == "session-1"
    assert payload["target_status"] == "terminated"


@pytest.mark.asyncio
async def test_agent_turn_upsert_blocks_before_repository_write():
    with patch.object(
        control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            await control_plane_repository.upsert_agent_turn(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                thread_id="thread-1",
                session_id="session-1",
                role="assistant",
                content="done",
                actor={"id": "agent-1"},
                run_id="run-1",
                request_id="request-1",
                turn_id="turn-1",
            )

    command, payload = kernel.call_args.args
    assert command == "control-plane-service-decision"
    assert payload["operation"] == "agent_turn_upsert"
    assert payload["record_type"] == "agent_turn"
    assert payload["tenant_id"] == "tenant-1"
    assert payload["workspace_id"] == "workspace-1"
    assert payload["actor_id"] == "agent-1"
    assert payload["thread_id"] == "thread-1"
    assert payload["run_id"] == "run-1"
    assert payload["idempotency_key"] == "request-1"


@pytest.mark.asyncio
async def test_agent_turn_transcript_event_append_blocks_before_repository_write():
    with patch.object(
        control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            await control_plane_repository.append_agent_turn_transcript_event(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                thread_id="thread-1",
                transcript_event={"type": "tool.result"},
                request_id="request-1",
                trace_id="trace-1",
                run_id="run-1",
            )

    command, payload = kernel.call_args.args
    assert command == "control-plane-service-decision"
    assert payload["operation"] == "agent_turn_transcript_event_append"
    assert payload["record_type"] == "agent_turn"
    assert payload["tenant_id"] == "tenant-1"
    assert payload["workspace_id"] == "workspace-1"
    assert payload["actor_id"] == "run-1"
    assert payload["thread_id"] == "thread-1"
    assert payload["run_id"] == "run-1"
    assert payload["idempotency_key"] == "trace-1"


@pytest.mark.asyncio
async def test_agent_thread_ensure_unexpected_next_action_blocks_before_repository_write():
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
            await control_plane_repository.ensure_agent_thread(
                thread_id="thread-1",
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                owner_user_id="user-1",
                master_agent_install_id="install-1",
                channel="web",
            )

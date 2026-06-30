from unittest.mock import patch

import pytest

from server_modules import control_plane_repository


@pytest.mark.asyncio
async def test_agent_trace_create_blocks_before_repository_write():
    with patch.object(
        control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            await control_plane_repository.create_agent_trace(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                root_agent_id="agent-1",
                surface="web",
                thread_id="thread-1",
                run_id="run-1",
                trace_id="trace-1",
            )

    command, payload = kernel.call_args.args
    assert command == "control-plane-service-decision"
    assert payload["operation"] == "agent_trace_create"
    assert payload["record_type"] == "agent_trace"
    assert payload["tenant_id"] == "tenant-1"
    assert payload["workspace_id"] == "workspace-1"
    assert payload["actor_id"] == "agent-1"
    assert payload["agent_id"] == "agent-1"
    assert payload["run_id"] == "run-1"
    assert payload["thread_id"] == "thread-1"
    assert payload["idempotency_key"] == "trace-1"


@pytest.mark.asyncio
async def test_agent_trace_event_write_blocks_before_repository_write():
    with patch.object(
        control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            await control_plane_repository.append_agent_trace_event(
                trace_id="trace-1",
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                seq=1,
                event_type="tool.started",
                persisted=True,
                agent_id="agent-1",
                child_run_id="run-child-1",
                event_id="trace-event-1",
            )

    command, payload = kernel.call_args.args
    assert command == "control-plane-service-decision"
    assert payload["operation"] == "agent_trace_event_write"
    assert payload["record_type"] == "agent_trace_event"
    assert payload["tenant_id"] == "tenant-1"
    assert payload["workspace_id"] == "workspace-1"
    assert payload["actor_id"] == "agent-1"
    assert payload["agent_id"] == "agent-1"
    assert payload["run_id"] == "run-child-1"
    assert payload["idempotency_key"] == "trace-event-1"


@pytest.mark.asyncio
async def test_agent_trace_finish_blocks_before_repository_write():
    with patch.object(
        control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            await control_plane_repository.finish_agent_trace(
                "trace-1",
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                outcome="success",
                final_message_id="message-1",
            )

    command, payload = kernel.call_args.args
    assert command == "control-plane-service-decision"
    assert payload["operation"] == "agent_trace_finish"
    assert payload["record_type"] == "agent_trace"
    assert payload["tenant_id"] == "tenant-1"
    assert payload["workspace_id"] == "workspace-1"
    assert payload["actor_id"] == "trace-finalizer"
    assert payload["target_status"] == "success"
    assert payload["idempotency_key"] == "trace-1"

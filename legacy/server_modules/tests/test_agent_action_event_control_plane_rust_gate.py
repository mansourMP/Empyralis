from unittest.mock import patch

import pytest

from server_modules import control_plane_repository


@pytest.mark.asyncio
async def test_agent_action_event_write_blocks_before_repository_write():
    with patch.object(
        control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            await control_plane_repository.record_agent_action_event(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                event={
                    "surface": "direct_tool",
                    "source_surface": "direct_tool",
                    "action_domain": "tool",
                    "action_type": "execute",
                    "action_name": "shell.run",
                    "status": "completed",
                    "agent_id": "agent-1",
                    "run_id": "run-1",
                    "idempotency_key": "action-1",
                },
            )

    command, payload = kernel.call_args.args
    assert command == "control-plane-service-decision"
    assert payload["operation"] == "agent_action_event_write"
    assert payload["record_type"] == "agent_action_event"
    assert payload["tenant_id"] == "tenant-1"
    assert payload["workspace_id"] == "workspace-1"
    assert payload["actor_id"] == "agent-1"
    assert payload["agent_id"] == "agent-1"
    assert payload["run_id"] == "run-1"
    assert payload["idempotency_key"] == "action-1"


@pytest.mark.asyncio
async def test_agent_action_event_write_unexpected_next_action_blocks_before_repository_write():
    with patch.object(
        control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        return_value={
            "ok": True,
            "decision": "allow",
            "reason": "control_plane_service_operation_allowed",
            "operation": "agent_action_event_write",
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
            await control_plane_repository.record_agent_action_event(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                event={
                    "surface": "direct_tool",
                    "source_surface": "direct_tool",
                    "action_domain": "tool",
                    "action_type": "execute",
                    "action_name": "shell.run",
                    "status": "completed",
                    "agent_id": "agent-1",
                    "run_id": "run-1",
                    "idempotency_key": "action-1",
                },
            )

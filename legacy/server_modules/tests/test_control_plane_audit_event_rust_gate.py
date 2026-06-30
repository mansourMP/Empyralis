from unittest.mock import patch

import pytest

from server_modules import control_plane_repository


@pytest.mark.asyncio
async def test_secret_access_event_write_blocks_before_repository_write():
    with patch.object(
        control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            await control_plane_repository.append_agent_secret_access_event(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                secret_kind="provider_credential",
                credential_id="cred-1",
                run_id="run-1",
                actor={"id": "agent-1"},
                allowed_fields=["api_key"],
                event_id="secret-event-1",
            )

    command, payload = kernel.call_args.args
    assert command == "control-plane-service-decision"
    assert payload["operation"] == "agent_secret_access_event_write"
    assert payload["record_type"] == "agent_secret_access_event"
    assert payload["tenant_id"] == "tenant-1"
    assert payload["workspace_id"] == "workspace-1"
    assert payload["actor_id"] == "agent-1"
    assert payload["run_id"] == "run-1"
    assert payload["idempotency_key"] == "secret-event-1"


@pytest.mark.asyncio
async def test_egress_event_write_blocks_before_repository_write():
    with patch.object(
        control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            await control_plane_repository.append_agent_egress_event(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                agent_install_id="agent-1",
                run_id="run-1",
                tool_name="browser.open",
                request_url="https://example.com",
                event_id="egress-event-1",
            )

    command, payload = kernel.call_args.args
    assert command == "control-plane-service-decision"
    assert payload["operation"] == "agent_egress_event_write"
    assert payload["record_type"] == "agent_egress_event"
    assert payload["tenant_id"] == "tenant-1"
    assert payload["workspace_id"] == "workspace-1"
    assert payload["actor_id"] == "agent-1"
    assert payload["agent_id"] == "agent-1"
    assert payload["run_id"] == "run-1"
    assert payload["idempotency_key"] == "egress-event-1"


@pytest.mark.asyncio
async def test_activity_ledger_event_write_blocks_before_repository_write():
    with patch.object(
        control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            await control_plane_repository.append_activity_ledger_event(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                actor_type="agent",
                actor_id="agent-1",
                event_class="tool_execution",
                run_id="run-1",
                thread_id="thread-1",
                channel="runtime",
                event_id="activity-event-1",
            )

    command, payload = kernel.call_args.args
    assert command == "control-plane-service-decision"
    assert payload["operation"] == "activity_ledger_event_write"
    assert payload["record_type"] == "activity_ledger_event"
    assert payload["tenant_id"] == "tenant-1"
    assert payload["workspace_id"] == "workspace-1"
    assert payload["actor_id"] == "agent-1"
    assert payload["run_id"] == "run-1"
    assert payload["thread_id"] == "thread-1"
    assert payload["idempotency_key"] == "activity-event-1"


@pytest.mark.asyncio
async def test_activity_ledger_event_write_unexpected_next_action_blocks_before_repository_write():
    with patch.object(
        control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        return_value={
            "ok": True,
            "decision": "allow",
            "reason": "control_plane_service_operation_allowed",
            "operation": "activity_ledger_event_write",
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
            await control_plane_repository.append_activity_ledger_event(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                actor_type="agent",
                actor_id="agent-1",
                event_class="tool_execution",
                run_id="run-1",
                thread_id="thread-1",
                channel="runtime",
                event_id="activity-event-1",
            )

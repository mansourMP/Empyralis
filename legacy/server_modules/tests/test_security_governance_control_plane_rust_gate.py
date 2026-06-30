from unittest.mock import patch

import pytest

from server_modules import control_plane_repository


@pytest.mark.asyncio
async def test_security_control_state_blocks_before_repository_write():
    with patch.object(
        control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            await control_plane_repository.upsert_security_control_state(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                scope_type="workspace",
                control_kind="kill_switch",
                scope_key="workspace-1",
                enabled=True,
                reason="incident",
            )

    command, payload = kernel.call_args.args
    assert command == "control-plane-service-decision"
    assert payload["operation"] == "security_control_state_write"
    assert payload["record_type"] == "security_control_state"
    assert payload["scope_type"] == "workspace"
    assert payload["control_kind"] == "kill_switch"
    assert payload["idempotency_key"] == "workspace:kill_switch:workspace-1:enabled"


@pytest.mark.asyncio
async def test_governance_hold_write_blocks_before_repository_write():
    with patch.object(
        control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            await control_plane_repository.upsert_governance_hold(
                tenant_id="tenant-1",
                scope_type="tenant",
                hold_key="legal_hold",
                reason="legal request",
            )

    command, payload = kernel.call_args.args
    assert command == "control-plane-service-decision"
    assert payload["operation"] == "governance_hold_write"
    assert payload["record_type"] == "governance_hold"
    assert payload["scope_type"] == "tenant"
    assert payload["workspace_id"] == ""
    assert payload["idempotency_key"] == "tenant:tenant-1:legal_hold:write"


@pytest.mark.asyncio
async def test_governance_hold_release_blocks_before_repository_write():
    with patch.object(
        control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            await control_plane_repository.release_governance_hold(
                tenant_id="tenant-1",
                scope_type="workspace",
                workspace_id="workspace-1",
                hold_key="legal_hold",
            )

    command, payload = kernel.call_args.args
    assert command == "control-plane-service-decision"
    assert payload["operation"] == "governance_hold_release"
    assert payload["record_type"] == "governance_hold"
    assert payload["target_status"] == "released"
    assert payload["idempotency_key"] == "workspace:workspace-1:legal_hold:release"


@pytest.mark.asyncio
async def test_security_control_state_unexpected_next_action_blocks_before_repository_write():
    with patch.object(
        control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        return_value={
            "ok": True,
            "decision": "allow",
            "reason": "control_plane_service_operation_allowed",
            "operation": "security_control_state_write",
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
            await control_plane_repository.upsert_security_control_state(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                scope_type="workspace",
                control_kind="kill_switch",
                scope_key="workspace-1",
                enabled=True,
                reason="incident",
            )

import pytest
from unittest.mock import patch

from server_modules import run_state_repository


@pytest.mark.asyncio
async def test_create_approval_request_blocks_before_pool_access():
    with patch.object(
        run_state_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            await run_state_repository.create_or_update_approval_request(
                "run-1",
                "approval-1",
                {"workspace_id": "workspace-1", "tenant_id": "tenant-1"},
                "system",
                "trace-1",
            )

    command, payload = kernel.call_args.args
    assert command == "runtime-state-store-decision"
    assert payload["operation"] == "create_or_update_approval_request"
    assert payload["state_class"] == "run_approvals"
    assert payload["run_id"] == "run-1"
    assert payload["workspace_id"] == "workspace-1"
    assert payload["tenant_id"] == "tenant-1"


@pytest.mark.asyncio
async def test_resolve_approval_if_pending_blocks_before_pool_access():
    with patch.object(
        run_state_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            await run_state_repository.resolve_approval_if_pending(
                "run-1",
                "approval-1",
                "approved",
                "user-1",
                "trace-1",
            )

    command, payload = kernel.call_args.args
    assert command == "runtime-state-store-decision"
    assert payload["operation"] == "resolve_approval_if_pending"
    assert payload["state_class"] == "run_approvals"
    assert payload["status"] == "approved"


@pytest.mark.asyncio
async def test_record_approval_resolution_blocks_before_pool_access():
    with patch.object(
        run_state_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            await run_state_repository.record_approval_resolution(
                "run-1",
                "approval-1",
                "rejected",
                "user-1",
                "trace-1",
            )

    command, payload = kernel.call_args.args
    assert command == "runtime-state-store-decision"
    assert payload["operation"] == "record_approval_resolution"
    assert payload["state_class"] == "run_approvals"
    assert payload["status"] == "rejected"

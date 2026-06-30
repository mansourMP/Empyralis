from unittest.mock import patch

import pytest

from server_modules import control_plane_repository


@pytest.mark.asyncio
async def test_personal_context_event_write_blocks_before_repository_write():
    with patch.object(
        control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            await control_plane_repository.append_personal_context_event(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                source_app="gmail",
                event_type="new_email",
                entity_id="msg-1",
                summary="New email",
            )

    command, payload = kernel.call_args.args
    assert command == "control-plane-service-decision"
    assert payload["operation"] == "personal_context_event_write"
    assert payload["record_type"] == "personal_context_event"
    assert payload["actor_id"] == "gmail"
    assert payload["idempotency_key"].startswith("gmail:new_email:msg-1:")


@pytest.mark.asyncio
async def test_personal_context_seen_update_blocks_before_repository_write():
    with patch.object(
        control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            await control_plane_repository.mark_personal_context_events_seen_by_sage(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                event_ids=["pctx-1"],
                seen_at="2026-06-01T00:00:00Z",
            )

    command, payload = kernel.call_args.args
    assert command == "control-plane-service-decision"
    assert payload["operation"] == "personal_context_event_seen_update"
    assert payload["record_type"] == "personal_context_event"
    assert payload["actor_id"] == "sage"
    assert payload["idempotency_key"] == "workspace-1:pctx-1:2026-06-01T00:00:00+00:00"

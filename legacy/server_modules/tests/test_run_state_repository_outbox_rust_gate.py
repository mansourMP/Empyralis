import asyncio
from unittest.mock import patch

import pytest

from server_modules import run_state_repository


def test_persist_outbox_event_blocks_before_pool_when_rust_denies() -> None:
    async def run() -> None:
        with patch.object(
            run_state_repository.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=RuntimeError("rust denied"),
        ) as kernel, patch.object(
            run_state_repository,
            "_require_pool",
        ) as require_pool:
            with pytest.raises(RuntimeError, match="rust denied"):
                await run_state_repository.persist_outbox_event(
                    event_id="event-1",
                    event_type="run_transition",
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    run_id="run-1",
                    machine_id=None,
                    trace_id="trace-1",
                    idempotency_key="idem-1",
                    payload={"ok": True},
                )

        command, payload = kernel.call_args.args
        assert command == "outbox-delivery-decision"
        assert payload["operation"] == "persist_event"
        assert payload["event_id"] == "event-1"
        assert payload["tenant_id"] == "tenant-1"
        require_pool.assert_not_called()

    asyncio.run(run())


def test_claim_due_outbox_events_blocks_before_pool_when_rust_denies() -> None:
    async def run() -> None:
        with patch.object(
            run_state_repository.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=RuntimeError("rust denied"),
        ) as kernel, patch.object(
            run_state_repository,
            "_read_pool",
        ) as read_pool:
            with pytest.raises(RuntimeError, match="rust denied"):
                await run_state_repository.claim_due_outbox_events(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    claimed_by="worker-1",
                    limit=7,
                )

        command, payload = kernel.call_args.args
        assert command == "outbox-delivery-decision"
        assert payload["operation"] == "claim_due"
        assert payload["tenant_id"] == "tenant-1"
        assert payload["workspace_id"] == "workspace-1"
        assert payload["claimed_by"] == "worker-1"
        assert payload["limit"] == 7
        read_pool.assert_not_called()

    asyncio.run(run())

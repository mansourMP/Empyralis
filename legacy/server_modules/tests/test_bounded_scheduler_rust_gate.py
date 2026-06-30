import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from server_modules import bounded_scheduler_service


def _policy() -> bounded_scheduler_service.SchedulerPolicyBounds:
    return bounded_scheduler_service.SchedulerPolicyBounds(
        quiet_hours_start=23,
        quiet_hours_end=7,
        max_event_triggers_per_hour=4,
        max_self_proposed_per_hour=2,
        max_runtime_seconds=20,
        minimum_battery_percent=20,
        require_network_online=False,
        require_owner_approval_for_privileged_wakeups=True,
        plan_tier="free",
    )


def test_claim_due_wake_requests_blocks_before_repository_claim() -> None:
    async def run() -> None:
        with patch.object(
            bounded_scheduler_service,
            "_load_scheduler_scope",
            new=AsyncMock(return_value=({}, {}, _policy())),
        ), patch.object(
            bounded_scheduler_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=RuntimeError("rust denied"),
        ) as kernel, patch.object(
            bounded_scheduler_service.control_plane_repository,
            "claim_due_agent_scheduler_wake_requests",
            new=AsyncMock(),
        ) as claim:
            with pytest.raises(RuntimeError, match="rust denied"):
                await bounded_scheduler_service.claim_due_wake_requests(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                )

        command, payload = kernel.call_args.args
        assert command == "session-scheduler-decision"
        assert payload["operation"] == "claim_wake_requests"
        assert payload["workspace_id"] == "workspace-1"
        assert payload["trigger_id"] == "claim_due_wake_requests"
        claim.assert_not_awaited()

    asyncio.run(run())


def test_claim_due_wake_requests_blocks_on_unexpected_next_action() -> None:
    async def run() -> None:
        with patch.object(
            bounded_scheduler_service,
            "_load_scheduler_scope",
            new=AsyncMock(return_value=({}, {}, _policy())),
        ), patch.object(
            bounded_scheduler_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={"ok": True, "decision": "allow", "next_action": "schedule_retry"},
        ), patch.object(
            bounded_scheduler_service.control_plane_repository,
            "claim_due_agent_scheduler_wake_requests",
            new=AsyncMock(),
        ) as claim:
            with pytest.raises(RuntimeError, match="unexpected next_action"):
                await bounded_scheduler_service.claim_due_wake_requests(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                )

        claim.assert_not_awaited()

    asyncio.run(run())


def test_schedule_retry_blocks_before_repository_update() -> None:
    async def run() -> None:
        with patch.object(
            bounded_scheduler_service,
            "_load_scheduler_scope",
            new=AsyncMock(return_value=({}, {}, _policy())),
        ), patch.object(
            bounded_scheduler_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=RuntimeError("rust denied"),
        ) as kernel, patch.object(
            bounded_scheduler_service.control_plane_repository,
            "update_agent_scheduler_wake_request_status",
            new=AsyncMock(),
        ) as update:
            with pytest.raises(RuntimeError, match="rust denied"):
                await bounded_scheduler_service.schedule_retry(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    wake_request={"id": "wake-1", "metadata": {"retry": {"retry_attempt": 0}}},
                    error="boom",
                )

        command, payload = kernel.call_args.args
        assert command == "session-scheduler-decision"
        assert payload["operation"] == "schedule_retry"
        assert payload["workspace_id"] == "workspace-1"
        assert payload["trigger_id"] == "wake-1"
        assert payload["attempt"] == 1
        update.assert_not_awaited()

    asyncio.run(run())


def test_schedule_retry_blocks_on_unexpected_next_action() -> None:
    async def run() -> None:
        with patch.object(
            bounded_scheduler_service,
            "_load_scheduler_scope",
            new=AsyncMock(return_value=({}, {}, _policy())),
        ), patch.object(
            bounded_scheduler_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={"ok": True, "decision": "allow", "next_action": "claim_due_wake_requests"},
        ), patch.object(
            bounded_scheduler_service.control_plane_repository,
            "update_agent_scheduler_wake_request_status",
            new=AsyncMock(),
        ) as update:
            with pytest.raises(RuntimeError, match="unexpected next_action"):
                await bounded_scheduler_service.schedule_retry(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    wake_request={"id": "wake-1", "metadata": {"retry": {"retry_attempt": 0}}},
                    error="boom",
                )

        update.assert_not_awaited()

    asyncio.run(run())

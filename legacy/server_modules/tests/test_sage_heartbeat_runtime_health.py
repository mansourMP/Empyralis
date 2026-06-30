import asyncio
from unittest.mock import AsyncMock, patch

from server_modules import rust_runtime_kernel_client, sage_heartbeat_service


def test_sage_heartbeat_snapshot_uses_rust_runtime_health_decision() -> None:
    async def run() -> None:
        with patch.object(
            sage_heartbeat_service,
            "list_sage_profile",
            return_value={
                "profile": {"complete": True, "recurring_responsibility": "watch queue"},
                "bootstrap": {"complete": True},
            },
        ), patch.object(
            sage_heartbeat_service.bounded_scheduler_service,
            "scheduler_status_snapshot",
            new=AsyncMock(
                return_value={
                    "policy": {
                        "quiet_hours_start": 23,
                        "quiet_hours_end": 7,
                        "max_event_triggers_per_hour": 4,
                        "max_self_proposed_per_hour": 2,
                        "max_runtime_seconds": 20,
                        "minimum_battery_percent": 20,
                    },
                    "exact_jobs": {"items": []},
                    "wake_queue": {"pending_count": 0, "claimed_count": 0},
                    "ambient_monitor": {"registered": True},
                }
            ),
        ), patch.object(
            sage_heartbeat_service,
            "runtime_lane_queue_snapshot",
            return_value={"queued_count": 0, "running_now_count": 0},
        ), patch.object(
            sage_heartbeat_service,
            "_plugin_health_snapshot",
            return_value={"ok": True},
        ), patch.object(
            sage_heartbeat_service,
            "_rust_kernel_health_snapshot",
            return_value={"available": True},
        ), patch.object(
            rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "decision": "allow",
                "reason": "heartbeat_snapshot_ready",
                "operation": "heartbeat_snapshot",
                "health": "healthy",
                "next_action": "idle",
            },
        ), patch.object(
            rust_runtime_kernel_client,
            "runtime_health_decision",
            return_value={
                "decision": "allow",
                "reason": "runtime_health_snapshot_normalized",
                "operation": "heartbeat_snapshot",
                "health": "healthy",
                "next_action": "idle",
                "readiness": {"ready": True},
            },
        ) as kernel:
            snapshot = await sage_heartbeat_service.build_sage_heartbeat_snapshot(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
            )

        assert snapshot["health"] == "healthy"
        assert snapshot["operator_next_action"] == "idle"
        assert snapshot["readiness"] == {"ready": True}
        assert snapshot["heartbeat_snapshot"]["operation"] == "heartbeat_snapshot"
        assert snapshot["runtime_health"]["operation"] == "heartbeat_snapshot"
        assert kernel.call_args.kwargs["operation"] == "heartbeat_snapshot"
        assert kernel.call_args.kwargs["workspace_id"] == "workspace-1"

    asyncio.run(run())


def test_sage_heartbeat_snapshot_blocks_on_unexpected_rust_next_action() -> None:
    async def run() -> None:
        with patch.object(
            sage_heartbeat_service,
            "list_sage_profile",
            return_value={
                "profile": {"complete": True, "recurring_responsibility": "watch queue"},
                "bootstrap": {"complete": True},
            },
        ), patch.object(
            sage_heartbeat_service.bounded_scheduler_service,
            "scheduler_status_snapshot",
            new=AsyncMock(
                return_value={
                    "policy": {
                        "quiet_hours_start": 23,
                        "quiet_hours_end": 7,
                        "max_event_triggers_per_hour": 4,
                        "max_self_proposed_per_hour": 2,
                        "max_runtime_seconds": 20,
                        "minimum_battery_percent": 20,
                    },
                    "exact_jobs": {"items": []},
                    "wake_queue": {"pending_count": 0, "claimed_count": 0},
                    "ambient_monitor": {"registered": True},
                }
            ),
        ), patch.object(
            sage_heartbeat_service,
            "runtime_lane_queue_snapshot",
            return_value={"queued_count": 0, "running_now_count": 0},
        ), patch.object(
            sage_heartbeat_service,
            "_plugin_health_snapshot",
            return_value={"ok": True},
        ), patch.object(
            sage_heartbeat_service,
            "_rust_kernel_health_snapshot",
            return_value={"available": True},
        ), patch.object(
            rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "decision": "allow",
                "reason": "heartbeat_snapshot_ready",
                "operation": "heartbeat_snapshot",
                "health": "healthy",
                "next_action": "operator_next_action",
            },
        ), patch.object(
            rust_runtime_kernel_client,
            "runtime_health_decision",
            return_value={
                "decision": "allow",
                "reason": "runtime_health_snapshot_normalized",
                "operation": "heartbeat_snapshot",
                "health": "healthy",
                "next_action": "operator_next_action",
                "readiness": {"ready": True},
            },
        ):
            try:
                await sage_heartbeat_service.build_sage_heartbeat_snapshot(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                )
            except RuntimeError as exc:
                assert str(exc) == "unexpected_next_action:operator_next_action"
                return
            raise AssertionError("expected heartbeat snapshot to fail closed on wrong next_action")

    asyncio.run(run())

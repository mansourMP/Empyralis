import asyncio
import unittest
from unittest.mock import AsyncMock, patch
from datetime import datetime, timezone

from server_modules import sage_heartbeat_service


class SageHeartbeatServiceTests(unittest.TestCase):
    def test_build_snapshot_surfaces_quiet_hours_and_next_action(self) -> None:
        def fake_heartbeat_snapshot(**payload):
            return {
                "ok": True,
                "decision": "allow",
                "reason": "heartbeat_snapshot_ready",
                "operation": "heartbeat_snapshot",
                "health": "quiet",
                "next_action": "wait_for_quiet_hours",
            }

        def fake_runtime_health_decision(**payload):
            operation = payload.get("operation")
            return {
                "ok": True,
                "decision": "allow",
                "reason": "runtime_health_snapshot_normalized",
                "operation": operation,
                "health": "quiet" if operation == "heartbeat_snapshot" else "healthy",
                "next_action": "wait_for_quiet_hours" if operation == "heartbeat_snapshot" else "idle",
                "readiness": {
                    "ready": operation == "readiness_gate",
                    "rust_kernel_available": True,
                    "scheduler_enabled": True,
                    "plugin_ok": True,
                    "profile_complete": True,
                },
            }

        with (
            patch(
                "server_modules.sage_heartbeat_service.list_sage_profile",
                return_value={
                    "profile": {
                        "recurring_responsibility": "Keep my inbox triaged.",
                        "communication_style": "Be direct.",
                    },
                    "bootstrap": {"complete": True, "progress_label": "5/5"},
                },
            ),
            patch(
                "server_modules.sage_heartbeat_service.bounded_scheduler_service.scheduler_status_snapshot",
                new=AsyncMock(
                    return_value={
                        "policy": {
                            "quiet_hours_start": 22,
                            "quiet_hours_end": 7,
                            "plan_tier": "pro",
                        },
                        "exact_jobs": {
                            "items": [
                                {
                                    "id": "sched-2",
                                    "name": "Later task",
                                    "enabled": True,
                                    "next_run_at": "2026-05-06T12:00:00Z",
                                    "wake_mode": "now",
                                    "delivery": "announce",
                                },
                                {
                                    "id": "sched-1",
                                    "name": "Morning follow-up",
                                    "enabled": True,
                                    "next_run_at": "2026-05-06T09:00:00Z",
                                    "wake_mode": "next-heartbeat",
                                    "delivery": "announce",
                                },
                            ],
                        },
                        "wake_queue": {"pending_count": 1, "claimed_count": 0, "pending": [], "claimed": []},
                        "ambient_monitor": {"status": "running"},
                    }
                ),
            ),
            patch(
                "server_modules.sage_heartbeat_service.runtime_lane_queue_snapshot",
                return_value={
                    "running": True,
                    "pending_count": 2,
                    "active_count": 1,
                    "recent": [
                        {
                            "id": "done-1",
                            "lane": "main",
                            "label": "Finished recap",
                            "status": "completed",
                            "summary": "Inbox triage summary delivered.",
                        }
                    ],
                    "lanes": {
                        "cron": {
                            "pending_count": 2,
                            "active_count": 1,
                            "pending": [
                                {
                                    "id": "approval-1",
                                    "label": "Need confirmation before sending",
                                    "status": "waiting_approval",
                                    "summary": "Waiting for approval before messaging Alex.",
                                },
                                {
                                    "id": "queued-1",
                                    "label": "Follow-up queue",
                                    "status": "queued",
                                    "summary": "Follow up on the morning plan.",
                                },
                            ],
                            "active": [
                                {
                                    "id": "run-1",
                                    "label": "Morning review",
                                    "status": "running",
                                    "summary": "Reviewing the day plan now.",
                                    "run_id": "run-1",
                                }
                            ],
                        },
                    },
                },
            ),
            patch(
                "server_modules.sage_heartbeat_service.bounded_scheduler_service.quiet_hours_status_snapshot",
                return_value={
                    "active": True,
                    "label": "Quiet hours active until 07:00",
                    "next_allowed_at": datetime(2026, 5, 6, 7, 0, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z"),
                },
            ),
            patch(
                "server_modules.sage_heartbeat_service._heartbeat_snapshot_decision",
                side_effect=fake_heartbeat_snapshot,
            ),
            patch(
                "server_modules.sage_heartbeat_service._runtime_health_decision",
                side_effect=fake_runtime_health_decision,
            ) as rust_health_mock,
        ):
            payload = asyncio.run(
                sage_heartbeat_service.build_sage_heartbeat_snapshot(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                )
            )

        self.assertEqual(payload["quiet_hours"]["label"], "22:00–07:00")
        self.assertEqual(payload["profile"]["recurring_responsibility"], "Keep my inbox triaged.")
        self.assertEqual(payload["next_scheduled_action"]["id"], "sched-1")
        self.assertEqual(payload["reminders"]["count"], 2)
        self.assertEqual(payload["lane_queue"]["pending_count"], 2)
        self.assertEqual(payload["lane_queue"]["active_count"], 1)
        self.assertEqual(payload["queue_overview"]["running_now_count"], 1)
        self.assertEqual(payload["queue_overview"]["queued_count"], 1)
        self.assertEqual(payload["queue_overview"]["blocked_on_approval_count"], 1)
        self.assertEqual(payload["queue_overview"]["done_count"], 1)
        self.assertEqual(payload["queue_overview"]["quiet_hours"]["label"], "Quiet hours active until 07:00")
        self.assertEqual(payload["queue_overview"]["lanes"]["scheduled"][0]["id"], "sched-2")
        self.assertEqual(payload["queue_overview"]["lanes"]["needs_ok"][0]["id"], "approval-1")
        self.assertEqual(payload["queue_overview"]["lanes"]["done"][0]["id"], "done-1")
        self.assertEqual(payload["heartbeat_snapshot"]["operation"], "heartbeat_snapshot")
        self.assertEqual(payload["runtime_health"]["operation"], "heartbeat_snapshot")
        self.assertEqual(payload["readiness_gate"]["operation"], "readiness_gate")
        self.assertTrue(payload["readiness"]["ready"])
        self.assertEqual(
            [call.kwargs["operation"] for call in rust_health_mock.call_args_list],
            ["heartbeat_snapshot", "readiness_gate"],
        )


if __name__ == "__main__":
    unittest.main()

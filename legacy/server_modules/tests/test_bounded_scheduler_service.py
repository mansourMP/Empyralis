import unittest
from unittest.mock import AsyncMock, patch
import importlib
from datetime import datetime, timezone

from server_modules import bounded_scheduler_service


class BoundedSchedulerServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        global bounded_scheduler_service

        bounded_scheduler_service = importlib.import_module("server_modules.bounded_scheduler_service")

    def test_resolve_scheduler_policy_uses_entitlement_defaults(self):
        policy = bounded_scheduler_service.resolve_scheduler_policy(
            workspace={"metadata": {"billing": {"plan": "free"}}},
            master_install={"metadata": {}},
        )

        self.assertEqual(policy.plan_tier, "free")
        self.assertEqual(policy.max_event_triggers_per_hour, 2)
        self.assertEqual(policy.max_self_proposed_per_hour, 1)
        self.assertEqual(policy.max_runtime_seconds, 15)

    async def test_maybe_schedule_event_trigger_ignores_low_priority_events(self):
        result = await bounded_scheduler_service.maybe_schedule_event_trigger(
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            event={
                "id": "evt-1",
                "event_type": "flashcards_created",
                "source_app": "study",
                "summary": "12 flashcards were created.",
                "priority": 20,
                "scope": {"audience": ["sage"]},
            },
        )

        self.assertIsNone(result)

    async def test_propose_self_wakeup_denies_privileged_without_approval(self):
        policy = bounded_scheduler_service.SchedulerPolicyBounds(
            quiet_hours_start=23,
            quiet_hours_end=7,
            max_event_triggers_per_hour=4,
            max_self_proposed_per_hour=2,
            max_runtime_seconds=20,
            minimum_battery_percent=20,
            require_network_online=False,
            require_owner_approval_for_privileged_wakeups=True,
            plan_tier="standard",
        )
        with (
            patch(
                "server_modules.bounded_scheduler_service._load_scheduler_scope",
                new=AsyncMock(return_value=({"metadata": {}}, {"id": "install-sage", "metadata": {}}, policy)),
            ),
            patch(
                "server_modules.bounded_scheduler_service._persist_wakeup",
                new=AsyncMock(return_value={"id": "wake-1", "status": "denied"}),
            ) as persist_mock,
        ):
            result = await bounded_scheduler_service.propose_self_wakeup(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                summary="Check whether the user still needs a sleep reminder.",
                reason="night_review",
                policy_context={"requires_privileged_runtime": True},
            )

        self.assertFalse(result["accepted"])
        self.assertEqual(result["wake_request"]["status"], "denied")
        self.assertTrue(persist_mock.await_args.kwargs["approval_required"])

    async def test_propose_self_wakeup_accepts_immediate_request_and_triggers_monitor(self):
        policy = bounded_scheduler_service.SchedulerPolicyBounds(
            quiet_hours_start=0,
            quiet_hours_end=0,
            max_event_triggers_per_hour=4,
            max_self_proposed_per_hour=2,
            max_runtime_seconds=20,
            minimum_battery_percent=20,
            require_network_online=False,
            require_owner_approval_for_privileged_wakeups=True,
            plan_tier="standard",
        )
        with (
            patch(
                "server_modules.bounded_scheduler_service._load_scheduler_scope",
                new=AsyncMock(return_value=({"metadata": {}}, {"id": "install-sage", "metadata": {}}, policy)),
            ),
            patch(
                "server_modules.bounded_scheduler_service.control_plane_repository.count_agent_scheduler_wake_requests_since",
                new=AsyncMock(return_value=0),
            ),
            patch(
                "server_modules.bounded_scheduler_service._persist_wakeup",
                new=AsyncMock(return_value={"id": "wake-2", "status": "pending"}),
            ),
            patch(
                "server_modules.bounded_scheduler_service._trigger_ambient_monitor",
                return_value={"ok": True},
            ) as trigger_mock,
        ):
            result = await bounded_scheduler_service.propose_self_wakeup(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                summary="Check whether the reply backlog needs a nudge.",
                reason="message_followup",
            )

        self.assertTrue(result["accepted"])
        self.assertEqual(result["wake_request"]["id"], "wake-2")
        trigger_mock.assert_called_once_with("workspace-1")

    async def test_build_wakeup_execution_bundle_uses_context_goals_and_preferences(self):
        policy = bounded_scheduler_service.SchedulerPolicyBounds(
            quiet_hours_start=23,
            quiet_hours_end=7,
            max_event_triggers_per_hour=4,
            max_self_proposed_per_hour=2,
            max_runtime_seconds=20,
            minimum_battery_percent=20,
            require_network_online=False,
            require_owner_approval_for_privileged_wakeups=True,
            plan_tier="standard",
        )
        with (
            patch(
                "server_modules.bounded_scheduler_service._load_scheduler_scope",
                new=AsyncMock(
                    return_value=(
                        {"metadata": {"goals": ["Prepare for finals"]}},
                        {"id": "install-sage", "metadata": {}},
                        policy,
                    )
                ),
            ),
            patch(
                "server_modules.personal_context_engine.list_events",
                new=AsyncMock(
                    return_value=[
                        {
                            "id": "evt-1",
                            "source_app": "calendar",
                            "summary": "Calendar conflict detected for Math Final.",
                        }
                    ]
                ),
            ),
            patch(
                "server_modules.bounded_scheduler_service.workspace_context.read_workspace_context_file",
                return_value="Prefer brief reminders after 9 PM.",
            ),
        ):
            result = await bounded_scheduler_service.build_wakeup_execution_bundle(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                heartbeat_tasks=["Review notification queue"],
                wake_requests=[{"id": "wake-1", "trigger_kind": "event_trigger", "summary": "Reply is still pending for Alex."}],
            )

        self.assertEqual(result["metadata"]["scheduler_mode"], "mixed")
        self.assertIn("Prepare for finals", result["message"])
        self.assertIn("Reply is still pending for Alex.", result["message"])
        self.assertIn("Calendar conflict detected for Math Final.", result["message"])
        self.assertIn("Prefer brief reminders", result["message"])

    async def test_scheduler_status_snapshot_reports_policy_jobs_and_queue(self):
        policy = bounded_scheduler_service.SchedulerPolicyBounds(
            quiet_hours_start=23,
            quiet_hours_end=7,
            max_event_triggers_per_hour=4,
            max_self_proposed_per_hour=2,
            max_runtime_seconds=20,
            minimum_battery_percent=20,
            require_network_online=False,
            require_owner_approval_for_privileged_wakeups=True,
            plan_tier="standard",
        )
        with (
            patch(
                "server_modules.bounded_scheduler_service._load_scheduler_scope",
                new=AsyncMock(return_value=({"metadata": {}}, {"id": "install-sage", "metadata": {}}, policy)),
            ),
            patch(
                "server_modules.bounded_scheduler_service.control_plane_repository.list_agent_scheduler_wake_requests",
                new=AsyncMock(side_effect=[[{"id": "wake-1"}], [{"id": "wake-2"}]]),
            ),
            patch(
                "server_modules.bounded_scheduler_service.ambient_monitor_status",
                return_value={"registered": True, "heartbeat": {"ok": True}},
            ),
            patch(
                "server_modules.runs_core.list_schedules",
                new=AsyncMock(return_value={"items": [{"id": "job-1"}, {"id": "job-2"}]}),
            ),
        ):
            result = await bounded_scheduler_service.scheduler_status_snapshot(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
            )

        self.assertEqual(result["policy"]["plan_tier"], "standard")
        self.assertTrue(result["ambient_monitor"]["registered"])
        self.assertEqual(result["exact_jobs"]["count"], 2)
        self.assertEqual(result["wake_queue"]["pending_count"], 1)
        self.assertEqual(result["wake_queue"]["claimed_count"], 1)

    def test_quiet_hours_status_snapshot_reports_active_window(self):
        policy = bounded_scheduler_service.SchedulerPolicyBounds(
            quiet_hours_start=22,
            quiet_hours_end=7,
            max_event_triggers_per_hour=4,
            max_self_proposed_per_hour=2,
            max_runtime_seconds=20,
            minimum_battery_percent=20,
            require_network_online=False,
            require_owner_approval_for_privileged_wakeups=True,
            plan_tier="standard",
        )

        snapshot = bounded_scheduler_service.quiet_hours_status_snapshot(
            policy=policy,
            now_utc=datetime(2026, 5, 5, 15, 30, tzinfo=timezone.utc),
        )

        self.assertTrue(snapshot["active"])
        self.assertIn("Quiet hours active until", snapshot["label"])
        self.assertTrue(snapshot["next_allowed_at"].endswith("Z"))


if __name__ == "__main__":
    unittest.main()

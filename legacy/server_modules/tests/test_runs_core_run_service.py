import asyncio
import queue
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from server_modules import runs_core
from server_modules.runtime_models import CronScheduleUpsertRequest, RunStartRequest


class RunsCoreRunServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._persist_patch = patch(
            "server_modules.acp_manager.run_state_repository.sync_upsert_live_run",
            return_value=None,
        )
        self._delete_patch = patch(
            "server_modules.acp_manager.run_state_repository.sync_delete_live_run",
            return_value=None,
        )
        self._persist_patch.start()
        self._delete_patch.start()
        self.addCleanup(self._persist_patch.stop)
        self.addCleanup(self._delete_patch.stop)

    def test_initialize_runtime_services_delegates_bootstrap_to_run_service(self) -> None:
        with patch.object(runs_core.run_service, "initialize_runtime_services", return_value=None) as bootstrap_mock:
            runs_core.initialize_runtime_services()

        bootstrap_mock.assert_called_once()
        _, kwargs = bootstrap_mock.call_args
        self.assertEqual(kwargs["runtime_state_db_path"], runs_core.ORION_RUNTIME_STATE_DB)
        self.assertIs(kwargs["load_live_runtime_state_fn"], runs_core._load_live_runtime_state)
        self.assertIs(kwargs["load_schedules_fn"], runs_core._load_schedules)
        self.assertEqual(kwargs["scheduler_enabled"], runs_core.ORION_SCHEDULER_ENABLED)
        self.assertEqual(kwargs["local_runtime_watchdog_enabled"], runs_core.ORION_LOCAL_COMPANION_ENABLED)
        self.assertTrue(callable(kwargs["run_outbox_delivery_forever_fn"]))
        self.assertTrue(callable(kwargs["recover_delegation_retries_on_startup_fn"]))

    def test_trigger_pending_heartbeat_schedules_delegates_to_run_service(self) -> None:
        previous_schedules = dict(runs_core.WEEKLY_SCHEDULES)
        try:
            runs_core.WEEKLY_SCHEDULES.clear()
            runs_core.WEEKLY_SCHEDULES["sched-1"] = {
                "id": "sched-1",
                "workspace_id": "ws-1",
                "enabled": True,
                "pending_heartbeat": True,
            }
            with patch.object(
                runs_core.rust_runtime_kernel_client,
                "run_trigger_decision",
                return_value={"ok": True, "decision": "allow", "next_action": "trigger_pending_schedules"},
            ) as rust_mock, patch.object(
                runs_core.run_service,
                "trigger_pending_heartbeat_schedules",
                return_value={"acted": False, "started": []},
            ) as trigger_mock:
                payload = runs_core.trigger_pending_heartbeat_schedules(workspace_id="ws-1")

            self.assertEqual(payload, {"acted": False, "started": []})
            rust_mock.assert_called_once()
            self.assertEqual(rust_mock.call_args.kwargs["operation"], "trigger_pending_heartbeat")
            self.assertEqual(rust_mock.call_args.kwargs["pending_count"], 1)
            trigger_mock.assert_called_once()
            _, kwargs = trigger_mock.call_args
            self.assertIs(kwargs["weekly_schedules"], runs_core.WEEKLY_SCHEDULES)
            self.assertIs(kwargs["persist_schedules_fn"], runs_core._persist_schedules)
            self.assertEqual(kwargs["workspace_id"], "ws-1")
        finally:
            runs_core.WEEKLY_SCHEDULES.clear()
            runs_core.WEEKLY_SCHEDULES.update(previous_schedules)

    def test_trigger_pending_heartbeat_schedules_noop_skips_run_service(self) -> None:
        previous_schedules = dict(runs_core.WEEKLY_SCHEDULES)
        try:
            runs_core.WEEKLY_SCHEDULES.clear()
            with patch.object(
                runs_core.rust_runtime_kernel_client,
                "run_trigger_decision",
                return_value={"ok": True, "decision": "allow", "next_action": "noop"},
            ) as rust_mock, patch.object(
                runs_core.run_service,
                "trigger_pending_heartbeat_schedules",
            ) as trigger_mock:
                payload = runs_core.trigger_pending_heartbeat_schedules(workspace_id="ws-1")

            self.assertEqual(payload, {"acted": False, "started": []})
            rust_mock.assert_called_once()
            trigger_mock.assert_not_called()
        finally:
            runs_core.WEEKLY_SCHEDULES.clear()
            runs_core.WEEKLY_SCHEDULES.update(previous_schedules)

    def test_create_schedule_wrong_next_action_blocks_persistence(self) -> None:
        previous_schedules = dict(runs_core.WEEKLY_SCHEDULES)
        try:
            runs_core.WEEKLY_SCHEDULES.clear()
            body = CronScheduleUpsertRequest(
                name="Morning",
                workspace_id="ws-1",
                enabled=True,
                cron="0 9 * * *",
                timezone="utc",
                wake_mode="now",
                delivery="announce",
                run_request=RunStartRequest(engine="orion", workspace_id="ws-1", user_goal="Check inbox"),
            )
            with patch.object(
                runs_core.rust_runtime_kernel_client,
                "run_trigger_decision",
                return_value={"ok": True, "decision": "allow", "next_action": "execute_scheduled_run"},
            ):
                with self.assertRaises(HTTPException) as raised:
                    asyncio.run(runs_core.create_schedule(body))

            self.assertEqual(raised.exception.status_code, 423)
            self.assertEqual(raised.exception.detail["reason"], "unexpected_next_action:execute_scheduled_run")
            self.assertEqual(runs_core.WEEKLY_SCHEDULES, {})
        finally:
            runs_core.WEEKLY_SCHEDULES.clear()
            runs_core.WEEKLY_SCHEDULES.update(previous_schedules)

    def test_trigger_schedule_now_wrong_next_action_blocks_execution(self) -> None:
        previous_schedules = dict(runs_core.WEEKLY_SCHEDULES)
        try:
            runs_core.WEEKLY_SCHEDULES.clear()
            runs_core.WEEKLY_SCHEDULES["sched-1"] = {
                "id": "sched-1",
                "name": "Morning",
                "workspace_id": "ws-1",
                "enabled": True,
                "cron": "0 9 * * *",
                "timezone": "utc",
                "run_request": {"engine": "orion", "workspace_id": "ws-1", "user_goal": "Check inbox"},
            }
            with patch.object(
                runs_core.rust_runtime_kernel_client,
                "run_trigger_decision",
                return_value={"ok": True, "decision": "allow", "next_action": "create_schedule"},
            ), patch.object(
                runs_core,
                "_execute_scheduled_run_request",
            ) as execute_mock:
                with self.assertRaises(HTTPException) as raised:
                    asyncio.run(runs_core.trigger_schedule_now("sched-1"))

            self.assertEqual(raised.exception.status_code, 423)
            self.assertEqual(raised.exception.detail["reason"], "unexpected_next_action:create_schedule")
            execute_mock.assert_not_called()
        finally:
            runs_core.WEEKLY_SCHEDULES.clear()
            runs_core.WEEKLY_SCHEDULES.update(previous_schedules)

    def test_begin_run_pending_confirmation_delegates_to_run_service(self) -> None:
        run = {
            "run_id": "run-approval-1",
            "status": "running",
            "logs": queue.Queue(),
            "context": {"metadata": {}},
        }
        previous_runs = dict(runs_core.runs)
        try:
            runs_core.runs.clear()
            runs_core.runs["run-approval-1"] = run

            with patch.object(
                runs_core.run_service,
                "begin_run_pending_confirmation",
                return_value={"approval_id": "approval-1"},
            ) as pending_mock:
                payload = runs_core._begin_run_pending_confirmation("run-approval-1", "Confirm run")

            self.assertEqual(payload["approval_id"], "approval-1")
            pending_mock.assert_called_once()
            args, kwargs = pending_mock.call_args
            self.assertEqual(args[0], "run-approval-1")
            self.assertEqual(args[1], "Confirm run")
            self.assertIs(kwargs["runs_by_id"], runs_core.runs)
            self.assertEqual(kwargs["default_approval_ttl_seconds"], runs_core.ORION_APPROVAL_TTL_SECONDS)
            self.assertIs(kwargs["set_run_status_fn"], runs_core.set_run_status)
        finally:
            runs_core.runs.clear()
            runs_core.runs.update(previous_runs)

    def test_set_run_status_delegates_live_transition_to_run_service(self) -> None:
        run = {
            "status": "running",
            "logs": queue.Queue(),
            "context": {"metadata": {}},
        }
        previous_runs = dict(runs_core.runs)
        try:
            runs_core.runs.clear()
            runs_core.runs["run-1"] = run

            with patch.object(runs_core.run_service, "transition_live_run_status", return_value=None) as transition_mock:
                runs_core.set_run_status("run-1", "completed")

            transition_mock.assert_called_once()
            args, kwargs = transition_mock.call_args
            self.assertEqual(args[0], "run-1")
            self.assertEqual(args[1], "completed")
            self.assertEqual(kwargs["run"].get("status"), "running")
            self.assertIs(kwargs["run"].get("logs"), run["logs"])
            self.assertEqual(kwargs["run"].get("context"), run["context"])
            self.assertIs(kwargs["local_pending_run_ids"], runs_core.LOCAL_PENDING_RUN_IDS)
            self.assertIs(kwargs["local_claimed_runs"], runs_core.LOCAL_CLAIMED_RUNS)
        finally:
            runs_core.runs.clear()
            runs_core.runs.update(previous_runs)


if __name__ == "__main__":
    unittest.main()

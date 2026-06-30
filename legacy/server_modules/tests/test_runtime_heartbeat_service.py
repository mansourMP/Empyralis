import threading
import unittest

from server_modules import runtime_heartbeat_service


class _Scheduler:
    def __init__(self) -> None:
        self.started = 0

    def start(self) -> None:
        self.started += 1

    def status(self) -> dict[str, object]:
        return {"running": True}

    def trigger_now(self) -> dict[str, object]:
        return {"triggered": True}


class RuntimeHeartbeatServiceTests(unittest.TestCase):
    def test_heartbeat_scheduler_returns_current_instance(self):
        scheduler = _Scheduler()

        result = runtime_heartbeat_service.heartbeat_scheduler(
            lock=threading.Lock(),
            scheduler=scheduler,
        )

        self.assertIs(result, scheduler)

    def test_ensure_heartbeat_scheduler_started_starts_once(self):
        created = []

        result = runtime_heartbeat_service.ensure_heartbeat_scheduler_started(
            lock=threading.Lock(),
            scheduler=None,
            scheduler_factory=lambda: created.append(_Scheduler()) or created[-1],
        )

        self.assertIs(result, created[0])
        self.assertEqual(created[0].started, 1)

    def test_heartbeat_status_payload_handles_missing_scheduler(self):
        self.assertEqual(
            runtime_heartbeat_service.heartbeat_status_payload(scheduler=None)["ok"],
            False,
        )

    def test_trigger_heartbeat_payload_raises_without_scheduler(self):
        with self.assertRaises(RuntimeError):
            runtime_heartbeat_service.trigger_heartbeat_payload(scheduler=None)

    def test_build_heartbeat_run_callback_returns_noop_summary_without_tasks_or_pending(self):
        callback = runtime_heartbeat_service.build_heartbeat_run_callback(
            build_inbound_agent_turn_request=lambda **kwargs: kwargs,
            trigger_pending_heartbeat_schedules=lambda **kwargs: {"started": []},
            execute_system_agent_turn=lambda **kwargs: {"run_id": "run-1"},
            run_execution_services=lambda: object(),
        )

        payload = callback([], {"workspace_id": "ws-1"})

        self.assertEqual(
            payload,
            {"acted": False, "summary": "No pending heartbeat tasks.", "scheduler_mode": "idle"},
        )

    def test_build_heartbeat_run_callback_queues_background_work_when_lane_queue_is_available(self):
        queued = []

        callback = runtime_heartbeat_service.build_heartbeat_run_callback(
            build_inbound_agent_turn_request=lambda **kwargs: kwargs,
            trigger_pending_heartbeat_schedules=lambda **kwargs: {"started": []},
            execute_system_agent_turn=lambda **kwargs: {"run_id": "run-1"},
            run_execution_services=lambda: object(),
            enqueue_lane_work=lambda **kwargs: queued.append(kwargs) or {"item_id": "lane-1"},
        )

        payload = callback(["Check inbox"], {"workspace_id": "ws-1", "trigger": "scheduled"})

        self.assertTrue(payload["acted"])
        self.assertTrue(payload["queued"])
        self.assertEqual(payload["lane"], "cron")
        self.assertEqual(payload["queue_item_id"], "lane-1")
        self.assertEqual(queued[0]["lane"], "cron")
        self.assertEqual(queued[0]["metadata"]["workspace_id"], "ws-1")

    def test_build_heartbeat_run_callback_starts_run_for_tasks(self):
        captured = {}
        callback = runtime_heartbeat_service.build_heartbeat_run_callback(
            build_inbound_agent_turn_request=lambda **kwargs: captured.setdefault("request", kwargs) or kwargs,
            trigger_pending_heartbeat_schedules=lambda **kwargs: {"started": [{"run_id": "pending-1"}]},
            execute_system_agent_turn=lambda **kwargs: {"run_id": "run-1"},
            run_execution_services=lambda: object(),
            resolve_workspace_tenant_id=lambda workspace_id: "tenant-1" if workspace_id == "ws-1" else None,
        )

        payload = callback(["Check inbox"], {"workspace_id": "ws-1", "trigger": "manual", "heartbeat_file": "HEARTBEAT.md"})

        self.assertTrue(payload["acted"])
        self.assertEqual(payload["run_id"], "run-1")
        self.assertEqual(captured["request"]["execution_mode"], "durable")
        self.assertEqual(captured["request"]["response_mode"], "artifact")
        self.assertIn("Check inbox", captured["request"]["message"])
        self.assertEqual(captured["request"]["context_hints"]["metadata"]["heartbeat_trigger"], "manual")
        self.assertEqual(captured["request"]["tenant_id"], "tenant-1")
        self.assertEqual(captured["request"]["workspace_id"], "ws-1")

    def test_build_heartbeat_run_callback_rejects_missing_workspace_scope(self):
        callback = runtime_heartbeat_service.build_heartbeat_run_callback(
            build_inbound_agent_turn_request=lambda **kwargs: kwargs,
            trigger_pending_heartbeat_schedules=lambda **kwargs: {"started": []},
            execute_system_agent_turn=lambda **kwargs: {"run_id": "run-1"},
            run_execution_services=lambda: object(),
        )

        payload = callback(["Check inbox"], {})

        self.assertEqual(payload, {"acted": False, "summary": "Heartbeat workspace scope is not configured."})

    def test_build_heartbeat_run_callback_rejects_missing_tenant_scope(self):
        callback = runtime_heartbeat_service.build_heartbeat_run_callback(
            build_inbound_agent_turn_request=lambda **kwargs: kwargs,
            trigger_pending_heartbeat_schedules=lambda **kwargs: {"started": []},
            execute_system_agent_turn=lambda **kwargs: {"run_id": "run-1"},
            run_execution_services=lambda: object(),
        )

        payload = callback(["Check inbox"], {"workspace_id": "ws-1"})

        self.assertEqual(payload, {"acted": False, "summary": "Heartbeat tenant scope is not configured."})

    def test_build_heartbeat_turn_request_shapes_canonical_durable_turn(self):
        turn_request = runtime_heartbeat_service.build_heartbeat_turn_request(
            build_inbound_agent_turn_request=lambda **kwargs: kwargs,
            tasks=["Check inbox"],
            metadata={
                "workspace_id": "ws-1",
                "tenant_id": "tenant-1",
                "owner_user_id": "user-1",
                "owner_email": "user@example.com",
                "trigger": "scheduled",
                "execution_target": "local_companion",
                "trust_mode": "guarded",
            },
            pending_started=[{"run_id": "pending-1"}],
        )

        self.assertEqual(turn_request["tenant_id"], "tenant-1")
        self.assertEqual(turn_request["workspace_id"], "ws-1")
        self.assertEqual(turn_request["execution_mode"], "durable")
        self.assertEqual(turn_request["response_mode"], "artifact")
        self.assertEqual(turn_request["actor_id"], "user-1")
        self.assertEqual(turn_request["policy_context"]["execution_target"], "local_companion")
        self.assertEqual(turn_request["policy_context"]["trust_mode"], "guarded")
        self.assertEqual(turn_request["context_hints"]["metadata"]["source"], "heartbeat")

    def test_build_heartbeat_notify_callback_uses_telegram_sender(self):
        seen = []

        async def _send(message: str, workspace_id: str = "default"):
            seen.append((message, workspace_id))

        callback = runtime_heartbeat_service.build_heartbeat_notify_callback(
            handle_telegram_send_message=_send,
            workspace_id="ws-1",
        )
        callback("ping")

        self.assertEqual(seen, [("ping", "ws-1")])

    def test_build_heartbeat_run_callback_executes_due_wake_requests_and_finalizes(self):
        captured = {}
        finalized = []

        callback = runtime_heartbeat_service.build_heartbeat_run_callback(
            build_inbound_agent_turn_request=lambda **kwargs: captured.setdefault("request", kwargs) or kwargs,
            trigger_pending_heartbeat_schedules=lambda **kwargs: {"started": []},
            execute_system_agent_turn=lambda **kwargs: {"run_id": "run-2", "status": "ok"},
            run_execution_services=lambda: object(),
            resolve_workspace_tenant_id=lambda workspace_id: "tenant-1",
            claim_due_scheduler_wake_requests=lambda **kwargs: {
                "items": [{"id": "wake-1", "trigger_kind": "event_trigger", "summary": "Reply backlog detected."}]
            },
            build_wakeup_execution_bundle=lambda **kwargs: {
                "summary": "Scheduler triggered 1 wake request(s) and 0 heartbeat task(s).",
                "recent_changes": [{"id": "evt-1", "source_app": "messages", "summary": "Reply pending for Alex."}],
                "scheduler_goals": ["Keep inbox healthy"],
                "user_preferences": "Prefer short reminders.",
                "policy": {"quiet_hours_start": 23, "quiet_hours_end": 7, "max_runtime_seconds": 20, "plan_tier": "standard"},
                "metadata": {"scheduler_mode": "wakeup", "context_event_ids": ["evt-1"]},
            },
            finalize_scheduler_wake_requests=lambda **kwargs: finalized.append(kwargs) or [{"id": "wake-1", "status": "executed"}],
        )

        payload = callback([], {"workspace_id": "ws-1"})

        self.assertTrue(payload["acted"])
        self.assertEqual(payload["scheduler_mode"], "wakeup")
        self.assertEqual(payload["wake_request_ids"], ["wake-1"])
        self.assertEqual(payload["context_event_ids"], ["evt-1"])
        self.assertIn("Reply backlog detected.", captured["request"]["message"])
        self.assertIn("Prefer short reminders.", captured["request"]["message"])
        self.assertEqual(finalized[0]["status"], "executed")

    def test_build_heartbeat_run_callback_marks_wake_request_failed_on_exception(self):
        finalized = []

        callback = runtime_heartbeat_service.build_heartbeat_run_callback(
            build_inbound_agent_turn_request=lambda **kwargs: kwargs,
            trigger_pending_heartbeat_schedules=lambda **kwargs: {"started": []},
            execute_system_agent_turn=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
            run_execution_services=lambda: object(),
            resolve_workspace_tenant_id=lambda workspace_id: "tenant-1",
            claim_due_scheduler_wake_requests=lambda **kwargs: {"items": [{"id": "wake-1", "summary": "Check back in later."}]},
            build_wakeup_execution_bundle=lambda **kwargs: {"policy": {"quiet_hours_start": 23, "quiet_hours_end": 7, "max_runtime_seconds": 20, "plan_tier": "standard"}},
            finalize_scheduler_wake_requests=lambda **kwargs: finalized.append(kwargs) or [{"id": "wake-1", "status": "failed"}],
        )

        with self.assertRaisesRegex(RuntimeError, "boom"):
            callback([], {"workspace_id": "ws-1"})

        self.assertEqual(finalized[0]["status"], "failed")
        self.assertEqual(finalized[0]["denial_reason"], "execution_failed")


if __name__ == "__main__":
    unittest.main()

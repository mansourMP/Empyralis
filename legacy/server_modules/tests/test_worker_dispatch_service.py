import queue
import threading
import unittest

from fastapi import HTTPException

from server_modules import worker_dispatch_service


class WorkerDispatchServiceTests(unittest.TestCase):
    def test_claim_local_run_assigns_matching_worker_and_persists_state(self) -> None:
        persisted = []
        seen = []
        pending = ["run-1"]
        claimed = {}
        runs_by_id = {"run-1": {"status": "queued_local"}}
        worker_registry = {"worker-1": {"capabilities": ["browser_automation"]}}

        claimed_run_id = worker_dispatch_service.claim_local_run(
            "worker-1",
            required_capabilities=["browser_automation"],
            local_queue_lock=threading.Lock(),
            pending_run_ids=pending,
            claimed_runs=claimed,
            worker_registry=worker_registry,
            runs_by_id=runs_by_id,
            lease_seconds=45,
            cleanup_stale_local_claims_fn=lambda: None,
            ordered_runtime_preferences_for_run_fn=lambda run: [],
            best_online_preferred_runtime_fn=lambda runtime_ids: None,
            required_capabilities_for_run_fn=lambda run: ["browser_automation"],
            normalize_capability_ids_fn=lambda items: [str(item).strip().lower() for item in (items or [])],
            persist_local_runtime_state_fn=lambda: persisted.append(True),
            mark_local_worker_seen_fn=lambda worker_id, run_id, status, note=None: seen.append(
                (worker_id, run_id, status, note)
            ),
            now_iso_fn=lambda: "2026-04-06T00:00:00Z",
        )

        self.assertEqual(claimed_run_id, "run-1")
        self.assertEqual(claimed["run-1"]["worker_id"], "worker-1")
        self.assertEqual(claimed["run-1"]["machine_id"], "worker-1")
        self.assertTrue(str(claimed["run-1"]["lease_id"]))
        self.assertEqual(claimed["run-1"]["lease_seconds"], 45)
        self.assertEqual(persisted, [True])
        self.assertEqual(seen, [("worker-1", "run-1", "busy", "claimed_local_run")])

    def test_pause_local_run_releases_claim_and_sets_waiting_for_input(self) -> None:
        events = []
        statuses = []
        seen = []
        persisted = []
        run = {
            "status": "running_local",
            "logs": queue.Queue(),
            "context": {"metadata": {}},
            "machine_lease_id": "lease-1",
            "local_worker_id": "worker-1",
        }
        claimed = {"run-1": {"worker_id": "worker-1", "machine_id": "machine-1", "lease_id": "lease-1"}}

        result = worker_dispatch_service.pause_local_run(
            "run-1",
            worker_id="worker-1",
            result_text="Need approval",
            result_data={"summary": "Need approval"},
            browser_checkpoint={"session_profile": "default", "next_action_index": 2},
            wait_reason="human_required",
            runs_by_id={"run-1": run},
            local_queue_lock=threading.Lock(),
            claimed_runs=claimed,
            emit_log_fn=lambda logs, level, message, **kwargs: events.append((level, message, kwargs)),
            mark_local_worker_seen_fn=lambda worker_id, run_id, status, note=None: seen.append(
                (worker_id, run_id, status, note)
            ),
            set_run_status_fn=lambda run_id, status: statuses.append((run_id, status)),
            persist_local_runtime_state_fn=lambda: persisted.append(True),
        )

        self.assertEqual(result["waiting_for_input"], True)
        self.assertEqual(claimed, {})
        self.assertEqual(statuses, [("run-1", "waiting_for_input")])
        self.assertEqual(run["context"]["metadata"]["browser_resume_supported"], True)
        self.assertIsNone(run["machine_lease_id"])
        self.assertIsNone(run["local_worker_id"])
        self.assertEqual(persisted, [True])
        self.assertEqual(seen, [("worker-1", None, "idle", "paused_waiting_for_input")])
        self.assertEqual(events[0][2]["event"], "local_pause_required")

    def test_claim_local_run_skips_suspended_machine(self) -> None:
        persisted = []
        seen = []
        pending = ["run-1"]
        claimed = {}
        runs_by_id = {"run-1": {"status": "queued_local"}}
        worker_registry = {"worker-1": {"capabilities": ["browser_automation"], "control_state": "suspended"}}

        claimed_run_id = worker_dispatch_service.claim_local_run(
            "worker-1",
            required_capabilities=["browser_automation"],
            local_queue_lock=threading.Lock(),
            pending_run_ids=pending,
            claimed_runs=claimed,
            worker_registry=worker_registry,
            runs_by_id=runs_by_id,
            lease_seconds=45,
            cleanup_stale_local_claims_fn=lambda: None,
            ordered_runtime_preferences_for_run_fn=lambda run: [],
            best_online_preferred_runtime_fn=lambda runtime_ids: None,
            required_capabilities_for_run_fn=lambda run: ["browser_automation"],
            normalize_capability_ids_fn=lambda items: [str(item).strip().lower() for item in (items or [])],
            persist_local_runtime_state_fn=lambda: persisted.append(True),
            mark_local_worker_seen_fn=lambda worker_id, run_id, status, note=None: seen.append(
                (worker_id, run_id, status, note)
            ),
            now_iso_fn=lambda: "2026-04-06T00:00:00Z",
        )

        self.assertIsNone(claimed_run_id)
        self.assertEqual(claimed, {})
        self.assertEqual(pending, ["run-1"])
        self.assertEqual(persisted, [])
        self.assertEqual(seen, [("worker-1", None, "idle", "idle_machine_suspended")])

    def test_pause_local_run_emits_manual_takeover_event_and_keeps_resume_checkpoint(self) -> None:
        events = []
        run = {
            "status": "running_local",
            "logs": queue.Queue(),
            "context": {
                "metadata": {
                    "pack_inputs": {
                        "operations": [
                            {"tool": "computer_control", "action": "click"},
                            {"tool": "computer_control", "action": "type"},
                        ]
                    }
                }
            },
            "local_execution_checkpoint": {"kind": "local_execution_v1", "next_operation_index": 1, "total_operations": 2},
            "machine_lease_id": "lease-1",
            "local_worker_id": "worker-1",
        }

        worker_dispatch_service.pause_local_run(
            "run-1",
            worker_id="worker-1",
            result_text="Control handed back to the user. Run paused for manual takeover.",
            result_data={"manual_takeover": True, "summary": "Control handed back to the user. Run paused for manual takeover."},
            browser_checkpoint=None,
            wait_reason="manual_takeover_requested",
            runs_by_id={"run-1": run},
            local_queue_lock=threading.Lock(),
            claimed_runs={"run-1": {"worker_id": "worker-1", "machine_id": "machine-1", "lease_id": "lease-1"}},
            emit_log_fn=lambda logs, level, message, **kwargs: events.append((level, message, kwargs)),
            mark_local_worker_seen_fn=lambda worker_id, run_id, status, note=None: None,
            set_run_status_fn=lambda run_id, status: None,
            persist_local_runtime_state_fn=lambda: None,
        )

        self.assertTrue(run["result_data"]["manual_takeover"])
        self.assertEqual(run["result_data"]["local_execution_checkpoint"]["next_operation_index"], 1)
        self.assertTrue(run["context"]["metadata"]["manual_takeover"])
        self.assertTrue(run["context"]["metadata"]["local_execution_resume_supported"])
        self.assertEqual(events[1][2]["event"], "computer_action")

    def test_complete_local_run_finishes_and_persists_memory(self) -> None:
        events = []
        statuses = []
        persisted = []
        runtime_state_persisted = []
        seen = []
        run = {
            "status": "running_local",
            "logs": queue.Queue(),
            "context": {"metadata": {}},
            "machine_lease_id": "lease-1",
            "local_worker_id": "worker-1",
        }
        claimed_runs = {"run-1": {"worker_id": "worker-1", "machine_id": "machine-1", "lease_id": "lease-1"}}

        result = worker_dispatch_service.complete_local_run(
            "run-1",
            worker_id="worker-1",
            result_text="Completed locally",
            result_data={"summary": "Completed locally"},
            usage_masked={"tokens": 10},
            runs_by_id={"run-1": run},
            local_queue_lock=threading.Lock(),
            claimed_runs=claimed_runs,
            emit_log_fn=lambda logs, level, message, **kwargs: events.append((level, message, kwargs)),
            mark_local_worker_seen_fn=lambda worker_id, run_id, status, note=None: seen.append(
                (worker_id, run_id, status, note)
            ),
            set_run_status_fn=lambda run_id, status: statuses.append((run_id, status)),
            persist_run_memory_fn=lambda run_id, payload: persisted.append((run_id, payload["result"])),
            persist_local_runtime_state_fn=lambda: runtime_state_persisted.append(True),
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(run["usage_masked"], {"tokens": 10})
        self.assertEqual(claimed_runs, {})
        self.assertIsNone(run["machine_lease_id"])
        self.assertIsNone(run["local_worker_id"])
        self.assertEqual(statuses, [("run-1", "completed")])
        self.assertEqual(persisted, [("run-1", "Completed locally")])
        self.assertEqual(runtime_state_persisted, [True])
        self.assertEqual(seen, [("worker-1", None, "idle", "completed_run")])
        self.assertEqual(events[1][2]["event"], "run_complete")
        self.assertIsNone(run.get("usage_accounting"))

    def test_complete_local_run_persists_usage_accounting_when_present(self) -> None:
        run = {
            "status": "running_local",
            "logs": queue.Queue(),
            "context": {"metadata": {}},
        }
        claimed_runs = {"run-1": {"worker_id": "worker-1"}}
        usage_masked = {
            "provider": "openai",
            "model": "gpt-4.1",
            "prompt_tokens": 100,
            "completion_tokens": 25,
            "total_tokens": 125,
            "estimated_cost_usd": 0.0004,
            "usage_accounting": {
                "usage_record_id": "uacct_run-1",
                "run_id": "run-1",
                "tenant_id": "tenant-1",
                "workspace_id": "ws-1",
                "deployed_agent_id": None,
                "source_surface": "durable_run",
                "requested_provider": "openai",
                "effective_provider": "openai",
                "requested_model": "gpt-4.1",
                "effective_model": "gpt-4.1",
                "input_tokens": 100,
                "output_tokens": 25,
                "total_tokens": 125,
                "estimated_cost_usd": 0.0004,
                "pricing_known": True,
                "pricing_registry_version": "2026-04-13",
                "pricing_source": "https://platform.openai.com/pricing",
                "estimation_mode": "provider_reported",
                "completed_at": "2026-04-13T00:00:00Z",
                "metadata": {},
            },
        }

        result = worker_dispatch_service.complete_local_run(
            "run-1",
            worker_id="worker-1",
            result_text="Completed locally",
            result_data={"summary": "Completed locally"},
            usage_masked=usage_masked,
            runs_by_id={"run-1": run},
            local_queue_lock=threading.Lock(),
            claimed_runs=claimed_runs,
            emit_log_fn=lambda *args, **kwargs: None,
            mark_local_worker_seen_fn=lambda *args, **kwargs: None,
            set_run_status_fn=lambda *args, **kwargs: None,
            persist_run_memory_fn=lambda *args, **kwargs: None,
            persist_local_runtime_state_fn=lambda: None,
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(run["usage_accounting"]["usage_record_id"], "uacct_run-1")
        self.assertEqual(run["usage_accounting"]["effective_provider"], "openai")

    def test_heartbeat_local_worker_touches_durable_claim(self) -> None:
        touched = []
        run = {"status": "running_local"}
        claimed = {"run-1": {"worker_id": "worker-1", "claimed_at": "2026-04-06T00:00:00Z"}}

        result = worker_dispatch_service.heartbeat_local_worker(
            "worker-1",
            current_run_id="run-1",
            note="step 1",
            runs_by_id={"run-1": run},
            local_queue_lock=threading.Lock(),
            claimed_runs=claimed,
            mark_local_worker_seen_fn=lambda *args, **kwargs: None,
            maybe_emit_local_still_working_fn=lambda *args, **kwargs: False,
            persist_local_runtime_state_fn=lambda: None,
            utc_now_iso_fn=lambda: "2026-04-06T00:00:10Z",
            touch_claim_heartbeat_fn=lambda run_id, worker_id, **kwargs: touched.append((run_id, worker_id, kwargs)),
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(touched, [("run-1", "worker-1", {"note": "step 1", "progress": True})])

    def test_heartbeat_local_run_touches_durable_claim(self) -> None:
        touched = []
        run = {"status": "running_local"}
        claimed = {"run-1": {"worker_id": "worker-1", "claimed_at": "2026-04-06T00:00:00Z"}}

        result = worker_dispatch_service.heartbeat_local_run(
            "run-1",
            worker_id="worker-1",
            note="still going",
            runs_by_id={"run-1": run},
            local_queue_lock=threading.Lock(),
            claimed_runs=claimed,
            maybe_emit_local_still_working_fn=lambda *args, **kwargs: False,
            mark_local_worker_seen_fn=lambda *args, **kwargs: None,
            utc_now_iso_fn=lambda: "2026-04-06T00:00:10Z",
            touch_claim_heartbeat_fn=lambda run_id, worker_id, **kwargs: touched.append((run_id, worker_id, kwargs)),
            progress_event=True,
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(touched, [("run-1", "worker-1", {"note": "still going", "progress": True})])

    def test_fail_local_run_preserves_interrupt_reason_and_clears_binding(self) -> None:
        events = []
        statuses = []
        seen = []
        runtime_state_persisted = []
        run = {
            "status": "running_local",
            "logs": queue.Queue(),
            "context": {"metadata": {}},
            "machine_lease_id": "lease-1",
            "local_worker_id": "worker-1",
            "machine_id": "machine-1",
            "interrupt_requested": True,
            "interrupt_reason": "Operator stop",
            "interrupt_scope": "run",
            "interrupt_runtime_id": "worker-1",
            "interrupt_machine_id": "machine-1",
        }
        claimed_runs = {"run-1": {"worker_id": "worker-1", "machine_id": "machine-1", "lease_id": "lease-1"}}

        result = worker_dispatch_service.fail_local_run(
            "run-1",
            worker_id="worker-1",
            error=None,
            runs_by_id={"run-1": run},
            local_queue_lock=threading.Lock(),
            claimed_runs=claimed_runs,
            emit_log_fn=lambda logs, level, message, **kwargs: events.append((level, message, kwargs)),
            mark_local_worker_seen_fn=lambda worker_id, run_id, status, note=None: seen.append(
                (worker_id, run_id, status, note)
            ),
            set_run_status_fn=lambda run_id, status: statuses.append((run_id, status)),
            persist_local_runtime_state_fn=lambda: runtime_state_persisted.append(True),
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(run["result"], "Operator stop")
        self.assertEqual(claimed_runs, {})
        self.assertIsNone(run["machine_lease_id"])
        self.assertIsNone(run["local_worker_id"])
        self.assertEqual(statuses, [("run-1", "failed")])
        self.assertEqual(runtime_state_persisted, [True])
        self.assertEqual(seen, [("worker-1", None, "idle", "failed_run")])
        self.assertEqual(events[0][2]["event"], "run_interrupted")

    def test_complete_local_run_treats_terminal_state_as_rust_owned_idempotent(self) -> None:
        run = {
            "status": "completed",
            "logs": queue.Queue(),
            "context": {"metadata": {}},
        }

        result = worker_dispatch_service.complete_local_run(
            "run-1",
            worker_id="worker-1",
            result_text="ignored",
            result_data=None,
            usage_masked=None,
            runs_by_id={"run-1": run},
            local_queue_lock=threading.Lock(),
            claimed_runs={},
            emit_log_fn=lambda *args, **kwargs: self.fail("terminal complete should not mutate logs"),
            mark_local_worker_seen_fn=lambda *args, **kwargs: self.fail("terminal complete should not mutate worker state"),
            set_run_status_fn=lambda *args, **kwargs: self.fail("terminal complete should not change status"),
            persist_run_memory_fn=lambda *args, **kwargs: self.fail("terminal complete should not persist memory"),
            persist_local_runtime_state_fn=lambda: self.fail("terminal complete should not persist runtime state"),
        )

        self.assertEqual(result, {"status": "ok", "run_id": "run-1", "already_terminal": True})

    def test_fail_local_run_treats_terminal_state_as_rust_owned_idempotent(self) -> None:
        run = {
            "status": "failed",
            "logs": queue.Queue(),
            "context": {"metadata": {}},
        }

        result = worker_dispatch_service.fail_local_run(
            "run-1",
            worker_id="worker-1",
            error="ignored",
            runs_by_id={"run-1": run},
            local_queue_lock=threading.Lock(),
            claimed_runs={},
            emit_log_fn=lambda *args, **kwargs: self.fail("terminal fail should not mutate logs"),
            mark_local_worker_seen_fn=lambda *args, **kwargs: self.fail("terminal fail should not mutate worker state"),
            set_run_status_fn=lambda *args, **kwargs: self.fail("terminal fail should not change status"),
            persist_local_runtime_state_fn=lambda: self.fail("terminal fail should not persist runtime state"),
        )

        self.assertEqual(result, {"status": "ok", "run_id": "run-1", "already_terminal": True})

    def test_pause_local_run_maps_rust_worker_ownership_denial_to_compatibility_403(self) -> None:
        run = {
            "status": "running_local",
            "logs": queue.Queue(),
            "context": {"metadata": {}},
        }
        with self.assertRaises(HTTPException) as excinfo:
            worker_dispatch_service.pause_local_run(
                "run-1",
                worker_id="worker-2",
                result_text="Need approval",
                result_data={"summary": "Need approval"},
                browser_checkpoint=None,
                wait_reason="human_required",
                runs_by_id={"run-1": run},
                local_queue_lock=threading.Lock(),
                claimed_runs={"run-1": {"worker_id": "worker-1", "machine_id": "machine-1", "lease_id": "lease-1"}},
                emit_log_fn=lambda *args, **kwargs: self.fail("ownership denial should happen before pause mutation"),
                mark_local_worker_seen_fn=lambda *args, **kwargs: self.fail("ownership denial should happen before worker mutation"),
                set_run_status_fn=lambda *args, **kwargs: self.fail("ownership denial should happen before status mutation"),
                persist_local_runtime_state_fn=lambda: self.fail("ownership denial should happen before persistence"),
            )

        self.assertEqual(excinfo.exception.status_code, 403)
        self.assertEqual(excinfo.exception.detail, "Worker does not own this local run.")

    def test_heartbeat_local_run_maps_rust_claim_missing_to_compatibility_409(self) -> None:
        run = {"status": "running_local"}

        with self.assertRaises(HTTPException) as excinfo:
            worker_dispatch_service.heartbeat_local_run(
                "run-1",
                worker_id="worker-1",
                note="still going",
                runs_by_id={"run-1": run},
                local_queue_lock=threading.Lock(),
                claimed_runs={},
                maybe_emit_local_still_working_fn=lambda *args, **kwargs: self.fail("claim-missing denial should happen before heartbeat mutation"),
                mark_local_worker_seen_fn=lambda *args, **kwargs: self.fail("claim-missing denial should happen before worker mutation"),
                utc_now_iso_fn=lambda: "2026-04-06T00:00:10Z",
                touch_claim_heartbeat_fn=lambda *args, **kwargs: self.fail("claim-missing denial should happen before durable touch"),
                progress_event=True,
            )

        self.assertEqual(excinfo.exception.status_code, 409)
        self.assertEqual(excinfo.exception.detail, "Run is not claimed by Gateway.")

    def test_pause_local_run_maps_rust_claim_missing_to_compatibility_409(self) -> None:
        run = {
            "status": "running_local",
            "logs": queue.Queue(),
            "context": {"metadata": {}},
        }

        with self.assertRaises(HTTPException) as excinfo:
            worker_dispatch_service.pause_local_run(
                "run-1",
                worker_id="worker-1",
                result_text="Need approval",
                result_data={"summary": "Need approval"},
                browser_checkpoint=None,
                wait_reason="human_required",
                runs_by_id={"run-1": run},
                local_queue_lock=threading.Lock(),
                claimed_runs={},
                emit_log_fn=lambda *args, **kwargs: self.fail("claim-missing denial should happen before pause mutation"),
                mark_local_worker_seen_fn=lambda *args, **kwargs: self.fail("claim-missing denial should happen before worker mutation"),
                set_run_status_fn=lambda *args, **kwargs: self.fail("claim-missing denial should happen before status mutation"),
                persist_local_runtime_state_fn=lambda: self.fail("claim-missing denial should happen before persistence"),
            )

        self.assertEqual(excinfo.exception.status_code, 409)
        self.assertEqual(excinfo.exception.detail, "Run is not claimed by Gateway.")

    def test_complete_local_run_maps_rust_claim_missing_to_compatibility_409(self) -> None:
        run = {
            "status": "running_local",
            "logs": queue.Queue(),
            "context": {"metadata": {}},
        }

        with self.assertRaises(HTTPException) as excinfo:
            worker_dispatch_service.complete_local_run(
                "run-1",
                worker_id="worker-1",
                result_text="done",
                result_data=None,
                usage_masked=None,
                runs_by_id={"run-1": run},
                local_queue_lock=threading.Lock(),
                claimed_runs={},
                emit_log_fn=lambda *args, **kwargs: self.fail("claim-missing denial should happen before complete mutation"),
                mark_local_worker_seen_fn=lambda *args, **kwargs: self.fail("claim-missing denial should happen before worker mutation"),
                set_run_status_fn=lambda *args, **kwargs: self.fail("claim-missing denial should happen before status mutation"),
                persist_run_memory_fn=lambda *args, **kwargs: self.fail("claim-missing denial should happen before memory persistence"),
                persist_local_runtime_state_fn=lambda: self.fail("claim-missing denial should happen before runtime persistence"),
            )

        self.assertEqual(excinfo.exception.status_code, 409)
        self.assertEqual(excinfo.exception.detail, "Run is not claimed by Gateway.")

    def test_fail_local_run_maps_rust_claim_missing_to_compatibility_409(self) -> None:
        run = {
            "status": "running_local",
            "logs": queue.Queue(),
            "context": {"metadata": {}},
        }

        with self.assertRaises(HTTPException) as excinfo:
            worker_dispatch_service.fail_local_run(
                "run-1",
                worker_id="worker-1",
                error="boom",
                runs_by_id={"run-1": run},
                local_queue_lock=threading.Lock(),
                claimed_runs={},
                emit_log_fn=lambda *args, **kwargs: self.fail("claim-missing denial should happen before fail mutation"),
                mark_local_worker_seen_fn=lambda *args, **kwargs: self.fail("claim-missing denial should happen before worker mutation"),
                set_run_status_fn=lambda *args, **kwargs: self.fail("claim-missing denial should happen before status mutation"),
                persist_local_runtime_state_fn=lambda: self.fail("claim-missing denial should happen before runtime persistence"),
            )

        self.assertEqual(excinfo.exception.status_code, 409)
        self.assertEqual(excinfo.exception.detail, "Run is not claimed by Gateway.")


if __name__ == "__main__":
    unittest.main()

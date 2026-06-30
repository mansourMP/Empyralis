import asyncio
import queue
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from server_modules import outbox_service, run_service, run_state_repository, runs_core, runs_engine, runs_execution, runs_output, runtime_runs_api, shared
from server_modules.runtime_state_store import (
    build_runtime_checkpoint_manifest,
    init_runtime_state_db,
    load_local_runtime_state,
    rehearse_runtime_checkpoint_restore,
)


class RuntimeDurableStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "runtime-state.sqlite3"
        init_runtime_state_db(self.db_path)
        self.patchers = [
            patch.object(runs_core, "ORION_RUNTIME_STATE_DB", self.db_path),
            patch.object(runs_execution, "ORION_RUNTIME_STATE_DB", self.db_path),
            patch.object(runs_output, "ORION_RUNTIME_STATE_DB", self.db_path),
        ]
        self.live_run_store: dict[str, dict] = {}
        self.archive_store: dict[str, dict] = {}
        self.transitions: list[dict] = []
        self.patchers.extend(
            [
                patch.object(
                    run_state_repository,
                    "sync_create_live_run_initial",
                    side_effect=self._sync_create_live_run_initial,
                ),
                patch.object(
                    run_state_repository,
                    "sync_upsert_live_run",
                    side_effect=self._sync_upsert_live_run,
                ),
                patch.object(
                    run_state_repository,
                    "sync_record_transition",
                    side_effect=self._sync_record_transition,
                ),
                patch.object(
                    run_state_repository,
                    "update_live_run_if_version_matches",
                    side_effect=self._update_live_run_if_version_matches,
                ),
                patch.object(
                    run_state_repository,
                    "sync_delete_live_run",
                    side_effect=self._sync_delete_live_run,
                ),
                patch.object(
                    run_state_repository,
                    "sync_list_live_runs",
                    side_effect=self._sync_list_live_runs,
                ),
                patch.object(
                    run_state_repository,
                    "sync_archive_run",
                    side_effect=self._sync_archive_run,
                ),
                patch.object(
                    run_state_repository,
                    "sync_list_run_archive",
                    side_effect=self._sync_list_run_archive,
                ),
                patch.object(
                    run_state_repository,
                    "dispatch_repository_call",
                    side_effect=self._dispatch_repository_call,
                ),
            ]
        )
        for patcher in self.patchers:
            patcher.start()
        shared.sync_acp_manager_paths(runtime_db_path=self.db_path)
        runs_core.ACP_MANAGER.reload_runtime_state()
        runs_core.runs.clear()
        runs_engine.runs.clear()
        runs_execution.runs.clear()
        runs_core.RUN_QUEUE_INDEX.clear()
        runs_engine.RUN_QUEUE_INDEX.clear()
        runs_execution.RUN_QUEUE_INDEX.clear()
        runs_core.LOCAL_PENDING_RUN_IDS.clear()
        runs_execution.LOCAL_PENDING_RUN_IDS.clear()
        runs_core.LOCAL_CLAIMED_RUNS.clear()
        runs_execution.LOCAL_CLAIMED_RUNS.clear()
        runs_core.LOCAL_WORKER_REGISTRY.clear()
        runs_execution.LOCAL_WORKER_REGISTRY.clear()

    def tearDown(self) -> None:
        runs_core.runs.clear()
        runs_engine.runs.clear()
        runs_execution.runs.clear()
        runs_core.RUN_QUEUE_INDEX.clear()
        runs_engine.RUN_QUEUE_INDEX.clear()
        runs_execution.RUN_QUEUE_INDEX.clear()
        runs_core.LOCAL_PENDING_RUN_IDS.clear()
        runs_execution.LOCAL_PENDING_RUN_IDS.clear()
        runs_core.LOCAL_CLAIMED_RUNS.clear()
        runs_execution.LOCAL_CLAIMED_RUNS.clear()
        runs_core.LOCAL_WORKER_REGISTRY.clear()
        runs_execution.LOCAL_WORKER_REGISTRY.clear()
        for patcher in reversed(self.patchers):
            patcher.stop()
        shared.sync_acp_manager_paths(runtime_db_path=runs_core.ORION_RUNTIME_STATE_DB)
        runs_core.ACP_MANAGER.reload_runtime_state()
        self.tmpdir.cleanup()

    def test_create_run_persists_live_run_and_local_queue_state(self):
        run_id = runs_execution.create_run(
            "orion",
            {
                "workspace_id": "ws-local",
                "user_goal": "Summarize inbox",
                "metadata": {"execution_target_selected": "local_companion"},
            },
        )

        live_runs = run_state_repository.sync_list_live_runs()
        self.assertEqual(len(live_runs), 1)
        self.assertEqual(live_runs[0]["run_id"], run_id)
        self.assertEqual(live_runs[0]["status"], "queued_local")
        self.assertEqual(live_runs[0]["_durable_version"], 3)

        local_state = load_local_runtime_state(self.db_path)
        self.assertEqual(local_state["pending_run_ids"], [run_id])
        self.assertEqual(local_state["claimed_runs"], {})

    def test_create_run_refuses_memory_first_registration_when_initial_durable_insert_fails(self):
        with patch.object(
            run_state_repository,
            "sync_create_live_run_initial",
            side_effect=run_state_repository.RunStatePersistenceError("db down"),
        ):
            with self.assertRaises(run_state_repository.RunStatePersistenceError):
                runs_execution.create_run(
                    "orion",
                    {
                        "workspace_id": "ws-local",
                        "user_goal": "Summarize inbox",
                        "metadata": {"execution_target_selected": "local_companion"},
                    },
                )

        self.assertEqual(self.live_run_store, {})
        self.assertEqual(runs_execution.runs, {})
        local_state = load_local_runtime_state(self.db_path)
        self.assertEqual(local_state["pending_run_ids"], [])
        self.assertEqual(local_state["claimed_runs"], {})

    def test_load_local_runtime_state_remains_explicit_checkpoint_restore_path(self):
        outbox_service.persist_local_runtime_state(
            db_path=self.db_path,
            pending_run_ids=["run-local-1"],
            claimed_runs={
                "run-local-2": {
                    "worker_id": "worker-1",
                    "claimed_at": "2026-04-12T00:00:00Z",
                    "lease_id": "lease-1",
                }
            },
            runtime_registrations={
                "worker-1": {
                    "runtime_id": "worker-1",
                    "status": "busy",
                    "current_run_id": "run-local-2",
                }
            },
        )

        local_state = load_local_runtime_state(self.db_path)

        self.assertEqual(local_state["pending_run_ids"], ["run-local-1"])
        self.assertEqual(local_state["claimed_runs"]["run-local-2"]["lease_id"], "lease-1")
        self.assertEqual(local_state["runtime_registrations"]["worker-1"]["current_run_id"], "run-local-2")

    def test_build_runtime_checkpoint_manifest_declares_checkpoint_only_store(self):
        manifest = build_runtime_checkpoint_manifest(self.db_path)

        self.assertEqual(manifest["store_id"], "runtime_checkpoint_sqlite")
        self.assertEqual(manifest["authority_mode"], "checkpoint_restore_only")
        self.assertIn("local_pending_queue", manifest["sqlite_tables"])
        self.assertIn("notifications", manifest["sqlite_tables"])
        self.assertIn("runtime_registrations", manifest["checkpoint_only_state_classes"])

    def test_rehearse_runtime_checkpoint_restore_reports_counts_and_ok(self):
        outbox_service.persist_local_runtime_state(
            db_path=self.db_path,
            pending_run_ids=["run-local-1"],
            claimed_runs={
                "run-local-2": {
                    "worker_id": "worker-1",
                    "claimed_at": "2026-04-12T00:00:00Z",
                    "lease_id": "lease-1",
                }
            },
            runtime_registrations={
                "worker-1": {
                    "runtime_id": "worker-1",
                    "status": "busy",
                    "current_run_id": "run-local-2",
                }
            },
        )

        rehearsal = rehearse_runtime_checkpoint_restore(self.db_path)

        self.assertTrue(rehearsal["ok"])
        self.assertEqual(rehearsal["store_id"], "runtime_checkpoint_sqlite")
        self.assertEqual(rehearsal["restored_state_summary"]["pending_run_count"], 1)
        self.assertEqual(rehearsal["restored_state_summary"]["claimed_run_count"], 1)
        self.assertEqual(rehearsal["restored_state_summary"]["runtime_registration_count"], 1)
        self.assertIn("local_claims", rehearsal["table_counts"])

    def test_stale_snapshot_does_not_overwrite_newer_durable_state(self):
        run_id = runs_execution.create_run(
            "orion",
            {
                "workspace_id": "ws-local",
                "user_goal": "Summarize inbox",
                "metadata": {"execution_target_selected": "local_companion"},
            },
        )
        current = dict(self.live_run_store[run_id])
        stale = dict(current)
        stale["status"] = "starting"
        stale["_durable_version"] = 0
        self._dispatch_repository_call(
            run_state_repository.update_live_run_if_version_matches(
                run_id,
                stale["workspace_id"],
                stale["tenant_id"],
                stale["status"],
                stale,
                stale.get("trace_id") or "",
                expected_version=0,
            ),
            operation="stale_snapshot_test",
        )

        self.assertEqual(self.live_run_store[run_id]["status"], "queued_local")
        self.assertEqual(self.live_run_store[run_id]["_durable_version"], 3)

    def test_post_insert_activation_failure_leaves_durable_row_registered(self):
        with self.assertRaises(RuntimeError):
            run_service.create_live_run(
                "orion",
                {"workspace_id": "ws-remote", "user_goal": "Run remotely", "metadata": {}},
                runtime_db_path=self.db_path,
                sync_acp_manager_paths_fn=shared.sync_acp_manager_paths,
                selected_execution_target_from_context_fn=lambda context: "hosted",
                runs_by_id=runs_execution.runs,
                run_queue_index=runs_execution.RUN_QUEUE_INDEX,
                metrics_inc_fn=lambda *_args, **_kwargs: None,
                persist_live_run_state_fn=runs_output._persist_live_run_state,
                hydrate_run_memory_context_fn=lambda *_args, **_kwargs: None,
                enqueue_local_companion_run_fn=lambda *_args, **_kwargs: None,
                start_background_run_fn=lambda _run_id: (_ for _ in ()).throw(RuntimeError("dispatch failed")),
                local_companion_target="local_companion",
                provider_profiles={},
                memory_enabled=False,
                utc_now_iso_fn=lambda: "2026-04-12T00:00:00Z",
            )

        self.assertEqual(len(self.live_run_store), 1)
        only_run = next(iter(self.live_run_store.values()))
        self.assertEqual(only_run["status"], "starting")
        self.assertEqual(only_run["_durable_version"], 0)

    def test_load_live_runtime_state_restores_waiting_confirmation(self):
        run_id = "run-waiting-1"
        run = {
            "run_id": run_id,
            "status": "waiting_for_input",
            "engine": "orion",
            "context": {
                "workspace_id": "ws-confirm",
                "user_goal": "Send this reply",
                "metadata": {},
            },
            "created_at": "2026-03-29T00:00:00Z",
            "updated_at": "2026-03-29T00:01:00Z",
            "result": None,
            "result_data": None,
            "events": [],
            "tool_policy_audit": [],
            "memory_trace": {
                "enabled": False,
                "reads": [],
                "writes": [],
                "last_error": None,
                "updated_at": "2026-03-29T00:01:00Z",
            },
            "pending_approval": {
                "approval_id": "approval-123",
                "status": "waiting",
                "scope": "once",
                "reusable": False,
                "prompt": "Confirm send",
            },
            "_event_seq": 0,
        }
        runs_output._persist_live_run_state(run_id, run)

        runs_core._load_live_runtime_state()

        restored = runs_core.runs[run_id]
        self.assertEqual(restored["status"], "waiting_for_input")
        self.assertEqual(restored["pending_approval"]["approval_id"], "approval-123")
        self.assertIsInstance(restored["logs"], queue.Queue)
        self.assertIsInstance(restored["input_queue"], queue.Queue)
        self.assertEqual(runs_core.LOCAL_PENDING_RUN_IDS, [])

    def test_load_live_runtime_state_prunes_orphaned_local_queue_cache(self):
        outbox_service.persist_local_runtime_state(
            db_path=self.db_path,
            pending_run_ids=["orphan-run"],
            claimed_runs={"ghost-run": {"worker_id": "worker-1", "claimed_at": "2026-04-10T00:00:00Z"}},
            runtime_registrations={"worker-1": {"status": "busy", "current_run_id": "ghost-run"}},
        )

        runs_core._load_live_runtime_state()

        self.assertEqual(runs_core.LOCAL_PENDING_RUN_IDS, [])
        self.assertEqual(runs_core.LOCAL_CLAIMED_RUNS, {})
        local_state = load_local_runtime_state(self.db_path)
        self.assertEqual(local_state["pending_run_ids"], [])
        self.assertEqual(local_state["claimed_runs"], {})

    @patch("server_modules.runs_engine._append_approval_audit")
    def test_wait_for_human_response_consumes_resolved_confirmation_after_restart(self, audit_mock):
        run_id = "run-resume-1"
        log_queue: queue.Queue = queue.Queue()
        run = {
            "run_id": run_id,
            "status": "waiting_for_input",
            "engine": "orion",
            "logs": log_queue,
            "input_queue": queue.Queue(),
            "thread_id": None,
            "context": {"workspace_id": "default", "metadata": {}},
            "created_at": "2026-03-29T00:00:00Z",
            "updated_at": "2026-03-29T00:01:00Z",
            "result": None,
            "result_data": None,
            "events": [],
            "tool_policy_audit": [],
            "memory_trace": {
                "enabled": False,
                "reads": [],
                "writes": [],
                "last_error": None,
                "updated_at": "2026-03-29T00:01:00Z",
            },
            "pending_confirmation": {
                "approval_id": "approval-resume-1",
                "correlation_id": "run:resume:approval-1",
                "status": "resolved",
                "scope": "once",
                "reusable": False,
                "prompt": "Confirm send",
                "decision": "proceed",
                "note": "resume after restart",
            },
            "pending_approval": {
                "approval_id": "approval-resume-1",
                "correlation_id": "run:resume:approval-1",
                "status": "resolved",
                "scope": "once",
                "reusable": False,
                "prompt": "Confirm send",
                "decision": "proceed",
                "note": "resume after restart",
            },
            "_event_seq": 0,
            "_resume_after_confirmation_scheduled": True,
        }
        runs_core.runs[run_id] = run
        runs_engine.runs[run_id] = run
        runs_execution.runs[run_id] = run
        stored_run = runs_core.runs[run_id]
        runs_core.RUN_QUEUE_INDEX[id(log_queue)] = run_id
        runs_engine.RUN_QUEUE_INDEX[id(log_queue)] = run_id
        runs_execution.RUN_QUEUE_INDEX[id(log_queue)] = run_id

        response = runs_engine.wait_for_human_response(run_id, "Confirm send")

        self.assertTrue(response["approved"])
        self.assertEqual(stored_run["status"], "executing")
        self.assertIsNone(stored_run["pending_confirmation"])
        self.assertIsNone(stored_run["pending_approval"])
        self.assertFalse(stored_run.get("_resume_after_confirmation_scheduled"))
        self.assertGreaterEqual(audit_mock.call_count, 2)

    @patch("server_modules.runs_core._append_approval_audit")
    def test_wait_for_human_response_consumes_resume_confirmation_token(self, audit_mock):
        run_id = "run-resume-token-1"
        log_queue: queue.Queue = queue.Queue()
        run = {
            "run_id": run_id,
            "status": "waiting_for_input",
            "engine": "orion",
            "logs": log_queue,
            "input_queue": queue.Queue(),
            "thread_id": None,
            "context": {"workspace_id": "default", "metadata": {}},
            "created_at": "2026-03-29T00:00:00Z",
            "updated_at": "2026-03-29T00:01:00Z",
            "result": None,
            "result_data": None,
            "events": [],
            "tool_policy_audit": [],
            "memory_trace": {
                "enabled": False,
                "reads": [],
                "writes": [],
                "last_error": None,
                "updated_at": "2026-03-29T00:01:00Z",
            },
            "pending_confirmation": {
                "approval_id": "approval-token-1",
                "correlation_id": "run:resume:approval-token-1",
                "status": "waiting",
                "scope": "once",
                "reusable": False,
                "prompt": "Confirm send",
            },
            "pending_approval": {
                "approval_id": "approval-token-1",
                "correlation_id": "run:resume:approval-token-1",
                "status": "waiting",
                "scope": "once",
                "reusable": False,
                "prompt": "Confirm send",
            },
            "_resume_confirmation_token": {
                "approval_id": "approval-token-1",
                "correlation_id": "run:resume:approval-token-1",
                "prompt": "Confirm send",
                "decision": "approve",
                "note": "restored standalone approval",
                "resolved_at": "2026-03-29T00:01:30Z",
                "scope": "once",
                "reusable": False,
            },
            "_event_seq": 0,
        }
        runs_core.runs[run_id] = run
        runs_engine.runs[run_id] = run
        runs_execution.runs[run_id] = run
        stored_run = runs_core.runs[run_id]
        runs_core.RUN_QUEUE_INDEX[id(log_queue)] = run_id
        runs_engine.RUN_QUEUE_INDEX[id(log_queue)] = run_id
        runs_execution.RUN_QUEUE_INDEX[id(log_queue)] = run_id

        response = runs_engine.wait_for_human_response(run_id, "Confirm send")

        self.assertTrue(response["approved"])
        self.assertEqual(stored_run["status"], "executing")
        self.assertIsNone(stored_run["pending_confirmation"])
        self.assertIsNone(stored_run["pending_approval"])
        self.assertNotIn("_resume_confirmation_token", stored_run)

    @patch("server_modules.runtime_runs_api.threading.Thread")
    @patch("server_modules.runtime_runs_api._late_server_export")
    def test_schedule_restored_run_resume_starts_once(self, late_export_mock, thread_cls_mock):
        persist_mock = Mock()
        mission_mock = Mock()

        def late_export(name: str):
            if name == "_persist_live_run_state":
                return persist_mock
            if name == "run_mission":
                return mission_mock
            raise AssertionError(name)

        late_export_mock.side_effect = late_export
        worker = Mock()
        thread_cls_mock.return_value = worker
        run = {
            "run_id": "run-resume-2",
            "status": "waiting_for_input",
            "thread_id": None,
        }

        first = runtime_runs_api._schedule_restored_run_resume("run-resume-2", run)
        second = runtime_runs_api._schedule_restored_run_resume("run-resume-2", run)

        self.assertTrue(first)
        self.assertTrue(second)
        self.assertTrue(run["_resume_after_confirmation_scheduled"])
        persist_mock.assert_called_once()
        worker.start.assert_called_once()

    def test_serialize_run_snapshot_preserves_pending_confirmation_alias(self):
        run = {
            "status": "waiting_for_input",
            "engine": "orion",
            "context": {
                "workspace_id": "default",
                "metadata": {},
            },
            "created_at": "2026-03-29T00:00:00Z",
            "updated_at": "2026-03-29T00:01:00Z",
            "completed_at": None,
            "duration_ms": None,
            "result": None,
            "result_data": {
                "outputs": {
                    "actions": [
                        {
                            "tool": "browser_automation",
                            "tabs": [
                                {"tabId": "main", "url": "https://example.com/login", "active": True},
                                {"tabId": "tab-2", "url": "https://example.com/help", "active": False},
                            ],
                            "console_entries": [{"tab": "main", "message": "warn"}],
                            "network_failures": [{"tab": "main", "url": "https://example.com/api", "error": "net::ERR_ABORTED"}],
                            "role_snapshot": [{"tag": "button", "role": "button", "name": "Sign in"}],
                            "accessibility_snapshot": {"nodes": [{"role": "RootWebArea", "name": "Example"}]},
                        }
                    ]
                }
            },
            "events": [],
            "tool_policy_audit": [],
            "usage_masked": {},
            "memory_trace": {
                "enabled": False,
                "reads": [],
                "writes": [],
                "last_error": None,
                "updated_at": "2026-03-29T00:01:00Z",
            },
            "pending_confirmation": {
                "approval_id": "approval-serialize-1",
                "prompt": "Confirm local execution",
                "status": "waiting",
            },
            "browser_checkpoint": {
                "current_url": "https://example.com/login",
                "next_action_index": 2,
                "session_profile": "qa-browser",
            },
        }

        snapshot = runs_output._serialize_run_snapshot("run-serialize-1", run)

        self.assertEqual(snapshot["pending_confirmation"]["approval_id"], "approval-serialize-1")
        self.assertEqual(snapshot["pending_approval"]["approval_id"], "approval-serialize-1")
        self.assertEqual(snapshot["browser_checkpoint"]["next_action_index"], 2)
        self.assertEqual(len(snapshot["browser_introspection"]["tabs"]), 2)
        self.assertEqual(snapshot["browser_introspection"]["console_entries"][0]["message"], "warn")

    def test_serialize_run_snapshot_projects_usage_accounting_without_legacy_usage_masked(self):
        run = {
            "status": "completed",
            "created_at": "2026-04-13T00:00:00Z",
            "updated_at": "2026-04-13T00:01:00Z",
            "completed_at": "2026-04-13T00:01:00Z",
            "context": {
                "tenant_id": "tenant-1",
                "workspace_id": "ws-1",
                "metadata": {"deployed_agent_id": "dagent-1"},
            },
            "usage_accounting": {
                "usage_record_id": "uacct_run-serialize-usage",
                "run_id": "run-serialize-usage",
                "tenant_id": "tenant-1",
                "workspace_id": "ws-1",
                "deployed_agent_id": "dagent-1",
                "source_surface": "deployed_agent_channel",
                "requested_provider": "openai",
                "effective_provider": "openai",
                "requested_model": "gpt-4.1",
                "effective_model": "gpt-4.1",
                "input_tokens": 120,
                "output_tokens": 30,
                "total_tokens": 150,
                "estimated_cost_usd": 0.00048,
                "pricing_known": True,
                "pricing_registry_version": "2026-04-13",
                "pricing_source": "https://platform.openai.com/pricing",
                "estimation_mode": "provider_reported",
                "completed_at": "2026-04-13T00:01:00Z",
                "metadata": {},
            },
            "events": [],
            "tool_policy_audit": [],
        }

        snapshot = runs_output._serialize_run_snapshot("run-serialize-usage", run)

        self.assertEqual(snapshot["usage_provider"], "openai")
        self.assertEqual(snapshot["usage_model"], "gpt-4.1")
        self.assertEqual(snapshot["usage_accounting"]["usage_record_id"], "uacct_run-serialize-usage")
        self.assertEqual(snapshot["usage_masked"]["usage_accounting"]["usage_record_id"], "uacct_run-serialize-usage")

    @patch("server_modules.runtime_runs_api._late_server_export")
    def test_schedule_restored_run_resume_requeues_local_checkpoint_run(self, late_export_mock):
        persisted = []
        enqueued = []

        def late_export(name: str):
            if name == "_persist_live_run_state":
                return lambda run_id, run: persisted.append((run_id, run.get("status")))
            if name == "_enqueue_local_companion_run":
                return lambda run_id, message="Run queued for Local Companion execution.", event="local_queued": enqueued.append((run_id, message, event))
            if name == "run_mission":
                return Mock()
            raise AssertionError(name)

        late_export_mock.side_effect = late_export
        run = {
            "run_id": "run-local-resume-1",
            "status": "waiting_for_input",
            "thread_id": None,
            "context": {
                "metadata": {
                    "execution_target_selected": "local_companion",
                }
            },
            "browser_checkpoint": {
                "current_url": "https://example.com/private",
                "next_action_index": 3,
                "session_profile": "qa-browser",
            },
        }

        resumed = runtime_runs_api._schedule_restored_run_resume("run-local-resume-1", run)

        self.assertTrue(resumed)
        self.assertTrue(run["_resume_after_confirmation_scheduled"])
        self.assertEqual(enqueued[0][0], "run-local-resume-1")
        self.assertEqual(enqueued[0][2], "local_resumed_from_checkpoint")

    def _sync_upsert_live_run(
        self,
        run_id: str,
        workspace_id: str,
        tenant_id: str,
        state: str,
        payload: dict,
        trace_id: str,
    ) -> None:
        snapshot = dict(payload)
        snapshot["run_id"] = run_id
        snapshot["workspace_id"] = workspace_id
        snapshot["tenant_id"] = tenant_id
        snapshot["status"] = state
        snapshot["trace_id"] = trace_id
        if "_durable_version" in snapshot:
            snapshot["_durable_version"] = int(snapshot.get("_durable_version") or 0) + 1
        else:
            snapshot["_durable_version"] = int(self.live_run_store.get(run_id, {}).get("_durable_version") or 0)
        self.live_run_store[run_id] = snapshot

    def _sync_create_live_run_initial(
        self,
        run_id: str,
        workspace_id: str,
        tenant_id: str,
        state: str,
        payload: dict,
        trace_id: str,
    ) -> dict:
        snapshot = dict(payload)
        snapshot["run_id"] = run_id
        snapshot["workspace_id"] = workspace_id
        snapshot["tenant_id"] = tenant_id
        snapshot["status"] = state
        snapshot["trace_id"] = trace_id
        snapshot["_durable_version"] = 0
        snapshot["_durable_registered_at"] = "2026-04-12T00:00:00Z"
        self.live_run_store[run_id] = snapshot
        return {"version": 0, "registered_at": "2026-04-12T00:00:00Z"}

    def _sync_record_transition(
        self,
        run_id: str,
        from_state: str,
        to_state: str,
        actor: str,
        trace_id: str,
    ) -> None:
        self.transitions.append(
            {
                "run_id": run_id,
                "from_state": from_state,
                "to_state": to_state,
                "actor": actor,
                "trace_id": trace_id,
            }
        )

    def _sync_delete_live_run(self, run_id: str) -> None:
        self.live_run_store.pop(run_id, None)

    def _sync_list_live_runs(self) -> list[dict]:
        return [dict(item) for item in self.live_run_store.values()]

    def _sync_archive_run(self, run_id: str, final_state: str, payload: dict, trace_id: str) -> None:
        snapshot = dict(payload)
        snapshot["run_id"] = run_id
        snapshot["status"] = final_state
        snapshot["trace_id"] = trace_id
        self.archive_store[run_id] = snapshot

    def _sync_list_run_archive(self, limit: int = 200) -> list[dict]:
        items = list(self.archive_store.values())
        return [dict(item) for item in items[: max(1, int(limit or 0))]]

    async def _update_live_run_if_version_matches(
        self,
        run_id: str,
        workspace_id: str,
        tenant_id: str,
        state: str,
        payload: dict,
        trace_id: str,
        *,
        expected_version: int,
    ) -> int | None:
        existing = dict(self.live_run_store.get(run_id) or {})
        if not existing:
            return None
        existing_version = int(existing.get("_durable_version") or 0)
        desired = dict(payload)
        desired["run_id"] = run_id
        desired["workspace_id"] = workspace_id
        desired["tenant_id"] = tenant_id
        desired["status"] = state
        desired["trace_id"] = trace_id
        desired["_durable_version"] = int(expected_version or 0) + 1
        if existing_version == int(expected_version or 0):
            self.live_run_store[run_id] = desired
            return desired["_durable_version"]
        existing_compare = dict(existing)
        existing_compare.pop("_durable_version", None)
        desired_compare = dict(desired)
        desired_compare.pop("_durable_version", None)
        if existing_version >= desired["_durable_version"] and existing_compare == desired_compare:
            return existing_version
        return None

    def _dispatch_repository_call(self, awaitable, *, operation: str) -> None:
        try:
            asyncio.run(awaitable)
        except run_state_repository.RunStateVersionConflictError:
            return
        except Exception:
            close_fn = getattr(awaitable, "close", None)
            if callable(close_fn):
                close_fn()
            return


if __name__ == "__main__":
    unittest.main()

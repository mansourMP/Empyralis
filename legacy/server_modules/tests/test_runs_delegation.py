import queue
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from server_modules import run_state_repository, runs_delegation, runs_output, shared
from server_modules.runtime_state_store import init_runtime_state_db


class _ImmediateTimer:
    def __init__(self, _delay, func, args=None, kwargs=None):
        self._func = func
        self._args = args or ()
        self._kwargs = kwargs or {}
        self.daemon = False

    def start(self):
        self._func(*self._args, **self._kwargs)


class RunsDelegationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "runtime-state.sqlite3"
        init_runtime_state_db(self.db_path)
        self.live_run_store: dict[str, dict] = {}
        self.archive_store: dict[str, dict] = {}
        self.patchers = [
            patch.object(runs_delegation, "ORION_RUNTIME_STATE_DB", self.db_path),
            patch.object(runs_output, "ORION_RUNTIME_STATE_DB", self.db_path),
            patch.object(run_state_repository, "sync_upsert_live_run", side_effect=self._sync_upsert_live_run),
            patch.object(run_state_repository, "sync_delete_live_run", side_effect=self._sync_delete_live_run),
            patch.object(run_state_repository, "sync_list_live_runs", side_effect=self._sync_list_live_runs),
            patch.object(run_state_repository, "sync_archive_run", side_effect=self._sync_archive_run),
            patch.object(run_state_repository, "sync_list_run_archive", side_effect=self._sync_list_run_archive),
        ]
        for patcher in self.patchers:
            patcher.start()
        shared.sync_acp_manager_paths(runtime_db_path=self.db_path)
        runs_delegation.ACP_MANAGER.reload_runtime_state()
        runs_delegation.runs.clear()
        with runs_delegation.RUN_HISTORY_LOCK:
            runs_delegation.RUN_HISTORY.clear()
        with runs_delegation._AUTO_RETRY_PENDING_LOCK:
            runs_delegation._AUTO_RETRY_PENDING.clear()
            runs_delegation._AUTO_RETRY_ATTEMPTS.clear()

    def tearDown(self) -> None:
        runs_delegation.runs.clear()
        runs_delegation.RUN_QUEUE_INDEX.clear()
        with runs_delegation.RUN_HISTORY_LOCK:
            runs_delegation.RUN_HISTORY.clear()
        with runs_delegation._AUTO_RETRY_PENDING_LOCK:
            runs_delegation._AUTO_RETRY_PENDING.clear()
            runs_delegation._AUTO_RETRY_ATTEMPTS.clear()
        for patcher in reversed(self.patchers):
            patcher.stop()
        shared.sync_acp_manager_paths(runtime_db_path=runs_delegation.ORION_RUNTIME_STATE_DB)
        runs_delegation.ACP_MANAGER.reload_runtime_state()
        self.tmpdir.cleanup()

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
        self.live_run_store[run_id] = snapshot

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

    def test_lookup_run_snapshot_reads_live_run_without_name_error(self):
        runs_delegation.runs["parent-run"] = {
            "status": "completed",
            "context": {"metadata": {}},
            "result_data": {"summary": "done"},
        }

        snapshot = runs_delegation._lookup_run_snapshot("parent-run")

        self.assertEqual(snapshot["run_id"], "parent-run")
        self.assertEqual(snapshot["status"], "completed")

    def test_lookup_run_snapshot_reads_archived_run_without_name_error(self):
        with runs_delegation.RUN_HISTORY_LOCK:
            runs_delegation.RUN_HISTORY.append(
                {
                    "run_id": "archived-run",
                    "status": "completed",
                    "context": {"metadata": {}},
                    "result_data": {"summary": "archived"},
                }
            )

        snapshot = runs_delegation._lookup_run_snapshot("archived-run")

        self.assertEqual(snapshot["run_id"], "archived-run")
        self.assertEqual(snapshot["status"], "completed")

    def test_refresh_parent_delegation_state_updates_live_parent_and_emits_log(self):
        parent_run_id = "11111111-1111-4111-8111-111111111111"
        child_run_id = "22222222-2222-4222-8222-222222222222"
        parent_logs = queue.Queue()
        runs_delegation.runs[parent_run_id] = {
            "status": "running",
            "logs": parent_logs,
            "events": [],
            "_event_seq": 0,
            "context": {"metadata": {}},
            "result_data": {},
        }
        runs_delegation.RUN_QUEUE_INDEX[id(parent_logs)] = parent_run_id
        runs_delegation.runs[child_run_id] = {
            "status": "completed",
            "context": {
                "metadata": {
                    "parent_run_id": parent_run_id,
                    "agent_role": "research",
                    "delegated_by_role": "orchestrator",
                }
            },
            "result": "Three market insights ready.",
        }

        summary = runs_delegation._refresh_parent_delegation_state(parent_run_id)

        self.assertIsNotNone(summary)
        self.assertEqual(summary["next_action"], "merge_results")
        self.assertEqual(
            runs_delegation.runs[parent_run_id]["result_data"]["orchestration"]["summary"]["next_action"],
            "merge_results",
        )
        event = parent_logs.get_nowait()
        self.assertEqual(event["event"], "delegation_state")

    @patch("server_modules.runs_delegation.build_doctor_run_gate_from_snapshot")
    @patch("server_modules.runs_delegation.apply_execution_route_metadata", create=True)
    @patch("server_modules.runs_delegation.decide_execution_target", create=True)
    @patch("server_modules.runs_delegation._prepare_run_start_request")
    @patch("server_modules.runs_delegation._compute_tool_policy_precheck", create=True)
    @patch("server_modules.runs_delegation.create_run", create=True)
    def test_create_run_from_request_rejects_blocked_local_execution_precheck(
        self,
        create_run_mock,
        precheck_mock,
        prepare_mock,
        decide_route_mock,
        apply_route_mock,
        doctor_mock,
    ):
        prepare_mock.return_value = {
            "engine": "orion",
            "metadata": {
                "outcome_pack": "local-execution-v1",
                "execution_target": "local_companion",
                "trust_mode": "guarded",
                "agent_role": "research",
            },
            "workflow_snapshot": None,
        }
        decide_route_mock.return_value = {"selected": "local_companion"}
        apply_route_mock.side_effect = lambda metadata, route: metadata
        doctor_mock.return_value = {"blocking": False}
        precheck_mock.return_value = {
            "blocked_count": 1,
            "items": [
                {
                    "decision": "blocked",
                    "tool_id": "browser_automation",
                    "capabilities": [{"title": "Authenticated browser automation"}],
                }
            ],
        }

        request = runs_delegation.RunStartRequest(
            engine="orion",
            workspace_id="default",
            user_goal="Read a signed-in page",
            agent_role="research",
            metadata={
                "outcome_pack": "local-execution-v1",
                "execution_target": "local_companion",
                "trust_mode": "guarded",
            },
        )

        with self.assertRaises(HTTPException) as ctx:
            runs_delegation._create_run_from_request(request)

        self.assertEqual(ctx.exception.status_code, 409)
        self.assertIn("Run blocked by local execution policy", str(ctx.exception.detail))
        create_run_mock.assert_not_called()

    @patch("server_modules.runs_delegation.build_doctor_run_gate_from_snapshot")
    @patch("server_modules.runs_delegation.apply_execution_route_metadata", create=True)
    @patch("server_modules.runs_delegation.decide_execution_target", create=True)
    @patch("server_modules.runs_delegation._prepare_run_start_request")
    @patch("server_modules.runs_delegation._compute_tool_policy_precheck", create=True)
    @patch("server_modules.runs_delegation._begin_run_pending_confirmation", create=True)
    @patch("server_modules.runs_delegation.create_run", create=True)
    def test_create_run_from_request_requires_local_confirmation_for_agent_machine(
        self,
        create_run_mock,
        pending_confirmation_mock,
        precheck_mock,
        prepare_mock,
        decide_route_mock,
        apply_route_mock,
        doctor_mock,
    ):
        prepare_mock.return_value = {
            "engine": "orion",
            "metadata": {
                "outcome_pack": "local-execution-v1",
                "execution_target": "local_companion",
                "trust_mode": "guarded",
                "agent_role": "research",
            },
            "workflow_snapshot": None,
        }
        decide_route_mock.return_value = {"selected": "local_companion"}
        apply_route_mock.side_effect = lambda metadata, route: metadata
        doctor_mock.return_value = {"blocking": False}
        precheck_mock.return_value = {
            "blocked_count": 0,
            "items": [
                {
                    "tool_id": "browser_automation",
                    "decision": "approval_required",
                    "execution_decision": "approval_required",
                    "reason": "session-backed interactive browser automation requires approval",
                }
            ],
            "allowed": [],
            "require_confirmation": ["browser_automation"],
            "require_confirmation_count": 1,
            "approval_required": ["browser_automation"],
            "approval_required_count": 1,
            "capability_ids": ["browser_automation"],
            "browser_automation_policy": {"reviewed_approval_required": True},
        }
        create_run_mock.return_value = "run-agent-delegation"
        pending_confirmation_mock.return_value = {"approval_id": "approval-local-1"}

        request = runs_delegation.RunStartRequest(
            engine="orion",
            workspace_id="default",
            user_goal="Read a signed-in page",
            agent_role="research",
            metadata={
                "outcome_pack": "local-execution-v1",
                "execution_target": "local_companion",
                "trust_mode": "guarded",
            },
        )

        with patch.object(runs_delegation, "agent_machine_inherited_owner_user_id", return_value="user-123"):
            with patch.object(runs_delegation, "agent_machine_full_trust_enabled", return_value=True):
                result = runs_delegation._create_run_from_request(request)

        self.assertEqual(result["status"], "waiting_for_input")
        self.assertEqual(result["pending_approval"]["approval_id"], "approval-local-1")
        create_kwargs = create_run_mock.call_args.kwargs
        self.assertTrue(create_kwargs["defer_local_enqueue"])
        metadata = create_kwargs["context"]["metadata"]
        self.assertEqual(metadata["owner_user_id"], "user-123")
        self.assertEqual(metadata["tool_policy_precheck"]["approval_required_count"], 1)
        self.assertTrue(metadata["local_execution_waiting_confirmation"])
        self.assertTrue(metadata["local_execution_waiting_approval"])
        self.assertNotIn("browser_reviewed_approved", metadata)

    @patch.object(runs_delegation.threading, "Timer", _ImmediateTimer)
    def test_child_run_auto_retries_on_failure(self):
        parent_run_id = "11111111-1111-4111-8111-111111111111"
        child_run_id = "22222222-2222-4222-8222-222222222222"
        parent_logs = queue.Queue()
        runs_delegation.runs[parent_run_id] = {
            "run_id": parent_run_id,
            "status": "running",
            "logs": parent_logs,
            "events": [],
            "_event_seq": 0,
            "context": {"metadata": {}},
            "result_data": {},
        }
        runs_delegation.RUN_QUEUE_INDEX[id(parent_logs)] = parent_run_id
        runs_delegation.runs[child_run_id] = {
            "run_id": child_run_id,
            "status": "failed",
            "created_at": "2026-04-02T00:00:00Z",
            "updated_at": "2026-04-02T00:00:00Z",
            "context": {
                "user_goal": "Research the market",
                "metadata": {
                    "parent_run_id": parent_run_id,
                    "agent_role": "research",
                    "delegated_by_role": "orchestrator",
                },
            },
        }

        created = []

        def _fake_create(req):
            created.append(req)
            retry_run_id = "33333333-3333-4333-8333-333333333333"
            runs_delegation.runs[retry_run_id] = {
                "run_id": retry_run_id,
                "status": "running",
                "created_at": "2026-04-02T00:00:01Z",
                "updated_at": "2026-04-02T00:00:01Z",
                "context": {
                    "user_goal": req.user_goal,
                    "metadata": dict(req.metadata or {}),
                },
            }
            return {"run_id": retry_run_id, "status": "starting"}

        with patch.object(runs_delegation, "_execute_delegated_run_request", side_effect=_fake_create):
            summary = runs_delegation._refresh_parent_delegation_state(parent_run_id, triggering_run_id=child_run_id)

        self.assertIsNotNone(summary)
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].metadata.get("retry_count"), 1)
        self.assertEqual(summary["next_action"], "waiting_for_children")

    def test_child_run_gives_up_after_max_retries(self):
        parent_run_id = "11111111-1111-4111-8111-111111111111"
        child_run_id = "22222222-2222-4222-8222-222222222222"
        parent_logs = queue.Queue()
        runs_delegation.runs[parent_run_id] = {
            "run_id": parent_run_id,
            "status": "running",
            "logs": parent_logs,
            "events": [],
            "_event_seq": 0,
            "context": {"metadata": {}},
            "result_data": {},
        }
        runs_delegation.RUN_QUEUE_INDEX[id(parent_logs)] = parent_run_id
        runs_delegation.runs[child_run_id] = {
            "run_id": child_run_id,
            "status": "failed",
            "created_at": "2026-04-02T00:00:00Z",
            "updated_at": "2026-04-02T00:00:00Z",
            "context": {
                "user_goal": "Research the market",
                "metadata": {
                    "parent_run_id": parent_run_id,
                    "agent_role": "research",
                    "delegated_by_role": "orchestrator",
                    "retry_count": 1,
                    "retry_sequence": 1,
                },
            },
        }

        with patch.object(runs_delegation, "_execute_delegated_run_request") as create_mock:
            summary = runs_delegation._refresh_parent_delegation_state(parent_run_id, triggering_run_id=child_run_id)

        self.assertIsNotNone(summary)
        self.assertEqual(summary["next_action"], "retry_failed_children")
        create_mock.assert_not_called()

    @patch.object(runs_delegation.threading, "Timer", _ImmediateTimer)
    def test_recover_pending_delegation_retries_on_startup_rehydrates_archived_failure(self):
        parent_run_id = "11111111-1111-4111-8111-111111111111"
        failed_child_run_id = "22222222-2222-4222-8222-222222222222"
        parent_logs = queue.Queue()
        runs_delegation.runs[parent_run_id] = {
            "run_id": parent_run_id,
            "status": "running",
            "agent_role": "orchestrator",
            "logs": parent_logs,
            "events": [],
            "_event_seq": 0,
            "context": {
                "metadata": {
                    "delegation_pending_retries": {
                        "22222222-2222-4222-8222-222222222222": {
                            "failed_child_run_id": failed_child_run_id,
                            "attempt": 1,
                            "next_retry_at": "2026-04-02T00:00:00Z",
                        }
                    }
                }
            },
            "result_data": {},
        }
        runs_delegation.RUN_QUEUE_INDEX[id(parent_logs)] = parent_run_id
        with runs_delegation.RUN_HISTORY_LOCK:
            runs_delegation.RUN_HISTORY.append(
                {
                    "run_id": failed_child_run_id,
                    "status": "failed",
                    "agent_role": "research",
                    "created_at": "2026-04-02T00:00:00Z",
                    "updated_at": "2026-04-02T00:00:00Z",
                    "parent_run_id": parent_run_id,
                    "context": {
                        "user_goal": "Research the market",
                        "metadata": {
                            "parent_run_id": parent_run_id,
                            "agent_role": "research",
                            "delegated_by_role": "orchestrator",
                        },
                    },
                }
            )

        created = []

        def _fake_create(req):
            created.append(req)
            return {"run_id": "33333333-3333-4333-8333-333333333333", "status": "starting"}

        with patch.object(runs_delegation, "_execute_delegated_run_request", side_effect=_fake_create):
            recovered = runs_delegation.recover_pending_delegation_retries_on_startup()

        self.assertEqual(recovered, [parent_run_id])
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].metadata.get("retry_count"), 1)
        pending_state = runs_delegation.runs[parent_run_id]["context"]["metadata"].get("delegation_pending_retries") or {}
        self.assertEqual(pending_state, {})

    @patch.object(runs_delegation.threading, "Timer", _ImmediateTimer)
    def test_stale_child_run_triggers_timeout_failure(self):
        parent_run_id = "11111111-1111-4111-8111-111111111111"
        child_run_id = "22222222-2222-4222-8222-222222222222"
        parent_logs = queue.Queue()
        runs_delegation.runs[parent_run_id] = {
            "run_id": parent_run_id,
            "status": "running",
            "logs": parent_logs,
            "events": [],
            "_event_seq": 0,
            "context": {"metadata": {}},
            "result_data": {},
        }
        runs_delegation.RUN_QUEUE_INDEX[id(parent_logs)] = parent_run_id
        runs_delegation.runs[child_run_id] = {
            "run_id": child_run_id,
            "status": "running_local",
            "created_at": "2026-04-02T00:00:00Z",
            "updated_at": "2026-04-02T00:00:00Z",
            "local_last_progress_at": "2026-04-01T23:50:00Z",
            "context": {
                "user_goal": "Research the market",
                "metadata": {
                    "parent_run_id": parent_run_id,
                    "agent_role": "research",
                    "delegated_by_role": "orchestrator",
                },
            },
            "logs": queue.Queue(),
        }

        created = []

        def _fake_create(req):
            created.append(req)
            retry_run_id = "33333333-3333-4333-8333-333333333333"
            runs_delegation.runs[retry_run_id] = {
                "run_id": retry_run_id,
                "status": "running",
                "created_at": "2026-04-02T00:10:01Z",
                "updated_at": "2026-04-02T00:10:01Z",
                "context": {
                    "user_goal": req.user_goal,
                    "metadata": dict(req.metadata or {}),
                },
            }
            return {"run_id": retry_run_id, "status": "starting"}

        with (
            patch.object(runs_delegation, "_utc_now", return_value=runs_delegation._parse_utc_ts("2026-04-02T00:10:30Z")),
            patch.object(runs_delegation, "_execute_delegated_run_request", side_effect=_fake_create),
        ):
            summary = runs_delegation._refresh_parent_delegation_state(parent_run_id, triggering_run_id=child_run_id)

        self.assertIsNotNone(summary)
        self.assertEqual(runs_delegation.runs[child_run_id]["result_data"]["error"], "delegated_child_timeout")
        self.assertEqual(len(created), 1)

    def test_routing_uses_llm_decision(self):
        parent_snapshot = {
            "run_id": "11111111-1111-4111-8111-111111111111",
            "user_goal": "Prepare a revenue spreadsheet and summarize outliers.",
            "context": {"metadata": {}},
        }

        with patch.object(
            runs_delegation,
            "_llm_auto_delegate_role",
            return_value={"agent_role": "finance", "reason": "Spreadsheet work fits the finance specialist."},
        ):
            plan = runs_delegation._build_auto_delegation_plan(parent_snapshot, max_children=2)

        self.assertEqual(plan[0]["agent_role"], "finance")
        self.assertEqual(plan[0]["metadata"]["auto_delegation_source"], "llm")

    def test_routing_falls_back_to_keyword_on_llm_failure(self):
        parent_snapshot = {
            "run_id": "11111111-1111-4111-8111-111111111111",
            "user_goal": "Build and fix the platform automation flow.",
            "context": {"metadata": {}},
        }

        with patch.object(runs_delegation, "_llm_auto_delegate_role", side_effect=RuntimeError("routing offline")):
            plan = runs_delegation._build_auto_delegation_plan(parent_snapshot, max_children=1)

        self.assertEqual(plan[0]["agent_role"], "builder")
        self.assertEqual(plan[0]["metadata"]["auto_delegation_source"], "keyword")

    def test_routing_decision_logged_as_step(self):
        parent_run_id = "11111111-1111-4111-8111-111111111111"
        parent_logs = queue.Queue()
        runs_delegation.runs[parent_run_id] = {
            "run_id": parent_run_id,
            "status": "running",
            "logs": parent_logs,
            "events": [],
            "_event_seq": 0,
            "context": {"metadata": {}},
        }
        runs_delegation.RUN_QUEUE_INDEX[id(parent_logs)] = parent_run_id

        plan = [
            {
                "agent_role": "finance",
                "user_goal": "Handle spreadsheet work",
                "metadata": {"auto_delegation_reason": "Spreadsheet work fits finance."},
            }
        ]

        runs_delegation._emit_auto_delegation_routing_log(
            parent_run_id,
            plan,
            strategy="llm",
            reason="Spreadsheet work fits finance.",
        )

        event = parent_logs.get_nowait()
        self.assertEqual(event["event"], "delegation_routing")
        self.assertEqual(event["data"]["strategy"], "llm")

    def test_execute_delegated_run_request_delegates_to_run_service(self):
        request = runs_delegation.RunStartRequest(
            engine="orion",
            workspace_id="default",
            user_goal="delegate this task",
        )

        with patch.object(runs_delegation.run_service, "execute_delegated_run_request", return_value={"status": "starting"}) as execute_mock:
            result = runs_delegation._execute_delegated_run_request(request)

        self.assertEqual(result["status"], "starting")
        self.assertIs(execute_mock.call_args.args[0], request)
        self.assertIs(execute_mock.call_args.kwargs["namespace"], runs_delegation.__dict__)
        self.assertIs(
            execute_mock.call_args.kwargs["execute_system_run_start_request_via_turn_runtime_fn"],
            runs_delegation.execute_system_run_start_request_via_turn_runtime,
        )


if __name__ == "__main__":
    unittest.main()

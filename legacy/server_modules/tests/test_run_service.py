import asyncio
from datetime import datetime, timezone
import queue
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from server_modules.agent_trace_service import TraceContext
from server_modules.agent_turn import build_agent_turn_request, build_run_start_turn_request
from server_modules import runtime_attachment_service
from server_modules.run_service import (
    AUTO_DELEGATION_ROLE_RULES,
    ROUTING_PROVIDER_ORDER,
    apply_browser_execution_metadata,
    build_run_creation_services,
    build_auto_delegation_plan,
    build_delegated_run_execution_services,
    build_execute_unowned_system_run_start_request_via_turn_runtime,
    build_server_run_creation_services,
    build_legacy_run_execution_services,
    build_legacy_run_execution_services_from_values,
    build_run_precheck_result,
    build_run_execution_services,
    build_server_run_execution_services,
    build_server_run_routing_preview_services,
    build_server_system_run_execution_services,
    build_schedule_system_run_execution_services,
    build_system_run_execution_services,
    build_system_run_execution_services_from_namespace,
    build_namespace_delegated_system_run_execution_services,
    build_legacy_local_execution_creation_services,
    build_legacy_orion_preparation_services,
    build_legacy_run_preparation_services,
    build_legacy_run_request_services,
    build_prepared_run_creation_services,
    build_run_preview_context,
    build_run_preparation_services,
    build_run_prepared_result_services,
    build_run_routing_preview_services,
    build_run_routing_preview,
    build_runs_core_creation_result,
    build_runs_core_legacy_preparation_services,
    build_runs_core_legacy_request_services,
    build_runs_core_runtime_preparation_services_from_namespace,
    build_runs_core_runtime_request_services_from_namespace,
    build_runs_core_result_services,
    build_runs_delegation_creation_result,
    build_runs_delegation_legacy_preparation_services,
    build_runs_delegation_legacy_request_services,
    build_runs_delegation_runtime_preparation_services_from_namespace,
    build_runs_delegation_runtime_request_services_from_namespace,
    build_runs_delegation_result_services,
    begin_run_pending_approval,
    begin_run_pending_confirmation,
    build_delegated_child_run_request,
    build_delegation_summary,
    build_run_relation_summary,
    build_live_run_record,
    create_live_run,
    clear_pending_confirmation,
    create_workflow_child_local_run,
    get_pending_confirmation,
    register_live_run,
    activate_live_run,
    execute_workflow_human_node,
    execute_workflow_local_tool,
    execute_workflow_subflow_node,
    set_pending_confirmation,
    transition_live_run_status,
    prepare_legacy_run_start_request,
    create_legacy_run_result_from_request,
    create_run_result_from_prepared_request,
    execute_durable_agent_turn_dispatch,
    execute_delegated_run_request,
    execute_built_legacy_unowned_system_run_start_request_via_turn_runtime,
    execute_built_unowned_system_run_start_request_via_turn_runtime,
    execute_run_start_request_via_turn_runtime,
    execute_system_run_start_request_via_turn_runtime,
    execute_unowned_system_run_start_request_via_turn_runtime,
    local_execution_block_prompt,
    local_execution_confirmation_prompt,
    local_execution_requires_start_confirmation,
    load_schedules,
    load_weekly_schedules,
    mark_local_execution_tools_approved,
    normalize_requested_max_iterations,
    precheck_human_action_labels,
    persist_schedules,
    persist_weekly_schedules,
    PreparedRunCreationServices,
    RunPreparationServices,
    RunCreationServices,
    RunExecutionServices,
    RunRoutingPreviewServices,
    LegacyLocalExecutionCreationCallbacks,
    LegacyOrionPreparationCallbacks,
    LegacyRunExecutionCallbacks,
    LegacyRunPreparationServices,
    LegacyRunRequestServices,
    RunPreparedResultServices,
    WorkflowRecursionError,
    safe_int,
    build_run_start_request_from_turn,
    build_schedule_bound_create_run_from_request,
    create_run_from_prepared_request,
    create_run_result_from_request,
    execute_durable_turn_request,
    prepare_run_start_request,
    build_retry_child_payload,
    find_run_relationships,
    emit_auto_delegation_routing_log,
    iter_known_run_snapshots,
    is_local_runtime_run,
    llm_auto_delegate_role,
    load_live_runtime_state,
    resolve_fastest_routing_context,
    refresh_parent_delegation_state,
    run_local_runtime_watchdog_pass,
    schedule_auto_retry_for_failed_children,
    timeout_stale_delegated_child_runs,
    trigger_pending_heartbeat_schedules,
    initialize_runtime_services,
    wait_for_workflow_child_run,
    wait_for_human_response,
)
from server_modules.runtime_models import RunStartRequest


class RunServiceTests(unittest.TestCase):
    def test_build_live_run_record_initializes_active_runtime_fields(self):
        record = build_live_run_record(
            run_id="run-1",
            engine="orion",
            context={"workspace_id": "default"},
            now_iso="2026-04-06T00:00:00Z",
            started_mono=12.5,
            log_queue=queue.Queue(),
            input_queue=queue.Queue(),
            memory_enabled=True,
            memory_updated_at="2026-04-06T00:00:00Z",
            active_profile_id="profile-1",
            active_profile_label="Primary",
            active_provider="openai",
            active_model="gpt-test",
        )

        self.assertEqual(record["run_id"], "run-1")
        self.assertEqual(record["status"], "starting")
        self.assertEqual(record["memory_trace"]["enabled"], True)
        self.assertEqual(record["active_profile_id"], "profile-1")
        self.assertEqual(record["active_provider"], "openai")

    def test_is_local_runtime_run_detects_local_status_or_selected_target(self):
        self.assertTrue(is_local_runtime_run({"status": "queued_local"}))
        self.assertTrue(
            is_local_runtime_run(
                {"status": "starting", "context": {"metadata": {"execution_target_selected": "local_companion"}}}
            )
        )
        self.assertFalse(is_local_runtime_run({"status": "running", "context": {"metadata": {}}}))

    def test_pending_confirmation_helpers_preserve_legacy_aliases(self):
        run = {"pending_confirmation": None, "pending_approval": {"approval_id": "legacy-1"}}

        self.assertEqual(get_pending_confirmation(run)["approval_id"], "legacy-1")

        set_pending_confirmation(run, {"approval_id": "approval-1"})

        self.assertEqual(run["pending_confirmation"]["approval_id"], "approval-1")
        self.assertEqual(run["pending_approval"]["approval_id"], "approval-1")

        clear_pending_confirmation(run)

        self.assertIsNone(run["pending_confirmation"])
        self.assertIsNone(run["pending_approval"])

    def test_begin_run_pending_confirmation_shapes_payload_and_waits_for_input(self):
        log_queue = queue.Queue()
        run = {
            "run_id": "run-approval-1",
            "logs": log_queue,
            "context": {"metadata": {"approval_ttl_seconds": 15}},
        }
        emitted = []
        audits = []
        status_changes = []
        outbox_events = []

        with (
            patch("server_modules.run_service.outbox_service.emit_approval_requested_event", side_effect=lambda **kwargs: outbox_events.append(kwargs)),
            patch(
                "server_modules.run_service.run_state_repository.sync_create_or_update_approval_request",
                side_effect=lambda *args, **kwargs: self.assertIsNone(run.get("pending_confirmation")) or {"version": 0},
            ),
        ):
            payload = begin_run_pending_confirmation(
                "run-approval-1",
                "Confirm send",
                runs_by_id={"run-approval-1": run},
                default_approval_ttl_seconds=600,
                approval_correlation_id_fn=lambda approval_id, run_id=None: f"corr:{run_id}:{approval_id}",
                append_approval_audit_fn=lambda **kwargs: audits.append(kwargs),
                json_safe_fn=lambda value: value,
                emit_log_fn=lambda *args, **kwargs: emitted.append((args, kwargs)),
                set_run_status_fn=lambda run_id, status: status_changes.append((run_id, status)),
                utc_now_fn=lambda: datetime(2026, 4, 6, 0, 0, 0, tzinfo=timezone.utc),
                utc_now_iso_fn=lambda: "2026-04-06T00:00:00Z",
                source="local_execution_start",
                metadata={
                    "approval_actions": ["browser_automation", "  ", "file_write"],
                    "target": "local_companion",
                    "policy_mode": "guarded",
                    "browser_session_profile": "qa-browser",
                    "browser_immutable_plan_hash": "hash-1",
                    "browser_reviewed_approval_required": True,
                },
                emit_pause_required=True,
            )

        self.assertEqual(payload["ttl_seconds"], 30)
        self.assertEqual(payload["scope"], "once")
        self.assertEqual(payload["actions"], ["browser_automation", "file_write"])
        self.assertEqual(payload["target"], "local_companion")
        self.assertEqual(run["pending_confirmation"], payload)
        self.assertEqual(run["pending_approval"], payload)
        self.assertEqual(status_changes, [("run-approval-1", "waiting_for_input")])
        self.assertEqual(log_queue.get_nowait(), "__PAUSE_REQUIRED__")
        self.assertEqual(emitted[0][1]["event"], "approval_requested")
        self.assertEqual(emitted[0][1]["data"]["browser"]["session_profile"], "qa-browser")
        self.assertEqual(emitted[1][1]["event"], "approval_waiting")
        self.assertEqual(audits[0]["stage"], "requested")
        self.assertEqual(audits[1]["stage"], "waiting")
        self.assertEqual(outbox_events[0]["approval_id"], payload["approval_id"])

    def test_begin_run_pending_confirmation_emits_trace_approval_requested(self):
        log_queue = queue.Queue()
        run = {
            "run_id": "run-approval-trace",
            "logs": log_queue,
            "context": {
                "workspace_id": "workspace-1",
                "tenant_id": "tenant-1",
                "metadata": {"trace_id": "trace-1", "approval_ttl_seconds": 15},
            },
        }
        trace_context = TraceContext(
            trace_id="trace-1",
            workspace_id="workspace-1",
            tenant_id="tenant-1",
            thread_id="thread-1",
            run_id="run-approval-trace",
            root_agent_id="sage",
        )

        with (
            patch("server_modules.run_service.outbox_service.emit_approval_requested_event"),
            patch(
                "server_modules.run_service.run_state_repository.sync_create_or_update_approval_request",
                return_value={"version": 0},
            ),
            patch("server_modules.run_service.run_async_tool_call", side_effect=lambda coro: asyncio.run(coro)),
            patch("server_modules.run_service.agent_trace_service.resume_trace", new=AsyncMock(return_value=trace_context)),
            patch(
                "server_modules.run_service.agent_trace_service.emit_approval_requested",
                new=AsyncMock(return_value="evt-approval"),
            ) as emit_approval_mock,
        ):
            payload = begin_run_pending_confirmation(
                "run-approval-trace",
                "Confirm send",
                runs_by_id={"run-approval-trace": run},
                default_approval_ttl_seconds=600,
                approval_correlation_id_fn=lambda approval_id, run_id=None: f"corr:{run_id}:{approval_id}",
                append_approval_audit_fn=lambda **kwargs: None,
                json_safe_fn=lambda value: value,
                emit_log_fn=lambda *args, **kwargs: None,
                set_run_status_fn=lambda run_id, status: None,
                utc_now_fn=lambda: datetime(2026, 4, 6, 0, 0, 0, tzinfo=timezone.utc),
                utc_now_iso_fn=lambda: "2026-04-06T00:00:00Z",
                source="local_execution_start",
                metadata={"approval_node_key": "approval-step", "target": "local_companion"},
                emit_pause_required=False,
            )

        self.assertTrue(payload["approval_id"])
        emit_approval_mock.assert_awaited_once()

    def test_begin_run_pending_confirmation_persists_browser_contract_on_durable_request(self):
        log_queue = queue.Queue()
        run = {
            "run_id": "run-approval-browser",
            "logs": log_queue,
            "context": {"metadata": {"approval_ttl_seconds": 60}},
        }
        captured = {}

        with (
            patch("server_modules.run_service.outbox_service.emit_approval_requested_event"),
            patch(
                "server_modules.run_service.run_state_repository.sync_create_or_update_approval_request",
                side_effect=lambda *args, **kwargs: captured.setdefault("payload", args[2]) or {"version": 0},
            ),
        ):
            begin_run_pending_confirmation(
                "run-approval-browser",
                "Review the browser run",
                runs_by_id={"run-approval-browser": run},
                default_approval_ttl_seconds=600,
                approval_correlation_id_fn=lambda approval_id, run_id=None: f"corr:{run_id}:{approval_id}",
                append_approval_audit_fn=lambda **kwargs: None,
                json_safe_fn=lambda value: value,
                emit_log_fn=lambda *args, **kwargs: None,
                set_run_status_fn=lambda run_id, status: None,
                utc_now_fn=lambda: datetime(2026, 4, 6, 0, 0, 0, tzinfo=timezone.utc),
                utc_now_iso_fn=lambda: "2026-04-06T00:00:00Z",
                metadata={
                    "browser_session_profile": "qa-browser",
                    "browser_immutable_plan_hash": "hash-1",
                    "browser_reviewed_approval_required": True,
                    "browser_interactive_actions": ["click"],
                },
            )

        self.assertEqual(captured["payload"]["browser"]["session_profile"], "qa-browser")
        self.assertEqual(captured["payload"]["browser"]["immutable_plan_hash"], "hash-1")
        self.assertTrue(captured["payload"]["browser"]["reviewed_approval_required"])

    def test_begin_run_pending_approval_delegates_to_confirmation_entrypoint(self):
        calls = []

        payload = begin_run_pending_approval(
            "run-approval-2",
            "Confirm run",
            begin_run_pending_confirmation_fn=lambda *args, **kwargs: calls.append((args, kwargs)) or {"approval_id": "approval-2"},
            source="runtime",
            metadata={"target": "cloud"},
            emit_pause_required=False,
        )

        self.assertEqual(payload["approval_id"], "approval-2")
        self.assertEqual(calls[0][0], ("run-approval-2", "Confirm run"))
        self.assertEqual(calls[0][1]["metadata"]["target"], "cloud")

    def test_wait_for_human_response_consumes_resolved_confirmation_after_restart(self):
        log_queue = queue.Queue()
        run = {
            "run_id": "run-approval-resume",
            "status": "waiting_for_input",
            "logs": log_queue,
            "input_queue": queue.Queue(),
            "context": {
                "metadata": {
                    "connector": "google_workspace",
                    "connector_action": "send_email",
                    "approval_node_key": "tool_1",
                    "approval_target": "ops@example.com",
                }
            },
            "pending_confirmation": {
                "approval_id": "approval-1",
                "correlation_id": "corr-1",
                "status": "resolved",
                "prompt": "Confirm send",
                "decision": "proceed",
                "note": "resume note",
                "metadata": {
                    "connector": "google_workspace",
                    "connector_action": "send_email",
                    "approval_node_key": "tool_1",
                    "approval_target": "ops@example.com",
                    "browser_session_profile": "qa-browser",
                    "browser_immutable_plan_hash": "hash-1",
                    "browser_reviewed_approval_required": True,
                },
            },
            "pending_approval": {
                "approval_id": "approval-1",
                "correlation_id": "corr-1",
                "status": "resolved",
                "prompt": "Confirm send",
                "decision": "proceed",
                "note": "resume note",
                "metadata": {
                    "connector": "google_workspace",
                    "connector_action": "send_email",
                    "approval_node_key": "tool_1",
                    "approval_target": "ops@example.com",
                    "browser_session_profile": "qa-browser",
                    "browser_immutable_plan_hash": "hash-1",
                    "browser_reviewed_approval_required": True,
                },
            },
            "_resume_after_confirmation_scheduled": True,
        }
        emitted = []
        audits = []
        status_changes = []

        with (
            patch(
                "server_modules.run_service.run_state_repository.sync_get_approval_record",
                return_value={"approval_id": "approval-1", "run_id": "run-approval-resume", "status": "resolved", "resolution": "approved", "decision_payload": {"decision": "proceed", "note": "resume note"}, "resolved_at": "2026-04-06T00:00:00Z"},
            ),
            patch("server_modules.run_service.run_state_repository.sync_record_approval_resolution", return_value={"approval_id": "approval-1"}),
        ):
            payload = wait_for_human_response(
                "run-approval-resume",
                "Confirm send",
                runs_by_id={"run-approval-resume": run},
                begin_run_pending_confirmation_fn=lambda *args, **kwargs: self.fail("should not begin new approval"),
                clear_pending_confirmation_fn=clear_pending_confirmation,
                get_pending_confirmation_fn=get_pending_confirmation,
                set_pending_confirmation_fn=set_pending_confirmation,
                approval_correlation_id_fn=lambda approval_id, run_id=None: f"corr:{run_id}:{approval_id}",
                append_approval_audit_fn=lambda **kwargs: audits.append(kwargs),
                emit_log_fn=lambda *args, **kwargs: emitted.append((args, kwargs)),
                set_run_status_fn=lambda run_id, status: status_changes.append((run_id, status)),
                utc_now_iso_fn=lambda: "2026-04-06T00:00:00Z",
                approval_ttl_seconds=600,
            )

        self.assertTrue(payload["approved"])
        self.assertEqual(status_changes, [("run-approval-resume", "executing")])
        self.assertIsNone(run["pending_confirmation"])
        self.assertIsNone(run["pending_approval"])
        self.assertNotIn("_resume_after_confirmation_scheduled", run)
        self.assertEqual(run["context"]["metadata"]["last_approval_result"]["approval_id"], "approval-1")
        self.assertEqual(run["context"]["metadata"]["resolved_approval_markers"][0]["decision"], "approved")
        self.assertEqual(emitted[0][1]["event"], "approval_received")
        self.assertEqual(emitted[1][1]["event"], "approval_resolved")
        self.assertEqual(emitted[1][1]["data"]["browser"]["session_profile"], "qa-browser")
        self.assertEqual(audits[0]["stage"], "received")
        self.assertEqual(audits[1]["stage"], "resolved")
        self.assertTrue(audits[1]["metadata"]["resumed_after_restart"])

    def test_wait_for_human_response_ignores_stale_approval_then_resolves_current(self):
        log_queue = queue.Queue()
        input_queue = queue.Queue()
        input_queue.put({"approval_id": "approval-stale", "decision": "approve"})
        input_queue.put({"approval_id": "approval-2", "decision": "approve", "note": "ship it"})
        run = {
            "run_id": "run-approval-live",
            "status": "waiting_for_input",
            "logs": log_queue,
            "input_queue": input_queue,
            "context": {"metadata": {}},
        }
        emitted = []
        audits = []
        status_changes = []

        def _begin(*args, **kwargs):
            payload = {
                "approval_id": "approval-2",
                "correlation_id": "corr-2",
                "ttl_seconds": 300,
                "status": "waiting",
                "prompt": "Confirm ship",
                "metadata": {
                    "connector": "google_workspace",
                    "connector_action": "send_email",
                    "approval_node_key": "tool_ship",
                    "approval_target": "ship@example.com",
                    "browser_session_profile": "qa-browser",
                    "browser_immutable_plan_hash": "hash-1",
                    "browser_reviewed_approval_required": True,
                },
            }
            set_pending_confirmation(run, payload)
            return payload

        with patch("server_modules.run_service.run_state_repository.sync_record_approval_resolution", return_value={"approval_id": "approval-2"}):
            payload = wait_for_human_response(
                "run-approval-live",
                "Confirm ship",
                runs_by_id={"run-approval-live": run},
                begin_run_pending_confirmation_fn=_begin,
                clear_pending_confirmation_fn=clear_pending_confirmation,
                get_pending_confirmation_fn=get_pending_confirmation,
                set_pending_confirmation_fn=set_pending_confirmation,
                approval_correlation_id_fn=lambda approval_id, run_id=None: f"corr:{run_id}:{approval_id}",
                append_approval_audit_fn=lambda **kwargs: audits.append(kwargs),
                emit_log_fn=lambda *args, **kwargs: emitted.append((args, kwargs)),
                set_run_status_fn=lambda run_id, status: status_changes.append((run_id, status)),
                utc_now_iso_fn=lambda: "2026-04-06T00:00:00Z",
                approval_ttl_seconds=600,
                monotonic_fn=lambda: 10.0,
            )

        self.assertTrue(payload["approved"])
        self.assertEqual(status_changes, [("run-approval-live", "executing")])
        self.assertIsNone(run["pending_confirmation"])
        self.assertEqual(run["context"]["metadata"]["last_approval_result"]["approval_id"], "approval-2")
        self.assertEqual(emitted[0][1]["event"], "approval_ignored")
        self.assertEqual(emitted[1][1]["event"], "approval_received")
        self.assertEqual(emitted[2][1]["event"], "approval_resolved")
        self.assertEqual(emitted[2][1]["data"]["browser"]["session_profile"], "qa-browser")
        self.assertEqual(audits[0]["stage"], "ignored")
        self.assertEqual(audits[1]["stage"], "received")
        self.assertEqual(audits[2]["stage"], "resolved")

    def test_wait_for_human_response_marks_timeout_when_no_decision_arrives(self):
        log_queue = queue.Queue()

        class _EmptyInputQueue:
            def get(self, timeout=None):
                raise queue.Empty()

        run = {
            "run_id": "run-approval-timeout",
            "status": "waiting_for_input",
            "logs": log_queue,
            "input_queue": _EmptyInputQueue(),
        }
        emitted = []
        audits = []

        def _begin(*args, **kwargs):
            payload = {
                "approval_id": "approval-timeout",
                "correlation_id": "corr-timeout",
                "ttl_seconds": 30,
                "status": "waiting",
                "prompt": "Confirm send",
            }
            set_pending_confirmation(run, payload)
            return payload

        monotonic_values = iter([100.0, 131.0])

        with (
            patch("server_modules.run_service.run_state_repository.sync_record_approval_resolution", return_value={"approval_id": "approval-timeout"}),
            self.assertRaises(RuntimeError),
        ):
            wait_for_human_response(
                "run-approval-timeout",
                "Confirm send",
                runs_by_id={"run-approval-timeout": run},
                begin_run_pending_confirmation_fn=_begin,
                clear_pending_confirmation_fn=clear_pending_confirmation,
                get_pending_confirmation_fn=get_pending_confirmation,
                set_pending_confirmation_fn=set_pending_confirmation,
                approval_correlation_id_fn=lambda approval_id, run_id=None: f"corr:{run_id}:{approval_id}",
                append_approval_audit_fn=lambda **kwargs: audits.append(kwargs),
                emit_log_fn=lambda *args, **kwargs: emitted.append((args, kwargs)),
                set_run_status_fn=lambda run_id, status: None,
                utc_now_iso_fn=lambda: "2026-04-06T00:00:00Z",
                approval_ttl_seconds=600,
                monotonic_fn=lambda: next(monotonic_values),
            )

        self.assertEqual(run["pending_confirmation"]["status"], "expired")
        self.assertEqual(run["pending_approval"]["status"], "expired")
        self.assertEqual(emitted[0][1]["event"], "approval_timeout")
        self.assertEqual(audits[0]["stage"], "timeout")

    @patch("server_modules.run_service.record_reliability_latency_sample")
    def test_wait_for_human_response_records_approval_propagation_latency(self, mock_record_latency):
        input_queue = queue.Queue()
        input_queue.put({"approval_id": "approval-3", "decision": "approve"})
        run = {
            "run_id": "run-approval-metric",
            "status": "waiting_for_input",
            "logs": queue.Queue(),
            "input_queue": input_queue,
        }

        def _begin(*args, **kwargs):
            payload = {
                "approval_id": "approval-3",
                "correlation_id": "corr-3",
                "ttl_seconds": 300,
                "status": "waiting",
                "prompt": "Confirm",
                "requested_at": "2026-04-06T00:00:00Z",
            }
            set_pending_confirmation(run, payload)
            return payload

        with patch("server_modules.run_service.run_state_repository.sync_record_approval_resolution", return_value={"approval_id": "approval-3"}):
            payload = wait_for_human_response(
                "run-approval-metric",
                "Confirm",
                runs_by_id={"run-approval-metric": run},
                begin_run_pending_confirmation_fn=_begin,
                clear_pending_confirmation_fn=clear_pending_confirmation,
                get_pending_confirmation_fn=get_pending_confirmation,
                set_pending_confirmation_fn=set_pending_confirmation,
                approval_correlation_id_fn=lambda approval_id, run_id=None: f"corr:{run_id}:{approval_id}",
                append_approval_audit_fn=lambda **kwargs: None,
                emit_log_fn=lambda *args, **kwargs: None,
                set_run_status_fn=lambda run_id, status: None,
                utc_now_iso_fn=lambda: "2026-04-06T00:00:01Z",
                approval_ttl_seconds=600,
                monotonic_fn=lambda: 10.0,
            )

        self.assertTrue(payload["approved"])
        self.assertEqual(mock_record_latency.call_args.args[0], "approval_propagation_latency_ms")
        self.assertEqual(mock_record_latency.call_args.args[1], 1000.0)

    def test_register_live_run_indexes_and_persists(self):
        runs = {}
        queue_index = {}
        persisted = []
        metrics = []
        run = {"logs": queue.Queue()}

        register_live_run(
            "run-1",
            run,
            runs_by_id=runs,
            run_queue_index=queue_index,
            metrics_inc_fn=lambda key, value=1: metrics.append((key, value)),
            persist_live_run_state_fn=lambda run_id, payload: persisted.append((run_id, payload)),
        )

        self.assertIs(runs["run-1"], run)
        self.assertEqual(queue_index[id(run["logs"])], "run-1")
        self.assertEqual(metrics, [("runs_started", 1)])
        self.assertEqual(persisted[0][0], "run-1")

    def test_activate_live_run_routes_local_target_through_hydrate_and_enqueue(self):
        calls = []
        result = activate_live_run(
            "run-1",
            {"run_id": "run-1"},
            selected_target="local_companion",
            local_companion_target="local_companion",
            defer_local_enqueue=False,
            hydrate_run_memory_context_fn=lambda run_id, run: calls.append(("hydrate", run_id)),
            enqueue_local_companion_run_fn=lambda run_id: calls.append(("enqueue", run_id)),
            start_background_run_fn=lambda run_id: calls.append(("start", run_id)),
        )

        self.assertEqual(result, "run-1")
        self.assertEqual(calls, [("hydrate", "run-1"), ("enqueue", "run-1")])

    def test_transition_live_run_status_handles_terminal_cleanup(self):
        run = {
            "status": "running",
            "_started_mono": 5.0,
            "logs": queue.Queue(),
            "context": {"workspace_id": "workspace-1", "tenant_id": "tenant-1", "metadata": {"trace_id": "trace-1"}},
        }
        queue_index = {id(run["logs"]): "run-1"}
        pending = ["run-1"]
        claimed = {"run-1": {"worker_id": "worker-1"}}
        archived = []
        removed = []
        synced = []
        metrics_added = []
        metrics_inc = []
        repo_dispatches = []
        transition_outbox = []
        artifact_outbox = []

        with patch("server_modules.run_service.run_state_repository.dispatch_repository_call", side_effect=lambda awaitable, operation: (repo_dispatches.append(operation), asyncio.run(awaitable))), \
             patch("server_modules.run_service.outbox_service.emit_run_transition_event", side_effect=lambda **kwargs: transition_outbox.append(kwargs)), \
             patch("server_modules.run_service.outbox_service.emit_artifact_created_event", side_effect=lambda **kwargs: artifact_outbox.append(kwargs)):
            transition_live_run_status(
                "run-1",
                "completed",
                run=run,
                now_mono=7.5,
                now_iso="2026-04-06T00:00:00Z",
                terminal_statuses={"completed", "failed", "timeout"},
                local_queue_lock=__import__("threading").Lock(),
                local_pending_run_ids=pending,
                local_claimed_runs=claimed,
                archive_run_if_terminal_fn=lambda run_id, payload: archived.append((run_id, payload["status"])),
                remove_live_run_state_fn=lambda run_id: removed.append(run_id),
                sync_local_runtime_state_snapshot_fn=lambda: synced.append(True),
                persist_live_run_state_fn=lambda run_id, payload: None,
                run_queue_index=queue_index,
                metrics_add_fn=lambda key, value: metrics_added.append((key, value)),
                metrics_inc_fn=lambda key, value=1: metrics_inc.append((key, value)),
            )

        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["completed_at"], "2026-04-06T00:00:00Z")
        self.assertEqual(run["duration_ms"], 2500.0)
        self.assertEqual(pending, [])
        self.assertEqual(claimed, {})
        self.assertEqual(archived, [("run-1", "completed")])
        self.assertEqual(removed, ["run-1"])
        self.assertEqual(synced, [True])
        self.assertNotIn(id(run["logs"]), queue_index)
        self.assertIn(("runs_completed", 1), metrics_inc)
        self.assertIn("update_live_run_if_version_matches:run-1:completed:0", repo_dispatches)
        self.assertIn("record_transition:run-1:completed", repo_dispatches)
        self.assertIn("archive_run:run-1:completed", repo_dispatches)
        self.assertEqual(transition_outbox[0]["to_state"], "completed")
        self.assertEqual(artifact_outbox, [])

    def test_transition_live_run_status_emits_trace_completion_and_artifacts(self):
        run = {
            "run_id": "run-1",
            "status": "running",
            "_started_mono": 5.0,
            "logs": queue.Queue(),
            "context": {"workspace_id": "workspace-1", "tenant_id": "tenant-1", "metadata": {"trace_id": "trace-1"}},
            "result_data": {
                "outputs": {
                    "artifacts": [
                        {"id": "artifact-1", "kind": "report", "title": "Run report", "mime_type": "text/markdown"}
                    ]
                }
            },
        }
        trace_context = TraceContext(
            trace_id="trace-1",
            workspace_id="workspace-1",
            tenant_id="tenant-1",
            thread_id="thread-1",
            run_id="run-1",
            root_agent_id="sage",
        )

        with patch("server_modules.run_service.run_state_repository.dispatch_repository_call", side_effect=lambda awaitable, operation: asyncio.run(awaitable)), \
             patch("server_modules.run_service.outbox_service.emit_run_transition_event"), \
             patch("server_modules.run_service.outbox_service.emit_artifact_created_event"), \
             patch("server_modules.run_service.run_async_tool_call", side_effect=lambda coro: asyncio.run(coro)), \
             patch("server_modules.run_service.agent_trace_service.resume_trace", new=AsyncMock(return_value=trace_context)), \
             patch("server_modules.run_service.agent_trace_service.emit_artifact_created", new=AsyncMock(return_value="evt-artifact")) as emit_artifact_mock, \
             patch("server_modules.run_service.agent_trace_service.emit_trace_completed", new=AsyncMock(return_value="evt-complete")) as emit_complete_mock, \
             patch("server_modules.run_service.agent_trace_service.finish_trace", new=AsyncMock(return_value={"id": "trace-1"})) as finish_mock:
            transition_live_run_status(
                "run-1",
                "completed",
                run=run,
                now_mono=7.5,
                now_iso="2026-04-06T00:00:00Z",
                terminal_statuses={"completed", "failed", "timeout"},
                local_queue_lock=__import__("threading").Lock(),
                local_pending_run_ids=[],
                local_claimed_runs={},
                archive_run_if_terminal_fn=lambda run_id, payload: None,
                remove_live_run_state_fn=lambda run_id: None,
                sync_local_runtime_state_snapshot_fn=lambda: None,
                persist_live_run_state_fn=lambda run_id, payload: None,
                run_queue_index={},
                metrics_add_fn=lambda key, value: None,
                metrics_inc_fn=lambda key, value=1: None,
            )

        emit_artifact_mock.assert_awaited_once()
        emit_complete_mock.assert_awaited_once()
        finish_mock.assert_awaited_once()

    def test_transition_live_run_status_emits_trace_failed_for_failed_run(self):
        run = {
            "run_id": "run-1",
            "status": "running",
            "_started_mono": 5.0,
            "logs": queue.Queue(),
            "result": "Tool execution failed.",
            "context": {"workspace_id": "workspace-1", "tenant_id": "tenant-1", "metadata": {"trace_id": "trace-1"}},
        }
        trace_context = TraceContext(
            trace_id="trace-1",
            workspace_id="workspace-1",
            tenant_id="tenant-1",
            thread_id="thread-1",
            run_id="run-1",
            root_agent_id="sage",
        )

        with patch("server_modules.run_service.run_state_repository.dispatch_repository_call", side_effect=lambda awaitable, operation: asyncio.run(awaitable)), \
             patch("server_modules.run_service.outbox_service.emit_run_transition_event"), \
             patch("server_modules.run_service.outbox_service.emit_artifact_created_event"), \
             patch("server_modules.run_service.run_async_tool_call", side_effect=lambda coro: asyncio.run(coro)), \
             patch("server_modules.run_service.agent_trace_service.resume_trace", new=AsyncMock(return_value=trace_context)), \
             patch("server_modules.run_service.agent_trace_service.emit_trace_failed", new=AsyncMock(return_value="evt-failed")) as emit_failed_mock, \
             patch("server_modules.run_service.agent_trace_service.finish_trace", new=AsyncMock(return_value={"id": "trace-1"})) as finish_mock:
            transition_live_run_status(
                "run-1",
                "failed",
                run=run,
                now_mono=7.5,
                now_iso="2026-04-06T00:00:00Z",
                terminal_statuses={"completed", "failed", "timeout"},
                local_queue_lock=__import__("threading").Lock(),
                local_pending_run_ids=[],
                local_claimed_runs={},
                archive_run_if_terminal_fn=lambda run_id, payload: None,
                remove_live_run_state_fn=lambda run_id: None,
                sync_local_runtime_state_snapshot_fn=lambda: None,
                persist_live_run_state_fn=lambda run_id, payload: None,
                run_queue_index={},
                metrics_add_fn=lambda key, value: None,
                metrics_inc_fn=lambda key, value=1: None,
            )

        emit_failed_mock.assert_awaited_once()
        finish_mock.assert_awaited_once()

    def test_transition_live_run_status_settles_deployed_agent_cost_cap(self):
        run = {
            "run_id": "run-1",
            "status": "running",
            "_started_mono": 5.0,
            "logs": queue.Queue(),
            "usage_masked": {
                "provider": "openai",
                "model": "gpt-4.1",
                "prompt_tokens": 1000,
                "completion_tokens": 1000,
                "estimated_cost_usd": 1.25,
            },
            "context": {
                "workspace_id": "workspace-1",
                "tenant_id": "tenant-1",
                "metadata": {
                    "deployed_agent_id": "dagent_1",
                },
            },
        }

        with (
            patch("server_modules.run_service.run_state_repository.dispatch_repository_call", side_effect=lambda awaitable, operation: asyncio.run(awaitable)),
            patch("server_modules.run_service.outbox_service.emit_run_transition_event"),
            patch("server_modules.run_service.outbox_service.emit_artifact_created_event"),
            patch("server_modules.run_service.run_async_tool_call", side_effect=lambda coro: asyncio.run(coro)),
            patch(
                "server_modules.run_service.deployed_agent_cost_cap_service.settle_deployed_agent_monthly_cost_cap",
                new=AsyncMock(return_value={"applied": True}),
            ) as settle_mock,
        ):
            transition_live_run_status(
                "run-1",
                "completed",
                run=run,
                now_mono=7.5,
                now_iso="2026-04-06T00:00:00Z",
                terminal_statuses={"completed", "failed", "timeout"},
                local_queue_lock=__import__("threading").Lock(),
                local_pending_run_ids=[],
                local_claimed_runs={},
                archive_run_if_terminal_fn=lambda run_id, payload: None,
                remove_live_run_state_fn=lambda run_id: None,
                sync_local_runtime_state_snapshot_fn=lambda: None,
                persist_live_run_state_fn=lambda run_id, payload: None,
                run_queue_index={},
                metrics_add_fn=lambda key, value: None,
                metrics_inc_fn=lambda key, value=1: None,
            )

        settle_mock.assert_awaited_once()
        self.assertEqual(settle_mock.await_args.kwargs["run_id"], "run-1")
        self.assertEqual(settle_mock.await_args.kwargs["status"], "completed")

    def test_transition_live_run_status_skips_duplicate_cost_cap_settlement_key(self):
        run = {
            "run_id": "run-1",
            "status": "running",
            "_started_mono": 5.0,
            "_deployed_agent_cost_cap_settlement_key": "run-1:completed",
            "logs": queue.Queue(),
            "usage_masked": {
                "provider": "openai",
                "model": "gpt-4.1",
                "prompt_tokens": 1000,
                "completion_tokens": 1000,
                "estimated_cost_usd": 1.25,
            },
            "context": {
                "workspace_id": "workspace-1",
                "tenant_id": "tenant-1",
                "metadata": {
                    "deployed_agent_id": "dagent_1",
                },
            },
        }

        with (
            patch("server_modules.run_service.run_state_repository.dispatch_repository_call", side_effect=lambda awaitable, operation: asyncio.run(awaitable)),
            patch("server_modules.run_service.outbox_service.emit_run_transition_event"),
            patch("server_modules.run_service.outbox_service.emit_artifact_created_event"),
            patch("server_modules.run_service.run_async_tool_call", side_effect=lambda coro: asyncio.run(coro)),
            patch(
                "server_modules.run_service.deployed_agent_cost_cap_service.settle_deployed_agent_monthly_cost_cap",
                new=AsyncMock(return_value={"applied": True}),
            ) as settle_mock,
        ):
            transition_live_run_status(
                "run-1",
                "completed",
                run=run,
                now_mono=7.5,
                now_iso="2026-04-06T00:00:00Z",
                terminal_statuses={"completed", "failed", "timeout"},
                local_queue_lock=__import__("threading").Lock(),
                local_pending_run_ids=[],
                local_claimed_runs={},
                archive_run_if_terminal_fn=lambda run_id, payload: None,
                remove_live_run_state_fn=lambda run_id: None,
                sync_local_runtime_state_snapshot_fn=lambda: None,
                persist_live_run_state_fn=lambda run_id, payload: None,
                run_queue_index={},
                metrics_add_fn=lambda key, value: None,
                metrics_inc_fn=lambda key, value=1: None,
            )

        settle_mock.assert_not_awaited()
        self.assertEqual(run["_deployed_agent_cost_cap_settlement_key"], "run-1:completed")

    def test_transition_live_run_status_continues_when_cost_cap_settlement_fails(self):
        run = {
            "run_id": "run-1",
            "status": "running",
            "_started_mono": 5.0,
            "logs": queue.Queue(),
            "usage_masked": {
                "provider": "openai",
                "model": "gpt-4.1",
                "prompt_tokens": 1000,
                "completion_tokens": 1000,
                "estimated_cost_usd": 1.25,
            },
            "context": {
                "workspace_id": "workspace-1",
                "tenant_id": "tenant-1",
                "metadata": {
                    "deployed_agent_id": "dagent_1",
                },
            },
        }

        with (
            patch("server_modules.run_service.run_state_repository.dispatch_repository_call", side_effect=lambda awaitable, operation: asyncio.run(awaitable)),
            patch("server_modules.run_service.outbox_service.emit_run_transition_event"),
            patch("server_modules.run_service.outbox_service.emit_artifact_created_event"),
            patch("server_modules.run_service.run_async_tool_call", side_effect=lambda coro: asyncio.run(coro)),
            patch(
                "server_modules.run_service.deployed_agent_cost_cap_service.settle_deployed_agent_monthly_cost_cap",
                new=AsyncMock(side_effect=RuntimeError("cap settlement failed")),
            ),
        ):
            transition_live_run_status(
                "run-1",
                "completed",
                run=run,
                now_mono=7.5,
                now_iso="2026-04-06T00:00:00Z",
                terminal_statuses={"completed", "failed", "timeout"},
                local_queue_lock=__import__("threading").Lock(),
                local_pending_run_ids=[],
                local_claimed_runs={},
                archive_run_if_terminal_fn=lambda run_id, payload: None,
                remove_live_run_state_fn=lambda run_id: None,
                sync_local_runtime_state_snapshot_fn=lambda: None,
                persist_live_run_state_fn=lambda run_id, payload: None,
                run_queue_index={},
                metrics_add_fn=lambda key, value: None,
                metrics_inc_fn=lambda key, value=1: None,
            )

        self.assertEqual(run["status"], "completed")

    def test_transition_live_run_status_publishes_safe_summary_bridge_for_local_completion(self):
        run = {
            "status": "running",
            "_started_mono": 5.0,
            "logs": queue.Queue(),
            "result": "Research summary ready.",
            "context": {
                "workspace_id": "workspace-1",
                "tenant_id": "tenant-1",
                "metadata": {
                    "trace_id": "trace-1",
                    "runtime_mode": "local_secure",
                    "active_agent_install_id": "install-research",
                    "runtime_selection": {
                        "hybrid_policy": {
                            "summary_bridge": {
                                "enabled": True,
                                "payload_classes": ["specialist_outcome_summaries"],
                            }
                        }
                    },
                },
            },
        }
        published = []

        with patch("server_modules.run_service.run_state_repository.dispatch_repository_call", side_effect=lambda awaitable, operation: asyncio.run(awaitable)), \
             patch("server_modules.run_service.outbox_service.emit_run_transition_event"), \
             patch("server_modules.run_service.outbox_service.emit_artifact_created_event"), \
             patch("server_modules.memory_service.publish_hybrid_summary_bridge_payload", side_effect=lambda *args, **kwargs: published.append((args, kwargs)) or {"record_id": "record-1"}):
            transition_live_run_status(
                "run-1",
                "completed",
                run=run,
                now_mono=7.5,
                now_iso="2026-04-06T00:00:00Z",
                terminal_statuses={"completed", "failed", "timeout"},
                local_queue_lock=__import__("threading").Lock(),
                local_pending_run_ids=[],
                local_claimed_runs={},
                archive_run_if_terminal_fn=lambda run_id, payload: None,
                remove_live_run_state_fn=lambda run_id: None,
                sync_local_runtime_state_snapshot_fn=lambda: None,
                persist_live_run_state_fn=lambda run_id, payload: None,
                run_queue_index={},
                metrics_add_fn=lambda key, value: None,
                metrics_inc_fn=lambda key, value=1: None,
            )

        self.assertEqual(len(published), 1)
        self.assertEqual(published[0][0][0], "workspace-1")
        self.assertEqual(published[0][1]["payload_class"], "specialist_outcome_summaries")
        self.assertEqual(published[0][1]["summary"], "Research summary ready.")
        self.assertEqual(published[0][1]["agent_install_id"], "install-research")
        self.assertEqual(published[0][1]["source_runtime_mode"], "local_secure")

    def test_transition_live_run_status_skips_summary_bridge_when_not_local(self):
        run = {
            "status": "running",
            "_started_mono": 5.0,
            "logs": queue.Queue(),
            "result": "Research summary ready.",
            "context": {
                "workspace_id": "workspace-1",
                "tenant_id": "tenant-1",
                "metadata": {
                    "trace_id": "trace-1",
                    "runtime_mode": "hosted_secure",
                    "runtime_selection": {
                        "hybrid_policy": {
                            "summary_bridge": {
                                "enabled": True,
                                "payload_classes": ["specialist_outcome_summaries"],
                            }
                        }
                    },
                },
            },
        }

        with patch("server_modules.run_service.run_state_repository.dispatch_repository_call", side_effect=lambda awaitable, operation: asyncio.run(awaitable)), \
             patch("server_modules.run_service.outbox_service.emit_run_transition_event"), \
             patch("server_modules.run_service.outbox_service.emit_artifact_created_event"), \
             patch("server_modules.memory_service.publish_hybrid_summary_bridge_payload") as publish_mock:
            transition_live_run_status(
                "run-1",
                "completed",
                run=run,
                now_mono=7.5,
                now_iso="2026-04-06T00:00:00Z",
                terminal_statuses={"completed", "failed", "timeout"},
                local_queue_lock=__import__("threading").Lock(),
                local_pending_run_ids=[],
                local_claimed_runs={},
                archive_run_if_terminal_fn=lambda run_id, payload: None,
                remove_live_run_state_fn=lambda run_id: None,
                sync_local_runtime_state_snapshot_fn=lambda: None,
                persist_live_run_state_fn=lambda run_id, payload: None,
                run_queue_index={},
                metrics_add_fn=lambda key, value: None,
                metrics_inc_fn=lambda key, value=1: None,
            )

        publish_mock.assert_not_called()

    def test_transition_live_run_status_publishes_bounded_local_private_summary_bridge_when_requested(self):
        run = {
            "status": "running",
            "_started_mono": 5.0,
            "logs": queue.Queue(),
            "result": "Local runtime summary available.",
            "context": {
                "workspace_id": "workspace-1",
                "tenant_id": "tenant-1",
                "metadata": {
                    "trace_id": "trace-1",
                    "runtime_mode": "local_secure",
                    "runtime_selection": {
                        "hybrid_policy": {
                            "summary_bridge": {
                                "enabled": True,
                                "payload_classes": ["bounded_local_private_summaries"],
                            }
                        }
                    },
                },
            },
        }
        published = []

        with patch("server_modules.run_service.run_state_repository.dispatch_repository_call", side_effect=lambda awaitable, operation: asyncio.run(awaitable)), \
             patch("server_modules.run_service.outbox_service.emit_run_transition_event"), \
             patch("server_modules.run_service.outbox_service.emit_artifact_created_event"), \
             patch("server_modules.memory_service.publish_hybrid_summary_bridge_payload", side_effect=lambda *args, **kwargs: published.append((args, kwargs)) or {"record_id": "record-1"}):
            transition_live_run_status(
                "run-1",
                "completed",
                run=run,
                now_mono=7.5,
                now_iso="2026-04-06T00:00:00Z",
                terminal_statuses={"completed", "failed", "timeout"},
                local_queue_lock=__import__("threading").Lock(),
                local_pending_run_ids=[],
                local_claimed_runs={},
                archive_run_if_terminal_fn=lambda run_id, payload: None,
                remove_live_run_state_fn=lambda run_id: None,
                sync_local_runtime_state_snapshot_fn=lambda: None,
                persist_live_run_state_fn=lambda run_id, payload: None,
                run_queue_index={},
                metrics_add_fn=lambda key, value: None,
                metrics_inc_fn=lambda key, value=1: None,
            )

        self.assertEqual(len(published), 1)
        self.assertEqual(published[0][0][0], "workspace-1")
        self.assertEqual(published[0][1]["payload_class"], "bounded_local_private_summaries")
        self.assertEqual(published[0][1]["summary"], "Local runtime summary available.")
        self.assertEqual(published[0][1]["memory_layer"], "local_private_memory")
        self.assertEqual(published[0][1]["source_runtime_mode"], "local_secure")

    def test_load_live_runtime_state_requeues_local_runs_and_fails_interrupted_cloud_runs(self):
        local_log_queue = queue.Queue()
        cloud_log_queue = queue.Queue()
        runs_by_id = {
            "local-run": {
                "status": "running_local",
                "logs": local_log_queue,
                "context": {"metadata": {"execution_target_selected": "local_companion"}},
            },
            "cloud-run": {
                "status": "running",
                "logs": cloud_log_queue,
                "context": {"metadata": {}},
            },
            "done-run": {
                "status": "completed",
                "logs": queue.Queue(),
                "context": {"metadata": {}},
            },
        }
        run_queue_index = {}
        local_pending = []
        removed = []
        persisted = []
        status_changes = []
        sync_calls = []

        load_live_runtime_state(
            startup_sync_fn=lambda: None,
            runs_by_id=runs_by_id,
            run_queue_index=run_queue_index,
            local_claimed_runs={},
            local_pending_run_ids=local_pending,
            local_queue_lock=__import__("threading").Lock(),
            persisted_terminal_statuses={"completed", "failed", "timeout"},
            remove_live_run_state_fn=lambda run_id: removed.append(run_id),
            persist_live_run_state_fn=lambda run_id, run: persisted.append((run_id, run["status"])),
            now_iso_fn=lambda: "2026-04-06T00:00:00Z",
            emit_log_fn=lambda *args, **kwargs: None,
            set_run_status_fn=lambda run_id, status: status_changes.append((run_id, status)),
            sync_local_runtime_state_snapshot_fn=lambda: sync_calls.append(True),
        )

        self.assertEqual(runs_by_id["local-run"]["status"], "queued_local")
        self.assertIn("local-run", local_pending)
        self.assertIn(("local-run", "queued_local"), persisted)
        self.assertEqual(status_changes, [("cloud-run", "failed")])
        self.assertEqual(removed, ["done-run"])
        self.assertEqual(sync_calls, [True])

    def test_persist_and_load_weekly_schedules_round_trip_normalized_state(self):
        schedules = {
            "sched-1": {
                "id": "sched-1",
                "wake_mode": "NEXT-HEARTBEAT",
                "delivery": "",
                "run_log": list(range(12)),
                "pending_heartbeat": 1,
                "pending_heartbeat_slot": "",
                "next_run_at": "",
            }
        }
        writes = []

        persist_weekly_schedules(
            schedules_lock=__import__("threading").Lock(),
            weekly_schedules=schedules,
            safe_write_json_fn=lambda path, payload: writes.append((path, payload)),
            schedules_file="schedules.json",
            utc_now_iso_fn=lambda: "2026-04-06T00:00:00Z",
        )

        loaded = {}
        load_weekly_schedules(
            schedules_lock=__import__("threading").Lock(),
            weekly_schedules=loaded,
            safe_read_json_fn=lambda path, default: writes[0][1],
            schedules_file="schedules.json",
            compute_schedule_next_run_at_fn=lambda item: "2026-04-07T00:00:00Z",
        )

        self.assertEqual(writes[0][1]["updated_at"], "2026-04-06T00:00:00Z")
        self.assertEqual(loaded["sched-1"]["wake_mode"], "next-heartbeat")
        self.assertEqual(loaded["sched-1"]["delivery"], "announce")
        self.assertEqual(loaded["sched-1"]["run_log"], list(range(2, 12)))
        self.assertTrue(loaded["sched-1"]["pending_heartbeat"])
        self.assertIsNone(loaded["sched-1"]["pending_heartbeat_slot"])
        self.assertEqual(loaded["sched-1"]["next_run_at"], "2026-04-07T00:00:00Z")

    def test_persist_and_load_schedules_delegate_to_weekly_helpers(self):
        seen = []

        persist_schedules(
            persist_weekly_schedules_fn=lambda: seen.append("persist"),
        )
        load_schedules(
            load_weekly_schedules_fn=lambda: seen.append("load"),
        )

        self.assertEqual(seen, ["persist", "load"])

    def test_trigger_pending_heartbeat_schedules_starts_runs_and_clears_pending(self):
        schedules = {
            "sched-1": {
                "id": "sched-1",
                "name": "Morning",
                "workspace_id": "ws-1",
                "enabled": True,
                "pending_heartbeat": True,
                "pending_heartbeat_slot": "slot-1",
                "run_request": {"engine": "orion", "workspace_id": "default", "user_goal": "Check inbox"},
            }
        }
        persisted = []

        result = trigger_pending_heartbeat_schedules(
            schedules_lock=__import__("threading").Lock(),
            weekly_schedules=schedules,
            run_start_request_class=lambda **kwargs: kwargs,
            execute_scheduled_run_request_fn=lambda request, schedule_id=None: {"run_id": f"run-for-{schedule_id}"},
            append_schedule_run_log_fn=lambda schedule, **kwargs: {**schedule, "run_log": [kwargs["status"]]},
            persist_schedules_fn=lambda: persisted.append(True),
            utc_now_fn=lambda: datetime(2026, 4, 6, 0, 0, 0, tzinfo=timezone.utc),
            utc_now_iso_fn=lambda: "2026-04-06T00:00:00Z",
        )

        self.assertTrue(result["acted"])
        self.assertEqual(result["started"][0]["run_id"], "run-for-sched-1")
        self.assertFalse(schedules["sched-1"]["pending_heartbeat"])
        self.assertIsNone(schedules["sched-1"]["pending_heartbeat_slot"])
        self.assertEqual(schedules["sched-1"]["run_log"], ["started"])
        self.assertEqual(persisted, [True])

    def test_trigger_pending_heartbeat_schedules_respects_workspace_scope(self):
        schedules = {
            "sched-1": {
                "id": "sched-1",
                "name": "Morning",
                "workspace_id": "ws-1",
                "enabled": True,
                "pending_heartbeat": True,
                "pending_heartbeat_slot": "slot-1",
                "run_request": {"engine": "orion", "workspace_id": "ws-1", "user_goal": "Check inbox"},
            },
            "sched-2": {
                "id": "sched-2",
                "name": "Ops",
                "workspace_id": "ws-2",
                "enabled": True,
                "pending_heartbeat": True,
                "pending_heartbeat_slot": "slot-2",
                "run_request": {"engine": "orion", "workspace_id": "ws-2", "user_goal": "Check alerts"},
            },
        }
        started = []

        result = trigger_pending_heartbeat_schedules(
            schedules_lock=__import__("threading").Lock(),
            weekly_schedules=schedules,
            run_start_request_class=lambda **kwargs: kwargs,
            execute_scheduled_run_request_fn=lambda request, schedule_id=None: started.append(schedule_id) or {"run_id": f"run-for-{schedule_id}"},
            append_schedule_run_log_fn=lambda schedule, **kwargs: {**schedule, "run_log": [kwargs["status"]]},
            persist_schedules_fn=lambda: None,
            utc_now_fn=lambda: datetime(2026, 4, 6, 0, 0, 0, tzinfo=timezone.utc),
            utc_now_iso_fn=lambda: "2026-04-06T00:00:00Z",
            workspace_id="ws-1",
        )

        self.assertTrue(result["acted"])
        self.assertEqual(started, ["sched-1"])
        self.assertFalse(schedules["sched-1"]["pending_heartbeat"])
        self.assertTrue(schedules["sched-2"]["pending_heartbeat"])

    def test_initialize_runtime_services_bootstraps_and_starts_threads_once(self):
        calls = []
        threads = []

        class _Thread:
            def __init__(self, *, target=None, daemon=None):
                self.target = target
                self.daemon = daemon
                self.started = False

            def start(self):
                self.started = True

        def _thread_factory(*, target=None, daemon=None):
            thread = _Thread(target=target, daemon=daemon)
            threads.append(thread)
            return thread

        initialized = {"value": False, "telegram_thread": None}

        initialize_runtime_services(
            is_initialized_fn=lambda: initialized["value"],
            mark_initialized_fn=lambda: initialized.__setitem__("value", True),
            runtime_state_db_path="runtime.sqlite3",
            setup_sessions_path="setup.json",
            provider_profiles_path="profiles.json",
            idempotency_path="idempotency.json",
            init_runtime_state_db_fn=lambda path: calls.append(("init_db", path)),
            sync_acp_manager_paths_fn=lambda **kwargs: calls.append(("sync_paths", kwargs)),
            initialize_chat_stream_runtime_state_fn=lambda: calls.append(("chat_stream", None)),
            load_live_runtime_state_fn=lambda: calls.append(("load_live", None)),
            load_run_history_fn=lambda: calls.append(("load_history", None)),
            load_approval_audit_fn=lambda: calls.append(("load_approval", None)),
            load_channel_events_fn=lambda: calls.append(("load_events", None)),
            load_schedules_fn=lambda: calls.append(("load_schedules", None)),
            load_setup_sessions_fn=lambda: calls.append(("load_setup_sessions", None)),
            load_provider_profiles_fn=lambda: calls.append(("load_provider_profiles", None)),
            load_idempotency_fn=lambda: calls.append(("load_idempotency", None)),
            replay_outbox_events_on_startup_fn=lambda: calls.append(("replay_outbox", None)),
            run_outbox_delivery_forever_fn=lambda: None,
            recover_expired_worker_leases_on_startup_fn=lambda: calls.append(("recover_expired_leases", None)),
            recover_orphaned_local_runs_on_startup_fn=lambda: calls.append(("recover_local", None)),
            recover_delegation_retries_on_startup_fn=lambda: calls.append(("recover_delegation", None)),
            load_runtime_skills_state_fn=lambda: calls.append(("load_skills", None)),
            load_telegram_autopilot_state_fn=lambda: calls.append(("load_telegram_state", None)),
            load_whatsapp_autopilot_state_fn=lambda: calls.append(("load_whatsapp_state", None)),
            local_runtime_watchdog_enabled=True,
            run_local_runtime_watchdog_forever_fn=lambda: None,
            set_local_runtime_watchdog_thread_fn=lambda thread: initialized.__setitem__("watchdog_thread", thread),
            scheduler_enabled=True,
            thread_factory=_thread_factory,
            run_weekly_scheduler_forever_fn=lambda: None,
            telegram_autopilot_enabled=True,
            run_telegram_autopilot_forever_fn=lambda: None,
            set_telegram_autopilot_thread_fn=lambda thread: initialized.__setitem__("telegram_thread", thread),
            whatsapp_autopilot_enabled=True,
            whatsapp_autopilot_activate_fn=lambda: calls.append(("activate_whatsapp", None)),
        )

        self.assertTrue(initialized["value"])
        self.assertEqual(calls[0], ("init_db", "runtime.sqlite3"))
        self.assertIn(("load_schedules", None), calls)
        self.assertIn(("replay_outbox", None), calls)
        self.assertIn(("recover_expired_leases", None), calls)
        self.assertIn(("recover_delegation", None), calls)
        self.assertIn(("activate_whatsapp", None), calls)
        self.assertEqual(len(threads), 4)
        self.assertTrue(all(thread.started for thread in threads))
        self.assertIs(initialized["watchdog_thread"], threads[1])
        self.assertIs(initialized["telegram_thread"], threads[3])

    def test_run_local_runtime_watchdog_pass_records_cleaned_run_ids(self):
        updates = []

        result = run_local_runtime_watchdog_pass(
            cleanup_stale_local_claims_fn=lambda: ["run-1", "run-2"],
            resume_due_checkpoint_recoveries_fn=lambda: ["run-3"],
            update_watchdog_status_fn=lambda **kwargs: updates.append(kwargs),
            utc_now_iso_fn=lambda: "2026-04-06T12:00:00Z",
            interval_seconds=5,
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["cleaned_run_ids"], ["run-1", "run-2"])
        self.assertEqual(result["resumed_run_ids"], ["run-3"])
        self.assertEqual(updates[0]["status"], "ok")
        self.assertEqual(updates[0]["cleaned_run_ids"], ["run-1", "run-2"])
        self.assertEqual(updates[0]["resumed_run_ids"], ["run-3"])

    def test_run_local_runtime_watchdog_pass_records_failures_without_crashing(self):
        updates = []

        result = run_local_runtime_watchdog_pass(
            cleanup_stale_local_claims_fn=lambda: (_ for _ in ()).throw(RuntimeError("lease cleanup broke")),
            resume_due_checkpoint_recoveries_fn=lambda: [],
            update_watchdog_status_fn=lambda **kwargs: updates.append(kwargs),
            utc_now_iso_fn=lambda: "2026-04-06T12:00:00Z",
            interval_seconds=5,
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("lease cleanup broke", result["summary"])
        self.assertEqual(updates[0]["status"], "error")

    def test_execute_workflow_human_node_requires_explicit_approval_even_for_full_trust(self):
        updates = []
        emitted = []
        state = {}

        result = execute_workflow_human_node(
            run_id="run-1",
            node_id="approval_1",
            label="Approve draft",
            variant="approval",
            config={"title": "Approve draft", "decision_options": ["approve", "reject"]},
            current_text="Draft ready",
            state=state,
            context={"metadata": {}},
            log_queue=queue.Queue(),
            update_node_state_fn=lambda *args, **kwargs: updates.append((args, kwargs)),
            wait_for_human_response_fn=lambda *args, **kwargs: {
                "decision": "proceed",
                "raw_decision": "Proceed",
                "note": "Approved explicitly",
                "approved": True,
            },
            agent_machine_full_trust_for_context_fn=lambda context: True,
            node_preview_text_fn=lambda value: str(value),
            json_safe_fn=lambda value: value,
            emit_log_fn=lambda *args, **kwargs: emitted.append((args, kwargs)),
        )

        self.assertEqual(result, "Approve draft: approved")
        self.assertEqual(state["last_data"]["decision"], "proceed")
        self.assertEqual(emitted[0][1]["event"], "workflow_human_resolved")
        self.assertEqual(updates[0][1]["status"], "waiting_human")
        self.assertEqual(updates[1][1]["status"], "succeeded")
        self.assertFalse(updates[1][1]["waiting_for_approval"])

    def test_execute_workflow_human_node_resolves_review_reply(self):
        updates = []
        emitted = []
        state = {}

        result = execute_workflow_human_node(
            run_id="run-2",
            node_id="review_1",
            label="Review draft",
            variant="review",
            config={"title": "Review draft", "instructions": "Share feedback"},
            current_text="Draft ready",
            state=state,
            context={"metadata": {}},
            log_queue=queue.Queue(),
            update_node_state_fn=lambda *args, **kwargs: updates.append((args, kwargs)),
            wait_for_human_response_fn=lambda *args, **kwargs: {
                "decision": "needs changes",
                "raw_decision": "Needs changes",
                "note": "Please fix tone",
                "approved": False,
            },
            agent_machine_full_trust_for_context_fn=lambda context: False,
            node_preview_text_fn=lambda value: str(value),
            json_safe_fn=lambda value: value,
            emit_log_fn=lambda *args, **kwargs: emitted.append((args, kwargs)),
        )

        self.assertEqual(result, "Please fix tone")
        self.assertEqual(updates[0][1]["status"], "waiting_human")
        self.assertEqual(updates[1][1]["status"], "succeeded")
        self.assertFalse(updates[1][1]["waiting_for_approval"])
        self.assertEqual(state["last_data"]["human_response"]["note"], "Please fix tone")
        self.assertEqual(emitted[0][1]["event"], "workflow_human_resolved")

    def test_create_workflow_child_local_run_builds_local_child_request(self):
        captured = []

        child_run_id = create_workflow_child_local_run(
            run_id="run-parent",
            context={
                "engine": "orion",
                "workspace_id": "ws-1",
                "business_plan": "plan",
                "agent_role": "builder",
                "provider": "openai",
                "model": "gpt-test",
                "credential_id": "cred-1",
                "metadata": {"existing": True},
            },
            label="Write file",
            operation={"tool": "read_write_files", "path": "README.md", "mode": "write"},
            summary="Write README",
            execute_workflow_child_run_request_fn=lambda request: captured.append(request) or {
                "run_id": "child-1",
                "route": {"selected": "local_companion"},
            },
            local_execution_pack_id="local-execution-v1",
            execution_target_local_companion="local_companion",
            trust_mode_auto="auto",
            metadata_overrides={"browser_session_profile": "default"},
        )

        self.assertEqual(child_run_id, "child-1")
        request = captured[0]
        self.assertEqual(request.parent_run_id, "run-parent")
        self.assertEqual(request.user_goal, "Write README")
        self.assertEqual(request.metadata["outcome_pack"], "local-execution-v1")
        self.assertEqual(request.metadata["pack_inputs"]["operations"][0]["tool"], "read_write_files")
        self.assertEqual(request.metadata["browser_session_profile"], "default")
        self.assertEqual(request.metadata["workflow_turn_depth"], 1)
        self.assertEqual(request.metadata["workflow_turn_parent_run_id"], "run-parent")
        self.assertEqual(request.metadata["workflow_turn_child_kind"], "local_tool_child_run")

    @patch.dict("os.environ", {"EMPYRALIS_MAX_WORKFLOW_TURN_DEPTH": "1"})
    def test_create_workflow_child_local_run_blocks_depth_limit(self):
        with self.assertRaises(WorkflowRecursionError):
            create_workflow_child_local_run(
                run_id="run-parent",
                context={
                    "engine": "orion",
                    "workspace_id": "ws-1",
                    "metadata": {"workflow_turn_depth": 1},
                },
                label="Write file",
                operation={"tool": "read_write_files", "path": "README.md", "mode": "write"},
                summary="Write README",
                execute_workflow_child_run_request_fn=lambda request: {
                    "run_id": "child-1",
                    "route": {"selected": "local_companion"},
                },
                local_execution_pack_id="local-execution-v1",
                execution_target_local_companion="local_companion",
                trust_mode_auto="auto",
            )

    def test_wait_for_workflow_child_run_emits_wait_and_resume_callbacks(self):
        runs_by_id = [
            {"status": "waiting_for_input", "pending_approval": {"approval_id": "approval-1"}},
            {"status": "running"},
            {"status": "completed", "result": "done"},
        ]
        waiting = []
        resumed = []
        ticks = {"value": 0}

        def _load_run(_run_id):
            if len(runs_by_id) > 1:
                return runs_by_id.pop(0)
            return runs_by_id[0]

        def _monotonic():
            ticks["value"] += 1
            return float(ticks["value"])

        result = wait_for_workflow_child_run(
            child_run_id="child-1",
            timeout_seconds=30,
            load_run_fn=_load_run,
            monotonic_fn=_monotonic,
            sleep_fn=lambda _seconds: None,
            on_waiting_for_input=lambda run_id, child_run: waiting.append((run_id, child_run["status"])),
            on_resumed=lambda run_id, child_run: resumed.append((run_id, child_run["status"])),
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(waiting, [("child-1", "waiting_for_input")])
        self.assertEqual(resumed, [("child-1", "running")])

    def test_execute_workflow_subflow_node_updates_state_from_child_run(self):
        updates = []
        emitted = []
        state = {"last_text": "Initial text"}
        requests = []

        result = execute_workflow_subflow_node(
            run_id="run-parent",
            node_id="subflow_1",
            label="Call child workflow",
            context={
                "engine": "orion",
                "workflow_id": "wf-parent",
                "workspace_id": "ws-1",
                "user_goal": "Do work",
                "business_plan": "Plan work",
                "metadata": {},
            },
            config={"workflow_id": "wf-child", "mode": "sync", "timeout_seconds": 45},
            current_text="Parent text",
            state=state,
            update_node_state_fn=lambda *args, **kwargs: updates.append((args, kwargs)),
            emit_log_fn=lambda *args, **kwargs: emitted.append((args, kwargs)),
            workflow_text_payload_fn=lambda value: str(value or ""),
            node_preview_text_fn=lambda value: str(value or ""),
            execute_workflow_child_run_request_fn=lambda request: requests.append(request) or {
                "run_id": "child-run-1",
                "route": {"selected": "cloud"},
            },
            load_run_fn=lambda _run_id: {
                "status": "completed",
                "result": "Child result",
                "result_data": {"summary": "ok"},
            },
            log_queue=queue.Queue(),
            execution_target_local_companion="local_companion",
        )

        self.assertEqual(result, "Child result")
        self.assertEqual(requests[0].workflow_id, "wf-child")
        self.assertEqual(requests[0].metadata["workflow_turn_depth"], 1)
        self.assertEqual(requests[0].metadata["workflow_turn_child_kind"], "subflow")
        self.assertEqual(state["last_text"], "Child result")
        self.assertEqual(state["last_data"]["child_workflow_id"], "wf-child")
        self.assertEqual(emitted[0][1]["event"], "workflow_subflow_start")
        self.assertEqual(emitted[1][1]["event"], "workflow_subflow_complete")
        self.assertEqual(updates[-1][1]["status"], "succeeded")

    @patch.dict("os.environ", {"EMPYRALIS_MAX_WORKFLOW_TURN_DEPTH": "1"})
    def test_execute_workflow_subflow_node_blocks_cross_workflow_recursion_limit(self):
        with self.assertRaises(WorkflowRecursionError):
            execute_workflow_subflow_node(
                run_id="run-parent",
                node_id="subflow_1",
                label="Call child workflow",
                context={
                    "engine": "orion",
                    "workflow_id": "wf-parent",
                    "workspace_id": "ws-1",
                    "metadata": {"workflow_turn_depth": 1},
                },
                config={"workflow_id": "wf-child", "mode": "sync", "timeout_seconds": 45},
                current_text="Parent text",
                state={"last_text": "Initial text"},
                update_node_state_fn=lambda *args, **kwargs: None,
                emit_log_fn=lambda *args, **kwargs: None,
                workflow_text_payload_fn=lambda value: str(value or ""),
                node_preview_text_fn=lambda value: str(value or ""),
                execute_workflow_child_run_request_fn=lambda request: {
                    "run_id": "child-run-1",
                    "route": {"selected": "cloud"},
                },
                load_run_fn=lambda _run_id: {"status": "completed", "result": "Child result"},
                log_queue=queue.Queue(),
                execution_target_local_companion="local_companion",
            )

    def test_execute_workflow_subflow_node_supports_local_companion_child_runs(self):
        state = {"last_text": "Initial text"}
        requests = []

        result = execute_workflow_subflow_node(
            run_id="run-parent-local",
            node_id="subflow_local",
            label="Call local child workflow",
            context={
                "engine": "orion",
                "workflow_id": "wf-parent",
                "workspace_id": "ws-1",
                "user_goal": "Do local work",
                "business_plan": "Plan local work",
                "metadata": {
                    "execution_target": "local_companion",
                    "execution_target_selected": "local_companion",
                },
            },
            config={"workflow_id": "wf-child-local", "mode": "sync", "timeout_seconds": 45},
            current_text="Parent text",
            state=state,
            update_node_state_fn=lambda *args, **kwargs: None,
            emit_log_fn=lambda *args, **kwargs: None,
            workflow_text_payload_fn=lambda value: str(value or ""),
            node_preview_text_fn=lambda value: str(value or ""),
            execute_workflow_child_run_request_fn=lambda request: requests.append(request) or {
                "run_id": "child-local-run-1",
                "route": {"selected": "local_companion"},
            },
            load_run_fn=lambda _run_id: {
                "status": "completed",
                "result": "Local child result",
                "result_data": {"summary": "local ok"},
            },
            log_queue=queue.Queue(),
            execution_target_local_companion="local_companion",
        )

        self.assertEqual(result, "Local child result")
        self.assertEqual(requests[0].workflow_id, "wf-child-local")
        self.assertEqual(requests[0].metadata["execution_target"], "local_companion")
        self.assertEqual(requests[0].metadata["workflow_turn_depth"], 1)
        self.assertEqual(state["last_data"]["child_run_id"], "child-local-run-1")

    def test_execute_workflow_local_tool_runs_via_child_local_run_lifecycle(self):
        requests = []

        result = execute_workflow_local_tool(
            run_id="run-local",
            context={"metadata": {}},
            config={
                "path": "README.md",
                "mode": "write",
                "execution_target": "local_companion",
                "permissions": {"file_mount_grants": []},
                "timeout_seconds": 30,
            },
            label="Write file",
            variant="file",
            current_text="Hello",
            normalize_execution_target_fn=lambda value: str(value or ""),
            assert_file_mount_access_fn=lambda path, mode, grants, target: {"mode": mode, "mount": {"root": ".", "path": path, "target": target}},
            browser_automation_policy_from_operations_fn=lambda operations: {},
            normalize_action_id_fn=lambda value: str(value or "").strip(),
            workflow_text_payload_fn=lambda value: str(value or ""),
            json_safe_fn=lambda value: value,
            execute_workflow_child_run_request_fn=lambda request: requests.append(request) or {
                "run_id": "child-local-1",
                "route": {"selected": "local_companion"},
            },
            local_execution_pack_id="local-execution-v1",
            execution_target_local_companion="local_companion",
            execution_target_cloud="cloud",
            trust_mode_auto="auto",
            browser_auth_actions=set(),
            load_run_fn=lambda _run_id: {
                "status": "completed",
                "result": "Local tool finished",
                "result_data": {"ok": True},
            },
        )

        self.assertEqual(result["summary"], "Local tool finished")
        self.assertEqual(result["result_data"]["local_child_run_id"], "child-local-1")
        self.assertEqual(requests[0].metadata["pack_inputs"]["operations"][0]["tool"], "filesystem.read_write")
        self.assertEqual(requests[0].metadata["workflow_turn_depth"], 1)

    def test_safe_int_and_normalize_requested_max_iterations(self):
        self.assertEqual(safe_int("7", 0), 7)
        self.assertEqual(safe_int("bad", 5), 5)
        self.assertEqual(normalize_requested_max_iterations("9"), 9)
        self.assertIsNone(normalize_requested_max_iterations(""))

    def test_build_service_bundles_preserve_shared_callbacks(self):
        preparation = build_run_preparation_services(
            engine_registry={"orion": object()},
            engine_validation_errors=[],
            supported_outcome_packs={"local_execution"},
            normalize_requested_max_iterations=normalize_requested_max_iterations,
            normalize_trust_mode=lambda value: str(value or ""),
            trust_mode_aliases={},
            valid_trust_modes={"guarded"},
            normalize_execution_target=lambda value: str(value or ""),
            valid_execution_targets={"cloud"},
            normalize_run_id_token=lambda value: str(value or "") or None,
            normalize_agent_role=lambda value: str(value or ""),
            detect_agent_role=lambda req, metadata: ("builder", "default"),
            resolve_app_permissions=lambda app_id: {},
            action_policy_from_app_permissions=lambda permissions: {},
            merge_action_policies=lambda existing, new: {},
            fetch_workflow_snapshot=lambda workflow_id: None,
        )
        creation = build_prepared_run_creation_services(
            decide_execution_target=lambda metadata, schedule_id=None: {"selected": "cloud"},
            apply_execution_route_metadata=lambda metadata, route: metadata,
            build_doctor_run_gate=lambda **kwargs: {"blocking": False},
            agent_machine_inherited_owner_user_id=lambda owner_user_id: owner_user_id,
            compute_tool_policy_precheck=lambda preview_context: {"blocked_count": 0},
            apply_browser_execution_metadata=apply_browser_execution_metadata,
            local_execution_block_prompt=local_execution_block_prompt,
            resolve_runtime_policy_mode=lambda metadata, selected_target=None: {"policy_mode": "guarded"},
            agent_machine_full_trust_enabled=lambda owner_user_id: False,
            local_execution_requires_start_confirmation=local_execution_requires_start_confirmation,
            mark_local_execution_tools_approved=mark_local_execution_tools_approved,
            precheck_human_action_labels=precheck_human_action_labels,
            local_execution_confirmation_prompt=local_execution_confirmation_prompt,
            begin_run_pending_confirmation=lambda *args, **kwargs: {"approval_id": "approval-1"},
            create_run=lambda **kwargs: "run-1",
        )

        self.assertIs(preparation.normalize_requested_max_iterations, normalize_requested_max_iterations)
        self.assertIs(creation.mark_local_execution_tools_approved, mark_local_execution_tools_approved)

    def test_prepare_legacy_run_start_request_uses_built_shared_services(self):
        request = RunStartRequest(engine="orion", workspace_id="default", user_goal="hello")

        prepared = prepare_legacy_run_start_request(
            request,
            services=LegacyRunPreparationServices(
                build_preparation_services=lambda: build_run_preparation_services(
                    engine_registry={"orion": object()},
                    engine_validation_errors=[],
                    supported_outcome_packs={"local_execution"},
                    normalize_requested_max_iterations=normalize_requested_max_iterations,
                    normalize_trust_mode=lambda value: str(value or ""),
                    trust_mode_aliases={},
                    valid_trust_modes={"guarded"},
                    normalize_execution_target=lambda value: str(value or ""),
                    valid_execution_targets={"cloud"},
                    normalize_run_id_token=lambda value: str(value or "") or None,
                    normalize_agent_role=lambda value: str(value or ""),
                    detect_agent_role=lambda req, metadata: ("builder", "default"),
                    resolve_app_permissions=lambda app_id: {},
                    action_policy_from_app_permissions=lambda permissions: {},
                    merge_action_policies=lambda existing, new: {},
                    fetch_workflow_snapshot=lambda workflow_id: None,
                )
            ),
        )

        self.assertEqual(prepared["engine"], "orion")
        self.assertEqual(prepared["metadata"]["agent_role"], "builder")

    def test_build_legacy_local_execution_creation_services_uses_shared_local_helpers(self):
        creation = build_legacy_local_execution_creation_services(
            callbacks=LegacyLocalExecutionCreationCallbacks(
                decide_execution_target=lambda metadata, schedule_id=None: {"selected": "cloud"},
                apply_execution_route_metadata=lambda metadata, route: metadata,
                build_doctor_run_gate=lambda **kwargs: {"blocking": False},
                agent_machine_inherited_owner_user_id=lambda owner_user_id: owner_user_id,
                compute_tool_policy_precheck=lambda preview_context: {"blocked_count": 0},
                resolve_runtime_policy_mode=lambda metadata, selected_target=None: {"policy_mode": "guarded"},
                agent_machine_full_trust_enabled=lambda owner_user_id: False,
                begin_run_pending_confirmation=lambda *args, **kwargs: {"approval_id": "approval-1"},
                create_run=lambda **kwargs: "run-1",
                local_execution_target="local_companion",
                local_execution_pack_id="local-execution-v1",
            )
        )

        self.assertIs(creation.apply_browser_execution_metadata, apply_browser_execution_metadata)
        self.assertIs(creation.mark_local_execution_tools_approved, mark_local_execution_tools_approved)

    def test_build_run_execution_services_preserves_callbacks(self):
        owner = object()
        prepare = object()
        create = object()

        services = build_run_execution_services(
            stamp_request_owner=owner,
            prepare_run_start_request=prepare,
            create_run_from_request=create,
        )

        self.assertIs(services.stamp_request_owner, owner)
        self.assertIs(services.prepare_run_start_request, prepare)
        self.assertIs(services.create_run_from_request, create)

    def test_build_system_run_execution_services_uses_noop_owner_stamp(self):
        prepare = object()
        create = object()

        services = build_system_run_execution_services(
            prepare_run_start_request=prepare,
            create_run_from_request=create,
        )

        request = object()
        self.assertIs(services.prepare_run_start_request, prepare)
        self.assertIs(services.create_run_from_request, create)
        self.assertIs(services.stamp_request_owner(request, object()), request)

    def test_build_server_system_run_execution_services_uses_server_exports(self):
        prepare = object()
        create = object()

        services = build_server_system_run_execution_services(
            late_server_export=lambda name: {
                "_prepare_run_start_request": prepare,
                "_create_run_from_request": create,
            }[name],
        )

        request = object()
        self.assertIs(services.prepare_run_start_request, prepare)
        self.assertIs(services.create_run_from_request, create)
        self.assertIs(services.stamp_request_owner(request, object()), request)

    def test_build_system_run_execution_services_from_namespace_prefers_namespace_callbacks(self):
        prepare = object()
        create = object()

        services = build_system_run_execution_services_from_namespace(
            namespace={
                "_prepare_run_start_request": prepare,
                "_create_run_from_request": create,
            }
        )

        request = object()
        self.assertIs(services.prepare_run_start_request, prepare)
        self.assertIs(services.create_run_from_request, create)
        self.assertIs(services.stamp_request_owner(request, object()), request)

    def test_build_system_run_execution_services_from_namespace_uses_fallbacks(self):
        prepare = object()
        create = object()

        services = build_system_run_execution_services_from_namespace(
            namespace={},
            prepare_run_start_request_fallback=lambda: prepare,
            create_run_from_request_fallback=lambda: create,
        )

        self.assertIs(services.prepare_run_start_request, prepare)
        self.assertIs(services.create_run_from_request, create)

    def test_build_schedule_system_run_execution_services_binds_schedule_id(self):
        services = build_schedule_system_run_execution_services(
            prepare_run_start_request=lambda req: {"metadata": {}},
            create_run_from_request=lambda req, schedule_id=None: {"run_id": "run-1", "schedule_id": schedule_id},
            schedule_id="sched-7",
        )

        result = services.create_run_from_request(object())

        self.assertEqual(result["schedule_id"], "sched-7")

    def test_build_namespace_delegated_system_run_execution_services_uses_namespace_callbacks(self):
        prepare = object()
        create = object()

        services = build_namespace_delegated_system_run_execution_services(
            namespace={
                "_prepare_run_start_request": prepare,
                "_create_run_from_request": create,
            }
        )

        request = object()
        self.assertIs(services.prepare_run_start_request, prepare)
        self.assertIs(services.create_run_from_request, create)
        self.assertIs(services.stamp_request_owner(request, object()), request)

    def test_build_delegated_run_execution_services_uses_namespace_callbacks(self):
        prepare = object()
        create = object()

        services = build_delegated_run_execution_services(
            namespace={
                "_prepare_run_start_request": prepare,
                "_create_run_from_request": create,
            }
        )

        request = object()
        self.assertIs(services.prepare_run_start_request, prepare)
        self.assertIs(services.create_run_from_request, create)
        self.assertIs(services.stamp_request_owner(request, object()), request)

    def test_build_server_run_execution_services_uses_server_exports(self):
        owner = object()
        prepare = object()
        create = object()

        services = build_server_run_execution_services(
            stamp_request_owner=owner,
            late_server_export=lambda name: {
                "_prepare_run_start_request": prepare,
                "_create_run_from_request": create,
            }[name],
        )

        self.assertIs(services.stamp_request_owner, owner)
        self.assertIs(services.prepare_run_start_request, prepare)
        self.assertIs(services.create_run_from_request, create)

    def test_build_run_creation_services_preserves_callback(self):
        create = object()

        services = build_run_creation_services(create_run_from_request=create)

        self.assertIs(services.create_run_from_request, create)

    def test_build_server_run_creation_services_uses_server_exports(self):
        create = object()

        services = build_server_run_creation_services(
            late_server_export=lambda name: {"_create_run_from_request": create}[name]
        )

        self.assertIs(services.create_run_from_request, create)

    def test_build_run_routing_preview_services_preserves_callbacks(self):
        prepare = object()
        precheck = object()

        services = build_run_routing_preview_services(
            prepare_run_start_request=prepare,
            compute_tool_policy_precheck=precheck,
        )

        self.assertIs(services.prepare_run_start_request, prepare)
        self.assertIs(services.compute_tool_policy_precheck, precheck)

    def test_build_server_run_routing_preview_services_uses_server_exports(self):
        prepare = object()
        precheck = object()

        services = build_server_run_routing_preview_services(
            late_server_export=lambda name: {
                "_prepare_run_start_request": prepare,
                "_compute_tool_policy_precheck": precheck,
            }[name]
        )

        self.assertIs(services.prepare_run_start_request, prepare)
        self.assertIs(services.compute_tool_policy_precheck, precheck)

    def test_build_legacy_run_execution_services_wraps_execution_callbacks(self):
        owner = object()
        prepare = object()
        create = object()

        services = build_legacy_run_execution_services(
            callbacks=LegacyRunExecutionCallbacks(
                stamp_request_owner=owner,
                prepare_run_start_request=prepare,
                create_run_from_request=create,
            )
        )

        self.assertIs(services.stamp_request_owner, owner)
        self.assertIs(services.prepare_run_start_request, prepare)
        self.assertIs(services.create_run_from_request, create)

    def test_build_legacy_run_execution_services_from_values_wraps_execution_callbacks(self):
        owner = object()
        prepare = object()
        create = object()

        services = build_legacy_run_execution_services_from_values(
            stamp_request_owner=owner,
            prepare_run_start_request=prepare,
            create_run_from_request=create,
        )

        self.assertIs(services.stamp_request_owner, owner)
        self.assertIs(services.prepare_run_start_request, prepare)
        self.assertIs(services.create_run_from_request, create)

    def test_build_legacy_orion_preparation_services_preserves_postprocess_callback(self):
        services = build_legacy_orion_preparation_services(
            callbacks=LegacyOrionPreparationCallbacks(
                engine_registry={"orion": object()},
                engine_validation_errors=[],
                supported_outcome_packs={"local_execution"},
                normalize_requested_max_iterations=normalize_requested_max_iterations,
                normalize_trust_mode=lambda value: str(value or ""),
                trust_mode_aliases={},
                valid_trust_modes={"guarded"},
                normalize_execution_target=lambda value: str(value or ""),
                valid_execution_targets={"cloud"},
                normalize_run_id_token=lambda value: str(value or "") or None,
                normalize_agent_role=lambda value: str(value or ""),
                detect_agent_role=lambda req, metadata: ("builder", "default"),
                resolve_app_permissions=lambda app_id: {},
                action_policy_from_app_permissions=lambda permissions: {},
                merge_action_policies=lambda existing, new: {},
                fetch_workflow_snapshot=lambda workflow_id: None,
                postprocess_metadata=lambda req, metadata: {**metadata, "postprocessed": True},
            )
        )

        prepared = prepare_run_start_request(
            RunStartRequest(engine="orion", workspace_id="default", user_goal="hello"),
            services=services,
        )

        self.assertTrue(prepared["metadata"]["postprocessed"])

    def test_build_legacy_run_preparation_services_wraps_orion_callbacks(self):
        services = build_legacy_run_preparation_services(
            callbacks=LegacyOrionPreparationCallbacks(
                engine_registry={"orion": object()},
                engine_validation_errors=[],
                supported_outcome_packs={"local_execution"},
                normalize_requested_max_iterations=normalize_requested_max_iterations,
                normalize_trust_mode=lambda value: str(value or ""),
                trust_mode_aliases={},
                valid_trust_modes={"guarded"},
                normalize_execution_target=lambda value: str(value or ""),
                valid_execution_targets={"cloud"},
                normalize_run_id_token=lambda value: str(value or "") or None,
                normalize_agent_role=lambda value: str(value or ""),
                detect_agent_role=lambda req, metadata: ("builder", "default"),
                resolve_app_permissions=lambda app_id: {},
                action_policy_from_app_permissions=lambda permissions: {},
                merge_action_policies=lambda existing, new: {},
                fetch_workflow_snapshot=lambda workflow_id: None,
            )
        )

        prepared = prepare_legacy_run_start_request(
            RunStartRequest(engine="orion", workspace_id="default", user_goal="hello"),
            services=services,
        )

        self.assertEqual(prepared["engine"], "orion")
        self.assertEqual(prepared["metadata"]["agent_role"], "builder")

    def test_build_runs_result_services_preserve_shared_result_builders(self):
        self.assertIsNotNone(build_runs_core_result_services().build_result)
        self.assertIsNotNone(build_runs_delegation_result_services().build_result)

    def test_build_runs_core_legacy_request_services_uses_core_result_builder(self):
        services = build_runs_core_legacy_request_services(
            prepare_run_start_request=lambda req: {"engine": "orion", "metadata": {}},
            decide_execution_target=lambda metadata, schedule_id=None: {"selected": "cloud"},
            apply_execution_route_metadata=lambda metadata, route: metadata,
            build_doctor_run_gate=lambda **kwargs: {"blocking": False},
            agent_machine_inherited_owner_user_id=lambda owner_user_id: owner_user_id,
            compute_tool_policy_precheck=lambda preview_context: {"blocked_count": 0},
            resolve_runtime_policy_mode=lambda metadata, selected_target=None: {"policy_mode": "guarded"},
            agent_machine_full_trust_enabled=lambda owner_user_id: False,
            begin_run_pending_confirmation=lambda *args, **kwargs: {"approval_id": "approval-1"},
            create_run=lambda **kwargs: "run-1",
            local_execution_target="local_companion",
            local_execution_pack_id="local-execution-v1",
            load_created_run=lambda run_id: {"active_profile_id": "profile-1"},
            now_iso=lambda: "2026-04-05T00:00:00Z",
        )

        self.assertIsNotNone(services.result_services.build_result)
        self.assertIs(services.build_creation_services().load_created_run("run-1")["active_profile_id"], "profile-1")

    def test_build_runs_delegation_legacy_request_services_uses_delegation_result_builder(self):
        services = build_runs_delegation_legacy_request_services(
            prepare_run_start_request=lambda req: {"engine": "orion", "metadata": {}},
            decide_execution_target=lambda metadata, schedule_id=None: {"selected": "cloud"},
            apply_execution_route_metadata=lambda metadata, route: metadata,
            build_doctor_run_gate=lambda **kwargs: {"blocking": False},
            agent_machine_inherited_owner_user_id=lambda owner_user_id: owner_user_id,
            compute_tool_policy_precheck=lambda preview_context: {"blocked_count": 0},
            resolve_runtime_policy_mode=lambda metadata, selected_target=None: {"policy_mode": "guarded"},
            agent_machine_full_trust_enabled=lambda owner_user_id: False,
            begin_run_pending_confirmation=lambda *args, **kwargs: {"approval_id": "approval-1"},
            create_run=lambda **kwargs: "run-1",
            local_execution_target="local_companion",
            local_execution_pack_id="local-execution-v1",
            now_iso=lambda: "2026-04-05T00:00:00Z",
        )

        self.assertIsNotNone(services.result_services.build_result)
        self.assertIsNone(services.build_creation_services().load_created_run)

    def test_build_runs_delegation_runtime_request_services_from_namespace_prefers_namespace_callbacks(self):
        namespace = {
            "_compute_tool_policy_precheck": lambda preview_context: {"blocked_count": 1},
            "_begin_run_pending_confirmation": lambda *args, **kwargs: {"approval_id": "approval-1"},
            "create_run": lambda **kwargs: "run-1",
        }

        services = build_runs_delegation_runtime_request_services_from_namespace(
            namespace=namespace,
            prepare_run_start_request=lambda req: {"engine": "orion", "metadata": {}},
            decide_execution_target=lambda metadata, schedule_id=None: {"selected": "cloud"},
            apply_execution_route_metadata=lambda metadata, route: metadata,
            build_doctor_run_gate=lambda **kwargs: {"blocking": False},
            agent_machine_inherited_owner_user_id=lambda owner_user_id: owner_user_id,
            resolve_runtime_policy_mode=lambda metadata, selected_target=None: {"policy_mode": "guarded"},
            agent_machine_full_trust_enabled=lambda owner_user_id: False,
            local_execution_target="local_companion",
            local_execution_pack_id="local-execution-v1",
        )

        creation = services.build_creation_services()
        self.assertEqual(creation.compute_tool_policy_precheck({})["blocked_count"], 1)
        self.assertEqual(creation.begin_run_pending_confirmation()["approval_id"], "approval-1")
        self.assertEqual(creation.create_run(), "run-1")

    def test_build_runs_delegation_runtime_request_services_from_namespace_uses_fallbacks(self):
        services = build_runs_delegation_runtime_request_services_from_namespace(
            namespace={},
            prepare_run_start_request=lambda req: {"engine": "orion", "metadata": {}},
            decide_execution_target=lambda metadata, schedule_id=None: {"selected": "cloud"},
            apply_execution_route_metadata=lambda metadata, route: metadata,
            build_doctor_run_gate=lambda **kwargs: {"blocking": False},
            agent_machine_inherited_owner_user_id=lambda owner_user_id: owner_user_id,
            resolve_runtime_policy_mode=lambda metadata, selected_target=None: {"policy_mode": "guarded"},
            agent_machine_full_trust_enabled=lambda owner_user_id: False,
            local_execution_target="local_companion",
            local_execution_pack_id="local-execution-v1",
            compute_tool_policy_precheck_fallback=lambda: (lambda preview_context: {"blocked_count": 2}),
            begin_run_pending_confirmation_fallback=lambda: (lambda *args, **kwargs: {"approval_id": "approval-2"}),
            create_run_fallback=lambda: (lambda **kwargs: "run-2"),
        )

        creation = services.build_creation_services()
        self.assertEqual(creation.compute_tool_policy_precheck({})["blocked_count"], 2)
        self.assertEqual(creation.begin_run_pending_confirmation()["approval_id"], "approval-2")
        self.assertEqual(creation.create_run(), "run-2")

    def test_build_runs_core_runtime_request_services_from_namespace_prefers_namespace_callbacks(self):
        namespace = {
            "_compute_tool_policy_precheck": lambda preview_context: {"blocked_count": 3},
            "_begin_run_pending_confirmation": lambda *args, **kwargs: {"approval_id": "approval-3"},
            "create_run": lambda **kwargs: "run-3",
        }

        services = build_runs_core_runtime_request_services_from_namespace(
            namespace=namespace,
            prepare_run_start_request=lambda req: {"engine": "orion", "metadata": {}},
            decide_execution_target=lambda metadata, schedule_id=None: {"selected": "cloud"},
            apply_execution_route_metadata=lambda metadata, route: metadata,
            build_doctor_run_gate=lambda **kwargs: {"blocking": False},
            agent_machine_inherited_owner_user_id=lambda owner_user_id: owner_user_id,
            resolve_runtime_policy_mode=lambda metadata, selected_target=None: {"policy_mode": "guarded"},
            agent_machine_full_trust_enabled=lambda owner_user_id: False,
            local_execution_target="local_companion",
            local_execution_pack_id="local-execution-v1",
            load_created_run=lambda run_id: {"run_id": run_id},
        )

        creation = services.build_creation_services()
        self.assertEqual(creation.compute_tool_policy_precheck({})["blocked_count"], 3)
        self.assertEqual(creation.begin_run_pending_confirmation()["approval_id"], "approval-3")
        self.assertEqual(creation.create_run(), "run-3")
        self.assertEqual(creation.load_created_run("run-x")["run_id"], "run-x")

    def test_build_runs_core_runtime_request_services_from_namespace_uses_fallbacks(self):
        services = build_runs_core_runtime_request_services_from_namespace(
            namespace={},
            prepare_run_start_request=lambda req: {"engine": "orion", "metadata": {}},
            decide_execution_target=lambda metadata, schedule_id=None: {"selected": "cloud"},
            apply_execution_route_metadata=lambda metadata, route: metadata,
            build_doctor_run_gate=lambda **kwargs: {"blocking": False},
            agent_machine_inherited_owner_user_id=lambda owner_user_id: owner_user_id,
            resolve_runtime_policy_mode=lambda metadata, selected_target=None: {"policy_mode": "guarded"},
            agent_machine_full_trust_enabled=lambda owner_user_id: False,
            local_execution_target="local_companion",
            local_execution_pack_id="local-execution-v1",
            compute_tool_policy_precheck_fallback=lambda: (lambda preview_context: {"blocked_count": 4}),
            begin_run_pending_confirmation_fallback=lambda: (lambda *args, **kwargs: {"approval_id": "approval-4"}),
            create_run_fallback=lambda: (lambda **kwargs: "run-4"),
        )

        creation = services.build_creation_services()
        self.assertEqual(creation.compute_tool_policy_precheck({})["blocked_count"], 4)
        self.assertEqual(creation.begin_run_pending_confirmation()["approval_id"], "approval-4")
        self.assertEqual(creation.create_run(), "run-4")

    def test_create_live_run_bootstraps_live_record_lifecycle(self):
        runs_by_id = {}
        run_queue_index = {}
        started = []
        run_id = create_live_run(
            "orion",
            {"workspace_id": "default", "metadata": {}},
            runtime_db_path="runtime.sqlite3",
            sync_acp_manager_paths_fn=lambda **kwargs: None,
            selected_execution_target_from_context_fn=lambda context: "cloud",
            runs_by_id=runs_by_id,
            run_queue_index=run_queue_index,
            metrics_inc_fn=lambda key, amount: None,
            persist_live_run_state_fn=lambda run_id, run: None,
            hydrate_run_memory_context_fn=lambda run_id, run: None,
            enqueue_local_companion_run_fn=lambda run_id: None,
            start_background_run_fn=lambda active_run_id: started.append(active_run_id),
            local_companion_target="local_companion",
            provider_profiles={"profile-1": {"provider": "openai", "model": "gpt-5", "label": "Fast"}},
            memory_enabled=True,
            utc_now_iso_fn=lambda: "2026-04-06T00:00:01Z",
            inject_runtime_skill_defaults_fn=lambda context: context.setdefault("metadata", {}).update({"skill_defaults": True}),
            compute_tool_policy_precheck_fn=lambda context: {"blocked_count": 0},
            create_live_run_initial_fn=lambda *args, **kwargs: {"version": 0, "registered_at": "2026-04-06T00:00:00Z"},
            record_run_transition_fn=lambda *args, **kwargs: None,
            uuid4_fn=lambda: "run-live-1",
            utcnow_fn=lambda: datetime(2026, 4, 6, 0, 0, 0),
            monotonic_fn=lambda: 123.0,
        )

        self.assertEqual(run_id, "run-live-1")
        self.assertEqual(started, ["run-live-1"])
        self.assertIn("run-live-1", runs_by_id)
        live_run = runs_by_id["run-live-1"]
        self.assertEqual(live_run["status"], "starting")
        self.assertEqual(live_run["active_provider"], None)
        self.assertEqual(live_run["context"]["metadata"]["tool_policy_precheck"]["blocked_count"], 0)
        self.assertTrue(live_run["context"]["metadata"]["skill_defaults"])

    @patch("server_modules.run_service.record_reliability_latency_sample")
    def test_create_live_run_records_enqueue_ack_latency(self, mock_record_latency):
        monotonic_values = iter([123.0, 123.25])

        run_id = create_live_run(
            "orion",
            {"workspace_id": "default", "metadata": {}},
            runtime_db_path="runtime.sqlite3",
            sync_acp_manager_paths_fn=lambda **kwargs: None,
            selected_execution_target_from_context_fn=lambda context: "cloud",
            runs_by_id={},
            run_queue_index={},
            metrics_inc_fn=lambda key, amount: None,
            persist_live_run_state_fn=lambda run_id, run: None,
            hydrate_run_memory_context_fn=lambda run_id, run: None,
            enqueue_local_companion_run_fn=lambda run_id: None,
            start_background_run_fn=lambda active_run_id: None,
            local_companion_target="local_companion",
            provider_profiles={},
            memory_enabled=False,
            utc_now_iso_fn=lambda: "2026-04-06T00:00:01Z",
            create_live_run_initial_fn=lambda *args, **kwargs: {"version": 0, "registered_at": "2026-04-06T00:00:00Z"},
            record_run_transition_fn=lambda *args, **kwargs: None,
            uuid4_fn=lambda: "run-live-metric",
            utcnow_fn=lambda: datetime(2026, 4, 6, 0, 0, 0),
            monotonic_fn=lambda: next(monotonic_values),
        )

        self.assertEqual(run_id, "run-live-metric")
        self.assertEqual(mock_record_latency.call_args.args[0], "run_enqueue_ack_latency_ms")
        self.assertEqual(mock_record_latency.call_args.args[1], 250.0)

    def test_create_live_run_queues_local_companion_target_without_background_start(self):
        enqueued = []
        started = []

        run_id = create_live_run(
            "orion",
            {"workspace_id": "default", "metadata": {}},
            runtime_db_path="runtime.sqlite3",
            sync_acp_manager_paths_fn=lambda **kwargs: None,
            selected_execution_target_from_context_fn=lambda context: "local_companion",
            runs_by_id={},
            run_queue_index={},
            metrics_inc_fn=lambda key, amount: None,
            persist_live_run_state_fn=lambda run_id, run: None,
            hydrate_run_memory_context_fn=lambda run_id, run: run.setdefault("hydrated", True),
            enqueue_local_companion_run_fn=lambda active_run_id: enqueued.append(active_run_id),
            start_background_run_fn=lambda active_run_id: started.append(active_run_id),
            local_companion_target="local_companion",
            provider_profiles={},
            memory_enabled=False,
            utc_now_iso_fn=lambda: "2026-04-06T00:00:01Z",
            create_live_run_initial_fn=lambda *args, **kwargs: {"version": 0, "registered_at": "2026-04-06T00:00:00Z"},
            record_run_transition_fn=lambda *args, **kwargs: None,
            uuid4_fn=lambda: "run-live-local",
            utcnow_fn=lambda: datetime(2026, 4, 6, 0, 0, 0),
            monotonic_fn=lambda: 123.0,
        )

        self.assertEqual(run_id, "run-live-local")
        self.assertEqual(enqueued, ["run-live-local"])
        self.assertEqual(started, [])

    def test_build_runs_core_legacy_preparation_services_preserves_postprocess(self):
        services = build_runs_core_legacy_preparation_services(
            engine_registry={"orion": object()},
            engine_validation_errors=[],
            supported_outcome_packs={"local_execution"},
            normalize_requested_max_iterations=normalize_requested_max_iterations,
            normalize_trust_mode=lambda value: str(value or ""),
            trust_mode_aliases={},
            valid_trust_modes={"guarded"},
            normalize_execution_target=lambda value: str(value or ""),
            valid_execution_targets={"cloud"},
            normalize_run_id_token=lambda value: str(value or "") or None,
            normalize_agent_role=lambda value: str(value or ""),
            detect_agent_role=lambda req, metadata: ("builder", "default"),
            resolve_app_permissions=lambda app_id: {},
            action_policy_from_app_permissions=lambda permissions: {},
            merge_action_policies=lambda existing, new: {},
            fetch_workflow_snapshot=lambda workflow_id: None,
            postprocess_metadata=lambda req, metadata: {**metadata, "postprocessed": True},
        )

        prepared = prepare_legacy_run_start_request(
            RunStartRequest(engine="orion", workspace_id="default", user_goal="hello"),
            services=services,
        )

        self.assertTrue(prepared["metadata"]["postprocessed"])

    def test_build_runs_core_runtime_preparation_services_from_namespace_uses_namespace_callbacks(self):
        services = build_runs_core_runtime_preparation_services_from_namespace(
            namespace={
                "_normalize_requested_max_iterations": normalize_requested_max_iterations,
                "_normalize_run_id_token": lambda value: str(value or "") or None,
                "_detect_agent_role": lambda req, metadata: ("builder", "default"),
                "_bind_obvious_connector_write_intent": lambda req, metadata: {**metadata, "postprocessed": True},
            },
            engine_registry={"orion": object()},
            engine_validation_errors=[],
            supported_outcome_packs={"local_execution"},
            normalize_trust_mode=lambda value: str(value or ""),
            trust_mode_aliases={},
            valid_trust_modes={"guarded"},
            normalize_execution_target=lambda value: str(value or ""),
            valid_execution_targets={"cloud"},
            normalize_agent_role=lambda value: str(value or ""),
            resolve_app_permissions=lambda app_id: {},
            action_policy_from_app_permissions=lambda permissions: {},
            merge_action_policies=lambda existing, new: {},
            fetch_workflow_snapshot=lambda workflow_id: None,
        )

        prepared = prepare_legacy_run_start_request(
            RunStartRequest(engine="orion", workspace_id="default", user_goal="hello"),
            services=services,
        )

        self.assertTrue(prepared["metadata"]["postprocessed"])

    def test_build_runs_delegation_legacy_preparation_services_omits_postprocess(self):
        services = build_runs_delegation_legacy_preparation_services(
            engine_registry={"orion": object()},
            engine_validation_errors=[],
            supported_outcome_packs={"local_execution"},
            normalize_requested_max_iterations=normalize_requested_max_iterations,
            normalize_trust_mode=lambda value: str(value or ""),
            trust_mode_aliases={},
            valid_trust_modes={"guarded"},
            normalize_execution_target=lambda value: str(value or ""),
            valid_execution_targets={"cloud"},
            normalize_run_id_token=lambda value: str(value or "") or None,
            normalize_agent_role=lambda value: str(value or ""),
            detect_agent_role=lambda req, metadata: ("builder", "default"),
            resolve_app_permissions=lambda app_id: {},
            action_policy_from_app_permissions=lambda permissions: {},
            merge_action_policies=lambda existing, new: {},
            fetch_workflow_snapshot=lambda workflow_id: None,
        )

        prepared = prepare_legacy_run_start_request(
            RunStartRequest(engine="orion", workspace_id="default", user_goal="hello"),
            services=services,
        )

        self.assertNotIn("postprocessed", prepared["metadata"])

    def test_build_runs_delegation_runtime_preparation_services_from_namespace_uses_namespace_callbacks(self):
        services = build_runs_delegation_runtime_preparation_services_from_namespace(
            namespace={
                "_normalize_requested_max_iterations": normalize_requested_max_iterations,
                "_normalize_run_id_token": lambda value: str(value or "") or None,
                "_detect_agent_role": lambda req, metadata: ("builder", "default"),
            },
            engine_registry={"orion": object()},
            engine_validation_errors=[],
            supported_outcome_packs={"local_execution"},
            normalize_trust_mode=lambda value: str(value or ""),
            trust_mode_aliases={},
            valid_trust_modes={"guarded"},
            normalize_execution_target=lambda value: str(value or ""),
            valid_execution_targets={"cloud"},
            normalize_agent_role=lambda value: str(value or ""),
            resolve_app_permissions=lambda app_id: {},
            action_policy_from_app_permissions=lambda permissions: {},
            merge_action_policies=lambda existing, new: {},
            fetch_workflow_snapshot=lambda workflow_id: None,
        )

        prepared = prepare_legacy_run_start_request(
            RunStartRequest(engine="orion", workspace_id="default", user_goal="hello"),
            services=services,
        )

        self.assertNotIn("postprocessed", prepared["metadata"])

    def test_build_run_start_request_from_turn_preserves_canonical_metadata(self):
        base_request = RunStartRequest(
            engine="orion",
            workspace_id="workspace-1",
            user_goal="Original goal",
            business_plan="Original business plan",
            provider="openai",
            model="gpt-test",
            agents=[{"id": "agent-1"}],
            metadata={"owner_user_id": "user-1", "trust_mode": "guarded"},
        )
        turn_request = build_run_start_turn_request(base_request)

        converted = build_run_start_request_from_turn(turn_request, base_request=base_request)

        self.assertEqual(converted.workspace_id, "workspace-1")
        self.assertEqual(converted.user_goal, "Original goal")
        self.assertEqual(converted.business_plan, "Original business plan")
        self.assertEqual(converted.provider, "openai")
        self.assertEqual(converted.agents, [{"id": "agent-1"}])
        self.assertEqual(converted.metadata["agent_turn_request"]["workspace_id"], "workspace-1")
        self.assertEqual(converted.metadata["agent_turn_request"]["message"], "Original goal")
        self.assertEqual(converted.metadata["channel"], "web")

    def test_build_run_start_request_from_turn_uses_context_hints_when_base_request_missing(self):
        turn_request = build_agent_turn_request(
            {
                "workspace_id": "workspace-2",
                "session_id": "session-2",
                "channel": "web",
                "actor": {"type": "user", "id": "user-2", "display_name": "User Two"},
                "message": "Run the workflow",
                "execution_mode": "durable",
                "response_mode": "artifact",
                "context_hints": {
                    "engine": "orion",
                    "workflow_id": "workflow-2",
                    "workflow_version_id": "wfver-2",
                    "provider": "anthropic",
                    "model": "claude-test",
                    "agent_role": "builder",
                    "business_plan": "Ship the builder changes",
                    "agents": [{"role": "builder"}],
                    "metadata": {"owner_user_id": "user-2"},
                },
            }
        )

        converted = build_run_start_request_from_turn(turn_request, base_request=None)

        self.assertEqual(converted.workflow_id, "workflow-2")
        self.assertEqual(converted.workflow_version_id, "wfver-2")
        self.assertEqual(converted.business_plan, "Ship the builder changes")
        self.assertEqual(converted.provider, "anthropic")
        self.assertEqual(converted.model, "claude-test")
        self.assertEqual(converted.agents, [{"role": "builder"}])

    def test_build_run_preview_context_preserves_workflow_metadata(self):
        request = RunStartRequest(
            engine="orion",
            workspace_id="default",
            user_goal="hello",
            workflow_id="workflow-1",
            provider="openai",
            model="gpt-test",
            credential_id="cred-1",
            agents=[{"id": "agent-1"}],
        )

        preview_context = build_run_preview_context(
            request,
            metadata={"agent_role": "builder"},
            workflow_snapshot={
                "definition": {"version": "1"},
                "name": "Inbox",
                "status": "active",
                "workflowVersionId": "wfver-1",
            },
        )

        self.assertEqual(preview_context["workflow_id"], "workflow-1")
        self.assertEqual(preview_context["workflow_version_id"], "wfver-1")
        self.assertEqual(preview_context["workflow_definition"], {"version": "1"})
        self.assertEqual(preview_context["workflow_name"], "Inbox")
        self.assertEqual(preview_context["workflow_status"], "active")
        self.assertEqual(preview_context["metadata"]["agent_role"], "builder")

    def test_build_run_routing_preview_uses_shared_route_and_precheck_flow(self):
        request = RunStartRequest(
            engine="orion",
            workspace_id="default",
            user_goal="hello",
            provider="openai",
            model="gpt-test",
        )

        with patch("server_modules.run_service.decide_execution_target", return_value={"selected": "cloud"}), patch(
            "server_modules.run_service.apply_execution_route_metadata",
            side_effect=lambda metadata, route: {**metadata, "execution_target_selected": route["selected"]},
        ):
            preview = build_run_routing_preview(
                request,
                services=RunRoutingPreviewServices(
                    prepare_run_start_request=lambda req: {
                        "engine": "orion",
                        "metadata": {"agent_role": "builder"},
                        "workflow_snapshot": {"definition": {"version": "1"}, "name": "Inbox", "status": "active"},
                    },
                    compute_tool_policy_precheck=lambda context: {
                        "blocked_count": 0,
                        "workflow_name": context.get("workflow_name"),
                        "selected_target": context["metadata"].get("execution_target_selected"),
                    },
                ),
            )

        self.assertEqual(preview["engine"], "orion")
        self.assertEqual(preview["route"]["selected"], "cloud")
        self.assertEqual(preview["metadata"]["execution_target_selected"], "cloud")
        self.assertEqual(preview["tool_policy_precheck"]["workflow_name"], "Inbox")
        self.assertEqual(preview["tool_policy_precheck"]["selected_target"], "cloud")

    def test_build_run_precheck_result_adds_doctor_preflight(self):
        request = RunStartRequest(
            engine="orion",
            workspace_id="default",
            user_goal="hello",
            provider="openai",
            model="gpt-test",
        )

        with patch("server_modules.run_service.decide_execution_target", return_value={"selected": "cloud"}), patch(
            "server_modules.run_service.apply_execution_route_metadata",
            side_effect=lambda metadata, route: {**metadata, "execution_target_selected": route["selected"]},
        ), patch(
            "server_modules.run_service.build_doctor_run_gate_live",
            new=AsyncMock(return_value={"blocking": False, "title": "ok"}),
        ):
            preview = asyncio.run(
                build_run_precheck_result(
                    request,
                    services=RunRoutingPreviewServices(
                        prepare_run_start_request=lambda req: {
                            "engine": "orion",
                            "metadata": {"agent_role": "builder"},
                            "workflow_snapshot": None,
                        },
                        compute_tool_policy_precheck=lambda context: {"blocked_count": 0},
                    ),
                )
            )

        self.assertEqual(preview["route"]["selected"], "cloud")
        self.assertFalse(preview["doctor_preflight"]["blocking"])
        self.assertEqual(preview["tool_policy_precheck"]["blocked_count"], 0)

    def test_execute_durable_turn_request_returns_run_result(self):
        run_request = RunStartRequest(
            engine="orion",
            workspace_id="default",
            user_goal="Summarize inbox state",
            provider="openai",
            model="gpt-test",
            metadata={"owner_user_id": "user-1"},
        )
        turn_request = build_run_start_turn_request(run_request)
        services = RunExecutionServices(
            stamp_request_owner=lambda req, current_user: req,
            prepare_run_start_request=lambda req: {"metadata": dict(req.metadata or {})},
            create_run_from_request=lambda req: {"run_id": "run-1", "status": "starting"},
        )

        with patch("server_modules.run_service.decide_execution_target", return_value={"selected": "cloud"}), patch(
            "server_modules.run_service.apply_execution_route_metadata",
            side_effect=lambda metadata, route: {**metadata, "execution_target_selected": route["selected"]},
        ), patch(
            "server_modules.run_service.build_doctor_run_gate_live",
            new=AsyncMock(return_value={"blocking": False, "title": "ok"}),
        ):
            execution = asyncio.run(
                execute_durable_turn_request(
                    turn_request=turn_request,
                    current_user={"user_id": "user-1"},
                    services=services,
                    base_request=run_request,
                )
            )

        self.assertEqual(execution["kind"], "durable_run")
        self.assertEqual(execution["result"]["run_id"], "run-1")
        self.assertFalse(execution["result"]["doctor_preflight"]["blocking"])

    def test_execute_durable_turn_request_creates_trace_when_missing(self):
        run_request = RunStartRequest(
            engine="orion",
            workspace_id="default",
            user_goal="Summarize inbox state",
            provider="openai",
            model="gpt-test",
            metadata={"owner_user_id": "user-1"},
        )
        turn_request = build_run_start_turn_request(run_request)
        captured = {}
        services = RunExecutionServices(
            stamp_request_owner=lambda req, current_user: req,
            prepare_run_start_request=lambda req: {"metadata": dict(req.metadata or {})},
            create_run_from_request=lambda req: (
                captured.update({"metadata": dict(req.metadata or {})}) or {"run_id": "run-1", "status": "starting"}
            ),
        )
        trace_context = TraceContext(
            trace_id="trace-durable-1",
            workspace_id="default",
            tenant_id="default",
            thread_id="thread-1",
            run_id=None,
            root_agent_id="sage",
        )

        with patch("server_modules.run_service.decide_execution_target", return_value={"selected": "cloud"}), patch(
            "server_modules.run_service.apply_execution_route_metadata",
            side_effect=lambda metadata, route: {**metadata, "execution_target_selected": route["selected"]},
        ), patch(
            "server_modules.run_service.build_doctor_run_gate_live",
            new=AsyncMock(return_value={"blocking": False, "title": "ok"}),
        ), patch(
            "server_modules.run_service.agent_trace_service.start_trace",
            new=AsyncMock(return_value=trace_context),
        ) as start_trace_mock, patch(
            "server_modules.run_service.agent_trace_service.emit_trace_started",
            new=AsyncMock(return_value="evt-start"),
        ) as emit_started_mock, patch(
            "server_modules.run_service.agent_trace_service.emit_trace_routed",
            new=AsyncMock(return_value="evt-routed"),
        ) as emit_routed_mock:
            execution = asyncio.run(
                execute_durable_turn_request(
                    turn_request=turn_request,
                    current_user={"user_id": "user-1"},
                    services=services,
                    base_request=run_request,
                    trace_context=None,
                )
            )

        self.assertEqual(execution["result"]["run_id"], "run-1")
        self.assertEqual(captured["metadata"]["trace_id"], "trace-durable-1")
        self.assertEqual(captured["metadata"]["request_trace_id"], "trace-durable-1")
        start_trace_mock.assert_awaited_once()
        emit_started_mock.assert_awaited_once()
        emit_routed_mock.assert_awaited_once()

    def test_execute_durable_turn_request_allows_unbound_local_companion_runs(self):
        run_request = RunStartRequest(
            engine="orion",
            workspace_id="default",
            user_goal="Write file demo.txt",
            metadata={
                "owner_user_id": "user-1",
                "execution_target": "local_companion",
                "outcome_pack": "local-execution-v1",
            },
        )
        turn_request = build_run_start_turn_request(run_request)
        services = RunExecutionServices(
            stamp_request_owner=lambda req, current_user: req,
            prepare_run_start_request=lambda req: {"metadata": dict(req.metadata or {})},
            create_run_from_request=lambda req: {"run_id": "run-local", "status": "starting"},
        )

        with patch(
            "server_modules.run_service.decide_execution_target",
            return_value={
                "selected": "local_companion",
                "requested": "local_companion",
                "reason": "Run is pinned to Gateway execution and no local computer is online yet.",
                "waiting_for_runtime": True,
                "waiting_for_capacity": False,
            },
        ), patch(
            "server_modules.run_service.apply_execution_route_metadata",
            side_effect=lambda metadata, route: {
                **metadata,
                "execution_target_selected": route["selected"],
                "execution_target_requested": route.get("requested"),
                "execution_target_reason": route.get("reason"),
                "execution_target_waiting_for_runtime": bool(route.get("waiting_for_runtime")),
                "execution_target_waiting_for_capacity": bool(route.get("waiting_for_capacity")),
            },
        ), patch(
            "server_modules.run_service.build_doctor_run_gate_live",
            new=AsyncMock(return_value={"blocking": False, "title": "ok"}),
        ):
            execution = asyncio.run(
                execute_durable_turn_request(
                    turn_request=turn_request,
                    current_user={"user_id": "user-1"},
                    services=services,
                    base_request=run_request,
                )
            )

        self.assertEqual(execution["kind"], "durable_run")
        self.assertEqual(execution["result"]["run_id"], "run-local")
        self.assertFalse(execution["result"]["doctor_preflight"]["blocking"])

    def test_execute_durable_turn_request_resolves_runtime_attachment_selection_for_install_runs(self):
        run_request = RunStartRequest(
            engine="orion",
            workspace_id="default",
            user_goal="Summarize inbox state",
            provider="openai",
            model="gpt-test",
            metadata={
                "owner_user_id": "user-1",
                "active_agent_install_id": "install-1",
                "runtime_mode": "local_secure",
            },
        )
        turn_request = build_run_start_turn_request(run_request)
        captured_metadata = {}

        def _create_run(req):
            captured_metadata.update(dict(req.metadata or {}))
            return {"run_id": "run-1", "status": "starting"}

        services = RunExecutionServices(
            stamp_request_owner=lambda req, current_user: req,
            prepare_run_start_request=lambda req: {"metadata": dict(req.metadata or {})},
            create_run_from_request=_create_run,
        )

        with patch(
            "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
            new=AsyncMock(
                return_value={
                    "id": "install-1",
                    "tenant_id": "default",
                    "workspace_id": "default",
                    "runtime_mode": "local_secure",
                    "runtime_profile_id": "profile-local",
                    "runtime_profile": {
                        "id": "profile-local",
                        "label": "Local Box",
                        "runtime_class": "desktop_companion",
                        "runtime_id": "runtime-local",
                        "machine_id": "machine-local",
                    },
                }
            ),
        ), patch(
            "server_modules.runtime_attachment_service.resolve_install_runtime_plan",
            new=AsyncMock(
                return_value={
                    "runtime_mode": "local_secure",
                    "deployment_mode": "hybrid",
                    "machine_target": "machine-local",
                    "execution_target_selected": "local_companion",
                    "selected_attachment": {
                        "attachment_id": "local_companion:profile-local",
                        "attachment_kind": "local_companion",
                    },
                }
            ),
        ), patch(
            "server_modules.run_service.decide_execution_target",
            return_value={"selected": "local_companion"},
        ), patch(
            "server_modules.run_service.apply_execution_route_metadata",
            side_effect=lambda metadata, route: {**metadata, "execution_target_selected": route["selected"]},
        ), patch(
            "server_modules.run_service.build_doctor_run_gate_live",
            new=AsyncMock(return_value={"blocking": False, "title": "ok"}),
        ):
            execution = asyncio.run(
                execute_durable_turn_request(
                    turn_request=turn_request,
                    current_user={"user_id": "user-1"},
                    services=services,
                    base_request=run_request,
                )
            )

        self.assertEqual(execution["result"]["run_id"], "run-1")
        self.assertEqual(captured_metadata["runtime_attachment_id"], "local_companion:profile-local")
        self.assertEqual(captured_metadata["machine_target"], "machine-local")
        self.assertEqual(captured_metadata["execution_target_selected"], "local_companion")
        self.assertEqual(captured_metadata["runtime_attachment_kind"], "local_companion")
        self.assertEqual(captured_metadata["runtime_scope"]["mode"], "local_secure")
        self.assertEqual(captured_metadata["runtime_selection"]["execution_target_selected"], "local_companion")

    def test_execute_durable_turn_request_rejects_runtime_attachment_resolution_failure(self):
        run_request = RunStartRequest(
            engine="orion",
            workspace_id="default",
            user_goal="Summarize inbox state",
            provider="openai",
            model="gpt-test",
            metadata={
                "owner_user_id": "user-1",
                "active_agent_install_id": "install-1",
            },
        )
        turn_request = build_run_start_turn_request(run_request)
        services = RunExecutionServices(
            stamp_request_owner=lambda req, current_user: req,
            prepare_run_start_request=lambda req: {"metadata": dict(req.metadata or {})},
            create_run_from_request=lambda req: {"run_id": "run-1", "status": "starting"},
        )

        with patch(
            "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
            new=AsyncMock(
                return_value={
                    "id": "install-1",
                    "tenant_id": "default",
                    "workspace_id": "default",
                    "runtime_mode": "local_secure",
                    "runtime_profile": {"runtime_class": "desktop_companion"},
                }
            ),
        ), patch(
            "server_modules.runtime_attachment_service.resolve_install_runtime_plan",
            new=AsyncMock(
                side_effect=runtime_attachment_service.RuntimeAttachmentSelectionError(
                    "No active local runtime attachment matches this specialist runtime profile."
                )
            ),
        ), patch(
            "server_modules.run_service.build_doctor_run_gate_live",
            new=AsyncMock(return_value={"blocking": False, "title": "ok"}),
        ):
            with self.assertRaises(HTTPException) as error:
                asyncio.run(
                    execute_durable_turn_request(
                        turn_request=turn_request,
                        current_user={"user_id": "user-1"},
                        services=services,
                        base_request=run_request,
                    )
                )

        self.assertEqual(error.exception.status_code, 409)
        self.assertIn("No active local runtime attachment", error.exception.detail)

    def test_execute_durable_turn_request_raises_on_doctor_block(self):
        run_request = RunStartRequest(
            engine="orion",
            workspace_id="default",
            user_goal="Summarize inbox state",
            provider="openai",
            model="gpt-test",
            metadata={"owner_user_id": "user-1"},
        )
        turn_request = build_run_start_turn_request(run_request)
        services = RunExecutionServices(
            stamp_request_owner=lambda req, current_user: req,
            prepare_run_start_request=lambda req: {"metadata": dict(req.metadata or {})},
            create_run_from_request=lambda req: {"run_id": "run-1", "status": "starting"},
        )

        with patch("server_modules.run_service.decide_execution_target", return_value={"selected": "cloud"}), patch(
            "server_modules.run_service.apply_execution_route_metadata",
            side_effect=lambda metadata, route: {**metadata, "execution_target_selected": route["selected"]},
        ), patch(
            "server_modules.run_service.build_doctor_run_gate_live",
            new=AsyncMock(return_value={"blocking": True, "detail": "blocked by doctor"}),
        ):
            with self.assertRaises(HTTPException) as error:
                asyncio.run(
                    execute_durable_turn_request(
                        turn_request=turn_request,
                        current_user={"user_id": "user-1"},
                        services=services,
                        base_request=run_request,
                    )
                )

        self.assertEqual(error.exception.status_code, 409)

    def test_create_run_result_from_request_normalizes_mapping_result(self):
        request = RunStartRequest(engine="orion", workspace_id="default", user_goal="hello")

        result = create_run_result_from_request(
            request,
            services=RunCreationServices(
                create_run_from_request=lambda req, schedule_id=None: {"run_id": "run-1", "schedule_id": schedule_id}
            ),
            schedule_id="sched-1",
        )

        self.assertEqual(result["run_id"], "run-1")
        self.assertEqual(result["schedule_id"], "sched-1")

    def test_build_schedule_bound_create_run_from_request_binds_schedule_id(self):
        create = build_schedule_bound_create_run_from_request(
            lambda req, schedule_id=None: {"run_id": "run-1", "schedule_id": schedule_id},
            schedule_id="sched-9",
        )

        result = create(object())

        self.assertEqual(result["schedule_id"], "sched-9")

    def test_create_run_from_prepared_request_returns_shared_creation_payload(self):
        request = RunStartRequest(
            engine="orion",
            workspace_id="default",
            user_goal="hello",
            provider="openai",
            model="gpt-test",
            metadata={"owner_user_id": "user-1"},
        )
        prepared = {
            "engine": "orion",
            "metadata": {"owner_user_id": "user-1", "agent_role": "orchestrator"},
            "workflow_snapshot": None,
        }

        result = create_run_from_prepared_request(
            request,
            prepared=prepared,
            services=PreparedRunCreationServices(
                decide_execution_target=lambda metadata, schedule_id=None: {"selected": "cloud"},
                apply_execution_route_metadata=lambda metadata, route: {**metadata, "execution_target_selected": route["selected"]},
                build_doctor_run_gate=lambda **kwargs: {"blocking": False},
                agent_machine_inherited_owner_user_id=lambda owner_user_id: owner_user_id,
                compute_tool_policy_precheck=lambda preview_context: {"blocked_count": 0},
                apply_browser_execution_metadata=lambda metadata: None,
                local_execution_block_prompt=lambda precheck: "blocked",
                resolve_runtime_policy_mode=lambda metadata, selected_target=None: {"policy_mode": "guarded"},
                agent_machine_full_trust_enabled=lambda owner_user_id: False,
                local_execution_requires_start_confirmation=lambda metadata, precheck: False,
                mark_local_execution_tools_approved=lambda metadata: None,
                precheck_human_action_labels=lambda precheck, decision="require_confirmation": [],
                local_execution_confirmation_prompt=lambda precheck: "confirm",
                begin_run_pending_confirmation=lambda *args, **kwargs: {"id": "approval-1"},
                create_run=lambda **kwargs: "run-1",
                load_created_run=lambda run_id: {"active_profile_id": "profile-1"},
                now_iso=lambda: "2026-04-04T00:00:00Z",
            ),
        )

        self.assertEqual(result["run_id"], "run-1")
        self.assertEqual(result["status"], "starting")
        self.assertEqual(result["route"]["selected"], "cloud")
        self.assertEqual(result["metadata"]["policy_mode"], "guarded")
        self.assertEqual(result["created_run"]["active_profile_id"], "profile-1")

    def test_create_run_from_prepared_request_stamps_hosted_runtime_scope(self):
        request = RunStartRequest(
            engine="orion",
            workspace_id="default",
            user_goal="hello",
            provider="openai",
            model="gpt-test",
            metadata={"owner_user_id": "user-1"},
        )
        prepared = {
            "engine": "orion",
            "metadata": {"owner_user_id": "user-1", "agent_role": "orchestrator"},
            "workflow_snapshot": None,
        }

        result = create_run_from_prepared_request(
            request,
            prepared=prepared,
            services=PreparedRunCreationServices(
                decide_execution_target=lambda metadata, schedule_id=None: {"selected": "cloud"},
                apply_execution_route_metadata=lambda metadata, route: {**metadata, "execution_target_selected": route["selected"]},
                build_doctor_run_gate=lambda **kwargs: {"blocking": False},
                agent_machine_inherited_owner_user_id=lambda owner_user_id: owner_user_id,
                compute_tool_policy_precheck=lambda preview_context: {"blocked_count": 0},
                apply_browser_execution_metadata=lambda metadata: None,
                local_execution_block_prompt=lambda precheck: "blocked",
                resolve_runtime_policy_mode=lambda metadata, selected_target=None: {"policy_mode": "guarded"},
                agent_machine_full_trust_enabled=lambda owner_user_id: False,
                local_execution_requires_start_confirmation=lambda metadata, precheck: False,
                mark_local_execution_tools_approved=lambda metadata: None,
                precheck_human_action_labels=lambda precheck, decision="require_confirmation": [],
                local_execution_confirmation_prompt=lambda precheck: "confirm",
                begin_run_pending_confirmation=lambda *args, **kwargs: {"id": "approval-1"},
                create_run=lambda **kwargs: "run-hosted",
            ),
        )

        self.assertEqual(result["metadata"]["runtime_mode"], "hosted_secure")
        self.assertEqual(result["metadata"]["runtime_scope"]["mode"], "hosted_secure")
        self.assertFalse(result["metadata"]["runtime_scope"]["state_layer_policy"]["cross_install_private_memory_allowed"])
        self.assertFalse(result["metadata"]["runtime_scope"]["state_layer_policy"]["specialist_to_captain_private_access"])

    def test_create_run_from_prepared_request_stamps_local_runtime_scope_from_selection(self):
        request = RunStartRequest(
            engine="orion",
            workspace_id="default",
            user_goal="hello",
            provider="openai",
            model="gpt-test",
            metadata={"owner_user_id": "user-1"},
        )
        prepared = {
            "engine": "orion",
            "metadata": {
                "owner_user_id": "user-1",
                "agent_role": "orchestrator",
                "runtime_mode": "local_secure",
                "machine_target": "machine-local",
                "runtime_selection": {
                    "execution_target_selected": "local_companion",
                    "machine_target": "machine-local",
                    "deployment_mode": "hybrid",
                    "selected_attachment": {
                        "attachment_id": "local_companion:profile-local",
                        "attachment_kind": "local_companion",
                    },
                },
            },
            "workflow_snapshot": None,
        }

        result = create_run_from_prepared_request(
            request,
            prepared=prepared,
            services=PreparedRunCreationServices(
                decide_execution_target=lambda metadata, schedule_id=None: {"selected": "local_companion"},
                apply_execution_route_metadata=lambda metadata, route: {**metadata, "execution_target_selected": route["selected"]},
                build_doctor_run_gate=lambda **kwargs: {"blocking": False},
                agent_machine_inherited_owner_user_id=lambda owner_user_id: owner_user_id,
                compute_tool_policy_precheck=lambda preview_context: {"blocked_count": 0},
                apply_browser_execution_metadata=lambda metadata: None,
                local_execution_block_prompt=lambda precheck: "blocked",
                resolve_runtime_policy_mode=lambda metadata, selected_target=None: {"policy_mode": "guarded"},
                agent_machine_full_trust_enabled=lambda owner_user_id: False,
                local_execution_requires_start_confirmation=lambda metadata, precheck: False,
                mark_local_execution_tools_approved=lambda metadata: None,
                precheck_human_action_labels=lambda precheck, decision="require_confirmation": [],
                local_execution_confirmation_prompt=lambda precheck: "confirm",
                begin_run_pending_confirmation=lambda *args, **kwargs: {"id": "approval-1"},
                create_run=lambda **kwargs: "run-local",
            ),
        )

        self.assertEqual(result["metadata"]["runtime_mode"], "local_secure")
        self.assertEqual(result["metadata"]["runtime_scope"]["mode"], "local_secure")
        self.assertEqual(result["metadata"]["runtime_attachment_id"], "local_companion:profile-local")
        self.assertEqual(result["metadata"]["machine_target"], "machine-local")
        self.assertEqual(result["metadata"]["runtime_scope"]["state_layer_policy"]["local_private_memory_access"], "allowed_locally")
        self.assertEqual(result["metadata"]["runtime_scope"]["state_layer_policy"]["artifacts_history"]["cross_install_exchange_mode"], "artifacts_only")

    def test_create_run_from_prepared_request_rejects_unresolved_runtime_binding(self):
        request = RunStartRequest(
            engine="orion",
            workspace_id="default",
            user_goal="hello",
            provider="openai",
            model="gpt-test",
            metadata={"owner_user_id": "user-1"},
        )
        prepared = {
            "engine": "orion",
            "metadata": {
                "owner_user_id": "user-1",
                "agent_role": "orchestrator",
                "machine_target": "machine-local",
            },
            "workflow_snapshot": None,
        }

        with self.assertRaises(HTTPException) as error:
            create_run_from_prepared_request(
                request,
                prepared=prepared,
                services=PreparedRunCreationServices(
                    decide_execution_target=lambda metadata, schedule_id=None: {"selected": "local_companion"},
                    apply_execution_route_metadata=lambda metadata, route: {**metadata, "execution_target_selected": route["selected"]},
                    build_doctor_run_gate=lambda **kwargs: {"blocking": False},
                    agent_machine_inherited_owner_user_id=lambda owner_user_id: owner_user_id,
                    compute_tool_policy_precheck=lambda preview_context: {"blocked_count": 0},
                    apply_browser_execution_metadata=lambda metadata: None,
                    local_execution_block_prompt=lambda precheck: "blocked",
                    resolve_runtime_policy_mode=lambda metadata, selected_target=None: {"policy_mode": "guarded"},
                    agent_machine_full_trust_enabled=lambda owner_user_id: False,
                    local_execution_requires_start_confirmation=lambda metadata, precheck: False,
                    mark_local_execution_tools_approved=lambda metadata: None,
                    precheck_human_action_labels=lambda precheck, decision="require_confirmation": [],
                    local_execution_confirmation_prompt=lambda precheck: "confirm",
                    begin_run_pending_confirmation=lambda *args, **kwargs: {"id": "approval-1"},
                    create_run=lambda **kwargs: "run-invalid",
                ),
            )

        self.assertEqual(error.exception.status_code, 409)
        self.assertIn("Runtime attachment selection is required", error.exception.detail)

    def test_create_run_result_from_prepared_request_applies_legacy_result_builder(self):
        request = RunStartRequest(engine="orion", workspace_id="default", user_goal="hello")
        prepared = {"engine": "orion", "metadata": {"agent_role": "builder"}, "workflow_snapshot": None}

        result = create_run_result_from_prepared_request(
            request,
            prepared=prepared,
            services=build_prepared_run_creation_services(
                decide_execution_target=lambda metadata, schedule_id=None: {"selected": "cloud"},
                apply_execution_route_metadata=lambda metadata, route: metadata,
                build_doctor_run_gate=lambda **kwargs: {"blocking": False},
                agent_machine_inherited_owner_user_id=lambda owner_user_id: owner_user_id,
                compute_tool_policy_precheck=lambda preview_context: {"blocked_count": 0},
                apply_browser_execution_metadata=lambda metadata: None,
                local_execution_block_prompt=lambda precheck: "blocked",
                resolve_runtime_policy_mode=lambda metadata, selected_target=None: {"policy_mode": "guarded"},
                agent_machine_full_trust_enabled=lambda owner_user_id: False,
                local_execution_requires_start_confirmation=lambda metadata, precheck: False,
                mark_local_execution_tools_approved=lambda metadata: None,
                precheck_human_action_labels=lambda precheck, decision="require_confirmation": [],
                local_execution_confirmation_prompt=lambda precheck: "confirm",
                begin_run_pending_confirmation=lambda *args, **kwargs: {"id": "approval-1"},
                create_run=lambda **kwargs: "run-2",
                load_created_run=lambda run_id: {"active_profile_id": "profile-2"},
                now_iso=lambda: "2026-04-05T00:00:00Z",
            ),
            result_services=RunPreparedResultServices(
                create_run_from_prepared_request=create_run_from_prepared_request,
                build_result=lambda req, *, created: build_runs_core_creation_result(req, created=created),
            ),
        )

        self.assertEqual(result["run_id"], "run-2")
        self.assertEqual(result["status"], "starting")
        self.assertEqual(result["route"]["selected"], "cloud")

    def test_create_legacy_run_result_from_request_delegates_shared_flow(self):
        request = RunStartRequest(engine="orion", workspace_id="default", user_goal="hello")

        result = create_legacy_run_result_from_request(
            request,
            services=LegacyRunRequestServices(
                prepare_run_start_request=lambda req: {"engine": "orion", "metadata": {}, "workflow_snapshot": None},
                build_creation_services=lambda: build_prepared_run_creation_services(
                    decide_execution_target=lambda metadata, schedule_id=None: {"selected": "cloud"},
                    apply_execution_route_metadata=lambda metadata, route: metadata,
                    build_doctor_run_gate=lambda **kwargs: {"blocking": False},
                    agent_machine_inherited_owner_user_id=lambda owner_user_id: owner_user_id,
                    compute_tool_policy_precheck=lambda preview_context: {"blocked_count": 0},
                    apply_browser_execution_metadata=lambda metadata: None,
                    local_execution_block_prompt=lambda precheck: "blocked",
                    resolve_runtime_policy_mode=lambda metadata, selected_target=None: {"policy_mode": "guarded"},
                    agent_machine_full_trust_enabled=lambda owner_user_id: False,
                    local_execution_requires_start_confirmation=lambda metadata, precheck: False,
                    mark_local_execution_tools_approved=lambda metadata: None,
                    precheck_human_action_labels=lambda precheck, decision="require_confirmation": [],
                    local_execution_confirmation_prompt=lambda precheck: "confirm",
                    begin_run_pending_confirmation=lambda *args, **kwargs: {"id": "approval-1"},
                    create_run=lambda **kwargs: "run-3",
                    now_iso=lambda: "2026-04-05T00:00:00Z",
                ),
                result_services=build_run_prepared_result_services(
                    create_run_from_prepared_request=create_run_from_prepared_request,
                    build_result=lambda req, *, created: build_runs_delegation_creation_result(created=created),
                ),
            ),
        )

        self.assertEqual(result["run_id"], "run-3")
        self.assertEqual(result["status"], "starting")
        self.assertEqual(result["route"]["selected"], "cloud")

    def test_build_legacy_run_request_services_wraps_creation_builder(self):
        services = build_legacy_run_request_services(
            prepare_run_start_request=lambda req: {"engine": "orion", "metadata": {"agent_role": "builder"}, "workflow_snapshot": None},
            callbacks=LegacyLocalExecutionCreationCallbacks(
                decide_execution_target=lambda metadata, schedule_id=None: {"selected": "cloud"},
                apply_execution_route_metadata=lambda metadata, route: metadata,
                build_doctor_run_gate=lambda **kwargs: {"blocking": False},
                agent_machine_inherited_owner_user_id=lambda owner_user_id: owner_user_id,
                compute_tool_policy_precheck=lambda preview_context: {"blocked_count": 0},
                resolve_runtime_policy_mode=lambda metadata, selected_target=None: {"policy_mode": "guarded"},
                agent_machine_full_trust_enabled=lambda owner_user_id: False,
                begin_run_pending_confirmation=lambda *args, **kwargs: {"approval_id": "approval-1"},
                create_run=lambda **kwargs: "run-1",
                local_execution_target="local_companion",
                local_execution_pack_id="local-execution-v1",
            ),
            result_services=build_runs_core_result_services(),
        )

        self.assertEqual(
            services.prepare_run_start_request(RunStartRequest()),
            {"engine": "orion", "metadata": {"agent_role": "builder"}, "workflow_snapshot": None},
        )
        built_creation = services.build_creation_services()
        self.assertIs(built_creation.mark_local_execution_tools_approved, mark_local_execution_tools_approved)

    def test_prepare_run_start_request_applies_shared_normalization_and_postprocess(self):
        request = RunStartRequest(
            engine="orion",
            workspace_id="default",
            user_goal="hello",
            workflow_id="wf-1",
            workflow_version_id="wfver-9",
            max_iterations=12,
            metadata={
                "trust_mode": "auto",
                "execution_target": "cloud",
                "delegated_by_role": "researcher",
                "app_id": "crm",
            },
        )

        prepared = prepare_run_start_request(
            request,
            services=RunPreparationServices(
                engine_registry={"orion": object()},
                engine_validation_errors=[],
                supported_outcome_packs={"local_execution"},
                normalize_requested_max_iterations=lambda value: 12,
                normalize_trust_mode=lambda value: "guarded" if value == "auto" else value,
                trust_mode_aliases={"auto": "guarded"},
                valid_trust_modes={"guarded", "strict"},
                normalize_execution_target=lambda value: str(value or "").strip().lower(),
                valid_execution_targets={"auto", "cloud", "local_companion"},
                normalize_run_id_token=lambda value: str(value or "").strip() or None,
                normalize_agent_role=lambda value: str(value or "").strip().lower(),
                detect_agent_role=lambda req, metadata: ("orchestrator", "detected"),
                resolve_app_permissions=lambda app_id: {"allow": ["read"]},
                action_policy_from_app_permissions=lambda permissions: {"allowed": permissions["allow"]},
                merge_action_policies=lambda existing, new: {"merged": [existing["action_policy"], new["action_policy"]]},
                fetch_workflow_snapshot=lambda workflow_id, **kwargs: {
                    "definition": {"version": "v1"},
                    "name": "Workflow",
                    "status": "active",
                    "workflowVersionId": kwargs.get("workflow_version_id"),
                    "versionNumber": 9,
                },
                postprocess_metadata=lambda req, metadata: {**metadata, "postprocessed": True},
            ),
        )

        self.assertEqual(prepared["engine"], "orion")
        self.assertEqual(prepared["metadata"]["trust_mode"], "guarded")
        self.assertEqual(prepared["metadata"]["max_iterations"], 12)
        self.assertEqual(prepared["metadata"]["agent_role"], "orchestrator")
        self.assertEqual(prepared["metadata"]["agent_role_source"], "detected")
        self.assertEqual(prepared["metadata"]["workflow_name"], "Workflow")
        self.assertEqual(prepared["metadata"]["workflow_version_id"], "wfver-9")
        self.assertEqual(prepared["metadata"]["workflow_version_number"], 9)
        self.assertEqual(prepared["metadata"]["postprocessed"], True)

    def test_prepare_run_start_request_rejects_implicit_app_memory_access(self):
        request = RunStartRequest(
            engine="orion",
            workspace_id="default",
            user_goal="hello",
            metadata={
                "app_id": "study",
                "captain_context": {"raw": True},
            },
        )

        with patch(
            "server_modules.run_service.app_bridge_service._resolve_registry_app_item",
            return_value={"id": "study", "status": "installed", "permissions": ["files.read"]},
        ):
            with self.assertRaises(HTTPException) as ctx:
                prepare_run_start_request(
                    request,
                    services=RunPreparationServices(
                        engine_registry={"orion": object()},
                        engine_validation_errors=[],
                        supported_outcome_packs={"local_execution"},
                        normalize_requested_max_iterations=lambda value: None,
                        normalize_trust_mode=lambda value: str(value or ""),
                        trust_mode_aliases={},
                        valid_trust_modes={"guarded", "strict"},
                        normalize_execution_target=lambda value: str(value or "").strip().lower(),
                        valid_execution_targets={"auto", "cloud", "local_companion"},
                        normalize_run_id_token=lambda value: str(value or "").strip() or None,
                        normalize_agent_role=lambda value: str(value or "").strip().lower(),
                        detect_agent_role=lambda req, metadata: ("orchestrator", "detected"),
                        resolve_app_permissions=lambda app_id: {"allow": ["read"]},
                        action_policy_from_app_permissions=lambda permissions: {"allowed": permissions["allow"]},
                        merge_action_policies=lambda existing, new: {"merged": [existing["action_policy"], new["action_policy"]]},
                        fetch_workflow_snapshot=lambda workflow_id, **kwargs: None,
                    ),
                )

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("captain_context", str(ctx.exception.detail))

    def test_prepare_run_start_request_normalizes_explicit_app_bridge(self):
        request = RunStartRequest(
            engine="orion",
            workspace_id="default",
            user_goal="hello",
            metadata={
                "app_id": "study",
                "app_context": {
                    "user_selected_inputs": {"topic": "biology"},
                    "app_history": [{"id": "h-1"}],
                },
                "app_bridge": {
                    "kind": "app_to_specialist",
                    "type": "status_request",
                    "target": {"target_install_id": "install-1"},
                },
            },
        )

        with patch(
            "server_modules.run_service.app_bridge_service._resolve_registry_app_item",
            return_value={"id": "study", "status": "installed", "permissions": ["files.read"]},
        ):
            prepared = prepare_run_start_request(
                request,
                services=RunPreparationServices(
                    engine_registry={"orion": object()},
                    engine_validation_errors=[],
                    supported_outcome_packs={"local_execution"},
                    normalize_requested_max_iterations=lambda value: None,
                    normalize_trust_mode=lambda value: str(value or ""),
                    trust_mode_aliases={},
                    valid_trust_modes={"guarded", "strict"},
                    normalize_execution_target=lambda value: str(value or "").strip().lower(),
                    valid_execution_targets={"auto", "cloud", "local_companion"},
                    normalize_run_id_token=lambda value: str(value or "").strip() or None,
                    normalize_agent_role=lambda value: str(value or "").strip().lower(),
                    detect_agent_role=lambda req, metadata: ("orchestrator", "detected"),
                    resolve_app_permissions=lambda app_id: {"allow": ["read"]},
                    action_policy_from_app_permissions=lambda permissions: {"allowed": permissions["allow"]},
                    merge_action_policies=lambda existing, new: {"merged": [existing["action_policy"], new["action_policy"]]},
                    fetch_workflow_snapshot=lambda workflow_id, **kwargs: None,
                ),
            )

        self.assertEqual(prepared["metadata"]["app_bridge"]["bridge_kind"], "app_to_specialist")
        self.assertEqual(prepared["metadata"]["app_bridge"]["bridge_type"], "status_request")
        self.assertEqual(
            prepared["metadata"]["app_bridge"]["target"]["target_install_id"],
            "install-1",
        )
        self.assertEqual(
            prepared["metadata"]["app_context_envelope"]["classes"],
            ["app_owned_history", "user_selected_inputs"],
        )
        self.assertTrue(prepared["metadata"]["app_runtime_contract"]["has_explicit_bridge"])

    def test_build_runs_core_creation_result_shapes_legacy_core_payload(self):
        request = RunStartRequest(
            engine="orion",
            workspace_id="default",
            user_goal="hello",
            provider="openai",
            model="gpt-test",
        )

        payload = build_runs_core_creation_result(
            request,
            created={
                "run_id": "run-1",
                "engine": "orion",
                "status": "starting",
                "route": {"selected": "cloud"},
                "doctor_preflight": {"blocking": False},
                "pending_confirmation": None,
                "created_run": {
                    "active_profile_id": "profile-1",
                    "active_profile_label": "Primary",
                    "active_provider": "openai",
                    "active_model": "gpt-test",
                },
                "metadata": {
                    "policy_mode": "guarded",
                    "agent_role": "orchestrator",
                    "agent_role_source": "detected",
                },
            },
        )

        self.assertEqual(payload["active_profile_id"], "profile-1")
        self.assertEqual(payload["requested_provider"], "openai")
        self.assertEqual(payload["requested_model"], "gpt-test")
        self.assertIsNone(payload["pending_approval"])

    def test_build_runs_delegation_creation_result_shapes_legacy_delegation_payload(self):
        payload = build_runs_delegation_creation_result(
            created={
                "run_id": "run-1",
                "engine": "orion",
                "status": "starting",
                "route": {"selected": "cloud"},
                "doctor_preflight": {"blocking": False},
                "pending_confirmation": {"approval_id": "approval-1"},
                "metadata": {
                    "agent_role": "researcher",
                    "agent_role_source": "delegated",
                },
            },
        )

        self.assertEqual(payload["agent_role"], "researcher")
        self.assertEqual(payload["pending_confirmation"]["approval_id"], "approval-1")
        self.assertEqual(payload["pending_approval"]["approval_id"], "approval-1")

    def test_local_execution_helper_block(self):
        precheck = {
            "require_confirmation_count": 1,
            "approval_required_count": 0,
            "require_confirmation": ["computer__click"],
            "allowed": ["file__read"],
            "items": [
                {
                    "tool_id": "computer__click",
                    "execution_decision": "require_confirmation",
                    "capabilities": [{"title": "Computer Click"}],
                }
            ],
            "browser_automation_policy": {
                "session_profiles": ["profile-1"],
                "immutable_plan_hash": "hash-1",
                "reviewed_approval_required": True,
            },
        }
        metadata = {
            "execution_target_selected": "local_companion",
            "outcome_pack": "local_execution",
            "tool_policy_precheck": precheck,
        }

        self.assertTrue(
            local_execution_requires_start_confirmation(
                metadata,
                precheck,
                local_execution_target="local_companion",
                local_execution_pack_id="local_execution",
            )
        )
        self.assertEqual(precheck_human_action_labels(precheck), ["Computer Click"])
        self.assertIn("Confirmation required", local_execution_confirmation_prompt(precheck))
        precheck["items"][0]["execution_decision"] = "deny"
        precheck["items"][0]["decision"] = "deny"
        self.assertIn("Run blocked", local_execution_block_prompt(precheck))
        precheck["items"][0]["execution_decision"] = "require_confirmation"
        precheck["items"][0]["decision"] = "require_confirmation"
        mark_local_execution_tools_approved(metadata)
        self.assertEqual(metadata["tool_policy_precheck"]["require_confirmation_count"], 0)
        self.assertIn("computer__click", metadata["tool_policy_precheck"]["allowed"])
        apply_browser_execution_metadata(metadata)
        self.assertEqual(metadata["browser_session_profile"], "profile-1")
        self.assertEqual(metadata["browser_immutable_plan_hash"], "hash-1")
        self.assertTrue(metadata["browser_reviewed_approval_required"])

    def test_build_delegated_child_run_request_does_not_inherit_parent_execution_metadata(self):
        request = build_delegated_child_run_request(
            {
                "run_id": "parent-run",
                "engine": "orion",
                "agent_role": "builder",
                "context": {
                    "workflow_id": "workflow-1",
                    "workspace_id": "default",
                    "business_plan": "Ship the fix",
                    "max_iterations": 7,
                    "provider": "openai",
                    "model": "gpt-test",
                    "credential_id": "cred-1",
                    "metadata": {
                        "workspace_agent_install_id": "install-parent-workspace",
                        "active_agent_install_id": "install-1",
                        "master_agent_install_id": "install-master",
                        "runtime_mode": "local_secure",
                        "runtime_scope": {"mode": "local_secure", "driver": "desktop_companion"},
                        "runtime_selection": {
                            "execution_target_selected": "local_companion",
                            "machine_target": "machine-local",
                            "deployment_mode": "hybrid",
                            "selected_attachment": {
                                "attachment_id": "local_companion:profile-local",
                                "attachment_kind": "local_companion",
                            },
                        },
                        "runtime_attachment_id": "local_companion:profile-local",
                        "runtime_attachment_kind": "local_companion",
                        "machine_target": "machine-local",
                        "root_folder_uri": "/workspace/parent",
                        "folder_grants": ["/workspace/parent", "/workspace/shared"],
                        "file_mount_grants": [{"mount": "parent", "mode": "write"}],
                        "execution_target_selected": "local_companion",
                        "trust_mode": "guarded",
                        "owner_user_id": "user-1",
                        "owner_email": "user@example.com",
                    },
                },
            },
            {
                "user_goal": "Handle browser work",
                "agent_role": "research",
                "metadata": {},
            },
            normalize_run_id_token=lambda value: str(value or "").strip() or None,
            normalize_agent_role=lambda value: str(value or "").strip().lower(),
            normalize_requested_max_iterations=lambda value: int(value) if value is not None else None,
            valid_execution_targets={"local_companion", "cloud", "auto"},
            note="Retry after failure.",
        )

        self.assertEqual(request.parent_run_id, "parent-run")
        self.assertIsNone(request.workflow_id)
        self.assertEqual(request.metadata["delegation_root_run_id"], "parent-run")
        self.assertEqual(request.metadata["delegated_by_role"], "builder")
        self.assertEqual(request.metadata["delegation_note"], "Retry after failure.")
        self.assertNotIn("active_agent_install_id", request.metadata)
        self.assertNotIn("workspace_agent_install_id", request.metadata)
        self.assertNotIn("runtime_mode", request.metadata)
        self.assertNotIn("runtime_scope", request.metadata)
        self.assertNotIn("runtime_attachment_id", request.metadata)
        self.assertNotIn("runtime_attachment_kind", request.metadata)
        self.assertNotIn("machine_target", request.metadata)
        self.assertNotIn("root_folder_uri", request.metadata)
        self.assertNotIn("folder_grants", request.metadata)
        self.assertNotIn("file_mount_grants", request.metadata)
        self.assertEqual(request.metadata["master_agent_install_id"], "install-master")
        self.assertEqual(request.metadata["owner_user_id"], "user-1")
        self.assertEqual(request.metadata["user_id"], "user-1")
        self.assertIsNone(request.provider)
        self.assertIsNone(request.model)
        self.assertIsNone(request.credential_id)
        self.assertIsNone(request.max_iterations)

    def test_build_delegated_child_run_request_keeps_explicit_child_runtime_and_file_grants(self):
        request = build_delegated_child_run_request(
            {
                "run_id": "parent-run",
                "engine": "orion",
                "agent_role": "builder",
                "context": {
                    "workspace_id": "default",
                    "metadata": {
                        "workspace_agent_install_id": "install-parent-workspace",
                        "active_agent_install_id": "install-parent-active",
                        "master_agent_install_id": "install-master",
                        "runtime_attachment_id": "local_companion:parent",
                        "machine_target": "machine-parent",
                        "root_folder_uri": "/workspace/parent",
                        "folder_grants": ["/workspace/parent"],
                        "file_mount_grants": [{"mount": "parent", "mode": "write"}],
                    },
                },
            },
            {
                "user_goal": "Handle browser work",
                "agent_role": "research",
                "metadata": {
                    "runtime_mode": "local_secure",
                    "runtime_attachment_id": "local_companion:child",
                    "machine_target": "machine-child",
                    "root_folder_uri": "/workspace/child",
                    "folder_grants": ["/workspace/child"],
                    "file_mount_grants": [{"mount": "child", "mode": "read"}],
                    "active_agent_install_id": "install-unsafe-explicit",
                    "workspace_agent_install_id": "install-unsafe-explicit",
                },
            },
            normalize_run_id_token=lambda value: str(value or "").strip() or None,
            normalize_agent_role=lambda value: str(value or "").strip().lower(),
            normalize_requested_max_iterations=lambda value: int(value) if value is not None else None,
            valid_execution_targets={"local_companion", "cloud", "auto"},
            note=None,
        )

        self.assertEqual(request.metadata["runtime_mode"], "local_secure")
        self.assertEqual(request.metadata["runtime_attachment_id"], "local_companion:child")
        self.assertEqual(request.metadata["machine_target"], "machine-child")
        self.assertEqual(request.metadata["root_folder_uri"], "/workspace/child")
        self.assertEqual(request.metadata["folder_grants"], ["/workspace/child"])
        self.assertEqual(request.metadata["file_mount_grants"], [{"mount": "child", "mode": "read"}])
        self.assertEqual(request.metadata["master_agent_install_id"], "install-master")
        self.assertNotIn("active_agent_install_id", request.metadata)
        self.assertNotIn("workspace_agent_install_id", request.metadata)

    def test_timeout_stale_delegated_child_runs_marks_failed_child(self):
        child_logs = queue.Queue()
        runs_by_id = {
            "child-run": {
                "run_id": "child-run",
                "status": "running_local",
                "local_last_progress_at": "2026-04-01T23:50:00Z",
                "logs": child_logs,
            }
        }
        log_calls = []
        status_calls = []

        timed_out = timeout_stale_delegated_child_runs(
            "parent-run",
            [dict(runs_by_id["child-run"])],
            runs_by_id=runs_by_id,
            terminal_run_statuses={"completed", "failed", "timeout"},
            stale_child_run_timeout_seconds=300,
            normalize_run_id_token=lambda value: str(value or "").strip() or None,
            parse_utc_ts=lambda value: datetime.fromisoformat(str(value).replace("Z", "+00:00")) if value else None,
            utc_now=lambda: datetime(2026, 4, 2, 0, 10, 30, tzinfo=timezone.utc),
            set_run_status=lambda run_id, status: status_calls.append((run_id, status)),
            emit_log=lambda *args, **kwargs: log_calls.append((args, kwargs)),
        )

        self.assertEqual(timed_out, ["child-run"])
        self.assertEqual(runs_by_id["child-run"]["result_data"]["error"], "delegated_child_timeout")
        self.assertEqual(status_calls, [("child-run", "failed")])
        self.assertEqual(log_calls[0][1]["event"], "delegated_child_timeout")

    def test_build_retry_child_payload_increments_retry_metadata(self):
        payload = build_retry_child_payload(
            {"run_id": "parent-run"},
            {
                "run_id": "child-run",
                "agent_role": "research",
                "context": {
                    "user_goal": "Analyze the docs",
                    "business_plan": "Ship the release",
                    "max_iterations": 4,
                    "metadata": {
                        "retry_sequence": 1,
                        "retry_count": 1,
                    },
                },
            },
            normalize_run_id_token=lambda value: str(value or "").strip() or None,
            normalize_agent_role=lambda value: str(value or "").strip().lower(),
            normalize_requested_max_iterations=lambda value: int(value) if value is not None else None,
            child_retry_count=lambda child: int(child.get("context", {}).get("metadata", {}).get("retry_count") or 0),
            note="Automatic retry after delegated child failure.",
        )

        self.assertEqual(payload["agent_role"], "research")
        self.assertEqual(payload["user_goal"], "Analyze the docs")
        self.assertEqual(payload["business_plan"], "Ship the release")
        self.assertEqual(payload["max_iterations"], 4)
        self.assertEqual(payload["metadata"]["retry_of_run_id"], "child-run")
        self.assertEqual(payload["metadata"]["retry_sequence"], 2)
        self.assertEqual(payload["metadata"]["retry_count"], 2)
        self.assertEqual(
            payload["metadata"]["delegation_retry_note"],
            "Automatic retry after delegated child failure.",
        )

    def test_schedule_auto_retry_for_failed_children_dispatches_retry_job(self):
        class _ImmediateTimer:
            def __init__(self, _delay, func, args=None, kwargs=None):
                self._func = func
                self._args = args or ()
                self._kwargs = kwargs or {}
                self.daemon = False

            def start(self):
                self._func(*self._args, **self._kwargs)

        log_queue = queue.Queue()
        runs_by_id = {
            "parent-run": {"logs": log_queue},
        }
        auto_retry_pending = set()
        auto_retry_attempts = {}
        created_requests = []
        refresh_calls = []

        scheduled = schedule_auto_retry_for_failed_children(
            {"run_id": "parent-run"},
            [
                {
                    "run_id": "child-run",
                    "status": "failed",
                    "created_at": "2026-04-02T00:00:00Z",
                    "updated_at": "2026-04-02T00:00:00Z",
                    "context": {"metadata": {}},
                }
            ],
            runs_by_id=runs_by_id,
            parse_utc_ts=lambda value: datetime.fromisoformat(str(value).replace("Z", "+00:00")) if value else None,
            child_lineage_key=lambda child: "lineage-1",
            failure_status=lambda status: str(status or "").strip().lower() == "failed",
            child_retry_count=lambda child: 0,
            safe_int=lambda value, default=0: int(value) if value is not None else default,
            auto_retry_pending=auto_retry_pending,
            auto_retry_attempts=auto_retry_attempts,
            auto_retry_pending_lock=__import__("threading").Lock(),
            auto_retry_max_retries=1,
            auto_retry_delay_seconds=0.0,
            emit_log=lambda *args, **kwargs: None,
            build_retry_child_payload_fn=lambda *args, **kwargs: {"metadata": {}},
            build_delegated_child_run_request_fn=lambda *args, **kwargs: RunStartRequest(
                engine="orion",
                workspace_id="default",
                user_goal="retry",
                metadata={"auto_retry": True},
            ),
            execute_delegated_run_request_fn=lambda req: created_requests.append(req) or {"status": "starting"},
            lookup_run_snapshot_fn=lambda run_id: {"run_id": run_id},
            find_run_relationships_fn=lambda run_id, snapshot: (
                snapshot,
                [
                    {
                        "run_id": "child-run",
                        "status": "failed",
                        "created_at": "2026-04-02T00:00:00Z",
                        "updated_at": "2026-04-02T00:00:00Z",
                        "context": {"metadata": {}},
                    }
                ],
            ),
            refresh_parent_delegation_state_fn=lambda parent_id, triggering_run_id=None: refresh_calls.append((parent_id, triggering_run_id)),
            timer_factory=_ImmediateTimer,
            triggering_run_id="child-run",
        )

        self.assertEqual(scheduled, {"lineage-1"})
        self.assertEqual(len(created_requests), 1)
        self.assertEqual(refresh_calls, [("parent-run", "child-run")])
        self.assertEqual(auto_retry_pending, set())
        self.assertEqual(auto_retry_attempts[("parent-run", "lineage-1")], 1)

    def test_schedule_auto_retry_for_failed_children_rehydrates_persisted_retry_after_restart(self):
        recorded_timers = []
        persisted_states = []
        runs_by_id = {
            "parent-run": {
                "run_id": "parent-run",
                "context": {
                    "metadata": {
                        "delegation_pending_retries": {
                            "lineage-1": {
                                "failed_child_run_id": "child-run",
                                "attempt": 1,
                                "next_retry_at": "2026-04-02T00:00:05Z",
                            }
                        }
                    }
                },
                "logs": queue.Queue(),
            },
        }

        class _RecordingTimer:
            def __init__(self, delay, func, args=None, kwargs=None):
                recorded_timers.append((delay, func, args or (), kwargs or {}))
                self.daemon = False

            def start(self):
                return None

        scheduled = schedule_auto_retry_for_failed_children(
            {"run_id": "parent-run"},
            [
                {
                    "run_id": "child-run",
                    "status": "failed",
                    "created_at": "2026-04-02T00:00:00Z",
                    "updated_at": "2026-04-02T00:00:00Z",
                    "context": {"metadata": {}},
                }
            ],
            runs_by_id=runs_by_id,
            parse_utc_ts=lambda value: datetime.fromisoformat(str(value).replace("Z", "+00:00")) if value else None,
            child_lineage_key=lambda child: "lineage-1",
            failure_status=lambda status: str(status or "").strip().lower() == "failed",
            child_retry_count=lambda child: 0,
            safe_int=lambda value, default=0: int(value) if value is not None else default,
            auto_retry_pending=set(),
            auto_retry_attempts={},
            auto_retry_pending_lock=__import__("threading").Lock(),
            auto_retry_max_retries=1,
            auto_retry_delay_seconds=3.0,
            emit_log=lambda *args, **kwargs: None,
            build_retry_child_payload_fn=lambda *args, **kwargs: {"metadata": {}},
            build_delegated_child_run_request_fn=lambda *args, **kwargs: RunStartRequest(
                engine="orion",
                workspace_id="default",
                user_goal="retry",
                metadata={"auto_retry": True},
            ),
            execute_delegated_run_request_fn=lambda req: {"status": "starting"},
            lookup_run_snapshot_fn=lambda run_id: {"run_id": run_id},
            find_run_relationships_fn=lambda run_id, snapshot: (snapshot, []),
            refresh_parent_delegation_state_fn=lambda parent_id, triggering_run_id=None: None,
            timer_factory=_RecordingTimer,
            persist_live_run_state_fn=lambda run_id, run: persisted_states.append((run_id, dict(run.get("context", {}).get("metadata", {})))),
            triggering_run_id="child-run",
        )

        self.assertEqual(scheduled, {"lineage-1"})
        self.assertEqual(len(recorded_timers), 1)
        self.assertGreaterEqual(recorded_timers[0][0], 0.0)
        self.assertEqual(persisted_states, [])

    def test_build_delegation_summary_reports_retrying_and_failed_children(self):
        summary = build_delegation_summary(
            {"run_id": "parent-run"},
            [
                {
                    "run_id": "child-a",
                    "status": "failed",
                    "agent_role": "research",
                    "updated_at": "2026-04-02T00:00:00Z",
                    "created_at": "2026-04-02T00:00:00Z",
                },
                {
                    "run_id": "child-b",
                    "status": "completed",
                    "agent_role": "builder",
                    "result_summary": "Finished implementation",
                    "updated_at": "2026-04-02T00:01:00Z",
                    "created_at": "2026-04-02T00:01:00Z",
                },
            ],
            normalize_run_id_token=lambda value: str(value or "").strip() or None,
            normalize_agent_role=lambda value: str(value or "").strip().lower(),
            parse_utc_ts=lambda value: datetime.fromisoformat(str(value).replace("Z", "+00:00")) if value else None,
            terminal_run_statuses={"completed", "failed", "timeout"},
            agent_workspace_labels={"research": "Research", "builder": "Builder"},
            child_lineage_key=lambda child: str(child.get("run_id") or ""),
            failure_status=lambda status: str(status or "").strip().lower() == "failed",
            parent_pending_retry_lineages=lambda parent_run_id: {"child-a"},
        )

        self.assertEqual(summary["next_action"], "waiting_for_children")
        self.assertEqual(summary["active_children"], 1)
        self.assertEqual(summary["completed_children"], 1)
        self.assertEqual(summary["failed_children"], 0)
        self.assertIn("research", summary["child_roles"])
        self.assertIn("builder", summary["child_roles"])
        self.assertIn("retrying", summary["summary_text"])

    def test_iter_known_run_snapshots_merges_live_and_archived_without_duplicates(self):
        snapshots = iter_known_run_snapshots(
            runs_by_id={
                "live-run": {"status": "running"},
                "shared-run": {"status": "completed"},
                "bad-run": "ignore-me",
            },
            run_history=[
                {"run_id": "archived-run", "status": "completed"},
                {"run_id": "shared-run", "status": "failed"},
                "ignore-me",
            ],
            serialize_run_snapshot_fn=lambda run_id, run: {"run_id": run_id, "status": run.get("status")},
        )

        self.assertEqual(
            [item["run_id"] for item in snapshots],
            ["live-run", "shared-run", "archived-run"],
        )

    def test_build_run_relation_summary_maps_agent_label(self):
        summary = build_run_relation_summary(
            {
                "run_id": "child-run",
                "agent_role": "research",
                "status": "completed",
                "result_summary": "done",
            },
            agent_workspace_labels={"research": "Research"},
        )

        self.assertEqual(summary["run_id"], "child-run")
        self.assertEqual(summary["agent_label"], "Research")
        self.assertEqual(summary["result_summary"], "done")

    def test_find_run_relationships_returns_parent_and_sorted_children(self):
        snapshot = {
            "run_id": "parent-run",
            "context": {"metadata": {"parent_run_id": "root-run"}},
        }

        parent, children = find_run_relationships(
            "parent-run",
            snapshot,
            extract_run_relationships_from_context_fn=lambda context: {
                "parent_run_id": context.get("metadata", {}).get("parent_run_id")
            },
            iter_known_run_snapshots_fn=lambda: [
                {
                    "run_id": "root-run",
                    "status": "completed",
                    "agent_role": "orchestrator",
                    "updated_at": "2026-04-01T00:00:00Z",
                },
                {
                    "run_id": "child-older",
                    "status": "completed",
                    "agent_role": "research",
                    "parent_run_id": "parent-run",
                    "updated_at": "2026-04-01T00:00:00Z",
                    "created_at": "2026-04-01T00:00:00Z",
                },
                {
                    "run_id": "child-newer",
                    "status": "running",
                    "agent_role": "builder",
                    "parent_run_id": "parent-run",
                    "updated_at": "2026-04-02T00:00:00Z",
                    "created_at": "2026-04-02T00:00:00Z",
                },
            ],
            normalize_run_id_token=lambda value: str(value or "").strip() or None,
            build_run_relation_summary_fn=lambda candidate: {
                "run_id": candidate.get("run_id"),
                "status": candidate.get("status"),
                "updated_at": candidate.get("updated_at"),
                "created_at": candidate.get("created_at"),
            },
            parse_utc_ts=lambda value: datetime.fromisoformat(str(value).replace("Z", "+00:00")) if value else None,
        )

        self.assertEqual(parent["run_id"], "root-run")
        self.assertEqual([item["run_id"] for item in children], ["child-newer", "child-older"])

    def test_llm_auto_delegate_role_returns_normalized_route(self):
        route = llm_auto_delegate_role(
            objective="Prepare a spreadsheet summary",
            business_plan="Review revenue outliers",
            parent_snapshot={"workspace_id": "default"},
            routing_role_rules=AUTO_DELEGATION_ROLE_RULES,
            normalize_agent_role=lambda value: str(value or "").strip().lower(),
            llm_task_fn=lambda *args, **kwargs: {"agent": "finance", "reason": "Spreadsheet work fits finance."},
            routing_context={"provider": "openai", "model": "gpt-test"},
        )

        self.assertEqual(route, {"agent_role": "finance", "reason": "Spreadsheet work fits finance."})

    def test_resolve_fastest_routing_context_returns_first_available_provider(self):
        context = resolve_fastest_routing_context(
            routing_provider_order=ROUTING_PROVIDER_ORDER,
            provider_has_key_fn=lambda provider: provider in {"anthropic", "gemini"},
            resolve_requested_model_fn=lambda request, metadata, provider: f"{provider}-model",
        )

        self.assertEqual(context, {"provider": "anthropic", "model": "anthropic-model"})

    def test_resolve_fastest_routing_context_includes_deepseek(self):
        context = resolve_fastest_routing_context(
            routing_provider_order=ROUTING_PROVIDER_ORDER,
            provider_has_key_fn=lambda provider: provider in {"deepseek"},
            resolve_requested_model_fn=lambda request, metadata, provider: f"{provider}-model",
        )

        self.assertEqual(context, {"provider": "deepseek", "model": "deepseek-model"})

    def test_resolve_fastest_routing_context_returns_none_without_available_provider(self):
        context = resolve_fastest_routing_context(
            routing_provider_order=ROUTING_PROVIDER_ORDER,
            provider_has_key_fn=lambda provider: False,
            resolve_requested_model_fn=lambda request, metadata, provider: f"{provider}-model",
        )

        self.assertIsNone(context)

    def test_execute_delegated_run_request_builds_services_through_canonical_helper(self):
        prepare = object()
        create = object()
        captured = {}

        result = execute_delegated_run_request(
            RunStartRequest(engine="orion", workspace_id="default", user_goal="delegate"),
            namespace={
                "_prepare_run_start_request": prepare,
                "_create_run_from_request": create,
            },
            execute_system_run_start_request_via_turn_runtime_fn="turn-runtime",
            execute_built_unowned_system_run_start_request_via_turn_runtime_fn=lambda request, **kwargs: captured.update(
                {
                    "request": request,
                    "execute_system": kwargs["execute_system_run_start_request_via_turn_runtime_fn"],
                    "services": kwargs["build_run_execution_services_fn"](),
                }
            )
            or {"status": "starting"},
        )

        self.assertEqual(result["status"], "starting")
        self.assertEqual(captured["execute_system"], "turn-runtime")
        self.assertIs(captured["services"].prepare_run_start_request, prepare)
        self.assertIs(captured["services"].create_run_from_request, create)

    def test_execute_run_start_request_via_turn_runtime_returns_result_payload(self):
        request = RunStartRequest(engine="orion", workspace_id="default", user_goal="hello")
        services = RunExecutionServices(
            stamp_request_owner=lambda req, current_user: req,
            prepare_run_start_request=lambda req: {"metadata": dict(req.metadata or {})},
            create_run_from_request=lambda req: {"run_id": "unused"},
        )

        result = __import__("asyncio").run(
            execute_run_start_request_via_turn_runtime(
                request,
                current_user={"user_id": "user-1"},
                stamp_request_owner_fn=lambda req, current_user: req,
                services=services,
                resolve_run_start_turn_request_fn=lambda **kwargs: type(
                    "Resolution",
                    (),
                    {"turn_request": "turn-1", "request": request},
                )(),
                execute_durable_turn_request_fn=AsyncMock(
                    return_value={"result": {"run_id": "run-1", "status": "starting"}}
                ),
            )
        )

        self.assertEqual(result["run_id"], "run-1")
        self.assertEqual(result["status"], "starting")

    def test_execute_durable_agent_turn_dispatch_returns_none_for_non_durable_mode(self):
        turn_request = type("TurnRequest", (), {"execution_mode": "direct_chat"})()

        result = __import__("asyncio").run(
            execute_durable_agent_turn_dispatch(
                turn_request=turn_request,
                current_user={"user_id": "user-1"},
                services=RunExecutionServices(
                    stamp_request_owner=lambda req, current_user: req,
                    prepare_run_start_request=lambda req: {"metadata": {}},
                    create_run_from_request=lambda req: {"run_id": "unused"},
                ),
            )
        )

        self.assertIsNone(result)

    def test_execute_durable_agent_turn_dispatch_runs_durable_execution_for_durable_mode(self):
        turn_request = type("TurnRequest", (), {"execution_mode": "durable"})()

        result = __import__("asyncio").run(
            execute_durable_agent_turn_dispatch(
                turn_request=turn_request,
                current_user={"user_id": "user-1"},
                services=RunExecutionServices(
                    stamp_request_owner=lambda req, current_user: req,
                    prepare_run_start_request=lambda req: {"metadata": {}},
                    create_run_from_request=lambda req: {"run_id": "unused"},
                ),
                base_request={"run": True},
                execute_durable_turn_request_fn=AsyncMock(return_value={"result": {"run_id": "run-durable"}}),
            )
        )

        self.assertEqual(result, {"result": {"run_id": "run-durable"}})

    def test_execute_system_run_start_request_via_turn_runtime_uses_system_user(self):
        request = RunStartRequest(engine="orion", workspace_id="default", user_goal="hello")
        captured = {}

        async def _fake_execute(request_payload, *, current_user, **kwargs):
            captured["current_user"] = dict(current_user or {})
            return {"run_id": "run-2", "status": "starting"}

        result = execute_system_run_start_request_via_turn_runtime(
            request,
            stamp_request_owner_fn=lambda req, current_user: req,
            services=RunExecutionServices(
                stamp_request_owner=lambda req, current_user: req,
                prepare_run_start_request=lambda req: {"metadata": dict(req.metadata or {})},
                create_run_from_request=lambda req: {"run_id": "unused"},
            ),
            execute_run_start_request_via_turn_runtime_fn=_fake_execute,
        )

        self.assertEqual(result["run_id"], "run-2")
        self.assertEqual(captured["current_user"]["auth_type"], "api_key")

    def test_execute_unowned_system_run_start_request_via_turn_runtime_uses_noop_owner_stamp(self):
        request = RunStartRequest(engine="orion", workspace_id="default", user_goal="hello")
        captured = {}
        original = object()

        def _fake_system_execute(request_payload, *, stamp_request_owner_fn, current_user=None, **kwargs):
            captured["stamped"] = stamp_request_owner_fn(original, {"user_id": "ignored"})
            captured["current_user"] = dict(current_user or {})
            return {"run_id": "run-3", "status": "starting"}

        result = execute_unowned_system_run_start_request_via_turn_runtime(
            request,
            services=RunExecutionServices(
                stamp_request_owner=lambda req, current_user: req,
                prepare_run_start_request=lambda req: {"metadata": dict(req.metadata or {})},
                create_run_from_request=lambda req: {"run_id": "unused"},
            ),
            execute_system_run_start_request_via_turn_runtime_fn=_fake_system_execute,
        )

        self.assertEqual(result["run_id"], "run-3")
        self.assertIs(captured["stamped"], original)

    def test_execute_built_unowned_system_run_start_request_via_turn_runtime_builds_services(self):
        request = RunStartRequest(engine="orion", workspace_id="default", user_goal="hello")
        built_services = RunExecutionServices(
            stamp_request_owner=lambda req, current_user: req,
            prepare_run_start_request=lambda req: {"metadata": {}},
            create_run_from_request=lambda req: {"run_id": "unused"},
        )
        captured = {}

        def _fake_execute(request_payload, *, stamp_request_owner_fn, services, **kwargs):
            captured["stamped"] = stamp_request_owner_fn("req", {"user_id": "ignored"})
            captured["services"] = services
            return {"run_id": "run-4", "status": "starting"}

        result = execute_built_unowned_system_run_start_request_via_turn_runtime(
            request,
            execute_system_run_start_request_via_turn_runtime_fn=_fake_execute,
            build_run_execution_services_fn=lambda: built_services,
        )

        self.assertEqual(result["run_id"], "run-4")
        self.assertEqual(captured["stamped"], "req")
        self.assertIs(captured["services"], built_services)

    def test_execute_built_legacy_unowned_system_run_start_request_via_turn_runtime_uses_runtime_even_with_mock(self):
        request = RunStartRequest(engine="orion", workspace_id="default", user_goal="hello")
        create_run = unittest.mock.Mock(return_value={"run_id": "run-5", "status": "starting"})
        built_services = RunExecutionServices(
            stamp_request_owner=lambda req, current_user: req,
            prepare_run_start_request=lambda req: {"metadata": {}},
            create_run_from_request=lambda req: {"run_id": "unused"},
        )

        with patch(
            "server_modules.run_service.execute_built_unowned_system_run_start_request_via_turn_runtime"
        ) as execute_built:
            execute_built.return_value = {"run_id": "run-5", "status": "starting"}
            result = execute_built_legacy_unowned_system_run_start_request_via_turn_runtime(
                request,
                execute_system_run_start_request_via_turn_runtime_fn=lambda *args, **kwargs: {"run_id": "unexpected"},
                build_run_execution_services_fn=lambda: built_services,
                create_run_from_request_fn=create_run,
            )

        self.assertEqual(result["run_id"], "run-5")
        create_run.assert_not_called()
        execute_built.assert_called_once()

    def test_execute_built_legacy_unowned_system_run_start_request_via_turn_runtime_uses_runtime_when_not_mocked(self):
        request = RunStartRequest(engine="orion", workspace_id="default", user_goal="hello")
        built_services = RunExecutionServices(
            stamp_request_owner=lambda req, current_user: req,
            prepare_run_start_request=lambda req: {"metadata": {}},
            create_run_from_request=lambda req: {"run_id": "unused"},
        )

        with patch(
            "server_modules.run_service.execute_built_unowned_system_run_start_request_via_turn_runtime",
            return_value={"run_id": "run-6", "status": "starting"},
        ) as execute_built:
            result = execute_built_legacy_unowned_system_run_start_request_via_turn_runtime(
                request,
                execute_system_run_start_request_via_turn_runtime_fn=lambda *args, **kwargs: {"run_id": "unexpected"},
                build_run_execution_services_fn=lambda: built_services,
                create_run_from_request_fn=lambda req: {"run_id": "unused"},
            )

        self.assertEqual(result["run_id"], "run-6")
        execute_built.assert_called_once()

    def test_build_execute_unowned_system_run_start_request_via_turn_runtime_returns_unowned_executor(self):
        captured = {}
        executor = build_execute_unowned_system_run_start_request_via_turn_runtime(
            execute_unowned_system_run_start_request_via_turn_runtime_fn=lambda request, *, services, current_user=None: captured.update(
                {
                    "request": request,
                    "services": services,
                    "current_user": current_user,
                }
            )
            or {"run_id": "run-7"}
        )
        services = RunExecutionServices(
            stamp_request_owner=lambda req, current_user: req,
            prepare_run_start_request=lambda req: {"metadata": {}},
            create_run_from_request=lambda req: {"run_id": "unused"},
        )

        result = executor(
            {"run": True},
            stamp_request_owner_fn=lambda req, current_user: "ignored",
            services=services,
            current_user={"user_id": "user-1"},
        )

        self.assertEqual(result["run_id"], "run-7")
        self.assertEqual(captured["request"], {"run": True})
        self.assertIs(captured["services"], services)
        self.assertEqual(captured["current_user"], {"user_id": "user-1"})

    def test_build_auto_delegation_plan_prefers_llm_route(self):
        plan = build_auto_delegation_plan(
            {
                "run_id": "parent-run",
                "user_goal": "Prepare a revenue spreadsheet and summarize outliers.",
                "context": {"metadata": {}},
            },
            max_children=2,
            routing_role_rules=AUTO_DELEGATION_ROLE_RULES,
            normalize_agent_role=lambda value: str(value or "").strip().lower(),
            llm_auto_delegate_role_fn=lambda **kwargs: {
                "agent_role": "finance",
                "reason": "Spreadsheet work fits the finance specialist.",
            },
        )

        self.assertEqual(plan[0]["agent_role"], "finance")
        self.assertEqual(plan[0]["metadata"]["auto_delegation_source"], "llm")

    def test_build_auto_delegation_plan_falls_back_to_keyword_when_llm_fails(self):
        plan = build_auto_delegation_plan(
            {
                "run_id": "parent-run",
                "user_goal": "Build and fix the platform automation flow.",
                "context": {"metadata": {}},
            },
            max_children=1,
            routing_role_rules=AUTO_DELEGATION_ROLE_RULES,
            normalize_agent_role=lambda value: str(value or "").strip().lower(),
            llm_auto_delegate_role_fn=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("routing offline")),
        )

        self.assertEqual(plan[0]["agent_role"], "builder")
        self.assertEqual(plan[0]["metadata"]["auto_delegation_source"], "keyword")

    def test_emit_auto_delegation_routing_log_emits_delegation_event(self):
        log_queue = queue.Queue()
        calls = []

        emit_auto_delegation_routing_log(
            "parent-run",
            [{"agent_role": "finance"}],
            runs_by_id={"parent-run": {"logs": log_queue}},
            emit_log_fn=lambda *args, **kwargs: calls.append((args, kwargs)),
            strategy="llm",
            reason="Spreadsheet work fits finance.",
        )

        self.assertEqual(calls[0][0][0], log_queue)
        self.assertEqual(calls[0][1]["event"], "delegation_routing")
        self.assertEqual(calls[0][1]["data"]["strategy"], "llm")

    def test_refresh_parent_delegation_state_updates_live_parent_and_emits_log(self):
        parent_logs = queue.Queue()
        runs_by_id = {
            "parent-run": {
                "context": {"metadata": {}},
                "result_data": {},
                "logs": parent_logs,
            }
        }
        archived = []
        emitted = []

        summary = refresh_parent_delegation_state(
            "parent-run",
            lookup_run_snapshot_fn=lambda run_id: {"run_id": run_id},
            find_run_relationships_fn=lambda run_id, snapshot: (
                {"run_id": run_id},
                [
                    {
                        "run_id": "child-1",
                        "status": "completed",
                        "agent_role": "builder",
                        "result_summary": "done",
                        "updated_at": "2026-04-02T00:01:00Z",
                        "created_at": "2026-04-02T00:01:00Z",
                    }
                ],
            ),
            timeout_stale_child_runs_fn=lambda parent_run_id, child_runs: [],
            schedule_auto_retry_for_failed_children_fn=lambda snapshot, child_runs, triggering_run_id=None: set(),
            build_delegation_summary_fn=lambda snapshot, child_runs, extra_retry_pending_lineages=None: {
                "ready": True,
                "next_action": "merge_results",
                "overall_status": "completed",
            },
            runs_by_id=runs_by_id,
            refresh_archived_run_snapshot_fn=lambda run_id, payload: archived.append((run_id, payload["updated_at"])),
            upsert_run_history_snapshot_fn=lambda snapshot: None,
            emit_log_fn=lambda *args, **kwargs: emitted.append((args, kwargs)),
            utc_now_iso_fn=lambda: "2026-04-06T00:00:00Z",
        )

        self.assertEqual(summary["next_action"], "merge_results")
        self.assertEqual(
            runs_by_id["parent-run"]["context"]["metadata"]["delegation_summary_cache"]["next_action"],
            "merge_results",
        )
        self.assertEqual(
            runs_by_id["parent-run"]["result_data"]["orchestration"]["updated_at"],
            "2026-04-06T00:00:00Z",
        )
        self.assertEqual(archived, [("parent-run", "2026-04-06T00:00:00Z")])
        self.assertEqual(emitted[0][1]["event"], "delegation_state")


if __name__ == "__main__":
    unittest.main()

import asyncio
import types
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from server_modules import runtime_route_registry_service
from server_modules import runtime_route_run_handlers_service


class _FakeApp:
    def __init__(self) -> None:
        self.routes = {}

    def _register(self, method, path, **kwargs):
        def _decorator(fn):
            self.routes[(method, path)] = fn
            return fn

        return _decorator

    def post(self, path, **kwargs):
        return self._register("POST", path, **kwargs)

    def get(self, path, **kwargs):
        return self._register("GET", path, **kwargs)

    def delete(self, path, **kwargs):
        return self._register("DELETE", path, **kwargs)


class _RunStartRequest:
    pass


class _RunDelegationRequest:
    pass


class _RunAutoDelegationRequest:
    pass


class _RunDelegationRetryRequest:
    pass


class _DecisionPayload:
    def validate_fields(self) -> None:
        return None


class _ApprovalResolvePayload:
    def validate_fields(self) -> None:
        return None


class RuntimeRunDetailApiTests(unittest.TestCase):
    def test_get_run_route_returns_detail_payload_for_selected_run_id(self):
        app = _FakeApp()
        captured = {}

        runtime_route_registry_service.register_runtime_run_routes(
            app,
            depends=lambda dependency: dependency,
            request_class=type("Request", (), {}),
            event_source_response_class=lambda events: events,
            require_api_key=object(),
            require_admin_api_key=object(),
            refresh_server_exports=lambda: None,
            heartbeat_scheduler=lambda: None,
            load_webhook_triggers=lambda: None,
            persist_webhook_triggers_locked=lambda: None,
            match_webhook_trigger_fn=lambda workspace_id, request_url: {},
            webhook_triggers={},
            webhook_trigger_lock=object(),
            run_start_request_class=_RunStartRequest,
            run_delegation_request_class=_RunDelegationRequest,
            run_auto_delegation_request_class=_RunAutoDelegationRequest,
            run_delegation_retry_request_class=_RunDelegationRetryRequest,
            decision_payload_class=_DecisionPayload,
            approval_resolve_payload_class=_ApprovalResolvePayload,
            workspace_memory_snapshot=lambda workspace_id: {},
            delete_memory=lambda workspace_id, key: {},
            read_workspace_context_files=lambda: [],
            write_workspace_context_file=lambda filename, content: {},
            single_agent_mode=False,
            runs={},
            serialize_run_snapshot=lambda run_id, run: {},
            iter_logs_for_run=lambda run_id: [],
            get_replay_payload=lambda run_id: {},
            direct_chat_stream_response_services=lambda: {},
            execute_run_start_request_via_turn_runtime=lambda *args, **kwargs: {},
            execute_system_run_start_request_via_turn_runtime=lambda *args, **kwargs: {},
            stamp_request_owner_fn=lambda *args, **kwargs: None,
            run_execution_services=lambda: {},
            build_run_routing_preview=lambda *args, **kwargs: {},
            build_run_precheck_result=lambda *args, **kwargs: {},
            run_routing_preview_services=lambda: {},
            delegate_run_children_callbacks={},
            auto_delegate_run_children_callbacks={},
            retry_failed_delegation_callbacks={},
            run_detail_callbacks={},
            runs_history_callbacks={},
            usage_snapshots_for_user_fn=lambda current_user: [],
            aggregate_usage_summary_fn=lambda snapshots: {},
            list_usage_runs_fn=lambda snapshots, **kwargs: {},
            submit_run_decision_callbacks={},
            resolve_run_approval_callbacks={},
            resume_waiting_run_callbacks={},
            pause_run_callbacks={},
            enforce_run_owner_access=lambda current_user, payload: None,
            runtime_workspace_service=types.SimpleNamespace(
                list_workspace_memory_payload=lambda *args, **kwargs: {},
                delete_workspace_memory_payload=lambda *args, **kwargs: {},
                workspace_context_files_payload=lambda *args, **kwargs: {},
                update_workspace_context_file_payload=lambda *args, **kwargs: {},
            ),
            runtime_heartbeat_service=types.SimpleNamespace(
                heartbeat_status_payload=lambda *args, **kwargs: {},
                trigger_heartbeat_payload=lambda *args, **kwargs: {},
            ),
            runtime_route_request_handlers_service=types.SimpleNamespace(
                respond_chat_response=lambda *args, **kwargs: {},
                update_workspace_context_file_response=lambda *args, **kwargs: {},
                trigger_heartbeat_response=lambda *args, **kwargs: {},
                register_webhook_trigger_response=lambda *args, **kwargs: {},
                ingest_webhook_response=lambda *args, **kwargs: {},
            ),
            runtime_route_run_handlers_service=runtime_route_run_handlers_service,
            runtime_request_service=types.SimpleNamespace(
                read_json_object_payload=lambda *args, **kwargs: {},
                require_authenticated_user=lambda current_user: current_user,
                read_json_payload=lambda *args, **kwargs: {},
            ),
            runtime_webhook_trigger_service=types.SimpleNamespace(
                register_webhook_trigger_payload=lambda *args, **kwargs: {},
                build_webhook_trigger=lambda *args, **kwargs: {},
                ingest_webhook_payload=lambda *args, **kwargs: {},
            ),
            runtime_run_delegation_service=types.SimpleNamespace(
                delegate_run_children=lambda *args, **kwargs: {},
                auto_delegate_run_children=lambda *args, **kwargs: {},
                retry_failed_delegation_runs=lambda *args, **kwargs: {},
            ),
            runtime_run_query_service=types.SimpleNamespace(
                build_run_detail_response=lambda run_id, **kwargs: (
                    captured.setdefault("run_id", run_id),
                    {
                        "run_id": run_id,
                        "status": "running",
                    },
                )[1],
            ),
            runtime_run_entry_service=types.SimpleNamespace(
                start_run_response=lambda *args, **kwargs: {},
                preview_routing_response=lambda *args, **kwargs: {},
                precheck_run_response=lambda *args, **kwargs: {},
                stream_run_response=lambda *args, **kwargs: {},
            ),
            runtime_run_replay_service=types.SimpleNamespace(
                replay_item_response_for_run=lambda *args, **kwargs: {},
                replay_run_from_run_id=lambda *args, **kwargs: {},
            ),
            runtime_run_approval_service=types.SimpleNamespace(
                submit_run_decision=lambda *args, **kwargs: {},
                resolve_run_approval=lambda *args, **kwargs: {},
                resolve_standalone_approval=lambda *args, **kwargs: {},
            ),
            runtime_run_control_service=types.SimpleNamespace(
                resume_waiting_run=lambda *args, **kwargs: {},
                pause_run_for_takeover=lambda *args, **kwargs: {},
            ),
            runtime_history_service=types.SimpleNamespace(
                build_runs_history_payload=lambda *args, **kwargs: {},
            ),
            runtime_usage_service=types.SimpleNamespace(
                usage_summary_payload=lambda *args, **kwargs: {},
                usage_runs_payload=lambda *args, **kwargs: {},
            ),
            build_direct_chat_stream_response=lambda *args, **kwargs: {},
            enforce_workspace_access_fn=lambda current_user, workspace_id, minimum_role="viewer": workspace_id,
        )

        with patch.object(
            runtime_route_registry_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "operation": "get_run",
                "next_action": "get_run",
            },
        ) as rust_mock:
            payload = asyncio.run(
                app.routes[("GET", "/runs/{run_id}")](
                    run_id="00000000-0000-0000-0000-000000000123",
                    current_user={"user_id": "user-1"},
                )
            )

        self.assertEqual(captured["run_id"], "00000000-0000-0000-0000-000000000123")
        self.assertEqual(payload["status"], "running")
        self.assertEqual(rust_mock.call_args.args[0], "run-api-decision")
        self.assertEqual(rust_mock.call_args.args[1]["operation"], "get_run")

    def test_get_run_route_request_owner_approval_blocks_before_detail_builder(self):
        app = _FakeApp()
        captured = {}

        runtime_route_registry_service.register_runtime_run_routes(
            app,
            depends=lambda dependency: dependency,
            request_class=type("Request", (), {}),
            event_source_response_class=lambda events: events,
            require_api_key=object(),
            require_admin_api_key=object(),
            refresh_server_exports=lambda: None,
            heartbeat_scheduler=lambda: None,
            load_webhook_triggers=lambda: None,
            persist_webhook_triggers_locked=lambda: None,
            match_webhook_trigger_fn=lambda workspace_id, request_url: {},
            webhook_triggers={},
            webhook_trigger_lock=object(),
            run_start_request_class=_RunStartRequest,
            run_delegation_request_class=_RunDelegationRequest,
            run_auto_delegation_request_class=_RunAutoDelegationRequest,
            run_delegation_retry_request_class=_RunDelegationRetryRequest,
            decision_payload_class=_DecisionPayload,
            approval_resolve_payload_class=_ApprovalResolvePayload,
            workspace_memory_snapshot=lambda workspace_id: {},
            delete_memory=lambda workspace_id, key: {},
            read_workspace_context_files=lambda: [],
            write_workspace_context_file=lambda filename, content: {},
            single_agent_mode=False,
            runs={"00000000-0000-0000-0000-000000000123": {"workspace_id": "ws-1", "tenant_id": "tenant-1", "status": "running"}},
            serialize_run_snapshot=lambda run_id, run: {},
            iter_logs_for_run=lambda run_id: [],
            get_replay_payload=lambda run_id: {},
            direct_chat_stream_response_services=lambda: {},
            execute_run_start_request_via_turn_runtime=lambda *args, **kwargs: {},
            execute_system_run_start_request_via_turn_runtime=lambda *args, **kwargs: {},
            stamp_request_owner_fn=lambda *args, **kwargs: None,
            run_execution_services=lambda: {},
            build_run_routing_preview=lambda *args, **kwargs: {},
            build_run_precheck_result=lambda *args, **kwargs: {},
            run_routing_preview_services=lambda: {},
            delegate_run_children_callbacks={},
            auto_delegate_run_children_callbacks={},
            retry_failed_delegation_callbacks={},
            run_detail_callbacks={},
            runs_history_callbacks={},
            usage_snapshots_for_user_fn=lambda current_user: [],
            aggregate_usage_summary_fn=lambda snapshots: {},
            list_usage_runs_fn=lambda snapshots, **kwargs: {},
            submit_run_decision_callbacks={},
            resolve_run_approval_callbacks={},
            resume_waiting_run_callbacks={},
            pause_run_callbacks={},
            enforce_run_owner_access=lambda current_user, payload: None,
            runtime_workspace_service=types.SimpleNamespace(
                list_workspace_memory_payload=lambda *args, **kwargs: {},
                delete_workspace_memory_payload=lambda *args, **kwargs: {},
                workspace_context_files_payload=lambda *args, **kwargs: {},
                update_workspace_context_file_payload=lambda *args, **kwargs: {},
            ),
            runtime_heartbeat_service=types.SimpleNamespace(
                heartbeat_status_payload=lambda *args, **kwargs: {},
                trigger_heartbeat_payload=lambda *args, **kwargs: {},
            ),
            runtime_route_request_handlers_service=types.SimpleNamespace(
                respond_chat_response=lambda *args, **kwargs: {},
                update_workspace_context_file_response=lambda *args, **kwargs: {},
                trigger_heartbeat_response=lambda *args, **kwargs: {},
                register_webhook_trigger_response=lambda *args, **kwargs: {},
                ingest_webhook_response=lambda *args, **kwargs: {},
            ),
            runtime_route_run_handlers_service=runtime_route_run_handlers_service,
            runtime_request_service=types.SimpleNamespace(
                read_json_object_payload=lambda *args, **kwargs: {},
                require_authenticated_user=lambda current_user: current_user,
                read_json_payload=lambda *args, **kwargs: {},
            ),
            runtime_webhook_trigger_service=types.SimpleNamespace(
                register_webhook_trigger_payload=lambda *args, **kwargs: {},
                build_webhook_trigger=lambda *args, **kwargs: {},
                ingest_webhook_payload=lambda *args, **kwargs: {},
            ),
            runtime_run_delegation_service=types.SimpleNamespace(
                delegate_run_children=lambda *args, **kwargs: {},
                auto_delegate_run_children=lambda *args, **kwargs: {},
                retry_failed_delegation_runs=lambda *args, **kwargs: {},
            ),
            runtime_run_query_service=types.SimpleNamespace(
                build_run_detail_response=lambda run_id, **kwargs: captured.setdefault("run_id", run_id) or {"run_id": run_id},
            ),
            runtime_run_entry_service=types.SimpleNamespace(
                start_run_response=lambda *args, **kwargs: {},
                preview_routing_response=lambda *args, **kwargs: {},
                precheck_run_response=lambda *args, **kwargs: {},
                stream_run_response=lambda *args, **kwargs: {},
            ),
            runtime_run_replay_service=types.SimpleNamespace(
                replay_item_response_for_run=lambda *args, **kwargs: {},
                replay_run_from_run_id=lambda *args, **kwargs: {},
            ),
            runtime_run_approval_service=types.SimpleNamespace(
                submit_run_decision=lambda *args, **kwargs: {},
                resolve_run_approval=lambda *args, **kwargs: {},
                resolve_standalone_approval=lambda *args, **kwargs: {},
            ),
            runtime_run_control_service=types.SimpleNamespace(
                resume_waiting_run=lambda *args, **kwargs: {},
                pause_run_for_takeover=lambda *args, **kwargs: {},
            ),
            runtime_history_service=types.SimpleNamespace(
                build_runs_history_payload=lambda *args, **kwargs: {},
            ),
            runtime_usage_service=types.SimpleNamespace(
                usage_summary_payload=lambda *args, **kwargs: {},
                usage_runs_payload=lambda *args, **kwargs: {},
            ),
            build_direct_chat_stream_response=lambda *args, **kwargs: {},
            enforce_workspace_access_fn=lambda current_user, workspace_id, minimum_role="viewer": workspace_id,
        )

        with patch.object(
            runtime_route_registry_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "require_approval",
                "operation": "get_run",
                "reason": "run_sensitive_payload_requires_approval",
                "next_action": "request_owner_approval",
            },
        ):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(
                    app.routes[("GET", "/runs/{run_id}")](
                        run_id="00000000-0000-0000-0000-000000000123",
                        current_user={"user_id": "user-1"},
                    )
                )

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(captured, {})

    def test_get_run_browser_checkpoint_route_request_owner_approval_blocks_before_builder(self):
        app = _FakeApp()
        captured = {}

        runtime_route_registry_service.register_runtime_run_routes(
            app,
            depends=lambda dependency: dependency,
            request_class=type("Request", (), {}),
            event_source_response_class=lambda events: events,
            require_api_key=object(),
            require_admin_api_key=object(),
            refresh_server_exports=lambda: None,
            heartbeat_scheduler=lambda: None,
            load_webhook_triggers=lambda: None,
            persist_webhook_triggers_locked=lambda: None,
            match_webhook_trigger_fn=lambda workspace_id, request_url: {},
            webhook_triggers={},
            webhook_trigger_lock=object(),
            run_start_request_class=_RunStartRequest,
            run_delegation_request_class=_RunDelegationRequest,
            run_auto_delegation_request_class=_RunAutoDelegationRequest,
            run_delegation_retry_request_class=_RunDelegationRetryRequest,
            decision_payload_class=_DecisionPayload,
            approval_resolve_payload_class=_ApprovalResolvePayload,
            workspace_memory_snapshot=lambda workspace_id: {},
            delete_memory=lambda workspace_id, key: {},
            read_workspace_context_files=lambda: [],
            write_workspace_context_file=lambda filename, content: {},
            single_agent_mode=False,
            runs={"00000000-0000-0000-0000-000000000123": {"workspace_id": "ws-1", "tenant_id": "tenant-1", "status": "running"}},
            serialize_run_snapshot=lambda run_id, run: {},
            iter_logs_for_run=lambda run_id: [],
            get_replay_payload=lambda run_id: {},
            direct_chat_stream_response_services=lambda: {},
            execute_run_start_request_via_turn_runtime=lambda *args, **kwargs: {},
            execute_system_run_start_request_via_turn_runtime=lambda *args, **kwargs: {},
            stamp_request_owner_fn=lambda *args, **kwargs: None,
            run_execution_services=lambda: {},
            build_run_routing_preview=lambda *args, **kwargs: {},
            build_run_precheck_result=lambda *args, **kwargs: {},
            run_routing_preview_services=lambda: {},
            delegate_run_children_callbacks={},
            auto_delegate_run_children_callbacks={},
            retry_failed_delegation_callbacks={},
            run_detail_callbacks={},
            runs_history_callbacks={},
            usage_snapshots_for_user_fn=lambda current_user: [],
            aggregate_usage_summary_fn=lambda snapshots: {},
            list_usage_runs_fn=lambda snapshots, **kwargs: {},
            submit_run_decision_callbacks={},
            resolve_run_approval_callbacks={},
            resume_waiting_run_callbacks={},
            pause_run_callbacks={},
            enforce_run_owner_access=lambda current_user, payload: None,
            runtime_workspace_service=types.SimpleNamespace(
                list_workspace_memory_payload=lambda *args, **kwargs: {},
                delete_workspace_memory_payload=lambda *args, **kwargs: {},
                workspace_context_files_payload=lambda *args, **kwargs: {},
                update_workspace_context_file_payload=lambda *args, **kwargs: {},
            ),
            runtime_heartbeat_service=types.SimpleNamespace(
                heartbeat_status_payload=lambda *args, **kwargs: {},
                trigger_heartbeat_payload=lambda *args, **kwargs: {},
            ),
            runtime_route_request_handlers_service=types.SimpleNamespace(
                respond_chat_response=lambda *args, **kwargs: {},
                update_workspace_context_file_response=lambda *args, **kwargs: {},
                trigger_heartbeat_response=lambda *args, **kwargs: {},
                register_webhook_trigger_response=lambda *args, **kwargs: {},
                ingest_webhook_response=lambda *args, **kwargs: {},
            ),
            runtime_route_run_handlers_service=types.SimpleNamespace(
                get_run_browser_checkpoint_route_response=lambda run_id, **kwargs: captured.setdefault("run_id", str(run_id)) or {"run_id": str(run_id)},
                get_run_browser_session_route_response=lambda *args, **kwargs: {},
                **runtime_route_run_handlers_service.__dict__,
            ),
            runtime_request_service=types.SimpleNamespace(
                read_json_object_payload=lambda *args, **kwargs: {},
                require_authenticated_user=lambda current_user: current_user,
                read_json_payload=lambda *args, **kwargs: {},
            ),
            runtime_webhook_trigger_service=types.SimpleNamespace(
                register_webhook_trigger_payload=lambda *args, **kwargs: {},
                build_webhook_trigger=lambda *args, **kwargs: {},
                ingest_webhook_payload=lambda *args, **kwargs: {},
            ),
            runtime_run_delegation_service=types.SimpleNamespace(
                delegate_run_children=lambda *args, **kwargs: {},
                auto_delegate_run_children=lambda *args, **kwargs: {},
                retry_failed_delegation_runs=lambda *args, **kwargs: {},
            ),
            runtime_run_query_service=types.SimpleNamespace(
                build_run_browser_checkpoint_response=lambda *args, **kwargs: captured.setdefault("builder_called", True) or {},
                build_run_browser_session_response=lambda *args, **kwargs: {},
                build_run_detail_response=lambda *args, **kwargs: {},
            ),
            runtime_run_entry_service=types.SimpleNamespace(
                start_run_response=lambda *args, **kwargs: {},
                preview_routing_response=lambda *args, **kwargs: {},
                precheck_run_response=lambda *args, **kwargs: {},
                stream_run_response=lambda *args, **kwargs: {},
            ),
            runtime_run_replay_service=types.SimpleNamespace(
                replay_item_response_for_run=lambda *args, **kwargs: {},
                replay_run_from_run_id=lambda *args, **kwargs: {},
            ),
            runtime_run_approval_service=types.SimpleNamespace(
                submit_run_decision=lambda *args, **kwargs: {},
                resolve_run_approval=lambda *args, **kwargs: {},
                resolve_standalone_approval=lambda *args, **kwargs: {},
            ),
            runtime_run_control_service=types.SimpleNamespace(
                resume_waiting_run=lambda *args, **kwargs: {},
                pause_run_for_takeover=lambda *args, **kwargs: {},
            ),
            runtime_history_service=types.SimpleNamespace(
                build_runs_history_payload=lambda *args, **kwargs: {},
            ),
            runtime_usage_service=types.SimpleNamespace(
                usage_summary_payload=lambda *args, **kwargs: {},
                usage_runs_payload=lambda *args, **kwargs: {},
            ),
            build_direct_chat_stream_response=lambda *args, **kwargs: {},
            enforce_workspace_access_fn=lambda current_user, workspace_id, minimum_role="viewer": workspace_id,
        )

        with patch.object(
            runtime_route_registry_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "require_approval",
                "operation": "get_run",
                "reason": "run_sensitive_payload_requires_approval",
                "next_action": "request_owner_approval",
            },
        ):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(
                    app.routes[("GET", "/runs/{run_id}/browser-checkpoint")](
                        run_id="00000000-0000-0000-0000-000000000123",
                        current_user={"user_id": "user-1"},
                    )
                )

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(captured, {})

    def test_get_run_browser_checkpoint_route_returns_checkpoint_payload_for_selected_run_id(self):
        app = _FakeApp()
        captured = {}

        runtime_route_registry_service.register_runtime_run_routes(
            app,
            depends=lambda dependency: dependency,
            request_class=type("Request", (), {}),
            event_source_response_class=lambda events: events,
            require_api_key=object(),
            require_admin_api_key=object(),
            refresh_server_exports=lambda: None,
            heartbeat_scheduler=lambda: None,
            load_webhook_triggers=lambda: None,
            persist_webhook_triggers_locked=lambda: None,
            match_webhook_trigger_fn=lambda workspace_id, request_url: {},
            webhook_triggers={},
            webhook_trigger_lock=object(),
            run_start_request_class=_RunStartRequest,
            run_delegation_request_class=_RunDelegationRequest,
            run_auto_delegation_request_class=_RunAutoDelegationRequest,
            run_delegation_retry_request_class=_RunDelegationRetryRequest,
            decision_payload_class=_DecisionPayload,
            approval_resolve_payload_class=_ApprovalResolvePayload,
            workspace_memory_snapshot=lambda workspace_id: {},
            delete_memory=lambda workspace_id, key: {},
            read_workspace_context_files=lambda: [],
            write_workspace_context_file=lambda filename, content: {},
            single_agent_mode=False,
            runs={},
            serialize_run_snapshot=lambda run_id, run: {},
            iter_logs_for_run=lambda run_id: [],
            get_replay_payload=lambda run_id: {},
            direct_chat_stream_response_services=lambda: {},
            execute_run_start_request_via_turn_runtime=lambda *args, **kwargs: {},
            execute_system_run_start_request_via_turn_runtime=lambda *args, **kwargs: {},
            stamp_request_owner_fn=lambda *args, **kwargs: None,
            run_execution_services=lambda: {},
            build_run_routing_preview=lambda *args, **kwargs: {},
            build_run_precheck_result=lambda *args, **kwargs: {},
            run_routing_preview_services=lambda: {},
            delegate_run_children_callbacks={},
            auto_delegate_run_children_callbacks={},
            retry_failed_delegation_callbacks={},
            run_detail_callbacks={},
            runs_history_callbacks={},
            usage_snapshots_for_user_fn=lambda current_user: [],
            aggregate_usage_summary_fn=lambda snapshots: {},
            list_usage_runs_fn=lambda snapshots, **kwargs: {},
            submit_run_decision_callbacks={},
            resolve_run_approval_callbacks={},
            resume_waiting_run_callbacks={},
            pause_run_callbacks={},
            enforce_run_owner_access=lambda current_user, payload: None,
            runtime_workspace_service=types.SimpleNamespace(
                list_workspace_memory_payload=lambda *args, **kwargs: {},
                delete_workspace_memory_payload=lambda *args, **kwargs: {},
                workspace_context_files_payload=lambda *args, **kwargs: {},
                update_workspace_context_file_payload=lambda *args, **kwargs: {},
            ),
            runtime_heartbeat_service=types.SimpleNamespace(
                heartbeat_status_payload=lambda *args, **kwargs: {},
                trigger_heartbeat_payload=lambda *args, **kwargs: {},
            ),
            runtime_route_request_handlers_service=types.SimpleNamespace(
                respond_chat_response=lambda *args, **kwargs: {},
                update_workspace_context_file_response=lambda *args, **kwargs: {},
                trigger_heartbeat_response=lambda *args, **kwargs: {},
                register_webhook_trigger_response=lambda *args, **kwargs: {},
                ingest_webhook_response=lambda *args, **kwargs: {},
            ),
            runtime_route_run_handlers_service=runtime_route_run_handlers_service,
            runtime_request_service=types.SimpleNamespace(
                read_json_object_payload=lambda *args, **kwargs: {},
                require_authenticated_user=lambda current_user: current_user,
                read_json_payload=lambda *args, **kwargs: {},
            ),
            runtime_webhook_trigger_service=types.SimpleNamespace(
                register_webhook_trigger_payload=lambda *args, **kwargs: {},
                build_webhook_trigger=lambda *args, **kwargs: {},
                ingest_webhook_payload=lambda *args, **kwargs: {},
            ),
            runtime_run_delegation_service=types.SimpleNamespace(
                delegate_run_children=lambda *args, **kwargs: {},
                auto_delegate_run_children=lambda *args, **kwargs: {},
                retry_failed_delegation_runs=lambda *args, **kwargs: {},
            ),
            runtime_run_query_service=types.SimpleNamespace(
                build_run_detail_response=lambda *args, **kwargs: {},
                build_run_browser_checkpoint_response=lambda run_id, **kwargs: (
                    captured.setdefault("run_id", run_id),
                    {"run_id": run_id, "available": True},
                )[1],
            ),
            runtime_run_entry_service=types.SimpleNamespace(
                start_run_response=lambda *args, **kwargs: {},
                preview_routing_response=lambda *args, **kwargs: {},
                precheck_run_response=lambda *args, **kwargs: {},
                stream_run_response=lambda *args, **kwargs: {},
            ),
            runtime_run_replay_service=types.SimpleNamespace(
                replay_item_response_for_run=lambda *args, **kwargs: {},
                replay_run_from_run_id=lambda *args, **kwargs: {},
            ),
            runtime_run_approval_service=types.SimpleNamespace(
                submit_run_decision=lambda *args, **kwargs: {},
                resolve_run_approval=lambda *args, **kwargs: {},
                resolve_standalone_approval=lambda *args, **kwargs: {},
            ),
            runtime_run_control_service=types.SimpleNamespace(
                resume_waiting_run=lambda *args, **kwargs: {},
                pause_run_for_takeover=lambda *args, **kwargs: {},
            ),
            runtime_history_service=types.SimpleNamespace(
                build_runs_history_payload=lambda *args, **kwargs: {},
            ),
            runtime_usage_service=types.SimpleNamespace(
                usage_summary_payload=lambda *args, **kwargs: {},
                usage_runs_payload=lambda *args, **kwargs: {},
            ),
            build_direct_chat_stream_response=lambda *args, **kwargs: {},
            enforce_workspace_access_fn=lambda current_user, workspace_id, minimum_role="viewer": workspace_id,
        )

        payload = asyncio.run(
            app.routes[("GET", "/runs/{run_id}/browser-checkpoint")](
                run_id="00000000-0000-0000-0000-000000000456",
                current_user={"user_id": "user-1"},
            )
        )

        self.assertEqual(captured["run_id"], "00000000-0000-0000-0000-000000000456")
        self.assertTrue(payload["available"])

    def test_get_run_browser_session_route_request_owner_approval_blocks_before_builder(self):
        app = _FakeApp()
        captured = {}

        runtime_route_registry_service.register_runtime_run_routes(
            app,
            depends=lambda dependency: dependency,
            request_class=type("Request", (), {}),
            event_source_response_class=lambda events: events,
            require_api_key=object(),
            require_admin_api_key=object(),
            refresh_server_exports=lambda: None,
            heartbeat_scheduler=lambda: None,
            load_webhook_triggers=lambda: None,
            persist_webhook_triggers_locked=lambda: None,
            match_webhook_trigger_fn=lambda workspace_id, request_url: {},
            webhook_triggers={},
            webhook_trigger_lock=object(),
            run_start_request_class=_RunStartRequest,
            run_delegation_request_class=_RunDelegationRequest,
            run_auto_delegation_request_class=_RunAutoDelegationRequest,
            run_delegation_retry_request_class=_RunDelegationRetryRequest,
            decision_payload_class=_DecisionPayload,
            approval_resolve_payload_class=_ApprovalResolvePayload,
            workspace_memory_snapshot=lambda workspace_id: {},
            delete_memory=lambda workspace_id, key: {},
            read_workspace_context_files=lambda: [],
            write_workspace_context_file=lambda filename, content: {},
            single_agent_mode=False,
            runs={"00000000-0000-0000-0000-000000000123": {"workspace_id": "ws-1", "tenant_id": "tenant-1", "status": "running"}},
            serialize_run_snapshot=lambda run_id, run: {},
            iter_logs_for_run=lambda run_id: [],
            get_replay_payload=lambda run_id: {},
            direct_chat_stream_response_services=lambda: {},
            execute_run_start_request_via_turn_runtime=lambda *args, **kwargs: {},
            execute_system_run_start_request_via_turn_runtime=lambda *args, **kwargs: {},
            stamp_request_owner_fn=lambda *args, **kwargs: None,
            run_execution_services=lambda: {},
            build_run_routing_preview=lambda *args, **kwargs: {},
            build_run_precheck_result=lambda *args, **kwargs: {},
            run_routing_preview_services=lambda: {},
            delegate_run_children_callbacks={},
            auto_delegate_run_children_callbacks={},
            retry_failed_delegation_callbacks={},
            run_detail_callbacks={},
            runs_history_callbacks={},
            usage_snapshots_for_user_fn=lambda current_user: [],
            aggregate_usage_summary_fn=lambda snapshots: {},
            list_usage_runs_fn=lambda snapshots, **kwargs: {},
            submit_run_decision_callbacks={},
            resolve_run_approval_callbacks={},
            resume_waiting_run_callbacks={},
            pause_run_callbacks={},
            enforce_run_owner_access=lambda current_user, payload: None,
            runtime_workspace_service=types.SimpleNamespace(
                list_workspace_memory_payload=lambda *args, **kwargs: {},
                delete_workspace_memory_payload=lambda *args, **kwargs: {},
                workspace_context_files_payload=lambda *args, **kwargs: {},
                update_workspace_context_file_payload=lambda *args, **kwargs: {},
            ),
            runtime_heartbeat_service=types.SimpleNamespace(
                heartbeat_status_payload=lambda *args, **kwargs: {},
                trigger_heartbeat_payload=lambda *args, **kwargs: {},
            ),
            runtime_route_request_handlers_service=types.SimpleNamespace(
                respond_chat_response=lambda *args, **kwargs: {},
                update_workspace_context_file_response=lambda *args, **kwargs: {},
                trigger_heartbeat_response=lambda *args, **kwargs: {},
                register_webhook_trigger_response=lambda *args, **kwargs: {},
                ingest_webhook_response=lambda *args, **kwargs: {},
            ),
            runtime_route_run_handlers_service=types.SimpleNamespace(
                get_run_browser_checkpoint_route_response=lambda *args, **kwargs: {},
                get_run_browser_session_route_response=lambda run_id, **kwargs: captured.setdefault("run_id", str(run_id)) or {"run_id": str(run_id)},
                **runtime_route_run_handlers_service.__dict__,
            ),
            runtime_request_service=types.SimpleNamespace(
                read_json_object_payload=lambda *args, **kwargs: {},
                require_authenticated_user=lambda current_user: current_user,
                read_json_payload=lambda *args, **kwargs: {},
            ),
            runtime_webhook_trigger_service=types.SimpleNamespace(
                register_webhook_trigger_payload=lambda *args, **kwargs: {},
                build_webhook_trigger=lambda *args, **kwargs: {},
                ingest_webhook_payload=lambda *args, **kwargs: {},
            ),
            runtime_run_delegation_service=types.SimpleNamespace(
                delegate_run_children=lambda *args, **kwargs: {},
                auto_delegate_run_children=lambda *args, **kwargs: {},
                retry_failed_delegation_runs=lambda *args, **kwargs: {},
            ),
            runtime_run_query_service=types.SimpleNamespace(
                build_run_browser_checkpoint_response=lambda *args, **kwargs: {},
                build_run_browser_session_response=lambda *args, **kwargs: captured.setdefault("builder_called", True) or {},
                build_run_detail_response=lambda *args, **kwargs: {},
            ),
            runtime_run_entry_service=types.SimpleNamespace(
                start_run_response=lambda *args, **kwargs: {},
                preview_routing_response=lambda *args, **kwargs: {},
                precheck_run_response=lambda *args, **kwargs: {},
                stream_run_response=lambda *args, **kwargs: {},
            ),
            runtime_run_replay_service=types.SimpleNamespace(
                replay_item_response_for_run=lambda *args, **kwargs: {},
                replay_run_from_run_id=lambda *args, **kwargs: {},
            ),
            runtime_run_approval_service=types.SimpleNamespace(
                submit_run_decision=lambda *args, **kwargs: {},
                resolve_run_approval=lambda *args, **kwargs: {},
                resolve_standalone_approval=lambda *args, **kwargs: {},
            ),
            runtime_run_control_service=types.SimpleNamespace(
                resume_waiting_run=lambda *args, **kwargs: {},
                pause_run_for_takeover=lambda *args, **kwargs: {},
            ),
            runtime_history_service=types.SimpleNamespace(
                build_runs_history_payload=lambda *args, **kwargs: {},
            ),
            runtime_usage_service=types.SimpleNamespace(
                usage_summary_payload=lambda *args, **kwargs: {},
                usage_runs_payload=lambda *args, **kwargs: {},
            ),
            build_direct_chat_stream_response=lambda *args, **kwargs: {},
            enforce_workspace_access_fn=lambda current_user, workspace_id, minimum_role="viewer": workspace_id,
        )

        with patch.object(
            runtime_route_registry_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "require_approval",
                "operation": "get_run",
                "reason": "run_sensitive_payload_requires_approval",
                "next_action": "request_owner_approval",
            },
        ):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(
                    app.routes[("GET", "/runs/{run_id}/browser-session")](
                        run_id="00000000-0000-0000-0000-000000000123",
                        current_user={"user_id": "user-1"},
                    )
                )

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(captured, {})

    def test_get_run_browser_session_route_returns_session_payload_for_selected_run_id(self):
        app = _FakeApp()
        captured = {}

        runtime_route_registry_service.register_runtime_run_routes(
            app,
            depends=lambda dependency: dependency,
            request_class=type("Request", (), {}),
            event_source_response_class=lambda events: events,
            require_api_key=object(),
            require_admin_api_key=object(),
            refresh_server_exports=lambda: None,
            heartbeat_scheduler=lambda: None,
            load_webhook_triggers=lambda: None,
            persist_webhook_triggers_locked=lambda: None,
            match_webhook_trigger_fn=lambda workspace_id, request_url: {},
            webhook_triggers={},
            webhook_trigger_lock=object(),
            run_start_request_class=_RunStartRequest,
            run_delegation_request_class=_RunDelegationRequest,
            run_auto_delegation_request_class=_RunAutoDelegationRequest,
            run_delegation_retry_request_class=_RunDelegationRetryRequest,
            decision_payload_class=_DecisionPayload,
            approval_resolve_payload_class=_ApprovalResolvePayload,
            workspace_memory_snapshot=lambda workspace_id: {},
            delete_memory=lambda workspace_id, key: {},
            read_workspace_context_files=lambda: [],
            write_workspace_context_file=lambda filename, content: {},
            single_agent_mode=False,
            runs={},
            serialize_run_snapshot=lambda run_id, run: {},
            iter_logs_for_run=lambda run_id: [],
            get_replay_payload=lambda run_id: {},
            direct_chat_stream_response_services=lambda: {},
            execute_run_start_request_via_turn_runtime=lambda *args, **kwargs: {},
            execute_system_run_start_request_via_turn_runtime=lambda *args, **kwargs: {},
            stamp_request_owner_fn=lambda *args, **kwargs: None,
            run_execution_services=lambda: {},
            build_run_routing_preview=lambda *args, **kwargs: {},
            build_run_precheck_result=lambda *args, **kwargs: {},
            run_routing_preview_services=lambda: {},
            delegate_run_children_callbacks={},
            auto_delegate_run_children_callbacks={},
            retry_failed_delegation_callbacks={},
            run_detail_callbacks={},
            runs_history_callbacks={},
            usage_snapshots_for_user_fn=lambda current_user: [],
            aggregate_usage_summary_fn=lambda snapshots: {},
            list_usage_runs_fn=lambda snapshots, **kwargs: {},
            submit_run_decision_callbacks={},
            resolve_run_approval_callbacks={},
            resume_waiting_run_callbacks={},
            pause_run_callbacks={},
            enforce_run_owner_access=lambda current_user, payload: None,
            runtime_workspace_service=types.SimpleNamespace(
                list_workspace_memory_payload=lambda *args, **kwargs: {},
                delete_workspace_memory_payload=lambda *args, **kwargs: {},
                workspace_context_files_payload=lambda *args, **kwargs: {},
                update_workspace_context_file_payload=lambda *args, **kwargs: {},
            ),
            runtime_heartbeat_service=types.SimpleNamespace(
                heartbeat_status_payload=lambda *args, **kwargs: {},
                trigger_heartbeat_payload=lambda *args, **kwargs: {},
            ),
            runtime_route_request_handlers_service=types.SimpleNamespace(
                respond_chat_response=lambda *args, **kwargs: {},
                update_workspace_context_file_response=lambda *args, **kwargs: {},
                trigger_heartbeat_response=lambda *args, **kwargs: {},
                register_webhook_trigger_response=lambda *args, **kwargs: {},
                ingest_webhook_response=lambda *args, **kwargs: {},
            ),
            runtime_route_run_handlers_service=runtime_route_run_handlers_service,
            runtime_request_service=types.SimpleNamespace(
                read_json_object_payload=lambda *args, **kwargs: {},
                require_authenticated_user=lambda current_user: current_user,
                read_json_payload=lambda *args, **kwargs: {},
            ),
            runtime_webhook_trigger_service=types.SimpleNamespace(
                register_webhook_trigger_payload=lambda *args, **kwargs: {},
                build_webhook_trigger=lambda *args, **kwargs: {},
                ingest_webhook_payload=lambda *args, **kwargs: {},
            ),
            runtime_run_delegation_service=types.SimpleNamespace(
                delegate_run_children=lambda *args, **kwargs: {},
                auto_delegate_run_children=lambda *args, **kwargs: {},
                retry_failed_delegation_runs=lambda *args, **kwargs: {},
            ),
            runtime_run_query_service=types.SimpleNamespace(
                build_run_detail_response=lambda *args, **kwargs: {},
                build_run_browser_session_response=lambda run_id, **kwargs: (
                    captured.setdefault("run_id", run_id),
                    {"run_id": run_id, "session_profile": "qa-browser"},
                )[1],
            ),
            runtime_run_entry_service=types.SimpleNamespace(
                start_run_response=lambda *args, **kwargs: {},
                preview_routing_response=lambda *args, **kwargs: {},
                precheck_run_response=lambda *args, **kwargs: {},
                stream_run_response=lambda *args, **kwargs: {},
            ),
            runtime_run_replay_service=types.SimpleNamespace(
                replay_item_response_for_run=lambda *args, **kwargs: {},
                replay_run_from_run_id=lambda *args, **kwargs: {},
            ),
            runtime_run_approval_service=types.SimpleNamespace(
                submit_run_decision=lambda *args, **kwargs: {},
                resolve_run_approval=lambda *args, **kwargs: {},
                resolve_standalone_approval=lambda *args, **kwargs: {},
            ),
            runtime_run_control_service=types.SimpleNamespace(
                resume_waiting_run=lambda *args, **kwargs: {},
                pause_run_for_takeover=lambda *args, **kwargs: {},
            ),
            runtime_history_service=types.SimpleNamespace(
                build_runs_history_payload=lambda *args, **kwargs: {},
            ),
            runtime_usage_service=types.SimpleNamespace(
                usage_summary_payload=lambda *args, **kwargs: {},
                usage_runs_payload=lambda *args, **kwargs: {},
            ),
            build_direct_chat_stream_response=lambda *args, **kwargs: {},
            enforce_workspace_access_fn=lambda current_user, workspace_id, minimum_role="viewer": workspace_id,
        )

        payload = asyncio.run(
            app.routes[("GET", "/runs/{run_id}/browser-session")](
                run_id="00000000-0000-0000-0000-000000000789",
                current_user={"user_id": "user-1"},
            )
        )

        self.assertEqual(captured["run_id"], "00000000-0000-0000-0000-000000000789")
        self.assertEqual(payload["session_profile"], "qa-browser")


if __name__ == "__main__":
    unittest.main()

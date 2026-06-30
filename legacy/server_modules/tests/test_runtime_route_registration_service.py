import threading
import types
import unittest

from server_modules import runtime_route_registration_service


class _DummyScheduler:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs


class RuntimeRouteRegistrationServiceTests(unittest.TestCase):
    def test_register_runtime_run_routes_from_api_builds_bootstrap_bindings_and_registry(self):
        calls = {}
        scheduler_holder = {}
        deps = types.SimpleNamespace(
            run_start_request_class=type("RunStartRequest", (), {}),
            build_inbound_agent_turn_request=lambda **kwargs: kwargs,
            execute_system_agent_turn=lambda **kwargs: {},
            trigger_pending_heartbeat_schedules=lambda **kwargs: {},
            handle_telegram_send_message=lambda *args, **kwargs: None,
            workspace_memory_snapshot=lambda workspace_id: {},
            delete_memory=lambda workspace_id, key: {},
            read_workspace_context_files=lambda: [],
            write_workspace_context_file=lambda filename, content: {},
            resolve_workspace_tenant_id=lambda workspace_id: f"tenant-for-{workspace_id}",
        )
        route_bindings = types.SimpleNamespace(
            serialize_run_snapshot=lambda run_id, run: {},
            delegate_run_children_callbacks={"delegate": True},
            auto_delegate_run_children_callbacks={"auto": True},
            retry_failed_delegation_callbacks={"retry": True},
            run_detail_callbacks={"detail": True},
            runs_history_callbacks={"history": True},
            submit_run_decision_callbacks={"decision": True},
            resolve_run_approval_callbacks={"approval": True},
            resume_waiting_run_callbacks={"resume": True},
            pause_run_callbacks={"pause": True},
        )
        server_module = types.SimpleNamespace(
            RUN_HISTORY_LOCK=object(),
            RUN_HISTORY=[],
            _history_item_matches=lambda *args, **kwargs: True,
            _summarize_history_item=lambda *args, **kwargs: {},
            RunDelegationRequest=type("RunDelegationRequest", (), {}),
            RunAutoDelegationRequest=type("RunAutoDelegationRequest", (), {}),
            RunDelegationRetryRequest=type("RunDelegationRetryRequest", (), {}),
            DecisionPayload=type("DecisionPayload", (), {}),
            ApprovalResolvePayload=type("ApprovalResolvePayload", (), {}),
            _get_pending_confirmation=lambda run: None,
            _parse_utc_ts=lambda value: value,
            _build_retry_child_payload=lambda child, relationship: {},
            _approval_correlation_id=lambda *args, **kwargs: "corr",
            _append_approval_audit=lambda *args, **kwargs: None,
            _set_pending_confirmation=lambda run, pending: None,
            normalize_agent_role=lambda value: str(value or ""),
            emit_log=lambda *args, **kwargs: None,
        )
        module_globals = {
            "_direct_chat_stream_response_services": lambda: {},
            "_stamp_request_owner": lambda *args, **kwargs: None,
            "_run_execution_services": lambda: {},
            "_run_routing_preview_services": lambda: {},
            "_usage_snapshots_for_user": lambda current_user: [],
            "_enforce_run_owner_access": lambda current_user, payload: None,
            "_current_user_is_privileged": lambda current_user: False,
            "_extract_run_owner_user_id": lambda payload: payload.get("user_id"),
            "_can_view_sensitive_run_payload": lambda current_user: True,
            "_limited_run_context_view": lambda payload: payload,
            "_limited_result_data_view": lambda payload: payload,
            "_resolve_local_execution_start_approval": lambda *args, **kwargs: {},
            "_utc_now": lambda: "now",
            "_utc_now_iso": lambda: "now-iso",
            "_run_thread_is_alive": lambda run: False,
            "_schedule_restored_run_resume": lambda run_id, run: True,
        }

        scheduler = runtime_route_registration_service.register_runtime_run_routes_from_api(
            object(),
            import_module=lambda name, fromlist=(): None,
            module_globals=module_globals,
            server_module=server_module,
            heartbeat_lock=threading.Lock(),
            heartbeat_scheduler=None,
            heartbeat_scheduler_refresher=lambda value: scheduler_holder.setdefault("value", value),
            load_webhook_triggers=lambda: calls.setdefault("load_webhooks", 0) or calls.__setitem__("load_webhooks", 1),
            heartbeat_scheduler_class=_DummyScheduler,
            runtime_heartbeat_service=types.SimpleNamespace(
                build_heartbeat_run_callback=lambda **kwargs: ("run", kwargs),
                build_heartbeat_notify_callback=lambda **kwargs: ("notify", kwargs),
                ensure_heartbeat_scheduler_started=lambda scheduler: scheduler,
                heartbeat_scheduler=lambda *, lock, scheduler: ("heartbeat", lock, scheduler),
            ),
            runtime_route_bootstrap_service=types.SimpleNamespace(
                import_runtime_run_route_dependencies=lambda **kwargs: deps,
                build_runtime_run_route_bootstrap_callbacks=lambda **kwargs: types.SimpleNamespace(
                    heartbeat_run_callback=("run", kwargs),
                    heartbeat_notify_callback=("notify", kwargs),
                ),
                ensure_runtime_run_route_bootstrap=lambda **kwargs: _DummyScheduler(
                    interval_seconds=kwargs["heartbeat_scheduler_factory"]().kwargs["interval_seconds"],
                    workspace_id=kwargs["heartbeat_scheduler_factory"]().kwargs["workspace_id"],
                ),
            ),
            runtime_route_binding_service=types.SimpleNamespace(
                build_runtime_route_bindings=lambda **kwargs: route_bindings,
            ),
            runtime_route_registry_service=types.SimpleNamespace(
                register_runtime_run_routes=lambda app, **kwargs: calls.setdefault("registry_kwargs", kwargs),
            ),
            depends=lambda dependency: dependency,
            request_class=object(),
            event_source_response_class=object(),
            require_api_key=object(),
            require_admin_api_key=object(),
            refresh_server_exports=lambda: None,
            match_webhook_trigger_fn=lambda workspace_id, request_url: {},
            webhook_triggers={},
            webhook_trigger_lock=object(),
            persist_webhook_triggers_locked=lambda: None,
            single_agent_mode=False,
            runs={},
            iter_logs_for_run=lambda run_id: [],
            get_replay_payload=lambda run_id: {},
            execute_run_start_request_via_turn_runtime=lambda *args, **kwargs: {},
            execute_system_run_start_request_via_turn_runtime=lambda *args, **kwargs: {},
            enforce_run_owner_access=lambda current_user, payload: None,
        )

        self.assertIsInstance(scheduler, _DummyScheduler)
        self.assertIs(scheduler_holder["value"], scheduler)
        self.assertIs(calls["registry_kwargs"]["run_start_request_class"], deps.run_start_request_class)
        self.assertEqual(calls["registry_kwargs"]["delegate_run_children_callbacks"], {"delegate": True})


if __name__ == "__main__":
    unittest.main()

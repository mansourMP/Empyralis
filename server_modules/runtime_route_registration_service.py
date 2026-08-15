from __future__ import annotations

from dataclasses import dataclass
import queue
import threading
from typing import Any
import uuid

from server_modules import bounded_scheduler_service
from server_modules.heartbeat import HeartbeatScheduler
from server_modules.auth import enforce_workspace_access
from server_modules import local_queue
from server_modules.run_execution_handle import attach_execution_handle
from server_modules import runtime_history_service
from server_modules import runtime_run_control_service
from server_modules import runtime_run_delegation_service
from server_modules import runtime_run_query_service
from server_modules.run_service import build_run_precheck_result as _build_run_precheck_result
from server_modules.run_service import build_run_routing_preview as _build_run_routing_preview
from server_modules import runtime_heartbeat_service as _runtime_heartbeat_service
from server_modules import runtime_route_bootstrap_service as _runtime_route_bootstrap_service
from server_modules import runtime_route_registry_service as _runtime_route_registry_service
from server_modules import runtime_run_detail_service as _runtime_run_detail_service
from server_modules import security_audit_service
from server_modules.usage_reporting import aggregate_usage_summary as _aggregate_usage_summary
from server_modules.usage_reporting import list_usage_runs as _list_usage_runs


def _module_global(module_globals: dict[str, Any], name: str) -> Any:
    return module_globals[name]


@dataclass(frozen=True)
class RuntimeRouteBindings:
    serialize_run_snapshot: Any
    delegate_run_children_callbacks: dict[str, Any]
    auto_delegate_run_children_callbacks: dict[str, Any]
    retry_failed_delegation_callbacks: dict[str, Any]
    run_detail_callbacks: dict[str, Any]
    runs_history_callbacks: dict[str, Any]
    resume_waiting_run_callbacks: dict[str, Any]
    pause_run_callbacks: dict[str, Any]


def build_runtime_route_bindings(
    *,
    late_server_export,
    enforce_run_owner_access,
    normalize_agent_role,
    execute_system_run_start_request_via_turn_runtime,
    stamp_request_owner_fn,
    run_execution_services,
    can_view_sensitive_run_payload,
    limited_run_context_view,
    limited_result_data_view_fn,
    get_pending_confirmation_fn,
    build_archived_run_detail_response,
    build_live_run_detail_response,
    refresh_server_exports,
    run_history_lock,
    run_history,
    history_item_matches,
    current_user_is_privileged,
    extract_run_owner_user_id,
    resolve_workspace_tenant_id,
    summarize_history_item,
    parse_utc_ts,
    build_retry_child_payload,
    approval_correlation_id,
    append_approval_audit,
    resolve_local_execution_start_approval,
    set_pending_confirmation,
    utc_now,
    utc_now_iso,
    run_thread_is_alive,
    emit_log,
    schedule_restored_run_resume,
) -> RuntimeRouteBindings:
    serialize_run_snapshot = late_server_export("_serialize_run_snapshot")

    def _ensure_live_run_handle(run_id: str, run_record: dict[str, Any]) -> dict[str, Any] | None:
        if not isinstance(run_record, dict):
            return None
        try:
            runs_by_id = late_server_export("runs")
            run_queue_index = late_server_export("RUN_QUEUE_INDEX")
            persist_live_run_state = late_server_export("_persist_live_run_state")
        except Exception:
            return None
        existing = runs_by_id.get(run_id) if isinstance(runs_by_id, dict) else None
        if isinstance(existing, dict):
            return existing
        restored = attach_execution_handle(
            dict(run_record),
            log_queue=queue.Queue(),
            input_queue=queue.Queue(),
            started_mono=None,
            finished_mono=None,
            first_value_mono=None,
            hitl_wait_start_mono=None,
            thread_id=None,
            event_seq=int(run_record.get("_event_seq") or 0),
        )
        if isinstance(runs_by_id, dict):
            runs_by_id[run_id] = restored
        log_queue = restored.get("logs")
        if log_queue is not None and isinstance(run_queue_index, dict):
            run_queue_index[id(log_queue)] = run_id
        try:
            persist_live_run_state(run_id, restored)
        except Exception:
            pass
        return restored

    delegate_run_children_callbacks = runtime_run_delegation_service.build_delegate_run_children_callbacks(
        lookup_run_snapshot=late_server_export("_lookup_run_snapshot"),
        enforce_run_owner_access=enforce_run_owner_access,
        normalize_agent_role=normalize_agent_role,
        build_delegated_run_request=late_server_export("_build_delegated_run_request"),
        execute_system_run_start_request_via_turn_runtime=execute_system_run_start_request_via_turn_runtime,
        stamp_request_owner_fn=stamp_request_owner_fn,
        run_execution_services=run_execution_services,
        normalize_run_id_token=late_server_export("_normalize_run_id_token"),
        refresh_parent_delegation_state=late_server_export("_refresh_parent_delegation_state"),
    )
    auto_delegate_run_children_callbacks = runtime_run_delegation_service.build_auto_delegate_run_children_callbacks(
        lookup_run_snapshot=late_server_export("_lookup_run_snapshot"),
        enforce_run_owner_access=enforce_run_owner_access,
        normalize_agent_role=normalize_agent_role,
        build_auto_delegation_plan=late_server_export("_build_auto_delegation_plan"),
        emit_auto_delegation_routing_log=late_server_export("_emit_auto_delegation_routing_log"),
        build_delegated_run_request=late_server_export("_build_delegated_run_request"),
        execute_system_run_start_request_via_turn_runtime=execute_system_run_start_request_via_turn_runtime,
        stamp_request_owner_fn=stamp_request_owner_fn,
        run_execution_services=run_execution_services,
        normalize_run_id_token=late_server_export("_normalize_run_id_token"),
        refresh_parent_delegation_state=late_server_export("_refresh_parent_delegation_state"),
    )
    retry_failed_delegation_callbacks = runtime_run_delegation_service.build_retry_failed_delegation_callbacks(
        lookup_run_snapshot=late_server_export("_lookup_run_snapshot"),
        enforce_run_owner_access=enforce_run_owner_access,
        normalize_agent_role=normalize_agent_role,
        find_run_relationships=late_server_export("_find_run_relationships"),
        normalize_run_id_token=late_server_export("_normalize_run_id_token"),
        parse_utc_ts=parse_utc_ts,
        build_retry_child_payload=build_retry_child_payload,
        build_delegated_run_request=late_server_export("_build_delegated_run_request"),
        execute_system_run_start_request_via_turn_runtime=execute_system_run_start_request_via_turn_runtime,
        stamp_request_owner_fn=stamp_request_owner_fn,
        run_execution_services=run_execution_services,
        refresh_parent_delegation_state=late_server_export("_refresh_parent_delegation_state"),
    )
    run_detail_callbacks = runtime_run_query_service.build_default_run_detail_response_callbacks(
        import_module=__import__,
        enforce_run_owner_access=enforce_run_owner_access,
        can_view_sensitive_run_payload=can_view_sensitive_run_payload,
        limited_run_context_view=limited_run_context_view,
        limited_result_data_view_fn=limited_result_data_view_fn,
        get_pending_confirmation_fn=get_pending_confirmation_fn,
        build_archived_run_detail_response=build_archived_run_detail_response,
        build_live_run_detail_response=build_live_run_detail_response,
        build_run_browser_checkpoint_payload=_runtime_run_detail_service.build_run_browser_checkpoint_payload,
        build_run_browser_session_payload=_runtime_run_detail_service.build_run_browser_session_payload,
    )
    runs_history_callbacks = runtime_history_service.build_runs_history_callbacks(
        refresh_server_exports=refresh_server_exports,
        run_history_lock=run_history_lock,
        run_history=run_history,
        history_item_matches=history_item_matches,
        current_user_is_privileged=current_user_is_privileged,
        extract_run_owner_user_id=extract_run_owner_user_id,
        enforce_workspace_access=enforce_workspace_access,
        resolve_workspace_tenant_id=resolve_workspace_tenant_id,
        normalize_run_id_token=late_server_export("_normalize_run_id_token"),
        summarize_history_item=summarize_history_item,
    )
    resume_waiting_run_callbacks = runtime_run_control_service.build_resume_waiting_run_callbacks(
        serialize_run_snapshot=serialize_run_snapshot,
        enforce_run_owner_access=enforce_run_owner_access,
        get_pending_confirmation=get_pending_confirmation_fn,
        begin_run_pending_confirmation=late_server_export("_begin_run_pending_confirmation"),
        emit_log=emit_log,
        schedule_restored_run_resume=schedule_restored_run_resume,
    )
    pause_run_callbacks = runtime_run_control_service.build_pause_run_callbacks(
        serialize_run_snapshot=serialize_run_snapshot,
        enforce_run_owner_access=enforce_run_owner_access,
        pause_local_run=lambda **kwargs: local_queue.handle_pause_local_run(
            uuid.UUID(str(kwargs.get("run_id"))),
            local_queue.LocalRunPausePayload(
                worker_id=kwargs.get("worker_id"),
                result_text=kwargs.get("result_text"),
                result_data=kwargs.get("result_data"),
                browser_checkpoint=kwargs.get("browser_checkpoint"),
                wait_reason=kwargs.get("wait_reason"),
            ),
        ),
    )

    return RuntimeRouteBindings(
        serialize_run_snapshot=serialize_run_snapshot,
        delegate_run_children_callbacks=delegate_run_children_callbacks,
        auto_delegate_run_children_callbacks=auto_delegate_run_children_callbacks,
        retry_failed_delegation_callbacks=retry_failed_delegation_callbacks,
        run_detail_callbacks=run_detail_callbacks,
        runs_history_callbacks=runs_history_callbacks,
        resume_waiting_run_callbacks=resume_waiting_run_callbacks,
        pause_run_callbacks=pause_run_callbacks,
    )


_WAKE_REQUEST_SCANNER_LOCK = threading.Lock()
_WAKE_REQUEST_SCANNER_STATE: dict[str, Any] = {"started": False}


def _ensure_wake_request_scanner_started(run_workspace_heartbeat: Any) -> None:
    """Starts the cross-workspace wake-request scanner exactly once per
    process, independent of the legacy single-workspace HeartbeatScheduler's
    `workspace_id` (frequently unset -- see HeartbeatScheduler's own
    scope_missing short-circuit in heartbeat.py). Scheduled wake-up execution
    must not depend on that value ever being configured: it was landing on
    `None` here on every boot, so due wake requests were written but never
    claimed. run_workspace_heartbeat is the same fully-wired, tier-safe
    claim -> execute -> finalize callback the legacy scheduler already uses
    (see bootstrap_callbacks.heartbeat_run_callback below) -- this just makes
    sure it actually gets invoked, per workspace that has due work, instead
    of only for one hardcoded-by-omission workspace that never existed."""
    if not bounded_scheduler_service.wake_request_scan_enabled():
        return
    with _WAKE_REQUEST_SCANNER_LOCK:
        if _WAKE_REQUEST_SCANNER_STATE["started"]:
            return
        stop_event = threading.Event()
        thread = threading.Thread(
            target=bounded_scheduler_service.run_wake_request_scan_forever,
            kwargs={
                "run_workspace_heartbeat": run_workspace_heartbeat,
                "stop_event": stop_event,
            },
            daemon=True,
            name="wake-request-scanner",
        )
        thread.start()
        _WAKE_REQUEST_SCANNER_STATE["started"] = True
        _WAKE_REQUEST_SCANNER_STATE["stop_event"] = stop_event
        _WAKE_REQUEST_SCANNER_STATE["thread"] = thread


def register_runtime_run_routes_from_api(
    app,
    *,
    import_module,
    module_globals,
    server_module,
    heartbeat_lock: Any,
    heartbeat_scheduler: Any,
    heartbeat_scheduler_refresher,
    load_webhook_triggers,
    heartbeat_scheduler_class=HeartbeatScheduler,
    interval_seconds: int = 30 * 60,
    workspace_id: str | None = None,
    runtime_heartbeat_service=_runtime_heartbeat_service,
    runtime_route_bootstrap_service=_runtime_route_bootstrap_service,
    runtime_route_binding_service=None,
    runtime_route_registry_service=_runtime_route_registry_service,
    runtime_run_detail_service=_runtime_run_detail_service,
    depends: Any,
    request_class: Any,
    event_source_response_class: Any,
    require_api_key: Any,
    require_admin_api_key: Any,
    refresh_server_exports,
    match_webhook_trigger_fn,
    webhook_triggers,
    webhook_trigger_lock,
    persist_webhook_triggers_locked,
    single_agent_mode: bool,
    runs: dict[str, Any],
    iter_logs_for_run,
    get_replay_payload,
    direct_chat_stream_response_services=None,
    execute_run_start_request_via_turn_runtime,
    execute_system_run_start_request_via_turn_runtime,
    stamp_request_owner_fn=None,
    run_execution_services=None,
    build_run_routing_preview=None,
    build_run_precheck_result=None,
    run_routing_preview_services=None,
    usage_snapshots_for_user_fn=None,
    aggregate_usage_summary_fn=None,
    list_usage_runs_fn=None,
    enforce_run_owner_access=None,
    current_user_is_privileged=None,
    extract_run_owner_user_id=None,
    summarize_history_item=None,
    normalize_agent_role=None,
    can_view_sensitive_run_payload=None,
    limited_run_context_view=None,
    limited_result_data_view_fn=None,
    get_pending_confirmation_fn=None,
    parse_utc_ts=None,
    build_retry_child_payload=None,
    approval_correlation_id=None,
    append_approval_audit=None,
    resolve_local_execution_start_approval=None,
    set_pending_confirmation=None,
    utc_now=None,
    utc_now_iso=None,
    run_thread_is_alive=None,
    emit_log=None,
    schedule_restored_run_resume=None,
) -> Any:
    direct_chat_stream_response_services = direct_chat_stream_response_services or _module_global(
        module_globals, "_direct_chat_stream_response_services"
    )
    stamp_request_owner_fn = stamp_request_owner_fn or _module_global(module_globals, "_stamp_request_owner")
    run_execution_services = run_execution_services or _module_global(module_globals, "_run_execution_services")
    build_run_routing_preview = build_run_routing_preview or _build_run_routing_preview
    build_run_precheck_result = build_run_precheck_result or _build_run_precheck_result
    run_routing_preview_services = run_routing_preview_services or _module_global(
        module_globals, "_run_routing_preview_services"
    )
    usage_snapshots_for_user_fn = usage_snapshots_for_user_fn or _module_global(
        module_globals, "_usage_snapshots_for_user"
    )
    aggregate_usage_summary_fn = aggregate_usage_summary_fn or _aggregate_usage_summary
    list_usage_runs_fn = list_usage_runs_fn or _list_usage_runs
    enforce_run_owner_access = enforce_run_owner_access or _module_global(
        module_globals, "_enforce_run_owner_access"
    )
    current_user_is_privileged = current_user_is_privileged or _module_global(
        module_globals, "_current_user_is_privileged"
    )
    extract_run_owner_user_id = extract_run_owner_user_id or _module_global(
        module_globals, "_extract_run_owner_user_id"
    )
    normalize_agent_role = normalize_agent_role or server_module.normalize_agent_role
    summarize_history_item = summarize_history_item or server_module._summarize_history_item
    can_view_sensitive_run_payload = can_view_sensitive_run_payload or _module_global(
        module_globals, "_can_view_sensitive_run_payload"
    )
    limited_run_context_view = limited_run_context_view or _module_global(
        module_globals, "_limited_run_context_view"
    )
    limited_result_data_view_fn = limited_result_data_view_fn or _module_global(
        module_globals, "_limited_result_data_view"
    )
    get_pending_confirmation_fn = get_pending_confirmation_fn or server_module._get_pending_confirmation
    parse_utc_ts = parse_utc_ts or server_module._parse_utc_ts
    build_retry_child_payload = build_retry_child_payload or server_module._build_retry_child_payload
    approval_correlation_id = approval_correlation_id or server_module._approval_correlation_id
    append_approval_audit = append_approval_audit or server_module._append_approval_audit
    resolve_local_execution_start_approval = resolve_local_execution_start_approval or _module_global(
        module_globals, "_resolve_local_execution_start_approval"
    )
    set_pending_confirmation = set_pending_confirmation or server_module._set_pending_confirmation
    utc_now = utc_now or _module_global(module_globals, "_utc_now")
    utc_now_iso = utc_now_iso or _module_global(module_globals, "_utc_now_iso")
    run_thread_is_alive = run_thread_is_alive or _module_global(module_globals, "_run_thread_is_alive")
    emit_log = emit_log or server_module.emit_log
    schedule_restored_run_resume = schedule_restored_run_resume or _module_global(
        module_globals, "_schedule_restored_run_resume"
    )

    deps = runtime_route_bootstrap_service.import_runtime_run_route_dependencies(
        import_module=import_module,
        module_globals=module_globals,
        server_module=server_module,
    )

    bootstrap_callbacks = runtime_route_bootstrap_service.build_runtime_run_route_bootstrap_callbacks(
        run_start_request_class=deps.run_start_request_class,
        build_inbound_agent_turn_request=deps.build_inbound_agent_turn_request,
        trigger_pending_heartbeat_schedules=deps.trigger_pending_heartbeat_schedules,
        execute_system_run_start_request_via_turn_runtime=execute_system_run_start_request_via_turn_runtime,
        execute_system_agent_turn=deps.execute_system_agent_turn,
        stamp_request_owner_fn=stamp_request_owner_fn,
        run_execution_services=run_execution_services,
        handle_telegram_send_message=deps.handle_telegram_send_message,
        build_heartbeat_run_callback=runtime_heartbeat_service.build_heartbeat_run_callback,
        build_heartbeat_notify_callback=runtime_heartbeat_service.build_heartbeat_notify_callback,
        resolve_workspace_tenant_id=deps.resolve_workspace_tenant_id,
        heartbeat_workspace_id=workspace_id,
    )

    heartbeat_scheduler = runtime_route_bootstrap_service.ensure_runtime_run_route_bootstrap(
        heartbeat_lock=heartbeat_lock,
        heartbeat_scheduler=heartbeat_scheduler,
        heartbeat_scheduler_factory=lambda: heartbeat_scheduler_class(
            interval_seconds=interval_seconds,
            workspace_id=workspace_id,
            run_callback=bootstrap_callbacks.heartbeat_run_callback,
            notify_callback=bootstrap_callbacks.heartbeat_notify_callback,
        ),
        ensure_heartbeat_scheduler_started=runtime_heartbeat_service.ensure_heartbeat_scheduler_started,
        load_webhook_triggers=load_webhook_triggers,
    )
    heartbeat_scheduler_refresher(heartbeat_scheduler)
    if heartbeat_scheduler is not None and workspace_id:
        bounded_scheduler_service.register_ambient_monitor(
            workspace_id=workspace_id,
            trigger_now=heartbeat_scheduler.trigger_now,
            status=heartbeat_scheduler.status,
        )
    _ensure_wake_request_scanner_started(bootstrap_callbacks.heartbeat_run_callback)

    if runtime_route_binding_service is None:
        runtime_route_binding_service = type(
            "_RuntimeRouteBindingService",
            (),
            {"build_runtime_route_bindings": staticmethod(build_runtime_route_bindings)},
        )()

    route_bindings = runtime_route_binding_service.build_runtime_route_bindings(
        late_server_export=lambda name: getattr(server_module, name),
        enforce_run_owner_access=enforce_run_owner_access,
        normalize_agent_role=normalize_agent_role,
        execute_system_run_start_request_via_turn_runtime=execute_system_run_start_request_via_turn_runtime,
        stamp_request_owner_fn=stamp_request_owner_fn,
        run_execution_services=run_execution_services,
        can_view_sensitive_run_payload=can_view_sensitive_run_payload,
        limited_run_context_view=limited_run_context_view,
        limited_result_data_view_fn=limited_result_data_view_fn,
        get_pending_confirmation_fn=get_pending_confirmation_fn,
        build_archived_run_detail_response=runtime_run_detail_service.build_archived_run_detail_response,
        build_live_run_detail_response=runtime_run_detail_service.build_live_run_detail_response,
        refresh_server_exports=refresh_server_exports,
        run_history_lock=server_module.RUN_HISTORY_LOCK,
        run_history=server_module.RUN_HISTORY,
        history_item_matches=server_module._history_item_matches,
        current_user_is_privileged=current_user_is_privileged,
        extract_run_owner_user_id=extract_run_owner_user_id,
        resolve_workspace_tenant_id=deps.resolve_workspace_tenant_id,
        summarize_history_item=summarize_history_item,
        parse_utc_ts=parse_utc_ts,
        build_retry_child_payload=build_retry_child_payload,
        approval_correlation_id=approval_correlation_id,
        append_approval_audit=append_approval_audit,
        resolve_local_execution_start_approval=resolve_local_execution_start_approval,
        set_pending_confirmation=set_pending_confirmation,
        utc_now=utc_now,
        utc_now_iso=utc_now_iso,
        run_thread_is_alive=run_thread_is_alive,
        emit_log=emit_log,
        schedule_restored_run_resume=schedule_restored_run_resume,
    )

    runtime_route_registry_service.register_runtime_run_routes(
        app,
        depends=depends,
        request_class=request_class,
        event_source_response_class=event_source_response_class,
        require_api_key=require_api_key,
        require_admin_api_key=require_admin_api_key,
        refresh_server_exports=refresh_server_exports,
        heartbeat_scheduler=lambda: runtime_heartbeat_service.heartbeat_scheduler(
            lock=heartbeat_lock,
            scheduler=heartbeat_scheduler,
        ),
        load_webhook_triggers=load_webhook_triggers,
        persist_webhook_triggers_locked=persist_webhook_triggers_locked,
        match_webhook_trigger_fn=match_webhook_trigger_fn,
        webhook_triggers=webhook_triggers,
        webhook_trigger_lock=webhook_trigger_lock,
        run_start_request_class=deps.run_start_request_class,
        run_delegation_request_class=server_module.RunDelegationRequest,
        run_auto_delegation_request_class=server_module.RunAutoDelegationRequest,
        run_delegation_retry_request_class=server_module.RunDelegationRetryRequest,
        decision_payload_class=server_module.DecisionPayload,
        workspace_memory_snapshot=deps.workspace_memory_snapshot,
        delete_memory=deps.delete_memory,
        read_workspace_context_files=deps.read_workspace_context_files,
        write_workspace_context_file=deps.write_workspace_context_file,
        single_agent_mode=single_agent_mode,
        runs=runs,
        serialize_run_snapshot=route_bindings.serialize_run_snapshot,
        iter_logs_for_run=iter_logs_for_run,
        get_replay_payload=get_replay_payload,
        direct_chat_stream_response_services=direct_chat_stream_response_services,
        execute_run_start_request_via_turn_runtime=execute_run_start_request_via_turn_runtime,
        execute_system_run_start_request_via_turn_runtime=execute_system_run_start_request_via_turn_runtime,
        stamp_request_owner_fn=stamp_request_owner_fn,
        run_execution_services=run_execution_services,
        build_run_routing_preview=build_run_routing_preview,
        build_run_precheck_result=build_run_precheck_result,
        run_routing_preview_services=run_routing_preview_services,
        delegate_run_children_callbacks=route_bindings.delegate_run_children_callbacks,
        auto_delegate_run_children_callbacks=route_bindings.auto_delegate_run_children_callbacks,
        retry_failed_delegation_callbacks=route_bindings.retry_failed_delegation_callbacks,
        run_detail_callbacks=route_bindings.run_detail_callbacks,
        runs_history_callbacks=route_bindings.runs_history_callbacks,
        usage_snapshots_for_user_fn=usage_snapshots_for_user_fn,
        aggregate_usage_summary_fn=aggregate_usage_summary_fn,
        list_usage_runs_fn=list_usage_runs_fn,
        resume_waiting_run_callbacks=route_bindings.resume_waiting_run_callbacks,
        pause_run_callbacks=route_bindings.pause_run_callbacks,
        enforce_run_owner_access=enforce_run_owner_access,
    )
    return heartbeat_scheduler

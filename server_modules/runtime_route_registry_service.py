from __future__ import annotations

import uuid
from typing import Any, Optional

from fastapi import HTTPException, Request

from server_modules.auth import enforce_workspace_access as _enforce_workspace_access
from server_modules.direct_chat_stream_response_service import build_direct_chat_stream_response as _build_direct_chat_stream_response
from server_modules import runtime_heartbeat_service as _runtime_heartbeat_service
from server_modules import runtime_history_service as _runtime_history_service
from server_modules import runtime_request_service as _runtime_request_service
from server_modules import runtime_route_request_handlers_service as _runtime_route_request_handlers_service
from server_modules import runtime_route_run_handlers_service as _runtime_route_run_handlers_service
from server_modules import runtime_run_control_service as _runtime_run_control_service
from server_modules import runtime_run_delegation_service as _runtime_run_delegation_service
from server_modules import runtime_run_entry_service as _runtime_run_entry_service
from server_modules import runtime_run_query_service as _runtime_run_query_service
from server_modules import runtime_run_replay_service as _runtime_run_replay_service
from server_modules import runtime_usage_service as _runtime_usage_service
from server_modules import runtime_webhook_trigger_service as _runtime_webhook_trigger_service
from server_modules import runtime_workspace_service as _runtime_workspace_service
from server_modules import rust_runtime_kernel_client
from server_modules import entitlements_service
from server_modules import safe_mode_service
from server_modules import run_state_repository



def _run_api_scope_from_record(run: Any, run_record: Any) -> dict[str, str]:
    payload = run if isinstance(run, dict) else run_record if isinstance(run_record, dict) else {}
    context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    return {
        "workspace_id": str(
            payload.get("workspace_id")
            or context.get("workspace_id")
            or metadata.get("workspace_id")
            or "default"
        ).strip() or "default",
        "tenant_id": str(
            payload.get("tenant_id")
            or context.get("tenant_id")
            or metadata.get("tenant_id")
            or "default"
        ).strip() or "default",
        "run_status": str(
            payload.get("status")
            or payload.get("state")
            or context.get("status")
            or metadata.get("status")
            or ""
        ).strip(),
    }


def _enforce_registered_run_api_decision(
    *,
    operation: str,
    run_id: str,
    current_user: Any,
    run: Any,
    run_record: Any,
    user_role: str = "member",
    body: Any = None,
) -> dict[str, Any]:
    scope = _run_api_scope_from_record(run, run_record)
    decision_body = {"user_id": str((current_user or {}).get("user_id") or "").strip() or "user"}
    if isinstance(body, dict):
        decision_body.update(body)
    rust_payload = {
        "operation": operation,
        "workspace_id": scope["workspace_id"],
        "tenant_id": scope["tenant_id"],
        "run_id": str(run_id or "").strip(),
        "run_status": scope["run_status"],
        "user_role": user_role,
        "workspace_access_denied": False,
        "owner_access_denied": False,
        "kill_switch_active": False,
        "history_window_allowed": True,
        "body": decision_body,
    }
    try:
        decision = rust_runtime_kernel_client.run_runtime_kernel_enforced("run-api-decision", rust_payload, allow_approval_required=True)
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        raise HTTPException(status_code=409, detail=f"Rust run-api gate blocked {operation}: {exc.reason}") from exc
    allowed_next_actions = {
        "approve_run": {"resolve_run_approval"},
        "resume_run": {"resume_run"},
        "pause_run": {"pause_run"},
        "get_run": {"get_run", "request_owner_approval"},
    }.get(operation)
    next_action = str(decision.get("next_action") or "").strip()
    if allowed_next_actions and next_action not in allowed_next_actions:
        raise HTTPException(
            status_code=423,
            detail=f"Rust run-api gate returned unexpected next_action for {operation}: {next_action or 'missing'}",
        )
    return decision


def _payload_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        dumped = value.model_dump()
        return dumped if isinstance(dumped, dict) else {}
    if hasattr(value, "dict"):
        dumped = value.dict()
        return dumped if isinstance(dumped, dict) else {}
    return {}


def _run_id_from_record(run: Any, run_record: Any) -> str:
    for payload in (run, run_record):
        if isinstance(payload, dict):
            value = payload.get("run_id") or payload.get("id")
            if value:
                return str(value).strip()
    return ""



def register_runtime_run_routes(
    app,
    *,
    depends: Any,
    request_class: Any,
    event_source_response_class: Any,
    require_api_key: Any,
    require_admin_api_key: Any,
    refresh_server_exports,
    heartbeat_scheduler,
    load_webhook_triggers,
    persist_webhook_triggers_locked,
    match_webhook_trigger_fn,
    webhook_triggers,
    webhook_trigger_lock,
    run_start_request_class: Any,
    run_delegation_request_class: Any,
    run_auto_delegation_request_class: Any,
    run_delegation_retry_request_class: Any,
    decision_payload_class: Any,
    workspace_memory_snapshot,
    delete_memory,
    read_workspace_context_files,
    write_workspace_context_file,
    single_agent_mode: bool,
    runs: dict[str, Any],
    serialize_run_snapshot,
    iter_logs_for_run,
    get_replay_payload,
    direct_chat_stream_response_services,
    execute_run_start_request_via_turn_runtime,
    execute_system_run_start_request_via_turn_runtime,
    stamp_request_owner_fn,
    run_execution_services,
    build_run_routing_preview,
    build_run_precheck_result,
    run_routing_preview_services,
    delegate_run_children_callbacks,
    auto_delegate_run_children_callbacks,
    retry_failed_delegation_callbacks,
    run_detail_callbacks,
    runs_history_callbacks,
    usage_snapshots_for_user_fn,
    aggregate_usage_summary_fn,
    list_usage_runs_fn,
    resume_waiting_run_callbacks,
    pause_run_callbacks,
    enforce_run_owner_access,
    runtime_workspace_service=_runtime_workspace_service,
    runtime_heartbeat_service=_runtime_heartbeat_service,
    runtime_route_request_handlers_service=_runtime_route_request_handlers_service,
    runtime_route_run_handlers_service=_runtime_route_run_handlers_service,
    runtime_request_service=_runtime_request_service,
    runtime_webhook_trigger_service=_runtime_webhook_trigger_service,
    runtime_run_delegation_service=_runtime_run_delegation_service,
    runtime_run_query_service=_runtime_run_query_service,
    runtime_run_entry_service=_runtime_run_entry_service,
    runtime_run_replay_service=_runtime_run_replay_service,
    runtime_run_control_service=_runtime_run_control_service,
    runtime_history_service=_runtime_history_service,
    runtime_usage_service=_runtime_usage_service,
    build_direct_chat_stream_response=_build_direct_chat_stream_response,
    enforce_workspace_access_fn=_enforce_workspace_access,
) -> None:
    viewer_dependency = require_api_key
    member_dependency = globals().get("require_member_api_key", None)
    if member_dependency is None:
        try:
            from server_modules.runtime_common import require_member_api_key as member_dependency  # type: ignore
        except Exception:
            member_dependency = require_api_key

    def _payload_bool(payload: dict[str, Any], key: str, *, default: bool = False) -> bool:
        value = payload.get(key)
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        text = str(value or "").strip().lower()
        if not text:
            return default
        return text in {"1", "true", "yes", "on", "enabled"}

    @app.get("/memory/{workspace_id}", dependencies=[depends(require_api_key)])
    async def list_workspace_memory(
        workspace_id: str,
        current_user=depends(require_api_key),
    ):
        refresh_server_exports()
        resolved_workspace_id = enforce_workspace_access_fn(
            current_user,
            workspace_id,
            minimum_role="viewer",
        )
        return runtime_workspace_service.list_workspace_memory_payload(
            resolved_workspace_id,
            workspace_memory_snapshot=workspace_memory_snapshot,
        )

    @app.delete("/memory/{workspace_id}/{key}", dependencies=[depends(require_api_key)])
    async def delete_workspace_memory(
        workspace_id: str,
        key: str,
        current_user=depends(require_api_key),
    ):
        refresh_server_exports()
        resolved_workspace_id = enforce_workspace_access_fn(
            current_user,
            workspace_id,
            minimum_role="member",
        )
        return runtime_workspace_service.delete_workspace_memory_payload(
            resolved_workspace_id,
            key,
            delete_memory=delete_memory,
        )

    @app.get("/workspace/context-files", dependencies=[depends(require_api_key)])
    async def get_workspace_context_files(current_user=depends(require_api_key)):
        refresh_server_exports()
        return runtime_workspace_service.workspace_context_files_payload(
            read_workspace_context_files=read_workspace_context_files,
        )

    @app.post("/workspace/context-files/{filename}", dependencies=[depends(require_api_key)])
    async def update_workspace_context_file(
        filename: str,
        request: Request,
        current_user=depends(require_api_key),
    ):
        refresh_server_exports()
        return await runtime_route_request_handlers_service.update_workspace_context_file_response(
            filename,
            request=request,
            read_json_payload=runtime_request_service.read_json_payload,
            update_workspace_context_file_payload=runtime_workspace_service.update_workspace_context_file_payload,
            write_workspace_context_file=write_workspace_context_file,
        )

    @app.get("/heartbeat/status", dependencies=[depends(require_api_key)])
    async def get_heartbeat_status(current_user=depends(require_api_key)):
        refresh_server_exports()
        return runtime_heartbeat_service.heartbeat_status_payload(
            scheduler=heartbeat_scheduler(),
        )

    @app.post("/heartbeat/trigger", dependencies=[depends(require_api_key)])
    async def trigger_heartbeat(current_user=depends(require_api_key)):
        refresh_server_exports()
        return runtime_route_request_handlers_service.trigger_heartbeat_response(
            heartbeat_scheduler=heartbeat_scheduler,
            trigger_heartbeat_payload=runtime_heartbeat_service.trigger_heartbeat_payload,
        )

    @app.post("/admin/kill-switch", dependencies=[depends(require_admin_api_key)])
    async def set_kill_switch(request: Request, current_user=depends(require_admin_api_key)):
        refresh_server_exports()
        payload = await runtime_request_service.read_json_object_payload(
            request,
            invalid_detail="Kill switch body must be an object.",
        )
        return {
            "ok": True,
            "result": safe_mode_service.set_kill_switch(
                scope=str(payload.get("scope") or "global").strip().lower() or "global",
                enabled=_payload_bool(payload, "enabled"),
                reason=str(payload.get("reason") or "").strip(),
                workspace_id=str(payload.get("workspace_id") or "").strip() or None,
                machine_id=str(payload.get("machine_id") or "").strip() or None,
                capability_id=str(payload.get("capability_id") or "").strip() or None,
                metadata=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else None,
            ),
            "state": safe_mode_service.state_snapshot(),
        }

    @app.post("/admin/safe-mode", dependencies=[depends(require_admin_api_key)])
    async def set_safe_mode(request: Request, current_user=depends(require_admin_api_key)):
        refresh_server_exports()
        payload = await runtime_request_service.read_json_object_payload(
            request,
            invalid_detail="Safe mode body must be an object.",
        )
        return {
            "ok": True,
            "result": safe_mode_service.set_safe_mode(
                enabled=_payload_bool(payload, "enabled"),
                reason=str(payload.get("reason") or "").strip(),
                workspace_id=str(payload.get("workspace_id") or "").strip() or None,
                machine_id=str(payload.get("machine_id") or "").strip() or None,
                metadata=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else None,
            ),
            "state": safe_mode_service.state_snapshot(),
        }

    @app.post("/webhooks/register", dependencies=[depends(require_api_key)])
    async def register_webhook_trigger(
        request: Request,
        current_user=depends(require_api_key),
    ):
        refresh_server_exports()
        return await runtime_route_request_handlers_service.register_webhook_trigger_response(
            request=request,
            current_user=current_user,
            load_webhook_triggers=load_webhook_triggers,
            read_json_payload=runtime_request_service.read_json_payload,
            enforce_workspace_access=enforce_workspace_access_fn,
            register_webhook_trigger_payload=runtime_webhook_trigger_service.register_webhook_trigger_payload,
            uuid_factory=uuid.uuid4,
            build_webhook_trigger_fn=runtime_webhook_trigger_service.build_webhook_trigger,
            triggers=webhook_triggers,
            lock=webhook_trigger_lock,
            persist_webhook_triggers_locked=persist_webhook_triggers_locked,
        )

    @app.post("/webhooks/ingest/{workspace_id}", dependencies=[depends(require_api_key)])
    async def ingest_webhook(
        workspace_id: str,
        request: Request,
        current_user=depends(require_api_key),
    ):
        refresh_server_exports()
        return await runtime_route_request_handlers_service.ingest_webhook_response(
            workspace_id,
            request=request,
            current_user=current_user,
            enforce_workspace_access=enforce_workspace_access_fn,
            read_json_payload=runtime_request_service.read_json_payload,
            ingest_webhook_payload=runtime_webhook_trigger_service.ingest_webhook_payload,
            match_webhook_trigger_fn=match_webhook_trigger_fn,
            run_start_request_class=run_start_request_class,
            execute_run_start_request_via_turn_runtime=execute_run_start_request_via_turn_runtime,
            stamp_request_owner_fn=stamp_request_owner_fn,
            run_execution_services=run_execution_services,
        )

    @app.post("/runs/{run_id}/delegate", dependencies=[depends(member_dependency)])
    async def delegate_run(
        run_id: uuid.UUID,
        body: run_delegation_request_class,
        current_user=depends(member_dependency),
    ):
        refresh_server_exports()
        return runtime_route_run_handlers_service.delegate_run_route_response(
            run_id,
            body=body,
            current_user=current_user,
            single_agent_mode=single_agent_mode,
            delegate_run_children_fn=runtime_run_delegation_service.delegate_run_children,
            callbacks=delegate_run_children_callbacks,
        )

    @app.post("/runs/{run_id}/delegate/auto", dependencies=[depends(member_dependency)])
    async def auto_delegate_run(
        run_id: uuid.UUID,
        body: Optional[run_auto_delegation_request_class] = None,
        current_user=depends(member_dependency),
    ):
        refresh_server_exports()
        return runtime_route_run_handlers_service.auto_delegate_run_route_response(
            run_id,
            body=body,
            current_user=current_user,
            single_agent_mode=single_agent_mode,
            request_payload_class=run_auto_delegation_request_class,
            auto_delegate_run_children_fn=runtime_run_delegation_service.auto_delegate_run_children,
            callbacks=auto_delegate_run_children_callbacks,
        )

    @app.post("/runs/{run_id}/delegate/retry-failed", dependencies=[depends(member_dependency)])
    async def retry_failed_delegation_runs(
        run_id: uuid.UUID,
        body: Optional[run_delegation_retry_request_class] = None,
        current_user=depends(member_dependency),
    ):
        refresh_server_exports()
        return runtime_route_run_handlers_service.retry_failed_delegation_runs_route_response(
            run_id,
            body=body,
            current_user=current_user,
            single_agent_mode=single_agent_mode,
            request_payload_class=run_delegation_retry_request_class,
            retry_failed_delegation_runs_fn=runtime_run_delegation_service.retry_failed_delegation_runs,
            callbacks=retry_failed_delegation_callbacks,
        )

    @app.post("/routing/preview", dependencies=[depends(member_dependency)])
    async def preview_routing(body: Optional[run_start_request_class] = None):
        refresh_server_exports()
        return runtime_route_run_handlers_service.preview_routing_route_response(
            body,
            run_start_request_class=run_start_request_class,
            preview_routing_response_fn=runtime_run_entry_service.preview_routing_response,
            build_run_routing_preview=build_run_routing_preview,
            run_routing_preview_services=run_routing_preview_services,
        )

    @app.post("/runs/precheck", dependencies=[depends(member_dependency)])
    async def precheck_run(body: Optional[run_start_request_class] = None):
        refresh_server_exports()
        return await runtime_route_run_handlers_service.precheck_run_route_response(
            body,
            run_start_request_class=run_start_request_class,
            precheck_run_response_fn=runtime_run_entry_service.precheck_run_response,
            build_run_precheck_result=build_run_precheck_result,
            run_routing_preview_services=run_routing_preview_services,
        )

    @app.get("/runs/{run_id}")
    async def get_run(run_id: uuid.UUID, current_user=depends(viewer_dependency)):
        refresh_server_exports()
        run_payload = runs.get(str(run_id))
        run_record = run_state_repository.sync_get_live_run(str(run_id))
        decision = _enforce_registered_run_api_decision(
            operation="get_run",
            run_id=str(run_id),
            current_user=current_user,
            run=run_payload,
            run_record=run_record,
            user_role="viewer",
        )
        if str(decision.get("next_action") or "").strip() == "request_owner_approval":
            raise HTTPException(
                status_code=409,
                detail=f"Rust run-api gate blocked get_run: {decision.get('reason') or 'owner_approval_required'}",
            )
        return runtime_run_query_service.build_run_detail_response(
            str(run_id),
            current_user=current_user,
            runs=runs,
            get_live_run_fn=run_state_repository.sync_get_live_run,
            **run_detail_callbacks,
        )

    @app.get("/runs/{run_id}/browser-checkpoint", dependencies=[depends(viewer_dependency)])
    async def get_run_browser_checkpoint(run_id: uuid.UUID, current_user=depends(viewer_dependency)):
        refresh_server_exports()
        run_payload = runs.get(str(run_id))
        run_record = run_state_repository.sync_get_live_run(str(run_id))
        decision = _enforce_registered_run_api_decision(
            operation="get_run",
            run_id=str(run_id),
            current_user=current_user,
            run=run_payload,
            run_record=run_record,
            user_role="viewer",
        )
        if str(decision.get("next_action") or "").strip() == "request_owner_approval":
            raise HTTPException(
                status_code=409,
                detail=f"Rust run-api gate blocked get_run: {decision.get('reason') or 'owner_approval_required'}",
            )
        return runtime_route_run_handlers_service.get_run_browser_checkpoint_route_response(
            run_id,
            current_user=current_user,
            build_run_browser_checkpoint_response_fn=runtime_run_query_service.build_run_browser_checkpoint_response,
            runs=runs,
            get_live_run_fn=run_state_repository.sync_get_live_run,
            callbacks=run_detail_callbacks,
        )

    @app.get("/runs/{run_id}/browser-session", dependencies=[depends(viewer_dependency)])
    async def get_run_browser_session(run_id: uuid.UUID, current_user=depends(viewer_dependency)):
        refresh_server_exports()
        run_payload = runs.get(str(run_id))
        run_record = run_state_repository.sync_get_live_run(str(run_id))
        decision = _enforce_registered_run_api_decision(
            operation="get_run",
            run_id=str(run_id),
            current_user=current_user,
            run=run_payload,
            run_record=run_record,
            user_role="viewer",
        )
        if str(decision.get("next_action") or "").strip() == "request_owner_approval":
            raise HTTPException(
                status_code=409,
                detail=f"Rust run-api gate blocked get_run: {decision.get('reason') or 'owner_approval_required'}",
            )
        return runtime_route_run_handlers_service.get_run_browser_session_route_response(
            run_id,
            current_user=current_user,
            build_run_browser_session_response_fn=runtime_run_query_service.build_run_browser_session_response,
            runs=runs,
            get_live_run_fn=run_state_repository.sync_get_live_run,
            callbacks=run_detail_callbacks,
        )

    @app.get("/history/runs", dependencies=[depends(viewer_dependency)])
    async def get_runs_history(
        limit: int = 30,
        workspace_id: Optional[str] = None,
        status: Optional[str] = None,
        pack_id: Optional[str] = None,
        current_user=depends(viewer_dependency),
    ):
        return runtime_history_service.build_runs_history_payload(
            limit=limit,
            workspace_id=workspace_id,
            status=status,
            pack_id=pack_id,
            current_user=current_user,
            **runs_history_callbacks,
        )

    @app.get("/usage/summary", dependencies=[depends(viewer_dependency)])
    async def get_usage_summary(period: str = "all", current_user=depends(viewer_dependency)):
        return runtime_usage_service.usage_summary_payload(
            period=period,
            current_user=current_user,
            usage_snapshots_for_user_fn=usage_snapshots_for_user_fn,
            aggregate_usage_summary_fn=aggregate_usage_summary_fn,
        )

    @app.get("/usage/runs", dependencies=[depends(viewer_dependency)])
    async def get_usage_runs(
        limit: int = 50,
        offset: int = 0,
        period: str = "all",
        current_user=depends(viewer_dependency),
    ):
        return runtime_usage_service.usage_runs_payload(
            limit=limit,
            offset=offset,
            period=period,
            current_user=current_user,
            usage_snapshots_for_user_fn=usage_snapshots_for_user_fn,
            list_usage_runs_fn=list_usage_runs_fn,
        )

    @app.get("/runs/{run_id}/replay", dependencies=[depends(require_admin_api_key)])
    async def get_run_replay(run_id: uuid.UUID):
        refresh_server_exports()
        replay_payload = get_replay_payload(str(run_id))
        decision = _enforce_registered_run_api_decision(
            operation="get_run",
            run_id=str(run_id),
            current_user={
                "user_id": "admin_api_key",
                "auth_type": "api_key",
                "role": "admin",
                "is_admin": True,
            },
            run=(
                replay_payload
                if isinstance(replay_payload, dict)
                else {"run_id": str(run_id)}
            ),
            run_record=None,
            user_role="admin",
        )
        if str(decision.get("next_action") or "").strip() == "request_owner_approval":
            raise HTTPException(
                status_code=409,
                detail=f"Rust run-api gate blocked get_run: {decision.get('reason') or 'owner_approval_required'}",
            )
        return runtime_route_run_handlers_service.get_run_replay_route_response(
            run_id,
            replay_item_response_for_run=runtime_run_replay_service.replay_item_response_for_run,
            get_replay_payload=get_replay_payload,
        )

    @app.post("/runs/{run_id}/replay", dependencies=[depends(require_admin_api_key)])
    async def replay_run(run_id: uuid.UUID):
        refresh_server_exports()
        replay_payload = get_replay_payload(str(run_id))
        decision = _enforce_registered_run_api_decision(
            operation="get_run",
            run_id=str(run_id),
            current_user={
                "user_id": "admin_api_key",
                "auth_type": "api_key",
                "role": "admin",
                "is_admin": True,
            },
            run=(
                replay_payload
                if isinstance(replay_payload, dict)
                else {"run_id": str(run_id)}
            ),
            run_record=None,
            user_role="admin",
        )
        if str(decision.get("next_action") or "").strip() == "request_owner_approval":
            raise HTTPException(
                status_code=409,
                detail=f"Rust run-api gate blocked get_run: {decision.get('reason') or 'owner_approval_required'}",
            )
        return runtime_route_run_handlers_service.replay_run_route_response(
            run_id,
            replay_run_from_run_id_fn=runtime_run_replay_service.replay_run_from_run_id,
            get_replay_payload=get_replay_payload,
            run_start_request_class=run_start_request_class,
            execute_system_run_start_request_via_turn_runtime=execute_system_run_start_request_via_turn_runtime,
            stamp_request_owner_fn=stamp_request_owner_fn,
            run_execution_services=run_execution_services,
        )

    @app.get("/runs/{run_id}/stream", dependencies=[depends(viewer_dependency)])
    async def stream_run(run_id: uuid.UUID, current_user=depends(viewer_dependency)):
        refresh_server_exports()
        run_payload = runs.get(str(run_id))
        run_record = run_state_repository.sync_get_live_run(str(run_id))
        decision = _enforce_registered_run_api_decision(
            operation="get_run",
            run_id=str(run_id),
            current_user=current_user,
            run=run_payload,
            run_record=run_record,
            user_role="viewer",
        )
        if str(decision.get("next_action") or "").strip() == "request_owner_approval":
            raise HTTPException(
                status_code=409,
                detail=f"Rust run-api gate blocked get_run: {decision.get('reason') or 'run_sensitive_payload_requires_approval'}",
            )
        return runtime_route_run_handlers_service.stream_run_route_response(
            run_id,
            current_user=current_user,
            stream_run_response_fn=runtime_run_entry_service.stream_run_response,
            runs=runs,
            get_live_run_fn=run_state_repository.sync_get_live_run,
            serialize_run_snapshot=serialize_run_snapshot,
            enforce_run_owner_access=enforce_run_owner_access,
            event_source_response_class=event_source_response_class,
            iter_logs_for_run=iter_logs_for_run,
        )




    @app.post("/runs/{run_id}/resume", dependencies=[depends(member_dependency)])
    async def resume_run(run_id: uuid.UUID, current_user=depends(member_dependency)):
        refresh_server_exports()
        run_payload = runs.get(str(run_id))
        run_record = run_state_repository.sync_get_live_run(str(run_id))
        _enforce_registered_run_api_decision(
            operation="resume_run",
            run_id=str(run_id),
            current_user=current_user,
            run=run_payload,
            run_record=run_record,
        )
        return runtime_route_run_handlers_service.resume_run_route_response(
            run_id,
            current_user=current_user,
            resume_waiting_run_fn=runtime_run_control_service.resume_waiting_run,
            run=run_payload,
            run_record=run_record,
            callbacks=resume_waiting_run_callbacks,
        )

    @app.post("/runs/{run_id}/pause", dependencies=[depends(member_dependency)])
    async def pause_run(run_id: uuid.UUID, current_user=depends(member_dependency)):
        refresh_server_exports()
        run_payload = runs.get(str(run_id))
        run_record = run_state_repository.sync_get_live_run(str(run_id))
        _enforce_registered_run_api_decision(
            operation="pause_run",
            run_id=str(run_id),
            current_user=current_user,
            run=run_payload,
            run_record=run_record,
        )
        return runtime_route_run_handlers_service.pause_run_route_response(
            run_id,
            current_user=current_user,
            pause_run_fn=runtime_run_control_service.pause_run_for_takeover,
            run=run_payload,
            run_record=run_record,
            callbacks=pause_run_callbacks,
        )

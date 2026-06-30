from __future__ import annotations

from typing import Any, Callable

from fastapi import HTTPException
from server_modules import rust_runtime_kernel_client


def _payload_field(payload: Any, key: str, default: Any = None) -> Any:
    if isinstance(payload, dict):
        return payload.get(key, default)
    return getattr(payload, key, default)


def _payload_metadata(payload: Any) -> dict[str, Any]:
    metadata = _payload_field(payload, "metadata", {})
    return dict(metadata or {}) if isinstance(metadata, dict) else {}


def _current_user_field(current_user: Any, key: str, default: Any = None) -> Any:
    if isinstance(current_user, dict):
        return current_user.get(key, default)
    return getattr(current_user, key, default)


def _enforce_platform_orchestration_decision(request_payload: Any, *, current_user: Any, operation: str) -> dict[str, Any]:
    metadata = _payload_metadata(request_payload)
    workspace_id = str(_payload_field(request_payload, "workspace_id", None) or metadata.get("workspace_id") or "default").strip() or "default"
    actor_id = str(_current_user_field(current_user, "user_id", None) or _current_user_field(current_user, "id", None) or "").strip()
    try:
        decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
            "platform-orchestration-decision",
            {
                "operation": operation,
                "tenant_id": str(_payload_field(request_payload, "tenant_id", None) or metadata.get("tenant_id") or "default").strip() or "default",
                "workspace_id": workspace_id,
                "actor_id": actor_id,
                "actor_role": str(_current_user_field(current_user, "role", None) or metadata.get("actor_role") or "member").strip() or "member",
                "request_id": str(metadata.get("request_id") or metadata.get("client_request_id") or "").strip() or None,
                "workflow_id": str(_payload_field(request_payload, "workflow_id", None) or metadata.get("workflow_id") or "").strip() or None,
                "execution_target": str(metadata.get("execution_target") or _payload_field(request_payload, "execution_target", None) or "cloud").strip() or "cloud",
                "runtime_mode": str(metadata.get("runtime_mode") or "hosted_secure").strip() or "hosted_secure",
                "workspace_access": True,
                "owner_access": str(_current_user_field(current_user, "role", "") or "").strip().lower() in {"owner", "admin"},
                "entitlement_ok": True,
                "billing_ok": True,
                "csrf_valid": True,
                "api_key_valid": True,
                "runtime_attached": True,
                "payload": True,
            },
        )
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        reason = str(getattr(exc, "reason", "") or "platform_orchestration_denied").strip()
        raise HTTPException(
            status_code=423,
            detail={
                "error": "rust_platform_orchestration_denied",
                "operation": operation,
                "reason": reason,
            },
        ) from exc
    expected_next_action = {
        "run_create": "create_or_route_run",
    }.get(operation)
    next_action = str(decision.get("next_action") or "").strip()
    if expected_next_action and next_action != expected_next_action:
        raise HTTPException(
            status_code=423,
            detail={
                "error": "rust_platform_orchestration_invalid_next_action",
                "operation": operation,
                "reason": f"unexpected_next_action:{next_action or 'missing'}",
            },
        )
    return decision


async def start_run_route_response(
    body: Any,
    *,
    current_user: Any,
    run_start_request_class: Callable[..., Any],
    start_run_response_fn: Callable[..., Any],
    execute_run_start_request_via_turn_runtime: Callable[..., Any],
    stamp_request_owner_fn: Callable[..., Any],
    run_execution_services: Callable[[], Any],
) -> Any:
    request_payload = body or run_start_request_class()
    _enforce_platform_orchestration_decision(
        request_payload,
        current_user=current_user,
        operation="run_create",
    )
    return await start_run_response_fn(
        request_payload,
        current_user=current_user,
        execute_run_start_request_via_turn_runtime=execute_run_start_request_via_turn_runtime,
        stamp_request_owner_fn=stamp_request_owner_fn,
        run_execution_services=run_execution_services,
    )


def preview_routing_route_response(
    body: Any,
    *,
    run_start_request_class: Callable[..., Any],
    preview_routing_response_fn: Callable[..., Any],
    build_run_routing_preview: Callable[..., Any],
    run_routing_preview_services: Callable[[], Any],
) -> Any:
    return preview_routing_response_fn(
        body or run_start_request_class(),
        build_run_routing_preview=build_run_routing_preview,
        run_routing_preview_services=run_routing_preview_services,
    )


async def precheck_run_route_response(
    body: Any,
    *,
    run_start_request_class: Callable[..., Any],
    precheck_run_response_fn: Callable[..., Any],
    build_run_precheck_result: Callable[..., Any],
    run_routing_preview_services: Callable[[], Any],
) -> Any:
    return await precheck_run_response_fn(
        body or run_start_request_class(),
        build_run_precheck_result=build_run_precheck_result,
        run_routing_preview_services=run_routing_preview_services,
    )


def _ensure_delegation_enabled(single_agent_mode: bool) -> None:
    if single_agent_mode:
        raise HTTPException(status_code=400, detail="Single-agent mode is enabled. Delegation is disabled.")


def delegate_run_route_response(
    run_id: Any,
    *,
    body: Any,
    current_user: Any,
    single_agent_mode: bool,
    delegate_run_children_fn: Callable[..., Any],
    callbacks: dict[str, Any],
) -> Any:
    _ensure_delegation_enabled(single_agent_mode)
    return delegate_run_children_fn(
        str(run_id),
        body=body,
        current_user=current_user,
        **callbacks,
    )


def auto_delegate_run_route_response(
    run_id: Any,
    *,
    body: Any,
    current_user: Any,
    single_agent_mode: bool,
    request_payload_class: Callable[..., Any],
    auto_delegate_run_children_fn: Callable[..., Any],
    callbacks: dict[str, Any],
) -> Any:
    _ensure_delegation_enabled(single_agent_mode)
    return auto_delegate_run_children_fn(
        str(run_id),
        request_payload=body or request_payload_class(),
        current_user=current_user,
        **callbacks,
    )


def retry_failed_delegation_runs_route_response(
    run_id: Any,
    *,
    body: Any,
    current_user: Any,
    single_agent_mode: bool,
    request_payload_class: Callable[..., Any],
    retry_failed_delegation_runs_fn: Callable[..., Any],
    callbacks: dict[str, Any],
) -> Any:
    _ensure_delegation_enabled(single_agent_mode)
    return retry_failed_delegation_runs_fn(
        str(run_id),
        request_payload=body or request_payload_class(),
        current_user=current_user,
        **callbacks,
    )


def get_run_replay_route_response(
    run_id: Any,
    *,
    replay_item_response_for_run: Callable[..., Any],
    get_replay_payload: Callable[[str], dict[str, Any]],
) -> Any:
    return replay_item_response_for_run(
        str(run_id),
        get_replay_payload=get_replay_payload,
    )


def replay_run_route_response(
    run_id: Any,
    *,
    replay_run_from_run_id_fn: Callable[..., Any],
    get_replay_payload: Callable[[str], dict[str, Any]],
    run_start_request_class: Callable[..., Any],
    execute_system_run_start_request_via_turn_runtime: Callable[..., Any],
    stamp_request_owner_fn: Callable[..., Any],
    run_execution_services: Callable[[], Any],
) -> Any:
    return replay_run_from_run_id_fn(
        str(run_id),
        get_replay_payload=get_replay_payload,
        run_start_request_class=run_start_request_class,
        execute_system_run_start_request_via_turn_runtime=execute_system_run_start_request_via_turn_runtime,
        stamp_request_owner_fn=stamp_request_owner_fn,
        run_execution_services=run_execution_services,
    )


def stream_run_route_response(
    run_id: Any,
    *,
    current_user: Any,
    stream_run_response_fn: Callable[..., Any],
    runs: dict[str, Any],
    get_live_run_fn: Callable[[str], dict[str, Any] | None],
    serialize_run_snapshot: Callable[[str, dict[str, Any]], Any],
    enforce_run_owner_access: Callable[[Any, Any], None],
    event_source_response_class: Callable[[Any], Any],
    iter_logs_for_run: Callable[[str], Any],
) -> Any:
    return stream_run_response_fn(
        str(run_id),
        current_user=current_user,
        runs=runs,
        get_live_run_fn=get_live_run_fn,
        serialize_run_snapshot=serialize_run_snapshot,
        enforce_run_owner_access=enforce_run_owner_access,
        event_source_response_class=event_source_response_class,
        iter_logs_for_run=iter_logs_for_run,
    )


def get_run_browser_checkpoint_route_response(
    run_id: Any,
    *,
    current_user: Any,
    build_run_browser_checkpoint_response_fn: Callable[..., Any],
    runs: dict[str, Any],
    get_live_run_fn: Callable[[str], dict[str, Any] | None],
    callbacks: dict[str, Any],
) -> Any:
    return build_run_browser_checkpoint_response_fn(
        str(run_id),
        current_user=current_user,
        runs=runs,
        get_live_run_fn=get_live_run_fn,
        **callbacks,
    )


def get_run_browser_session_route_response(
    run_id: Any,
    *,
    current_user: Any,
    build_run_browser_session_response_fn: Callable[..., Any],
    runs: dict[str, Any],
    get_live_run_fn: Callable[[str], dict[str, Any] | None],
    callbacks: dict[str, Any],
) -> Any:
    return build_run_browser_session_response_fn(
        str(run_id),
        current_user=current_user,
        runs=runs,
        get_live_run_fn=get_live_run_fn,
        **callbacks,
    )


def submit_run_decision_route_response(
    run_id: Any,
    *,
    payload: Any,
    current_user: Any,
    submit_run_decision_fn: Callable[..., Any],
    run: dict[str, Any] | None,
    run_record: dict[str, Any] | None,
    callbacks: dict[str, Any],
) -> Any:
    payload.validate_fields()
    return submit_run_decision_fn(
        str(run_id),
        run=run,
        run_record=run_record,
        payload=payload,
        current_user=current_user,
        **callbacks,
    )


def resolve_run_approval_route_response(
    run_id: Any,
    approval_id: str,
    *,
    payload: Any,
    current_user: Any,
    resolve_run_approval_fn: Callable[..., Any],
    run: dict[str, Any] | None,
    run_record: dict[str, Any] | None,
    callbacks: dict[str, Any],
) -> Any:
    payload.validate_fields()
    return resolve_run_approval_fn(
        str(run_id),
        approval_id,
        run=run,
        run_record=run_record,
        payload=payload,
        current_user=current_user,
        **callbacks,
    )


def resume_run_route_response(
    run_id: Any,
    *,
    current_user: Any,
    resume_waiting_run_fn: Callable[..., Any],
    run: dict[str, Any] | None,
    run_record: dict[str, Any] | None,
    callbacks: dict[str, Any],
) -> Any:
    return resume_waiting_run_fn(
        str(run_id),
        run=run,
        run_record=run_record,
        current_user=current_user,
        **callbacks,
    )


def pause_run_route_response(
    run_id: Any,
    *,
    current_user: Any,
    pause_run_fn: Callable[..., Any],
    run: dict[str, Any] | None,
    run_record: dict[str, Any] | None,
    callbacks: dict[str, Any],
) -> Any:
    return pause_run_fn(
        str(run_id),
        run=run,
        run_record=run_record,
        current_user=current_user,
        **callbacks,
    )

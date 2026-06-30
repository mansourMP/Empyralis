from __future__ import annotations

from typing import Any, Callable

from fastapi import HTTPException
from server_modules import rust_runtime_kernel_client


def _field(payload: Any, name: str, default: Any = None) -> Any:
    if isinstance(payload, dict):
        return payload.get(name, default)
    return getattr(payload, name, default)


def _metadata(payload: Any) -> dict[str, Any]:
    value = _field(payload, "metadata", {})
    return dict(value or {}) if isinstance(value, dict) else {}


def _enforce_run_preparation_decision(request_payload: Any, *, operation: str = "prepare_run_start") -> dict[str, Any]:
    metadata = _metadata(request_payload)
    workspace_id = str(_field(request_payload, "workspace_id", None) or metadata.get("workspace_id") or "default").strip() or "default"
    tenant_id = str(_field(request_payload, "tenant_id", None) or metadata.get("tenant_id") or "default").strip() or "default"
    workflow_id = str(_field(request_payload, "workflow_id", None) or metadata.get("workflow_id") or "").strip()
    try:
        return rust_runtime_kernel_client.run_runtime_kernel_enforced(
            "run-preparation-decision",
            {
                "operation": operation,
                "workspace_id": workspace_id,
                "tenant_id": tenant_id,
                "engine": str(_field(request_payload, "engine", None) or metadata.get("engine") or "orion").strip() or "orion",
                "workflow_id": workflow_id or None,
                "agent_role": str(_field(request_payload, "agent_role", None) or metadata.get("agent_role") or "").strip() or None,
                "outcome_pack": str(metadata.get("outcome_pack") or "").strip(),
                "trust_mode": str(metadata.get("trust_mode") or "auto").strip() or "auto",
                "execution_target": str(metadata.get("execution_target") or "auto").strip() or "auto",
                "elevated_mode": str(metadata.get("elevated_mode") or metadata.get("elevated") or "off").strip() or "off",
                "max_iterations": metadata.get("max_iterations"),
                "pack_inputs_present": "pack_inputs" in metadata,
                "pack_inputs_is_object": isinstance(metadata.get("pack_inputs"), dict),
                "outcome_scope_present": "outcome_scope" in metadata,
                "outcome_scope_is_list": isinstance(metadata.get("outcome_scope"), list),
                "approval_rules_present": "approval_rules" in metadata,
                "approval_rules_is_object": isinstance(metadata.get("approval_rules"), dict),
                "schedule_present": "schedule" in metadata,
                "schedule_is_object": isinstance(metadata.get("schedule"), dict),
                "connector_credential_present": "connector_credential_id" in metadata,
                "connector_credential_is_string": isinstance(metadata.get("connector_credential_id"), str),
                "workflow_required": bool(metadata.get("workflow_required")),
                "workflow_snapshot_present": bool(metadata.get("workflow_snapshot")),
                "workflow_definition_present": bool((metadata.get("workflow_snapshot") or {}).get("definition"))
                if isinstance(metadata.get("workflow_snapshot"), dict)
                else True,
                "app_id_present": bool(metadata.get("app_id")),
                "app_permissions_resolved": bool(metadata.get("app_permissions_resolved")),
                "elevated_approval_present": bool(metadata.get("elevated_approval_present")),
            },
        )
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        reason = str(getattr(exc, "reason", "") or "run_preparation_denied").strip()
        raise HTTPException(
            status_code=423,
            detail={
                "error": "rust_run_preparation_denied",
                "operation": operation,
                "reason": reason,
            },
        ) from exc


def _require_run_preparation_next_action(decision: dict[str, Any], *allowed_actions: str) -> str:
    next_action = str(decision.get("next_action") or "").strip()
    if next_action in allowed_actions:
        return next_action
    operation = str(decision.get("operation") or "run_preparation").strip() or "run_preparation"
    raise HTTPException(
        status_code=423,
        detail={
            "error": "rust_run_preparation_denied",
            "operation": operation,
            "reason": f"unexpected_next_action:{next_action or 'missing'}",
        },
    )


def _stream_run_user_role(current_user: Any) -> str:
    if isinstance(current_user, dict):
        if str(current_user.get("auth_type") or "").strip().lower() == "api_key":
            return "system"
        role = str(current_user.get("workspace_role") or current_user.get("role") or "viewer").strip()
        return role or "viewer"
    return "viewer"


def _enforce_stream_run_read_decision(
    *,
    run_id: str,
    current_user: Any,
    run_record: dict[str, Any],
) -> dict[str, Any]:
    context = run_record.get("context") if isinstance(run_record.get("context"), dict) else {}
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    workspace_id = str(
        run_record.get("workspace_id")
        or context.get("workspace_id")
        or metadata.get("workspace_id")
        or "default"
    ).strip() or "default"
    tenant_id = str(
        run_record.get("tenant_id")
        or context.get("tenant_id")
        or metadata.get("tenant_id")
        or "default"
    ).strip() or "default"
    run_status = str(
        run_record.get("status")
        or run_record.get("state")
        or context.get("status")
        or metadata.get("status")
        or ""
    ).strip()
    try:
        decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
            "run-api-decision",
            {
                "operation": "get_run",
                "workspace_id": workspace_id,
                "tenant_id": tenant_id,
                "run_id": str(run_id or "").strip(),
                "run_status": run_status,
                "user_role": _stream_run_user_role(current_user),
                "workspace_access_denied": False,
                "owner_access_denied": False,
                "kill_switch_active": False,
                "history_window_allowed": True,
                "body": {
                    "user_id": str((current_user or {}).get("user_id") or "").strip() or "user",
                    "stream_requested": True,
                },
            },
            allow_approval_required=True,
        )
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        raise HTTPException(status_code=409, detail=f"Rust run-api gate blocked get_run: {exc.reason}") from exc
    next_action = str(decision.get("next_action") or "").strip()
    if next_action == "request_owner_approval":
        raise HTTPException(
            status_code=409,
            detail=f"Rust run-api gate blocked get_run: {decision.get('reason') or 'owner_approval_required'}",
        )
    if next_action != "get_run":
        raise HTTPException(
            status_code=423,
            detail=f"Rust run-api gate returned unexpected next_action for get_run: {next_action or 'missing'}",
        )
    return dict(decision)


def _enforce_run_routing_decision(request_payload: Any, *, operation: str = "routing_preview") -> dict[str, Any]:
    metadata = _metadata(request_payload)
    workspace_id = str(_field(request_payload, "workspace_id", None) or metadata.get("workspace_id") or "default").strip() or "default"
    execution_target = str(
        _field(request_payload, "execution_target", None)
        or metadata.get("execution_target_selected")
        or metadata.get("execution_target")
        or "auto"
    ).strip() or "auto"
    parent_run_id = str(_field(request_payload, "parent_run_id", None) or metadata.get("parent_run_id") or "").strip()
    schedule_id = str(_field(request_payload, "schedule_id", None) or metadata.get("schedule_id") or "").strip()
    run_id = str(_field(request_payload, "run_id", None) or _field(request_payload, "id", None) or metadata.get("run_id") or "").strip()
    try:
        return rust_runtime_kernel_client.run_runtime_kernel_enforced(
            "run-routing-decision",
            {
                "operation": operation,
                "workspace_id": workspace_id,
                "run_id": run_id or None,
                "schedule_id": schedule_id or None,
                "parent_run_id": parent_run_id or None,
                "execution_target": execution_target,
                "workflow_required": False,
                "workflow_snapshot_present": False,
            },
        )
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        reason = str(getattr(exc, "reason", "") or "run_routing_denied").strip()
        raise HTTPException(
            status_code=423,
            detail={
                "error": "rust_run_routing_denied",
                "operation": operation,
                "reason": reason,
            },
        ) from exc


def _require_run_routing_next_action(decision: dict[str, Any], *allowed_actions: str) -> str:
    next_action = str(decision.get("next_action") or "").strip()
    if next_action in allowed_actions:
        return next_action
    operation = str(decision.get("operation") or "run_routing").strip() or "run_routing"
    raise HTTPException(
        status_code=423,
        detail={
            "error": "rust_run_routing_denied",
            "operation": operation,
            "reason": f"unexpected_next_action:{next_action or 'missing'}",
        },
    )


def _apply_run_preparation_normalization(request_payload: Any, decision: dict[str, Any]) -> Any:
    if not isinstance(request_payload, dict):
        return request_payload
    normalized = dict(request_payload)
    metadata = dict(normalized.get("metadata") or {}) if isinstance(normalized.get("metadata"), dict) else {}
    for key in ("engine", "workflow_id"):
        value = decision.get(key)
        if value not in (None, ""):
            normalized[key] = value
    for key in ("agent_role", "outcome_pack", "trust_mode", "execution_target", "elevated_mode"):
        value = decision.get(key)
        if value not in (None, ""):
            metadata[key] = value
    normalized["metadata"] = metadata
    return normalized


async def start_run_response(
    request_payload: Any,
    *,
    current_user: Any,
    execute_run_start_request_via_turn_runtime: Callable[..., Any],
    stamp_request_owner_fn: Callable[..., Any],
    run_execution_services: Callable[[], Any],
) -> Any:
    decision = _enforce_run_preparation_decision(request_payload, operation="prepare_run_start")
    _require_run_preparation_next_action(decision, "prepare_run_request")
    normalized_request_payload = _apply_run_preparation_normalization(request_payload, decision)
    return await execute_run_start_request_via_turn_runtime(
        normalized_request_payload,
        current_user=current_user,
        stamp_request_owner_fn=stamp_request_owner_fn,
        services=run_execution_services(),
    )


def preview_routing_response(
    request_payload: Any,
    *,
    build_run_routing_preview: Callable[..., dict[str, Any]],
    run_routing_preview_services: Callable[[], Any],
) -> dict[str, Any]:
    decision = _enforce_run_routing_decision(request_payload, operation="routing_preview")
    _require_run_routing_next_action(decision, "build_routing_preview")
    preview = build_run_routing_preview(request_payload, services=run_routing_preview_services())
    return {
        "engine": preview["engine"],
        "agent_role": preview["metadata"].get("agent_role"),
        "agent_role_source": preview["metadata"].get("agent_role_source"),
        "route": preview["route"],
        "tool_policy_precheck": preview["tool_policy_precheck"],
    }


async def precheck_run_response(
    request_payload: Any,
    *,
    build_run_precheck_result: Callable[..., Any],
    run_routing_preview_services: Callable[[], Any],
) -> dict[str, Any]:
    decision = _enforce_run_routing_decision(request_payload, operation="routing_preview")
    _require_run_routing_next_action(decision, "build_routing_preview")
    preview = await build_run_precheck_result(request_payload, services=run_routing_preview_services())
    return {
        "ok": True,
        "engine": preview["engine"],
        "agent_role": preview["metadata"].get("agent_role"),
        "agent_role_source": preview["metadata"].get("agent_role_source"),
        "route": preview["route"],
        "tool_policy_precheck": preview["tool_policy_precheck"],
        "doctor_preflight": preview["doctor_preflight"],
    }


def stream_run_response(
    run_id: str,
    *,
    current_user: Any,
    runs: dict[str, Any],
    get_live_run_fn: Callable[[str], dict[str, Any] | None],
    serialize_run_snapshot: Callable[[str, dict[str, Any]], Any],
    enforce_run_owner_access: Callable[[Any, Any], None],
    event_source_response_class: Callable[[Any], Any],
    iter_logs_for_run: Callable[[str], Any],
) -> Any:
    run_record = get_live_run_fn(run_id)
    if not isinstance(run_record, dict):
        raise HTTPException(404, "Run ID not found")
    snapshot = serialize_run_snapshot(run_id, run_record)
    enforce_run_owner_access(current_user, snapshot)
    _enforce_stream_run_read_decision(
        run_id=run_id,
        current_user=current_user,
        run_record=run_record,
    )
    if run_id not in runs:
        raise HTTPException(409, "Run is not active in this process.")
    return event_source_response_class(iter_logs_for_run(run_id))

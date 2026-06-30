from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any, Callable
import uuid

from fastapi import HTTPException
from server_modules import agent_trace_service
from server_modules.direct_tool_config_service import run_async_tool_call
from server_modules import rust_runtime_kernel_client


LOGGER = logging.getLogger(__name__)


def _parent_scope(parent_snapshot: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], str, str]:
    parent_context, parent_metadata = _parent_run_context(parent_snapshot)
    workspace_id = str(
        parent_snapshot.get("workspace_id")
        or parent_context.get("workspace_id")
        or parent_metadata.get("workspace_id")
        or "default"
    ).strip() or "default"
    tenant_id = str(
        parent_snapshot.get("tenant_id")
        or parent_context.get("tenant_id")
        or parent_metadata.get("tenant_id")
        or "default"
    ).strip() or "default"
    return parent_context, parent_metadata, workspace_id, tenant_id


def _enforce_delegation_child_decision(
    *,
    parent_run_id: str,
    parent_snapshot: dict[str, Any],
    child_payload: dict[str, Any],
) -> dict[str, Any]:
    parent_context, parent_metadata, workspace_id, _tenant_id = _parent_scope(parent_snapshot)
    rust_payload = {
        "operation": "delegation_child",
        "workspace_id": workspace_id,
        "run_id": str(parent_snapshot.get("run_id") or "").strip() or None,
        "parent_run_id": str(parent_run_id or "").strip(),
        "execution_target": str(
            child_payload.get("execution_target")
            or parent_snapshot.get("execution_target")
            or parent_context.get("execution_target")
            or parent_metadata.get("execution_target")
            or "auto"
        ).strip() or "auto",
        "runtime_mode": str(
            child_payload.get("runtime_mode")
            or parent_snapshot.get("runtime_mode")
            or parent_context.get("runtime_mode")
            or parent_metadata.get("runtime_mode")
            or ""
        ).strip(),
        "agent_role": str(child_payload.get("agent_role") or "").strip(),
        "user_goal": str(child_payload.get("user_goal") or "").strip(),
        "workflow_turn_depth": int(
            child_payload.get("workflow_turn_depth")
            or parent_metadata.get("workflow_turn_depth")
            or 0
        ),
        "max_workflow_turn_depth": int(
            child_payload.get("max_workflow_turn_depth")
            or parent_metadata.get("max_workflow_turn_depth")
            or 999999
        ),
    }
    try:
        decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
            "run-routing-decision",
            rust_payload,
            allow_approval_required=True,
        )
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        raise HTTPException(status_code=409, detail=f"Rust run-routing gate blocked delegation_child: {exc.reason}") from exc
    next_action = str(decision.get("next_action") or "").strip()
    if next_action != "create_delegated_child_run":
        raise HTTPException(
            status_code=423,
            detail=(
                "Rust run-routing gate returned unexpected next_action for delegation_child: "
                f"{next_action or 'missing'}"
            ),
        )
    return dict(decision)


def _enforce_delegation_merge_retry_decision(
    *,
    parent_run_id: str,
    parent_snapshot: dict[str, Any],
    child_runs: list[dict[str, Any]],
) -> dict[str, Any]:
    parent_context, parent_metadata, workspace_id, _tenant_id = _parent_scope(parent_snapshot)
    active_children = sum(
        1
        for child in child_runs
        if str(child.get("status") or "").strip().lower() in {"running", "queued", "approved", "retry_scheduled"}
    )
    waiting_children = sum(
        1
        for child in child_runs
        if str(child.get("status") or "").strip().lower() in {"waiting_for_input", "awaiting_approval"}
    )
    failed_children = sum(
        1
        for child in child_runs
        if str(child.get("status") or "").strip().lower() in {"failed", "error", "timeout", "cancelled", "stopped"}
    )
    rust_payload = {
        "operation": "delegation_merge",
        "workspace_id": workspace_id,
        "run_id": str(parent_snapshot.get("run_id") or "").strip() or None,
        "parent_run_id": str(parent_run_id or "").strip(),
        "execution_target": str(
            parent_snapshot.get("execution_target")
            or parent_context.get("execution_target")
            or parent_metadata.get("execution_target")
            or "auto"
        ).strip() or "auto",
        "runtime_mode": str(
            parent_snapshot.get("runtime_mode")
            or parent_context.get("runtime_mode")
            or parent_metadata.get("runtime_mode")
            or ""
        ).strip(),
        "active_children": active_children,
        "waiting_children": waiting_children,
        "failed_children": failed_children,
    }
    try:
        decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
            "run-routing-decision",
            rust_payload,
            allow_approval_required=True,
        )
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        raise HTTPException(status_code=409, detail=f"Rust run-routing gate blocked delegation_merge: {exc.reason}") from exc
    next_action = str(decision.get("next_action") or "").strip()
    if next_action != "retry_failed_children":
        raise HTTPException(
            status_code=423,
            detail=(
                "Rust run-routing gate returned unexpected next_action for delegation_merge: "
                f"{next_action or 'missing'}"
            ),
        )
    return dict(decision)


def _enforce_retry_run_orchestration_decision(
    *,
    parent_snapshot: dict[str, Any],
    child_snapshot: dict[str, Any],
) -> dict[str, Any]:
    parent_context, parent_metadata, workspace_id, _tenant_id = _parent_scope(parent_snapshot)
    child_context = child_snapshot.get("context") if isinstance(child_snapshot.get("context"), dict) else {}
    child_metadata = child_context.get("metadata") if isinstance(child_context.get("metadata"), dict) else {}
    if not isinstance(child_metadata, dict):
        child_metadata = {}
    retry_policy = child_metadata.get("retry_policy") if isinstance(child_metadata.get("retry_policy"), dict) else {}
    attempts = int(
        child_metadata.get("retry_count")
        or child_metadata.get("retry_sequence")
        or child_snapshot.get("retry_count")
        or child_snapshot.get("retry_sequence")
        or 0
    )
    max_attempts = int(
        retry_policy.get("max_attempts")
        or child_metadata.get("max_attempts")
        or child_metadata.get("max_retry_attempts")
        or child_snapshot.get("max_attempts")
        or 3
    )
    rust_payload = {
        "operation": "retry",
        "workspace_id": workspace_id,
        "run_id": str(child_snapshot.get("run_id") or "").strip() or None,
        "status": str(child_snapshot.get("status") or "").strip().lower(),
        "mode": str(
            child_snapshot.get("mode")
            or child_context.get("mode")
            or parent_snapshot.get("mode")
            or parent_context.get("mode")
            or parent_metadata.get("mode")
            or "background"
        ).strip(),
        "priority": str(
            child_snapshot.get("priority")
            or child_context.get("priority")
            or parent_snapshot.get("priority")
            or parent_context.get("priority")
            or parent_metadata.get("priority")
            or "normal"
        ).strip(),
        "attempts": max(0, attempts),
        "max_attempts": max(1, max_attempts),
    }
    try:
        decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
            "run-orchestration-decision",
            rust_payload,
            allow_approval_required=True,
        )
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        raise HTTPException(status_code=409, detail=f"Rust run-orchestration gate blocked retry: {exc.reason}") from exc
    next_action = str(decision.get("next_action") or "").strip()
    if next_action != "retry_run":
        raise HTTPException(
            status_code=423,
            detail=(
                "Rust run-orchestration gate returned unexpected next_action for retry: "
                f"{next_action or 'missing'}"
            ),
        )
    return dict(decision)


def _parent_run_context(parent_snapshot: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    parent_context = parent_snapshot.get("context") if isinstance(parent_snapshot.get("context"), dict) else {}
    parent_metadata = parent_context.get("metadata") if isinstance(parent_context.get("metadata"), dict) else {}
    return parent_context, parent_metadata


def _resume_parent_trace_context(parent_snapshot: dict[str, Any]) -> Any:
    parent_context, parent_metadata = _parent_run_context(parent_snapshot)
    trace_id = (
        str(parent_snapshot.get("trace_id") or "").strip()
        or str(parent_context.get("trace_id") or "").strip()
        or str(parent_metadata.get("trace_id") or "").strip()
        or str(parent_metadata.get("request_trace_id") or "").strip()
    )
    if not trace_id:
        return None
    try:
        return run_async_tool_call(
            agent_trace_service.resume_trace(
                trace_id=trace_id,
                tenant_id=str(
                    parent_snapshot.get("tenant_id")
                    or parent_context.get("tenant_id")
                    or parent_metadata.get("tenant_id")
                    or "default"
                ).strip()
                or "default",
                workspace_id=str(
                    parent_snapshot.get("workspace_id")
                    or parent_context.get("workspace_id")
                    or parent_metadata.get("workspace_id")
                    or "default"
                ).strip()
                or "default",
                thread_id=str(
                    parent_snapshot.get("thread_id")
                    or parent_context.get("thread_id")
                    or parent_metadata.get("thread_id")
                    or parent_metadata.get("session_id")
                    or ""
                ).strip()
                or None,
                run_id=str(parent_snapshot.get("run_id") or "").strip() or None,
            )
        )
    except Exception as exc:
        LOGGER.warning("Failed to resume delegation trace context: %s", exc)
        return None


def _emit_trace(awaitable: Any, *, operation: str) -> Any:
    try:
        return run_async_tool_call(awaitable)
    except Exception as exc:
        LOGGER.warning("Failed to emit delegation trace during %s: %s", operation, exc)
        return None


def _orchestrator_parent(
    parent_run_id: str,
    *,
    current_user: Any,
    lookup_run_snapshot: Callable[[str], dict[str, Any]],
    enforce_run_owner_access: Callable[[Any, Any], None],
    normalize_agent_role: Callable[[Any], str],
    invalid_detail: str,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    parent_snapshot = lookup_run_snapshot(parent_run_id)
    enforce_run_owner_access(current_user, parent_snapshot)
    _parent_context, parent_metadata = _parent_run_context(parent_snapshot)
    parent_role = normalize_agent_role(parent_snapshot.get("agent_role") or parent_metadata.get("agent_role"))
    if parent_role != "orchestrator":
        raise HTTPException(status_code=400, detail=invalid_detail)
    return parent_snapshot, parent_metadata, parent_role


def _delegation_root_run_id(
    parent_run_id: str,
    *,
    parent_snapshot: dict[str, Any],
    parent_metadata: dict[str, Any],
    normalize_run_id_token: Callable[[Any], str | None],
) -> str:
    return (
        normalize_run_id_token(
            parent_snapshot.get("delegation_root_run_id")
            or parent_metadata.get("delegation_root_run_id")
            or parent_run_id
        )
        or parent_run_id
    )


def build_delegate_run_children_callbacks(
    *,
    lookup_run_snapshot: Callable[[str], dict[str, Any]],
    enforce_run_owner_access: Callable[[Any, Any], None],
    normalize_agent_role: Callable[[Any], str],
    build_delegated_run_request: Callable[..., Any],
    execute_system_run_start_request_via_turn_runtime: Callable[..., dict[str, Any]],
    stamp_request_owner_fn: Callable[..., Any],
    run_execution_services: Callable[[], Any],
    normalize_run_id_token: Callable[[Any], str | None],
    refresh_parent_delegation_state: Callable[[str], Any],
) -> dict[str, Any]:
    return {
        "lookup_run_snapshot": lookup_run_snapshot,
        "enforce_run_owner_access": enforce_run_owner_access,
        "normalize_agent_role": normalize_agent_role,
        "build_delegated_run_request": build_delegated_run_request,
        "execute_system_run_start_request_via_turn_runtime": execute_system_run_start_request_via_turn_runtime,
        "stamp_request_owner_fn": stamp_request_owner_fn,
        "run_execution_services": run_execution_services,
        "normalize_run_id_token": normalize_run_id_token,
        "refresh_parent_delegation_state": refresh_parent_delegation_state,
    }


def build_auto_delegate_run_children_callbacks(
    *,
    lookup_run_snapshot: Callable[[str], dict[str, Any]],
    enforce_run_owner_access: Callable[[Any, Any], None],
    normalize_agent_role: Callable[[Any], str],
    build_auto_delegation_plan: Callable[..., list[dict[str, Any]]],
    emit_auto_delegation_routing_log: Callable[..., Any] | None,
    build_delegated_run_request: Callable[..., Any],
    execute_system_run_start_request_via_turn_runtime: Callable[..., dict[str, Any]],
    stamp_request_owner_fn: Callable[..., Any],
    run_execution_services: Callable[[], Any],
    normalize_run_id_token: Callable[[Any], str | None],
    refresh_parent_delegation_state: Callable[[str], Any],
) -> dict[str, Any]:
    callbacks = build_delegate_run_children_callbacks(
        lookup_run_snapshot=lookup_run_snapshot,
        enforce_run_owner_access=enforce_run_owner_access,
        normalize_agent_role=normalize_agent_role,
        build_delegated_run_request=build_delegated_run_request,
        execute_system_run_start_request_via_turn_runtime=execute_system_run_start_request_via_turn_runtime,
        stamp_request_owner_fn=stamp_request_owner_fn,
        run_execution_services=run_execution_services,
        normalize_run_id_token=normalize_run_id_token,
        refresh_parent_delegation_state=refresh_parent_delegation_state,
    )
    callbacks.update(
        {
            "build_auto_delegation_plan": build_auto_delegation_plan,
            "emit_auto_delegation_routing_log": emit_auto_delegation_routing_log,
        }
    )
    return callbacks


def build_retry_failed_delegation_callbacks(
    *,
    lookup_run_snapshot: Callable[[str], dict[str, Any]],
    enforce_run_owner_access: Callable[[Any, Any], None],
    normalize_agent_role: Callable[[Any], str],
    find_run_relationships: Callable[[str, dict[str, Any]], tuple[Any, list[dict[str, Any]]]],
    normalize_run_id_token: Callable[[Any], str | None],
    parse_utc_ts: Callable[[Any], Any],
    build_retry_child_payload: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
    build_delegated_run_request: Callable[..., Any],
    execute_system_run_start_request_via_turn_runtime: Callable[..., dict[str, Any]],
    stamp_request_owner_fn: Callable[..., Any],
    run_execution_services: Callable[[], Any],
    refresh_parent_delegation_state: Callable[[str], Any],
) -> dict[str, Any]:
    callbacks = build_delegate_run_children_callbacks(
        lookup_run_snapshot=lookup_run_snapshot,
        enforce_run_owner_access=enforce_run_owner_access,
        normalize_agent_role=normalize_agent_role,
        build_delegated_run_request=build_delegated_run_request,
        execute_system_run_start_request_via_turn_runtime=execute_system_run_start_request_via_turn_runtime,
        stamp_request_owner_fn=stamp_request_owner_fn,
        run_execution_services=run_execution_services,
        normalize_run_id_token=normalize_run_id_token,
        refresh_parent_delegation_state=refresh_parent_delegation_state,
    )
    callbacks.update(
        {
            "find_run_relationships": find_run_relationships,
            "parse_utc_ts": parse_utc_ts,
            "build_retry_child_payload": build_retry_child_payload,
        }
    )
    return callbacks


def delegate_run_children(
    parent_run_id: str,
    *,
    body: Any,
    current_user: Any,
    lookup_run_snapshot: Callable[[str], dict[str, Any]],
    enforce_run_owner_access: Callable[[Any, Any], None],
    normalize_agent_role: Callable[[Any], str],
    build_delegated_run_request: Callable[..., Any],
    execute_system_run_start_request_via_turn_runtime: Callable[..., dict[str, Any]],
    stamp_request_owner_fn: Callable[..., Any],
    run_execution_services: Callable[[], Any],
    normalize_run_id_token: Callable[[Any], str | None],
    refresh_parent_delegation_state: Callable[[str], Any],
) -> dict[str, Any]:
    body.validate_fields()
    parent_snapshot, parent_metadata, parent_role = _orchestrator_parent(
        parent_run_id,
        current_user=current_user,
        lookup_run_snapshot=lookup_run_snapshot,
        enforce_run_owner_access=enforce_run_owner_access,
        normalize_agent_role=normalize_agent_role,
        invalid_detail="Delegation is only available from orchestrator-owned runs.",
    )
    note = str(body.note or "").strip() or None
    created = []
    trace_context = _resume_parent_trace_context(parent_snapshot)
    delegation_root_run_id = _delegation_root_run_id(
        parent_run_id,
        parent_snapshot=parent_snapshot,
        parent_metadata=parent_metadata,
        normalize_run_id_token=normalize_run_id_token,
    )
    for child in body.children:
        item_id = str(uuid.uuid4())
        target_role = normalize_agent_role(child.agent_role)
        if not target_role or target_role == "orchestrator":
            raise HTTPException(status_code=400, detail="Delegated child runs must target a specialist agent role.")
        child_payload = {
            "agent_role": target_role,
            "user_goal": child.user_goal,
            "business_plan": child.business_plan,
            "metadata": child.metadata if isinstance(child.metadata, dict) else {},
        }
        _enforce_delegation_child_decision(
            parent_run_id=parent_run_id,
            parent_snapshot=parent_snapshot,
            child_payload=child_payload,
        )
        delegated_req = build_delegated_run_request(parent_snapshot, child_payload, note=note)
        result = execute_system_run_start_request_via_turn_runtime(
            delegated_req,
            stamp_request_owner_fn=stamp_request_owner_fn,
            services=run_execution_services(),
        )
        child_run_id = str((result or {}).get("run_id") or "").strip()
        if trace_context is not None and child_run_id:
            _emit_trace(
                agent_trace_service.emit_delegation_started(
                    trace_context,
                    item_id,
                    child_run_id,
                    target_role,
                    target_role,
                    str(child.user_goal or "").strip() or None,
                ),
                operation=f"delegation.started:{parent_run_id}:{child_run_id}",
            )
            _emit_trace(
                agent_trace_service.emit_delegation_finished(
                    trace_context,
                    item_id,
                    child_run_id,
                    "ok",
                    f"Delegated child run {child_run_id} created.",
                ),
                operation=f"delegation.finished:{parent_run_id}:{child_run_id}",
            )
        created.append(
            {
                **result,
                "parent_run_id": parent_run_id,
                "delegation_root_run_id": delegation_root_run_id,
                "delegated_by_role": parent_role,
                "user_goal": child.user_goal,
            }
        )
    refresh_parent_delegation_state(parent_run_id)
    return {
        "ok": True,
        "parent_run_id": parent_run_id,
        "count": len(created),
        "items": created,
    }


def auto_delegate_run_children(
    parent_run_id: str,
    *,
    request_payload: Any,
    current_user: Any,
    lookup_run_snapshot: Callable[[str], dict[str, Any]],
    enforce_run_owner_access: Callable[[Any, Any], None],
    normalize_agent_role: Callable[[Any], str],
    build_auto_delegation_plan: Callable[..., list[dict[str, Any]]],
    emit_auto_delegation_routing_log: Callable[..., Any] | None,
    build_delegated_run_request: Callable[..., Any],
    execute_system_run_start_request_via_turn_runtime: Callable[..., dict[str, Any]],
    stamp_request_owner_fn: Callable[..., Any],
    run_execution_services: Callable[[], Any],
    normalize_run_id_token: Callable[[Any], str | None],
    refresh_parent_delegation_state: Callable[[str], Any],
) -> dict[str, Any]:
    request_payload.validate_fields()
    parent_snapshot, parent_metadata, parent_role = _orchestrator_parent(
        parent_run_id,
        current_user=current_user,
        lookup_run_snapshot=lookup_run_snapshot,
        enforce_run_owner_access=enforce_run_owner_access,
        normalize_agent_role=normalize_agent_role,
        invalid_detail="Auto-delegation is only available from orchestrator-owned runs.",
    )
    plan = build_auto_delegation_plan(parent_snapshot, max_children=int(request_payload.max_children or 3))
    if not plan:
        raise HTTPException(status_code=400, detail="No specialist delegation rules matched this run.")
    routing_source = str((((plan[0].get("metadata") if isinstance(plan[0], dict) else {}) or {}).get("auto_delegation_source") or "keyword")).strip()
    routing_reason = str((((plan[0].get("metadata") if isinstance(plan[0], dict) else {}) or {}).get("auto_delegation_reason") or "")).strip()
    if callable(emit_auto_delegation_routing_log):
        emit_auto_delegation_routing_log(parent_run_id, plan, strategy=routing_source, reason=routing_reason)
    note = str(request_payload.note or "").strip() or "Auto-planned by orchestrator rules."
    created = []
    trace_context = _resume_parent_trace_context(parent_snapshot)
    plan_id = str(uuid.uuid4())
    if trace_context is not None:
        _emit_trace(
            agent_trace_service.emit_plan_started(
                trace_context,
                plan_id,
                "Delegation Plan",
                note,
            ),
            operation=f"plan.started:{parent_run_id}",
        )
    delegation_root_run_id = _delegation_root_run_id(
        parent_run_id,
        parent_snapshot=parent_snapshot,
        parent_metadata=parent_metadata,
        normalize_run_id_token=normalize_run_id_token,
    )
    for index, child in enumerate(plan, start=1):
        item_id = str(uuid.uuid4())
        target_role = str(child.get("agent_role") or "").strip()
        if trace_context is not None:
            _emit_trace(
                agent_trace_service.emit_plan_item(
                    trace_context,
                    plan_id,
                    item_id,
                    index,
                    f"Delegate to {target_role or 'specialist'}",
                    "delegate",
                    target_role or "specialist",
                    [],
                    str(child.get("user_goal") or "").strip() or None,
                ),
                operation=f"plan.item.created:{parent_run_id}:{index}",
            )
        _enforce_delegation_child_decision(
            parent_run_id=parent_run_id,
            parent_snapshot=parent_snapshot,
            child_payload=child,
        )
        delegated_req = build_delegated_run_request(parent_snapshot, child, note=note)
        try:
            result = execute_system_run_start_request_via_turn_runtime(
                delegated_req,
                stamp_request_owner_fn=stamp_request_owner_fn,
                services=run_execution_services(),
                current_user=current_user,
            )
        except Exception as exc:
            if trace_context is not None:
                _emit_trace(
                    agent_trace_service.emit_plan_item_updated(
                        trace_context,
                        item_id,
                        "failed",
                        str(exc)[:280],
                    ),
                    operation=f"plan.item.updated:{parent_run_id}:{item_id}:failed",
                )
            raise
        child_run_id = str((result or {}).get("run_id") or "").strip()
        if trace_context is not None and child_run_id:
            _emit_trace(
                agent_trace_service.emit_delegation_started(
                    trace_context,
                    item_id,
                    child_run_id,
                    target_role or "specialist",
                    target_role or "specialist",
                    str(child.get("user_goal") or "").strip() or None,
                ),
                operation=f"delegation.started:{parent_run_id}:{child_run_id}",
            )
            _emit_trace(
                agent_trace_service.emit_delegation_finished(
                    trace_context,
                    item_id,
                    child_run_id,
                    "ok",
                    f"Delegated child run {child_run_id} created.",
                ),
                operation=f"delegation.finished:{parent_run_id}:{child_run_id}",
            )
            _emit_trace(
                agent_trace_service.emit_plan_item_updated(
                    trace_context,
                    item_id,
                    "done",
                    f"Started child run {child_run_id}.",
                ),
                operation=f"plan.item.updated:{parent_run_id}:{item_id}:done",
            )
        created.append(
            {
                **result,
                "parent_run_id": parent_run_id,
                "delegation_root_run_id": delegation_root_run_id,
                "delegated_by_role": parent_role,
                "user_goal": child.get("user_goal"),
                "auto_delegation_rule": (child.get("metadata") or {}).get("auto_delegation_rule"),
            }
        )
    refresh_parent_delegation_state(parent_run_id)
    return {
        "ok": True,
        "parent_run_id": parent_run_id,
        "count": len(created),
        "note": note,
        "plan": plan,
        "items": created,
    }


def retry_failed_delegation_runs(
    parent_run_id: str,
    *,
    request_payload: Any,
    current_user: Any,
    lookup_run_snapshot: Callable[[str], dict[str, Any]],
    enforce_run_owner_access: Callable[[Any, Any], None],
    normalize_agent_role: Callable[[Any], str],
    find_run_relationships: Callable[[str, dict[str, Any]], tuple[Any, list[dict[str, Any]]]],
    normalize_run_id_token: Callable[[Any], str | None],
    parse_utc_ts: Callable[[Any], Any],
    build_retry_child_payload: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
    build_delegated_run_request: Callable[..., Any],
    execute_system_run_start_request_via_turn_runtime: Callable[..., dict[str, Any]],
    stamp_request_owner_fn: Callable[..., Any],
    run_execution_services: Callable[[], Any],
    refresh_parent_delegation_state: Callable[[str], Any],
) -> dict[str, Any]:
    request_payload.validate_fields()
    parent_snapshot, _parent_metadata, _parent_role = _orchestrator_parent(
        parent_run_id,
        current_user=current_user,
        lookup_run_snapshot=lookup_run_snapshot,
        enforce_run_owner_access=enforce_run_owner_access,
        normalize_agent_role=normalize_agent_role,
        invalid_detail="Retry delegation is only available from orchestrator-owned runs.",
    )
    _, child_runs = find_run_relationships(parent_run_id, parent_snapshot)
    if not child_runs:
        raise HTTPException(status_code=400, detail="This orchestrator run does not have delegated child runs.")
    _enforce_delegation_merge_retry_decision(
        parent_run_id=parent_run_id,
        parent_snapshot=parent_snapshot,
        child_runs=child_runs,
    )
    latest_by_lineage: dict[str, dict[str, Any]] = {}
    for child in child_runs:
        lineage_key = (
            normalize_run_id_token(child.get("retry_root_run_id"))
            or normalize_run_id_token(child.get("retry_of_run_id"))
            or normalize_run_id_token(child.get("run_id"))
            or str(child.get("run_id") or "")
        )
        previous = latest_by_lineage.get(lineage_key)
        child_sort_key = (
            parse_utc_ts(child.get("updated_at")) or parse_utc_ts(child.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc),
            str(child.get("run_id") or ""),
        )
        previous_sort_key = (
            parse_utc_ts(previous.get("updated_at")) or parse_utc_ts(previous.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc),
            str(previous.get("run_id") or ""),
        ) if isinstance(previous, dict) else None
        if previous_sort_key is None or child_sort_key > previous_sort_key:
            latest_by_lineage[lineage_key] = child
    failed_effective_children = [
        child for child in latest_by_lineage.values()
        if str(child.get("status") or "").strip().lower() in {"failed", "error", "timeout", "cancelled", "stopped"}
    ]
    if request_payload.failed_run_ids:
        allowed = set(request_payload.failed_run_ids)
        failed_effective_children = [
            child for child in failed_effective_children if str(child.get("run_id") or "").strip() in allowed
        ]
    if not failed_effective_children:
        raise HTTPException(status_code=400, detail="No retryable failed child runs were found for this orchestrator run.")
    note = str(request_payload.note or "").strip() or "Retry requested from orchestration summary."
    created = []
    trace_context = _resume_parent_trace_context(parent_snapshot)
    for child in failed_effective_children:
        _enforce_retry_run_orchestration_decision(
            parent_snapshot=parent_snapshot,
            child_snapshot=child,
        )
        item_id = str(uuid.uuid4())
        child_payload = build_retry_child_payload(parent_snapshot, child, note=note)
        _enforce_delegation_child_decision(
            parent_run_id=parent_run_id,
            parent_snapshot=parent_snapshot,
            child_payload=child_payload,
        )
        delegated_req = build_delegated_run_request(parent_snapshot, child_payload, note=note)
        result = execute_system_run_start_request_via_turn_runtime(
            delegated_req,
            stamp_request_owner_fn=stamp_request_owner_fn,
            services=run_execution_services(),
        )
        child_run_id = str((result or {}).get("run_id") or "").strip()
        target_role = str(child_payload.get("agent_role") or "").strip() or "specialist"
        if trace_context is not None and child_run_id:
            _emit_trace(
                agent_trace_service.emit_delegation_started(
                    trace_context,
                    item_id,
                    child_run_id,
                    target_role,
                    target_role,
                    str(child_payload.get("user_goal") or "").strip() or None,
                ),
                operation=f"delegation.started:{parent_run_id}:{child_run_id}:retry",
            )
            _emit_trace(
                agent_trace_service.emit_delegation_finished(
                    trace_context,
                    item_id,
                    child_run_id,
                    "ok",
                    f"Retry child run {child_run_id} created.",
                ),
                operation=f"delegation.finished:{parent_run_id}:{child_run_id}:retry",
            )
        created.append(
            {
                **result,
                "parent_run_id": parent_run_id,
                "retry_of_run_id": child_payload.get("metadata", {}).get("retry_of_run_id"),
                "retry_root_run_id": child_payload.get("metadata", {}).get("retry_root_run_id"),
                "retry_sequence": child_payload.get("metadata", {}).get("retry_sequence"),
                "agent_role": child_payload.get("agent_role"),
                "user_goal": child_payload.get("user_goal"),
            }
        )
    refresh_parent_delegation_state(parent_run_id)
    return {
        "ok": True,
        "parent_run_id": parent_run_id,
        "count": len(created),
        "note": note,
        "items": created,
    }

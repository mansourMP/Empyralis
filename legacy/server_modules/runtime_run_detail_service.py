from __future__ import annotations

from typing import Any, Callable, Optional

from server_modules import browser_checkpoint_service


FAILURE_STATUSES = {"failed", "error", "stopped", "timeout", "cancelled"}
TERMINAL_STATUSES = FAILURE_STATUSES | {"completed", "success"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _normalized_status(value: Any) -> str:
    return _text(value).lower()


def _latest_event(source: dict[str, Any], *event_names: str) -> Optional[dict[str, Any]]:
    events = source.get("events") if isinstance(source.get("events"), list) else []
    wanted = {_normalized_status(item) for item in event_names if _text(item)}
    for item in reversed(events):
        if not isinstance(item, dict):
            continue
        if _normalized_status(item.get("event")) in wanted:
            return item
    return None


def _latest_error_like_event(source: dict[str, Any]) -> Optional[dict[str, Any]]:
    events = source.get("events") if isinstance(source.get("events"), list) else []
    for item in reversed(events):
        if not isinstance(item, dict):
            continue
        event_name = _normalized_status(item.get("event"))
        level = _normalized_status(item.get("level"))
        if level == "error" or event_name in {
            "run_error",
            "timeout",
            "run_stopped",
            "approval_timeout",
            "runtime_restart_interrupted_run",
            "memory_context_error",
            "memory_write_error",
        }:
            return item
    return None


def build_run_diagnostics(
    *,
    source: dict[str, Any],
    metadata: dict[str, Any],
    pending_confirmation: Any,
    archived: bool,
) -> dict[str, Any]:
    status = _normalized_status(source.get("status"))
    schedule_id = _text(source.get("schedule_id") or metadata.get("schedule_id")) or None
    scheduled = bool(source.get("scheduled") or metadata.get("scheduled") or schedule_id)
    selected_target = _normalized_status(
        source.get("execution_target_selected")
        or metadata.get("execution_target_selected")
        or source.get("execution_target")
        or metadata.get("execution_target")
    ) or None
    waiting_for_runtime = bool(
        source.get("execution_target_waiting_for_runtime")
        or metadata.get("execution_target_waiting_for_runtime")
    )
    waiting_for_capacity = bool(
        source.get("execution_target_waiting_for_capacity")
        or metadata.get("execution_target_waiting_for_capacity")
    )
    route_reason = _text(source.get("execution_target_reason") or metadata.get("execution_target_reason")) or None
    route_fallback = _text(source.get("execution_target_fallback") or metadata.get("execution_target_fallback")) or None
    local_last_heartbeat_at = source.get("local_last_heartbeat_at")
    result_data = source.get("result_data") if isinstance(source.get("result_data"), dict) else {}
    browser_resume_supported = browser_checkpoint_service.browser_resume_supported(source, metadata)
    local_execution_resume_supported = bool(
        source.get("local_execution_checkpoint")
        or result_data.get("local_execution_checkpoint")
        or metadata.get("local_execution_checkpoint")
        or metadata.get("local_execution_resume_supported")
    )
    manual_takeover_active = bool(
        source.get("manual_takeover")
        or result_data.get("manual_takeover")
        or metadata.get("manual_takeover")
    )
    resumed_after_restart = bool(source.get("resume_after_confirmation_scheduled"))
    retry_of_run_id = _text(source.get("retry_of_run_id") or metadata.get("retry_of_run_id")) or None
    retry_root_run_id = _text(source.get("retry_root_run_id") or metadata.get("retry_root_run_id")) or None
    retry_sequence = source.get("retry_sequence") if isinstance(source.get("retry_sequence"), int) else metadata.get("retry_sequence")

    failure_event = _latest_event(
        source,
        "run_error",
        "timeout",
        "run_stopped",
        "approval_timeout",
        "runtime_restart_interrupted_run",
    ) or _latest_error_like_event(source)
    failure_message = _text(
        (failure_event or {}).get("message")
        or source.get("result")
        or source.get("result_summary")
    ) or None
    failure_event_name = _normalized_status((failure_event or {}).get("event")) or None

    category = "active"
    headline = "Run in progress"
    summary = route_reason or "The run is still in progress."
    next_step = None
    blocked_on = None
    local_status = None

    if pending_confirmation and isinstance(pending_confirmation, dict) and _text(pending_confirmation.get("approval_id")):
        category = "approval_wait"
        headline = "Waiting for confirmation"
        summary = _text(pending_confirmation.get("prompt")) or "This run is paused until a decision is made."
        next_step = "Approve or decline the pending action so the run can continue."
        blocked_on = "approval"
    elif manual_takeover_active and status == "waiting_for_input":
        category = "manual_takeover"
        headline = "Manual takeover active"
        summary = "AI execution is paused. You currently have control of the machine."
        next_step = (
            "Resume the run when you want the AI to continue from its saved checkpoint."
            if browser_resume_supported or local_execution_resume_supported
            else "Resume is not available until the run records a resumable checkpoint."
        )
        blocked_on = "manual_takeover"
        local_status = "paused_for_takeover"
    elif waiting_for_capacity:
        category = "local_capacity_wait"
        headline = "Waiting for local machine capacity"
        summary = route_reason or "A compatible local machine is online, but all capable machines are currently busy."
        next_step = "Keep a capable local machine online and wait for capacity to free up."
        blocked_on = "local_capacity"
        local_status = "waiting_for_capacity"
    elif waiting_for_runtime:
        category = "local_runtime_wait"
        headline = "Waiting for the right local machine"
        summary = route_reason or "This run requires a local machine with capabilities that are not online yet."
        next_step = "Bring a capable local machine online or adjust future execution targeting."
        blocked_on = "local_runtime"
        local_status = "waiting_for_runtime"
    elif resumed_after_restart and status == "waiting_for_input":
        category = "resume_pending"
        headline = "Resume queued after recovery"
        summary = (
            "This run was restored after a restart and is queued to resume from its saved checkpoint."
            if browser_resume_supported
            else "This run was restored after a restart and is queued to resume."
        )
        next_step = "Wait for Gateway to resume the run."
        blocked_on = "resume"
        local_status = "resuming_after_restart"
    elif status == "queued_local":
        category = "local_queue"
        headline = "Queued for local machine execution"
        summary = route_reason or "This run is queued for Gateway."
        next_step = "Keep Gateway online so the run can be claimed."
        local_status = "queued_local"
    elif status == "running_local":
        category = "local_running"
        headline = "Running on a local machine"
        summary = route_reason or "This run is currently executing on Gateway."
        next_step = "Monitor artifacts, workflow progress, or confirmations if the run pauses."
        local_status = "running_local"
    elif status in FAILURE_STATUSES:
        category = "failure"
        headline = "Run failed"
        summary = failure_message or "This run stopped before it completed."
        if failure_event_name == "approval_timeout":
            next_step = "Re-run the task and resolve the approval before it expires."
            blocked_on = "approval_timeout"
        elif failure_event_name == "runtime_restart_interrupted_run":
            next_step = "Re-run the task now that the runtime is healthy again."
            blocked_on = "runtime_restart"
        else:
            next_step = "Open inspect, review the latest timeline and logs, then retry when the issue is understood."
    elif status in {"completed", "success"}:
        category = "completed"
        headline = "Run completed"
        summary = _text(source.get("result") or source.get("result_summary")) or "The run completed successfully."
        next_step = "Review the result, artifacts, or workflow trace if you need more detail."

    if scheduled and category not in {"failure", "completed"}:
        summary = f"{summary} Triggered by schedule." if summary else "Triggered by schedule."

    if route_fallback and category != "failure":
        summary = f"{summary} Fallback applied: {route_fallback}.".strip()

    return {
        "category": category,
        "headline": headline,
        "summary": summary,
        "next_step": next_step,
        "blocked_on": blocked_on,
        "failure_message": failure_message,
        "failure_event": failure_event_name,
        "scheduled": scheduled,
        "schedule_id": schedule_id,
        "selected_target": selected_target,
        "local_target": selected_target in {"local", "local_companion"},
        "local_status": local_status,
        "local_last_heartbeat_at": local_last_heartbeat_at,
        "browser_resume_supported": browser_resume_supported,
        "local_execution_resume_supported": local_execution_resume_supported,
        "manual_takeover_active": manual_takeover_active,
        "resumed_after_restart": resumed_after_restart,
        "retry_of_run_id": retry_of_run_id,
        "retry_root_run_id": retry_root_run_id,
        "retry_sequence": retry_sequence if isinstance(retry_sequence, int) else None,
        "archived": bool(archived),
    }


def can_view_sensitive_run_payload(user: Optional[dict]) -> bool:
    if not isinstance(user, dict):
        return False
    if bool(user.get("is_admin")):
        return True
    return str(user.get("auth_type") or "").strip() == "api_key"


def limited_run_context_view(context: dict) -> dict:
    if not isinstance(context, dict):
        return {}
    return {
        "workspace_id": context.get("workspace_id"),
        "workflow_id": context.get("workflow_id"),
        "user_goal": context.get("user_goal"),
        "business_plan": context.get("business_plan"),
    }


def limited_result_data_view(result_data: Any) -> Optional[dict]:
    if not isinstance(result_data, dict):
        return None
    execution_summary = result_data.get("execution_summary") if isinstance(result_data.get("execution_summary"), dict) else {}
    return {
        "summary": result_data.get("summary"),
        "pack_id": result_data.get("pack_id"),
        "execution_summary": {
            "risk_level": execution_summary.get("risk_level"),
            "next_action": execution_summary.get("next_action"),
            "approval_required": execution_summary.get("approval_required"),
            "approval_reason": execution_summary.get("approval_reason"),
            "estimated_time_saved_minutes": execution_summary.get("estimated_time_saved_minutes"),
        },
    }


def _latest_computer_proof(source: dict[str, Any], metadata: dict[str, Any]) -> Optional[dict[str, Any]]:
    result_data = source.get("result_data") if isinstance(source.get("result_data"), dict) else {}
    for candidate in (
        metadata.get("latest_computer_proof"),
        result_data.get("computer_proof"),
        result_data.get("latest_computer_proof"),
        source.get("computer_proof"),
        source.get("latest_computer_proof"),
    ):
        if isinstance(candidate, dict) and candidate:
            return dict(candidate)
    return None


def _route_payload(source: dict[str, Any]) -> dict[str, Any]:
    return {
        "requested": source.get("execution_target_requested"),
        "selected": source.get("execution_target_selected"),
        "reason": source.get("execution_target_reason"),
        "fallback": source.get("execution_target_fallback"),
        "required_capabilities": source.get("execution_target_required_capabilities"),
        "missing_capabilities": source.get("execution_target_missing_capabilities"),
        "matching_runtime_ids": source.get("execution_target_matching_runtime_ids"),
        "available_runtime_ids": source.get("execution_target_available_runtime_ids"),
        "busy_runtime_ids": source.get("execution_target_busy_runtime_ids"),
        "busy_runtime_labels": source.get("execution_target_busy_runtime_labels"),
        "queued_ahead_count": source.get("execution_target_queued_ahead_count"),
        "estimated_wait_band": source.get("execution_target_estimated_wait_band"),
        "waiting_for_runtime": source.get("execution_target_waiting_for_runtime"),
        "waiting_for_capacity": source.get("execution_target_waiting_for_capacity"),
        "preferred_runtime_id": source.get("execution_target_preferred_runtime_id"),
        "preferred_runtime_label": source.get("execution_target_preferred_runtime_label"),
        "preferred_runtime_reason": source.get("execution_target_preferred_runtime_reason"),
    }


def _context_metadata(source: dict[str, Any]) -> dict[str, Any]:
    context = source.get("context") if isinstance(source.get("context"), dict) else {}
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    return metadata


def _machine_interrupt_payload(source: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    context_metadata = _context_metadata(source)
    runtime_id = (
        _text(
            source.get("runtime_id")
            or source.get("local_worker_id")
            or context_metadata.get("runtime_id")
            or metadata.get("runtime_id")
        )
        or None
    )
    machine_id = (
        _text(
            source.get("machine_id")
            or context_metadata.get("machine_id")
            or metadata.get("machine_id")
            or runtime_id
        )
        or None
    )
    interrupt_requested = bool(
        source.get("interrupt_requested")
        or context_metadata.get("interrupt_requested")
        or metadata.get("interrupt_requested")
    )
    interrupt_reason = (
        _text(
            source.get("interrupt_reason")
            or context_metadata.get("interrupt_reason")
            or metadata.get("interrupt_reason")
        )
        or None
    )
    interrupt_scope = (
        _normalized_status(
            source.get("interrupt_scope")
            or context_metadata.get("interrupt_scope")
            or metadata.get("interrupt_scope")
        )
        or None
    )
    interrupt_requested_at = (
        source.get("interrupt_requested_at")
        or context_metadata.get("interrupt_requested_at")
        or metadata.get("interrupt_requested_at")
        or None
    )
    return {
        "machine_id": machine_id,
        "runtime_id": runtime_id,
        "interrupt_requested": interrupt_requested,
        "interrupt_reason": interrupt_reason,
        "interrupt_scope": interrupt_scope,
        "interrupt_requested_at": interrupt_requested_at,
    }


def _browser_session_profile(source: dict[str, Any], metadata: dict[str, Any]) -> Optional[str]:
    checkpoint = browser_checkpoint_service.browser_checkpoint_from_run(source)
    summary = browser_checkpoint_service.checkpoint_summary(checkpoint)
    return (
        _text(summary.get("session_profile") or metadata.get("browser_session_profile"))
        or None
    )


def _browser_introspection_payload(
    source: dict[str, Any],
    result_data: Any,
) -> Optional[dict[str, Any]]:
    payload = source.get("browser_introspection") if isinstance(source.get("browser_introspection"), dict) else None
    if isinstance(payload, dict) and payload:
        return dict(payload)
    return browser_checkpoint_service.browser_introspection_snapshot(source, result_data)


def build_run_browser_checkpoint_payload(
    *,
    run_id: str,
    source: dict[str, Any],
    metadata: dict[str, Any],
    include_sensitive: bool,
    archived: bool,
) -> dict[str, Any]:
    checkpoint = browser_checkpoint_service.browser_checkpoint_from_run(source)
    checkpoint_summary = browser_checkpoint_service.checkpoint_summary(checkpoint)
    session_profile = _browser_session_profile(source, metadata)
    if session_profile and not checkpoint_summary.get("session_profile"):
        checkpoint_summary = {
            **checkpoint_summary,
            "session_profile": session_profile,
        }
    browser_resume_supported = browser_checkpoint_service.browser_resume_supported(source, metadata)
    return {
        "run_id": run_id,
        "archived": bool(archived),
        "available": bool(checkpoint or browser_resume_supported or session_profile),
        "browser_resume_supported": browser_resume_supported,
        "session_profile": session_profile,
        "checkpoint_summary": checkpoint_summary,
        "checkpoint": checkpoint if include_sensitive and checkpoint else None,
        "browser_reviewed_approval_required": bool(metadata.get("browser_reviewed_approval_required")),
        "browser_immutable_plan_hash": _text(metadata.get("browser_immutable_plan_hash")) or None,
        "browser_execution_binding": (
            metadata.get("browser_execution_binding")
            if include_sensitive and isinstance(metadata.get("browser_execution_binding"), dict)
            else None
        ),
    }


def build_run_browser_session_payload(
    *,
    run_id: str,
    source: dict[str, Any],
    metadata: dict[str, Any],
    include_sensitive: bool,
    archived: bool,
) -> dict[str, Any]:
    checkpoint = browser_checkpoint_service.browser_checkpoint_from_run(source)
    checkpoint_summary = browser_checkpoint_service.checkpoint_summary(checkpoint)
    session_profile = _browser_session_profile(source, metadata)
    if session_profile and not checkpoint_summary.get("session_profile"):
        checkpoint_summary = {
            **checkpoint_summary,
            "session_profile": session_profile,
        }
    browser_introspection = _browser_introspection_payload(source, source.get("result_data"))
    browser_resume_supported = browser_checkpoint_service.browser_resume_supported(source, metadata)
    machine_interrupt = _machine_interrupt_payload(source, metadata)
    return {
        "run_id": run_id,
        "archived": bool(archived),
        "available": bool(checkpoint or browser_introspection or session_profile or browser_resume_supported),
        "browser_resume_supported": browser_resume_supported,
        "session_profile": session_profile,
        "checkpoint_summary": checkpoint_summary,
        "browser_introspection": browser_introspection if include_sensitive and isinstance(browser_introspection, dict) else None,
        "browser_reviewed_approval_required": bool(metadata.get("browser_reviewed_approval_required")),
        "browser_immutable_plan_hash": _text(metadata.get("browser_immutable_plan_hash")) or None,
        "browser_execution_binding": (
            metadata.get("browser_execution_binding")
            if include_sensitive and isinstance(metadata.get("browser_execution_binding"), dict)
            else None
        ),
        "machine_id": machine_interrupt.get("machine_id"),
        "runtime_id": machine_interrupt.get("runtime_id"),
    }


def build_archived_run_detail_response(
    *,
    run_id: str,
    snapshot: dict[str, Any],
    metadata: dict[str, Any],
    include_sensitive: bool,
    safe_context: dict[str, Any],
    parent_run: Any,
    child_runs: Any,
    delegation_summary: Any,
    connector_binding: Any,
    limited_result_data_view_fn: Callable[[Any], Optional[dict]],
    limited_node_states_view_fn: Callable[[Any], Any],
) -> dict[str, Any]:
    machine_interrupt = _machine_interrupt_payload(snapshot, metadata)
    computer_proof = _latest_computer_proof(snapshot, metadata)
    response = {
        "run_id": run_id,
        "engine": snapshot.get("engine", "orion"),
        "status": snapshot.get("status", "unknown"),
        "owner_user_id": snapshot.get("owner_user_id"),
        "owner_email": snapshot.get("owner_email"),
        "created_at": snapshot.get("created_at"),
        "updated_at": snapshot.get("updated_at"),
        "duration_ms": snapshot.get("duration_ms"),
        "time_to_first_value_ms": snapshot.get("time_to_first_value_ms"),
        "hitl_wait_total_ms": round(float(snapshot.get("hitl_wait_total_ms") or 0.0), 2),
        "usage_masked": snapshot.get("usage_masked"),
        "active_profile_id": snapshot.get("active_profile_id"),
        "active_profile_label": snapshot.get("active_profile_label"),
        "active_profile_provider": snapshot.get("active_profile_provider"),
        "active_profile_model": snapshot.get("active_profile_model"),
        "active_adapter": snapshot.get("active_adapter"),
        "requested_provider": snapshot.get("requested_provider"),
        "effective_provider": snapshot.get("effective_provider"),
        "requested_model": snapshot.get("requested_model"),
        "effective_model": snapshot.get("effective_model"),
        "provider_overridden": bool(snapshot.get("provider_overridden")),
        "model_overridden": bool(snapshot.get("model_overridden")),
        "fallback_used": bool(snapshot.get("fallback_used")),
        "result": snapshot.get("result_summary"),
        "result_data": snapshot.get("result_data") if include_sensitive else limited_result_data_view_fn(snapshot.get("result_data")),
        "agent_role": metadata.get("agent_role"),
        "agent_role_source": metadata.get("agent_role_source"),
        "parent_run_id": snapshot.get("parent_run_id"),
        "delegation_root_run_id": snapshot.get("delegation_root_run_id"),
        "delegated_by_run_id": snapshot.get("delegated_by_run_id"),
        "delegated_by_role": snapshot.get("delegated_by_role"),
        "delegation_note": snapshot.get("delegation_note"),
        "parent_run": parent_run,
        "child_runs": child_runs,
        "delegation_summary": delegation_summary,
        "connector_binding": connector_binding,
        "tool_capabilities": snapshot.get("tool_capabilities") if isinstance(snapshot.get("tool_capabilities"), list) else [],
        "approval_outcome": snapshot.get("approval_outcome"),
        "evidence_items": snapshot.get("evidence_items") if isinstance(snapshot.get("evidence_items"), list) else [],
        "run_detail_contract": snapshot.get("run_detail_contract") if isinstance(snapshot.get("run_detail_contract"), dict) else {},
        "node_states": snapshot.get("node_states") if include_sensitive else limited_node_states_view_fn(snapshot.get("node_states")),
        "tool_policy_precheck": snapshot.get("tool_policy_precheck") if include_sensitive else None,
        "tool_policy_audit": snapshot.get("tool_policy_audit") if include_sensitive else [],
        "memory_trace": snapshot.get("memory_trace") if include_sensitive else {},
        "pending_confirmation": snapshot.get("pending_confirmation") or snapshot.get("pending_approval"),
        "pending_approval": snapshot.get("pending_confirmation") or snapshot.get("pending_approval"),
        "browser_checkpoint": snapshot.get("browser_checkpoint") if include_sensitive else None,
        "computer_proof": computer_proof,
        "latest_computer_proof": computer_proof,
        "dag": snapshot.get("dag") if include_sensitive else None,
        "context": safe_context,
        "execution_target_requested": snapshot.get("execution_target_requested"),
        "execution_target_selected": snapshot.get("execution_target_selected"),
        "execution_target_reason": snapshot.get("execution_target_reason"),
        "execution_target_fallback": snapshot.get("execution_target_fallback"),
        "execution_target_required_capabilities": snapshot.get("execution_target_required_capabilities"),
        "execution_target_missing_capabilities": snapshot.get("execution_target_missing_capabilities"),
        "execution_target_matching_runtime_ids": snapshot.get("execution_target_matching_runtime_ids"),
        "execution_target_available_runtime_ids": snapshot.get("execution_target_available_runtime_ids"),
        "execution_target_busy_runtime_ids": snapshot.get("execution_target_busy_runtime_ids"),
        "execution_target_busy_runtime_labels": snapshot.get("execution_target_busy_runtime_labels"),
        "execution_target_queued_ahead_count": snapshot.get("execution_target_queued_ahead_count"),
        "execution_target_estimated_wait_band": snapshot.get("execution_target_estimated_wait_band"),
        "execution_target_waiting_for_runtime": snapshot.get("execution_target_waiting_for_runtime"),
        "execution_target_waiting_for_capacity": snapshot.get("execution_target_waiting_for_capacity"),
        "execution_target_preferred_runtime_id": snapshot.get("execution_target_preferred_runtime_id"),
        "execution_target_preferred_runtime_label": snapshot.get("execution_target_preferred_runtime_label"),
        "execution_target_preferred_runtime_reason": snapshot.get("execution_target_preferred_runtime_reason"),
        **machine_interrupt,
        "route": _route_payload(snapshot),
        "diagnostics": build_run_diagnostics(
            source=snapshot,
            metadata=metadata,
            pending_confirmation=snapshot.get("pending_confirmation") or snapshot.get("pending_approval"),
            archived=True,
        ),
        "archived": True,
    }
    if snapshot.get("fallback_reason"):
        response["fallback_reason"] = snapshot.get("fallback_reason")
    return response


def build_live_run_detail_response(
    *,
    run_id: str,
    run: dict[str, Any],
    snapshot: dict[str, Any],
    metadata: dict[str, Any],
    include_sensitive: bool,
    safe_context: dict[str, Any],
    parent_run: Any,
    child_runs: Any,
    delegation_summary: Any,
    connector_binding: Any,
    limited_result_data_view_fn: Callable[[Any], Optional[dict]],
    limited_node_states_view_fn: Callable[[Any], Any],
    trim_memory_trace_fn: Callable[[dict[str, Any]], Any],
    get_pending_confirmation_fn: Callable[[dict[str, Any]], Any],
) -> dict[str, Any]:
    pending_confirmation = get_pending_confirmation_fn(run) or None
    machine_interrupt = _machine_interrupt_payload(run, metadata)
    computer_proof = _latest_computer_proof(run, metadata)
    response = {
        "run_id": run_id,
        "engine": run.get("engine", "orion"),
        "status": run.get("status", "unknown"),
        "owner_user_id": str(metadata.get("owner_user_id") or "").strip() or None,
        "owner_email": str(metadata.get("owner_email") or "").strip().lower() or None,
        "created_at": run.get("created_at"),
        "updated_at": run.get("updated_at"),
        "duration_ms": run.get("duration_ms"),
        "time_to_first_value_ms": run.get("time_to_first_value_ms"),
        "hitl_wait_total_ms": round(float(run.get("_hitl_wait_total_ms", 0.0)), 2),
        "usage_masked": run.get("usage_masked"),
        "active_profile_id": snapshot.get("active_profile_id"),
        "active_profile_label": snapshot.get("active_profile_label"),
        "active_profile_provider": snapshot.get("active_profile_provider"),
        "active_profile_model": snapshot.get("active_profile_model"),
        "active_adapter": snapshot.get("active_adapter"),
        "requested_provider": snapshot.get("requested_provider"),
        "effective_provider": snapshot.get("effective_provider"),
        "requested_model": snapshot.get("requested_model"),
        "effective_model": snapshot.get("effective_model"),
        "provider_overridden": bool(snapshot.get("provider_overridden")),
        "model_overridden": bool(snapshot.get("model_overridden")),
        "fallback_used": bool(snapshot.get("fallback_used")),
        "result": run.get("result"),
        "result_data": run.get("result_data") if include_sensitive else limited_result_data_view_fn(run.get("result_data")),
        "agent_role": metadata.get("agent_role"),
        "agent_role_source": metadata.get("agent_role_source"),
        "parent_run_id": metadata.get("parent_run_id"),
        "delegation_root_run_id": metadata.get("delegation_root_run_id"),
        "delegated_by_run_id": metadata.get("delegated_by_run_id"),
        "delegated_by_role": metadata.get("delegated_by_role"),
        "delegation_note": metadata.get("delegation_note"),
        "parent_run": parent_run,
        "child_runs": child_runs,
        "delegation_summary": delegation_summary,
        "connector_binding": connector_binding,
        "tool_capabilities": snapshot.get("tool_capabilities") if isinstance(snapshot.get("tool_capabilities"), list) else [],
        "approval_outcome": snapshot.get("approval_outcome"),
        "evidence_items": snapshot.get("evidence_items") if isinstance(snapshot.get("evidence_items"), list) else [],
        "run_detail_contract": snapshot.get("run_detail_contract") if isinstance(snapshot.get("run_detail_contract"), dict) else {},
        "node_states": snapshot.get("node_states") if include_sensitive else limited_node_states_view_fn(snapshot.get("node_states")),
        "tool_policy_precheck": metadata.get("tool_policy_precheck") if include_sensitive else None,
        "tool_policy_audit": run.get("tool_policy_audit") if include_sensitive and isinstance(run.get("tool_policy_audit"), list) else [],
        "memory_trace": trim_memory_trace_fn(
            run.get("memory_trace") if include_sensitive and isinstance(run.get("memory_trace"), dict) else {}
        ),
        "pending_confirmation": pending_confirmation,
        "pending_approval": pending_confirmation,
        "browser_checkpoint": run.get("browser_checkpoint") if include_sensitive and isinstance(run.get("browser_checkpoint"), dict) else None,
        "computer_proof": computer_proof,
        "latest_computer_proof": computer_proof,
        "dag": run.get("dag") if include_sensitive else None,
        "context": safe_context,
        "execution_target_requested": metadata.get("execution_target_requested"),
        "execution_target_selected": metadata.get("execution_target_selected"),
        "execution_target_reason": metadata.get("execution_target_reason"),
        "execution_target_fallback": metadata.get("execution_target_fallback"),
        "execution_target_required_capabilities": metadata.get("execution_target_required_capabilities"),
        "execution_target_missing_capabilities": metadata.get("execution_target_missing_capabilities"),
        "execution_target_matching_runtime_ids": metadata.get("execution_target_matching_runtime_ids"),
        "execution_target_available_runtime_ids": metadata.get("execution_target_available_runtime_ids"),
        "execution_target_busy_runtime_ids": metadata.get("execution_target_busy_runtime_ids"),
        "execution_target_busy_runtime_labels": metadata.get("execution_target_busy_runtime_labels"),
        "execution_target_queued_ahead_count": metadata.get("execution_target_queued_ahead_count"),
        "execution_target_estimated_wait_band": metadata.get("execution_target_estimated_wait_band"),
        "execution_target_waiting_for_runtime": metadata.get("execution_target_waiting_for_runtime"),
        "execution_target_waiting_for_capacity": metadata.get("execution_target_waiting_for_capacity"),
        "execution_target_preferred_runtime_id": metadata.get("execution_target_preferred_runtime_id"),
        "execution_target_preferred_runtime_label": metadata.get("execution_target_preferred_runtime_label"),
        "execution_target_preferred_runtime_reason": metadata.get("execution_target_preferred_runtime_reason"),
        **machine_interrupt,
        "route": _route_payload(metadata),
        "diagnostics": build_run_diagnostics(
            source={
                **snapshot,
                "status": run.get("status", "unknown"),
                "result": run.get("result"),
                "local_last_heartbeat_at": run.get("local_last_heartbeat_at"),
                "resume_after_confirmation_scheduled": bool(run.get("_resume_after_confirmation_scheduled")),
                "browser_resume_supported": metadata.get("browser_resume_supported"),
                "scheduled": metadata.get("scheduled"),
                "schedule_id": metadata.get("schedule_id"),
            },
            metadata=metadata,
            pending_confirmation=pending_confirmation,
            archived=False,
        ),
        "archived": False,
    }
    if snapshot.get("fallback_reason"):
        response["fallback_reason"] = snapshot.get("fallback_reason")
    return response

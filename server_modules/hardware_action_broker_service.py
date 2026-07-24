from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import uuid

from server_modules import (
    agent_trace_service,
    # Kept on the facade module for existing tests/operator monkeypatches while
    # target execution lives in hardware_runtime_adapters.
    agent_registry_repository,
    execution_mode_policy,
    gateway_approval_service,
    gateway_execution_service,
    gateway_protocol_service,
    gateway_state_repository,
    hardware_access_policy_service,
    hardware_result_correlator_service,
    hardware_runtime_session_service,
    hardware_runtime_target_resolver,
    runtime_attachment_service,
    rust_runtime_kernel_client,
    secret_redaction_service,
    session_service,
    thread_service,
    virtual_computer_runtime,
)
from server_modules.capability_registry import resolve_capability
from server_modules.hardware_runtime_adapters import (
    cloud_computer_adapter,
    gateway_adapter,
    self_hosted_node_adapter,
)


HARDWARE_RUNTIME_SESSION_BINDING = "sage_hardware_action"
HARDWARE_RUNTIME_CHANNEL = "hardware_runtime"
HARDWARE_ACTION_STATES = {
    "ready",
    "running",
    "waiting_approval",
    "degraded",
    "offline",
    "failed",
    "terminated",
}
DEFAULT_GUARDED_RUNTIME_ACCESS_MODE = execution_mode_policy.GUARDED_RUNTIME_ACCESS_MODE
FULL_RUNTIME_ACCESS_MODE = execution_mode_policy.FULL_RUNTIME_ACCESS_MODE

_FRIENDLY_RUNTIME_LABEL = {
    "user_device_gateway": "Agent Computer",
    "self_hosted_node": "Server",
    "empyralis_cloud_computer": "Cloud Computer",
}

_CLOUD_COMPUTER_RUNTIME_REGISTRY: Any = None

normalize_hardware_capability_id = hardware_access_policy_service.normalize_hardware_capability_id
normalize_runtime_access_mode = hardware_access_policy_service.normalize_runtime_access_mode
_runtime_access_metadata = hardware_access_policy_service.runtime_access_metadata
_hardware_action_requires_software_approval = hardware_access_policy_service.hardware_action_requires_software_approval
_runtime_target_ids = hardware_runtime_target_resolver.runtime_target_ids
_runtime_edge = hardware_runtime_target_resolver.runtime_edge
_runtime_artifact_ids_from_response = hardware_result_correlator_service.runtime_artifact_ids_from_response
_artifact_ids_from_execution = hardware_result_correlator_service.artifact_ids_from_execution
_artifact_ids_from_artifact_records = hardware_result_correlator_service.artifact_ids_from_artifact_records
_emit_tool_started = hardware_result_correlator_service.emit_tool_started
_emit_tool_result = hardware_result_correlator_service.emit_tool_result
_emit_artifacts = hardware_result_correlator_service.emit_artifacts
_emit_approval_resolved = hardware_result_correlator_service.emit_approval_resolved
_emit_hardware_stop_transcript_event = hardware_result_correlator_service.emit_hardware_stop_transcript_event
_new_runtime_session_id = hardware_runtime_session_service.new_runtime_session_id
_normalize_state = hardware_runtime_session_service.normalize_state
_audit_event = hardware_runtime_session_service.audit_event
_session_view = hardware_runtime_session_service.session_view
_runtime_session_with_correlation = hardware_runtime_session_service.runtime_session_with_correlation
_create_runtime_session = hardware_runtime_session_service.create_runtime_session
_update_runtime_session = hardware_runtime_session_service.update_runtime_session


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> Dict[str, Any]:
    return dict(value or {}) if isinstance(value, dict) else {}


def _list_dicts(value: Any) -> List[Dict[str, Any]]:
    return [dict(item) for item in list(value or []) if isinstance(item, dict)]


def _new_run_id() -> str:
    return f"hrun_{uuid.uuid4().hex}"


def _new_request_id() -> str:
    return f"hreq_{uuid.uuid4().hex}"


def _runtime_action_connector_and_action(action_id: str) -> tuple[str, str]:
    token = _text(action_id).lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "browser.open": ("browser", "navigate"),
        "browser.open_url": ("browser", "navigate"),
        "browser.navigate": ("browser", "navigate"),
        "browser.new_tab": ("browser", "new_tab"),
        "browser.download_file": ("browser", "download_file"),
        "screenshot.capture": ("browser", "screenshot"),
        "browser.screenshot": ("browser", "screenshot"),
        "computer.click": ("computer", "click"),
        "computer.type": ("computer", "type"),
        "shell.exec": ("shell", "exec"),
        "shell.execute": ("shell", "exec"),
    }
    if token in aliases:
        return aliases[token]
    if "." in token:
        connector_id, action = token.split(".", 1)
        return connector_id, action
    return "hardware", token


def _runtime_action_argument_projection(arguments: Dict[str, Any]) -> Dict[str, Any]:
    projected: Dict[str, Any] = {}
    for key in ("url", "selector", "text", "input", "command", "x", "y"):
        if key in arguments:
            projected[key] = arguments.get(key)
    return projected


def _enforce_cloud_runtime_action_decision(
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: Optional[str],
    action_id: str,
    arguments: Dict[str, Any],
    run_id: str,
    thread_id: Optional[str],
    request_id: str,
    runtime_session_id: Optional[str],
) -> Dict[str, Any]:
    connector_id, connector_action = _runtime_action_connector_and_action(action_id)
    payload = {
        "operation": "execute_runtime_action",
        "runtime_session_binding": "cloud_computer_agent",
        "studio_agent_mode": "cloud_computer",
        "connector_id": connector_id,
        "action_id": connector_action,
        "agent_id": _text(user_id) or "sage",
        "tenant_id": _text(tenant_id) or "default",
        "workspace_id": _text(workspace_id) or "default",
        "runtime_session_id": _text(runtime_session_id) or f"pending:{_text(request_id)}",
        "run_id": _text(run_id),
        "thread_id": _text(thread_id),
        **_runtime_action_argument_projection(arguments),
    }
    decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
        "runtime-action-decision",
        payload,
    )
    next_action = _text(decision.get("next_action"))
    if next_action != "execute_cloud_runtime_action":
        raise RuntimeError("unexpected_next_action")
    return decision


def get_cloud_computer_runtime_registry() -> virtual_computer_runtime.VirtualComputerRuntimeRegistry:
    return cloud_computer_adapter.get_cloud_computer_runtime_registry()


async def _resolve_trace_context(
    trace_context: Any,
    *,
    trace_id: str,
    tenant_id: str,
    workspace_id: str,
    thread_id: Optional[str],
    run_id: str,
) -> Any:
    if trace_context is not None:
        return trace_context
    if not trace_id:
        return None
    return await agent_trace_service.resume_trace(
        trace_id=trace_id,
        tenant_id=_text(tenant_id) or "default",
        workspace_id=_text(workspace_id) or "default",
        thread_id=_text(thread_id) or None,
        run_id=run_id,
        root_agent_id="sage",
    )


def _runtime_session_approval_by_id(runtime_session: Dict[str, Any], approval_id: str) -> Optional[Dict[str, Any]]:
    token = _text(approval_id)
    if not token:
        return None
    for approval in _list_dicts(runtime_session.get("approvals")):
        if _text(approval.get("approval_id")) == token:
            return approval
    return None


def _runtime_session_raw_approval_payload(metadata: Dict[str, Any], approval_id: str) -> Dict[str, Any]:
    token = _text(approval_id)
    payloads = _dict(metadata.get("approval_execution_payloads"))
    payload = payloads.get(token) if token else None
    return _dict(payload)


async def _find_runtime_hardware_approval(
    approval_id: str,
    *,
    workspace_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    token = _text(approval_id)
    if not token:
        return None
    record = await session_service.find_runtime_session_by_approval_id(
        token,
        workspace_id=_text(workspace_id) or None,
    )
    if not isinstance(record, dict):
        return None
    metadata = _dict(record.get("metadata"))
    if _text(metadata.get("runtime_session_binding")) != HARDWARE_RUNTIME_SESSION_BINDING:
        return None
    request_payload = _runtime_session_raw_approval_payload(metadata, token)
    runtime_session = _runtime_session_with_correlation(
        _session_view(_text(record.get("session_id")), metadata),
        payload=request_payload,
        session_record=record,
    )
    approval = _runtime_session_approval_by_id(runtime_session, token)
    if not isinstance(approval, dict):
        return None
    request_payload = request_payload or _dict(approval.get("request_payload"))
    return {
        "approval": approval,
        "request_payload": request_payload,
        "runtime_session": runtime_session,
        "session_record": record,
        "metadata": metadata,
    }


async def get_runtime_hardware_approval(
    approval_id: str,
    *,
    workspace_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    match = await _find_runtime_hardware_approval(approval_id, workspace_id=workspace_id)
    if not isinstance(match, dict):
        return None
    approval = _dict(match.get("approval"))
    runtime_session = _dict(match.get("runtime_session"))
    return {
        **approval,
        "approval_id": _text(approval.get("approval_id")),
        "runtime_session_id": _text(runtime_session.get("session_id")),
        "runtime_target": _text(runtime_session.get("canonical_runtime_target")) or _text(runtime_session.get("runtime_target")),
        "workspace_id": _text(runtime_session.get("workspace_id")) or None,
        "run_id": _text(runtime_session.get("run_id")) or None,
        "trace_id": _text(runtime_session.get("trace_id")) or None,
    }


def _approval_resolution_status(decision: str) -> str:
    token = _text(decision).lower()
    if token in {"proceed", "approve", "approved", "yes", "y", "continue", "ok", "allow_once", "allow_session"}:
        return "approved"
    if token in {"hold", "reject", "rejected", "no", "n", "abort", "stop", "cancel", "deny", "denied"}:
        return "rejected"
    raise ValueError("Hardware approval decision must be approved or rejected.")


async def _set_runtime_session_approval_status(
    runtime_session: Dict[str, Any],
    *,
    approval_id: str,
    status: str,
    decision: str,
    actor: str,
    note: Optional[str],
    approval_scope: Optional[str],
    state: str,
    audit_action: str,
    extra_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    token = _text(approval_id)
    resolved_at = _utc_now_iso()
    approvals: List[Dict[str, Any]] = []
    found = False
    for item in _list_dicts(runtime_session.get("approvals")):
        approval = dict(item)
        if _text(approval.get("approval_id")) == token:
            found = True
            approval.update(
                {
                    "status": _text(status),
                    "decision": _text(decision),
                    "decision_actor": _text(actor) or "user",
                    "decision_note": _text(note) or None,
                    "approval_scope": _text(approval_scope) or "once",
                    "resolved_at": resolved_at,
                }
            )
        approvals.append(approval)
    if not found and token:
        approvals.append(
            {
                "approval_id": token,
                "status": _text(status),
                "decision": _text(decision),
                "decision_actor": _text(actor) or "user",
                "decision_note": _text(note) or None,
                "approval_scope": _text(approval_scope) or "once",
                "resolved_at": resolved_at,
            }
        )
    return await _update_runtime_session(
        runtime_session,
        state=state,
        audit_action=audit_action,
        reason=_text(note) or None,
        extra_metadata={
            "approval_id": token,
            "approval_decision": _text(decision),
            "approval_scope": _text(approval_scope) or "once",
            "approvals": approvals,
            **_dict(extra_metadata),
        },
    )


async def resolve_runtime_hardware_approval(
    approval_id: str,
    *,
    decision: str,
    actor: str,
    note: Optional[str] = None,
    workspace_id: Optional[str] = None,
    approval_scope: Optional[str] = None,
    timeout_seconds: Optional[int] = None,
) -> Dict[str, Any]:
    resolved_decision = _approval_resolution_status(decision)
    match = await _find_runtime_hardware_approval(approval_id, workspace_id=workspace_id)
    if not isinstance(match, dict):
        return {"status": "not_found", "approval_id": _text(approval_id)}
    approval = _dict(match.get("approval"))
    runtime_session = _dict(match.get("runtime_session"))
    request_payload = _dict(match.get("request_payload")) or _dict(approval.get("request_payload"))
    approval_id = _text(approval.get("approval_id")) or _text(approval_id)
    current_status = _text(approval.get("status")).lower()
    if current_status in {"executed", "completed", "running", "rejected", "denied", "failed", "terminated"}:
        return {
            "status": current_status,
            "approval": approval,
            "runtime_session": runtime_session,
            "trace_id": _text(runtime_session.get("trace_id")) or None,
        }

    capability_id = _text(request_payload.get("capability_id")) or _text(runtime_session.get("capability_id"))
    action_id = _text(request_payload.get("action_id") or request_payload.get("action")) or _text(runtime_session.get("action_id"))
    arguments = _dict(request_payload.get("arguments"))
    runtime_target = _text(request_payload.get("runtime_target")) or _text(runtime_session.get("canonical_runtime_target"))
    run_id = _text(request_payload.get("run_id")) or _text(runtime_session.get("run_id")) or _text(runtime_session.get("session_id"))
    trace_id = _text(request_payload.get("trace_id")) or _text(runtime_session.get("trace_id"))
    request_id = _text(request_payload.get("request_id")) or _text(runtime_session.get("request_id")) or approval_id
    thread_id = _text(request_payload.get("thread_id")) or _text(runtime_session.get("thread_id")) or None
    runtime_access_mode = _text(request_payload.get("runtime_access_mode")) or _text(runtime_session.get("runtime_access_mode"))
    trace_context = await _resolve_trace_context(
        None,
        trace_id=trace_id,
        tenant_id=_text(request_payload.get("tenant_id")) or _text(runtime_session.get("tenant_id")) or "default",
        workspace_id=_text(request_payload.get("workspace_id")) or _text(runtime_session.get("workspace_id")) or "default",
        thread_id=thread_id,
        run_id=run_id,
    )
    await _emit_approval_resolved(
        trace_context,
        runtime_session,
        approval_id=approval_id,
        decision=resolved_decision,
        actor=actor,
        note=note,
    )

    if resolved_decision == "rejected":
        runtime_session = await _set_runtime_session_approval_status(
            runtime_session,
            approval_id=approval_id,
            status="rejected",
            decision=resolved_decision,
            actor=actor,
            note=note,
            approval_scope=approval_scope,
            state="terminated",
            audit_action="hardware_action.approval_rejected",
        )
        await _emit_tool_result(
            trace_context,
            tool_call_id=request_id,
            status="terminated",
            summary="Hardware action denied.",
            capability_id=capability_id,
            arguments=arguments,
            runtime_session=runtime_session,
            runtime_target=runtime_target,
            request_id=request_id,
            action_id=action_id,
            metadata={"approval_id": approval_id, "approval_decision": resolved_decision},
        )
        return {
            "status": "rejected",
            "approval": _runtime_session_approval_by_id(runtime_session, approval_id) or approval,
            "runtime_session": runtime_session,
            "trace_id": trace_id,
        }

    runtime_session = await _set_runtime_session_approval_status(
        runtime_session,
        approval_id=approval_id,
        status="approved",
        decision=resolved_decision,
        actor=actor,
        note=note,
        approval_scope=approval_scope,
        state="running",
        audit_action="hardware_action.approval_approved",
    )
    if runtime_target == "empyralis_cloud_computer":
        result = await cloud_computer_adapter.execute_cloud_computer_action(
            tenant_id=_text(request_payload.get("tenant_id")) or _text(runtime_session.get("tenant_id")) or "default",
            workspace_id=_text(request_payload.get("workspace_id")) or _text(runtime_session.get("workspace_id")) or "default",
            user_id=_text(request_payload.get("user_id")) or _text(runtime_session.get("user_id")) or None,
            action_id=action_id,
            capability_id=capability_id,
            arguments=arguments,
            runtime_session=runtime_session,
            run_id=run_id,
            trace_id=trace_id,
            thread_id=thread_id,
            request_id=request_id,
            trace_context=trace_context,
            require_approval=False,
            runtime_access_mode=runtime_access_mode,
            cost_metadata=_dict(runtime_session.get("billing")),
            tool_call_id=request_id,
            runtime_registry_getter=get_cloud_computer_runtime_registry,
        )
    elif runtime_target == "self_hosted_node":
        result = await self_hosted_node_adapter.execute_self_hosted_node_action(
            tenant_id=_text(request_payload.get("tenant_id")) or _text(runtime_session.get("tenant_id")) or "default",
            workspace_id=_text(request_payload.get("workspace_id")) or _text(runtime_session.get("workspace_id")) or "default",
            user_id=_text(request_payload.get("user_id")) or _text(runtime_session.get("user_id")) or None,
            node_id=_text(request_payload.get("runtime_node_id") or request_payload.get("node_id")) or _text(runtime_session.get("runtime_node_id")) or None,
            action_id=action_id,
            capability_id=capability_id,
            arguments=arguments,
            runtime_session=runtime_session,
            run_id=run_id,
            trace_id=trace_id,
            thread_id=thread_id,
            request_id=request_id,
            trace_context=trace_context,
            require_approval=False,
            runtime_access_mode=runtime_access_mode,
            tool_call_id=request_id,
        )
    else:
        runtime_session = await _set_runtime_session_approval_status(
            runtime_session,
            approval_id=approval_id,
            status="failed",
            decision=resolved_decision,
            actor=actor,
            note="Unsupported runtime approval target.",
            approval_scope=approval_scope,
            state="failed",
            audit_action="hardware_action.approval_resume_failed",
            extra_metadata={"runtime_target": runtime_target, "failure_reason": "unsupported_runtime_target"},
        )
        result = {
            "status": "failed",
            "reason": "unsupported_runtime_target",
            "approval": _runtime_session_approval_by_id(runtime_session, approval_id) or approval,
            "runtime_session": runtime_session,
            "trace_id": trace_id,
        }
    final_session = _dict(result.get("runtime_session")) or runtime_session
    final_status = _text(result.get("status")).lower()
    approval_status = "executed" if final_status in {"completed", "running"} else "failed"
    final_session = await _set_runtime_session_approval_status(
        final_session,
        approval_id=approval_id,
        status=approval_status,
        decision=resolved_decision,
        actor=actor,
        note=note,
        approval_scope=approval_scope,
        state=_text(final_session.get("state")) or ("running" if final_status == "running" else "ready"),
        audit_action="hardware_action.approval_resumed",
        extra_metadata={
            "resume_status": final_status,
            "execution_request_id": request_id,
        },
    )
    result["approval"] = _runtime_session_approval_by_id(final_session, approval_id) or approval
    result["runtime_session"] = final_session
    result["approval_id"] = approval_id
    return result


async def record_gateway_approval_resolution(
    result: Dict[str, Any],
    *,
    actor: str = "user",
    note: Optional[str] = None,
) -> Dict[str, Any]:
    approval = _dict(result.get("approval"))
    request_payload = _dict(approval.get("request_payload"))
    if _text(request_payload.get("runtime_session_binding")) != HARDWARE_RUNTIME_SESSION_BINDING:
        return result
    session_id = _text(request_payload.get("runtime_session_id"))
    if not session_id:
        return result
    session_record = await session_service.get_session(session_id) or {}
    runtime_session = _runtime_session_with_correlation(
        _session_view(session_id, _dict(session_record.get("metadata")) or request_payload),
        payload=request_payload,
        session_record=session_record,
    )
    trace_id = _text(request_payload.get("trace_id")) or _text(runtime_session.get("trace_id"))
    run_id = _text(request_payload.get("run_id")) or _text(runtime_session.get("run_id")) or session_id
    trace_context = await _resolve_trace_context(
        None,
        trace_id=trace_id,
        tenant_id=_text(runtime_session.get("tenant_id")) or _text((session_record or {}).get("tenant_id")) or "default",
        workspace_id=_text(runtime_session.get("workspace_id")) or _text((session_record or {}).get("workspace_id")) or "default",
        thread_id=_text(request_payload.get("thread_id")) or _text(runtime_session.get("thread_id")) or None,
        run_id=run_id,
    )
    approval_id = _text(approval.get("approval_id"))
    decision = _text(approval.get("decision") or result.get("status")) or "resolved"
    if approval_id:
        await _emit_approval_resolved(
            trace_context,
            runtime_session,
            approval_id=approval_id,
            decision=decision,
            actor=actor,
            note=note,
        )
    status = _text(result.get("status")).lower()
    capability_id = _text(request_payload.get("capability_id")) or _text(runtime_session.get("capability_id"))
    tool_call_id = _text(request_payload.get("request_id")) or _text(runtime_session.get("request_id")) or approval_id or session_id
    if status in {"executed", "approved"} and isinstance(result.get("execution"), dict):
        execution = _dict(result.get("execution"))
        artifact_ids = _artifact_ids_from_execution(execution)
        await _emit_artifacts(trace_context, artifact_ids, capability_id, runtime_session=runtime_session)
        summary = gateway_adapter.execution_summary(capability_id, execution)
        runtime_session = await _update_runtime_session(
            runtime_session,
            state="ready",
            audit_action="hardware_action.approval_executed",
            artifacts=artifact_ids,
            extra_metadata={
                "approval_id": approval_id,
                "approval_decision": decision,
                "result_summary": summary,
                "execution_request_id": _text(execution.get("request_id")) or tool_call_id,
            },
        )
        await _emit_tool_result(
            trace_context,
            tool_call_id=tool_call_id,
            status="completed",
            summary=summary,
            artifact_ids=artifact_ids,
            capability_id=capability_id,
            arguments=_dict(request_payload.get("arguments")),
            runtime_session=runtime_session,
            runtime_target=_text(request_payload.get("runtime_target")),
            request_id=_text(request_payload.get("request_id")),
            action_id=_text(request_payload.get("action_id") or request_payload.get("action")),
            metadata={"approval_id": approval_id, "approval_decision": decision},
        )
        result["runtime_session"] = runtime_session
        result["artifacts"] = artifact_ids
        return result
    if status in {"rejected", "denied"} or decision in {"rejected", "denied"}:
        runtime_session = await _update_runtime_session(
            runtime_session,
            state="terminated",
            audit_action="hardware_action.approval_rejected",
            reason=_text(note) or "approval_rejected",
            extra_metadata={"approval_id": approval_id, "approval_decision": decision},
        )
        await _emit_tool_result(
            trace_context,
            tool_call_id=tool_call_id,
            status="terminated",
            summary="Hardware action denied.",
            capability_id=capability_id,
            arguments=_dict(request_payload.get("arguments")),
            runtime_session=runtime_session,
            runtime_target=_text(request_payload.get("runtime_target")),
            request_id=_text(request_payload.get("request_id")),
            action_id=_text(request_payload.get("action_id") or request_payload.get("action")),
            metadata={"approval_id": approval_id, "approval_decision": decision},
        )
        result["runtime_session"] = runtime_session
        return result
    return result


async def record_self_hosted_command_completion(completion: Dict[str, Any]) -> Dict[str, Any]:
    return await self_hosted_node_adapter.record_self_hosted_command_completion(completion)



async def execute_hardware_action(
    *,
    tenant_id: str,
    workspace_id: str,
    action_id: str,
    arguments: Optional[Dict[str, Any]] = None,
    runtime_target: str = "cloud_default",
    capability_id: Optional[str] = None,
    user_id: Optional[str] = None,
    gateway_id: Optional[str] = None,
    device_id: Optional[str] = None,
    node_id: Optional[str] = None,
    run_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    request_id: Optional[str] = None,
    session_id: Optional[str] = None,
    timeout_seconds: Optional[int] = None,
    trace_context: Any = None,
    require_approval: Optional[bool] = None,
    runtime_access_mode: Optional[str] = None,
    execution_mode: Optional[str] = None,
    cost_metadata: Optional[Dict[str, Any]] = None,
    agent_scope: str = "studio_agent",
    file_mount_grants: Optional[List[Dict[str, Any]]] = None,
    agent_install_id: Optional[str] = None,
) -> Dict[str, Any]:
    args = _dict(arguments)
    target_ids = _runtime_target_ids(runtime_target)
    runtime_target_id = target_ids["runtime_target"]
    canonical_target_id = target_ids["canonical_runtime_target"]
    resolved_capability_id = normalize_hardware_capability_id(action_id, capability_id)
    resolved_run_id = _text(run_id) or _new_run_id()
    resolved_request_id = _text(request_id) or _new_request_id()
    resolved_trace_id = _text(trace_id) or f"trace_{uuid.uuid4().hex}"
    resolved_runtime_access_mode = normalize_runtime_access_mode(runtime_access_mode, execution_mode=execution_mode)
    raw_access_or_execution_mode = _text(runtime_access_mode).lower()
    resolved_execution_mode = _text(execution_mode) or (
        raw_access_or_execution_mode
        if raw_access_or_execution_mode in set(execution_mode_policy.SUPPORTED_EXECUTION_MODES)
        else ("full_access" if resolved_runtime_access_mode == FULL_RUNTIME_ACCESS_MODE else "default")
    )
    tool_call_id = resolved_request_id
    initial_state = (
        "running"
        if canonical_target_id in {"user_device_gateway", "empyralis_cloud_computer", "self_hosted_node"}
        else "degraded"
    )
    if canonical_target_id == "empyralis_cloud_computer":
        _enforce_cloud_runtime_action_decision(
            tenant_id=_text(tenant_id) or "default",
            workspace_id=_text(workspace_id) or "default",
            user_id=user_id,
            action_id=_text(action_id),
            arguments=args,
            run_id=resolved_run_id,
            thread_id=thread_id,
            request_id=resolved_request_id,
            runtime_session_id=session_id or f"pending:{resolved_request_id}",
        )
    runtime_session = await _create_runtime_session(
        tenant_id=_text(tenant_id) or "default",
        workspace_id=_text(workspace_id) or "default",
        user_id=_text(user_id),
        runtime_target=runtime_target_id,
        canonical_runtime_target=canonical_target_id,
        gateway_id=gateway_id,
        device_id=device_id,
        node_id=node_id,
        action_id=_text(action_id),
        capability_id=resolved_capability_id,
        run_id=resolved_run_id,
        trace_id=resolved_trace_id,
        request_id=resolved_request_id,
        thread_id=thread_id,
        session_id=session_id,
        initial_state=initial_state,
        cost_metadata=cost_metadata,
        runtime_access_mode=resolved_runtime_access_mode,
        execution_mode=resolved_execution_mode,
    )
    resolved_trace_context = await _resolve_trace_context(
        trace_context,
        trace_id=resolved_trace_id,
        tenant_id=_text(tenant_id) or "default",
        workspace_id=_text(workspace_id) or "default",
        thread_id=thread_id,
        run_id=resolved_run_id,
    )
    await _emit_tool_started(
        resolved_trace_context,
        tool_call_id=tool_call_id,
        capability_id=resolved_capability_id,
        arguments=args,
    )

    if not resolved_capability_id or resolve_capability(resolved_capability_id, enforce_kill_switch=False) is None:
        reason = "unknown_hardware_capability"
        runtime_session = await _update_runtime_session(
            runtime_session,
            state="failed",
            audit_action="hardware_action.failed",
            reason=reason,
            extra_metadata={"failure_reason": reason},
        )
        await _emit_tool_result(
            resolved_trace_context,
            tool_call_id=tool_call_id,
            status="failed",
            summary="Hardware capability is not registered.",
            capability_id=resolved_capability_id,
            arguments=args,
            runtime_session=runtime_session,
            runtime_target=canonical_target_id,
            request_id=resolved_request_id,
            action_id=_text(action_id),
            metadata={"failure_reason": reason},
        )
        return {
            "status": "failed",
            "reason": reason,
            "runtime_session": runtime_session,
            "trace_id": resolved_trace_id,
        }

    if canonical_target_id == "cloud_default":
        reason = "hardware_action_requires_optional_runtime_target"
        runtime_session = await _update_runtime_session(
            runtime_session,
            state="degraded",
            audit_action="hardware_action.degraded",
            reason=reason,
            extra_metadata={"degraded_reason": reason},
        )
        await _emit_tool_result(
            resolved_trace_context,
            tool_call_id=tool_call_id,
            status="degraded",
            summary="Cloud chat remains available, but this hardware action needs a selected runtime target.",
            capability_id=resolved_capability_id,
            arguments=args,
            runtime_session=runtime_session,
            runtime_target=canonical_target_id,
            request_id=resolved_request_id,
            action_id=_text(action_id),
            metadata={"degraded_reason": reason},
        )
        return {
            "status": "degraded",
            "reason": reason,
            "runtime_session": runtime_session,
            "trace_id": resolved_trace_id,
        }

    # ── Emit progress so the chat doesn't go silent during hardware actions ──
    friendly_label = _FRIENDLY_RUNTIME_LABEL.get(canonical_target_id, "Computer")
    try:
        await agent_trace_service.emit_tool_progress(
            resolved_trace_context,
            tool_call_id=tool_call_id,
            message=f"Running {resolved_capability_id} on your {friendly_label}…",
            percent=0,
        )
    except Exception:
        pass

    if canonical_target_id == "empyralis_cloud_computer":
        return await cloud_computer_adapter.execute_cloud_computer_action(
            tenant_id=_text(tenant_id) or "default",
            workspace_id=_text(workspace_id) or "default",
            user_id=user_id,
            action_id=_text(action_id),
            capability_id=resolved_capability_id,
            arguments=args,
            runtime_session=runtime_session,
            run_id=resolved_run_id,
            trace_id=resolved_trace_id,
            thread_id=thread_id,
            request_id=resolved_request_id,
            trace_context=resolved_trace_context,
            require_approval=require_approval,
            runtime_access_mode=resolved_runtime_access_mode,
            cost_metadata=cost_metadata,
            tool_call_id=tool_call_id,
            runtime_registry_getter=get_cloud_computer_runtime_registry,
        )

    if canonical_target_id == "self_hosted_node":
        return await self_hosted_node_adapter.execute_self_hosted_node_action(
            tenant_id=_text(tenant_id) or "default",
            workspace_id=_text(workspace_id) or "default",
            user_id=user_id,
            node_id=node_id,
            action_id=_text(action_id),
            capability_id=resolved_capability_id,
            arguments=args,
            runtime_session=runtime_session,
            run_id=resolved_run_id,
            trace_id=resolved_trace_id,
            thread_id=thread_id,
            request_id=resolved_request_id,
            trace_context=resolved_trace_context,
            require_approval=require_approval,
            runtime_access_mode=resolved_runtime_access_mode,
            tool_call_id=tool_call_id,
        )

    return await gateway_adapter.execute_gateway_action(
        tenant_id=_text(tenant_id) or "default",
        workspace_id=_text(workspace_id) or "default",
        user_id=user_id,
        gateway_id=gateway_id,
        device_id=device_id,
        action_id=_text(action_id),
        capability_id=resolved_capability_id,
        arguments=args,
        runtime_session=runtime_session,
        run_id=resolved_run_id,
        trace_id=resolved_trace_id,
        thread_id=thread_id,
        request_id=resolved_request_id,
        trace_context=resolved_trace_context,
        require_approval=require_approval,
        runtime_access_mode=resolved_runtime_access_mode,
        timeout_seconds=timeout_seconds,
        tool_call_id=tool_call_id,
        runtime_target=canonical_target_id,
        agent_scope=agent_scope,
        file_mount_grants=file_mount_grants,
        agent_install_id=agent_install_id,
    )


async def stop_hardware_action(
    *,
    tenant_id: str,
    workspace_id: str,
    run_id: str,
    runtime_target: str = "user_device_gateway",
    gateway_id: Optional[str] = None,
    node_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    target_request_id: Optional[str] = None,
    request_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    reason: Optional[str] = None,
    session_id: Optional[str] = None,
    timeout_seconds: Optional[int] = None,
) -> Dict[str, Any]:
    target_ids = _runtime_target_ids(runtime_target)
    canonical_target_id = target_ids["canonical_runtime_target"]
    resolved_trace_id = _text(trace_id) or f"trace_{uuid.uuid4().hex}"
    resolved_request_id = _text(request_id) or _new_request_id()
    if canonical_target_id == "empyralis_cloud_computer":
        return await cloud_computer_adapter.stop_cloud_computer_action(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            run_id=run_id,
            target_ids=target_ids,
            trace_id=resolved_trace_id,
            target_request_id=target_request_id,
            request_id=resolved_request_id,
            thread_id=thread_id,
            reason=reason,
            session_id=session_id,
            runtime_registry_getter=get_cloud_computer_runtime_registry,
        )
    if canonical_target_id == "self_hosted_node":
        return await self_hosted_node_adapter.stop_self_hosted_node_action(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            run_id=run_id,
            target_ids=target_ids,
            node_id=node_id,
            trace_id=resolved_trace_id,
            target_request_id=target_request_id,
            request_id=resolved_request_id,
            thread_id=thread_id,
            reason=reason,
            session_id=session_id,
        )
    if canonical_target_id != "user_device_gateway":
        return {
            "status": "degraded",
            "reason": "stop_adapter_not_configured",
            "runtime_target": target_ids["runtime_target"],
            "canonical_runtime_target": canonical_target_id,
            "trace_id": resolved_trace_id,
        }
    return await gateway_adapter.stop_gateway_action(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        run_id=run_id,
        target_ids=target_ids,
        gateway_id=gateway_id,
        trace_id=resolved_trace_id,
        target_request_id=target_request_id,
        request_id=resolved_request_id,
        thread_id=thread_id,
        reason=reason,
        session_id=session_id,
        timeout_seconds=timeout_seconds,
    )

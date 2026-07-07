from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from server_modules import (
    agent_computer_permission_secret_model,
    agent_trace_service,
    execution_mode_policy,
    file_mount_security,
    gateway_approval_service,
    gateway_execution_service,
    gateway_protocol_service,
    gateway_registry_service,
    gateway_state_repository,
    hardware_access_policy_service,
    hardware_result_correlator_service,
    hardware_runtime_session_service,
    hardware_runtime_target_resolver,
    rust_runtime_kernel_client,
    runtime_policy,
)
from server_modules.hardware_runtime_adapters.common import dict_value, text


def _resolve_file_mount_for_gateway_action(
    capability_id: str,
    arguments: Dict[str, Any],
    file_mount_grants: Optional[List[Dict[str, Any]]],
) -> Dict[str, Any]:
    """Resolves which named mount (artifacts/project/shared/knowledge/local_root/
    connector_files) a filesystem.* gateway action is allowed to touch, and at
    what grant level, using the same file_mount_security path run_service.py's
    execute_workflow_local_tool() already uses for the equivalent workflow
    execution path. Returns arguments unchanged for non-filesystem capabilities
    (shell.execute has no single target path to resolve a mount against).

    The Gateway trusts this resolved mount NAME (not a host path — the Gateway
    itself, not the cloud, knows how mount names map to its own local disk
    layout) and mounts exactly that as the container's /workspace volume.
    """
    normalized_capability = text(capability_id).strip()
    if not normalized_capability.startswith("filesystem."):
        return arguments
    raw_path = arguments.get("path") or arguments.get("file_path")
    if not text(raw_path):
        # No path to resolve a mount against — let the Gateway's own
        # validation reject the missing-path case with its usual message.
        return arguments
    raw_mode = arguments.get("mode") or arguments.get("operation") or (
        "write" if normalized_capability == "filesystem.write" else "read"
    )
    execution_target = runtime_policy.normalize_execution_target(
        arguments.get("execution_target") or runtime_policy.EXECUTION_TARGET_LOCAL_COMPANION
    )
    file_access = file_mount_security.assert_file_mount_access(
        raw_path,
        raw_mode,
        file_mount_grants,
        execution_target,
    )
    resolved_arguments = dict(arguments)
    resolved_arguments["mount"] = file_access["mount"]
    return resolved_arguments


HARDWARE_RUNTIME_SESSION_BINDING = hardware_runtime_session_service.HARDWARE_RUNTIME_SESSION_BINDING
FULL_RUNTIME_ACCESS_MODE = execution_mode_policy.FULL_RUNTIME_ACCESS_MODE
DEFAULT_AGENT_COMPUTER_TOOL_TIMEOUT_SECONDS = 120
LOGGER = logging.getLogger(__name__)


def _actor_role_from_runtime_context(*, runtime_access_mode: str, user_id: Optional[str]) -> str:
    if text(runtime_access_mode).lower() == FULL_RUNTIME_ACCESS_MODE:
        return "owner"
    if text(user_id):
        return "member"
    return "viewer"


def _enforce_gateway_action_decision(
    *,
    operation: str,
    tenant_id: str,
    workspace_id: str,
    gateway_id: str,
    run_id: str,
    request_id: str,
    capability_id: str,
    user_id: Optional[str],
    runtime_access_mode: str,
    risk_decision: str,
    approval_provided: bool,
) -> Dict[str, Any]:
    payload = {
        "operation": operation,
        "tenant_id": text(tenant_id) or "default",
        "workspace_id": text(workspace_id) or "default",
        "gateway_id": text(gateway_id),
        "run_id": text(run_id),
        "request_id": text(request_id),
        "capability_id": text(capability_id),
        "actor_role": _actor_role_from_runtime_context(
            runtime_access_mode=runtime_access_mode,
            user_id=user_id,
        ),
        "gateway_connected": True,
        "risk_decision": text(risk_decision) or "allow",
        "approval_provided": bool(approval_provided),
    }
    try:
        decision = rust_runtime_kernel_client.gateway_action_decision(**payload)
        decision = rust_runtime_kernel_client.enforce_kernel_decision(
            "gateway-action-decision",
            decision,
            allow_approval_required=operation == "tool_execute",
        )
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        reason = text(getattr(exc, "reason", "")) or "rust_gateway_action_denied"
        raise RuntimeError(f"Rust gateway-action gate blocked {operation}: {reason}") from exc
    expected_next_action = {
        ("tool_execute", "allow"): "dispatch_tool_invoke",
        ("tool_execute", "require_approval"): "request_gateway_tool_approval",
        ("browser_session_stop", "allow"): "stop_browser_session",
    }.get((operation, text(decision.get("decision")).lower()))
    next_action = text(decision.get("next_action"))
    if expected_next_action and next_action != expected_next_action:
        raise RuntimeError(
            f"Rust gateway-action gate returned unexpected next_action for {operation}: "
            f"{next_action or 'missing'}"
        )
    return decision


def find_gateway_registration(
    *,
    gateway_id: Optional[str],
    workspace_id: str,
) -> Optional[Dict[str, Any]]:
    gateway_token = text(gateway_id)
    if gateway_token:
        return gateway_state_repository.get_gateway_registration(gateway_token)
    registrations = gateway_state_repository.list_workspace_gateway_registrations(
        text(workspace_id) or "default",
        include_revoked=False,
    )
    active: List[Dict[str, Any]] = [
        item
        for item in registrations
        if text(item.get("status")).lower() == "active"
        and text(item.get("device_trust_state")).lower() != "revoked"
    ]
    first_live: Optional[Dict[str, Any]] = None
    for registration in active:
        candidate_gateway_id = text(registration.get("gateway_id"))
        if not candidate_gateway_id or not gateway_protocol_service.gateway_connection_is_live(candidate_gateway_id):
            continue
        if first_live is None:
            first_live = registration
        try:
            public_payload = gateway_registry_service.gateway_registration_public_payload(registration)
            if text(public_payload.get("connection_status")).lower() == "online":
                return registration
        except Exception:
            return registration
    return first_live or (active[0] if active else None)


def registration_is_usable(registration: Optional[Dict[str, Any]], *, workspace_id: str) -> tuple[bool, str]:
    if not isinstance(registration, dict) or not registration:
        return False, "gateway_registration_missing"
    if text(registration.get("status")).lower() != "active":
        return False, "gateway_registration_inactive"
    if text(registration.get("device_trust_state")).lower() == "revoked":
        return False, "gateway_device_revoked"
    registration_workspace_id = text(registration.get("workspace_id"))
    if registration_workspace_id and registration_workspace_id != (text(workspace_id) or "default"):
        return False, "gateway_workspace_mismatch"
    return True, ""


def execution_summary(capability_id: str, execution: Dict[str, Any]) -> str:
    result = dict_value(execution.get("result"))
    summary = text(result.get("summary") or execution.get("summary"))
    if summary:
        return summary
    if capability_id == "shell.execute":
        command = text(result.get("command"))
        exit_code = result.get("exit_code")
        if command:
            return f"Ran command: {command}"
        if exit_code is not None:
            return f"Shell command finished with exit code {exit_code}."
    if capability_id.startswith("filesystem."):
        path = text(result.get("path"))
        mode = text(result.get("mode")) or "file"
        if path:
            return f"{mode.capitalize()} file action completed: {path}"
    return f"{capability_id or 'hardware action'} completed."


async def execute_gateway_action(
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: Optional[str],
    gateway_id: Optional[str],
    device_id: Optional[str],
    action_id: str,
    capability_id: str,
    arguments: Dict[str, Any],
    runtime_session: Dict[str, Any],
    run_id: str,
    trace_id: str,
    thread_id: Optional[str],
    request_id: str,
    trace_context: Any,
    require_approval: Optional[bool],
    runtime_access_mode: str,
    timeout_seconds: Optional[int],
    tool_call_id: str,
    runtime_target: str = "user_device_gateway",
    agent_scope: str = "studio_agent",
    file_mount_grants: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    try:
        arguments = _resolve_file_mount_for_gateway_action(capability_id, arguments, file_mount_grants)
    except Exception as exc:
        failure = agent_computer_permission_secret_model.local_failure_classification(exc)
        state = text(failure.get("state")) or "failed"
        reason = text(failure.get("reason")) or "file_mount_access_denied"
        summary = text(failure.get("summary")) or "File mount access denied."
        LOGGER.warning(
            "Gateway hardware action blocked by file-mount policy: type=%s reason=%s capability=%s request_id=%s",
            type(exc).__name__,
            reason,
            capability_id,
            request_id,
        )
        runtime_session = await hardware_runtime_session_service.update_runtime_session(
            runtime_session,
            state=state,
            audit_action="hardware_action.failed",
            reason=reason,
            extra_metadata={"failure_reason": reason},
        )
        await hardware_result_correlator_service.emit_tool_result(
            trace_context,
            tool_call_id=tool_call_id,
            status=state,
            summary=summary,
            capability_id=capability_id,
            arguments=arguments,
            runtime_session=runtime_session,
            runtime_target=runtime_target,
            request_id=request_id,
            action_id=action_id,
            metadata={"failure_reason": reason},
        )
        return {
            "status": state,
            "reason": reason,
            "runtime_session": runtime_session,
            "trace_id": trace_id,
        }
    registration = find_gateway_registration(
        gateway_id=gateway_id,
        workspace_id=text(workspace_id) or "default",
    )
    usable, unusable_reason = registration_is_usable(
        registration,
        workspace_id=text(workspace_id) or "default",
    )
    if not usable:
        runtime_session = await hardware_runtime_session_service.update_runtime_session(
            runtime_session,
            state="offline",
            audit_action="hardware_action.offline",
            reason=unusable_reason,
            extra_metadata={"offline_reason": unusable_reason},
        )
        await hardware_result_correlator_service.emit_tool_result(
            trace_context,
            tool_call_id=tool_call_id,
            status="offline",
            summary="Gateway is not available for this workspace.",
            capability_id=capability_id,
            arguments=arguments,
            runtime_session=runtime_session,
            runtime_target=runtime_target,
            request_id=request_id,
            action_id=action_id,
            metadata={"offline_reason": unusable_reason},
        )
        return {
            "status": "offline",
            "reason": unusable_reason,
            "runtime_session": runtime_session,
            "trace_id": trace_id,
        }

    gateway_token = text((registration or {}).get("gateway_id"))
    device_token = text(device_id) or text((registration or {}).get("device_id"))
    ready, not_ready_reason = gateway_execution_service.gateway_registration_execution_readiness(
        registration,
        workspace_id=text(workspace_id) or "default",
        capability_id=capability_id,
    )
    if not ready:
        reason = not_ready_reason or "gateway_unavailable"
        runtime_session = await hardware_runtime_session_service.update_runtime_session(
            runtime_session,
            state="offline",
            audit_action="hardware_action.offline",
            reason=reason,
            extra_metadata={"gateway_id": gateway_token, "device_id": device_token, "offline_reason": reason},
        )
        await hardware_result_correlator_service.emit_tool_result(
            trace_context,
            tool_call_id=tool_call_id,
            status="offline",
            summary="Gateway is not ready for this hardware action.",
            capability_id=capability_id,
            arguments=arguments,
            runtime_session=runtime_session,
            runtime_target=runtime_target,
            request_id=request_id,
            action_id=action_id,
            metadata={"gateway_id": gateway_token, "device_id": device_token, "offline_reason": reason},
        )
        return {
            "status": "offline",
            "reason": reason,
            "runtime_session": runtime_session,
            "trace_id": trace_id,
        }

    runtime_session = await hardware_runtime_session_service.update_runtime_session(
        runtime_session,
        state="running",
        audit_action="hardware_action.gateway_selected",
        extra_metadata={"gateway_id": gateway_token, "device_id": device_token},
    )

    approval_required = hardware_access_policy_service.hardware_action_requires_software_approval(
        runtime_access_mode=runtime_access_mode,
        capability_id=capability_id,
        action_id=action_id,
        arguments=arguments,
        require_approval=require_approval,
    )
    _enforce_gateway_action_decision(
        operation="tool_execute",
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        gateway_id=gateway_token,
        run_id=run_id,
        request_id=request_id,
        capability_id=capability_id,
        user_id=user_id,
        runtime_access_mode=runtime_access_mode,
        risk_decision="require_approval" if approval_required else "allow",
        approval_provided=False,
    )
    if approval_required:
        approval = await gateway_approval_service.request_gateway_tool_approval(
            registration=registration or {},
            capability_id=capability_id,
            arguments=arguments,
            run_id=run_id,
            trace_id=trace_id,
            request_id=request_id,
            runtime_session_id=text(runtime_session.get("session_id")),
            runtime_target=runtime_target,
            runtime_access_mode=runtime_access_mode,
            runtime_session_binding=HARDWARE_RUNTIME_SESSION_BINDING,
            thread_id=thread_id,
            agent_scope=agent_scope,
        )
        approval_id = text(approval.get("approval_id"))
        if approval_id:
            await agent_trace_service.emit_approval_requested(
                trace_context,
                approval_id=approval_id,
                kind="hardware_action",
                title=f"Approve {capability_id}",
                description=f"Approval required before running {capability_id} on the selected runtime.",
                blocking_item_id=None,
            )
        runtime_session = await hardware_runtime_session_service.update_runtime_session(
            runtime_session,
            state="waiting_approval",
            audit_action="hardware_action.approval_requested",
            approvals=[approval],
            extra_metadata={
                "gateway_id": gateway_token,
                "device_id": device_token,
                "approval_id": approval_id,
                "runtime_access_mode": runtime_access_mode,
            },
        )
        await hardware_result_correlator_service.emit_tool_result(
            trace_context,
            tool_call_id=tool_call_id,
            status="waiting_approval",
            summary=f"Waiting for approval to run {capability_id}.",
            capability_id=capability_id,
            arguments=arguments,
            runtime_session=runtime_session,
            runtime_target=runtime_target,
            request_id=request_id,
            action_id=action_id,
            metadata={"gateway_id": gateway_token, "device_id": device_token, "approval_id": approval_id},
        )
        return {
            "status": "waiting_approval",
            "approval": approval,
            "runtime_session": runtime_session,
            "trace_id": trace_id,
        }

    try:
        execution = await gateway_execution_service.execute_tool_via_gateway(
            gateway_id=gateway_token,
            capability_id=capability_id,
            arguments=arguments,
            run_id=run_id,
            trace_id=trace_id,
            workspace_id=text(workspace_id) or "default",
            timeout_seconds=int(timeout_seconds or DEFAULT_AGENT_COMPUTER_TOOL_TIMEOUT_SECONDS),
            request_id=request_id,
            runtime_access_mode=runtime_access_mode,
            empyralis_approved=runtime_access_mode == FULL_RUNTIME_ACCESS_MODE,
            agent_scope=agent_scope,
        )
    except Exception as exc:
        failure = agent_computer_permission_secret_model.local_failure_classification(exc)
        state = text(failure.get("state")) or "failed"
        reason = text(failure.get("reason")) or "hardware_action_failed"
        summary = text(failure.get("summary")) or "Hardware action failed."
        LOGGER.warning(
            "Gateway hardware action failed after dispatch setup: type=%s reason=%s capability=%s request_id=%s",
            type(exc).__name__,
            reason,
            capability_id,
            request_id,
        )
        metadata = agent_computer_permission_secret_model.diagnostic_metadata(
            {"gateway_id": gateway_token, "device_id": device_token, "failure_reason": reason}
        )
        runtime_session = await hardware_runtime_session_service.update_runtime_session(
            runtime_session,
            state=state,
            audit_action="hardware_action.failed",
            reason=reason,
            extra_metadata=metadata,
        )
        await hardware_result_correlator_service.emit_tool_result(
            trace_context,
            tool_call_id=tool_call_id,
            status=state,
            summary=summary,
            capability_id=capability_id,
            arguments=arguments,
            runtime_session=runtime_session,
            runtime_target=runtime_target,
            request_id=request_id,
            action_id=action_id,
            metadata=metadata,
        )
        try:
            await agent_trace_service.emit_tool_progress(
                trace_context,
                tool_call_id=tool_call_id,
                message=f"Failed to run {capability_id} on your Agent Computer",
                percent=100,
            )
        except Exception:
            pass
        return {
            "status": state,
            "reason": reason,
            "runtime_session": runtime_session,
            "trace_id": trace_id,
        }

    # ── Emit progress completion for Mac gateway path ──
    try:
        await agent_trace_service.emit_tool_progress(
            trace_context,
            tool_call_id=tool_call_id,
            message=f"Completed {capability_id} on your Agent Computer",
            percent=100,
        )
    except Exception:
        pass

    artifact_ids = hardware_result_correlator_service.artifact_ids_from_execution(execution)
    await hardware_result_correlator_service.emit_artifacts(
        trace_context,
        artifact_ids,
        capability_id,
        runtime_session=runtime_session,
    )
    summary = execution_summary(capability_id, execution)
    runtime_session = await hardware_runtime_session_service.update_runtime_session(
        runtime_session,
        state="ready",
        audit_action="hardware_action.completed",
        artifacts=artifact_ids,
        extra_metadata={
            "gateway_id": gateway_token,
            "device_id": device_token,
            "result_summary": summary,
            "execution_request_id": text(execution.get("request_id")) or request_id,
        },
    )
    await hardware_result_correlator_service.emit_tool_result(
        trace_context,
        tool_call_id=tool_call_id,
        status="completed",
        summary=summary,
        artifact_ids=artifact_ids,
        capability_id=capability_id,
        arguments=arguments,
        runtime_session=runtime_session,
        runtime_target=runtime_target,
        request_id=request_id,
        action_id=action_id,
        metadata={"gateway_id": gateway_token, "device_id": device_token},
    )
    return {
        "status": "completed",
        "execution": execution,
        "runtime_session": runtime_session,
        "artifacts": artifact_ids,
        "trace_id": trace_id,
    }


async def stop_gateway_action(
    *,
    tenant_id: str,
    workspace_id: str,
    run_id: str,
    target_ids: Dict[str, str],
    gateway_id: Optional[str],
    trace_id: str,
    target_request_id: Optional[str],
    request_id: str,
    thread_id: Optional[str],
    reason: Optional[str],
    session_id: Optional[str],
    timeout_seconds: Optional[int],
) -> Dict[str, Any]:
    canonical_target_id = target_ids["canonical_runtime_target"]
    registration = find_gateway_registration(
        gateway_id=gateway_id,
        workspace_id=text(workspace_id) or "default",
    )
    usable, unusable_reason = registration_is_usable(
        registration,
        workspace_id=text(workspace_id) or "default",
    )
    if not usable:
        return {
            "status": "offline",
            "reason": unusable_reason,
            "trace_id": trace_id,
        }
    gateway_token = text((registration or {}).get("gateway_id"))
    if not gateway_protocol_service.gateway_connection_is_live(gateway_token):
        return {
            "status": "offline",
            "reason": "gateway_offline",
            "trace_id": trace_id,
        }
    _enforce_gateway_action_decision(
        operation="browser_session_stop",
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        gateway_id=gateway_token,
        run_id=run_id,
        request_id=request_id,
        capability_id="browser.session.interrupt",
        user_id=text((registration or {}).get("user_id")) or None,
        runtime_access_mode="guarded",
        risk_decision="allow",
        approval_provided=True,
    )
    try:
        interrupted = await gateway_execution_service.interrupt_tool_via_gateway(
            gateway_id=gateway_token,
            run_id=text(run_id),
            trace_id=trace_id,
            workspace_id=text(workspace_id) or "default",
            target_request_id=text(target_request_id) or None,
            reason=text(reason) or "operator_requested_stop",
            timeout_seconds=int(timeout_seconds or gateway_protocol_service.DEFAULT_TOOL_REQUEST_TIMEOUT_SECONDS),
            request_id=request_id,
        )
    except Exception as exc:
        return {
            "status": "failed",
            "reason": str(exc),
            "trace_id": trace_id,
        }
    if text(session_id):
        session_view = {
            "session_id": text(session_id),
            "runtime_session_binding": HARDWARE_RUNTIME_SESSION_BINDING,
            "state": "running",
            "runtime_target": target_ids["runtime_target"],
            "canonical_runtime_target": canonical_target_id,
            "runtime_fabric_target": canonical_target_id,
            "hardware_edge": hardware_runtime_target_resolver.runtime_edge(canonical_target_id),
            "tenant_id": text(tenant_id) or "default",
            "workspace_id": text(workspace_id) or "default",
            "thread_id": text(thread_id) or None,
            "gateway_id": gateway_token,
            "run_id": text(run_id),
            "trace_id": trace_id,
            "request_id": request_id,
            "capability_id": "tool.interrupt",
            "action_id": "hardware.stop",
            "approvals": [],
            "artifacts": [],
            "audit_events": [],
            "billing": {},
        }
        session_view = await hardware_runtime_session_service.update_runtime_session(
            session_view,
            state="terminated",
            audit_action="hardware_action.terminated",
            reason=text(reason) or "operator_requested_stop",
            extra_metadata={"gateway_id": gateway_token, "interrupt_request_id": request_id},
        )
        await hardware_result_correlator_service.emit_hardware_stop_transcript_event(
            session_view,
            runtime_target=canonical_target_id,
            target_request_id=target_request_id,
            reason=reason,
        )
    else:
        session_view = None
    return {
        "status": "terminated",
        "execution": interrupted,
        "runtime_session": session_view,
        "trace_id": trace_id,
    }

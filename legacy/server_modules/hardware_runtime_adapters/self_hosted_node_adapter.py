from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from server_modules import (
    agent_registry_repository,
    agent_trace_service,
    hardware_access_policy_service,
    hardware_activity_event_service,
    hardware_result_correlator_service,
    hardware_runtime_session_service,
    hardware_runtime_target_resolver,
    runtime_attachment_service,
    rust_runtime_kernel_client,
    secret_redaction_service,
    session_service,
)
from server_modules.hardware_runtime_adapters.common import dict_value, resolve_trace_context, text, utc_now_iso


HARDWARE_RUNTIME_SESSION_BINDING = hardware_runtime_session_service.HARDWARE_RUNTIME_SESSION_BINDING


def self_hosted_required_capabilities(capability_id: str) -> List[str]:
    if capability_id == "shell.execute":
        return ["shell.execute"]
    if capability_id.startswith("filesystem."):
        return ["file_access"]
    if capability_id == "browser_automation.interactive":
        return ["browser.automation"]
    if capability_id == "screenshot.capture" or capability_id.startswith("computer_control."):
        return ["computer_control"]
    return []


def self_hosted_capability_aliases(capability_id: str) -> set[str]:
    if capability_id == "tool.interrupt":
        return set()
    if capability_id == "shell.execute":
        return {"shell.execute", "terminal.exec", "shell", "command"}
    if capability_id.startswith("filesystem."):
        return {"file_access", "filesystem", "filesystem.read", "filesystem.write", "filesystem.read_write"}
    if capability_id == "browser_automation.interactive":
        return {"browser.automation", "browser_automation.interactive", "browser", "web"}
    if capability_id == "screenshot.capture":
        return {"screenshot.capture", "screenshot", "computer_control"}
    if capability_id.startswith("computer_control."):
        return {"computer_control", capability_id}
    return {capability_id} if capability_id else set()


def self_hosted_attachment_matches_node(attachment: Dict[str, Any], node_token: str) -> bool:
    if not node_token:
        return True
    candidate_values = {
        text(attachment.get("runtime_node_id")),
        text(attachment.get("runtime_id")),
        text(attachment.get("machine_id")),
        text(attachment.get("attachment_id")),
        text(attachment.get("runtime_profile_id")),
        text(attachment.get("runtime_profile_slug")),
    }
    return node_token in candidate_values


def self_hosted_capability_available(attachment: Dict[str, Any], capability_id: str) -> bool:
    aliases = {item.lower() for item in self_hosted_capability_aliases(capability_id)}
    if not aliases:
        return True
    available = {text(item).lower() for item in list(attachment.get("capabilities") or []) if text(item)}
    if not available:
        return False
    return bool(aliases & available)


def self_hosted_runtime_action_connector_and_action(action_id: str) -> tuple[str, str]:
    token = text(action_id).lower().replace("-", "_").replace(" ", "_")
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


def self_hosted_runtime_action_argument_projection(arguments: Dict[str, Any]) -> Dict[str, Any]:
    projected: Dict[str, Any] = {}
    for key in ("url", "selector", "text", "input", "command", "x", "y"):
        if key in arguments:
            projected[key] = arguments.get(key)
    return projected


def self_hosted_concurrency_exceeded(attachment: Dict[str, Any]) -> bool:
    active = attachment.get("active_session_count", attachment.get("active_sessions"))
    limit = attachment.get("max_concurrent_sessions")
    try:
        active_count = int(active or 0)
        max_count = int(limit or 0)
    except (TypeError, ValueError):
        return False
    return max_count > 0 and active_count >= max_count


def enforce_self_hosted_runtime_action_decision(
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: Optional[str],
    action_id: str,
    arguments: Dict[str, Any],
    runtime_session: Dict[str, Any],
    run_id: str,
    thread_id: Optional[str],
    attachment: Dict[str, Any],
) -> Dict[str, Any]:
    connector_id, connector_action = self_hosted_runtime_action_connector_and_action(action_id)
    payload = {
        "operation": "execute_runtime_action",
        "runtime_session_binding": "self_hosted_agent",
        "studio_agent_mode": "self_hosted",
        "connector_id": connector_id,
        "action_id": connector_action,
        "agent_id": text(user_id) or "sage",
        "tenant_id": text(tenant_id) or "default",
        "workspace_id": text(workspace_id) or "default",
        "runtime_session_id": text(runtime_session.get("session_id")),
        "run_id": text(run_id),
        "thread_id": text(thread_id),
        "self_hosted_node_gate_passed": True,
        "self_hosted_node_concurrency_exceeded": self_hosted_concurrency_exceeded(attachment),
        **self_hosted_runtime_action_argument_projection(arguments),
    }
    decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
        "runtime-action-decision",
        payload,
    )
    next_action = text(decision.get("next_action"))
    if next_action != "execute_self_hosted_runtime_action":
        raise RuntimeError(f"unexpected_next_action:{next_action or 'missing'}")
    return decision


async def select_self_hosted_attachment(
    *,
    tenant_id: str,
    workspace_id: str,
    node_id: Optional[str],
    capability_id: str,
) -> tuple[Optional[Dict[str, Any]], str]:
    inventory = await runtime_attachment_service.list_workspace_runtime_attachments(
        tenant_id=text(tenant_id) or "default",
        workspace_id=text(workspace_id) or "default",
    )
    node_token = text(node_id)
    attachments = [
        dict(item)
        for item in list((inventory or {}).get("attachments") or [])
        if isinstance(item, dict)
        and text(item.get("attachment_kind")) == "self_hosted_business_node"
        and self_hosted_attachment_matches_node(item, node_token)
    ]
    if not attachments:
        return None, "self_hosted_node_not_found"
    attachment = attachments[0]
    try:
        runtime_attachment_service.ensure_self_hosted_node_gate(
            attachment=attachment,
            workspace_id=text(workspace_id) or "default",
            required_capabilities=[],
        )
    except runtime_attachment_service.RuntimeAttachmentSelectionError as exc:
        return attachment, exc.reason
    if not self_hosted_capability_available(attachment, capability_id):
        return attachment, "self_hosted_node_capability_mismatch"
    return attachment, ""


def self_hosted_completion_summary(
    *,
    capability_id: str,
    status: str,
    result_payload: Dict[str, Any],
    error: Optional[str],
) -> str:
    if text(error):
        return text(error)
    summary = text(result_payload.get("summary") or result_payload.get("message") or result_payload.get("output_summary"))
    if summary:
        return summary
    if capability_id == "shell.execute":
        command = text(result_payload.get("command"))
        exit_code = result_payload.get("exit_code")
        if command:
            return f"Ran command: {command}" if status == "completed" else f"Command failed: {command}"
        if exit_code is not None:
            return f"Shell command finished with exit code {exit_code}."
    if capability_id.startswith("filesystem."):
        path = text(result_payload.get("path"))
        if path:
            return f"File action completed: {path}" if status == "completed" else f"File action failed: {path}"
    return "Self-hosted node action completed." if status == "completed" else "Self-hosted node action failed."


async def execute_self_hosted_node_action(
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: Optional[str],
    node_id: Optional[str],
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
    tool_call_id: str,
) -> Dict[str, Any]:
    attachment, unavailable_reason = await select_self_hosted_attachment(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        node_id=node_id,
        capability_id=capability_id,
    )
    if unavailable_reason:
        state = "offline" if unavailable_reason == "self_hosted_node_offline" else "degraded"
        if unavailable_reason == "self_hosted_node_revoked":
            state = "failed"
        runtime_session = await hardware_runtime_session_service.update_runtime_session(
            runtime_session,
            state=state,
            audit_action="hardware_action.self_hosted_unavailable",
            reason=unavailable_reason,
            extra_metadata={"unavailable_reason": unavailable_reason},
        )
        await hardware_result_correlator_service.emit_tool_result(
            trace_context,
            tool_call_id=tool_call_id,
            status=state,
            summary="Self-hosted node is not available for this hardware action.",
            capability_id=capability_id,
            arguments=arguments,
            runtime_session=runtime_session,
            runtime_target="self_hosted_node",
            request_id=request_id,
            action_id=action_id,
            metadata={"unavailable_reason": unavailable_reason},
        )
        return {"status": state, "reason": unavailable_reason, "runtime_session": runtime_session, "trace_id": trace_id}

    attachment = dict(attachment or {})
    enforce_self_hosted_runtime_action_decision(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
        action_id=action_id,
        arguments=arguments,
        runtime_session=runtime_session,
        run_id=run_id,
        thread_id=thread_id,
        attachment=attachment,
    )
    runtime_node_id = text(attachment.get("runtime_node_id") or attachment.get("runtime_id"))
    runtime_profile_id = text(attachment.get("runtime_profile_id"))
    runtime_attachment_id = text(attachment.get("attachment_id"))
    node_kind = text(attachment.get("node_kind"))
    approval_required = hardware_access_policy_service.hardware_action_requires_software_approval(
        runtime_access_mode=runtime_access_mode,
        capability_id=capability_id,
        action_id=action_id,
        arguments=arguments,
        require_approval=require_approval,
    )
    if approval_required:
        approval = {
            "approval_id": f"selfhostapproval_{uuid.uuid4().hex}",
            "status": "pending",
            "kind": "self_hosted_node_action",
            "requested_at": utc_now_iso(),
            "request_payload": {
                "runtime_target": "self_hosted_node",
                "runtime_access_mode": hardware_access_policy_service.normalize_runtime_access_mode(runtime_access_mode),
                "runtime_session_id": text(runtime_session.get("session_id")),
                "runtime_session_binding": HARDWARE_RUNTIME_SESSION_BINDING,
                "runtime_node_id": runtime_node_id,
                "runtime_profile_id": runtime_profile_id,
                "capability_id": capability_id,
                "action_id": action_id,
                "arguments": secret_redaction_service.sanitize_mapping(arguments),
                "run_id": run_id,
                "trace_id": trace_id,
                "thread_id": text(thread_id) or None,
                "request_id": request_id,
            },
        }
        approval_execution_payload = {
            **approval["request_payload"],
            "arguments": arguments,
            "user_id": text(user_id) or None,
            "runtime_attachment_id": runtime_attachment_id,
            "node_kind": node_kind or None,
        }
        await agent_trace_service.emit_approval_requested(
            trace_context,
            approval_id=approval["approval_id"],
            kind="hardware_action",
            title=f"Approve {capability_id}",
            description=f"Approval required before running {capability_id} on the self-hosted node.",
            blocking_item_id=None,
        )
        runtime_session = await hardware_runtime_session_service.update_runtime_session(
            runtime_session,
            state="waiting_approval",
            audit_action="hardware_action.approval_requested",
            approvals=[approval],
            extra_metadata={
                "runtime_node_id": runtime_node_id,
                "runtime_profile_id": runtime_profile_id,
                "approval_id": approval["approval_id"],
                "runtime_access_mode": hardware_access_policy_service.normalize_runtime_access_mode(runtime_access_mode),
                "approval_execution_payloads": {approval["approval_id"]: approval_execution_payload},
            },
        )
        await hardware_result_correlator_service.emit_tool_result(
            trace_context,
            tool_call_id=tool_call_id,
            status="waiting_approval",
            summary=f"Waiting for approval to run {capability_id} on the self-hosted node.",
            capability_id=capability_id,
            arguments=arguments,
            runtime_session=runtime_session,
            runtime_target="self_hosted_node",
            request_id=request_id,
            action_id=action_id,
            metadata={"approval_id": approval["approval_id"], "runtime_node_id": runtime_node_id, "runtime_profile_id": runtime_profile_id},
        )
        return {"status": "waiting_approval", "approval": approval, "runtime_session": runtime_session, "trace_id": trace_id}

    command_payload = {
        "runtime_session_binding": HARDWARE_RUNTIME_SESSION_BINDING,
        "runtime_session_id": text(runtime_session.get("session_id")),
        "runtime_target": "self_hosted_node",
        "runtime_access_mode": hardware_access_policy_service.normalize_runtime_access_mode(runtime_access_mode),
        "runtime_node_id": runtime_node_id,
        "runtime_profile_id": runtime_profile_id,
        "runtime_attachment_id": runtime_attachment_id,
        "node_kind": node_kind or None,
        "workspace_id": text(workspace_id) or "default",
        "tenant_id": text(tenant_id) or "default",
        "user_id": text(user_id) or None,
        "run_id": run_id,
        "trace_id": trace_id,
        "thread_id": text(thread_id) or None,
        "request_id": request_id,
        "capability_id": capability_id,
        "action_id": action_id,
        "arguments": arguments,
        "required_capabilities": self_hosted_required_capabilities(capability_id),
    }
    try:
        enqueued = await agent_registry_repository.enqueue_self_hosted_runtime_command(
            runtime_profile_id=runtime_profile_id,
            tenant_id=text(tenant_id) or "default",
            workspace_id=text(workspace_id) or "default",
            runtime_node_id=runtime_node_id,
            agent_id="sage",
            command_type="hardware_action",
            command_payload=command_payload,
            requested_by_user_id=text(user_id) or None,
            ttl_seconds=agent_registry_repository.SELF_HOSTED_NODE_COMMAND_DEFAULT_TTL_SECONDS,
        )
    except Exception as exc:
        message = str(exc)
        runtime_session = await hardware_runtime_session_service.update_runtime_session(
            runtime_session,
            state="failed",
            audit_action="hardware_action.failed",
            reason=message,
            extra_metadata={"runtime_node_id": runtime_node_id, "runtime_profile_id": runtime_profile_id, "failure_reason": message},
        )
        await hardware_result_correlator_service.emit_tool_result(
            trace_context,
            tool_call_id=tool_call_id,
            status="failed",
            summary=message or "Self-hosted node command enqueue failed.",
            capability_id=capability_id,
            arguments=arguments,
            runtime_session=runtime_session,
            runtime_target="self_hosted_node",
            request_id=request_id,
            action_id=action_id,
            metadata={"runtime_node_id": runtime_node_id, "runtime_profile_id": runtime_profile_id, "failure_reason": message},
        )
        return {"status": "failed", "reason": message, "runtime_session": runtime_session, "trace_id": trace_id}

    command = dict_value(enqueued.get("command"))
    command_id = text(command.get("id"))
    summary = "Queued self-hosted node action."
    runtime_session = await hardware_runtime_session_service.update_runtime_session(
        runtime_session,
        state="running",
        audit_action="hardware_action.self_hosted_command_enqueued",
        extra_metadata={
            "runtime_node_id": runtime_node_id,
            "runtime_profile_id": runtime_profile_id,
            "runtime_attachment_id": runtime_attachment_id,
            "node_kind": node_kind or None,
            "self_hosted_command_id": command_id,
            "result_summary": summary,
        },
    )
    await hardware_result_correlator_service.emit_tool_result(
        trace_context,
        tool_call_id=tool_call_id,
        status="running",
        summary=summary,
        capability_id=capability_id,
        arguments=arguments,
        runtime_session=runtime_session,
        runtime_target="self_hosted_node",
        request_id=request_id,
        action_id=action_id,
        metadata={
            "runtime_node_id": runtime_node_id,
            "runtime_profile_id": runtime_profile_id,
            "runtime_attachment_id": runtime_attachment_id,
            "self_hosted_command_id": command_id,
        },
    )
    # ── Emit progress so the chat isn't silent while the VPS works ──
    try:
        await agent_trace_service.emit_tool_progress(
            trace_context,
            tool_call_id=tool_call_id,
            message=f"Running {capability_id} on your Server…",
            percent=0,
        )
    except Exception:
        pass
    return {
        "status": "running",
        "execution": {
            "runtime_target": "self_hosted_node",
            "canonical_runtime_target": "self_hosted_node",
            "runtime_node_id": runtime_node_id,
            "runtime_profile_id": runtime_profile_id,
            "runtime_attachment_id": runtime_attachment_id,
            "command": command,
            "command_enqueue": enqueued,
            "capability_id": capability_id,
            "action_id": action_id,
            "run_id": run_id,
            "trace_id": trace_id,
            "request_id": request_id,
        },
        "runtime_session": runtime_session,
        "artifacts": [],
        "trace_id": trace_id,
    }


async def record_self_hosted_command_completion(completion: Dict[str, Any]) -> Dict[str, Any]:
    command = dict_value(completion.get("command"))
    command_payload = dict_value(command.get("command_payload"))
    if text(command_payload.get("runtime_session_binding")) != HARDWARE_RUNTIME_SESSION_BINDING:
        return completion
    session_id = text(command_payload.get("runtime_session_id"))
    if not session_id:
        return completion
    session_record = await session_service.get_session(session_id) or {}
    runtime_session = hardware_runtime_session_service.runtime_session_with_correlation(
        hardware_runtime_session_service.session_view(session_id, dict_value(session_record.get("metadata")) or command_payload),
        payload=command_payload,
        session_record=session_record,
    )
    status = text(completion.get("status")).lower()
    terminal_state = "ready" if status == "completed" else "failed"
    result_payload = dict_value(completion.get("result_payload") or command.get("result_payload"))
    error = text(completion.get("error") or command.get("error")) or None
    capability_id = text(command_payload.get("capability_id")) or text(runtime_session.get("capability_id"))
    artifact_ids = hardware_result_correlator_service.artifact_ids_from_artifact_records(completion.get("artifacts") or command.get("artifacts"))
    summary = self_hosted_completion_summary(
        capability_id=capability_id,
        status=status,
        result_payload=result_payload,
        error=error,
    )
    trace_context = await resolve_trace_context(
        None,
        trace_id=text(command_payload.get("trace_id")) or text(runtime_session.get("trace_id")),
        tenant_id=text(command_payload.get("tenant_id")) or text((session_record or {}).get("tenant_id")) or "default",
        workspace_id=text(command_payload.get("workspace_id")) or text((session_record or {}).get("workspace_id")) or "default",
        thread_id=text(command_payload.get("thread_id")) or text(runtime_session.get("thread_id")) or None,
        run_id=text(command_payload.get("run_id")) or text(runtime_session.get("run_id")) or session_id,
    )
    await hardware_result_correlator_service.emit_artifacts(trace_context, artifact_ids, capability_id, runtime_session=runtime_session)
    runtime_session = await hardware_runtime_session_service.update_runtime_session(
        runtime_session,
        state=terminal_state,
        audit_action="hardware_action.completed" if status == "completed" else "hardware_action.failed",
        reason=error,
        artifacts=artifact_ids,
        extra_metadata={
            "runtime_node_id": text(command_payload.get("runtime_node_id")) or None,
            "runtime_profile_id": text(command_payload.get("runtime_profile_id")) or None,
            "self_hosted_command_id": text(completion.get("command_id") or command.get("id")) or None,
            "result_summary": summary,
            "result_payload": result_payload,
            "completion_status": status,
        },
    )
    await hardware_result_correlator_service.emit_tool_result(
        trace_context,
        tool_call_id=text(command_payload.get("request_id")) or text(runtime_session.get("request_id")) or session_id,
        status="completed" if status == "completed" else "failed",
        summary=summary,
        artifact_ids=artifact_ids,
        capability_id=capability_id,
        arguments=dict_value(command_payload.get("arguments")),
        runtime_session=runtime_session,
        runtime_target="self_hosted_node",
        request_id=text(command_payload.get("request_id")),
        action_id=text(command_payload.get("action_id")),
        metadata={
            "self_hosted_command_id": text(completion.get("command_id") or command.get("id")),
            "runtime_node_id": text(command_payload.get("runtime_node_id")),
            "runtime_profile_id": text(command_payload.get("runtime_profile_id")),
            "completion_status": status,
        },
    )
    # ── Emit progress + hardware activity for VPS completion/failure ──
    is_completed = status == "completed"
    try:
        await agent_trace_service.emit_tool_progress(
            trace_context,
            tool_call_id=text(command_payload.get("request_id")) or text(runtime_session.get("request_id")) or session_id,
            message=f"{"Completed" if is_completed else "Failed to run"} {capability_id} on your Server",
            percent=100,
        )
    except Exception:
        pass
    try:
        hardware_activity_event_service.emit_hardware_action_event(
            workspace_id=text(command_payload.get("workspace_id")) or "default",
            tenant_id=text(command_payload.get("tenant_id")) or "default",
            gateway_id=text(command_payload.get("runtime_node_id")) or "unknown",
            capability=capability_id,
            status="completed" if is_completed else "failed",
            duration_ms=0,
            run_id=text(command_payload.get("run_id")) or session_id,
            trace_id=text(command_payload.get("trace_id")) or None,
        )
    except Exception:
        pass
    completion["runtime_session"] = runtime_session
    completion["artifacts"] = artifact_ids
    return completion


async def stop_self_hosted_node_action(
    *,
    tenant_id: str,
    workspace_id: str,
    run_id: str,
    target_ids: Dict[str, str],
    node_id: Optional[str],
    trace_id: str,
    target_request_id: Optional[str],
    request_id: str,
    thread_id: Optional[str],
    reason: Optional[str],
    session_id: Optional[str],
) -> Dict[str, Any]:
    canonical_target_id = target_ids["canonical_runtime_target"]
    attachment, unavailable_reason = await select_self_hosted_attachment(
        tenant_id=text(tenant_id) or "default",
        workspace_id=text(workspace_id) or "default",
        node_id=node_id,
        capability_id="tool.interrupt",
    )
    if unavailable_reason:
        return {
            "status": "offline" if unavailable_reason == "self_hosted_node_offline" else "degraded",
            "reason": unavailable_reason,
            "runtime_target": target_ids["runtime_target"],
            "canonical_runtime_target": canonical_target_id,
            "trace_id": trace_id,
        }
    attachment = dict(attachment or {})
    runtime_node_id = text(attachment.get("runtime_node_id") or attachment.get("runtime_id"))
    runtime_profile_id = text(attachment.get("runtime_profile_id"))
    command_payload = {
        "runtime_session_binding": HARDWARE_RUNTIME_SESSION_BINDING,
        "runtime_session_id": text(session_id) or None,
        "runtime_target": "self_hosted_node",
        "runtime_node_id": runtime_node_id,
        "runtime_profile_id": runtime_profile_id,
        "run_id": text(run_id),
        "trace_id": trace_id,
        "request_id": request_id,
        "target_request_id": text(target_request_id) or None,
        "reason": text(reason) or "operator_requested_stop",
    }
    try:
        enqueued = await agent_registry_repository.enqueue_self_hosted_runtime_command(
            runtime_profile_id=runtime_profile_id,
            tenant_id=text(tenant_id) or "default",
            workspace_id=text(workspace_id) or "default",
            runtime_node_id=runtime_node_id,
            agent_id="sage",
            command_type="cancel_runtime_action",
            command_payload=command_payload,
            requested_by_user_id=None,
            ttl_seconds=agent_registry_repository.SELF_HOSTED_NODE_COMMAND_DEFAULT_TTL_SECONDS,
        )
    except Exception as exc:
        return {"status": "failed", "reason": str(exc), "trace_id": trace_id}
    session_view = None
    if text(session_id):
        command = dict_value(enqueued.get("command"))
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
            "runtime_node_id": runtime_node_id,
            "runtime_profile_id": runtime_profile_id,
            "self_hosted_command_id": text(command.get("id")),
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
            extra_metadata={"interrupt_request_id": request_id},
        )
        await hardware_result_correlator_service.emit_hardware_stop_transcript_event(
            session_view,
            runtime_target=canonical_target_id,
            target_request_id=target_request_id,
            reason=reason,
        )
    return {
        "status": "terminated",
        "execution": enqueued,
        "runtime_session": session_view,
        "trace_id": trace_id,
    }

from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

from server_modules import (
    agent_trace_service,
    execution_mode_policy,
    hardware_access_policy_service,
    hardware_result_correlator_service,
    hardware_runtime_session_service,
    hardware_runtime_target_resolver,
    rust_runtime_kernel_client,
    secret_redaction_service,
    virtual_computer_runtime,
)
from server_modules.hardware_runtime_adapters.common import dict_value, text, utc_now_iso


HARDWARE_RUNTIME_SESSION_BINDING = hardware_runtime_session_service.HARDWARE_RUNTIME_SESSION_BINDING
FULL_RUNTIME_ACCESS_MODE = execution_mode_policy.FULL_RUNTIME_ACCESS_MODE
_CLOUD_COMPUTER_RUNTIME_REGISTRY: Any = None


def get_cloud_computer_runtime_registry() -> virtual_computer_runtime.VirtualComputerRuntimeRegistry:
    global _CLOUD_COMPUTER_RUNTIME_REGISTRY
    if _CLOUD_COMPUTER_RUNTIME_REGISTRY is None:
        _CLOUD_COMPUTER_RUNTIME_REGISTRY = virtual_computer_runtime.build_default_runtime_registry()
    return _CLOUD_COMPUTER_RUNTIME_REGISTRY


def cloud_runtime_choice_for_action(capability_id: str, action_id: str, arguments: Dict[str, Any]) -> str:
    action_token = text(action_id).lower().replace("__", ".")
    requested_action = text(arguments.get("action") or arguments.get("virtual_action")).lower()
    if capability_id == "shell.execute" or "shell" in action_token or requested_action == "run_command":
        return virtual_computer_runtime.RUNTIME_CHOICE_VIRTUAL_CODE_SANDBOX
    if capability_id.startswith("computer_control."):
        return virtual_computer_runtime.RUNTIME_CHOICE_VIRTUAL_DESKTOP
    return virtual_computer_runtime.RUNTIME_CHOICE_VIRTUAL_BROWSER


def cloud_computer_virtual_action(
    *,
    capability_id: str,
    action_id: str,
    arguments: Dict[str, Any],
) -> tuple[str, Dict[str, Any], str]:
    action_token = text(action_id).lower().replace("__", ".")
    requested_action = text(arguments.get("action") or arguments.get("virtual_action")).lower()
    requested_action = requested_action.replace("__", ".")
    if capability_id == "screenshot.capture" or "screenshot" in action_token or requested_action in {"screenshot", "screen"}:
        return virtual_computer_runtime.ACTION_SCREENSHOT, {}, "stream_screenshot"
    if capability_id == "shell.execute" or requested_action in {"run_command", "shell.execute", "shell.exec"}:
        command = text(arguments.get("command") or arguments.get("script"))
        return virtual_computer_runtime.ACTION_RUN_COMMAND, {"command": command}, "execute_action"
    if capability_id == "computer_control.click" or requested_action in {"click", "computer.click"}:
        return (
            virtual_computer_runtime.ACTION_CLICK,
            {
                "x": arguments.get("x"),
                "y": arguments.get("y"),
                "target_text": arguments.get("target_text"),
                "target_description": arguments.get("target_description"),
            },
            "execute_action",
        )
    if capability_id == "computer_control.type" or requested_action in {"type", "computer.type"}:
        return (
            virtual_computer_runtime.ACTION_TYPE,
            {
                "text": arguments.get("text") or arguments.get("value"),
                "target_text": arguments.get("target_text"),
                "target_description": arguments.get("target_description"),
            },
            "execute_action",
        )
    if capability_id == "computer_control.key" or requested_action in {"hotkey", "key", "press"}:
        keys = arguments.get("keys") or arguments.get("key")
        key_args = {"keys": keys} if isinstance(keys, list) else {"key": keys}
        return virtual_computer_runtime.ACTION_HOTKEY, key_args, "execute_action"
    if requested_action in {"scroll", "browser.scroll"}:
        return (
            virtual_computer_runtime.ACTION_SCROLL,
            {"delta_x": arguments.get("delta_x"), "delta_y": arguments.get("delta_y")},
            "execute_action",
        )
    if requested_action in {"wait", "browser.wait"}:
        return (
            virtual_computer_runtime.ACTION_WAIT,
            {"duration_ms": arguments.get("duration_ms"), "seconds": arguments.get("seconds")},
            "execute_action",
        )
    if requested_action in {"download", "download_file", "browser.download"} or "download" in action_token:
        return (
            virtual_computer_runtime.ACTION_DOWNLOAD_ARTIFACT,
            {"url": arguments.get("url"), "artifact_type": arguments.get("artifact_type")},
            "execute_action",
        )
    if capability_id == "browser_automation.interactive":
        return (
            virtual_computer_runtime.ACTION_OPEN_URL,
            {"url": arguments.get("url") or arguments.get("start_url")},
            "execute_action",
        )
    return "", {}, ""


def cloud_computer_approval_required(
    *,
    capability_id: str,
    action_id: str,
    virtual_action: str,
    arguments: Dict[str, Any],
    runtime_access_mode: str,
    require_approval: Optional[bool],
) -> bool:
    if virtual_action == virtual_computer_runtime.ACTION_KILL_SWITCH:
        return hardware_access_policy_service.normalize_runtime_access_mode(runtime_access_mode) != FULL_RUNTIME_ACCESS_MODE
    return hardware_access_policy_service.hardware_action_requires_software_approval(
        runtime_access_mode=runtime_access_mode,
        capability_id=capability_id,
        action_id=action_id,
        arguments=arguments,
        require_approval=require_approval,
        virtual_action=virtual_action,
    )


def runtime_cost_metadata(response: Dict[str, Any], *, runtime_choice: str, provider_id: Optional[str]) -> Dict[str, Any]:
    action_result = response.get("action_result") if isinstance(response.get("action_result"), dict) else {}
    cost_metadata = {
        "runtime_choice": runtime_choice,
        "runtime_provider_id": text(provider_id) or text(response.get("runtime_provider_id")) or None,
        "runtime_kind": text(response.get("runtime_kind")) or None,
        "cost_quota": response.get("cost_quota") if isinstance(response.get("cost_quota"), dict) else None,
        "cost_usage": response.get("cost_usage") if isinstance(response.get("cost_usage"), dict) else None,
        "action_cost_estimate": action_result.get("cost_estimate") if isinstance(action_result.get("cost_estimate"), dict) else None,
    }
    return {key: value for key, value in cost_metadata.items() if value not in (None, "", {})}


def cloud_computer_session_persistence(cost_metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    metadata = dict_value(cost_metadata)
    requested = metadata.get("session_persistence") or metadata.get("runtime_session_persistence")
    if isinstance(requested, dict) and requested:
        return dict(requested)
    return {
        "session_mode": virtual_computer_runtime.SESSION_PERSISTENCE_EPHEMERAL,
        "auto_terminate_on_task_complete": True,
    }


def cloud_computer_control_metadata(cost_metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    metadata = dict_value(cost_metadata)
    return {
        key: dict(metadata.get(key) or {})
        for key in ("cost_quota", "runtime_quota", "computer_automation")
        if isinstance(metadata.get(key), dict) and metadata.get(key)
    }


def cloud_computer_summary(capability_id: str, virtual_action: str, response: Dict[str, Any]) -> str:
    action_result = response.get("action_result") if isinstance(response.get("action_result"), dict) else {}
    if virtual_action == virtual_computer_runtime.ACTION_OPEN_URL:
        session = response.get("session") if isinstance(response.get("session"), dict) else {}
        url = text(session.get("ui_current_url"))
        return f"Used cloud computer browser: {url}" if url else "Used cloud computer browser."
    if virtual_action == virtual_computer_runtime.ACTION_SCREENSHOT:
        return "Captured cloud computer screenshot."
    if virtual_action == virtual_computer_runtime.ACTION_RUN_COMMAND:
        return "Ran command in cloud computer sandbox."
    completed_action = text(action_result.get("action") or virtual_action)
    if completed_action:
        return f"Cloud computer action completed: {completed_action}."
    return f"{capability_id or 'cloud computer action'} completed."


def _virtual_action_payload(arguments: Dict[str, Any]) -> Dict[str, Any]:
    projected: Dict[str, Any] = {}
    for key in ("url", "text", "command", "x", "y", "delta_x", "delta_y", "duration_ms", "seconds"):
        if key in arguments:
            projected[key] = arguments.get(key)
    keys = arguments.get("keys") or arguments.get("key")
    if isinstance(keys, list):
        projected["key_count"] = len(keys)
        projected["hotkey_count"] = len(keys)
        projected["hotkey_token_too_long"] = any(len(text(item)) > 32 for item in keys)
    elif keys is not None:
        projected["key_count"] = 1
        projected["hotkey_count"] = 1
        projected["hotkey_token_too_long"] = len(text(keys)) > 32
    return projected


def enforce_virtual_computer_decision(
    operation: str,
    payload: Dict[str, Any],
    *,
    allow_approval_required: bool = False,
) -> Dict[str, Any]:
    expected_next_actions = {
        "provider_admission": {"admit_virtual_computer_provider"},
        "isolation_profile": {"build_isolation_profile"},
        "identity_context": {"build_identity_context"},
        "cost_quota": {"build_cost_quota_profile"},
        "action_payload": {
            "validate_computer_use_action_payload",
            "request_run_command_approval",
            "request_computer_action_approval",
        },
        "network_policy": {
            "assert_network_browser_security",
            "request_download_approval",
        },
        "artifact_export": {
            "collect_virtual_computer_artifact",
            "request_local_artifact_export_approval",
        },
        "session_state": {"assert_virtual_session_active"},
    }
    decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
        "virtual-computer-decision",
        {"operation": operation, **payload},
        allow_approval_required=allow_approval_required,
    )
    next_action = text(decision.get("next_action"))
    if next_action not in expected_next_actions.get(operation, set()):
        raise RuntimeError(f"unexpected_next_action:{next_action or 'missing'}")
    return decision


async def execute_cloud_computer_action(
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: Optional[str],
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
    cost_metadata: Optional[Dict[str, Any]],
    tool_call_id: str,
    runtime_registry_getter: Any = get_cloud_computer_runtime_registry,
) -> Dict[str, Any]:
    runtime_choice = cloud_runtime_choice_for_action(capability_id, action_id, arguments)
    virtual_action, virtual_action_args, dispatch = cloud_computer_virtual_action(
        capability_id=capability_id,
        action_id=action_id,
        arguments=arguments,
    )
    if not virtual_action or not dispatch:
        reason = "cloud_computer_action_not_supported"
        runtime_session = await hardware_runtime_session_service.update_runtime_session(
            runtime_session,
            state="degraded",
            audit_action="hardware_action.degraded",
            reason=reason,
            extra_metadata={"degraded_reason": reason, "runtime_choice": runtime_choice, "capability_id": capability_id},
        )
        await hardware_result_correlator_service.emit_tool_result(
            trace_context,
            tool_call_id=tool_call_id,
            status="degraded",
            summary="Cloud computer target is available, but this action is not supported by the adapter yet.",
            capability_id=capability_id,
            arguments=arguments,
            runtime_session=runtime_session,
            runtime_target="empyralis_cloud_computer",
            request_id=request_id,
            action_id=action_id,
            metadata={"runtime_choice": runtime_choice, "degraded_reason": reason},
        )
        return {"status": "degraded", "reason": reason, "runtime_session": runtime_session, "trace_id": trace_id}

    virtual_action_decision = enforce_virtual_computer_decision(
        "action_payload",
        {
            "runtime_choice": runtime_choice,
            "computer_action": virtual_action,
            "tool_action": virtual_action,
            "virtual_action": virtual_action,
            "action_args": virtual_action_args,
            "approval_granted": hardware_access_policy_service.normalize_runtime_access_mode(runtime_access_mode) == FULL_RUNTIME_ACCESS_MODE,
            **_virtual_action_payload(virtual_action_args),
        },
        allow_approval_required=True,
    )
    if virtual_action in {virtual_computer_runtime.ACTION_OPEN_URL, virtual_computer_runtime.ACTION_DOWNLOAD_ARTIFACT}:
        enforce_virtual_computer_decision(
            "network_policy",
            {
                "runtime_choice": runtime_choice,
                "computer_action": virtual_action,
                "tool_action": virtual_action,
                "virtual_action": virtual_action,
                "url": text(virtual_action_args.get("url")),
            },
        )
    if virtual_action == virtual_computer_runtime.ACTION_SCREENSHOT:
        enforce_virtual_computer_decision(
            "artifact_export",
            {
                "runtime_choice": runtime_choice,
                "artifact_type": "screenshot",
                "export_target": "workspace",
            },
        )

    if bool(virtual_action_decision.get("approval_required")) or cloud_computer_approval_required(
        capability_id=capability_id,
        action_id=action_id,
        virtual_action=virtual_action,
        arguments=arguments,
        runtime_access_mode=runtime_access_mode,
        require_approval=require_approval,
    ):
        approval = {
            "approval_id": f"cloudapproval_{uuid.uuid4().hex}",
            "status": "pending",
            "kind": "cloud_computer_action",
            "requested_at": utc_now_iso(),
            "request_payload": {
                "runtime_target": "empyralis_cloud_computer",
                "runtime_access_mode": hardware_access_policy_service.normalize_runtime_access_mode(runtime_access_mode),
                "runtime_session_id": text(runtime_session.get("session_id")),
                "runtime_session_binding": HARDWARE_RUNTIME_SESSION_BINDING,
                "capability_id": capability_id,
                "action_id": action_id,
                "action": virtual_action,
                "arguments": secret_redaction_service.sanitize_mapping(virtual_action_args),
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
        }
        await agent_trace_service.emit_approval_requested(
            trace_context,
            approval_id=approval["approval_id"],
            kind="hardware_action",
            title=f"Approve {capability_id}",
            description=f"Approval required before running {capability_id} on the cloud computer.",
            blocking_item_id=None,
        )
        runtime_session = await hardware_runtime_session_service.update_runtime_session(
            runtime_session,
            state="waiting_approval",
            audit_action="hardware_action.approval_requested",
            approvals=[approval],
            extra_metadata={
                "runtime_choice": runtime_choice,
                "approval_id": approval["approval_id"],
                "capability_id": capability_id,
                "runtime_access_mode": hardware_access_policy_service.normalize_runtime_access_mode(runtime_access_mode),
                "approval_execution_payloads": {approval["approval_id"]: approval_execution_payload},
            },
        )
        await hardware_result_correlator_service.emit_tool_result(
            trace_context,
            tool_call_id=tool_call_id,
            status="waiting_approval",
            summary=f"Waiting for approval to run {capability_id} on the cloud computer.",
            capability_id=capability_id,
            arguments=virtual_action_args,
            runtime_session=runtime_session,
            runtime_target="empyralis_cloud_computer",
            request_id=request_id,
            action_id=virtual_action,
            metadata={"runtime_choice": runtime_choice, "approval_id": approval["approval_id"]},
        )
        return {"status": "waiting_approval", "approval": approval, "runtime_session": runtime_session, "trace_id": trace_id}

    provider_id = text((dict_value(cost_metadata).get("runtime_provider_id") or dict_value(cost_metadata).get("provider_id"))) or None
    session_persistence = cloud_computer_session_persistence(cost_metadata)
    session_mode = text(session_persistence.get("session_mode") or session_persistence.get("mode")).lower()
    control_metadata = cloud_computer_control_metadata(cost_metadata)
    identity_metadata = dict_value(dict_value(cost_metadata).get("identity_context"))
    runtime_session_id = text(runtime_session.get("session_id")) or hardware_runtime_session_service.new_runtime_session_id()
    enforce_virtual_computer_decision(
        "identity_context",
        {
            "runtime_choice": runtime_choice,
            **identity_metadata,
        },
    )
    enforce_virtual_computer_decision(
        "provider_admission",
        {
            "runtime_provider_id": provider_id or "",
            "runtime_choice": runtime_choice,
            "runtime_target": runtime_choice,
        },
    )
    enforce_virtual_computer_decision(
        "isolation_profile",
        {
            "runtime_choice": runtime_choice,
            **dict_value(control_metadata.get("computer_automation")),
        },
    )
    enforce_virtual_computer_decision(
        "cost_quota",
        {
            "runtime_choice": runtime_choice,
            **dict_value(control_metadata.get("runtime_quota")),
            **dict_value(control_metadata.get("cost_quota")),
        },
    )
    create_payload = {
        "tenant_id": text(tenant_id) or "default",
        "workspace_id": text(workspace_id) or "default",
        "user_id": text(user_id),
        "agent_id": "sage",
        "run_id": run_id,
        "trace_id": trace_id,
        "thread_id": text(thread_id) or None,
        "request_id": request_id,
        "session_id": runtime_session_id,
        "runtime_session_id": runtime_session_id,
        "browser_session_id": runtime_session_id,
        "runtime_choice": runtime_choice,
        "runtime_provider_id": provider_id,
        "source": HARDWARE_RUNTIME_SESSION_BINDING,
        "require_session_token": False,
        "ephemeral_task": session_mode != virtual_computer_runtime.SESSION_PERSISTENCE_RESUMABLE,
        "session_persistence": session_persistence,
        **control_metadata,
        "metadata": {
            "runtime_session_binding": HARDWARE_RUNTIME_SESSION_BINDING,
            "runtime_target": "empyralis_cloud_computer",
            "runtime_access_mode": hardware_access_policy_service.normalize_runtime_access_mode(runtime_access_mode),
            "capability_id": capability_id,
        },
    }
    runtime = runtime_registry_getter().resolve(runtime_choice, preferred_provider_id=provider_id)
    try:
        session_payload = await runtime.create_session(create_payload)
        runtime_session = await hardware_runtime_session_service.update_runtime_session(
            runtime_session,
            state="running",
            audit_action="hardware_action.cloud_computer_selected",
            extra_metadata={
                "runtime_choice": runtime_choice,
                "runtime_kind": text(session_payload.get("runtime_kind")) or None,
                "cloud_computer_session_id": text(session_payload.get("session_id")) or runtime_session_id,
            },
        )
        action_payload = {
            **create_payload,
            "session_id": text(session_payload.get("session_id")) or runtime_session_id,
            "runtime_session_id": text(session_payload.get("session_id")) or runtime_session_id,
            "browser_session_id": text(session_payload.get("session_id")) or runtime_session_id,
            "action": virtual_action,
            "action_args": virtual_action_args,
            "policy_metadata": {"runtime_session_binding": HARDWARE_RUNTIME_SESSION_BINDING, "capability_id": capability_id},
        }
        if dispatch == "stream_screenshot":
            action_response = await runtime.stream_screenshot(action_payload)
        else:
            action_response = await runtime.execute_action(action_payload)
    except Exception as exc:
        message = str(exc)
        state = "degraded" if "not configured" in message.lower() else "failed"
        runtime_session = await hardware_runtime_session_service.update_runtime_session(
            runtime_session,
            state=state,
            audit_action="hardware_action.failed",
            reason=message,
            extra_metadata={"runtime_choice": runtime_choice, "capability_id": capability_id, "failure_reason": message},
        )
        await hardware_result_correlator_service.emit_tool_result(
            trace_context,
            tool_call_id=tool_call_id,
            status=state,
            summary=message or "Cloud computer action failed.",
            capability_id=capability_id,
            arguments=virtual_action_args,
            runtime_session=runtime_session,
            runtime_target="empyralis_cloud_computer",
            request_id=request_id,
            action_id=virtual_action,
            metadata={"runtime_choice": runtime_choice, "failure_reason": message},
        )
        return {"status": state, "reason": message, "runtime_session": runtime_session, "trace_id": trace_id}

    artifact_ids = hardware_result_correlator_service.runtime_artifact_ids_from_response(action_response)
    await hardware_result_correlator_service.emit_artifacts(trace_context, artifact_ids, capability_id, runtime_session=runtime_session)
    cost = runtime_cost_metadata(action_response, runtime_choice=runtime_choice, provider_id=provider_id)
    summary = cloud_computer_summary(capability_id, virtual_action, action_response)
    runtime_session = await hardware_runtime_session_service.update_runtime_session(
        runtime_session,
        state="ready",
        audit_action="hardware_action.completed",
        artifacts=artifact_ids,
        extra_metadata={
            "runtime_choice": runtime_choice,
            "runtime_kind": text(action_response.get("runtime_kind")) or None,
            "cloud_computer_session_id": text(action_response.get("session_id")) or runtime_session_id,
            "result_summary": summary,
            "execution_request_id": request_id,
            "billing": cost,
        },
    )
    await hardware_result_correlator_service.emit_tool_result(
        trace_context,
        tool_call_id=tool_call_id,
        status="completed",
        summary=summary,
        artifact_ids=artifact_ids,
        capability_id=capability_id,
        arguments=virtual_action_args,
        runtime_session=runtime_session,
        runtime_target="empyralis_cloud_computer",
        request_id=request_id,
        action_id=virtual_action,
        metadata={"runtime_choice": runtime_choice, "cloud_computer_session_id": text(action_response.get("session_id")) or runtime_session_id},
    )
    execution = {
        "runtime_target": "empyralis_cloud_computer",
        "canonical_runtime_target": "empyralis_cloud_computer",
        "runtime_choice": runtime_choice,
        "runtime_provider_id": provider_id,
        "session": session_payload,
        "result": action_response,
        "capability_id": capability_id,
        "action": virtual_action,
        "run_id": run_id,
        "trace_id": trace_id,
        "request_id": request_id,
        "cost_metadata": cost,
    }
    return {
        "status": "completed",
        "execution": execution,
        "runtime_session": runtime_session,
        "artifacts": artifact_ids,
        "trace_id": trace_id,
    }


async def stop_cloud_computer_action(
    *,
    tenant_id: str,
    workspace_id: str,
    run_id: str,
    target_ids: Dict[str, str],
    trace_id: str,
    target_request_id: Optional[str],
    request_id: str,
    thread_id: Optional[str],
    reason: Optional[str],
    session_id: Optional[str],
    runtime_registry_getter: Any = get_cloud_computer_runtime_registry,
) -> Dict[str, Any]:
    canonical_target_id = target_ids["canonical_runtime_target"]
    runtime_session_id = text(session_id)
    if not runtime_session_id:
        return {
            "status": "degraded",
            "reason": "cloud_computer_session_id_required",
            "runtime_target": target_ids["runtime_target"],
            "canonical_runtime_target": canonical_target_id,
            "trace_id": trace_id,
        }
    enforce_virtual_computer_decision(
        "session_state",
        {
            "runtime_choice": virtual_computer_runtime.RUNTIME_CHOICE_VIRTUAL_BROWSER,
            "state": "running",
            "session_operation": "terminate",
            "session_id": runtime_session_id,
        },
    )
    try:
        runtime = runtime_registry_getter().resolve(virtual_computer_runtime.RUNTIME_CHOICE_VIRTUAL_BROWSER)
        terminated = await runtime.terminate_session(
            {
                "tenant_id": text(tenant_id) or "default",
                "workspace_id": text(workspace_id) or "default",
                "session_id": runtime_session_id,
                "runtime_session_id": runtime_session_id,
                "browser_session_id": runtime_session_id,
                "run_id": text(run_id),
                "trace_id": trace_id,
                "request_id": request_id,
                "manual_terminate": True,
                "reason": text(reason) or "operator_requested_stop",
            }
        )
    except Exception as exc:
        return {"status": "failed", "reason": str(exc), "trace_id": trace_id}
    session_view = {
        "session_id": runtime_session_id,
        "runtime_session_binding": HARDWARE_RUNTIME_SESSION_BINDING,
        "state": "running",
        "runtime_target": target_ids["runtime_target"],
        "canonical_runtime_target": canonical_target_id,
        "runtime_fabric_target": canonical_target_id,
        "hardware_edge": hardware_runtime_target_resolver.runtime_edge(canonical_target_id),
        "tenant_id": text(tenant_id) or "default",
        "workspace_id": text(workspace_id) or "default",
        "thread_id": text(thread_id) or None,
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
        "execution": terminated,
        "runtime_session": session_view,
        "trace_id": trace_id,
    }

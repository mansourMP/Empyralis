from __future__ import annotations

import base64
import binascii
import time
from typing import Any, Dict, Optional

from server_modules import (
    agent_computer_policy_service,
    artifact_service,
    execution_mode_policy,
    gateway_activity_service,
    gateway_inventory_service,
    gateway_registry_service,
    gateway_protocol_service,
    gateway_state_repository,
    gateway_transparency_service,
    hardware_activity_event_service,
    machine_capability_check,
    rust_runtime_kernel_client,
    secret_redaction_service,
)
from server_modules import execution_mode_policy


SCREEN_REQUIRED_CAPABILITIES = {
    "screenshot.capture",
    "screenshot",
    "screen.capture",
    "ocr",
    "screen.ocr",
}

ACCESSIBILITY_REQUIRED_CAPABILITIES = {
    "mouse",
    "keyboard",
    "mouse.click",
    "mouse.move",
    "keyboard.type",
    "keyboard.press",
    "input.click",
    "input.type",
    "app.focus",
    "window.control",
}

_PERMISSION_PROBE_CACHE_TTL_SECONDS = 60
_PERMISSION_PROBE_CACHE: Dict[str, tuple[float, Dict[str, str]]] = {}


def _text(value: Any, fallback: str = "") -> str:
    token = str(value or "").strip()
    return token or fallback


def _gateway_supervisor_capability(
    capability_id: str,
    arguments: Optional[Dict[str, Any]],
) -> tuple[str, Dict[str, Any]]:
    normalized = str(capability_id or "").strip()
    args = dict(arguments or {})
    if normalized == "shell.execute":
        params = args.get("params") if isinstance(args.get("params"), dict) else {}
        nested_arguments = args.get("arguments") if isinstance(args.get("arguments"), dict) else {}
        command = _text(
            args.get("command")
            or params.get("command")
            or nested_arguments.get("command")
        )
        if command:
            args = {"command": command}
        return normalized, args
    if normalized == "filesystem.read":
        args.setdefault("mode", "read")
        return "filesystem.read_write", args
    if normalized == "filesystem.write":
        args.setdefault("mode", "write")
        return "filesystem.read_write", args
    return normalized, args


def _registration_metadata(registration: Dict[str, Any]) -> Dict[str, Any]:
    metadata = registration.get("metadata") if isinstance(registration.get("metadata"), dict) else {}
    return dict(metadata or {})


def _runtime_access_mode_for_dispatch(
    *,
    registration: Dict[str, Any],
    explicit_mode: Optional[str],
) -> str:
    metadata = _registration_metadata(registration)
    return execution_mode_policy.normalize_runtime_access_mode(
        explicit_mode or metadata.get("runtime_access_mode")
    )


def _full_access_warning_acknowledged(registration: Dict[str, Any]) -> bool:
    metadata = _registration_metadata(registration)
    return bool(metadata.get("autonomous_agent_setup_warning_acknowledged"))


def _registration_sage_full_access_metadata(registration: Dict[str, Any]) -> bool:
    metadata = _registration_metadata(registration)
    runtime_access_mode = execution_mode_policy.normalize_runtime_access_mode(
        metadata.get("runtime_access_mode")
    )
    agent_scope = str(metadata.get("agent_scope") or "").strip().lower()
    warning_acknowledged = bool(metadata.get("autonomous_agent_setup_warning_acknowledged"))
    return (
        runtime_access_mode == execution_mode_policy.FULL_RUNTIME_ACCESS_MODE
        and agent_scope == "sage"
        and warning_acknowledged
    )


def _policy_payload_for_dispatch(
    *,
    registration: Dict[str, Any],
    runtime_access_mode: str,
    agent_scope: str,
) -> Dict[str, Any]:
    metadata = _registration_metadata(registration)
    policy_payload = (
        metadata.get("agent_computer_policy")
        or metadata.get("computer_policy")
        or metadata.get("gateway_policy")
    )
    policy = None
    if isinstance(policy_payload, dict):
        try:
            policy = agent_computer_policy_service.normalize_agent_computer_policy(policy_payload)
        except Exception:
            policy = None
    if policy is None:
        policy_id = str(metadata.get("agent_computer_policy_id") or "").strip()
        workspace_id = str(registration.get("workspace_id") or "").strip()
        if policy_id and workspace_id:
            try:
                policy = agent_computer_policy_service.get_saved_agent_computer_policy(
                    workspace_id=workspace_id,
                    policy_id=policy_id,
                )
            except Exception:
                policy = None
    if policy is None:
        autonomy_mode = (
            agent_computer_policy_service.AUTONOMY_YOLO
            if runtime_access_mode == execution_mode_policy.FULL_RUNTIME_ACCESS_MODE
            else agent_computer_policy_service.AUTONOMY_ASK_EVERY_TIME
        )
        policy = agent_computer_policy_service.build_default_agent_computer_policy(
            autonomy_mode=autonomy_mode,
            policy_id=str(metadata.get("agent_computer_policy_id") or "").strip()
            or f"gateway:{str(registration.get('gateway_id') or 'default').strip() or 'default'}",
            filesystem_scope=("/",) if runtime_access_mode == execution_mode_policy.FULL_RUNTIME_ACCESS_MODE else (),
        )
    mode = "default"
    if runtime_access_mode == execution_mode_policy.FULL_RUNTIME_ACCESS_MODE:
        mode = "full_access"
    elif runtime_access_mode == execution_mode_policy.CUSTOM_RUNTIME_ACCESS_MODE:
        mode = "custom"
    return {
        "mode": mode,
        "agent_scope": str(agent_scope or "").strip() or "studio_agent",
        "filesystem_scope": list(policy.filesystem_scope),
        "allowed_paths": list(policy.filesystem_scope),
        "blocked_filesystem_scope": list(policy.blocked_filesystem_scope),
        "full_access_warning_acknowledged": _full_access_warning_acknowledged(registration),
        "policy_id": policy.policy_id,
        "policy_version": policy.policy_version,
    }


def _has_gateway_capability(registration: Dict[str, Any], capability_id: str) -> bool:
    return gateway_inventory_service.registration_has_execution_capability(registration, capability_id)


def _permission_statuses_for_gateway(*, gateway_id: str, workspace_id: str) -> Dict[str, str]:
    cache_key = str(gateway_id or "").strip()
    now = time.time()
    cached = _PERMISSION_PROBE_CACHE.get(cache_key)
    if cached and now - cached[0] <= _PERMISSION_PROBE_CACHE_TTL_SECONDS:
        return dict(cached[1])
    registration = gateway_state_repository.get_gateway_registration(cache_key)
    registration_metadata = dict((registration or {}).get("metadata") or {}) if isinstance(registration, dict) else {}
    permission_probe = registration_metadata.get("permission_probe") if isinstance(registration_metadata.get("permission_probe"), dict) else {}
    statuses: Dict[str, str] = {}
    for key, value in permission_probe.items():
        if isinstance(value, dict):
            statuses[str(key or "").strip().lower()] = str(value.get("status") or "unknown").strip().lower() or "unknown"
        elif isinstance(value, str):
            statuses[str(key or "").strip().lower()] = value.strip().lower() or "unknown"
    if statuses:
        _PERMISSION_PROBE_CACHE[cache_key] = (now, statuses)
        return dict(statuses)
    payload = machine_capability_check.desktop_setup_status(workspace_id=workspace_id)
    for item in list(payload.get("checks") or []):
        if not isinstance(item, dict):
            continue
        check_id = str(item.get("id") or item.get("name") or "").strip().lower()
        status = str(item.get("status") or "unknown").strip().lower() or "unknown"
        if check_id:
            statuses[check_id] = status
    _PERMISSION_PROBE_CACHE[cache_key] = (now, statuses)
    return dict(statuses)


def _enforce_hardware_permission_gate(
    *,
    gateway_id: str,
    workspace_id: str,
    capability_id: str,
) -> None:
    normalized_capability = str(capability_id or "").strip().lower()
    if normalized_capability not in SCREEN_REQUIRED_CAPABILITIES and normalized_capability not in ACCESSIBILITY_REQUIRED_CAPABILITIES:
        return
    statuses = _permission_statuses_for_gateway(gateway_id=gateway_id, workspace_id=workspace_id)
    if normalized_capability in SCREEN_REQUIRED_CAPABILITIES:
        if statuses.get("screen_recording") == "denied":
            raise PermissionError(
                "Screen recording permission is required for this action. "
                "Grant access in System Preferences > Privacy & Security > "
                "Screen Recording, then reconnect."
            )
    if normalized_capability in ACCESSIBILITY_REQUIRED_CAPABILITIES:
        if statuses.get("accessibility") == "denied":
            raise PermissionError(
                "Accessibility permission is required for this action. "
                "Grant access in System Preferences > Privacy & Security > "
                "Accessibility, then reconnect."
            )


def _heartbeat_capability_ready(
    *,
    registration: Dict[str, Any],
    status_payload: Dict[str, Any],
    capability_id: str,
) -> bool:
    registration_metadata = dict(registration.get("metadata") or {})
    status_metadata = dict(status_payload.get("metadata") or {})
    return gateway_inventory_service.capability_ready_from_any_metadata(
        capability_id,
        registration_metadata,
        status_metadata,
        status_payload,
    )


def gateway_registration_execution_readiness(
    registration: Optional[Dict[str, Any]],
    *,
    workspace_id: str,
    capability_id: str,
) -> tuple[bool, str]:
    if not isinstance(registration, dict) or not registration:
        return False, "gateway_registration_missing"
    if _text(registration.get("status")).lower() != "active":
        return False, "gateway_registration_inactive"
    if _text(registration.get("device_trust_state")).lower() == "revoked":
        return False, "gateway_device_revoked"
    registration_workspace_id = _text(registration.get("workspace_id"))
    if registration_workspace_id and registration_workspace_id != (_text(workspace_id) or "default"):
        return False, "gateway_workspace_mismatch"
    if not _has_gateway_capability(registration, capability_id):
        return False, "gateway_capability_missing"
    gateway_id = _text(registration.get("gateway_id"))
    if not gateway_protocol_service.gateway_connection_is_live(gateway_id):
        return False, "gateway_offline"
    status_payload = gateway_registry_service.gateway_registration_public_payload(registration)
    if not bool(status_payload.get("heartbeat_fresh")):
        return False, "gateway_heartbeat_stale"
    connection_status = _text(status_payload.get("connection_status")).lower()
    if connection_status != "online":
        return False, "gateway_unhealthy"
    reported_health = _text(status_payload.get("reported_health_state")).lower()
    if reported_health in {"degraded", "offline", "unhealthy", "error", "blocked"}:
        return False, "gateway_unhealthy"
    if not _heartbeat_capability_ready(
        registration=registration,
        status_payload=status_payload,
        capability_id=capability_id,
    ):
        return False, "gateway_capability_not_ready"
    return True, ""


def _materialize_gateway_artifacts(
    *,
    capability_id: str,
    response: Dict[str, Any],
    registration: Dict[str, Any],
    run_id: str,
    screenshot_retention: Optional[str] = None,
) -> Dict[str, Any]:
    result = dict(response.get("result") or {})
    if str(capability_id or "").strip() != "screenshot.capture":
        return result

    images = result.get("images")
    if not isinstance(images, list):
        return result

    compact_images = []
    artifacts = []
    retention = str(screenshot_retention or "session_only").strip().lower() or "session_only"
    for index, item in enumerate(images):
        if not isinstance(item, dict):
            continue
        encoded = str(item.get("data_base64") or "").strip()
        compact_item = {
            key: value
            for key, value in item.items()
            if key not in {"data_base64", "base64", "data"}
        }
        compact_item["screenshot_retention"] = retention
        if retention == "off":
            compact_item["artifact_retained"] = False
            compact_images.append({key: value for key, value in compact_item.items() if value is not None})
            continue
        if encoded:
            try:
                content = base64.b64decode(encoded, validate=True)
                record = artifact_service.store_artifact_bytes(
                    content,
                    run_id=str(run_id or "").strip(),
                    kind="screenshot",
                    file_name=f"gateway-screenshot-{index + 1}.png",
                    tenant_id=str(registration.get("tenant_id") or "default").strip() or "default",
                    workspace_id=str(registration.get("workspace_id") or "default").strip() or "default",
                    machine_id=str(registration.get("device_id") or "").strip() or None,
                    content_type="image/png",
                    metadata={
                        "source": "empyralis_gateway",
                        "gateway_id": str(registration.get("gateway_id") or "").strip(),
                        "device_id": str(registration.get("device_id") or "").strip(),
                        "capability_id": "screenshot.capture",
                        "monitor_name": str(item.get("monitor_name") or "").strip() or None,
                        "width": item.get("width"),
                        "height": item.get("height"),
                        "retention": retention,
                    },
                )
                artifact_payload = record.as_payload()
                artifacts.append(artifact_payload)
                compact_item["artifact_id"] = artifact_payload.get("artifact_id")
                compact_item["uri"] = artifact_payload.get("uri")
                compact_item["artifact_retained"] = True
            except (binascii.Error, ValueError):
                compact_item["artifact_error"] = "invalid_screenshot_payload"
        compact_images.append({key: value for key, value in compact_item.items() if value is not None})

    result["images"] = compact_images
    if artifacts:
        existing = result.get("artifacts")
        merged = list(existing) if isinstance(existing, list) else []
        merged.extend(artifacts)
        result["artifacts"] = merged
    return result


def _require_active_gateway_registration(gateway_id: str, *, workspace_id: str = "") -> Dict[str, Any]:
    registration = gateway_state_repository.get_gateway_registration(gateway_id)
    if not registration:
        raise ValueError("Gateway registration was not found.")
    if str(registration.get("status") or "").strip().lower() != "active":
        raise ValueError("Gateway registration is not active.")
    if str(registration.get("device_trust_state") or "").strip().lower() == "revoked":
        raise ValueError("Gateway device trust was revoked.")
    if workspace_id:
        reg_ws = str(registration.get("workspace_id") or "").strip()
        if reg_ws and reg_ws != workspace_id:
            raise PermissionError(
                f"Caller workspace_id {workspace_id} does not match registration workspace_id {reg_ws}."
            )
    if not str(registration.get("workspace_id") or "").strip():
        raise ValueError("Gateway registration is missing workspace_id.")
    return registration


def _enforce_gateway_service_decision(**payload: Any) -> Dict[str, Any]:
    try:
        decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
            "gateway-service-decision",
            payload,
        )
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        reason = _text(getattr(exc, "reason", "")) or "gateway_service_denied"
        raise ValueError(f"Rust gateway-service blocked {payload.get('operation') or 'operation'}: {reason}") from exc
    allowed_next_actions = {
        "tool_execute": {"dispatch_gateway_operation"},
        "tool_interrupt": {"dispatch_gateway_operation"},
    }.get(_text(payload.get("operation")))
    next_action = _text(
        (decision.get("mutation_plan") if isinstance(decision.get("mutation_plan"), dict) else {}).get("next_action")
        or decision.get("next_action")
    )
    if allowed_next_actions and next_action not in allowed_next_actions:
        raise ValueError(
            "Rust gateway-service returned unexpected next_action for "
            f"{payload.get('operation') or 'operation'}: {next_action or 'missing'}"
        )
    return decision


def _enforce_gateway_quota_check(
    *,
    gateway_id: str,
    session_id: str,
    workspace_id: str,
    tenant_id: str,
    device_id: str,
    request_id: str,
    quota_profile: str,
) -> Dict[str, Any]:
    payload = {
        "operation": "quota_check",
        "tenant_id": str(tenant_id or "").strip() or "default",
        "workspace_id": str(workspace_id or "").strip() or "default",
        "actor_id": "system",
        "actor_role": "owner",
        "gateway_id": str(gateway_id or "").strip(),
        "session_id": str(session_id or "").strip(),
        "request_id": str(request_id or "").strip() or None,
        "capability_id": "gateway.quota.check",
        "trace_id": str(request_id or "").strip() or None,
        "quota_profile": str(quota_profile or "").strip() or "standard",
        "risk_level": "normal",
        "policy_decision": "allow",
        "device_trust_state": "trusted",
        "protocol_version": "gateway.v1",
        "approval_provided": True,
        "approval_memory_hit": False,
        "kill_switch_enabled": False,
        "quota_ok": True,
        "gateway_registered": True,
        "session_valid": True,
        "websocket_token_present": True,
        "frame_valid": True,
        "payload_present": True,
        "metadata": {
            "gateway_id": str(gateway_id or "").strip(),
            "session_id": str(session_id or "").strip(),
            "workspace_id": str(workspace_id or "").strip() or "default",
            "tenant_id": str(tenant_id or "").strip() or "default",
            "device_id": str(device_id or "").strip() or None,
            "quota_profile": str(quota_profile or "").strip() or "standard",
        },
    }
    try:
        decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
            "gateway-service-decision",
            payload,
        )
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        reason = _text(getattr(exc, "reason", "")) or "gateway_quota_check_denied"
        raise ValueError(f"Rust gateway-service blocked quota_check: {reason}") from exc
    next_action = _text(decision.get("next_action"))
    if next_action != "allow_gateway_service_operation":
        raise ValueError(
            "Rust gateway-service returned unexpected next_action for "
            f"quota_check: {next_action or 'missing'}"
        )
    return decision


async def execute_tool_via_gateway(
    *,
    gateway_id: str,
    capability_id: str,
    arguments: Optional[Dict[str, Any]],
    run_id: str,
    trace_id: str,
    workspace_id: str,
    timeout_seconds: int = gateway_protocol_service.DEFAULT_TOOL_REQUEST_TIMEOUT_SECONDS,
    request_id: Optional[str] = None,
    runtime_access_mode: Optional[str] = None,
    empyralis_approved: bool = False,
    screenshot_retention: Optional[str] = None,
    gateway_session_id: Optional[str] = None,
    actor_id: Optional[str] = None,
    agent_scope: Optional[str] = None,
    emit_hardware_activity: bool = True,
) -> Dict[str, Any]:
    registration = _require_active_gateway_registration(gateway_id, workspace_id=workspace_id)
    _gw = str(registration.get("gateway_id") or "").strip()
    _ws = str(registration.get("workspace_id") or "").strip()
    _tid = str(trace_id or "").strip()
    _cap = str(capability_id or "").strip()
    supervisor_capability_id, supervisor_arguments = _gateway_supervisor_capability(_cap, arguments)
    registration_metadata = dict(registration.get("metadata") or {})
    resolved_agent_scope = str(agent_scope or "").strip().lower() or "studio_agent"
    resolved_runtime_access_mode = _runtime_access_mode_for_dispatch(
        registration=registration,
        explicit_mode=runtime_access_mode,
    )
    if (
        resolved_runtime_access_mode == execution_mode_policy.FULL_RUNTIME_ACCESS_MODE
        and resolved_agent_scope != "sage"
    ):
        raise PermissionError("full_access Agent Computer execution is available only to Sage.")
    if (
        resolved_runtime_access_mode == execution_mode_policy.FULL_RUNTIME_ACCESS_MODE
        and not _registration_sage_full_access_metadata(registration)
    ):
        raise PermissionError(
            "full_access Agent Computer execution requires the current Sage Full Access setup warning acknowledgement."
        )
    policy_payload = _policy_payload_for_dispatch(
        registration=registration,
        runtime_access_mode=resolved_runtime_access_mode,
        agent_scope=resolved_agent_scope,
    )
    _session_id = _text(
        gateway_session_id
        or registration.get("active_session_id")
        or registration_metadata.get("gateway_session_id")
        or request_id
        or run_id
    )
    _enforce_gateway_quota_check(
        gateway_id=_gw,
        session_id=_session_id,
        workspace_id=_ws,
        tenant_id=str(registration.get("tenant_id") or "default").strip() or "default",
        device_id=str(registration.get("device_id") or "").strip(),
        request_id=_text(request_id or run_id),
        quota_profile="gateway_tool_execution",
    )
    _enforce_hardware_permission_gate(
        gateway_id=_gw,
        workspace_id=_ws,
        capability_id=_cap,
    )
    _enforce_gateway_service_decision(
        operation="tool_execute",
        tenant_id=str(registration.get("tenant_id") or "default").strip() or "default",
        workspace_id=_ws,
        actor_id=_text(actor_id, "system"),
        actor_role="system",
        gateway_id=_gw,
        session_id=_session_id,
        request_id=_text(request_id),
        capability_id=_cap,
        run_id=_text(run_id),
        trace_id=_tid,
        risk_level="normal",
        policy_decision="allow",
        device_trust_state=_text(registration.get("device_trust_state"), "trusted"),
        protocol_version="gateway.v1",
        approval_provided=bool(empyralis_approved),
        approval_memory_hit=True,
        kill_switch_enabled=False,
        quota_ok=True,
        gateway_registered=True,
        session_valid=True,
        frame_valid=True,
        payload_present=True,
    )
    ready, readiness_reason = gateway_registration_execution_readiness(
        registration,
        workspace_id=workspace_id,
        capability_id=_cap,
    )
    if not ready:
        raise ValueError(readiness_reason)
    gateway_transparency_service.emit_gateway_action_event(
        event_type="gateway_action_started",
        title=f"Gateway action: {_cap}",
        summary=f"Executing {_cap} through paired gateway {_gw}",
        status="running",
        trace_id=_tid,
        workspace_id=_ws,
        gateway_id=_gw,
        capability_id=_cap,
    )
    dispatch_started = time.time()
    try:
        response = await gateway_protocol_service.dispatch_tool_invoke(
            gateway_id=str(gateway_id or "").strip(),
            capability_id=supervisor_capability_id,
            arguments=supervisor_arguments,
            run_id=str(run_id or "").strip(),
            trace_id=str(trace_id or "").strip(),
            workspace_id=_ws,
            timeout_seconds=timeout_seconds,
            request_id=request_id,
            runtime_access_mode=resolved_runtime_access_mode,
            empyralis_approved=empyralis_approved,
            agent_scope=resolved_agent_scope,
            policy=policy_payload,
        )
        result = _materialize_gateway_artifacts(
            capability_id=_cap,
            response=response,
            registration=registration,
            run_id=str(response.get("run_id") or run_id).strip(),
            screenshot_retention=screenshot_retention,
        )
    except Exception:
        if emit_hardware_activity:
            hardware_activity_event_service.emit_hardware_action_event(
                workspace_id=_ws,
                tenant_id=str(registration.get("tenant_id") or "default").strip() or "default",
                gateway_id=_gw,
                capability=_cap,
                status="failed",
                duration_ms=int(max(0, (time.time() - dispatch_started) * 1000)),
                run_id=str(run_id or "").strip() or None,
                trace_id=_tid or None,
            )
        raise
    if emit_hardware_activity:
        hardware_activity_event_service.emit_hardware_action_event(
            workspace_id=_ws,
            tenant_id=str(registration.get("tenant_id") or "default").strip() or "default",
            gateway_id=_gw,
            capability=_cap,
            status="completed",
            duration_ms=int(max(0, (time.time() - dispatch_started) * 1000)),
            run_id=str(response.get("run_id") or run_id).strip() or None,
            trace_id=_tid or None,
        )
    activity_payload = {
        "request_id": str(response.get("request_id") or request_id or "").strip() or None,
        "capability_id": _cap,
        "gateway_capability_id": str(response.get("capability_id") or supervisor_capability_id).strip(),
        "run_id": str(response.get("run_id") or run_id).strip(),
        "result": result,
    }
    try:
        await gateway_activity_service.append_gateway_activity(
            registration,
            action="gateway_tool_executed",
            title="Gateway tool executed",
            summary=f"Executed {capability_id} through the paired local gateway.",
            status="completed",
            payload=secret_redaction_service.sanitize_mapping(activity_payload),
            trace_id=str(trace_id or "").strip() or None,
        )
    except Exception:
        pass
    try:
        gateway_transparency_service.emit_gateway_action_event(
            event_type="gateway_action_completed",
            title=f"Gateway action completed: {_cap}",
            summary=f"Completed {_cap} through gateway {_gw}",
            status="completed",
            trace_id=_tid,
            workspace_id=_ws,
            gateway_id=_gw,
            capability_id=_cap,
        )
    except Exception:
        pass
    return {
        "gateway_id": _gw,
        "device_id": str(registration.get("device_id") or "").strip(),
        "workspace_id": _ws,
        "request_id": str(response.get("request_id") or request_id or "").strip(),
        "capability_id": _cap,
        "gateway_capability_id": str(response.get("capability_id") or supervisor_capability_id).strip(),
        "run_id": str(response.get("run_id") or run_id).strip(),
        "result": result,
    }


async def interrupt_tool_via_gateway(
    *,
    gateway_id: str,
    run_id: str,
    trace_id: str,
    workspace_id: str,
    target_request_id: Optional[str] = None,
    reason: Optional[str] = None,
    timeout_seconds: int = gateway_protocol_service.DEFAULT_TOOL_REQUEST_TIMEOUT_SECONDS,
    request_id: Optional[str] = None,
) -> Dict[str, Any]:
    registration = _require_active_gateway_registration(gateway_id, workspace_id=workspace_id)
    _gw = str(registration.get("gateway_id") or "").strip()
    _ws = str(registration.get("workspace_id") or "").strip()
    _tid = str(trace_id or "").strip()
    _session_id = str(request_id or run_id or "").strip()
    _enforce_gateway_quota_check(
        gateway_id=_gw,
        session_id=_session_id,
        workspace_id=_ws,
        tenant_id=str(registration.get("tenant_id") or "default").strip() or "default",
        device_id=str(registration.get("device_id") or "").strip(),
        request_id=str(request_id or run_id or "").strip(),
        quota_profile="gateway_tool_execution",
    )
    _enforce_gateway_service_decision(
        operation="tool_interrupt",
        tenant_id=str(registration.get("tenant_id") or "default").strip() or "default",
        workspace_id=_ws,
        actor_id="system",
        actor_role="system",
        gateway_id=_gw,
        session_id=_session_id,
        request_id=str(request_id or "").strip(),
        capability_id="tool.interrupt",
        run_id=str(run_id or "").strip(),
        trace_id=_tid,
        risk_level="normal",
        policy_decision="allow",
        device_trust_state=_text(registration.get("device_trust_state"), "trusted"),
        protocol_version="gateway.v1",
        approval_provided=True,
        approval_memory_hit=True,
        kill_switch_enabled=False,
        quota_ok=True,
        gateway_registered=True,
        session_valid=True,
        frame_valid=True,
        payload_present=True,
    )
    gateway_transparency_service.emit_gateway_action_event(
        event_type="gateway_action_started",
        title="Gateway interrupt",
        summary=f"Interrupting run {run_id} on gateway {_gw}",
        status="running",
        trace_id=_tid,
        workspace_id=_ws,
        gateway_id=_gw,
        capability_id="tool.interrupt",
    )
    response = await gateway_protocol_service.dispatch_tool_interrupt(
        gateway_id=str(gateway_id or "").strip(),
        run_id=str(run_id or "").strip(),
        trace_id=str(trace_id or "").strip(),
        workspace_id=_ws,
        target_request_id=str(target_request_id or "").strip() or None,
        reason=str(reason or "").strip() or None,
        timeout_seconds=timeout_seconds,
        request_id=request_id,
    )
    activity_payload = {
        "request_id": str(response.get("request_id") or request_id or "").strip() or None,
        "run_id": str(response.get("run_id") or run_id).strip(),
        "target_request_id": str(response.get("target_request_id") or target_request_id or "").strip() or None,
        "interrupted": bool(response.get("interrupted")),
        "interrupt_count": int(response.get("interrupt_count") or 0),
    }
    await gateway_activity_service.append_gateway_activity(
        registration,
        action="gateway_tool_interrupted",
        title="Gateway tool interrupted",
        summary=f"Interrupted local gateway run {run_id}.",
        status="completed",
        payload=secret_redaction_service.sanitize_mapping(activity_payload),
        trace_id=str(trace_id or "").strip() or None,
    )
    gateway_transparency_service.emit_gateway_action_event(
        event_type="gateway_action_completed",
        title="Gateway interrupt completed",
        summary=f"Interrupted run {run_id} on gateway {_gw}",
        status="completed",
        trace_id=_tid,
        workspace_id=_ws,
        gateway_id=_gw,
        capability_id="tool.interrupt",
    )
    return {
        "gateway_id": str(registration.get("gateway_id") or "").strip(),
        "device_id": str(registration.get("device_id") or "").strip(),
        "workspace_id": str(registration.get("workspace_id") or "").strip(),
        "request_id": str(response.get("request_id") or request_id or "").strip(),
        "run_id": str(response.get("run_id") or run_id).strip(),
        "target_request_id": str(response.get("target_request_id") or target_request_id or "").strip() or None,
        "interrupted": bool(response.get("interrupted")),
        "interrupt_count": int(response.get("interrupt_count") or 0),
    }

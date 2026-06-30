from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import uuid

from server_modules import (
    hardware_access_policy_service,
    hardware_runtime_target_resolver,
    rust_runtime_kernel_client,
    secret_redaction_service,
    session_service,
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


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> Dict[str, Any]:
    return dict(value or {}) if isinstance(value, dict) else {}


def _list_dicts(value: Any) -> List[Dict[str, Any]]:
    return [dict(item) for item in list(value or []) if isinstance(item, dict)]


def _runtime_binding_mode_for_target(canonical_runtime_target: str) -> str:
    target = _text(canonical_runtime_target)
    if target == "empyralis_cloud_computer":
        return "cloud_computer"
    if target == "user_device_gateway":
        return "my_computer"
    if target == "self_hosted_node":
        return "self_hosted"
    return "text"


def _enforce_runtime_binding_decision(
    *,
    tenant_id: str,
    workspace_id: str,
    canonical_runtime_target: str,
    gateway_id: Optional[str],
    device_id: Optional[str],
    node_id: Optional[str],
    thread_id: Optional[str],
    session_id: str,
) -> Dict[str, Any]:
    mode = _runtime_binding_mode_for_target(canonical_runtime_target)
    if mode == "self_hosted":
        return {
            "ok": True,
            "decision": "allow",
            "operation": "ensure_self_hosted_runtime_session",
            "next_action": "skip_hardware_self_hosted_runtime_binding",
        }
    operation = "ensure_cloud_runtime_session" if mode == "cloud_computer" else "ensure_runtime_session"
    payload = {
        "operation": operation,
        "deployed_agent_id": "sage_hardware_runtime",
        "tenant_id": _text(tenant_id) or "default",
        "workspace_id": _text(workspace_id) or "default",
        "session_id": _text(session_id),
        "thread_id": _text(thread_id) or _text(session_id),
        "studio_agent_mode": mode,
    }
    if mode == "my_computer":
        gateway_present = bool(_text(gateway_id) or _text(device_id))
        payload.update(
            {
                "local_gateway_attachment_present": gateway_present,
                "local_gateway_online": gateway_present,
                "local_gateway_healthy": gateway_present,
                "local_gateway_revoked": False,
                "runtime_attachment_id": _text(gateway_id) or _text(device_id) or None,
                "gateway_id": _text(gateway_id) or None,
                "runtime_node_id": _text(node_id) or None,
            }
        )
    try:
        decision = rust_runtime_kernel_client.run_runtime_kernel_enforced("runtime-binding-decision", payload)
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        raise RuntimeError(f"Rust runtime-binding gate blocked {operation}: {exc.reason}") from exc
    next_action = _text(decision.get("next_action"))
    expected_next_action = {
        "text": "skip_runtime_binding",
        "cloud_computer": "create_cloud_runtime_session",
        "my_computer": "create_local_gateway_runtime_session",
    }.get(mode)
    if expected_next_action and next_action != expected_next_action:
        raise RuntimeError(
            f"Rust runtime-binding gate blocked {operation}: unexpected_next_action:{next_action or 'missing'}"
        )
    return dict(decision)


def new_runtime_session_id() -> str:
    return f"hrs_{uuid.uuid4().hex}"


def normalize_state(value: Any) -> str:
    token = _text(value).lower()
    return token if token in HARDWARE_ACTION_STATES else "running"


def audit_event(action: str, *, state: str, reason: Optional[str] = None, payload: Any = None) -> Dict[str, Any]:
    event = {
        "action": _text(action) or "hardware_action.event",
        "state": normalize_state(state),
        "at": _utc_now_iso(),
    }
    if reason:
        event["reason"] = _text(reason)
    if isinstance(payload, dict) and payload:
        event["payload"] = secret_redaction_service.sanitize_mapping(payload)
    return event


def session_view(session_id: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "session_id": _text(session_id),
        "runtime_session_binding": HARDWARE_RUNTIME_SESSION_BINDING,
        "state": normalize_state(metadata.get("state")),
        "runtime_target": _text(metadata.get("runtime_target")) or "cloud_default",
        "canonical_runtime_target": _text(metadata.get("canonical_runtime_target")) or "cloud_default",
        "runtime_fabric_target": _text(metadata.get("runtime_fabric_target"))
        or _text(metadata.get("canonical_runtime_target"))
        or "cloud_default",
        "hardware_edge": _text(metadata.get("hardware_edge")) or "managed_cloud",
        "runtime_access_mode": hardware_access_policy_service.normalize_runtime_access_mode(metadata.get("runtime_access_mode")),
        "execution_mode": _text(metadata.get("execution_mode")) or None,
        "permission_mode": _text(metadata.get("permission_mode")) or None,
        "approval_mode": _text(metadata.get("approval_mode")) or None,
        "empyralis_action_approvals_enabled": bool(metadata.get("empyralis_action_approvals_enabled", True)),
        "tenant_id": _text(metadata.get("tenant_id")) or None,
        "workspace_id": _text(metadata.get("workspace_id")) or None,
        "thread_id": _text(metadata.get("thread_id")) or None,
        "user_id": _text(metadata.get("user_id")) or None,
        "gateway_id": _text(metadata.get("gateway_id")) or None,
        "device_id": _text(metadata.get("device_id")) or None,
        "node_id": _text(metadata.get("node_id")) or None,
        "runtime_node_id": _text(metadata.get("runtime_node_id")) or None,
        "runtime_profile_id": _text(metadata.get("runtime_profile_id")) or None,
        "runtime_attachment_id": _text(metadata.get("runtime_attachment_id")) or None,
        "self_hosted_command_id": _text(metadata.get("self_hosted_command_id")) or None,
        "runtime_choice": _text(metadata.get("runtime_choice")) or None,
        "runtime_kind": _text(metadata.get("runtime_kind")) or None,
        "cloud_computer_session_id": _text(metadata.get("cloud_computer_session_id")) or None,
        "run_id": _text(metadata.get("run_id")) or None,
        "trace_id": _text(metadata.get("trace_id")) or None,
        "request_id": _text(metadata.get("request_id")) or None,
        "capability_id": _text(metadata.get("capability_id")) or None,
        "action_id": _text(metadata.get("action_id")) or None,
        "approvals": _list_dicts(metadata.get("approvals")),
        "artifacts": list(metadata.get("artifacts") or []),
        "audit_events": _list_dicts(metadata.get("audit_events")),
        "billing": _dict(metadata.get("billing")),
    }


def runtime_session_with_correlation(
    runtime_session: Dict[str, Any],
    *,
    payload: Optional[Dict[str, Any]] = None,
    session_record: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    session = dict(runtime_session or {})
    source = _dict(payload)
    record = _dict(session_record)
    for key in (
        "tenant_id",
        "workspace_id",
        "thread_id",
        "user_id",
        "run_id",
        "trace_id",
        "request_id",
        "capability_id",
        "action_id",
        "runtime_node_id",
        "runtime_profile_id",
    ):
        if _text(session.get(key)):
            continue
        candidate = _text(source.get(key)) or _text(record.get(key))
        if candidate:
            session[key] = candidate
    return session


async def create_runtime_session(
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    runtime_target: str,
    canonical_runtime_target: str,
    gateway_id: Optional[str],
    device_id: Optional[str],
    node_id: Optional[str],
    action_id: str,
    capability_id: str,
    run_id: str,
    trace_id: str,
    request_id: str,
    thread_id: Optional[str],
    session_id: Optional[str],
    initial_state: str,
    cost_metadata: Optional[Dict[str, Any]],
    runtime_access_mode: str,
    execution_mode: Optional[str],
) -> Dict[str, Any]:
    resolved_session_id = _text(session_id) or new_runtime_session_id()
    _enforce_runtime_binding_decision(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        canonical_runtime_target=canonical_runtime_target,
        gateway_id=gateway_id,
        device_id=device_id,
        node_id=node_id,
        thread_id=thread_id,
        session_id=resolved_session_id,
    )
    access_metadata = hardware_access_policy_service.runtime_access_metadata(runtime_access_mode, execution_mode)
    metadata = {
        "thread_id": _text(thread_id) or resolved_session_id,
        "runtime_session_binding": HARDWARE_RUNTIME_SESSION_BINDING,
        "runtime_session_id": resolved_session_id,
        "runtime_target": runtime_target,
        "canonical_runtime_target": canonical_runtime_target,
        "runtime_fabric_target": canonical_runtime_target,
        "hardware_edge": hardware_runtime_target_resolver.runtime_edge(canonical_runtime_target),
        **access_metadata,
        "state": normalize_state(initial_state),
        "tenant_id": _text(tenant_id) or "default",
        "workspace_id": _text(workspace_id) or "default",
        "user_id": _text(user_id),
        "gateway_id": _text(gateway_id) or None,
        "device_id": _text(device_id) or None,
        "node_id": _text(node_id) or None,
        "action_id": action_id,
        "capability_id": capability_id,
        "run_id": run_id,
        "trace_id": trace_id,
        "request_id": request_id,
        "approvals": [],
        "artifacts": [],
        "audit_events": [
            audit_event(
                "hardware_runtime_session.created",
                state=normalize_state(initial_state),
                payload={
                    "runtime_target": runtime_target,
                    "canonical_runtime_target": canonical_runtime_target,
                    "runtime_access_mode": access_metadata["runtime_access_mode"],
                    "capability_id": capability_id,
                    "request_id": request_id,
                },
            )
        ],
        "billing": _dict(cost_metadata),
    }
    await session_service.create_session(
        workspace_id=_text(workspace_id) or "default",
        tenant_id=_text(tenant_id) or "default",
        actor={
            "type": "sage_hardware_runtime",
            "id": _text(user_id) or "sage",
            "display_name": "Sage hardware runtime",
        },
        channel=HARDWARE_RUNTIME_CHANNEL,
        metadata=metadata,
        session_id=resolved_session_id,
    )
    return session_view(resolved_session_id, metadata)


async def update_runtime_session(
    runtime_session: Dict[str, Any],
    *,
    state: str,
    audit_action: str,
    reason: Optional[str] = None,
    approvals: Optional[List[Dict[str, Any]]] = None,
    artifacts: Optional[List[Any]] = None,
    extra_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    metadata = {
        key: value
        for key, value in runtime_session.items()
        if key
        in {
            "runtime_session_binding",
            "state",
            "runtime_target",
            "canonical_runtime_target",
            "runtime_fabric_target",
            "hardware_edge",
            "runtime_access_mode",
            "execution_mode",
            "permission_mode",
            "approval_mode",
            "empyralis_action_approvals_enabled",
            "tenant_id",
            "workspace_id",
            "thread_id",
            "user_id",
            "gateway_id",
            "device_id",
            "node_id",
            "runtime_node_id",
            "runtime_profile_id",
            "runtime_attachment_id",
            "self_hosted_command_id",
            "runtime_choice",
            "runtime_kind",
            "cloud_computer_session_id",
            "run_id",
            "trace_id",
            "request_id",
            "capability_id",
            "action_id",
            "approvals",
            "artifacts",
            "audit_events",
            "billing",
        }
    }
    metadata["state"] = normalize_state(state)
    merged_approvals = _list_dicts(metadata.get("approvals"))
    for approval in list(approvals or []):
        if isinstance(approval, dict):
            merged_approvals.append(secret_redaction_service.sanitize_mapping(approval))
    metadata["approvals"] = merged_approvals
    merged_artifacts = list(metadata.get("artifacts") or [])
    for artifact in list(artifacts or []):
        if artifact and artifact not in merged_artifacts:
            merged_artifacts.append(artifact)
    metadata["artifacts"] = merged_artifacts
    audit_events = _list_dicts(metadata.get("audit_events"))
    audit_events.append(
        audit_event(
            audit_action,
            state=metadata["state"],
            reason=reason,
            payload=extra_metadata,
        )
    )
    metadata["audit_events"] = audit_events
    if isinstance(extra_metadata, dict):
        for key, value in extra_metadata.items():
            if key not in {"arguments", "raw_arguments"}:
                metadata[key] = value
    session_id = _text(runtime_session.get("session_id"))
    try:
        updated = await session_service.extend_session(session_id, metadata_updates=metadata)
    except Exception:
        updated = None
    if isinstance(updated, dict):
        updated_metadata = _dict(updated.get("metadata"))
        if updated_metadata:
            return session_view(session_id, updated_metadata)
    return session_view(session_id, metadata)

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from server_modules import (
    browser_approval_service,
    browser_checkpoint_service,
    gateway_activity_service,
    gateway_execution_service,
    gateway_protocol_service,
    gateway_state_repository,
    rust_runtime_kernel_client,
    runtime_policy,
)


BROWSER_SESSION_START_CAPABILITY = "browser.session.start"
BROWSER_SESSION_ACTION_CAPABILITY = "browser.session.action"
BROWSER_SESSION_TAKEOVER_CAPABILITY = "browser.session.takeover"
BROWSER_SESSION_RESUME_CAPABILITY = "browser.session.resume"
BROWSER_SESSION_INTERRUPT_CAPABILITY = "browser.session.interrupt"

RISKY_BROWSER_ACTIONS = {
    "click",
    "fill",
    "select",
    "upload_files",
    "execute_js",
    "download_file",
    "new_tab",
    "close_tab",
}

BROWSER_SESSION_MODE_MANAGED_PROFILE = "managed_profile"
BROWSER_SESSION_MODE_EXISTING_SESSION_ATTACH = "existing_session_attach"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _enforce_gateway_browser_action_decision(
    *,
    registration: Dict[str, Any],
    capability_id: str,
    arguments: Optional[Dict[str, Any]],
    run_id: str,
    trace_id: str,
    request_id: Optional[str],
) -> Dict[str, Any]:
    operation = ""
    expected_next_action = ""
    if capability_id == BROWSER_SESSION_START_CAPABILITY:
        operation = "browser_session_start"
        expected_next_action = "start_browser_session"
    elif capability_id == BROWSER_SESSION_ACTION_CAPABILITY:
        operation = "browser_action"
        expected_next_action = "dispatch_browser_action"
    elif capability_id == BROWSER_SESSION_INTERRUPT_CAPABILITY:
        operation = "browser_session_stop"
        expected_next_action = "stop_browser_session"
    elif capability_id == BROWSER_SESSION_RESUME_CAPABILITY:
        operation = "browser_session_resume"
        expected_next_action = "resume_browser_session"
    elif capability_id == BROWSER_SESSION_TAKEOVER_CAPABILITY:
        operation = "browser_session_takeover"
        expected_next_action = "takeover_browser_session"
    else:
        return {}
    payload = {
        "operation": operation,
        "workspace_id": str(registration.get("workspace_id") or "").strip() or "default",
        "tenant_id": str(registration.get("tenant_id") or "").strip() or "default",
        "gateway_id": str(registration.get("gateway_id") or "").strip(),
        "run_id": str(run_id or "").strip(),
        "request_id": str(request_id or "").strip() or None,
        "trace_id": str(trace_id or "").strip() or None,
        "capability_id": str(capability_id or "").strip(),
        "actor_role": "owner",
        "gateway_connected": True,
        "risk_decision": "allow",
        "approval_provided": True,
    }
    browser_session_id = str((arguments or {}).get("browser_session_id") or "").strip()
    if browser_session_id:
        payload["browser_session_id"] = browser_session_id
    try:
        decision = rust_runtime_kernel_client.gateway_action_decision(**payload)
        decision = rust_runtime_kernel_client.enforce_kernel_decision(
            "gateway-action-decision",
            decision,
        )
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        reason = str(getattr(exc, "reason", "") or "gateway_browser_action_denied").strip()
        raise RuntimeError(f"Rust gateway-action gate blocked {operation}: {reason}") from exc
    next_action = str(decision.get("next_action") or "").strip()
    if next_action != expected_next_action:
        raise RuntimeError(
            f"Rust gateway-action gate returned unexpected next_action for {operation}: "
            f"{next_action or 'missing'}"
        )
    return decision


def _enforce_gateway_browser_fallback_decision(
    *,
    registration: Dict[str, Any],
    run_id: str,
    trace_id: str,
) -> Dict[str, Any]:
    payload = {
        "operation": "browser_session_start",
        "workspace_id": str(registration.get("workspace_id") or "").strip() or "default",
        "tenant_id": str(registration.get("tenant_id") or "").strip() or "default",
        "gateway_id": str(registration.get("gateway_id") or "").strip(),
        "run_id": str(run_id or "").strip(),
        "request_id": None,
        "trace_id": str(trace_id or "").strip() or None,
        "capability_id": BROWSER_SESSION_START_CAPABILITY,
        "actor_role": "owner",
        "gateway_connected": False,
        "cloud_fallback_available": True,
        "risk_decision": "allow",
        "approval_provided": True,
    }
    try:
        decision = rust_runtime_kernel_client.gateway_action_decision(**payload)
        decision = rust_runtime_kernel_client.enforce_kernel_decision(
            "gateway-action-decision",
            decision,
        )
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        reason = str(getattr(exc, "reason", "") or "gateway_browser_fallback_blocked").strip()
        raise RuntimeError(f"Rust gateway-action gate blocked browser fallback: {reason}") from exc
    next_action = str(decision.get("next_action") or "").strip()
    if next_action != "prepare_cloud_browser_fallback":
        raise RuntimeError(
            "Rust gateway-action gate returned unexpected next_action for browser fallback: "
            f"{next_action or 'missing'}"
        )
    return decision


def _gateway_browser_quota_profile(capability_id: str) -> str:
    if capability_id in {
        BROWSER_SESSION_START_CAPABILITY,
        BROWSER_SESSION_RESUME_CAPABILITY,
    }:
        return "gateway_browser_session"
    return ""


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


def browser_action_requires_owner_approval(
    action: Optional[str],
    *,
    reviewed_approval_required: bool = False,
) -> bool:
    if reviewed_approval_required:
        return True
    return str(action or "").strip().lower() in RISKY_BROWSER_ACTIONS


def _interactive_actions(values: Optional[Iterable[Any]]) -> list[str]:
    return [
        str(item or "").strip().lower()
        for item in (values or [])
        if str(item or "").strip()
    ]


def normalize_browser_session_mode(value: Any) -> str:
    token = str(value or "").strip().lower()
    if token == BROWSER_SESSION_MODE_EXISTING_SESSION_ATTACH:
        return BROWSER_SESSION_MODE_EXISTING_SESSION_ATTACH
    return BROWSER_SESSION_MODE_MANAGED_PROFILE


def browser_session_requires_reviewed_approval(
    *,
    session_mode: Optional[str],
    attach_endpoint_url: Optional[str] = None,
    reviewed_approval_required: bool = False,
) -> bool:
    if reviewed_approval_required:
        return True
    return (
        normalize_browser_session_mode(session_mode) == BROWSER_SESSION_MODE_EXISTING_SESSION_ATTACH
        and bool(str(attach_endpoint_url or "").strip())
    )


def _browser_plan_hash(payload: Dict[str, Any]) -> str:
    encoded = json.dumps(dict(payload or {}), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _browser_metadata(
    *,
    session_profile: Optional[str],
    session_mode: Optional[str] = None,
    attach_endpoint_url: Optional[str] = None,
    interactive_actions: Optional[Iterable[Any]] = None,
    reviewed_approval_required: bool = False,
    reviewed_approved: bool = False,
    browser_session_id: Optional[str] = None,
    checkpoint: Optional[Dict[str, Any]] = None,
    plan_payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    profile_token = str(session_profile or "").strip()
    resolved_session_mode = normalize_browser_session_mode(session_mode)
    resolved_attach_endpoint_url = str(attach_endpoint_url or "").strip() or None
    plan_hash = _browser_plan_hash(
        {
            "session_profile": profile_token,
            "session_mode": resolved_session_mode,
            "attach_endpoint_url": resolved_attach_endpoint_url,
            "interactive_actions": _interactive_actions(interactive_actions),
            "plan": dict(plan_payload or {}),
        }
    )
    metadata = browser_approval_service.browser_policy_metadata_overrides(
        {
            "reviewed_approval_required": bool(reviewed_approval_required),
            "immutable_plan_hash": plan_hash,
        },
        session_profile=profile_token,
        interactive_actions=_interactive_actions(interactive_actions),
    )
    metadata["execution_target_selected"] = "local_gateway"
    metadata["browser_execution_binding"] = runtime_policy.build_browser_execution_binding(
        _repo_root(),
        plan_hash,
        profile_token,
    )
    metadata["browser_session_mode"] = resolved_session_mode
    metadata["browser_attach_requested"] = resolved_session_mode == BROWSER_SESSION_MODE_EXISTING_SESSION_ATTACH
    metadata["browser_sensitive_access"] = bool(reviewed_approval_required)
    if reviewed_approval_required:
        metadata["browser_sensitive_reason"] = (
            "existing_session_attach"
            if resolved_session_mode == BROWSER_SESSION_MODE_EXISTING_SESSION_ATTACH
            else "reviewed_browser_access"
        )
    else:
        metadata.pop("browser_sensitive_reason", None)
    if resolved_attach_endpoint_url:
        metadata["browser_attach_endpoint_url"] = resolved_attach_endpoint_url
    else:
        metadata.pop("browser_attach_endpoint_url", None)
    metadata["browser_resume_supported"] = bool(checkpoint)
    if reviewed_approved:
        metadata["browser_reviewed_approved"] = True
        metadata["browser_reviewed_approved_at"] = gateway_state_repository._utc_now_iso()
    if browser_session_id:
        metadata["browser_session_id"] = str(browser_session_id).strip()
    if checkpoint:
        metadata["browser_checkpoint"] = dict(checkpoint)
    return metadata


def build_gateway_browser_metadata(
    *,
    session_profile: Optional[str],
    session_mode: Optional[str] = None,
    attach_endpoint_url: Optional[str] = None,
    interactive_actions: Optional[Iterable[Any]] = None,
    reviewed_approval_required: bool = False,
    reviewed_approved: bool = False,
    browser_session_id: Optional[str] = None,
    checkpoint: Optional[Dict[str, Any]] = None,
    plan_payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return _browser_metadata(
        session_profile=session_profile,
        session_mode=session_mode,
        attach_endpoint_url=attach_endpoint_url,
        interactive_actions=interactive_actions,
        reviewed_approval_required=reviewed_approval_required,
        reviewed_approved=reviewed_approved,
        browser_session_id=browser_session_id,
        checkpoint=checkpoint,
        plan_payload=plan_payload,
    )


def _persist_browser_session_result(
    *,
    registration: Dict[str, Any],
    run_id: str,
    trace_id: str,
    result: Dict[str, Any],
    fallback_execution_target: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    browser_session = result.get("browser_session") if isinstance(result.get("browser_session"), dict) else {}
    if not browser_session:
        browser_session_id = str(result.get("browser_session_id") or "").strip()
        if not browser_session_id:
            return None
        browser_session = {
            "browser_session_id": browser_session_id,
            "status": result.get("status"),
            "current_url": result.get("current_url"),
            "session_profile": result.get("session_profile"),
            "manual_takeover": result.get("manual_takeover"),
            "resume_supported": result.get("resume_supported"),
            "checkpoint": result.get("checkpoint") if isinstance(result.get("checkpoint"), dict) else {},
            "snapshot": result.get("snapshot") if isinstance(result.get("snapshot"), dict) else {},
            "metadata": result.get("metadata") if isinstance(result.get("metadata"), dict) else {},
            "execution_binding": result.get("execution_binding") if isinstance(result.get("execution_binding"), dict) else {},
            "immutable_plan_hash": result.get("immutable_plan_hash"),
            "reviewed_approval_required": result.get("reviewed_approval_required"),
            "reviewed_approved": result.get("reviewed_approved") or result.get("reviewed_approval_approved"),
        }
    checkpoint = browser_session.get("checkpoint") if isinstance(browser_session.get("checkpoint"), dict) else {}
    metadata = browser_session.get("metadata") if isinstance(browser_session.get("metadata"), dict) else {}
    execution_target = (
        str(browser_session.get("execution_target") or fallback_execution_target or "local_gateway").strip()
        or "local_gateway"
    )
    session = gateway_state_repository.upsert_gateway_browser_session(
        browser_session_id=str(browser_session.get("browser_session_id") or "").strip(),
        gateway_id=str(registration.get("gateway_id") or "").strip(),
        device_id=str(registration.get("device_id") or "").strip(),
        tenant_id=str(registration.get("tenant_id") or "").strip(),
        workspace_id=str(registration.get("workspace_id") or "").strip(),
        user_id=str(registration.get("user_id") or "").strip(),
        run_id=str(run_id or "").strip(),
        trace_id=str(trace_id or "").strip() or None,
        status=str(browser_session.get("status") or result.get("status") or "active").strip() or "active",
        execution_target=execution_target,
        session_profile=str(browser_session.get("session_profile") or "").strip() or None,
        current_url=str(browser_session.get("current_url") or "").strip() or None,
        manual_takeover=bool(browser_session.get("manual_takeover")),
        resume_supported=bool(
            browser_session.get("resume_supported")
            or browser_checkpoint_service.browser_resume_supported({"browser_checkpoint": checkpoint}, metadata)
        ),
        reviewed_approval_required=bool(browser_session.get("reviewed_approval_required") or metadata.get("browser_reviewed_approval_required")),
        reviewed_approved=bool(browser_session.get("reviewed_approved") or metadata.get("browser_reviewed_approved")),
        immutable_plan_hash=str(browser_session.get("immutable_plan_hash") or metadata.get("browser_immutable_plan_hash") or "").strip() or None,
        execution_binding=(
            browser_session.get("execution_binding")
            if isinstance(browser_session.get("execution_binding"), dict)
            else metadata.get("browser_execution_binding")
            if isinstance(metadata.get("browser_execution_binding"), dict)
            else {}
        ),
        checkpoint_payload=checkpoint,
        snapshot_payload=browser_session.get("snapshot") if isinstance(browser_session.get("snapshot"), dict) else {},
        metadata=metadata,
        fallback_ready_at=str(browser_session.get("fallback_ready_at") or "").strip() or None,
        interrupted_at=str(browser_session.get("interrupted_at") or "").strip() or None,
    )
    return session


async def _append_browser_activity(
    registration: Dict[str, Any],
    *,
    action: str,
    title: str,
    summary: str,
    status: str,
    trace_id: str,
    payload: Optional[Dict[str, Any]] = None,
) -> None:
    await gateway_activity_service.append_gateway_activity(
        registration,
        action=action,
        title=title,
        summary=summary,
        status=status,
        event_class="browser_action",
        payload=payload,
        trace_id=trace_id or None,
    )


def build_cloud_browser_fallback(
    *,
    registration: Dict[str, Any],
    run_id: str,
    trace_id: str,
    session_profile: Optional[str],
    reason: str,
    checkpoint: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    _enforce_gateway_browser_fallback_decision(
        registration=registration,
        run_id=run_id,
        trace_id=trace_id,
    )
    browser_session_id = f"gbsess_fallback_{uuid.uuid4().hex}"
    metadata = _browser_metadata(
        session_profile=session_profile,
        interactive_actions=[],
        reviewed_approval_required=False,
        plan_payload={"reason": reason, "execution_target": "cloud_browser"},
        checkpoint=checkpoint,
        browser_session_id=browser_session_id,
    )
    metadata["execution_target_selected"] = "cloud_browser"
    session = gateway_state_repository.upsert_gateway_browser_session(
        browser_session_id=browser_session_id,
        gateway_id=str(registration.get("gateway_id") or "").strip(),
        device_id=str(registration.get("device_id") or "").strip(),
        tenant_id=str(registration.get("tenant_id") or "").strip(),
        workspace_id=str(registration.get("workspace_id") or "").strip(),
        user_id=str(registration.get("user_id") or "").strip(),
        run_id=str(run_id or "").strip(),
        trace_id=str(trace_id or "").strip() or None,
        status="fallback_ready",
        execution_target="cloud_browser",
        session_profile=str(session_profile or "").strip() or None,
        manual_takeover=False,
        resume_supported=bool(checkpoint),
        reviewed_approval_required=False,
        reviewed_approved=False,
        immutable_plan_hash=str(metadata.get("browser_immutable_plan_hash") or "").strip() or None,
        execution_binding=(
            metadata.get("browser_execution_binding")
            if isinstance(metadata.get("browser_execution_binding"), dict)
            else {}
        ),
        checkpoint_payload=checkpoint,
        metadata={
            **metadata,
            "fallback_reason": str(reason or "").strip(),
        },
        fallback_ready_at=gateway_state_repository._utc_now_iso(),
    )
    gateway_state_repository.record_gateway_event(
        gateway_id=str(registration.get("gateway_id") or "").strip(),
        session_id=None,
        direction="system",
        frame_kind="event",
        message_type="gateway.browser.fallback_ready",
        payload={"browser_session": session, "reason": str(reason or "").strip()},
    )
    return {
        "status": "fallback_ready",
        "execution_target": "cloud_browser",
        "gateway_id": str(registration.get("gateway_id") or "").strip(),
        "run_id": str(run_id or "").strip(),
        "trace_id": str(trace_id or "").strip(),
        "browser_session": session,
        "fallback_reason": str(reason or "").strip(),
    }


async def build_cloud_browser_fallback_response(
    *,
    registration: Dict[str, Any],
    run_id: str,
    trace_id: str,
    session_profile: Optional[str],
    reason: str,
    checkpoint: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload = build_cloud_browser_fallback(
        registration=registration,
        run_id=run_id,
        trace_id=trace_id,
        session_profile=session_profile,
        reason=reason,
        checkpoint=checkpoint,
    )
    await _append_browser_activity(
        registration,
        action="gateway_browser_fallback_ready",
        title="Cloud browser fallback ready",
        summary="Local browser runtime was unavailable, so cloud browser fallback was prepared.",
        status="degraded",
        trace_id=trace_id,
        payload=payload,
    )
    return payload


async def execute_browser_capability_via_gateway(
    *,
    gateway_id: str,
    capability_id: str,
    arguments: Optional[Dict[str, Any]],
    run_id: str,
    trace_id: str,
    workspace_id: str,
    timeout_seconds: int = gateway_protocol_service.DEFAULT_TOOL_REQUEST_TIMEOUT_SECONDS,
    request_id: Optional[str] = None,
) -> Dict[str, Any]:
    registration = _require_active_gateway_registration(gateway_id, workspace_id=workspace_id)
    _enforce_gateway_browser_action_decision(
        registration=registration,
        capability_id=capability_id,
        arguments=arguments,
        run_id=run_id,
        trace_id=trace_id,
        request_id=request_id,
    )
    quota_profile = _gateway_browser_quota_profile(capability_id)
    if quota_profile:
        registration_metadata = dict(registration.get("metadata") or {})
        gateway_execution_service._enforce_gateway_quota_check(
            gateway_id=str(registration.get("gateway_id") or "").strip(),
            session_id=str(
                registration.get("active_session_id")
                or registration_metadata.get("gateway_session_id")
                or request_id
                or run_id
                or ""
            ).strip(),
            workspace_id=str(registration.get("workspace_id") or "").strip(),
            tenant_id=str(registration.get("tenant_id") or "").strip() or "default",
            device_id=str(registration.get("device_id") or "").strip(),
            request_id=str(request_id or run_id or "").strip(),
            quota_profile=quota_profile,
        )
    _ws = str(registration.get("workspace_id") or "").strip()
    response = await gateway_execution_service.execute_tool_via_gateway(
        gateway_id=gateway_id,
        capability_id=capability_id,
        arguments=dict(arguments or {}),
        run_id=run_id,
        trace_id=trace_id,
        workspace_id=_ws,
        timeout_seconds=timeout_seconds,
        request_id=request_id,
        agent_scope="sage",
    )
    session = _persist_browser_session_result(
        registration=registration,
        run_id=run_id,
        trace_id=trace_id,
        result=dict(response.get("result") or {}),
    )
    activity_payload = {
        "capability_id": str(capability_id or "").strip(),
        "request_id": str(response.get("request_id") or request_id or "").strip() or None,
        "browser_session": session,
    }
    if capability_id == BROWSER_SESSION_START_CAPABILITY:
        session_status = str((session or {}).get("status") or "").strip().lower()
        activity_status = "completed"
        title = "Gateway browser session started"
        summary = "Started a local browser session through the paired gateway."
        if session_status == "attached":
            title = "Gateway browser attached"
            summary = "Attached to an existing local browser session through the paired gateway."
        elif session_status == "attach_required":
            activity_status = "waiting_for_input"
            title = "Gateway browser attach required"
            summary = "Existing-session browser attach needs a local debugging endpoint before it can start."
        elif session_status in {"attach_failed", "not_attached"}:
            activity_status = "degraded" if session_status == "not_attached" else "failed"
            title = "Gateway browser attach unavailable"
            summary = "Existing-session browser attach could not be established through the paired gateway."
        await _append_browser_activity(
            registration,
            action="gateway_browser_session_started",
            title=title,
            summary=summary,
            status=activity_status,
            trace_id=trace_id,
            payload=activity_payload,
        )
    elif capability_id == BROWSER_SESSION_ACTION_CAPABILITY:
        action_name = str((arguments or {}).get("action") or "").strip() or "browser_action"
        session_status = str((session or {}).get("status") or "").strip().lower()
        activity_status = "completed"
        title = "Gateway browser action executed"
        summary = f"Executed browser action {action_name}."
        if session_status == "attach_required":
            activity_status = "waiting_for_input"
            title = "Gateway browser attach required"
            summary = "Browser action is waiting for an existing-session attach endpoint."
        elif session_status == "not_attached":
            activity_status = "degraded"
            title = "Gateway browser not attached"
            summary = "Browser action could not run because the existing-session browser is not attached."
        elif session_status == "attach_failed":
            activity_status = "failed"
            title = "Gateway browser attach failed"
            summary = "Browser action could not run because existing-session browser attach failed."
        await _append_browser_activity(
            registration,
            action="gateway_browser_action_executed",
            title=title,
            summary=summary,
            status=activity_status,
            trace_id=trace_id,
            payload=activity_payload,
        )
    elif capability_id == BROWSER_SESSION_TAKEOVER_CAPABILITY:
        await _append_browser_activity(
            registration,
            action="gateway_browser_takeover",
            title="Gateway browser takeover enabled",
            summary="Browser session is paused for manual takeover.",
            status="waiting_for_input",
            trace_id=trace_id,
            payload=activity_payload,
        )
    elif capability_id == BROWSER_SESSION_RESUME_CAPABILITY:
        session_status = str((session or {}).get("status") or "").strip().lower()
        activity_status = "completed"
        title = "Gateway browser session resumed"
        summary = "Browser session resumed from its checkpoint."
        if session_status == "attach_required":
            activity_status = "waiting_for_input"
            title = "Gateway browser attach required"
            summary = "Browser session resume is waiting for an existing-session attach endpoint."
        elif session_status == "not_attached":
            activity_status = "degraded"
            title = "Gateway browser not attached"
            summary = "Browser session resume could not continue because the existing-session browser is not attached."
        elif session_status == "attach_failed":
            activity_status = "failed"
            title = "Gateway browser attach failed"
            summary = "Browser session resume could not continue because existing-session browser attach failed."
        await _append_browser_activity(
            registration,
            action="gateway_browser_resumed",
            title=title,
            summary=summary,
            status=activity_status,
            trace_id=trace_id,
            payload=activity_payload,
        )
    elif capability_id == BROWSER_SESSION_INTERRUPT_CAPABILITY:
        await _append_browser_activity(
            registration,
            action="gateway_browser_interrupted",
            title="Gateway browser session interrupted",
            summary="Browser session interrupt was delivered to the gateway.",
            status="completed",
            trace_id=trace_id,
            payload=activity_payload,
        )
    return {
        **response,
        "browser_session": session,
    }

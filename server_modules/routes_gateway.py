from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import os
import shlex
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from urllib import parse as urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
from starlette import status
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from server_modules.auth import enforce_workspace_access, validate_csrf, workspace_tenant_id
from server_modules.connection_oauth_service import request_origin as _oauth_request_origin
from server_modules.kill_switch_gate import assert_not_killed, KillSwitchBlockedError
from server_modules.gateway_quota_enforcement import (
    evaluate_gateway_quota,
    GATEWAY_TOOL_EXECUTION,
    GATEWAY_BROWSER_SESSION,
    GATEWAY_APPROVAL_ACTION,
    GATEWAY_WS_CONNECTION,
)
from server_modules.safety_error_contract import (
    kill_switch_error,
    quota_exceeded_error,
    to_http_body,
    to_http_status,
)
from server_modules.agent_computer_policy_service import (
    AgentComputerPolicyError,
    build_default_agent_computer_policy,
    effective_agent_computer_policy,
    get_saved_agent_computer_policy,
    normalize_agent_computer_policy,
    upsert_agent_computer_policy,
    validate_agent_computer_policy as validate_agent_computer_policy_contract,
)
from server_modules.capability_risk_classifier_service import (
    DECISION_BLOCK,
    CapabilityRiskClassifierError,
    classify_gateway_browser_action_risk,
    classify_gateway_tool_risk,
)
from server_modules.runtime_common import require_api_key
from server_modules import (
    agent_computer_profile_service,
    cli_setup_service,
    dedicated_workstation_setup_service,
    execution_mode_policy,
    gateway_browser_service,
    gateway_doctor_service,
    gateway_execution_service,
    gateway_health_service,
    machine_capability_check,
    gateway_pairing_service,
    gateway_protocol_service,
    gateway_registry_service,
    gateway_restart_service,
    gateway_self_update_service,
    gateway_state_repository,
    hardware_activity_event_service,
    hardware_action_broker_service,
    kill_switch_gate,
    rust_runtime_kernel_client,
    safe_mode_service,
    security_audit_service,
    vps_provisioning_service,
)


LOGGER = logging.getLogger(__name__)


router = APIRouter()
GATEWAY_WS_SAFE_SUBPROTOCOL = "empyralis.gateway.v1"
GATEWAY_WS_TOKEN_SUBPROTOCOL_PREFIX = "empyralis.gateway.session."
_REPO_ROOT = Path(__file__).resolve().parents[1]
_AGENT_COMPUTER_INSTALLER_PATH = _REPO_ROOT / "scripts" / "install-agent-computer.sh"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _gateway_ws_query_token_allowed() -> bool:
    if str(os.getenv("EMPYRALIS_ALLOW_GATEWAY_WS_QUERY_TOKEN", "")).strip().lower() in {"1", "true", "yes", "on"}:
        return True
    environment = str(os.getenv("ORION_ENV") or os.getenv("ENV") or "").strip().lower()
    return environment in {"", "dev", "development", "local", "test", "testing"}


def _gateway_ws_session_token_from_subprotocol(websocket: WebSocket) -> tuple[str, Optional[str]]:
    raw = str(websocket.headers.get("sec-websocket-protocol") or "")
    protocols = [item.strip() for item in raw.split(",") if item.strip()]
    token = ""
    for item in protocols:
        if item.startswith(GATEWAY_WS_TOKEN_SUBPROTOCOL_PREFIX):
            token = item[len(GATEWAY_WS_TOKEN_SUBPROTOCOL_PREFIX) :].strip()
            break
    accept_subprotocol = GATEWAY_WS_SAFE_SUBPROTOCOL if GATEWAY_WS_SAFE_SUBPROTOCOL in protocols else None
    return token, accept_subprotocol


def _enforce_gateway_safety_gates(
    *,
    gateway_id: str,
    workspace_id: str,
    quota_profile: str,
    agent_id: str = "",
    tenant_id: str = "",
    capability_id: str = "",
) -> None:
    """Centralized safety enforcement for gateway endpoints.

    Checks kill switches and quotas before any gateway operation.
    Raises HTTPException with standardized error body on block.
    """
    try:
        assert_not_killed(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            gateway_id=gateway_id,
            agent_id=agent_id,
        )
    except KillSwitchBlockedError as exc:
        error = kill_switch_error(
            scope=exc.decision.scope,
            detail=exc.decision.detail,
            trace_id=exc.decision.trace_id,
        )
        raise HTTPException(
            status_code=to_http_status(error),
            detail=to_http_body(error),
        )

    if capability_id:
        disabled_state = safe_mode_service.resolve_capability_disable_state(
            capability_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            machine_id=gateway_id,
        )
        if bool(disabled_state.get("disabled")) and str(disabled_state.get("type") or "") == "kill_switch":
            error = kill_switch_error(
                scope=str(disabled_state.get("scope") or "workspace"),
                detail=str(disabled_state.get("reason") or "Gateway action blocked by kill switch."),
            )
            raise HTTPException(
                status_code=to_http_status(error),
                detail=to_http_body(error),
            )

    if quota_profile:
        decision = evaluate_gateway_quota(
            profile=quota_profile,
            gateway_id=gateway_id,
        )
        if not decision.allowed:
            error = quota_exceeded_error(
                profile=quota_profile,
                retry_after_seconds=decision.retry_after_seconds,
            )
            raise HTTPException(
                status_code=to_http_status(error),
                detail=to_http_body(error),
            )


def _latest_gateway_session_id(gateway_id: str) -> str:
    session = gateway_state_repository.get_latest_gateway_session(gateway_id, include_revoked=False)
    if not isinstance(session, dict):
        return ""
    status_value = str(session.get("status") or "").strip().lower()
    if status_value in {"expired", "revoked"}:
        return ""
    return str(session.get("session_id") or "").strip()


def _enforce_gateway_service_decision(
    *,
    operation: str,
    gateway_id: str,
    workspace_id: str,
    tenant_id: str,
    actor_id: str,
    quota_profile: str,
    capability_id: str = "",
    run_id: str = "",
    trace_id: str = "",
    request_id: str = "",
    browser_session_id: str = "",
    approval_provided: bool = False,
    approval_memory_hit: bool = False,
    risk_decision: str = "normal",
    cloud_fallback_enabled: bool = False,
    cloud_fallback_approved: bool = False,
) -> Dict[str, Any]:
    session_id = _latest_gateway_session_id(gateway_id) or str(request_id or "").strip() or gateway_id
    payload = {
        "operation": operation,
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "actor_id": actor_id,
        "gateway_id": gateway_id,
        "session_id": session_id,
        "request_id": request_id,
        "capability_id": capability_id,
        "run_id": run_id,
        "trace_id": trace_id,
        "quota_profile": quota_profile,
        "risk_level": risk_decision,
        "policy_decision": "allow",
        "approval_provided": approval_provided,
        "approval_memory_hit": approval_memory_hit,
        "kill_switch_enabled": False,
        "quota_ok": True,
        "gateway_registered": True,
        "session_valid": bool(session_id),
        "websocket_token_present": True,
        "protocol_version": GATEWAY_WS_SAFE_SUBPROTOCOL,
        "frame_valid": True,
        "payload_present": True,
        "browser_session_id": browser_session_id,
        "cloud_fallback_enabled": cloud_fallback_enabled,
        "cloud_fallback_approved": cloud_fallback_approved,
    }
    try:
        decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
            "gateway-service-decision",
            payload,
        )
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        detail = str(exc.reason or "rust_gateway_service_denied").strip()
        raise HTTPException(status_code=409, detail=f"Rust gateway-service gate blocked {operation}: {detail}")
    expected_next_action = {
        "tool_execute": "dispatch_gateway_operation",
        "tool_interrupt": "dispatch_gateway_operation",
        "browser_session": "dispatch_gateway_operation",
        "browser_action": "dispatch_gateway_operation",
        "browser_fallback": "dispatch_gateway_operation",
        "cloud_fallback": "dispatch_gateway_operation",
        "protocol_route": "dispatch_gateway_operation",
        "approval_request": "request_gateway_owner_approval",
        "approval_resolve": "persist_approval_decision",
        "health_check": "publish_gateway_health",
    }.get(operation, "allow_gateway_service_operation")
    next_action = str(decision.get("next_action") or "").strip()
    if next_action != expected_next_action:
        raise HTTPException(
            status_code=423,
            detail=(
                "Rust gateway-service gate returned unexpected next_action for "
                f"{operation}: {next_action or 'missing'}"
            ),
        )
    return decision


def _enforce_gateway_acp_turn_decision(
    *,
    workspace_id: str,
    request_id: str,
    message: str,
) -> Dict[str, Any]:
    payload = {
        "operation": "acp_turn",
        "workspace_id": str(workspace_id or "").strip() or "default",
        "tenant_id": "default",
        "request_id": str(request_id or "").strip() or None,
        "capability_id": "sage.chat",
        "actor_role": "owner",
        "api_key_valid": True,
        "message": str(message or "").strip(),
        "health_probe": False,
    }
    try:
        decision = rust_runtime_kernel_client.gateway_action_decision(**payload)
        decision = rust_runtime_kernel_client.enforce_kernel_decision(
            "gateway-action-decision",
            decision,
        )
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        detail = str(exc.reason or "rust_gateway_acp_turn_denied").strip()
        raise HTTPException(status_code=409, detail=f"Rust gateway-action gate blocked acp_turn: {detail}")
    next_action = str(decision.get("next_action") or "").strip()
    if next_action != "route_acp_turn":
        raise HTTPException(
            status_code=423,
            detail=(
                "Rust gateway-action gate returned unexpected next_action for "
                f"acp_turn: {next_action or 'missing'}"
            ),
        )
    return decision


def _enforce_gateway_diagnostics_export_decision(
    *,
    workspace_id: str,
    tenant_id: str,
    actor_role: str,
) -> Dict[str, Any]:
    payload = {
        "operation": "diagnostics_export",
        "workspace_id": str(workspace_id or "").strip() or "default",
        "tenant_id": str(tenant_id or "").strip() or "default",
        "capability_id": "diagnostics.export",
        "actor_role": str(actor_role or "").strip() or "viewer",
    }
    try:
        decision = rust_runtime_kernel_client.gateway_action_decision(**payload)
        decision = rust_runtime_kernel_client.enforce_kernel_decision(
            "gateway-action-decision",
            decision,
        )
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        detail = str(exc.reason or "rust_gateway_diagnostics_export_denied").strip()
        raise HTTPException(
            status_code=409,
            detail=f"Rust gateway-action gate blocked diagnostics_export: {detail}",
        )
    next_action = str(decision.get("next_action") or "").strip()
    if next_action != "export_diagnostics_bundle":
        raise HTTPException(
            status_code=423,
            detail=(
                "Rust gateway-action gate returned unexpected next_action for "
                f"diagnostics_export: {next_action or 'missing'}"
            ),
        )
    return decision


def _audit_approval_bypass(
    *,
    gateway_id: str,
    capability_id: str,
    workspace_id: str,
    tenant_id: str,
) -> None:
    try:
        security_audit_service.emit_security_audit_event(
            action="gateway.approval_bypassed",
            status="logged",
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            detail=f"Gateway action executed without interactive approval for capability {capability_id}.",
            metadata={
                "gateway_id": gateway_id,
                "capability_id": capability_id,
                "interactive_approvals": False,
            },
        )
    except Exception as exc:
        LOGGER.warning("Failed to emit gateway approval bypass audit for %s: %s", gateway_id, exc)


def _audit_gateway_governance_decision(
    *,
    ug_decision: Any,
    gateway_id: str,
    workspace_id: str,
    tenant_id: str,
    capability_id: str,
    explicit_full_access: bool = False,
) -> None:
    """Log the governance decision for audit — never block.

    With internalized governance, evaluate_action_policy runs for AUDIT ONLY.
    This logs the risk level, whether approval would have been required under
    the old model, and whether the gateway had explicit full access.
    """
    try:
        decision_str = getattr(ug_decision, 'decision', str(ug_decision))
        risk_level = ""
        if hasattr(ug_decision, 'risk_decision') and ug_decision.risk_decision:
            risk_level = str(
                getattr(ug_decision.risk_decision, 'risk_level',
                        ug_decision.risk_decision.get('risk_level', ''))
                if isinstance(ug_decision.risk_decision, dict)
                else getattr(ug_decision.risk_decision, 'risk_level', '')
            ).strip()
        security_audit_service.emit_security_audit_event(
            action="gateway.governance_audit",
            status="logged",
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            detail=(
                f"Governance audit for gateway {gateway_id} capability {capability_id}. "
                f"Decision: {decision_str}, risk: {risk_level or 'unclassified'}, "
                f"full_access: {explicit_full_access}. "
                f"Approval is internalized — execution proceeds without consumer-facing gate."
            ),
            metadata={
                "gateway_id": gateway_id,
                "capability_id": capability_id,
                "governance_decision": decision_str,
                "risk_level": risk_level,
                "explicit_full_access": explicit_full_access,
                "approval_internalized": True,
            },
        )
    except Exception as exc:
        LOGGER.warning("Failed to emit gateway governance audit for %s: %s", gateway_id, exc)


def _gateway_policy_from_registration(registration: Dict[str, Any]):
    metadata = registration.get("metadata") if isinstance(registration.get("metadata"), dict) else {}
    policy_payload = (
        metadata.get("agent_computer_policy")
        or metadata.get("computer_policy")
        or metadata.get("gateway_policy")
    )
    if isinstance(policy_payload, dict):
        return normalize_agent_computer_policy(policy_payload)
    policy_id = str(metadata.get("agent_computer_policy_id") or "").strip()
    workspace_id = str(registration.get("workspace_id") or "").strip()
    if policy_id and workspace_id:
        saved_policy = get_saved_agent_computer_policy(workspace_id=workspace_id, policy_id=policy_id)
        if saved_policy is not None:
            return saved_policy
    gateway_token = str(registration.get("gateway_id") or "").strip() or "default"
    runtime_access_mode = str(metadata.get("runtime_access_mode") or "").strip().lower()
    explicit_full_access = _sage_full_access_metadata(metadata)
    return build_default_agent_computer_policy(
        autonomy_mode="yolo" if explicit_full_access else "ask_every_time",
        policy_id=policy_id or f"gateway:{gateway_token}",
        filesystem_scope=("/",) if explicit_full_access else (),
    )


def _sage_full_access_metadata(metadata: Dict[str, Any]) -> bool:
    runtime_access_mode = str(metadata.get("runtime_access_mode") or "").strip().lower()
    agent_scope = str(metadata.get("agent_scope") or "").strip().lower()
    warning_acknowledged = bool(metadata.get("autonomous_agent_setup_warning_acknowledged"))
    return (
        runtime_access_mode == "full_access"
        and agent_scope == "sage"
        and warning_acknowledged
    )


def _registration_sage_full_access(registration: Dict[str, Any]) -> bool:
    metadata = registration.get("metadata") if isinstance(registration.get("metadata"), dict) else {}
    return _sage_full_access_metadata(metadata)


def _registration_requires_full_access_reconfirmation(registration: Dict[str, Any]) -> bool:
    metadata = registration.get("metadata") if isinstance(registration.get("metadata"), dict) else {}
    runtime_access_mode = execution_mode_policy.normalize_runtime_access_mode(
        metadata.get("runtime_access_mode")
    )
    return (
        runtime_access_mode == execution_mode_policy.FULL_RUNTIME_ACCESS_MODE
        and not _sage_full_access_metadata(metadata)
    )


def _raise_full_access_reconfirmation_required() -> None:
    raise HTTPException(
        status_code=409,
        detail={
            "error": "FULL_ACCESS_RECONFIRMATION_REQUIRED",
            "reason": "This Agent Computer must be re-confirmed through the current Full Access setup before full-access actions can run.",
        },
    )


def _emit_gateway_approval_memory_used(
    *,
    gateway_id: str,
    workspace_id: str,
    tenant_id: str,
    rule,
) -> None:
    pass  # Phase 4: approval memory system removed


def _consume_gateway_approval_memory(
    *,
    registration: Dict[str, Any],
    workspace_id: str,
    tenant_id: str,
    actor_user_id: str,
    policy_id: str,
    risk_decision,
    payload: Dict[str, Any],
    run_id: str = "",
    trace_id: str = "",
    request_id: str = "",
    browser_session_id: str = "",
):
    return None  # Phase 4: approval memory system removed


def _emit_gateway_risk_decision(
    *,
    gateway_id: str,
    workspace_id: str,
    tenant_id: str,
    risk_decision,
) -> None:
    # Accept both CapabilityRiskDecision (old, has .as_dict()/.decision)
    # and plain dict from unified gate's risk_decision field
    if isinstance(risk_decision, dict):
        payload = risk_decision
        decision_label = risk_decision.get("decision", "unknown")
    elif callable(getattr(risk_decision, "as_dict", None)):
        payload = risk_decision.as_dict()
        decision_label = str(getattr(risk_decision, "decision", "unknown") or "unknown")
    else:
        return  # unknown format, skip audit
    try:
        security_audit_service.emit_security_audit_event(
            action=f"gateway.risk_decision.{decision_label}",
            status="blocked" if risk_decision.decision == DECISION_BLOCK else "logged",
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            detail=f"Gateway risk decision {risk_decision.decision} for {risk_decision.capability}.",
            metadata={
                "gateway_id": gateway_id,
                "risk_decision": payload,
            },
        )
    except Exception as exc:
        LOGGER.warning("Failed to emit gateway risk decision audit for %s: %s", gateway_id, exc)
    try:
        gateway_state_repository.record_gateway_event(
            gateway_id=gateway_id,
            session_id=None,
            direction="server",
            frame_kind="audit",
            message_type=f"gateway.risk_decision.{risk_decision.decision}",
            payload={
                "workspace_id": workspace_id,
                "risk_decision": payload,
            },
        )
    except Exception as exc:
        LOGGER.warning("Failed to record gateway risk decision event for %s: %s", gateway_id, exc)


def _block_gateway_risk_decision(*, risk_decision) -> None:
    # Accept both CapabilityRiskDecision (old) and ActionPolicyDecision (unified gate)
    if hasattr(risk_decision, "blocked_reason"):
        reason = risk_decision.blocked_reason
    elif hasattr(risk_decision, "reason"):
        reason = risk_decision.reason
    else:
        reason = "Gateway action blocked by risk policy."
    reason = reason or "Gateway action blocked by risk policy."
    as_dict = risk_decision.as_dict() if callable(getattr(risk_decision, "as_dict", None)) else dict(risk_decision) if isinstance(risk_decision, dict) else {}
    raise HTTPException(
        status_code=403,
        detail={
            "error": "GATEWAY_RISK_BLOCKED",
            "reason": reason,
            "risk_decision": as_dict,
        },
    )


class GatewayPairingIntentCreateRequest(BaseModel):
    workspace_id: Optional[str] = None
    ttl_seconds: Optional[int] = Field(
        default=None,
        ge=gateway_pairing_service.MIN_GATEWAY_PAIRING_TTL_SECONDS,
        le=gateway_pairing_service.MAX_GATEWAY_PAIRING_TTL_SECONDS,
    )
    display_name: Optional[str] = None
    platform: Optional[str] = None
    runtime_access_mode: Optional[str] = None
    autonomous_agent_setup_warning_acknowledged: bool = False
    metadata: Dict[str, Any] = Field(default_factory=dict)


class GatewayRegistrationRequest(BaseModel):
    pairing_token: str = Field(min_length=1)
    device_id: str = Field(min_length=1)
    gateway_id: Optional[str] = None
    display_name: Optional[str] = None
    platform: Optional[str] = None
    capabilities: list[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class GatewayProvisioningEventRequest(BaseModel):
    """A status/failure beacon from a box that is still installing — see
    POST /gateway/provisioning-events. Authenticated by the pairing token the
    box was handed in cloud-init, exactly like POST /gateway/registrations.
    """

    pairing_token: str = Field(min_length=1)
    phase: str = Field(default="install", max_length=64)
    message: str = Field(default="", max_length=2000)
    terminal: bool = True


class GatewaySshPairingRequest(BaseModel):
    workspace_id: str = Field(min_length=1)
    pairing_token: Optional[str] = None
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=22, ge=1, le=65535)
    username: str = Field(min_length=1, max_length=128)
    auth_mode: str = Field(default="password")
    password: Optional[str] = None
    ssh_key: Optional[str] = None
    remote_root: Optional[str] = None
    runtime_access_mode: Optional[str] = None
    autonomous_agent_setup_warning_acknowledged: bool = False
    expected_host_fingerprint: Optional[str] = None


class HardwareVPSProvisionRequest(BaseModel):
    workspace_id: Optional[str] = None
    provider: str = Field(min_length=1, max_length=64)
    credentials: Dict[str, Any] = Field(default_factory=dict)
    token_id: Optional[str] = Field(default=None, max_length=192)
    region: Optional[str] = Field(default=None, max_length=64)
    size: Optional[str] = Field(default=None, max_length=128)
    runtime_access_mode: Optional[str] = None
    autonomous_agent_setup_warning_acknowledged: bool = False
    metadata: Dict[str, Any] = Field(default_factory=dict)


class HardwareVPSTokenRequest(BaseModel):
    workspace_id: Optional[str] = None
    provider: str = Field(min_length=1, max_length=64)
    credentials: Dict[str, Any] = Field(default_factory=dict)


class GoogleVPSProjectCreateRequest(BaseModel):
    workspace_id: Optional[str] = None
    setup_id: str = Field(min_length=1, max_length=192)
    project_name: str = Field(min_length=1, max_length=160)


class GoogleVPSBootstrapRequest(BaseModel):
    workspace_id: Optional[str] = None
    setup_id: str = Field(min_length=1, max_length=192)
    project_id: str = Field(min_length=1, max_length=64)


class HardwareVPSAwsConnectRequest(BaseModel):
    workspace_id: Optional[str] = None
    account_id: str = Field(min_length=1, max_length=32)


class HardwareVPSAwsConfirmRequest(BaseModel):
    workspace_id: Optional[str] = None
    connection_id: str = Field(min_length=1, max_length=192)


def _gateway_api_url_for_remote_setup() -> str:
    return (
        str(os.getenv("EMPYRALIS_GATEWAY_API_URL") or "").strip()
        or str(os.getenv("NEXT_PUBLIC_ORION_API_URL") or "").strip()
        or str(os.getenv("NEXT_PUBLIC_API_URL") or "").strip()
        or "http://127.0.0.1:8001"
    )


def _ssh_private_key_from_text(key_text: str) -> Any:
    try:
        import paramiko
    except Exception as exc:  # pragma: no cover - dependency checked by route.
        raise RuntimeError("SSH support is not installed.") from exc

    key_stream = io.StringIO(key_text)
    key_classes = (
        getattr(paramiko, "Ed25519Key", None),
        getattr(paramiko, "RSAKey", None),
        getattr(paramiko, "ECDSAKey", None),
        getattr(paramiko, "DSSKey", None),
    )
    last_error: Exception | None = None
    for key_class in key_classes:
        if key_class is None:
            continue
        key_stream.seek(0)
        try:
            return key_class.from_private_key(key_stream)
        except Exception as exc:
            last_error = exc
    raise ValueError("SSH key could not be parsed.") from last_error


def _remote_agent_computer_setup_command(
    *,
    workspace_id: str,
    pairing_token: str,
    display_name: str,
    remote_root: str,
) -> str:
    root = (remote_root or "").strip()
    if root:
        # Power-user path: the box already has an Empyralis checkout with the
        # gateway built. Kept for a collaborator connecting a dev machine.
        api_url = _gateway_api_url_for_remote_setup()
        return "\n".join(
            [
                "set -e",
                f"cd {shlex.quote(root)}",
                f"export EMPYRALIS_GATEWAY_API_URL={shlex.quote(api_url)}",
                f"export EMPYRALIS_GATEWAY_PAIRING_TOKEN={shlex.quote(pairing_token)}",
                f"export EMPYRALIS_GATEWAY_EXPECTED_WORKSPACE_ID={shlex.quote(workspace_id)}",
                f"export EMPYRALIS_GATEWAY_DISPLAY_NAME={shlex.quote(display_name)}",
                "scripts/agent_computer.sh stop || true",
                "scripts/agent_computer.sh service-install --system",
                "scripts/agent_computer.sh start",
            ]
        )
    # Default: a fresh Ubuntu box, nothing pre-installed. Download and run the
    # exact same prebuilt-gateway installer the cloud-provisioning path uses
    # (curl | ... sudo -E bash — see vps_provisioning_service.cloud_init_script)
    # so "connect your own server" works on any bare box, no checkout/build.
    installer_url = vps_provisioning_service.agent_installer_url()
    api_url = (
        vps_provisioning_service._ensure_api_path_suffix(vps_provisioning_service.PUBLIC_API_URL)
        or _gateway_api_url_for_remote_setup()
    )
    repo_token = vps_provisioning_service._installer_repo_token()
    repo_token_env = f" EMPYRALIS_REPO_TOKEN={shlex.quote(repo_token)}" if repo_token else ""
    return "\n".join(
        [
            "set -e",
            f"curl -fsSL {shlex.quote(installer_url)} | "
            f"EMPYRALIS_PAIRING_TOKEN={shlex.quote(pairing_token)} "
            f"EMPYRALIS_API_URL={shlex.quote(api_url)} "
            f"EMPYRALIS_GATEWAY_DISPLAY_NAME={shlex.quote(display_name)}"
            f"{repo_token_env} sudo -E bash",
        ]
    )


def _vps_oauth_hardware_redirect_url(
    request: Request,
    *,
    workspace_id: str,
    provider: str,
    result: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
    return_to: str = "",
) -> str:
    """Build the "come back where you started" URL the DigitalOcean/Google
    cloud-VPS OAuth callbacks redirect to. Mirrors routes_connections.py's
    _oauth_completion_url — the same same-tab redirect pattern every other
    OAuth connector already uses successfully (no popup, no
    window.opener/postMessage).

    The wizard (HardwareSection) renders at more than one URL — Settings,
    where it lives now, and the standalone /hardware route kept for deep
    links — so the destination is whatever `return_to` the /start request
    recorded (threaded through the OAuth state record, validated by
    vps_provisioning_service.normalize_oauth_return_path: same-origin
    absolute paths only). `/w/{workspace}/hardware` remains the fallback for
    every case where no origin was recorded: an older client that doesn't
    send return_to, or a state token so broken we never learned anything
    about the flow.

    Always returns a URL: when the workspace_id is unknown too (state was
    invalid/expired/missing before we ever learned it — e.g. the callback URL
    hit cold, with a garbage state param) the fallback path uses "default",
    the same convention create_digitalocean_oauth_start /
    create_google_oauth_start use when minting the state record in the first
    place, rather than stranding the user with nowhere to go.
    """
    clean_workspace_id = str(workspace_id or "").strip() or "default"
    query: Dict[str, str] = {"vps_oauth_provider": provider}
    if error:
        query["vps_oauth_error"] = str(error)
    else:
        query["vps_oauth"] = provider
        for key in ("token_id", "setup_id"):
            value = str((result or {}).get(key) or "").strip()
            if value:
                query[key] = value
    destination = vps_provisioning_service.normalize_oauth_return_path(
        return_to or (result or {}).get("return_to")
    ) or f"/w/{urlparse.quote(clean_workspace_id)}/hardware"
    # A recorded return_to may carry its own query string; merge rather than
    # clobber, with the OAuth result params winning on a key collision.
    split_destination = urlparse.urlsplit(destination)
    merged_query = dict(urlparse.parse_qsl(split_destination.query, keep_blank_values=True))
    merged_query.update(query)
    return (
        f"{_oauth_request_origin(request)}"
        f"{split_destination.path}?"
        f"{urlparse.urlencode(merged_query)}"
    )


def _run_remote_agent_computer_setup_via_ssh(
    *,
    host: str,
    port: int,
    username: str,
    auth_mode: str,
    password: Optional[str],
    ssh_key: Optional[str],
    expected_host_fingerprint: Optional[str],
    command: str,
) -> Dict[str, Any]:
    try:
        import paramiko
    except Exception as exc:
        raise RuntimeError("SSH support is not installed. Install requirements.txt, then retry.") from exc

    normalized_auth_mode = str(auth_mode or "password").strip().lower()
    client = paramiko.SSHClient()
    expected_fingerprint = _normalize_ssh_fingerprint(expected_host_fingerprint)
    if expected_fingerprint:
        host_key = _fetch_ssh_host_key(paramiko, host, int(port or 22))
        if expected_fingerprint not in _ssh_host_key_fingerprints(host_key):
            raise ValueError("SSH host fingerprint did not match the expected fingerprint.")
        host_keys = client.get_host_keys()
        host_keys.add(host, host_key.get_name(), host_key)
        if int(port or 22) != 22:
            host_keys.add(f"[{host}]:{int(port or 22)}", host_key.get_name(), host_key)
    else:
        client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    connect_kwargs: Dict[str, Any] = {
        "hostname": host,
        "port": int(port or 22),
        "username": username,
        "timeout": 12,
        "banner_timeout": 12,
        "auth_timeout": 12,
        "look_for_keys": False,
        "allow_agent": False,
    }
    if normalized_auth_mode == "ssh_key":
        if not str(ssh_key or "").strip():
            raise ValueError("SSH key is required.")
        connect_kwargs["pkey"] = _ssh_private_key_from_text(str(ssh_key or ""))
    else:
        if not str(password or "").strip():
            raise ValueError("Password is required.")
        connect_kwargs["password"] = password

    try:
        client.connect(**connect_kwargs)
        stdin, stdout, stderr = client.exec_command(command, timeout=180)
        del stdin
        exit_code = int(stdout.channel.recv_exit_status())
        stdout_text = stdout.read().decode("utf-8", errors="replace")[-2000:]
        stderr_text = stderr.read().decode("utf-8", errors="replace")[-2000:]
    finally:
        client.close()
    return {
        "exit_code": exit_code,
        "stdout_tail": stdout_text,
        "stderr_tail": stderr_text,
    }


def _normalize_ssh_fingerprint(value: Optional[str]) -> str:
    token = str(value or "").strip().lower()
    if not token:
        return ""
    if token.startswith("sha256:"):
        return "sha256:" + token.split(":", 1)[1].rstrip("=")
    return token.replace("-", ":")


def _ssh_host_key_fingerprints(host_key: Any) -> set[str]:
    import base64
    import hashlib

    md5_bytes = host_key.get_fingerprint()
    md5_colon = ":".join(f"{byte:02x}" for byte in md5_bytes)
    sha256 = base64.b64encode(hashlib.sha256(host_key.asbytes()).digest()).decode("ascii").rstrip("=")
    return {md5_colon.lower(), f"sha256:{sha256.lower()}"}


def _fetch_ssh_host_key(paramiko: Any, host: str, port: int) -> Any:
    transport = paramiko.Transport((host, int(port or 22)))
    try:
        transport.start_client(timeout=12)
        return transport.get_remote_server_key()
    finally:
        transport.close()


def _ssh_exception_detail(error: Exception) -> str:
    class_name = error.__class__.__name__.lower()
    message = str(error).strip()
    if "authentication" in class_name or "auth" in message.lower():
        return "SSH authentication failed. Check the username, password, or SSH key."
    if "timeout" in class_name or "timed out" in message.lower() or "timeout" in message.lower():
        return "SSH connection timed out. Check the host, port, firewall, and network reachability."
    if "no route" in message.lower() or "name or service not known" in message.lower() or "nodename" in message.lower():
        return "SSH host could not be reached. Check the server address."
    return f"Remote setup failed: {message}"


def _ssh_setup_failure_detail(result: Dict[str, Any]) -> str:
    output = f"{result.get('stdout_tail') or ''}\n{result.get('stderr_tail') or ''}".lower()
    if "no such file or directory" in output and "scripts/agent_computer.sh" in output:
        return "Remote setup failed because Agent Computer script was not found. Confirm the remote Empyralis checkout path."
    if "cd:" in output and "no such file or directory" in output:
        return "Remote setup failed because the remote Empyralis checkout path does not exist."
    if "sudo" in output and ("permission" in output or "password" in output or "not allowed" in output):
        return "Remote setup needs service-install permissions. Connect with a user that can install the Agent Computer service."
    if "permission denied" in output:
        return "Remote setup failed because the SSH user does not have permission to install or start Agent Computer."
    if "command not found" in output and ("node" in output or "npm" in output or "cargo" in output):
        return "Remote setup failed because required runtime tools are missing on the server."
    return "Remote setup failed. Confirm the remote Empyralis checkout path, runtime tools, and SSH permissions."


class GatewaySessionCreateRequest(BaseModel):
    gateway_id: str = Field(min_length=1)
    gateway_token: str = Field(min_length=1)
    session_ttl_seconds: Optional[int] = Field(default=None, ge=60, le=24 * 60 * 60)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class GatewayRegistrationRevokeRequest(BaseModel):
    reason: Optional[str] = None


class GatewayRegistrationRenameRequest(BaseModel):
    display_name: str = Field(..., min_length=1, max_length=80)


class DedicatedWorkstationBindRequest(BaseModel):
    workspace_id: Optional[str] = Field(default=None, min_length=1)
    policy_id: str = Field(min_length=1, max_length=160)
    machine_label: Optional[str] = Field(default=None, max_length=160)
    trace_id: Optional[str] = Field(default=None, max_length=200)


class DedicatedWorkstationControlRequest(BaseModel):
    workspace_id: Optional[str] = Field(default=None, min_length=1)
    reason: Optional[str] = Field(default=None, max_length=500)
    trace_id: Optional[str] = Field(default=None, max_length=200)


class AgentComputerPolicyRequest(BaseModel):
    workspace_id: str = Field(min_length=1)
    policy: Dict[str, Any] = Field(default_factory=dict)


class AgentComputerControlRequest(BaseModel):
    workspace_id: str = Field(min_length=1)
    reason: Optional[str] = Field(default=None, max_length=500)
    trace_id: Optional[str] = Field(default=None, max_length=200)


class GatewayToolExecuteRequest(BaseModel):
    capability_id: str = Field(min_length=1)
    arguments: Dict[str, Any] = Field(default_factory=dict)
    run_id: str = Field(min_length=1)
    trace_id: Optional[str] = None
    workspace_id: Optional[str] = None
    request_id: Optional[str] = None
    timeout_seconds: Optional[int] = Field(default=None, ge=1, le=120)
    interactive_approvals: bool = True


class GatewayToolInterruptRequest(BaseModel):
    run_id: str = Field(min_length=1)
    trace_id: Optional[str] = None
    workspace_id: Optional[str] = None
    target_request_id: Optional[str] = None
    reason: Optional[str] = None
    request_id: Optional[str] = None
    timeout_seconds: Optional[int] = Field(default=None, ge=1, le=120)


class CliInstallRequest(BaseModel):
    runtime: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    workspace_id: Optional[str] = None
    trace_id: Optional[str] = None
    request_id: Optional[str] = None


class GatewaySelfUpdateRequest(BaseModel):
    workspace_id: Optional[str] = None
    # Both optional: omitted means "resolve the latest published build
    # server-side" — see gateway_self_update_service.trigger_gateway_self_update.
    # Explicit values let an operator target a specific rollback/pinned build.
    target_version: Optional[str] = None
    artifact_url: Optional[str] = None


class GatewayRestartRequest(BaseModel):
    workspace_id: Optional[str] = None


class GatewayDoctorRunRequest(BaseModel):
    workspace_id: Optional[str] = None
    # False (default): report-only — every check still runs, nothing on the
    # box changes. True: each check's own conservative, idempotent repair is
    # attempted for anything not already passing, then re-validated. See
    # gateway_doctor_service.run_gateway_doctor's doc comment.
    repair: bool = False


class CliLoginStartRequest(BaseModel):
    runtime: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    workspace_id: Optional[str] = None
    trace_id: Optional[str] = None
    request_id: Optional[str] = None
    # BYO-brain multi-method (see cli_setup_service.LOGIN_METHODS). Absent
    # means "use the runtime's default method" (device_auth for Codex,
    # console for Claude Code) — preserving the legacy behavior for callers
    # that haven't been upgraded to the auth-method chooser UI.
    method: Optional[str] = None


class CliLoginInputRequest(BaseModel):
    # Legacy shape: `code` on its own. New shape: `value` + optional `kind`
    # (`code` | `api_key` | `access_token`). If `code` is present and `value`
    # isn't, the service treats it as `{kind: "code", value: code}`. Length
    # cap widened for API keys / access tokens.
    code: Optional[str] = Field(default=None, max_length=4096)
    value: Optional[str] = Field(default=None, max_length=4096)
    kind: Optional[str] = None
    workspace_id: Optional[str] = None
    trace_id: Optional[str] = None
    request_id: Optional[str] = None


class CliLoginCancelRequest(BaseModel):
    workspace_id: Optional[str] = None
    trace_id: Optional[str] = None
    request_id: Optional[str] = None
    reason: Optional[str] = Field(default=None, max_length=200)


_REMOTE_HARDWARE_CAPABILITY_PROBE = r'''
import json
import os
import subprocess
import sys
from pathlib import Path

def entry(status, source, detail=""):
    return {"status": status, "source": source, "detail": str(detail or "")}

def screen_recording():
    if sys.platform == "darwin":
        try:
            import Quartz
            probe = getattr(Quartz, "CGPreflightScreenCaptureAccess", None)
            if callable(probe):
                return entry("granted" if bool(probe()) else "denied", "direct_probe", "macOS screen recording permission probe.")
        except Exception as exc:
            return entry("unknown", "direct_probe", exc)
        return entry("unknown", "direct_probe", "macOS screen recording permission has not been confirmed yet.")
    if sys.platform.startswith("win"):
        return entry("granted", "os_default", "Windows desktop capture does not require a separate privacy grant for this local desktop flow.")
    return entry("unsupported", "platform_capabilities", "Screen recording permission probing is not implemented on this platform.")

def accessibility():
    if sys.platform == "darwin":
        try:
            result = subprocess.run(
                ["osascript", "-e", 'tell application "System Events" to get name of first process'],
                capture_output=True,
                text=True,
                timeout=3,
            )
            if result.returncode == 0 and result.stdout.strip():
                return entry("granted", "osascript_probe", "macOS accessibility permission probe.")
            error = (result.stderr or result.stdout or "").strip()
            if "-1743" in error or "not authorized" in error.lower() or "not allowed" in error.lower():
                return entry("denied", "osascript_probe", error)
            return entry("unknown", "osascript_probe", error)
        except Exception as exc:
            return entry("unknown", "osascript_probe", exc)
    if sys.platform.startswith("win"):
        return entry("granted", "os_default", "Windows desktop automation does not require a separate accessibility privacy grant for this local flow.")
    return entry("unsupported", "platform_capabilities", "Accessibility permission probing is not implemented on this platform.")

def filesystem():
    try:
        probe_dir = Path(os.environ.get("EMPYRALIS_STATE_HOME") or Path.home() / ".empyralis" / "state") / "runtime" / "desktop_setup_probe"
        probe_dir.mkdir(parents=True, exist_ok=True)
        probe_file = probe_dir / "write-test.tmp"
        probe_file.write_text("ok", encoding="utf-8")
        ok = probe_file.read_text(encoding="utf-8") == "ok"
        probe_file.unlink(missing_ok=True)
        return entry("granted" if ok else "denied", "local_probe", "Writable local state directory.")
    except Exception as exc:
        return entry("denied", "local_probe", exc)

print(json.dumps({
    "checks": [
        {"id": "screen_recording", "name": "screen_recording", **screen_recording()},
        {"id": "accessibility", "name": "accessibility", **accessibility()},
        {"id": "filesystem", "name": "filesystem", **filesystem()},
    ]
}))
'''.strip()


def _remote_hardware_probe_command() -> str:
    encoded = base64.b64encode(_REMOTE_HARDWARE_CAPABILITY_PROBE.encode("utf-8")).decode("ascii")
    return f"python3 -c 'import base64; exec(base64.b64decode(\"{encoded}\"))'"


def _default_hardware_capability_checks(status: str = "unknown") -> list[Dict[str, str]]:
    return [
        {"name": "screen_recording", "status": status},
        {"name": "accessibility", "status": status},
        {"name": "filesystem", "status": status},
    ]


def _normalize_hardware_capability_checks(payload: Dict[str, Any]) -> list[Dict[str, str]]:
    by_name: Dict[str, str] = {}
    for item in list(payload.get("checks") or []):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("id") or "").strip().lower()
        status_value = str(item.get("status") or "").strip().lower()
        if name in {"screen_recording", "accessibility", "filesystem"} and status_value in {"granted", "denied", "unknown", "unsupported"}:
            by_name[name] = status_value
    return [
        {"name": name, "status": by_name.get(name, "unknown")}
        for name in ("screen_recording", "accessibility", "filesystem")
    ]


def _active_workspace_gateway_registration(workspace_id: str) -> Optional[Dict[str, Any]]:
    active: list[Dict[str, Any]] = []
    for registration in gateway_state_repository.list_workspace_gateway_registrations(workspace_id):
        if not isinstance(registration, dict):
            continue
        if str(registration.get("status") or "").strip().lower() != "active":
            continue
        active.append(registration)
    if not active:
        return None
    online = [
        registration
        for registration in active
        if str(gateway_registry_service.gateway_registration_public_payload(registration).get("connection_status") or "").strip().lower() == "online"
    ]
    return (online or active)[0]


class GatewayApprovalResolveRequest(BaseModel):
    decision: str = Field(min_length=1)
    note: Optional[str] = None
    timeout_seconds: Optional[int] = Field(default=None, ge=1, le=120)
    remember_for_seconds: Optional[int] = Field(default=None, ge=60, le=24 * 60 * 60)
    remember_scope: Dict[str, Any] = Field(default_factory=dict)


class GatewayBrowserSessionStartRequest(BaseModel):
    url: Optional[str] = None
    session_profile: Optional[str] = None
    session_mode: Optional[str] = None
    attach_endpoint_url: Optional[str] = None
    interactive_actions: list[str] = Field(default_factory=list)
    reviewed_approval_required: bool = False
    allow_cloud_fallback: bool = False
    run_id: str = Field(min_length=1)
    trace_id: Optional[str] = None
    workspace_id: Optional[str] = None
    request_id: Optional[str] = None
    timeout_seconds: Optional[int] = Field(default=None, ge=1, le=120)
    interactive_approvals: bool = True


class GatewayBrowserActionRequest(BaseModel):
    action: str = Field(min_length=1)
    action_args: Dict[str, Any] = Field(default_factory=dict)
    reviewed_approval_required: Optional[bool] = None
    run_id: str = Field(min_length=1)
    trace_id: Optional[str] = None
    workspace_id: Optional[str] = None
    request_id: Optional[str] = None
    timeout_seconds: Optional[int] = Field(default=None, ge=1, le=120)
    interactive_approvals: bool = True
    allow_cloud_fallback: bool = False


class GatewayBrowserSessionControlRequest(BaseModel):
    run_id: str = Field(min_length=1)
    trace_id: Optional[str] = None
    workspace_id: Optional[str] = None
    request_id: Optional[str] = None
    timeout_seconds: Optional[int] = Field(default=None, ge=1, le=120)
    note: Optional[str] = None


def _accessible_gateway_registration(
    gateway_id: str,
    current_user,
    *,
    workspace_id: Optional[str] = None,
    minimum_role: str = "member",
) -> tuple[Dict[str, Any], str]:
    registration = gateway_state_repository.get_gateway_registration(gateway_id)
    if not registration:
        raise HTTPException(status_code=404, detail="Gateway registration was not found.")
    registration_workspace_id = str(registration.get("workspace_id") or "").strip() or "default"
    resolved_workspace_id = enforce_workspace_access(
        current_user,
        workspace_id or registration_workspace_id,
        minimum_role=minimum_role,
    )
    if resolved_workspace_id != registration_workspace_id:
        raise HTTPException(status_code=403, detail="Workspace is not accessible for this user.")
    return registration, resolved_workspace_id


def _policy_id_for_agent_computer(*, computer_id: str, profile: Optional[Any], registration: Optional[Dict[str, Any]]) -> str:
    if profile is not None and str(getattr(profile, "policy_id", "") or "").strip():
        return str(getattr(profile, "policy_id") or "").strip()
    metadata = registration.get("metadata") if isinstance((registration or {}).get("metadata"), dict) else {}
    configured = str(metadata.get("agent_computer_policy_id") or "").strip()
    if configured:
        return configured
    return f"agent-computer:{str(computer_id or '').strip()}"


def _runtime_access_mode_for_agent_computer(*, profile: Optional[Any], registration: Optional[Dict[str, Any]]) -> str:
    metadata = registration.get("metadata") if isinstance((registration or {}).get("metadata"), dict) else {}
    return str(metadata.get("runtime_access_mode") or "").strip().lower() or "default_guarded"


def _accessible_agent_computer(
    computer_id: str,
    current_user,
    *,
    workspace_id: str,
    minimum_role: str = "owner",
) -> tuple[str, Optional[Any], Optional[Dict[str, Any]], str]:
    resolved_workspace_id = enforce_workspace_access(
        current_user,
        workspace_id,
        minimum_role=minimum_role,
    )
    profile = agent_computer_profile_service.get_agent_computer_profile(
        workspace_id=resolved_workspace_id,
        profile_id=computer_id,
    )
    registration = None
    if profile is not None and str(getattr(profile, "gateway_id", "") or "").strip():
        registration = gateway_state_repository.get_gateway_registration(str(getattr(profile, "gateway_id") or "").strip())
        if registration and str(registration.get("workspace_id") or "").strip() != resolved_workspace_id:
            raise HTTPException(status_code=403, detail="Agent Computer registration is not accessible for this workspace.")
    if profile is None:
        registration = gateway_state_repository.get_gateway_registration(computer_id)
        if not registration:
            raise HTTPException(status_code=404, detail="Agent Computer was not found.")
        registration_workspace_id = str(registration.get("workspace_id") or "").strip() or "default"
        if registration_workspace_id != resolved_workspace_id:
            raise HTTPException(status_code=403, detail="Agent Computer is not accessible for this workspace.")
    policy_id = _policy_id_for_agent_computer(computer_id=computer_id, profile=profile, registration=registration)
    return resolved_workspace_id, profile, registration, policy_id


def _agent_computer_policy_response(
    *,
    computer_id: str,
    workspace_id: str,
    policy_id: str,
    profile: Optional[Any],
    registration: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    runtime_access_mode = _runtime_access_mode_for_agent_computer(profile=profile, registration=registration)
    policy, saved = effective_agent_computer_policy(
        workspace_id=workspace_id,
        policy_id=policy_id,
        runtime_access_mode=runtime_access_mode,
    )
    gateway_id = str((registration or {}).get("gateway_id") or getattr(profile, "gateway_id", "") or "").strip()
    kill_decision = kill_switch_gate.evaluate_kill_switch(
        workspace_id=workspace_id,
        gateway_id=gateway_id,
    )
    return {
        "computer_id": str(computer_id or "").strip(),
        "workspace_id": workspace_id,
        "policy_id": policy_id,
        "saved": bool(saved),
        "runtime_access_mode": runtime_access_mode,
        "custom_policy_ready": bool(saved),
        "policy": policy.as_dict(),
        "effective_policy": policy.as_dict(),
        "computer": profile.as_dict() if profile is not None else None,
        "gateway_id": gateway_id or None,
        "emergency_stop": {
            "active": bool(kill_decision.blocked and kill_decision.scope == "gateway"),
            "reason": kill_decision.reason or None,
            "detail": kill_decision.detail or None,
        },
        "safety_summary": {
            "agent_computer_public_name": "Agent Computer",
            "full_access_requires_literal_mode": runtime_access_mode == "full_access",
            "custom_without_saved_policy_is_guarded": not saved and runtime_access_mode == "custom",
        },
    }


def _persist_agent_computer_policy_binding(
    *,
    computer_id: str,
    workspace_id: str,
    policy_id: str,
    profile: Optional[Any],
    registration: Optional[Dict[str, Any]],
) -> tuple[Optional[Any], Optional[Dict[str, Any]]]:
    if profile is not None:
        payload = profile.as_dict()
        payload["policy_id"] = policy_id
        profile = agent_computer_profile_service.upsert_agent_computer_profile(payload)
    if registration is not None:
        registration = gateway_state_repository.update_gateway_registration_state(
            gateway_id=str(registration.get("gateway_id") or computer_id).strip(),
            metadata={
                "agent_computer_policy_id": policy_id,
                "agent_computer_policy_saved_at": _utc_now_iso(),
            },
        ) or registration
    return profile, registration


async def _shutdown_revoked_live_gateway_connection(gateway_id: str, *, reason: str) -> bool:
    get_connection = getattr(gateway_protocol_service, "_get_live_connection", None)
    unregister_connection = getattr(gateway_protocol_service, "_unregister_live_connection", None)
    if not callable(get_connection):
        return False
    connection = get_connection(gateway_id)
    if connection is None:
        return False
    session_id = str(getattr(connection, "session_id", "") or "").strip()
    websocket = getattr(connection, "websocket", None)
    if websocket is not None and hasattr(websocket, "close"):
        try:
            await websocket.close(code=4403, reason=reason)
        except Exception:
            pass
    if callable(unregister_connection):
        unregister_connection(gateway_id=gateway_id, session_id=session_id, reason=reason)
    return True


@router.get("/agent-computers/{computer_id}/policy")
async def get_agent_computer_policy(
    computer_id: str,
    workspace_id: str = Query(..., min_length=1),
    current_user=Depends(require_api_key),
):
    resolved_workspace_id, profile, registration, policy_id = _accessible_agent_computer(
        computer_id,
        current_user,
        workspace_id=workspace_id,
        minimum_role="owner",
    )
    _enforce_gateway_service_decision(
        operation="gateway_policy_read",
        gateway_id=str((registration or {}).get("gateway_id") or computer_id).strip(),
        workspace_id=resolved_workspace_id,
        tenant_id=workspace_tenant_id(current_user, resolved_workspace_id),
        actor_id=str((current_user or {}).get("user_id") or "").strip() or "user",
        quota_profile=GATEWAY_WS_CONNECTION,
        capability_id="agent_computer.policy.read",
        run_id="gateway-policy-read",
        trace_id=f"gateway-policy-read:{computer_id}",
        request_id=f"gateway-policy-read:{computer_id}",
        approval_provided=True,
        approval_memory_hit=False,
        risk_decision="normal",
    )
    return _agent_computer_policy_response(
        computer_id=computer_id,
        workspace_id=resolved_workspace_id,
        policy_id=policy_id,
        profile=profile,
        registration=registration,
    )


@router.post("/agent-computers/{computer_id}/policy/validate")
async def validate_agent_computer_policy_route(
    computer_id: str,
    body: AgentComputerPolicyRequest,
    request: Request,
    current_user=Depends(require_api_key),
):
    validate_csrf(request)
    resolved_workspace_id, profile, registration, policy_id = _accessible_agent_computer(
        computer_id,
        current_user,
        workspace_id=body.workspace_id,
        minimum_role="owner",
    )
    _enforce_gateway_service_decision(
        operation="gateway_policy_write",
        gateway_id=str((registration or {}).get("gateway_id") or computer_id).strip(),
        workspace_id=resolved_workspace_id,
        tenant_id=workspace_tenant_id(current_user, resolved_workspace_id),
        actor_id=str((current_user or {}).get("user_id") or "").strip() or "user",
        quota_profile=GATEWAY_WS_CONNECTION,
        capability_id="agent_computer.policy.validate",
        run_id="gateway-policy-validate",
        trace_id=f"gateway-policy-validate:{computer_id}",
        request_id=f"gateway-policy-validate:{computer_id}",
        approval_provided=True,
        approval_memory_hit=False,
        risk_decision="normal",
    )
    candidate = dict(body.policy or {})
    candidate["policy_id"] = str(candidate.get("policy_id") or policy_id).strip() or policy_id
    try:
        policy = validate_agent_computer_policy_contract(candidate)
    except AgentComputerPolicyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        **_agent_computer_policy_response(
            computer_id=computer_id,
            workspace_id=resolved_workspace_id,
            policy_id=policy.policy_id,
            profile=profile,
            registration=registration,
        ),
        "valid": True,
        "policy": policy.as_dict(),
        "effective_policy": policy.as_dict(),
    }


@router.put("/agent-computers/{computer_id}/policy")
async def update_agent_computer_policy(
    computer_id: str,
    body: AgentComputerPolicyRequest,
    request: Request,
    current_user=Depends(require_api_key),
):
    validate_csrf(request)
    resolved_workspace_id, profile, registration, policy_id = _accessible_agent_computer(
        computer_id,
        current_user,
        workspace_id=body.workspace_id,
        minimum_role="owner",
    )
    _enforce_gateway_service_decision(
        operation="gateway_policy_write",
        gateway_id=str((registration or {}).get("gateway_id") or computer_id).strip(),
        workspace_id=resolved_workspace_id,
        tenant_id=workspace_tenant_id(current_user, resolved_workspace_id),
        actor_id=str((current_user or {}).get("user_id") or "").strip() or "user",
        quota_profile=GATEWAY_WS_CONNECTION,
        capability_id="agent_computer.policy.write",
        run_id="gateway-policy-write",
        trace_id=f"gateway-policy-write:{computer_id}",
        request_id=f"gateway-policy-write:{computer_id}",
        approval_provided=True,
        approval_memory_hit=False,
        risk_decision="normal",
    )
    candidate = dict(body.policy or {})
    candidate["policy_id"] = str(candidate.get("policy_id") or policy_id).strip() or policy_id
    try:
        policy = upsert_agent_computer_policy(
            workspace_id=resolved_workspace_id,
            policy=candidate,
        )
        profile, registration = _persist_agent_computer_policy_binding(
            computer_id=computer_id,
            workspace_id=resolved_workspace_id,
            policy_id=policy.policy_id,
            profile=profile,
            registration=registration,
        )
    except AgentComputerPolicyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _agent_computer_policy_response(
        computer_id=computer_id,
        workspace_id=resolved_workspace_id,
        policy_id=policy.policy_id,
        profile=profile,
        registration=registration,
    )


@router.post("/agent-computers/{computer_id}/emergency-stop")
async def emergency_stop_agent_computer(
    computer_id: str,
    body: AgentComputerControlRequest,
    request: Request,
    current_user=Depends(require_api_key),
):
    validate_csrf(request)
    resolved_workspace_id, profile, registration, policy_id = _accessible_agent_computer(
        computer_id,
        current_user,
        workspace_id=body.workspace_id,
        minimum_role="owner",
    )
    gateway_id = str((registration or {}).get("gateway_id") or getattr(profile, "gateway_id", "") or "").strip()
    if not gateway_id:
        raise HTTPException(status_code=400, detail="Agent Computer emergency stop requires a bound computer connection.")
    reason = str(body.reason or "").strip() or "Agent Computer emergency stop."
    kill_switch_gate.set_kill_switch(f"{kill_switch_gate.GATEWAY_KILL_PREFIX}{gateway_id}")
    latest_session = gateway_state_repository.get_latest_gateway_session(gateway_id, include_revoked=False)
    if latest_session:
        gateway_state_repository.mark_gateway_session_disconnected(
            str(latest_session.get("session_id") or "").strip(),
            reason=reason,
        )
    registration = gateway_state_repository.update_gateway_registration_state(
        gateway_id=gateway_id,
        metadata={
            "agent_computer_emergency_stop": {
                "active": True,
                "reason": reason,
                "stopped_by_user_id": str((current_user or {}).get("user_id") or "").strip() or None,
            }
        },
    ) or registration
    await _shutdown_revoked_live_gateway_connection(gateway_id, reason="agent computer emergency stop")
    return _agent_computer_policy_response(
        computer_id=computer_id,
        workspace_id=resolved_workspace_id,
        policy_id=policy_id,
        profile=profile,
        registration=registration,
    )


@router.post("/agent-computers/{computer_id}/clear-emergency-stop")
async def clear_agent_computer_emergency_stop(
    computer_id: str,
    body: AgentComputerControlRequest,
    request: Request,
    current_user=Depends(require_api_key),
):
    validate_csrf(request)
    resolved_workspace_id, profile, registration, policy_id = _accessible_agent_computer(
        computer_id,
        current_user,
        workspace_id=body.workspace_id,
        minimum_role="owner",
    )
    gateway_id = str((registration or {}).get("gateway_id") or getattr(profile, "gateway_id", "") or "").strip()
    if not gateway_id:
        raise HTTPException(status_code=400, detail="Agent Computer emergency stop requires a bound computer connection.")
    kill_switch_gate.clear_kill_switch(f"{kill_switch_gate.GATEWAY_KILL_PREFIX}{gateway_id}")
    registration = gateway_state_repository.update_gateway_registration_state(
        gateway_id=gateway_id,
        metadata={
            "agent_computer_emergency_stop": {
                "active": False,
                "cleared_by_user_id": str((current_user or {}).get("user_id") or "").strip() or None,
            }
        },
    ) or registration
    return _agent_computer_policy_response(
        computer_id=computer_id,
        workspace_id=resolved_workspace_id,
        policy_id=policy_id,
        profile=profile,
        registration=registration,
    )


@router.post("/gateway/pairings/intents")
async def create_gateway_pairing_intent(
    body: Optional[GatewayPairingIntentCreateRequest] = None,
    current_user=Depends(require_api_key),
):
    payload = body or GatewayPairingIntentCreateRequest()
    workspace_id = enforce_workspace_access(
        current_user,
        payload.workspace_id or "default",
        minimum_role="owner",
    )
    tenant_id = workspace_tenant_id(current_user, workspace_id)
    metadata = dict(payload.metadata or {})
    metadata.setdefault("agent_scope", "sage")
    try:
        pairing = gateway_pairing_service.create_gateway_pairing_intent(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=str((current_user or {}).get("user_id") or "").strip() or "unknown-user",
            ttl_seconds=payload.ttl_seconds,
            display_name=payload.display_name,
            platform=payload.platform,
            metadata=metadata,
            runtime_access_mode=payload.runtime_access_mode,
            autonomous_agent_setup_warning_acknowledged=payload.autonomous_agent_setup_warning_acknowledged,
        )
    except ValueError as exc:
        detail = str(exc)
        status_code = 429 if "too many pending gateway pairing requests" in detail.lower() else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc
    return pairing


@router.post("/gateway/pairings/ssh")
async def create_gateway_ssh_pairing(
    body: GatewaySshPairingRequest,
    current_user=Depends(require_api_key),
):
    workspace_id = enforce_workspace_access(
        current_user,
        body.workspace_id,
        minimum_role="owner",
    )
    tenant_id = workspace_tenant_id(current_user, workspace_id)
    pairing_token = str(body.pairing_token or "").strip()
    pairing: Dict[str, Any] | None = None
    if not pairing_token:
        try:
            requested_mode = execution_mode_policy.normalize_runtime_access_mode(body.runtime_access_mode)
            warning_acknowledged = bool(body.autonomous_agent_setup_warning_acknowledged)
            pairing = gateway_pairing_service.create_gateway_pairing_intent(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                user_id=str((current_user or {}).get("user_id") or "").strip() or "unknown-user",
                ttl_seconds=None,
                display_name=f"{body.username}@{body.host}",
                platform="linux",
                metadata={
                    "setup_source": "ssh",
                    "host": body.host,
                    "remote_root": body.remote_root or "~/Multi_Agent_Orchestrator_Project",
                    "agent_scope": "sage",
                },
                runtime_access_mode=requested_mode,
                autonomous_agent_setup_warning_acknowledged=warning_acknowledged,
            )
            pairing_token = str(pairing.get("pairing_token") or "").strip()
        except ValueError as exc:
            detail = str(exc)
            status_code = 429 if "too many pending gateway pairing requests" in detail.lower() else 400
            raise HTTPException(status_code=status_code, detail=detail) from exc
    if not pairing_token:
        raise HTTPException(status_code=400, detail="pairing_token is required.")

    command = _remote_agent_computer_setup_command(
        workspace_id=workspace_id,
        pairing_token=pairing_token,
        display_name=f"{body.username}@{body.host}",
        # Empty by default -> fresh-box installer path. Only a user who typed a
        # checkout path gets the legacy "cd + agent_computer.sh" behavior.
        remote_root=(body.remote_root or "").strip(),
    )
    try:
        result = _run_remote_agent_computer_setup_via_ssh(
            host=str(body.host).strip(),
            port=int(body.port or 22),
            username=str(body.username).strip(),
            auth_mode=body.auth_mode,
            password=body.password,
            ssh_key=body.ssh_key,
            expected_host_fingerprint=body.expected_host_fingerprint,
            command=command,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=_ssh_exception_detail(exc)) from exc
    if int(result.get("exit_code") or 0) != 0:
        raise HTTPException(
            status_code=502,
            detail=_ssh_setup_failure_detail(result),
        )
    return {
        "ok": True,
        "status": "setup_completed",
        "workspace_id": workspace_id,
        "host": body.host,
        "port": int(body.port or 22),
        "username": body.username,
        "pairing": {
            "created": pairing is not None,
            "expires_at": pairing.get("expires_at") if isinstance(pairing, dict) else None,
        },
    }


@router.get("/hardware/bootstrap/install.sh", response_class=PlainTextResponse)
async def get_agent_computer_bootstrap_installer() -> PlainTextResponse:
    if not _AGENT_COMPUTER_INSTALLER_PATH.exists():
        raise HTTPException(status_code=404, detail="Agent Computer installer is not available")
    return PlainTextResponse(
        _AGENT_COMPUTER_INSTALLER_PATH.read_text(encoding="utf-8"),
        media_type="text/plain",
    )


@router.get("/hardware/vps/oauth/digitalocean/start")
async def start_digitalocean_vps_oauth(
    workspace_id: Optional[str] = None,
    # The in-app path the wizard is being opened from, so the callback can put
    # the user back there (Settings and /hardware both render it). Optional:
    # an older client that omits it still lands on the Hardware fallback, and
    # anything not a same-origin absolute path is dropped by
    # normalize_oauth_return_path rather than trusted.
    return_to: Optional[str] = None,
    current_user=Depends(require_api_key),
):
    workspace = enforce_workspace_access(
        current_user,
        workspace_id or "default",
        minimum_role="owner",
    )
    tenant_id = workspace_tenant_id(current_user, workspace)
    user_id = str((current_user or {}).get("user_id") or "").strip() or "unknown-user"
    try:
        return vps_provisioning_service.create_digitalocean_oauth_start(
            workspace_id=workspace,
            tenant_id=tenant_id,
            user_id=user_id,
            return_to=str(return_to or ""),
        )
    except vps_provisioning_service.VPSProvisioningError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/hardware/vps/oauth/digitalocean/callback")
async def complete_digitalocean_vps_oauth(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    error_description: Optional[str] = None,
):
    # Same-tab redirect only — no popup, no window.opener/postMessage (see
    # startDigitalOceanOAuth in cloud-vps-setup-panel.tsx). Every branch
    # below, success or failure, ends in a 303 back to the Hardware page;
    # this route never renders HTML or 400s the browser directly, so a
    # duplicate/replayed hit (retry, prefetch, or the old popup +
    # same-tab-fallback double-fire this replaced) always lands the user
    # somewhere sane instead of a blank dead end.
    if error:
        message = str(error_description or error or "DigitalOcean authorization was cancelled.")
        # State is still in the file at this point (only popped on the
        # success path below) — peek it for workspace_id so the redirect
        # below can still send the user back to the right workspace's
        # Hardware page instead of dead-ending on an error page.
        workspace_id = vps_provisioning_service.peek_oauth_state_workspace_id(str(state or ""))
        redirect_url = _vps_oauth_hardware_redirect_url(
            request,
            workspace_id=workspace_id,
            provider="digitalocean",
            error=message,
            return_to=vps_provisioning_service.peek_oauth_state_return_to(str(state or "")),
        )
        return RedirectResponse(redirect_url, status_code=303)
    try:
        result = vps_provisioning_service.complete_digitalocean_oauth_callback(
            code=str(code or ""),
            state=str(state or ""),
        )
    except vps_provisioning_service.VPSProvisioningError as exc:
        # exc.workspace_id is populated when the failure happened after the
        # state record was popped (e.g. a bad client secret during token
        # exchange, or a cached failure replayed for a duplicate hit — see
        # complete_digitalocean_oauth_callback); the state peek is the
        # fallback for failures raised before that (missing/invalid/expired
        # state with nothing cached either, where there's nothing to peek).
        workspace_id = getattr(exc, "workspace_id", "") or vps_provisioning_service.peek_oauth_state_workspace_id(
            str(state or "")
        )
        redirect_url = _vps_oauth_hardware_redirect_url(
            request,
            workspace_id=workspace_id,
            provider="digitalocean",
            error=str(exc),
            # Same fallback chain as workspace_id above: the popped state
            # record's value (reattached to the exception), else a peek for
            # failures raised before the pop.
            return_to=getattr(exc, "return_to", "")
            or vps_provisioning_service.peek_oauth_state_return_to(str(state or "")),
        )
        return RedirectResponse(redirect_url, status_code=303)
    redirect_url = _vps_oauth_hardware_redirect_url(
        request, workspace_id=str(result.get("workspace_id") or ""), provider="digitalocean", result=result,
    )
    return RedirectResponse(redirect_url, status_code=303)


@router.get("/hardware/vps/oauth/google/start")
async def start_google_vps_oauth(
    workspace_id: Optional[str] = None,
    # See start_digitalocean_vps_oauth.
    return_to: Optional[str] = None,
    current_user=Depends(require_api_key),
):
    workspace = enforce_workspace_access(
        current_user,
        workspace_id or "default",
        minimum_role="owner",
    )
    tenant_id = workspace_tenant_id(current_user, workspace)
    user_id = str((current_user or {}).get("user_id") or "").strip() or "unknown-user"
    try:
        return vps_provisioning_service.create_google_oauth_start(
            workspace_id=workspace,
            tenant_id=tenant_id,
            user_id=user_id,
            return_to=str(return_to or ""),
        )
    except vps_provisioning_service.VPSProvisioningError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/hardware/vps/oauth/google/callback")
async def complete_google_vps_oauth(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    error_description: Optional[str] = None,
):
    # Same-tab redirect only — see the matching comment on
    # complete_digitalocean_vps_oauth above.
    if error:
        message = str(error_description or error or "Google authorization was cancelled.")
        workspace_id = vps_provisioning_service.peek_oauth_state_workspace_id(str(state or ""))
        redirect_url = _vps_oauth_hardware_redirect_url(
            request,
            workspace_id=workspace_id,
            provider="google",
            error=message,
            return_to=vps_provisioning_service.peek_oauth_state_return_to(str(state or "")),
        )
        return RedirectResponse(redirect_url, status_code=303)
    try:
        result = vps_provisioning_service.complete_google_oauth_callback(
            code=str(code or ""),
            state=str(state or ""),
        )
    except vps_provisioning_service.VPSProvisioningError as exc:
        workspace_id = getattr(exc, "workspace_id", "") or vps_provisioning_service.peek_oauth_state_workspace_id(
            str(state or "")
        )
        redirect_url = _vps_oauth_hardware_redirect_url(
            request,
            workspace_id=workspace_id,
            provider="google",
            error=str(exc),
            # See the matching branch in complete_digitalocean_vps_oauth.
            return_to=getattr(exc, "return_to", "")
            or vps_provisioning_service.peek_oauth_state_return_to(str(state or "")),
        )
        return RedirectResponse(redirect_url, status_code=303)
    redirect_url = _vps_oauth_hardware_redirect_url(
        request, workspace_id=str(result.get("workspace_id") or ""), provider="google", result=result,
    )
    return RedirectResponse(redirect_url, status_code=303)


@router.get("/hardware/vps/google/projects")
async def list_google_vps_projects(
    setup_id: str = Query(..., min_length=1),
    workspace_id: Optional[str] = None,
    current_user=Depends(require_api_key),
):
    workspace = enforce_workspace_access(
        current_user,
        workspace_id or "default",
        minimum_role="owner",
    )
    user_id = str((current_user or {}).get("user_id") or "").strip() or None
    try:
        return vps_provisioning_service.list_google_projects(setup_id, workspace_id=workspace, user_id=user_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Google sign-in session was not found or has expired.") from exc
    except vps_provisioning_service.VPSProvisioningError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/hardware/vps/google/projects")
async def create_google_vps_project(
    body: GoogleVPSProjectCreateRequest,
    current_user=Depends(require_api_key),
):
    workspace = enforce_workspace_access(
        current_user,
        body.workspace_id or "default",
        minimum_role="owner",
    )
    user_id = str((current_user or {}).get("user_id") or "").strip() or None
    try:
        return vps_provisioning_service.create_google_project(
            body.setup_id,
            body.project_name,
            workspace_id=workspace,
            user_id=user_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Google sign-in session was not found or has expired.") from exc
    except vps_provisioning_service.VPSProvisioningError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/hardware/vps/google/projects/{project_id}/billing")
async def get_google_vps_project_billing(
    project_id: str,
    setup_id: str = Query(..., min_length=1),
    workspace_id: Optional[str] = None,
    current_user=Depends(require_api_key),
):
    workspace = enforce_workspace_access(
        current_user,
        workspace_id or "default",
        minimum_role="owner",
    )
    user_id = str((current_user or {}).get("user_id") or "").strip() or None
    try:
        return vps_provisioning_service.check_google_project_billing(
            setup_id,
            project_id,
            workspace_id=workspace,
            user_id=user_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Google sign-in session was not found or has expired.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except vps_provisioning_service.VPSProvisioningError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/hardware/vps/google/bootstrap")
async def bootstrap_google_vps_project(
    body: GoogleVPSBootstrapRequest,
    current_user=Depends(require_api_key),
):
    workspace_id = enforce_workspace_access(
        current_user,
        body.workspace_id or "default",
        minimum_role="owner",
    )
    tenant_id = workspace_tenant_id(current_user, workspace_id)
    user_id = str((current_user or {}).get("user_id") or "").strip() or "unknown-user"
    try:
        return vps_provisioning_service.finish_google_bootstrap(
            body.setup_id,
            body.project_id,
            workspace_id=workspace_id,
            tenant_id=tenant_id,
            user_id=user_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Google sign-in session was not found or has expired.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except vps_provisioning_service.VPSProvisioningError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/hardware/vps/tokens")
async def create_hardware_vps_token(
    body: HardwareVPSTokenRequest,
    current_user=Depends(require_api_key),
):
    workspace_id = enforce_workspace_access(
        current_user,
        body.workspace_id or "default",
        minimum_role="owner",
    )
    tenant_id = workspace_tenant_id(current_user, workspace_id)
    user_id = str((current_user or {}).get("user_id") or "").strip() or "unknown-user"
    try:
        token_id = vps_provisioning_service.store_vps_provider_token(
            provider=body.provider,
            workspace_id=workspace_id,
            tenant_id=tenant_id,
            user_id=user_id,
            credentials=body.credentials,
            source="api_token",
        )
        provider_id = vps_provisioning_service.resolve_provider_options(body.provider, None, None)["provider"]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"provider": provider_id, "token_id": token_id}


@router.post("/hardware/vps/aws/connect")
async def start_aws_vps_connect(
    body: HardwareVPSAwsConnectRequest,
    current_user=Depends(require_api_key),
):
    """Step 1 of the AWS cross-account IAM role flow (no API key paste, no
    OAuth — see vps_provisioning_service's "AWS cross-account IAM role"
    section): the customer types their 12-digit AWS account id, and this
    hands back a pre-filled CloudFormation Quick-Create-Stack URL plus the
    role ARN it derives from the fixed-role-name convention. Nothing is
    trusted yet — see /hardware/vps/aws/confirm for the step that actually
    proves the role exists."""
    workspace_id = enforce_workspace_access(
        current_user,
        body.workspace_id or "default",
        minimum_role="owner",
    )
    tenant_id = workspace_tenant_id(current_user, workspace_id)
    user_id = str((current_user or {}).get("user_id") or "").strip() or "unknown-user"
    try:
        return vps_provisioning_service.create_aws_connect_intent(
            workspace_id=workspace_id,
            tenant_id=tenant_id,
            user_id=user_id,
            account_id=body.account_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except vps_provisioning_service.VPSProvisioningError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/hardware/vps/aws/confirm")
async def confirm_aws_vps_connect(
    body: HardwareVPSAwsConfirmRequest,
    current_user=Depends(require_api_key),
):
    """Step 2: after the customer has (supposedly) run the CloudFormation
    stack, attempt sts:AssumeRole against the role ARN + ExternalId from the
    matching /hardware/vps/aws/connect call. Success stores a token_id the
    rest of the VPS routes treat exactly like a DigitalOcean/Hetzner/Vultr
    one; failure (stack not finished yet, wrong account, etc.) is safely
    retryable — the connection_id stays valid until it expires."""
    workspace_id = enforce_workspace_access(
        current_user,
        body.workspace_id or "default",
        minimum_role="owner",
    )
    tenant_id = workspace_tenant_id(current_user, workspace_id)
    user_id = str((current_user or {}).get("user_id") or "").strip() or "unknown-user"
    try:
        return vps_provisioning_service.confirm_aws_connection(
            connection_id=body.connection_id,
            workspace_id=workspace_id,
            tenant_id=tenant_id,
            user_id=user_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="AWS connection request was not found or has expired.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except vps_provisioning_service.VPSProvisioningError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/hardware/vps/plans")
async def get_hardware_vps_plans(
    provider: str = Query(..., min_length=1),
    token_id: Optional[str] = Query(default=None),
    workspace_id: Optional[str] = None,
    current_user=Depends(require_api_key),
):
    try:
        provider_id = vps_provisioning_service.resolve_provider_options(provider, None, None)["provider"]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    clean_token_id = str(token_id or "").strip()
    if not clean_token_id:
        # No connected account yet — only Vultr publishes plans/prices
        # without auth (see fetch_public_provider_plans), which is what lets
        # the picker show real prices before the "connect account" step.
        try:
            return vps_provisioning_service.fetch_public_provider_plans(provider_id)
        except vps_provisioning_service.VPSProvisioningError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    workspace = enforce_workspace_access(
        current_user,
        workspace_id or "default",
        minimum_role="owner",
    )
    user_id = str((current_user or {}).get("user_id") or "").strip() or None
    try:
        return vps_provisioning_service.fetch_provider_plans(
            provider_id,
            token_id=clean_token_id,
            workspace_id=workspace,
            user_id=user_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="VPS provider credential was not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except vps_provisioning_service.VPSProvisioningError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/hardware/vps/connections")
async def list_hardware_vps_connections(
    workspace_id: Optional[str] = None,
    current_user=Depends(require_api_key),
):
    """Backend-authoritative list of this workspace's already-connected VPS
    providers (MAN-105) — CloudVpsSetupPanel calls this on open and reuses
    any returned token_id straight into fetch_provider_plans instead of
    sending the user through DigitalOcean OAuth / Google sign-in /
    CloudFormation again, whether that connection was made from this
    browser, a different one, or by a teammate on the same workspace."""
    workspace = enforce_workspace_access(
        current_user,
        workspace_id or "default",
        minimum_role="owner",
    )
    return {"connections": vps_provisioning_service.list_vps_provider_tokens(workspace_id=workspace)}


# Holds strong references to the fire-and-forget asyncio.Task objects
# provision_hardware_vps schedules for the actual (multi-minute) droplet
# lifecycle — asyncio only weakly tracks a Task via the event loop while
# something else holds a reference to it; without this set, a Task can be
# garbage-collected mid-run and silently stop (a well-known asyncio
# footgun). Mirrors the exact same pattern gateway_protocol_service.py uses
# for its own per-connection background tasks (_track_background_task
# there), just module-scoped here since these aren't tied to one
# connection's lifetime. Entries remove themselves via add_done_callback the
# moment each task finishes, so this never grows unbounded.
_VPS_PROVISION_BACKGROUND_TASKS: set[asyncio.Task] = set()


def _track_vps_provision_task(task: "asyncio.Task") -> None:
    _VPS_PROVISION_BACKGROUND_TASKS.add(task)
    task.add_done_callback(_VPS_PROVISION_BACKGROUND_TASKS.discard)


@router.post("/hardware/vps/provision")
async def provision_hardware_vps(
    body: HardwareVPSProvisionRequest,
    current_user=Depends(require_api_key),
):
    """Kicks off droplet/instance provisioning and returns immediately with
    status 'provisioning' — it does NOT wait for the create call, let alone
    boot/install/connect, to finish. Those run in a background asyncio task
    (vps_provisioning_service.run_vps_provisioning_lifecycle, scheduled
    below) so a slow provider call can never block this request past
    Cloudflare's edge timeout and 502 it — which used to happen with
    provision_vps() called synchronously right here, and worse, could leave
    an already-created droplet with no vps_id ever reaching the client to
    track or delete it by. The frontend (cloud-vps-setup-panel.tsx) polls
    GET /hardware/vps/{vps_id}/status for progress; see that route and
    get_vps_provision_status/_resolved_record_status for how 'provisioning'
    -> 'registering' -> 'connected'/'failed' gets resolved.
    """
    workspace_id = enforce_workspace_access(
        current_user,
        body.workspace_id or "default",
        minimum_role="owner",
    )
    tenant_id = workspace_tenant_id(current_user, workspace_id)
    user_id = str((current_user or {}).get("user_id") or "").strip() or "unknown-user"
    try:
        resolved = vps_provisioning_service.resolve_provider_options(body.provider, body.region, body.size)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    vps_id = f"vps_{uuid.uuid4().hex}"
    try:
        request_metadata = dict(body.metadata or {})
        request_metadata.update(
            {
                "setup_source": "vps",
                "vps_id": vps_id,
                "provider": resolved["provider"],
                "region": resolved["region"],
                "size": resolved["size"],
                "agent_scope": "sage",
            }
        )
        pairing = gateway_pairing_service.create_gateway_pairing_intent(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            ttl_seconds=None,
            display_name=f"{vps_provisioning_service.PROVIDER_CONFIGS[resolved['provider']].label} Agent Computer",
            platform="linux",
            metadata=request_metadata,
            runtime_access_mode=body.runtime_access_mode,
            autonomous_agent_setup_warning_acknowledged=body.autonomous_agent_setup_warning_acknowledged,
        )
    except ValueError as exc:
        detail = str(exc)
        status_code = 429 if "too many pending gateway pairing requests" in detail.lower() else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc

    pairing_token = str(pairing.get("pairing_token") or "").strip()
    if not pairing_token:
        raise HTTPException(status_code=500, detail="Gateway pairing token was not created.")
    pairing_id = str(pairing.get("pairing_id") or "").strip() or None
    resolved_token_id = str(body.token_id or "").strip() or None
    try:
        credentials = (
            vps_provisioning_service.load_vps_provider_credentials(
                resolved_token_id,
                provider=resolved["provider"],
                workspace_id=workspace_id,
                user_id=user_id,
            )
            if resolved_token_id
            else body.credentials
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="VPS provider credential was not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Persist a 'provisioning' placeholder BEFORE returning — with no
    # provider_resource_id yet, since the provider hasn't been asked to
    # create anything at this point — so GET /hardware/vps/{vps_id}/status
    # is pollable from the instant this response lands. The background
    # lifecycle below (run_vps_provisioning_lifecycle) replaces this same
    # record with the real provider_resource_id/public_ip once the droplet
    # actually exists, and is the only thing that ever gets to fail it.
    await vps_provisioning_service.record_vps_provision(
        vps_id=vps_id,
        workspace_id=workspace_id,
        tenant_id=tenant_id,
        user_id=user_id,
        provider=resolved["provider"],
        provider_resource_id="",
        public_ip=None,
        region=resolved["region"],
        size=resolved["size"],
        status="provisioning",
        pairing_token=pairing_token,
        credentials=credentials,
        pairing_id=pairing_id,
    )

    _track_vps_provision_task(
        asyncio.create_task(
            vps_provisioning_service.run_vps_provisioning_lifecycle(
                vps_id=vps_id,
                provider=resolved["provider"],
                credentials=credentials,
                region=resolved["region"],
                size=resolved["size"],
                pairing_token=pairing_token,
                token_id=resolved_token_id,
                workspace_id=workspace_id,
                tenant_id=tenant_id,
                user_id=user_id,
                pairing_id=pairing_id,
            )
        )
    )

    return {
        "pairing_token": pairing_token,
        "vps_id": vps_id,
        "provider_resource_id": "",
        "public_ip": None,
        "status": "provisioning",
    }


@router.get("/hardware/vps/regions")
async def get_hardware_vps_regions(
    provider: str = Query(..., min_length=1),
    token_id: Optional[str] = Query(default=None),
    workspace_id: Optional[str] = None,
    current_user=Depends(require_api_key),
):
    try:
        provider_id = vps_provisioning_service.resolve_provider_options(provider, None, None)["provider"]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # A stored provider credential unlocks the live catalog (region
    # availability changes over time; the static list is a curated fallback,
    # not a hard limit). No credential yet (still on the 'provider' picker
    # step) just serves the static list, same as before this endpoint could
    # go live.
    if token_id and workspace_id:
        resolved_workspace = enforce_workspace_access(
            current_user,
            workspace_id,
            minimum_role="owner",
        )
        user_id = str((current_user or {}).get("user_id") or "").strip() or None
        try:
            return vps_provisioning_service.fetch_provider_regions(
                provider_id,
                token_id=token_id,
                workspace_id=resolved_workspace,
                user_id=user_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="VPS provider credential was not found.") from exc
        except vps_provisioning_service.VPSProvisioningError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    # No connected account yet (still on the 'provider' picker step, or
    # browsing Vultr pre-connect — see the frontend's pre-connect browsing
    # step). fetch_public_provider_regions is a safe drop-in for the old
    # provider_catalog() lookup here — same field set for every provider,
    # it just additionally prefers Vultr's live public region data when
    # available and never raises for a provider resolve_provider_options
    # already validated above.
    return vps_provisioning_service.fetch_public_provider_regions(provider_id)


@router.get("/hardware/vps/{vps_id}/status")
async def get_hardware_vps_status(
    vps_id: str,
    current_user=Depends(require_api_key),
):
    try:
        record = await vps_provisioning_service.load_vps_record(vps_id)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="VPS provisioning record was not found.") from exc
    workspace_id = enforce_workspace_access(
        current_user,
        record.get("workspace_id") or "default",
        minimum_role="viewer",
    )
    if workspace_id != str(record.get("workspace_id") or "").strip():
        raise HTTPException(status_code=404, detail="VPS provisioning record was not found.")
    try:
        status_record = await vps_provisioning_service.get_vps_provision_status(vps_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="VPS provisioning record was not found.") from exc
    return {
        "vps_id": status_record["vps_id"],
        "provider": status_record["provider"],
        "provider_resource_id": status_record["provider_resource_id"],
        "public_ip": status_record["public_ip"],
        "region": status_record["region"],
        "size": status_record["size"],
        "status": status_record["status"],
        "error": status_record.get("error"),
        # MAN-121: what the box itself last said about its own install, so the
        # setup panel can show a real reason instead of a spinner that stops.
        "install_error": status_record.get("install_error"),
        "install_phase": status_record.get("install_phase"),
        "install_reported_at": status_record.get("install_reported_at"),
    }


@router.delete("/hardware/vps/{vps_id}")
async def delete_hardware_vps(
    vps_id: str,
    current_user=Depends(require_api_key),
):
    try:
        record = await vps_provisioning_service.load_vps_record(vps_id)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="VPS provisioning record was not found.") from exc
    workspace_id = enforce_workspace_access(
        current_user,
        record.get("workspace_id") or "default",
        minimum_role="owner",
    )
    if workspace_id != str(record.get("workspace_id") or "").strip():
        raise HTTPException(status_code=404, detail="VPS provisioning record was not found.")
    try:
        deleted_record = await vps_provisioning_service.delete_recorded_vps(vps_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="VPS provisioning record was not found.") from exc
    except vps_provisioning_service.VPSProvisioningError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "vps_id": deleted_record["vps_id"],
        "provider": deleted_record["provider"],
        "provider_resource_id": deleted_record["provider_resource_id"],
        "status": deleted_record["status"],
    }


@router.post("/gateway/registrations")
async def register_gateway(body: GatewayRegistrationRequest):
    try:
        registration = gateway_pairing_service.register_gateway(
            pairing_token=body.pairing_token,
            device_id=body.device_id,
            gateway_id=body.gateway_id,
            display_name=body.display_name,
            platform=body.platform,
            capabilities=body.capabilities,
            metadata=body.metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "gateway": gateway_registry_service.gateway_registration_public_payload(registration),
        "gateway_token": str(registration.get("gateway_token") or ""),
        "scope": gateway_registry_service.gateway_scope_payload(registration),
    }


@router.post("/gateway/provisioning-events")
async def report_gateway_provisioning_event(body: GatewayProvisioningEventRequest):
    """The other half of the pairing conversation (MAN-121).

    POST /gateway/registrations above is the box's success phone-home. This is
    its failure phone-home: install-agent-computer.sh's error path calls it so a
    box that dies while installing (artifact download 404, apt failure, Node
    install failure, unsupported image) can say WHY, instead of the platform
    silently inferring a 20-minute timeout with no explanation anywhere except a
    log file behind SSH.

    Unauthenticated by design, like /gateway/registrations — the box has no
    credential yet except its pairing token, which is the bearer secret here.
    A token matching no record is discarded; nothing is ever created from a
    beacon, only annotated onto an existing provisioning record.
    """
    try:
        record = await vps_provisioning_service.record_vps_install_event(
            pairing_token=body.pairing_token,
            phase=body.phase,
            message=body.message,
            terminal=bool(body.terminal),
        )
    except Exception:  # noqa: BLE001 - diagnostics must never break the box's install further
        LOGGER.exception("gateway provisioning beacon could not be recorded")
        return {"recorded": False}
    return {"recorded": bool(record)}


@router.post("/gateway/sessions")
async def create_gateway_session(request: Request, body: GatewaySessionCreateRequest):
    try:
        return await gateway_registry_service.create_gateway_session(
            request,
            gateway_id=body.gateway_id,
            gateway_token=body.gateway_token,
            session_ttl_seconds=body.session_ttl_seconds,
            metadata=body.metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.get("/gateway/registrations")
async def list_gateway_registrations(
    workspace_id: str = Query(..., min_length=1),
    current_user=Depends(require_api_key),
):
    resolved_workspace_id = enforce_workspace_access(
        current_user,
        workspace_id,
        minimum_role="viewer",
    )
    return gateway_registry_service.list_workspace_gateways(workspace_id=resolved_workspace_id)


@router.get("/gateway/hardware/capabilities")
async def get_gateway_hardware_capabilities(
    workspace_id: str = Query(..., min_length=1),
    current_user=Depends(require_api_key),
):
    resolved_workspace_id = enforce_workspace_access(
        current_user,
        workspace_id,
        minimum_role="member",
    )
    registration = _active_workspace_gateway_registration(resolved_workspace_id)
    if not registration:
        return {
            "available": False,
            "checks": [],
        }
    gateway_id = str(registration.get("gateway_id") or "").strip()
    fallback_payload = machine_capability_check.desktop_setup_status(workspace_id=resolved_workspace_id)
    try:
        response = await gateway_execution_service.execute_tool_via_gateway(
            gateway_id=gateway_id,
            capability_id="shell.execute",
            arguments={"command": _remote_hardware_probe_command()},
            run_id=f"hardware-capability-probe-{gateway_id}-{int(datetime.now(timezone.utc).timestamp())}",
            trace_id=f"hardware-capability-probe-{gateway_id}",
            workspace_id=resolved_workspace_id,
            timeout_seconds=15,
            request_id=f"hardware-capability-probe-{gateway_id}",
            runtime_access_mode="default_guarded",
            empyralis_approved=True,
            agent_scope="system",
            emit_hardware_activity=False,
        )
        result = response.get("result") if isinstance(response.get("result"), dict) else {}
        stdout = str(result.get("stdout") or "").strip()
        payload = json.loads(stdout) if stdout else {}
        checks = _normalize_hardware_capability_checks(payload if isinstance(payload, dict) else {})
    except Exception:
        checks = _normalize_hardware_capability_checks(fallback_payload if isinstance(fallback_payload, dict) else {})
    return {
        "available": True,
        "gateway_id": gateway_id,
        "checks": checks,
    }


@router.get("/gateway/hardware/activity/stream")
async def stream_gateway_hardware_activity(
    workspace_id: str = Query(..., min_length=1),
    current_user=Depends(require_api_key),
):
    resolved_workspace_id = enforce_workspace_access(
        current_user,
        workspace_id,
        minimum_role="member",
    )
    return EventSourceResponse(
        hardware_activity_event_service.iter_hardware_action_sse(workspace_id=resolved_workspace_id),
        ping=15,
    )

@router.get("/workstation/{workspace_id}/sage/turns/stream")
async def workstation_sage_turns_sse(
    workspace_id: str,
    current_user=Depends(require_api_key),
):
    """SSE stream that emits events when new turns appear in sage-main."""
    resolved_workspace_id = enforce_workspace_access(
        current_user, workspace_id, minimum_role="member",
    )
    from server_modules.sage_agent_runtime_service import SAGE_THREAD_ID

    async def _turn_event_generator():
        import asyncio as _asyncio
        from server_modules import thread_service as _ts
        import time as _time
        last_seen_created_at = None
        while True:
            try:
                thread_record = await _ts.get_thread(
                    SAGE_THREAD_ID,
                    tenant_id="default",
                    workspace_id=resolved_workspace_id,
                    include_turns=True,
                )
                if isinstance(thread_record, dict):
                    raw_turns = list(thread_record.get("turns") or [])
                    if raw_turns:
                        # Find newest turn by created_at
                        newest = max(raw_turns, key=lambda t: str(t.get("created_at") or ""))
                        new_created = str(newest.get("created_at") or "")
                        if last_seen_created_at is not None and new_created > last_seen_created_at:
                            # New turn detected — emit event
                            import json as _json
                            yield {
                                "event": "new_turn",
                                "data": _json.dumps({
                                    "thread_id": SAGE_THREAD_ID,
                                    "workspace_id": resolved_workspace_id,
                                    "role": newest.get("role"),
                                    "created_at": new_created,
                                }),
                            }
                        last_seen_created_at = new_created
            except Exception:
                pass
            await _asyncio.sleep(2)

    return EventSourceResponse(_turn_event_generator(), ping=15)




@router.post("/gateway/registrations/{gateway_id}/rotate-token")
async def rotate_gateway_registration_token(
    gateway_id: str,
    current_user=Depends(require_api_key),
):
    registration = gateway_state_repository.get_gateway_registration(gateway_id)
    if not registration:
        raise HTTPException(status_code=404, detail="Gateway registration was not found.")
    registration_workspace_id = str(registration.get("workspace_id") or "").strip() or "default"
    resolved_workspace_id = enforce_workspace_access(
        current_user,
        registration_workspace_id,
        minimum_role="owner",
    )
    if resolved_workspace_id != registration_workspace_id:
        raise HTTPException(status_code=403, detail="Workspace is not accessible for this user.")
    tenant_id = workspace_tenant_id(current_user, resolved_workspace_id)
    try:
        return gateway_registry_service.rotate_gateway_registration_token(
            gateway_id=gateway_id,
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            user_id=str((current_user or {}).get("user_id") or "").strip() or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/gateway/registrations/{gateway_id}/revoke")
async def revoke_gateway_registration(
    gateway_id: str,
    body: Optional[GatewayRegistrationRevokeRequest] = None,
    current_user=Depends(require_api_key),
):
    registration = gateway_state_repository.get_gateway_registration(gateway_id)
    if not registration:
        raise HTTPException(status_code=404, detail="Gateway registration was not found.")
    registration_workspace_id = str(registration.get("workspace_id") or "").strip() or "default"
    resolved_workspace_id = enforce_workspace_access(
        current_user,
        registration_workspace_id,
        minimum_role="owner",
    )
    if resolved_workspace_id != registration_workspace_id:
        raise HTTPException(status_code=403, detail="Workspace is not accessible for this user.")
    tenant_id = workspace_tenant_id(current_user, resolved_workspace_id)
    try:
        revoked_payload = gateway_registry_service.revoke_gateway_registration(
            gateway_id=gateway_id,
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            user_id=str((current_user or {}).get("user_id") or "").strip() or None,
            reason=str((body or GatewayRegistrationRevokeRequest()).reason or "").strip() or None,
        )
        mutation_plan = dict(revoked_payload.pop("mutation_plan", {}) or {})
        if mutation_plan.get("shutdown_live_connection", True):
            revoked_payload["live_connection_shutdown"] = await _shutdown_revoked_live_gateway_connection(
                gateway_id,
                reason="registration revoked",
            )
        else:
            revoked_payload["live_connection_shutdown"] = False
        if mutation_plan.get("mark_dedicated_workstation_revoked", True):
            dedicated_workstation_setup_service.mark_dedicated_workstation_revoked(
                gateway_id=gateway_id,
                reason=str(mutation_plan.get("revocation_reason") or "").strip() or "Gateway registration revoked.",
                actor_user_id=str((current_user or {}).get("user_id") or "").strip(),
            )
        return revoked_payload
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/gateway/registrations/{gateway_id}/rename")
async def rename_gateway_registration_route(
    gateway_id: str,
    body: GatewayRegistrationRenameRequest,
    current_user=Depends(require_api_key),
):
    registration = gateway_state_repository.get_gateway_registration(gateway_id)
    if not registration:
        raise HTTPException(status_code=404, detail="Gateway registration was not found.")
    registration_workspace_id = str(registration.get("workspace_id") or "").strip() or "default"
    resolved_workspace_id = enforce_workspace_access(
        current_user,
        registration_workspace_id,
        minimum_role="owner",
    )
    if resolved_workspace_id != registration_workspace_id:
        raise HTTPException(status_code=403, detail="Workspace is not accessible for this user.")
    tenant_id = workspace_tenant_id(current_user, resolved_workspace_id)
    try:
        return gateway_registry_service.rename_gateway_registration(
            gateway_id=gateway_id,
            display_name=body.display_name,
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            user_id=str((current_user or {}).get("user_id") or "").strip() or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/gateway/registrations/{gateway_id}/dedicated-workstation/bind")
async def bind_dedicated_workstation(
    gateway_id: str,
    body: DedicatedWorkstationBindRequest,
    current_user=Depends(require_api_key),
):
    registration, resolved_workspace_id = _accessible_gateway_registration(
        gateway_id,
        current_user,
        workspace_id=body.workspace_id,
        minimum_role="owner",
    )
    tenant_id = workspace_tenant_id(current_user, resolved_workspace_id)
    try:
        return dedicated_workstation_setup_service.bind_dedicated_workstation(
            gateway_id=gateway_id,
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            user_id=str((current_user or {}).get("user_id") or registration.get("user_id") or "").strip(),
            policy_id=body.policy_id,
            machine_label=str(body.machine_label or "").strip(),
            trace_id=str(body.trace_id or "").strip(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/gateway/registrations/{gateway_id}/dedicated-workstation/readiness")
async def get_dedicated_workstation_readiness(
    gateway_id: str,
    workspace_id: Optional[str] = Query(default=None, min_length=1),
    trace_id: Optional[str] = Query(default=None, max_length=200),
    current_user=Depends(require_api_key),
):
    registration, resolved_workspace_id = _accessible_gateway_registration(
        gateway_id,
        current_user,
        workspace_id=workspace_id,
        minimum_role="viewer",
    )
    tenant_id = workspace_tenant_id(current_user, resolved_workspace_id)
    try:
        return dedicated_workstation_setup_service.dedicated_workstation_readiness(
            gateway_id=gateway_id,
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            user_id=str((current_user or {}).get("user_id") or registration.get("user_id") or "").strip(),
            trace_id=str(trace_id or "").strip(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/gateway/registrations/{gateway_id}/dedicated-workstation/kill")
async def kill_dedicated_workstation(
    gateway_id: str,
    body: Optional[DedicatedWorkstationControlRequest] = None,
    current_user=Depends(require_api_key),
):
    payload = body or DedicatedWorkstationControlRequest()
    registration, resolved_workspace_id = _accessible_gateway_registration(
        gateway_id,
        current_user,
        workspace_id=payload.workspace_id,
        minimum_role="owner",
    )
    tenant_id = workspace_tenant_id(current_user, resolved_workspace_id)
    try:
        response = dedicated_workstation_setup_service.kill_dedicated_workstation(
            gateway_id=gateway_id,
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            user_id=str((current_user or {}).get("user_id") or registration.get("user_id") or "").strip(),
            reason=str(payload.reason or "").strip(),
            trace_id=str(payload.trace_id or "").strip(),
        )
        response["live_connection_shutdown"] = await _shutdown_revoked_live_gateway_connection(
            gateway_id,
            reason="dedicated workstation stopped",
        )
        return response
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/gateway/registrations/{gateway_id}/dedicated-workstation/clear-kill")
async def clear_dedicated_workstation_kill(
    gateway_id: str,
    body: Optional[DedicatedWorkstationControlRequest] = None,
    current_user=Depends(require_api_key),
):
    payload = body or DedicatedWorkstationControlRequest()
    registration, resolved_workspace_id = _accessible_gateway_registration(
        gateway_id,
        current_user,
        workspace_id=payload.workspace_id,
        minimum_role="owner",
    )
    tenant_id = workspace_tenant_id(current_user, resolved_workspace_id)
    try:
        return dedicated_workstation_setup_service.clear_dedicated_workstation_kill(
            gateway_id=gateway_id,
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            user_id=str((current_user or {}).get("user_id") or registration.get("user_id") or "").strip(),
            trace_id=str(payload.trace_id or "").strip(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/gateway/registrations/{gateway_id}/tools/execute")
async def execute_gateway_tool(
    gateway_id: str,
    body: GatewayToolExecuteRequest,
    current_user=Depends(require_api_key),
):
    registration = gateway_state_repository.get_gateway_registration(gateway_id)
    if not registration:
        raise HTTPException(status_code=404, detail="Gateway registration was not found.")
    registration_workspace_id = str(registration.get("workspace_id") or "").strip() or "default"
    resolved_workspace_id = enforce_workspace_access(
        current_user,
        body.workspace_id or registration_workspace_id,
        minimum_role="member",
    )
    if resolved_workspace_id != registration_workspace_id:
        raise HTTPException(status_code=403, detail="Workspace is not accessible for this user.")
    if _registration_requires_full_access_reconfirmation(registration):
        _raise_full_access_reconfirmation_required()

    tenant_id = workspace_tenant_id(current_user, resolved_workspace_id)
    _enforce_gateway_safety_gates(
        gateway_id=gateway_id,
        workspace_id=resolved_workspace_id,
        quota_profile=GATEWAY_TOOL_EXECUTION,
        tenant_id=tenant_id,
        capability_id=body.capability_id,
    )

    # ── Unified governance gate — AUDIT ONLY ──
    # The gate runs kill switch → safe mode → risk classifier for audit trail.
    # Consumers NEVER see approval cards or blocked messages — governance is
    # internalized into the agent's system prompt.  The ONE exception is the
    # kill switch, which remains as platform defense-in-depth.
    from server_modules.unified_governance_gate import evaluate_action_policy
    gateway_policy = _gateway_policy_from_registration(registration)
    ug_decision = evaluate_action_policy(
        workspace_id=resolved_workspace_id,
        actor_user_id=str((current_user or {}).get("user_id") or "").strip() or "user",
        agent_id="sage",
        tenant_id=tenant_id,
        gateway_id=gateway_id,
        policy=gateway_policy,
        capability=body.capability_id,
        action_class=body.arguments.get("action_class") if isinstance(body.arguments, dict) else None,
        target_url=body.arguments.get("url") if isinstance(body.arguments, dict) else None,
        target_path=body.arguments.get("path") if isinstance(body.arguments, dict) else None,
        payload=body.arguments,
        consume_approval_memory=False,
        surface="gateway_tool",
    )

    # Emit risk decision for audit (extract from the unified gate's risk_decision dict)
    _emit_gateway_risk_decision(
        gateway_id=gateway_id,
        workspace_id=resolved_workspace_id,
        tenant_id=tenant_id,
        risk_decision=ug_decision.risk_decision if isinstance(ug_decision.risk_decision, dict) else {},
    )

    # ── Kill switch: defense-in-depth ──
    # When the kill switch is active, refuse execution as a platform safeguard.
    # The agent's system prompt already says "capabilities suspended" — this is
    # a backup. Returns a graceful error, not a consumer-facing BLOCKED card.
    if ug_decision.blocked:
        _block_gateway_risk_decision(risk_decision=ug_decision)
        # Execution is blocked by kill switch — log and return gracefully
        _enforce_gateway_service_decision(
            operation="tool_execute_blocked",
            gateway_id=gateway_id,
            workspace_id=resolved_workspace_id,
            tenant_id=tenant_id,
            actor_id=str((current_user or {}).get("user_id") or "").strip() or "user",
            quota_profile=GATEWAY_TOOL_EXECUTION,
            capability_id=body.capability_id,
            run_id=body.run_id,
            trace_id=str(body.trace_id or body.request_id or body.run_id).strip() or body.run_id,
            request_id=str(body.request_id or "").strip() or body.run_id,
            approval_provided=False,
            approval_memory_hit=False,
            risk_decision=ug_decision.decision,
        )
        # Return gracefully — the channel will see this as a normal tool failure,
        # not a governance block. The agent handles it naturally.
        from fastapi import HTTPException as _HTTPException
        raise _HTTPException(
            status_code=503,
            detail="Service temporarily unavailable. Please try again later."
        )

    # ── Approval is now INTERNALIZED — log for audit, never block ──
    explicit_full_access = _registration_sage_full_access(registration)
    _audit_gateway_governance_decision(
        ug_decision=ug_decision,
        gateway_id=gateway_id,
        workspace_id=resolved_workspace_id,
        tenant_id=tenant_id,
        capability_id=body.capability_id,
        explicit_full_access=explicit_full_access,
    )

    # Consume approval memory for audit trail (if present)
    if ug_decision.approval_required and not explicit_full_access:
        try:
            _consume_gateway_approval_memory(
                registration=registration,
                workspace_id=resolved_workspace_id,
                tenant_id=tenant_id,
                actor_user_id=str((current_user or {}).get("user_id") or "").strip() or "user",
                policy_id=gateway_policy.policy_id,
                risk_decision=ug_decision,
                payload=body.arguments,
                run_id=body.run_id,
                trace_id=str(body.trace_id or body.request_id or body.run_id).strip() or body.run_id,
                request_id=str(body.request_id or "").strip() or body.run_id,
            )
        except Exception:
            pass  # approval memory is audit-only — never block

    _audit_approval_bypass(
        gateway_id=gateway_id,
        capability_id=body.capability_id,
        workspace_id=resolved_workspace_id,
        tenant_id=tenant_id,
    )

    _enforce_gateway_service_decision(
        operation="tool_execute",
        gateway_id=gateway_id,
        workspace_id=resolved_workspace_id,
        tenant_id=tenant_id,
        actor_id=str((current_user or {}).get("user_id") or "").strip() or "user",
        quota_profile=GATEWAY_TOOL_EXECUTION,
        capability_id=body.capability_id,
        run_id=body.run_id,
        trace_id=str(body.trace_id or body.request_id or body.run_id).strip() or body.run_id,
        request_id=str(body.request_id or "").strip() or body.run_id,
        approval_provided=True,  # approval is internalized — always provided
        approval_memory_hit=False,
        risk_decision=ug_decision.decision,
    )
    try:
        return await gateway_execution_service.execute_tool_via_gateway(
            gateway_id=gateway_id,
            capability_id=body.capability_id,
            arguments=body.arguments,
            run_id=body.run_id,
            trace_id=str(body.trace_id or body.request_id or body.run_id).strip() or body.run_id,
            workspace_id=resolved_workspace_id,
            timeout_seconds=int(body.timeout_seconds or gateway_protocol_service.DEFAULT_TOOL_REQUEST_TIMEOUT_SECONDS),
            request_id=str(body.request_id or "").strip() or None,
            screenshot_retention=gateway_policy.screenshot_retention,
            agent_scope="sage",
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        detail = str(exc)
        status_code = 409 if "not currently connected" in detail.lower() else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc


@router.get("/gateway/registrations/{gateway_id}/events")
async def list_gateway_registration_events(
    gateway_id: str,
    limit: int = Query(default=50, ge=1, le=500),
    current_user=Depends(require_api_key),
):
    registration = gateway_state_repository.get_gateway_registration(gateway_id)
    if not registration:
        raise HTTPException(status_code=404, detail="Gateway registration was not found.")
    registration_workspace_id = str(registration.get("workspace_id") or "").strip() or "default"
    resolved_workspace_id = enforce_workspace_access(
        current_user,
        registration_workspace_id,
        minimum_role="viewer",
    )
    if resolved_workspace_id != registration_workspace_id:
        raise HTTPException(status_code=403, detail="Workspace is not accessible for this user.")
    items = gateway_state_repository.list_gateway_events(gateway_id, limit=limit)
    return {
        "gateway_id": gateway_id,
        "count": len(items),
        "items": items,
    }


@router.post("/gateway/registrations/{gateway_id}/self-update")
async def self_update_gateway(
    gateway_id: str,
    body: GatewaySelfUpdateRequest,
    current_user=Depends(require_api_key),
):
    """Trigger a self-update on a paired gateway — one button, no SSH. See
    gateway_self_update_service.trigger_gateway_self_update() for the
    dispatch (routes through the same tool-invoke transport as cli/install
    below) and its module docstring for what "latest" means today.
    "member" (not "viewer") because this changes what's running on the box —
    same gate cli/install and personal-channels install use."""
    registration, resolved_workspace_id = _accessible_gateway_registration(
        gateway_id,
        current_user,
        workspace_id=body.workspace_id,
        minimum_role="member",
    )
    try:
        return await gateway_self_update_service.trigger_gateway_self_update(
            gateway_id=gateway_id,
            workspace_id=resolved_workspace_id,
            registration=registration,
            target_version=str(body.target_version or ""),
            artifact_url=str(body.artifact_url or ""),
            actor_id=str((current_user or {}).get("user_id") or "").strip() or None,
        )
    except gateway_self_update_service.GatewaySelfUpdateError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("/gateway/registrations/{gateway_id}/restart")
async def restart_gateway(
    gateway_id: str,
    body: GatewayRestartRequest,
    current_user=Depends(require_api_key),
):
    """In-product "restart the gateway" — one button, no SSH, no VPS
    destroy+recreate. See gateway_restart_service.trigger_gateway_restart()
    for the dispatch (same tool-invoke transport as self-update/doctor just
    above) and empyralis-gateway/src/update/gateway-restart-runtime.ts for
    why this needs no target_version/artifact_url: it re-execs the exact
    build already on disk, nothing is downloaded or swapped.
    "member" (not "viewer") because this interrupts anything currently
    running on the box — same gate self-update/doctor-run use."""
    _registration, resolved_workspace_id = _accessible_gateway_registration(
        gateway_id,
        current_user,
        workspace_id=body.workspace_id,
        minimum_role="member",
    )
    try:
        return await gateway_restart_service.trigger_gateway_restart(
            gateway_id=gateway_id,
            workspace_id=resolved_workspace_id,
            actor_id=str((current_user or {}).get("user_id") or "").strip() or None,
        )
    except gateway_restart_service.GatewayRestartError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("/gateway/registrations/{gateway_id}/cli/install")
async def install_gateway_cli_runtime(
    gateway_id: str,
    body: CliInstallRequest,
    current_user=Depends(require_api_key),
):
    _registration, resolved_workspace_id = _accessible_gateway_registration(
        gateway_id,
        current_user,
        workspace_id=body.workspace_id,
        minimum_role="member",
    )
    try:
        return await cli_setup_service.install_cli_runtime(
            gateway_id=gateway_id,
            workspace_id=resolved_workspace_id,
            runtime=body.runtime,
            run_id=body.run_id,
            trace_id=str(body.trace_id or "").strip(),
            request_id=str(body.request_id or "").strip() or None,
            actor_id=str((current_user or {}).get("user_id") or "").strip() or None,
        )
    except cli_setup_service.CliSetupError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("/gateway/registrations/{gateway_id}/cli/login/start")
async def start_gateway_cli_login(
    gateway_id: str,
    body: CliLoginStartRequest,
    current_user=Depends(require_api_key),
):
    _registration, resolved_workspace_id = _accessible_gateway_registration(
        gateway_id,
        current_user,
        workspace_id=body.workspace_id,
        minimum_role="member",
    )
    try:
        return await cli_setup_service.start_cli_login(
            gateway_id=gateway_id,
            workspace_id=resolved_workspace_id,
            runtime=body.runtime,
            method=str(body.method or "").strip() or None,
            run_id=body.run_id,
            trace_id=str(body.trace_id or "").strip(),
            request_id=str(body.request_id or "").strip() or None,
            actor_id=str((current_user or {}).get("user_id") or "").strip() or None,
        )
    except cli_setup_service.CliSetupError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("/gateway/registrations/{gateway_id}/cli/login/{run_id}/input")
async def submit_gateway_cli_login_input(
    gateway_id: str,
    run_id: str,
    body: CliLoginInputRequest,
    current_user=Depends(require_api_key),
):
    _registration, resolved_workspace_id = _accessible_gateway_registration(
        gateway_id,
        current_user,
        workspace_id=body.workspace_id,
        minimum_role="member",
    )
    resolved_value = str(body.value or body.code or "").strip()
    if not resolved_value:
        raise HTTPException(status_code=422, detail="Value is required.")
    try:
        return await cli_setup_service.submit_cli_login_input(
            gateway_id=gateway_id,
            workspace_id=resolved_workspace_id,
            run_id=run_id,
            value=resolved_value,
            # Back-compat: submit_cli_login_input's own signature still
            # accepts (and documents accepting) the pre-multi-method `code=`
            # kwarg for callers that haven't moved to value=/kind= — value=
            # always wins there when both are set, so passing the same
            # resolved value under both names is a no-op for behavior and
            # keeps this route compatible with that documented contract.
            code=resolved_value,
            kind=str(body.kind or "").strip() or None,
            trace_id=str(body.trace_id or "").strip(),
            request_id=str(body.request_id or "").strip() or None,
            actor_id=str((current_user or {}).get("user_id") or "").strip() or None,
        )
    except cli_setup_service.CliSetupError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("/gateway/registrations/{gateway_id}/cli/login/{run_id}/cancel")
async def cancel_gateway_cli_login(
    gateway_id: str,
    run_id: str,
    body: Optional[CliLoginCancelRequest] = None,
    current_user=Depends(require_api_key),
):
    payload = body or CliLoginCancelRequest()
    _registration, resolved_workspace_id = _accessible_gateway_registration(
        gateway_id,
        current_user,
        workspace_id=payload.workspace_id,
        minimum_role="member",
    )
    try:
        return await cli_setup_service.cancel_cli_login(
            gateway_id=gateway_id,
            workspace_id=resolved_workspace_id,
            run_id=run_id,
            trace_id=str(payload.trace_id or "").strip(),
            request_id=str(payload.request_id or "").strip() or None,
            reason=payload.reason,
        )
    except cli_setup_service.CliSetupError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.get("/gateway/cli/methods")
async def list_gateway_cli_login_methods(
    current_user=Depends(require_api_key),
):
    """List every auth method each runtime supports, in display order.
    The frontend's CliSetupControl uses this to render the auth-method
    chooser instead of hardcoding a single flow. Static; safe to cache."""
    del current_user  # not workspace-scoped, static shape
    return {"runtimes": cli_setup_service.LOGIN_METHODS}


@router.get("/gateway/registrations/{gateway_id}/cli/login/{run_id}/events")
async def list_gateway_cli_login_events(
    gateway_id: str,
    run_id: str,
    limit: int = Query(default=50, ge=1, le=500),
    current_user=Depends(require_api_key),
):
    _registration, _resolved_workspace_id = _accessible_gateway_registration(
        gateway_id,
        current_user,
        minimum_role="viewer",
    )
    items = cli_setup_service.list_cli_login_events(gateway_id=gateway_id, run_id=run_id, limit=limit)
    return {
        "gateway_id": gateway_id,
        "run_id": run_id,
        "count": len(items),
        "items": items,
    }


@router.get("/gateway/registrations/{gateway_id}/browser/sessions")
async def list_gateway_browser_sessions(
    gateway_id: str,
    session_status: Optional[str] = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=500),
    current_user=Depends(require_api_key),
):
    _registration, _resolved_workspace_id = _accessible_gateway_registration(
        gateway_id,
        current_user,
        minimum_role="viewer",
    )
    items = gateway_state_repository.list_gateway_browser_sessions(
        gateway_id,
        status=session_status,
        limit=limit,
    )
    return {
        "gateway_id": gateway_id,
        "count": len(items),
        "items": items,
    }


@router.post("/gateway/registrations/{gateway_id}/browser/sessions")
async def start_gateway_browser_session(
    gateway_id: str,
    body: GatewayBrowserSessionStartRequest,
    current_user=Depends(require_api_key),
):
    registration, resolved_workspace_id = _accessible_gateway_registration(
        gateway_id,
        current_user,
        workspace_id=body.workspace_id,
        minimum_role="member",
    )
    if _registration_requires_full_access_reconfirmation(registration):
        _raise_full_access_reconfirmation_required()
    _enforce_gateway_safety_gates(
        gateway_id=gateway_id,
        workspace_id=resolved_workspace_id,
        quota_profile=GATEWAY_BROWSER_SESSION,
    )
    trace_id = str(body.trace_id or body.request_id or body.run_id).strip() or body.run_id
    session_mode = gateway_browser_service.normalize_browser_session_mode(body.session_mode)
    attach_endpoint_url = str(body.attach_endpoint_url or "").strip() or None
    reviewed_required = gateway_browser_service.browser_session_requires_reviewed_approval(
        session_mode=session_mode,
        attach_endpoint_url=attach_endpoint_url,
        reviewed_approval_required=bool(body.reviewed_approval_required),
    )
    plan_payload = {
        "url": str(body.url or "").strip() or None,
        "session_mode": session_mode,
        "attach_endpoint_configured": bool(attach_endpoint_url),
        "interactive_actions": list(body.interactive_actions or []),
    }
    browser_metadata = gateway_browser_service.build_gateway_browser_metadata(
        session_profile=body.session_profile,
        session_mode=session_mode,
        attach_endpoint_url=attach_endpoint_url,
        interactive_actions=body.interactive_actions,
        reviewed_approval_required=reviewed_required,
        plan_payload=plan_payload,
    )
    arguments = {
        "url": str(body.url or "").strip() or None,
        "session_profile": str(body.session_profile or "").strip() or None,
        "session_mode": session_mode,
        "attach_endpoint_url": attach_endpoint_url,
        "interactive_actions": list(body.interactive_actions or []),
        "browser_metadata": browser_metadata,
    }
    tenant_id = workspace_tenant_id(current_user, resolved_workspace_id)
    try:
        gateway_policy = _gateway_policy_from_registration(registration)
        risk_payload = dict(arguments)
        if (
            session_mode == gateway_browser_service.BROWSER_SESSION_MODE_MANAGED_PROFILE
            and not tuple(getattr(gateway_policy, "domain_allowlist", ()) or ())
        ):
            risk_payload["url"] = None
        risk_decision = classify_gateway_browser_action_risk(
            policy=gateway_policy,
            browser_action="start",
            payload=risk_payload,
            reviewed_approval_required=reviewed_required,
        )
    except CapabilityRiskClassifierError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _emit_gateway_risk_decision(
        gateway_id=gateway_id,
        workspace_id=resolved_workspace_id,
        tenant_id=tenant_id,
        risk_decision=risk_decision,
    )
    if risk_decision.decision == DECISION_BLOCK:
        _block_gateway_risk_decision(risk_decision=risk_decision)
    remembered_approval_rule = _consume_gateway_approval_memory(
        registration=registration,
        workspace_id=resolved_workspace_id,
        tenant_id=tenant_id,
        actor_user_id=str((current_user or {}).get("user_id") or "").strip() or "user",
        policy_id=gateway_policy.policy_id,
        risk_decision=risk_decision,
        payload=arguments,
        run_id=body.run_id,
        trace_id=trace_id,
        request_id=str(body.request_id or "").strip() or body.run_id,
    )
    if body.allow_cloud_fallback and not gateway_protocol_service.gateway_connection_is_live(gateway_id):
        _enforce_gateway_service_decision(
            operation="cloud_fallback",
            gateway_id=gateway_id,
            workspace_id=resolved_workspace_id,
            tenant_id=tenant_id,
            actor_id=str((current_user or {}).get("user_id") or "").strip() or "user",
            quota_profile=GATEWAY_BROWSER_SESSION,
            capability_id=gateway_browser_service.BROWSER_SESSION_START_CAPABILITY,
            run_id=body.run_id,
            trace_id=trace_id,
            request_id=str(body.request_id or "").strip() or body.run_id,
            approval_provided=True,
            approval_memory_hit=remembered_approval_rule is not None,
            risk_decision=risk_decision.decision,
            cloud_fallback_enabled=True,
            cloud_fallback_approved=True,
        )
        fallback = await gateway_browser_service.build_cloud_browser_fallback_response(
            registration=registration,
            run_id=body.run_id,
            trace_id=trace_id,
            session_profile=body.session_profile,
            reason="Local gateway browser runtime is offline; cloud browser fallback prepared.",
        )
        return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=fallback)
    _enforce_gateway_service_decision(
        operation="browser_session",
        gateway_id=gateway_id,
        workspace_id=resolved_workspace_id,
        tenant_id=tenant_id,
        actor_id=str((current_user or {}).get("user_id") or "").strip() or "user",
        quota_profile=GATEWAY_BROWSER_SESSION,
        capability_id=gateway_browser_service.BROWSER_SESSION_START_CAPABILITY,
        run_id=body.run_id,
        trace_id=trace_id,
        request_id=str(body.request_id or "").strip() or body.run_id,
        approval_provided=True,
        approval_memory_hit=remembered_approval_rule is not None,
        risk_decision=risk_decision.decision,
        cloud_fallback_enabled=bool(body.allow_cloud_fallback),
        cloud_fallback_approved=bool(body.allow_cloud_fallback),
    )
    try:
        response = await gateway_browser_service.execute_browser_capability_via_gateway(
            gateway_id=gateway_id,
            capability_id=gateway_browser_service.BROWSER_SESSION_START_CAPABILITY,
            arguments=arguments,
            run_id=body.run_id,
            trace_id=trace_id,
            workspace_id=resolved_workspace_id,
            timeout_seconds=int(body.timeout_seconds or gateway_protocol_service.DEFAULT_TOOL_REQUEST_TIMEOUT_SECONDS),
            request_id=str(body.request_id or "").strip() or None,
        )
        browser_status = str((response.get("browser_session") or {}).get("status") or response.get("status") or "").strip().lower()
        if browser_status in {"attach_required", "attach_failed", "not_attached"}:
            return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=response)
        return response
    except ValueError as exc:
        detail = str(exc)
        if body.allow_cloud_fallback and "not currently connected" in detail.lower():
            _enforce_gateway_service_decision(
                operation="cloud_fallback",
                gateway_id=gateway_id,
                workspace_id=resolved_workspace_id,
                tenant_id=tenant_id,
                actor_id=str((current_user or {}).get("user_id") or "").strip() or "user",
                quota_profile=GATEWAY_BROWSER_SESSION,
                capability_id=gateway_browser_service.BROWSER_SESSION_START_CAPABILITY,
                run_id=body.run_id,
                trace_id=trace_id,
                request_id=str(body.request_id or "").strip() or body.run_id,
                approval_provided=True,
                approval_memory_hit=remembered_approval_rule is not None,
                risk_decision=risk_decision.decision,
                cloud_fallback_enabled=True,
                cloud_fallback_approved=True,
            )
            fallback = await gateway_browser_service.build_cloud_browser_fallback_response(
                registration=registration,
                run_id=body.run_id,
                trace_id=trace_id,
                session_profile=body.session_profile,
                reason=detail,
            )
            return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=fallback)
        status_code = 409 if "not currently connected" in detail.lower() else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc


@router.post("/gateway/registrations/{gateway_id}/browser/sessions/{browser_session_id}/actions")
async def execute_gateway_browser_action(
    gateway_id: str,
    browser_session_id: str,
    body: GatewayBrowserActionRequest,
    current_user=Depends(require_api_key),
):
    registration, resolved_workspace_id = _accessible_gateway_registration(
        gateway_id,
        current_user,
        workspace_id=body.workspace_id,
        minimum_role="member",
    )
    if _registration_requires_full_access_reconfirmation(registration):
        _raise_full_access_reconfirmation_required()
    _enforce_gateway_safety_gates(
        gateway_id=gateway_id,
        workspace_id=resolved_workspace_id,
        quota_profile=GATEWAY_TOOL_EXECUTION,
    )
    browser_session = gateway_state_repository.get_gateway_browser_session(browser_session_id)
    if not browser_session or str(browser_session.get("gateway_id") or "").strip() != str(gateway_id or "").strip():
        raise HTTPException(status_code=404, detail="Gateway browser session was not found.")
    trace_id = str(body.trace_id or body.request_id or body.run_id).strip() or body.run_id
    session_metadata = browser_session.get("metadata") if isinstance(browser_session.get("metadata"), dict) else {}
    session_mode = gateway_browser_service.normalize_browser_session_mode(session_metadata.get("browser_session_mode"))
    attach_endpoint_url = str(session_metadata.get("browser_attach_endpoint_url") or "").strip() or None
    reviewed_required = gateway_browser_service.browser_session_requires_reviewed_approval(
        session_mode=session_mode,
        attach_endpoint_url=attach_endpoint_url,
        reviewed_approval_required=(
            bool(body.reviewed_approval_required)
            if body.reviewed_approval_required is not None
            else bool(browser_session.get("reviewed_approval_required"))
        ),
    )
    browser_metadata = gateway_browser_service.build_gateway_browser_metadata(
        session_profile=browser_session.get("session_profile"),
        session_mode=session_mode,
        attach_endpoint_url=attach_endpoint_url,
        interactive_actions=[body.action],
        reviewed_approval_required=reviewed_required,
        reviewed_approved=bool(browser_session.get("reviewed_approved")),
        browser_session_id=browser_session_id,
        checkpoint=browser_session.get("checkpoint") if isinstance(browser_session.get("checkpoint"), dict) else {},
        plan_payload={
            "action": str(body.action or "").strip(),
            "action_args": dict(body.action_args or {}),
        },
    )
    arguments = {
        "browser_session_id": browser_session_id,
        "session_mode": session_mode,
        "attach_endpoint_url": attach_endpoint_url,
        "action": str(body.action or "").strip(),
        "action_args": dict(body.action_args or {}),
        "browser_metadata": browser_metadata,
        "checkpoint": browser_session.get("checkpoint") if isinstance(browser_session.get("checkpoint"), dict) else {},
    }
    tenant_id = workspace_tenant_id(current_user, resolved_workspace_id)
    try:
        gateway_policy = _gateway_policy_from_registration(registration)
        risk_decision = classify_gateway_browser_action_risk(
            policy=gateway_policy,
            browser_action=body.action,
            payload=arguments,
            reviewed_approval_required=reviewed_required,
        )
    except CapabilityRiskClassifierError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _emit_gateway_risk_decision(
        gateway_id=gateway_id,
        workspace_id=resolved_workspace_id,
        tenant_id=tenant_id,
        risk_decision=risk_decision,
    )
    if risk_decision.decision == DECISION_BLOCK:
        _block_gateway_risk_decision(risk_decision=risk_decision)
    remembered_approval_rule = _consume_gateway_approval_memory(
        registration=registration,
        workspace_id=resolved_workspace_id,
        tenant_id=tenant_id,
        actor_user_id=str((current_user or {}).get("user_id") or "").strip() or "user",
        policy_id=gateway_policy.policy_id,
        risk_decision=risk_decision,
        payload=arguments,
        run_id=body.run_id,
        trace_id=trace_id,
        request_id=str(body.request_id or "").strip() or body.run_id,
        browser_session_id=browser_session_id,
    )
    if body.allow_cloud_fallback and not gateway_protocol_service.gateway_connection_is_live(gateway_id):
        _enforce_gateway_service_decision(
            operation="cloud_fallback",
            gateway_id=gateway_id,
            workspace_id=resolved_workspace_id,
            tenant_id=tenant_id,
            actor_id=str((current_user or {}).get("user_id") or "").strip() or "user",
            quota_profile=GATEWAY_TOOL_EXECUTION,
            capability_id=gateway_browser_service.BROWSER_SESSION_ACTION_CAPABILITY,
            run_id=body.run_id,
            trace_id=trace_id,
            request_id=str(body.request_id or "").strip() or body.run_id,
            browser_session_id=browser_session_id,
            approval_provided=True,
            approval_memory_hit=remembered_approval_rule is not None,
            risk_decision=risk_decision.decision,
            cloud_fallback_enabled=True,
            cloud_fallback_approved=True,
        )
        fallback = await gateway_browser_service.build_cloud_browser_fallback_response(
            registration=registration,
            run_id=body.run_id,
            trace_id=trace_id,
            session_profile=browser_session.get("session_profile"),
            reason="Local gateway browser runtime is offline; cloud browser fallback prepared.",
            checkpoint=browser_session.get("checkpoint") if isinstance(browser_session.get("checkpoint"), dict) else {},
        )
        return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=fallback)
    _enforce_gateway_service_decision(
        operation="browser_action",
        gateway_id=gateway_id,
        workspace_id=resolved_workspace_id,
        tenant_id=tenant_id,
        actor_id=str((current_user or {}).get("user_id") or "").strip() or "user",
        quota_profile=GATEWAY_TOOL_EXECUTION,
        capability_id=gateway_browser_service.BROWSER_SESSION_ACTION_CAPABILITY,
        run_id=body.run_id,
        trace_id=trace_id,
        request_id=str(body.request_id or "").strip() or body.run_id,
        browser_session_id=browser_session_id,
        approval_provided=True,
        approval_memory_hit=remembered_approval_rule is not None,
        risk_decision=risk_decision.decision,
    )
    try:
        response = await gateway_browser_service.execute_browser_capability_via_gateway(
            gateway_id=gateway_id,
            capability_id=gateway_browser_service.BROWSER_SESSION_ACTION_CAPABILITY,
            arguments=arguments,
            run_id=body.run_id,
            trace_id=trace_id,
            workspace_id=resolved_workspace_id,
            timeout_seconds=int(body.timeout_seconds or gateway_protocol_service.DEFAULT_TOOL_REQUEST_TIMEOUT_SECONDS),
            request_id=str(body.request_id or "").strip() or None,
        )
        browser_status = str((response.get("browser_session") or {}).get("status") or response.get("status") or "").strip().lower()
        if browser_status in {"attach_required", "attach_failed", "not_attached"}:
            return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=response)
        return response
    except ValueError as exc:
        detail = str(exc)
        if body.allow_cloud_fallback and "not currently connected" in detail.lower():
            _enforce_gateway_service_decision(
                operation="cloud_fallback",
                gateway_id=gateway_id,
                workspace_id=resolved_workspace_id,
                tenant_id=tenant_id,
                actor_id=str((current_user or {}).get("user_id") or "").strip() or "user",
                quota_profile=GATEWAY_TOOL_EXECUTION,
                capability_id=gateway_browser_service.BROWSER_SESSION_ACTION_CAPABILITY,
                run_id=body.run_id,
                trace_id=trace_id,
                request_id=str(body.request_id or "").strip() or body.run_id,
                browser_session_id=browser_session_id,
                approval_provided=True,
                approval_memory_hit=remembered_approval_rule is not None,
                risk_decision=risk_decision.decision,
                cloud_fallback_enabled=True,
                cloud_fallback_approved=True,
            )
            fallback = await gateway_browser_service.build_cloud_browser_fallback_response(
                registration=registration,
                run_id=body.run_id,
                trace_id=trace_id,
                session_profile=browser_session.get("session_profile"),
                reason=detail,
                checkpoint=browser_session.get("checkpoint") if isinstance(browser_session.get("checkpoint"), dict) else {},
            )
            return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=fallback)
        status_code = 409 if "not currently connected" in detail.lower() else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc


@router.post("/gateway/registrations/{gateway_id}/browser/sessions/{browser_session_id}/takeover")
async def takeover_gateway_browser_session(
    gateway_id: str,
    browser_session_id: str,
    body: GatewayBrowserSessionControlRequest,
    current_user=Depends(require_api_key),
):
    _browser_session = gateway_state_repository.get_gateway_browser_session(browser_session_id)
    if not _browser_session or str(_browser_session.get("gateway_id") or "").strip() != str(gateway_id or "").strip():
        raise HTTPException(status_code=404, detail="Gateway browser session was not found.")
    _registration, resolved_workspace_id = _accessible_gateway_registration(
        gateway_id,
        current_user,
        workspace_id=body.workspace_id,
        minimum_role="member",
    )
    _enforce_gateway_safety_gates(
        gateway_id=gateway_id,
        workspace_id=resolved_workspace_id,
        quota_profile=GATEWAY_TOOL_EXECUTION,
    )
    tenant_id = workspace_tenant_id(current_user, resolved_workspace_id)
    trace_id = str(body.trace_id or body.request_id or body.run_id).strip() or body.run_id
    _enforce_gateway_service_decision(
        operation="browser_action",
        gateway_id=gateway_id,
        workspace_id=resolved_workspace_id,
        tenant_id=tenant_id,
        actor_id=str((current_user or {}).get("user_id") or "").strip() or "user",
        quota_profile=GATEWAY_TOOL_EXECUTION,
        capability_id=gateway_browser_service.BROWSER_SESSION_TAKEOVER_CAPABILITY,
        run_id=body.run_id,
        trace_id=trace_id,
        request_id=str(body.request_id or "").strip() or body.run_id,
        browser_session_id=browser_session_id,
        approval_provided=True,
        approval_memory_hit=False,
        risk_decision="normal",
    )
    try:
        return await gateway_browser_service.execute_browser_capability_via_gateway(
            gateway_id=gateway_id,
            capability_id=gateway_browser_service.BROWSER_SESSION_TAKEOVER_CAPABILITY,
            arguments={"browser_session_id": browser_session_id, "note": str(body.note or "").strip() or None},
            run_id=body.run_id,
            trace_id=trace_id,
            workspace_id=resolved_workspace_id,
            timeout_seconds=int(body.timeout_seconds or gateway_protocol_service.DEFAULT_TOOL_REQUEST_TIMEOUT_SECONDS),
            request_id=str(body.request_id or "").strip() or None,
        )
    except ValueError as exc:
        detail = str(exc)
        status_code = 409 if "not currently connected" in detail.lower() else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc


@router.post("/gateway/registrations/{gateway_id}/browser/sessions/{browser_session_id}/resume")
async def resume_gateway_browser_session(
    gateway_id: str,
    browser_session_id: str,
    body: GatewayBrowserSessionControlRequest,
    current_user=Depends(require_api_key),
):
    browser_session = gateway_state_repository.get_gateway_browser_session(browser_session_id)
    if not browser_session or str(browser_session.get("gateway_id") or "").strip() != str(gateway_id or "").strip():
        raise HTTPException(status_code=404, detail="Gateway browser session was not found.")
    registration, resolved_workspace_id = _accessible_gateway_registration(
        gateway_id,
        current_user,
        workspace_id=body.workspace_id,
        minimum_role="member",
    )
    _enforce_gateway_safety_gates(
        gateway_id=gateway_id,
        workspace_id=resolved_workspace_id,
        quota_profile=GATEWAY_BROWSER_SESSION,
    )
    tenant_id = workspace_tenant_id(current_user, resolved_workspace_id)
    trace_id = str(body.trace_id or body.request_id or body.run_id).strip() or body.run_id
    session_metadata = browser_session.get("metadata") if isinstance(browser_session.get("metadata"), dict) else {}
    session_mode = gateway_browser_service.normalize_browser_session_mode(session_metadata.get("browser_session_mode"))
    attach_endpoint_url = str(session_metadata.get("browser_attach_endpoint_url") or "").strip() or None
    if not gateway_protocol_service.gateway_connection_is_live(gateway_id):
        _enforce_gateway_service_decision(
            operation="cloud_fallback",
            gateway_id=gateway_id,
            workspace_id=resolved_workspace_id,
            tenant_id=tenant_id,
            actor_id=str((current_user or {}).get("user_id") or "").strip() or "user",
            quota_profile=GATEWAY_BROWSER_SESSION,
            capability_id=gateway_browser_service.BROWSER_SESSION_RESUME_CAPABILITY,
            run_id=body.run_id,
            trace_id=trace_id,
            request_id=str(body.request_id or "").strip() or body.run_id,
            browser_session_id=browser_session_id,
            approval_provided=True,
            approval_memory_hit=False,
            risk_decision="normal",
            cloud_fallback_enabled=True,
            cloud_fallback_approved=True,
        )
        fallback = await gateway_browser_service.build_cloud_browser_fallback_response(
            registration=registration,
            run_id=body.run_id,
            trace_id=trace_id,
            session_profile=browser_session.get("session_profile"),
            reason="Local gateway browser runtime is offline; cloud browser fallback prepared.",
            checkpoint=browser_session.get("checkpoint") if isinstance(browser_session.get("checkpoint"), dict) else {},
        )
        return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=fallback)
    _enforce_gateway_service_decision(
        operation="browser_session",
        gateway_id=gateway_id,
        workspace_id=resolved_workspace_id,
        tenant_id=tenant_id,
        actor_id=str((current_user or {}).get("user_id") or "").strip() or "user",
        quota_profile=GATEWAY_BROWSER_SESSION,
        capability_id=gateway_browser_service.BROWSER_SESSION_RESUME_CAPABILITY,
        run_id=body.run_id,
        trace_id=trace_id,
        request_id=str(body.request_id or "").strip() or body.run_id,
        browser_session_id=browser_session_id,
        approval_provided=True,
        approval_memory_hit=False,
        risk_decision="normal",
    )
    try:
        response = await gateway_browser_service.execute_browser_capability_via_gateway(
            gateway_id=gateway_id,
            capability_id=gateway_browser_service.BROWSER_SESSION_RESUME_CAPABILITY,
            arguments={
                "browser_session_id": browser_session_id,
                "session_mode": session_mode,
                "attach_endpoint_url": attach_endpoint_url,
                "checkpoint": browser_session.get("checkpoint") if isinstance(browser_session.get("checkpoint"), dict) else {},
            },
            run_id=body.run_id,
            trace_id=trace_id,
            workspace_id=resolved_workspace_id,
            timeout_seconds=int(body.timeout_seconds or gateway_protocol_service.DEFAULT_TOOL_REQUEST_TIMEOUT_SECONDS),
            request_id=str(body.request_id or "").strip() or None,
        )
        browser_status = str((response.get("browser_session") or {}).get("status") or response.get("status") or "").strip().lower()
        if browser_status in {"attach_required", "attach_failed", "not_attached"}:
            return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=response)
        return response
    except ValueError as exc:
        detail = str(exc)
        if "not currently connected" in detail.lower():
            _enforce_gateway_service_decision(
                operation="cloud_fallback",
                gateway_id=gateway_id,
                workspace_id=resolved_workspace_id,
                tenant_id=tenant_id,
                actor_id=str((current_user or {}).get("user_id") or "").strip() or "user",
                quota_profile=GATEWAY_BROWSER_SESSION,
                capability_id=gateway_browser_service.BROWSER_SESSION_RESUME_CAPABILITY,
                run_id=body.run_id,
                trace_id=trace_id,
                request_id=str(body.request_id or "").strip() or body.run_id,
                browser_session_id=browser_session_id,
                approval_provided=True,
                approval_memory_hit=False,
                risk_decision="normal",
                cloud_fallback_enabled=True,
                cloud_fallback_approved=True,
            )
            fallback = await gateway_browser_service.build_cloud_browser_fallback_response(
                registration=registration,
                run_id=body.run_id,
                trace_id=trace_id,
                session_profile=browser_session.get("session_profile"),
                reason=detail,
                checkpoint=browser_session.get("checkpoint") if isinstance(browser_session.get("checkpoint"), dict) else {},
            )
            return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=fallback)
        raise HTTPException(status_code=400, detail=detail) from exc


@router.post("/gateway/registrations/{gateway_id}/browser/sessions/{browser_session_id}/interrupt")
async def interrupt_gateway_browser_session(
    gateway_id: str,
    browser_session_id: str,
    body: GatewayBrowserSessionControlRequest,
    current_user=Depends(require_api_key),
):
    browser_session = gateway_state_repository.get_gateway_browser_session(browser_session_id)
    if not browser_session or str(browser_session.get("gateway_id") or "").strip() != str(gateway_id or "").strip():
        raise HTTPException(status_code=404, detail="Gateway browser session was not found.")
    _registration, resolved_workspace_id = _accessible_gateway_registration(
        gateway_id,
        current_user,
        workspace_id=body.workspace_id,
        minimum_role="member",
    )
    _enforce_gateway_safety_gates(
        gateway_id=gateway_id,
        workspace_id=resolved_workspace_id,
        quota_profile=GATEWAY_TOOL_EXECUTION,
    )
    tenant_id = workspace_tenant_id(current_user, resolved_workspace_id)
    trace_id = str(body.trace_id or body.request_id or body.run_id).strip() or body.run_id
    _enforce_gateway_service_decision(
        operation="browser_action",
        gateway_id=gateway_id,
        workspace_id=resolved_workspace_id,
        tenant_id=tenant_id,
        actor_id=str((current_user or {}).get("user_id") or "").strip() or "user",
        quota_profile=GATEWAY_TOOL_EXECUTION,
        capability_id=gateway_browser_service.BROWSER_SESSION_INTERRUPT_CAPABILITY,
        run_id=body.run_id,
        trace_id=trace_id,
        request_id=str(body.request_id or "").strip() or body.run_id,
        browser_session_id=browser_session_id,
        approval_provided=True,
        approval_memory_hit=False,
        risk_decision="normal",
    )
    try:
        return await gateway_browser_service.execute_browser_capability_via_gateway(
            gateway_id=gateway_id,
            capability_id=gateway_browser_service.BROWSER_SESSION_INTERRUPT_CAPABILITY,
            arguments={
                "browser_session_id": browser_session_id,
                "note": str(body.note or "").strip() or None,
            },
            run_id=body.run_id,
            trace_id=trace_id,
            workspace_id=resolved_workspace_id,
            timeout_seconds=int(body.timeout_seconds or gateway_protocol_service.DEFAULT_TOOL_REQUEST_TIMEOUT_SECONDS),
            request_id=str(body.request_id or "").strip() or None,
        )
    except ValueError as exc:
        detail = str(exc)
        status_code = 409 if "not currently connected" in detail.lower() else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc


@router.get("/gateway/registrations/{gateway_id}/doctor")
async def get_gateway_registration_doctor(
    gateway_id: str,
    force_provider_probe: bool = Query(False),
    current_user=Depends(require_api_key),
):
    registration = gateway_state_repository.get_gateway_registration(gateway_id)
    if not registration:
        raise HTTPException(status_code=404, detail="Gateway registration was not found.")
    registration_workspace_id = str(registration.get("workspace_id") or "").strip() or "default"
    resolved_workspace_id = enforce_workspace_access(
        current_user,
        registration_workspace_id,
        minimum_role="viewer",
    )
    if resolved_workspace_id != registration_workspace_id:
        raise HTTPException(status_code=403, detail="Workspace is not accessible for this user.")
    _enforce_gateway_service_decision(
        operation="health_check",
        gateway_id=gateway_id,
        workspace_id=resolved_workspace_id,
        tenant_id=workspace_tenant_id(current_user, resolved_workspace_id),
        actor_id=str((current_user or {}).get("user_id") or "").strip() or "user",
        quota_profile=GATEWAY_WS_CONNECTION,
        capability_id="gateway.health",
        run_id="gateway-health",
        trace_id=f"gateway-health:{gateway_id}",
        request_id=f"gateway-health:{gateway_id}",
        approval_provided=True,
        approval_memory_hit=False,
        risk_decision="normal",
    )
    try:
        return gateway_health_service.gateway_doctor_payload(
            gateway_id,
            force_provider_probe=bool(force_provider_probe),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/gateway/registrations/{gateway_id}/doctor/run")
async def run_gateway_registration_doctor(
    gateway_id: str,
    body: GatewayDoctorRunRequest,
    current_user=Depends(require_api_key),
):
    """Runs the in-gateway detect -> (safe) repair -> re-validate doctor pass
    LIVE on the paired computer — distinct from the GET .../doctor endpoint
    just above, which only aggregates backend-stored state plus a few live
    provider probes and has no repair mode. This dispatches the
    gateway.doctor.run capability (empyralis-gateway/src/health/gateway-
    doctor.ts) over the same tool-invoke transport self-update/cli-install
    use (gateway_doctor_service.run_gateway_doctor()).

    "member" (not "viewer") because repair=true can change local gateway
    state (refreshing its passive-inventory cache today; more repairs may
    follow) — same gate self-update and cli/install use, even though a
    report-only (repair=false) run changes nothing itself."""
    _registration, resolved_workspace_id = _accessible_gateway_registration(
        gateway_id,
        current_user,
        workspace_id=body.workspace_id,
        minimum_role="member",
    )
    try:
        return await gateway_doctor_service.run_gateway_doctor(
            gateway_id=gateway_id,
            workspace_id=resolved_workspace_id,
            repair=bool(body.repair),
            actor_id=str((current_user or {}).get("user_id") or "").strip() or None,
        )
    except gateway_doctor_service.GatewayDoctorError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("/gateway/registrations/{gateway_id}/tools/interrupt")
async def interrupt_gateway_tool(
    gateway_id: str,
    body: GatewayToolInterruptRequest,
    current_user=Depends(require_api_key),
):
    registration = gateway_state_repository.get_gateway_registration(gateway_id)
    if not registration:
        raise HTTPException(status_code=404, detail="Gateway registration was not found.")
    registration_workspace_id = str(registration.get("workspace_id") or "").strip() or "default"
    resolved_workspace_id = enforce_workspace_access(
        current_user,
        body.workspace_id or registration_workspace_id,
        minimum_role="member",
    )
    if resolved_workspace_id != registration_workspace_id:
        raise HTTPException(status_code=403, detail="Workspace is not accessible for this user.")
    _enforce_gateway_service_decision(
        operation="tool_interrupt",
        gateway_id=gateway_id,
        workspace_id=resolved_workspace_id,
        tenant_id=workspace_tenant_id(current_user, resolved_workspace_id),
        actor_id=str((current_user or {}).get("user_id") or "").strip() or "user",
        quota_profile=GATEWAY_TOOL_EXECUTION,
        capability_id="tool.interrupt",
        run_id=body.run_id,
        trace_id=str(body.trace_id or body.request_id or body.run_id).strip() or body.run_id,
        request_id=str(body.request_id or "").strip() or body.run_id,
        approval_provided=True,
        approval_memory_hit=False,
        risk_decision="normal",
    )
    try:
        return await gateway_execution_service.interrupt_tool_via_gateway(
            gateway_id=gateway_id,
            run_id=body.run_id,
            trace_id=str(body.trace_id or body.request_id or body.run_id).strip() or body.run_id,
            workspace_id=resolved_workspace_id,
            target_request_id=str(body.target_request_id or "").strip() or None,
            reason=str(body.reason or "").strip() or None,
            timeout_seconds=int(body.timeout_seconds or gateway_protocol_service.DEFAULT_TOOL_REQUEST_TIMEOUT_SECONDS),
            request_id=str(body.request_id or "").strip() or None,
        )
    except ValueError as exc:
        detail = str(exc)
        status_code = 409 if "not currently connected" in detail.lower() else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc


@router.websocket("/gateway/ws")
async def gateway_websocket(
    websocket: WebSocket,
    gateway_id: str = Query(..., min_length=1),
    session_token: Optional[str] = Query(default=None, min_length=1),
):
    subprotocol_token, accept_subprotocol = _gateway_ws_session_token_from_subprotocol(websocket)
    if session_token and not _gateway_ws_query_token_allowed():
        await websocket.close(code=4401, reason="query session token disabled")
        return
    resolved_session_token = str(subprotocol_token or session_token or "").strip()
    if not resolved_session_token:
        await websocket.close(code=4401, reason="session token required")
        return
    registration = gateway_state_repository.get_gateway_registration(gateway_id)
    workspace_id = str((registration or {}).get("workspace_id") or "").strip() or "default"
    try:
        _enforce_gateway_safety_gates(
            gateway_id=gateway_id,
            workspace_id=workspace_id,
            quota_profile=GATEWAY_WS_CONNECTION,
        )
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {"error": {"code": "GATEWAY_BLOCKED"}}
        code = str((detail.get("error") or {}).get("code") or "GATEWAY_BLOCKED") if isinstance(detail, dict) else "GATEWAY_BLOCKED"
        await websocket.close(code=4403, reason=code[:120])
        return
    await gateway_protocol_service.handle_gateway_websocket(
        websocket,
        gateway_id=gateway_id,
        session_token=resolved_session_token,
        accept_subprotocol=accept_subprotocol,
    )


@router.post("/gateway/acp/turn", status_code=200)
async def acp_turn_endpoint(
    request: Request,
    workspace_id: str = Query(..., description="Workspace ID"),
    current_user=Depends(require_api_key),
):
    from server_modules.acp_bridge_service import (
        parse_acp_message,
        translate_acp_to_gateway,
        translate_gateway_to_acp,
        build_acp_error,
    )
    from server_modules.sage_agent_runtime_service import handle_sage_chat
    from server_modules.channel_adapter import normalize_sage_inbound, filter_outbound_reply
    from server_modules.sage_agent_runtime_contract import SageTurnResult

    resolved_workspace_id = enforce_workspace_access(
        current_user,
        workspace_id,
        minimum_role="member",
    )
    tenant_id = workspace_tenant_id(current_user, resolved_workspace_id)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            content=build_acp_error("invalid_json", "Request body is not valid JSON."),
            status_code=400,
        )

    acp_msg = parse_acp_message(body)
    if acp_msg.get("error"):
        return JSONResponse(content=acp_msg, status_code=400)

    gw_frame = translate_acp_to_gateway(acp_msg)
    msg_type = acp_msg.get("type", "")

    if msg_type == "health.check":
        from server_modules.acp_bridge_service import build_acp_health_response
        return JSONResponse(content=build_acp_health_response(), status_code=200)

    if msg_type == "agent.turn":
        try:
            params = gw_frame.get("params", {})
            payload_workspace_id = str(params.get("workspace_id") or "").strip()
            if payload_workspace_id and payload_workspace_id != resolved_workspace_id:
                return JSONResponse(
                    content=build_acp_error(
                        "workspace_mismatch",
                        "ACP payload workspace_id does not match the authenticated workspace.",
                        acp_msg.get("id", ""),
                    ),
                    status_code=403,
                )
            message = str(params.get("message", "")).strip()
            _enforce_gateway_acp_turn_decision(
                workspace_id=resolved_workspace_id,
                request_id=str(acp_msg.get("id") or "").strip(),
                message=message,
            )
            turn = normalize_sage_inbound(
                workspace_id=resolved_workspace_id,
                tenant_id=tenant_id,
                message=message,
                surface="acp",
                mode="owner_sage",
                current_user=current_user,
                channel_origin="acp",
            )
            result = await handle_sage_chat(
                workspace_id=turn.workspace_id,
                tenant_id=turn.tenant_id,
                message=turn.message,
                surface=turn.surface,
                mode=turn.mode,
                current_user=turn.current_user,
                channel_origin=turn.channel_origin,
                sender_name=(turn.current_user or {}).get("name") or (turn.current_user or {}).get("email") or "",
                sender_id=(turn.current_user or {}).get("user_id") or "",
            )
            # Filter silent replies before ACP response
            raw_message = result.get("message") if isinstance(result, dict) else ""
            if isinstance(result, SageTurnResult):
                raw_message = result.reply
            filtered_message = filter_outbound_reply(raw_message)
            if isinstance(result, SageTurnResult):
                reply_data = {
                    "reply": filtered_message or result.reply,
                    "session_id": result.session_id or "",
                    "provider": result.provider or "",
                    "model": result.model or "",
                    "trace_id": result.trace_id or "",
                }
            elif isinstance(result, dict):
                reply_data = {
                    "reply": str(result.get("reply") or result.get("message") or ""),
                    "session_id": str(result.get("session_id") or ""),
                    "trace_id": str(result.get("trace_id") or ""),
                }
            else:
                reply_data = {"reply": str(result)}
            return JSONResponse(
                content={
                    "id": acp_msg.get("id", ""),
                    "type": "response",
                    "result": reply_data,
                    "timestamp": acp_msg.get("timestamp", ""),
                },
                status_code=200,
            )
        except Exception as exc:
            return JSONResponse(
                content=build_acp_error("turn_failed", str(exc), acp_msg.get("id", "")),
                status_code=500,
            )

    if msg_type in ("session.create",):
        return JSONResponse(
            content={
                "id": acp_msg.get("id", ""),
                "type": "response",
                "result": {
                    "session_id": gw_frame.get("params", {}).get("session_id", ""),
                    "status": "created",
                },
                "timestamp": acp_msg.get("timestamp", ""),
            },
            status_code=200,
        )

    return JSONResponse(
        content=build_acp_error(
            "not_implemented",
            f"ACP message type '{msg_type}' is recognized but not yet implemented.",
            acp_msg.get("id", ""),
        ),
        status_code=501,
    )


@router.get("/diagnostics/sessions/{session_id}/export")
async def export_session_diagnostics_endpoint(
    session_id: str,
    workspace_id: str = Query("default", min_length=1),
    current_user=Depends(require_api_key),
):
    resolved_workspace_id = enforce_workspace_access(
        current_user,
        workspace_id,
        minimum_role="viewer",
    )
    from server_modules.session_diagnostics_service import export_session_trace

    payload = await export_session_trace(session_id)
    if payload.get("error") == "session_id_required":
        raise HTTPException(status_code=400, detail="session_id_required")
    if payload.get("error") == "session_not_found":
        raise HTTPException(status_code=404, detail="session_not_found")
    session_workspace_id = str((payload.get("session") or {}).get("workspace_id") or "").strip()
    if session_workspace_id and session_workspace_id != resolved_workspace_id:
        raise HTTPException(status_code=403, detail="Session is not in the requested workspace.")
    return payload


@router.get("/diagnostics/workspace/{workspace_id}/bundle")
async def export_workspace_diagnostics_endpoint(
    workspace_id: str,
    current_user=Depends(require_api_key),
):
    resolved_workspace_id = enforce_workspace_access(
        current_user,
        workspace_id,
        minimum_role="viewer",
    )
    _enforce_gateway_diagnostics_export_decision(
        workspace_id=resolved_workspace_id,
        tenant_id=workspace_tenant_id(current_user, resolved_workspace_id),
        actor_role=str(current_user.get("role") or "viewer").strip() or "viewer",
    )
    from server_modules.session_diagnostics_service import export_diagnostics_bundle

    payload = await export_diagnostics_bundle(resolved_workspace_id)
    if payload.get("error") == "workspace_id_required":
        raise HTTPException(status_code=400, detail="workspace_id_required")
    return payload

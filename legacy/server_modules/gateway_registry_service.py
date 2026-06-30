from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional
from urllib.parse import quote

from fastapi import HTTPException
from fastapi import Request

from server_modules import auth, execution_mode_policy, gateway_state_repository, session_service


DEFAULT_GATEWAY_SESSION_TTL_SECONDS = 15 * 60
DEFAULT_GATEWAY_HEARTBEAT_INTERVAL_SECONDS = 20
DEFAULT_GATEWAY_FRESH_HEARTBEAT_SECONDS = max(45, DEFAULT_GATEWAY_HEARTBEAT_INTERVAL_SECONDS * 2)


def _parse_utc_ts(value: Any) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _heartbeat_age_seconds(value: Any) -> Optional[int]:
    parsed = _parse_utc_ts(value)
    if parsed is None:
        return None
    age = (datetime.now(timezone.utc) - parsed).total_seconds()
    return max(int(age), 0)


def _gateway_connection_payload(registration: Dict[str, Any]) -> Dict[str, Any]:
    latest_session = gateway_state_repository.get_latest_gateway_session(
        str(registration.get("gateway_id") or "").strip(),
        include_revoked=True,
    )
    last_heartbeat_at = (
        (latest_session or {}).get("last_heartbeat_at")
        or registration.get("last_heartbeat_at")
    )
    heartbeat_age_seconds = _heartbeat_age_seconds(last_heartbeat_at)
    heartbeat_fresh = (
        heartbeat_age_seconds is not None
        and heartbeat_age_seconds <= DEFAULT_GATEWAY_FRESH_HEARTBEAT_SECONDS
    )
    registration_status = str(registration.get("status") or "").strip().lower()
    device_trust_state = str(registration.get("device_trust_state") or "").strip().lower()
    session_status = str((latest_session or {}).get("status") or "").strip().lower()
    latest_session_metadata = dict((latest_session or {}).get("metadata") or {})
    registration_metadata = dict(registration.get("metadata") or {})
    reported_health_state = str(
        latest_session_metadata.get("health_state")
        or registration_metadata.get("health_state")
        or ""
    ).strip().lower()
    if registration_status == "revoked" or device_trust_state == "revoked":
        connection_status = "revoked"
    elif session_status == "pending":
        connection_status = "reconnecting"
    elif reported_health_state == "reconnecting":
        recently_active = (
            heartbeat_age_seconds is None
            or heartbeat_age_seconds <= DEFAULT_GATEWAY_FRESH_HEARTBEAT_SECONDS * 4
        )
        connection_status = (
            "reconnecting"
            if session_status in {"connected", "disconnected", "pending"} and recently_active
            else "offline"
        )
    elif reported_health_state == "degraded":
        connection_status = "degraded"
    elif session_status == "connected" and heartbeat_fresh:
        connection_status = "online"
    elif session_status in {"connected", "pending"}:
        connection_status = "degraded"
    else:
        connection_status = "offline"
    return {
        "connection_status": connection_status,
        "reported_health_state": reported_health_state or None,
        "heartbeat_fresh": bool(heartbeat_fresh),
        "heartbeat_age_seconds": heartbeat_age_seconds,
        "latest_session_id": str((latest_session or {}).get("session_id") or "").strip() or None,
        "latest_session_status": session_status or None,
        "latest_connected_at": (latest_session or {}).get("connected_at"),
        "latest_disconnected_at": (latest_session or {}).get("disconnected_at"),
    }


def gateway_registration_public_payload(registration: Dict[str, Any]) -> Dict[str, Any]:
    metadata = dict(registration.get("metadata") or {})
    runtime_access_mode = execution_mode_policy.normalize_runtime_access_mode(
        metadata.get("runtime_access_mode")
    )
    return {
        "gateway_id": str(registration.get("gateway_id") or ""),
        "device_id": str(registration.get("device_id") or ""),
        "tenant_id": str(registration.get("tenant_id") or ""),
        "workspace_id": str(registration.get("workspace_id") or ""),
        "user_id": str(registration.get("user_id") or ""),
        "status": str(registration.get("status") or ""),
        "device_trust_state": str(registration.get("device_trust_state") or ""),
        "display_name": registration.get("display_name"),
        "platform": registration.get("platform"),
        "metadata": metadata,
        "agent_computer_policy_id": metadata.get("agent_computer_policy_id"),
        "agent_computer_emergency_stop": metadata.get("agent_computer_emergency_stop"),
        "runtime_access_mode": runtime_access_mode,
        "runtime_access_label": execution_mode_policy.public_runtime_access_label(runtime_access_mode),
        "runtime_access_setup_warning": execution_mode_policy.runtime_access_setup_warning(runtime_access_mode),
        "autonomous_agent_setup_warning_acknowledged": bool(
            metadata.get("autonomous_agent_setup_warning_acknowledged")
        ),
        "capabilities": list(registration.get("capabilities") or []),
        "journal_cursor": int(registration.get("journal_cursor") or 0),
        "checkpoint_cursor": int(registration.get("checkpoint_cursor") or 0),
        "created_at": registration.get("created_at"),
        "updated_at": registration.get("updated_at"),
        "last_seen_at": registration.get("last_seen_at"),
        "last_heartbeat_at": registration.get("last_heartbeat_at"),
        "token_rotated_at": registration.get("token_rotated_at"),
        "revoked_at": registration.get("revoked_at"),
        "revoked_reason": registration.get("revoked_reason"),
        **_gateway_connection_payload(registration),
    }


def gateway_scope_payload(registration: Dict[str, Any]) -> Dict[str, str]:
    return {
        "tenant_id": str(registration.get("tenant_id") or ""),
        "workspace_id": str(registration.get("workspace_id") or ""),
        "user_id": str(registration.get("user_id") or ""),
        "device_id": str(registration.get("device_id") or ""),
        "gateway_id": str(registration.get("gateway_id") or ""),
    }


def build_gateway_ws_url(
    request: Request,
    *,
    gateway_id: str,
    session_token: str,
) -> str:
    forwarded_proto = str(request.headers.get("x-forwarded-proto") or "").split(",", 1)[0].strip().lower()
    request_scheme = str(request.url.scheme or "").strip().lower()
    scheme = "wss" if (forwarded_proto or request_scheme) == "https" else "ws"
    forwarded_host = str(request.headers.get("x-forwarded-host") or "").split(",", 1)[0].strip()
    netloc = forwarded_host or request.headers.get("host") or request.url.netloc
    return (
        f"{scheme}://{netloc}/api/gateway/ws"
        f"?gateway_id={quote(str(gateway_id or '').strip())}"
    )


async def create_gateway_session(
    request: Request,
    *,
    gateway_id: str,
    gateway_token: str,
    session_ttl_seconds: Optional[int] = None,
    metadata: Optional[Dict[str, Any]] = None,
    trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    registration = gateway_state_repository.get_gateway_registration(gateway_id)
    if not registration:
        raise ValueError("Gateway registration was not found.")
    try:
        device_link = auth.validate_local_gateway_device_link(
            user_id=str(registration.get("user_id") or "").strip(),
            workspace_id=str(registration.get("workspace_id") or "").strip(),
            device_id=str(registration.get("device_id") or "").strip(),
            gateway_id=str(registration.get("gateway_id") or "").strip(),
        )
    except HTTPException as exc:
        gateway_state_repository.update_gateway_registration_state(
            gateway_id=str(gateway_id or "").strip(),
            device_trust_state="revoked"
            if "revoked" in str(exc.detail or "").strip().lower()
            else str(registration.get("device_trust_state") or "verified").strip() or "verified",
            status="revoked"
            if "revoked" in str(exc.detail or "").strip().lower()
            else str(registration.get("status") or "active").strip() or "active",
            metadata={"last_identity_error": str(exc.detail)},
        )
        raise ValueError(str(exc.detail)) from exc
    session = gateway_state_repository.issue_gateway_session(
        gateway_id=gateway_id,
        gateway_token=gateway_token,
        ttl_seconds=int(session_ttl_seconds or DEFAULT_GATEWAY_SESSION_TTL_SECONDS),
        metadata=metadata,
    )
    session_metadata = {
        **dict(metadata or {}),
        "tenant_id": str(registration.get("tenant_id") or "").strip(),
        "workspace_id": str(registration.get("workspace_id") or "").strip(),
        "user_id": str(registration.get("user_id") or "").strip(),
        "device_id": str(registration.get("device_id") or "").strip(),
        "gateway_id": str(registration.get("gateway_id") or "").strip(),
        "device_trust_state": str(device_link.get("trust_state") or "verified").strip() or "verified",
    }
    auth.create_auth_session(
        str(registration.get("user_id") or "").strip(),
        channel="local_runtime_companion",
        device_id=str(registration.get("device_id") or "").strip() or None,
        runtime_id=str(registration.get("gateway_id") or "").strip() or None,
        trust_state=str(device_link.get("trust_state") or "verified").strip() or "verified",
        session_id=str(session.get("session_id") or "").strip() or None,
        session_family_id=str(registration.get("gateway_id") or "").strip() or None,
        metadata=session_metadata,
        ttl_seconds=int(session_ttl_seconds or DEFAULT_GATEWAY_SESSION_TTL_SECONDS),
    )
    await session_service.create_local_gateway_session(
        tenant_id=str(registration.get("tenant_id") or "").strip(),
        workspace_id=str(registration.get("workspace_id") or "").strip(),
        user_id=str(registration.get("user_id") or "").strip(),
        device_id=str(registration.get("device_id") or "").strip(),
        gateway_id=str(registration.get("gateway_id") or "").strip(),
        display_name=str(registration.get("display_name") or "").strip() or None,
        metadata=session_metadata,
        session_id=str(session.get("session_id") or "").strip() or None,
        ttl_seconds=int(session_ttl_seconds or DEFAULT_GATEWAY_SESSION_TTL_SECONDS),
    )
    registration = gateway_state_repository.sync_gateway_registration_identity(
        gateway_id=str(gateway_id or "").strip(),
        tenant_id=str(registration.get("tenant_id") or "").strip() or None,
        workspace_id=str(registration.get("workspace_id") or "").strip() or None,
        user_id=str(registration.get("user_id") or "").strip() or None,
        device_id=str(registration.get("device_id") or "").strip() or None,
        device_trust_state=str(device_link.get("trust_state") or "verified").strip() or "verified",
        metadata={
            "auth_session_id": str(session.get("session_id") or "").strip() or None,
            "runtime_session_id": str(session.get("session_id") or "").strip() or None,
        },
    ) or registration
    return {
        "session_id": str(session.get("session_id") or ""),
        "gateway_id": str(gateway_id or ""),
        "session_token": str(session.get("session_token") or ""),
        "ws_url": build_gateway_ws_url(
            request,
            gateway_id=str(gateway_id or ""),
            session_token=str(session.get("session_token") or ""),
        ),
        "heartbeat_interval_seconds": DEFAULT_GATEWAY_HEARTBEAT_INTERVAL_SECONDS,
        "scope": gateway_scope_payload(registration),
        "gateway": gateway_registration_public_payload(registration),
        "created_at": session.get("created_at"),
        "expires_at": session.get("expires_at"),
    }


def list_workspace_gateways(*, workspace_id: str) -> Dict[str, Any]:
    items = [
        gateway_registration_public_payload(item)
        for item in gateway_state_repository.list_workspace_gateway_registrations(workspace_id)
    ]
    return {
        "workspace_id": str(workspace_id or "").strip() or "default",
        "count": len(items),
        "items": items,
    }


def rotate_gateway_registration_token(
    *,
    gateway_id: str,
    tenant_id: str,
    workspace_id: str,
    user_id: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    registration = gateway_state_repository.get_gateway_registration(gateway_id)
    if not registration:
        raise ValueError("Gateway registration was not found.")
    try:
        device_link = auth.validate_local_gateway_device_link(
            user_id=str(registration.get("user_id") or "").strip(),
            workspace_id=str(registration.get("workspace_id") or "").strip(),
            device_id=str(registration.get("device_id") or "").strip(),
            gateway_id=str(registration.get("gateway_id") or "").strip(),
        )
    except HTTPException as exc:
        gateway_state_repository.update_gateway_registration_state(
            gateway_id=str(gateway_id or "").strip(),
            device_trust_state="revoked"
            if "revoked" in str(exc.detail or "").strip().lower()
            else str(registration.get("device_trust_state") or "verified").strip() or "verified",
            status="revoked"
            if "revoked" in str(exc.detail or "").strip().lower()
            else str(registration.get("status") or "active").strip() or "active",
            metadata={"last_identity_error": str(exc.detail)},
        )
        raise ValueError(str(exc.detail)) from exc
    rotated = gateway_state_repository.rotate_gateway_token(
        gateway_id=gateway_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
    )
    fresh_gateway_token = str(rotated.get("gateway_token") or "")
    rotated = gateway_state_repository.sync_gateway_registration_identity(
        gateway_id=str(rotated.get("gateway_id") or "").strip(),
        tenant_id=str(rotated.get("tenant_id") or "").strip() or None,
        workspace_id=str(rotated.get("workspace_id") or "").strip() or None,
        user_id=str(rotated.get("user_id") or "").strip() or None,
        device_id=str(rotated.get("device_id") or "").strip() or None,
        device_trust_state=str(device_link.get("trust_state") or "verified").strip() or "verified",
        metadata={
            "device_link": {
                "device_id": str(device_link.get("device_id") or "").strip() or None,
                "workspace_id": str(device_link.get("workspace_id") or "").strip() or None,
                "status": str(device_link.get("status") or "").strip() or None,
                "trust_state": str(device_link.get("trust_state") or "").strip() or None,
            }
        },
    ) or rotated
    rotated["gateway_token"] = fresh_gateway_token
    return {
        "gateway": gateway_registration_public_payload(rotated),
        "gateway_token": str(rotated.get("gateway_token") or ""),
        "scope": gateway_scope_payload(rotated),
    }


def revoke_gateway_registration(
    *,
    gateway_id: str,
    tenant_id: str,
    workspace_id: str,
    user_id: Optional[str] = None,
    reason: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    registration = gateway_state_repository.get_gateway_registration(gateway_id)
    if not registration:
        raise ValueError("Gateway registration was not found.")
    if str(registration.get("tenant_id") or "").strip() != str(tenant_id or "").strip():
        raise ValueError("Gateway registration scope mismatch.")
    if str(registration.get("workspace_id") or "").strip() != str(workspace_id or "").strip():
        raise ValueError("Gateway registration scope mismatch.")
    if user_id and str(registration.get("user_id") or "").strip() != str(user_id or "").strip():
        raise ValueError("Gateway registration scope mismatch.")
    resolved_reason = reason or "Gateway registration revoked."
    revoked = gateway_state_repository.revoke_gateway_registration(
        gateway_id=gateway_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
        reason=reason,
    )
    if not revoked:
        raise ValueError("Gateway registration was not found.")
    auth.revoke_local_gateway_device_link(
        user_id=str(revoked.get("user_id") or "").strip(),
        device_id=str(revoked.get("device_id") or "").strip(),
        reason=resolved_reason,
    )
    return {
        "gateway": gateway_registration_public_payload(revoked),
        "scope": gateway_scope_payload(revoked),
        "mutation_plan": {
            "shutdown_live_connection": True,
            "mark_dedicated_workstation_revoked": True,
            "revocation_reason": resolved_reason,
        },
    }

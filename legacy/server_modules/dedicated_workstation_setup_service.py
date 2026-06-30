from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional

from server_modules import (
    agent_computer_profile_service,
    gateway_registry_service,
    gateway_state_repository,
    kill_switch_gate,
    security_audit_service,
)


DEDICATED_METADATA_KEY = "dedicated_workstation"
DEDICATED_PROFILE_PREFIX = "acp_dedicated_"


class DedicatedWorkstationSetupError(ValueError):
    pass


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _text(value: Any, fallback: str = "") -> str:
    token = str(value or "").strip()
    return token or fallback


def _profile_id_for_gateway(gateway_id: str) -> str:
    suffix = re.sub(r"[^A-Za-z0-9_]+", "_", _text(gateway_id)).strip("_").lower()
    if not suffix:
        raise DedicatedWorkstationSetupError("gateway_id is required.")
    return f"{DEDICATED_PROFILE_PREFIX}{suffix}"


def _metadata(registration: Mapping[str, Any]) -> Dict[str, Any]:
    value = registration.get("metadata")
    return dict(value) if isinstance(value, dict) else {}


def _dedicated_metadata(registration: Mapping[str, Any]) -> Dict[str, Any]:
    value = _metadata(registration).get(DEDICATED_METADATA_KEY)
    return dict(value) if isinstance(value, dict) else {}


def _require_registration(
    *,
    gateway_id: str,
    tenant_id: str = "",
    workspace_id: str = "",
    user_id: str = "",
) -> Dict[str, Any]:
    registration = gateway_state_repository.get_gateway_registration(gateway_id)
    if not registration:
        raise DedicatedWorkstationSetupError("Gateway registration was not found.")
    if tenant_id and _text(registration.get("tenant_id")) != _text(tenant_id):
        raise DedicatedWorkstationSetupError("Gateway registration scope mismatch.")
    if workspace_id and _text(registration.get("workspace_id")) != _text(workspace_id):
        raise DedicatedWorkstationSetupError("Gateway registration scope mismatch.")
    if user_id and _text(registration.get("user_id")) != _text(user_id):
        raise DedicatedWorkstationSetupError("Gateway registration scope mismatch.")
    return registration


def _registration_health(registration: Mapping[str, Any]) -> str:
    public = gateway_registry_service.gateway_registration_public_payload(dict(registration))
    status = _text(public.get("connection_status")).lower()
    trust_state = _text(public.get("device_trust_state") or public.get("status")).lower()
    if trust_state == "revoked" or status == "revoked":
        return agent_computer_profile_service.HEALTH_REVOKED
    if status == "online":
        return agent_computer_profile_service.HEALTH_ONLINE
    if status in {"degraded", "reconnecting"}:
        return agent_computer_profile_service.HEALTH_UNHEALTHY
    if status == "offline":
        return agent_computer_profile_service.HEALTH_OFFLINE
    return agent_computer_profile_service.HEALTH_PENDING


def _profile_payload(
    *,
    registration: Mapping[str, Any],
    profile_id: str,
    policy_id: str,
    machine_label: str,
    health_state: str,
) -> Dict[str, Any]:
    gateway_id = _text(registration.get("gateway_id"))
    return {
        "profile_id": profile_id,
        "workspace_id": _text(registration.get("workspace_id"), "default"),
        "owner_user_id": _text(registration.get("user_id")),
        "policy_id": policy_id,
        "machine_label": machine_label or _text(registration.get("display_name"), "Dedicated computer"),
        "environment_kind": agent_computer_profile_service.ENVIRONMENT_DEDICATED_WORKSTATION,
        "gateway_id": gateway_id,
        "runtime_attachment_id": f"local_companion:{gateway_id}",
        "runtime_profile_id": profile_id,
        "dedicated_to_agent": True,
        "health_state": health_state,
        "last_seen_at": registration.get("last_seen_at") or registration.get("last_heartbeat_at"),
        "recording_policy": agent_computer_profile_service.RECORDING_SESSION_ONLY,
        "filesystem_roots": [],
        "browser_profiles": [],
        "channel_access": [],
    }


def _update_registration_dedicated_metadata(
    *,
    registration: Mapping[str, Any],
    dedicated_payload: Dict[str, Any],
    status: Optional[str] = None,
) -> Dict[str, Any]:
    return gateway_state_repository.update_gateway_registration_state(
        gateway_id=_text(registration.get("gateway_id")),
        metadata={DEDICATED_METADATA_KEY: dedicated_payload},
        status=status,
    ) or dict(registration)


def _emit_audit(
    *,
    action: str,
    status: str,
    registration: Mapping[str, Any],
    profile_id: str = "",
    detail: str = "",
    actor_user_id: str = "",
    trace_id: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    security_audit_service.emit_security_audit_event(
        action=action,
        status=status,
        tenant_id=_text(registration.get("tenant_id"), "default"),
        workspace_id=_text(registration.get("workspace_id"), "default"),
        actor_user_id=actor_user_id or _text(registration.get("user_id")),
        machine_id=_text(registration.get("gateway_id")),
        trace_id=trace_id,
        detail=detail,
        metadata={
            "gateway_id": _text(registration.get("gateway_id")),
            "profile_id": profile_id or None,
            **dict(metadata or {}),
        },
    )


def bind_dedicated_workstation(
    *,
    gateway_id: str,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    policy_id: str,
    machine_label: str = "",
    trace_id: str = "",
) -> Dict[str, Any]:
    if not _text(policy_id):
        raise DedicatedWorkstationSetupError("policy_id is required for dedicated computer binding.")
    registration = _require_registration(
        gateway_id=gateway_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
    )
    if _text(registration.get("status")).lower() == "revoked" or _text(registration.get("device_trust_state")).lower() == "revoked":
        raise DedicatedWorkstationSetupError("Cannot bind a revoked computer as a dedicated workstation.")
    existing_metadata = _dedicated_metadata(registration)
    profile_id = _text(existing_metadata.get("profile_id")) or _profile_id_for_gateway(gateway_id)
    label = machine_label or _text(existing_metadata.get("machine_label")) or _text(registration.get("display_name"), "Dedicated computer")
    profile = agent_computer_profile_service.upsert_agent_computer_profile(
        _profile_payload(
            registration=registration,
            profile_id=profile_id,
            policy_id=policy_id,
            machine_label=label,
            health_state=_registration_health(registration),
        )
    )
    dedicated_payload = {
        **existing_metadata,
        "profile_id": profile.profile_id,
        "policy_id": profile.policy_id,
        "machine_label": profile.machine_label,
        "environment_kind": profile.environment_kind,
        "dedicated_to_agent": True,
        "bound_at": existing_metadata.get("bound_at") or _utc_now_iso(),
        "bound_by_user_id": user_id,
        "status": "bound",
    }
    registration = _update_registration_dedicated_metadata(
        registration=registration,
        dedicated_payload=dedicated_payload,
    )
    gateway_state_repository.record_gateway_event(
        gateway_id=_text(registration.get("gateway_id")),
        session_id=None,
        direction="system",
        frame_kind="event",
        message_type="dedicated_workstation.bound",
        payload={
            "workspace_id": workspace_id,
            "profile_id": profile.profile_id,
            "policy_id": profile.policy_id,
        },
    )
    _emit_audit(
        action="dedicated_workstation.bound",
        status="success",
        registration=registration,
        profile_id=profile.profile_id,
        actor_user_id=user_id,
        trace_id=trace_id,
        detail="Dedicated computer binding created.",
    )
    return dedicated_workstation_readiness(
        gateway_id=gateway_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
        trace_id=trace_id,
    )


def dedicated_workstation_readiness(
    *,
    gateway_id: str,
    tenant_id: str = "",
    workspace_id: str = "",
    user_id: str = "",
    trace_id: str = "",
) -> Dict[str, Any]:
    registration = _require_registration(
        gateway_id=gateway_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
    )
    public_gateway = gateway_registry_service.gateway_registration_public_payload(registration)
    dedicated = _dedicated_metadata(registration)
    profile_id = _text(dedicated.get("profile_id"))
    profile = (
        agent_computer_profile_service.get_agent_computer_profile(
            workspace_id=_text(registration.get("workspace_id"), "default"),
            profile_id=profile_id,
        )
        if profile_id
        else None
    )
    blockers: list[str] = []
    health_state = _registration_health(registration)
    kill_decision = kill_switch_gate.evaluate_kill_switch(
        workspace_id=_text(registration.get("workspace_id"), "default"),
        gateway_id=_text(registration.get("gateway_id")),
        trace_id=trace_id,
    )
    if not profile:
        blockers.append("not_bound")
    elif profile.environment_kind != agent_computer_profile_service.ENVIRONMENT_DEDICATED_WORKSTATION:
        blockers.append("not_dedicated_workstation")
    elif profile.gateway_id != _text(registration.get("gateway_id")):
        blockers.append("gateway_profile_mismatch")
    elif profile.health_state != health_state:
        profile = agent_computer_profile_service.update_agent_computer_profile_health(
            workspace_id=profile.workspace_id,
            profile_id=profile.profile_id,
            health_state=health_state,
            last_seen_at=registration.get("last_seen_at") or registration.get("last_heartbeat_at"),
        )
    if kill_decision.blocked:
        blockers.append("kill_switch_active")
    if _text(public_gateway.get("status")).lower() == "revoked" or _text(public_gateway.get("device_trust_state")).lower() == "revoked":
        blockers.append("revoked")
    if _text(public_gateway.get("connection_status")).lower() != "online":
        blockers.append(f"connection_{_text(public_gateway.get('connection_status'), 'unknown')}")
    if profile and not profile.policy_id:
        blockers.append("policy_missing")

    if "revoked" in blockers:
        readiness = "revoked"
    elif "kill_switch_active" in blockers:
        readiness = "killed"
    elif "not_bound" in blockers:
        readiness = "not_bound"
    elif any(item.startswith("connection_") for item in blockers):
        readiness = "offline"
    elif blockers:
        readiness = "unhealthy"
    else:
        readiness = "ready"

    return {
        "gateway_id": _text(registration.get("gateway_id")),
        "workspace_id": _text(registration.get("workspace_id"), "default"),
        "readiness": readiness,
        "ready": readiness == "ready",
        "blockers": blockers,
        "profile": profile.as_dict() if profile else None,
        "gateway": public_gateway,
        "kill_switch": {
            "active": kill_decision.blocked,
            "reason": kill_decision.reason or None,
            "scope": kill_decision.scope or None,
            "detail": kill_decision.detail or None,
        },
    }


def kill_dedicated_workstation(
    *,
    gateway_id: str,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    reason: str = "",
    trace_id: str = "",
) -> Dict[str, Any]:
    registration = _require_registration(
        gateway_id=gateway_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
    )
    dedicated = _dedicated_metadata(registration)
    if not _text(dedicated.get("profile_id")):
        raise DedicatedWorkstationSetupError("Dedicated workstation is not bound.")
    kill_switch_gate.set_kill_switch(f"{kill_switch_gate.GATEWAY_KILL_PREFIX}{gateway_id}")
    latest_session = gateway_state_repository.get_latest_gateway_session(gateway_id, include_revoked=False)
    if latest_session:
        gateway_state_repository.mark_gateway_session_disconnected(
            _text(latest_session.get("session_id")),
            reason=reason or "dedicated workstation stopped",
        )
    dedicated["kill_state"] = {
        "active": True,
        "reason": reason or "Dedicated workstation stopped.",
        "stopped_at": _utc_now_iso(),
        "stopped_by_user_id": user_id,
    }
    registration = _update_registration_dedicated_metadata(
        registration=registration,
        dedicated_payload=dedicated,
    )
    profile_id = _text(dedicated.get("profile_id"))
    agent_computer_profile_service.update_agent_computer_profile_health(
        workspace_id=workspace_id,
        profile_id=profile_id,
        health_state=agent_computer_profile_service.HEALTH_UNHEALTHY,
    )
    gateway_state_repository.record_gateway_event(
        gateway_id=gateway_id,
        session_id=_text((latest_session or {}).get("session_id")) or None,
        direction="system",
        frame_kind="event",
        message_type="dedicated_workstation.killed",
        payload={"workspace_id": workspace_id, "profile_id": profile_id, "reason": reason or None},
    )
    _emit_audit(
        action="dedicated_workstation.killed",
        status="success",
        registration=registration,
        profile_id=profile_id,
        actor_user_id=user_id,
        trace_id=trace_id,
        detail=reason or "Dedicated workstation stopped.",
    )
    return dedicated_workstation_readiness(
        gateway_id=gateway_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
        trace_id=trace_id,
    )


def clear_dedicated_workstation_kill(
    *,
    gateway_id: str,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    trace_id: str = "",
) -> Dict[str, Any]:
    registration = _require_registration(
        gateway_id=gateway_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
    )
    dedicated = _dedicated_metadata(registration)
    if not _text(dedicated.get("profile_id")):
        raise DedicatedWorkstationSetupError("Dedicated workstation is not bound.")
    kill_switch_gate.clear_kill_switch(f"{kill_switch_gate.GATEWAY_KILL_PREFIX}{gateway_id}")
    dedicated["kill_state"] = {
        "active": False,
        "cleared_at": _utc_now_iso(),
        "cleared_by_user_id": user_id,
    }
    registration = _update_registration_dedicated_metadata(
        registration=registration,
        dedicated_payload=dedicated,
    )
    gateway_state_repository.record_gateway_event(
        gateway_id=gateway_id,
        session_id=None,
        direction="system",
        frame_kind="event",
        message_type="dedicated_workstation.kill_cleared",
        payload={"workspace_id": workspace_id, "profile_id": _text(dedicated.get("profile_id"))},
    )
    _emit_audit(
        action="dedicated_workstation.kill_cleared",
        status="success",
        registration=registration,
        profile_id=_text(dedicated.get("profile_id")),
        actor_user_id=user_id,
        trace_id=trace_id,
        detail="Dedicated workstation stop cleared.",
    )
    return dedicated_workstation_readiness(
        gateway_id=gateway_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
        trace_id=trace_id,
    )


def mark_dedicated_workstation_revoked(
    *,
    gateway_id: str,
    reason: str = "",
    actor_user_id: str = "",
    trace_id: str = "",
) -> Optional[Dict[str, Any]]:
    registration = gateway_state_repository.get_gateway_registration(gateway_id)
    if not registration:
        return None
    dedicated = _dedicated_metadata(registration)
    profile_id = _text(dedicated.get("profile_id"))
    if not profile_id:
        return None
    dedicated["status"] = "revoked"
    dedicated["revoked_at"] = _utc_now_iso()
    dedicated["revoked_reason"] = reason or "Dedicated workstation revoked."
    registration = _update_registration_dedicated_metadata(
        registration=registration,
        dedicated_payload=dedicated,
        status="revoked",
    )
    try:
        agent_computer_profile_service.update_agent_computer_profile_health(
            workspace_id=_text(registration.get("workspace_id"), "default"),
            profile_id=profile_id,
            health_state=agent_computer_profile_service.HEALTH_REVOKED,
        )
    except Exception:
        pass
    gateway_state_repository.record_gateway_event(
        gateway_id=gateway_id,
        session_id=None,
        direction="system",
        frame_kind="event",
        message_type="dedicated_workstation.revoked",
        payload={
            "workspace_id": _text(registration.get("workspace_id"), "default"),
            "profile_id": profile_id,
            "reason": reason or None,
        },
    )
    _emit_audit(
        action="dedicated_workstation.revoked",
        status="success",
        registration=registration,
        profile_id=profile_id,
        actor_user_id=actor_user_id,
        trace_id=trace_id,
        detail=reason or "Dedicated workstation revoked.",
    )
    return dedicated_workstation_readiness(
        gateway_id=gateway_id,
        tenant_id=_text(registration.get("tenant_id")),
        workspace_id=_text(registration.get("workspace_id")),
        user_id=_text(registration.get("user_id")),
        trace_id=trace_id,
    )

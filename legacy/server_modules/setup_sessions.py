from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set

from fastapi import HTTPException
from pydantic import BaseModel

def _init():
    return None


def _utc_now() -> datetime:
    from server_modules import runtime_common

    return runtime_common._utc_now()


def _utc_now_iso() -> str:
    from server_modules import runtime_common

    return runtime_common._utc_now_iso()


def _parse_utc_ts(value: Any) -> Optional[datetime]:
    from server_modules import runtime_common

    return runtime_common._parse_utc_ts(value)


def _normalize_workspace_id(value: Any) -> str:
    from server_modules import runtime_common

    return runtime_common._normalize_workspace_id(value)


def _shared_state():
    from server_modules import shared

    return shared


def _runtime_config():
    from server_modules import runtime_config

    return runtime_config


def _provider_catalog():
    from server_modules import provider_profiles

    return provider_profiles.PROVIDER_CATALOG

_SETUP_ACTIONS: Set[str] = {
    "select_provider",
    "submit_credential",
    "verify",
    "complete",
    "cancel",
    "resume",
    "gateway_location",
    "remote_gateway",
    "sections",
    "onboarding_mode",
    "config_handling",
    "reset_scope",
    "workspace",
    "provider_auth_choice",
    "credential_handling",
    "model_filter",
    "model_override",
    "model_override_skipped",
    "web_tools",
    "gateway_config",
    "daemon",
    "channel_choice",
    "connector_added",
    "skills",
    "health_check",
    "risk_ack",
}

_CONFIG_ACTIONS: Set[str] = {
    "gateway_location",
    "remote_gateway",
    "sections",
    "onboarding_mode",
    "config_handling",
    "reset_scope",
    "workspace",
    "provider_auth_choice",
    "credential_handling",
    "model_filter",
    "model_override",
    "model_override_skipped",
    "web_tools",
    "gateway_config",
    "daemon",
    "channel_choice",
    "connector_added",
    "skills",
    "health_check",
}


class SetupSessionCreateRequest(BaseModel):
    workspace_id: Optional[str] = None
    provider: Optional[str] = None
    flow: Optional[str] = None

class SetupSessionActionRequest(BaseModel):
    action: str
    payload: Optional[Dict[str, Any]] = None

    def validate_fields(self):
        act = str(self.action or "").strip().lower()
        if act not in _SETUP_ACTIONS:
            raise HTTPException(status_code=400, detail=f"Invalid or unknown setup action '{act}'.")

# --- COPIED LOGIC ---
def _cleanup_setup_sessions_locked(now: Optional[datetime] = None):
    ref = now or _utc_now()
    for session in _shared_state().SETUP_SESSIONS.values():
        if not isinstance(session, dict):
            continue
        if str(session.get("state") or "") in {"complete", "cancelled", "expired"}:
            continue
        expires_at = _parse_utc_ts(session.get("expires_at"))
        if expires_at is not None and expires_at <= ref:
            session["state"] = "expired"
            session["updated_at"] = _utc_now_iso()
            session["events"] = (session.get("events") if isinstance(session.get("events"), list) else []) + [{
                "ts": _utc_now_iso(),
                "action": "expire",
                "state": "expired",
            }]


def _persist_setup_sessions():
    shared = _shared_state()
    with shared.SETUP_SESSIONS_LOCK:
        _cleanup_setup_sessions_locked()
    shared.sync_acp_manager_paths(setup_sessions_path=_runtime_config().ORION_SETUP_SESSIONS_FILE)
    shared.ACP_MANAGER._persist_setup_sessions()


def _load_setup_sessions():
    shared = _shared_state()
    with shared.SETUP_SESSIONS_LOCK:
        shared.sync_acp_manager_paths(setup_sessions_path=_runtime_config().ORION_SETUP_SESSIONS_FILE)
        shared.ACP_MANAGER.reload_secondary_state()
        _cleanup_setup_sessions_locked()


def _setup_session_event(session: Dict[str, Any], action: str):
    _init()
    events = session.get("events") if isinstance(session.get("events"), list) else []
    events.append(
        {
            "ts": _utc_now_iso(),
            "action": action,
            "state": session.get("state"),
        }
    )
    if len(events) > 100:
        events = events[-100:]
    session["events"] = events


def _session_config(session: Dict[str, Any]) -> Dict[str, Any]:
    config = session.get("config")
    return config if isinstance(config, dict) else {}


def _risk_acknowledged(session: Dict[str, Any]) -> bool:
    risk_payload = _session_config(session).get("risk_ack")
    if isinstance(risk_payload, dict):
        return bool(risk_payload.get("accepted"))
    return False


def _derive_next_step(session: Dict[str, Any]) -> str:
    state = str(session.get("state") or "init")
    config = _session_config(session)

    if state == "complete":
        return "done"
    if state == "cancelled":
        return "resume"
    if state == "expired":
        return "resume"
    if state == "failed":
        return "verify_or_auth"
    if state == "provider_selected":
        return "credential"
    if state == "credential_captured":
        return "verify"

    if not _risk_acknowledged(session):
        return "risk_ack"
    if "gateway_location" not in config:
        return "gateway_location"
    if "sections" not in config:
        return "sections"
    if "provider_auth_choice" not in config and not str(session.get("provider") or "").strip():
        return "provider_auth_choice"
    if str(session.get("provider") or "").strip() and not str(session.get("credential_id") or "").strip():
        return "credential"
    if str(session.get("credential_id") or "").strip() and state not in {"verified"}:
        return "verify"
    if "channel_choice" not in config:
        return "channels"
    return "complete"


def _allowed_actions_for_state(state: str) -> List[str]:
    state_norm = str(state or "init").strip().lower() or "init"

    if state_norm in {"complete"}:
        return ["resume"]
    if state_norm in {"cancelled"}:
        return ["resume"]
    if state_norm in {"expired"}:
        return ["resume", "cancel"]
    if state_norm in {"failed"}:
        return ["resume", "cancel", "verify", "submit_credential", "credential_handling", "provider_auth_choice"]
    if state_norm == "provider_selected":
        return ["submit_credential", "credential_handling", "cancel", "resume", "verify"]
    if state_norm == "credential_captured":
        return ["verify", "submit_credential", "cancel", "resume"]
    if state_norm == "verified":
        return sorted({"complete", "cancel", "resume"} | _CONFIG_ACTIONS)
    return sorted({"select_provider", "complete", "cancel", "resume"} | _CONFIG_ACTIONS)


def _setup_state_transition(session: Dict[str, Any], action: str, payload: Optional[Dict[str, Any]] = None):
    payload = payload if isinstance(payload, dict) else {}
    state = str(session.get("state") or "init")
    now_iso = _utc_now_iso()
    ttl = max(300, _runtime_config().ORION_SETUP_SESSION_TTL_SECONDS)
    expires_at = (_utc_now() + timedelta(seconds=ttl)).isoformat().replace("+00:00", "Z")

    if state in {"cancelled", "complete"} and action not in {"resume"}:
        raise HTTPException(status_code=409, detail=f"Cannot '{action}' from terminal setup state '{state}'.")
    if state == "expired" and action not in {"resume", "cancel"}:
        raise HTTPException(status_code=409, detail="Setup session expired. Resume or create a new session.")

    if action == "resume":
        if state in {"cancelled", "expired", "failed"}:
            session["state"] = "init"
            session["provider"] = None
            session["credential_id"] = None
            session["last_error"] = None
        session["updated_at"] = now_iso
        session["expires_at"] = expires_at
        _setup_session_event(session, "resume")
        return

    if action == "cancel":
        session["state"] = "cancelled"
        session["updated_at"] = now_iso
        session["expires_at"] = expires_at
        _setup_session_event(session, "cancel")
        return

    if action == "select_provider":
        provider = str(payload.get("provider") or "").strip().lower()
        if provider not in _provider_catalog():
            raise HTTPException(status_code=400, detail="A valid provider is required.")
        session["provider"] = provider
        session["state"] = "provider_selected"
        session["updated_at"] = now_iso
        session["expires_at"] = expires_at
        _setup_session_event(session, "select_provider")
        return

    if action == "submit_credential":
        credential_id = str(payload.get("credential_id") or "").strip()
        if not credential_id:
            raise HTTPException(status_code=400, detail="credential_id is required.")
        session["credential_id"] = credential_id
        session["state"] = "credential_captured"
        session["updated_at"] = now_iso
        session["expires_at"] = expires_at
        _setup_session_event(session, "submit_credential")
        return

    if action == "risk_ack":
        accepted = bool(payload.get("accepted"))
        source = str(payload.get("source") or "unknown").strip() or "unknown"
        config = _session_config(session)
        config["risk_ack"] = {
            "accepted": accepted,
            "source": source,
            "ts": now_iso,
        }
        session["config"] = config
        if accepted:
            if state not in {"verified", "complete"}:
                session["state"] = "configuring"
            session["last_error"] = None
        else:
            session["state"] = "cancelled"
            session["last_error"] = "risk_not_acknowledged"
        session["updated_at"] = now_iso
        session["expires_at"] = expires_at
        _setup_session_event(session, "risk_ack")
        return

    if action == "verify":
        ok = bool(payload.get("ok"))
        session["state"] = "verified" if ok else "failed"
        if not ok:
            session["last_error"] = str(payload.get("error") or "Verification failed.")
        else:
            session["last_error"] = None
        session["updated_at"] = now_iso
        session["expires_at"] = expires_at
        _setup_session_event(session, "verify")
        return

    if action in _CONFIG_ACTIONS:
        config = _session_config(session)
        if action == "connector_added":
            added = config.get("connectors_added") if isinstance(config.get("connectors_added"), list) else []
            added.append(payload)
            config["connectors_added"] = added[-25:]
        else:
            config[action] = payload
        session["config"] = config
        if state not in {"verified", "complete"}:
            session["state"] = "configuring"
        session["updated_at"] = now_iso
        session["expires_at"] = expires_at
        _setup_session_event(session, action)
        return

    if action == "complete":
        session["state"] = "complete"
        session["updated_at"] = now_iso
        session["expires_at"] = expires_at
        _setup_session_event(session, "complete")
        return

    raise HTTPException(status_code=400, detail=f"Unsupported setup action '{action}'.")

# --- COPIED ENDPOINTS ---
def _serialize_setup_session(item: Dict[str, Any]) -> Dict[str, Any]:
    state = str(item.get("state") or "init")
    config = _session_config(item)
    next_step = _derive_next_step(item)
    safety = {
        "risk_acknowledged": _risk_acknowledged(item),
    }
    return {
        "id": item.get("id"),
        "workspace_id": item.get("workspace_id"),
        "state": state,
        "flow": item.get("flow") or "quickstart",
        "next_step": next_step,
        "allowed_actions": _allowed_actions_for_state(state),
        "safety": safety,
        "provider": item.get("provider"),
        "credential_id": item.get("credential_id"),
        "last_error": item.get("last_error"),
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
        "expires_at": item.get("expires_at"),
        "config": config,
        "events": item.get("events", []),
    }


async def handle_create_setup_session(body: Optional[SetupSessionCreateRequest] = None):
    payload = body or SetupSessionCreateRequest()
    now = _utc_now_iso()
    ttl = max(300, _runtime_config().ORION_SETUP_SESSION_TTL_SECONDS)
    expires_at = (_utc_now() + timedelta(seconds=ttl)).isoformat().replace("+00:00", "Z")
    workspace_id = _normalize_workspace_id(payload.workspace_id) or "default"
    flow = str(payload.flow or "quickstart").strip().lower() or "quickstart"
    if flow not in {"quickstart", "manual", "configure"}:
        flow = "quickstart"
    session_id = str(uuid.uuid4())
    session = {
        "id": session_id,
        "workspace_id": workspace_id,
        "flow": flow,
        "state": "init",
        "provider": None,
        "credential_id": None,
        "config": {},
        "last_error": None,
        "created_at": now,
        "updated_at": now,
        "expires_at": expires_at,
        "events": [],
    }
    shared = _shared_state()
    with shared.SETUP_SESSIONS_LOCK:
        shared.SETUP_SESSIONS[session_id] = session
        if payload.provider:
            _setup_state_transition(session, "select_provider", {"provider": payload.provider})
            shared.SETUP_SESSIONS[session_id] = session
    _persist_setup_sessions()
    return {"session": _serialize_setup_session(session)}


async def handle_get_setup_session(session_id: str):
    shared = _shared_state()
    with shared.SETUP_SESSIONS_LOCK:
        _cleanup_setup_sessions_locked()
        session = shared.SETUP_SESSIONS.get(session_id)
        if not isinstance(session, dict):
            raise HTTPException(status_code=404, detail="Setup session not found.")
    return {"session": _serialize_setup_session(session)}


async def handle_setup_session_action(session_id: str, body: SetupSessionActionRequest):
    body.validate_fields()
    action = str(body.action).strip().lower()
    payload = dict(body.payload or {})
    shared = _shared_state()
    with shared.SETUP_SESSIONS_LOCK:
        _cleanup_setup_sessions_locked()
        session = shared.SETUP_SESSIONS.get(session_id)
        if not isinstance(session, dict):
            raise HTTPException(status_code=404, detail="Setup session not found.")
        _setup_state_transition(session, action, payload)
        shared.SETUP_SESSIONS[session_id] = session
    _persist_setup_sessions()
    return {"session": _serialize_setup_session(session)}


async def handle_cancel_setup_session(session_id: str):
    shared = _shared_state()
    with shared.SETUP_SESSIONS_LOCK:
        session = shared.SETUP_SESSIONS.get(session_id)
        if not isinstance(session, dict):
            raise HTTPException(status_code=404, detail="Setup session not found.")
        _setup_state_transition(session, "cancel", {})
        shared.SETUP_SESSIONS[session_id] = session
    _persist_setup_sessions()
    return {"session": _serialize_setup_session(session)}


async def handle_resume_setup_session(session_id: str):
    shared = _shared_state()
    with shared.SETUP_SESSIONS_LOCK:
        session = shared.SETUP_SESSIONS.get(session_id)
        if not isinstance(session, dict):
            raise HTTPException(status_code=404, detail="Setup session not found.")
        _setup_state_transition(session, "resume", {})
        shared.SETUP_SESSIONS[session_id] = session
    _persist_setup_sessions()
    return {"session": _serialize_setup_session(session)}


# Onboarding aliases keep backward compatibility while allowing step-driven clients
# to use explicit onboarding endpoints.
async def handle_create_onboarding_session(body: Optional[SetupSessionCreateRequest] = None):
    return await handle_create_setup_session(body)


async def handle_get_onboarding_session(session_id: str):
    return await handle_get_setup_session(session_id)


async def handle_onboarding_session_action(session_id: str, body: SetupSessionActionRequest):
    return await handle_setup_session_action(session_id, body)


async def handle_cancel_onboarding_session(session_id: str):
    return await handle_cancel_setup_session(session_id)


async def handle_resume_onboarding_session(session_id: str):
    return await handle_resume_setup_session(session_id)

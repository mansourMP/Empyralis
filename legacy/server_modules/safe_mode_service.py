from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List

from server_modules import control_plane_repository, rust_runtime_kernel_client

# Cross-reference: kill_switch_gate is still the lightweight top-level
# emergency gate for gateway/personal-channel dispatch. This service owns the
# richer scoped safe-mode, incident-control, and durable kill-switch model.

_UNSAFE_PREFIXES = ("computer_control", "browser_automation")
_UNSAFE_EXACT = {"shell.execute"}
_LOCK = threading.Lock()
_DURABLE_SCOPE_TYPES = {"workspace", "agent", "channel", "connector"}
_DURABLE_CACHE_TTL_SECONDS = 2.0


@dataclass(slots=True)
class SafeModeState:
    enabled: bool
    reason: str = ""
    blocked_capabilities: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class KillSwitchState:
    enabled: bool
    reason: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class IncidentControlState:
    mode: str
    reason: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


_GLOBAL_SAFE_MODE = SafeModeState(enabled=False)
_TENANT_SAFE_MODE: Dict[str, SafeModeState] = {}
_WORKSPACE_SAFE_MODE: Dict[str, SafeModeState] = {}
_MACHINE_SAFE_MODE: Dict[str, SafeModeState] = {}

_GLOBAL_KILL_SWITCH = KillSwitchState(enabled=False)
_TENANT_KILL_SWITCHES: Dict[str, KillSwitchState] = {}
_WORKSPACE_KILL_SWITCHES: Dict[str, KillSwitchState] = {}
_MACHINE_KILL_SWITCHES: Dict[str, KillSwitchState] = {}
_CAPABILITY_KILL_SWITCHES: Dict[str, KillSwitchState] = {}
_AGENT_KILL_SWITCHES: Dict[str, KillSwitchState] = {}
_CHANNEL_KILL_SWITCHES: Dict[str, KillSwitchState] = {}
_CONNECTOR_KILL_SWITCHES: Dict[str, KillSwitchState] = {}
_WORKSPACE_INCIDENT_CONTROLS: Dict[str, IncidentControlState] = {}
_CHANNEL_INCIDENT_CONTROLS: Dict[str, IncidentControlState] = {}
_CONNECTOR_INCIDENT_CONTROLS: Dict[str, IncidentControlState] = {}

_DURABLE_CONTROL_CACHE: Dict[str, Dict[str, Any]] = {}


class SafeModeRustGateError(RuntimeError):
    pass


def _normalize_token(value: Any) -> str:
    return str(value or "").strip().lower()


def _safe_mode_blocked_capabilities() -> list[str]:
    return [
        "computer_control",
        "computer_control.*",
        "browser_automation.interactive",
        "browser_automation.*",
        "shell.execute",
    ]


def _clone_safe_mode(state: SafeModeState) -> Dict[str, Any]:
    return {
        "enabled": bool(state.enabled),
        "reason": str(state.reason or ""),
        "blocked_capabilities": list(state.blocked_capabilities or []),
        "metadata": dict(state.metadata or {}),
    }


def _clone_kill_switch(state: KillSwitchState) -> Dict[str, Any]:
    return {
        "enabled": bool(state.enabled),
        "reason": str(state.reason or ""),
        "metadata": dict(state.metadata or {}),
    }


def _normalize_incident_mode(value: Any) -> str:
    token = _normalize_token(value)
    aliases = {
        "paused": "pause",
        "draining": "drain",
        "rejected": "reject",
    }
    token = aliases.get(token, token)
    return token if token in {"active", "pause", "drain", "reject"} else "active"


def _incident_mode_status(mode: str) -> str:
    normalized = _normalize_incident_mode(mode)
    return {
        "pause": "paused",
        "drain": "draining",
        "reject": "rejected",
    }.get(normalized, "active")


def _clone_incident_control(state: IncidentControlState) -> Dict[str, Any]:
    normalized_mode = _normalize_incident_mode(state.mode)
    return {
        "active": normalized_mode != "active",
        "mode": normalized_mode,
        "reason": str(state.reason or ""),
        "metadata": dict(state.metadata or {}),
    }


def _is_unsafe_capability(capability_id: str) -> bool:
    token = _normalize_token(capability_id)
    if token in _UNSAFE_EXACT:
        return True
    return any(token == prefix or token.startswith(f"{prefix}.") for prefix in _UNSAFE_PREFIXES)


def _scoped_key(*parts: Any) -> str:
    return "::".join(_normalize_token(part) for part in parts if _normalize_token(part))


def _agent_key(workspace_id: Any, agent_install_id: Any) -> str:
    return _scoped_key(workspace_id, agent_install_id)


def _channel_key(workspace_id: Any, channel_key: Any, endpoint_key: Any = None) -> str:
    return _scoped_key(workspace_id, channel_key, endpoint_key)


def _connector_key(workspace_id: Any, connector_id: Any, credential_id: Any = None) -> str:
    return _scoped_key(workspace_id, connector_id, credential_id)


def _workspace_cache_key(tenant_id: Any, workspace_id: Any) -> str:
    return _scoped_key(tenant_id, workspace_id)


def _run_coro_sync(coro: Any) -> Any:
    try:
        return asyncio.run(coro)
    except RuntimeError as exc:
        if "asyncio.run() cannot be called from a running event loop" not in str(exc):
            close = getattr(coro, "close", None)
            if callable(close):
                close()
            raise

    result: Dict[str, Any] = {}
    failure: Dict[str, BaseException] = {}

    def _runner() -> None:
        try:
            result["value"] = asyncio.run(coro)
        except BaseException as err:  # pragma: no cover
            failure["error"] = err

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()
    if "error" in failure:
        raise failure["error"]
    return result.get("value")


def _normalize_control_row(value: Any) -> Dict[str, Any]:
    row = dict(value or {}) if isinstance(value, dict) else {}
    row["scope_type"] = _normalize_token(row.get("scope_type"))
    row["control_kind"] = _normalize_token(row.get("control_kind"))
    row["scope_key"] = _normalize_token(row.get("scope_key"))
    row["channel_key"] = _normalize_token(row.get("channel_key"))
    row["endpoint_key"] = _normalize_token(row.get("endpoint_key"))
    row["connector_id"] = _normalize_token(row.get("connector_id"))
    row["credential_id"] = str(row.get("credential_id") or "").strip()
    row["agent_install_id"] = str(row.get("agent_install_id") or "").strip()
    row["reason"] = str(row.get("reason") or "")
    row["status"] = _normalize_token(row.get("status"))
    row["enabled"] = bool(row.get("enabled"))
    row["metadata"] = dict(row.get("metadata") or {}) if isinstance(row.get("metadata"), dict) else {}
    return row


def _invalidate_durable_cache(*, tenant_id: str | None = None, workspace_id: str | None = None) -> None:
    cache_key = _workspace_cache_key(tenant_id, workspace_id)
    if not cache_key:
        return
    with _LOCK:
        _DURABLE_CONTROL_CACHE.pop(cache_key, None)


def _load_durable_workspace_controls(
    *,
    tenant_id: str | None = None,
    workspace_id: str | None = None,
) -> List[Dict[str, Any]]:
    tenant_token = _normalize_token(tenant_id)
    workspace_token = _normalize_token(workspace_id)
    if not tenant_token or not workspace_token:
        return []
    cache_key = _workspace_cache_key(tenant_token, workspace_token)
    now = time.monotonic()
    with _LOCK:
        cached = _DURABLE_CONTROL_CACHE.get(cache_key)
        if isinstance(cached, dict) and float(cached.get("loaded_at") or 0.0) + _DURABLE_CACHE_TTL_SECONDS >= now:
            return [dict(item) for item in list(cached.get("items") or [])]
    try:
        rows = _run_coro_sync(
            control_plane_repository.list_security_control_states(
                tenant_id=tenant_token,
                workspace_id=workspace_token,
                scope_types=["workspace", "agent", "channel", "connector"],
                active_only=True,
            )
        )
    except Exception:
        return []
    normalized = [_normalize_control_row(row) for row in list(rows or []) if isinstance(row, dict)]
    with _LOCK:
        _DURABLE_CONTROL_CACHE[cache_key] = {
            "loaded_at": now,
            "items": [dict(item) for item in normalized],
        }
    return normalized


def _actor_user_id(metadata: Dict[str, Any]) -> str | None:
    for key in ("requested_by_user_id", "actor_user_id", "user_id"):
        token = str(metadata.get(key) or "").strip()
        if token:
            return token
    return None


def _write_action(*, enabled: bool, metadata: Dict[str, Any]) -> str:
    status = _normalize_token(metadata.get("status"))
    if status in {"revoked", "rotated"}:
        return status
    return "enabled" if enabled else "disabled"


def _enforce_security_control_state_decision(
    *,
    operation: str,
    scope: str,
    enabled: bool,
    tenant_id: str | None = None,
    workspace_id: str | None = None,
    reason: str = "",
    metadata: Dict[str, Any] | None = None,
    control_kind: str = "safe_mode",
    scope_key: str = "",
) -> Dict[str, Any]:
    payload = {
        "scope": _normalize_token(scope),
        "scope_key": str(scope_key or "").strip(),
        "enabled": bool(enabled),
        "tenant_id": _normalize_token(tenant_id),
        "workspace_id": _normalize_token(workspace_id),
        "reason": str(reason or "").strip(),
        "control_kind": _normalize_token(control_kind),
        "metadata": dict(metadata or {}),
    }
    try:
        decision = rust_runtime_kernel_client.runtime_state_store_decision(
            operation=operation,
            state_class="security_control_state",
            tenant_id=payload["tenant_id"],
            workspace_id=payload["workspace_id"],
            actor_id=_actor_user_id(payload["metadata"]) or "system",
            status="active" if enabled else "inactive",
            payload=payload,
            payload_bytes=len(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")),
            workspace_access=True,
            owner_access=True,
        )
        rust_runtime_kernel_client.enforce_kernel_decision(
            "runtime-state-store-decision",
            decision,
        )
        next_action = _normalize_token(decision.get("next_action"))
        if next_action != _normalize_token(operation):
            raise SafeModeRustGateError("unexpected_next_action")
        return decision
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        raise SafeModeRustGateError(exc.reason) from exc


def _persist_security_control_state(
    *,
    scope: str,
    enabled: bool,
    tenant_id: str | None = None,
    workspace_id: str | None = None,
    reason: str = "",
    metadata: Dict[str, Any] | None = None,
    agent_install_id: str | None = None,
    channel_key: str | None = None,
    endpoint_key: str | None = None,
    connector_id: str | None = None,
    credential_id: str | None = None,
    control_kind: str = "kill_switch",
) -> Dict[str, Any] | None:
    scope_token = _normalize_token(scope)
    tenant_token = _normalize_token(tenant_id)
    workspace_token = _normalize_token(workspace_id)
    if scope_token not in _DURABLE_SCOPE_TYPES:
        return None
    if not tenant_token or not workspace_token:
        return None
    metadata_payload = dict(metadata or {})
    if scope_token == "workspace":
        scope_key = "workspace"
    elif scope_token == "agent":
        scope_key = str(agent_install_id or "").strip()
    elif scope_token == "channel":
        scope_key = _scoped_key(channel_key, endpoint_key)
    else:
        scope_key = _scoped_key(connector_id, credential_id)
    if not scope_key:
        return None
    _enforce_security_control_state_decision(
        operation="upsert_security_control_state",
        scope=scope_token,
        enabled=enabled,
        tenant_id=tenant_token,
        workspace_id=workspace_token,
        reason=reason,
        metadata=metadata_payload,
        control_kind=control_kind,
        scope_key=scope_key,
    )
    try:
        row = _run_coro_sync(
            control_plane_repository.upsert_security_control_state(
                tenant_id=tenant_token,
                workspace_id=workspace_token,
                scope_type=scope_token,
                control_kind=control_kind,
                scope_key=scope_key,
                enabled=bool(enabled),
                status=str(metadata_payload.get("status") or ("active" if enabled else "inactive")).strip().lower() or ("active" if enabled else "inactive"),
                reason=str(reason or "").strip(),
                metadata=metadata_payload,
                agent_install_id=str(agent_install_id or "").strip() or None,
                channel_key=str(channel_key or "").strip().lower() or None,
                endpoint_key=str(endpoint_key or "").strip().lower() or None,
                connector_id=str(connector_id or "").strip().lower() or None,
                credential_id=str(credential_id or "").strip() or None,
                created_by_user_id=_actor_user_id(metadata_payload),
                action=_write_action(enabled=bool(enabled), metadata=metadata_payload),
            )
        )
    except Exception:
        return None
    _invalidate_durable_cache(tenant_id=tenant_token, workspace_id=workspace_token)
    return row if isinstance(row, dict) else None


def _load_matching_durable_controls(
    *,
    tenant_id: str | None = None,
    workspace_id: str | None = None,
    control_kind: str,
    scope_type: str,
) -> List[Dict[str, Any]]:
    return [
        row
        for row in _load_durable_workspace_controls(tenant_id=tenant_id, workspace_id=workspace_id)
        if _normalize_token(row.get("control_kind")) == _normalize_token(control_kind)
        and _normalize_token(row.get("scope_type")) == _normalize_token(scope_type)
        and bool(row.get("enabled"))
    ]


def set_safe_mode(
    *,
    enabled: bool,
    reason: str = "",
    tenant_id: str | None = None,
    workspace_id: str | None = None,
    machine_id: str | None = None,
    metadata: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    state = SafeModeState(
        enabled=bool(enabled),
        reason=str(reason or ""),
        blocked_capabilities=_safe_mode_blocked_capabilities() if enabled else [],
        metadata=dict(metadata or {}),
    )
    tenant_token = _normalize_token(tenant_id)
    workspace_token = _normalize_token(workspace_id)
    machine_token = _normalize_token(machine_id)
    if tenant_token:
        scope = "tenant"
        scope_key = tenant_token
    elif workspace_token:
        scope = "workspace"
        scope_key = workspace_token
    elif machine_token:
        scope = "machine"
        scope_key = machine_token
    else:
        scope = "global"
        scope_key = "global"
    _enforce_security_control_state_decision(
        operation="set_safe_mode_state",
        scope=scope,
        enabled=state.enabled,
        tenant_id=tenant_token,
        workspace_id=workspace_token,
        reason=state.reason,
        metadata=state.metadata,
        control_kind="safe_mode",
        scope_key=scope_key,
    )
    with _LOCK:
        if tenant_token:
            if state.enabled:
                _TENANT_SAFE_MODE[tenant_token] = state
            else:
                _TENANT_SAFE_MODE.pop(tenant_token, None)
            return {"scope": "tenant", "tenant_id": tenant_token, "state": _clone_safe_mode(state)}
        if workspace_token:
            if state.enabled:
                _WORKSPACE_SAFE_MODE[workspace_token] = state
            else:
                _WORKSPACE_SAFE_MODE.pop(workspace_token, None)
        elif machine_token:
            if state.enabled:
                _MACHINE_SAFE_MODE[machine_token] = state
            else:
                _MACHINE_SAFE_MODE.pop(machine_token, None)
            return {"scope": "machine", "machine_id": machine_token, "state": _clone_safe_mode(state)}
        else:
            _GLOBAL_SAFE_MODE.enabled = state.enabled
            _GLOBAL_SAFE_MODE.reason = state.reason
            _GLOBAL_SAFE_MODE.blocked_capabilities = list(state.blocked_capabilities)
            _GLOBAL_SAFE_MODE.metadata = dict(state.metadata)
            return {"scope": "global", "state": _clone_safe_mode(_GLOBAL_SAFE_MODE)}

    _persist_security_control_state(
        scope="workspace",
        enabled=bool(enabled),
        tenant_id=tenant_token or None,
        workspace_id=workspace_token or None,
        reason=str(reason or ""),
        metadata={
            **dict(metadata or {}),
            "blocked_capabilities": _safe_mode_blocked_capabilities() if enabled else [],
        },
        control_kind="safe_mode",
    )
    return {"scope": "workspace", "workspace_id": workspace_token, "state": _clone_safe_mode(state)}


def set_kill_switch(
    *,
    scope: str,
    enabled: bool,
    reason: str = "",
    tenant_id: str | None = None,
    workspace_id: str | None = None,
    machine_id: str | None = None,
    capability_id: str | None = None,
    agent_install_id: str | None = None,
    channel_key: str | None = None,
    endpoint_key: str | None = None,
    connector_id: str | None = None,
    credential_id: str | None = None,
    metadata: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    clean_scope = _normalize_token(scope) or "global"
    state = KillSwitchState(enabled=bool(enabled), reason=str(reason or ""), metadata=dict(metadata or {}))
    tenant_token = _normalize_token(tenant_id)
    workspace_token = _normalize_token(workspace_id)
    machine_token = _normalize_token(machine_id)
    capability_token = _normalize_token(capability_id)
    agent_token = _normalize_token(agent_install_id)
    channel_token = _normalize_token(channel_key)
    endpoint_token = _normalize_token(endpoint_key)
    connector_token = _normalize_token(connector_id)
    credential_token = str(credential_id or "").strip()
    with _LOCK:
        if clean_scope == "tenant":
            if not tenant_token:
                raise ValueError("tenant_id is required for tenant kill switches.")
            if state.enabled:
                _TENANT_KILL_SWITCHES[tenant_token] = state
            else:
                _TENANT_KILL_SWITCHES.pop(tenant_token, None)
            return {"scope": "tenant", "tenant_id": tenant_token, "state": _clone_kill_switch(state)}
        if clean_scope == "workspace":
            if not workspace_token:
                raise ValueError("workspace_id is required for workspace kill switches.")
            if state.enabled:
                _WORKSPACE_KILL_SWITCHES[workspace_token] = state
            else:
                _WORKSPACE_KILL_SWITCHES.pop(workspace_token, None)
        elif clean_scope == "machine":
            if not machine_token:
                raise ValueError("machine_id is required for machine kill switches.")
            if state.enabled:
                _MACHINE_KILL_SWITCHES[machine_token] = state
            else:
                _MACHINE_KILL_SWITCHES.pop(machine_token, None)
            return {"scope": "machine", "machine_id": machine_token, "state": _clone_kill_switch(state)}
        elif clean_scope == "capability":
            if not capability_token:
                raise ValueError("capability_id is required for capability kill switches.")
            if state.enabled:
                _CAPABILITY_KILL_SWITCHES[capability_token] = state
            else:
                _CAPABILITY_KILL_SWITCHES.pop(capability_token, None)
            return {"scope": "capability", "capability_id": capability_token, "state": _clone_kill_switch(state)}
        elif clean_scope == "agent":
            if not workspace_token or not agent_token:
                raise ValueError("workspace_id and agent_install_id are required for agent kill switches.")
            key = _agent_key(workspace_token, agent_token)
            if state.enabled:
                _AGENT_KILL_SWITCHES[key] = state
            else:
                _AGENT_KILL_SWITCHES.pop(key, None)
        elif clean_scope == "channel":
            if not workspace_token or not channel_token:
                raise ValueError("workspace_id and channel_key are required for channel kill switches.")
            key = _channel_key(workspace_token, channel_token, endpoint_token)
            if state.enabled:
                _CHANNEL_KILL_SWITCHES[key] = state
            else:
                _CHANNEL_KILL_SWITCHES.pop(key, None)
        elif clean_scope == "connector":
            if not workspace_token or not connector_token:
                raise ValueError("workspace_id and connector_id are required for connector kill switches.")
            key = _connector_key(workspace_token, connector_token, credential_token)
            if state.enabled:
                _CONNECTOR_KILL_SWITCHES[key] = state
            else:
                _CONNECTOR_KILL_SWITCHES.pop(key, None)
        else:
            _GLOBAL_KILL_SWITCH.enabled = state.enabled
            _GLOBAL_KILL_SWITCH.reason = state.reason
            _GLOBAL_KILL_SWITCH.metadata = dict(state.metadata)
            return {"scope": "global", "state": _clone_kill_switch(_GLOBAL_KILL_SWITCH)}

    durable_row = _persist_security_control_state(
        scope=clean_scope,
        enabled=bool(enabled),
        tenant_id=tenant_token or None,
        workspace_id=workspace_token or None,
        reason=str(reason or ""),
        metadata=dict(metadata or {}),
        agent_install_id=agent_token or None,
        channel_key=channel_token or None,
        endpoint_key=endpoint_token or None,
        connector_id=connector_token or None,
        credential_id=credential_token or None,
        control_kind="kill_switch",
    )
    response = {"scope": clean_scope, "state": _clone_kill_switch(state)}
    if clean_scope == "workspace":
        response["workspace_id"] = workspace_token
    elif clean_scope == "agent":
        response["workspace_id"] = workspace_token
        response["agent_install_id"] = agent_token
    elif clean_scope == "channel":
        response["workspace_id"] = workspace_token
        response["channel_key"] = channel_token
        response["endpoint_key"] = endpoint_token or None
    elif clean_scope == "connector":
        response["workspace_id"] = workspace_token
        response["connector_id"] = connector_token
        response["credential_id"] = credential_token or None
    if isinstance(durable_row, dict):
        response["durable"] = True
        response["control_state_id"] = durable_row.get("id")
    return response


def set_incident_control(
    *,
    scope: str,
    mode: str,
    tenant_id: str | None = None,
    workspace_id: str | None = None,
    reason: str = "",
    channel_key: str | None = None,
    endpoint_key: str | None = None,
    connector_id: str | None = None,
    credential_id: str | None = None,
    metadata: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    clean_scope = _normalize_token(scope)
    if clean_scope not in {"workspace", "channel", "connector"}:
        raise ValueError("Incident controls currently support workspace, channel, and connector scopes only.")
    workspace_token = _normalize_token(workspace_id)
    if not workspace_token:
        raise ValueError("workspace_id is required for incident controls.")
    tenant_token = _normalize_token(tenant_id)
    channel_token = _normalize_token(channel_key)
    endpoint_token = _normalize_token(endpoint_key)
    connector_token = _normalize_token(connector_id)
    credential_token = str(credential_id or "").strip()
    normalized_mode = _normalize_incident_mode(mode)
    state = IncidentControlState(
        mode=normalized_mode,
        reason=str(reason or ""),
        metadata=dict(metadata or {}),
    )
    with _LOCK:
        if clean_scope == "workspace":
            if normalized_mode == "active":
                _WORKSPACE_INCIDENT_CONTROLS.pop(workspace_token, None)
            else:
                _WORKSPACE_INCIDENT_CONTROLS[workspace_token] = state
        elif clean_scope == "channel":
            if not channel_token:
                raise ValueError("channel_key is required for channel incident controls.")
            key = _channel_key(workspace_token, channel_token, endpoint_token)
            if normalized_mode == "active":
                _CHANNEL_INCIDENT_CONTROLS.pop(key, None)
            else:
                _CHANNEL_INCIDENT_CONTROLS[key] = state
        else:
            if not connector_token:
                raise ValueError("connector_id is required for connector incident controls.")
            key = _connector_key(workspace_token, connector_token, credential_token)
            if normalized_mode == "active":
                _CONNECTOR_INCIDENT_CONTROLS.pop(key, None)
            else:
                _CONNECTOR_INCIDENT_CONTROLS[key] = state

    durable_row = _persist_security_control_state(
        scope=clean_scope,
        enabled=normalized_mode != "active",
        tenant_id=tenant_token or None,
        workspace_id=workspace_token,
        reason=str(reason or ""),
        metadata={
            **dict(metadata or {}),
            "mode": normalized_mode,
            "status": _incident_mode_status(normalized_mode),
        },
        channel_key=channel_token or None,
        endpoint_key=endpoint_token or None,
        connector_id=connector_token or None,
        credential_id=credential_token or None,
        control_kind="incident_control",
    )
    response = {
        "scope": clean_scope,
        "mode": normalized_mode,
        "active": normalized_mode != "active",
        "state": _clone_incident_control(state),
        "workspace_id": workspace_token,
    }
    if clean_scope == "channel":
        response["channel_key"] = channel_token
        response["endpoint_key"] = endpoint_token or None
    elif clean_scope == "connector":
        response["connector_id"] = connector_token
        response["credential_id"] = credential_token or None
    if isinstance(durable_row, dict):
        response["durable"] = True
        response["control_state_id"] = durable_row.get("id")
    return response


def resolve_capability_disable_state(
    capability_id: str,
    *,
    tenant_id: str | None = None,
    workspace_id: str | None = None,
    machine_id: str | None = None,
) -> Dict[str, Any]:
    token = _normalize_token(capability_id)
    if not token:
        return {"disabled": False, "reason": "", "scope": None, "matched_chain": []}
    tenant_token = _normalize_token(tenant_id)
    workspace_token = _normalize_token(workspace_id)
    machine_token = _normalize_token(machine_id)
    matched_chain: list[dict[str, Any]] = []

    def _record(source_type: str, scope: str, state: Any, *, extra: Dict[str, Any] | None = None) -> None:
        entry = {
            "type": source_type,
            "scope": scope,
            "reason": str(getattr(state, "reason", "") or ""),
            "metadata": dict(getattr(state, "metadata", {}) or {}),
        }
        if extra:
            entry.update(extra)
        matched_chain.append(entry)

    with _LOCK:
        if bool(_GLOBAL_KILL_SWITCH.enabled):
            _record("kill_switch", "global", _GLOBAL_KILL_SWITCH)
        if bool(_GLOBAL_SAFE_MODE.enabled) and _is_unsafe_capability(token):
            _record("safe_mode", "global", _GLOBAL_SAFE_MODE, extra={"capability_id": token})
        tenant_switch = _TENANT_KILL_SWITCHES.get(tenant_token) if tenant_token else None
        if isinstance(tenant_switch, KillSwitchState) and tenant_switch.enabled:
            _record("kill_switch", "tenant", tenant_switch, extra={"tenant_id": tenant_token})
        tenant_safe = _TENANT_SAFE_MODE.get(tenant_token) if tenant_token else None
        if isinstance(tenant_safe, SafeModeState) and tenant_safe.enabled and _is_unsafe_capability(token):
            _record("safe_mode", "tenant", tenant_safe, extra={"tenant_id": tenant_token, "capability_id": token})
        workspace_switch = _WORKSPACE_KILL_SWITCHES.get(workspace_token) if workspace_token else None
        if isinstance(workspace_switch, KillSwitchState) and workspace_switch.enabled:
            _record("kill_switch", "workspace", workspace_switch, extra={"workspace_id": workspace_token})
        workspace_safe = _WORKSPACE_SAFE_MODE.get(workspace_token) if workspace_token else None
        if isinstance(workspace_safe, SafeModeState) and workspace_safe.enabled and _is_unsafe_capability(token):
            _record("safe_mode", "workspace", workspace_safe, extra={"workspace_id": workspace_token, "capability_id": token})
        machine_switch = _MACHINE_KILL_SWITCHES.get(machine_token) if machine_token else None
        if isinstance(machine_switch, KillSwitchState) and machine_switch.enabled:
            _record("kill_switch", "machine", machine_switch, extra={"machine_id": machine_token})
        machine_safe = _MACHINE_SAFE_MODE.get(machine_token) if machine_token else None
        if isinstance(machine_safe, SafeModeState) and machine_safe.enabled and _is_unsafe_capability(token):
            _record("safe_mode", "machine", machine_safe, extra={"machine_id": machine_token, "capability_id": token})
        capability_switch = _CAPABILITY_KILL_SWITCHES.get(token)
        if isinstance(capability_switch, KillSwitchState) and capability_switch.enabled:
            _record("kill_switch", "capability", capability_switch, extra={"capability_id": token})

    if _is_unsafe_capability(token):
        for row in _load_matching_durable_controls(
            tenant_id=tenant_token or None,
            workspace_id=workspace_token or None,
            control_kind="safe_mode",
            scope_type="workspace",
        ):
            matched_chain.append(
                {
                    "type": "safe_mode",
                    "scope": "workspace",
                    "reason": str(row.get("reason") or ""),
                    "metadata": dict(row.get("metadata") or {}),
                    "workspace_id": workspace_token,
                    "capability_id": token,
                }
            )
    for row in _load_matching_durable_controls(
        tenant_id=tenant_token or None,
        workspace_id=workspace_token or None,
        control_kind="kill_switch",
        scope_type="workspace",
    ):
        matched_chain.append(
            {
                "type": "kill_switch",
                "scope": "workspace",
                "reason": str(row.get("reason") or ""),
                "metadata": dict(row.get("metadata") or {}),
                "workspace_id": workspace_token,
            }
        )

    if not matched_chain:
        return {"disabled": False, "reason": "", "scope": None, "matched_chain": []}
    matched = matched_chain[-1]
    rust_payload = {
        "capability": token,
        "capability_id": token,
        "safe_mode_enabled": any(item.get("type") == "safe_mode" for item in matched_chain),
        "kill_switch_enabled": any(item.get("type") == "kill_switch" for item in matched_chain),
        "tenant_id": tenant_token or None,
        "workspace_id": workspace_token or None,
        "machine_id": machine_token or None,
        "matched_chain": matched_chain,
    }

    def _expected_next_action(decision_value: Any) -> str:
        normalized = _normalize_token(decision_value)
        if normalized == "allow":
            return "allow_safe_mode_capability"
        if normalized == "degrade":
            return "degrade_safe_mode_capability"
        if normalized == "block":
            return "disable_safe_mode_capability"
        return ""

    def _unexpected_next_action_response(decision: Dict[str, Any], expected: str) -> Dict[str, Any]:
        rust_scope = str(decision.get("control_scope") or matched.get("scope") or "rust_kernel").strip() or "rust_kernel"
        rust_type = str(decision.get("control_type") or matched.get("type") or "safe_mode").strip() or "safe_mode"
        return {
            "disabled": True,
            "reason": f"unexpected_next_action:{expected}",
            "scope": rust_scope,
            "type": rust_type,
            "matched_chain": matched_chain,
            "rust_decision": dict(decision),
        }

    try:
        rust_decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
            "safe-mode-decision",
            rust_payload,
        )
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        expected_action = _expected_next_action(exc.decision.get("decision"))
        next_action = str(exc.decision.get("next_action") or "").strip()
        if expected_action and next_action != expected_action:
            return _unexpected_next_action_response(exc.decision, expected_action)
        rust_scope = str(exc.decision.get("control_scope") or matched.get("scope") or "rust_kernel").strip() or "rust_kernel"
        rust_type = str(exc.decision.get("control_type") or matched.get("type") or "safe_mode").strip() or "safe_mode"
        rust_reason = str(exc.decision.get("control_reason") or matched.get("reason") or exc.reason or "").strip()
        return {
            "disabled": True,
            "reason": rust_reason,
            "scope": rust_scope,
            "type": rust_type,
            "matched_chain": matched_chain,
            "rust_decision": dict(exc.decision),
        }
    expected_action = _expected_next_action(rust_decision.get("decision"))
    next_action = str(rust_decision.get("next_action") or "").strip()
    if expected_action and next_action != expected_action:
        return _unexpected_next_action_response(rust_decision, expected_action)
    if rust_decision.get("decision") != "block":
        return {
            "disabled": False,
            "reason": "",
            "scope": None,
            "matched_chain": matched_chain,
            "rust_decision": dict(rust_decision),
        }
    rust_scope = str(rust_decision.get("control_scope") or matched.get("scope") or "rust_kernel").strip() or "rust_kernel"
    rust_type = str(rust_decision.get("control_type") or matched.get("type") or "safe_mode").strip() or "safe_mode"
    rust_reason = str(rust_decision.get("control_reason") or matched.get("reason") or "").strip()
    return {
        "disabled": True,
        "reason": rust_reason,
        "scope": rust_scope,
        "type": rust_type,
        "matched_chain": matched_chain,
        "rust_decision": dict(rust_decision),
    }


def is_capability_disabled(
    capability_id: str,
    *,
    tenant_id: str | None = None,
    workspace_id: str | None = None,
    machine_id: str | None = None,
) -> bool:
    return bool(
        resolve_capability_disable_state(
            capability_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            machine_id=machine_id,
        ).get("disabled")
    )


def resolve_machine_policy_status(
    *,
    tenant_id: str | None = None,
    workspace_id: str | None = None,
    machine_id: str | None = None,
    capability_ids: List[str] | None = None,
) -> Dict[str, Any]:
    tenant_token = _normalize_token(tenant_id)
    workspace_token = _normalize_token(workspace_id)
    machine_token = _normalize_token(machine_id)
    capability_tokens = [_normalize_token(item) for item in (capability_ids or []) if _normalize_token(item)]

    safe_chain: list[dict[str, Any]] = []
    kill_chain: list[dict[str, Any]] = []

    def _push(chain: list[dict[str, Any]], state_type: str, scope: str, state: Any, *, extra: Dict[str, Any] | None = None) -> None:
        entry = {
            "type": state_type,
            "scope": scope,
            "reason": str(getattr(state, "reason", "") or ""),
            "metadata": dict(getattr(state, "metadata", {}) or {}),
        }
        if extra:
            entry.update(extra)
        chain.append(entry)

    with _LOCK:
        if bool(_GLOBAL_SAFE_MODE.enabled):
            _push(safe_chain, "safe_mode", "global", _GLOBAL_SAFE_MODE)
        if bool(_GLOBAL_KILL_SWITCH.enabled):
            _push(kill_chain, "kill_switch", "global", _GLOBAL_KILL_SWITCH)

        tenant_safe = _TENANT_SAFE_MODE.get(tenant_token) if tenant_token else None
        if isinstance(tenant_safe, SafeModeState) and tenant_safe.enabled:
            _push(safe_chain, "safe_mode", "tenant", tenant_safe, extra={"tenant_id": tenant_token})
        tenant_kill = _TENANT_KILL_SWITCHES.get(tenant_token) if tenant_token else None
        if isinstance(tenant_kill, KillSwitchState) and tenant_kill.enabled:
            _push(kill_chain, "kill_switch", "tenant", tenant_kill, extra={"tenant_id": tenant_token})

        workspace_safe = _WORKSPACE_SAFE_MODE.get(workspace_token) if workspace_token else None
        if isinstance(workspace_safe, SafeModeState) and workspace_safe.enabled:
            _push(safe_chain, "safe_mode", "workspace", workspace_safe, extra={"workspace_id": workspace_token})
        workspace_kill = _WORKSPACE_KILL_SWITCHES.get(workspace_token) if workspace_token else None
        if isinstance(workspace_kill, KillSwitchState) and workspace_kill.enabled:
            _push(kill_chain, "kill_switch", "workspace", workspace_kill, extra={"workspace_id": workspace_token})

        machine_safe = _MACHINE_SAFE_MODE.get(machine_token) if machine_token else None
        if isinstance(machine_safe, SafeModeState) and machine_safe.enabled:
            _push(safe_chain, "safe_mode", "machine", machine_safe, extra={"machine_id": machine_token})
        machine_kill = _MACHINE_KILL_SWITCHES.get(machine_token) if machine_token else None
        if isinstance(machine_kill, KillSwitchState) and machine_kill.enabled:
            _push(kill_chain, "kill_switch", "machine", machine_kill, extra={"machine_id": machine_token})

        for capability_token in capability_tokens:
            capability_kill = _CAPABILITY_KILL_SWITCHES.get(capability_token)
            if isinstance(capability_kill, KillSwitchState) and capability_kill.enabled:
                _push(
                    kill_chain,
                    "kill_switch",
                    "capability",
                    capability_kill,
                    extra={"capability_id": capability_token},
                )

    for row in _load_matching_durable_controls(
        tenant_id=tenant_token or None,
        workspace_id=workspace_token or None,
        control_kind="safe_mode",
        scope_type="workspace",
    ):
        safe_chain.append(
            {
                "type": "safe_mode",
                "scope": "workspace",
                "reason": str(row.get("reason") or ""),
                "metadata": dict(row.get("metadata") or {}),
                "workspace_id": workspace_token,
            }
        )
    for row in _load_matching_durable_controls(
        tenant_id=tenant_token or None,
        workspace_id=workspace_token or None,
        control_kind="kill_switch",
        scope_type="workspace",
    ):
        kill_chain.append(
            {
                "type": "kill_switch",
                "scope": "workspace",
                "reason": str(row.get("reason") or ""),
                "metadata": dict(row.get("metadata") or {}),
                "workspace_id": workspace_token,
            }
        )

    def _summarize(chain: list[dict[str, Any]]) -> Dict[str, Any]:
        if not chain:
            return {"active": False, "scope": None, "reason": "", "matched_chain": []}
        matched = chain[-1]
        return {
            "active": True,
            "scope": matched.get("scope"),
            "reason": str(matched.get("reason") or ""),
            "matched_chain": chain,
        }

    return {
        "safe_mode": _summarize(safe_chain),
        "kill_switch": _summarize(kill_chain),
    }


def _base_kill_chain(*, tenant_id: str | None = None, workspace_id: str | None = None) -> list[dict[str, Any]]:
    tenant_token = _normalize_token(tenant_id)
    workspace_token = _normalize_token(workspace_id)
    chain: list[dict[str, Any]] = []

    def _push(scope: str, state: Any, *, extra: Dict[str, Any] | None = None) -> None:
        entry = {
            "type": "kill_switch",
            "scope": scope,
            "reason": str(getattr(state, "reason", "") or ""),
            "metadata": dict(getattr(state, "metadata", {}) or {}),
        }
        if extra:
            entry.update(extra)
        chain.append(entry)

    with _LOCK:
        if bool(_GLOBAL_KILL_SWITCH.enabled):
            _push("global", _GLOBAL_KILL_SWITCH)
        tenant_state = _TENANT_KILL_SWITCHES.get(tenant_token) if tenant_token else None
        if isinstance(tenant_state, KillSwitchState) and tenant_state.enabled:
            _push("tenant", tenant_state, extra={"tenant_id": tenant_token})
        workspace_state = _WORKSPACE_KILL_SWITCHES.get(workspace_token) if workspace_token else None
        if isinstance(workspace_state, KillSwitchState) and workspace_state.enabled:
            _push("workspace", workspace_state, extra={"workspace_id": workspace_token})

    for row in _load_matching_durable_controls(
        tenant_id=tenant_token or None,
        workspace_id=workspace_token or None,
        control_kind="kill_switch",
        scope_type="workspace",
    ):
        chain.append(
            {
                "type": "kill_switch",
                "scope": "workspace",
                "reason": str(row.get("reason") or ""),
                "metadata": dict(row.get("metadata") or {}),
                "workspace_id": workspace_token,
            }
        )
    return chain


def _summarize_kill_chain(chain: list[dict[str, Any]]) -> Dict[str, Any]:
    if not chain:
        return {"active": False, "reason": "", "scope": None, "matched_chain": []}
    matched = chain[-1]
    return {
        "active": True,
        "reason": str(matched.get("reason") or ""),
        "scope": matched.get("scope"),
        "matched_chain": chain,
    }


def _base_incident_chain(*, tenant_id: str | None = None, workspace_id: str | None = None) -> list[dict[str, Any]]:
    tenant_token = _normalize_token(tenant_id)
    workspace_token = _normalize_token(workspace_id)
    chain: list[dict[str, Any]] = []
    if workspace_token:
        with _LOCK:
            state = _WORKSPACE_INCIDENT_CONTROLS.get(workspace_token)
            if isinstance(state, IncidentControlState) and _normalize_incident_mode(state.mode) != "active":
                chain.append(
                    {
                        "type": "incident_control",
                        "scope": "workspace",
                        "mode": _normalize_incident_mode(state.mode),
                        "reason": str(state.reason or ""),
                        "metadata": dict(state.metadata or {}),
                        "workspace_id": workspace_token,
                    }
                )
        for row in _load_matching_durable_controls(
            tenant_id=tenant_token or None,
            workspace_id=workspace_token,
            control_kind="incident_control",
            scope_type="workspace",
        ):
            mode = _normalize_incident_mode(row.get("status") or row.get("metadata", {}).get("mode"))
            if mode == "active":
                continue
            chain.append(
                {
                    "type": "incident_control",
                    "scope": "workspace",
                    "mode": mode,
                    "reason": str(row.get("reason") or ""),
                    "metadata": dict(row.get("metadata") or {}),
                    "workspace_id": workspace_token,
                }
            )
    return chain


def _summarize_incident_chain(chain: list[dict[str, Any]]) -> Dict[str, Any]:
    if not chain:
        return {"active": False, "mode": "active", "reason": "", "scope": None, "matched_chain": []}
    matched = chain[-1]
    return {
        "active": True,
        "mode": _normalize_incident_mode(matched.get("mode")),
        "reason": str(matched.get("reason") or ""),
        "scope": matched.get("scope"),
        "matched_chain": chain,
    }


def resolve_workspace_incident_state(
    *,
    tenant_id: str | None = None,
    workspace_id: str | None = None,
) -> Dict[str, Any]:
    return _summarize_incident_chain(_base_incident_chain(tenant_id=tenant_id, workspace_id=workspace_id))


def resolve_channel_incident_state(
    *,
    tenant_id: str | None = None,
    workspace_id: str | None = None,
    channel_key: str,
    endpoint_key: str | None = None,
) -> Dict[str, Any]:
    workspace_token = _normalize_token(workspace_id)
    channel_token = _normalize_token(channel_key)
    endpoint_token = _normalize_token(endpoint_key)
    chain = _base_incident_chain(tenant_id=tenant_id, workspace_id=workspace_id)
    if workspace_token and channel_token:
        with _LOCK:
            scoped_states = [
                _CHANNEL_INCIDENT_CONTROLS.get(_channel_key(workspace_token, channel_token)),
                _CHANNEL_INCIDENT_CONTROLS.get(_channel_key(workspace_token, channel_token, endpoint_token)) if endpoint_token else None,
            ]
            for state in scoped_states:
                if not isinstance(state, IncidentControlState):
                    continue
                mode = _normalize_incident_mode(state.mode)
                if mode == "active":
                    continue
                chain.append(
                    {
                        "type": "incident_control",
                        "scope": "channel",
                        "mode": mode,
                        "reason": str(state.reason or ""),
                        "metadata": dict(state.metadata or {}),
                        "workspace_id": workspace_token,
                        "channel_key": channel_token,
                        "endpoint_key": endpoint_token or None,
                    }
                )
        for row in _load_matching_durable_controls(
            tenant_id=tenant_id,
            workspace_id=workspace_token,
            control_kind="incident_control",
            scope_type="channel",
        ):
            durable_channel = _normalize_token(row.get("channel_key"))
            durable_endpoint = _normalize_token(row.get("endpoint_key"))
            if durable_channel != channel_token:
                continue
            if durable_endpoint and durable_endpoint != endpoint_token:
                continue
            mode = _normalize_incident_mode(row.get("status") or row.get("metadata", {}).get("mode"))
            if mode == "active":
                continue
            chain.append(
                {
                    "type": "incident_control",
                    "scope": "channel",
                    "mode": mode,
                    "reason": str(row.get("reason") or ""),
                    "metadata": dict(row.get("metadata") or {}),
                    "workspace_id": workspace_token,
                    "channel_key": channel_token,
                    "endpoint_key": durable_endpoint or endpoint_token or None,
                }
            )
    return _summarize_incident_chain(chain)


def resolve_connector_incident_state(
    *,
    tenant_id: str | None = None,
    workspace_id: str | None = None,
    connector_id: str,
    credential_id: str | None = None,
) -> Dict[str, Any]:
    workspace_token = _normalize_token(workspace_id)
    connector_token = _normalize_token(connector_id)
    credential_token = str(credential_id or "").strip()
    chain = _base_incident_chain(tenant_id=tenant_id, workspace_id=workspace_id)
    if workspace_token and connector_token:
        with _LOCK:
            scoped_states = [
                _CONNECTOR_INCIDENT_CONTROLS.get(_connector_key(workspace_token, connector_token)),
                _CONNECTOR_INCIDENT_CONTROLS.get(_connector_key(workspace_token, connector_token, credential_token)) if credential_token else None,
            ]
            for state in scoped_states:
                if not isinstance(state, IncidentControlState):
                    continue
                mode = _normalize_incident_mode(state.mode)
                if mode == "active":
                    continue
                chain.append(
                    {
                        "type": "incident_control",
                        "scope": "connector",
                        "mode": mode,
                        "reason": str(state.reason or ""),
                        "metadata": dict(state.metadata or {}),
                        "workspace_id": workspace_token,
                        "connector_id": connector_token,
                        "credential_id": credential_token or None,
                    }
                )
        for row in _load_matching_durable_controls(
            tenant_id=tenant_id,
            workspace_id=workspace_token,
            control_kind="incident_control",
            scope_type="connector",
        ):
            durable_connector = _normalize_token(row.get("connector_id"))
            durable_credential = str(row.get("credential_id") or "").strip()
            if durable_connector != connector_token:
                continue
            if durable_credential and durable_credential != credential_token:
                continue
            mode = _normalize_incident_mode(row.get("status") or row.get("metadata", {}).get("mode"))
            if mode == "active":
                continue
            chain.append(
                {
                    "type": "incident_control",
                    "scope": "connector",
                    "mode": mode,
                    "reason": str(row.get("reason") or ""),
                    "metadata": dict(row.get("metadata") or {}),
                    "workspace_id": workspace_token,
                    "connector_id": connector_token,
                    "credential_id": durable_credential or credential_token or None,
                }
            )
    return _summarize_incident_chain(chain)


def resolve_agent_disable_state(
    *,
    tenant_id: str | None = None,
    workspace_id: str | None = None,
    agent_install_id: str | None = None,
) -> Dict[str, Any]:
    workspace_token = _normalize_token(workspace_id)
    agent_token = str(agent_install_id or "").strip()
    chain = _base_kill_chain(tenant_id=tenant_id, workspace_id=workspace_id)
    if workspace_token and agent_token:
        with _LOCK:
            state = _AGENT_KILL_SWITCHES.get(_agent_key(workspace_token, agent_token))
            if isinstance(state, KillSwitchState) and state.enabled:
                chain.append(
                    {
                        "type": "kill_switch",
                        "scope": "agent",
                        "reason": str(state.reason or ""),
                        "metadata": dict(state.metadata or {}),
                        "workspace_id": workspace_token,
                        "agent_install_id": agent_token,
                    }
                )
        for row in _load_matching_durable_controls(
            tenant_id=tenant_id,
            workspace_id=workspace_token,
            control_kind="kill_switch",
            scope_type="agent",
        ):
            if str(row.get("agent_install_id") or "").strip() != agent_token:
                continue
            chain.append(
                {
                    "type": "kill_switch",
                    "scope": "agent",
                    "reason": str(row.get("reason") or ""),
                    "metadata": dict(row.get("metadata") or {}),
                    "workspace_id": workspace_token,
                    "agent_install_id": agent_token,
                }
            )
    return _summarize_kill_chain(chain)


def is_agent_disabled(
    *,
    tenant_id: str | None = None,
    workspace_id: str | None = None,
    agent_install_id: str | None = None,
) -> bool:
    return bool(
        resolve_agent_disable_state(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            agent_install_id=agent_install_id,
        ).get("active")
    )


def resolve_channel_disable_state(
    *,
    tenant_id: str | None = None,
    workspace_id: str | None = None,
    channel_key: str,
    endpoint_key: str | None = None,
) -> Dict[str, Any]:
    workspace_token = _normalize_token(workspace_id)
    channel_token = _normalize_token(channel_key)
    endpoint_token = _normalize_token(endpoint_key)
    chain = _base_kill_chain(tenant_id=tenant_id, workspace_id=workspace_id)
    if workspace_token and channel_token:
        with _LOCK:
            scoped_states = [
                _CHANNEL_KILL_SWITCHES.get(_channel_key(workspace_token, channel_token)),
                _CHANNEL_KILL_SWITCHES.get(_channel_key(workspace_token, channel_token, endpoint_token)) if endpoint_token else None,
            ]
            for state in scoped_states:
                if isinstance(state, KillSwitchState) and state.enabled:
                    chain.append(
                        {
                            "type": "kill_switch",
                            "scope": "channel",
                            "reason": str(state.reason or ""),
                            "metadata": dict(state.metadata or {}),
                            "workspace_id": workspace_token,
                            "channel_key": channel_token,
                            "endpoint_key": endpoint_token or None,
                        }
                    )
        for row in _load_matching_durable_controls(
            tenant_id=tenant_id,
            workspace_id=workspace_token,
            control_kind="kill_switch",
            scope_type="channel",
        ):
            durable_channel = _normalize_token(row.get("channel_key"))
            durable_endpoint = _normalize_token(row.get("endpoint_key"))
            if durable_channel != channel_token:
                continue
            if durable_endpoint and durable_endpoint != endpoint_token:
                continue
            chain.append(
                {
                    "type": "kill_switch",
                    "scope": "channel",
                    "reason": str(row.get("reason") or ""),
                    "metadata": dict(row.get("metadata") or {}),
                    "workspace_id": workspace_token,
                    "channel_key": channel_token,
                    "endpoint_key": durable_endpoint or endpoint_token or None,
                }
            )
    return _summarize_kill_chain(chain)


def is_channel_disabled(
    *,
    tenant_id: str | None = None,
    workspace_id: str | None = None,
    channel_key: str,
    endpoint_key: str | None = None,
) -> bool:
    return bool(
        resolve_channel_disable_state(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            channel_key=channel_key,
            endpoint_key=endpoint_key,
        ).get("active")
    )


def resolve_connector_disable_state(
    *,
    tenant_id: str | None = None,
    workspace_id: str | None = None,
    connector_id: str,
    credential_id: str | None = None,
) -> Dict[str, Any]:
    workspace_token = _normalize_token(workspace_id)
    connector_token = _normalize_token(connector_id)
    credential_token = str(credential_id or "").strip()
    chain = _base_kill_chain(tenant_id=tenant_id, workspace_id=workspace_id)
    if workspace_token and connector_token:
        with _LOCK:
            scoped_states = [
                _CONNECTOR_KILL_SWITCHES.get(_connector_key(workspace_token, connector_token)),
                _CONNECTOR_KILL_SWITCHES.get(_connector_key(workspace_token, connector_token, credential_token)) if credential_token else None,
            ]
            for state in scoped_states:
                if isinstance(state, KillSwitchState) and state.enabled:
                    chain.append(
                        {
                            "type": "kill_switch",
                            "scope": "connector",
                            "reason": str(state.reason or ""),
                            "metadata": dict(state.metadata or {}),
                            "workspace_id": workspace_token,
                            "connector_id": connector_token,
                            "credential_id": credential_token or None,
                        }
                    )
        for row in _load_matching_durable_controls(
            tenant_id=tenant_id,
            workspace_id=workspace_token,
            control_kind="kill_switch",
            scope_type="connector",
        ):
            durable_connector = _normalize_token(row.get("connector_id"))
            durable_credential = str(row.get("credential_id") or "").strip()
            if durable_connector != connector_token:
                continue
            if durable_credential and durable_credential != credential_token:
                continue
            chain.append(
                {
                    "type": "kill_switch",
                    "scope": "connector",
                    "reason": str(row.get("reason") or ""),
                    "metadata": dict(row.get("metadata") or {}),
                    "workspace_id": workspace_token,
                    "connector_id": connector_token,
                    "credential_id": durable_credential or credential_token or None,
                }
            )
    return _summarize_kill_chain(chain)


def is_connector_disabled(
    *,
    tenant_id: str | None = None,
    workspace_id: str | None = None,
    connector_id: str,
    credential_id: str | None = None,
) -> bool:
    return bool(
        resolve_connector_disable_state(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            connector_id=connector_id,
            credential_id=credential_id,
        ).get("active")
    )


def revoke_connector_access(
    *,
    tenant_id: str | None = None,
    workspace_id: str,
    connector_id: str,
    credential_id: str | None = None,
    reason: str = "",
    metadata: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    payload = dict(metadata or {})
    payload.update(
        {
            "status": "revoked",
            "requires_rotation": True,
            "connector_id": _normalize_token(connector_id),
            "credential_id": str(credential_id or "").strip(),
        }
    )
    result = set_kill_switch(
        scope="connector",
        enabled=True,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        connector_id=connector_id,
        credential_id=credential_id,
        reason=str(reason or "Compromised connector access revoked.").strip() or "Compromised connector access revoked.",
        metadata=payload,
    )
    result["action"] = "revoked"
    return result


def rotate_connector_access(
    *,
    tenant_id: str | None = None,
    workspace_id: str,
    connector_id: str,
    previous_credential_id: str | None = None,
    replacement_credential_id: str | None = None,
    reason: str = "",
    metadata: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    payload = dict(metadata or {})
    payload.update(
        {
            "status": "rotated",
            "requires_rotation": False,
            "connector_id": _normalize_token(connector_id),
            "credential_id": str(previous_credential_id or "").strip(),
            "rotated_to_credential_id": str(replacement_credential_id or "").strip(),
        }
    )
    result = set_kill_switch(
        scope="connector",
        enabled=True,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        connector_id=connector_id,
        credential_id=previous_credential_id,
        reason=str(reason or "Compromised connector credential rotated out of service.").strip() or "Compromised connector credential rotated out of service.",
        metadata=payload,
    )
    result["action"] = "rotated"
    result["replacement_credential_id"] = str(replacement_credential_id or "").strip()
    return result


def resolve_operator_control_report(
    *,
    tenant_id: str | None = None,
    workspace_id: str | None = None,
    agent_install_id: str | None = None,
    machine_id: str | None = None,
    gateway_id: str | None = None,
    channel_key: str | None = None,
    endpoint_key: str | None = None,
    capability_id: str | None = None,
    connector_id: str | None = None,
    credential_id: str | None = None,
    trace_id: str = "",
) -> Dict[str, Any]:
    """Return the combined operator view across both policy systems."""
    machine_token = _normalize_token(machine_id or gateway_id)
    try:
        from server_modules import kill_switch_gate

        top_level_decision = kill_switch_gate.evaluate_kill_switch(
            tenant_id=tenant_id or "",
            workspace_id=workspace_id or "",
            agent_id=str(agent_install_id or "").strip(),
            gateway_id=machine_token,
            trace_id=trace_id,
        )
        top_level_gate = {
            "blocked": bool(top_level_decision.blocked),
            "reason": top_level_decision.reason,
            "scope": top_level_decision.scope,
            "detail": top_level_decision.detail,
            "trace_id": top_level_decision.trace_id,
            "restart": kill_switch_gate.get_kill_switch_restart_info(),
        }
    except Exception:
        top_level_gate = {
            "blocked": False,
            "reason": "",
            "scope": "",
            "detail": "",
            "trace_id": trace_id,
            "restart": {},
        }

    report: Dict[str, Any] = {
        "top_level_emergency_gate": top_level_gate,
        "safe_mode_service": {
            "machine_policy": resolve_machine_policy_status(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                machine_id=machine_token or None,
                capability_ids=[capability_id] if capability_id else None,
            ),
            "workspace_incident": resolve_workspace_incident_state(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
            ),
        },
    }
    if agent_install_id:
        report["safe_mode_service"]["agent"] = resolve_agent_disable_state(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            agent_install_id=agent_install_id,
        )
    if channel_key:
        report["safe_mode_service"]["channel"] = {
            "kill_switch": resolve_channel_disable_state(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                channel_key=channel_key,
                endpoint_key=endpoint_key,
            ),
            "incident": resolve_channel_incident_state(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                channel_key=channel_key,
                endpoint_key=endpoint_key,
            ),
        }
    if capability_id:
        report["safe_mode_service"]["capability"] = resolve_capability_disable_state(
            capability_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            machine_id=machine_token or None,
        )
    if connector_id:
        report["safe_mode_service"]["connector"] = {
            "kill_switch": resolve_connector_disable_state(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                connector_id=connector_id,
                credential_id=credential_id,
            ),
            "incident": resolve_connector_incident_state(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                connector_id=connector_id,
                credential_id=credential_id,
            ),
        }
    return report


def state_snapshot() -> Dict[str, Any]:
    with _LOCK:
        durable_cache = {
            key: {"loaded_at": value.get("loaded_at"), "count": len(list(value.get("items") or []))}
            for key, value in _DURABLE_CONTROL_CACHE.items()
        }
        return {
            "safe_mode": {
                "global": _clone_safe_mode(_GLOBAL_SAFE_MODE),
                "tenant": {key: _clone_safe_mode(value) for key, value in _TENANT_SAFE_MODE.items()},
                "workspace": {key: _clone_safe_mode(value) for key, value in _WORKSPACE_SAFE_MODE.items()},
                "machine": {key: _clone_safe_mode(value) for key, value in _MACHINE_SAFE_MODE.items()},
            },
            "kill_switches": {
                "global": _clone_kill_switch(_GLOBAL_KILL_SWITCH),
                "tenant": {key: _clone_kill_switch(value) for key, value in _TENANT_KILL_SWITCHES.items()},
                "workspace": {key: _clone_kill_switch(value) for key, value in _WORKSPACE_KILL_SWITCHES.items()},
                "machine": {key: _clone_kill_switch(value) for key, value in _MACHINE_KILL_SWITCHES.items()},
                "capability": {key: _clone_kill_switch(value) for key, value in _CAPABILITY_KILL_SWITCHES.items()},
                "agent": {key: _clone_kill_switch(value) for key, value in _AGENT_KILL_SWITCHES.items()},
                "channel": {key: _clone_kill_switch(value) for key, value in _CHANNEL_KILL_SWITCHES.items()},
                "connector": {key: _clone_kill_switch(value) for key, value in _CONNECTOR_KILL_SWITCHES.items()},
            },
            "incident_controls": {
                "workspace": {key: _clone_incident_control(value) for key, value in _WORKSPACE_INCIDENT_CONTROLS.items()},
                "channel": {key: _clone_incident_control(value) for key, value in _CHANNEL_INCIDENT_CONTROLS.items()},
                "connector": {key: _clone_incident_control(value) for key, value in _CONNECTOR_INCIDENT_CONTROLS.items()},
            },
            "durable_cache": durable_cache,
        }


def reset_state_for_tests(*, clear_durable: bool = True) -> None:
    with _LOCK:
        _GLOBAL_SAFE_MODE.enabled = False
        _GLOBAL_SAFE_MODE.reason = ""
        _GLOBAL_SAFE_MODE.blocked_capabilities = []
        _GLOBAL_SAFE_MODE.metadata = {}
        _TENANT_SAFE_MODE.clear()
        _WORKSPACE_SAFE_MODE.clear()
        _MACHINE_SAFE_MODE.clear()
        _GLOBAL_KILL_SWITCH.enabled = False
        _GLOBAL_KILL_SWITCH.reason = ""
        _GLOBAL_KILL_SWITCH.metadata = {}
        _TENANT_KILL_SWITCHES.clear()
        _WORKSPACE_KILL_SWITCHES.clear()
        _MACHINE_KILL_SWITCHES.clear()
        _CAPABILITY_KILL_SWITCHES.clear()
        _AGENT_KILL_SWITCHES.clear()
        _CHANNEL_KILL_SWITCHES.clear()
        _CONNECTOR_KILL_SWITCHES.clear()
        _WORKSPACE_INCIDENT_CONTROLS.clear()
        _CHANNEL_INCIDENT_CONTROLS.clear()
        _CONNECTOR_INCIDENT_CONTROLS.clear()
        _DURABLE_CONTROL_CACHE.clear()
    if clear_durable:
        try:
            _run_coro_sync(control_plane_repository.clear_security_control_state_for_tests())
        except Exception:
            pass

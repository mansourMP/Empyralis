from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Dict, Optional

from server_modules import rust_runtime_kernel_client

_log = logging.getLogger(__name__)

_last_restart_time: float = time.time()

_EMPYRALIS_STATE_HOME = Path(
    os.getenv("EMPYRALIS_STATE_HOME", str(Path.home() / ".empyralis" / "state"))
).expanduser()
_KILL_SWITCH_FILE = _EMPYRALIS_STATE_HOME / "kill_switches.json"
_KILL_SWITCH_FILE_LOCK = Lock()

# Cross-reference: safe_mode_service owns the richer scoped incident-control
# model; this module remains the top-level emergency gate used by gateway and
# personal-channel paths before work is dispatched.


@dataclass(frozen=True, slots=True)
class KillSwitchDecision:
    blocked: bool
    reason: str
    scope: str
    detail: str = ""
    trace_id: str = ""


GLOBAL_KILL_KEY = "global_pilot"
WORKSPACE_KILL_PREFIX = "workspace:"
AGENT_KILL_PREFIX = "agent:"
GATEWAY_KILL_PREFIX = "gateway:"


_KILL_STATE: Dict[str, bool] = {}
_RESOLVERS: list[Callable[[], Dict[str, bool]]] = []


class KillSwitchRustGateError(RuntimeError):
    pass


def _enforce_kill_switch_state_decision(*, operation: str, key: str, active: bool) -> Dict[str, Any]:
    normalized_key = str(key or "").strip()
    payload = {
        "key": normalized_key,
        "active": bool(active),
        "scope": _kill_switch_scope(normalized_key),
    }
    try:
        decision = rust_runtime_kernel_client.runtime_state_store_decision(
            operation=operation,
            state_class="kill_switches",
            actor_id="system",
            status="active" if active else "cleared",
            payload=payload,
            payload_bytes=len(json.dumps(payload, sort_keys=True).encode("utf-8")),
            workspace_access=True,
            owner_access=True,
        )
        rust_runtime_kernel_client.enforce_kernel_decision(
            "runtime-state-store-decision",
            decision,
        )
        next_action = str(decision.get("next_action") or "").strip()
        if next_action != operation:
            raise KillSwitchRustGateError("unexpected_next_action")
        return decision
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        raise KillSwitchRustGateError(exc.reason) from exc


def _kill_switch_scope(key: str) -> str:
    if key == GLOBAL_KILL_KEY:
        return "global"
    if key.startswith(WORKSPACE_KILL_PREFIX):
        return "workspace"
    if key.startswith(AGENT_KILL_PREFIX):
        return "agent"
    if key.startswith(GATEWAY_KILL_PREFIX):
        return "gateway"
    return "custom"


def _read_kill_switches_from_file() -> Dict[str, bool]:
    try:
        _KILL_SWITCH_FILE.parent.mkdir(parents=True, exist_ok=True)
        if _KILL_SWITCH_FILE.exists():
            raw = json.loads(_KILL_SWITCH_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return {str(k): bool(v) for k, v in raw.items()}
    except Exception:
        _log.warning("Failed to read kill switches from %s", _KILL_SWITCH_FILE, exc_info=True)
    return {}


def _write_kill_switches_to_file(state: Dict[str, bool]) -> None:
    try:
        _KILL_SWITCH_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {str(k): bool(v) for k, v in state.items() if v}
        _KILL_SWITCH_FILE.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    except Exception:
        _log.warning("Failed to write kill switches to %s", _KILL_SWITCH_FILE, exc_info=True)


def _reload_kill_switches() -> None:
    """Restore persisted kill switches into in-memory state on startup."""
    persisted = _read_kill_switches_from_file()
    if persisted:
        _KILL_STATE.update(persisted)
        _log.warning(
            "Reloaded %d persisted kill switch(es) from %s after restart at %s: %s",
            len(persisted),
            _KILL_SWITCH_FILE,
            _last_restart_time,
            list(persisted.keys()),
        )
    else:
        _log.info("No persisted kill switches found at %s. State initialized empty.", _KILL_SWITCH_FILE)


def _file_resolver() -> Dict[str, bool]:
    return _read_kill_switches_from_file()


def set_kill_switch(key: str, *, active: bool = True) -> None:
    _enforce_kill_switch_state_decision(operation="set_kill_switch", key=key, active=active)
    _KILL_STATE[key] = active
    with _KILL_SWITCH_FILE_LOCK:
        _write_kill_switches_to_file(_KILL_STATE)
    _log.error("Kill switch SET: key=%s active=%s", key, active)


def clear_kill_switch(key: str) -> None:
    _enforce_kill_switch_state_decision(operation="clear_kill_switch", key=key, active=False)
    _KILL_STATE.pop(key, None)
    with _KILL_SWITCH_FILE_LOCK:
        _write_kill_switches_to_file(_KILL_STATE)
    _log.error("Kill switch CLEARED: key=%s", key)


def is_kill_active(key: str) -> bool:
    if key in _KILL_STATE:
        return _KILL_STATE[key]
    for resolver in _RESOLVERS:
        try:
            external = resolver()
            if key in external:
                return external[key]
        except Exception:
            pass
    return False


def register_kill_resolver(resolver: Callable[[], Dict[str, bool]]) -> None:
    if resolver not in _RESOLVERS:
        _RESOLVERS.append(resolver)


def get_kill_switch_restart_info() -> dict:
    """Return restart metadata so operators can detect kill-switch state changes."""
    return {
        "last_restart_time": _last_restart_time,
        "persisted_file": str(_KILL_SWITCH_FILE),
        "active_keys": [k for k, v in _KILL_STATE.items() if v],
    }


def evaluate_kill_switch(
    *,
    tenant_id: str = "",
    workspace_id: str = "",
    agent_id: str = "",
    gateway_id: str = "",
    trace_id: str = "",
) -> KillSwitchDecision:
    if is_kill_active(GLOBAL_KILL_KEY):
        return KillSwitchDecision(
            blocked=True,
            reason="global_kill_active",
            scope="global",
            detail="The platform is in emergency stop mode. All operations are blocked.",
            trace_id=trace_id,
        )

    if workspace_id and is_kill_active(f"{WORKSPACE_KILL_PREFIX}{workspace_id}"):
        return KillSwitchDecision(
            blocked=True,
            reason="workspace_kill_active",
            scope="workspace",
            detail=f"Workspace {workspace_id} has been stopped.",
            trace_id=trace_id,
        )

    if agent_id and is_kill_active(f"{AGENT_KILL_PREFIX}{agent_id}"):
        return KillSwitchDecision(
            blocked=True,
            reason="agent_kill_active",
            scope="agent",
            detail=f"Agent {agent_id} has been stopped by its owner.",
            trace_id=trace_id,
        )

    if gateway_id and is_kill_active(f"{GATEWAY_KILL_PREFIX}{gateway_id}"):
        return KillSwitchDecision(
            blocked=True,
            reason="gateway_kill_active",
            scope="gateway",
            detail=f"Gateway {gateway_id} has been stopped.",
            trace_id=trace_id,
        )

    safe_mode_decision = _evaluate_safe_mode_emergency_kill(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        agent_id=agent_id,
        gateway_id=gateway_id,
        trace_id=trace_id,
    )
    if safe_mode_decision.blocked:
        return safe_mode_decision

    return KillSwitchDecision(blocked=False, reason="", scope="", trace_id=trace_id)


def assert_not_killed(
    *,
    tenant_id: str = "",
    workspace_id: str = "",
    agent_id: str = "",
    gateway_id: str = "",
    trace_id: str = "",
) -> None:
    decision = evaluate_kill_switch(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        agent_id=agent_id,
        gateway_id=gateway_id,
        trace_id=trace_id,
    )
    if decision.blocked:
        from server_modules import security_audit_service

        try:
            security_audit_service.emit_security_audit_event(
                action="kill_switch.denied",
                status="blocked",
                tenant_id="",
                workspace_id=workspace_id,
                detail=decision.detail,
                metadata={
                    "reason": decision.reason,
                    "scope": decision.scope,
                    "agent_id": agent_id or None,
                    "gateway_id": gateway_id or None,
                },
            )
        except Exception:
            pass
        raise KillSwitchBlockedError(decision)


def _evaluate_safe_mode_emergency_kill(
    *,
    tenant_id: str = "",
    workspace_id: str = "",
    agent_id: str = "",
    gateway_id: str = "",
    trace_id: str = "",
) -> KillSwitchDecision:
    """Bridge the top-level emergency gate to richer safe-mode controls.

    File-backed keys in this module remain the fastest emergency stop. The
    richer incident-control source lives in safe_mode_service, so this bridge
    lets gateway and personal-channel dispatch paths see workspace, agent, and
    machine kill switches set through the operator/security APIs.
    """
    try:
        from server_modules import safe_mode_service
    except Exception:
        return KillSwitchDecision(blocked=False, reason="", scope="", trace_id=trace_id)

    try:
        machine_policy = safe_mode_service.resolve_machine_policy_status(
            tenant_id=tenant_id or None,
            workspace_id=workspace_id or None,
            machine_id=gateway_id or None,
        )
    except Exception:
        machine_policy = {}
    kill_state = machine_policy.get("kill_switch") if isinstance(machine_policy, dict) else {}
    if isinstance(kill_state, dict) and bool(kill_state.get("active")):
        scope = str(kill_state.get("scope") or "workspace").strip() or "workspace"
        reason = str(kill_state.get("reason") or "").strip()
        target = {
            "global": "The platform is in emergency stop mode.",
            "tenant": f"Tenant {tenant_id} has been stopped.",
            "workspace": f"Workspace {workspace_id} has been stopped.",
            "machine": f"Gateway {gateway_id} has been stopped.",
            "capability": "A required gateway capability has been stopped.",
        }.get(scope, "This operation has been stopped.")
        return KillSwitchDecision(
            blocked=True,
            reason=f"safe_mode_{scope}_kill_active",
            scope=scope,
            detail=reason or target,
            trace_id=trace_id,
        )

    if workspace_id and agent_id:
        try:
            agent_state = safe_mode_service.resolve_agent_disable_state(
                tenant_id=tenant_id or None,
                workspace_id=workspace_id,
                agent_install_id=agent_id,
            )
        except Exception:
            agent_state = {}
        if isinstance(agent_state, dict) and bool(agent_state.get("active")):
            return KillSwitchDecision(
                blocked=True,
                reason="safe_mode_agent_kill_active",
                scope="agent",
                detail=str(agent_state.get("reason") or f"Agent {agent_id} has been stopped."),
                trace_id=trace_id,
            )

    return KillSwitchDecision(blocked=False, reason="", scope="", trace_id=trace_id)


class KillSwitchBlockedError(Exception):
    def __init__(self, decision: KillSwitchDecision):
        self.decision = decision
        super().__init__(decision.detail)


_reload_kill_switches()
register_kill_resolver(_file_resolver)

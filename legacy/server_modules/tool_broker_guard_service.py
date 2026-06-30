from __future__ import annotations

import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Deque, Dict, Tuple


DEFAULT_WINDOW_SECONDS = 60
DEFAULT_SESSION_LIMIT = 80
DEFAULT_TOOL_LIMIT = 30
DEFAULT_EXECUTE_LIMIT = 10
DEFAULT_CONNECTOR_WRITE_LIMIT = 12
DEFAULT_ACTION_CLASS_LIMIT = 20

_LOCK = threading.Lock()
_EVENTS: Deque["_BrokerEvent"] = deque()


@dataclass(frozen=True)
class BrokerGuardDecision:
    allowed: bool
    code: str = ""
    detail: str = ""
    snapshot: Dict[str, Any] | None = None


@dataclass(frozen=True)
class _BrokerEvent:
    ts: float
    session_key: str
    tool_key: str
    action_class: str
    connector_scope: str
    surface: str


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name) or default))
    except Exception:
        return default


def _normalize(value: Any, *, default: str = "unknown") -> str:
    token = str(value or "").strip().lower()
    return token or default


def _session_key(claims: Dict[str, Any]) -> str:
    grant_id = str(claims.get("grant_id") or "").strip()
    if grant_id:
        return f"grant:{grant_id}"
    return "::".join(
        [
            _normalize(claims.get("tenant_id")),
            _normalize(claims.get("workspace_id")),
            _normalize(claims.get("manifest_id")),
            _normalize(claims.get("agent_install_id"), default="sage"),
        ]
    )


def _prune(now: float, window_seconds: int) -> None:
    cutoff = now - max(1, int(window_seconds or DEFAULT_WINDOW_SECONDS))
    while _EVENTS and _EVENTS[0].ts < cutoff:
        _EVENTS.popleft()


def reset_state_for_tests() -> None:
    with _LOCK:
        _EVENTS.clear()


def _emit_denial_side_effects(
    *,
    claims: Dict[str, Any],
    code: str,
    detail: str,
    snapshot: Dict[str, Any],
) -> None:
    tenant_id = str(claims.get("tenant_id") or "").strip() or None
    workspace_id = str(claims.get("workspace_id") or "").strip() or None
    connector_scope = str(snapshot.get("connector_scope") or "").strip().lower()
    try:
        from server_modules import security_audit_service

        security_audit_service.emit_security_audit_event(
            action="tool_broker.guard_denied",
            status="blocked",
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            detail=detail,
            metadata={
                "code": code,
                "snapshot": snapshot,
                "manifest_id": claims.get("manifest_id"),
                "agent_install_id": claims.get("agent_install_id"),
            },
            idempotency_key=f"tool_broker.guard_denied:{code}:{workspace_id or 'default'}:{int(time.time())}",
        )
    except Exception:
        pass
    if not workspace_id or code not in {
        "broker_execute_circuit_breaker",
        "broker_connector_write_circuit_breaker",
    }:
        return
    try:
        from server_modules import safe_mode_service

        if code == "broker_connector_write_circuit_breaker" and connector_scope:
            safe_mode_service.set_incident_control(
                scope="connector",
                mode="pause",
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                connector_id=connector_scope,
                reason=detail,
                metadata={"source": "tool_broker_guard", "code": code, "snapshot": snapshot},
            )
        else:
            safe_mode_service.set_incident_control(
                scope="workspace",
                mode="pause",
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                reason=detail,
                metadata={"source": "tool_broker_guard", "code": code, "snapshot": snapshot},
            )
    except Exception:
        pass


def evaluate_tool_call(
    *,
    claims: Dict[str, Any],
    tool_key: str,
    action_class: str,
    connector_scope: str = "",
    surface: str = "skill",
    now: float | None = None,
) -> BrokerGuardDecision:
    resolved_now = float(now if now is not None else time.time())
    window_seconds = _env_int("EMPYRALIS_TOOL_BROKER_WINDOW_SECONDS", DEFAULT_WINDOW_SECONDS)
    session_limit = _env_int("EMPYRALIS_TOOL_BROKER_SESSION_LIMIT", DEFAULT_SESSION_LIMIT)
    tool_limit = _env_int("EMPYRALIS_TOOL_BROKER_TOOL_LIMIT", DEFAULT_TOOL_LIMIT)
    execute_limit = _env_int("EMPYRALIS_TOOL_BROKER_EXECUTE_LIMIT", DEFAULT_EXECUTE_LIMIT)
    connector_write_limit = _env_int(
        "EMPYRALIS_TOOL_BROKER_CONNECTOR_WRITE_LIMIT",
        DEFAULT_CONNECTOR_WRITE_LIMIT,
    )
    action_class_limit = _env_int("EMPYRALIS_TOOL_BROKER_ACTION_CLASS_LIMIT", DEFAULT_ACTION_CLASS_LIMIT)
    session_key = _session_key(claims)
    normalized_tool = _normalize(tool_key)
    normalized_action = _normalize(action_class, default="read")
    normalized_scope = _normalize(connector_scope, default="")
    normalized_surface = _normalize(surface)

    denied_decision: BrokerGuardDecision | None = None
    with _LOCK:
        _prune(resolved_now, window_seconds)
        session_events = [event for event in _EVENTS if event.session_key == session_key]
        same_tool_events = [event for event in session_events if event.tool_key == normalized_tool]
        execute_events = [event for event in session_events if event.action_class == "execute"]
        same_action_events = [event for event in session_events if event.action_class == normalized_action]
        connector_write_events = [
            event
            for event in session_events
            if event.surface == "connector" and event.action_class in {"write", "execute"}
        ]
        snapshot = {
            "window_seconds": window_seconds,
            "session_count": len(session_events),
            "tool_count": len(same_tool_events),
            "execute_count": len(execute_events),
            "action_class_count": len(same_action_events),
            "connector_write_count": len(connector_write_events),
            "session_limit": session_limit,
            "tool_limit": tool_limit,
            "execute_limit": execute_limit,
            "action_class_limit": action_class_limit,
            "connector_write_limit": connector_write_limit,
            "tool_key": normalized_tool,
            "action_class": normalized_action,
            "connector_scope": normalized_scope,
            "surface": normalized_surface,
        }
        denial: Tuple[str, str] | None = None
        if len(session_events) >= session_limit:
            denial = (
                "broker_session_rate_limited",
                "This agent session made too many tool calls in a short window.",
            )
        elif len(same_tool_events) >= tool_limit:
            denial = (
                "broker_tool_rate_limited",
                f"The {normalized_tool} tool repeated too many times in a short window.",
            )
        elif normalized_action == "execute" and len(execute_events) >= execute_limit:
            denial = (
                "broker_execute_circuit_breaker",
                "Execute-class tool calls tripped the broker circuit breaker.",
            )
        elif len(same_action_events) >= action_class_limit:
            denial = (
                "broker_action_class_rate_limited",
                f"{normalized_action.title()}-class tool calls repeated too many times in a short window.",
            )
        elif normalized_surface == "connector" and normalized_action in {"write", "execute"} and len(connector_write_events) >= connector_write_limit:
            denial = (
                "broker_connector_write_circuit_breaker",
                "Connector write activity tripped the broker circuit breaker.",
            )

        if denial is not None:
            code, detail = denial
            denied_decision = BrokerGuardDecision(False, code=code, detail=detail, snapshot=snapshot)
        else:
            _EVENTS.append(
                _BrokerEvent(
                    ts=resolved_now,
                    session_key=session_key,
                    tool_key=normalized_tool,
                    action_class=normalized_action,
                    connector_scope=normalized_scope,
                    surface=normalized_surface,
                )
            )
            snapshot["session_count"] += 1
            snapshot["tool_count"] += 1
            if normalized_action == "execute":
                snapshot["execute_count"] += 1
            snapshot["action_class_count"] += 1
            if normalized_surface == "connector" and normalized_action in {"write", "execute"}:
                snapshot["connector_write_count"] += 1
            return BrokerGuardDecision(True, snapshot=snapshot)

    if denied_decision is not None:
        _emit_denial_side_effects(
            claims=claims,
            code=denied_decision.code,
            detail=denied_decision.detail,
            snapshot=dict(denied_decision.snapshot or {}),
        )
        return denied_decision
    return BrokerGuardDecision(True)

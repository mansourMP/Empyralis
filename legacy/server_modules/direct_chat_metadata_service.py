from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


def build_context_used(
    *,
    workspace_id: str,
    requested_provider: str,
    effective_provider: Optional[str],
    requested_model: str,
    effective_model: Optional[str],
    reasoning_effort: Optional[str],
    connected_systems: List[str],
    tool_capabilities: List[Dict[str, Any]],
    prior_messages_used: bool,
    history_mode: str,
    run_created: bool,
    fallback_used: bool = False,
    fallback_reason: Optional[str] = None,
) -> Dict[str, Any]:
    provider_overridden = bool(
        requested_provider and effective_provider and requested_provider != effective_provider
    )
    model_overridden = bool(
        requested_model and effective_model and requested_model != effective_model
    )
    payload = {
        "workspace": workspace_id or "default",
        "requested_provider": requested_provider or None,
        "effective_provider": effective_provider or None,
        "requested_model": requested_model or None,
        "effective_model": effective_model or None,
        "provider_overridden": provider_overridden,
        "model_overridden": model_overridden,
        "fallback_used": bool(fallback_used),
        "reasoning_effort": reasoning_effort or None,
        "connected_systems": connected_systems,
        "tool_capabilities": tool_capabilities,
        "prior_messages_used": bool(prior_messages_used),
        "history_mode": history_mode if history_mode in {"none", "raw_messages", "summary"} else "none",
        "run_created": bool(run_created),
    }
    if fallback_reason:
        payload["fallback_reason"] = fallback_reason
    return payload


def with_context_used(payload: Dict[str, Any], context_used: Dict[str, Any]) -> Dict[str, Any]:
    next_payload = dict(payload)
    next_payload["context_used"] = context_used
    return next_payload


def heartbeat_pending_tasks_for_suggestions(
    *,
    workspace_context_dir_fn: Callable[[], Path],
    parse_unchecked_heartbeat_tasks_fn: Optional[Callable[[str], List[str]]] = None,
) -> List[str]:
    if parse_unchecked_heartbeat_tasks_fn is None:
        try:
            from server_modules.heartbeat import parse_unchecked_heartbeat_tasks as parse_unchecked_heartbeat_tasks_fn
        except Exception:
            return []
    heartbeat_path = workspace_context_dir_fn() / "HEARTBEAT.md"
    if not heartbeat_path.exists():
        return []
    try:
        text = heartbeat_path.read_text(encoding="utf-8")
    except Exception:
        return []
    return parse_unchecked_heartbeat_tasks_fn(text)[:3]


def recent_run_prompts_for_suggestions(
    workspace_id: str,
    *,
    run_history: Any,
) -> List[str]:
    prompts: List[str] = []
    seen: set[str] = set()
    normalized_workspace_id = str(workspace_id or "default").strip() or "default"
    for item in list(run_history) if isinstance(run_history, list) else list(run_history or []):
        if not isinstance(item, dict):
            continue
        if str(item.get("workspace_id") or "").strip() != normalized_workspace_id:
            continue
        goal = re.sub(r"\s+", " ", str(item.get("user_goal") or "").strip())
        if len(goal) < 12:
            continue
        key = goal.lower()
        if key in seen:
            continue
        seen.add(key)
        prompts.append(goal)
        if len(prompts) >= 3:
            break
    return prompts

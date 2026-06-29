from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set

from server_modules import skills_service


PUBLIC_GENERATION_ERROR_MESSAGES = (
    "a temporary error occurred while generating the response. please try again in a moment.",
    "the response took too long. please try again.",
    "the service is temporarily at capacity. please try again in a moment.",
    "thread not found.",
)

FAILED_PRIOR_MESSAGE_STATUSES = {
    "aborted",
    "cancelled",
    "canceled",
    "error",
    "failed",
    "stopped",
    "timeout",
    "timed_out",
}


def _compact_labels(items: Any, *, limit: int = 4) -> str:
    labels: List[str] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or item.get("id") or "").strip()
        if not label or label in labels:
            continue
        labels.append(label)
        if len(labels) >= limit:
            break
    return ", ".join(labels) if labels else "none"


def _credits_summary_line(credits_truth: Dict[str, Any]) -> str:
    hosted_enabled = bool(credits_truth.get("hosted_enabled"))
    reason = str(credits_truth.get("reason") or "").strip().lower()
    if hosted_enabled:
        return "Workspace AI: available"
    if reason:
        return f"Workspace AI: blocked ({reason.replace('_', ' ')})"
    return "Workspace AI: unavailable"


def is_public_generation_error_message(content: Any) -> bool:
    normalized = re.sub(r"\s+", " ", str(content or "").strip()).lower()
    if not normalized:
        return False
    if normalized == "not found":
        return True
    return any(message in normalized for message in PUBLIC_GENERATION_ERROR_MESSAGES)


def should_exclude_prior_message(role: str, content: Any, status: Any = None) -> bool:
    normalized_role = str(role or "").strip().lower()
    if normalized_role != "assistant":
        return False
    normalized_status = str(status or "").strip().lower()
    if normalized_status in FAILED_PRIOR_MESSAGE_STATUSES:
        return True
    return is_public_generation_error_message(content)


def _agent_turn_policy_context(session_ctx: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    context = session_ctx if isinstance(session_ctx, dict) else {}
    request = context.get("agent_turn_request") if isinstance(context.get("agent_turn_request"), dict) else {}
    policy_context = request.get("policy_context") if isinstance(request.get("policy_context"), dict) else {}
    return dict(policy_context)


def agent_machine_owner_user_id(session_ctx: Optional[Dict[str, Any]]) -> str:
    context = session_ctx if isinstance(session_ctx, dict) else {}
    meta = context.get("meta") if isinstance(context.get("meta"), dict) else {}
    return (
        str(context.get("user_id") or "").strip()
        or str(meta.get("owner_user_id") or "").strip()
        or str(meta.get("user_id") or "").strip()
    )


def session_mode_requested(session_ctx: Optional[Dict[str, Any]]) -> Optional[str]:
    policy_context = _agent_turn_policy_context(session_ctx)
    requested = str(
        policy_context.get("requested_session_mode")
        or policy_context.get("effective_session_mode")
        or policy_context.get("session_mode")
        or ""
    ).strip().lower()
    return requested or None


def session_mode_effective(session_ctx: Optional[Dict[str, Any]]) -> Optional[str]:
    policy_context = _agent_turn_policy_context(session_ctx)
    effective = str(
        policy_context.get("effective_session_mode")
        or policy_context.get("session_mode")
        or ""
    ).strip().lower()
    return effective or None


def availability_lines(
    workspace_id: str,
    availability: Dict[str, Any],
    *,
    normalize_tool_capabilities: Any,
) -> List[str]:
    ai_ready = bool(availability.get("ai_ready"))
    labels = skills_service.availability_label_summary(
        {"tool_capabilities": normalize_tool_capabilities(availability)}
    )
    lines = [
        f"Workspace: {workspace_id or 'default'}",
        f"AI account: {'ready' if ai_ready else 'not ready'}",
        f"Connected systems: {', '.join(labels['connected']) if labels['connected'] else 'none'}",
        f"Unavailable now: {', '.join(labels['unavailable']) if labels['unavailable'] else 'none'}",
        f"Not verified: {', '.join(labels['unverified']) if labels['unverified'] else 'none'}",
    ]
    capability_truth = availability.get("capability_truth") if isinstance(availability.get("capability_truth"), dict) else {}
    provider_truth = capability_truth.get("provider") if isinstance(capability_truth.get("provider"), dict) else {}
    my_computer = capability_truth.get("my_computer") if isinstance(capability_truth.get("my_computer"), dict) else {}
    setup_actions = (
        capability_truth.get("required_setup_actions")
        if isinstance(capability_truth.get("required_setup_actions"), list)
        else []
    )

    provider_label = str(provider_truth.get("label") or availability.get("provider") or "AI").strip() or "AI"
    provider_ready = bool(provider_truth.get("ai_ready"))
    lines.append(f"Selected AI model: {provider_label} ({'ready' if provider_ready else 'setup required'})")

    credits_truth = capability_truth.get("credits") if isinstance(capability_truth.get("credits"), dict) else {}
    if credits_truth:
        lines.append(_credits_summary_line(credits_truth))

    computer_state = str(my_computer.get("state") or "").strip().lower()
    if computer_state:
        lines.append(f"My Computer: {computer_state.replace('_', ' ')}")

    connected_apps = capability_truth.get("connected_apps") if isinstance(capability_truth.get("connected_apps"), list) else []
    channels = capability_truth.get("channels") if isinstance(capability_truth.get("channels"), list) else []
    byok_providers = capability_truth.get("byok_providers") if isinstance(capability_truth.get("byok_providers"), list) else []

    lines.append(f"Connected apps: {_compact_labels(connected_apps)}")
    lines.append(f"Messaging channels: {_compact_labels(channels)}")
    if byok_providers:
        lines.append(f"Available AI connections: {_compact_labels(byok_providers)}")

    if setup_actions:
        action_labels = [
            str(item.get("label") or "").strip()
            for item in setup_actions
            if isinstance(item, dict) and str(item.get("label") or "").strip()
        ]
        if action_labels:
            lines.append(f"Required setup actions: {', '.join(action_labels[:4])}")
    return lines


def connected_system_labels(
    availability: Dict[str, Any],
    *,
    normalize_tool_capabilities: Any,
) -> List[str]:
    return skills_service.connected_availability_labels(
        {"tool_capabilities": normalize_tool_capabilities(availability)}
    )


def context_tool_capabilities(
    availability: Dict[str, Any],
    *,
    normalize_tool_capabilities: Any,
    max_context_tool_actions: int,
    max_context_tool_capabilities: int,
) -> List[Dict[str, Any]]:
    return skills_service.context_availability_capabilities(
        {"tool_capabilities": normalize_tool_capabilities(availability)},
        max_context_tool_actions=max_context_tool_actions,
        max_context_tool_capabilities=max_context_tool_capabilities,
    )


def normalize_prior_messages(
    prior_messages: Any,
    *,
    max_direct_chat_prior_message_chars: int,
    max_direct_chat_prior_messages: int,
) -> List[Dict[str, str]]:
    if not isinstance(prior_messages, list):
        return []
    normalized: List[Dict[str, str]] = []
    for item in prior_messages:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        if role not in {"user", "assistant"}:
            continue
        content = re.sub(r"\s+", " ", str(item.get("content") or "").strip())
        if not content:
            continue
        if should_exclude_prior_message(role, content, item.get("status")):
            continue
        if len(content) > max_direct_chat_prior_message_chars:
            content = content[: max_direct_chat_prior_message_chars - 1].rstrip() + "…"
        normalized.append({"role": role, "content": content})
    return normalized[-max_direct_chat_prior_messages:]


def direct_tool_session_key(workspace_id: str, thread_id: str) -> str:
    normalized_workspace_id = str(workspace_id or "default").strip() or "default"
    normalized_thread_id = str(thread_id or "").strip() or "direct-chat"
    return f"{normalized_workspace_id}:{normalized_thread_id}"


def direct_chat_session_key(workspace_id: str, thread_id: str) -> str:
    return direct_tool_session_key(workspace_id, thread_id)


def parse_slash_command(message: str) -> Dict[str, str]:
    normalized = str(message or "").strip()
    if not normalized.startswith("/"):
        return {}
    tokens = normalized.split()
    if not tokens:
        return {}
    command = tokens[0][1:].strip().lower()
    remainder = normalized[len(tokens[0]):].strip()
    return {"command": command, "remainder": remainder}


def session_model_preference(session_key: str, *, store: Dict[str, Dict[str, Optional[str]]]) -> Dict[str, Optional[str]]:
    stored = store.get(session_key)
    if not isinstance(stored, dict):
        return {"provider": None, "model": None}
    return {
        "provider": str(stored.get("provider") or "").strip() or None,
        "model": str(stored.get("model") or "").strip() or None,
    }


def set_session_model_preference(
    session_key: str,
    *,
    provider: Optional[str],
    model: Optional[str],
    store: Dict[str, Dict[str, Optional[str]]],
) -> None:
    store[session_key] = {
        "provider": str(provider or "").strip() or None,
        "model": str(model or "").strip() or None,
    }


def mark_thread_cleared(session_key: str, *, clear_markers: Set[str]) -> None:
    clear_markers.add(session_key)


def consume_thread_cleared(session_key: str, *, clear_markers: Set[str]) -> bool:
    if session_key not in clear_markers:
        return False
    clear_markers.discard(session_key)
    return True


def active_run_count(workspace_id: str, *, live_runs: Any) -> int:
    total = 0
    for run in list(live_runs.values()) if isinstance(live_runs, dict) else []:
        if not isinstance(run, dict):
            continue
        status = str(run.get("status") or "").strip().lower()
        if status in {"completed", "failed", "timeout", "stopped", "cancelled"}:
            continue
        context = run.get("context") if isinstance(run.get("context"), dict) else {}
        run_workspace_id = str(context.get("workspace_id") or "").strip() or "default"
        if run_workspace_id == workspace_id:
            total += 1
    return total


def slash_command_help_text() -> str:
    return (
        "Available commands:\n"
        "/status\n"
        "/memory\n"
        "/forget <key>\n"
        "/model <name>\n"
        "/clear\n"
        "/help\n\n"
        "Built-in tools when relevant:\n"
        "memory_search / memory_get\n"
        "web search\n"
        "web fetch\n"
        "http_request\n"
        "generate_image\n"
        "browser navigate / screenshot / click / fill / get_page_state\n"
        "computer ocr / click / type / applescript / clipboard / notify / list_apps / launch_app / speak"
    )

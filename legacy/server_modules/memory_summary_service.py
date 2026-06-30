from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from server_modules.conversation_memory_policy import (
    DIRECT_CHAT_PROFILE,
    EXTERNAL_CHANNEL_CUSTOMER_PROFILE,
    get_memory_policy_profile,
)


SUMMARY_MESSAGE_ROLE = "assistant"
SUMMARY_LINE_MAX_CHARS = 240


def _compact_line(text: Any, limit: int = 320) -> str:
    normalized = re.sub(r"\s+", " ", str(text or "").strip())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1].rstrip() + "…"


def summarize_messages(
    messages: List[Dict[str, Any]],
    *,
    max_chars: int | None = None,
    policy_profile: str = DIRECT_CHAT_PROFILE,
) -> str:
    resolved_profile = get_memory_policy_profile(policy_profile)
    resolved_max_chars = max_chars if max_chars is not None else resolved_profile.max_summary_chars
    if not isinstance(messages, list) or not messages:
        return ""
    lines: List[str] = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "message").strip().lower()
        content = _compact_line(item.get("content"), limit=SUMMARY_LINE_MAX_CHARS)
        if not content:
            continue
        label = "User" if role == "user" else "Assistant" if role == "assistant" else role.title() or "Message"
        lines.append(f"- {label}: {content}")
        if len("\n".join(lines)) >= resolved_max_chars:
            break
    if not lines:
        return ""
    summary = "Earlier conversation summary:\n" + "\n".join(lines)
    return summary[:resolved_max_chars].rstrip()


def build_transcript_summary(
    *,
    messages: List[Dict[str, Any]],
    user_message: str,
    assistant_reply: str,
) -> str:
    notable_user_points: List[str] = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        if str(item.get("role") or "").strip().lower() != "user":
            continue
        content = _compact_line(item.get("content"), limit=220)
        if content:
            notable_user_points.append(content)
        if len(notable_user_points) >= 2:
            break
    primary_user_line = _compact_line(user_message, limit=280)
    if not primary_user_line and notable_user_points:
        primary_user_line = notable_user_points[0]
    assistant_line = _compact_line(assistant_reply, limit=420)
    lines = ["Transcript summary:"]
    if primary_user_line:
        lines.append(f"- Latest user request: {primary_user_line}")
    if notable_user_points:
        lines.append(f"- Earlier context: {' | '.join(notable_user_points[:2])}")
    if assistant_line:
        lines.append(f"- Outcome: {assistant_line}")
    return "\n".join(lines).strip()


def build_deployed_agent_memory_summary(
    *,
    messages: List[Dict[str, Any]],
    prior_summary: Optional[str] = None,
    max_chars: int | None = None,
) -> str:
    resolved_profile = get_memory_policy_profile(EXTERNAL_CHANNEL_CUSTOMER_PROFILE)
    resolved_max_chars = max_chars if max_chars is not None else resolved_profile.max_summary_chars
    blocks: List[str] = []
    normalized_prior = str(prior_summary or "").strip()
    if normalized_prior:
        if len(normalized_prior) > 800:
            normalized_prior = normalized_prior[:799].rstrip() + "…"
        blocks.append(f"Earlier persisted summary:\n{normalized_prior}")
    message_summary = summarize_messages(
        messages,
        max_chars=max(400, resolved_max_chars - len("\n\n".join(blocks)) - 48),
        policy_profile=EXTERNAL_CHANNEL_CUSTOMER_PROFILE,
    )
    if message_summary:
        blocks.append(message_summary)
    if not blocks:
        return ""
    summary = "Persistent customer memory:\n" + "\n\n".join(blocks)
    if len(summary) <= resolved_max_chars:
        return summary
    return summary[: resolved_max_chars - 1].rstrip() + "…"


def build_direct_chat_daily_log_summary(
    *,
    user_message: str,
    assistant_reply: str,
) -> str:
    normalized_user_message = _compact_line(user_message, limit=320)
    normalized_assistant_reply = _compact_line(assistant_reply, limit=500)
    if not normalized_user_message or not normalized_assistant_reply:
        return ""
    return (
        f"- User: {normalized_user_message}\n"
        f"- Assistant: {normalized_assistant_reply}"
    ).strip()

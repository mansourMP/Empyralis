from __future__ import annotations

import math
from typing import Any, Dict, List

from server_modules.conversation_memory_policy import DIRECT_CHAT_PROFILE, get_memory_policy_profile
from server_modules.memory_summary_service import summarize_messages as summarize_messages_with_policy


_DIRECT_CHAT_POLICY = get_memory_policy_profile(DIRECT_CHAT_PROFILE)
DEFAULT_MAX_TOKENS = _DIRECT_CHAT_POLICY.max_prompt_tokens
DEFAULT_PRESERVE_LAST_MESSAGES = _DIRECT_CHAT_POLICY.preserve_last_messages
SUMMARY_MESSAGE_ROLE = "assistant"
SUMMARY_MAX_CHARS = _DIRECT_CHAT_POLICY.max_summary_chars
SUMMARY_LINE_MAX_CHARS = 240


def estimate_text_tokens(text: Any) -> int:
    normalized = str(text or "").strip()
    if not normalized:
        return 0
    return max(1, int(math.ceil(len(normalized) / 4.0)))


def estimate_message_tokens(message: Dict[str, Any]) -> int:
    if not isinstance(message, dict):
        return 0
    return estimate_text_tokens(message.get("content")) + 6


def estimate_conversation_tokens(messages: List[Dict[str, Any]]) -> int:
    return sum(estimate_message_tokens(message) for message in messages if isinstance(message, dict))


def summarize_messages(messages: List[Dict[str, Any]], *, max_chars: int = SUMMARY_MAX_CHARS) -> str:
    return summarize_messages_with_policy(
        messages,
        max_chars=max_chars,
        policy_profile=DIRECT_CHAT_PROFILE,
    )


def _trim_tail_to_token_budget(
    messages: List[Dict[str, Any]],
    *,
    budget_tokens: int,
) -> List[Dict[str, Any]]:
    safe_budget = max(200, int(budget_tokens or 0))
    kept: List[Dict[str, Any]] = []
    total = 0
    for message in reversed(messages):
        token_cost = estimate_message_tokens(message)
        if kept and total + token_cost > safe_budget:
            break
        kept.append(message)
        total += token_cost
    if not kept and messages:
        return [messages[-1]]
    kept.reverse()
    return kept


def compact_conversation_history(
    messages: List[Dict[str, Any]],
    *,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    preserve_last_messages: int = DEFAULT_PRESERVE_LAST_MESSAGES,
    recent_message_budget_tokens: int | None = None,
    summary_max_chars: int | None = None,
) -> Dict[str, Any]:
    normalized_messages = [dict(item) for item in messages if isinstance(item, dict)]
    original_tokens = estimate_conversation_tokens(normalized_messages)
    if original_tokens <= max_tokens:
        return {
            "messages": normalized_messages,
            "compacted": False,
            "original_tokens": original_tokens,
            "compacted_tokens": original_tokens,
            "summary": "",
        }

    safe_preserve_last = max(1, int(preserve_last_messages or DEFAULT_PRESERVE_LAST_MESSAGES))
    tail = normalized_messages[-safe_preserve_last:]
    tail_budget = int(
        recent_message_budget_tokens
        if recent_message_budget_tokens is not None
        else max(800, safe_preserve_last * 600)
    )
    tail = _trim_tail_to_token_budget(tail, budget_tokens=tail_budget)
    head = normalized_messages[:-safe_preserve_last]
    summary = summarize_messages(
        head,
        max_chars=max(400, int(summary_max_chars or SUMMARY_MAX_CHARS)),
    )
    compacted_messages: List[Dict[str, Any]] = []
    if summary:
        compacted_messages.append({"role": SUMMARY_MESSAGE_ROLE, "content": summary})
    compacted_messages.extend(tail)

    compacted_tokens = estimate_conversation_tokens(compacted_messages)
    if compacted_tokens > max_tokens and len(tail) > 1:
        trimmed_tail = tail[-min(len(tail), max(4, safe_preserve_last // 2)) :]
        compacted_messages = ([{"role": SUMMARY_MESSAGE_ROLE, "content": summary}] if summary else []) + trimmed_tail
        compacted_tokens = estimate_conversation_tokens(compacted_messages)

    return {
        "messages": compacted_messages,
        "compacted": True,
        "original_tokens": original_tokens,
        "compacted_tokens": compacted_tokens,
        "summary": summary,
    }

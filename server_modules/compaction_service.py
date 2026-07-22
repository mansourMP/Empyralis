"""
Compaction engine — OpenClaw-style summarization for long conversations.

Triggered when context tokens exceed (context_window - reserve_tokens).
Walks back from newest turn, keeps ~KEEP_RECENT_TOKENS of recent messages,
serializes older turns as structured text, calls LLM for summary, appends
CompactionEntry to agent_turns, and reloads context from summary + recent.

No memory flush. No dedicated model. No recursive summarization.
Matches OpenClaw's compaction.ts design.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from scripts.orion_local_worker_llm import openai_chat_text

LOGGER = logging.getLogger(__name__)

# ── Step 1: Token estimation ──────────────────────────────────────────

def estimate_tokens(text: str) -> int:
    """Fast heuristic: ~4 chars per token. Sufficient for threshold checks."""
    return max(0, len(text or "") // 4)


def estimate_turn_tokens(turn: Dict[str, Any]) -> int:
    """Estimate tokens for a single turn dict (from agent_turns row)."""
    content = str(turn.get("content") or "")
    return estimate_tokens(content)


def estimate_turns_tokens(turns: List[Dict[str, Any]]) -> int:
    """Estimate total tokens across a list of turns."""
    return sum(estimate_turn_tokens(t) for t in turns)


# ── Step 3: Compaction trigger ────────────────────────────────────────

COMPACTION_RESERVE_TOKENS = 16384
TOOL_RESULT_MAX_CHARS = 2000
DEFAULT_CONTEXT_WINDOW = 128000  # fallback when model context window is unknown
_KEEP_RECENT_CAP = 30000         # hard cap on keep-recent tokens regardless of window size
_KEEP_RECENT_RATIO = 0.15        # fraction of context window to keep as recent turns


def keep_recent_tokens_for_window(context_window: int) -> int:
    """Return keep-recent token budget proportional to the context window.

    ~15% of the window, capped at _KEEP_RECENT_CAP to avoid keeping
    an unreasonable number of turns for 1M-window models.
    """
    proportional = int(context_window * _KEEP_RECENT_RATIO)
    return min(proportional, _KEEP_RECENT_CAP)


def resolve_context_window(
    provider: str | None = None,
    model: str | None = None,
) -> int:
    """Return the ACTUAL context window for a provider/model pair.

    Falls back to DEFAULT_CONTEXT_WINDOW (128K) only when the provider/model
    combination is unknown.
    """
    from server_modules.provider_profiles import context_window_for_model

    window = context_window_for_model(provider, model)
    return window if window else DEFAULT_CONTEXT_WINDOW


def should_compact(
    turns: List[Dict[str, Any]],
    *,
    context_window: int,
    reserve_tokens: int = COMPACTION_RESERVE_TOKENS,
) -> bool:
    """Check if total turn tokens exceed the safe threshold.

    Args:
        turns: list of turn dicts from agent_turns
        context_window: the ACTUAL context window of the model in use (REQUIRED — no default)
        reserve_tokens: tokens to reserve for system prompt + reply (default 16384)
    """
    total = estimate_turns_tokens(turns)
    threshold = max(1, context_window - reserve_tokens)
    return total > threshold


def find_cut_point(
    turns: List[Dict[str, Any]],
    keep_recent_tokens: int | None = None,
    context_window: int | None = None,
) -> int:
    """Walk backwards from newest turn. Return index of first turn to KEEP.
    Turns BEFORE this index get summarized. Turns AT and AFTER stay raw.
    Returns 0 if everything fits (no cut needed).

    If keep_recent_tokens is None, it's computed from context_window
    (proportional, ~15%). If both are None, falls back to a reasonable default.
    """
    if keep_recent_tokens is None:
        keep_recent_tokens = (
            keep_recent_tokens_for_window(context_window)
            if context_window is not None
            else keep_recent_tokens_for_window(DEFAULT_CONTEXT_WINDOW)
        )
    accumulated = 0
    for i in range(len(turns) - 1, -1, -1):
        accumulated += estimate_turn_tokens(turns[i])
        if accumulated >= keep_recent_tokens:
            return i
    return 0


# ── Step 2: Tool-result pruning ───────────────────────────────────────

def prune_tool_result(turn: Dict[str, Any]) -> str:
    """Prune verbose tool results for context assembly.
    Returns the (possibly pruned) content string.
    The actual result stays intact in agent_turns — this is in-memory only.
    """
    role = str(turn.get("role") or "").strip().lower()
    content = str(turn.get("content") or "")

    if role == "tool_result" and len(content) > TOOL_RESULT_MAX_CHARS:
        tool_name = "unknown"
        meta = turn.get("metadata")
        if isinstance(meta, dict):
            tool_name = str(meta.get("tool_name") or meta.get("name") or "unknown")
        return (
            f"[tool result: {tool_name} — {len(content)} chars"
            f" — pruned for context]"
        )

    return content


# ── Step 4: Serialize for summarization ───────────────────────────────

def serialize_turns_for_compaction(turns: List[Dict[str, Any]]) -> str:
    """Convert turns to structured text — NOT conversation format.
    Prevents the model from treating it as a chat to continue.
    Matches OpenClaw's serializeConversation().
    """
    lines: List[str] = []
    for t in turns:
        role = str(t.get("role") or "").strip().lower()
        content = str(t.get("content") or "").strip()
        if not content:
            continue

        if role == "user":
            lines.append(f"[User]: {content}")
        elif role == "assistant":
            lines.append(f"[Assistant]: {content}")
        elif role == "tool_result":
            pruned = prune_tool_result(t)
            tool_name = "unknown"
            meta = t.get("metadata")
            if isinstance(meta, dict):
                tool_name = str(meta.get("tool_name") or meta.get("name") or "unknown")
            lines.append(f"[Tool result: {tool_name}]: {pruned}")
        elif role == "compaction_summary":
            lines.append(f"[Previous summary]: {content}")
        elif role == "system":
            lines.append(f"[System]: {content}")
        else:
            lines.append(f"[{role}]: {content}")

    return "\n\n".join(lines)


COMPACTION_PROMPT = """Summarize this conversation segment concisely. Preserve:
- Decisions made and their outcomes
- User preferences and stated requirements  
- Active tasks and their current status
- Pending items that need follow-up
- Important facts about the user or their work

Do not invent. Do not speculate. Be factual and dense.

Previous summary (if any):
{previous_summary}

Conversation to summarize:
{conversation_text}"""


async def compact_turns(
    turns: List[Dict[str, Any]],
    *,
    workspace_id: str,
    tenant_id: str,
    thread_id: str = "sage-main",
    session_id: str = "",
    previous_summary: str = "",
    provider: str = "",
    model: str = "",
    # docs/design/audit-context-anatomy.md fix #3 (compaction gap, half b):
    # optional so every existing call site keeps working unchanged — when a
    # caller has a live agent_trace_service.TraceContext in scope (typed Any
    # here to avoid importing agent_trace_service at module load time, same
    # lazy-import style already used below for control_plane_repository),
    # a "compaction.skipped" trace event is emitted alongside the WARNING
    # log below whenever this returns "" instead of a real summary.
    trace_context: Any = None,
) -> str:
    """Summarize turns and persist as a CompactionEntry in agent_turns.
    Returns the summary text, or "" if compaction was skipped.

    2026-07-23 founder ruling ("no model fallback chains" — if the user's
    model fails, it fails visibly; the owner changes their model): this
    used to fall back to a platform-wide DeepSeek key/model
    (`provider or "deepseek"`) whenever the caller didn't resolve the
    turn's own provider. That fallback is REMOVED — there is no substitute
    model. If the turn's own provider can't be determined, compaction is
    skipped outright (never silently run on a model the user never chose
    and isn't paying for). Compaction is best-effort context management,
    not a guarantee — the caller always falls through to raw truncation —
    but a skip must never be invisible, so the same WARNING log +
    compaction.skipped trace event fire here as they do for an in-flight
    provider failure below.
    """
    from server_modules import control_plane_repository

    resolved_provider = str(provider or "").strip().lower()
    resolved_model = str(model or "").strip()

    if not resolved_provider:
        reason = "model_unavailable_no_fallback"
        LOGGER.warning(
            "compact_turns: no summary produced (provider=%s, model=%s, reason=%s) — "
            "compaction is a no-op for this call; caller falls back to raw truncation",
            "(none)", resolved_model or "(none)", reason,
        )
        if trace_context is not None:
            try:
                from server_modules import agent_trace_service

                await agent_trace_service.emit_compaction_skipped(
                    trace_context, reason, resolved_provider, resolved_model,
                )
            except Exception:
                pass  # observability must never break the calling turn
        return ""

    text = serialize_turns_for_compaction(turns)

    prompt = COMPACTION_PROMPT.format(
        previous_summary=previous_summary or "(none — this is the first compaction)",
        conversation_text=text,
    )

    # Use the existing generation pipeline with a short, focused prompt
    # Use openai_chat_text directly (sync call, same as generate_chat_reply_with_provider_fallback)
    text, _usage, _model, error = openai_chat_text(
        system_prompt=prompt,
        user_prompt="",  # all instructions are in the system prompt
        provider=resolved_provider,
        model_override=resolved_model or None,
    )
    summary = (text or "").strip()

    if not summary:
        # Not the no-fallback skip above — a real provider/model was
        # resolved but the call itself came back empty (missing key,
        # rate-limited, empty response, etc). Fails safe (the turn falls
        # through to raw-truncation elsewhere), but must never be invisible.
        reason = str(error or "empty_summary").strip() or "empty_summary"
        LOGGER.warning(
            "compact_turns: no summary produced (provider=%s, model=%s, reason=%s) — "
            "compaction is a no-op for this call; caller falls back to raw truncation",
            resolved_provider, resolved_model or "default", reason,
        )
        if trace_context is not None:
            try:
                from server_modules import agent_trace_service

                await agent_trace_service.emit_compaction_skipped(
                    trace_context, reason, resolved_provider, resolved_model,
                )
            except Exception:
                pass  # observability must never break the calling turn
        return ""

    # Persist as agent_turn
    try:
        await control_plane_repository.upsert_agent_turn(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            thread_id=thread_id,
            session_id=session_id or None,
            role="compaction_summary",
            content=summary,
            status="completed",
        )
    except Exception:
        pass  # never break the main turn for persistence failures

    return summary


# ── Step 5: Context assembly after compaction ─────────────────────────

def build_context_from_compaction(
    summary: str,
    kept_turns: List[Dict[str, Any]],
) -> List[Dict[str, str]]:
    """Assemble context after compaction: summary first, then recent raw turns."""
    messages: List[Dict[str, str]] = []

    if summary:
        messages.append({
            "role": "system",
            "content": (
                "[Compacted context from earlier in this conversation]:\n"
                + summary
            ),
        })

    for t in kept_turns:
        role = str(t.get("role") or "").strip().lower()
        content = str(t.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})

    return messages


# ── Step 6: Previous summary carry-forward ────────────────────────────

async def load_previous_summary(
    workspace_id: str,
    tenant_id: str,
    thread_id: str = "sage-main",
) -> str:
    """Load the most recent compaction summary for iterative context."""
    from server_modules import control_plane_repository

    turns = await control_plane_repository.list_agent_turns(
        thread_id=thread_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        limit=10,
    )
    for t in (turns or []):
        if isinstance(t, dict) and t.get("role") == "compaction_summary":
            return str(t.get("content") or "")
    return ""


# ── Overflow check ────────────────────────────────────────────────────

OVERFLOW_KEYWORDS = (
    "context_length_exceeded",
    "request_too_large",
    "maximum context length",
    "context window",
    "token limit",
    "too many tokens",
    # Anthropic's actual Messages API error text for an overflowing prompt is
    # "prompt is too long: N tokens > M maximum" — it does not contain any of
    # the OpenAI-shaped phrases above (docs/design/audit-context-currency.md
    # fix #1/#2: "find how overflow surfaces per provider"). Verified against
    # _anthropic_response_error() in scripts/orion_local_worker_llm.py, which
    # returns the raw `error.message` string from Anthropic's response body
    # untouched — none of the prior keywords would have matched it.
    "prompt is too long",
)


def is_context_overflow_error(error_message: str) -> bool:
    """Check if a provider error is a context overflow."""
    msg = error_message.lower()
    return any(kw in msg for kw in OVERFLOW_KEYWORDS)

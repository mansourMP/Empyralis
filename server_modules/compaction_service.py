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
#
# TODO ("ALSO ADOPT" — token measurement, left undone this pass): Codex's
# context_window.rs uses a hybrid — the LAST response's provider-reported
# usage (exact) as the baseline "truth", then only ESTIMATES tokens for
# whatever's been appended since (the new user message, tool results, etc)
# on top of that. Every threshold check in this module instead estimates
# the ENTIRE turns list from scratch every time via this chars/4 heuristic,
# with no plumbing to carry a prior turn's real provider usage forward as
# a baseline. Wiring that in would mean threading last-response usage
# (already computed — see agent_turn_runtime_service.py's `usage` dict
# from generate_chat_reply_with_provider_fallback, and
# scripts/orion_local_worker_llm.py's usage_masked shapes) through into
# every should_compact/find_cut_point call site, plus a place to persist
# "tokens as of turn N" per thread — a real, cross-file plumbing change
# outside this pass's file ownership (touches scripts/orion_local_worker_
# llm.py and the calling sites' usage-threading, not just this module).
# Left as a heuristic-only implementation with FIXED_SAFETY_MARGIN_TOKENS
# sized to cover this estimator's known error bars in the meantime (see
# BUG 5's threshold formula below) rather than silently pretending the
# estimate is exact.

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

# BUG 5 fix (docs/design — compaction end-to-end audit, 2026-07-24): the old
# formula was `threshold = context_window - COMPACTION_RESERVE_TOKENS` with a
# single flat reserve (16384) applied to every model regardless of its real
# window size. That produces wildly different (and wrong) fill fractions:
#   1,000,000 window -> fires at 983,616/1,000,000 = 98.4% full (too late —
#                        almost no room left to act once it does trigger)
#   32,000 window     -> fires at 15,616/32,000 = 49% full (compacts a
#                        half-empty conversation for no reason)
#   <=16,384 window   -> threshold clamped to 1 by the old `max(1, ...)` —
#                        should_compact() returned True on every single
#                        message regardless of actual usage.
# Replaced with the industry "subtract reserves, then take a ratio of what's
# left" formula. Codex hard-clamps compaction at 90% of the context window
# (`(context_window * 9) / 10` — verified in
# codex-rs/protocol/src/openai_models.rs); Claude Code's own 1M-context model
# compacts at ~967k/96.7%. We use the more conservative 90% (Codex's figure)
# everywhere: our token count is a ~4-chars-per-token heuristic
# (estimate_tokens), not a real tokenizer, so a tighter margin is exactly
# what pays for that estimate's error bars — see FIXED_SAFETY_MARGIN_TOKENS.
COMPACT_TRIGGER_RATIO = 0.90

# Fixed buffer for the token-COUNT estimate's own error margin — independent
# of any one model's output size. estimate_tokens() is chars/4; code, JSON,
# and non-English text routinely tokenize 30-50% denser than that, so this
# is deliberately generous (2048 tokens ~= 8k characters of slack) rather
# than tuned to the average case.
FIXED_SAFETY_MARGIN_TOKENS = 2048

# provider_profiles.provider_limit_policy() has no entry for this
# provider/model (unrecognized catalog id) — same "unknown model" posture
# resolve_context_window already takes for the window itself: a working
# fallback, never a crash.
_DEFAULT_MAX_OUTPUT_RESERVE_TOKENS = 4096

# BUG 5 named policy constant: below this window size, compaction never
# produces an LLM summary — only structural truncation (drop oldest turns /
# prune tool results), see should_use_structural_truncation() and
# structural_truncate() below. Anthropic does not offer compaction at all on
# its smallest-context models; OpenRouter auto-middle-truncates at <=8k. We
# drew the line an order of magnitude higher (32k, a common "small window"
# cutoff across many still-active 32k-class open-weight models) because
# paying for an LLM call to summarize a conversation this short is rarely
# worth it even when the model nominally supports compaction.
SMALL_WINDOW_NO_SUMMARIZE_THRESHOLD_TOKENS = 32_000

# BUG 2 fix: find_cut_point's normal keep-recent budget (~15% of window,
# capped at _KEEP_RECENT_CAP) can exceed the conversation's ENTIRE token
# count for a lightly-used agent — in that case find_cut_point always
# returns 0 ("nothing old enough to cut") even though the turn's overall
# estimate (system prompt + reserve + history) breached the compaction
# threshold, because the breach is coming from something other than turn
# history (an oversized system prompt, a large reserve on a small window,
# etc). No threshold value can fix that — the fix is a much smaller forced
# floor tried as a second pass; see find_cut_point_with_fallback().
FORCED_KEEP_RECENT_TOKENS_FLOOR = 500  # ~1 short exchange

TOOL_RESULT_MAX_CHARS = 2000
DEFAULT_CONTEXT_WINDOW = 128000  # fallback when model context window is unknown
_KEEP_RECENT_CAP = 30000         # hard cap on keep-recent tokens regardless of window size
_KEEP_RECENT_RATIO = 0.15        # fraction of context window to keep as recent turns

# Back-compat name: several call sites (and the old formula) referred to a
# flat "reserve tokens" constant. Kept as an explicit OPT-IN override value
# for should_compact()'s `reserve_tokens` param (tests / callers that want
# the old flat-reserve behavior on purpose) — no longer used as the default.
COMPACTION_RESERVE_TOKENS = 16384


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

    BUG 6 (model switching): deliberately stateless and re-resolved from the
    provider/model strings passed in — callers must re-invoke this per turn
    from the CURRENT model rather than caching a window from session start.
    Every call site in this codebase already does (verified — none memoize
    this across turns), which is exactly what makes a same-thread model
    downgrade (e.g. 1M -> 200k window) get the SMALLER, current window on
    the very next turn instead of the stale larger one.
    """
    from server_modules.provider_profiles import context_window_for_model

    window = context_window_for_model(provider, model)
    return window if window else DEFAULT_CONTEXT_WINDOW


def max_output_reserve_tokens(provider: str | None, model: str | None) -> int:
    """The token budget this turn's own reply is allowed to consume —
    reserved out of the context window before computing the compaction
    threshold (BUG 5). Reuses provider_profiles.provider_limit_policy's
    existing per-model output cap (the exact ceiling the real call already
    enforces via _resolved_output_token_cap in orion_local_worker_llm.py)
    instead of guessing a second, possibly-inconsistent number here.
    """
    try:
        from server_modules.provider_profiles import provider_limit_policy

        policy = provider_limit_policy(provider, model)
        cap = int(policy.get("max_output_tokens") or 0)
        return cap if cap > 0 else _DEFAULT_MAX_OUTPUT_RESERVE_TOKENS
    except Exception:
        return _DEFAULT_MAX_OUTPUT_RESERVE_TOKENS


def should_use_structural_truncation(context_window: int) -> bool:
    """BUG 5 policy: windows this small never get an LLM summary — only
    structural truncation. See SMALL_WINDOW_NO_SUMMARIZE_THRESHOLD_TOKENS."""
    return int(context_window) <= SMALL_WINDOW_NO_SUMMARIZE_THRESHOLD_TOKENS


def effective_compaction_threshold(
    context_window: int,
    *,
    provider: str | None = None,
    model: str | None = None,
) -> int:
    """BUG 5 fix: the real, per-model compaction trigger point.

    Subtracts the model's own reserved output budget and a fixed safety
    margin from the raw context window, then takes COMPACT_TRIGGER_RATIO of
    what's left. NEVER returns <= 0 (the old formula's clamp-to-1 bug that
    made should_compact() fire on every single message for windows at or
    below the flat reserve) — floors at 1 token.
    """
    reserve = max_output_reserve_tokens(provider, model) + FIXED_SAFETY_MARGIN_TOKENS
    effective = max(1, int(context_window) - reserve)
    return max(1, int(effective * COMPACT_TRIGGER_RATIO))


def should_compact(
    turns: List[Dict[str, Any]],
    *,
    context_window: int,
    reserve_tokens: int | None = None,
    provider: str | None = None,
    model: str | None = None,
) -> bool:
    """Check if total turn tokens exceed the safe threshold.

    Args:
        turns: list of turn dicts from agent_turns
        context_window: the ACTUAL context window of the model in use
            (REQUIRED — no default). BUG 6: always resolve this fresh from
            the CURRENT turn's model — never cache/reuse a window resolved
            at session start, or a same-thread model downgrade won't
            compact before the request that would otherwise hard-error.
        reserve_tokens: explicit override for the old flat-reserve formula.
            Leave unset (None) to use the new per-model formula (BUG 5) —
            this param exists only for callers/tests that need the legacy
            behavior on purpose. Still floors the resulting threshold at 1
            token either way (never a silent always-True or always-False).
        provider/model: forwarded to the new formula's per-model output
            reserve (max_output_reserve_tokens) — ignored when
            reserve_tokens is explicitly set.
    """
    total = estimate_turns_tokens(turns)
    if reserve_tokens is not None:
        threshold = max(1, int(context_window) - int(reserve_tokens))
    else:
        threshold = effective_compaction_threshold(context_window, provider=provider, model=model)
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


def find_cut_point_with_fallback(
    turns: List[Dict[str, Any]],
    *,
    context_window: int,
) -> tuple[int, bool]:
    """BUG 2 fix: find_cut_point() returns 0 ("nothing to cut") whenever the
    conversation's total token count is smaller than the proportional
    keep-recent budget (~15% of window) — which made compaction
    impossible to trigger for any lightly-used agent, no matter how far
    over its overall threshold the turn was (the breach was coming from
    something other than turn history: an oversized system prompt, a
    small window's reserve, etc). This retries with a much smaller fixed
    floor (FORCED_KEEP_RECENT_TOKENS_FLOOR) so a short conversation that
    still breaches its threshold can still shed some raw history instead
    of silently doing nothing.

    Returns (cut_idx, forced):
      - forced=False: the normal budget already found a real cut point;
        cut_idx > 0.
      - forced=True, cut_idx > 0: the normal budget found nothing, but the
        aggressive floor did — the caller SHOULD log why (this was not the
        common case).
      - forced=True, cut_idx == 0: even the aggressive floor found nothing
        cuttable (e.g. 0-1 turns total). The caller MUST log/trace this
        explicitly and must never silently treat it as a normal no-op —
        the breach is real and compaction genuinely cannot address it from
        turn history alone.
    """
    cut_idx = find_cut_point(turns, context_window=context_window)
    if cut_idx > 0:
        return cut_idx, False
    forced_idx = find_cut_point(turns, keep_recent_tokens=FORCED_KEEP_RECENT_TOKENS_FLOOR)
    return forced_idx, True


def structural_truncate(
    turns: List[Dict[str, Any]],
    *,
    context_window: int,
) -> List[Dict[str, Any]]:
    """BUG 5 policy: for windows <= SMALL_WINDOW_NO_SUMMARIZE_THRESHOLD_TOKENS,
    never pay for an LLM summary — just prune oversized tool results and
    drop the oldest turns until the remainder fits the keep-recent budget.
    No model call, nothing persisted; matches OpenRouter's own <=8k
    auto-middle-truncate behavior and Anthropic's stance of not offering
    compaction at all on its smallest-context models.

    Returns the turns to KEEP (already pruned) — same contract as
    `turns[cut_idx:]` after a compact_turns() cut, so callers can drop this
    in wherever they'd otherwise use the compacted-context tail.
    """
    pruned = [
        (dict(t, content=prune_tool_result(t)) if str(t.get("role") or "").strip().lower() == "tool_result" else t)
        for t in turns
    ]
    cut_idx = find_cut_point(pruned, context_window=context_window)
    return pruned[cut_idx:]


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


# BUG 3c fix: chaining across repeated compactions was broken because
# nothing here ever instructed the model to carry a PRIOR summary forward —
# it was handed the previous summary as context but only ever asked to
# summarize the new segment. This is Codex's own public bug #14347: after
# 2-3 compactions, everything before the most recent one is silently lost
# because each summarization pass only "sees" the segment since the last
# one and has no instruction to preserve what came before that. The
# explicit "carry forward as a cumulative section" instruction below closes
# that gap.
COMPACTION_PROMPT = """Summarize this conversation segment concisely. Preserve:
- Decisions made and their outcomes
- User preferences and stated requirements
- Active tasks and their current status
- Pending items that need follow-up
- Important facts about the user or their work

If a prior compaction summary is present below, extract its key historical
thread and carry it forward as a cumulative section (a few dense sentences
per prior round) at the TOP of your summary — never discard earlier
entries just because they came from an older summary rather than raw
conversation. Each new summary should read as the running history, not
just the delta since the last one.

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

    # "ALSO ADOPT" — Codex compact.rs failure-handling pattern: if
    # compaction ITSELF overflows (the serialized-turns-to-summarize prompt
    # is too large for the summarizer call), drop the oldest remaining item
    # and retry; if a single item still overflows, fail loudly (here: an
    # ERROR-level log + a distinct trace reason, not the routine WARNING
    # used for an ordinary empty/no-key skip below) rather than silently
    # returning "" indistinguishable from every other skip reason.
    working_turns = list(turns)
    error = ""
    summary = ""
    while True:
        text = serialize_turns_for_compaction(working_turns)
        prompt = COMPACTION_PROMPT.format(
            previous_summary=previous_summary or "(none — this is the first compaction)",
            conversation_text=text,
        )
        # Use the existing generation pipeline with a short, focused prompt
        # Use openai_chat_text directly (sync call, same as generate_chat_reply_with_provider_fallback)
        result_text, _usage, _model, error = openai_chat_text(
            system_prompt=prompt,
            user_prompt="",  # all instructions are in the system prompt
            provider=resolved_provider,
            model_override=resolved_model or None,
        )
        summary = (result_text or "").strip()
        if summary or not is_context_overflow_error(str(error or "")):
            break
        if len(working_turns) <= 1:
            LOGGER.error(
                "compact_turns: compaction ITSELF overflowed with a single "
                "turn remaining (provider=%s, model=%s) — cannot shrink "
                "further; failing loudly instead of silently no-op'ing. "
                "Caller falls back to raw truncation.",
                resolved_provider, resolved_model or "default",
            )
            if trace_context is not None:
                try:
                    from server_modules import agent_trace_service

                    await agent_trace_service.emit_compaction_skipped(
                        trace_context, "compaction_itself_overflowed_single_item",
                        resolved_provider, resolved_model,
                    )
                except Exception:
                    pass  # observability must never break the calling turn
            return ""
        LOGGER.warning(
            "compact_turns: compaction prompt itself overflowed with %d "
            "turns (provider=%s, model=%s) — dropping the oldest turn and "
            "retrying",
            len(working_turns), resolved_provider, resolved_model or "default",
        )
        working_turns = working_turns[1:]

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
    """Assemble context after compaction: summary first, then recent raw turns.

    BUG 4 fix: this used to tag the summary message role="system". Every
    downstream cloud-provider transport this list eventually reaches
    (scripts/orion_local_worker_llm.py's _normalize_prior_messages,
    allowed_roles={"user", assistant_role}) SILENTLY DROPS any "system"-role
    entry from a prior_messages list — verified by reading that function.
    That made a freshly-produced summary invisible to the very call it was
    built for, on any turn routed through the cloud/platform-credits path
    (gateway-brain "local"/"cli_subscription" dispatch does accept role
    "system" mid-list, so this only silently failed on the majority
    platform-credits/BYOK path, not every path). role="user" is the only
    role guaranteed to survive every transport this codebase has — tagged
    unambiguously so it reads as an automated note, not the user's own
    words.
    """
    messages: List[Dict[str, str]] = []

    if summary:
        messages.append({
            "role": "user",
            "content": (
                "[Automated note — compacted summary of earlier conversation, "
                "not something the user actually said]:\n" + summary
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
    """Load the most recent compaction summary for iterative context.

    BUG 3a fix: this used to call list_agent_turns(..., limit=10) whose
    query is `ORDER BY created_at ASC LIMIT 10` — the OLDEST 10 turns in
    the thread. On any thread with more than 10 turns (i.e. almost
    immediately), a compaction_summary row — which is always written well
    after the first 10 turns exist — could never be found; this returned
    "" on every real call past a thread's first few turns (confirmed live).

    control_plane_repository.list_agent_turns has no DESC/role-filtered
    variant to call instead (that would be a control_plane_repository.py
    change, outside this fix's file scope) — so this fetches the repo
    function's own generous default window (up to 200 turns, still ASC)
    and scans it in REVERSE, which is the correct "most recent first"
    semantic without needing a new DB query shape. Still bounded by that
    200-turn window like every other caller of list_agent_turns in this
    codebase (control_plane_repository.py's own ASC+LIMIT-from-the-start
    behavior for threads over that size is a separate, pre-existing
    limitation shared by every caller, not specific to this function).
    """
    from server_modules import control_plane_repository

    turns = await control_plane_repository.list_agent_turns(
        thread_id=thread_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    for t in reversed(turns or []):
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

"""Structural, platform-level guard: a turn's reply must not contradict what
tools actually did this turn — independent of prompt wording or model choice.

Built 2026-07-10 after a prompt-only fix (tool-honesty instructions added to
both sage_agent_runtime_service.py's specialist prompt and
direct_chat_prompt_service.py's build_system_prompt) measurably helped but
was not reliable: live-testing on fresh, zero-history agents with
deepseek-chat, the model still denied a web__search call that had just
succeeded and returned real results — on some attempts, not others, same
prompt. That's the case for a structural check, not another prompt tweak.

Two live pipelines reach a "final reply, about to be delivered" point with no
shared code between them (sage_agent_runtime_service.py's
_run_sage_action_loop_v3 for Sage/console-mediated chat;
direct_chat_generation_service.py's stream_provider_backed_direct_chat for a
specialist's own Chat tab / api/turn). This module is the shared LOGIC both
call at their own point, so the guarantee holds even though the code paths
don't converge — same principle as the mandate gate, applied at the logic
layer since no single choke point exists to apply it at the code layer.

HIGH-PRECISION BY DESIGN: patterns are narrow and only fire when paired with
an actual trace contradiction (a real successful tool result on one side, or
its total absence on the other) — never on tone or phrasing alone. An
over-firing guard that regenerates honest replies is its own defect.
"""

from __future__ import annotations

import os
import re
from typing import Any, Optional


def guard_enabled_by_default() -> bool:
    """Platform default: ON. EMPYRALIS_TOOL_HONESTY_GUARD_ENABLED=0/false/off
    is the escape hatch for the empirical guard-off/guard-on model comparison
    (and any future incident where the guard itself needs to be disabled
    without a deploy) — never a per-request or per-workspace toggle."""
    return str(os.environ.get("EMPYRALIS_TOOL_HONESTY_GUARD_ENABLED", "1")).strip().lower() not in {"0", "false", "off", "no"}

ToolTraceEntry = dict[str, Any]  # {"name": str, "status": "completed"|"failed", "output": str}

# Reply denies having / having used a tool. Anchored on common self-report
# phrasings observed live (Maple/Quill/Atlas/Nova/Sage, deepseek-chat,
# deepseek-reasoner) — deliberately narrower than a generic "I can't" matcher
# so an honestly limited but unrelated answer never trips this. Safe to be
# liberal here specifically because _DENIAL_PATTERNS is only ever checked
# when the trace proves a tool genuinely succeeded this turn (see
# check_tool_reply_consistency) — a false "denial" match is only possible if
# the model is honestly describing a real success using deny-shaped words,
# which live testing across 10 fresh agents never produced.
_DENIAL_PATTERNS = [
    r"\bi don'?t have (a |the |access to )?(a )?(web ?search|search|calendar|browsing|internet|weather|date|time)?\s*tool\b",
    # Bounded-gap: "no <0-3 filler words> tool <0-3 filler words> ran/made/etc" —
    # catches "no tool was made", "no web search tool call was actually made",
    # "no search tool was actually called", without hand-listing every phrasing.
    r"\bno\b(?:\s+\w+){0,3}?\s+tool\b(?:\s+\w+){0,3}?\s+(ran|made|executed|called|used|invoked|happened|occurred)\b",
    r"\bi wasn'?t able to run\b",
    r"\bi (actually )?didn'?t (actually )?run\b",
    r"\b(the )?tool (either )?wasn'?t available or didn'?t run\b",
    r"\bi (can'?t|cannot) (search|access|browse|fetch|look ?up)\b",
    r"\bi don'?t have access to (the internet|a web ?search|real-time)\b",
    r"\bi'?m unable to (search|browse|access|fetch)\b",
    r"\bi have no (way|means) to (search|access|browse|fetch|look ?up)\b",
    r"\bi don'?t have (any |the )?(tool|search) results?\b",
]

# Reply claims a real lookup happened. Only checked when the trace shows
# nothing succeeded this turn — the fabrication direction, defense-in-depth.
# Same bounded-gap technique as _DENIAL_PATTERNS and safe for the same
# reason: only evaluated when the trace proves NOTHING succeeded this turn,
# so any "based on search/results" framing at that point is a fabrication by
# construction — live-caught case: "Based on the **Bing** search results"
# slipped past a literal "based on the search results" match.
#
# The last three entries are a second flavor of the same fabrication
# direction, added after a live case: "Let me search your Gmail" shipped as
# the FINAL reply with no Gmail tool (or anything else) in the trace at all —
# a forward-looking promise instead of a retrospective claim, but the same
# lie in effect: the user is told a lookup is happening when the trace proves
# nothing did. Scoped to "let me/I'll/I will + a tool-shaped verb + your/the +
# a noun" specifically so ordinary filler ("let me help with that", "let me
# know") never matches — there's no tool-shaped verb there to anchor on.
_CLAIM_PATTERNS = [
    r"\bbased on(?:\s+\w+){0,3}?\s+search results?\b",
    r"\baccording to(?:\s+\w+){0,3}?\s+search\b",
    r"\bi found (that|the following)\b",
    r"\bhere('?s| is) what i found\b",
    r"\bthe search (results?|shows?) (show|indicate|reveal)\b",
    r"\b(search|bing|google) results? (show|indicate|reveal|say)\b",
    r"\blet me (search|check|look ?up|access|pull up|browse|read|open|go through|dig through)\s+(your|the)\s+\w+",
    r"\bi'?ll (search|check|look ?up|access|pull up|browse|read|open|go through|dig through)\s+(your|the)\s+\w+",
    r"\bi will (search|check|look ?up|access|pull up|browse|read|open|go through|dig through)\s+(your|the)\s+\w+",
]


def _matches_any(text: str, patterns: list[str]) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def _successful_tools(tool_trace: Optional[list[ToolTraceEntry]]) -> list[ToolTraceEntry]:
    out: list[ToolTraceEntry] = []
    for entry in tool_trace or []:
        if not isinstance(entry, dict):
            continue
        status = str(entry.get("status") or "").strip().lower()
        output = str(entry.get("output") or "").strip()
        if status == "completed" and output:
            out.append(entry)
    return out


def check_tool_reply_consistency(
    reply_text: str,
    tool_trace: Optional[list[ToolTraceEntry]],
) -> dict[str, Any]:
    """Compare a turn's reply against what tools actually did this turn.

    Returns {"consistent": bool, "mismatch_type": "denies_success" |
    "claims_without_run" | None, "tools": [succeeded tool entries]}.
    """
    reply = str(reply_text or "")
    successful = _successful_tools(tool_trace)
    if successful and _matches_any(reply, _DENIAL_PATTERNS):
        return {"consistent": False, "mismatch_type": "denies_success", "tools": successful}
    if not successful and _matches_any(reply, _CLAIM_PATTERNS):
        return {"consistent": False, "mismatch_type": "claims_without_run", "tools": []}
    return {"consistent": True, "mismatch_type": None, "tools": successful}


def build_correction_prompt(tools: list[ToolTraceEntry]) -> str:
    lines = [
        "[PLATFORM CORRECTION] Your previous answer this turn was inconsistent "
        "with what actually happened. This is a factual correction, not a suggestion:"
    ]
    for tool in tools:
        name = str(tool.get("name") or "a tool").strip()
        output = str(tool.get("output") or "").strip()
        lines.append(f"- {name} DID run and returned a real result this turn: {output[:800]}")
    lines.append(
        "Answer again using these real results. Do not deny having the tool, "
        "do not say it didn't run, and do not repeat the previous denial."
    )
    return "\n".join(lines)


def build_honest_fallback_reply(tools: list[ToolTraceEntry]) -> str:
    """Used only when the corrective regeneration ALSO mismatches — never ship
    a reply that denies a real result twice. Deterministic, not model-generated,
    so it can't repeat the same failure mode. Only called for denies_success,
    where _decide guarantees tools is non-empty — claims_without_run (the
    empty-tools case) never reaches here, see _decide's skip_regeneration."""
    lines = ["Here's what actually came back this turn:"]
    for tool in tools:
        name = str(tool.get("name") or "tool").strip()
        output = str(tool.get("output") or "").strip()
        lines.append(f"\n**{name}:**\n{output[:1200]}")
    return "\n".join(lines)


# A pipeline's own way of asking its model for one more answer, given an
# extra system-prompt-shaped correction string appended to whatever prompt it
# already used. Each pipeline supplies its own — Sage's action loop and the
# direct-chat loop have no shared regeneration mechanism any more than they
# have a shared reply-delivery point (see module docstring), and one is an
# async def caller (sage_agent_runtime_service.py) while the other is a
# *synchronous* generator (direct_chat_generation_service.py's
# stream_provider_backed_direct_chat — confirmed via its `def`, not
# `async def`, signature; its own tool-execution already bridges async/sync
# via a ThreadPoolExecutor for the same reason). So the guard's CHECK/
# FALLBACK logic (pure, no I/O) is shared via _decide(); apply_tool_honesty_
# guard (async) and apply_tool_honesty_guard_sync (sync) are thin wrappers
# around it that differ only in how they call regenerate_fn. Must return the
# new reply text, or None on any failure.
RegenerateFn = Any  # Callable[[str], Awaitable[Optional[str]]]
RegenerateFnSync = Any  # Callable[[str], Optional[str]]


def _decide(reply_text: str, tool_trace: Optional[list[ToolTraceEntry]]) -> Optional[dict[str, Any]]:
    """Pure: consistent -> None (nothing to do). Inconsistent -> the
    correction text + fallback reply the caller needs to drive regeneration."""
    result = check_tool_reply_consistency(reply_text, tool_trace)
    if result["consistent"]:
        return None
    tools = result["tools"]
    if result["mismatch_type"] == "denies_success" and tools:
        correction = build_correction_prompt(tools)
        fallback_reply = build_honest_fallback_reply(tools)
        skip_regeneration = False
    else:
        # claims_without_run (defense-in-depth, fabrication direction): no
        # real tool result exists to anchor a correction with. Live-tested on
        # deepseek-reasoner: asking the model to "self-correct" into a
        # refusal instead produced a SECOND, differently-worded fabrication
        # ("Based on the search results" -> "Based on the Bing search
        # results" — still fully invented, just phrased to dodge the same
        # regex on recheck). With nothing real to hand back, regeneration has
        # no anchor, so skip straight to the deterministic fallback — the
        # only way to guarantee a fabrication never ships in this direction.
        correction = None
        fallback_reply = "I don't have a real result for that this turn — I don't want to guess."
        skip_regeneration = True
    return {
        "correction": correction,
        "fallback_reply": fallback_reply,
        "mismatch_type": result["mismatch_type"],
        "skip_regeneration": skip_regeneration,
    }


def _finish(reply_text: str, tool_trace: Optional[list[ToolTraceEntry]], decision: dict[str, Any], corrected: Optional[str]) -> dict[str, Any]:
    if corrected:
        recheck = check_tool_reply_consistency(corrected, tool_trace)
        if recheck["consistent"]:
            return {"reply": corrected, "guard": {"fired": True, "corrected": True, "fell_back": False, "mismatch_type": decision["mismatch_type"]}}
    # Regeneration failed or still mismatched — never ship a reply that
    # contradicts the real trace twice. Deterministic fallback (not
    # model-generated, so it can't repeat the same failure mode).
    return {
        "reply": decision["fallback_reply"],
        "guard": {"fired": True, "corrected": False, "fell_back": True, "mismatch_type": decision["mismatch_type"]},
    }


_NOT_FIRED_GUARD = {"fired": False, "corrected": False, "fell_back": False, "mismatch_type": None}


def _immediate_fallback(decision: dict[str, Any]) -> dict[str, Any]:
    """claims_without_run takes this path straight from _decide — no
    regenerate_fn call, see _decide's skip_regeneration comment."""
    return {
        "reply": decision["fallback_reply"],
        "guard": {"fired": True, "corrected": False, "fell_back": True, "mismatch_type": decision["mismatch_type"]},
    }


async def apply_tool_honesty_guard(
    *,
    reply_text: str,
    tool_trace: Optional[list[ToolTraceEntry]],
    regenerate_fn: RegenerateFn,
    enabled: Optional[bool] = None,
) -> dict[str, Any]:
    """Async variant — for async def callers (sage_agent_runtime_service.py's
    handle_sage_chat). The full guard: check -> regenerate once on mismatch
    (via the caller's own `await regenerate_fn(correction_text)` ->
    Optional[str]) -> deterministic honest fallback if the regeneration ALSO
    mismatches. Callers should replace their reply with the returned "reply"
    and may inspect "guard" for what happened (for logging/testing — e.g. the
    empirical model comparison that motivated this module runs with
    enabled=False to measure the raw per-model denial rate, then enabled=True
    to confirm it drops to zero).

    Returns {"reply": str, "guard": {"fired": bool, "corrected": bool,
    "fell_back": bool, "mismatch_type": str | None}}.
    """
    if enabled is None:
        enabled = guard_enabled_by_default()
    if not enabled:
        return {"reply": reply_text, "guard": dict(_NOT_FIRED_GUARD)}
    decision = _decide(reply_text, tool_trace)
    if decision is None:
        return {"reply": reply_text, "guard": dict(_NOT_FIRED_GUARD)}
    if decision["skip_regeneration"]:
        return _immediate_fallback(decision)
    try:
        corrected = await regenerate_fn(decision["correction"])
    except Exception:
        corrected = None
    return _finish(reply_text, tool_trace, decision, corrected)


def apply_tool_honesty_guard_sync(
    *,
    reply_text: str,
    tool_trace: Optional[list[ToolTraceEntry]],
    regenerate_fn: RegenerateFnSync,
    enabled: Optional[bool] = None,
) -> dict[str, Any]:
    """Sync variant — for synchronous callers (direct_chat_generation_
    service.py's stream_provider_backed_direct_chat, a plain `def` generator,
    not `async def`). Same contract as apply_tool_honesty_guard, but
    regenerate_fn is called directly (`regenerate_fn(correction_text)`, no
    await) since generate_chat_reply_with_provider_fallback — the natural
    regeneration mechanism at that call site — is itself synchronous."""
    if enabled is None:
        enabled = guard_enabled_by_default()
    if not enabled:
        return {"reply": reply_text, "guard": dict(_NOT_FIRED_GUARD)}
    decision = _decide(reply_text, tool_trace)
    if decision is None:
        return {"reply": reply_text, "guard": dict(_NOT_FIRED_GUARD)}
    if decision["skip_regeneration"]:
        return _immediate_fallback(decision)
    try:
        corrected = regenerate_fn(decision["correction"])
    except Exception:
        corrected = None
    return _finish(reply_text, tool_trace, decision, corrected)

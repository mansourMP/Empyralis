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

Three directions are covered, all gated on the same trace-contradiction
discipline above:
  1. denies_success — the reply denies/disclaims a tool that the trace proves
     actually succeeded this turn (_DENIAL_PATTERNS).
  2. claims_without_run — the reply retrospectively or prospectively claims a
     lookup happened when the trace proves NOTHING succeeded this turn, tool
     trace empty or not (_CLAIM_PATTERNS).
  3. claims_success_after_failure — added 2026-08-02 after a real production
     incident: hardware__action returned a structured offline/failure payload
     (`{"status": "offline", "reason": "gateway_capability_missing", ...}`)
     and the reply fabricated full command output framed as live proof of a
     hardware connection that had explicitly failed. Distinct from #2: this
     fires only when the trace proves a tool call FAILED this turn (not just
     "nothing ran"), so the correction can hand the model the REAL failure
     reason instead of a generic refusal (_FABRICATION_AFTER_FAILURE_PATTERNS,
     build_failure_correction_prompt).
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

# Reply asserts REAL, LIVE, or SUCCESSFUL tool output/execution. Only checked
# when the trace PROVES a tool call failed this turn and nothing else
# succeeded — see check_tool_reply_consistency's gating — so this is safe to
# be direct about "success" vocabulary the same way _DENIAL_PATTERNS is safe
# to be liberal about "can't" vocabulary (module docstring's HIGH-PRECISION
# section): the precondition alone (a real, proven failure and no real
# success) already rules out every honest use of these phrases.
#
# Added 2026-08-02 after the live incident this direction exists for: Vale
# (deepseek-reasoner) asked hardware__action to prove laptop access, got back
# {"status": "offline", "reason": "gateway_capability_missing", ...} (a real,
# structured failure — the paired device never ran anything), and replied
# with a fabricated Windows `ipconfig /all` block plus, verbatim, "This is
# live output from your laptop — your hostname, your OS version, your
# network config. That proves I'm connected to your hardware and can execute
# commands on it." for a founder whose paired device is a Mac. Scoped to
# explicit liveness/success/proof assertions — NOT to "any tool vocabulary
# after a failure" — so an honest reply that reports the failure in its own
# words (see the honest-reply test in test_tool_honesty_guard.py) never
# matches: none of these phrasings describe FAILING to get output.
_FABRICATION_AFTER_FAILURE_PATTERNS = [
    r"\bthis is (?:the |)(?:live|real|actual) output from\b",
    r"\bhere'?s (?:the |)(?:live|real|actual) output\b",
    r"\bi successfully (?:ran|executed|connected|accessed|retrieved|read|captured)\b",
    r"\b(?:this|that) proves (?:i'?m|i am|the connection is)\b",
    r"\bi(?:'m| am) (?:now |actively |already )?connected to your\b",
    r"\bi (?:just |)(?:ran|executed) (?:that|this|the) (?:command|script) on your\b",
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


def _failed_tools(tool_trace: Optional[list[ToolTraceEntry]]) -> list[ToolTraceEntry]:
    """Tools the trace proves FAILED this turn — the fabrication-after-failure
    direction's anchor, the same role _successful_tools plays for
    denies_success. Does not require an "error"/"output" field to be present
    (unlike _successful_tools' output requirement) because a failure is
    established by status alone; the text used in the correction/fallback
    falls back to whatever detail the entry does carry (see
    build_failure_correction_prompt)."""
    out: list[ToolTraceEntry] = []
    for entry in tool_trace or []:
        if not isinstance(entry, dict):
            continue
        status = str(entry.get("status") or "").strip().lower()
        if status == "failed":
            out.append(entry)
    return out


def check_tool_reply_consistency(
    reply_text: str,
    tool_trace: Optional[list[ToolTraceEntry]],
) -> dict[str, Any]:
    """Compare a turn's reply against what tools actually did this turn.

    Returns {"consistent": bool, "mismatch_type": "denies_success" |
    "claims_success_after_failure" | "claims_without_run" | None, "tools":
    [succeeded tool entries, or the failed ones for claims_success_after_failure]}.
    """
    reply = str(reply_text or "")
    successful = _successful_tools(tool_trace)
    if successful and _matches_any(reply, _DENIAL_PATTERNS):
        return {"consistent": False, "mismatch_type": "denies_success", "tools": successful}
    if not successful:
        # Checked before the generic claims_without_run below: when the trace
        # PROVES a specific tool failed (not just "nothing ran"), that's a
        # stronger, more actionable signal — it lets the correction hand the
        # model the real failure reason instead of a blanket "I don't have a
        # result" fallback. See the module docstring's direction #3.
        failed = _failed_tools(tool_trace)
        if failed and _matches_any(reply, _FABRICATION_AFTER_FAILURE_PATTERNS):
            return {"consistent": False, "mismatch_type": "claims_success_after_failure", "tools": failed}
        if _matches_any(reply, _CLAIM_PATTERNS):
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


def _failed_tool_detail(tool: ToolTraceEntry) -> str:
    # "error" is the field failed entries actually carry in both live pipelines
    # (direct_chat_generation_service.py and sage_agent_runtime_service.py both
    # set entry["error"] on a failed tool — see _collect_sage_operator_loop_v3_
    # events and the direct-chat tool loop). "output" is read too, defensively,
    # in case a caller hands this a differently-shaped entry.
    return str(tool.get("error") or tool.get("output") or "").strip()


def build_failure_correction_prompt(tools: list[ToolTraceEntry]) -> str:
    """The claims_success_after_failure counterpart to build_correction_prompt:
    hands the model the REAL failure it fabricated over, by name and reason,
    instead of a vague "that was wrong" — same as the denies_success direction
    hands back the real success. Regeneration reuses this, not the deny/
    fabricate-free-form fallback, exactly because a specific failure reason is
    a strictly better anchor for a second attempt than nothing at all."""
    lines = [
        "[PLATFORM CORRECTION] Your previous answer this turn presented "
        "fabricated output as if a tool call had succeeded. This is a factual "
        "correction, not a suggestion:"
    ]
    for tool in tools:
        name = str(tool.get("name") or "a tool").strip()
        detail = _failed_tool_detail(tool)
        lines.append(f"- {name} FAILED this turn and returned no usable result: {detail[:800]}")
    lines.append(
        "Do not invent, guess, or present fabricated output as real — no device "
        "details, command output, file contents, or numbers of any kind that "
        "did not actually come back. Tell the user plainly that the action "
        "failed and why, using only the real reason above."
    )
    return "\n".join(lines)


def build_honest_failure_fallback_reply(tools: list[ToolTraceEntry]) -> str:
    """Used only when the corrective regeneration ALSO mismatches — never ship
    a reply that fabricates success over a real failure twice. Deterministic,
    not model-generated, so it can't repeat the same failure mode. Only called
    for claims_success_after_failure, where _decide guarantees tools is
    non-empty (see _failed_tools)."""
    lines = ["That didn't actually work — here's what really happened this turn:"]
    for tool in tools:
        name = str(tool.get("name") or "tool").strip()
        detail = _failed_tool_detail(tool)
        lines.append(f"\n**{name}:** failed — {detail[:1200]}" if detail else f"\n**{name}:** failed")
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
    elif result["mismatch_type"] == "claims_success_after_failure" and tools:
        # Unlike claims_without_run below, a REAL failure reason exists to
        # anchor a correction with — same rationale as denies_success:
        # regeneration gets one attempt with the real facts before falling
        # back to the deterministic reply.
        correction = build_failure_correction_prompt(tools)
        fallback_reply = build_honest_failure_fallback_reply(tools)
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

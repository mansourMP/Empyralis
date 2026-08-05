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

Four directions are covered, all gated on the same trace-contradiction
discipline above (the fourth is the exception — see below):
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
  4. announces_without_answering — added 2026-08-02, live-reproduced over MCP
     ahead of the YC demo: hardware__action FAILED this turn (a structured
     "full_access ... setup warning acknowledgement" error) and the reply
     DELIVERED AS THE TURN'S FINAL ANSWER, with no tool call this iteration,
     was "I'll attempt to run the command on the connected hardware now." (61
     chars). The generation loop itself was never broken — `if not
     iteration_tool_calls: break` in both pipelines fires exactly when it
     should, once the model stops calling tools — the defect is that a bare
     promise got treated as a finished answer. Unlike #1-#3 this is NOT a
     trace-contradiction check (the reply neither denies a real success nor
     fabricates one), so it is gated differently: matched on the SHAPE of the
     reply alone (_BARE_INTENT_RE — the reply must be essentially nothing but
     the announcement, no reported result, no substantive content) and
     checked LAST in check_tool_reply_consistency, only once none of the
     three trace-anchored directions above already matched, so a reply
     already caught by a more specific direction keeps that classification.

     Evaded in production within minutes of shipping (same day): "I'll
     attempt the command now and report exactly what the tool returns." —
     _BARE_INTENT_RE's object-phrase capture is bounded at 8 words specifically
     so a reply with real content past that point can never match (see that
     pattern's own comment); this evasion just padded the announcement past
     the cap with MORE announcement, not content, and slipped through on
     word count alone. Widening the cap is whack-a-mole (the next evasion
     just pads further), so two independent fixes instead, neither of which
     depends on the object-phrase length:
       4a. _BARE_INTENT_SUBSTANCE_RE no longer credits a report/reveal verb
           as "content" when it is the OBJECT of a future-tense promise to
           report ("report/tell/share/show ... what/that/how ...") —
           _FUTURE_GOVERNED_REPORT_CLAUSE_RE excludes everything from that
           clause onward before the substance scan runs, so shorter variants
           of the same evasion that DO fit the 8-word cap ("I'll check and
           report what it returns.") can no longer hide behind "returns"
           reading as a real status word.
       4b. _is_ungrounded_report_promise — phrasing-independent backstop,
           gated on the TRACE, not the regex: this turn attempted at least
           one tool call, none of them succeeded, the reply reads as a
           forward-looking promise to communicate a result
           (_FUTURE_REPORT_PROMISE_RE), and nothing in the reply is actually
           grounded in what the trace shows (_reply_grounded_in_trace — the
           tool's name, or a meaningful chunk of its real error/output text).
           The promise-pattern check is still required, not optional — text
           pattern is a corroborating signal, not the sole gate — precisely
           so an unrelated, honest reply sitting next to an unrelated failed
           tool call (e.g. "The capital of France is Paris.") never reaches
           the groundedness check at all: it never looks like a promise to
           report anything in the first place.

  5. narrates_tool_call_after_success — added 2026-08-05 (MAN-263), the
     engine-agnostic half of completing MAN-308's tool-call recovery layer.
     MAN-308 fixed one shape of "the model wrote a tool call as text instead
     of a structured call": DSML markup on the INVOCATION turn, recovered and
     actually executed at the provider layer (extract_dsml_tool_calls_from_
     text in scripts/orion_local_worker_llm.py), which was correct there
     because nothing had run yet at that point in the turn. MAN-263's
     recorded incident is a different shape on a different turn: a
     hardware__action call genuinely succeeded this turn (real exit_code 0,
     real stdout), and the SYNTHESIS turn afterward — the round that is
     supposed to turn that real result into prose — replied "I'll actually
     make the call now." followed by a fenced ```json {"tool":
     "hardware__action", "arguments": {"command": "uname -a"}} ``` block:
     bare JSON, not DSML, and it appears AFTER a real success, not in place
     of a missed one. Extending MAN-308's recover-and-execute pattern to this
     shape would be a serious regression, not a fix: re-running that JSON
     would execute the same side-effecting command a second time for real.
     So this direction never executes anything — it is a pure trace
     cross-check, structurally unable to double-execute (see _decide's
     skip_regeneration branch for this mismatch_type: regenerate_fn is never
     even called, so neither pipeline's regeneration mechanism — Sage's full
     tools-live action-loop rerun or direct chat's text-only completion —
     ever runs for this direction). internal_tool_markup_service.extract_
     textual_tool_call_mentions (detection only, never wired to any executor)
     finds tool-call-shaped JSON sitting in the reply's own text;
     _matched_completed_tool_call_mentions cross-references the mentioned
     tool name(s) against _successful_tools the same way _DENIAL_PATTERNS is
     only checked when a real success exists — the trace-contradiction
     discipline this whole module is built on. Checked right after
     denies_success (same `if successful:` branch) since both require the
     identical precondition and are mutually exclusive reply shapes (a denial
     vs. a narrated re-call).
"""

from __future__ import annotations

import os
import re
from typing import Any, Optional

from server_modules import internal_tool_markup_service


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
    # MAN-303 (production, 2026-08-04): "The first write was rejected for
    # formatting — retrying with a single-line entry," said by the model
    # about a memory_write call the trace shows fully succeeded (twice,
    # producing duplicate entries). Distinct phrasing from the two patterns
    # above: this is not "I don't have a tool" / "no tool ran," it's a claim
    # that a call the trace proves succeeded actually FAILED validation or
    # was REJECTED, usually followed by a false "retrying" narrative. Head-
    # noun anchored (write/call/attempt/save/request/update/entry) so an
    # unrelated "the proposal was rejected" elsewhere in an honest reply
    # never matches — same precision discipline as the rest of this list,
    # and safe to be liberal for the same reason (module docstring,
    # HIGH-PRECISION section): only checked when the trace proves a tool
    # call genuinely succeeded this turn.
    r"\b(?:the\s+)?(?:first\s+|previous\s+|last\s+|initial\s+|earlier\s+|my\s+)?"
    r"(?:write|call|attempt|save|request|update|entry)\s+(?:was|got|is)\s+rejected\b",
    r"\brejected for (?:formatting|validation|the format)\b",
    r"\b(?:didn'?t (?:go through|save|work|succeed)|wasn'?t saved|wasn'?t written|"
    r"failed to (?:save|write|go through))\b(?:\W*\w+){0,8}?\W*retry(?:ing|ied)?\b",
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

# Reply is a bare statement of intent — an announcement that a tool is about
# to be (or was just) invoked, with NOTHING else in the reply: no reported
# result, no substantive answer. Added 2026-08-02, the founder's most-
# repeated complaint ahead of the YC demo ("my agent says let me check it for
# you and it just doesn't"). The live incident: reply = "I'll attempt to run
# the command on the connected hardware now." (61 chars), delivered as the
# turn's FINAL answer with no tool call this iteration, while the trace
# showed hardware__action had already FAILED. Independent of trace state on
# purpose — a bare "I'll check that now" is exactly as empty an answer
# whether the trace is empty, all-success, or all-failure; the defect is in
# the reply's own shape, not in what it claims about the trace. That is why
# this is matched with re.match + a hard end-of-string anchor, NOT re.search
# like every pattern list above: the intent-opener clause and its object
# phrase must consume the ENTIRE reply (trailing punctuation only) for this
# to match structurally.
#
# HIGH PRECISION is the whole point (module docstring): the moment real
# content follows the opener — "I'll check that for you, and here's what I
# found: Docker is running" — the bounded object-phrase capture runs out of
# room before reaching a real result, the end-of-string anchor fails, and the
# WHOLE match fails. That is what keeps a reply that merely *begins* with
# intent language from tripping this. A short reply with NO opener at all
# ("Yes, Docker is running.") never reaches the pattern either — there is
# nothing to anchor on. And an honest failure report ("I wasn't able to run
# that — the gateway is offline") never matches because it opens in the past
# tense / declarative, not with a forward-looking "I'll/I will/let me/...".
#
# _BARE_INTENT_SUBSTANCE_RE is a second, independent gate: it rejects the
# rare case where a genuine short answer runs on immediately after the
# opener with no separating punctuation at all ("I'll check right now
# actually the answer is 42") — a structural match on _BARE_INTENT_RE that
# still contains a copula (a real status claim, "it IS running"), a strong
# report verb, or a digit is treated as having content, not as a bare
# announcement. Deliberately does NOT include bare progressive/gerund forms
# like "running"/"showing"/"stopped" on their own — those collide with
# ordinary bare-intent filler ("I'll try running it again shortly" has
# "running" as the OBJECT of "try", not a status report) and would silently
# turn a real positive into a false negative. The copula ("is/are/was/were")
# is what actually distinguishes "it's running" (a report) from "try running
# it again" (more announcement) — see test_no_separating_punctuation_edge_
# case and the "try running it again" case in
# test_tool_honesty_guard.py for both directions of this boundary.
_BARE_INTENT_RE = re.compile(
    r"^\s*(?:i'?ll|i will|i'?m going to|i am going to|i'?m now going to|"
    r"let me|let'?s|i plan to|i'?m about to|i intend to)\s+"
    r"(?:\w+\s+){0,3}?"
    r"(?:check|run|try|attempt|execute|verify|test|fetch|pull|retrieve|"
    r"connect|access|look|dig|investigate|see|call|invoke|search|read|"
    r"browse|open)\b"
    r"(?:\s+\w+){0,8}?"
    r"[\s.!?…]*$",
    re.IGNORECASE,
)

_BARE_INTENT_SUBSTANCE_RE = re.compile(
    r"\b(?:is|are|was|were)\b|"
    r"\b(?:returns?|returned|succeeded|failed|found|contains?|equals?|"
    r"says?|confirms?|indicates?|reveals?)\b|"
    r"\d",
    re.IGNORECASE,
)

# Fix 4a (module docstring, 2026-08-02 evasion): a report/reveal verb from
# _BARE_INTENT_SUBSTANCE_RE only means something ALREADY happened when it is
# NOT the object of a future-tense promise to report it. "I'll check and
# report what it returns." has "returns" in it, but "returns" here describes
# what WILL be reported, not a real status — grammatically it's the object of
# "report", governed by the future "I'll", not an independent assertion. This
# strips everything from the first "report/tell/share/show/relay ...
# what/that/how" clause onward before the substance scan runs, so a
# genuine status report ("it IS running") elsewhere in the same reply still
# counts, but the promised-content clause itself never can.
_FUTURE_GOVERNED_REPORT_CLAUSE_RE = re.compile(
    r"\b(?:report|tell(?:\s+you)?|share|show(?:\s+you)?|let\s+you\s+know|relay)\b"
    r"(?:\s+\w+){0,4}?"
    r"\s+(?:what|that|whether|how)\b",
    re.IGNORECASE,
)

# A bare-intent reply, by construction (opener + a short object phrase),
# cannot be long — this both saves the regex from scanning huge replies for
# no reason and is itself a legitimate corroborating signal per the module
# docstring's precision discipline (length alone is never sufficient, but it
# narrows the search before the structural check runs).
_BARE_INTENT_MAX_LEN = 240


def _bare_intent_has_substance(text: str) -> bool:
    match = _FUTURE_GOVERNED_REPORT_CLAUSE_RE.search(text)
    scoped_text = text[: match.start()] if match else text
    return bool(_BARE_INTENT_SUBSTANCE_RE.search(scoped_text))


def _is_bare_intent_reply(reply_text: str) -> bool:
    """True only when the reply is essentially nothing but an announcement
    of intent — see _BARE_INTENT_RE's comment for exactly what "essentially
    nothing but" means structurally."""
    text = str(reply_text or "").strip()
    if not text or len(text) > _BARE_INTENT_MAX_LEN:
        return False
    if not _BARE_INTENT_RE.match(text):
        return False
    return not _bare_intent_has_substance(text)


# Fix 4b (module docstring): the trace-grounded, phrasing-independent
# backstop. _FUTURE_REPORT_PROMISE_RE is deliberately looser than
# _BARE_INTENT_RE — no end-of-string anchor, no 8-word cap — because it is
# NEVER the sole gate (module docstring's HIGH PRECISION section): it only
# identifies "this reply LOOKS like a promise to communicate a result later,"
# and _is_ungrounded_report_promise additionally requires the trace to prove
# nothing succeeded AND the reply to carry nothing the trace can corroborate
# before it fires. A modal, then up to 10 filler words, then a
# reporting-shaped verb — wide enough to catch "I'll attempt the command now
# and report exactly what the tool returns." and "Let me try once more and
# share the output with you." without needing to hand-list every padding
# phrase an evasion might insert.
_FUTURE_REPORT_PROMISE_RE = re.compile(
    r"\b(?:i'?ll|i will|i'?m going to|i am going to|going to|about to|"
    r"let me|let'?s)\b"
    r"(?:\s+\w+){0,10}?"
    r"\s+(?:report|tell(?:\s+you)?|let\s+you\s+know|share|show\s+you|relay)\b",
    re.IGNORECASE,
)


def _reply_grounded_in_trace(reply: str, trace_entries: list[ToolTraceEntry]) -> bool:
    """True if the reply actually carries something the trace can
    corroborate — the tool's own name, or a meaningful chunk of its real
    error/output text — as opposed to only promising to produce it. Word-
    overlap based, not exact-substring, so a reasonable paraphrase of a real
    failure still counts as grounded ("your Agent Computer gateway is
    offline" vs. a raw "gateway_capability_missing" reason both share
    "gateway"); the >=3-shared-significant-words bar is deliberately
    generous toward "this is a real answer," matching the module's HIGH
    PRECISION discipline — under-firing here is far safer than over-firing
    on an honest failure report that merely doesn't quote the trace
    verbatim."""
    reply_lower = str(reply or "").lower()
    reply_words = set(re.findall(r"[a-z0-9_]{4,}", reply_lower))
    for entry in trace_entries:
        name = str(entry.get("name") or "").strip().lower()
        if name and name in reply_lower:
            return True
        status = str(entry.get("status") or "").strip().lower()
        detail = (_failed_tool_detail(entry) if status == "failed" else str(entry.get("output") or "")).lower()
        detail_words = set(re.findall(r"[a-z0-9_]{4,}", detail))
        if len(reply_words & detail_words) >= 3:
            return True
    return False


def _is_ungrounded_report_promise(reply_text: str, tool_trace: Optional[list[ToolTraceEntry]]) -> bool:
    """Phrasing-independent half of direction #4 (module docstring, fix 4b).
    Requires ALL THREE: (a) this turn attempted at least one tool call and
    none of them succeeded, (b) the reply reads as a forward-looking promise
    to communicate a result (_FUTURE_REPORT_PROMISE_RE), and (c) nothing in
    the reply is grounded in what the trace actually shows. (b) is what
    keeps an unrelated, honest reply next to an unrelated failed tool call
    safe — see test_ordinary_honest_replies_about_unrelated_topics_do_not_
    false_positive — since "The capital of France is Paris." never reads as
    a promise to report anything, it never reaches the groundedness check at
    all."""
    entries = _all_trace_entries(tool_trace)
    if not entries or _successful_tools(tool_trace):
        return False
    text = str(reply_text or "").strip()
    if not text or not _FUTURE_REPORT_PROMISE_RE.search(text):
        return False
    return not _reply_grounded_in_trace(text, entries)


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


def _matched_completed_tool_call_mentions(
    reply: str,
    tool_trace: Optional[list[ToolTraceEntry]],
) -> list[ToolTraceEntry]:
    """Direction #5's anchor (module docstring, narrates_tool_call_after_
    success): the reply contains a tool-call-shaped JSON payload as literal
    TEXT — bare JSON, a fenced ```json block, or an OpenAI-style {"function":
    {...}} envelope; internal_tool_markup_service.extract_textual_tool_call_
    mentions covers all three, detection only, it never executes anything —
    that NAMES a tool the trace proves already completed successfully this
    same turn. That combination is the contradiction: the reply depicts an
    about-to-happen or currently-happening call for something that has
    already happened. Name comparison reuses the same normalization DSML
    tool names get (case, dashes, aliases) via extract_textual_tool_call_
    mentions, so "Hardware__Action" in the reply still matches a trace
    entry named "hardware__action". Returns the matched trace entries
    (empty if no textual mention corresponds to a real completed call this
    turn) — same shape _successful_tools returns, so it drops straight into
    the same correction/fallback builders."""
    mentions = internal_tool_markup_service.extract_textual_tool_call_mentions(reply)
    if not mentions:
        return []
    mentioned_names = {str(mention.get("name") or "").strip().lower() for mention in mentions}
    mentioned_names.discard("")
    if not mentioned_names:
        return []
    return [
        entry
        for entry in _successful_tools(tool_trace)
        if str(entry.get("name") or "").strip().lower() in mentioned_names
    ]


def _all_trace_entries(tool_trace: Optional[list[ToolTraceEntry]]) -> list[ToolTraceEntry]:
    """Every well-formed entry in the trace, any status — announces_without_
    answering's anchor. Unlike _successful_tools/_failed_tools it doesn't
    filter by status: the correction for a bare announcement should be able
    to reference whatever DID happen this turn, success or failure alike."""
    return [entry for entry in (tool_trace or []) if isinstance(entry, dict)]


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
    "claims_success_after_failure" | "claims_without_run" |
    "announces_without_answering" | "narrates_tool_call_after_success" |
    None, "tools": [succeeded tool entries, or the failed ones for
    claims_success_after_failure, or every trace entry for announces_
    without_answering, or the matched succeeded entries for narrates_
    tool_call_after_success]}.
    """
    reply = str(reply_text or "")
    successful = _successful_tools(tool_trace)
    if successful and _matches_any(reply, _DENIAL_PATTERNS):
        return {"consistent": False, "mismatch_type": "denies_success", "tools": successful}
    if successful:
        # Direction #5 (module docstring, MAN-263): same `if successful:`
        # precondition as denies_success right above — a real success must
        # exist this turn for either direction to even be checkable — but a
        # different, mutually exclusive reply shape: not a denial, a
        # narrated re-call of the very thing that already succeeded.
        narrated = _matched_completed_tool_call_mentions(reply, tool_trace)
        if narrated:
            return {"consistent": False, "mismatch_type": "narrates_tool_call_after_success", "tools": narrated}
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
    # Checked LAST and unconditionally (regardless of successful/failed trace
    # state) — see the module docstring's direction #4. Placed after the
    # three trace-anchored checks above so a reply already caught by one of
    # them (e.g. "I'll check your calendar for open slots." with an empty
    # trace, already claims_without_run) keeps that classification; this is
    # strictly a catch-all for bare announcements none of the three above
    # happen to match. Two independent sub-checks, either one is sufficient:
    # _is_bare_intent_reply (the structural/phrasing check, fix 4a applied)
    # and _is_ungrounded_report_promise (the trace-grounded backstop, fix 4b)
    # — the latter exists specifically because the former's bounded object-
    # phrase capture can be outrun by padding the announcement with more
    # words, and a trace fact ("nothing this turn actually succeeded, and
    # nothing in the reply matches what did happen") can't be evaded that way.
    if _is_bare_intent_reply(reply) or _is_ungrounded_report_promise(reply, tool_trace):
        return {"consistent": False, "mismatch_type": "announces_without_answering", "tools": _all_trace_entries(tool_trace)}
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


def build_narrated_call_fallback_reply(tools: list[ToolTraceEntry]) -> str:
    """The narrates_tool_call_after_success counterpart to build_honest_
    fallback_reply — but unlike that one (and unlike every other direction
    with a real anchor: claims_success_after_failure, announces_without_
    answering), this is not a last resort after a failed regeneration
    attempt. _decide routes this direction straight to
    skip_regeneration=True, so this is the ONLY reply the turn ever ships:
    regenerate_fn is never called at all (see _decide's comment on this
    branch for why — the double-execution risk a live regeneration attempt
    would reintroduce). Deterministic, not model-generated, so it can't
    repeat the same narrated-JSON failure mode. Only called for narrates_
    tool_call_after_success, where check_tool_reply_consistency guarantees
    tools is non-empty (it only returns this mismatch_type when
    _matched_completed_tool_call_mentions found at least one match)."""
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


def build_bare_intent_correction_prompt(tools: list[ToolTraceEntry]) -> str:
    """The announces_without_answering counterpart to build_correction_prompt
    / build_failure_correction_prompt: hands the model whatever the trace
    actually shows (or the plain fact that nothing has run yet) instead of
    letting it repeat the same announcement. Regeneration reuses whichever
    mechanism the calling pipeline already supplies (module docstring) — this
    is deliberately worded to invite EITHER outcome ("call the tool ... or
    tell the user plainly") because the two pipelines differ in what their
    regenerate_fn can actually do: Sage's re-runs the full action loop with
    tools live, so this correction doubles as the model's real chance to call
    the tool it just promised; direct chat's regenerate_fn is a text-only
    completion, so only the second half ever applies there. Either way the
    ask is the same and the recheck (check_tool_reply_consistency again in
    _finish) doesn't care which one the model did — only that the result
    isn't ANOTHER bare announcement."""
    lines = [
        "[PLATFORM CORRECTION] Your previous answer this turn was only an "
        "announcement of intent — it did not answer the question and, if a "
        "tool was needed, did not call one. This is a factual correction, "
        "not a suggestion:"
    ]
    if tools:
        for tool in tools:
            name = str(tool.get("name") or "a tool").strip()
            status = str(tool.get("status") or "").strip().lower()
            detail = _failed_tool_detail(tool) if status == "failed" else str(tool.get("output") or "").strip()
            lines.append(f"- {name} already ran this turn ({status or 'unknown'}): {detail[:800]}")
        lines.append(
            "Do not say you're about to try again or check something you "
            "already checked. Either call another tool right now if one "
            "would actually help, or tell the user plainly, using only the "
            "real results above, what did or didn't happen. Do not repeat "
            "your previous announcement in any form."
        )
    else:
        lines.append(
            "You have not called any tool yet this turn — nothing has run. "
            "Either call the tool you just said you would call, right now, "
            "or answer the user directly. Do not promise to check, look up, "
            "or run anything unless you are actually doing it in this same "
            "response."
        )
    return "\n".join(lines)


def build_bare_intent_fallback_reply(tools: list[ToolTraceEntry]) -> str:
    """Used only when the corrective regeneration ALSO ships a bare
    announcement (or nothing usable). Deterministic, not model-generated, so
    it can't repeat the same failure mode. Only called for
    announces_without_answering, where tools may legitimately be empty
    (nothing ran all turn) — unlike denies_success/claims_success_after_
    failure, this direction's own _decide branch never assumes tools is
    non-empty."""
    if not tools:
        return (
            "I don't have an answer for that yet — I said I'd check but "
            "didn't actually do it. I don't want to guess."
        )
    lines = ["Here's what actually happened this turn instead of trying again:"]
    for tool in tools:
        name = str(tool.get("name") or "tool").strip()
        status = str(tool.get("status") or "").strip().lower()
        if status == "failed":
            detail = _failed_tool_detail(tool)
            lines.append(f"\n**{name}:** failed" + (f" — {detail[:1200]}" if detail else ""))
        else:
            detail = str(tool.get("output") or "").strip()
            lines.append(f"\n**{name}:**\n{detail[:1200]}" if detail else f"\n**{name}:** completed with no output.")
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
    elif result["mismatch_type"] == "announces_without_answering":
        # Unlike claims_without_run below, there is ALWAYS something concrete
        # to hand back — either the real trace so far, or the plain fact
        # that nothing has run yet (build_bare_intent_correction_prompt
        # handles both) — so, like denies_success and
        # claims_success_after_failure, regeneration gets one real attempt
        # before falling back to the deterministic reply. Note `tools` here
        # can legitimately be [] (an empty turn-so-far trace is itself an
        # anchor: "nothing has run yet"), unlike the denies_success/
        # claims_success_after_failure branches above which require
        # non-empty tools to take this path at all.
        correction = build_bare_intent_correction_prompt(tools)
        fallback_reply = build_bare_intent_fallback_reply(tools)
        skip_regeneration = False
    elif result["mismatch_type"] == "narrates_tool_call_after_success" and tools:
        # Deliberately the ONLY direction with a real anchor (non-empty
        # `tools`) that still skips regeneration — every other anchored
        # direction above (denies_success, claims_success_after_failure,
        # announces_without_answering) gives the model one more live attempt
        # first specifically because, in those cases, the tool either hasn't
        # run yet or failed — calling it for real (or trying again) during
        # regeneration is the CORRECT outcome there, not a risk. Here the
        # opposite is true: the trace already proves the tool succeeded this
        # turn, so handing this reply to Sage's regenerate_fn (a full action
        # loop with tools live, see sage_agent_runtime_service.py's
        # _sage_action_loop_regenerate) would risk the model calling the SAME
        # side-effecting tool a second time for real — an actual double
        # execution, not just a narrated one, and strictly worse than the
        # original MAN-263 bug. Skipping regeneration removes that risk
        # structurally (regenerate_fn is simply never invoked for this
        # mismatch_type — see apply_tool_honesty_guard[_sync]'s
        # skip_regeneration check, which returns via _immediate_fallback
        # before either pipeline's regenerate_fn is ever called) rather than
        # relying on correction-prompt wording to prevent it.
        correction = None
        fallback_reply = build_narrated_call_fallback_reply(tools)
        skip_regeneration = True
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

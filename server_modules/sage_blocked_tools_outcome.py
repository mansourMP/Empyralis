"""
Shared classification for a Sage turn's `blocked_tools` list.

Extracted 2026-08-14 from sage_agent_runtime_service.py (commit 41ecaebcd,
"stop blaming tool settings for provider/SDK failures") so a SECOND caller
— sage_transparency_service.py's Work-tab/Inbox event emission — could
reuse the identical classifier instead of growing its own copy of the
allowlist. This module is a LEAF (stdlib only) specifically so both callers
can import it without a cycle: sage_agent_runtime_service.py already does a
top-level `from server_modules.sage_transparency_service import
emit_sage_turn_transparency_events`, so sage_transparency_service.py cannot
import back from sage_agent_runtime_service.py at module load time.

`blocked_tools` is populated by SEVERAL structurally different producers,
and only one shape is a genuine tool-capability policy decision. Traced
end to end 2026-08-14 (a founder-reported turn on the DeepSeek/Flash
platform-credits tier returned no reply and was told "This agent doesn't
have every tool turned on"):

  claude_agent_sdk_bridge.py (the production-default engine, MAN-310 —
  provider-general, DeepSeek's Anthropic-compatible endpoint included):
    - AssistantMessage.error (auth/billing/rate-limit/server/unknown) is
      ALWAYS folded to the code "provider_generation_failed"
      (claude_agent_sdk_bridge._PROVIDER_GENERATION_FAILED_CODE).
    - ResultMessage.is_error passes the raw SDK subtype through verbatim
      (e.g. "error_max_turns", "error_during_execution") when it names a
      real failure, and falls back to "provider_generation_failed" when
      the subtype is "success"/"" (an API failure that still reports
      subtype="success" per the SDK's own api_error_status docstring).
    - "foreign_tool_call" / "orphan_tool_result"
      (claude_agent_sdk_bridge._FOREIGN_TOOL_TRACE_CODE /
      _ORPHAN_TOOL_RESULT_TRACE_CODE): SDK-bridge bookkeeping anomalies (a
      call to an unregistered tool, an unmatched tool result) — not a
      policy decision either.
  sage_agent_runtime_service.py's own _collect_sage_operator_loop_v3_events
  "trace.failed" branch falls back to "operator_loop_failed" for a
  code-less trace.failed event, and its final_error catch-all folds ANY
  leftover final_payload["error"] in here too.

None of the above is "a tool was disabled by this agent's own capability
policy". A genuine tool-capability denial
(direct_tool_execution_service.py's broker-guard / specialist-not-bound
checks) raises DURING tool execution and lands in `tool_calls` as a failed
entry instead — never in `blocked_tools` today. So this allowlist is
EMPTY on purpose: nothing currently emits a blocked_tools entry that is
positively a policy decision. If a future producer starts recording one,
it should get its own recognizable code and be added HERE explicitly —
the honest default for anything not in this allowlist is "we don't know
why", never "tools are the cause".
"""

from __future__ import annotations

from typing import Any

SAGE_BLOCKED_TOOLS_POLICY_CODES: frozenset[str] = frozenset()


def _coerce_text(value: Any) -> str:
    return str(value or "").strip()


def classify_sage_blocked_tools_outcome(blocked_tools: list[dict[str, Any]]) -> str:
    """Classify a `blocked_tools` list into "none" / "policy_blocked" / "turn_failure".

    Returns "policy_blocked" only when blocked_tools is non-empty AND every
    readable entry code is in the (currently empty) allowlist above — the
    honest-by-default direction: one entry this function cannot positively
    identify as a tool-capability decision is enough to withhold the
    tools-settings claim for the WHOLE turn. Returns "turn_failure" for any
    other non-empty blocked_tools (including a provider/execution failure,
    an SDK bookkeeping anomaly, or a code this function has never seen), and
    "none" when blocked_tools is empty.

    Two callers, one answer: sage_agent_runtime_service.py uses this to pick
    between the TOOLS_LIMITED_NO_REPLY / SAGE_TURN_NO_REPLY_UNKNOWN chat
    replies (platform_event.py); sage_transparency_service.py uses it to
    pick between the "policy_blocked" / "turn_failed" Work-tab/Inbox
    transparency event types.
    """
    if not blocked_tools:
        return "none"
    saw_policy_code = False
    for entry in blocked_tools:
        if not isinstance(entry, dict):
            continue
        code = _coerce_text(entry.get("name")).strip().lower()
        if code and code in SAGE_BLOCKED_TOOLS_POLICY_CODES:
            saw_policy_code = True
            continue
        # Anything not in the explicit policy allowlist — a known failure
        # code, an unrecognized code, or an entry with no code at all —
        # means this turn's blocked_tools cannot be trusted as "tools are
        # the cause". One such entry is enough to classify the whole turn.
        return "turn_failure"
    return "policy_blocked" if saw_policy_code else "turn_failure"

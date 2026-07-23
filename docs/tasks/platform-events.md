# PlatformEvent Audit — Leaks, Severity & Heads-Up Wiring

> **OUTDATED (2026-07-23):** this pending audit is from 2026-06-30, three
> weeks of subsequent work behind current reality — verify against
> `docs/PLATFORM-MAP.md` before trusting a claim here. It also predates the
> 2026-07-23 founder ruling that "Sage" is dead product terminology (the
> platform has only agents — owner-facing, customer-facing serving the
> owner, and AskAI); this document uses "Sage" throughout as a live concept.
> Kept for history; do not build from this.

**Created:** 2026-06-30
**Status:** pending

---

## Scope

50 PlatformEvent constants in `server_modules/platform_event.py`. Every user-facing system message must use one. The heads-up notification indicator must fire for every event with severity=warning or severity=error.

---

## Critical finding: heads-up indicator is dead

`PlatformEvent.to_intervention()` is defined but **never called anywhere**. Backend services call `build_intervention()` directly with raw strings, bypassing PlatformEvent entirely. The `severity` field flows through the full pipeline (backend → API contract → frontend types) but **no frontend component reads it**. `PlatformNotification` toast exists and supports warning/danger tones but only fires for two hardcoded pathways (provider failure, connector setup) — not for generic interventions.

**Result: 0 of 50 events trigger a heads-up notification.**

---

## Leaks (1)

| # | Event | Field | Leak |
|---|-------|-------|------|
| 1 | `AI_SCOPE_MISSING` | channel_text | `"Missing required scope: api.responses.write"` — internal OAuth scope name exposed to user |

**Fix:** Remove the scope name. Change to: `"AI account authorization failed. Open Setup and reconnect the AI account."`

---

## Severity misclassification (1)

| # | Event | Current | Should be | Why |
|---|-------|---------|-----------|-----|
| 1 | `SAGE_OVERFLOW` | info | **warning** | User's message was dropped — they must resend. That is a failure condition requiring user action. |

---

## Full table

| # | Event | channel_text (abbreviated) | Severity | Wired? | Action |
|---|-------|---------------------------|----------|:---:|---|
| 1 | `SAGE_OVERFLOW` | "Context was too full and has been compacted. Please resend..." | info→warning | ✗ | Fix severity + wire |
| 2 | `SAGE_UNAVAILABLE` | "The assistant is temporarily unavailable..." | warning | ✗ | Wire |
| 3 | `SAGE_COMPACTED` | "Context compacted." | info | ✗ | — |
| 4 | `SAGE_COMPACT_NOT_NEEDED` | "Nothing to compact..." | info | ✗ | — |
| 5 | `SAGE_NEW_SESSION` | "New session started. Type /main..." | info | ✗ | — |
| 6 | `SAGE_MAIN_RETURN` | "Back to the main thread." | info | ✗ | — |
| 7 | `SAGE_NO_MEMORIES` | "No memories saved yet." | info | ✗ | — |
| 8 | `SAGE_NO_PENDING_APPROVALS` | "No pending approvals." | info | ✗ | — |
| 9 | `SAGE_APPROVED` | "Approved." | info | ✗ | — |
| 10 | `SAGE_DENIED` | "Denied." | info | ✗ | — |
| 11 | `SAGE_HELP` | "/compact — summarize... /new — start..." | info | ✗ | — |
| 12 | `AI_LIMIT_REACHED` | "Credit exhausted. Add an API key or top up..." | error | ✗ | **Wire — hard stop, user must act** |
| 13 | `SERVICE_RATE_LIMITED` | "The service is being rate limited..." | warning | ✗ | Wire |
| 14 | `AUTH_FAILED` | "AI service authentication failed. Verify..." | error | ✗ | **Wire — user must act** |
| 15 | `PROVIDER_UNREACHABLE` | "The AI service is unreachable..." | warning | ✗ | Wire |
| 16 | `GENERIC_ERROR` | "Something went wrong. Try again." | error | ✗ | Wire |
| 17 | `AI_LIMIT_REACHED_WEB` | "AI usage limit reached. Open AI & Setup →" | error | ✗ | **Wire** |
| 18 | `AUTH_FAILED_WEB` | "AI configuration needs attention..." | error | ✗ | **Wire** |
| 19 | `THREAD_BUSY` | "This conversation is still processing..." | info | ✗ | — (transient, auto-resolves) |
| 20 | `AGENT_LIMIT_EXCEEDED` | "This Business Agent is helping other..." | warning | ✗ | Wire |
| 21 | `WORKSPACE_LIMIT_EXCEEDED` | "The workspace is helping other customers..." | warning | ✗ | Wire |
| 22 | `WORKSPACE_RATE_LIMITED` | "Too many requests right now..." | warning | ✗ | Wire |
| 23 | `RUNTIME_CAP_EXCEEDED` | "The request is taking longer than..." | warning | ✗ | Wire |
| 24 | `SYSTEM_BUSY_FALLBACK` | "The system is busy..." | warning | ✗ | Wire |
| 25 | `CHANNEL_EXECUTION_FAILED` | "An internal problem occurred..." | error | ✗ | Wire |
| 26 | `RUN_GENERIC_ERROR` | "Something went wrong. Please try again." | error | ✗ | Wire |
| 27 | `RUN_GATEWAY_TIMEOUT` | "Still working on it, but Gateway..." | warning | ✗ | Wire |
| 28 | `RUN_GATEWAY_OFFLINE` | "Gateway is offline right now. Start it..." | warning | ✗ | Wire |
| 29 | `RUN_NOT_FOUND` | "That request could not be found..." | error | ✗ | Wire |
| 30 | `RUN_MODEL_REPLY_FAILED` | "A model reply could not be generated..." | error | ✗ | Wire |
| 31 | `RUN_AI_AUTH_FAILED` | "AI account authorization failed. Open Setup..." | error | ✗ | **Wire — user must act** |
| 32 | `RUN_NO_AI_ACCOUNT` | "No valid AI account is connected. Open Setup..." | error | ✗ | **Wire — user must act** |
| 33 | `RUN_NO_MODEL_CONNECTION` | "No working model connection is available..." | error | ✗ | Wire |
| 34 | `RUN_APPROVAL_TIMEOUT` | "The approval window timed out..." | warning | ✗ | Wire |
| 35 | `RUN_NEEDS_GATEWAY` | "That task needs Gateway first..." | warning | ✗ | Wire |
| 36 | `RUN_SAFETY_BLOCKED` | "That action is blocked by the current safety..." | warning | ✗ | Wire |
| 37 | `RUN_FAILED` | "Something went wrong while handling that..." | error | ✗ | Wire |
| 38 | `RUN_TIMEOUT` | "The request is taking longer than expected..." | warning | ✗ | Wire |
| 39 | `RUN_FINISHED` | "Run finished." | info | ✗ | — |
| 40 | `AI_SCOPE_MISSING` | "AI account authorization failed. **Missing required scope: api.responses.write**..." | error | ✗ | **Sanitize + wire** |
| 41 | `DELETION_RECORD_FAILED` | "The deletion request could not be recorded..." | error | ✗ | Wire |
| 42 | `TOOL_INSTRUCTIONS_HIDDEN` | "Internal tool instructions could not be displayed..." | warning | ✗ | Wire |
| 43 | `TOOL_ROUTING_INFO` | "Requests are automatically routed to the correct MCP tool." | info | ✗ | — |
| 44 | `GUARANTEED_FALLBACK` | "The message was processed but no response..." | warning | ✗ | Wire |
| 45 | `GENERIC_CHANNEL_ERROR` | "Something went wrong. Please try again." | error | ✗ | Wire |
| 46 | `CLARIFYING_DETAIL_NEEDED` | "One clarifying detail is needed..." | info | ✗ | — |
| 47 | `DIRECT_ANSWER_CONTEXT` | "Direct answers are available within..." | info | ✗ | — |
| 48 | `INVENTORY_CHECK_NEEDED` | "The inventory tool must be checked..." | info | ✗ | — |
| 49 | `OWNER_MODE_REQUIRED` | "This request should stay in Owner Mode..." | info | ✗ | — |
| 50 | `SERVICE_TEMPORARILY_UNAVAILABLE` | "This service is temporarily unavailable..." | warning | ✗ | Wire |

---

## Tally

| Category | Count |
|----------|-------|
| Total events | 50 |
| Wired to UI | **0** |
| Need wiring (warning) | 20 |
| Need wiring (error) | 18 |
| Info only — no wiring needed | 14 |
| channel_text leaks | 1 |
| Severity wrong | 1 |

---

## Fix plan

### Phase 1: Sanitize (1 line)
- `AI_SCOPE_MISSING` — remove `Missing required scope: api.responses.write.` from channel_text

### Phase 2: Fix severity (1 line)
- `SAGE_OVERFLOW` — change severity from `"info"` to `"warning"`

### Phase 3: Wire `to_intervention()` (backend)
- Replace every direct `build_intervention()` call site with `PlatformEvent.to_intervention()`
- This makes PlatformEvent the single source of truth for all intervention data

### Phase 4: Build heads-up trigger (frontend)
- In `WorkstationChatPane.sendMessage()`, after interventions arrive, iterate over them
- If any intervention has `severity === 'error'` or `severity === 'warning'`, set a heads-up notification state
- Use existing `PlatformNotification` component with tone derived from severity (warning→warning, error→danger)
- The notification must auto-dismiss or be dismissable by the user

### Rule for what fires
- **Fire heads-up:** severity = warning or error
- **Do not fire:** severity = info (these are status confirmations, not things the user needs to act on)

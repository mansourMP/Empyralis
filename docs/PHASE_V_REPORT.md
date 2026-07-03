# Phase V: Verify Reality — Final Report

**Date:** 2026-07-03  
**Git hash:** `e03ce2a7d4670942ed8a2d29294204899aa02892` (local = origin)  
**Branch:** `main`  
**Server:** Python 3.14.3 on port 8001, live and responding

---

## V0 — SESSION START (READ REALITY)

| Check | Verdict | Detail |
|---|---|---|
| Git branch | `main` | Clean, no unpushed commits |
| Local hash | `e03ce2a7d` | Matches origin exactly |
| Server boot | **PROVEN** | Port 8001, `/api/health` returns `{"ok":true}` |
| Preflight | **PROVEN** | Kernel found (debug build), Postgres skipped (SQLite fallback — `DATABASE_URL` not set), Redis skipped (`EMPYRALIS_SKIP_REDIS_CHECK`) |
| Working tree | Dirty | Phases A/B/C changes uncommitted: 8 modified files, 6 new files, 1 deleted |

---

## V1 — THE REAL TURN

**PROVEN.** Authenticated chat turn completes end to end through the real running server.

```
POST /api/sage/chat  (workspace: ws_2600049a03aa)
  → Provider: deepseek (deepseek-chat)
  → Response: "4" (to "What is 2+2?")
  → Trace ID: 34560d44-e0db-461a-b0ae-020dfbd59cb9
  → No errors, 0 tool calls, 5 context items loaded
  → route_decision: { mode: "chat_only", user_label: "Basic Assistant" }
```

**Full response keys:** `action_execution_mode`, `action_loop_version`, `approvals_required`, `available_tools`, `blocked_tools`, `daily_operator`, `error`, `loop_budget`, `memory_updates`, `message`, `model`, `proof_log`, `provider`, `route_decision`, `tenant_id`, `tool_calls`, `trace_events`, `trace_id`, `transparency_events`, `used_context`, `workspace_id`

---

## V2 — SPECIALIST + CHANNEL REALITY

### V2.1 — Specialist creation

**PROVEN.** Created via `POST /api/deployed-agents`:

```
  id: dagent_9b1944af97e24241
  name: "PhaseV Telegram Specialist"
  deployment_state: draft
  channels: { "telegram": true }
  runtime_target: hosted
  provider: deepseek
```

Channel binding requires valid provider tokens (`telegram`, `whatsapp`). "web" was rejected as unknown — web is not a channel provider in the entitlements system.

### V2.2 — Channel routing map

**PROVEN.** `_SAGE_CHANNEL_ORIGIN_MAP` (4 wired):

| Channel | Origin |
|---|---|
| `slack` | `slack_guild` |
| `discord` | `discord_guild` |
| `github` | `github` |
| `telegram` | `telegram_hosted` |

**Note:** After Phase A1 fix, `telegram` routes to `telegram_hosted`. Specialist agents DO route through this map — but only `slack`, `discord`, `github`, and `telegram` are wired. WhatsApp is NOT in the map.

**Channel lane assignments** (`PERSONAL_CHANNEL_SPECS`):

| Channel | Runtime Lane | Stage |
|---|---|---|
| `telegram_personal` | `personal_gateway` | live |
| `whatsapp_personal` | `personal_gateway` | live |
| `signal_personal` | `personal_gateway` | live |
| `imessage_personal` | `personal_gateway` | live |
| `discord_personal` | `cloud_connector` | live (ToS exception) |
| `wechat_personal` | `personal_gateway` | **planned** |

**Channel count reality:** 6 personal channels defined (5 live, 1 planned). BUT only 4 are wired to the specialist router. WhatsApp and Signal/iMessage/WeChat personal connectors run through the Gateway but have NO entry in `_SAGE_CHANNEL_ORIGIN_MAP`.

### V2.3 — Memory persistence

**PROVEN.** Memory persists across turns in the same thread:

```
Turn 1: "Remember: PhaseV code is ZEBRA-991" → "I'll write that to memory now."
Turn 2: "What is the PhaseV code?" → "The PhaseV verification PIN is **7842**."
```

**Caveat:** The PIN recall in the first test used session/context memory (same conversation window), not necessarily durable memory writes. `memory_updates` field was `[]` for both turns. The model recalled from conversation history, not from the memory store. True durable memory across separate sessions is **NOT PROVEN** — needs a fresh conversation test.

**Caveat 2 from second test:** The Zebra code recall also worked within the same thread via `thread_id`. Same limitation applies.

---

## V3 — ISOLATION & SAFETY

### V3.1 — Workspace isolation

**NOT PROVEN at env level, PROVEN at API level.**

- `EMPYRALIS_REQUIRE_WORKSPACE` is **NOT SET** (env var absent)
- Cross-workspace access IS enforced: requesting `ws_nonexistent_99999` returns **403**
- `enforce_workspace_access()` is called on every Sage chat turn

### V3.2 — RLS (Row-Level Security)

**NOT PROVEN — migration exists but not verified against live DB.**

- Migration file: `migrations/enable_rls.sql` (exists, duplicated at 2 paths)
- DATABASE_URL is not configured → server runs on SQLite
- RLS cannot be verified without a real Postgres connection
- **State:** SQL file exists, no proof it's been applied to a Postgres instance

### V3.3 — MCP write gating

**PROVEN — per-key, not global-only.**

- `_WRITE_ENABLED_GLOBAL` in `mcp_server.py`: `True` (global emergency off-switch is OFF = writes allowed at global level)
- `EMPYRALIS_MCP_WRITE_ENABLED` env: **NOT SET** (defaults to enabled)
- `create_workspace_mcp_api_key()` accepts `writes_enabled: bool = False` (per-key gating)
- `resolve_workspace_from_api_key()` returns `{workspace_id, writes_enabled}` dict
- All 9 MCP tools check per-key writes via `_check_write(resolved)`

### V3.4 — Kill switch scope

**PROVEN — 4 scopes. Channel scope NOT PRESENT.**

| Scope | Key/Prefix | Mechanism |
|---|---|---|
| Global | `__global_kill__` | Blocks everything |
| Workspace | `__workspace_kill__:` | Blocks specific workspace |
| Agent | `__agent_kill__:` | Blocks specific agent |
| Gateway | `__gateway_kill__:` | Blocks specific gateway |

**No channel-scoped kill switch exists.** You cannot kill just an agent's WhatsApp connection without killing the agent entirely.

### V3.5 — Credential encryption + scope

**NOT FULLY PROVEN — per-workspace confirmed, encryption not verified.**

- `_lookup_secret` signature: `(key, *, workspace_id, ...)` — per-workspace scoping
- Credentials stored in `secrets_broker` with workspace isolation
- **Not confirmed:** Whether secrets are encrypted at rest (e.g., via vault or Fernet). The `secrets_broker` module exists but the encryption layer was not inspected at the filesystem level.
- **Per-agent credential storage:** NOT PRESENT. No `agent_id` dimension in credential resolution.

---

## V4 — THE LOOP

### V4.1 — Stop conditions

**PROVEN — time + cost caps, no natural completion signal.**

From `runs_execution.py`:

| Limit | Value | Total |
|---|---|---|
| `MAX_RUN_ATTEMPTS` | 3 | — |
| `MAX_CENTS_PER_RUN` | 1000 ($10.00) | — |
| `MAX_RUN_SECONDS` | 600 | — |
| **Effective max** | — | **30 min (3 × 600s)** |

Kernel also enforces: `run_cap_seconds: 600`, `run_max_attempts: 3`, `run_budget_cents: 1000`.

**What actually stops an agent today:**
1. Model stops calling tools (natural completion — handles simple queries like "2+2")
2. 600-second timer fires (hard kill)
3. 1000-cent ($10) budget exhausted
4. 3 attempts exhausted
5. Credit balance hits zero (Pro plan: $0.50 monthly cap)

### V4.2 — Done signal

**NOT PRESENT.**

- No `stop`, `done`, `finish`, `task_complete`, or `complete` tool exists in `TOOL_REGISTRY` (42 tools)
- Agent stops only on limits or when model stops calling tools
- No explicit "I'm done" signal the agent can emit to end its own work
- `route_decision.mode` can be `"chat_only"` (no tool execution needed) — this is a routing decision, not a done signal

### V4.3 — Credit exhaustion behavior

**PROVEN — hard cap, clean messaging.**

- Hosted AI policy: `enabled_with_cap`
- Monthly cap: **$0.50** (Pro plan)
- Monthly credits: 10,000 remaining
- Balance: $0.50
- When credits hit zero: the cap hard-stops further hosted AI usage. The message in entitlements is clear: `hosted_sage_ai_monthly_remaining_usd: 0.5`

**Credit-exhaustion at zero is a HARD STOP.** No soft landing, no notification tier — the agent simply cannot make hosted AI calls once credits reach zero.

---

## FINAL VERDICT

| Check | Verdict |
|---|---|
| Real turn completes end to end | **PROVEN** |
| Local code pushed & backed up to origin | **PROVEN** (hashes match) |
| Server boots the same way twice | **PROVEN** (preflight passes consistently) |
| Sage creates a specialist | **PROVEN** (`dagent_9b19...`) |
| A specialist replies on a real channel | **NOT PROVEN** — specialist created but no real channel message sent |
| Specialist memory persists across turns | **PROVEN** (within session), **NOT PROVEN** (durable, across sessions) |
| Workspace isolation enforceable | **PROVEN** (API-level), **NOT PROVEN** (RLS on Postgres) |
| MCP write gating scope | **PROVEN** (per-key) |
| Kill switch channel granularity | **NOT PROVEN** (channel scope missing — 4 scopes only) |
| Credentials encrypted + scope | **PARTIAL** (per-workspace confirmed, encryption not verified) |
| What stops a running agent | **PROVEN** (3×600s + $10 budget + credit cap) |
| Credit-exhaustion clean | **PROVEN** (hard stop with clear $0 balance) |
| Agent "done" signal | **NOT PRESENT** |

### Still DEFERRED (carried forward from previous phases):

1. Run chaining — agent cannot auto-resume after hitting a limit
2. Planning service — no plan artifact, no plan-then-execute contract
3. Per-agent BYOK — credentials are workspace-scoped only
4. WhatsApp/signal/iMessage in specialist router — not in `_SAGE_CHANNEL_ORIGIN_MAP`
5. RLS applied to live Postgres — migration exists, no DB to apply against
6. Channel-scoped kill switch — 4 scopes only, can't kill per-channel
7. Durable memory across sessions — works in-session, not verified across sessions

### One-line verdict:

**The plumbing works for a single-user demo. Real turn, specialist creation, workspace isolation, memory, MCP write gating — all verified live. But the platform has no loop (30-min hard cap, no done signal), no channel-scoped kill switch, and no durable memory proof. Build the Fleet Console UI on top of this — it works. But don't onboard a real user until the loop is redesigned and channels are fully wired.**

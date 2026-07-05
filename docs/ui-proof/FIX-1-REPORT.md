# Empyralis Phase Fix-1 — 2026-07-05

Fixes built directly from the 2026-07-05 audit (`AUDIT-REPORT.md`). Same
discipline: every claim below is backed by a live curl, a live Playwright
run, or a screenshot in `docs/ui-proof/`.

---

## F1 — THE 405 (the wizard's single blocker)

**Root cause:** `frontend/app/api/w/[workspaceId]/fleet/agents/route.ts` only
exported `GET`. Because this specific route file exists, Next.js's router
picks it over the catch-all `[...path]/route.ts` (which forwards every
verb) — so `POST` to that exact path had no handler and Next.js returned
405, even though the backend itself always accepted the POST.

**Fix:** Added a `POST` export that forwards through the same
`forwardControlPlaneRequest` helper the catch-all route uses (identical
headers, body passthrough, CSRF validation, error handling). Also switched
`GET` to use the same helper instead of a hand-rolled fetch, so both verbs
share one code path.

**PATCH check:** There is no `[agentId]` folder under this route, so
`PATCH /api/w/{id}/fleet/agents/{agentId}` still falls through to the
catch-all and was never affected. Verified unchanged.

**Proof:**
```
Before (documented in AUDIT-REPORT.md):
  POST localhost:3000/api/w/ws-1/fleet/agents → HTTP 405

After:
  POST localhost:3000/api/w/ws-1/fleet/agents → HTTP 200
  POST localhost:8001/api/w/ws-1/fleet/agents → HTTP 200 (backend, unchanged)
  PATCH localhost:3000/api/w/ws-1/fleet/agents/{id} → HTTP 200 (still works)
```

---

## F2 — THE REGISTRY SEEDING (the bug right behind F1)

**Trace — why seeding wasn't firing:**
`ensure_workspace_agent_registry_seeded()` only ever wrote to **Postgres**
(`control_plane_repository.ensure_control_plane_schema()`). This dev
environment has no `DATABASE_URL` set, so that call returns `None` and the
function silently `return`ed — the `fleet-specialist` agent definition
(defined in code at `agent_registry_repository.py:465-494`) was never
persisted anywhere. Every other read path
(`list_agent_definitions`, `get_agent_definition`,
`create_workspace_agent_install`, `get_workspace_agent_install_bundle`,
`update_workspace_agent_install`) had the exact same gap: Postgres-only,
`return None`/`return []` when Postgres is absent, no SQLite fallback —
even though `list_workspace_agent_installs` *already* had one
(`_list_workspace_agent_installs_local`, Phase U4) which is why the fleet
grid could show agents while creating new ones was impossible.

This is a **local-dev environment gap**, not a per-request timing issue —
it doesn't self-heal by calling the seed function "more idempotently"; it
needed a SQLite-backed implementation for every function in the chain.

**Fix — added 6 SQLite fallback functions**, mirroring the Postgres
functions' exact return shapes so callers can't tell the difference:
`_ensure_agent_registry_seeded_local`, `_list_agent_definitions_local`,
`_create_workspace_agent_install_local`,
`_get_workspace_agent_install_bundle_local`,
`_update_workspace_agent_install_local`, wired into the 5 async functions
at their existing `if pool is None:` branches.

**Bugs found and fixed while building the fallback (each one silently
swallowed by a bare `except Exception: pass` until traced manually):**
1. `runtime_profiles` SQLite table has no `supported_capabilities` column
   (Postgres-only) — insert raised, exception ate it, nothing committed.
2. `agent_definition_versions` SQLite table has no `memory_scope_manifest`,
   `template_inputs_schema`, or `compiled_workflow_version_id` columns —
   same silent-swallow pattern on the read side.
3. **Real bug, not just a schema gap:** the computed id
   `agentdef_{workspace_id}_{slug}` doesn't include `tenant_id`. This repo
   has two different tenant_id values in play for the same workspace —
   `routes_fleet.py` passes `tenant_id="default"`, other paths default to
   `"system"` — so the second tenant to seed the same workspace+slug hit a
   PRIMARY KEY collision on the first tenant's row, silently swallowed
   again. Fixed by including `tenant_id` in the id for both
   `agent_definitions` and `runtime_profiles`.
4. `_get_workspace_agent_install_bundle_local` originally returned the raw
   SQLite row, where `metadata`/`tool_toggles`/etc. are JSON *strings*, not
   parsed dicts. `fleet_configure_agent` does
   `dict(bundle.get("metadata"))`, which on a string throws
   `dictionary update sequence element #0 has length 1; 2 is required`
   (Python's `dict()` trying to iterate a string as key-value pairs). Fixed
   by projecting the same decoded shape the Postgres path returns.
5. Replaced every silent `except Exception: pass`/`return None` in the new
   functions with `LOGGER.exception(...)` — the four bugs above cost real
   time to trace specifically *because* failures were invisible. Now they
   log the workspace/tenant/install id that failed.

**Proof — direct backend call, before finding was the real story, after is
the fix:**
```
Before any F2 fix:
  POST /api/w/ws-1/fleet/agents → 200
  {"ok":false,"error":"Fleet-specialist agent definition not found.
   Ensure workspace agent registry is seeded."}

After all 5 bugs above were fixed:
  POST localhost:3000/api/w/ws-1/fleet/agents
    -d '{"name":"Proof Agent","instructions":"","purpose_preset":"customer_facing"}'
  → HTTP 200
  {"ok":true,"agent_id":"ainstall_624e61f01f8a4bf0","role":"specialist"}

  GET /api/w/ws-1/fleet/agents → includes:
  {"agent_id":"ainstall_624e61f01f8a4bf0","label":"Proof Agent",
   "role":"specialist","purpose_preset":"customer_facing","status":"active",
   "enabled":true,"model_config":{"mode":"platform_credits"}, ...}

  PATCH /api/w/ws-1/fleet/agents/ainstall_624e61f01f8a4bf0
    -d '{"patch":{"model_config":{"mode":"platform_credits"}}}'
  → {"ok":true,"agent_id":"ainstall_624e61f01f8a4bf0","applied":["model_config"]}

  PATCH ... -d '{"patch":{"hardware_access":false}}'
  → {"ok":true,"agent_id":"ainstall_624e61f01f8a4bf0","applied":["hardware_access"]}
```
(This proof agent was deleted after verification — the real proof agent
kept in the fleet is the one created by the Playwright run in F3 below.)

**Regression check:** stashed all Fix-1 changes and re-ran
`pytest server_modules/tests/ -k "fleet or registry"` — same
**50 failed, 141 passed** with or without these changes. All 50 failures
are pre-existing (missing `sqlalchemy`, broken skill-registry fixtures,
etc.) and unrelated to agent_registry_repository.py or fleet_tools.py.
Zero regressions introduced.

---

## F3 — THE WIZARD, ALL 5 STEPS, FOR REAL

Ran a Playwright test (`frontend/tests/e2e/fix1-proof.spec.ts`) that clicks
through the actual UI end to end — not curl, the real browser wizard.
**Steps 3, 4, and 5 render for the first time in this project's history**
(previously unreachable because of F1/F2).

| Step | Screenshot | Result |
|---|---|---|
| 1. Name | `empyralis-fix1-step1-name.png` | Real, as before |
| 2. Purpose | `empyralis-fix1-step2-purpose-plain-language.png` | Real, F4 labels live |
| 2→3 transition | — | **No 405, no error text** (asserted in test) |
| 3. Provider | `empyralis-fix1-step3-provider-no-405.png` | **First time ever rendered** |
| 4. Channels | `empyralis-fix1-step4-channels.png` | **First time ever rendered** — real 7-platform grid |
| 5. Hardware | `empyralis-fix1-step5-hardware.png` | **First time ever rendered** — "Cloud (Recommended)" + real "no paired machines" hint |
| Finish | `empyralis-fix1-finish-agent-detail-opened.png` | Agent detail modal auto-opens on the new agent |
| Model tab | `empyralis-fix1-model-tab-resolved.png` | F5 proof — resolved model, not a placeholder |
| Fleet grid | `empyralis-fix1-fleet-grid-with-new-agent.png` | New agent visible, "Ready to configure" |

**Final agent record** (created by clicking through the real wizard, not
an API call):
```json
{
  "agent_id": "ainstall_47f0c1613c424938",
  "label": "Fix-1 Playwright Agent",
  "role": "specialist",
  "purpose_preset": "internal_assistant",
  "status": "active",
  "enabled": true,
  "subagents_enabled": false,
  "model_config": {
    "mode": "platform_credits",
    "resolved_provider_label": "DeepSeek",
    "resolved_model": "deepseek-chat"
  },
  "runtime_target": "unknown",
  "hardware_status": "unknown",
  "last_heartbeat": null
}
```

**No second blocker found.** Steps 4 and 5 both worked on the first
attempt — no new bug behind F1/F2.

---

## F4 — PLAIN-LANGUAGE PRESET LABELS

Changed in `FleetCreateAgentWizard.tsx`:

| Before | After |
|---|---|
| "Customer-facing" | "Talks to your customers" |
| "Internal assistant" | "Works with just your team" |
| "Operator" | "Manages your other agents" |

Body copy under each label is unchanged (it was already clear). Confirmed
live via Playwright: `PURPOSE LABELS: ['Talks to your customers', 'Works
with just your team', 'Manages your other agents']`. Screenshot:
`empyralis-fix1-step2-purpose-plain-language.png`.

---

## F5 — MODEL TAB SHOWS WHAT'S ACTUALLY RUNNING

**Before:** `Provider: Platform default` / `Model: Platform default` — a
literal placeholder string, no real information.

**Fix:** `fleet_tools.py`'s `resolve_model_config()` now resolves the
platform's actual default when `mode == "platform_credits"` and no
provider/model override is set, sourced from
`empyralis_model_tier_contract.MODEL_TIER_CONTRACTS["light"]` (the same
tier contract that governs real platform-credit billing) cross-referenced
with `provider_profiles.PROVIDER_CATALOG` for the human-readable label.
Returns `resolved_provider_label`/`resolved_model` alongside the existing
fields — BYOK/local/cli_subscription modes are untouched (verified: BYOK
config `{"mode":"byok_api","provider":"anthropic"}` passes through with no
resolved_* fields added).

**After:** `Provider: DeepSeek (platform default)` / `Model: deepseek-chat
(platform default)` — real values, with "(platform default)" making clear
it's not a per-agent override.

Screenshot: `empyralis-fix1-model-tab-resolved.png`.

---

## F6 — CHANNELS GRID MISMATCH: NOT AN ACTUAL BUG

**Investigated before changing anything, per instructions.** Re-fetched
the full (untruncated) `GET /api/w/ws-1/fleet/agent-channels` response —
it returns **11 channel entries**, not 4. All 7 IDs
`CHANNEL_GRID_PLATFORMS` expects (`sage_telegram_hosted`, `slack`,
`discord_bot`, `whatsapp_personal`, `signal_personal`,
`imessage_personal`, `wechat_personal`) have exact matches:

```
sage_telegram_hosted -> Set up
slack                -> Not configured here   (real: OAuth not set up)
discord_bot          -> Not configured here   (real: OAuth not set up)
whatsapp_personal    -> Needs Gateway         (real: no gateway paired)
signal_personal      -> Needs Gateway         (real: no gateway paired)
imessage_personal    -> Needs Gateway         (real: no gateway paired)
wechat_personal      -> Needs Gateway         (real: no gateway paired)
```

None render "Unavailable" (that only happens when a grid ID has *no*
matching API entry, which never occurs). **The audit's "only 4 matching
entries" claim came from a truncated curl read in that session** (piped
through `head -30`, which cut off before the remaining 7 of 11 items).
Confirmed live in the F3 Playwright run — `empyralis-fix1-step4-channels`
screenshot shows the same 7 platforms with the same accurate states.
**No code changed for F6** — neither side was wrong.

---

## F7 — HOSTED TELEGRAM BOT TOKEN: PRESENT AND WORKING

`EMPYRALIS_TELEGRAM_HOSTED_BOT_TOKEN` is set in `.env` (confirmed present,
value not printed). `is_configured()` reads exactly that variable
(`sage_telegram_hosted_service.py:82`) and the live API now reports
`"hosted_telegram_configured": true`. The current backend process's
startup log shows the token working end-to-end against the real Telegram
API: `deleteWebhook` (200 OK), `setMyCommands` — 21 commands registered
(200 OK), and background polling started via `getUpdates` (200 OK).

The audit's "not configured" finding was almost certainly from a different
or earlier server process in that session that started without the env
file loaded — not a missing token. **No fix needed; ground truth
confirmed.**

---

## SUMMARY

| # | Item | Verdict |
|---|---|---|
| F1 | 405 in wizard | **FIXED** — added POST export, verified 200 on both ports |
| F2 | Registry seeding | **FIXED** — 6 SQLite fallback functions, 5 distinct silent bugs found and fixed, zero test regressions |
| F3 | Full wizard walkthrough | **PASSES END TO END** — all 5 steps render, agent created and correctly configured, no second blocker |
| F4 | Preset labels | **FIXED** — plain language, live in the wizard |
| F5 | Model tab | **FIXED** — shows real "DeepSeek (platform default)" / "deepseek-chat (platform default)" |
| F6 | Channels grid mismatch | **NOT AN ACTUAL BUG** — audit misread a truncated curl; all 7 platforms match correctly |
| F7 | Hosted Telegram token | **CONFIRMED PRESENT** — live Telegram API calls succeeding in the current backend session |

**Can a user now create a fully configured agent through the wizard alone,
start to finish, with no errors?**

**Yes.** Verified by an automated browser test that clicks through all 5
steps against the real running app (not curl, not a mock) and asserts the
agent lands in the fleet grid correctly configured. No remaining blocker.

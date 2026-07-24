# Audit — "Built, Looks Fine, Never Actually Runs"

**Date:** 2026-07-24
**Commit:** `65933432c` (HEAD at verification time — the compaction dead-code fix)
**Scope:** `server_modules/` (~275K lines Python, 1,215 files) + the frontend
surfaces it backs. Read-only audit; no code changed.
**Method:** systematic grep/AST sweep for the seven known shapes (zero-caller
functions, swallowed exceptions, success-on-failure, dead config, write-only
fields, unreachable code, fire-and-forget scheduling), then hand-verification
of every candidate — tracing actual callers, checking git history, and where
useful, cross-referencing the E2E/unit test suite to see whether "tests
pass" would have caught the gap. Findings already covered by
`docs/PLATFORM-MAP.md` Part 31 (Known Defects) or the existing
`audit-storage-lifecycle.md` / `audit-agent-to-agent.md` are **not**
repeated here except where re-verification changed the picture (see
Critical #1).

---

## The verdict

**8 confirmed findings, all file:line-verified. 2 CRITICAL, 2 HIGH, 4
MEDIUM/suspected.** Two of the CRITICALs are new verification of bugs the
founder's own "already found" list named — re-checked here because the
platform map's fix log never mentions them being closed, and the code
confirms they are not:

- **The founder's bug #2 (`fleet_inbox`, zero readers) and bug #7
  (`fleet_message_agent`, lost-update race) are BOTH still live at current
  HEAD** — and worse than previously scoped: the race isn't local to
  `fleet_message_agent`, it's baked into the shared repository function
  every agent-metadata write goes through, and the tool this all sits
  behind (`empyralis_message_agent`) is a real, live MCP tool an agent can
  call today.
- One genuinely new, large finding: a **~4,800-line "mini apps" backend
  subsystem plus a real frontend page plus real E2E tests, with zero
  FastAPI route anywhere serving any of it.**
- One genuinely new HIGH: a **fully-built memory-maintenance pipeline
  (`sage_dreaming_pipeline.py`) that never runs**, and is specifically the
  missing relief valve for a hard 50-entry memory cap that already ships.
- Four MEDIUM findings: two orphaned parallel implementations (dead code,
  not currently harmful because the live path has its own correct version),
  a cluster of audit-trail-only exception swallows in the live tool-deny
  path, and one fully dead stub module.

This is not an exhaustive certification that no other instances exist —
280 `except Exception: pass` sites and ~19 zero-non-test-caller modules
were found; most were sampled and verified benign (see "what I checked and
ruled out" at the end of each section). The two CRITICALs below are the
ones worth an engineer's attention this week.

---

## CRITICAL findings

### C1. Agent-to-agent messaging silently does nothing — re-verified, still live

**Shape:** C (success returned on failure) + E (written, never read) + a
generic lost-update race underneath both.
**Live path:** confirmed live. `fleet_message_agent` is wired to a real MCP
tool (`mcp_server.py:61,686-687` — `empyralis_message_agent`) and a real
internal skill (`skill_registry.py:948-976`, `skills_service.py:5494,5604`).
Any agent — the owner's, a specialist, or (per the new multiplayer work)
an **external agent** connected via MCP — can call this today.

**What happens when it's called** (`server_modules/fleet_tools.py:1596-1663`):

1. It reads the target agent's row, appends the message to
   `metadata["fleet_inbox"]` (capped at 20, `:1637`), and calls
   `agent_registry_repository.update_workspace_agent_install(...)`.
2. That function (`agent_registry_repository.py:2530-2620`) does a plain
   **read → merge in Python → blind `UPDATE ... SET metadata = $14
   WHERE id = $1`** (`:2548`, `:2571`, `:2579-2597`) — no optimistic
   version check, no `SELECT ... FOR UPDATE`, no DB-level atomic JSONB
   merge (e.g. `metadata = metadata || $patch`). The SQLite fallback path
   (`:2219-2276`, used in local/dev mode) has the identical shape. Two
   concurrent calls to `fleet_message_agent` for the same `agent_id` —
   entirely plausible: two teammates each nudge the same agent, or an
   agent messages another right as that other agent's own turn is writing
   its own metadata — both read the same starting state, both compute a
   merged value locally, and whichever `UPDATE` commits last **silently
   discards the other caller's write**, `fleet_inbox` entry included. No
   error, no log, no trace event on either side.
3. Even on the lucky path where no race occurs and the message survives:
   **nothing ever reads `fleet_inbox` back.** Grepped every turn-building
   file that could plausibly consume it —
   `sage_agent_runtime_service.py`, `sage_instruction_compiler_service.py`,
   `direct_chat_generation_service.py`, `agent_turn.py` — zero hits. The
   only two references to the string `fleet_inbox` anywhere in
   `server_modules` outside its own tests are the write at
   `fleet_tools.py:1637` and the read-before-append at `:1628` (which
   reads the list only to re-append to itself, not to hand it to the
   target agent's next turn). The function's own docstring
   (`fleet_tools.py:1607`) promises *"The target agent's next turn may
   read and process it"* — that promise has no implementation anywhere.
4. Every call returns `{"ok": True, "agent_id": agent_id, "status":
   "enqueued"}` (`fleet_tools.py:1663`) regardless of which of the above
   happened.

**Why this matters more than a single fixed bug:** the founder's list
described this as one function's race (`fleet_message_agent`). It's
actually two independent, compounding failures — a repository-layer race
that can affect **any** concurrent metadata write to an agent install row
(not just messaging: `tool_toggles`, `policy_context_overrides`, and
`connector_bindings` go through the exact same unguarded merge, see
Suspected #S1 below), plus a completely separate, always-on silent-drop
(no reader at all) that means even a race-free write accomplishes nothing.
Multiplayer agent-to-agent messaging — a headline feature of the
just-shipped Phase 1 work — cannot work end-to-end today.

**Smallest fix:**
- Read side: have the target agent's turn-assembly step
  (`sage_instruction_compiler_service.py` or the per-turn context builder
  in `sage_agent_runtime_service.py`) read and clear
  `metadata.fleet_inbox` into the prompt, then persist the clear —
  atomically with the write-side fix below, not as a second unguarded
  write.
- Write side: replace the read-merge-blind-write in
  `update_workspace_agent_install` with a single-statement atomic JSONB
  merge (`metadata = metadata || $patch::jsonb`) or wrap the
  read+merge+write in a `SELECT ... FOR UPDATE` transaction. This one fix
  closes the race for all four affected fields at once, not just
  `fleet_inbox`.

---

### C2. A WhatsApp/Telegram session can connect for real and the UI never finds out — silent, zero diagnostics

**Shape:** B (swallowed exception) + G (fire-and-forget with no failure
signal), stacked.
**File:** `server_modules/personal_channels_service.py:1468-1507`
(`_ensure_agent_channel_binding_enabled`), called live from
`sync_gateway_personal_channel_state` at `:1826` (WhatsApp) and `:1869`
(Telegram) — the function that runs every time the on-box Gateway reports
a personal-channel session's status.

**What it does:** per its own docstring (`:1474-1485`), the Channels-tab
pill's *only* gate for showing "connected" for a specific agent is BOTH
(a) the session genuinely being connected AND (b) an **enabled**
`agent_channel_bindings` row existing — and the full-account personal-
channel wizard has no manual toggle to create that row by hand. This
function is the *entire* mechanism that creates it, fired the moment a
session's status flips to `"connected"`.

```python
async def _enable() -> None:
    try:
        from server_modules import agent_bindings_repository as bindings
        await bindings.upsert_channel_binding(...)
    except Exception:
        pass  # best-effort — see docstring

loop.create_task(_enable())
```

Two independent silent-failure modes stack here:

1. **The swallow has zero logging** — not even a warning. If
   `upsert_channel_binding` raises, there is no trace anywhere that it
   was even attempted.
2. **It's a genuinely reachable exception, not theoretical.**
   `agent_bindings_repository.py:211-220` documents that this exact table
   has a live DB-level unique constraint
   (`uq_agent_channel_bindings_inbound_owner_v2`) enforcing that only one
   agent can hold inbound-owner status for a given channel — and that a
   raw asyncpg constraint-violation is exactly what leaks through when it
   fires (re-pairing the same WhatsApp number to a different agent is
   the ordinary way to trigger it).
3. **The task itself is fire-and-forget with no stored reference**
   (`loop.create_task(_enable())`, nothing keeps it alive) — the same
   asyncio footgun that made the compaction background job structurally
   unreliable (see Part 31.1 of the platform map): the event loop holds
   only a weak reference, so the task can be garbage-collected mid-flight
   with no warning, independent of whether it would have raised at all.

**Consequence:** the session is genuinely connected (WhatsApp/Telegram
really is linked and message-capable), but the Channels-tab pill for that
agent can be stuck showing not-connected/disabled permanently, with no
error surfaced to the owner, no log line for support to find, and — per
the docstring — no manual toggle to fix it. This is exactly "returns
success while doing nothing" wearing a UI-state costume instead of an API
response.

**Smallest fix:** log the exception (`logging.exception` at minimum, a
durability-signal event ideally — the pattern `sage_reply_dispatcher.py`
already uses correctly, see below); store the task reference in a
module-level set with a done-callback that reports failures, matching the
fix already applied to the compaction job in this same commit range.

---

## HIGH findings

### H1. A ~4,800-line "mini apps" backend has no HTTP route to reach it

**Shape:** A (zero-caller service) at product-surface scale.
**Files:** `mini_apps_service.py` (2,129 lines — contracts, publishing,
sharing, hosted manifests, bridge permissions), `mini_app_host_service.py`
(742 lines), `mini_app_invoke_service.py` (190 lines),
`mini_app_token_exchange_service.py` (167 lines),
`calorie_tracking_service.py` (444 lines), `flashcards_tracking_service.py`
(764 lines), `discovery_feed_service.py` (376 lines). ~4,800 lines total.

**Verification (four independent negative checks, all confirming the
same gap):**
1. No `routes_*.py` file, no `*_api.py` file, and `server.py` (the
   composition root that mounts all 21 real routers) contains **zero**
   references to `mini_apps_service`, `mini_app_host_service`,
   `calorie_tracking_service`, `flashcards_tracking_service`, or
   `discovery_feed_service`.
2. `mini_apps_service.py`/`mini_app_host_service.py` construct URL strings
   like `/api/workspaces/{workspace_id}/mini-apps/{app_id}/hosted-manifest`
   (`mini_apps_service.py:763`) and `.../bridge/messages`
   (`mini_app_host_service.py:493`) as **metadata values returned in API
   responses** (self-documenting "here's where a client could fetch this"
   strings) — not as route registrations. Grepped every route file's
   `@router.get/post/put/patch/delete` decorator string for `mini`: zero
   hits.
3. The frontend genuinely calls these exact URLs
   (`frontend/lib/workspace/hosted-mini-app-surface.tsx:103`) through the
   real catch-all backend proxy (`frontend/app/api/[...path]/route.ts` →
   `forwardControlPlaneRequest` → the same Python control plane) — so a
   real user hitting `/w/{id}/applications/{appId}` gets a real 404, not
   a mock. There is even an E2E spec exercising the identical path
   (`frontend/tests/e2e/app-marketplace-identity.spec.ts:144`, a real
   `fetch()` through `page.evaluate`, not a route mock).
4. `calorie_tracking_service.log_calorie_event` / `update_calorie_goals` /
   `calorie_overview`, `flashcards_tracking_service.create_flashcard` /
   `log_review_result` / `retrieve_flashcard_records`,
   `discovery_feed_service.list_discovery_feed` / `adopt_discovery_item`
   — every one of these real, tested functions has **zero** callers
   anywhere outside its own unit test file.

**The false-confidence mechanism, confirmed:** `test_mini_apps_service.py`
(18 passing tests), `test_calorie_tracking_service.py`,
`test_flashcards_tracking_service.py`, `test_discovery_feed_service.py`
all import the service module directly and call its Python functions —
never through `TestClient`/`httpx`/any HTTP layer. A fully green unit-test
suite here proves the business logic works; it proves nothing about
whether an HTTP client can ever reach it, and none can.

**Also notable:** `mini_apps_service.FIRST_PARTY_MINI_APP_IDS = {
"calorie_tracking", "flashcards"}` (`:34-37`) and the matching
`FIRST_PARTY_APP_OPEN_PATHS` build a frontend URL under
`/w/{workspace_id}/applications/{...}` — but neither `calorie_tracking`
nor `flashcards` appears anywhere in the frontend beyond that constant's
own construction, and the "Applications" page itself is not in
`PrimaryRail.tsx`'s 5-item nav (`Inbox`, `Conversations`, `Projects`,
`Agents`, `Hardware`) — so even the frontend half is orphaned from normal
navigation, not just missing a backend.

**Smallest fix:** either mount a `routes_mini_apps.py` router that calls
into these existing service functions (the business logic is already
written and unit-tested — this is a routing-layer task, not a rewrite),
or, if this was deliberately shelved, delete/flag it the way the
Signal/iMessage/WeChat "dead routes preserved, not deleted" architecture
decision already documents for other unfinished surfaces, so the next
engineer doesn't mistake 18 green tests for a working feature.

---

### H2. The one thing that would relieve the memory cap never runs

**Shape:** A (zero-caller service), directly gating a real, live constraint.
**File:** `server_modules/sage_dreaming_pipeline.py` (351 lines) —
`light_sleep` (dedup via Jaccard similarity + category match, `:111-165`),
`rem_sleep` (cross-reference near-duplicate entries across categories,
`:168-233`), `deep_sleep` (age-based pruning + a promotion/demotion map
for sensitivity tiers, `:236-318`), `run_dreaming_cycle` (orchestrates the
three stages, `:321-351`).

**Verification:** zero references to `sage_dreaming_pipeline`,
`light_sleep`, `rem_sleep`, `deep_sleep`, or `run_dreaming_cycle` anywhere
in `server_modules` outside the module itself and one test file
(`test_sage_dreaming_pipeline_rust_gate.py`) — and that test only exercises
the internal `_write_dreaming_json` rust-gate helper directly, never any
of the four real entry points. No route, no scheduler, no command
dispatcher slash-command, no cron references it.

**Why it matters, concretely:** `sage_memory_service.py:34` defines
`SAGE_MEMORY_ENTRY_LIMIT = 50`, enforced at `:474-475` — a real,
non-silent hard stop (`raise HTTPException(409, "Sage memory limit
reached.")`) once an agent's memory hits 50 entries. `sage_dreaming_
pipeline.py` is visibly *built to be* the relief valve for exactly this
wall — dedup near-duplicate memories, prune stale ones, make room — and
it never runs. Every account will eventually hit the 409 wall with zero
automated path to recover; the fix that was written for this specific
problem is dead code.

**Secondary, smaller bug found inside the same file while verifying it:**
`deep_sleep` (`:236-318`) builds `promotion_map`/`demotion_map` dicts
(`:253-257`) and always reports `"promoted": 0, "demoted": 0` in its
result (`:295-296`) — the maps are constructed but never consulted inside
the pruning loop (`:259-277`), so even if this function were wired up
today, its promotion/demotion feature would silently no-op forever. Not
severed from a caller (there is none) but worth fixing in the same pass.

**Smallest fix:** call `run_dreaming_cycle` on a schedule (the platform
already has a working cron/weekly scheduler per the platform map's §18 —
attach it there rather than building a new one) or from the same
"memory limit reached" 409 path as a just-in-time compaction attempt
before failing the write outright.

---

## MEDIUM + suspected

### M1. Two orphaned parallel implementations (dead, not currently harmful)

- **`channel_event_journal_service.py`** (117 lines —
  `is_duplicate_inbound`, `record_inbound_message`, `record_outbound_result`,
  `_ensure_channel_thread`) has zero callers anywhere outside a test-file
  architecture-boundary allowlist (`test_channel_execution_separation.py:41`,
  which merely lists it as a "channel file" for import-direction checking,
  not a functional caller). The live inbound-dedup path actually used by
  `personal_channels_service.py` (3 call sites: `:2421`, `:2617`, `:3100`)
  is a completely separate function, `personal_channels_repository.
  record_inbound_message`, which has its own working duplicate-detection
  (verified by `test_sage_channel_certification_core.py:191-211`).
- **`channel_execution_quota_adapter.py`** (47 lines) defines
  `channel_execution_slot` as a thin wrapper around
  `channel_concurrency_service.channel_execution_slot` — but every real
  caller (`agent_channel_router.py`, per the patch target in
  `test_channel_ingress_contract_unification.py:199`) imports and calls
  `channel_concurrency_service.channel_execution_slot` **directly**,
  bypassing the adapter entirely.

Both are exactly the founder's known-bug-#5 shape (a dedicated
implementation wired to nothing while a parallel path the live traffic
actually takes does the job) — but in these two cases the live path's own
version is correct, so there is no current customer-facing gap. Risk is
maintenance/audit confusion: a reviewer grepping for "where is channel
dedup enforced" will find this module first and believe it's live.

### M2. Silent audit-trail loss on the live tool-denial path

**File:** `server_modules/tool_broker.py:475, 678, 697, 766, 898` — five
`except Exception: pass` blocks, all guarding a security/billing
audit-event emission that happens immediately before `raise
ToolExecutionDeniedError(...)`. Two of them (`:766`, `:898`) schedule the
emission via `asyncio.create_task(...)` with no reference stored (same
fire-and-forget shape as C2 and the fixed compaction bug), inside a try
that catches `RuntimeError` for "no running loop" but not failures from
the task itself.

**Verified benign for the primary behavior:** the actual security
decision (denying the tool call) is never swallowed — it's on the next
line unconditionally. What can silently vanish is the *record* of why and
when a specific tool call was denied — an observability/forensics gap,
not a live correctness bug. Ranked MEDIUM rather than CRITICAL because
the founder's severity rubric reserves CRITICAL for wrong live behavior,
and denial enforcement itself is intact here.

### M3. A genuinely dead stub with a self-contradicting docstring

**File:** `server_modules/approval_contracts.py` (27 lines). Its own
docstring says *"This module exists to prevent import errors"* — but
zero files anywhere in the repository (not even tests) import it. The
justification for keeping it is moot; it is fully orphaned.

### S1. Suspected, unverified — the same repository-layer race likely affects more than messaging

`update_workspace_agent_install` (`agent_registry_repository.py:2530-2620`)
merges `tool_toggles` (`:2569`), `policy_context_overrides` (`:2570`), and
`metadata` (`:2571`) using the identical read-then-blind-overwrite pattern
that C1 exploits for `fleet_inbox`. I did not find or construct a second
concrete live scenario where two callers race on `tool_toggles` or
`policy_context_overrides` for the *same* agent install concurrently
(would need production traffic-pattern data, not static analysis, to
confirm how often that occurs) — flagging as suspected rather than
confirmed. The fix for C1 (an atomic JSONB merge or row lock in this one
function) closes this for free regardless.

---

## What I checked and ruled out (to save the next pass from re-treading)

- **280 `except Exception: pass` sites** were enumerated across
  `server_modules`. Sampled ~35 in the highest-traffic live files
  (`tool_broker.py`, `agent_turn.py`, `sage_reply_dispatcher.py`,
  `personal_channels_service.py`, `notification_service.py`,
  `gateway_execution_service.py`, `runs_execution.py`,
  `channel_delivery_outbox_service.py`). The overwhelming majority guard
  best-effort telemetry/trace/activity-log emission **after** the real
  side effect already completed or the real error already propagated —
  a defensible pattern, not a bug, and `sage_reply_dispatcher.py` in
  particular is disciplined about it (explicit "best-effort, never blocks
  send" comments, warnings logged on the real failure path). Only the two
  CRITICALs and M2 above guard something that matters.
- **19 modules with zero non-test references** were found via a
  repo-wide substring scan; `sage_dreaming_pipeline.py` (H2) and the
  mini-apps cluster (H1) were the real findings. The rest
  (`agent_registry_models.py`, `channel_types.py`, `policy_presets.py`,
  `session_lifecycle_service.py`, `rust_authorization_shadow_service.py`,
  `cli_companion_service.py`, `business_messaging_channel_adapter_service.py`,
  `autopilot_bridge_registry_service.py`, `vault_migration_stage4b.py`)
  were checked individually: most are exercised only by a dedicated
  "rust-gate" unit test that calls their real functions directly — worth
  noting as the same false-confidence pattern as H1 at smaller scale, but
  none of them sit behind a live customer action the way H1/H2 do, so
  they're not written up as standalone findings. `business_messaging_
  channel_adapter_service.py` (Apple Business Messaging) matches this
  repo's own explicit "dead routes preserved, not deleted" architecture
  decision (Appendix A #6) — intentional, not a bug.
- **Known bugs #1, #3, #4, #6, and the `role="system"` drop** (compaction
  background job, `retention_enforcement_job.py`, `PlatformEvent.
  to_intervention()`, the delegation engine, the provider normalizer) were
  re-checked against current HEAD only where cheap to do so; #1 is fixed
  in this commit range per `docs/PLATFORM-MAP.md` Part 31.1 and the
  `65933432c` commit message. #3, #4, #6 were not re-verified in this pass
  (out of scope — already documented in `audit-storage-lifecycle.md` and
  `audit-agent-to-agent.md`); only #2 and #7 were re-verified because they
  intersected with a module (`fleet_tools.py`) this pass was already deep
  in.
- **Config/flag audit (shape D):** ~213 env vars referenced in exactly one
  file were enumerated as candidates for "written/documented but never
  consulted." Spot-checked `EMPYRALIS_MCP_WRITE_ENABLED` (real, wired in
  `mcp_server.py` — the scan's blind spot was that it only walked
  `server_modules/`, missing root-level `mcp_server.py`/`server.py`),
  `EMPYRALIS_PRIMARY_COMPACTION_ENABLED`, `EMPYRALIS_CONTINUOUS_WORK_
  ENABLED` (both real, wired). No new falsy-zero or dead-flag instances
  found beyond the `model_tier`/`max_context_tokens` ones already in Part
  31.3.
- **Frontend TypeScript** was checked only where it intersected a backend
  finding (H1's Applications page, PrimaryRail nav). A dedicated frontend-
  side sweep for the same shapes (unreachable components, dead API routes,
  props nothing reads) was not performed and would be a reasonable next
  pass.

---

## The systemic patterns — what recurs and why

1. **Unit tests that import the service module and call its Python
   functions directly are the single biggest source of false confidence
   in this codebase.** Every zero-caller finding in this audit (H1, H2,
   and the smaller M1/rust-gate cluster) has a fully green, real,
   non-trivial test file sitting right next to it — the tests are not
   fake, they correctly validate the business logic. What they never do
   is cross the boundary the real bug lives on: an HTTP route that
   doesn't exist, a scheduler that never calls in, a turn-loop that never
   reads the field back. A 100%-passing test suite and "this feature
   works" have quietly become two different claims in this codebase, and
   nothing currently distinguishes them.
2. **Fire-and-forget `asyncio.create_task`/`ensure_future` with no stored
   reference is a recurring habit, not a one-off.** Beyond the
   already-fixed compaction job, this exact shape appears at
   `personal_channels_service.py:1507` (C2) and twice in
   `tool_broker.py` (M2) at minimum. Every instance shares the same two
   failure modes: the task can be GC'd before completing, and if it's
   also wrapped in `except Exception: pass`, a real failure inside it
   produces zero trace either way.
3. **Read-merge-blind-overwrite on JSON/JSONB metadata columns has no
   guard rail anywhere.** `update_workspace_agent_install` (C1/S1) is one
   function, but the pattern — read a row, merge a patch into it in
   Python, `UPDATE` the whole column back with no version check or DB-side
   atomic merge — is the generic shape the founder's own bug #7 was a
   specific instance of. Nothing in this codebase currently prevents a
   second such function from being written the same way.
4. **Most `except Exception: pass` sites are fine, which makes the
   dangerous minority easy to miss by inspection.** Because the
   overwhelming majority of the 280 instances correctly guard
   already-succeeded telemetry, a reviewer skimming for "swallowed
   exceptions" burns most of their attention on true negatives before
   reaching the handful (C2, M2) guarding something that matters. The
   signal-to-noise ratio itself is the problem, not the count.

---

## Recommended guardrails

**The one guardrail worth building first: a CI "reachability" check.**
A small script (no new dependencies — this audit's own zero-caller sweep
took under a minute to write) that, for every `server_modules/*.py` file,
checks whether any of its public top-level names is referenced from
*any* file outside `server_modules/tests/` and outside itself. Anything
with zero non-test references fails the build unless it's in a small,
reviewed allowlist (with a one-line reason — mirroring how this repo
already documents its intentional dead routes in Appendix A #6). This one
check would have caught H1 (mini apps), H2 (dreaming pipeline), the
already-known `retention_enforcement_job.py` and the 7,731-line
delegation engine, on day one of each landing — before a test suite ever
had the chance to look green.

Supporting, smaller mechanical guards:

- **Route-coverage test:** for every literal API path string the frontend
  actually fetches (grep `frontend/**/*.ts(x)` for template-literal fetch
  calls, or better, walk the E2E spec files), assert a live FastAPI route
  matches it. Would have caught H1 directly and specifically, independent
  of the reachability check above.
- **Ban bare `except Exception: pass`** via a lint rule (a custom AST
  check, or `flake8-bugbear`'s B036-style rule) requiring at minimum a
  one-line `logging.debug(...)` call inside the block. Cheap, mechanical,
  and — per the systemic-patterns note above — the goal isn't to forbid
  swallowing (most of the 280 are legitimate), it's to make every swallow
  leave *one* line of evidence, so C2 and M2's silent branches stop being
  silent even before anyone fixes the underlying logic.
- **Fire-and-forget task lint:** flag any `asyncio.create_task(...)` /
  `asyncio.ensure_future(...)` whose return value isn't assigned to a
  variable (or immediately awaited). A one-line AST check
  (`ast.Call` inside an `ast.Expr` statement, not an `ast.Assign`) catches
  every instance found in this audit, including the one already fixed.
- **Atomic-patch helper for JSONB metadata columns:** add one shared
  repository helper (`UPDATE ... SET metadata = metadata || $patch::jsonb`)
  and a lint/review rule against new ad hoc read-merge-write functions
  touching `workspace_agent_installs.metadata` (or any other JSONB
  column) outside it. This is the direct fix for C1/S1, but making it a
  guardrail (not just a bugfix) prevents the third instance of this exact
  shape.

# Audit: Agent-to-Agent Communication, Identity, and Cost Rollups

Read-only audit, 2026-07-24. Scope: the founder's five questions on
multi-agent messaging, naming/addressing, reliability at ~20 agents,
project-level shared state, and per-agent cost rollups. Every claim below
is file:line-verified against the current tree (not the worktrees, not
`/legacy`). Where an earlier audit doc (`backbone-empyralis.md`,
`gap-ai-operation.md`, `PLATFORM-MAP.md`, `gap-map-consolidated.md`,
`backbone-plan.md`, `tasks-to-agents-research.md`) already covered a
finding, it is re-verified fresh here rather than taken on faith — one
correction to a prior doc is flagged explicitly (see §1, `triage_service.py`).

---

## Verdict

**No. Twenty agents cannot talk to each other today, in any form — not
because the messaging is unreliable, but because there is no delivery at
all, and the one write path that exists is gated to a single caller.**
`fleet_message_agent` (`server_modules/fleet_tools.py:1564-1631`) writes a
message into the target install's `install_metadata.fleet_inbox`
(capped at 20 entries) — and that is the entire mechanism. A repo-wide grep
for `fleet_inbox` (excluding worktrees/legacy) turns up exactly two lines,
both in this one function: one writer, zero readers, anywhere. No turn,
scheduler tick, or wakeup ever loads a `fleet_inbox` entry back into a
model's context. On top of that, the tool is role-gated to `operator` only
(`skills_service.py:5521-5528`, redundantly re-enforced at
`tool_broker.py:909-915`) — a specialist agent has no tool that lets it
message *any* other agent, peer or otherwise. Only Sage (the single
per-workspace operator install) can even attempt it, and Sage's own attempt
goes into a mailbox nothing reads. Separately, a real, general-purpose,
depth-capped, role-typed delegation engine exists (`runs_delegation.py` +
`runtime_run_delegation_service.py` + `runs_execution.py`, 7,731 lines
combined) with three live HTTP endpoints — but a full grep of `frontend/`,
every `ToolDescriptor` registration, and `scripts/` finds its only caller
is a bash smoke-test script (`scripts/empyralis_core_smoke.sh:802-989`).
Nothing in the product calls it. The founder's instinct that "mentioning by
name isn't practical, addressing by ID is what we have to do" matches what
the system already does for the one operator-only tool that exists
(`fleet_id` is a real, opaque `ainstall_<hex>` id, never a display name) —
but there is no @-mention parser anywhere that would let a *model* resolve
a name to that id inside a message; the one tool that takes an id requires
the caller to already have called `fleet__list_agents` first.

---

## Every path that exists

| Path | State | File:line |
|---|---|---|
| `fleet_message_agent` → `install_metadata.fleet_inbox` | **BUILT-BUT-DEAD** — writes, zero readers anywhere in the codebase | `server_modules/fleet_tools.py:1564-1631` (write); repo-wide grep for `fleet_inbox` finds no reader |
| `triage_service._enqueue_owner_notification` (2nd fleet_inbox writer, per prior audit docs) | **REMOVED** — prior docs (`backbone-empyralis.md:326-334`) describe this as a live second writer; it no longer exists. `triage_service.py` was gutted 548→84 lines in commit `6d15bb05d` ("remove the triage input-blocking gate", 2026-07-23), which deleted this function entirely | `git log -S"_enqueue_owner_notification" -- server_modules/triage_service.py`; zero hits for `inbox` in current `triage_service.py` |
| `fleet_message_agent` role gate | **REAL, enforced twice** — only `role == "operator"` may call it; every `fleet_create_agent`-made agent is seeded `role="specialist"` and the `operator` capability preset is explicitly reserved/not creatable | `skills_service.py:5509-5528`; redundant gate at `tool_broker.py:629-654, 909-915` |
| Delegation engine (`delegate_run_children` / `auto_delegate_run_children` / `retry_failed_delegation_runs`) | **REAL backend, REAL HTTP routes, NO product caller** — 7 roles, depth-1 cap, max 3 parallel children, 300s stale-child timeout, a Rust-side routing gate | `server_modules/runs_delegation.py` (566 lines) + `runtime_run_delegation_service.py` (859) + `runs_execution.py` (6,306) = 7,731 lines; roles at `runs_delegation.py:35-43`; depth cap `run_service.py:81-129`; Rust gate `empyralis-runtime-kernel/src/run_routing.rs:263-294`; routes `runtime_route_registry_service.py:360-408`; only caller found = `scripts/empyralis_core_smoke.sh:802-989` |
| Trace-event rendering of delegation (`delegation.started`/`.finished`) | **REAL, but decorative** — the chat UI knows how to render these events, but nothing produces them outside the dead-caller engine above | `frontend/lib/workspace/codex-chat/event-projector.ts:771-795` |
| Task assignment → agent wakeup (`project_tasks_service.assign_task` → `bounded_scheduler_service.schedule_task_assigned_wakeup`) | **REAL backend, DB-backed, wired to the scheduler; ZERO frontend callers** — a task has `title/description/status/assignee_agent_id/plan/metadata`, assigning it fires a real, policy-gated wakeup | Table `project_tasks` (`migrations/add_project_tasks.sql:28-50`); service `server_modules/project_tasks_service.py:315-365`; routes `server_modules/routes_fleet.py:243,277,314,353`; wakeup `server_modules/bounded_scheduler_service.py:635-712`; grep of `frontend/app` + `frontend/lib` for `fleet/tasks` or `project_tasks` → zero hits |
| Studio-connector channel routing (Slack/Discord/GitHub/Telegram) | **REAL, but one-binding-one-agent, no addressing between agents** | `server_modules/agent_channel_router.py:66-115` (`_resolve_agent_for_inbound`, exact-match on a real persisted channel binding, no fan-out, no @mention parsing) |
| Ambient scheduler / wakeup engine (`bounded_scheduler_service.py`) | **REAL, rate-limited, atomic-claim delivery mechanism — never wired to `fleet_inbox`** | 1,408 lines; quiet hours, `max_event_triggers_per_hour=4`, `max_self_proposed_per_hour=2`, `DEFAULT_WAKE_BATCH_LIMIT=5`, `DEFAULT_WAKE_SCAN_POLL_SECONDS=20` at `bounded_scheduler_service.py:13-23`; atomic claim at `claim_due_wake_requests` (`:798-826`) → `control_plane_repository.claim_due_agent_scheduler_wake_requests` |
| `mention_gating_service.py` | **REAL, but answers a different question** — this is "did a human @-mention/reply-to *this* agent in a group chat," a single-agent addressing gate, not an agent-to-agent or multi-agent @-mention resolver | `server_modules/mention_gating_service.py:1-40` |
| Per-agent usage rollup (`GET /api/w/{ws}/fleet/usage`) | **REAL, fully wired, live in the frontend today** — not part of "agent-to-agent" messaging, but directly answers Q5 below | `server_modules/routes_fleet.py:59-82`; `server_modules/usage_events_repository.py:231-320`; consumed by `frontend/lib/workspace/fleet/FleetAgentDetail.tsx:304,2789` and 4 other pages |

---

## Identity + naming

**Stable identifier.** `workspace_agent_installs.id` — an opaque
`ainstall_<hex>` token (confirmed by the id-regex in
`skill_registry.py:960`, `r"ainstall_[0-9a-fA-F]+"`), returned as
`agent_id` by every fleet API (`fleet_tools.py:748`). This is the ONE thing
every fleet tool (`configure_agent`, `message_agent`, `get_agent_activity`,
`schedule_task`) requires as a parameter — never the display label.

**Auto-naming: real, and it already does what the founder is asking for.**
`fleet_create_agent` (`fleet_tools.py:2067-2081`) auto-assigns a name from
a curated 50-word pool (`agent_name_pool.py:16-24`, "Atlas," "Nova," "Onyx"
etc.) whenever the create-agent wizard's Placement step commits without a
name typed — "NAME IS NOT A STEP" per the code comment
(`fleet_tools.py:2071`). Collision-checked case-insensitively against every
existing label in the *workspace* (`agent_name_pool.py:27-45`,
`list_workspace_agent_installs(..., include_master=True)`); once the pool
is exhausted it appends a numeric suffix ("Atlas 2") rather than looping.
"Sage" is reserved and excluded from the pool (`agent_name_pool.py:8`).

**Uniqueness is NOT enforced after creation, and not at the DB level.**
The collision check above only runs at auto-assignment time. A manual
rename via `fleet_configure_agent`'s `display_name` patch
(`fleet_tools.py:1364-1372`) does **zero** collision checking — it truncates
to 200 chars and writes straight to the `label` column. No `UNIQUE`
constraint on `workspace_agent_installs.label` was found in any tracked
migration (`migrations/*.sql` — grepped for `label` + `unique|index`, no
hits; the base table's original `CREATE TABLE` is not in this repo's
tracked migrations at all, so this is stated as "not found in the tracked
schema," not "provably absent everywhere"). **Two agents in the same
workspace can share a display name today**, and nothing would catch it.

**No @-mention parser anywhere.** Grepped for `mention_resolver` /
`parse_mention` / `resolve_mention` across `server_modules/` — the only hit
is `mention_gating_service.py`, which (see table above) is the
human-mentions-the-bot gate, not a multi-agent name→id resolver. Three
separate comments (`bounded_scheduler_service.py:648`,
`project_tasks_service.py:14,325`, `routes_fleet.py:362`) explicitly flag
"a **future** @-mention resolver" as planned-not-built, all pointing at the
same seam: `project_tasks_service.assign_task` is designed to be "the ONE
code path" both the HTTP API and that future resolver would call.

**Forgeability.** A model can only ever get a real `agent_id` from calling
`fleet__list_agents` first (`fleet_tools.py:702-762`, returns
`{agent_id, label, ...}` per install) — there is no cached/memorized
address a model could stale-reference across turns by design, but nothing
stops it from hallucinating one. If it does: `fleet_message_agent` calls
`get_workspace_agent_install_bundle(agent_id, tenant_id=..., workspace_id=...)`
(`fleet_tools.py:1585-1592`), which filters by BOTH `tenant_id` and
`workspace_id` (`agent_registry_repository.py:2654-2667`) — a hallucinated,
mistyped, or cross-workspace id simply returns "not found," it can never
silently misdirect a message into another workspace/tenant. Containment is
real; correctness is not (the caller gets a clean error, but only after
the fact, and only if it bothers to call `list_agents` first — nothing
forces it to).

---

## Failure modes at scale

Since there is no live delivery mechanism, this section is necessarily
about the failure modes *of the pieces that exist*, extrapolated to what
would break the moment someone tried to wire them together at ~20 agents.

- **No rate limit, no fan-out cap, no loop guard on `fleet_message_agent`
  itself.** It is a bare "read metadata, append, write metadata" function
  with no per-hour cap, no max-inbox-writers-per-minute, nothing. The only
  bound is the 20-entry FIFO cap on the inbox array
  (`fleet_tools.py:1596-1605`) — and that's a *storage* cap, not a
  *rate* cap; a storm of 200 messages/minute would still write 200 times,
  each write silently evicting the oldest queued message with no
  notification to anyone.
- **Real lost-update race under concurrent senders to the same target.**
  `fleet_message_agent` reads the target's bundle, computes
  `inbox[-20:]` from that snapshot, then calls
  `update_workspace_agent_install(metadata=meta)`
  (`fleet_tools.py:1585-1612`). That function does its own independent
  `existing` re-read and a shallow top-level merge
  (`agent_registry_repository.py:2548,2571`: `{**existing.metadata,
  **patch}}`) — `fleet_inbox` is a single top-level key, so whichever
  writer's UPDATE lands last **fully overwrites the array**, not merges
  it. Two agents messaging the same target within the same read-write
  window can silently clobber each other's message — and each caller still
  gets back `{"ok": True, "status": "enqueued"}`, with no way to know it
  lost the race. At 20 agents potentially messaging a shared
  coordinator/orchestrator agent, this is not a hypothetical.
- **No cycle detection, only a structural depth cap — and only in the
  unused delegation engine.** `assert_subagent_spawn_allowed`
  (`run_service.py:118-129`) denies any spawn that would exceed depth 1;
  since a depth-1 child cannot itself spawn (its own attempt would need
  depth 2), an A→B→A ping-pong is structurally impossible **in that
  engine** — but that engine has no caller (see table). `fleet_message_agent`
  has no depth/turn concept at all, so if it were ever wired to deliver
  into a turn, A messages B, B's turn (if it also had the tool and the
  operator role) could message A right back with nothing stopping an
  infinite loop except the 20-entry cap silently truncating history.
- **Unanswered messages: no state machine at all.** There is no
  "delivered," "read," "replied," or "expired" status on a `fleet_inbox`
  entry (`fleet_tools.py:1598-1603` — just `from_agent_id`, `message`,
  `enqueued_at`, `message_id`). Since nothing reads it, "unanswered" isn't
  a state that's ever reached; it just sits until evicted by the 20-cap.
- **Cost per delivered message, if wired through the existing scheduler:
  bounded, because delivery is batched, not per-message.**
  `bounded_scheduler_service.build_wakeup_execution_bundle`
  (`:1157-1277`) groups every due wake request **by authority tier** into
  ONE message and executes it as ONE turn per tier — not one turn per wake
  request. If `fleet_inbox` were ever hooked into this (it isn't today),
  N queued inter-agent messages to the same agent would cost at most a
  handful of turns per scan cycle (`DEFAULT_WAKE_SCAN_POLL_SECONDS=20`,
  `DEFAULT_WAKE_BATCH_LIMIT=5`), not N turns. The scheduler's own delivery
  is atomic/exactly-once per row (`claim_due_wake_requests` →
  `claim_due_agent_scheduler_wake_requests`, a DB-level claim, unlike
  `fleet_inbox`'s unlocked read-modify-write above) — a real idempotency
  guarantee, just not one `fleet_inbox` benefits from today.
- **Net at "20 agents":** the honest failure mode isn't "it breaks under
  load" — it's "it was never turned on." The pieces that DO have real
  rate limits, atomic claims, and tier isolation (the scheduler) were built
  for owner-configured wakeups and task-assignment wakeups, not
  agent-to-agent chatter, and nothing routes inter-agent messages through
  them.

---

## Project documents + per-agent memory

**Projects have no document/content store beyond a one-line description,
confirmed.** `FleetProject` (`frontend/lib/workspace/fleet/fleet-data.ts:54-68`)
is exactly `{id, name, description?, agent_count?, status?, is_default?,
icon?, tint?, metadata?, created_at?}` — `description` is the only
free-text field, and `metadata` is an opaque `Record<string, unknown>` with
no defined document/cookbook shape written anywhere.

**Per-agent memory shape (current, post the 2026-07-24 redaction work).**
Scoped strictly by `agent_install_id`
(`specialist_runtime_context.py:75-77`, `memory_scope()` returns
`self.agent_install_id` — `project_id` never enters this module at all,
grepped zero hits). One `MEMORY.md` index plus topic files under
`memory/files/`, both capped at **200 lines / 25KB, whichever hits first**
(`workspace_context.py:150-151`, `memory_service.py:989,1024`), with a
count cap of **40 topic files for hardware-backed agents, 20 for
cloud-only** (`workspace_context.py:126,136` — raised from 5 the same day
per the in-code founder-ruling comment). This is airtight per-agent
isolation, by design — which is exactly why it has nothing to say about
project-level sharing.

**What already exists that a project-level shared store would want to
copy: the connector-credential pattern.** This is the one place a
project-scoped shared *resource* (as opposed to a private per-agent one)
already works in production. `vault_credentials.project_id`
(`control_plane_repository.py:3777-3787`, "UI Phase 1: project-scoped
connector credentials — a credential now lives at project scope; agents
subscribe to it via `agent_connector_bindings` instead of each holding a
private copy") + `agent_connector_bindings`
(`control_plane_repository.py:755-766`, `UNIQUE(agent_install_id,
connector_key)`) is a real, live, two-table pattern: **one row holds the
shared thing at project scope, a second table is a many-to-one
subscription/binding from each agent to it.** `agent_channel_bindings`
(`:768-779`) is the identical shape for channels.

**What a project-level cookbook/shared-doc feature would need.** Not a new
storage primitive — the memory machinery (`workspace_context.py`'s
200-line/25KB-capped markdown files, already durable, already
diff/read/write-tooled) and the vault-credential project-scoping pattern
above cover both halves: a `project_documents` table (or a `project_id`
column added the same way `vault_credentials.project_id` was, via
`ALTER TABLE ... ADD COLUMN IF NOT EXISTS project_id ... REFERENCES
projects(id)`) for the shared content, with each agent's own per-agent
memory optionally *linking to* (not copying) it — mirroring
`agent_connector_bindings` rather than `agent_channel_bindings` verbatim,
since a doc, unlike a channel, isn't exclusive to one agent.

**The nearest thing to "project state agents already share" that is real
today: `project_tasks`.** Not a document, but a genuine project-scoped
mutable object multiple agents can be pointed at
(`title/description/status/assignee_agent_id/plan/metadata`,
`migrations/add_project_tasks.sql:28-50`), wired to a real assignment API
and a real scheduler wakeup — just with zero frontend surface (see table
above; matches the memory note "tasks UI parked").

---

## Per-agent spend rollups

**Feasible from existing data — because it's not just feasible, it's
already built, wired, and live in the product.** `usage_events`
(`server_modules/usage_events_repository.py:88-109`) is a normalized,
one-row-per-LLM-call table with `agent_install_id`, `project_id`,
`workspace_id`, `usd_cost`, and `created_at`, with a purpose-built index
for exactly this query: `idx_usage_events_agent ON usage_events(tenant_id,
workspace_id, agent_install_id, created_at DESC)` (`:108`). Its own
docstring (`:1-11`) says it exists specifically because "the existing
monthly cost ledgers [`deployed_agent_monthly_cost_ledger`,
`workspace_hosted_ai_monthly_cost_ledger`, both real tables at
`control_plane_repository.py:876-916`] aggregate by month and lack
`agent_install_id` / `project_id`" — i.e., those two ledgers were checked
and found NOT to carry the per-agent dimension the founder wants;
`usage_events` is the fix that was already built for that gap. `credit_ledger_events`
(`control_plane_repository.py:918-944`) does carry an `agent_id` column
too, but the two live writers checked
(`direct_chat_hosted_usage_service.py:375-404,588-607`) never populate it
for Sage/specialist direct-chat turns — `usage_events` is the table that
actually gets a populated `agent_install_id` on every call, via a
per-turn context-var set at `sage_agent_runtime_service.py:3793-3820`
(`usage_events_repository.set_usage_attribution`) and read at the LLM-call
site (`record_usage_from_context`, called from both
`sage_agent_runtime_service.py` and `direct_chat_hosted_usage_service.py`).

**The rollup query and the endpoint both already exist.**
`usage_events_repository.summarize_usage(scope="agent", scope_id=<agent_id>,
period="day"|"week"|"month")` (`usage_events_repository.py:231-320`)
returns totals, time-bucketed history, and a full
agent×provider×model×payer matrix. It's served at `GET
/api/w/{workspace_id}/fleet/usage?scope=agent&id=<agent_id>&period=day`
(`routes_fleet.py:59-82`) and is already consumed by
`FleetAgentDetail.tsx:304,2789` (the agent detail Properties panel) plus
four other frontend pages (`billing/page.tsx`, `agents/page.tsx`,
`projects/page.tsx`, `WorkTab.tsx`). **"How much did agent X spend today /
this week / this month" is not new work — it is answerable today, in
production, from a page that already renders it.** The only real gap: it
answers hosted-AI token cost, not the full unified-credit picture (hardware
minutes, SMS, media) the founder's credit-system doctrine calls for —
that's a scope note, not a missing-data problem.

---

## The smallest reliable design

Reuse, don't rebuild — almost everything needed already exists in pieces
that were built for adjacent problems and never connected:

1. **Addressing: keep ID-based, formalize the lookup, add a resolver.**
   The founder is right that ID-addressing (not name-mentioning) is the
   only sound approach, and it's already how the one gated tool works.
   Build the "future @-mention resolver" the code already anticipates in
   three places (`bounded_scheduler_service.py:648`,
   `project_tasks_service.py:14,325`, `routes_fleet.py:362`) as a thin
   layer: model emits `@Label`, resolver calls the same
   `list_workspace_agent_installs` lookup `agent_name_pool` already does
   for collision-checking, resolves to `agent_id`, and rejects
   ambiguous/duplicate labels outright (which requires finally enforcing
   label uniqueness — see §Identity — since today two agents CAN share a
   name and a resolver built on top of that would be ambiguous by
   construction).
2. **Delivery: don't extend `fleet_inbox` — replace it with the scheduler's
   own wake-request row, which already has everything `fleet_inbox`
   lacks.** `bounded_scheduler_service.py` already has atomic claim
   (`claim_due_wake_requests`), authority-tier isolation, quiet
   hours/battery/network gating, and per-tier message batching
   (`build_wakeup_execution_bundle`) — genuine reliability engineering
   `fleet_inbox` has none of. Add a `"agent_message"` trigger kind
   alongside the existing `"task_assigned"`/`"self_proposed"` kinds
   (`bounded_scheduler_service.py:635-795` is the pattern to copy), and a
   sender-side rate limit modeled on `max_self_proposed_per_hour`
   (`:16`) applied per (sender, recipient) pair — this is what actually
   stops a storm; nothing today does.
3. **Cycle/loop guard: reuse the depth cap verbatim, invert who it applies
   to.** `assert_subagent_spawn_allowed`'s depth-1 structural cap
   (`run_service.py:118-129`) is the right primitive for "A messages B, B
   cannot message anyone back at the same depth" — apply the identical
   depth-stamp-on-metadata pattern to agent-message wake requests instead
   of workflow runs, so a reply is depth+1 of the message that prompted it
   and a third hop is denied by construction, not by a runtime cycle
   detector that has to notice a loop after the fact.
4. **Role gate: open it up deliberately, not by accident.** Right now
   `fleet_message_agent` is operator-only by the same blanket gate that
   covers `create_agent`/`configure_agent` (`skills_service.py:5509-5528`).
   Peer-to-peer messaging needs its own, narrower gate (e.g. "any
   `subagents_enabled` specialist may message another agent in the same
   project" — reusing the existing `subagents_enabled` flag,
   `fleet_tools.py:92-98`, rather than inventing a new permission) —
   copy-pasting the operator gate for this would keep 20 specialists as
   mute as they are today.
5. **Project documents: the vault-credential pattern, not a new subsystem.**
   Add a `project_documents` table the same way `vault_credentials.project_id`
   was added (`control_plane_repository.py:3777-3787`) — content lives at
   project scope, a binding table (mirroring `agent_connector_bindings`,
   `:755-766`) lets every agent in the project read it, and the existing
   200-line/25KB memory-file discipline (`workspace_context.py:150-151`)
   is the right cap to reuse rather than inventing a new size limit.
6. **Cost: nothing to build.** `usage_events` + `summarize_usage` +
   `GET /fleet/usage?scope=agent` already answer the founder's cost
   question completely for hosted-AI spend; wiring agent-to-agent message
   volume into the SAME table (tag each delivered `agent_message` wake
   request with a `usage_events` row, `usd_cost=0`, just for count/rate
   visibility) would make "how many messages did agent X send today" a
   `GROUP BY` away from "how much did it cost," using the exact index
   (`idx_usage_events_agent`) that already exists.

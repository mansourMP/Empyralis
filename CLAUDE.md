# Empyralis — standing decisions

**Read this file first — it is your persistent memory for this project.**
When you learn something durable (a settled decision, a recurring failure
worth warning the next agent about, an architecture call that will still be
true next week), add it here yourself, in the existing terse style, without
waiting to be asked.

**Explain with diagrams, not essays.** The founder reads code blocks and
shapes far better than prose. When explaining a mechanism, a gap, or a
decision, draw it — boxes, arrows, before/after — inside a code block. State
the verdict in one line first ("we have it" / "we don't" / "we should"),
then the diagram. Never make him read three paragraphs to reach a fact that
fits on one line.

**Act like a cofounder, not a status report.** When you find a real problem,
fix it — do not describe it and wait. Reporting "here are three open gaps"
and then sitting still is a failure, even when the report is accurate. If
something is broken and the fix is clear, start it and say what you started.
If it needs a decision only the founder can make, ask ONE sharp question and
propose your answer — never a menu of options with no recommendation. The
founder should never have to discover a known problem himself, or ask you to
begin work you already knew was needed. Bring him the finished thing, or the
one blocking question, and nothing in between.

Linear is the system of record. Issues, plans, and status live there, not here.
This file holds only the durable decisions an agent needs *before* it starts
working — the things that don't change when a ticket closes.

**Design/audit/gap/research documents are not kept.** They were snapshots of a
moment; they went stale within a week and agents cited them as present truth.
Deleted 2026-07-31. Findings become Linear issues; decisions become lines here.

**When a system is being replaced, STOP BUILDING ON IT.** Once the founder
settles on adopting a replacement, every hour spent improving the outgoing
system is waste that gets deleted — and worse, it reads as progress. This was
violated badly on 2026-08-08: after the decision to run OpenClaw's gateway as
the channel transport, work continued on the old per-channel code — its
pairing UI, its channel cards, a group-policy panel built against a gate
model OpenClaw replaces. Four commits of UI work were abandoned unmerged.

**There is no "but real customers are being harmed" exception, because there
are no production users on the gateway.** The people using it are people the
founder knows personally. He has stated this more than once; an agent
reasoning about urgency must not invent a customer base to justify work on
the outgoing system. That false premise is exactly what was used on
2026-08-08 to rationalise fixing old channel bugs mid-replacement.

If the outgoing system has a defect, the answer is one line to the founder,
not a fix. Weighing "live bug" against "being deleted" silently, and acting
on your own answer, is how a replacement program quietly becomes maintenance
of two systems at once.

Corollary, from the same day: never present a system being adopted as a
menu of parts to pick from. The founder's words, after saying it many times:
*"there is only one thing which is channels."* Adopt the whole thing, or
argue against adopting it — never quietly curate a subset and call it
adoption.

## Positioning

**Empyralis is the owned-context layer for a team, with execution attached.**
Frontier models are rented and commoditizing — "AI agent platform" stops
meaning anything once everyone has agents, the same way "has a website"
stopped meaning anything in 2005. What doesn't commoditize is what a team
owns: its accumulated context, its skills, its work history, and where its
agents actually execute. Linear holds issues but no memory, no skills, no
execution — agents are guests it delegates to. Anthropic holds a session
that resets and is theirs, not the team's. Empyralis holds the team's
context and runs the work. Settled 2026-08-07.

Never build a coding surface — Claude Code / Codex / Cursor are the
execution layer; Empyralis is the layer above them. The board is the
product; chat is only input. Nothing of value may exist only in a
conversation.

Target user: someone who runs agents on behalf of other people — a
developer hosting agents for client businesses, a team lead whose teammates
consume an agent's output, a person running an agent for family. Not a solo
developer coding alone — Claude Code already serves that person for free.

**"Agents working alongside a team" and "hosting agents for others" are the
same product, not two.** Both need: an agent that does real work on a real
machine, a shared surface where others see the outcome, and private
conversations. Do not build two systems, two onboardings, or two pricing
stories for them. The only axis that genuinely differs is who may talk to
the agent — already modelled as `audience: owner | external`.

**Execution locality.** Identity lives in the workspace; execution happens
where the agent is placed; the connection carries only jobs and results.
When hardware reliability is the problem, move the work — never patch the
connection. A design that round-trips per tool call is treating the symptom.

## Product laws

**No approval system.** No approve/deny buttons, no approval-pending states. An
agent acts on its own reasoning. The "done" gate is the owner reviewing or
reopening work on their own time — never a popup that blocks an agent mid-action.
Guardrails are named and narrow (an enumerated list of high-consequence actions),
never a blanket gate over everything.

**Best, not most.** Match the discipline of the tools we're measured against, not
their feature surface. Every feature added is surface area that must be
maintained, reviewed, and eventually justified to a customer.

**A surface must earn its place.** Adding a top-level route, tab, or section is a
deliberate decision, not a default. If something can live one level down, it
should. Most configuration is set once and does not deserve equal billing with
the things people look at daily.

**No dead controls.** If a control cannot be used in the current state, it is not
rendered. A control whose own label admits it does nothing is a design bug, not
a caption.

**Projects hold members directly. There is no Teams layer.** Decided 2026-07-31
after examining Linear's model, where a project carries its own member list and
lead independent of teams. Empyralis has one workflow, so a team tier would be
ceremony every customer leaves empty.

**Non-owners never see personal or self-chat threads.** Conservative default,
enforced without asking.

**Hardware attaches to its owner, never to the project.** Decided 2026-08-06.
An agent joining a project must never implicitly give that project's members
hands on the hardware the agent runs on — a person's Mac holds their sessions,
files, and keys, and a project invite is not physical access. Sharing a
machine with a project is an explicit per-machine opt-in by the hardware's
owner, default off. An agent whose task needs hardware nobody opted in runs
cloud-side with fewer capabilities — a clean degradation, never an error and
never a silent borrow. The full sharing model: sessions/threads are private to
the person (always, no setting); the agent (name, config, memory, task
history) is shared with the project; hardware is per-owner opt-in.

**Conversations are private. Work is shared.** Multiplayer means teammates share
issues, tasks, status and outcomes — never each other's agent transcripts. How a
person talks to their agent is like their terminal scrollback: nobody reviews it
and nobody wants it watched. Decided 2026-08-01 after looking at Conductor
(YC, $22M Series A), which runs many agents per developer and still routes all
collaboration through PRs and a Linear integration — no shared-chat surface
exists in the funded competitor either. Anything that would put one person's
agent conversation in front of a teammate is out of scope; put the artifact in
the task instead.

**A goal is a durable retry loop, never a state machine that decides when to
give up.** Landed 2026-08-08 (`agent_goals`, built on top of
`bounded_scheduler_service.py`'s existing wake-request machinery — no second
scheduler; `runs_core.py`'s own unrelated cron scheduler still bypasses quiet
hours/rate caps and stays untouched). "Go negotiate with this supplier, retry
with a different offer if rejected, escalate after 3 attempts" is an authored
instruction injected into every turn that works the goal (the wake-turn
message-assembly seam `runtime_heartbeat_service.build_heartbeat_turn_request`
already used for task-assigned wakeups) — never code that computes whether a
negotiation "failed enough" to escalate. Status vocabulary is
`project_tasks_service.TASK_STATUS_ORDER` plus exactly two states a task
can't express: `exhausted` (the bounded attempt/lifetime ceiling was hit —
system-recorded, the model can never set it) and `cancelled` (deliberately
abandoned). `attempt_count` is advanced ONLY by the firing code
(`bounded_scheduler_service._fire_goal`), never by the model narrating its
own progress — the same honesty posture `tool_honesty_guard` exists for
elsewhere. `goal__*` is a third member of `_PROJECT_SCOPED_CONNECTOR_IDS`
alongside `project_task__*`/`document__*` — project membership is the grant,
not a connector binding.

## Craft doctrine

- One accent colour, spent on the single primary action in a view. Everything
  else is neutral. Two accent-filled buttons in one view is a bug.
- Dense inside a group, airy between groups.
- Motion 100–150ms, ease-out, on state change only. Never decorative.
- Real heading structure (`h1`/`h2`), not styled divs.
- Primary navigation is real links, so cmd-click and middle-click work.
- A professional tool labels; it does not lecture. Multi-sentence policy prose
  above a group of controls is a signal the design is wrong. Empty states that
  teach are the exception — they have nothing else to show.

## Recurring failure modes in this codebase

These have each bitten more than once. Check for them before trusting that
something works.

**Built, tested, and never wired.** The most common defect here is not broken
code — it is complete, correct, tested code with **zero callers**. Confirmed
instances: `retention_enforcement_job.py`, `session_service.prune_expired_sessions`,
`_resolve_cloud_provider`'s `check_master_model_config` flag (full
implementation, docstring instructing callers to pass it, unit tests, never
passed by anyone), and `_persist_agent_group_policy_config` (the write path
for channel group policy — its absence is why an agent replied unprompted in
a public Telegram group and got the owner banned). **Before believing a
feature exists, grep for its callers.** "The function is there" is not
evidence it runs.

**"Code exists" is not "reachable on the live path."** Engine dispatch,
tool bundles, and channel routing have all repeatedly surprised us. An audit
that reads a function and concludes the feature works is worth little; trace
from the real entry point to the real call site.

**A compiled artifact is a live-path risk `grep` can't see.** `empyralis-runtime-kernel`
is a Rust binary invoked over subprocess (`rust_runtime_kernel_client.py`) —
it is never re-read from source, so a correct, merged, tested `.rs` fix
changes nothing about the running enforcement until something explicitly
rebuilds it. MAN-306: a 2026-07-28 fix widened `TERMINAL_RUN_STATUSES` so an
ordinary completed task run's archive write is recognized as terminal, but
the documented deploy flow (`docs/DEPLOY-RUNBOOK.md`) never ran `cargo
build` — only `git merge`, `pip install`, `npm run build`, restart — so the
box kept enforcing the pre-fix policy and every ordinary assignment tripped
`archive_non_terminal_run_requires_review` for weeks, with no error anywhere
saying why. Fixed two ways: the deploy runbook now has an explicit rebuild
step (3a), and `preflight.py`'s `_check_kernel()` now refuses to boot if any
file under `empyralis-runtime-kernel/src/` (or `Cargo.toml`/`Cargo.lock`) is
newer than the binary — a source/binary mismatch is now a loud boot failure,
not a silent policy regression. Any other subprocess-invoked or
out-of-process compiled dependency this codebase grows needs the same
staleness gate; `grep`-for-callers doesn't catch drift in an artifact that
isn't source.

**`execFile`'s `timeout` option is not a timeout, and neither is
`execFileSync`'s.** Both send `killSignal` (SIGTERM) exactly once and never
escalate. `execFile`'s CALLBACK still only fires on the child's exit, so a
child that ignores SIGTERM leaves the wrapping promise pending FOREVER and its
ProcessWrap + stdio PipeWraps refcounted on the event loop; `execFileSync`
is worse — it goes back to blocking, freezing the whole process with the
event loop stopped, so no timer, no handle dump and no
`process.getActiveResourcesInfo()` can even observe it.

```
execFile(cmd, args, {timeout: T})
  t=T   SIGTERM ──▶ child ignores it ──▶ ... nothing, ever
        callback: never    promise: pending    handles: held for process life

execFileWithTimeout(cmd, args, T)          <- shell/exec-file-with-timeout.ts
  t=T   resolve({timedOut:true})   ── the DEADLINE belongs to the caller
        SIGTERM ─(grace)─▶ SIGKILL ─▶ unref child + stdio
```

`docker info` on macOS does exactly this while waiting on a wedged Docker
Desktop socket. `health/service-inventory.ts` probes Docker at boot
(`index.ts`), on every `shell.execute` (`shell/runtime.ts`'s `isDockerReady`)
and from the heartbeat (`cloud/ws-client.ts`) — and its 60s cache is only
WRITTEN after the probe resolves, so a wedged probe also means the cache never
fills and the next caller spawns another immortal child. Found 2026-08-08 on
the founder's own box: the live gateway, up 3 days, holding 4 of them, with
~50 more reparented to init from earlier gateway processes, oldest over a day
old — plus `ws-client`'s `passiveInventoryRefresh` single-flight stuck non-null
forever, so capability re-advertisement had silently frozen. It is also why
four gateway test files passed every assertion and then hung, which is why
nobody had a clean `npm test` signal for weeks. Every spawn-with-a-deadline now
goes through `shell/exec-file-with-timeout.ts`, and a drift assertion in
`__tests__/exec-file-timeout-child-leak.test.ts` bans the raw option in `src/`
— a behavioural test cannot catch its reintroduction, because it type-checks
and behaves perfectly against every child that does die on SIGTERM. When you
add a subprocess with a timeout, the timeout is yours to enforce: resolve on
your own deadline, escalate to SIGKILL, and unref what refuses to die.

**A check that derives its own expectations from the thing it checks is
blind, and reports "passed".** `preflight._check_rls()` verified that every
table listed in `migrations/enable_rls.sql` had RLS + FORCE + a policy — all
40 did — but it got its list of "tenant-scoped tables" by parsing that same
file. So a table carrying `tenant_id`/`workspace_id` that nobody added to the
migration was simultaneously unprotected AND unverified. **60** were, on
2026-08-08. Fixed by asking the live database which tables carry a scope
column and requiring each to be either in the migration or in
`preflight._RLS_COVERAGE_EXCEPTIONS` with a written verdict (seeded with all
60, so boot is unaffected and only NEW drift fails; `EMPYRALIS_SKIP_RLS_
COVERAGE_CHECK` disables just that half, so an incomplete list is never a
reason to reach for `EMPYRALIS_SKIP_RLS_CHECK`). Two rules follow. When you
write a conformance check, the expected set and the actual set must come from
**different** sources — otherwise it can only ever confirm itself. And
scrapers must handle inline DDL: half those tables are created by per-module
`_ensure_*_tables()` helpers whose `CREATE TABLE` has no trailing semicolon,
so a scraper anchored on `;` finds 30 and silently reports the other 30 do
not exist.

**No RLS ≠ a leak, and RLS is not always the fix.** Of those 60: most are
scoped by explicit `WHERE tenant_id/workspace_id` in application SQL; 10 are
local SQLite files where Postgres RLS is inapplicable; 6 have no live reader
at all. Four *cannot* take the standard policy — `vault_credentials`,
`workspace_policies`, `tenant_policies`, `tenant_enterprise_settings` carry
only ONE of the two columns, and `empyralis_rls_scope_match(tenant_id,
workspace_id)` requires both. `vault_credentials` is the sharpest: nullable
`workspace_id` is load-bearing (platform-scoped credentials are the NULLs), so
a naive policy would blank them — and `vault_repository.list_all()` is a
full-table `SELECT` with no `WHERE` that every vault operation goes through,
the boundary applied in Python afterwards. Separately, the four
`run_state_repository` tables (`live_runs`, `run_archive`, `runtime_sessions`,
`runtime_outbox`) are read through a plain asyncpg pool that never sets the
session GUCs, so a policy there would blank the runtime's own reads. Before
recommending RLS on a table, answer whether its existing queries would still
return rows.

**`require_api_key` is not an authorization check.** `runtime_common.py:345`
resolves ANY authenticated user of ANY tenant — it answers "is someone logged
in", never "may this person see this workspace". Four routes carried it as
their only gate and handed every tenant's machine ids, hostnames, workspace
ids, run ids and dead-letter hotspots to any signed-in customer:

```
BEFORE                                    AFTER
  any bearer session                        operator? (auth-admin OR ORION_API_KEY)
        │                                        ├── yes ─▶ global view   [ops daemon]
        ▼                                        └── no  ─▶ caller's workspaces only
  /runtime/runtimes/status   ─▶ whole fleet             summary + capability_queue
  /local/workers/status      ─▶ whole fleet             RE-DERIVED from the scoped set
  /runtime/runtimes/reliability ─▶ 10 tenants' run ids
  /health/internal           ─▶ global workspace top-5
```

Fixed 2026-08-08. The global path is gated on
`auth.has_platform_fleet_operator_access` — an auth-admin identity, or
possession of `ORION_API_KEY`, which is an operator secret; customer machines
bootstrap with a per-machine enrollment token instead. Keeping that path is not
a convenience: `scripts/orion_ops_daemon.py` and the `orion_*.sh` scripts poll
`/runtime/runtimes/status` and restart the runtime when `summary.online` is 0,
so scoping them to nothing would have caused a restart loop. Use
`enforce_workspace_access` for anything workspace-shaped,
`current_user_has_auth_admin_access` for operator tools, and
`has_platform_fleet_operator_access` for a cross-tenant fleet view.

Two rules follow. **A filtered item list beside an unfiltered summary is still
a disclosure, just an arithmetic one** — hence
`local_queue.summarize_worker_items`, so the scoped and global views cannot
drift. And **`WHERE ($1 = '' OR tenant_id = $1)` fails OPEN**: a forgotten
argument returns every tenant. `list_fleet_workers` /
`list_fleet_queue_partitions` now raise unless the caller passes
`include_all_tenants=True`, so a deliberate global read is greppable and an
accidental one is loud.

The rest of that idiom is closed too (2026-08-08, `fix/vacuous-tenant-filters`).
`run_state_repository`'s `list_live_runs_page` / `count_live_runs` /
`list_pending_approvals_page` — plus the zero-caller `list_pending_approvals`,
which had no workspace predicate at all — now bind the scope unconditionally
(`WHERE ($1::boolean OR workspace_id = ANY($2::text[]))`, where `$1` can only
come from an explicit `include_all_workspaces=True`), and
`_require_explicit_workspace_scope` raises on a missing one **in the sync
wrapper as well as the coroutine** — `_run_sync` swallows exceptions into
`fallback`, so a guard only inside the coroutine turns a forgotten scope into a
silent `[]` instead of a loud failure. An EMPTY `workspace_ids` still means
"this caller may see no workspace" and returns nothing.

The caller-side half was the sharper bug. `agent_workspace_api`'s
`workspace_filter = … if workspace_id else None` produced an unscoped read AND
skipped every `if workspace_filter and …` re-filter below it — one omitted query
parameter defeated both layers. Two of the three routes sat behind
`require_admin_api_key`, which is `enforce_minimum_role(…, "owner")`: **any
workspace owner of any tenant, a role check and not a tenancy check**. The
third, `_workspace_artifacts_payload` (`GET /artifacts`,
`GET /artifacts/workspace`), sat behind plain `require_api_key` and was
therefore a live leak, not a latent one — omit `workspace_id` and it walked
other tenants' live runs into `_get_replay_payload` and returned their run ids
and artifact paths. A missing `workspace_id` now resolves to the CALLER'S OWN
workspace via `enforce_workspace_access(current_user, None)`, never to "all";
`/runs` legitimately spans several, so it passes the caller's
`allowed_workspace_ids` as a list. `_list_workspace_live_runs_bounded` and its
approvals sibling take `workspace_id` as a REQUIRED argument with no default —
the same "a scope column with a default is a loaded gun" rule.

Reintroduction is guarded by `test_run_state_scope_fails_closed.py`'s
`FailOpenScopeFilterDriftTests`: a source scan for the three fail-open shapes
(`$n = '' OR`, `$n IS NULL OR`, `CARDINALITY(…) = 0 OR`) landing on a
tenant/workspace column, diffed against a hand-written allowlist carrying a
written verdict per surviving instance. A behavioural test cannot catch a NEW
one — it type-checks and behaves perfectly for every caller that remembers the
argument.

Note the near-miss that is NOT a bug: public `GET /health` computes the same
cross-tenant payload but `public_health()` returns only `{"ok": ...}` — trace
the response shaping, not just the payload construction, before calling
something a leak.

**Silent misrouting beats loud failure, and that is a bug.** A model calling
the CLI's built-in `TaskCreate` instead of `project_task__create` reported
"Task #1 created successfully" while `project_tasks` stayed empty — real
tool, real success, wrong bookkeeping, customer told work was done that
never happened. Hence `ClaudeAgentOptions.tools=[]` in the SDK bridge:
agents get Empyralis-native tools only, never the CLI's built-ins. Any
change that reintroduces built-in tools reintroduces this. When removing or
renaming a provider/model/route, make stale config **fail loudly** rather
than fall through to a default — see `model_router`'s deliberate retention of
a `vertex` branch after Vertex was removed.

**Stale string matching.** An error bucket matched `"ai limit"`; the message
was reworded to `"AI usage limit reached"` and users got a generic "Something
went wrong" for five weeks. Match on stable codes, never on prose.

**A redactor placed on an agent-visible path is a capability gate, and its
allowlist is the gate's key.** `secret_redaction_service`'s
`_SAFE_IDENTIFIER_PATTERN` allowed exactly ONE separator between alphanumeric
runs, which cannot express our own `connector__action` convention — so its
high-entropy sweep rewrote every `__` tool name of 20+ characters to
`[redacted-secret]`. 17 of 72 registered tools, inside
`sage_agent_runtime_service._build_prompt_envelope`, whose output IS the system
prompt the model is handed. An agent cannot call a tool whose name it never
sees: assign, update, label, schedule-recurring, configure-another-agent and
the whole browser/computer family were gone, with no error anywhere — it
presented as the model "choosing not to". Fixed 2026-08-08 in the allowlist
(separator runs `{1,2}`, segments capped at 24), never by loosening the
detector — weakening an entropy rule to fix a naming problem trades a silent
capability bug for a silent secret leak. Three rules follow. **Redaction
belongs on what is WRITTEN OUT (logs, ledger, traces, channel replies), never
on what is READ IN by the model** — the same call redacted `user_message` too,
so a person quoting a tool name at their own agent had it eaten. **A memory
write redacted before disk is a permanent edit**, not a display filter — all
four `memory_service` seams plus `agent_memory_tools.memory_write` were
corrupting stored notes that merely mentioned a tool. And any allowlist inside
a redactor must be **driven off the live registry in a test**
(`test_tool_name_secret_redaction.py` enumerates
`skills_service.registered_direct_chat_tool_names_for_logging()`, the same list
`server.py` logs as `Registered tools: [...]`) — a hand-copied sample goes
stale the moment someone adds a tool, and this failure is silent by
construction.

**A guard called once inside a 2,300-line function is a guard the next branch
will skip.** `handle_sage_chat` applied `_guard_sage_visible_reply` near its
end; the BYO-brain `local` and `cli_subscription` branches `return`ed ~1,100
lines earlier and never reached it, so raw model output went out through
`sage_turn_adapter.execute_sage_turn` (which relays `result["message"]`
verbatim) to WhatsApp/Telegram/Signal/iMessage/OpenClaw, and through
`record_assistant_turn` into durable history that later turns re-inject. The
worst two branches to miss: those are the turns running on the owner's own box
against their own local model or CLI subscription, i.e. the output most likely
to carry file contents or credentials. Fixed 2026-08-08 by moving the guard to
the seams every branch must cross rather than adding two more call sites —
`handle_sage_chat` is now a thin wrapper over `_handle_sage_chat_unguarded`
(the old body) and guards the returned message once, and
`thread_service.record_assistant_turn` guards `reply` before the write.

```
BEFORE                                   AFTER
handle_sage_chat                         handle_sage_chat  (wrapper)
 ├ local            ─── return  ✗guard    └ _handle_sage_chat_unguarded
 ├ cli_subscription ─── return  ✗guard        ├ local / cli / loop / fallback
 ├ action loop  ─guard─ return                └ any branch added later
 └ fallback     ─guard─ return             ──▶ _guard_sage_visible_reply ──▶ out
```

Three rules follow. **Put a safety filter on the narrow waist, never on each
branch** — a per-branch call is a rule the next author has to know, a wrapper
is one they cannot reach around; this only works because the guard is
idempotent, so verify that before wrapping. **Guard display AND persistence
separately** — a reply cleaned for the screen but stored raw is still a leak
the moment thread history becomes prompt context, and the two paths are
genuinely different seams. And **a structural test is the only thing that
catches the NEXT branch**: `test_unguarded_reply_paths.py` AST-asserts that
`_handle_sage_chat_unguarded` has exactly one call site and it is the wrapper,
because behavioural tests can only cover the branches that exist today.

Still open, found while fixing this: the tool-name allowlist above still loses
to trailing punctuation — `redact_text("Use project_task__update.")` returns
`Use [redacted-secret]`, because `_HIGH_ENTROPY_CANDIDATE_PATTERN` swallows the
final `.` and `_SAFE_IDENTIFIER_PATTERN` requires the token to end alphanumeric.
Bare names are fine, so `test_tool_name_secret_redaction.py` (which enumerates
the live registry unpunctuated) does not see it.

**`users.tenant_id` / `users.workspace_id` are not the authoritative tenant.**
These Postgres columns are written once, at signup, to the user's first/home
workspace — never updated afterward. The moment a user is invited into a
*second* workspace bound to a different tenant (the normal multiplayer case),
they go stale. Trusting them for request-scoped resolution instead of
resolving per-workspace (`control_plane_repository.resolve_tenant_id_for_workspace`
/ `auth.workspace_tenant_id`) broke inviting a brand-new email into a
project — `routes_workspaces.py`'s `_control_plane_tenant_id()` read
`user["tenant_id"]` first, got the wrong tenant, and a real project lookup
silently 400'd with "Project not found in this workspace." Fixed 2026-08-08;
same-shaped bug also found and fixed in `workspace_admin_service.py` and
`workspace_ai_route_service.py` (both had their own `_current_user_tenant_id`
reading the same stale field). A schema-level fix (rename the columns to
`home_tenant_id`/`home_workspace_id` so a future misread fails loudly instead
of returning a plausible wrong value) is drafted and tested against a
disposable DB but not applied — needs the founder's sign-off, since it also
requires updating every legitimate "home tenant" reader
(`account_shell_service.py`'s cache-key seed, `routes_workspaces.py`'s
`create_workspace` bootstrap). Before reading `tenant_id` off a user record
anywhere, resolve it from the workspace instead.

**A channel list copied into a third place.** The local-bridge channel map
exists in `personal_channels_service.LOCAL_BRIDGE_PERSONAL_CHANNELS`, in
`empyralis-gateway/src/channels/local-bridge-runtime.ts`, and — until
2026-08-08 — a third time as a literal in `routes_personal_channels.py`.
Adding the OpenClaw channels updated the first two, so those channels
accepted inbound and dispatched automatic replies while
`POST /personal-channels/{key}/gateways/{id}/messages` answered 404 for the
same key. Now derived from the service map. The gateway's copy is a genuine
cross-language duplicate and stays, guarded by drift assertions in both
directions; a same-language third copy never earns its place.

**A mock protects a seam, not a path.** `test_genuinely_silent_turn_still_returns_none`
patched `execute_sage_turn`, got its `None`, and passed for months — while
the code AFTER that seam ran a second, unmocked LLM turn. The sync
`build_whatsapp_personal_reply` / `build_telegram_personal_reply` (both live:
`personal_channels_service.py:2775` and `:3277`) treated the unified path's
`None` as "nothing to send, try harder" and fell through to the legacy
no-tools `_build_personal_reply`, which re-asked the model with no mention
gate, no envelope and no group context — asked "hello" it answered "Hello!
How can I help you today?" and shipped it. **Silence is a decision, never a
failure.** The async builders never had that fallback; now the sync ones
don't either, and the whole legacy no-tools path is deleted rather than left
for someone to rewire. Fixed 2026-08-08. Two rules follow. A test asserting
an ABSENCE must also assert the call count, or it cannot tell "nothing
happened" from "something else happened". And when a mocked path still
reaches a provider, the path has moved out from under the patch — find where
it goes now before re-pointing the mock, because the move is usually the bug.

**A row written under one scope and updated under another is a silent
no-op, and a test that reads the UNION of both scopes will never see it.**
Second instance of "silence is a decision" being defeated, this time from
the persistence layer. `_handle_local_bridge_gateway_channel_inbound`
recorded its inbound row with the real `agent_id`;
`_deliver_local_bridge_personal_reply` had no `agent_id` parameter at all,
so all four of its `mark_inbound_processed` calls defaulted to
`LEGACY_UNSCOPED_AGENT_ID` — an UPDATE keyed on
`(gateway_id, channel_key, agent_id, external_message_id)` that matched zero
rows, no error. The lost write is the no-reply marker, i.e. the only record
that the agent was asked and DELIBERATELY said nothing, and the only reader
is the guard at the top of that same function. `channel.inbound` is
at-least-once on every leg (`ws-client.publishEvent` re-enqueues into a
replayable outbox; `local-bridge-runtime`'s seen-event set is in-memory;
the OpenClaw plugin retries through its own durable `BoundedRetryQueue`), so
a redelivered message re-ran the turn and answered where the first pass had
chosen silence — reproduced, it emits the same "Hello! How can I help you
today?" as the group-ban incident. Affected the whole local-bridge family
(Signal/iMessage/WeChat + all five OpenClaw channels); the shared
`_control_command_block_result` had the same defect and reached
WhatsApp/Telegram too. Hosted/cloud channels do not share it —
`handle_cloud_channel_inbound` never touches `personal_channel_inbound_messages`.
Fixed 2026-08-08; `agent_id` is now a REQUIRED keyword with no default on
both, so a forgetful caller fails loudly. Three rules follow. **A scope
column with a default is a loaded gun** — make it required on any function
that both reads and writes the scoped row. **Never write a test that reads
the union of two scopes to stay green either way**; `test_openclaw_channel_
outbound.py`'s `_all_rows` did exactly that, deliberately, and is why this
survived a build that was looking right at it. And outbound rows on this
family stay unscoped ON PURPOSE (the explicit `POST .../messages` route has
no `agent_id` and must share one idempotency namespace with auto-replies) —
that one is uniform, not a mismatch, so do not "finish the job".

**A stand-in left in `sys.modules` becomes production's `server` forever.**
Eight modules late-bind `server` with `if _server is not None: return` and
cache it; `external_write_safety` additionally copies its whole namespace
into its own globals. Fourteen test modules install a stand-in
`types.ModuleType("server")` carrying a handful of attributes — 67 install
sites — for the length of one test, each restoring it in its own cleanup
block (`test_sage_context_files_api.py` never restores at all), and a test
that FAILS before reaching that block leaves the stand-in registered. One
does today
(`test_runtime_runs_api_canonical_routes.py::test_create_runtime_session_
canonicalizes_web_direct_chat_thread`). Everything that late-bound after
that point kept a six-name module for the rest of the process, silently:
`IDEMPOTENCY_RECORDS` and the rest of `server`'s namespace simply were not
there, and the AttributeError surfaced in unrelated tests much later.
That, not `importlib.reload`, is what made the Python suite report a
different number on different orderings — 450 failures in alphabetical
order, 465 and 462 under two shuffles, the same 8801 tests and the same
commit. Fixed 2026-08-08 two ways: `external_write_safety._init()` rebinds
when `sys.modules["server"]` is not the object it cached and refreshes the
names it copied, plus a module-level `__getattr__` so reading a copied name
from OUTSIDE forces the bind instead of depending on some earlier test
having called in; and conftest restores `sys.modules["server"]` after every
test and drops any `_server` cache holding something else — on the narrow
waist, because the per-file cleanup block is precisely the rule that already
failed. `test_reload_isolation.py` guards the reload half structurally.

Do NOT extend that rebind to the other seven late-binders. Tests for
`local_queue`, `provider_profiles` and `vault_store` inject a
`SimpleNamespace` straight into `module._server` on purpose, and a guard
that re-imports the real `server` whenever `_server` is not
`sys.modules["server"]` throws their stub away — measured: +26 failures
across `test_local_queue_machine_controls.py`,
`test_local_queue_watchdog.py` and `test_local_worker_crash_rehearsal.py`.
Those seven dereference `_server.attr` at call time, so a stale bind fails
loudly anyway; only `external_write_safety` copies the namespace, and only a
namespace copy can go missing in silence.

**The suite is deterministic; the ENVIRONMENT is what moves the number.**
Two identical-order full runs produce byte-identical failing sets (450 vs
450, zero flapping) even with three other suites competing for the box. So
when two people quote different numbers they ran different experiments.
Three things change it without changing a line of source, and every run now
prints all three in its header: **the interpreter** (`python -m pytest`
takes whatever is first on PATH — the repo venv is 3.12, `ci.yml` pins
3.14, and their pytest/fastapi versions differ), **whether the Rust kernel
binary is built** (`target/` is untracked, so a fresh worktree skips 77
`@pytest.mark.kernel` tests that a `cargo build` tree runs for real), and
**how many suites are in flight** (a concurrent run gets killed under memory
pressure, and a killed run's truncated output reads as a *smaller* failure
count, not as an error — that is how "±16" gets quoted). Run it as
`DATABASE_URL= venv/bin/python -m pytest server_modules/tests`, alone.

**Nothing gates on the Python suite.** Every workflow in `.github/workflows`
is `on: workflow_dispatch` — no push trigger, no PR trigger, no git hooks.
`ci.yml`'s one pytest job runs 21 hand-picked files, not the suite, and
`--ignore`s nothing. A suite carrying ~450 known failures is not a gate and
should not be described as one.

**Tests write to the developer's real `~/.empyralis/state`.** Nineteen
modules bake `EMPYRALIS_STATE_HOME` at IMPORT time; conftest sets the env var
in a fixture, which is far too late, and hand-patches only seven constants
across five modules. The rest still point at the real home —
`control_plane_repository.LOCAL_CONTROL_PLANE_DB_FILE` and its 8.8MB
`agent-threads.json` sibling have their mtime moved by an ordinary `pytest`
run. Every parallel agent's suite shares those files. Redirecting them
generically (walk `sys.modules` for `server_modules.*` `Path` attributes
under `~/.empyralis`) works and is drafted, but it UNMASKS at least three
tests in `test_connectors_actions_store_credential_cross_workspace_ownership.py`
that pass only because the developer's real vault key file exists — on a
clean box they hit `runtime_kernel_unavailable`. Left out of the determinism
fix deliberately so a pollution fix does not arrive disguised as a stability
one; it needs its own change and its own full-suite measurement.

**Branches whose work gets redone on main.** Nine branches were found with
real commits, all superseded by the same fixes re-implemented directly on
main days later. If a branch exists, merge it or delete it — leaving it means
someone rebuilds it.

**An untracked work order is invisible to the agent doing the work.** Since
one agent = one worktree, an uncommitted file exists ONLY in the primary
tree. `CHANNEL-ADOPTION-PLAN.md` sat untracked for three whole build steps
while every dispatched agent was instructed to read it first — none of them
could, and the failure is silent (a missing file, not an error). Two rules
follow. **Commit any document you intend an agent to read**, before
dispatching. And when handing off, put the load-bearing constraints in the
prompt itself, not only behind a path — a prompt always arrives, a file
reference may not.

**Delete a superseded work order; do not archive it.** A stale design doc
gets cited as present truth, which is bad. A stale *work order* gets
**executed**, which is worse. `CHANNEL-PORT-PLAN.md` instructed an agent to
hand-port ~190,000 lines of OpenClaw TypeScript — the precise program its
own successor was written to cancel. Deleted 2026-08-08. If a plan is dead,
the file dies with it; git history is the archive.

## Learn from the masters, then verify

Adopting Anthropic's Agent SDK beat the hand-rolled harness. Rejecting
RAG/embeddings for agentic search followed Claude Code's own documented
reversal (Boris Cherny: *"Early versions of Claude Code used RAG + a local
vector db, but we found pretty quickly that agentic search generally works
better"*). OpenClaw's three-gate channel model (DM pairing → group allowlist
→ mention gating, consistent defaults across every channel) is the reference
for channel authorization, and `mention_gating_service.py` is already a port
of it.

**The RAG pipeline that contradicted that decision is deleted (2026-08-08).**
`knowledge_rag_service.py` (chunking, hash/sentence-transformer embeddings, a
LanceDB vector store, RRF fusion), the four derived tables
(`knowledge_sources`/`_chunks`/`_embeddings`/`_retrieval_events`), their
repository functions, the Rust control-plane gate ops, `lancedb`+`pandas`, the
`knowledge_retrieval` credit type and the `rag` action domain all went with it.
It was not merely off-doctrine — it fed nothing:

```
upload ─▶ chunk ─▶ embed ─▶ knowledge_chunks/_embeddings
                                    │
                                    ▼
                         retrieve_knowledge()   ← 1 non-test caller:
                                    │             verify_deployed_agent_
                                    ▼             knowledge_retrieval(),
                              (no agent turn)     the endpoint whose only
                                                  job was to say the index
                                                  worked. No frontend
                                                  called even that.
```

What actually feeds a turn — and still does — is
`unified_memory_service._search_knowledge_documents`: a plain keyword search
over the raw uploaded files on disk, reaching the prompt through
`workspace_context_memory_adapter` under the same "Retrieved Knowledge Sources"
heading. Two implementations of one idea existed side by side; the agentic one
was the live one. **Uploaded files are the user's data and were not touched** —
`POST /deployed-agents/{id}/knowledge/files` still stores them; only the
derived index is gone. Do not confuse the dropped `knowledge_sources` TABLE
(derived) with `deployed_agents.knowledge_sources` JSONB (owner config, kept).
`preflight._check_removed_knowledge_rag_config()` refuses to boot if
`EMPYRALIS_RAG_*` / `OPENAI_EMBEDDINGS_URL` are still set, so the removed knobs
fail loudly instead of looking configured.

But **do not import a single-operator project's security assumptions into a
multi-tenant one.** OpenClaw's own docs: *"not a hostile multi-tenant
security boundary… one trusted operator boundary per gateway."* Their CVE
record (sandbox escape; a client-asserted `senderIsOwner` flag trusted
because it arrived over loopback) is what happens when that boundary is
ignored. Read their source, port the design, never vendor their core.

**"Channels" is ONE system, never a per-channel integration list.** The
founder's instruction, given repeatedly and violated anyway: *"there is only
one thing which is channels… we don't have telegram or WhatsApp, we have
channels."* We run OpenClaw's gateway as the transport and wire ONE adapter;
whatever channels their gateway carries, we carry. Both legs already do a
bare `openclaw_` prefix strip/prepend, so there is no per-channel code on our
side — which means a hand-maintained channel list is pure curation and pure
defect. The first build shipped a hardcoded 5-name tuple in
`channel_lane_contract_service` plus a parallel label map in
`personal_channels_service`, with a drift check between them; the drift check
was treating a symptom of a list that should not exist. **If adding a channel
requires an Empyralis code change, it is wired wrong.** Same test for any
future transport we adopt: derive the capability set from the thing that owns
it, never transcribe it.

**OpenClaw's `message_received` tap is post-gate and fact-less.** Verified
against the shipped v2026.6.10 bundle 2026-08-08, correcting the earlier
belief (recorded in the bridge plugin and in CHANNEL-ADOPTION-PLAN.md) that
it "fires unconditionally on every inbound message". The hook call site
(`dispatch-*.js:1240`) is unconditional; reaching it is not.
`message-access-*.js`'s `decideChannelIngress` returns admission
`drop`/`skip`/`pairing-required` with gate effects literally named
`block-dispatch`, and each adapter returns before enqueueing
(`message-handler.preflight-*.js:1009` for Discord, `bot-*.js:4132` for
Telegram — which on a mention miss fires only the INTERNAL hook, never the
plugin one). Their own doc: *"A mention miss returns `admission: "skip"` so
the turn kernel does not process an observe-only turn."*

Worse, the event carries neither `isGroup` nor `wasMentioned` —
`toPluginMessageReceivedEvent` forwards both only on the sibling
`inbound_claim` event, and the internal fact is
`Boolean(ctx.GroupSubject || ctx.GroupChannel)` where only `GroupChannel`
survives into metadata, so a Telegram/WhatsApp group looks exactly like a DM
at the tap. WhatsApp additionally suppresses the hook entirely unless
`channels.whatsapp.pluginHooks.messageReceived: true` is set.

Hence `personal_channels_service.normalize_openclaw_gate_facts`: unknown
group-ness is treated as a GROUP (strict side of the union), so it lands on
Gates 2/3 (allowlist + require-mention) instead of Gate 1 (default open).
Never derive a mention from message text on this path — the payload lacks
the bot's own handle/id, it misses Telegram `text_mention` and WhatsApp
`mentionedJid` entirely, and `mention_gating_service`'s contract forbids
reading content at all. Consequence to state plainly to anyone who asks why
their OpenClaw channel is silent: it is silent BY DESIGN until the owner
allowlists the chat and turns `require_mention` off for it, or until
OpenClaw starts forwarding `wasMentioned` (the bridge schema and mapper
already carry the field).

**OpenClaw outbound is a WS RPC from the box, never an HTTP call from the
cloud.** Landed 2026-08-08 (step 3). Their `admin-http-rpc` allowlist has no
send/message method, so a stateless cloud caller cannot deliver at all;
delivery is only reachable through `message.action` on the Gateway
**WebSocket**, from a process on the same machine. Hence
`empyralis-gateway/src/openclaw/openclaw-gateway-client.ts` holding one live
loopback session shared by all five channel runtimes. Do not "simplify" this
into an HTTP call — it does not exist.

Three rules that path must keep. **Scope is `operator.write`, never
`operator.admin`** — admin is the only scope under which OpenClaw honours a
client-asserted `senderIsOwner`, which is literally one of their CVEs; a
client that cannot claim it can never reintroduce it. **Classify by
`error.code`** (their closed `ErrorCodes` set plus `retryable`/`retryAfterMs`),
never by the sentence — an unknown code is treated as PERMANENT so it
surfaces instead of looping. **Retry only under the caller's own
`idempotencyKey`**, which OpenClaw dedupes on (`resolveGatewayInflightRequest`);
that key is what makes a retry not a duplicate message, so it is required,
never defaulted.

The cloud side needed no new outbound stack: `_OpenClawPersonalChannelHandler`
already inherits `_deliver_local_bridge_personal_reply` ->
`dispatch_channel_outbound`, and the only missing piece was a
`PersonalChannelRuntime` registered under the `openclaw_*` keys. **The bridge
plugin's `message_sending` cancel predicate also fires on the replies we
originate**, so the gateway refuses to send text that predicate would match
(`wouldBridgePluginCancel`) — an invisible cancellation inside OpenClaw is
exactly the silent drop this step exists to eliminate. **A configured but
disconnected outbound socket must never be reported with an
inbound-blocking health status** (`disconnected`/`unavailable`/…) — inbound
arrives over loopback HTTP from the plugin and does not depend on that
socket, and `_assert_gateway_advertised_personal_channel` would drop already-
arrived messages. Report `connecting` with `connected: false`.

**OpenClaw's config is a DERIVED ARTIFACT of Empyralis policy, and the
mapping is per-axis, not one-to-one.** Landed 2026-08-08 (step 4,
`empyralis-gateway/src/openclaw/provisioning/`). Their config decides what we
ever see, so ours can only narrow it. Which store is authoritative follows
from which gate fact survives their tap:

```
AXIS               FACT AT OUR TAP     AUTHORITATIVE   OPENCLAW MUST BE
dm sender policy   sender id: PRESENT  Empyralis       ⊇ (never stricter)
group chat policy  chat id:   PRESENT  Empyralis       ⊇ (never stricter)
require_mention    wasMentioned: GONE  OpenClaw ONLY   == (exact)
```

Stricter-than-Empyralis is the bug: the message vanishes before our process
exists, our settings screen still says "open", and nothing can report it.
Looser is safe (we re-decide) but must be RECORDED — every widening carries a
code. `require_mention` has no ⊇ escape, so where it cannot be expressed the
CHANNEL FAILS CLOSED with an owner-facing reason. Today that is exactly
`channels.zalo` + `require_mention: false` (no `requireMention` field, no
per-group map, and their default is TRUE).

Drift: regenerate-and-restart, always journaled (`openclaw.provision
.drift_corrected`); refuse-to-run when read-back cannot verify the lockdown
or the policy, when the version is off the pin, or when `openclaw security
audit` is not clean. Never reconcile the other way — importing their config
into our database would make the owner's settings a lagging mirror of a file
they cannot see. Verification always reads the EFFECTIVE config back out of
OpenClaw, never the document we meant to write.

Four things about their product that only running it reveals — none are in
the schema, and each is silent:
- **`dmPolicy: "open"` alone means DROP EVERY DM.** `allowFrom` must contain
  `"*"`. Their config validator says so; the schema does not.
- **`--profile` does not isolate the agent workspace.** It lands in
  `~/.openclaw/workspace-<profile>` — inside the operator's shared tree,
  beside every other customer's. Pin `agents.defaults.workspace` into the
  profile's own state dir, or the one-instance-per-customer claim is hollow.
- **A `groups: {"*": …}` entry sets `allowAll`**, silently turning a
  `groupPolicy: "allowlist"` into "every group". Use per-chat keys.
- **Their id is `qqbot`, not `qq`.** The Empyralis `channel_key` suffix must
  BE their channel id verbatim; both legs do a bare prefix strip.

A transport instance gets NO tool authority (`tools.profile: "minimal"`,
elevated off, `fs.workspaceOnly`, an explicit denylist) — their own audit
refuses the instance otherwise, and it is right to: anyone who can message a
tool-enabled agent shares its authority. Their
`security.trust_model.multi_user_heuristic` warn always fires and is
acknowledged, but ONLY because per-profile isolation, no tools, and no brain
are each enforced by a lockdown check that would refuse the instance first.
Never silence a finding via their `security.audit.suppressions` — the
lockdown forbids it outright, so "the audit is clean" keeps meaning
something.

Provisioning WRITES the config; it does not restart their process, and it
says so (`restart_required`). Their `gateway.restart.request` is scoped
`operator.admin`, and `operator.admin` stays permanently out of reach — it is
the only scope under which OpenClaw honours a client-asserted `senderIsOwner`,
one of their CVEs. The supervised unit's KeepAlive brings a new config into
force; never widen the scope to hurry that along, and never report a policy
as in force when it has only been written.

**A real Empyralis-provisioned OpenClaw instance has now existed** (2026-08-08,
profile `empyralis-first-run`, launchd `ai.empyralis.openclaw.empyralis-first-run`).
Running it for the first time settled four things a code reading could not:

```
WHAT A FIRST RUN ACTUALLY NEEDS, IN ORDER
  1. openclaw@2026.6.10 on PATH                 provisioning REFUSES otherwise
  2. an Empyralis gateway with BOTH secrets     EMPYRALIS_BRIDGE_TOKEN +
     (else openclaw.provision is never              EMPYRALIS_OPENCLAW_GATEWAY_TOKEN
      advertised, so the cloud cannot dispatch)
  3. a cloud trigger  ─────────────────────────▶ THIS WAS MISSING. see below.
  4. `openclaw plugins install @openclaw/<id>`   NOT DONE BY PROVISIONING. see below.
  5. the channel credential                      ← the only owner-supplied step
```

**There was no way to set the transport up.** `provision_openclaw_gateway`'s
only caller was `reconcile_openclaw_policy_best_effort` (PATCH
.../group-policy), and the boot reconcile is a documented no-op on a box that
has never been provisioned — it re-asserts a stored policy and cannot create
the first one. So the first run on any machine had no entry point, and
everything downstream of it was dead in practice. Closed by
`POST /personal-channels/openclaw/gateways/{id}/provision`, which is
deliberately NOT best-effort: a settings save that cannot reach the box must
still return 200, but a setup action that returns "nothing happened" is
indistinguishable from success. A `refused` result stays a 200 carrying the
whole report — a refusal is a successful round trip that says why.

**None of the five transported channels ships bundled in openclaw@2026.6.10.**
Its `dist/extensions/` carries imessage/irc/mattermost/signal/sms/telegram —
NOT feishu, line, qqbot, zalo or msteams, all five of which are separate
`@openclaw/<id>` npm packages. Their own docs claim zalo and msteams are
bundled "in current releases"; in the pinned build they are not. Provisioning
writes `channels.<id>.*` policy for a channel whose plugin is absent, which is
exactly why `message.action` answers `unsupported channel: <id>` — that clean
rejection means "no plugin AND no credential", not "no credential". Step 5
needs `openclaw plugins install` in the provisioning run (and a decision about
pinning those packages, which version independently of the CLI — 2026.7.1
against a 2026.6.10 CLI today).

Two smaller ones, both live-path: `OpenClawProvisioningRuntime` never passes
`probeHealth` to the provisioner, so `healthy` is ALWAYS null in every result
the cloud sees. And the OpenClaw channels are absent from
`channel_lane_contract_service.PERSONAL_CHANNEL_ROADMAP` (they are added to
`PERSONAL_CHANNEL_SPECS` only), so `GET /personal-channels/gateways/{id}/channels`
— the sole channel-listing endpoint, and what any UI would render — does not
show them at all. A second hand-maintained channel list, doing exactly what the
"channels is ONE system" rule above says it must not.

**A gateway frame `seq` is allocated ONCE, through
`GatewayCheckpoints.allocateClientSeq()`.** Never
`(await checkpoints.load()).lastClientSeq + 1` at a call site: that shape lost
a real customer message on the first live inbound test, and it fails two
independent ways.

```
publishEvent  (one call per bridge-plugin POST — genuinely concurrent)
  A: load() ──await──▶ seq=1 ──▶ save() ──▶ send      seq 1  ✓ delivered
  B: load() ──await──▶ seq=1 ──▶ save() ──▶ send      seq 1  ✗ 4408, message GONE
     └ both read before either wrote        └ and save() is DEBOUNCED 100ms,
                                              so even serialized, B re-reads 0
```

The cloud treats a non-increasing `seq` as fatal (`gateway_protocol_service`'s
`gateway frame replay detected`, close code 4408). The second message is lost
outright — already written to the socket, so never enqueued in the outbox and
nothing to replay, while the bridge plugin's own durable queue had been 202'd
and dropped it. Nothing anywhere reports the loss. Observed with two events
23ms apart; two real messages in the same second would do it.

Both halves are fixed in `allocateClientSeq` (in-memory mirror + its own gate),
which is the same in-memory-mirror pattern `lastKnownHealthState` already used
in that class for the same debounce reason, plus `withClientSeqLock` in
`ws-client` so frames are WRITTEN in allocation order — allocating in order and
sending out of order trips the identical guard.
`__tests__/ws-client-event-seq-race.test.ts` drives the REAL
`GatewayCheckpoints`, not a stand-in: a stub whose `save()` writes through
turns green as soon as the race is closed while production still emits
duplicates — "a mock protects a seam, not a path", measured.

## Testing the UI

**Seed your own data. Never ask for the founder's account, and never copy secrets.**
An agent testing a UI at scale should sign up a fresh local account and create
what it needs — 20 agents, 40 projects, a task with 50 comments — then look at
the real screen. It takes minutes, needs no credentials, and exercises the
actual render path. A static reproduction proves the mock renders, not the app.

**The one blessed way to bring up a throwaway stack is
`frontend/scripts/start-e2e-backend.sh`.** Do not hand-roll a backend boot —
that is exactly how MAN-202 happened: a hand-rolled stack, run from a git
worktree, silently inherited `DATABASE_URL` from the real repo root's `.env`
and an agent wiped the founder's local database while believing it was
isolated. `DATABASE_URL` must always be exported explicitly, pointing at a
database whose name says it's disposable (e.g. `empyralis_test`) — the
runtime now refuses to boot a dev/test/local process without it
(`server_modules/preflight.py`'s `_check_local_stack_database_url`). Never
set it by copying a value you found somewhere; if you don't know what it
should be, ask rather than guess.

**A test may never reach a live LLM provider.** Enforced in
`server_modules/tests/conftest.py`, sibling to the `DATABASE_URL` guard and
added for the same reason: a credentialed developer's `pytest` run was making
real, billed DeepSeek/OpenAI calls, and on a box WITHOUT credentials the same
calls failed quietly and let assertions pass for unrelated reasons. There is
no single provider chokepoint to patch — traffic leaves through
`scripts/orion_local_worker_llm.py` (urllib + a `curl` fallback),
`runtime_common.http_json_request`, `openai_compat_adapter`'s httpx client,
the Node `claude` CLI the Agent SDK spawns, and several one-off SDK clients —
so the guard sits at `socket.socket.connect` (every in-process transport ends
there) plus a subprocess denylist for the ones that leave the process. The
violation is a **`BaseException`**, because the channel and runtime paths are
full of broad `except Exception:` handlers that would otherwise swallow it,
and it is re-raised at teardown so not even a bare `except:` buys a green
test. Allowed: loopback, the `DATABASE_URL` host, `curl` at a loopback URL,
and local CLI capability probes (`claude auth status`). Opt in with
`@pytest.mark.live_provider` or `EMPYRALIS_TEST_ALLOW_LIVE_PROVIDER_CALLS=1`;
no test needs either today. Turning it on exposed 12 tests
(`test_sage_agent_runtime_service.py` ×6, `test_preflight.py` ×3,
`test_operator_chat.py`, `test_sage_chat_api.py`) that had been calling
providers for real — still open, and each needs a mock, not a weaker
assertion.

Python tests passing is not evidence the UI works. A test asserting a function
returns a dict does not notice that the button calling it fires no request.
Anything user-facing gets driven in a real browser: click it, watch the network
tab, read the console.

## Working agreements

- **One agent = one worktree = one branch.** Never two agents editing the same
  working tree. See `docs/AGENT-OPERATING-RULES.md`.
- **Never `git stash` when other agents are running.** Worktrees share one
  `.git`, so they share one stash stack — a `stash pop` can silently pull in a
  *different* agent's uncommitted work. This happened 2026-07-31 and was caught
  only because the agent inspected what it popped. To revert temporarily, use
  `git diff > /tmp/x.patch` + `git checkout --`, then `git apply`.
- Never weaken a test assertion to make it pass. A green suite that asserts
  nothing is worse than a red one.
- Never commit `frontend/next-env.d.ts` or `frontend/tsconfig.json` — a dev
  server with a custom dist dir rewrites both, and committing them breaks
  everyone else's build.
- Deploys: `docs/DEPLOY-RUNBOOK.md`. Production is a single VPS; the frontend
  build must be detached (`nohup`) or a dropped SSH session kills it.
- **Apply production migrations as the app's own database role, not as the
  Postgres superuser.** A superuser-applied migration leaves the new table
  owned by `postgres`; the app cannot alter its own table on boot and
  crash-loops. This took production down for ~4 minutes on 2026-08-07. Fix is
  `ALTER TABLE <t> OWNER TO empyralis_app`, but not making the mistake is
  cheaper. Also: `migrations/enable_rls.sql` must be re-run after adding any
  new table — without its policy the table exists with no RLS and reads
  return nothing.
- **Cloudflare fronts production**, undocumented in the runbook and in
  `deploy/nginx-empyralis.conf`, both of which read as though nginx
  terminates TLS directly. Its ~100s idle timeout — not nginx's 86400s — is
  the real ceiling on any long request. A silent SSE stream gets cut at
  ~125s; keepalive comments prevent it.
- Agent worktrees accumulate and nothing prunes them. 177 of them (plus an
  11GB `.git`) filled the disk to 100% mid-session on 2026-08-07 and killed
  several running agents. Prune merged ones periodically; never force-remove
  one with uncommitted work.

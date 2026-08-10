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

**A second engine behind one dispatch seam must be symmetric about MONEY,
not just about its return value.** MAN-310 put the Claude Agent SDK behind
`_run_sage_action_loop_v3._collect_stream_events` and made it the production
default (2026-08-06). Both branches produce the identical event contract —
which is what got reviewed, and what the branch's own comment asserts
("same return contract... the two paths never interact"). But the credit
debit for an ordinary turn was never IN that contract: it lived inside the
legacy branch's *callee*, `stream_provider_backed_direct_chat` ->
`direct_chat_hosted_usage_service`, which labels itself "the PRIMARY debit
path". Swapping the engine swapped the debit out with it.

```
_collect_stream_events
  ├ legacy → stream_provider_backed_direct_chat → …hosted_usage… ─▶ DEBIT ✓
  └ sdk    → collect_events_via_claude_agent_sdk ───────────────▶ nothing  ✗
              ↑ THE PRODUCTION DEFAULT
```

The other debit (`debit_workspace_credits_for_turn_atomic`) sat in the
cloud-fallthrough block, which a normal turn never reaches — the action-loop
branch `return`s ~500 lines earlier. So every ordinary turn on the default
engine wrote a real `usage_events` row and charged nothing: spend metered,
never billed, no error anywhere, the billing page showing usage nobody paid
for. Fixed 2026-08-09 by fusing metering and debiting into one function
(`_meter_and_debit_turn`), so "record spend without charging for it" is not
expressible in that module.

Three rules follow. **When you add a branch behind a dispatch seam,
enumerate the SIDE EFFECTS of the old branch's callees, not just its return
value** — identical return contracts are exactly what made this review pass.
**A test-only default is a permanent blind spot over the real default
path**: `_resolve_turn_engine_id` returns `""` under `PYTEST_CURRENT_TEST`
so old mocks keep working, which means every test that does not pass
`engine_options={"engine": "claude_agent_sdk"}` exercises the engine
production does NOT use — that is how a suite carrying a whole file on
double-charge prevention never noticed a zero-charge bug. And **a money path
needs a call-COUNT assertion**: "a debit happened" is satisfied by a double
charge just as happily as by a correct one, so
`test_default_engine_credit_debit.py` asserts `== 1`, plus AST assertions
that the debit primitive has exactly one call site and that the two seam
call sites are mutually exclusive by control flow — behavioural tests can
only cover the engines that exist today.

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

**"Nothing to send" is THREE facts, and collapsing them threw away
customers' messages.** Third instance of "silence is a decision" being
defeated, this time at the delivery seam. `_build_error_reply_dict` returns
`{"text": ""}` on ANY turn failure (credits gone, provider unreachable,
context overflow, a crash) — byte-identical to a turn that ran fine and
deliberately said nothing. All four personal-channel delivery seams
(WhatsApp, Telegram, local-bridge/OpenClaw, cloud) read that one empty string
the same way and wrote the `<channel>:noreply:` marker, which is the durable
"the agent was asked and CHOSE not to answer" record AND the only thing the
redelivery guard reads. So a message the platform failed to answer was
permanently recorded as answered: the person got nothing, was told nothing,
and the at-least-once retry (`ws-client`'s replayable outbox,
`local-bridge-runtime`'s seen-set, the OpenClaw plugin's `BoundedRetryQueue`)
was cancelled by the lie. Fixed 2026-08-09 in
`channel_adapter.resolve_channel_reply_outcome` —
deliver / silent / **undelivered** — on the narrow waist beside
`filter_channel_outbound_reply`, with an AST drift test banning a direct
filter call in `personal_channels_service.py` so a fifth seam cannot
reintroduce it. Undelivered writes no marker, audits `status="failed"`, and
leaves the row retriable.

Three rules follow. **An empty string is not a decision** — if two callers
must tell "chose not to" from "could not", the producer has to say which, and
a `text == ""` sentinel structurally cannot. **A suppression filter answers
"may this be sent", never "did the agent answer"** — those are different
questions and `filter_channel_outbound_reply` was being asked both.
And `filter_outbound_reply` (the `[SILENT]`/`NO_REPLY` sentinels — the
MODEL's own silence) and `is_channel_suppressed_text` (a PLATFORM status
string) must stay distinguishable at any seam that classifies intent;
collapsing them turns a legitimate quiet turn into an infinite retry.

**Audience is now expressible on the channel boundary, via a SECOND
allowlist, never a widening of the first.** `CHANNEL_SAFE_CODES` was
audience-blind, so an owner texting their own agent got exactly what a
stranger got. `platform_event.CHANNEL_OWNER_SAFE_CODES` +
`owner_channel_text_for_code()` is code-in / frozen-literal-out: it has no
string parameter, so no exception text, provider response, classified prose,
`ErrorNotification.raw_detail` or secret can travel through it, and
`CHANNEL_SUPPRESSED_TEXTS` is unchanged so the stranger path is byte-for-byte
what it was. Holds one code today (`channel_execution_failed`) and is
asserted to hold ONLY codes something actually produces — no dead entries.
Extend it with a producer that carries a stable CODE, never with a keyword
match on an exception's prose.

**`channel_concurrency_service` has ZERO production callers** — the whole
lease/quota gate (`thread_busy`, `agent_limit_exceeded`,
`workspace_limit_exceeded`, `workspace_rate_limited`), its
`agent_channel_execution_leases` table, its RLS policy, its Rust kernel ops
and five test files, wired to nothing. Its only importer,
`channel_execution_quota_adapter.py`, has zero callers of its own; the one
test that patches it through `agent_channel_router` patches symbols that
module no longer has. So `thread_busy` cannot fire today and no channel
message is ever refused for concurrency: a second message arriving mid-turn
starts a SECOND CONCURRENT TURN that read thread history before the first
turn's reply was written. Nothing is dropped; context is torn and reply order
is nondeterministic. Verified 2026-08-09. Do not describe the concurrency
gate as live, and do not "fix" a thread_busy drop that does not exist.

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

**Groups on the OpenClaw transport use gate-before-model, never see-and-decide
— founder's decision, 2026-08-09, overriding Ruling A for this path only.**
Ruling A (2026-07-16, `personal_channels_service.py`'s own comment block):
"Groups = see-and-decide, NOT mention-gated... the agent should SEE every
group message and decide to reply or stay silent by its own judgment." That
stays the rule for first-party channels (Telegram/WhatsApp/etc, still
running, being cut over and deleted per channel — see step 6 below) because
CLAUDE.md's own "stop building on the outgoing system" rule forbids new gate
logic on code scheduled for deletion.

For the OpenClaw transport the founder wants their behavior instead: never
let the model see a group message it hasn't been cleared to answer, full
stop, no judgment call delegated to the model. **This is already what
happens, structurally, for two independent reasons** — nothing new to build:

1. OpenClaw's own `decideChannelIngress` gates before we ever see the
   message (see the entry immediately below) — there is no code path on
   their transport where an unadmitted message reaches a model at all.
2. `normalize_openclaw_gate_facts` (below) treats unknown group-ness as a
   GROUP and routes to Gates 2/3, so even a message their gate admits still
   needs an explicit owner allowlist + mention-off before Empyralis dispatches
   a turn. No [SILENT] marker, no model judgment, no leak surface — the
   category of bug Ruling A's see-and-decide model is structurally exposed
   to (an agent choosing wrong in public) cannot occur on this path.

Net effect: the founder's two channel worlds already have two different
policies, correctly, without anyone writing a switch — old world sees and
decides, new world never sees until cleared. When the old world is deleted
(step 6), see-and-decide goes with it and gate-before-model is what's left,
which is exactly the target state.

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

**"Channels" is ONE system. The OpenClaw channel set is DERIVED from
OpenClaw, never hand-listed.** Landed 2026-08-08. Empyralis has no
per-channel code on this lane at all — both legs do a bare prefix
strip/prepend (`normalizeOpenClawChannelKey`,
`openClawChannelIdFromChannelKey`), so every channel they carry already works
through the identical path. Only the REGISTRY was curating, and it curated
badly: five channels out of their twenty-seven, typed by hand in FOUR places
(a tuple in `channel_lane_contract_service`, a parallel label map in
`personal_channels_service` with a set-equality check between them, an array
in the gateway's `capabilities.ts`, and a fifth list inside the schema
fixture generator). A set-equality check between two hand-written maps can
only ever answer "do my copies agree", never "are they right" — both were
wrong together, one carried `qq` (an id OpenClaw does not have), and two
empty sets would have been equal.

```
BEFORE                                  AFTER
  tuple(5) ─set-equality─ map(5)          openclaw_channel_manifest.json
     │                      │               (generated, 27, pinned)
     └─── TS array(5) ──────┘                     │
     └─── fixture list(6) ──┘        ┌────────────┼─────────────┐
  add a channel = 4 edits +         lane       personal    generated-
  remember their id verbatim        contract   channels    openclaw-
                                                           channels.ts
                                    add a channel = regenerate
```

Source of truth is `scripts/generate_openclaw_channel_manifest.py`, run
against the PINNED install: `openclaw channels list --all --json` for the
authoritative id set, `dist/channel-catalog.json` + `dist/extensions/*/
package.json` for labels (their own display names — `qqbot` is "QQ Bot",
which the deleted map called "QQ"), and `openclaw config schema` for the
per-channel policy shape `OPENCLAW_CHANNEL_POLICY_SHAPES` used to transcribe
by hand. The derivation reproduces all five hand-written shape rows exactly,
which is why it is trusted for the other twenty-two. The two id sources are
INDEPENDENT (a live CLI query vs files on disk) and generation FAILS if they
disagree — the expected set and the actual set must never come from one
place.

A checked-in manifest, not a runtime shell-out: the cloud has no OpenClaw
and never will, yet the cloud is the party that decides whether a
`channel_key` is real (`assert_personal_gateway_channel`). The set is a
property of the pinned version, exactly like the `message.action` param
names that pin already covers.

**The verbatim-id invariant is now automatic.** Nobody types a suffix; every
`channel_key` is `f"openclaw_{id}"` where the id came out of their registry,
and `openclaw_channel_id()` is a manifest LOOKUP rather than a prefix strip
— so an id they do not have fails on our side instead of arriving as
"unsupported channel" outbound, or not failing at all inbound.

**Overlap is COMPUTED and defaults to the proven implementation.** Eight of
their channels are platforms Empyralis already implements: telegram,
whatsapp, signal, imessage, `openclaw-weixin` (consumer WeChat — `wecom` is
WeChat Work, a different product and not an overlap), discord, slack, sms.
The overlap set is the intersection of the derived OpenClaw ids with the
platform tokens derived from BOTH first-party catalogs, so a platform they
add later that collides with ours is caught the day it ships and resolved in
favour of the existing runtime by DEFAULT. Superseded channels are DECLARED
and visible in the platform catalog (`status: superseded_by_first_party`,
`superseded_by` naming the owner) but never enter the lane specs, the
handler registry, or the gateway's advertisement — so declaring all 27
cannot put two runtimes on one account.
`openclaw_channel_registry.OPENCLAW_CUT_OVER_CHANNEL_IDS` is the ONE
authored datum in the whole system, and it is a decision rather than a list:
moving an id into it is step 6's swap, and the first-party implementation
must be deleted in the same change.

**"Not yet proven live" is expressed without a list.** `stage: "preview"` is
uniform across every OpenClaw channel — a property of the transport, so
there is nothing per-channel to keep honest. Promotion is not a catalog
edit: `proven_live` in `get_gateway_personal_channel_surfaces` is OBSERVED
(a real connection on a real gateway), the only version of "promote it from
a real message, not from a code reading" that cannot rot into a stale claim.

Two things this fixed in passing. The OpenClaw channels were in
`PERSONAL_CHANNEL_SPECS` but in NEITHER catalog, so
`get_gateway_personal_channel_surfaces` — which iterates
`personal_channel_catalog()` — never listed one: wired end to end and
invisible, the "built, tested, and never wired" shape one level up from the
code. And the schema fixture generator's own hand-written channel list was
the most dangerous of the five copies, because a fixture that stops covering
a channel does not fail — it silently stops checking it.

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
rejection means "no plugin AND no credential", not "no credential". **Closed
2026-08-09 — see below.**

**Channel plugins are INSTALLED by provisioning now, and their idempotency is
ours to enforce.** Landed 2026-08-09 (step 5,
`empyralis-gateway/src/openclaw/provisioning/openclaw-plugin-install.ts`), run
between the version pin and the schema audit so that BOTH the schema audit and
`openclaw security audit` see the third-party code loaded — "the audit was
clean before we added it" is not a claim worth making. Four things only running
the real CLI reveals:

```
openclaw plugins install <spec>            (measured, not read)
  first time   ─▶ exit 0, ~35s, writes plugins.entries.<id> + an install record
  SECOND time  ─▶ RE-DOWNLOADS the tarball, then exit 1
                  "plugin already exists … (delete it first)"
```

A naive install call therefore turns every reprovision into a failure — and a
NETWORK-DEPENDENT one, on a box that is fully installed and working. The
install is gated on read state (`plugins registry --json` ->
`installRecords[<pluginId>].resolvedSpec`), never on a try/catch, and the skip
branch shells out to nothing. Verified live: reprovisioning with the npm
registry blackholed returns `provisioned`, `configChanged: false`, no drift.

**The plugin version pin is OBSERVED, never authored.** Their installer
resolves host compatibility out loud: `Resolved @openclaw/feishu to
@openclaw/feishu@2026.7.1, but that version is incompatible with this OpenClaw
runtime; using newest compatible @openclaw/feishu@2026.6.10.` Authoring a
version literal would transcribe a compatibility decision only they can make;
but "newest compatible" MOVES, so two boxes provisioned a month apart would
silently run different code — the MAN-306 shape again. Both closed by letting
them choose once and holding them to it: `--pin` records an exact
`<name>@<version>`, that spec is copied into the Empyralis provisioning record
(`pluginPins`), and every later run refuses with
`openclaw_plugin_version_drift` when the live install record no longer equals
it. Their catalog's `min_host_version` is checked against our own pin BEFORE
the install, so "this plugin needs a newer OpenClaw" is a refusal rather than a
silent downgrade — and a range shape we cannot evaluate is ALSO a refusal, since
an unevaluated compatibility claim is one we cannot vouch for.

**The install descriptor is DERIVED from `dist/channel-catalog.json`, not
guessed.** `@openclaw/<id>` is right for the 16 official plugins and wrong for
all four external ones (`wecom` -> `@wecom/wecom-openclaw-plugin`, whose PLUGIN
id is `wecom-openclaw-plugin` — and the install registry keys on the plugin id,
not the channel id). The manifest generator emits `plugin_install` per channel
and FAILS unless the installable catalog (20) and the bundled extensions (7)
PARTITION the 27: a channel in neither would be advertised by Empyralis and
impossible to bring up, which is exactly the state Feishu was in.

**"No plugin" and "no credential" are now separately reportable, structurally.**
Measured on a real instance either side of one provisioning run:

```
                      plugin   credential   what OpenClaw says on a send
BEFORE                absent   absent       "Channel is unavailable: feishu.
                                             Install the official external
                                             plugin with: openclaw plugins
                                             install @openclaw/feishu"
AFTER  provisioning   present  absent       "Feishu account \"default\" not
                                             configured"
```

Never classify those by the sentence — both are bare `new Error(...)` in their
`channel-selection` module with no error code attached, and "stale string
matching" is already a documented failure here. The structural fact is
`channels list --all --json` -> `installed: true|false`, surfaced per channel
as `channel_plugins[].installed`. After a `provisioned` result, "no plugin" is
impossible for a requested channel, so any remaining failure IS the credential.

**Install only what is asked for; report on everything.** Twenty plugins on
every box is minutes of network per boot plus twenty third-party packages
running beside a customer's messages. Reporting scope is every enabled channel;
INSTALL scope is `install_plugin`, opt-IN across the cloud boundary and
computed from (an enabled `agent_channel_bindings` row) ∪ (a stored policy KEY
in the agent's install metadata) ∪ an explicit `install_channels` request on
the provision route. Bindings alone DEADLOCK — a binding is written when a
session connects, and a channel cannot connect before its plugin exists. Key
PRESENCE, never the policy's value: the loaders normalize a missing entry into
a full default document, so a value comparison cannot tell "never configured"
from "configured, and happens to match the default".

**A channel PLUGIN contributes its own tool surface, and the global `tools.*`
lockdown does not reach it.** The sharpest thing step 5 turned up, and it was
invisible before it because there were no plugins installed to contribute one.
`@openclaw/feishu` ships `channels.feishu.tools` — doc / chat / wiki / drive /
perm / scopes / bitable / base — i.e. create documents, manage permissions and
reach Drive in the owner's Feishu tenant, on an instance whose entire job is to
be a radio. `tools.profile: "minimal"` + `tools.elevated.enabled: false` +
`tools.deny` do not touch it. Their own audit catches it, but ONLY once a
credential is configured, which is precisely the moment the owner is least able
to act on it:

```
channels.feishu.doc_owner_open_id [warn]
  "channels.feishu tools include \"doc\"; feishu_doc action \"create\" can grant
   document access to the trusted requesting Feishu user."
  remediation: "Disable channels.feishu.tools.doc when not needed…"
```

`resolveOpenClawChannelToolFlags` now discovers every `channels.<id>.tools.<flag>`
boolean from the installed schema and writes them all FALSE, and a non-boolean
there is a shape finding that refuses the run. DISCOVERED, never listed — a
hard-coded set of Feishu's eight would stop covering the ninth and would cover
nothing for the next plugin that grows the node. Anything else a plugin
contributes to `channels.<id>.*` deserves the same question: the global
lockdown was written against a bundle with no third-party channel code in it.

**Provisioning never wipes a channel credential, and that is verified rather
than assumed.** `config patch` merges recursively and `findConfigDrift` is
one-directional, so `channels.<id>.appId`/`appSecret` — which the generator
does not write — survive every reprovision. Checked live with a placeholder
credential across a full provisioning run. It matters because the owner's setup
step and the boot reconcile would otherwise race, and the failure would present
as a channel that mysteriously logs out.

**`plugins.allow` must be non-empty, and that only became true once we started
installing.** OpenClaw said it itself on the first live run: *"plugins.allow is
empty; discovered non-bundled plugins may auto-load: feishu (…). Set
plugins.allow to explicit trusted ids."* Once provisioning writes into that
plugin directory, "whatever is on disk" stops being a safe inventory. The
generated config now names the bridge plugin plus exactly what that box
installed, and `plugin_allowlist_empty` is a lockdown violation. Safe rather
than blunt because of their own semantics — *"Configured bundled chat channels
can still activate their bundled plugin when the channel is explicitly enabled
in config"* — and this generator always writes an explicit `channels.<id>`
block. Verified live: bridge + installed feishu + bundled irc all `loaded`,
telegram (not enabled) `disabled`.

Two smaller ones, both live-path: `OpenClawProvisioningRuntime` never passes
`probeHealth` to the provisioner, so `healthy` is ALWAYS null in every result
the cloud sees. And the OpenClaw channels are absent from
`channel_lane_contract_service.PERSONAL_CHANNEL_ROADMAP` (they are added to
`PERSONAL_CHANNEL_SPECS` only), so `GET /personal-channels/gateways/{id}/channels`
— the sole channel-listing endpoint, and what any UI would render — does not
show them at all. A second hand-maintained channel list, doing exactly what the
"channels is ONE system" rule above says it must not.

**The channel SETUP FORM is generated from OpenClaw's schema, exactly like the
channel list is generated from their registry.** Landed 2026-08-09 (step 8).
Twenty-four hand-written forms would be the five-entry channel tuple all over
again: stale the day upstream renames a field, and simply absent for the
twenty-fifth channel. `scripts/generate_openclaw_channel_manifest.py` now emits
a per-channel `credential_shape` from the same `openclaw config schema` parse
that already produces the policy shape.

```
FIELD CLASS   SIGNAL (structural, no channel name anywhere)
secret        their SecretRef union:
                anyOf[{type:string}, oneOf[{source:const env|file|exec,
                                            provider, id}]]
              — OpenClaw's OWN declaration that a value is a credential.
              Corroborated independently by their per-plugin
              `dist/*secret-contract*.js`, which names the same paths.
identifier    plain string, no default/enum, not a *File/*Path, not in the
              policy surface, and NAME carried by <= 5 of their 24 channel
              nodes. Cross-channel name frequency is the second axis:
              boilerplate is shared (name, responsePrefix, historyLimit,
              webhookPath), a credential companion is unique to its platform
              (appId, accountSid, tenantId, homeserver, channelAccessToken).
              Secrets are EXEMPT — botToken is on four channels and is still
              a credential.
pairing       a channel declaring NEITHER a SecretRef field NOR a `*File`
              variant takes no pasted credential at all. They ship a
              file-backed variant only for credentials, which is why LINE
              (whose channelAccessToken/channelSecret are plain strings)
              still lands in `credential` via tokenFile/secretFile, and IRC's
              `password` via passwordFile. WhatsApp, iMessage, Signal,
              Twitch, Synology Chat, Zalo Personal and Tlon land in
              `pairing` and render NO form — a dead control is a product-law
              violation, not a caption.
plugin_absent the plugin contributes `channels.<id>` only once installed, so
              its fields are unknowable. Say so; never guess.
```

**`openclaw config get --json` REDACTS every secret at the source**
(`"appSecret": "__OPENCLAW_REDACTED__"`). That is what makes "the credential
never comes back out" structural rather than a rule to remember: the cloud
process cannot hold a stored channel credential even once it is stored, so
`set: true|false` is the only credential-shaped thing any read can produce.
The write path treats that placeholder arriving as a VALUE as a refusal — a
form echoing a masked read back would otherwise overwrite a live credential
with the literal string.

**The credential is a PASS-THROUGH; nothing in the cloud stores it.** Browser
-> `PUT /personal-channels/openclaw/gateways/{id}/channels/{key}/credential`
-> gateway WS -> `openclaw config patch` on the owner's own box. No table, no
log, no journal (field NAMES only, and the audit row carries names only too).
Deliberately not `vault_credentials`: that table has no `tenant_id` and
`list_all()` is a full-table read with the boundary applied in Python, so one
row per channel per gateway would make a known-weak scoping story worse for a
second copy of a secret with no reader. Execution locality already says the
credential belongs where the transport runs. The cost — rebuild the box and
the owner re-enters it — is the same trade the gateway token already makes.

**`openclaw.channel_setup` is a SEPARATE capability from `openclaw.provision`,
because they answer to different authorities.** Empyralis owns the policy and
REGENERATES it every run; the owner owns the credential and nothing may
regenerate it. Fusing them gives either a reprovision that wipes a credential
or a credential save that drags a whole policy render behind it. Verified live
2026-08-09: a full provisioning run left two browser-written credentials
intact (`config patch` merges, and the generator never writes those keys).

**Three states, never collapsed — the bar is OpenClaw's own `channels list`:**
`- Feishu: not installed, configured, disabled, run openclaw plugins install…`
i.e. plugin / credential / switch, plus the specific next action, on one line.
A single "Connected / Not connected" light destroys exactly the fact that says
which of the three to go fix. `installed` comes from
`channels list --all --json` -> `chat.<id>.installed`, `configured` is
PER-FIELD (one boolean would repeat the same collapse a level down), `enabled`
from the effective config. Each non-working state carries a BUTTON, not a
command, because the cloud can already drive the device; where the action
genuinely cannot happen in a browser (a QR scan) the row says so instead of
rendering a control that submits nothing. Never "Connected" — a connection is
proven by a real message arriving, and the screen has seen none.

**Those three states live in the panel a channel CARD opens, never on the card
face. The channel surface is a square-card grid — settled, and violated twice
already.** Every channel — first-party and transported alike — is one
`.fleet-channel-card` in one `.fleet-channel-grid` (4 across, 2 at <=900px),
and a card face is **icon + label + ONE pill, full stop**. Two failure modes
sit on either side of that and both have shipped:

```
✗ 2026-08-08  two sections     grid of 7 cards, then a separate panel below
                               "two components stacked is not one interface"
✗ 2026-08-09  one flat LIST    merged correctly, then rendered as text rows
                               carrying subtitle + 3 chips + a sentence + a
                               button, ×26 — a wall of text, rejected outright
✓ 2026-08-10  ONE card grid    face = icon + label + 1 pill
                               everything else ─▶ the panel the card OPENS
```

Fixing the second by reverting to the first is not available; both
instructions stand at once. `channelCardPill` (openclaw-channel-copy.ts, beside
`remediationFor`) is what makes them compatible: it reduces a channel to the
single most actionable word for the face off the SAME remediation the panel
renders, so pill and panel cannot drift. Nothing is dropped — the three states,
the remediation sentence and its button all live in the panel, which is the
shared `.fleet-channel-banner` shell both kinds of card open, so a transported
channel and a first-party one behave identically. The credential form is a
FORM BODY (`CredentialForm`), not a dialog: the panel owns the shell, so a
credential state renders inline rather than stacking a second modal on the
first. When a shell is replaced, delete the CSS it needed — the row-density
overrides left behind are how the next author rebuilds the list.

**Every hardware path installs the transport itself, and the customer never
types a command.** Landed 2026-08-09. Neither path did before:
`scripts/install-agent-computer.sh` (which the DigitalOcean/Hetzner/Vultr
cloud-init runs verbatim — `cloud_init_script` only downloads and executes it)
had zero OpenClaw references, and neither did `deploy/packer`, the BAKED image
path DigitalOcean actually prefers when a snapshot resolves. So
`provision_openclaw_gateway` had no trigger on a fresh box.

```
WHERE THE INSTALL HAPPENS NOW, AND WHY THERE
  gateway, every boot        AUTHORITATIVE. openclaw-runtime-install.ts, step 1
    ensureProvisionedAtBoot  of provision(). The only thing that reaches boxes
                             installed BEFORE this (they never re-run an
                             installer; they do take self-updates), the only
                             thing that works on an unprivileged Mac, and it
                             keeps the pin in ONE file beside its check.
  shell installers           SAME CODE, via openclaw-install-plan-cli.ts. They
    (root, pre-gateway)      own only what the gateway cannot: /etc/systemd/
                             system, and a moment before the gateway exists.
                             No version literal, no unit body, no npm knowledge
                             in bash — they write bytes they were handed.
  packer image               --runtime-only: the SOFTWARE only. Secrets, config
                             and the unit are per-box, at first boot.
```

Corollaries. **A boot reconcile that no-ops on a never-provisioned box is a
feature nobody can reach** — `reconcileFromLastAppliedPolicy` returned
undefined there, so the state that needed provisioning most was the one state
that never got it. `ensureProvisionedAtBoot` baselines with the EMPTY channel
policy: every lockdown step is channel-independent, so the box comes up
installed, locked down, audited and supervised while carrying no inbound
policy and fetching no third-party plugin. And **a secret baked into an image
is one credential for the whole fleet wearing a per-box costume** —
`80-verify.sh` now refutes the presence of `local-secrets.json` on the image.

**`EMPYRALIS_BRIDGE_TOKEN` and `EMPYRALIS_OPENCLAW_GATEWAY_TOKEN` were never
set by anything, so the entire channel transport was un-constructed on every
box this product has ever provisioned.** `index.ts` gated the inbound
listener, the outbound WS client, the OpenClaw channel runtimes AND the
`openclaw.provision` advertisement on both being present; no installer in the
repo wrote either name. Not broken — never built. "Built, tested, and never
wired", one level up from the code. Both are loopback-only secrets whose two
ends are BOTH written by us (the bridge plugin runs inside OpenClaw on the
same box; OpenClaw's `gateway.auth.token` is written by our own provisioning),
so there was never anything for a human to supply: `openclaw-local-secrets.ts`
mints and persists them under `stateDir`, env still wins, and an env-supplied
value is deliberately NOT copied to disk (a copy would make a later edit of
the env file silently ineffective). This is also what fixes already-installed
boxes, which never re-run an installer but do restart.

**OpenClaw needs a NEWER Node than the gateway, and the box must run both.**
`openclaw@2026.6.10` requires Node >= 22.19. `install_node20()` installs Node
20 on every Agent Computer; the founder's Mac runs Node 26, which is the only
reason hand-testing ever worked. The box cannot simply move: the gateway ships
as a PREBUILT artifact whose native modules (bufferutil, utf-8-validate,
sharp) are compiled against Node 20's ABI, so bumping it would break every
published artifact on every existing box. Hence `${INSTALL_ROOT}/openclaw-node`
and `withOpenClawNodeOnPath`, which prepends that bin dir to the CHILD's PATH
only — the npm install and the supervised unit, never the gateway itself.

```
npm install --global openclaw@2026.6.10   under Node 20
  exit 0, ~10min, 350MB of node_modules, /…/bin/openclaw on PATH
  then EVERY invocation:
    "openclaw: Node.js v22.19+ is required (current: v20.20.2)."
  unit: Restart=always + RestartSec=5  ─▶  restart loop, forever
  installer log:  "channel transport installed and running"
```

Three rules follow. **`npm install --global` does not enforce `engines`**, so
a package's own runtime floor is ours to check — before the install, because
350MB that can never run is worse than nothing (`openclaw_runtime_node_too_old`).
**The Node that INSTALLS is not the Node that RUNS**: the installed bin is
`#!/usr/bin/env node`, so the runtime is decided by PATH at exec time, and
installing under one while supervising under another is a transport that
installs cleanly and exits on every call. And **exit 0 is not a decision** —
the first live run wrote and STARTED a unit because the plan CLI exited 0,
while the plan it printed said `refused`; the installer now reads
`runtimeInstall.action` and `provision.status`, and a plan with no usable Node
carries no unit at all, so there is nothing to start.

**A `warn` from their audit is BLOCKING, so a directory nobody looked at can
make a box provision once and refuse forever.** The OpenClaw profile state dir
came out `755` on a real box — a `mkdir` inheriting a systemd service's 022
umask, or OpenClaw creating it before we get there — and their
`fs.state_dir.perms_readable` check reports that at `warn`, which
`blockingAuditFindings` treats as blocking. First run: provisioned. Every run
after it: `openclaw_security_audit_not_clean`. Nothing about that reads as a
permissions problem. Now chmod 0700 on EVERY run rather than at creation,
because the directory that broke it already existed — and it holds the gateway
token and the conversation state, so 755 was wrong on its own terms too.

**A systemd unit with no `User=` runs as root, and `--profile` resolves against
`$HOME`.** Both halves matter for the co-located transport: root is absurd
authority for a process whose whole job is to be a radio, and a root-run
OpenClaw keeps its state in `/root/.openclaw-<p>` while the gateway reads and
writes its own — one config, two instances, no error anywhere. The shared
renderer took a `user` field for this; omitting it renders byte-identically,
so the gateway's own unit is unchanged and no existing box reports drift.

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

**Outbound anti-ban: we inherit the chunking and the per-channel throttling,
and we inherit NOTHING from their agent loop — which turns out to be almost
nothing.** Verified 2026-08-09 against the pinned v2026.6.10 bundle, no live
traffic. `message.action` and OpenClaw's own agent reply converge on the SAME
function three frames down, so anything below that line is ours for free:

```
OURS    message.action (WS RPC) ─▶ sendHandlers["message.action"]  send-BMn-S3XR.js
                                   dispatchChannelMessageAction
                                   plugin.actions.handleAction     e.g. telegram
                                                                   action-runtime-*.js
THEIRS  inbound ─▶ reply dispatcher ─▶ deliver ──┐
                   (humanDelay, typing, sendChain)│
                                                  ▼
                          BOTH ─▶ sendDurableMessageBatch
                                  deliverOutboundPayloadsInternal  deliver-BPqL55uX.js
                                    ├ sendTextChunks   CHUNKING, per-plugin limit ✓
                                    └ plugin.sendText  the plugin's own API client ✓
        ─────────── everything ABOVE the join is theirs alone ───────────
                 humanDelay ✗   typing ✗   inbound debounce ✗
```

Four things this settles, each of which a code reading gets wrong by default:

- **Chunking is INHERITED** and is not ours to do. Split happens in
  `deliverOutboundPayloadsInternal`'s `sendTextChunks` using the plugin's own
  `chunker`/`textChunkLimit`/`resolveEffectiveTextChunkLimit` (Telegram 4000
  capped to 4096, SMS 1500, IRC 350, ClickClack none at all). Never pre-split
  on our side: N pre-split messages are N `message.action` calls, which is
  strictly worse than one call they chunk.
- **Neither path paces the chunks.** `for (const unit of units) results.push(
  await sendHandler.sendText(...))` has no delay — for their agent too. So
  there is no gap here to close, and no version of "their pipeline paces and
  ours doesn't". Spacing, where it exists, is the PLUGIN's transport:
  Telegram's `getOrCreateAccountThrottler` (`send-B-QsV5Qz.js`) installs
  grammY's `apiThrottler` on `bot.api.config.use` — 1 msg/s per chat, 20/min
  per group, 30/s per token — plus a `GroupFairQueue` per forum topic; Discord,
  Matrix and Synology ship their own send queues. **Signal, iMessage, IRC, SMS
  and ClickClack have none, and no 429/retry-after handling either.** Adopting
  OpenClaw buys real pacing per channel, not uniform pacing — do not describe
  it as a blanket protection. And for the channels this lane actually routes
  today (feishu/line/qqbot/zalo/msteams) the plugin is third-party npm that
  provisioning installs, so whether it paces **cannot be determined from
  OpenClaw's own source** — it is a property of each plugin, not of the
  transport. Say that, rather than generalising from Telegram.
- **Presence is structurally unreachable through this transport.**
  `CHANNEL_MESSAGE_ACTION_NAMES` has no typing action at all (`read` and
  `set-presence` exist; typing does not). Typing lives on the plugin's
  `heartbeat.sendTyping`, driven by `createTypingCallbacks` from their inbound
  dispatch and heartbeat runner under `session.typingMode` — agent-loop only.
  Nothing at our seam can send it; it needs an upstream action, not a fix here.
- **`agents.defaults.humanDelay`** (a random 800–2500ms between reply BLOCKS,
  `reply-dispatcher.ts`) is their only above-the-plugin pacing, it is
  **`mode: "off"` by default**, and it spaces blocks — we emit one final text
  per turn, so there would be nothing for it to space. Not a gap.

**A server-supplied `retryAfterMs` is a FLOOR, never a ceiling.** The one real
defect found, and the only pacing lever this seam actually owns:
`OpenClawGatewayClient.delayBeforeRetry` computed
`Math.min(retryAfterMs ?? backoff, 2000)` — it read OpenClaw's own structured
backoff and clamped it DOWNWARD. Told "wait 30s" it waited 2s and retried,
twice. That is the exact shape of the incident their
`extensions/telegram/src/sendchataction-401-backoff.ts` exists for, reintroduced
at our own seam while adopting them to avoid it. Now `waitBeforeRetry`: wait
`max(asked, our own backoff)`, and when the ask exceeds one in-band wait
(`RETRY_HONOUR_BUDGET_MS`) **stop retrying** and return the transient outcome
carrying `retryAfterMs`, so the cloud owns the wait. Refusing is strictly less
traffic than the clamp was, so it can never push `channel.outbound` past its own
120s timeout. Guarded behaviourally AND by a source assertion banning
`Math.min(… retryAfterMs …)` — the defect is a one-token change that
type-checks and is silent in production.

Two facts that make our retry safe and that a reader will otherwise re-derive.
OpenClaw caches `message.action` FAILURES under the idempotency key for
**5 minutes** (`DEDUPE_TTL_MS`, `resolveGatewayInflightRequest`), so an in-band
retry replays the cached error and never re-hits the platform — the retry only
ever helps a transport-level failure. And **every error the send path throws
comes back as `UNAVAILABLE` with no `retryable` and no `retryAfterMs`**
(`createGatewayInflightUnavailableFailure`), so "Feishu account not configured",
"chat not found" and "bot was kicked" are indistinguishable from a busy adapter
at the wire. Do not add a keyword matcher for them; the structural fact is
`channels list --all --json` -> `installed`, already surfaced as
`channel_plugins[].installed`.

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

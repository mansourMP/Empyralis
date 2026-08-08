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

But **do not import a single-operator project's security assumptions into a
multi-tenant one.** OpenClaw's own docs: *"not a hostile multi-tenant
security boundary… one trusted operator boundary per gateway."* Their CVE
record (sandbox escape; a client-asserted `senderIsOwner` flag trusted
because it arrived over loopback) is what happens when that boundary is
ignored. Read their source, port the design, never vendor their core.

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

# Empyralis — standing decisions

**Read this file first — it is your persistent memory for this project.**
When you learn something durable (a settled decision, a recurring failure
worth warning the next agent about, an architecture call that will still be
true next week), add it here yourself, in the existing terse style, without
waiting to be asked.

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

**Branches whose work gets redone on main.** Nine branches were found with
real commits, all superseded by the same fixes re-implemented directly on
main days later. If a branch exists, merge it or delete it — leaving it means
someone rebuilds it.

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

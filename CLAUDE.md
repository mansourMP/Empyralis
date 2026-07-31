# Empyralis — standing decisions

Linear is the system of record. Issues, plans, and status live there, not here.
This file holds only the durable decisions an agent needs *before* it starts
working — the things that don't change when a ticket closes.

**Design/audit/gap/research documents are not kept.** They were snapshots of a
moment; they went stale within a week and agents cited them as present truth.
Deleted 2026-07-31. Findings become Linear issues; decisions become lines here.

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

## Testing the UI

**Seed your own data. Never ask for the founder's account, and never copy secrets.**
An agent testing a UI at scale should sign up a fresh local account and create
what it needs — 20 agents, 40 projects, a task with 50 comments — then look at
the real screen. It takes minutes, needs no credentials, and exercises the
actual render path. A static reproduction proves the mock renders, not the app.

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

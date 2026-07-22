# Tasks → Agents: how the industry does delegation-by-assignment, and how Empyralis should build it

**Question this doc answers:** the founder's vision is that inside Empyralis's existing
**Projects**, the owner writes a task ("do this today"), @-mentions/assigns a specific agent,
and that agent picks it up and works it — like delegating an issue to a teammate in Linear.
This doc researches how the industry's best current products do exactly this (from live
official docs, not training memory), distills the convergent pattern, and maps it onto what
Empyralis already has vs. what's missing.

**Research date:** 2026-07-23. All sources below were fetched live this session.

---

## 1. Per-product patterns (official sources)

### 1.1 Linear Agents — the founder's north star

Linear shipped a first-class **Agent API** (their term: AIP, "Agent Interaction Protocol")
built around one core object: the **Agent Session**.

- **Trigger.** A session is created automatically in exactly two ways: a user **@-mentions**
  the agent, or **assigns/delegates an issue** to it. Both fire an `AgentSessionEvent` webhook
  to the agent's registered application with the issue + context.
  [Getting Started – Linear Developers](https://linear.app/developers/agents)
- **Ownership stays human.** *"Assigning an issue to an agent delegates the issue to that
  agent while the human teammate remains the primary assignee and owner."* The agent works
  the issue; the human is still accountable for it. [AI Agents – Linear Docs](https://linear.app/docs/agents-in-linear)
- **Session lifecycle — 6 states, tracked automatically.** `pending → active → (error |
  awaitingInput) → complete`, plus `stale` for an unresponsive agent. *"You don't need to
  manage agent session state manually. Linear tracks session lifecycle automatically based on
  the last emitted activity."* [Developing the Agent Interaction](https://linear.app/developers/agent-interaction)
- **5 activity types are the entire vocabulary an agent speaks back in:** `thought`,
  `elicitation` (asks the user something → flips session to `awaitingInput`), `action` (a tool
  call, optionally with a result), `response` (final output), `error`. The agent must emit a
  `thought` **within 10 seconds** of session creation just to acknowledge it heard the
  delegation — or Linear may mark it unresponsive. [Developing the Agent Interaction](https://linear.app/developers/agent-interaction)
- **The agent is a real workspace member,** not a bot bolted on: it appears in @-mention and
  filter menus with its app's name/icon, can be @-mentioned in any comment, assigned issues,
  reply to comments, and collaborate on docs/projects. Registration is a standard OAuth2
  Application with `app:mentionable` / `app:assignable` scopes. [Getting Started – Linear Developers](https://linear.app/developers/agents)
- **Progress visibility for the human** is entirely issue-native: the human sees the agent's
  activity stream on the issue itself, the agent's own user profile shows its issue history,
  "My issues" still lists delegated issues, and you can filter/segment by delegate.
  [AI Agents – Linear Docs](https://linear.app/docs/agents-in-linear)
- **Automations** (Business/Enterprise): a workspace can trigger an agent session
  automatically when an issue enters triage, not just on explicit @-mention/assign.
  [Introducing Linear Agent](https://linear.app/changelog/2026-03-24-introducing-linear-agent)

### 1.2 GitHub Copilot coding agent — assign-an-issue-get-a-PR

- **Assignment surface:** set **Copilot** as the Assignee on an Issue (also: `@copilot` in a
  PR comment, the agents panel, or third-party triggers like Azure Boards/Slack). An optional
  prompt field lets you add instructions beyond the issue body; you can also pick starting
  branch, target repo, model. Batch-assign multiple issues at once.
  [Assigning and completing issues with coding agent](https://github.blog/ai-and-ml/github-copilot/assigning-and-completing-issues-with-coding-agent-in-github-copilot/),
  [Using Copilot cloud agent on GitHub](https://docs.github.com/en/copilot/how-tos/use-copilot-agents/cloud-agent/use-cloud-agent-on-github)
- **Snapshot context, not live context.** Copilot receives the issue title, description, and
  comments **at assignment time** — it will *not* react to further comments added after it
  starts. This is a deliberate scoping choice worth noting for our own design.
  [Using Copilot cloud agent on GitHub](https://docs.github.com/en/copilot/how-tos/use-copilot-agents/cloud-agent/use-cloud-agent-on-github)
- **Background execution:** an ephemeral GitHub Actions environment; every step is a real
  commit, visible in logs. Hard **59-minute session cap**.
  [About GitHub Copilot cloud agent](https://docs.github.com/copilot/concepts/agents/coding-agent/about-coding-agent)
- **Done = PR + review request,** not "done" on the issue itself: *"start working on the task,
  raise a pull request, then request a review from you when it's finished."* Thumbs up/down
  feedback buttons on the PR. [About GitHub Copilot cloud agent](https://docs.github.com/copilot/concepts/agents/coding-agent/about-coding-agent)

### 1.3 Devin (Cognition) — plan-first, multi-trigger, escalate-to-full-session

Three separate ways to invoke Devin from the same issue, each a deliberately different
commitment level: [Devin Integration – Linear](https://linear.app/integrations/devin), [Linear – Devin Docs](https://docs.devin.ai/integrations/linear)

- **Assign** the issue to Devin directly → runs a configured *default playbook*.
- **Label** it with a specific playbook macro (`!plan`, `!implement`, `!triage`, `!review`) →
  runs that named playbook.
- **@-mention** Devin in a comment with free-text instructions → runs with no playbook, just
  the instruction.
- **Always analysis-first:** within minutes Devin comments a code summary, an implementation
  **plan**, edge cases/clarifying questions, and a 🔴/🟠/🟢 confidence indicator — *before*
  writing any code. The human reviews/edits the plan in Linear comments, then either continues
  there or clicks through to escalate into a full Devin session / "Devin Space" for deeper
  work. Session + any resulting PRs are auto-linked back to the Linear issue.

### 1.4 Asana AI Studio / AI Teammates — the non-dev-tool pattern, with an explicit approval gate

- **AI Teammates are assignable like any other team member** and act only within the
  permission boundary a human already set — *"AI Teammates are designed to follow the exact
  same rules as your colleagues... they only see, search, or summarize the tasks and projects
  they've been explicitly invited to."* They must be explicitly added as members/collaborators
  to see private objects. [AI Teammate Access Control – Asana Help Center](https://help.asana.com/s/article/understanding-access-control-for-ai-teammates?language=en_US)
- **Checkpoints, not silent autonomy:** *"AI Teammates keep your team in the loop with
  checkpoints to show their work,"* and *"every action is auditable and reversible."*
  [Asana AI Teammates](https://asana.com/product/ai/ai-teammates)
- **AI Studio Rules vs. AI Teammates are a deliberate split:** Rules are for structured,
  deterministic trigger→action automation; Teammates are invoked *from* a rule (or directly)
  when a step needs interpretation/reasoning rather than a fixed action.
  [How to use pre-built AI rules in AI Studio](https://help.asana.com/s/article/ai-studio-pre-built-ai-rules?language=en_US)

### 1.5 ClickUp Super Agents / monday.com AI Agents — the "assignee OR mention" convergence, again

- **ClickUp:** *"you can @mention them, assign them tasks, and message them directly, just
  like a human colleague."* Setting a Super Agent as a task's assignee is itself the trigger.
  [ClickUp Super Agents](https://clickup.com/brain/agents)
- **monday.com:** every agent ships with two triggers that always exist regardless of what
  else you configure — **"When Assigned"** and **"When Mentioned."** This is the cleanest,
  most literal confirmation of the pattern the founder wants: assignment and @-mention are
  *the two default entry points*, full stop, on a mainstream PM tool.
  [AI Agents on monday.com – Support](https://support.monday.com/hc/en-us/articles/33347027353746-AI-Agents-on-monday-com)

### 1.6 Cursor background/cloud agents & Claude Code on the web — dispatch-and-review, our closest sibling architecture

- **Cursor:** `Ctrl+E` (or Slack/web) dispatches a prompt; the agent clones the repo into a
  fresh cloud VM, works on its own branch, and pushes a PR when done while you keep working
  locally — renamed **Cloud Agent** in 2026, now with an optional full desktop/browser
  environment ("Computer Use"). [Cursor Background Agents Guide](https://aitechfy.com/blog/cursor-background-agents/), official docs at `docs.cursor.com` (redirects to `cursor.com/docs`).
- **Claude Code on the web** (`claude.ai/code`) is the pattern closest to what Empyralis would
  need to build *inside its own product* rather than atop GitHub:
  - Each cloud session is an isolated, Anthropic-managed VM with your repo cloned; **sessions
    persist across browser closes** and are monitorable from the mobile app.
  - `claude --cloud "<task>"` dispatches from a terminal; `/tasks` lists/monitors running
    sessions; `--teleport` pulls a cloud session back into a local terminal, one-way.
  - **Progress reporting is a first-class, linkable object:** every session has its own
    transcript URL, and — as of v2.1.179 — every commit Claude makes in a web session carries
    a `Claude-Session: <url>` git trailer, and PR bodies get the session URL on their own
    line, specifically so "a reviewer can open the run that produced them."
  - **Review = a diff view with inline comments** (`+42 -18` indicator → open diff → comment
    on a line → sent to Claude on your next message), separate from and prior to PR creation.
  - **Auto-fix** is an explicit, separately-toggled escalation: once turned on for a PR, Claude
    watches GitHub webhooks for CI failures/review comments and either fixes confidently,
    *asks the human* if the request is ambiguous, or no-ops on duplicates — never silently
    guesses on anything "architecturally significant."
  [Use Claude Code on the web – Claude Code Docs](https://code.claude.com/docs/en/claude-code-on-the-web)

---

## 2. The convergent design (what all 7 products independently agree on)

| Dimension | The pattern every product converges on |
|---|---|
| **Assignment surface** | Two entry points, always both present: **assign** (sets a real "owner" field to the agent) and **@-mention** (fires it inline, doesn't change ownership). monday.com literalizes this as two always-on triggers; Linear, GitHub, Devin, ClickUp all implement the same duality under different names. |
| **Human stays the accountable owner** | Linear is explicit: the human "remains the primary assignee and owner" even after delegating. Asana frames it as permission-scoped teammates, not autonomous actors. The agent is a *worker*, the human is still the *owner of record*. |
| **A session/run object, not a chat message** | Every product spins up a distinct, addressable unit of work (Agent Session, Actions run, Devin session, Cloud VM session) the moment it's triggered — with its own id, its own lifecycle, its own progress stream — separate from the task/issue text itself. |
| **A small, closed vocabulary of lifecycle states** | Linear: 6 states (`pending/active/error/awaitingInput/complete/stale`). GitHub: implicit states via commits→PR→review. Claude Code: session status + diff indicator. The state machine is always small and always exposes an explicit "waiting on you" state distinct from "still working." |
| **Progress reported back ON the task, live** | Not a side channel — activity posts as comments/timeline entries *on the same issue/task* the human is already looking at. Linear's activity stream, Devin's plan-comment, GitHub's commit log, Claude Code's session-linked commits/PR body all do this. |
| **A plan/scope step before execution, for anything non-trivial** | Devin *always* posts a plan + confidence before coding. Claude Code's "ultraplan" and plan-mode pattern separate "decide what to do" from "go do it." Elicitation (Linear) is the same idea reactively — ask before assuming. |
| **"Done" is a proposal, not a fact — review is a distinct gate** | GitHub: PR + requested review. Devin: draft PR from a session. Claude Code: diff view + inline comments before PR. Asana: checkpoints, auditable/reversible actions. None of the 7 let an agent silently close out its own delegated work with no human-visible artifact to react to. |
| **Escalation is explicit and asymmetric** | Ambiguous → ask (Claude Code auto-fix, Devin's plan step, Linear's `elicitation`/`awaitingInput`). Confident + safe → just do it and report. No product lets an agent guess silently on something consequential; all of them bias toward "ask" when unsure. |
| **Snapshot context is normal and named as a limitation** | GitHub explicitly documents that Copilot won't see comments added after assignment. This isn't hidden — it's a stated contract so users know to re-assign/re-prompt rather than assume live awareness. |

---

## 3. Empyralis today: what already exists vs. what's missing

### 3.1 What "Projects" are today — a container for agents, not a task tracker

- `frontend/app/(account)/w/[workspaceId]/projects/page.tsx` — the Projects list. Each row is
  an agent-count/cost/token/status rollup for a project; "New project" (`NewProjectDialog`,
  same file, ~L346-418) takes only `name` + `description`. **No task field exists anywhere in
  this flow.**
- `frontend/app/(account)/w/[workspaceId]/projects/[projectId]/page.tsx` — project detail is
  literally `AgentsList` filtered to `agent.project_id === projectId` (L120) plus a cost
  rollup. A project's entire content model is "which agents live here."
- `FleetProject` type (`frontend/lib/workspace/fleet/fleet-data.ts:54-68`): `id, name,
  description, agent_count, status, is_default, icon, tint, metadata, created_at`. No
  `tasks`, no backlog, no work-item concept at all.
- **Conclusion:** Empyralis's "Projects" today are the Linear-equivalent of a *Team* (a
  grouping boundary for agents + their spend), not the Linear-equivalent of a *Project full of
  issues*. The founder's ask requires adding a genuinely new object — a task/work-item — that
  doesn't exist in the data model yet.

### 3.2 What already exists that a task object can ride on

**A model-authored plan/task-list tool is already shipped — but scoped to one turn, not durable.**
This is real, working infrastructure, close in shape to what's needed, just wired to the wrong
lifecycle:

- `update_plan` is a real, always-on tool (`server_modules/tool_registry_service.py:36`;
  `_ALWAYS_MANDATORY_TOOL_NAMES` in `server_modules/sage_agent_runtime_service.py:2254`). The
  model calls it with a full task list (`{id, title, status: pending|active|done|skipped}`)
  and the handler in `server_modules/direct_chat_generation_service.py:1837-1907`
  full-replaces the turn's plan, preserving task ids by title-match so the frontend can key
  off `id` across repeated calls (comment at L1842-1845).
- It's a genuine escape hatch from the 5-tool-iteration cap: closing out the last open task
  grants the model a bypass round to wrap up (L1868-1874), and every other round with open
  tasks bypasses the cap check too (referenced at L1420).
- It's traced as a first-class event: `agent_trace_service.emit_plan_updated`
  (`server_modules/agent_trace_service.py:505-522`) emits `plan.updated` with the full task
  array, `persisted=True`.
- The frontend already renders it: `frontend/lib/workspace/fleet/tabs/WorkTab.tsx` has a full
  `PlanTask`/`latestPlanTasks` contract (L312-345) and a `PlanSection` component (L651-688)
  with per-status marks (done/active/skipped/pending) — visually, this is already a Linear-
  issue-shaped checklist UI.
- **The gap:** `current_plan` is initialized to `[]` at the top of every turn
  (`direct_chat_generation_service.py:1203`) and the WorkTab only ever shows the *latest
  plan.updated event on one conversation's trace* (`WorkTab.tsx:793-826`, keyed by
  `selected` thread). **The plan does not survive past the turn/conversation it was created
  in, and it is not addressable per-Project — it's per-conversation.** This is exactly
  `docs/design/backbone-plan.md` Tier A #1 / Tier C #5's open item: *"Plans-as-repo-files...
  Persist the agent's plan/TODO as a durable artifact — survives restarts."* That's precisely
  the seam a real task object needs to sit on.

**Scheduled wake-ups exist and are genuinely strong — this is the "how does the agent actually
start working without a human in the loop" mechanism.**
`server_modules/bounded_scheduler_service.py` (1314 lines):
- `propose_self_wakeup()` (L635-715) and `maybe_schedule_event_trigger()` (L565-632) — an
  agent (or the context engine) can schedule its own future check-in, rate-limited
  (`DEFAULT_MAX_SELF_PROPOSED_PER_HOUR=2`, L16) and quiet-hours gated.
- `claim_due_wake_requests()`/`finalize_wake_requests()` (L718-899) — batch claim/resolve.
- A **native, first-party approval gate for privileged wakeups**
  (`propose_self_wakeup`, condition at L660): if the wakeup needs privileged runtime and the
  policy requires owner approval and none was granted, the request is persisted as `denied` —
  it simply never runs. Per `docs/design/backbone-empyralis.md` §6, this has no OpenClaw
  equivalent — a real Empyralis lead worth keeping when task-assignment adds a new wakeup
  trigger type.
- Every wake-up re-enters the *same* runtime as an ordinary chat turn (via
  `heartbeat_run_callback` → `_run_sage_action_loop_v3`) — there's no separate execution
  engine to build; "assign a task, agent wakes and works it" is a new **trigger reason** into
  a scheduler that already exists and already knows how to wake an agent into a real turn.

**No @-mention-to-agent selection mechanism exists anywhere today — this must be net-new.**
- `server_modules/agent_channel_router.py`'s `_resolve_agent_for_inbound` (L66-106) resolves
  **one channel binding → one agent**, via `agent_channel_bindings` (matches `channel_type` +
  `endpoint_key`). There is no concept of "which of several agents does this @-mention refer
  to" anywhere in this file.
- `mentions_any()` (`server_modules/direct_chat_availability_service.py:61-62`) is unrelated —
  it's keyword-in-text detection used for write-request classification (e.g., "does this
  message mention Telegram"), not agent-identity resolution.
- Per the memory ruling already on file (channel behavior rulings): groups today are
  "agent see-and-decide," not mention-gated at all — the opposite of the precise,
  routes-to-exactly-one-agent semantics Linear's `@mention` has. **A real `@agent-name` parser
  that resolves to a specific `agent_install_id` inside a Project does not exist and is 100%
  new work,** even though the UI convention (typing `@`) is familiar from chat.

**Sub-agent/delegation messaging exists but is a confirmed dead letter — relevant if "assign to
an agent" ever needs agent-to-agent handoff, not just owner-to-agent.**
`fleet_message_agent` (`server_modules/fleet_tools.py:1564`) writes into the target agent's
`install_metadata.fleet_inbox` (fire-and-forget, `fleet_tools.py:1596-1605`), but per
`docs/design/backbone-empyralis.md` §5, `fleet_inbox` is **written but never read anywhere in
the codebase** — confirmed by a full-repo grep. This is not itself the owner→agent assignment
path, but if the eventual design lets one agent hand a task to another, this is the seam that
would need to actually be read, not just the plumbing to build fresh.

**A connector already exists for the *reverse* direction (Empyralis agent → external Linear),
not the *forward* one the founder wants.**
`server_modules/connector_manifests.py:250-264` — a `linear` connector manifest with
`list_teams/list_issues/get_issue/create_issue/update_issue/list_projects/add_comment` actions
already exists (no triggers). This lets an Empyralis agent *act on* an external Linear
workspace as a tool call. It's the wrong direction for this feature (the founder wants
Empyralis's own internal Projects to work like Linear, not for our agents to operate someone's
real Linear board) — but it does prove the "issue-shaped CRUD" tool contract is a pattern this
codebase already knows how to build, twice (Notion too, `connector_manifests.py:232-249`).

**"MCP layer" question, answered directly:** our agent tool surface already supports two kinds
of tools side by side — native connector actions (like the Linear one above) and real,
workspace-connected MCP server tools, deferred-loaded and namespaced `mcp__<server_id>__<tool>`
(`server_modules/tool_registry_service.py:314-328`, gated by `EMPYRALIS_MCP_TOOLS_ENABLED`).
**New task tools (`create_task`, `assign_task`, `list_my_tasks`, `update_task_status`,
`comment_on_task`) belong as first-party tools in this same registry, not as an MCP server** —
they need to read/write our own Postgres task table and fire our own scheduler/trace events,
which is exactly the shape of every other native tool already in this file, not an external
MCP integration. (Whether to *also* expose task tools over MCP so a user's own Claude
Code/Cursor session could manage Empyralis tasks is a legitimate v2 idea, but it's additive,
not a prerequisite.)

### 3.3 Summary table — build vs. reuse

| Piece the founder's UX needs | Empyralis today | Verdict |
|---|---|---|
| Task object (title, description, status, assignee) living inside a Project | Does not exist — Projects only contain agents | **Build** (new DB table + API) |
| `@agent` mention parser inside a task/comment composer | Does not exist anywhere (channels are 1-binding-1-agent; groups are unmention-gated) | **Build** |
| "Assign to agent" as a persisted, addressable field | Does not exist | **Build** |
| A session/run object the assignment kicks off, with lifecycle states | Adjacent: `agent_trace_service` traces + `plan.updated` events already have almost this shape, just scoped to a chat turn | **Extend**, not build from scratch |
| A durable plan/checklist UI on the task | `update_plan` tool + `PlanSection` (`WorkTab.tsx`) — visually already there, just per-conversation not per-task | **Re-scope existing code**, biggest reuse win |
| The mechanism that actually wakes the agent to start working | `bounded_scheduler_service.py` — strong, real, approval-gated | **Reuse directly**, add "task assigned" as a new wakeup trigger reason |
| Progress reported back onto the task, live | `agent_trace_service` event stream already flows to a UI (WorkTab) | **Reuse the pipe, change the destination** (task page, not conversation) |
| Review/close-out gate before a task reads "done" | Nothing — no review/approval pattern currently gates a plan reaching 100% done | **Build** (smallest of the net-new pieces if scoped to "owner marks done/reopens") |
| Tool contract for the agent to manipulate its own assigned tasks | `tool_registry_service.py` native-tool pattern, proven twice already (Notion, Linear connectors) | **Build 4-5 new native tools**, same pattern as existing connectors |

---

## 4. Recommendation for Empyralis

### 4.1 Where tasks live
Add a first-class `task` object scoped to a Project (not to an agent, not to a conversation):
`id, project_id, title, description, status (open/in_progress/blocked/done), assignee_agent_id
(nullable — unassigned = backlog), created_by, due_at?, created_at, updated_at`. This is the
Linear-issue-in-a-Linear-project shape, deliberately minimal for v1 (no cycles/priorities/
labels yet — those are v2 polish, not core to "assign a task to an agent").

### 4.2 Assignment surface (mirrors the convergent pattern in §2)
- **Explicit assignee field** on the task (dropdown of the Project's agents) — this is
  "assign," the durable-ownership path.
- **`@agent-name` inline in the task's description or a comment** — this is "mention," the
  inline-nudge path, distinct from assignment. Needs the net-new parser called out in §3.2;
  resolve against agents in that Project only (same scoping Linear/monday use — mention
  autocomplete is workspace-wide, but *routing* only matters when it resolves to a real agent
  id).
- Both should fire the same downstream event so there's one code path, not two.

### 4.3 Session lifecycle — reuse `agent_trace_service`, don't invent a second one
When a task is assigned/mentioned, schedule a wakeup via `bounded_scheduler_service`
(new trigger reason: `task_assigned`) that starts a real `agent_turn()` with the task's
title+description as the seed prompt, tagged with the task's id in trace metadata. Give the
task a small state machine mirroring Linear's, trimmed to what we need:
`pending → active → (blocked | awaiting_input) → done`. `blocked`/`awaiting_input` should map
directly onto the existing `approval.requested`/`elicitation`-shaped moments already flowing
through the trace stream (WorkTab already renders `approval.requested`/`approval.resolved`,
`WorkTab.tsx:427-444` — reuse, don't reinvent).

### 4.4 The plan ties in directly — this is the single biggest "already 80% built" win
Re-scope `update_plan`'s `current_plan` from per-turn to per-task: when a turn is working an
assigned task, seed `current_plan` from the task's persisted plan at turn start and persist it
back (not just trace it) when the turn ends, so the checklist survives across the wakeup →
turn → wakeup cycle a real task will need. The `PlanSection` UI in `WorkTab.tsx` (L651-688) is
already the right visual — it just needs to render on a **task page**, keyed by `task_id`, not
only inside a conversation's trace.

### 4.5 UI needed (net-new frontend)
- A **Tasks tab inside the Project detail page** (`projects/[projectId]/page.tsx`), sibling to
  the existing agents list — a Linear-issue-list-style view: title, assignee avatar/name,
  status pill, updated-at.
- A **task detail panel**: description, assignee picker, the re-scoped `PlanSection`, and an
  activity/comment feed (the existing `ActivityTimeline` component pattern from `WorkTab.tsx`
  already does 90% of this rendering job).
- An **`@mention` composer control** wherever task text is authored (new task dialog, task
  description, comments) that autocompletes against the Project's agents.
- A **review/done gate**: task reaching `done` should be a one-click owner action ("Mark
  done" / "Reopen"), not something the agent can silently flip itself all the way to closed —
  matches every product in §2 treating "done" as a proposal.

### 4.6 Backend seams — honest build order
1. **Task table + CRUD API** (new — smallest, unblocks everything else).
2. **Persist `update_plan`'s plan keyed by `task_id`** instead of only tracing it turn-scoped
   (extend existing code — `direct_chat_generation_service.py:1203-1907`,
   `agent_trace_service.py:505-522`).
3. **`task_assigned` wakeup trigger** in `bounded_scheduler_service.py` (new trigger reason,
   same claim/finalize machinery already there).
4. **`@agent` mention parser + resolver**, scoped to a Project's agents (net-new — no existing
   code to extend, per §3.2).
5. **4-5 new native tools** in `tool_registry_service.py`'s pattern (`list_my_tasks`,
   `update_task_status`, `comment_on_task`, `create_task` for agent-initiated follow-ups) so
   an agent can read/update its own assigned work mid-turn, not just receive a seed prompt.
6. **Review/done gate** — smallest net-new piece, an owner-only status transition.
7. *(Not required for v1, don't build it first)* Real sub-agent task handoff
   (`fleet_message_agent`/`fleet_inbox` becoming live) — only matters once agents can assign
   tasks to *each other*, which is explicitly out of scope for "owner assigns to an agent."

### 4.7 What NOT to build first
Don't build a second execution engine. `runs_execution.py`'s visual workflow DAG
(`docs/design/backbone-empyralis.md` §6) is a different product (human-authored deterministic
automation) — task assignment should ride the *chat/turn* runtime via the scheduler, exactly
like Linear/Devin/Claude Code all ride their existing agent runtime rather than inventing a
parallel one.

---

## 5. Top 5 pitfalls (from what the research surfaced)

1. **Letting the agent close its own task with no human-visible artifact.** Every single
   product in §2 gates "done" behind something a human reacts to (PR review, diff comments,
   checkpoints, elicitation). Skipping this is the fastest way to make the feature feel unsafe
   and get disabled after the first bad surprise.
2. **Building assignment and @-mention as two different code paths.** They should differ only
   in whether the `assignee_agent_id` field changes — Linear's own docs stress the human stays
   "primary assignee and owner" even when delegated, so the ownership semantics must stay
   identical regardless of trigger.
3. **Snapshotting task context at wake-time and pretending it's live** (or the opposite —
   claiming live-awareness you don't have). GitHub is explicit that Copilot won't see comments
   added after assignment; if Empyralis's agent also can't react to a task edited mid-run, say
   so in the UI rather than let the owner assume otherwise.
4. **No visible "waiting on you" state.** Linear's `awaitingInput`, Claude Code's ambiguous-
   request pause, Devin's "Start session?" confirm-step — all exist because silent stalls
   (agent blocked on a question, nobody notices) are the single most common failure mode of
   background agents. The task status machine (§4.3) must have a state that's impossible to
   miss in the Projects UI, not just buried in trace events.
5. **Treating "continuous work" as solved because `update_plan` exists.** It's real, but it's
   turn-scoped (`current_plan = []` every turn, `direct_chat_generation_service.py:1203`) and
   the 5-tool-iteration cap is still the hard ceiling underneath it
   (`backbone-empyralis.md` §6). A task assigned today and picked up via a scheduled wakeup
   tomorrow needs its plan to survive that gap — that's net-new persistence work (§4.4), not
   already covered by the tool existing.

---

## Sources (fetched live, 2026-07-23)

- Linear: [Getting Started – Developers](https://linear.app/developers/agents) · [AI Agents – Docs](https://linear.app/docs/agents-in-linear) · [Developing the Agent Interaction](https://linear.app/developers/agent-interaction) · [Introducing Linear Agent – Changelog](https://linear.app/changelog/2026-03-24-introducing-linear-agent) · [Devin Integration](https://linear.app/integrations/devin)
- GitHub: [About Copilot cloud agent](https://docs.github.com/copilot/concepts/agents/coding-agent/about-coding-agent) · [Using Copilot cloud agent](https://docs.github.com/en/copilot/how-tos/use-copilot-agents/cloud-agent/use-cloud-agent-on-github) · [Assigning and completing issues (blog)](https://github.blog/ai-and-ml/github-copilot/assigning-and-completing-issues-with-coding-agent-in-github-copilot/)
- Devin: [Linear – Devin Docs](https://docs.devin.ai/integrations/linear) · [Devin Integration – Linear](https://linear.app/integrations/devin)
- Asana: [AI Teammate Access Control](https://help.asana.com/s/article/understanding-access-control-for-ai-teammates?language=en_US) · [AI Teammates product page](https://asana.com/product/ai/ai-teammates) · [AI Studio pre-built rules](https://help.asana.com/s/article/ai-studio-pre-built-ai-rules?language=en_US)
- ClickUp: [Super Agents](https://clickup.com/brain/agents)
- monday.com: [AI Agents on monday.com – Support](https://support.monday.com/hc/en-us/articles/33347027353746-AI-Agents-on-monday-com)
- Cursor: [Background Agents Guide (2026)](https://aitechfy.com/blog/cursor-background-agents/) (official docs at `docs.cursor.com` → redirects to `cursor.com/docs`)
- Claude Code: [Use Claude Code on the web](https://code.claude.com/docs/en/claude-code-on-the-web)

**Repo evidence** (this session, file:line-verified): `frontend/app/(account)/w/[workspaceId]/projects/page.tsx`, `frontend/app/(account)/w/[workspaceId]/projects/[projectId]/page.tsx`, `frontend/lib/workspace/fleet/fleet-data.ts:54-68`, `frontend/lib/workspace/fleet/tabs/WorkTab.tsx`, `server_modules/tool_registry_service.py:36,314-328`, `server_modules/sage_agent_runtime_service.py:2254`, `server_modules/direct_chat_generation_service.py:1203,1837-1907`, `server_modules/agent_trace_service.py:505-522`, `server_modules/bounded_scheduler_service.py`, `server_modules/agent_channel_router.py:66-106`, `server_modules/direct_chat_availability_service.py:61-62`, `server_modules/fleet_tools.py:1564-1605`, `server_modules/connector_manifests.py:232-264`, `docs/design/backbone-plan.md`, `docs/design/backbone-empyralis.md` §5/§6.

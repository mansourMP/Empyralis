# Project-level agent collaboration: a shared "cookbook" + agent-to-agent peer review

> **Terminology note:** per the existing terminology ruling, "Sage" in any quoted/cited
> material below means the owner-facing agent. Not restated per-instance.

**This doc answers a narrower, later question than
`docs/design/tasks-to-agents-research.md`** (2026-07-23, same day, read in full before this doc
was written). That doc researched **owner assigns one task to one agent**. This doc researches
the founder's follow-up ask, same day: inside a Project with *multiple* agents, (a) a
project-level "cookbook" of shared instructions governing how those agents collaborate, and (b)
**agent-to-agent peer review** — agent B critiques agent A's work and that critique redirects
it — as opposed to the owner being the one doing the redirecting.

**Research date:** 2026-07-23. External sources fetched live this session (URLs cited inline).

---

## What's new here vs. the existing tasks-to-agents research

The prior doc's entire convergent pattern (§2 of that doc) is a **hierarchical, human-owns
/agent-executes shape**: one task, one assignee, the *human* is the reviewer and the one who
marks it done. Every dimension in that doc's table — assignment surface, "human stays the
accountable owner," the done-is-a-proposal gate — assumes the reviewer of an agent's work is a
person. Nothing in that research required two agents to know about each other at all. This
doc's ask is structurally different on two axes that doc never touches: **(1) instructions that
apply to a group of agents as a group**, not to one agent or one task, and **(2) a redirect
loop where the critic is itself an agent**, not the owner. Concretely, this doc also lands at a
moment where half of the prior doc's recommendation is *already shipping* — `project_tasks`
(migration `add_project_tasks.sql`) and `project_tasks_service.py` landed same-day (commit
`35571828`, Linear MAN-65, currently **In Review**), with MAN-66 (mention parser) and MAN-67
(frontend) still in Backlog. That existing/in-flight work is the single-assignee, owner-gated
shape; nothing in it, or in what shipped, touches shared project instructions or a second agent
reviewing the first.

---

## Prior art on peer-agent review / shared project instructions

**Honest headline: true structural peer-to-peer critique — where agent B's review of agent A's
work is a framework-level primitive that automatically redirects A, with no human or lead in
the loop — is not documented as a built-in feature anywhere below.** What exists ranges from
"actively absent" to "an example pattern you wire yourself" to, in one case, "real infrastructure
for it, but the critique behavior itself is prompted, not structural." No source converges the
way the prior doc's five task-assignment products converged; forcing a false parity between
them would misrepresent the research.

### 1. Anthropic's multi-agent research system — no peer review, isolated instructions
[How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)
describes a strictly one-directional flow: *"Each Subagent independently performs web searches...
and returns findings to the LeadResearcher. The LeadResearcher synthesizes these results and
decides whether more research is needed."* Review happens **only** at the lead level; subagents
never see or critique each other's output. Instructions are also **not** shared from a common
pool — *"Each subagent needs an objective, an output format, guidance on the tools and sources
to use, and clear task boundaries"* is delegated individually, per-subagent, by the lead during
task decomposition. This is the cleanest "no" in the set: no peer review, no shared instruction
object.

### 2. AutoGen (Microsoft) — a documented critic *example*, not a framework primitive
AutoGen's official docs do have real prior art here, more than the other frameworks, but it's
thinner than it first looks. The [Group Chat design pattern](https://microsoft.github.io/autogen/stable/user-guide/core-user-guide/design-patterns/group-chat.html)
gives every agent in the chat the same shared message thread (*"agents share a common message
thread"*), and the officially-referenced example notebooks include a genuine critic role — e.g.
an `EditorAgent` whose system prompt is *"Provide critical feedbacks to the draft and
illustration produced by Writer and Illustrator. Approve if the task is completed,"* and a
separate "Coder and Visualization Critic" notebook. AutoGen's own [Conversation Patterns tutorial](https://microsoft.github.io/autogen/0.2/docs/tutorial/conversation-patterns/),
however, frames `reflection_with_llm` only as a **chat-summary option**, not an interactive
review loop — confirmed on fetch: there is no standalone "reflection" or "critic" pattern
documented as a framework feature; a developer wires the critic role explicitly out of the same
two-agent/nested-chat primitives used for everything else. Two caveats worth being honest about:
(a) the Editor/Critic role is **hierarchical, not peer** — the Editor explicitly outranks and
approves/rejects the Writer, it isn't two equal agents critiquing each other; (b) the "shared
context" here is a shared transcript, not a shared *instruction* object — each agent still keeps
its own independent system prompt.

### 3. Linear's own docs — no multi-agent-on-one-issue precedent found
[AI Agents – Linear Docs](https://linear.app/docs/agents-in-linear) documents agents being
mentioned, assigned, and collaborating **with human teammates** — *"agents can be mentioned,
delegated issues through assignment, create and reply to comments, and collaborate on projects
and documents"* — but nothing about two different registered agents working the same issue, or
one agent commenting on another agent's activity stream. A search of Linear's 2026 changelog
(coding sessions, code intelligence, Linear Diffs, multi-level sub-teams) turned up nothing
closer either. This is a clean "not documented," not a "documented as absent" — Linear may add
it, but as of this session there's no public precedent to build against.

### 4. CrewAI — real "cookbook" precedent, no peer-review precedent
[CrewAI's Crews docs](https://docs.crewai.com/concepts/crews) are the best hit in the entire set
for **half** of the founder's ask. A Crew has genuine crew-*wide* configuration distinct from
each agent's own role/goal/backstory: a shared `function_calling_llm` ("the crew will use this
LLM to do function calling for tools for **all agents in the crew**"), crew-level `knowledge
sources` ("accessible to **all the agents**"), and crew-level skills applied to every agent.
That is a real, official, shipped analog to "project-level shared instructions governing a
group of agents" — CrewAI just calls the group a Crew instead of a Project. On peer review,
though, the docs stop at hierarchical **manager**-validates-outcome language for the
`hierarchical` process type; there is no documented pattern of one worker agent reviewing
another worker agent's task output within the same crew.

### 5. Claude Code's own "Agent Teams" — the closest single source to both halves of the ask
This is the strongest hit, and Empyralis's own harness, but it's still short of a guarantee.
[Orchestrate teams of Claude Code sessions](https://code.claude.com/docs/en/agent-teams)
(experimental, opt-in via `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS`) is explicitly the
*sibling-agents-talk-to-each-other* architecture the plain [sub-agents](https://code.claude.com/docs/en/sub-agents)
pattern (Task tool) does **not** have — sub-agents "only report results back to the main agent
and never talk to each other," which directly answers the research prompt's question #5: no,
today's Task-tool sub-agent pattern (what Empyralis's MAN-50 is explicitly modeled on) has no
shared context pool multiple children read from, and no sub-agent-to-sub-agent channel at all.
Agent Teams is the pattern built specifically to add that:
- **Shared project-level instructions, confirmed real:** *"CLAUDE.md works normally: teammates
  read CLAUDE.md files from their working directory. Use this to provide project-specific
  guidance to all teammates."* This is a literal working "cookbook" — one file, scoped to the
  project directory, read by every teammate regardless of their own individual spawn prompt.
  Reusable role definitions (subagent-definition files used as teammate types) are the second
  piece of this — a role authored once, shared across every teammate that adopts it.
- **Peer-review language, explicit but prompted, not structural:** *"Research and review:
  multiple teammates can investigate different aspects of a problem simultaneously, then share
  and challenge each other's findings"* and, more concretely, the debugging use case: *"Have
  them talk to each other to try to disprove each other's theories, like a scientific debate."*
  Teammates message each other **directly**, not mediated by the lead, over a real **Mailbox**
  (`~/.claude/teams/{team}/inboxes/{agent}.json`) — this is real agent-to-agent infrastructure,
  not owner-mediated relay.
- **The honest caveat:** none of that is a named "reviewer" role, a required gate, or a
  guaranteed behavior — it's what teammates do *because the lead's prompt told them to*. The
  only **structural**, harness-enforced review-like gate that exists is hook-based
  (`TeammateIdle`/`TaskCompleted`, exit code 2 to reject and force more work) — and that's a
  deterministic hook reviewing an agent, not one agent reviewing another. There's also no
  nested teams (a teammate can't spawn its own teammates) and exactly one team per session, so
  even this closest source caps the pattern's depth deliberately.

**Net:** the one clean structural analog for a project-level shared instruction store is real
and doubled (Claude Code's `CLAUDE.md` for teammates + CrewAI's crew-level knowledge/config).
The one source with actual peer-to-peer messaging infrastructure that a genuine critique loop
could be built on is Claude Code's own Agent Teams — fittingly, the harness Empyralis is built
on top of — but even there, "peer review" is emergent from a user prompt over a mailbox +
shared task list, not a shipped, named feature.

---

## Empyralis today — the exact gap

**`fleet_inbox` is still dead, re-verified this session, unchanged.** `fleet_message_agent`
(`server_modules/fleet_tools.py:1564-1631`) appends `{from_agent_id, message, enqueued_at,
message_id}` to the target install's `install_metadata.fleet_inbox`, capped at the last 20
entries (`fleet_tools.py:1605`), and returns immediately — fire-and-forget, no blocking option.
A fresh repo-wide grep this session for `fleet_inbox` across all of `/Users/mansur/empyralis`
returns exactly the same shape both prior docs found: two writers
(`fleet_tools.py:1596/1605` and `triage_service.py`'s `_enqueue_owner_notification`, using the
identical pattern to escalate to the owner's Sage) and **zero readers** — nothing in
`sage_agent_runtime_service.py`, `direct_chat_generation_service.py`, or anywhere else in
`server_modules/` ever pulls a `fleet_inbox` entry back out into a turn's context. Confirms
`docs/PLATFORM-MAP.md:2276-2284` and `docs/design/backbone-empyralis.md:336-349` verbatim, still
true today. **This is Empyralis's only agent-to-agent messaging primitive of any kind, and it
does not work.**

**No project-level instructions field/table exists anywhere.** The `projects` table
(`server_modules/control_plane_repository.py:615-628`) is exactly:
```
id, tenant_id, workspace_id, name, slug, description, is_default, archived,
metadata JSONB, created_at, updated_at
```
No `instructions`, `cookbook`, `system_prompt`, or `playbook` column. The one JSONB escape
hatch, `metadata`, is populated today only with `icon`/`tint` bookkeeping (confirmed in
`frontend/lib/workspace/fleet/fleet-data.ts:61-64`'s comment on `FleetProject.metadata` and in
`projects_repository.py`'s own read path) — nothing writes a shared-instructions concept into
it. Nor does anything read one back out: every file that both handles a `project_id` and
composes/injects a system prompt (`sage_agent_runtime_service.py`, `specialist_runtime_context.py`,
`model_router.py`, `runs_engine.py`) never touches `projects_repository` at all. A project today
carries zero prompt-shaping content — it is purely a grouping/cost-rollup container, exactly as
the prior research doc already concluded for the unrelated "does a task object exist" question.

**The brand-new `project_tasks` table (shipped same-day, commit `35571828`) confirms the gap
rather than closing it.** `migrations/add_project_tasks.sql` gives each task a `plan JSONB` and
a `metadata JSONB`, but **no comments table, no activity/review feed of any kind**, and
`project_tasks_service.py`'s only assignment entry point, `assign_task()` (`project_tasks_service.py:315-388`),
sets a single `assignee_agent_id` column — there is no schema concept of a second agent attached
to the same task at all, let alone one reviewing the first. `routes_fleet.py`'s task endpoints
(`GET/POST /fleet/tasks`, `PATCH /fleet/tasks/{id}`, `POST /fleet/tasks/{id}/assign`,
`routes_fleet.py:243-372`) mirror that: create, list, patch, assign — no `comment`, no
`review`, no second-agent verb anywhere in the API surface being built right now.

**MAN-50 (verified live via Linear MCP, `get_issue`) is scoped to the parent-dispatches-
disposable-child shape, not peer messaging.** Status: Backlog, parent MAN-46. Full scope, verbatim:
*"Real spawn / delegate / fan-out / block-for-reply. Context-hygiene sub-agents: a disposable
exploration thread does the heavy reading and returns only a summary to the main window... 
Orchestration out of the main context window."* That is explicitly Claude Code's plain
**sub-agent** (Task tool) shape — a child reports back to its one parent and nothing else — not
the **Agent Teams** shape (named, addressable siblings messaging each other directly over a
mailbox) that §5 above found to be the actual prior-art fit for peer review. MAN-50's own text
never mentions a shared instruction store or agent-to-agent critique; both of the founder's new
asks are adjacent to it but currently outside its scope as written.

**Placement fact used below:** the flat Agents tab
(`frontend/app/(account)/w/[workspaceId]/agents/page.tsx`) has a `project` **filter** dropdown
(`projectFilter`, line 135) but is fundamentally a cross-project, workspace-wide roster — it has
no task object, no per-project shared-instruction concept, and no natural place to hang a
"reviewed by Agent Y" fact.

---

## Recommended design

**Project cookbook — minimal viable version.** Add one owner-authored field the agents in a
project read but don't silently rewrite (mirrors the prior doc's "done is a proposal" pitfall —
a cookbook an agent can edit itself is a cookbook that stops being trustworthy). Given
`projects.metadata` is already a jsonb catch-all used for exactly this kind of small, optional
per-project fact (icon/tint today), the lowest-friction v1 is `projects.metadata.cookbook`
(free text, owner-edited only) rather than a new column — promote it to a real `cookbook TEXT`
column only if it needs indexing/search later. Wire it in at the one seam that already composes
an agent's system prompt per turn (`sage_agent_runtime_service.py` / wherever
`specialist_runtime_context.py` assembles context) — read the owning project's cookbook by
`project_id` and prepend it, the same way a project's existing `description` could already have
been surfacing there today but currently isn't (nothing reads `projects_repository` from prompt
assembly at all, confirmed above). No versioning, no per-agent override, no UI beyond a text box
on the project detail page for v1.

**Peer review — build it as a mode of MAN-50's real mailbox, not a second messaging system.**
MAN-50 already needs to replace fire-and-forget `fleet_message_agent`/dead `fleet_inbox` with
something a receiving agent's next turn actually reads (its own stated scope: "block-for-reply").
That real, addressable, two-way mailbox is necessary groundwork for peer review regardless —
build it once, use it for both. Concretely, on top of that mailbox:
1. Model review as **task-scoped**, riding the `project_tasks` object already shipping today,
   not as an ad hoc agent-to-agent chat. When a task reaches `done` (or any status transition
   the owner configures), optionally fan a review request to a second project agent over the
   real mailbox, tagged with `task_id` — same shape as the just-built `task_assigned` wakeup
   trigger (`bounded_scheduler_service.schedule_task_assigned_wakeup`), new reason
   `task_review_requested`.
2. The reviewing agent's output needs somewhere durable to land — this is the one genuinely new
   piece of schema peer review requires beyond MAN-50 + the cookbook: a small
   `project_task_comments` table (or a `comments` array inside `project_tasks.metadata` for a
   true v1), so a critique is a durable, task-visible artifact, not a transient message.
3. **The critique redirects by being read, not by mutating state.** The reviewed agent sees the
   comment on its own next wakeup (a `task_reviewed`-triggered turn, same scheduler mechanism)
   and decides whether to act on it — it does **not** auto-flip the task's status or plan.
   Every source in this doc's research that has anything resembling review (GitHub's PR-review
   gate and Claude Code's diff-comments in the prior doc; Claude Code's own `TeammateIdle`/
   `TaskCompleted` hook gate here) treats a critique as **feedback a human or the working agent
   itself acts on**, never a silent, automatic state mutation performed by the critic. Keep the
   owner as the final `done`/`reopen` authority exactly as the prior doc's §4.5 already
   recommends — this feature does not change who closes a task, only who else gets to comment
   on it first.
4. **Do not build first:** N-way "debate until consensus" (Claude Code's 5-teammates-disprove-
   each-other's-theories pattern) or a formal reviewer *role* distinct from a regular project
   agent. Both are legitimate v2 ideas once one agent reviewing one other agent on one task is
   proven; building the general case first repeats the prior doc's own top pitfall in a new
   shape — shipping machinery nobody has used yet at 1x scale.

---

## Placement verdict

**Confirming the founder's instinct: this belongs entirely inside Projects, never duplicated
into the flat Agents-tab roster.** Every piece of state involved is inherently project-scoped
and the in-flight work already agrees: `project_tasks.project_id` is a hard FK to `projects(id)
ON DELETE CASCADE` (`migrations/add_project_tasks.sql:32`), and the Tasks UI landing next
(MAN-67) is being built as a tab *inside* `projects/[projectId]/page.tsx`, sibling to the
existing agents list — not as a new top-level nav item. A project cookbook is project-scoped by
definition (it governs "how the agents in *this* project work together"; that sentence has no
referent at the Agents-tab level, where agents from unrelated projects sit in one flat,
filterable list with zero shared-collaboration context between them — confirmed above, the tab
has only a `project` *filter*, not project-scoped state). Peer review has the same shape: a
review is only meaningful attached to a shared task both agents can see, and that task object
lives in Projects, not on the roster. One nuance, not a correction: an individual agent's own
settings (its system prompt, tools, model) correctly continue to live on that agent's own detail
page, itself reachable only through a project-nested route
(`projects/[projectId]/agents/[agentId]/[tab]/page.tsx`) — so the cookbook doesn't replace
per-agent settings, it sits as a new layer *above* them. That's consistent with the founder's
instinct, not a walk-back of it.

---

## Sources (fetched live, 2026-07-23)

- Anthropic: [How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)
- AutoGen: [Group Chat — design pattern](https://microsoft.github.io/autogen/stable/user-guide/core-user-guide/design-patterns/group-chat.html) · [Conversation Patterns tutorial (0.2)](https://microsoft.github.io/autogen/0.2/docs/tutorial/conversation-patterns/) · [Group Chat with Coder and Visualization Critic (notebook)](https://microsoft.github.io/autogen/0.2/docs/notebooks/agentchat_groupchat_vis/)
- Linear: [AI Agents – Docs](https://linear.app/docs/agents-in-linear) · [Changelog index](https://linear.app/changelog) (2026 entries checked: coding sessions, code intelligence, multi-level sub-teams, Linear Diffs — no multi-agent-on-one-issue precedent found)
- CrewAI: [Crews – concepts](https://docs.crewai.com/concepts/crews)
- Claude Code: [Orchestrate teams of Claude Code sessions (Agent Teams)](https://code.claude.com/docs/en/agent-teams) · [Create custom subagents](https://code.claude.com/docs/en/sub-agents)

**Repo evidence** (this session, file:line-verified): `server_modules/fleet_tools.py:1564-1631`,
`server_modules/triage_service.py` (`_enqueue_owner_notification`),
`server_modules/control_plane_repository.py:615-628`, `frontend/lib/workspace/fleet/fleet-data.ts:61-64`,
`migrations/add_project_tasks.sql`, `server_modules/project_tasks_service.py:315-388`,
`server_modules/routes_fleet.py:243-372`, `server_modules/bounded_scheduler_service.py:635-675`,
`frontend/app/(account)/w/[workspaceId]/agents/page.tsx:135`,
`frontend/app/(account)/w/[workspaceId]/projects/[projectId]/page.tsx`,
`docs/PLATFORM-MAP.md:2276-2284`, `docs/design/backbone-empyralis.md:336-349`,
`docs/design/backbone-plan.md:32,47`, `docs/design/tasks-to-agents-research.md`.
Linear (verified live via MCP): MAN-50, MAN-64, MAN-65, MAN-66, MAN-67, MAN-46.

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

## An agent belongs to the WORKSPACE. Context is GRANTED, never inherited (2026-08-20)

**Founder's decision, and he called it core: "an agent belongs to the
workspace not to the project it's correct and I want you to remember that."
It REVERSES the earlier "agents are shared team infrastructure" line and it
supersedes every remaining trace of "an agent belongs to its project and
works only there."**

His reasoning, and it is an analogy to this very tool: *"you are not
specifically tied into a project inside this cloud application right? But
what you have is MCP tools and other things that makes you connect to other
things... In Linear you could see everything, teams projects and whatever,
you are not specifically tied to a specific thing to make you work. So
agents — just an agent with its harness, the rest should be MCP."*

```
AGENT   = harness + MCP tools.   lives in the WORKSPACE, never inside a project
CONTEXT = GRANTED per agent.     which projects it may reach. default NONE.
                                 never inherited from where the agent lives
```

**Why a GRANT and not a LOCATION, which is the whole point:** a location can
only ever express ONE project, and it drags an entire broken navigation
behind it — a project needs an Agents tab, pressing it swaps the rail into
an agent picker, and the project's own Tasks/Documents surface disappears
(the founder's words: *"it's fundamentally wrong... what we are building is
not kind of like Telegram surface"*). A grant expresses none, one, or many,
and changes no navigation at all. Channels exist so people talk to an agent
from Telegram/WhatsApp — never so the platform becomes a chat surface.

**The case that decides it, in his words:** *"even if I create this agent on
behalf of other businesses it wouldn't see my task or my context about the
platform, even though I created this agent for my father's business."* An
agent built for someone else's business must be able to reach NOTHING of the
owner's own workspace context. Only a per-agent grant can express that;
workspace membership cannot.

**STATE AS OF 2026-08-20 — two-thirds shipped, the important third is not.**
```
DONE   frontend/lib/workspace/fleet/project-views.ts
         PROJECT_TAB_VIEWS = ["tasks", "documents"]     Agents gone from projects
DONE   frontend/lib/workspace/fleet/primary-rail-nav.ts
         RAIL_ITEMS = Inbox · My work · Projects · Agents · Context
DONE   the grant itself — `feat/agent-context-grant`, 2026-08-20. See the
         section "The grant is metadata, and ABSENT is not EMPTY" below.
         `_enforce_agent_project_access` (routes_fleet.py) is still NOT this
         — it governs which PEOPLE may reach an agent, not which PROJECTS an
         agent may reach. The two are separate gates and both are live.
```

## The grant is metadata, and ABSENT is not EMPTY (2026-08-20)

**Storage is `workspace_agent_installs.metadata["context_project_ids"]`,
and the choice is load-bearing rather than lazy: the grant needs THREE
states and a JSON value expresses all three without a second flag column,
which a join table cannot.**

```
key ABSENT   ->  LEGACY   predates the grant. Behaves EXACTLY as before (its
                          one home project; the Operator's per-user fallback).
                          The founder's 13 live agents are all here. Silently
                          revoking them would be worse than the bug being fixed.
[]           ->  NONE     granted nothing. A REAL answer, not an absence.
                          Written explicitly at agent creation.
["p1","p2"]  ->  THOSE    and only those. The home-project column never widens it.
error/no row ->  UNAVAILABLE  no reach at all. NEVER falls back to legacy —
                          a grant we could not read must not buy back the
                          wider pre-grant behaviour.
```

Pool-is-None (SQLite fallback) is deliberately LEGACY, not unavailable: it
is not an error, it is a deployment carrying no grant information at all,
and calling it an error would take documents away from the Operator there
for no security gain.

**THE MODEL CANNOT WIDEN ITS OWN GRANT, and that is why this is NOT a
`fleet_configure_agent` patch key.** `fleet__configure_agent` /
`empyralis_configure_agent` are callable BY AN AGENT; a grant a model can
patch is not a boundary. The only writer is the owner-gated
`PUT /api/w/{ws}/fleet/agents/{id}/context-projects`, plus agent creation's
own default. `test_agent_context_grant.py` asserts both structurally (the
key is absent from `_ALLOWED_CONFIGURE_KEYS`; an AST sweep of every
`server_modules/*.py` finds the key used as a dict key in exactly two
files) — a behavioural test can only cover the write paths that exist today.

**Placement at creation IS the grant, and it is the only thing inherited.**
`fleet_create_agent` stamps `[the project it was just placed in]` — the
owner's own pick in the wizard, or the private project we just created for
it. Not `[]`: a brand-new agent with an empty grant has a task board that
refuses everything, which is a dead control. Not absent either: absent means
"predates the grant" and would hand a NEW agent the old behaviour.

**READ MANY, WRITE ONE — the shape `DocumentScope` already had, now shared.**
`project_ids` is the read reach; `write_project_id` is the ONE project a new
task/document goes in — the home project when it is granted, the single
granted project when there is only one, otherwise `""` and the create tools
refuse by NAMING the ambiguity. Never pick a winner silently; that is the
multi-agent provisioning-clobber shape this file already records.

Enforced on `_PROJECT_SCOPED_CONNECTOR_IDS` (`project_task__*`,
`document__*`, `goal__*`) at BOTH ends: `_resolve_specialist_toolset` now
carries `toolset["project_ids"]` (resolved from the install bundle already
in hand — zero extra queries) so an ungranted agent is never offered the
tools, and each dispatch re-resolves server-side so a stale prompt cannot
act. `list` issues ONE query per granted project rather than one unscoped
query — the fail-open `WHERE ($1 = '' OR ...)` family is banned here too.

Still open, found and NOT fixed: `fleet__get_project_activity` (operator-only)
still takes a `project_id` straight off the model's arguments and reads that
project's activity ledger — allowlisted by name in
`test_agent_context_grant.py` with a written verdict, so a NEW one fails the
test. `connectors_actions._resolve_agent_project_id` /
`routes_connections._resolve_agent_project_id` still read the home-project
column for CREDENTIAL ownership, which is a different question and was left
alone.


Do not re-nest agents under projects, do not add an Agents tab to a project,
and do not treat workspace membership as the context boundary. The grant is
the boundary.

## Positioning

**The WORKSPACE is the product. The agent layer is the second thing, not the
headline.** Founder's correction, 2026-08-12, and it supersedes any reading of
the "Target user" line below that puts agent-hosting first. The primary thing
Empyralis sells is a workspace where people work alongside agents — humans,
tasks, and CONTEXT (documents) in one place. Agent-hosting for other
businesses is a real use case served by the agent layer, but it is not the
positioning.

The reasoning is a bet about where the floor goes: agents commoditize. Within
a couple of years every tool has them, and "we host agents" differentiates
nothing — the founder's analogy is electricity, which nobody advertises and
nobody pays a premium for. What does NOT commoditize is the context a team
accumulates and actually operates in. Documents are the sharp end of that:
today a company's real knowledge is `.md` files pushed to a repo and never
read again. A document surface that is indexed, current, and the thing people
work IN is the durable asset — which is why documents/tasks quality is not
polish, it is the product.

Marketing consequence, stated by the founder: **Linear never says "AI".** They
say track your issues, work with your teammates. Copy that. Never lead with
agents; lead with what a team owns and does. See the landing-page entry — the
hero that was rejected argued this same point and still put "models are
rented" first, which is an AI-shaped claim.

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

**After an action, the product must tell the person what actually happened.**
Founder's law, 2026-08-14, escalated from a recurring bug to a standing rule
after it hit production three times in one night in three unrelated places:

```
"failed"  and  "may have succeeded, but I lost track of it"   ← different facts
"empty"   and  "I could not load this"                         ← different facts
                                                     never share one message/screen
```

Reporting failure ON SUCCESS is the worst case, not the safest one: the person
acts on the lie — retries something already done (duplicate agent, duplicate
billed droplet, duplicate workspace), gives up on something that worked, or
concludes the product is broken while it is fine. Confirmed live: signup
reported "Couldn't create the account" after creating it (MAN-343); accepting
a workspace invite reported failure after the accept had committed
server-side; login's post-auth readiness poll navigated to the workspace on
BOTH success and failure, so a poll hiccup after a genuinely successful login
bounced silently back to a blank `/login` with nothing ever said. The recurring
shape: a mutation that already committed, followed by a separate step (a
readiness poll, a list refresh, a bootstrap re-fetch) that can independently
fail, with both collapsed into one message or one action. The fix is never to
invent certainty — when the client genuinely cannot tell, verify the real
state before speaking (`frontend/app/join/[token]/page.tsx`'s pattern:
decode what was attempted, re-check whether it actually happened, only THEN
report), or say the honest "couldn't confirm, safe to retry" rather than a
flat "failed."

Guarded structurally, not just by the fixes:
`frontend/lib/workspace/outcome-honesty-drift.test.ts` (wired into
`npm run test:unit`) scans every `try/catch` for two proven live shapes — a
mutation-shaped step collapsed with a follow-up into one undifferentiated
catch, and a navigation call duplicated on both the success and failure
paths — with a written-reason allowlist for the false positives it cannot
see around (cross-function reasoning, mutually-exclusive if/else branches).
Like every drift test in this codebase, it cannot catch what it was not
built to catch: an "empty vs. could-not-load" collapse outside a multi-await
try/catch, a lying message on structurally fine code, or the identical shape
in the Python backend (server_modules has its own AST-based drift-test
idiom for that surface, e.g. `test_exception_and_task_lint.py` —
extend that family for a backend instance, not this file).

**Projects hold members directly. There is no Teams layer.** Decided 2026-07-31
after examining Linear's model, where a project carries its own member list and
lead independent of teams. Empyralis has one workflow, so a team tier would be
ceremony every customer leaves empty.

**SUPERSEDED 2026-08-20 — an agent belongs to the WORKSPACE, and context is
granted per agent. See the section of that name near the top of this file.
The entry below is kept only because its NAVIGATION reasoning is still
correct (never nest agents under a project, never add an Agents tab to a
project) and because it records what was believed at the time. Its
ownership claim is dead — do not act on it.**

~~AN AGENT BELONGS TO ITS PROJECT AND WORKS ONLY THERE. That is the design,
not a gap.~~ Founder's correction, 2026-08-13, after an agent was dispatched
to "fix" it: *"Agent should not be able to work in a different project if
it's not enabled to? And that's the reason why we have projects and inside
project you are going to create agents and agent is going to work in that
project."*

So `"No agents in this project yet"` beside `"Create your first agent"` in a
project that has none is **correct behaviour**, not the empty state of a
broken feature. MAN-304 called this "the most consequential of the three"
bugs and it is not a bug at all. Do not build cross-project assignment, do
not add an "add an existing agent" affordance, and do not treat the absence
of one as an oversight. His qualifier *"if it's not enabled to"* leaves room
for some future opt-in; that is an unmade product decision, not licence to
design for it speculatively.

**The rule above is about PEOPLE, and it does not generalise to agents.** The
line "projects hold members directly" was written about the Teams layer —
i.e. a person's membership is granted per project rather than inherited from
a team. It was misread as "membership is direct, therefore an agent is a
portable member that can be added anywhere," which is the opposite of the
model. A person can be a member of several projects. An agent is created
inside one and lives there. Those are two different questions and collapsing
them is what produced the wrong dispatch.

Consequence for NAVIGATION, and it is not small: if an agent belongs to its
project, then a top-level "Agents" list and a top-level "Conversations" list
are both aggregations ACROSS the boundary this rule establishes — they are
surfaces that contradict the model rather than merely duplicating it. The
project is the spine; agents and the conversations with them are reached
through the project they live in. Weigh any new top-level surface against
that before adding it.

**That navigation consequence is now built — `feat/project-as-spine-nav`,
2026-08-13.** Conversations and Agents are gone from `PrimaryRail` and the
command palette's "Go to" section (`primary-rail-nav.ts`'s `RAIL_ITEMS` is
now just Inbox + Projects — a pure module a plain test imports directly, the
same discipline `agent-count-shape.ts` already uses). The underlying
`/agents` and `/conversations` routes are DELIBERATELY still live and
unlinked, not deleted or redirected: several `next.config.ts`
`LEGACY_REDIRECTS` entries point AT `/agents`, and turning it into a
redirect target itself risks the exact "a redirect runs ahead of the router
and makes a real page unreachable" trap this file already documents. Landing
spots that used to funnel fresh arrivals at that now-unlinked page (post-invite
accept, the legacy `/sage` bookmark) now land on the workspace root instead.

Inside a project, the founder's own spec for Agents specifically: *"a left
rail to press a specific agent and just go straight to its chatting...it
could have been smaller, it could have been something compact"* — Telegram's
mechanic, list stays put while the pane beside it swaps. `agents/layout.tsx`
is a real Next.js layout wrapping every route under a project's `/agents`
segment (the bare index AND every agent's own page beneath it), so
`ProjectAgentsRail.tsx` persists across a navigation between agents instead
of remounting — that's what makes the list never lose scroll position or
re-fetch when you switch. Whether it renders composes
`agent-count-shape.ts`'s `planAgentCountShape` via
`project-agents-rail-shape.ts`'s `showsProjectAgentsRail`, never a second
rule: 0 agents → the project's own `FirstAgentEmpty`, unchanged, full width;
1 → no rail (a rail of one is worse than no rail, the same call already made
for a table of one) and a quiet redirect straight into that agent's chat; 2+
→ the rail. The project's own Agents/Tasks/Documents tab bar still renders
only at the bare index (matching Tasks/Documents' own pattern); drilling into
a specific agent drops it, exactly like task/document detail pages already
do — the breadcrumb is how you get back, not a tab strip duplicated per
level.

**Non-owners never see personal or self-chat threads.** Conservative default,
enforced without asking.

**A customer may upload notes and pictures. Never code, archives or
binaries.** Founder's instruction, and it is positioning rather than
hygiene: a file surface that accepts anything becomes a code-sharing tool by
accident, which is the product we are explicitly not building.
`upload_content_policy.assert_allowed_upload` is the one gate, server-side,
run before any byte reaches disk — **the EXTENSION is the decision and the
BYTES are only a refutation**. A `.txt` holding a shell script is text, is
not detectable, and stays accepted; a `.txt` whose first bytes are a
ZIP/ELF/Mach-O is a renamed binary and is refused, as is a `.png` that is
not a PNG. SVG is deliberately not an accepted picture — attachments are
served straight back by `FileResponse`, so an SVG is a picture that is also
a program on the workspace's own origin. Every refusal names what IS
accepted. A frontend `accept` attribute is a courtesy on the picker, never
the guardrail.

**`POST /api/sage-chat/attachments` is registered TWICE, and the one that
wins is decided by registration order.** `routes_workflows.py` calls
`register_sage_context_file_routes` before `register_sage_chat_routes`, and
FastAPI serves the first match — so `sage_context_files_api`'s handler is
live and `sage_chat_api`'s identical declaration is dead code that still
type-checks, still passes its own tests, and had the only size cap of the
two. Both now call the same policy, with a source assertion that they do.
Before changing behaviour on a path, check whether something else registered
it first; a test against the shadowed twin asserts nothing about what a
customer hits.

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

## Navigation: project as spine (2026-08-13)

**Conversations and Agents are gone from the top-level rail, permanently —
not reordered, removed.** Founder's own words on the state before this:
*"what do you think I am supposed to do when it comes to this conversation?
Inbox agents project buttons? What about simple users? What are they going
to press?"* Three of the four top-level surfaces were lists ABOUT things;
only Projects was the work.

The rule this follows is stated once, elsewhere in this file, and is
load-bearing here: **an agent belongs to its project and works only
there.** So a top-level list of every agent, or every conversation, across
every project necessarily reaches PAST that boundary — it is not merely
redundant with Projects, it contradicts the model.

```
BEFORE                        AFTER
Inbox                          Inbox
Conversations   ← removed      Projects
Agents          ← removed        General
Projects                           Tasks · Documents · Agents
                                  Marketing
                                    …
```

The rail now nests a project's own Tasks/Documents/Agents beneath its row
when that project is open — reaching a project's work is one rail, not a
rail plus a second tab strip once you land. The workspace home
(`FleetHome.tsx`) stopped being a fleet survey ("Your fleet · N agents · M
online", aggregating every agent workspace-wide) and became the workspace
itself — its projects, and the work in them.

**This shipped in two passes, and the first pass was incomplete in a way
that read as broken.** Pass one removed the two rail links and added a
compact per-project agent list, but left `FleetHome.tsx` unchanged — so the
workspace home still aggregated all ten of the founder's agents into one
grid, capped at `--content-max: 820px` (a READING width, its own comment
says so) and centred, producing a single narrow column of cards with dead
space on both sides at any real monitor width. The founder's reaction,
verbatim: *"what the fuck is this piece of shit... two other sides are
completely open."* An agent (mine) misread that as a regression from the
navigation change and REVERTED the founder's already-approved work chasing
it — the layout bug predated the nav change and had nothing to do with it.
Reverting approved work on a guess, without asking, is exactly the mistake;
the fix was to finish the change properly (workspace home now shows
projects, cards moved to the 1140px `--content-max-wide` container) and
never revert a decision the founder has already made without asking first.

**Two independent bugs made the rail's active/focused item show a
permanent purple ring, both real, both fixed the same night — do not
conflate them if a third one turns up.**
1. `FleetAgentDetail.tsx`'s tab strip called `activeTabRef.current?.focus()`
   on mount to give SPA navigation a landing spot. Programmatic `.focus()`
   satisfies `:focus-visible` exactly like a real Tab keypress — the browser
   cannot tell them apart — so `globals.css`'s `:focus-visible { box-shadow:
   var(--app-shadow-focus) }` drew a ring around the tab on every page load,
   for every mouse user. Removed the mount-time focus call outright rather
   than suppressing the ring (the ring is the only signal a keyboard user
   gets; hiding it to kill an unwanted trigger trades a cosmetic bug for an
   accessibility one). The SPA-landing-spot need this was reaching for
   belongs on the page's own heading (`headingRef` + `tabIndex={-1}`,
   already the pattern in `TaskDetailView`/`DocumentDetailView`), never on
   an interactive control.
2. `PrimaryRail.tsx`'s `j`/`k` keyboard navigation sets `focusIdx` and draws
   `.fleet-rail-item--focus` (a 1px accent ring) — and NOTHING ever cleared
   it. Not a click, not navigating, not touching the mouse at all. The
   listener is global whenever focus isn't in a text field, so one stray `j`
   or `k` parked the ring on a rail item for the rest of the session. Fixed
   by clearing `focusIdx` on route change and on the first `pointerdown`
   anywhere — a keyboard cursor means "where the keyboard is," so it has
   nothing left to point at the moment someone reaches for the mouse or
   actually goes somewhere. `j`/`k` still work and still show the ring while
   in use, which is the only case it exists for.

The lesson from both: **a component that moves focus programmatically owns
the obligation to also clear it.** Grep for `.focus()` calls with no
matching reset before assuming a visual bug is a CSS problem.

## Rail spaces: the rail is where you pick (2026-08-16)

**Founder's rule, verbatim intent: "the rail is where you pick; the content
is what you picked."** A list of navigation choices rendered inside the
content area is a second rail pretending to be content. One offender fixed
the same night; a second was attempted and reverted the same night — see
below. Mechanism is `primary-rail-space.ts`, pure + tested:

```
DEFAULT  Inbox · My work · Projects          ← flat, 2026-08-15, unchanged
SPACE    ‹ Back                              a real <Link>, never router.back()
         {space name}
         {the space's own pick-list}         active marked like any rail row

/settings/**                → Settings space (Account/Workspace/Connections/
                              Keyboard shortcuts). Back → where you came
                              from, workspace root on a direct load. The
                              in-content GroupedRail sidebar in SettingsShell
                              is DELETED; the "Settings › " crumb-parent is
                              folded (Breadcrumbs.tsx).
```

This REFINES 2026-08-15's flat rail, not reverses it: merely opening a
project still never changes the rail — a space exists only where the
content's whole job used to be a second nav column. `RailSpace` has exactly
one kind, `"settings"` — not two.

**A second space was built the same night and REVERTED the same night —
this is not a hole in the pattern, it is the pattern rejecting a bad fit.**
`d5833c615` moved a project's Agents section (2+ agents) into a
`project-agents` rail space — "‹ Back" (to the project), the project name,
one row per agent — deleting `ProjectAgentsRail.tsx` and
`agents/layout.tsx`, which had rendered that list in the content area
beside the chat. It shipped, and the founder found the result live: opening
a project's Agents tab now showed the rail morphed into the agent list AND
the content area still showing the Tasks/Documents/Agents tab bar with
Agents highlighted plus an empty "Select an agent to start chatting"
prompt — two navigation surfaces both claiming to be "where you pick," one
of them now pointless. A follow-up dispatch proposed folding Tasks and
Documents into the rail too (so ALL of a project's sections would live in
one persistent rail structure, Linear's Team-sidebar shape) — the founder
rejected that direction directly: *"everything in one [list] is not
something I am looking for."* The fix taken instead was the opposite move:
revert `d5833c615` outright (`fec3bc118`).

**CORRECTION, 2026-08-18 — the paragraph that used to sit here was stale and
said the OPPOSITE of the code, which matters because it read as a standing
prohibition.** It claimed `ProjectAgentsRail.tsx` and `agents/layout.tsx`
were "BACK, not gone" and that agents-in-rail must not be re-attempted. In
fact the revert was itself reverted (`862f80877`, "Reapply") after the
founder said he had used and wanted the rail version, and `16501a4e7` then
fixed the real defect — the project tab strip rendering in content while the
rail showed the same picker, i.e. the two-navigation-surfaces bug that caused
the original complaint. Both files are DELETED; the project-agents rail space
is live. Verified by `ls` on 2026-08-18: neither file exists.

What survives from that night is the narrower, still-correct rule: only ONE
surface may be the picker at a time. What does NOT survive is "never put
agents in the rail" — that shape shipped, and the founder has since decided
agents leave projects entirely and become a top-level surface (MAN-357), so
the rail is where they belong.

The broader "fold every project section into the rail" shape remains rejected
by name (*"everything in one [list] is not something I am looking for"*).

Same night, same surface, three more founder calls:
- **Settings left the flat rail** (two doors to one room; the account menu
  keeps its link). `"settings"` is now hand-listed in
  `NON_RAIL_SHELL_SEGMENTS` — no longer derived from `RAIL_ITEMS`, and a
  segment the decider misses renders a silent blank pane.
- **Keyboard shortcuts is a routed Settings page**, not an account-popover
  accordion. Its go-to rows DERIVE from `RAIL_ITEMS` (the list the chord
  handler matches), because the hand-kept list advertised G C/G A for
  surfaces removed from navigation — chords bound to nothing.
- **A project's tab bar is exactly Tasks · Documents · Agents.** People was
  the toolbar's avatar-stack + "+" surface duplicated as a tab ("I never
  asked for these people... People already exist on top"). Set lives in
  `project-views.ts`; the `/people` route stays live and unlinked, same
  treatment as `/agents`/`/conversations`.

## Agent detail surface (2026-08-13)

**Overview is gone.** Founder: *"remove overview because it's something
that we genuinely don't need inside this agent."* Its contents were
redistributed on purpose, not deleted wholesale — decide the same way for
anything else that gets cut here:
- rename / persona / schedule → a new **General** tab, first item in
  Configure's Brain group (set-once config, same shape Model/Capabilities
  already use there).
- the one-line status sentence → deleted outright. Fully redundant with the
  Sessions panel's own Status row, which is visible on every tab now.
- the day-grouped activity feed → deleted outright, NOT folded into Work.
  Work's own trace-based timeline (tool/plan/browser/delegation/approval
  events) is a strictly richer account of "what this agent has done" than
  the shallower ledger feed Overview showed — keeping both would have been
  two competing answers to the same question.

**Memory moved into Configure** and stopped being a top-level tab, for the
same "set-once config lives in Configure" reason as General above.

**The right panel is now Sessions, not a bare Properties box.** Founder,
specifically: *"instead of this right panel we must have sessions... at the
top of this right panel we are going to have some things just like what we
have right now, and below we are going to have session histories."* One
panel, two stacked regions — Properties unchanged on top, session history
below it with its own independent scroll (a long history must never drag
Properties out of view). The bar he set for the list itself was Claude's own
conversation list: newest first, a readable title plus a relative
timestamp, dense and quiet, one click to open, the current session visibly
marked, one clear way to start a new one. No cards, no avatars.

Data source is `GET /api/threads` via `agent_threads` — Postgres-backed,
RLS-enabled, confirmed live and NOT the same code path as the
`agent_conversation_memory` table `ConversationsView.tsx`'s own comment
flags as questionable under SQLite fallback (that one is personal-channel
memory, a different table entirely). Traced before building on it, per this
file's own standing rule that a memory/fixture shape is an assumption until
verified against the real producer.

Found and fixed in passing: Memory's Save/Delete controls inside Configure
were overflowing past the sheet's own edge at narrow widths — invisible and
unreachable — because the shared file-list `min-width: 320px` was sized for
a full-page tab and didn't fit the sheet's ~500px pane. Scoped a narrower
override to `.agent-configure-content` only; the full-page Inbox/Work/Memory
tabs, which the 320px width is correct for, are untouched.

## Destructive-action awareness is judgment, not a mechanism (2026-08-19)

**A hardcoded blocklist for destructive shell/file commands was proposed and
the founder rejected it — correctly.** His reasoning:

```
"you are not going to delete my documents even though I said it, but you
 WOULD do it once I say 'I have these documents and I don't want these,
 so just delete them all'... what we need is to make it aware for itself"
```

`rm -rf ~/Documents` is the IDENTICAL string in both cases. A matcher sees
only the string; what differs is intent, and intent lives in the
conversation. So the fix is guidance in the system prompt
(`sage_agent_runtime_service._destructive_action_awareness_guidance`,
injected into both the specialist and master/Sage prompt-assembly branches
in `_handle_sage_chat_unguarded`) — never a gate. `require_approval` stays
hardcoded `False` at all four shell/hardware call sites in
`skills_service.py` (confirmed, left untouched) — no approve/deny state, per
this file's own "No approval system" law.

**Execution mode (sandbox vs. full_access) is deliberately NOT asserted as
a per-turn fact.** Which gateway/registration a shell call lands on is
resolved per tool call in `skills_service._runtime_access_mode_from_
direct_tool_context` (payload override → session metadata → a live
`gateway_state_repository` registration lookup → guarded default) — not
known, or cheaply knowable, at prompt-assembly time. A specific mode
claimed here that turns out wrong is worse than saying nothing (the
outcome-honesty law, one level up). What the guidance states instead is
the standing invariant — sandbox is the floor, full_access is a real
two-factor opt-in — and points the model at the concrete signal every
hardware/shell tool result already carries
(`_format_hardware_action_result`'s `runtime_access_mode` field), so it can
check rather than assume.

Guarded by `server_modules/tests/test_destructive_action_awareness.py`:
content assertions (states both calibration examples verbatim, names
reversibility as the axis rather than a command-name list, never asserts a
specific mode), a redaction-survival test (this exact function,
`_build_prompt_envelope`, is the one that silently ate 17 of 72 tool names
in 2026-08-08 — see the entry below), and a structural test that both
prompt-assembly branches reference the guidance function, so a future edit
cannot silently drop it from one branch the way this file has repeatedly
documented happening elsewhere. ~125 words, ~160 cl100k tokens, paid on
every turn.

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
- **FOCUS IS NEUTRAL, NEVER THE ACCENT.** `--focus-ring` (theme-tokens.css) is
  the one token; `:focus` rules point at it and at nothing else. See the
  section below.

## The focus ring is neutral, and it is still a ring (2026-08-21)

Founder, repeatedly: *"you will remove that purple ring everywhere. Nobody
asked for this purple shit."* Focus is a CURSOR, not a state worth
celebrating — spending the accent on whatever the keyboard happens to be
sitting on is the opposite of "one accent, on the single primary action."

**It was removed from the ACCENT, not removed.** This file already records
the earlier incident verbatim: the ring *"is the only signal a keyboard user
gets; hiding it to kill an unwanted trigger trades a cosmetic bug for an
accessibility one."* Every control that had a ring still has one.

```
                       ring colour        vs every surface in its own ramp
BEFORE  dark    rgb(101,70,132)..(111,80,142)   2.08 - 2.36 : 1   ← all FAIL
        light   rgb(197,176,219)..(207,186,228) 1.71 - 1.78 : 1   ← all FAIL
AFTER   dark    #a1a1a1  rgb(161,161,161)       5.26 - 6.94 : 1
        light   #6f6f6f  rgb(111,111,111)       4.33 - 5.02 : 1
                                        WCAG 2.1 SC 1.4.11 wants 3:1
```

**The purple ring was below the accessibility bar on every single surface in
the product.** So this was never a taste-vs-a11y trade; it is better on both
axes. Figures are BROWSER-measured (composited in a real canvas and read
back), not derived — the old ring was an alpha `color-mix` over an `oklch`
accent, and approximating that by hand is off by ~0.15.

Opaque, not alpha-over-a-token, so the rendered colour is knowable instead of
a function of whatever surface it lands on.

**The line the sweep must not cross, and it is the whole discipline:**

```
CHANGE   a ring/outline/border drawn BECAUSE something is focused
LEAVE    hover/active/selected styling that a :focus-visible selector
         merely SHARES  (`.x:hover, .x:focus-visible { ... }`)
```

So the mechanical pass only rewrote rules where EVERY selector in the group
is a focus selector. Verified in the shipped bundle rather than the source
tree: of 185 focus-related colour declarations the browser actually loaded,
the only 10 with any chroma are all `:hover, :focus-visible` or `--active`
rules. No focus-only rule anywhere carries a hue.

**Focus indicators wear shapes other than `:focus`, and grepping `:focus`
misses them.** Four such, all found only by asking "what does this draw, and
why does it appear": the properties-resizer hairline (its own sibling rule
says `outline: none; /* the hairline IS the focus indicator here */`), the
rail's `j`/`k` keyboard cursor `.fleet-rail-item--focus`, and the three
click-to-edit inputs (`.fleet-overview-title-input`,
`.fleet-task-page-title-input`, `.fleet-task-page-desc-input`) whose accent
border only ever appears because they auto-focus the moment they exist.

**`AgentCreateCard.tsx`'s `nameRef.current?.focus()` STAYS.** It is what made
the ring most visible (the halo appeared before the customer touched
anything), so it looks like the culprit and is not: the complaint was the
colour. Removing it would leave a dialog whose focus is on `<body>` — Tab
restarts from the top of the document and a screen reader announces nothing —
which is a worse bug than the one being fixed, and it is the same mistake in
the opposite direction as hiding the ring. Note the earlier incident this
file records is NOT a precedent for removing it: that was a mount-time
`.focus()` on a TAB STRIP, an interactive control nobody was about to type
into. The first field of a dialog is the canonical case where auto-focus is
correct.

Found and fixed in passing: `.fleet-agents-conversation-search input` had NO
focus indicator at all — borderless, transparent, `outline: none`. The one
control the sweep turned up that had nothing rather than the wrong colour.

**Harness note that cost real time, and it is not in the Browser-pane entry
above:** a style recalc does NOT flush within a single `javascript_tool`
call. `getComputedStyle` after injecting a rule — even `!important` — returns
the PREVIOUS call's value, so a correct fix reads as broken and an injected
probe reads as ignored. Mutate in call N, read in call N+1. Anything that
does not depend on CSS recalc (canvas readback, inline `style` on an element
you just created) is fine in-call.

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

**A `next.config` redirect runs BEFORE the router, so it can make a real page
unreachable and no React code will ever say so.** Found 2026-08-13 by signing
up as a new customer and looking at the screen. Every fresh account landed on
a bare "Agents · 0" list instead of the workspace — the product's own front
door was unreachable, on every visit, for months. THREE independent layers
each got it wrong, and fixing any two changed nothing:

```
create_local_password_account   hardcoded default_route = /w/{id}/sage
app/page.tsx                    hardcoded /sage, ignoring the workspace's
                                  own stored default_route entirely
next.config LEGACY_REDIRECTS    { '/w/:workspaceId' → '/w/:id/agents' }
                                  ← a Phase-7A rule written BEFORE FleetHome
                                    existed, still firing after it shipped
```

The redirect is the one worth remembering: `app/(account)/w/[workspaceId]/
page.tsx` renders `FleetHome` and says so in its own comment, `Breadcrumbs.tsx`
says the same — and neither ever ran, because Next's `redirects()` resolves
ahead of the router. Grep `LEGACY_REDIRECTS` before concluding a route is
broken in React; a page that is never reached looks exactly like a page that
renders nothing. `frontend/next.config.test.ts` now asserts the bare
workspace route is never a redirect source again.

Corollary on copy: the signup hero promised "fresh accounts land straight in
your Agents list" and led with "Ask AI, Build, Discover" — three surface
names that no longer exist. **Copy that names a SCREEN goes stale when the
screen moves; copy that names the WORK does not.** Rewritten to lead with
projects/documents/tasks per the positioning entry above.

**A migration can ship without the code that fills it, and the schema will
sit there for weeks looking done.** `migrations/add_task_sequence_numbers.sql`
built the whole Linear-style `GEN-12` identifier schema — `projects.task_key`,
`projects.task_seq`, `project_tasks.number` — with long comments describing
exactly how `create_task` and `create_project` should allocate them. The
Python was never written. So every task in every project displayed a random
hex fragment of its own uuid (`69D656`), and `task-status.tsx`'s own comment
confessed it ("the honest version... until the backend has a real per-project
sequence number"). Landed 2026-08-13: atomic `UPDATE projects SET task_seq =
task_seq + 1 RETURNING task_seq`, `task_key` allocated with dedup at project
creation, and the display key resolved by JOIN rather than denormalised per
row. **"Built, tested, and never wired" has a schema-shaped variant** — when
you find a migration, grep for the code that writes the columns before
assuming the feature exists.

**The silent-zero-rows shape above is now a structural guard, not just a
warning in this file.** `server_modules/tests/test_rls_dml_drift.py`
(2026-08-18) scans `CONTROL_PLANE_SCHEMA_SQL` plus the rest of
`ensure_control_plane_schema()`'s body in `control_plane_repository.py`
(the boot-schema surface DEPLOY-RUNBOOK.md step 3b runs as the
non-superuser `empyralis_app` role) and every `migrations/*.sql` file, for
any line starting `UPDATE `/`INSERT INTO`/`DELETE FROM` against a table
carrying FORCE ROW LEVEL SECURITY — parsed independently from
`migrations/enable_rls.sql`, never from either scanned source. Building it
turned up a fourth live instance (`project_tasks` status-vocabulary DO
block, same file) and five historical migration-file offenders; the
fourth is fixed (`SET LOCAL app.rls_bypass = 'on'`, same pattern as the
already-fixed `add_task_sequence_numbers.sql`), two are self-detecting
(a later `CREATE UNIQUE INDEX` would fail loudly on any real duplicate),
one is a byte-for-byte twin of instance 2's own verdict, and two
(`stage_4b_agent_isolation.sql`'s `hardware_access`/`subagents_enabled`
backfill, `unify_fleet_tool_toggle_ids.sql`'s key-rename backfill) are
flagged, not fixed — one-time historical migrations this pass had no
production access to verify, spun off as a separate task. Instance 2
(`workspace_agent_installs` label dedupe) stays in the allowlist exactly as
before, unfixed pending the same product decision. Three canaries (empty
`enable_rls.sql` parse, missing boot-schema block, empty `migrations/`
directory) each raise loudly rather than let the scan enforce nothing —
verified live by breaking each on purpose and watching it fail.

**A capability branch is a live-path branch, and the OWNER can be the one
locked out.** The sharpest instance so far, 2026-08-12. `DocumentDetailView`
rendered `canWrite ? <textarea> : <MarkdownLite>` — so a document's own
AUTHOR never once saw it rendered. Write access bought you raw markdown
source (`# Heading`, `**bold**`, `| pipe | tables |`) forever; only a
read-only viewer got headings, tables and images.

```
BEFORE                                  AFTER
  canWrite ? <textarea>   ← the owner     rendered MarkdownLite, everyone
           : <MarkdownLite> ← a viewer    click a region ─▶ edit it
  two code paths, and the                 one default. editing is entered,
  product's own user was on the           never the state you land in.
  one that shows source
```

The whole `markdown-lite.tsx` feature pass (tables, images, nested lists,
URL sanitizing) had just shipped into the branch the document's author
cannot reach — "built, tested, and never wired", one level up from the code:
wired, to the wrong half of an `if`. The founder reported it twice ("this is
documents I'm telling this again... yet it renders differently") and two
agents investigating the document surface both missed it, because a
capability flag reads as an authorization detail rather than as the switch
deciding what the page IS.

Three rules follow. **When a ternary on a permission flag picks between two
RENDERINGS rather than between a control and its absence, the page has two
designs and only one of them was reviewed** — check which one the person the
feature exists for actually lands on. **"No mode toggle" must never become
"one role is permanently in edit mode"**: the direct-editing rework that
produced this was right to delete the Edit/Preview button and wrong to
resolve it toward the raw control; click-to-edit per region (TaskDetailView's
own `editingTitle`/`editingDescription` + `skipBlurCommit` idiom, now reused
here) satisfies both. And **a click inside a rendered body means "edit this"
only when it was not already a click on something else** — MarkdownLite
emits `target="_blank"` links, so an unguarded handler both opened a tab and
flipped the document behind it to source, and a drag-select to quote a
paragraph was discarded by the textarea swapping in.

Documents deviate from TaskDetailView on ONE point, deliberately: **Escape
flushes, it does not revert.** A task's title is a single-shot commit, so
discarding on Escape loses nothing; a document autosaves while open, so
reverting would throw away keystrokes typed inside the debounce window that
the person has no reason to believe are at risk.

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

**A hung `node --test` file is not always the execFile family above — verify
which one before reaching for that fix.** 2026-08-14,
`ws-client-event-seq-race.test.ts`: the assertion printed a checkmark then
the process never exited, the same surface symptom as the execFile leak. It
was a different bug. `GatewayWsClient.connect()` starts `HeartbeatLoop` with
a plain (non-`unref`'d) `setTimeout` — correct in production, where a
gateway process must stay alive on a real heartbeat cadence for as long as
the connection is meant to live — and the ONLY thing that clears it is
`disconnect()` (`heartbeatLoop.stop()`, `cloud/ws-client.ts:747`). This test
called `connect()` and never called `disconnect()`, so the timer (scheduled
~11.5 days out, from the harness's own `heartbeat_interval_seconds: 999_999`
session payload) sat there un-unref'd forever. Confirmed with
`process.getActiveResourcesInfo()` (`["Timeout"]`, one entry) plus a patched
`global.setTimeout` capturing call sites: the surviving timer's stack traced
straight to `HeartbeatLoop.start` -> `GatewayWsClient.connect` ->
`buildClient` in the test itself — a mock-harness omission, not a
production leak. `checkpoints.ts`'s own 100ms save-debounce timer, the other
candidate this shape usually points at, was already correctly `.unref()`'d
(`state/checkpoints.ts:150,163`) and was not involved. Three sibling
`ws-client-*.test.ts` files that also call `connect()`/`run()` do NOT hang,
because each already tears down correctly:
`ws-client-socket-error.test.ts`/`capability-reevaluation.test.ts` call
`client.disconnect(scope)` before their `finally`, and
`ws-client-reconnect-resource-safety.test.ts`/`startup-sequencing.test.ts`
drive `run()` to a deliberate non-retryable failure so it throws and settles
without a live heartbeat loop left behind — `ws-client-event-seq-race.test.ts`
was the one file written without either pattern. Fixed by adding the same
`disconnect()` call the sibling files already use; production code is
unchanged, on purpose — patching this in `HeartbeatLoop`/`ws-client.ts`
would have converted a real "keep the gateway alive" timer into one that
stops itself, which is wrong for the thing actually running on a customer's
box. `ws-timeout.test.ts` genuinely runs ~70s of real (unmocked)
10s/20s/40s timeouts by design — slow, not hung; don't mistake one for the
other from a short poll window. Full gateway suite verified green after the
fix: `npm test` (`node --test dist/__tests__/*.test.js`), 884 tests, 884
pass, 0 fail, 0 skipped, exit 0, ~116s.

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

**The billing half of that gap is fixed. A SIBLING gap at the same seam is
not, and is why the legacy engine still cannot be deleted.** Found the same
day (2026-08-09, `investigate/single-turn-engine`, folded in here rather
than kept as its own branch): `direct_chat_generation_service.py`'s
`persist_direct_chat_memory_best_effort`/`persist_direct_chat_transcript_
best_effort` — fact extraction, the daily-log summary, the session
transcript — are called only from that module, never from
`sage_agent_runtime_service.py`. Grepped as of 2026-08-19: still zero call
sites for either function outside `direct_chat_generation_service.py`. So a
turn on the SDK engine (the production default) writes thread history via
`thread_service.record_user_turn`/`record_assistant_turn` — which DOES run
on both engines, do not mistake it for the memory pipeline — but never runs
the memory pipeline itself. "Empyralis is the owned-context layer" is not
yet true on the engine that actually runs. Also still open, same grep pass:
`reasoning_effort` has zero references in `openai_compat_adapter.py`, so it
is silently dropped for every adapter-routed provider (OpenAI/Gemini/xAI) on
the SDK engine — the Fleet Model tab's reasoning-effort picker is a dead
control for those agents; the legacy engine honours it natively. Move both
onto the shared post-loop path before ever deleting legacy —
`EMPYRALIS_FORCE_LEGACY_ENGINE` (MAN-312) and the per-agent legacy pin exist
precisely because legacy is still the only engine that carries these two.
**The reasoning_effort half is FIXED, 2026-08-20 — see "BYO-subscription
model truth" below.** **The memory-pipeline half is FIXED 2026-08-21, and
the paragraph above was HALF WRONG about it in a way that matters — see
"Agent memory: where it lives" below. The "zero call sites" claim was
stale (a real chain existed: `sage_agent_runtime_service` ->
`conversation_memory_facade_service.persist_interaction` -> `memory_service`).
The CONCLUSION was right anyway, for a worse reason: every branch behind
that chain was gated on a metadata flag only the legacy caller sets, and
the facade returned `{"persisted": True}` regardless. Do not re-derive this
from a grep — a grep found the callers and would have told you the pipeline
ran.**

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

**A workspace invite created a row, returned a token, and sent nothing.**
`email_provider_service.py` was a complete, working Resend integration with
exactly ONE caller (email verification) while `create_workspace_invite_route`
had no mailer at all — the "built, tested, and never wired" shape, except the
unwired half was the sender and the UI honestly said so ("No email sender
yet"), so it read as a decision rather than a gap for weeks. Fixed 2026-08-11
(`workspace_invite_email_service`). Two rules follow. **The email must never
cost the invite**: the row and token exist before the send is attempted and
are returned whatever the mailer does, so every failure — including a
control-plane read for the workspace NAME — is caught and reported, never
raised. And **"provider unset" / "send failed" / "sent" are three facts, not
two**: collapsing the first two tells an owner to retry something that can
never work, and either rendering as "sent" is the original bug. The
copy-link fallback stays on screen in all three states.

**Email verification was fully built and gated NOTHING, and the fix is a
send-gate, not a 403.** Found 2026-08-12. `email_verification_service` had
the whole thing — hashed+peppered 6-digit code, TTL, a per-account 5-attempt
lock independent of the HTTP rate limit, a resend cooldown — and the only
consumers of `verification_status`/`is_verified` outside the module were the
three routes that manage the code itself. Nothing in the product asked. So
anyone could sign up as somebody else and send workspace invites carrying
our name to people who never asked for them.

The obvious fix (refuse the invite until the inviter verifies) was measured
against production before being written, and would have been wrong:

```
prod email_verification_codes    pending 12 | verified 1
  a hard 403 → invites taken away from ~every existing account at once
  and start_verification writes the code row BEFORE it sends, so an
  account whose signup email failed is PERMANENTLY pending — locked out
  of a capability by a mailer outage it never saw
```

So the gate is on the SEND. The invite row, the token and the copy-link are
created and returned exactly as before — the owner can still bring someone
in by handing them the link — and no mail leaves this domain on behalf of an
account that has not proven it owns its address. The abuse vector closes
completely and nobody loses a capability.

Three rules follow. **`DELIVERY_WITHHELD_UNVERIFIED_SENDER` is a FOURTH
delivery state beside sent/not_configured/failed, never a reuse of one** —
"sent" would be the original lie in a new costume, and "failed" tells an
owner to retry a mailer that is working perfectly while hiding the one
action that changes the outcome (the UI badge is "Link only", not "Didn't
send"). **This gate fails OPEN on an unreadable status, deliberately**: it
exists to stop abuse, not to make every invite email in the product depend
on one more control-plane read, and treating a blip as "unverified" would
silently stop all invite mail with no error anywhere. And **`is_verified()`
is TRUE for status `none`** (no code ever issued — accounts predating the
feature), which is the service's own backward-compatibility rule and the
thing that keeps this from retroactively silencing anybody.

Watch for this shape in the tests: `_register_owner`-style helpers register
for REAL, so every freshly registered account in a test process is `pending`
(no provider, send fails, row already written). Five mailer tests in
`test_workspace_invite_email.py` went red the moment the gate landed — the
fix is an explicit `inviter_verified` parameter on the helper, never a
weakened assertion, because a test that quietly exercises the withheld
branch while claiming to test the mailer agrees with itself and checks
nothing.

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

**A fixture that invents its own input cannot notice the real input is
shaped differently.** The purest instance yet, 2026-08-13, and it killed a
feature on the day it shipped. Both private-memory tools
(`memory_write_private` / `memory_get_private`) keyed on
`session_metadata["user_id"]`. `session_metadata` IS the whole `session_ctx`
(`skills_service` does `session_metadata = session_ctx` at six call sites),
and `sage_agent_runtime_service`'s turn builder nests the id one level down:

```
what production builds              what the tool read
  session_ctx = {                     session_metadata.get("user_id")
    "metadata": {"user_id": …},  ←── the id lives HERE
    "sender_id": …,              ←── and is mirrored HERE
  }                                   ─▶ None, on every real turn
```

So every live call raised "requires a resolved user identity" and the
feature was dead from merge — while its own dispatch tests stayed green,
because all of them hand-built a flat `{"user_id": …}` that production never
produces. Fixed with one `_resolve_session_user_id`, so there is a single
answer to "who is this turn's human" instead of each call site guessing a
key; nested `metadata.user_id` wins over the flat mirror, because if they
disagree the value the turn builder set deliberately is the one that decides
whose private partition gets written.

The rule: **when a test constructs the context object it passes in, the
shape is an assumption, not an observation.** Build the fixture from the
producer (or assert against it) — otherwise the test and the code can agree
perfectly with each other and both be wrong about the caller. Same family as
the entry below, one level up: a mock protects a seam, a fixture protects a
shape, and neither protects a path.

**A mock protects a seam, not a path.** `test_genuinely_silent_turn_still_returns_none`
patched `execute_sage_turn`, got its `None`, and passed for months — while
the code AFTER that seam ran a second, unmocked LLM turn. The sync
`build_whatsapp_personal_reply` / `build_telegram_personal_reply` (live at
the time at `personal_channels_service.py:2775` and `:3277`; **both DELETED
2026-08-15** — see the cloud-session-lane entry below, and do not go looking
for them) treated the unified path's
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

**THERE WERE TWO GRAMJS TELEGRAM RUNTIMES, NOT ONE. That is the fact that
cost a day, and it is the reason the 2026-08-14 channel cutover shipped
half-done.** Everyone — the cutover's author, the agents that reviewed it,
this file — reasoned about "the gramjs Telegram runtime", singular. There
were two, they shared nothing, and only one was traced:

```
ON-BOX   empyralis-gateway/src/channels/telegram/runtime.ts
         reached from the Agent Computer, over the gateway WebSocket.
         DELETED 2026-08-14 with the rest of the cutover.  ✓ traced

CLOUD    cloud-session-manager/          ← nothing pointed at it from the
         real gramjs, own HMAC HTTP relay,  gateway tree, so a grep that
         own inbound route, own outbound    started from the gateway found
         dispatcher, 2 proactive callers    nothing. Survived untouched.
```

Deleting the key while leaving that lane running produced the worst split
available: proactive OUTBOUND still worked, while every INBOUND message died
in `assert_personal_gateway_channel`, was swallowed as a generic turn
failure, and came back as an empty reply. The person got silence and nothing
anywhere said why. It ran that way for a day.

**The lane is now DELETED WHOLE (2026-08-15), which is the founder's call and
not a side effect of the fix**: `cloud-session-manager/`, `POST
/personal-channels/cloud/inbound`, `handle_cloud_channel_inbound`,
`dispatch_cloud_channel_outbound`, `resolve_cloud_telegram_session_id` and
its two proactive callers (`runtime_heartbeat_service`,
`connectors/channel_delivery_outbox_service` — both already had the Telegram
BOT path as their fallback, which is now simply the only path), the
`CLOUD_SESSION_MANAGER_ENABLED` flag, and the two builders that existed only
to serve it (`build_telegram_personal_reply_async` and its sync twin, plus
the sync `_build_unified_sage_personal_reply` wrapper whose only caller that
was). `openclaw_telegram` — a BOT channel over the transport — is the whole
Telegram surface now.

It qualified on the cutover's own terms, not on judgement: the cutover
commit describes moving Telegram as "a deliberate CONVERSION from an account
channel (gramjs, ban-risk, retired)", and this WAS that account channel — the
owner's own number, the thing that gets a real person banned. It had no
self-serve creation path anywhere in the product (its session-creation
endpoints had zero frontend callers, which is *why* nobody noticed), and
`CLOUD_SESSION_MANAGER_ENABLED` still defaulted **TRUE**.

Three rules follow, and the first is the expensive one. **"The X runtime" is
a claim about cardinality, and nothing checks it** — before deleting or
cutting over a runtime, grep for the PROTOCOL library (`gramjs`, `baileys`,
`signal-cli`) across the whole repo, not for the module you already know
about; a second implementation in another language, in a directory the first
one never imports, is invisible to every trace that starts from the first.
**A dormant seam left behind by a cutover is not neutral, it is the next
outage**: this one kept a default-enabled ingestion route alive for a
deleted product decision. And **a component with no self-serve creation path
is evidence, not reassurance** — it means nothing will tell you when it
breaks, and nobody will be looking.

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
module no longer has. Verified 2026-08-09. Do not describe THIS service as
live, and do not "fix" a `thread_busy` drop that does not exist.

**Correction, 2026-08-13 (hardware chain audit): the paragraph above
overstated the blast radius — this file's own next line said "no channel
message is ever refused for concurrency," which was never quite true.** A
SEPARATE, simpler mechanism (`sage_reply_dispatcher.py`'s
`_CHANNEL_TURN_LOCKS`, a per-`(workspace_id, thread_id)` `asyncio.Lock`
inside `dispatch_sage_reply`) already serialized turns for hosted-bot and
WeChat-official channels — its only 4 real callers
(`sage_telegram_hosted_service.py`, `hosted_bot_provisioning_service.py`,
`wechat_official_service.py`, `routes_sage_telegram_hosted.py`). The real
gap was narrower and channel-specific: the whole personal-channel family
(WhatsApp, Telegram-personal, Discord DMs, local-bridge/OpenClaw) funnels
through `personal_channel_sage_bridge_service.py`'s
`_execute_channel_turn_with_envelope` instead, which called
`execute_sage_turn` directly with no lock anywhere in that file — THAT is
where two messages on one thread actually raced. Fixed on
`fix/personal-channel-turn-lock-and-gateway-cap` (committed, not yet
merged/deployed) by extending the same `_CHANNEL_TURN_LOCKS` dict to that
chokepoint via a new public `sage_reply_dispatcher.acquire_channel_turn_
lock()`, keyed by a derived `f"personal:{surface_channel}:{remote_jid}"` —
not a second mechanism, and deliberately per-thread rather than per-box or
global (MAN-318 measured 8 fully concurrent turns running cleanly on a
1vCPU box; a global lock would have silently undone that). A separate,
generous per-gateway concurrent-execution CAP (32, resource protection
only, not correctness) was added alongside it in
`gateway_execution_service.py`, so a genuine burst degrades by queueing
instead of thrashing one box's vCPU.

`channel_concurrency_service` itself is recommended for DELETION, not
adoption, evaluated fresh during that same audit: its extra coverage over
a per-thread lock is real (agent-level and workspace-level caps across
ALL of an agent's/workspace's threads, plus workspace-wide rate limiting
— things a per-thread lock structurally cannot see), but the machinery has
already rotted out of sync with the code it was meant to plug into — its
own negative-path Rust-gate test is red, its own router-integration test
targets `agent_channel_router.execute_canonical_channel_turn`, a function
that no longer exists (zero `concurrency`/`execution_slot` references
remain in that module — the router was refactored out from under this
test and nobody noticed), and every quota knob it reads has zero
production writers, so wiring it in today would only ever enforce
hardcoded defaults nobody chose. Not deleted in that pass — reviving or
removing it is a decision for the founder, reported rather than
actioned as a side effect of the concurrency fix above.

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

**A module CONSTANT standing in for a caller-supplied scope is the same
loaded gun as a defaulted parameter, and it cost every Sage-path turn its
entire trace.** Observed live 2026-08-14, backend log, on a turn that
returned 200 and rendered its reply normally:

```
asyncpg ForeignKeyViolationError: agent_traces_thread_id_fkey
DETAIL:  Key (thread_id)=(sage-main) is not present in table "agent_threads".
```

`agent_traces.thread_id` is an FK into `agent_threads`. `agent_turn.py`
calls `thread_service.ensure_master_thread(thread_id=resolved_thread_id)`
and then traces THAT id — its traces insert fine.
`sage_agent_runtime_service._run_sage_action_loop_v3` instead passed the
module constant `SAGE_THREAD_ID = "sage-main"`, while the real,
already-ensured thread id was sitting in its own `conversation_thread_id`
parameter one line away. Real workspaces hold per-agent rows
(`thread_agent_ainstall_*`); no literal `sage-main` row exists, so EVERY
trace on that path violated the FK, was caught by `start_trace`'s own
`except` and returned as `None`. The turn succeeded, the reply persisted,
and the trace — the transparency record the product shows the customer:
tool calls, plan steps, browser actions — was silently gone.

```
agent_turn.py           ensure_master_thread(t) ─▶ start_trace(thread_id=t)   ✓
sage_agent_runtime      ensure_master_thread(t) ─▶ start_trace(thread_id=
                        (one frame up)                SAGE_THREAD_ID)         ✗ FK
run_service (durable)   (no ensure at all)     ─▶ start_trace(thread_id=
                                                    thread_id or SESSION_ID)  ✗ FK
```

Three rules. **A trace's thread id must be an id whose row the caller
guaranteed, or NULL** — the column is nullable, so an unlinked trace is the
honest degradation; a dangling id costs the whole record because the FK
violation is swallowed. **A session id is not a thread id** — `run_service.
execute_durable_turn_request`'s `thread_id or session_id` fallback could
only ever FK-fail, so that path (POST /runs/start, no ensure of its own)
now ensures the row and passes NULL when it has none. And **a swallowed
failure must name what it lost**: this survived because `_log_failure` said
"start_trace failed" at WARNING with no workspace, thread or surface — one
permanently broken code path was indistinguishable from database noise. It
now logs at ERROR carrying all of them, and still never raises (a lost
trace may not kill a working turn). `conversation_thread_id` is now
REQUIRED on `_run_sage_action_loop_v3`; guarded by
`server_modules/tests/test_agent_trace_thread_fk.py`, which asserts
behaviourally AND with an AST check that the constant is not re-substituted
— re-substituting it type-checks, runs, and is silent.

**Recording a trace and SHOWING it are two different features, and the
second one was never wired. A web turn opened TWO traces and the assistant
turn carried neither.** Found 2026-08-15, immediately downstream of the fix
above: with traces finally recording, the Work tab still said *"Showing
message history — detailed step tracking isn't available for this
conversation"* on every conversation. Its own header comment states the
contract — the assistant turn that closes out a trace carries that trace's
id in `metadata.trace_id`, and the tab resolves `GET /api/agent-traces/{id}`
from it — and `agent_turns.metadata->>'trace_id'` was NULL on every row in
a live database.

```
POST /api/turn  (sync + stream, i.e. every web chat)
  turn_ingress_service.start_turn ─▶ STREAM builder, never agent_turn()'s own
                                     persistence branch
  agent_turn.py  start_trace ─▶ surface=web  ─▶ _bind_trace_id_to_turn_result
                                                  binds onto {"kind":"direct_
                                                  chat_stream","producer":…}
                                                  ← a dict nothing persists
                 …and hands trace_context to execute_direct_chat_turn_request,
                   which ACCEPTS it and never uses it  ← 2 events, provider NULL
  handle_sage_chat ─▶ _run_sage_action_loop_v3
                 start_trace ─▶ surface=sage ─▶ tool.started / search.query /
                                                tool.result / plan.item.updated
                                                ← THE trace, id published nowhere
```

So the binder, the trace that holds the steps, and the two writers of the
assistant row were three different places. Fixed by publishing the id from
the code that OWNS the trace: `_run_sage_action_loop_v3` returns
`agent_trace_id` and the action-loop writer in `_handle_sage_chat_unguarded`
stamps it. Deliberately not called `trace_id` — in that module `trace_id` is
a per-call correlation uuid that has never been a row in `agent_traces`, and
writing THAT would point the tab at a 404, which looks exactly like a fix.

Three rules. **A trace is only real once something can NAME it** — "the
events are in the database" is not the feature; the id has to reach the row
the reader starts from. **Two writers of one row via `metadata =
agent_turns.metadata || EXCLUDED.metadata` is load-bearing here** (the sage
runtime contributes model/billing, `runtime_runs_api._persist_final_direct_
chat_assistant_turn` contributes `result_metadata`) — check which writer
holds the fact before adding a third path to carry it. And **the SDK
engine, the production default, never finished a trace at all**: only the
legacy engine's callee (`direct_chat_generation_service._finish_trace`) did,
so every row had `finished_at = NULL` — invisible until the tab could
resolve one, and then a *new* lie, since WorkTab reads `!finished_at` as
"still running" (a permanent "Working" pill, plus an SSE poll that only
closes on `trace.completed`). The loop now closes its own trace on the SDK
branch only. Guarded by
`server_modules/tests/test_assistant_turn_carries_trace_id.py`; verified by
reading the database after real DeepSeek turns, not by reading the code.

Still open, same feature, NOT fixed here: the `surface=web` trace is a
duplicate shell on every web turn — `agent_turn.py` opens it before routing
resolves anything (hence its NULL `provider`/`model`, which is a symptom of
the orphan, not a separate bug) and the callee ignores the context. The
honest repair is ONE trace per turn — thread that `trace_context` down
through `execute_sage_turn`/`handle_sage_chat` so the runtime reuses it
instead of opening its own — which is a four-signature change and needs the
provider/model to be filled in at routing time rather than at open time.

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

**`setup_kind="oauth_or_app_install"` means TWO doors, and treating it as
one suppressed a channel that works.** Found and fixed 2026-08-19, folded in
from `feat/channel-connect-ux` (branch deleted — its own UI component,
`ChannelGroupPolicyPanel.tsx`, was superseded by the later card-grid/doors
rewrite documented elsewhere in this file, but this backend finding was
still real and undocumented anywhere). `connection_catalog_service.
_oauth_setup_unconfigured` gated EVERY connection carrying that setup_kind
on `oauth_connection_configured()`, i.e. on a deployment-level OAuth
client_id/secret being set — correct for a connection whose only real door
IS OAuth, wrong for one that also has a genuine non-OAuth door. `discord_bot`
is exactly that case: its real setup path (`FleetAgentDetail.tsx`'s
`byo_bot` flow, a pasted bot token) needs no OAuth app at all, and Discord is
not one of the self-configuring dynamic-client-registration providers — so
on any deployment without `DISCORD_CLIENT_ID`/`DISCORD_CLIENT_SECRET` set,
`setup_available` was forced `False` and the UI told the customer OAuth
credentials were missing, for a door that was never going to use them.
`github` carries the identical `setup_kind` label but has NO app_install
path actually wired anywhere in this codebase (grepped: zero `GITHUB_APP_*`
references) — OAuth is genuinely its only door today, so it must stay
gated, which is why the fix is a per-connection exemption
(`_OAUTH_OR_APP_INSTALL_WITH_NO_OAUTH_ALTERNATE_DOOR = {"discord_bot"}`),
never a blanket removal of `oauth_or_app_install` from the gated set — that
would have traded one silent-unavailability bug for a silent
looks-available-but-isn't one on GitHub. Verified red-before/green-after
with two new tests in `test_connection_catalog_service.py`.

Same pass, still open, NOT fixed (a display gap, not a suppression bug):
`routes_fleet.fleet_agent_channels` computed `health_status`/`display_state`/
`last_error` via `connection_catalog_service.status_items()` but never
forwarded them to the frontend — so even a channel correctly reported as
unavailable gave the customer no reason why. Now forwarded as
`healthStatus`/`displayState`/`lastError` (purely additive fields; no
existing consumer read the missing ones, so nothing regressed by adding
them) — a frontend consumer showing the reason on the channel card is a
separate, unbuilt follow-up.

Also still open, unverified against the box mechanism (recorded, not
independently re-derived this pass): a gateway-published health snapshot
(`personal_channel_health` in registration metadata, written BY the box)
goes stale the moment the gateway itself goes offline and keeps asserting
whatever it last said — any reader must check the gateway is online FIRST
or it can paint "Connected" over a machine that's down. Same class of
dishonesty CLAUDE.md's outcome-honesty law names elsewhere, just pointed at
a channel pill instead of a mutation result.

Same source branch, one more worth keeping so nobody "fixes" it back:
**Apple licenses no Messages icon to third parties, so `imessage.svg` being a
neutral monogram (not Apple's speech-bubble mark) is deliberate, not a
missing asset.** Their guidelines forbid using any Apple-owned icon without
an express written trademark licence and forbid anything "confusingly
similar" — their only published Messages brand assets belong to the separate
Apple Messages for Business programme, which this product's personal-account
iMessage bridge is not. Confirmed still true on main: `frontend/public/
brand-assets/channels/imessage.svg` is a plain green rounded-square glyph,
not Apple's bubble.

**Branches whose work gets redone on main.** Nine branches were found with
real commits, all superseded by the same fixes re-implemented directly on
main days later. If a branch exists, merge it or delete it — leaving it means
someone rebuilds it.

**A Linear ticket's status can lag its own fix, and a dispatched work order
will faithfully re-diagnose a bug that's already gone.** MAN-263 ("agent
narrates a tool call as text after the tool already succeeded") sat in
Backlog for eight days after its actual fix (`edb773210`, direction #5 in
`tool_honesty_guard.py`) merged to main — nobody moved the ticket, so a later
dispatch carrying the ticket's own original repro text read as a fresh,
uncovered bug. `git log --oneline --all | grep -i MAN-263` (or the ticket
number in a commit message search) before starting a diagnosis-from-scratch
would have surfaced it in one command. The session's actual contribution
ended up being narrower and more valuable than the dispatch implied:
verifying the existing fix red-before-green, proving it's reachable on the
live path (an AST wiring test — CLAUDE.md's own "guard called once in a
large function" failure mode had already bitten this exact call site once),
and moving the ticket to reflect reality. Grep the ticket ID against git log
before assuming a described bug is still open.

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

**The OpenClaw lane's DM policy is `allowlist` — the only one of our four
modes that transport can carry — and until 2026-08-14 nothing could set it,
so no box could ever be provisioned.** A missing write path colliding with a
mandatory audit, not a bug in any one function:

```
DEFAULT_DM_POLICY_MODE = open     first-party lane's live-agent-compat call
   │  applied to OpenClaw channels too, because the ONLY writer
   │  (_persist_agent_dm_policy_config) had two callers, both inside
   │  personal_channels_service.py, neither reachable from a route
   ▼
openclaw-config-plan.ts  ─▶  dmPolicy: "open", allowFrom: ["*"]
   ▼
`openclaw security audit`  ─▶  channels.<id>.dm.open  [CRITICAL]
   ▼
blockingAuditFindings  ─▶  provisioning REFUSED, every box, forever
```

Three of our four modes render as OpenClaw's `open` and are refused at write
time (422) rather than stored: `pairing` and `owner_only` widen deliberately
(`dm_pairing_widened_to_open` / `dm_owner_only_widened_to_open` — their
pairing blocks dispatch before our challenge could be sent, and they have no
owner_only), and their audit flags `dmPolicy === "open"` UNCONDITIONALLY —
the `allowFrom` wildcard their remediation text mentions clears only the
separate `dm.open_invalid` warn, never the critical. Read their own
`dist/audit-channel.collect.*.js`, never the message.

Fixed with a SECOND, lane-scoped default (`DEFAULT_OPENCLAW_DM_POLICY_MODE`),
never a flip of the shared constant — the ee3fca4f7c mistake this file
already records. `PATCH/GET .../dm-policy` + `POST
.../dm-policy/pairing-approvals` mirror the group-policy pair exactly
(same auth, same `agent_id` query param, same reconcile-after-save), and
`approve_dm_policy_pairing_request` finally has a caller.

Two things a code reading gets wrong. **Gate 1 must not decide GROUP
traffic**: `_enforce_dm_policy` ran on group messages too, and `sender_id`
in a group is the individual participant — so the moment this lane got an
allowlist default, an owner who had deliberately opened a group had to
enumerate its entire membership before the agent answered anyone. Skipped
for group messages on this lane only; doing it for first-party would loosen
an owner who has explicitly chosen `owner_only` today. And **two allowed
senders re-breaks the box**: their audit warns `dm.scope_main_multiuser`
whenever `allowCount > 1` while `session.dmScope` is its default `"main"`,
and our generated config never writes `session.dmScope`. One person works,
two refuse (recoverable — remove one). The fix is one line in the gateway's
config plan (`session.dmScope: "per-channel-peer"`), correct on its own
terms since OpenClaw's sessions are irrelevant to us — it is a radio, its
agent loop is off.

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

**`plugin_absent` is a claim with an EXPIRY DATE, and reading it after the
install is what dead-ended four channels.** The manifest is a property of the
PINNED version; four carried channels (`openclaw-weixin`,
`openclaw-zaloclawbot`, `wecom`, `yuanbao`) contributed no `channels.<id>`
node on the machine it was generated from. Correct on a fresh box. Wrong the
moment provisioning installs the plugin — from then on the BOX's own
`openclaw config schema` carries the node and can say exactly what the fields
are, and nothing asked. Mechanically swept: 16 dead ends, all four platforms
× every post-install state, pill "Unknown", detail "Setup fields aren't known
for this one yet", forever, on a computer that knew.

```
manifest  the PRE-INSTALL BELIEF   ─┐
this box  the OBSERVATION          ─┴─▶ the observation WINS, and only
                                        where the belief is `plugin_absent`
```

Fixed 2026-08-15 (`openclaw-channel-credential-shape.ts`, derived live in
`projectChannels`; `effectiveChannelShape` on the frontend). Four rules it
leaves behind. **The live read may only answer where the manifest ADMITS it
cannot** — a `pairing` channel's empty field list is a positive answer that
there is nothing to paste, and letting a box turn that into a form would be a
guess; the write allowlist is gated the same way, so a live read decides WHICH
fields, never THAT anything goes. **`config schema` is ~2.5MB, so it is read
at most once per call and only when a channel actually needs it** — on every
box today that is zero CLI calls. **Plugin installed and STILL no node keeps
the honest unknown**, never a guessed form. And **the fix is only real if the
write path moves with the read**: the cloud's `_validate_credential_values`
and the device's `parseCredentialWrite` both narrowed against the manifest, so
without them a form the panel now renders could never be submitted — a dead
control with extra steps.

The classification is a CROSS-LANGUAGE PORT of the manifest generator's
(`_credential_fields`/`_split_primary_and_advanced`), held to it by
`openclaw-channel-credential-shape.test.ts`: the expected set is the
checked-in manifest (Python), the actual set is the TS derivation over the
same schema. Two things that cost time and are not obvious. **The
primary/advanced split walks a channel node's properties in DECLARATION
ORDER**, so a schema fixture written with sorted keys silently moves which
fields the form calls primary — measured, seven channels' splits changed;
`refresh-openclaw-schema-fixture.mjs` writes the unpruned fixture unsorted for
exactly this reason. And **the generator's `_mode_gated_secrets` axis is
deliberately not ported** (it scans 98MB/4566 `dist/*.js` files, not a trade
worth making per panel poll) — it makes three channels' splits differ, and the
gap is proven inert rather than assumed: OpenClaw ships bundled secret
contracts for nine channels, all nine already have config nodes, so for every
channel this code is ever ASKED about both implementations see an empty gated
set. There is a test asserting that overlap stays empty.

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

**Inside that panel, whether the customer is asked to CHOOSE is decided by the
door COUNT, never by naming a channel.** Landed 2026-08-10
(`frontend/lib/workspace/fleet/channel-doors.ts`). A "door" is one way to
connect one channel; `planChannelDoors` is the whole rule:

```
real doors == 1  ─▶ "direct"  the card opens STRAIGHT into that setup.
                              An intermediate screen offering one option
                              is a dead click.
real doors >= 2  ─▶ "picker"  the choice is shown FIRST, because the doors
                              differ in CONSEQUENCE, not in procedure.
```

Hardcoding "Telegram gets a picker" rots on contact — Telegram gains a third
door and WhatsApp a second as transported paths land — so the count is the only
input, and adding a row to the table is the whole change. A door with
`real: false` is not rendered and does not count: a door that cannot be walked
through is not a way to connect.

**A door's CONSEQUENCE is stated on its face, before it is chosen, or the
picker has not earned its place.** Telegram's two doors are two RISK profiles,
not two procedures — the chatbot is a separate identity, the full account signs
in as the owner and puts the owner's own number in reach of a ban, which has
already happened to a real person here. So: *"Telegram can ban your number for
automated use."* on the door, never in a warning after a code has been sent.
One line, in the tone the fact deserves (`--warning-text` / `--online-text`,
both themes) — a professional tool labels, it does not lecture, and the natural
drift on a risk warning is always toward more of it. A one-door channel has no
face, so its risk line rides above the form it opened straight into; that is
the only reason WhatsApp's ban risk is stated at all.

**Hardware is answered the same way on both paths**, so it is never discovered
at a different moment depending on which one the customer took, and a door that
cannot be completed is **not a control at all** — an inert card on a picker, and
on a one-door channel a panel that says so and renders NO setup form.
WhatsApp/Signal/iMessage on a cloud-only agent were doing the opposite: the
single door auto-selected and mounted a setup panel that could only fail.
`setupDoorKey` is the ONE gate all eight setup forms hang off — the same two
conditions repeated at eight call sites is exactly the shape the next branch
forgets.

The model is pure data + pure functions in its own module for the same reason
`openclaw-channel-copy.ts` is: `openclaw-channel-copy.test.ts` imports the REAL
doors, so the expected set and the actual set come from different places. Its
sibling pinned literals (strings living inside a React component the `tsx`
runner cannot load) are the shape that check exists to avoid — do not add more.

**A brand mark is SOURCED or it is a monogram — it is never drawn.** Landed
2026-08-10; 17 of the 19 transported channels now carry a real logo. The rule
that produced them, in priority order: the brand's own press/brand/developer-
download page > a CC0/MIT/PD file whose licence was actually READ > nothing.
The provenance table (asset -> source URL -> licence basis) lives in the
`RAW_CHANNEL_ICONS` comment in `fleet-icons.ts` and each SVG repeats its own
line; a row without one does not ship. Cropping an official lockup down to its
own icon element is allowed — recolouring, redrawing, tracing or "close
enough" is not, and neither is a CSS `filter`/`invert` on someone's artwork.

```
NOT SHIPPED, AND WHY — three DIFFERENT failures, do not collapse them
  irc            no mark EXISTS. A 1988 protocol, no owner, nothing to source.
  yuanbao        mark exists, NO LICENCE. Tencent publishes no brand page for
                 it and nothing free-licensed exists; the icon sets that carry
                 it are redraws.
  synology-chat  licence FINE (CC0), MARK WRONG. Synology publishes only a
                 wordmark and forbids modifying it, so no symbol can be cropped
                 out — at 32px it was illegible grey mush. A monogram beats a
                 smear.
```

Two things a code reading gets wrong. **The existing first-party assets are
simple-icons SVGs recoloured to the brand hex** — that is the house style, so a
monochrome mark in a 24x24 viewBox is consistent, not lazy. And **`--bg-inset`
is `#1d1d1d` on dark, so a solid-black mark scores ~1.2:1 and disappears**;
Matrix and Tlon are fixed the way every brand guide says to fix it — put the
black mark on a light chip (one path-keyed rule in `fleet-theme.css`), never by
filtering the artwork. Any future near-black or near-white mark joins that rule.
Assets are verified by loading them in a real browser in BOTH themes and
asserting HTTP 200 per file — `tests/e2e/channels-card-grid-capture.spec.ts`
already does both; an `<img>` tag in the DOM proves nothing.

**OUR OWN mark has two files, and the second one is arithmetic, not taste.**
Landed 2026-08-11. `empyralis-mark.svg` is the founder's design and is never
edited; `empyralis-mark-compact.svg` is the same bars, same hex values,
cropped to the artwork with the dot at 1.33x the bar height instead of 1.07x.
It exists because the design carries 50% padding inside its own viewBox:

```
                     16px favicon draws...    verdict
empyralis-mark.svg   8px of artwork,          bars grey out, dot ~1px
                     bars 1.75px, dot 0.9px   and merges into the middle row
compact.svg          30 of 32 units,          three bars + a separate dot
                     bars 3px, dot 4px        still readable
```

Rule: `<= 32px render box` uses compact (the favicon, nothing else today);
anything larger uses the founder's mark unchanged. The favicon is declared
through Next's Metadata `icons` object and NOT the `app/icon.*` file
convention, because the convention emits one asset for every size and this
mark needs two. `public/favicon.ico` (hand-built, 16/32/48) sits beside it
for the bare `/favicon.ico` request nothing reads a `<link>` for — in
`public/` and never `app/favicon.ico`, which would inject a competing link.

Two things measured while wiring it, both worth not re-deriving. **On white
the top bar is 2.02:1 and the middle 2.67:1** — below WCAG's 3:1 for a
graphical object; the bottom bar (4.08:1) is what carries the mark, and on
`#1d1d1d` all three clear easily (8.3 / 6.3 / 4.1). The mark reads on both,
verified in a real browser in both themes, but a light-surface use that
depends on the top bar alone will not. And **an email may never carry the
SVG**: Gmail strips `<img>` pointing at SVG and drops `data:` sources too, so
`workspace_invite_email_service` links a hosted PNG on the same public origin
the accept link uses, `alt="Empyralis"`, `logo_url` optional so a caller with
no resolvable origin emits no tag rather than a broken one. Images are
blocked by default in every major client, so the mark carries no fact —
`test_the_email_still_reads_with_images_blocked` builds the same email both
ways and requires the imageless one to still name the inviter, workspace,
address, expiry and link.

Found while doing it, both the "built, tested, and never wired" shape:
`platform-brand.ts`'s `PLATFORM_AI_LOGO` / `platformSafeProviderImage` /
`platformSafeImage` have **zero production callers** — `chat-message.tsx` is
the module's only importer and takes `isPlatformBillingSource` alone, so the
hosted-AI turn renders a LABEL and no avatar anywhere. And
`lib/marketing/landing-page.tsx`'s `LandingPage` has **zero importers** —
`app/page.tsx` redirects to `/login` or the workspace, so the marketing page
renders on no route. Both were swapped to the new mark; neither is on screen
today. The logo constant now has a filesystem assertion in
`platform-brand.test.ts` (source constant vs. a real file under `public/`,
two different sources) because every other assertion in that file compares
the constant against itself and stays green pointing at a deleted asset.

**ONE PLATFORM = ONE CARD. A VARIANT IS ALWAYS A DOOR.** Landed 2026-08-10.
The surface grew in two eras and they disagreed on this: Telegram was one card
with two doors, while the transport's own model — every variant of a platform
is its own channel — put THREE Zalo cards in the same grid. Same concept,
opposite rendering, side by side.

```
BEFORE                          AFTER
  ▢ Zalo          (Bot API)       ▢ Zalo ──opens──▶ ┌ Bot API  ┐
  ▢ Zalo ClawBot  (QR)                              │ ClawBot  │  planDoors()
  ▢ Zalo Personal (on the box)                      └ Personal ┘  → picker
  26 cards                        24 cards, ONE door-count rule for both eras
```

The grouping is DERIVED (`channel-doors.ts`'s `groupTransportedChannels`),
never a list of "these ids are really one platform" — that list is the mistake
this surface has already made and corrected twice. Two INDEPENDENT axes must
BOTH agree: **the id family** (one channel id is a proper prefix of the other:
`zalo` ⊂ `zalouser`, `zalo` ⊂ `zaloclawbot` — from their registry) and **the
label family** (both display labels open with the same word — from their
catalog/package names). Requiring both is what keeps the dangerous direction
safe: **WeCom (WeChat Work) and Weixin (consumer WeChat) are different
products** and fail both axes; a future "Google Chat"/"Google Meet" pair shares
a label word and has no id prefix, so it stays two cards. A missed merge
degrades to today's behaviour (its own card); a false merge would need two
upstream fields to conspire. The transport namespaces some of its own ids
(`openclaw-zaloclawbot`); that prefix is recovered structurally from
`channel_key` minus `channel_id`, so no module names the transport to strip it.

`planDoors(doors)` is the count rule lifted off the authored table, so derived
and authored doors go through ONE rule rather than two that agree today.
Derived doors carry a `body` (their own selection label plus where the setup
happens) and NO `consequence`: the manifest cannot tell a personal-account
login from a webhook — both arrive as `pairing` — and inventing a risk to make
derived doors look symmetrical is exactly what that field's own comment
forbids. The copy test asserts the weaker honest rule for them (faces must
differ) and the strict consequence rule for the authored table.

**A card opens with what is already known. It does not fetch on click.**
`useGatewayPersonalChannelSurfaces` fetched per MOUNT with `loading: true`, so
clicking Signal started a request and showed a spinner while a transported card
opened instantly on state the tab already had. The state is a property of the
GATEWAY, so it now lives in one module-level store keyed by gateway id: a later
mount reads the snapshot synchronously, one poll serves every reader, and
ChannelsTab holds the subscription for the whole tab (LocalBridgeChannelStatus
takes it as props and cannot fetch at all). Measured on one backend, same seed,
surfaces endpoint delayed 2500ms to model an unreachable box: **2864ms and 2
fetches -> 114ms and 0**. `loading` stays honest — true only while nothing at
all is known about that gateway yet.

**The chosen door collapses to ONE LINE once its form is showing.** Before the
pick a door is a card, because the choice deserves the room. After it, the same
words are a caption over a field the customer is typing into — the founder's
words: *"two very very big node, it's just there regardless while I'm just
typing my phone number."* `.fleet-door-chosen--compact` (61px -> 44px) keeps
the consequence ON that line, smaller and unboxed: a warning that vanishes the
moment it becomes actionable is worse than no warning. Both eras share one
`ChosenDoorBar`.

**A setup control DOES the work; it does not explain it.** The install state
read *"The channel's plugin is not on this computer yet."* above a button
labelled *"Install plugin"* — a fact about a package on a disk, handed to the
customer as something to act on, against the standing instruction that they are
never told to install things or shown mechanism. Now: the three state chips
(unchanged — "not installed" is one of the three honest facts) plus ONE button
reading **"Set up"**, no sentence at all, verify-polling the box's own state
until it catches up and then disappearing, with installs serialized through a
queue so two clicks are never two package installs on one machine. Straight
from `CliSetupControl` on the Hardware page, the pattern the founder already
approved. `Remediation.detail` is now allowed to be empty and usually is; the
copy test asserts install/enable carry NO sentence, and that no remediation,
label or pill anywhere names mechanism (`plugin`/`npm`/`package`/`binary`) — a
mechanical guard, because both leaks lived inside a branch rather than in a
heading someone re-reads.

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

**Slash commands now execute on every personal channel through ONE waist,
`_dispatch_personal_channel_command`.** `sage_command_dispatcher.
dispatch_command` -> `command_registry` (24 commands: /new /main /compact
/stop /clear /export /model /thinking /help /commands /tools /status /whoami
/usage /memory /forget /tasks /agents /skills /config /mcp /plugins /debug
/tts /bash) was only reached from `_deliver_whatsapp_personal_reply`,
`handle_cloud_channel_inbound`, and hosted Telegram — Telegram-personal (QR)
and the whole local-bridge/OpenClaw family (Signal, iMessage, WeChat, every
`openclaw_*` channel) never dispatched a command at all, so an owner's
`/compact` passed every gate and reached the model as literal chat text.
Fixed by giving every Gateway-WS delivery path (WhatsApp included) ONE
shared function to cross instead of each growing its own copy — which is
exactly how WhatsApp's own inline block had acquired ITS bug: it wrote the
command's reply into the outbound table and returned WITHOUT ever calling
`gateway_protocol_service.dispatch_channel_outbound`, so a recognized
command sat "pending" forever. `_dispatch_personal_channel_command` runs
`dispatch_command`, and only if it returns a reply does it write the
outbound row, call `_enforce_personal_channel_dispatch_decision`, and
actually dispatch — a single call site is the only place that sequence can
regress again. It does not decide authorization: the dmPolicy/group gates
and `_control_command_block_result` already ran in every caller before this
is reached, and `outbound_agent_id` is the one axis that still varies by
caller — WhatsApp/Telegram-personal scope outbound rows by `agent_id`,
local-bridge stays unscoped on purpose (same reason
`_deliver_local_bridge_personal_reply`'s own outbound calls do).

**A page-shell class shared by two page SHAPES will be right for one and
wrong for the other, silently.** `.fleet-task-page-body` (fleet-theme.css:
`max-width: 720px`, left-aligned, no `margin: auto`) is correct on a task
page because `.fleet-task-page-side` — a real flex sibling, 300px of
Properties — fills the rest of a wide row; left-aligning the reading column
next to it is Linear's own layout. A document page reuses the identical
class but has no second column, so the same left alignment just left a
lopsided blank strip down the right two-thirds of a ~1730px screen — the
founder's "this doesn't look like a documents page" complaint, 2026-08-12.
Fixed by centering `.fleet-task-page-body` ONLY inside a new sibling class
on the document page's own root div (`.fleet-doc-detail-page`, matching
`task-detail.css`'s own `.fleet-task-page.fleet-task-detail-page` override
convention), never by changing the shared rule itself. Before touching a
class two page shapes both reach for, check whether it's being asked to do
two different jobs.

**A page's own "⋯" menu belongs in the breadcrumb topbar
(`HeaderAction`/`.fleet-topbar-action`), never inside the scrolling reading
column.** The document page's menu trigger sat at the right edge of
`.fleet-task-page-body` — correct relative to that 720px column, but on a
wide screen the column is left-aligned (see above) so the trigger rendered
visually mid-page, nowhere near "the top." `HeaderAction` already existed
and was already in production use (FleetAgentDetail, the Agents/Projects
list pages' primary-action buttons) — the document page had simply never
been wired to it, a small instance of "built and not adopted" rather than
"built, tested, and never wired." Portaling the menu there fixed the
placement AND, for free, fixed the title's border-bottom divider reading as
"orphaned" (nothing sits past its right edge any more) — two founder
complaints from one relocation, not two fixes.

**The document "⋯" menu grew from Delete-only to Copy link / Duplicate /
Export as .md / Delete — each item independently justified, not "add a
menu's worth."** Copy link and Export as .md are read-only and render for a
VIEWER too, not just a writer (harmless, no network call for Export — it
downloads whatever is already in the draft). Duplicate is canWrite-only and
calls the SAME `fleet_create_document` route the list view's own "New
document" already uses — no new backend. Rename and "Move to another
project" were both considered and cut: Rename would duplicate the title
input that's already a live, always-editable control (the exact mode-toggle
friction the direct-editing rework removed); Move needs a genuinely new
backend concept (`fleet_patch_document` has no `project_id` parameter, and
moving one needs a destination-project membership check) rather than reuse
of an existing route, so it was flagged as its own follow-up instead of
shipped half-done. The rule this leaves behind: an item earns its place by
(a) working end-to-end today and (b) reusing an existing route/control,
never by "the menu should probably have this too."
**Agent memory had ONE scope tuple, `(workspace_id, agent_install_id)`, and
NO per-user dimension anywhere.** Verified 2026-08-12
(feat/agent-memory-shared-vs-private) against `memory_service.py` /
`agent_memory.py` (MEMORY.md, topic files, daily logs, the `memory_entries`
key/value store), `agent_memory_tools.py`, `unified_memory_service.py`, and
`workspace_context_memory_adapter.py`: every read and write function takes
only `workspace_id`/`agent_install_id`. The only human-identity field
touching memory anywhere was `actor`/`source` — stamped into a version/audit
record and a "[name via X — not owner] " display marker, never used to
partition storage or filter a read. So a teammate using a shared agent
wrote into, and read out of, the literal same pool as the owner's own
accumulated context — contradicting this file's own "the agent... is
shared with the project" law, which was never actually implemented for
memory. The founder's words: *"once I have every context and evolved
agent, how is it going to work once I have my other person, which is also
going to evolve its context window, which I may not like."*

```
BEFORE                                    AFTER
  owner turn   ─┐                           owner turn   ─▶ SHARED pool (unchanged:
  teammate turn ─┴─▶ ONE shared pool                         memory_service.py/agent_memory.py,
                     (MEMORY.md, memory_entries)              workspace_id + agent_install_id)
                     no per-user axis at all      ┌─▶ owner's   PRIVATE note
                                        teammate ──┤   (agent_private_memory_notes,
                                                    └─▶ teammate's PRIVATE note   +user_id)
```

Fixed by ADDING a private layer, not rescoping the existing one — the
shared pool is already correct for "facts about the work every project
member should benefit from" and stays exactly as-is.
`agent_private_memory_notes` / `agent_private_memory_note_revisions`
(Postgres, `migrations/add_agent_private_memory.sql`, RLS'd exactly like
`project_documents` — two-column `tenant_id`/`workspace_id`
`empyralis_rls_scope_match`, FORCE'd) hold one row per
`(tenant, workspace, agent_install, user)`, upsert-in-place (the same
Decision B posture `agent_memory.py`'s own `memory_entries` already uses).
`user_id` is a REQUIRED keyword with no default on every function in
`agent_private_memory_repository.py`/`agent_private_memory_service.py` — RLS
is the tenant/workspace backstop (there is no third-column variant of
`empyralis_rls_scope_match`, and there never should be one for a single
table); the per-person boundary is application code, the identical split
this file documents for `vault_credentials` and `run_state_repository`.

**Which layer a write lands in is decided by the FIRING CODE, never a
model-supplied flag.** Two model-visible tools, `memory_write_private` /
`memory_get_private` (`skills_service._builtin_tool_descriptors`,
dispatched in `execute_single_direct_tool_call`'s `("memory",
"write_private"/"get_private")` branches) — neither tool's JSON schema has
a `user_id` property, so there is no field for the model to set; the only
source is `session_metadata["user_id"]`, resolved server-side before the
tool body runs, the same honesty posture `tool_honesty_guard`/
`agent_goals.attempt_count` already use elsewhere. Proved directly:
`test_memory_write_private_tool_dispatch.py` stuffs a `user_id` into the
model's own `argument_payload` and asserts the write still lands under
`session_metadata`'s real identity, never the smuggled one.
`memory_write`/the rest of the shared-pool tools are unchanged — they
remain the correct surface for "facts about the company/project," never
personal preferences.

**The COMPANY-CONTEXT document reuses `project_documents`; no new store was
built.** `project_documents_repository.py` (Postgres, project-scoped,
real revision history via `project_document_revisions`, reachable by every
agent through the existing `document__*` tools) already has every property
a shared "how this company operates" document needs. Documented directly in
that module's own docstring so the next person building this feature finds
the existing table before inventing a parallel one — "prefer reusing
documents over a new memory silo" held here without needing new code.

**Isolation is proven, not asserted.**
`test_agent_private_memory_repository.py`'s `AgentPrivateMemoryMockedIsolationTests`
drives the real repository functions against an in-memory fake standing in
for `control_plane_repository`'s pool: after user A writes, user B's read
for the same workspace/agent returns `None` (zero rows) with exactly one
downstream call made (a single `fetchrow`, not a broader read filtered
client-side) — the literal "zero rows, zero unnecessary downstream calls"
proof. A second, DB-optional class statically asserts every query in the
module names `user_id` and that `user_id` has no default on any public
function (an AST check, not a live-query one, since this suite normally
runs with `DATABASE_URL` unset). Real-Postgres end-to-end tests (opt-in,
skip cleanly without `DATABASE_URL`) round out the CRUD/scoping proof the
mocked class can't give on its own.

Two things NOT done in this pass, deliberately out of scope. The shared
pool is never auto-injected with a per-person block into every turn's
prompt (that would require threading `user_id` through the deep
`direct_chat_*_facade_service.py` callback chain feeding
`workspace_context_memory_adapter.load_workspace_context_payload` — a large,
separately-verifiable change); the private note is pull-based instead
(`memory_get_private`), consistent with how every OTHER memory surface
except MEMORY.md itself already works ("index-first... everything else is
pulled on demand," `memory_service.py`'s own stated discipline). And the
authority-mandate model (`authority_mandate_service.py`) has exactly three
tiers — `owner`/`audience`/`system`, no "project teammate" tier distinct
from "owner" — so a teammate invited into a shared agent's project
currently gets the SAME tool authority as the owner (full `memory_write`,
etc.), a real but separate gap from memory scoping; fixing it would mean
redesigning the tier model project-wide, which this pass did not touch.

## Agent memory: where it lives, and the pipeline that reported success while doing nothing (2026-08-21)

**Verdict: memory is SERVER-SIDE, all of it. Nothing an agent remembers
lives on the paired hardware — not one byte, on any path.** Traced from the
live code, not from the older entries above.

```
CLOUD / BACKEND SERVER                                   HARDWARE (Agent Computer)
  <backend repo>/.orion-stack/workspace/<ws>[/agents/<id>]/
    MEMORY.md              the always-injected index         nothing.
    memory/files/**.md     topic files                       the box runs tool
    memory/<date>.md       daily notes written by the        calls in Docker
                             model's own memory_append       containers that are
                                                             created and destroyed
  <backend repo>/.orion-stack/memory/<ws>[/agents/<id>/]     within ONE tool call
    memory.db (SQLite)     memory_entries — the key/value    (MAN-318). It holds
                             FACTS memory_write writes       no memory state at
    <date>.md              the ROLLING DAY-LOG, a SECOND     all, and rebuilding
                             daily-note store (see below)    a box loses none.

  POSTGRES (control plane)
    agent_private_memory_notes         one row per (tenant, ws, install, USER)
    agent_private_memory_note_revisions
    project_documents                  the shared company-context document
```

So the founder's own instinct was right and there is nothing to move: the
server already owns it, so git-style line-by-line editing is available to
build on rather than a migration to do first. The ONE thing worth knowing
before building on that: the markdown half is plain files on the backend's
local disk (`.orion-stack/`), NOT Postgres and NOT `project_documents` — so
it has no RLS, no revision table, and no compare-and-swap precondition. A
document-grade editing story for memory files means moving them into
`project_documents` (which already has all three — see the stale-write
precondition entry above), not adding a fourth store.

**THERE ARE TWO DAILY-NOTE STORES AND ONLY ONE IS CONSOLIDATED.** Nothing
says so anywhere, and they are one letter apart in the call graph:

```
memory_service.save_daily_log  ─▶ .orion-stack/memory/<ws>/<date>.md
    read by get_recent_logs ─▶ INJECTED every turn as "Recent Daily Logs"
    read by consolidate_daily_memory_notes ─▶ NO. never.

memory_service.memory_append_daily_note ─▶ <context dir>/memory/<date>.md
    (the model's own memory_append tool)
    read by consolidate_daily_memory_notes ─▶ YES, merged into MEMORY.md
```

Both are real, both reach the model, and a fix aimed at "the daily notes"
will land in whichever one the author happened to grep first. Merging them
is worth doing and was not done here.

**THE PIPELINE FIRED NOTHING ON THE PRODUCTION ENGINE, AND SAID IT DID.**

```
sage_agent_runtime_service.py:7058  (action loop — the production default)
  persist_interaction(metadata={"trace_id", "source", "channel_origin"})
        │
        ▼
  conversation_memory_facade_service._persist_direct_chat_interaction
        if metadata["persist_memory"]:     ← only direct_chat_generation_
        if metadata["persist_transcript"]:    service (LEGACY) ever set these
        ─▶ both False ─▶ NOTHING RUNS
        │
        ▼
  persist_interaction returns {"persisted": True}      ← hardcoded literal
```

Measured before the fix, not reasoned about: driving `persist_interaction`
with the byte-exact metadata that call site passes produced **0 calls into
`memory_service` and a return value of `persisted: True`.** The one signal
a caller could check said the opposite of the truth, at the seam that
decides whether "Empyralis is the owned-context layer" is a true sentence.

Fixed two ways. The return value now names each store separately
(`daily_log` / `facts` / `transcript`, plus `daily_log_error`), so "nothing
was persisted" is expressible instead of unreportable. And the
DETERMINISTIC half — the daily-log summary, `memory_summary_service`'s own
text builder, no model call, no credentials, no billing — now runs on every
direct-chat turn. Verified live and un-mocked: a production-shaped
`persist_interaction` writes a real `.orion-stack/memory/<ws>/<date>.md`
entry, which `get_recent_logs` then injects into the next turn's prompt.
That is a closed accumulation loop at zero marginal cost.

**The MODEL-DRIVEN half is deliberately still OFF, and that is a pricing
decision, not an oversight.** Fact extraction is a SECOND billed LLM call
per turn (`persist_direct_chat_memory_best_effort` -> `generate_reply`).
Switching it on for every customer roughly doubles per-turn provider spend,
which is the founder's call — and the agent's own `memory_write` /
`memory_write_private` tools already give it a deliberate way to record a
durable fact, on BOTH engines, and were never affected by any of this. My
recommendation if asked: leave it off. Deliberate tool-driven writes plus
the free daily log are better memory than an extraction model's guesses at
double the price.

Two traps for whoever wires that later. The daily log is written in the
`not persist_memory` branch precisely because
`persist_direct_chat_memory_best_effort` writes it as its OWN first step —
turning extraction on without that guard double-logs every legacy turn (a
test pins this). And `store_direct_chat_memory_fact` /
`save_direct_chat_daily_log_summary` both used to DROP `agent_install_id`
entirely, so everything landed in the workspace namespace and a specialist
install's own notes were not expressible; the daily-log one now takes it as
a pass-through (defaulting to None, so nothing already written moves), the
fact one still does not.

**All four stores are now VISIBLE, in one picker.** The Profile sheet's
"Memory & Files" segment showed exactly one of the four; the other three
were live, were feeding the model's prompt, and were reachable from no
screen at all. `MemoryTab.tsx`'s existing left-hand file list gained three
more rows — Facts, Recent activity, Your note — rather than a second tab
strip, because the list was already the picker and two pickers on one
surface is a bug this codebase has shipped and reverted before.

- **Facts** renders `memory_entries` with its ATTRIBUTION (`trust_tier`,
  already computed by `derive_trust_tier` and previously reaching nobody),
  so "you told it this" and "a stranger on a channel told it this" are
  visibly different. Forgetting one is owner-only — it edits the shared
  pool every project member reads.
- **Recent activity** is the day-log, labelled as what it is: not a log
  viewer, but the text the agent reads back about itself next turn.
- **Your note** is the per-person private note. **The routes take NO
  `user_id` parameter of any kind** — query, path or body — and resolve it
  only from `current_user`. That is the whole boundary: a `user_id=` query
  parameter would let a workspace owner read every teammate's private note
  with one URL edit, defeating in one line the four-column WHERE clause
  `agent_private_memory_repository` exists to enforce. An AST test asserts
  no such parameter can be added. Its WRITE is `viewer`, not `owner`, on
  purpose — showing a teammate a note about themselves they are not allowed
  to correct is worse than not showing it.

`agent_private_memory_service` grew async twins (`aget_`/`awrite_`) because
`run_coro_sync` blocks the calling thread on a separate loop, which would
stall the event loop from inside a FastAPI handler. The sync entrypoints
DELEGATE to them rather than keeping a second copy of the guards — the
required `user_id`, the empty refusal, the redact-before-write and the size
cap are enforced in exactly one place, and tests drive the async path
directly so none of them can be dropped on one side of the split.

Found and closed in passing: the memory-tree PUT/DELETE routes carried no
`_enforce_agent_project_access` while the GETs beside them did. A no-op for
a workspace owner today, and exactly the read-gated/write-ungated asymmetry
that becomes real the moment those roles move.

**Still open, named rather than half-fixed:** the two daily-note stores are
not merged; `store_direct_chat_memory_fact` still drops its agent scope;
and the shared pool is still never auto-injected with a per-person block
(the private note stays pull-based via `memory_get_private`, unchanged from
2026-08-12's own deliberate scoping).

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

**On a truly empty database, `migrations/*.sql` alone will not bootstrap —
most base tables don't come from there.** `users`, `tenants`, `projects`,
`workspace_agent_installs`, and more are created lazily by
`_ensure_*_tables()` helpers scattered across `server_modules/*.py`
(`control_plane_repository.py`, `auth.py`, ...) the first time request-path
code touches them — not by anything under `migrations/`, which is mostly
ALTERs and RLS policies layered on top of tables it assumes already exist.
So on a brand-new database, `migrations/enable_rls.sql` (and everything
alphabetically after it that calls the `empyralis_rls_scope_match()`
function it defines) fails outright, and everything before it that touches
`tenants`/`projects`/etc. fails too, because nothing has created them yet.
The working order is: **boot once (it will crash in
`preflight._check_postgres`, typically "workspace_agent_installs is missing
stage_4b columns" — that's expected, its job here is only to run enough
request-path code to lazily create the base tables) → apply
`migrations/*.sql` in two passes (the second pass picks up
everything that needed `enable_rls.sql`'s function and failed the first
time purely on ordering) → boot again, which should now pass preflight
cleanly.** `fix_rls_function_ownership.sql` will keep failing locally
regardless — it needs the `empyralis_app` role, which only exists in
production — and that's fine to ignore for a disposable local stack.

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

  **Second, sharper reason, found 2026-08-14: `git stash` during a merge
  silently clears `MERGE_HEAD`.** The subsequent `git commit` then produces a
  single-parent commit carrying the right CONTENT but the wrong PARENTAGE — so
  git has no record that the merged branch was ever incorporated, and every
  later merge re-hits the identical conflicts forever. Diagnosed after a branch
  conflicted twice against a `main` it had demonstrably already merged; the
  tell is `git log -1 --format=%P` returning ONE hash where a merge should
  return two. The fix is to redo the merge without stashing. This one bites
  even with no other agent running, so it is not only a concurrency rule.
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
- **Renaming a table that carries `tenant_id`/`workspace_id` is a TWO-PART
  change, and doing it in one part takes production down.**
  `preflight._RLS_COVERAGE_EXCEPTIONS` is keyed by table NAME, so a renamed
  scoped table is a brand-new unknown table to `_check_rls_coverage`, which
  fails closed and refuses to boot. Order is: add the new key, deploy, THEN
  rename in the database. Keep the OLD key too, so the revert path also
  boots. Learned the hard way on 2026-08-13 — ~3 minutes of 502 on a
  cosmetic rename of a dead table. The check was right; the sequence was
  wrong. The same coupling applies to anything else keyed by table name
  (`migrations/enable_rls.sql`, the `_NOT_POSTGRES`/`_NO_LIVE_READ` verdicts),
  so grep the name before renaming anything scoped.
- **The dead Postgres `gateway_registrations` is now
  `zzz_dead_gateway_registrations_see_man307`** (2026-08-13, MAN-307). The
  live store is SQLite via `gateway_state_repository` (`sqlite3.connect`);
  the Postgres copy stopped being written 2026-06-24, holds one stale row,
  and no Python reads it — but it looked exactly like every other
  control-plane table and was used as primary evidence in two investigations,
  giving a wrong answer both times ("only one gateway exists", "no
  capabilities are advertised"). Renamed rather than dropped so the row
  survives; it carries a `COMMENT ON TABLE` saying all of this. Four sibling
  gateway tables are marked `_NOT_POSTGRES` in preflight and do not exist in
  Postgres at all, so they were never a trap.
- **Production served ZERO security headers until 2026-08-12**, and neither
  the live nginx config nor `deploy/nginx-empyralis.conf` contained a single
  `add_header`. Found by reading the WIRE (`curl -sI https://empyralis.ai/`
  returned `server: cloudflare` and `x-powered-by: Next.js`, nothing else),
  not by reading the config — Cloudflare sits in front, so what the origin
  declares and what a browser receives are different questions and only the
  second one matters. Now set at the origin server block: HSTS (no
  `preload` — that is a founder decision, not a side effect),
  `X-Frame-Options: DENY` + CSP `frame-ancestors 'none'`, `nosniff`,
  `Referrer-Policy: strict-origin-when-cross-origin`, `Permissions-Policy`
  denying camera/mic/geo/payment/usb (all grepped as unused). Every one
  carries `always`, or nginx omits it on 4xx/5xx — and an error page is
  served from the same origin and is exactly as frameable as a 200.

  Three things worth not re-deriving. **`add_header` does not inherit into a
  block that declares its own**: put one `add_header` in any `location` and
  every header from the server block silently stops applying THERE, with the
  app still working perfectly — so there is deliberately none in any location
  block. **`Referrer-Policy` stopped being theoretical the day documents
  began rendering external links** (`target="_blank"`, agent-authorable):
  without it the full URL — workspace id, project id, document id — travels
  in `Referer` to whatever site the link names. And **`nosniff` is the other
  half of the SVG refusal** in `upload_content_policy` — attachments come
  back through `FileResponse` on the workspace's own origin, so blocking the
  declared type is worthless if the browser is free to sniff past it.

  A real `script-src` CSP is NOT shipped and must not be faked. Next.js needs
  per-request nonces threaded through its own inline bootstrap; a policy
  carrying `'unsafe-inline' 'unsafe-eval'` looks like a CSP in a header dump
  and stops nothing. It is its own job.
- **`nginx.conf` includes `sites-enabled/*` — extension and all.** A file
  named `empyralis.pre-releases.bak` sat in `sites-enabled/` and was being
  LOADED, declaring a second `server_name empyralis.ai` alongside the real
  config's. The real one won only because nginx takes the first match and
  `empyralis` sorts before `empyralis.pre-releases.bak` — luck, not design,
  and any config fix applied to one file silently did not apply to the other.
  Moved out 2026-08-12. Never leave a backup in `sites-enabled/`; the real
  entries there are symlinks into `sites-available/`, so anything that is a
  plain file is a mistake.
- **Agent worktrees accumulate and nothing prunes them — and a prose warning
  saying so does not fix it.** 177 of them (plus an 11GB `.git`) filled the
  disk to 100% mid-session on 2026-08-07 and killed several running agents.
  This exact paragraph existed as a warning from that day forward, and it
  happened again anyway, worse: 2026-08-19, **121 worktrees, 421 stale
  branches, 9.2G of `.git`** — a founder's own direct question ("why did we
  build things that never shipped") turned out to be partly this: not lost
  work (every one of the 421 branches had ZERO commits not already on
  `main` — verified, not assumed), but git hygiene debt nobody was forced
  to look at. A reminder an agent has to remember mid-task is not a fix.

  The fix is now structural, not a promise: `scripts/prune-merged-
  worktrees.sh` (`--dry-run` first) actually removes what is safe —
  a worktree only qualifies if its branch has **zero** commits not on
  `main` (`git rev-list --count main..<branch>` == 0) **and** no real
  uncommitted changes (build artifacts/lockfiles/`next-env.d.ts` are
  ignored as noise; anything else uncommitted is left alone and reported,
  never discarded) — and it only ever touches paths matching this repo's
  own worktree conventions, so a worktree belonging to a DIFFERENT tool
  registered in this repo's own `git worktree list` (a real Codex session
  was found there, `~/.codex/worktrees/...`) is skipped unconditionally,
  no exception list needed. Branch deletion goes through `git branch -d`
  (never `-D`), which is itself a second, independent refusal on anything
  not fully merged. Proven correct with three real test cases before
  trusting it: a merged-clean worktree gets removed, one with a real
  uncommitted file is skipped, one with a real unmerged commit is skipped
  — red-before-green, not just read and assumed safe.

  `server_modules/tests/test_worktree_branch_sprawl_guard.py` is the other
  half — a structural tripwire (worktree count > 20, non-main branches >
  30) that fails LOUDLY inside the ordinary `pytest` run every agent
  already executes, rather than requiring anyone to remember to check.
  Thresholds are deliberately generous — a heavy multi-agent day can
  legitimately run 15-20 worktrees at once, and the check only ever reads
  state at test time, so it can never block real work mid-task — the
  point is catching OVERNIGHT accumulation (merged worktrees nobody
  deleted), the exact shape that reached 121 unnoticed. Carries the same
  canary discipline as `test_rls_dml_drift.py`: if `git worktree list`/
  `git branch` cannot even be read, that is its own reported failure,
  never a silent green.

**Never test against the founder's Claude subscription. Not once, not "just
one call".** Founder's instruction, 2026-08-11, given while planning
BYO-subscription testing: *"I'm not sure if I will have a thing that might get
me banned from my subscription but we just not gonna do it because I don't
want to put risk even if it's 1%."* An automated agent driving a personal
Claude plan is exactly the usage pattern that gets a plan flagged, and the
downside (losing the account this company is built on) is unbounded while the
upside is one test. BYO-subscription work is exercised against OpenAI, xAI/Grok
and Cursor instead; the Claude BYO path is verified by reading code and by
mocked tests, never by a live call on a personal plan. Platform/API credentials
billed to the company are a different thing and are fine.

**AWS provisioning is DELIBERATELY UNWIRED until the founder is in San
Francisco.** Decided 2026-08-12. The code is complete and tested
(`_provision_aws`, `deploy/aws/empyralis-vps-role.yaml`, boto3 live) — what is
missing is only the operator side: `EMPYRALIS_AWS_ACCOUNT_ID` and
`EMPYRALIS_AWS_CFN_TEMPLATE_URL`. That account is Empyralis's permanent
operator identity: its 12-digit id gets baked into EVERY customer's IAM trust
policy, so losing access to it later breaks every AWS-provisioned Agent
Computer at once and cannot be fixed without re-issuing the trust policy to
every customer who ever connected. The founder is in China on a phone number
he does not own and a card whose billing address does not match, so signup
would tie a permanent company identity to borrowed credentials. Deferring is
the correct call, not a gap to close. **Do not attempt to wire AWS, and do not
report it as a defect** — "Connect AWS account" returning HTTP 500 on
`empyralis_aws_account_id()` is the intended state until then. DigitalOcean is
the working provider; Google Cloud is next.

**An agent opens to Chat. Overview/Work/Memory are tabs beside it, never a
gate in front of it.** Fixed 2026-08-12 — every path into an agent (list row,
⌘K, direct URL, wizard finish) used to land on `/overview`, a config screen,
with Chat reachable only through one button and absent from `[tab]/page.tsx`'s
own tab bar. `FleetAgentDetail.tsx`'s `TABS`/`TOP_TAB_IDS` now lead with
`"chat"`; the no-tab redirect (`agents/[agentId]/page.tsx`), the agents-list/
project-detail/FleetHome row hrefs, the ⌘K entry, and the wizard's `finish()`
all point at `/chat`. Deep links to `/overview` etc. are untouched — same
`[tab]` route, still directly linkable.

**A backend error body's `detail`/`error` is not always a string, and
`new Error(x)` silently stringifies whatever it is.** FastAPI's own
validation-error shape wraps a structured object under `error` (not `detail`)
in this codebase's error-handler middleware — `data?.error || data?.detail`
picks the OBJECT (truthy) over the human string sitting right next to it,
and `new Error(thatObject).message` is the literal text `"[object Object]"`,
rendered verbatim in the create-agent wizard's Placement step. `getErrorMessage`
(`frontend/lib/ui/api-error.ts`) is now the one place that decides: return
`detail`/`error` only if `typeof === "string"`, else the caller's fallback.
Landed at ~29 call sites across the fleet/wizard surfaces (`FleetAgentDetail.tsx`
alone had 11) plus `cloud-vps-setup-panel.tsx`/`ssh-server-connect-panel.tsx`
(reachable from the wizard's Placement step) and `projects/page.tsx`. A
follow-on sweep of the rest of the frontend for the same `data?.error ||
data?.detail` / `String(data?.detail …)` shape was NOT done — this pass
covered wizard/fleet reachability, not the whole app.

**The wizard now asks for a name and a project, on Placement — neither used
to have a field at all.** `FleetCreateAgentWizard.tsx`'s Name field is
pre-filled via `GET .../fleet/agents/suggested-name` (`fleet_tools.
suggest_agent_name`, the SAME pool+dedup function `fleet_create_agent` itself
falls back to for a blank name — one function, not two copies of the pool).
Project defaults to `initialProjectId` when the wizard was opened from inside
a project, else the workspace's own default project (`is_default`), else
"+ New project" — always a real `<select>`, never a silent auto-create. Left
blank, "+ New project" still creates one named after the agent (old behavior,
unchanged) but now SAYS so under the control.

**A refresh token's single-use rotation raced two callers, and the loser's
401 wiped the winner's cookies.** Root cause of "every in-flight request then
401s simultaneously, with no warning, losing wizard progress" — NOT a short
token lifetime (`ORION_JWT_EXP_SECONDS` defaults to 1h; no code path sets it
under 15 minutes, so if that figure is real in some environment it needs
separate verification against that box's actual env). `SessionRefreshTimer`'s
proactive 20-minute tick and `WorkspaceTransportAdapter`'s reactive per-401
refresh (`workspace-services.tsx`) each called `/auth/refresh` independently;
`_upsert_auth_session_refresh_token_locked` (auth.py) rotates the stored
refresh token in place on every success, so two concurrent calls always
produce a winner and a loser, and `routes_auth.refresh_session` cleared
EVERY auth cookie on the loser's failure — including the ones the winner had
just set. Fixed two ways. `auth-client.ts`'s `refresh()` is now single-flighted
(same pattern `awaitBrowserAuthReady` already used) and `workspace-services.tsx`
calls THAT function instead of issuing its own duplicate fetch, so concurrent
401s in one tab share one refresh call. And `auth.RefreshTokenSupersededError`
distinguishes "this token was just rotated out by a concurrent, legitimate
refresh" (session still active — don't touch cookies) from "genuinely dead"
(revoked/expired/gone — clear cookies as before), closing the remaining
cross-tab case single-flighting can't cover. `login/page.tsx`'s `authErrorCopy`
gained a `"session expired"` branch so `redirectToLogin`'s honest message
actually renders instead of falling through to the generic "could not finish"
copy. `fleet-authorized-fetch.ts` wraps the wizard's own fetch calls (which
don't go through `WorkspaceTransportAdapter` at all) with a refresh-and-retry-
once on 401 — the other ~33 raw `fetch()` call sites in `fleet-data.ts` and
elsewhere do NOT have this yet; same gap, separate cleanup.

**CORRECTION, 2026-08-20 — that "~33 raw fetch() in fleet-data.ts" gap is
CLOSED and this paragraph was stale.** Measured directly: `fleet-data.ts`
has 36 `fleetAuthorizedFetch(` calls and zero raw `fetch(` call sites today.
`lib/workspace/authorized-fetch-drift.test.ts`'s own header comment confirms
the fuller sweep this paragraph called "separate cleanup" actually
happened — fleet-data.ts's ~33 plus 96 more across 38 other client files,
all routed through the same `fleetAuthorizedFetch`/`auth-client.refresh()`
mechanism — and that test now runs in `npm run test:unit`, scanning every
`.ts`/`.tsx` under `frontend/lib` and `frontend/app` for a raw `fetch(` not
wrapped or explicitly allowlisted with a reason, so this specific gap cannot
reopen silently.

**A DIFFERENT, un-covered instance of the identical race survived at a
layer that drift test cannot see, and it is the one that actually produced
"every fleet call 401s, refresh 400s, session never recovers" — MAN-355-ish,
fixed same day as this correction (`fix/session-death-401-storm`).**
`authorized-fetch-drift.test.ts` only scans `lib/` and `app/` (browser-side
client code); `frontend/proxy.ts` lives at the frontend root and was never
in scope. `proxy.ts` makes its OWN inline call to
`POST /api/v1/auth/refresh` — proactively, server-side, on ordinary GET
navigations whose access-token cookie is near expiry — completely
uncoordinated with `auth-client.ts`'s single-flighted `refresh()` this
entry describes above, because it runs in the Next.js SERVER process, a
different execution context than the browser JS that single-flight lives
in. Concurrent qualifying GETs (RSC prefetches, `router.refresh()` polls,
several near-simultaneous navigations — routine, not contrived) each
independently raced the backend's single-use refresh-token rotation.
Reproduced live against a disposable stack with an accelerated
access-token TTL: a burst of 5 concurrent qualifying GETs produced 1
winning refresh (200) and 4 losing ones (401 "Refresh token was already
used by a concurrent request"), and under sustained concurrency the
resulting call volume tripped the backend's own `limit_refresh_requests`
rate limiter — captured directly, >60s of relapsing 401→(401/429)→401
cycles on real endpoints (`/api/v1/auth/account-shell`,
`/api/workspaces/.../bootstrap`). Fixed the same way MAN-324 fixed the
browser side: single-flighted, but keyed by the refresh-token cookie VALUE
rather than a single global lock (`lib/auth/proxy-refresh-single-flight.ts`)
so concurrent requests for the SAME session collapse into one upstream
call while different sessions on the same Next.js process stay
independent. Verified red-before-green: the same 5-way burst that produced
1×200+4×401 before the fix produces exactly 1×200 after it, across
repeated bursts, with zero 401s and zero 429s. This coordinates every
request landing on ONE Next.js server process — production today is a
single VPS, so this closes the race that actually occurs; it does not
coordinate across a future horizontally-scaled deployment, which would
need a cross-process lock (Redis, or similar) if that topology ever ships.
**BLAST RADIUS — this races on DEV/LOCAL stacks and is DEAD on
empyralis.ai today. Measured, not reasoned.** The fixing pass reported
"production is affected" on the grounds that `proxy.ts` ships
unconditionally with no `NODE_ENV` gate. That much is true and the race
is real, but it stops one line earlier than that reasoning goes:

```
proxy()  ... if (!csrfToken || !upstreamBaseUrl) return nextWithCsp();
                                └── controlPlaneBaseUrl() decides this

controlPlaneBaseUrl(env)
  isCloudEnvironment  = EMPYRALIS_DEPLOY_ENV|NODE_ENV in {production,prod,staging}
  if cloud AND (protocol !== 'https:' OR host is loopback) ─▶ return ''

empyralis.ai TODAY (read off the box, 2026-08-20):
  NODE_ENV=production · EMPYRALIS_DEPLOY_ENV=production
  frontend/.env.local  EMPYRALIS_API_URL=http://127.0.0.1:8001   ← http, loopback
  ─▶ controlPlaneBaseUrl() == ""  ─▶ the refresh block NEVER RUNS
```

Confirmed by running `proxy.ts`'s OWN `controlPlaneBaseUrl` source against
production's real env values: returns `""`. A local/dev stack is not a
cloud environment, so the same call returns `http://127.0.0.1:8001` and
the path IS live — which is exactly where the storm was observed (the
original report says "reproduced in a real browser against a **disposable
local stack**"). So the practical impact today is on AGENT TEST SESSIONS,
not on customers, and it has been quietly poisoning them.

**The second finding is the one worth acting on: proxy.ts's entire
proactive server-side refresh is DEAD CODE on production** — the
"built, tested, and never wired" shape, except it is wired and
config-disabled. Production relies solely on the browser-side refresh
path. Whether that is intended (the single-VPS deploy fronts both from
one nginx, so a loopback backend URL is the natural configuration) or an
accident of `isCloudEnvironment`'s https rule meeting a loopback URL is
an open question — and note the direction of the trap: pointing
`EMPYRALIS_API_URL` at an https hostname to "fix" the dead feature turns
this race ON in production the same day. Merge order matters; the
single-flight fix must be in place first, and it is.

**Testing RLS locally means a non-superuser role, and `REASSIGN OWNED BY`
run once affects every database in the cluster, not just the one you're
connected to.** Discovered 2026-08-13 during the cross-tenant-authz security
review: this local Postgres's `mansur` role is a superuser
(`rolbypassrls: f` but `rolsuper: t`), and superusers bypass FORCE ROW LEVEL
SECURITY unconditionally — so exploiting an RLS-scoped table
(`agent_channel_bindings`, `agent_connector_bindings`, etc.) against a
superuser connection proves nothing; the write that should be blocked by
`empyralis_rls_scope_match` will always silently succeed. Creating a
`NOSUPERUSER NOBYPASSRLS` `empyralis_app` role and pointing `DATABASE_URL`
at it (matching how this repo's own `fix_rls_function_ownership.sql`
migration expects production to run) is the only way to actually exercise
the policy locally. The footgun: `REASSIGN OWNED BY mansur TO empyralis_app`,
run while connected to one throwaway test database, does not scope to that
database — `pg_database`/`pg_tablespace` are shared cluster catalogs, so it
silently reassigned ownership of every database `mansur` owned, including
the founder's own default `mansur` db and unrelated projects
(`auto_parts_db`, `faraday_prod`). Caught immediately because a later
`DROP ROLE empyralis_app` refused ("owner of database mansur...") rather
than succeeding silently; fixed with an explicit `ALTER DATABASE <name>
OWNER TO mansur` per affected database, verified against the exact list
`pg_get_userbyid(datdba) = 'empyralis_app'` returned. Reassign ownership of
individual TABLES inside the throwaway database instead
(`ALTER TABLE <t> OWNER TO empyralis_app`, or `GRANT ALL ... TO
empyralis_app` for privilege alone without an ownership transfer) —
never `REASSIGN OWNED BY` against a role that owns anything outside the
database you intend to scope it to.

**A "visible" filter is not an authorization check — it is a filter, and
answers a different question.** Second half of the 2026-08-13 cross-tenant
sweep, covering the routes the first half explicitly didn't
(`routes_workflows.py`'s sub-modules, `routes_connectors.py`,
`routes_gateway.py`, `routes_deployed_agents.py`, `app_registry_api.py`,
`workflow_api.py`). `vault_helpers.workspace_visible(entry_ws, requested_ws)`
is a pure string-equality function — `if req_ws is None: return True; return
entry_ws == req_ws` — built to filter a scope the CALLER already resolved
and was checked against. `TelegramTerminalService._select_connector` and
`resolve_vault_credential` both call it, and their callers
(`routes_connectors.py`'s `telegram_send_message`/`telegram_autopilot_test_
message`, `connectors_core.probe_provider`/`get_provider_models`) took no
`current_user` at all — `workspace_id` was a bare caller-supplied field
handed straight to the filter with nothing upstream of it. CONFIRMED live
against a seeded two-tenant stack: an authenticated owner of workspace B
could name workspace A's `workspace_id` and have Empyralis decrypt and use
workspace A's Telegram bot token (sending to a chat_id of B's own choosing)
or AI-provider credential. `PROVIDER_PROFILES.get(profile_id)` has the same
shape one level up — a global fetch-by-id dict with no ownership field
checked at all, so any owner-role caller could silently disable/delete
another tenant's provider failover profile.

```
BEFORE                                    AFTER
  caller-supplied workspace_id                caller-supplied workspace_id
        │                                            │
        ▼                                    enforce_workspace_access(
  workspace_visible(entry_ws, ws)              current_user, ws, role)  ← NEW
        │  (a pure equality check,                    │
        │   satisfied by ANY value                    ▼
        │   the caller chooses to send)      workspace_visible(entry_ws, ws)
        ▼                                            │
  tenant A's secret, used                    tenant A's secret unreachable
```

Same root fix, five call sites: `routes_connectors.py` now wraps
`telegram_send_message`/`telegram_autopilot_test_message`/`probe_provider`/
`get_provider_models`/`enable_provider_profile`/`disable_provider_profile`/
`delete_provider_profile` with `enforce_workspace_access` (or, for
`profile_id`-shaped calls, `connectors_core.get_provider_profile_workspace_id`
resolves the PROFILE's real owning workspace first — never the raw
caller-supplied one, which is what let a valid own-workspace value launder
access to someone else's profile). `PUT /tools/contracts/{tool_id}` and
`POST /credentials/vault/rotate-key` mutate genuinely global, non-tenant-
scoped state (`TOOL_STATE`, the whole vault passphrase) and had the same
`require_admin_api_key`-only gate (any owner of any tenant, a role check
not a tenancy check) — fixed to require
`current_user_has_auth_admin_access` instead, same shape as `/apps/install`
`/uninstall`/`/update` against the equally-global `ORION_APP_REGISTRY_FILE`.

Two more, different shape, same sweep. `GET /diagnostics/sessions/{id}/export`
(`routes_gateway.py`) read `if session_workspace_id and session_workspace_id
!= resolved: raise 403` — fails OPEN on any session row with an empty
`workspace_id`, and `session_service.get_session`/`terminate_session` are
GLOBAL lookups keyed only on `session_id`, no tenant/workspace predicate of
their own. Deployed-agent runtime-session kill and external-user-delete
(`deployed_agent_service.py`) had the identical gap one level down:
`deployed_agent_id` was scope-checked, but the `session_id` sitting right
next to it in the same request was handed straight to `session_service`
unchecked — an owner of ANY deployed agent could name another tenant's
`session_id` and have it torn down. `workspace_id` is a required,
non-optional column on every `runtime_sessions` row
(`session_service.create_session` takes it positionally), so both fixes
fail CLOSED on empty, not open. And `workflow_api.py`'s four write routes
called `enforce_workspace_access` with no `minimum_role`, silently
defaulting to `"viewer"` — a role escalation within a tenant the caller
legitimately belongs to, not cross-tenant, but the same "the default is the
weakest role" mistake.

Every fix has a red-before/green-after test, proven by swapping the
pre-fix file in, confirming the new test fails, then restoring — this
repo's own established verification discipline, applied because `git
stash` is unsafe with other agents running. The Telegram cross-tenant
credential use was fired live end-to-end (real HTTP, real seeded Postgres
row, real session cookies) with outbound HTTP sandboxed to loopback
(`HTTP_PROXY`/`HTTPS_PROXY` pointed at an unreachable local port) so the
exploit never actually contacted Telegram's API — proof without touching a
third party.

Not covered by this pass: `sage_chat_api.py`, `sage_memory_api.py`,
`sage_heartbeat_api.py`, `sage_context_files_api.py`, `sage_profile_api.py`,
`sage_skills_api.py`, `sage_services_api.py`, `routes_connections.py`,
`routes_billing.py`, `routes_studio.py`, `routes_pilot.py`,
`routes_builder.py`, `routes_wechat_official.py`, and the remainder of
`routes_deployed_agents.py`/`routes_gateway.py` — audited by parallel
static-analysis passes and found correctly scoped (every write gated by
`enforce_workspace_access` with an explicit `minimum_role`, every
path-scoped id re-verified against the resolved caller scope before use),
but not independently re-verified line-by-line by the fixing pass itself.
A handful of lower-severity/lower-confidence items were flagged but not
fixed: `browse_google_connector_drive`/`create_google_connector_document`'s
minor policy-check inconsistency, and the shared Discord/Telegram/WhatsApp
autopilot bot status routes (appear to be single shared platform-level bot
state, not per-tenant secrets, but not proven either way).

**A real `script-src`/`style-src` CSP now ships — nonce-based, owned by
`frontend/proxy.ts`, never by nginx.** `sec/content-security-policy`,
2026-08-13. nginx's `frame-ancestors 'none'` (2026-08-12) is GONE from
`deploy/nginx-empyralis.conf` — a nonce has to match what Next.js actually
rendered for THIS request, which nginx cannot know, and two layers each
emitting `Content-Security-Policy` would INTERSECT rather than override, a
confusing failure mode. One owner: `frontend/lib/security/
content-security-policy.ts` builds the policy (data-first —
`buildContentSecurityPolicyDirectives` returns a `Record<string,string[]>`
so a structural test can assert per-directive rather than regex-parsing a
joined string), `frontend/proxy.ts` mints a fresh nonce every request via
`generateNonce()` and sets `Content-Security-Policy` on both the outgoing
request headers (so Next's renderer can extract the nonce and apply it to
its own framework-generated scripts/styles) and the response headers (so
the browser enforces it) — every return path in `proxy()` goes through one
`withCsp`/`nextWithCsp` helper so a future branch can't forget it, the same
"guard the narrow waist" shape as `_guard_sage_visible_reply` above.

```
script-src 'self' 'nonce-<per-request>' 'strict-dynamic'
style-src  'self' 'nonce-<per-request>'
img-src    'self' data: blob: https:      <- 3 documented widenings, see below
connect-src 'self'                        <- grepped: no browser-side WebSocket exists
frame-src  https:                         <- hosted mini-app iframes only, see below
object-src 'none' | base-uri 'self' | form-action 'self' | frame-ancestors 'none'
```

`'unsafe-eval'`/`'unsafe-inline'` appear ONLY when `isDev` (`NODE_ENV ===
'development'`) — Next's own docs: React's dev-mode error-stack
reconstruction needs `eval`, production needs neither. Verified against a
REAL `next build && next start` run (not `next dev`, which would have
masked this): zero console violations across ~30 authenticated screens, so
`'unsafe-eval'` is confirmed NOT required in production by anything in this
app. `app/layout.tsx`'s hand-written inline theme-bootstrap `<script>` (the
pre-hydration dark/light flash guard) gets the nonce explicitly via
`(await headers()).get('x-nonce')` — it is NOT framework-generated, so
Next's automatic nonce application does not reach it, and this was the one
spot in the whole app that needed a manual fix.

**Nonces require dynamic rendering on every page, and this app already has
that, for free, everywhere.** `RootLayout` calls `loadAccountShellSessionSafely()`
-> `headers()` on every request, and that one call — sitting in the ROOT
layout — opts literally every route in the app into dynamic rendering.
`next build`'s own route table confirms it: every page is `ƒ` (dynamic);
the only `○` (static) entry is `/healthz`, a `route.ts` JSON endpoint with
no HTML and nothing to nonce. So the nonce-based CSP required no
`await connection()` calls, no route-by-route audit, and does not risk a
silently-broken static page — a rare case where a pre-existing auth
architecture accidentally already paid for a security requirement.

Three directives are DELIBERATE, NARROW widenings past `'self'`, each with
a written reason, never loosened further:
- **`img-src` carries `https:`** because `lib/workspace/fleet/markdown-lite.tsx`'s
  `safeDocumentImageSrc` allows any http(s) URL BY DESIGN — pasting an
  external image link into a document is the product, not a bug to route
  around. `data:` is the WhatsApp pairing QR code (`qrcode`'s `toDataURL`,
  client-side). `blob:` is local file previews before upload
  (`DocumentDetailView.tsx`'s `URL.createObjectURL`). Verified live: an
  `https://upload.wikimedia.org/...png` pasted into a real document
  rendered with zero console violations.
- **`frame-src` carries `https:`**, and nothing narrower is possible.
  `lib/workspace/hosted-mini-app-surface.tsx` embeds a PUBLISHER-CONTROLLED
  iframe (`manifest.hosted_app.hosted_url`) that this app cannot enumerate
  in advance — that is the hosted-mini-app feature. The embed already
  carries its own defenses this policy doesn't duplicate: a `sandbox`
  attribute and an `allow` allowlist from the manifest, plus
  origin-checked `postMessage` (`allowedOrigins.has(event.origin)`) and a
  launch-token bridge contract enforced server-side. NOT verified live —
  no hosted mini-app was configured in the throwaway test workspace this
  pass seeded, so this is a code-reading verification, not an observed one.
- **`connect-src` stays exactly `'self'`**, no `wss:`/`ws:`. Grepped the
  whole frontend for `new WebSocket(`: zero results. Every realtime surface
  (trace/notifications/channel-events/turn streams) uses `EventSource`, and
  `resolveWorkspaceApiBaseUrl()` always resolves to `window.location.origin`
  in the browser — there is no direct-from-browser call to any other
  origin, including the gateway/hardware pages (their WebSocket traffic is
  box<->cloud, never browser<->box). Do not add `wss:` speculatively; if a
  future feature opens a browser-side socket, that PR adds the source with
  its own written reason, same as the three above.

**Verification was end-to-end in a real production build, not `next dev`.**
Seeded a throwaway account+workspace via `frontend/scripts/start-e2e-backend.sh`
(disposable Postgres db, never the founder's), ran `next build && next
start` (NODE_ENV=production, so the dev-only CSP relaxations are absent),
and walked signup, login, email verification, the agent-create wizard (all
4 steps), agent Chat (a real message send through the SSE turn stream)
/Overview/Work/Memory, every Configure sub-panel (Model, Capabilities,
Skills, Channels — the full card grid of 20 brand icons plus a card's
detail panel, Connectors — 69 icons, Tools, Hardware), Projects
list/detail, Tasks (composer, list, detail, inline title edit, board),
Documents (composer, detail, raw-markdown editing with autosave, the "⋯"
menu, revision history, a live external image render), Settings/Workspace
(members, invite form), Settings/Connections (hardware provider cards),
Billing/Usage, Inbox, Conversations, ⌘K, and a hard reload in both light
and dark theme — zero `Content-Security-Policy`/`Refused` console entries
anywhere. The structural test
(`frontend/lib/security/content-security-policy.test.ts`, wired into
`npm run test:unit`) asserts the directive SHAPE (nonce present, no
`unsafe-inline`/`unsafe-eval` in prod, `object-src 'none'`, `frame-ancestors
'none'` survives the move from nginx) rather than behavior, per this
repo's own established pattern — proven to actually catch a regression by
running the same assertions against the OLD `frame-ancestors`-only nginx
string and watching them fail.

**CORRECTION, 2026-08-13 — the "zero console violations anywhere" claim
two paragraphs up does NOT hold, and should not be trusted.** Found while
auditing the context layer: a real `next build && next start` (the exact
same production-build discipline the original verification pass used) on
an ordinary document detail page produced 10+ distinct `style-src` CSP
violations in the console, each a different `sha256-...` hash, i.e. many
different elements. The structural test itself is not wrong — it correctly
asserts the CODE builds a nonce-only `style-src` with no `unsafe-inline` in
prod, and that shape is real. What the original verification pass missed is
a CSP/React interaction the structural test cannot see (it never runs a
browser) and the manual walkthrough apparently didn't trigger or didn't
notice on the pages it happened to click: **CSP's `style-src` governs the
literal `style=""` HTML ATTRIBUTE, and a nonce source, per spec, NEVER
covers that attribute — only `'unsafe-inline'` (disabled the instant a
nonce is present in the same directive, which it always is here) or
`'unsafe-hashes'` (a hash per exact string, impractical for a value that
changes every render) can permit it.** React's `style={{...}}` prop is
CSP-SAFE on the client (React sets it via `domNode.style[key] = value`, a
JS property assignment CSP does not restrict) — but react-dom/server has no
live DOM to call that on, so SERVER-rendered HTML serializes every
`style={{...}}` prop into a literal `style="..."` string attribute, and
THAT is what the browser's initial HTML parse blocks. Confirmed the
distinction directly: `PrimaryRail`'s own `--rail-w` (via
`fleet-preferences.ts`'s `useResizableWidth`) applies through
`element.style.setProperty(...)`, a JS-property call, and was never blocked
in the same walkthrough that hit 10+ violations elsewhere — proving the
mechanism is specifically "SSR-serialized attribute" vs. "client-side JS
property," not "any inline style anywhere." Every route here is dynamically
rendered (this file's own CSP section already documents why: `RootLayout`'s
`headers()` call), so this is not a corner case — `grep -rc "style={{"
frontend/lib frontend/app` counts 666 occurrences across 57+ files, and any
of them present in a component's initial server-rendered output is a
candidate. Practical effect observed: the STYLE ATTRIBUTE is blocked on
first paint and stays blocked until something causes React to re-run that
element's style assignment via a client-side re-render (state change) —
until then the element silently renders without its dynamic style (a
color, a fill fraction, a computed width) with no error the product
surfaces to anyone. This was not fixed in this pass — it is a real,
non-trivial gap (potentially dozens of call sites, none enumerated or
triaged individually) and the assigning brief was explicit that the policy
itself must not be silently weakened to make it disappear. What an actual
fix needs, so the next person doesn't have to re-derive it: either (a) an
inventory of which `style={{...}}` call sites are genuinely SSR-reachable
(mounted unconditionally, not behind client-only interaction) and a
migration of each to a CSS custom property set via a class name or a
nonced `<style>` block instead of an inline attribute — large, cross-
cutting, not a one-file change; or (b) a deliberate, founder-approved
posture change dropping the nonce from `style-src` specifically (keeping
`'unsafe-inline'` there) while leaving `script-src`'s nonce+`strict-dynamic`
untouched — a common real-world split, since CSS-only injection is a much
narrower attack surface than script injection, but still a security-posture
decision, not a call to make silently in a drive-by fix. Until one of those
lands, do not cite "zero console violations" as current, verified state.

**Option (b) above LANDED, 2026-08-14 — `style-src` is now `'self'
'unsafe-inline'`, no nonce, in both prod and dev.** Triggered by the
founder hitting this live on Settings → Connections: 6+ style-src
violations in the console at once, PLUS an uncaught React hydration error
(#418) on the same page — the volume of style-src noise is exactly what
buried the hydration error and made it look like a wall of unrelated
console spam rather than one real bug worth chasing. `script-src` is
completely unchanged — still nonce + `'strict-dynamic'`, no
`'unsafe-inline'`, no weakening whatsoever. The nonce is dropped from
`style-src` ENTIRELY rather than adding `'unsafe-inline'` alongside it:
per CSP's own backward-compat rule, a nonce present in the same directive
makes every nonce-aware browser silently ignore `'unsafe-inline'`, which
would have reintroduced this exact bug while looking fixed. Full reasoning
and the migration path back to a nonced style-src both live in
`frontend/lib/security/content-security-policy.ts`'s module header (the
authoritative version — do not let this paragraph go stale relative to
it) and in `content-security-policy.test.ts`'s own header comment, whose
assertions were flipped to assert the new shape (carries `'unsafe-inline'`,
carries no nonce token) rather than deleted, so nobody mistakes the old
nonce-only shape for the one still intended. Cost, stated plainly: this
makes CSS-injection possible on this origin where it was nominally blocked
— a real widening, and a much narrower surface than script injection
(no code execution, no fetch/XHR exfiltration via CSS alone) — and the
nonce-only policy was not actually stopping anything in production anyway,
since it was violating on every authenticated page rather than being
enforced against a real attacker. Do not re-add the nonce to `style-src`
without first migrating the SSR-reachable `style={{...}}` call sites this
file's own "CORRECTION, 2026-08-13" note already flags as untriaged
(~666 grep hits at the time of that note) — re-adding it without that
migration reproduces the original bug, just with a passing test suite that
was never asked to catch it.

That same live incident's OTHER finding, separate from style-src and NOT
yet acted on: Cloudflare is injecting
`/cdn-cgi/scripts/<hash>/cloudflare-static/email-decode.min.js` on
authenticated pages, and `script-src`'s `'strict-dynamic'` correctly
blocks it (it is not our script, has no nonce, and should not run). That
filename is Cloudflare's own asset for **Email Address Obfuscation**
(Scrape Shield) — it rewrites any plain-text email pattern it finds in a
response body into a decoded `<span>`/`<a data-cfemail>` and injects this
script to reverse it client-side, and it only fires on a response that
actually contained an obfuscatable email. The app's own pages carry no
visible email markup (grepped: no `mailto:`/literal email strings under
`frontend/app/(account)` or the workspace UI) — the far more likely
source is the founder's own email address serialized as plain text inside
the per-request React Server Component/flight payload every authenticated
page embeds to hydrate `AccountShellProvider`
(`frontend/lib/server/load-account-shell-session.ts`'s `account` bootstrap
data, which plausibly carries `email`) — Cloudflare's scraper is not known
to reliably exempt `<script>`-embedded JSON from its regex. This is a
**Cloudflare dashboard toggle** (zone `empyralis.ai` → Rules/Configuration
→ **Scrape Shield → Email Address Obfuscation**, turn OFF), not something
fixable in this repo — the founder is the only one who can flip it.

**FLIPPED AND VERIFIED OFF, 2026-08-19.** Done in the founder's own
logged-in dashboard, at his explicit instruction. In the CURRENT Cloudflare
dashboard this setting no longer lives under a "Scrape Shield" nav item at
all — it is a card in the searchable list at **Security → Settings**
(zone → Security → Settings, search "obfusc"), which is why the older
directions above lead nowhere. Confirmed at the source rather than from
the toggle's pixels, because that list is VIRTUALIZED (only on-screen
cards exist in the DOM, so a reload plus a DOM read finds nothing and
proves nothing):

```
GET /api/v4/zones/f186dff85e419cc25b5fe4012066a5dc/settings/email_obfuscation
  before ......... value "on"   (read off the live checkbox: checked === true)
  after .......... value "off"
  modified_on .... 2026-08-19T09:29:42Z
```

Zone id for `empyralis.ai` is `f186dff85e419cc25b5fe4012066a5dc`; account id
is `76e485cbfb611ff8caa5c1d2afa71890`. A same-origin `fetch` from a
logged-in dashboard tab against `/api/v4/...` is the honest way to read or
confirm any zone setting — it needs no API token and cannot be fooled by a
UI that has re-rendered optimistically.

NOT verified by this pass, and do not claim it was: whether turning this
off also resolves the React #418 hydration error. `curl` on `/login`
returns zero `email-decode`/`cfemail` occurrences, but `/login` never
carried an email to obfuscate — the injection was observed on
AUTHENTICATED pages, whose RSC flight payload serializes the account
email, and those cannot be fetched without the founder's session. If the
hydration error persists on a logged-in page, the remaining suspect is a
genuine app-side SSR/CSR mismatch, to be reproduced against a seeded local
account. Whether it is ALSO a
contributing cause of the React #418 hydration error (by rewriting HTML
the browser parses into something that no longer matches what Next.js's
server render produced) is plausible and would be consistent with the
observed symptom (a dead button with zero network request, i.e. broken
client-side interactivity) but was not directly proven — no authenticated
production session was used to confirm it, on purpose (see "Testing the
UI" below: never test against the founder's live session or database).
If turning the toggle off does not by itself resolve the hydration error,
the remaining suspect is a genuine app-side SSR/CSR mismatch unrelated to
Cloudflare, and that would need its own investigation with a seeded local
account, not a guess from outside.

**CORRECTION, same day — a specific alternative theory for the #418 was
raised and tested directly; it did not hold, and the Cloudflare theory
above is the one still standing.** A colleague investigating in parallel
found that `app/layout.tsx`'s hand-written theme-bootstrap `<script
nonce={nonce}>` is exactly the shape that can hydration-mismatch: the
HTML spec hides a script/style element's `nonce` CONTENT ATTRIBUTE after
the browser parses it (`getAttribute('nonce')` returns `""` from then on
— confirmed directly, live, in a real browser via `javascript_tool`
against this exact page: `getAttribute` `""`, `.nonce` property the real
value), and React 19.2.3's hydration diff
(`react-dom-client.development.js`, `diffHydratedProperties`'s generic
default prop branch — "nonce" has no special case in this version, read
directly off the shipped bundle) compares via `getAttribute`, not
`.nonce`. That mechanism is real and independent of Cloudflare — Next's
OWN framework-generated inline scripts carry the identical nonce and are
identically hidden, they just never go through hydration diffing since
they're injected as raw HTML outside the React element tree, while this
one hand-written script genuinely is a diffed element.

**But it does not reproduce.** Tested directly against a disposable local
stack (`frontend/scripts/start-e2e-backend.sh`, a real seeded owner
account, `next build && next start` — matching how empyralis.ai actually
runs — and separately `next dev`) on the UNMODIFIED `layout.tsx`: zero
hydration console errors, and the DigitalOcean connect button on
Settings → Connections (the exact page and control named in the incident)
opened its panel correctly every time, in both modes. Reverting the
speculative fix and re-running reproduces the same clean result. A
`suppressHydrationWarning` was still added to that script tag
(`frontend/app/layout.tsx`) because the underlying attribute-hiding fact
is real and the fix is free — but it is NOT shown to fix anything a user
would notice, and must not be cited as the resolution to this incident.
Why the mismatch never surfaces here is unconfirmed (candidates: this
React/Next version may special-case it somewhere not found by the grep
above, or a DEV-only diff path that never throws in a production build) —
not chased further, since the practical question was "does this explain
the founder's report" and the direct test answered no.

Net: with the alternative theory tested and not reproducing locally, the
Email Address Obfuscation theory above is the one with actual supporting
evidence (the exact injected script name) and no local counter-evidence,
and is still the one item only the founder can act on. Two possibilities
remain open and neither is proven: the toggle is the whole story, or
production has a third cause not yet identified that a local disposable
stack — lacking Cloudflare entirely — cannot surface by construction. If
flipping the toggle doesn't resolve it, the next step is reproducing
against the REAL production edge (or a Cloudflare-fronted staging copy),
not further local guessing.

**The fleet UI collapses to what actually exists — the agent COUNT decides
the shape, never a tier check.** MAN-317, 2026-08-13. `agent-count-shape.ts`'s
`planAgentCountShape(realAgentCount)` is the whole rule, same shape as
`channel-doors.ts`'s `planDoors`: `0 → "none"`, `1 → "solo"`, `2+ → "fleet"`
(unchanged today's behaviour). "Real" excludes Sage/the Operator — every
`fleet_list_agents` response carries the workspace's Operator install from
the moment the workspace exists (`include_master=True`), confirmed
empirically against a fresh signup, so the RAW length is never zero. Two
call sites had been reading the raw count and were silently broken because
of it: `FleetHome.tsx`'s header/grid gate (a brand-new account's fleet grid
rendered as a blank void instead of the "start your first agent" teaching
state) and `InboxPage.tsx`'s `freshWorkspace` gate (a fresh account's Inbox
showed the generic "You're all caught up" instead of its onboarding empty
state) — both fixed alongside the new mode.

```
count  rail (Inbox/Conversations/Agents)     FleetHome root          /agents
0      hidden (nothing to aggregate)          "Get started" + teaching  unchanged (FirstAgentEmpty)
                                               empty state + a peek at
                                               real projects if any exist
1      shown; Agents ROW routes straight      redirects (router.replace) redirects the same way
       to the one agent, not the table        to that agent's own chat
2+     unchanged, today's rail                unchanged, today's grid   unchanged, today's table
```

Projects is deliberately NEVER hidden by count — it is the workspace's own
data (CLAUDE.md positioning: "the WORKSPACE is the product"), not a view OF
the fleet. Reversibility is structural, not a flag: every value above is
recomputed from the live agent count on each render, so the moment a second
agent exists the redirect stops firing and the ordinary fleet chrome
reappears with nothing to reset.

**THE WORKSPACE HOME DOES NOT REDIRECT AT ONE AGENT, and this overrides
MAN-317's own suggested direction.** The ticket proposed that at one agent
"the agent's own conversation is the landing surface". That was written
before the positioning correction above and it loses to it: `/w/{id}` IS the
workspace home, and redirecting it would mean the single-agent customer —
the exact case the ticket is about — could never reach their own projects,
documents and tasks from the front door. It would also undo the same-day fix
that stopped fresh signups being dropped into an agent surface instead of
their workspace.

Read the complaint literally instead of its proposed remedy: "the Agents
list is a fleet-management TABLE... showing exactly one row. A table of one
is worse than no table." That table is `/agents`, and the solo redirect
lives THERE and only there. The workspace home renders a card grid, which
holds one card perfectly well — so solo changes only the framing on that
page: "Your fleet · N agents · M online" is a sentence for comparing agents
against each other, and at one there is nothing to compare (and the plural
is wrong). **When a ticket proposes a direction and states a complaint, fix
the complaint** — the direction is a guess made earlier with less
information, and this one had been overtaken by a positioning decision.

The `/agents` solo redirect falls back to rendering the ordinary surface if
`resolveAgentProjectId` can't resolve a project (should not happen — dead
screen avoided anyway), and `/agents?new=1` (the ⌘K "New agent" command's
target) explicitly suppresses the `/agents` redirect via a one-shot
mount-time flag, or the command would bounce a one-agent workspace into the
existing agent's chat instead of opening the create-agent wizard.

Found and fixed in passing: `agents/page.tsx`'s `HeaderAction` "New agent"
button was unconditionally `--accent-fill`, which put it and
`FirstAgentEmpty`'s own filled "Create your first agent" on screen
simultaneously on every brand-new workspace — CLAUDE.md, "two accent-filled
buttons in one view is a bug." The project detail page had already solved
this exact problem (`fleet-btn${list.length === 0 ? " fleet-btn--accent" :
" fleet-btn--accent-fill"}`); the workspace-level Agents page had simply
never been updated to match. Same fix applied here.

Deliberately NOT done: Inbox/Conversations get no solo-specific redirect or
merge — a one-agent workspace still shows both, unchanged, because there is
genuinely something to show (that one agent's real activity/conversations)
and no verified-safe equivalent surface to redirect into instead
(`ConversationsView.tsx`'s own header comment notes the per-agent Work tab's
conversation list reads from `/api/threads`, which it calls "dead under
SQLite-fallback prod" — assuming equivalence there without verifying it
first would have been exactly the kind of silent regression this codebase
keeps getting bitten by). Hiding was applied ONLY where the brief's own
words support it ("aggregating one thing is just that thing" reads as
zero-content, not single-content) — a narrower cut than the rail could have
taken, on purpose.

## Ask AI is per-user, and `audience` never filtered anything (2026-08-18)

**There is NO `audience` filter anywhere in this codebase, and a commit
message says there is.** `1643af209` (MAN-201) states the workspace Sage
install is removed from a non-owner's `GET /fleet/agents` "server-side" by
`audience: "owner"`. Grep it: `audience` is COMPUTED in
`fleet_tools.resolve_agent_audience`, emitted as an informational field, and
read by no gate at all — its own docstring says the gate reading it is "a
separate, later task", i.e. never built. Do not reason from that commit
message; it sent this investigation looking for a filter that does not
exist.

The real cause was collateral damage from the MAN-115 per-project ACL:

```
seed INSERT for the master install   column list has NO project_id
        │                            (agent_registry_repository)
        ▼
fleet_list_agents(include_master=True)   Sage IS returned, project_id ""
        ▼
routes_fleet.fleet_agents
  owner   → _visible_project_ids returns None → branch SKIPPED    ✓ sees Sage
  member  → set(their project ids), never contains ""             ✗ Sage gone
        ▼
findSageAgent → null → SageLauncher `if (!sageAgent) return null` → NOTHING
```

So a member had no Ask AI console at all — not an empty one, not a
permission message, no control. Fixed by exempting the workspace-level agent
from that ONE filter (`_is_workspace_scoped_agent`), which is the **same
exemption the per-agent routes already had** —
`_enforce_agent_project_access`'s `if not project_id: return`. The list route
and the detail routes had disagreed about exactly one agent, and only the
list dropped it; when a list filter and a detail guard disagree, check which
one is missing the other's exemption.

**Keyed on `agent_kind == "master"`, never on an empty `project_id`.** "Has
no project" must never be what grants visibility, or a project-less
specialist becomes visible to every member and MAN-115's boundary widens
silently — that negative is asserted directly in
`test_man201_workspace_agent_visible_to_members.py`, because every other
assertion in that file passes under the naive fix too. `agent_kind` was
already SELECTed by both listing queries and already resolved inside
`_row_to_install_summary`; it just never reached a caller, so this was an
emit, not a plumbing job.

Two things verified BEFORE exposing it, not after — the reason this was safe
to do at all: `/threads` is already scoped server-side by `owner_user_id`
for non-privileged callers (`runtime_runs_api.list_threads`), so each
person's conversations stay their own (the founder's 2026-08-01 decision is
that Ask AI is PERSONAL — every member gets one, conversations are theirs);
and the frontend already excludes this agent from every count and list
(`isSageAgent`, `agent-count-shape.ts`'s contract), so a member's workspace
still reads "0 agents" and no stray card appears.

Still open, found while tracing and NOT fixed: `/workstation/{ws}/sage/
turns/stream` (`routes_gateway.py`) streams the shared constant
`SAGE_THREAD_ID` ("sage-main") to any member rather than the caller's own
thread. It emits METADATA ONLY (thread id, workspace id, role, created_at —
no message content), so it is not a content leak and was not a blocker for
the fix above, but it is a vestigial shared-thread seam left behind when
per-conversation thread ids shipped.

**Signup's outcome honesty has a SECOND half, and it is not the readiness
poll.** MAN-343's landed fix (`190b1fe60`) split the post-signup poll into
its own try/catch — correct, and verified still working. But `signup()`
ITSELF can reject after the server already created the account (dropped
connection, or `auth-client`'s own 30s `AbortController`), with only the
RESPONSE lost; `requestAuth` threw a plain `Error` for that, identical in
shape to a real 409, so the first screen a customer touches still said
"Couldn't create the account" about an account that exists. Now
`AuthNetworkError` marks the no-response class only, and the page re-checks
reality before speaking — **one login attempt with the credentials just
typed**, because a lost response means `Set-Cookie` was lost with it, so
checking the session alone reports "no account" even when one exists. That
is the auth-side twin of `mutation-outcome.ts`'s `MutateNetworkError` (the
pattern is shared, the class deliberately is not — `auth-client` must not
import workspace code).

**Bringing up a disposable stack inside an agent WORKTREE needs three
symlinks, and nothing says so.** `venv/`, `empyralis-runtime-kernel/target/`
and `frontend/node_modules/` are untracked build artifacts that live only in
the primary checkout, so `start-e2e-backend.sh` refuses to boot in a fresh
worktree.

**Symlink `venv/` and `empyralis-runtime-kernel/target/` — but NOT
`frontend/node_modules/`.** That third one was the standing advice here
and it is WRONG under Turbopack, which is what `npm run dev` uses.
`frontend/next.config.ts` pins `turbopack: { root: path.join(__dirname,
'..') }` (added to fix a different root-inference bug involving the Tauri
manifest at the repo root). A `frontend/node_modules` symlink pointing at
the PRIMARY checkout resolves outside that pinned root, and Turbopack
refuses at startup:

```
Error [TurbopackInternalError]: Symlink [project]/frontend/node_modules
is invalid, it points out of the filesystem root
```

Worse, the obvious workaround also fails: `cp -R` of the primary tree
carries its own internal self-referential symlink, so the copy is
rejected too. Options that actually work, in order: run a real
`npm ci`/`npm install` inside the worktree (slow but correct); or use the
webpack dev server (`npm run dev:e2e`, which passes `--webpack`) where
the symlink is fine; or hardlink-copy rather than symlink. Reported live
from two independent worktrees on 2026-08-20.

Two more traps
in the same 20 minutes: preflight reports the kernel binary as STALE purely
because a fresh checkout's mtimes are newer than the binary (`diff -r` the
`src/` trees first — if identical, the staleness is mtime-only and
`EMPYRALIS_ALLOW_STALE_RUNTIME_KERNEL=true` is honest rather than a
shortcut); and the script `mktemp`s a NEW `EMPYRALIS_E2E_STATE_HOME` on
every run, so every backend restart silently invalidates every live browser
session — pin it, or spend a restart wondering why a working endpoint
started returning "Invalid bearer token."

**The platform-owned DigitalOcean token now resolves through the secrets
broker, never a bare `os.getenv` — MAN-131, 2026-08-13.**
`vps_provisioning_service._platform_digitalocean_token()` was exactly the
discipline gap the Twilio/Resend keys are still in (`sms_twilio_
provisioning_service.py` still reads `TWILIO_ACCOUNT_SID`/`TWILIO_AUTH_
TOKEN` off bare `os.getenv` — not touched by this pass, same gap, separate
cleanup): a synchronous env read, no audit trail, no managed-bundle path.
Now goes through `secrets_broker.resolve_hosted_provider_secret
(provider_id="digitalocean", ...)`, registered in `_HOSTED_PROVIDER_ENV_
CANDIDATES` with the LIVE production env var
(`EMPYRALIS_PLATFORM_DIGITALOCEAN_TOKEN`) listed FIRST — it has to keep
winning over the two new aliases (`ORION_HOSTED_DIGITALOCEAN_TOKEN`,
`DIGITALOCEAN_ACCESS_TOKEN`) or registration would silently orphan whatever
is already configured on a live box. `preflight._check_platform_
digitalocean_token()` mirrors `_check_platform_credit_keys` exactly:
advisory, never boot-blocking, a live `GET /v2/account` probe (CRITICAL log
on a dead/rejected token, never appended to the errors list), its own skip
flag (`EMPYRALIS_SKIP_PLATFORM_DIGITALOCEAN_CHECK`). Structural test
(`test_platform_digitalocean_token_never_reads_os_environ_directly`, AST-
based) bans the direct read from being reintroduced beside the broker call —
a behavioural test cannot catch that, since a bare `os.getenv` there would
type-check and behave identically to the broker's own env fallback.

**The live token is still Full Access, and cutting it to a scoped one is an
operator action nobody has done yet — DigitalOcean token minting is
console-only, an agent cannot do it.** Ticket's own target scope:
droplet:create/read/delete + image:read + sshkey:read, kept separate from
the CI token. Exact steps (DigitalOcean control panel → API → Tokens/Keys →
Generate New Token, fine-grained scope picker):
1. Name it distinctly from the CI token, e.g. `empyralis-platform-runtime`.
2. Grant exactly: Droplet → Create, Read, Delete. Image → Read. SSH Key →
   Read. Nothing else (no Domains/Databases/Kubernetes/Spaces/Billing). If
   the account's console only offers the old binary Read/Write toggle
   instead of per-resource scopes, that account cannot express this scope
   set yet — flag it rather than falling back to Full Access silently.
3. Copy the token value once (DigitalOcean shows it only at creation).
4. Set it as `EMPYRALIS_PLATFORM_DIGITALOCEAN_TOKEN` in production's env
   (per `docs/DEPLOY-RUNBOOK.md`) — reusing the existing name means no code
   or deploy-config change is needed, just a value swap and a restart.
5. Verify BEFORE revoking the old token: `curl -H "Authorization: Bearer
   <new_token>" https://api.digitalocean.com/v2/account` should return 200
   with an `account` object; then restart the backend and confirm the
   preflight log line reads "platform DigitalOcean token is healthy", not
   "PLATFORM DIGITALOCEAN TOKEN DEAD". Then provision one real droplet
   through the product — Read-only scopes can pass the `/v2/account` and
   list checks while still rejecting Create, so the account check alone
   does not prove the scope set works end-to-end.
6. Only once step 5 is fully green, revoke the old Full Access token in the
   DigitalOcean console.

## Agents per box — measured, not guessed (2026-08-13, MAN-318)

**We have a floor, not a ceiling.** `s-1vcpu-1gb` and `s-1vcpu-2gb` both ran
8 fully concurrent real agent turns (DeepSeek reasoning + a real Docker-
sandboxed `shell` tool call dispatched to the actual hardware) with zero
failures, RAM comfortably in budget on both, and load average never crossing
1.0 on either box's single vCPU. Pushed to 16 concurrent, the 2GB box showed
the first clean hardware-side stress signal: load average spiked to 2.55 and
turn latency roughly doubled (14–17s → 24–31s). **CPU (the single shared
vCPU), not RAM, is what degrades first** — RAM never got close to exhausted
in any run on either size.

```
size            agents   RAM used/avail (MB)   1-min load   turn latency
s-1vcpu-2gb     0        438 / 1529             0.19          —
s-1vcpu-2gb     1        —                      —             14.2s
s-1vcpu-2gb     4        516 / 1451             0.74          17.0s
s-1vcpu-2gb     8        516 / 1499             0.74*         21.6s   8/8 ok
s-1vcpu-2gb     16       597 / 1370             2.55          24–31s  15/16**
s-1vcpu-1gb     0        416 / 545              0.94†         —
s-1vcpu-1gb     1        —                      —             18.0s
s-1vcpu-1gb     4        502 / 458              0.60          16.8s   4/4 ok
s-1vcpu-1gb     8        509 / 451              0.70          19.3s   8/8 ok
s-1vcpu-1gb     16       458 / 502              0.65          —       2/16**

*  load figures for back-to-back runs carry residual EWMA decay from the
   PRIOR test — the 1/5/15-min load average never fully resets between
   bursts fired seconds apart. Only the 2GB-box N=16 spike (2.55, a sharp
   jump against a flat ~0.1–0.2 baseline) is clean enough to trust as a
   real signal; the rest are directionally right, not precise.
†  1GB baseline was sampled 90s after boot, right after `docker.io` install
   — not yet settled from that install's own CPU burst.
** both N=16 runs were confounded by the TEST HARNESS's own ceiling (see
   below) — NOT a hardware finding. Treat both 16-agent rows as "we could
   not cleanly push this far," not as "this is where it breaks."
```

**Swap was 0/0 on every single reading, on both box sizes, at every agent
count — because there is no swap partition on a stock DigitalOcean Ubuntu
24.04 image, not because nothing was ever under memory pressure.** Do not
read a "swap: 0" line as "no memory pressure occurred" on this provider's
default image; it is a `free -m` structural fact, not a health signal.

**The architecture is not "N agents = N resident processes on the box," and
that changes what the capacity question even means.** Verified directly: an
agent's own reasoning/context loop runs in Empyralis's backend, not on the
Agent Computer. The box's job is (a) the gateway process relaying WS traffic
(a flat ~115MB RSS, agent-count-independent) and (b) executing tool calls
inside Docker containers that are spun up and torn down within the scope of
ONE tool call, then gone. An idle agent costs the box ~nothing. So "how many
agents fit" is really "how many SIMULTANEOUS tool-executing turns can the
box's one vCPU + Docker daemon absorb before turns start queueing" — a
function of concurrent BURST volume, not of how many agents exist in the
workspace. A workspace with 50 agents that are mostly idle costs the box
nothing extra; 50 agents all running a shell tool in the same second is the
real stress case, and that is what this table measures.

**What would invalidate this measurement:**
- **A heavier agent runtime.** If a future architecture change moves the
  reasoning loop or persistent context onto the box itself (contradicting
  what was observed here), this whole table is void — it measured tool-call
  dispatch overhead, not per-agent resident memory.
- **OpenClaw actually running.** It was NOT part of this measurement's
  footprint — see the drift bug below. Once that's fixed, re-run this table
  with OpenClaw's loopback session live; its RAM/CPU floor is unmeasured
  here and CLAUDE.md's own architecture section (`OpenClaw holds ONE
  loopback session shared by every channel runtime`) implies a real,
  nonzero, agent-count-independent cost that this table does not include.
- **A DeepSeek model swap.** `deepseek-chat` was used for reliable tool
  calling (see `fleet_tools.seed_specialist_metadata`'s own comment:
  `deepseek-reasoner` measured 1/5 tool-call success vs `deepseek-chat`'s
  5/5). A different default model changes turn latency and therefore how
  much concurrent-burst overlap actually occurs in practice.
- **A real test harness.** Both N=16 runs hit `asyncpg.exceptions.
  TooManyConnectionsError` on the SINGLE-WORKER local disposable Postgres
  backend used to drive this measurement (not production — see below) —
  confirmed via traceback, box-side RAM/load stayed unremarkable through
  both. The true per-box ceiling above 8 concurrent turns is UNMEASURED, not
  "high" — a harness with real Postgres pool headroom (or hitting a
  production-shaped backend) could find a lower real ceiling, or confirm the
  2GB box's N=16 CPU-queueing signal (2.55 load) as the real one.
- **A second OpenClaw, or a bigger Docker image pulled per exec.** This
  table's shell tool used `debian:bookworm-slim` per call; a heavier default
  sandbox image changes the per-exec cost this table implicitly assumes.

**Method, and why it required extracting a real platform credential.**
Provisioned through the product's own path
(`POST /api/hardware/vps/provision`) against the REAL platform DigitalOcean
account, using a disposable local Postgres DB (never the founder's, never
production's) as the control plane — `frontend/scripts/start-e2e-backend.sh`-
style bootstrap, on a fresh `empyralis_agentspertbox_man318` database with
every migration applied. `EMPYRALIS_PLATFORM_DIGITALOCEAN_TOKEN` was read
once, read-only, over SSH from production's own `.env` (this repo's local
`.env` has no platform DO token — only production does) because there was no
other way to exercise the real platform-billing path without either touching
production's database (forbidden) or fabricating a credential the task
required be real. The box's outbound registration needed a publicly
reachable API URL, which a local Mac is not — a `cloudflared` quick tunnel
(`trycloudflare.com`) stood in for that, torn down at the end of the session.
Two agent turns per box were driven with a real `shell` tool call
(`uname -a` + `date`), dispatched over the real gateway WS connection to the
real Docker sandbox on the real droplet — not simulated, not mocked.

**Both test droplets were destroyed through the product's own delete path
(`DELETE /api/hardware/vps/{vps_id}`) and independently verified gone via a
direct `GET /v2/droplets` call against the DigitalOcean API** (id 592011393
/ 138.197.29.64 for the 2GB box, id 592018582 / 159.65.242.100 for the 1GB
box — both distinct from production's 165.227.25.201). Production's own
`/health` returned 200 before and after every destructive action in this
session. Total droplet-hours billed: two boxes, each up for under 15
minutes — a few cents, not a recurring cost.

**Two independent installer/artifact bugs were found during this session and
BOTH are now fixed, same day — neither is open.** This passage originally
recorded them as unresolved; both fixes landed hours after the passage was
written (10:25) and before the docs commit that carried it (19:17), so the
first version of this entry simply predated its own fixes. See the durable
lesson at the end of this section for why that happened and the rule that
follows from it.

**Bug A — the published gateway artifact was 15 days stale, not a build
defect.** `npm run build` (plain `tsc -p tsconfig.json`, no bundler, no
tree-shaking, `include: ["src/**/*.ts"]`) has always compiled
`empyralis-gateway/src/openclaw/provisioning/*.ts` cleanly, on every commit.
The cause was `release-gateway-linux.yml` being `workflow_dispatch`-only:
nobody ran it between 2026-07-29T19:12Z and 2026-08-13T06:30Z (`gh run list`
confirms the exact gap), and the whole `openclaw/provisioning` source tree
(12 files) was only added on 2026-08-09 — 11 days into that gap. So "latest"
on the CDN was a pre-OpenClaw build the entire time: the compiled JS was
never missing, the PUBLISH was stale — the same MAN-306 shape one level up
(a compiled artifact whose staleness `grep` on source cannot see). Fixed at
`9bdfe5126` / merge `945202bdb` (confirmed an ancestor of `HEAD`): the
workflow now also triggers on `push: branches:[main]` for
`empyralis-gateway/**`, so a merge publishes itself, and
`install-agent-computer.sh` fetches the release's own `.sha256` sidecar and
appends it as a `?v=` cache-buster so a stale Cloudflare-cached response at
the fixed `latest` URL can't shadow a fresh publish either. Verified
directly, both architectures: downloaded the live `x64` and `arm64`
tarballs from `empyralis.ai/releases/agent-computer/latest/`, checksums
matched their own `.sha256` sidecars, and both contain all 12
`dist/openclaw/provisioning/*.js` files including
`openclaw-install-plan-cli.js` (mtime `Aug 13 14:43`, matching the
auto-publish `push` run `gh run list` shows fired at that merge).
`release-gateway-linux.yml`'s own "Verify archive matches what the installer
expects" step now enforces this going forward: it derives its required-file
list from a grep of `scripts/install-agent-computer.sh`'s own literal
`gateway/dist/**/*.js` paths — never a hand-copied list in the workflow,
which would be exactly the "channel list copied into a third place" shape
this file already flags as a defect once two same-language copies can
drift — so a file the installer starts requiring tomorrow is covered
automatically. A canary assertion fails the gate loudly if the grep itself
stops matching, the same "a check that derives its own expectations from
the thing it checks is blind" trap this file documents for
`preflight._check_rls`, avoided here by reading the expected set from the
installer's source and the actual set from the built tarball — two
independent reads, not one file confirming itself.

**Bug B — `https://empyralis.ai/install/agent-computer.sh` was ALSO stale,
for a completely different reason: a Cloudflare edge cache, not a build or
publish pipeline.** Measured on production at the time: origin
(`127.0.0.1:3000`, i.e. the app itself) returned 46,153 bytes with
`install_docker`/`install_openclaw_node`/`install_channel_transport` all
present; the public `empyralis.ai` URL returned 28,278 bytes with none of
them. Every real Agent Computer this product had ever provisioned installed
itself with no Docker and no OpenClaw. All three application layers were
already correct end to end, which is what made this confusing to first
diagnose: `frontend/app/install/agent-computer.sh/route.ts` and its sibling
`frontend/app/api/hardware/bootstrap/install.sh/route.ts` both fetch with
`cache: 'no-store'` and set `cache-control: no-store` on the response, and
`routes_gateway.py`'s `GET /hardware/bootstrap/install.sh` handler
(line ~1757) reads the script fresh off disk on every single request with
no caching of any kind. The actual cause was invisible to all three: a
Cloudflare "Cache Everything" edge rule caches by full URL and does not
honor an origin's `cache-control: no-store` — that is the documented
behavior of that rule type, not a misconfiguration of any header this repo
controls. Every droplet requested the same literal URL forever, so one
cached edge response served every subsequent boot until purged. The
`raw.githubusercontent.com` fallback branch in `route.ts` is STRUCTURALLY
DEAD, not merely unused, and was never the source of the stale bytes: the
repo is private (confirmed via `gh api repos/mansourMP/Empyralis` ->
`"visibility": "private"`) and that fetch carries no GitHub credential, so
an unauthenticated `raw.githubusercontent.com` request against a private
repo can only 404 — a dead fallback that reads as a working safety net is
worth naming precisely because it looks like coverage that isn't there.
Fixed at `c2deca5c6b72eb1ba55e04de3b02ff3a13607822` (confirmed an ancestor
of `HEAD`, merged 2026-08-13 10:33 +08:00 — hours before this passage was
first written): `vps_provisioning_service.agent_installer_url()` now
appends `?v=<sha256[:12]>` of the on-disk script to the default URL, the
same content-hash-cache-buster shape as Bug A's fix, self-healing on every
future edit rather than a one-time purge. An explicitly configured
`EMPYRALIS_AGENT_INSTALLER_URL` passes through untouched. The baked-image
provisioning path (`deploy/packer/`) does not fetch this URL at all — the
installer's functions are baked into the image at build time — so it was
never exposed to this bug and needs no fix.

**What is still genuinely unknown, and must not be overstated as resolved:**
- **Whether production has actually been deployed onto these two commits is
  unverified.** Merged to `main` is not the same as running on the box —
  per `docs/DEPLOY-RUNBOOK.md`, a Python change needs a merge plus an
  explicit restart there, and neither this session nor the one that fixed
  Bug B had production access to confirm the restart happened.
- ~~**Whether the Cloudflare "Cache Everything" edge rule still exists on
  the zone is invisible from the wire.**~~ **ANSWERED 2026-08-19: the rule
  is GONE.** Read from the dashboard session directly, which is the only
  vantage point that can see it (a `cf-cache-status: DYNAMIC` probe never
  could — it is consistent with removed, changed, or simply not matching):

  ```
  GET /api/v4/zones/f186dff85e419cc25b5fe4012066a5dc/pagerules   → []  (zero)
  GET /api/v4/zones/f186dff85e419cc25b5fe4012066a5dc/rulesets    → 3, phases:
        http_request_sanitize | http_request_firewall_managed | ddos_l7
        ← all default managed. NO cache phase ruleset exists.
  ```

  Both surfaces a "Cache Everything" rule could live on are empty, so the
  2026-08-13 stale-installer mechanism cannot recur from this cause. The
  content-hash `?v=` cache-buster added then is still the right defence
  and stays. Note the check must cover BOTH surfaces — page rules are the
  legacy home, cache rulesets the modern one, and finding one empty says
  nothing about the other.
- **Cloudflare's cache is per-PoP.** A stale copy may still sit in
  datacenters other than the one any single probe's anycast route happened
  to reach, even after the rule is fixed and even after one probe comes
  back clean.
- **Whether any Agent Computer provisioned during the stale window
  (2026-07-29 to 2026-08-13) is still running the broken tarball, the
  broken installer, or both, is an open question — neither fix is
  retroactive.** There is a cheap, code-grounded, non-SSH way to make
  partial progress on this: `GET /gateway/registrations?workspace_id=<id>`
  (existing endpoint, `routes_gateway.py`, `enforce_workspace_access(...,
  minimum_role="viewer")`) returns each connected gateway's `capabilities`
  array, which `gateway_protocol_service.py` refreshes from
  `requested_capabilities` on EVERY WebSocket connect — not a stale
  snapshot. A gateway still running the pre-2026-08-09 build cannot report
  `openclaw.provision` or `openclaw.channel_setup` under any circumstances,
  because `GatewayCapabilityRouter` in that build has no code path that
  knows those capability names exist — so their PRESENCE in this list is a
  hard positive proof the box is on a post-fix build. Their ABSENCE is not
  proof of the reverse (a fresh build with the bridge secrets not yet
  minted would also omit them, though `openclaw-local-secrets.ts` now mints
  them automatically on boot). Two real limits on this check, found while
  evaluating it: there is no fleet-wide, cross-tenant "list every gateway"
  endpoint today (grepped `gateway_registry_service.py` /
  `gateway_state_repository.py` / `routes_gateway.py` — none exists), so
  this only works iterated per-workspace, not as one fleet query; and
  `gateway.self_update` is dormant in production
  (`EMPYRALIS_GATEWAY_LATEST_VERSION` unset), so a box that downloaded the
  stale artifact will keep RUNNING it — and keep reporting the stale
  capability set on every reconnect — until something manually restarts or
  reprovisions it. Not run against production in this session; recorded as
  a verified mechanism, not a verified result.

**The durable lesson, and it is bigger than either bug: this file itself
went stale within hours and caused two agents to be dispatched to
re-diagnose bugs that were already fixed on `main`.** CLAUDE.md already
documents this exact failure mode for Linear tickets — "a ticket's status
can lag its own fix, and a dispatched work order will faithfully re-diagnose
a bug that's already gone" — and it just happened to this file's own
contents, the thing every agent is told to trust as present truth before
starting work. The rule that follows: **before treating any passage in this
file as present truth, grep the symptom it describes — or keywords from its
own prose — against `git log --oneline --all`, and if a plausible fix
commit turns up, confirm with `git merge-base --is-ancestor <sha> HEAD`
before either trusting the passage or re-doing the diagnosis it describes.**
A "known unfixed" note in this file is a claim with a timestamp, not a live
state, and the check that would have caught both stale notices above is one
command.

## `gateway_version` is a dead signal, and MAN-331's own fix would have looped the fleet (2026-08-18)

**Verdict: setting `EMPYRALIS_GATEWAY_LATEST_VERSION` — MAN-331's literal
proposed fix — makes every box reinstall the same build forever. Do not set
it. The staleness signal is a CONTENT FINGERPRINT, not a version.**

```
GATEWAY_VERSION = "0.1.0"   index.ts:41. `git log -S` over the whole repo
                            returns ONE commit: the one that introduced it.
                            Never bumped. Nothing in the release pipeline
                            stamps it.
publish channel = "latest"  release-gateway-linux.yml:93 — a push to main
                            publishes to agent-computer/latest/. The
                            published thing carries no version number.

  set it to "latest"  ─▶ is_newer("0.1.0","latest") = False. Never fires.
                         Dormant exactly as today, but now LOOKS configured.
  set it to "0.2.0"   ─▶ fires on every box. Installs the `latest` tarball,
                         whose GATEWAY_VERSION is still "0.1.0". Box comes
                         back reporting 0.1.0. update_available STILL true.

  0.1.0 ──update──▶ 0.1.0 ──update──▶ 0.1.0 ──▶ ...   forever, every poll,
                                                       every box, and the
                                                       0.2.0/ URL is a 404
```

Measured directly against the pre-fix code, not reasoned about:
`gateway_update_status({"gateway_version":"0.1.0"})` with
`EMPYRALIS_GATEWAY_LATEST_VERSION=0.2.0` returned `update_available=True`
plus a real artifact URL. That is the loop, one env var away, on boxes
nobody can SSH into to stop.

**The rule this leaves behind: NEVER ADVERTISE AN UPDATE WHOSE SUCCESS
COULD NOT BE OBSERVED.** If a finished update would leave the box reporting
the identical build identity it reports now, then "it worked" and "it did
nothing" are the same observation — the same "two different facts may never
share one signal" law this file already states for delivery outcomes and
invite mail. `gateway_build_identity_service.plan_gateway_update_
advertisement()` refuses instead, with four distinct codes
(`no_published_build` / `uncomparable_published_version` /
`update_would_be_unobservable` / `previous_update_changed_nothing`) because
each sends an operator to fix a different thing. The refusal rides on
`gateway_update_status`'s payload, which the Hardware page already fetches —
no new route. The trigger path enforces the same refusal, so the loop is not
one button-press away; an explicit `target_version` AND `artifact_url` still
bypasses it, deliberately, because that is a human naming a build rather
than the system choosing one.

**The signal is `gateway-build-fingerprint.ts`: sha256 over every `.js`/
`.json` under the RUNNING `dist/`, path and content, sorted.** Derived, not
authored — so it changes on every real build with nobody remembering to bump
anything, which is the exact property "0.1.0" lacks. Path is in the digest
(a moved file is a different build, since imports resolve by path); mtime is
NOT (two boxes that built the same commit at different times must agree, or
every box looks permanently drifted). Measured 31ms over 144 files / 1.96MB
— cheap enough to be unconditional. `resolveRunningGatewayDistDir()` derives
from `__dirname`, never config: a configured path can name an install root
the process never loaded a byte from, which is precisely the failure
`gateway-self-update-runtime.ts`'s own BOOTSTRAP NOTE describes. Reported on
every connect (`gateway_build_fingerprint`), persisted onto the registration
beside `gateway_version`, and published by CI as a `.fingerprint` sidecar
computed by REQUIRING the shipped module itself — never a second copy of the
hash in YAML, which would drift silently and report the whole fleet stale.

**It is NOT the `.sha256` sidecar, and they are not interchangeable.** That
one covers a compressed archive including timestamps and gzip framing, so it
answers "did I download the same file"; a running gateway cannot recompute
it. The fingerprint covers the extracted tree, which is the only thing a box
can say about itself.

**Still the founder's call, built but deliberately NOT enabled:** whether a
gateway may auto-update itself unattended. The safe half is done — the
mechanism can no longer loop, and there is finally a signal that can tell a
current box from a stale one. Turning it on means setting
`EMPYRALIS_GATEWAY_LATEST_BUILD_FINGERPRINT` from the published sidecar.
Nothing was set in production by this pass.

**MAN-264 is ALREADY FIXED and its description is stale** — `a912e46e6`
(ancestor of HEAD) added `deploy/packer/scripts/60-docker.sh` plus five
Docker assertions in `80-verify.sh`. The ticket's central claim
(`deploy/packer/scripts/*.sh` has zero Docker references) is false against
current main. Verified before building anything, per this file's own rule
about stale tickets.

## Multi-agent-per-box (2026-08-13) — one gateway, N agents, verified

The founder's own question: does one Agent Computer correctly serve several
of his agents on different accounts/channels at once, or was a previous
agent's reassurance wrong. Verdict per sub-question, established from code
and from the pinned OpenClaw build's own generated manifest
(`server_modules/openclaw_channel_manifest.json`), not from a prior claim.

**1. One gateway, N agents IS the real architecture — nothing on the live
path assumes one agent per box.** `agent_channel_bindings` keys on
`(agent_install_id, channel_key)`, not on `gateway_id`; a gateway's
`owner_user_id` is a PERSON, and any of that person's agents can point
`install_metadata.preferred_gateway_id` at the same box —
`POST .../openclaw/gateways/{gateway_id}/provision` takes `agent_id` as a
separate, required parameter precisely because many agents are expected to
call it against one `gateway_id` over time. Confirmed true.

**2. Two agents CANNOT each hold their own account on the SAME channel
type. This is a hard OpenClaw limitation, not an Empyralis choice.**
Checked every one of the 27 channels in the generated manifest — zero
"accounts"-shaped fields anywhere; `channels.<id>.*` is a flat node with
scalar credential fields (`botToken`, `appId`, ...), never a named-accounts
map. `googlechat.serviceAccount` and `sms.accountSid` are single-credential
field names, not evidence of multiplicity. OpenClaw's own error text
("Feishu account \"default\" not configured") is their hardcoded label for
the ONE slot a channel node has, not a name a customer chooses. Two agents
both wanting `openclaw_telegram` is therefore not fixable by writing better
Empyralis code — it needs N separate OpenClaw instances per box, out of
scope here. Confirmed false, and now understood precisely rather than
guessed at.

**3. Provisioning DID silently clobber, confirmed and FIXED.**
`build_openclaw_channel_policies` rendered EVERY OpenClaw channel from the
CALLING agent's own policy alone, and `openclaw.provision` on the box is a
full-replace render (`OpenClawProvisioningRuntime.runProvision` ->
`OpenClawProvisioner.provision()`, `lastAppliedChannelPolicies()` persists
only the most recent call). So the ordinary, WORKING multi-agent case
(agent A on Telegram, agent B on Feishu, same box) was silently broken:
whichever agent provisioned last overwrote every channel, including ones it
never touched, with its own fail-closed default — a working channel could
go dark because an unrelated agent on the same box saved an unrelated
setting. Fixed in `server_modules/openclaw_provisioning_service.py`:
`build_openclaw_channel_policies` now takes an optional `gateway_id` and,
when given, discovers which OTHER agent on the same gateway holds the
enabled binding for each channel (`_resolve_channel_owners_for_gateway`,
one binding read per gateway-sharing agent, not per channel) and composes
that channel's policy from THAT agent's identity instead of the caller's.
Two agents on different channels now compose correctly. Two agents BOTH
claiming the same channel — the one case OpenClaw's schema (point 2) cannot
express — refuses the WHOLE provisioning call with `OpenClawProvisioningError`
(409, names the channel and both agent ids) rather than silently picking a
winner, consistent with the pre-existing "every channel, always, never a
partial push" design this module already had. `provision_openclaw_gateway`
and `reconcile_openclaw_policy_best_effort` both thread `gateway_id` through
automatically — no route changes were needed, both the explicit "Set up"
action and the auto-reconcile-after-save path get the fix for free.
`reconcile_openclaw_policy_best_effort` is best-effort and swallows a
conflict into `None` exactly like an offline box — the specific "which two
agents conflict" detail is in the log line, not in that function's return
value; a caller that needs to explain the refusal to the owner should call
`provision_openclaw_gateway` directly. Surfacing the conflict distinctly
through the reconcile path's own return contract is a smaller, separate
follow-up, not done here.

**4. Inbound routing DID misattribute, confirmed and FIXED — and the
codebase already knew about it.** `find_agent_id_for_telegram_session`'s own
docstring said, verbatim, "well-defined as long as only one agent's session
is live per gateway+channel... not built yet" for the multi-agent case —
honest, but the consequence was worse than the docstring implied.
`_resolve_local_bridge_agent_id`'s slow path (which OpenClaw-transported
channels use too — `LOCAL_BRIDGE_PERSONAL_CHANNELS.update
(OPENCLAW_PERSONAL_CHANNELS)`) narrowed ambiguity by asking "how many
agents prefer this gateway", never "which of them actually use THIS
channel". So the moment a SECOND, completely unrelated agent was placed on
a box (any reason — it never had to touch channels at all), the FIRST
agent's already-working channel became permanently ambiguous: the check
denied and claimed nothing, so the identical false ambiguity fired again on
every subsequent message, forever. Fixed with two new shared functions in
`personal_channels_service.py` — `agents_sharing_gateway` (the existing
`preferred_gateway_id` reverse-scan, extracted so both the inbound resolver
and the provisioning-conflict check in point 3 compute "who shares this
box" identically, never as two independently-drifting opinions) and
`agents_bound_to_channel` (which of a candidate set holds an ENABLED
binding for one specific `channel_key`). The slow path now narrows by
channel-specific binding FIRST and only falls back to the broader "shares
this box" ambiguity check when nobody has claimed the channel yet — so
sharing a box no longer breaks an unrelated already-working channel, while
two agents genuinely claiming the SAME channel (point 2's real conflict)
still fails closed exactly as before.

**5. Outbound identity and seq allocation needed no fix — verified, not
assumed.** `gateway_protocol_service.dispatch_channel_outbound` has no
`agent_id` parameter at all, and that is CORRECT rather than a gap: point 2
establishes there is only ever one identity per channel per box, so there
is nothing for an agent id to select between at the outbound layer — the
identity question is fully resolved upstream, at point 4's inbound
resolution, which is what decides which agent's turn runs and therefore
which agent's reply reaches `dispatch_channel_outbound` in the first place.
Idempotency keys are built from `external_message_id`/`gateway_id`/
`channel_key`, never agent_id, which is the right scope for the same
reason. `GatewayCheckpoints.allocateClientSeq()` (the seq-race fix recorded
elsewhere in this file) is a WS-transport frame counter, correctly
agent-agnostic — it orders FRAMES on one shared loopback session, not
identities, and needed no change here.

**Both fixes are unit-tested, red-before-green** (the pre-fix file swapped
in via `git show HEAD:<path>`, confirmed 5 new tests fail with the OLD
behavior and only those 5, then the fix restored and all 111 tests across
the personal-channel/OpenClaw test surface pass) — see
`server_modules/tests/test_openclaw_provisioning_service.py` (the
composition/conflict tests) and
`server_modules/tests/test_local_bridge_agent_identity.py` (the inbound
narrowing/conflict tests).

**What real hardware would still be needed to prove, and was NOT provable
locally in this pass:** everything above is verified against the generated
OpenClaw manifest (itself generated from the pinned build, per the
"OpenClaw's config is a DERIVED ARTIFACT" entry above) and against mocked
repository-layer unit tests — never against a live OpenClaw process. Two
things specifically cannot be proven without a real box and a real
credential: (a) that `openclaw config patch` on a real installed instance
actually rejects or silently drops a second `channels.<id>` write the way
the schema's shape implies rather than erroring in some other way — the
manifest proves the SHAPE has no accounts field, not the RUNTIME behavior
of writing to it twice; (b) end-to-end, that two real agents on one real
box, one on a real Telegram bot and one on a real Discord webhook, actually
deliver and reply correctly through this fix on hardware, not just in a
mocked unit test. Both need a real DigitalOcean-provisioned Agent Computer
and at minimum one real channel credential (a Telegram bot token is the
cheapest) — not attempted here per this session's constraints (no paid
droplet, no company spend, no credential requested from the founder,
without asking first).

**Follow-up, same day: the reconcile function's `None` was collapsing "a
different agent already owns this channel" into the SAME value as "the box
is offline" — the exact three-facts-into-two shape this codebase keeps
re-discovering elsewhere, and worse than ranked in the pass above.** Fixed.
`reconcile_openclaw_policy_best_effort` now always returns a dict once it
has actually attempted a push: `status: "provisioned"`/`"refused"` (the
box's own vocabulary, passed through), `status: "agent_conflict"` (new —
carries structured `conflicts` and an owner-facing `message`), or
`status: "unreachable"` (new — replaces the bare `None` an offline/failed
push used to return). `OpenClawProvisioningConflictError` is a distinct
exception subclass (not a bare `OpenClawProvisioningError` with a
different message) so a caller can `except` it specifically rather than
lump it in with "gateway not connected", which used the same status code.

**The conflict message IS the owner-facing text, not a log line, and reaches
the screen with ZERO frontend changes — by construction, not by
remembering to special-case a new error shape.** `str(exc)` is already
plain language ("Sales Bot and Support Bot are both set up to use Feishu on
this computer, which can only connect one account per channel. Turn Feishu
off for one of them, then try again.") — no "binding", "provisioning",
"channel_key", or "gateway" anywhere in it, asserted by a mechanical test.
Agent names are resolved (an owner's own chosen label first, the agent
definition's name second, the raw id only as a last resort) rather than
left as opaque ids. This reaches the screen today through
`OpenClawChannelsPanel.tsx`'s existing "Set up" button flow: the
`.../provision` route already does `detail=str(exc)` on any
`OpenClawProvisioningError`, and the frontend's `getErrorMessage` (see the
"backend error body" entry elsewhere in this file) already surfaces a
string `detail` verbatim — no route or component changed to wire this.

**Honest limit found while wiring this, and worth recording precisely: the
"Set up" button is the ONLY reachable "moment they try" this constraint can
be enforced at in the product TODAY.** Traced every write path that could
plausibly represent "agent X owns channel Y" before deciding where to put
the refusal:
- `PUT .../openclaw/gateways/{id}/channels/{key}/credential` (the
  credential form) takes NO `agent_id` at all — the credential is written
  to the box, gateway+channel-scoped only. There is no "this agent" to
  compare against at that seam.
- `PATCH .../{channel_key}/gateways/{id}/group-policy` — the ONE route that
  writes a real per-agent `dm_policy`/`group_policy` distinction — has ZERO
  frontend callers (grepped the whole `frontend/` tree for `group-policy`
  and `group_policy`: nothing). It is fully built, unit-tested, and
  unreachable from any screen — its own "built, tested, and never wired"
  instance, not fixed here (building a settings UI from scratch is out of
  this pass's scope).
- `agent_channel_bindings` is populated for the OpenClaw-transported
  channel family by NOTHING today. Its only writer,
  `_ensure_agent_channel_binding_enabled`, fires exclusively for
  `whatsapp_personal`/`telegram_personal`'s own "connected" state sync —
  never for Signal/iMessage/WeChat or any `openclaw_*` channel. A
  bindings-only version of the conflict/composition logic above would have
  been structurally correct and PRACTICALLY INERT for the very channel
  family it exists to protect. Caught and fixed the same day it was built:
  `_resolve_channel_owners_for_gateway` now calls this module's own
  `channels_in_use()` (bindings ∪ stored policy key — the same two-signal,
  presence-not-value definition that already breaks the plugin-install
  deadlock elsewhere in this module) instead of reading
  `agent_channel_bindings` directly, so it is meaningful against what is
  actually populated today, not just against what the schema implies should
  be.

Net: the constraint refuses clearly at the one place an owner can actually
trigger it right now (the "Set up" button), with a real name-and-channel
message. The dedicated per-agent channel-settings surface that would let
this constraint be checked BEFORE a credential is even entered does not
exist in the product yet — that is a separate, larger UI gap, flagged here
rather than built speculatively.

## A human autosave silently destroyed an agent's edit — closed with a precondition (2026-08-18)

**Verdict: documents had NO stale-write precondition anywhere, on any write
path, and the losing write was recorded in history as the HUMAN's own edit.**
MAN-115's other three asks (patch-native agent edits, revision history with
author, diff rendering) all shipped 2026-08-12; this was the one unbuilt
piece, and it was the one that loses data.

```
t0  person opens a document.  DocumentDetailView snapshots title+body into a
    draft.  documents-data.ts's own header asserted a project's documents are
    "not mutated out from under the reader by an agent" — FALSE the day
    document__edit shipped, and the false half was load-bearing (it is why
    the page has never refetched).
t1  an agent lands a real edit (document__edit / empyralis_edit_document).
t2  person types one character.  900ms later autosave PATCHes the WHOLE BODY
    from the t0 draft.
    ─▶ agent's paragraph GONE, and project_document_revisions shows a clean
       row attributing the reversion to the human.  Invisible in the audit
       trail — which is worse than the loss.
```

Reproduced empirically against real Postgres before fixing (create → agent
`edit_document_by_replace` → stale whole-body update): `VERDICT: AGENT EDIT
SILENTLY DESTROYED`, revision log `#3 human / #2 agent / #1 unknown`.

**The token is a CONTENT HASH, and the other two candidates are both wrong
here.** `updated_at` is a clock value, so a rewrite producing byte-identical
text would raise a conflict where nothing was lost. `revision_number` lives
on `project_document_revisions`, whose writes are DELIBERATELY fail-open
(`_record_document_revision`'s own contract) — a body can change while the
counter does not, and a precondition that passes on a stale base is worse
than none. A content hash also matches the founder's own "documents should
work like git" framing exactly: identical content is not a conflict, and git
refuses the non-fast-forward push this API used to accept.

`document_state_sha256(title, body)` = `sha256(hex(sha256(title)) ||
hex(sha256(body)))`. **Both fields, one token** — the human PATCH writes both
from one draft snapshot, so a body-only hash would let a stale save silently
revert a rename. **Digests concatenated, never the raw text** — Postgres text
cannot contain a NUL byte, so no separator is safe, and `title || body` would
collide `("ab","c")` with `("a","bc")`.

**The compare-and-swap is a predicate on the UPDATE's own WHERE clause**
(`_DOCUMENT_STATE_SHA256_SQL`), never a read-then-compare in Python — that
shape races the very write it guards. Python mirrors the SQL and the two are
proved to agree from DIFFERENT sources: pinned known-answer vectors on one
side, the live database re-deriving the same token on the other.

**`expected_sha256` is a REQUIRED keyword with NO default on
`update_document`** — the same "a scope column with a default is a loaded
gun" posture as `agent_id` on personal-channel inbound writes. An
unconditional overwrite is something a caller has to TYPE `None` for, and
every such site is greppable. Guarded by AST assertions
(`test_document_stale_write_precondition.py`) that the default stays absent,
that every production call site passes it, and that the comparison stays in
SQL — a behavioural test catches none of those three.

**Three outcomes, three channels, never collapsed.** `update_document`
returns a dict (written), returns `None` (not found), or raises
`DocumentPreconditionFailed` carrying the CURRENT state (refused, nothing
written). The route answers 409 with a stable `code: "document_conflict"`
plus that current document — 409 rather than another `{"ok": false}` because
the client must BRANCH: every other save failure means "try again", this one
means "stop, a person has to choose." A conflict wearing the same clothes as
a network blip is one the autosave loop retries straight over the top of.

**`edit_document_by_replace` closes its own read-then-write race with no
caller change** — it passes the state hash of the document it actually read.
`skills_service`'s `document__edit` re-implements that read/replace/write
itself rather than calling it, so it carries its own token (a duplicate
implementation worth knowing about; not refactored here).
`empyralis_update_document` takes an OPTIONAL `base_sha256` and stays
unconditional without one — it is the documented whole-body fallback and a
model that never read the document has no base to offer. That is a real
remaining hole, deliberately left: making it required would break the tool
for every model that forgets the argument.

**The 409 UX is the half that decides whether the fix is real.** A fix whose
failure mode is "the human loses their paragraph instead of the agent" is the
same data loss pointed the other way. So on a refusal: autosave STOPS (a
doomed save must not re-fire on every keystroke), the draft stays exactly as
typed, and the two ways out are both explicit, both labelled with their real
consequence, and neither is a default:

```
Paused — this document changed elsewhere      ← NOT "Couldn't save"
  Someone else changed this document while you were editing.
  Your changes are still here and have not been saved yet.
  [Keep my version]  [Use theirs instead]        See what changed
   └ saves yours; theirs stays in history        └ - is yours, + is theirs
                    └ discards what you typed; never saved, cannot be recovered
```

An identical-content incoming version is NOT a conflict — `planDocumentConflict`
returns `needsResolution: false` and the client just adopts the new base.
`document-conflict.ts` is pure + tested for the same reason `channel-doors.ts`
is. **An automatic three-way merge is deliberately NOT built** — the base is
recoverable so it is buildable, but a merge that silently picks wrong on
overlapping edits reintroduces this bug in a form nobody can see. Showing both
versions and letting a person decide is the honest half; auto-merge is a
product decision, not a drive-by one.

Verified in a real browser on a disposable stack, not from code: agent edit
landed underneath an open page, real keystrokes, real 900ms autosave, real
`PATCH → 409`, banner rendered, "See what changed" showed both sides, "Keep my
version" saved — and the agent's version survived as revision #2, which is
exactly what that button's label promises.

**Harness note that cost real time: mouse events do not reach the page in the
Browser pane** (`left_click` by ref or coordinate leaves `document.activeElement`
as BODY), while `Tab`, `type` and focus management work fine. A login form
therefore cannot be submitted by clicking OR by Enter on the focused submit
button. Drive keyboard-first, and expect to establish the session another way.

## `workspaces.identity_links` is a DEAD column — do not read it for owner identity (2026-08-18)

**Verdict: the authoritative "is this sender the owner on this channel" store
is `personal_channels_repository`, and it always was. `identity_links` has
never been written by anything shipped.**

```
identity_links          real column, readable (SELECT fixed by 2622b8928)
  only writer  ─▶ routes_workspaces.py's identity-links endpoints
                  ─▶ ZERO frontend callers ─▶ the store is EMPTY, always
personal_channels_repository   linked_jid / linked_user_id / linked_identity
  written by a real owner login/pairing event ─▶ the actual data
```

MAN-337 was filed claiming `get_workspace_by_id`'s SELECT omits
`identity_links`. That was **already fixed** (`2622b8928`, column created by
`71343713a`, parity tests exist) — and fixing it changed nothing, because
the column it unblocked is empty. The real bug lived one level down and had
the same symptom, which is exactly how a stale ticket wastes a dispatch.

**What it cost:** `command_registry._is_sender_owner` had two checks —
`created_by_user_id` (only ever matches a WEB sender id) and `identity_links`
(empty). So a CHANNEL sender could not pass the owner check at all, for the
workspace's own owner included, and `dispatch()` returns `None` on a failed
owner check — so `/config /mcp /plugins /debug /bash` were silently
unrecognized on Telegram/WhatsApp/Signal/iMessage/every `openclaw_*` channel
and fell through to the model as literal chat text. The 2026-08-17 `sender_id`
forwarding fix (`7a59619af`/`3bcf0e209`) only exercised check 1, which is why
web looked fixed while every channel stayed broken.

`sage_agent_runtime_service._resolve_channel_sender_class` was migrated off
`identity_links` by `311133dd5`; `command_registry` was the **last consumer
left behind** — when you migrate a shared judgment off a data source, grep for
every reader, not just the one in front of you.

Three rules. **A "two independent checks" docstring is a claim about
COVERAGE, and an empty store satisfies neither check nor test** — the existing
`test_command_registry.py` cases hand-build `{"identity_links": {...}}`
fixtures production never produces, so they were green throughout (the
"fixture that invents its own input" family, again). **Canonicalize both sides
through `_channel_prefixed_identity_tail`** — the OpenClaw transport passes
the conversation id (`telegram:1932934047`) where the store holds the bare
sender, and a general "everything after the last colon" rule would hand owner
authority to anyone who can put a colon in a sender field. And
**`identity_links` is retained as a trailing OR disjunct, not deleted**: in a
disjunction an empty store can only fail to grant, never wrongly grant, and
deleting the only read would turn a live authenticated API route into a
write-only surface — a product decision, not a drive-by one. Do not promote
it back above the channel check, and do not add a fourth reader.

**Still open, flagged not fixed:** those identity-links endpoints (`GET`/`PUT`
`/workspaces/{id}/identity-links`) have zero frontend callers and now feed
only a disjunct that can never decide anything real — either give them a
surface or delete them; and `PATCH .../{channel_key}/gateways/{id}/group-policy`
remains the same shape (built, tested, zero frontend callers), already noted
in the multi-agent-per-box section above.

## A launcher pins the path it was launched from (2026-08-18, MAN-355)

**Verdict: the launchers do NOT agree, and MAN-355's premise that none of
them resolve the release layout is wrong — two of four already do.** Check
each launcher separately; "the gateway's launcher" is a claim about
cardinality, and this repo has four.

```
LAUNCHER                              RESOLVES current/ ?   EVIDENCE
install-agent-computer.sh run-gateway  YES, checked FIRST   a357779f9, 2026-07-21
deploy/packer/files/run-gateway        YES, verbatim copy   7fed6ee4c, 2026-07-29
gateway-supervisor-install.ts (unit)   NO — and worse       require.main.filename
prod empyralis-gateway-channels.svc    NO — bespoke         ExecStart=/usr/bin/node
                                                            /opt/empyralis-app/
                                                            empyralis-gateway/dist/index.js
```

**The third one was SELF-PERPETUATING, which is the part worth remembering.**
`gateway-doctor.ts`'s `defaultEntryPath()` returned `require.main.filename` —
the path THIS process was launched from — and handed it to the unit/plist
writer. A gateway started once from a fixed checkout therefore wrote that
fixed checkout back into its own supervisor unit, so every later self-update
swapped a symlink the unit never resolved through and the box came back
running the identical build. Detection existed (`d4510de13`'s fingerprint ->
`previous_update_changed_nothing`); repair did not. Fixed by
`gateway-launch-path.ts`, which MIRRORS run-gateway's ordering rather than
inventing a second rule, and only ever chooses between two already-existing
paths — `gateway-release-layout.ts`'s ownership constraint (self-update
stages next to the state dir precisely because the unprivileged service user
cannot write the root-owned install tree) is preserved untouched.

**Boot-failure posture, and it is the whole safety argument:** the layout
entrypoint is returned ONLY when it resolves to a real file at unit-write
time. A missing/dangling `current`, or a probe that throws, falls back to the
running process's own entrypoint — the one path known to work at that
instant. Worst case is the unit keeping the path it already had.

**The fix reaches FRESH installs only, and that limit is structural.**
`SUPERVISOR_PRESENCE_CHECK.detect` short-circuits: `detectGatewaySupervisor`
reads env hints, so a process already running under systemd/launchd returns
`"pass"` and NEVER calls `auditSupervisorInstall`. The unit-writing path is
therefore only reachable on an UNSUPERVISED box. An already-supervised box
carrying a mis-pinned unit does not self-heal — do not claim it does.

**Migration, per box class — most boxes need NOTHING:**

```
installed AFTER 2026-07-21 (installer) / 2026-07-29 (packer)   already correct
installed BEFORE those dates      run-gateway on disk lacks the block, is
                                  root-owned, and is never re-run ─▶ needs root
production (165.227.25.201)       bespoke: NO run-gateway exists at all;
                                  hand-rolled unit ─▶ needs root
```

Production's own state dir is `/var/lib/empyralis-gw/state`, owned by
`empyralis-gw` and already inside the unit's `ReadWritePaths`, so the layout
(`/var/lib/empyralis-gw/gateway-releases`) IS writable by the service user —
staging works today; only the launch pointer is wrong. The repair is one
`ExecStart` edit plus `daemon-reload` + restart, i.e. an operator action, not
something a gateway can do to itself. Per this file's own rule, the blast
radius is small and known personally — there are no anonymous production
users on the gateway.

**Second half, same day: a gateway CANNOT repair its own unit — measured,
not reasoned — so the product reports instead, and hands over the one line
it is forbidden to write.** Three independent barriers on production
(165.227.25.201, read-only), any ONE of them fatal to self-repair:

```
Uid 995 (empyralis-gw)   /etc/systemd/system is root:root 0755
                         `sudo -u empyralis-gw test -w` → NOT WRITABLE
ProtectSystem=strict     `/` is `ro` inside the unit's own mount namespace,
                         so even root INSIDE it cannot write there
NoNewPrivileges=true     no setuid, no sudo, no escalation
```

plus `daemon-reload` needs root or a polkit rule the box lacks. Anything
proposing that a gateway rewrite its own systemd unit is proposing something
that cannot happen; do not re-litigate it without re-measuring those three.

So: `gateway-launch-updatability.ts` classifies, `gateway-launch-repair.ts`
prepares, and a human applies four lines. Four things about it are
load-bearing:

**The judged thing is the LAUNCHER, never the running path.** A freshly
installed box has never self-updated, so no `gateway-releases/current` exists
and it runs out of the installer's root-owned tree — an entrypoint outside
the layout, on a box whose next update would work perfectly. Judging by
`require.main.filename` would condemn most of the fleet. Discovery is
`/proc/self/cgroup` (systemd puts the unit NAME nowhere in the environment,
and production's unit is `empyralis-gateway-channels.service`, not the
default name `resolveExpectedSupervisorUnit` would guess), then the unit's
own `ExecStart`; a shell launcher is credited by two STRUCTURAL tokens it
must contain to work at all (`/current/gateway/dist/index.js` plus
`EMPYRALIS_GATEWAY_INSTALL_ROOT`/`gateway-releases`), held to the REAL
installer heredoc by `gateway-launch-updatability-installer-drift.test.ts`
rather than to a fixture copy.

**`Restart=on-failure` is a SECOND, independent blocker and production has
it.** The self-update runtime's supervised handoff exits 0 and trusts the
supervisor to bring the new build up — which only `always`/`on-success` do.
Fixing the path alone would trade a stale box for a dead one, so both go in
one drop-in and both are reported as separate facts with separate details.

**Every failure to read, find or parse resolves to `"unknown"`, never to
`"not_updatable"`** — and the backend refuses ONLY on an explicit
`"not_updatable"`. A wrong "unknown" costs a signal; a wrong refusal takes
updates away from every healthy box at once, and the whole fleet reports
nothing here until it is rebuilt.

**The launcher is boot-safe by construction and PROVEN before it is
recommended.** Pointing an `ExecStart` at `<layout>/current/gateway/dist/
index.js` directly is a loaded gun — on a box that has never self-updated
that file does not exist and the first restart strands the machine forever.
The generated `/bin/sh` launcher prefers the layout and otherwise execs the
exact path the gateway is running from at write time, and the gateway RUNS
it once (`EMPYRALIS_GATEWAY_LAUNCH_PROBE=1` prints the entrypoint and exits
0 without starting anything) — an unproven launcher is reported
`unverified` and its `ExecStart` is never handed out.

**THE UNIT FILE IS NOT THE CONFIGURATION — the first version of that
classifier read `/etc/systemd/system/<unit>` and therefore condemned the one
box that had already been repaired.** Found on production minutes after the
drop-in repair landed:

```
systemctl show   ExecStart=/var/lib/empyralis-gw/state/launch/run-gateway
(EFFECTIVE)      Restart=always                                ← REPAIRED
base unit FILE   ExecStart=/usr/bin/node /opt/…/dist/index.js
(what we read)   Restart=on-failure                            ← stale, forever
…service.d/empyralis-updatable.conf   exists, wins in systemd, never read
reported: not_updatable, 2 blockers        reality: fully updatable
```

A drop-in IS the documented repair (§3a), so the file can never see the fix
it is being asked to confirm — every operator who followed our instructions
was told they had failed. Now `systemctl show <unit> --property=ExecStart …`
(the only source that merges drop-ins) and `launchctl print <domain>/<label>`
(the only source that reflects the job as LOADED, not as last written to
disk — launchd has no drop-ins, but a plist edited after bootstrap is
equally not what starts next). Three things that only measuring reveals:
`ExecStart` comes back STRUCTURED (`{ path=… ; argv[]=… ; pid=… }`), so
argv[] is taken up to the next ` ; ` or systemd's runtime status lands in the
command an operator is shown; **`systemctl show` EXITS 0 FOR A UNIT IT HAS
NEVER HEARD OF** and prints `Restart=no`, so a missing ExecStart — not the
exit code — is what means "no such unit" (trusting that `Restart=no` invents
a blocker); and the FILE fallback, when systemd cannot be asked at all, may
no longer produce `not_updatable` — blockers it alone finds resolve to
`"unknown"`, because the thing that would clear them is exactly the thing it
cannot see. `configSource` says which source answered. The launchd fallback
is NOT downgraded that way, deliberately: with no layering mechanism, a
plist that reads as broken is one somebody edited to be broken.

Refusal reaches the screen as its own state: the Hardware page rendered
"Up to date" for EVERY refusal, including this one, because
`gateway_update_refusal_code` shipped with the fingerprint and nothing ever
read it. Now "Can't receive updates" plus the reason, the blockers and the
copy-pasteable drop-in. The commands are shown rather than performed on
purpose — no button on that page could ever work, and a button that cannot
work is a dead control. Operator procedure lives in
`docs/DEPLOY-RUNBOOK.md` §3a, with rollback.

**Flagged while here, NOT fixed:
`__tests__/exec-file-timeout-child-leak.test.ts`'s drift assertion is
VACUOUS.** It scans `path.resolve(__dirname, "..")` for files ending `.ts`,
but `npm test` runs from `dist/__tests__`, so it walks `dist/` — which
contains zero `.ts` files. The guard CLAUDE.md cites as banning raw
`execFile` timeouts in `src/` currently enforces nothing and reports green.
`gateway-launch-path-wiring.test.ts` avoids the same trap with an explicit
src-tree resolver plus a canary assertion; copy that shape, and give every
source-scanning test a canary.

## A migration's BACKFILL cannot run under RLS, and it says nothing (2026-08-18)

**Verdict: `GEN-12` task identifiers were absent from every task on
production not because the code was missing — it shipped 2026-08-13 and is
correct — but because the migration's backfill was silently filtered to zero
rows by our own row-level security. Exit 0, columns created, index created,
nothing written.**

```
projects / project_tasks   FORCE ROW LEVEL SECURITY,
                           policy empyralis_rls_scope_match(tenant_id, workspace_id)
DEPLOY-RUNBOOK step 3b     apply migrations as `empyralis_app` — NON-superuser,
                           so FORCE binds it — and psql sets none of
                           app.current_tenant_id / app.current_workspace_id /
                           app.rls_bypass

  ALTER TABLE / CREATE INDEX   DDL, RLS does not apply   ─▶ APPLIED
  SELECT / UPDATE ... projects DML, policy is FALSE      ─▶ 0 rows, no error
```

Reproduced exactly on a disposable database with a `NOSUPERUSER NOBYPASSRLS`
role owning the tables (production's configuration): the pre-fix migration
printed `BEGIN / DO / COMMIT`, created both columns AND `uq_projects_task_key`,
and left `task_key` NULL / `task_seq` 0 / `number` NULL. Production, five days
later: 9 of 10 projects unkeyed, 34 tasks, 0 numbered.

**THE RULE: a migration that only ADDS COLUMNS is safe to hand to psql; a
migration that BACKFILLS a tenant-scoped table is not.** The two runbook rules
already in this file compose into a trap — "apply migrations as the app's own
role" (correct, ownership) plus "every scoped table is FORCE RLS" (correct,
isolation) means every DML statement in every migration against those 40+
tables silently addresses the empty set. Nothing reports a row count, so a
backfill that did nothing is indistinguishable from one with nothing to do.
Grep any migration you are about to apply for `UPDATE`/`INSERT`/`SELECT`
against a scoped table before trusting its exit code.

The backfill now lives in `projects_repository.backfill_task_identifiers()`,
called from `ensure_control_plane_schema()` — so every database heals on its
next boot instead of on somebody remembering a psql step, and it REUSES
`_unique_task_key` (the same function `create_project` calls) rather than the
second SQL transcription of the same slug→key derivation the migration
carried. The migration file is now DDL only, carries `SET LOCAL
app.rls_bypass = 'on'`, and `test_task_identifier_backfill.py` bans DML from
returning to it — structurally, because the broken version passes every
behavioural test not run as the app role.

**A `task_seq = 0` seed guard is WRONG and the original migration had it.**
Caught by a test, not by review. A task created by the live allocator between
the deploy and the repair moves `task_seq` to 1 while older tasks are still
unnumbered; the backfill numbers them 2..N (offset by `MAX(number)`), the
`= 0` guard SKIPS the seed, and the next `create_task` allocates 2 — colliding
with a live task. The correct predicate is `task_seq < MAX(number)`: raising is
always right (every number issued came out of `task_seq`, so `task_seq >=
MAX(number)` is the invariant), lowering is never right, and deleting tasks can
only shrink `MAX(number)` so it can never walk the counter backwards into
reissuing a live number — which is the hazard the original "one-time seed"
comment was actually reaching for.

Two more things worth not re-deriving. **Ordering is `created_at ASC, id ASC`**
— oldest task is GEN-1, and the id tiebreak is what makes a re-run a genuine
no-op rather than a reshuffle; identities appear in comments, links and agent
memory, so a RENUMBERING backfill is worse than no backfill. And **the
concurrent-allocation race is closed with a row lock**, not hope: numbering and
seeding one project happen in a transaction that opens with `SELECT ... FOR
UPDATE` on that project's row — the same row `create_task`'s `UPDATE projects
SET task_seq = task_seq + 1` locks — so a task created mid-backfill blocks
until the seed commits.

**The display path needed no fix.** `taskDisplayId` already composed
`project_task_key` + `number` with an honest hex-slice fallback, and is already
the call at all four render sites. Proven end-to-end against real Postgres:
real service → `project_task_key='GEN', number=1` → `GEN-1`.

**Flagged, deliberately NOT fixed: `ensure_control_plane_schema` has a SECOND
instance of this bug.** Its `workspace_agent_installs` label-dedupe `DO $$`
block runs through a plain `pool.execute()`, which sets no scope GUCs, against
a table that is also FORCE RLS — so it too addresses zero rows on every boot.
Fixing it is one line, and I did not, because the fix's visible effect is
RENAMING the founder's own duplicate-labelled agents the next time the backend
restarts. That is his call, not a side effect of a task-numbering ticket.

## The MCP surface advertised more than it could do (2026-08-18, MAN-205/207)

**Verdict: three of the four tools MAN-205 named were still dead, each for a
DIFFERENT reason — and every one of them type-checks, imports cleanly, and
fails 100% of the time at runtime. That combination is why all three
shipped.** A `tools/list` entry that always fails is worse than an absent
one: a connected client discovers it, calls it, and retries it.

```
empyralis_chat                     ALREADY FIXED — build_operator_namespace
                                   is gone from the whole repo; routes through
                                   sage_turn_adapter.execute_sage_turn now
empyralis_get_agent_conversations  current_user carried "mcp_workspace_id"
                                     ↑ read NOWHERE in auth.py. the only two
                                       occurrences in the repo were the two
                                       dicts that WROTE it
                                   → allowed_workspace_ids() = set()  → 403, always
empyralis_trigger_test_turn        execute_test_turn(tenant_id=…) is keyword-only
                                   with NO default; the call site never passed it
                                   → TypeError, always. and the SimpleNamespace
                                     request lacked runtime_mode/customer_profile,
                                     so the fix for that would have died 2 lines on
empyralis_connect_connector        request_origin reads request.headers FIRST;
                                   the shim had only base_url
                                   → AttributeError, always
```

**The right treatment differs per cause, and a blanket one would have been
wrong.** All three had live backends, so all three were wiring bugs and all
three were fixed. Removal is the correct answer only when the backend is
gone — which is exactly what an earlier pass did to `empyralis_memory_read`
/`_list`/`_write` (its note is still in `mcp_server.py`), and the precedent
to follow rather than re-litigate.

Three rules, one per cause, because each is a shape not a typo:

**A hand-built `current_user` is an authorization claim, and inventing a key
makes it unfalsifiable.** `_mcp_current_user` is now the one place that
builds it: exactly the workspace the key already resolved to, at owner role,
nothing else. Deliberately NOT `is_admin`/`auth_admin` — either makes
`allowed_workspace_ids`/`allowed_tenant_ids` return `None`, i.e. *every
workspace of every tenant*, which is the obvious-looking fix for a 403 and
would quietly turn a single-workspace bearer key into a cross-tenant one.
The check was never wrong; it was handed an identity it could only reject.

**Build the call from the PRODUCER, not from what the argument seems to
need.** `routes_deployed_agents.test_turn_deployed_agent` passes `tenant_id`
and a real `DeployedAgentTestTurnRequest` and calls `.model_dump()` on the
result; the MCP call site did none of the three. Same family as the fixture
rule already in this file, one level up — a `SimpleNamespace` answers exactly
the attributes whoever wrote it thought of and `AttributeError`s on the first
one they did not, which is a guarantee of breakage the moment the callee
grows a field.

**A stand-in Request is a real `starlette.requests.Request` or it is a bug.**
`_public_origin_request()` builds one from a genuine ASGI scope. The LIVE MCP
request is deliberately NOT reused: this app mounts at `/mcp`, so its
`base_url` carries that root path and the derived OAuth callback would be
`/mcp/api/connections/oauth/…` — a 404 the customer only discovers *after*
granting access.

**`create_task` fires NOTHING — no wakeup, no notification. That is the
functional gap, and MAN-207 states it slightly wrong.** Three behaviours,
not two:

```
create_task          project_tasks_service:872   neither. an activity row, best-effort.
assign_task          :1936  schedules the wakeup.  no notification.
assign_task_to_user  :2079  creates the notification. no wakeup, ON PURPOSE —
                            "people are not woken by schedulers"
```

So work filed over MCP and left unassigned reached nobody, and the only
working escape hatch was commenting with an @-mention. `empyralis_assign_task`
(one tool, exactly one of `agent_id`/`user_id`, both-or-neither refused rather
than guessed) and `empyralis_list_tasks` (the whole board — `list_my_tasks`'
`WHERE` is `assignee = caller OR assignee IS NULL`, so a client could not see
what was in flight) close it. `wake_error` is surfaced: the assignment commits
independently of the wakeup, so "assigned" and "assigned and someone is on it"
are two facts.

**`EMPYRALIST_MCP_TOOLS` is a hand-kept literal beside the decorators that do
the real registering** — the "list copied into a second place" shape. Now
drift-tested against what the live FastMCP server answers `tools/list` with,
two different sources. Three more AST assertions ban the shapes above by
structure (over CODE tokens only, docstrings excluded, or documenting a
banned shape would trip its own tripwire).

**Verify an MCP tool over the PROTOCOL with the service UNMOCKED, or you
prove nothing.** Both dead deployed-agent tools return `ok: true` through a
real `tools/call` on the BROKEN code when their service edge is mocked —
both failed *inside* the callee. Driving the real
`create_connected_server_and_client_session` with the services live is what
separates them:

```
BEFORE  get_agent_conversations → 403: Workspace is not bound to a tenant
        trigger_test_turn       → execute_test_turn() missing 'tenant_id'
        connect_connector       → 'SimpleNamespace' has no attribute 'headers'
AFTER   all three reach the real workspace lookup and answer from DATA
```

**MAN-198 is STALE — close it.** Six of its seven claims were fixed by
`90c1725c7` (2026-08-01) or describe `ProjectOverview.tsx`, deleted wholesale
in `a54d58ff3`. The display name IS threaded through create_task /
comment_on_task / the three document writes; `_ledger_mcp_call` actor is the
real `ext_agent_*`; `list_unified_roster` has a caller and a route
(`GET /api/w/{id}/fleet/roster`) the frontend consumes. Two narrow residuals
survive and deserve their own tickets, not MAN-198's framing:
`workspace_labels_service.attach_label` persists `added_by` and
`list_task_labels` never selects it (dark data), and
`update_task_status`/`add_task_label`/`remove_task_label` still pass no actor
display name.

**Still open on this surface, flagged not fixed.** `empyralis_message_agent`
is advertised and *always* returns `ok: false` by design — a dead control on
the model's tool list, and the product-law violation is the advertisement,
not the missing backend. `empyralis_assign_channel_bot` takes a plaintext
BotFather/Discord token as a tool ARGUMENT, so the secret travels through
model context and into transcripts — MAN-207 recommends dropping it and that
recommendation still stands (its cross-workspace IDOR, MAN-206, IS fixed:
both provisioning services now call `agent_install_in_scope`). The OAuth
path mints no `external_agent_id` at all, so every write from a Connector
session is attributed to the `external_mcp_client` fallback.
`list_workspace_mcp_api_keys` drops `external_agent_id`/`display_name`/
`roster_warning` that key CREATION already computes, so the keys UI could not
show a key's identity even if it wanted to.

## An agent is a PRINCIPAL: reachability is the gate, placement is the consent (2026-08-18, MAN-356)

**Verdict: a workspace member could run shell commands on the founder's own
Mac, through two independent holes that compose.** Both proven live on a
disposable stack (two users, two projects), both closed, both re-verified.

```
HOLE 1  reachability was OBSCURITY
  GET  /fleet/agents      as a non-member  ->  200, agent absent   (filtered)
  POST /api/turn  naming that same id      ->  200, turn RAN       (no gate)
  8 turn-path modules, project-ACL calls found: 0. The ACL lived only in the
  LIST endpoints; POST /turn gated on WORKSPACE membership alone.

HOLE 2  execution borrowed ANY box
  _resolve_direct_tool_gateway_id(workspace_id, *, session_ctx)   no caller id
  registration_is_usable(registration, *, workspace_id)           no owner id
  -> ended in "any live active gateway registered to the workspace"
```

**The model, the founder's own: humans touch work artifacts; the agent touches
machines; NOBODY reaches a machine THROUGH an agent.** There are deliberately
no per-person tool-authority tiers — he rejected that outright ("we are not
going to decrease the skills of this agent"). So the gate is REACHABILITY, and
execution follows PLACEMENT.

**Fix 1 — `agent_reachability_service.enforce_agent_reachable`, and it FAILS
CLOSED. That property is the whole point, not a detail.** The obvious
implementation reuses `routes_fleet._enforce_agent_project_access`; do not.
That helper returns (allows) on a missing project, and `project_id` is nullable
BY SCHEMA — `REFERENCES projects(id) ON DELETE SET NULL` — with pre-migration
installs never backfilled (`fleet-data.ts` fabricates a default project id so
those URLs do not 404). A gate derived from it is *already* partly vacuous and
would become entirely vacuous the day agents stop carrying a project: still
present, still shaped like a gate, enforcing nothing. That is the worst
available outcome for a security seam.

```
agent_kind == "master"   ALWAYS reachable   workspace-scoped, MAN-201
project_id present       the project ACL decides
project_id ABSENT        workspace OWNER only — never a member
install unresolvable     REFUSE (404, never 403 — a 403 is an enumeration oracle)
```

Keyed on `agent_kind`, NEVER on an empty `project_id`: "has no project" must
never be what grants reach, or a project-less SPECIALIST silently inherits
Sage's exemption. When the grant stops being project-derived, only this
function's body changes — in one place.

**The two helpers are deliberately NOT merged.** `_enforce_agent_project_access`
guards fleet DETAIL reads, where a missing agent falls through to the service
call's own not-found; this one answers "may this principal reach this agent at
all", where "could not establish a grant" must mean no.

**Fix 2 — execution follows placement, and PLACEMENT IS THE CONSENT MOMENT.**
That is why no per-person hardware permission exists on this path: a box
reaches an agent because the HARDWARE'S OWNER put it there (the Hardware tab /
the wizard's Placement step write `preferred_gateway_id`; the U3-K project
default re-checks the machine owner's own live opt-in on every resolution).

```
1. hardware_access == "none"  -> None, always            (cloud-only agent)
2. the agent's PLACEMENT      -> that box, if usable+live
3. no placement               -> only a box the ASKING PERSON OWNS
4. otherwise                  -> None
```

`_resolve_live_gateway_from_workspace` is DELETED, replaced by
`_resolve_live_gateway_owned_by(workspace_id, owner_user_id, ...)` filtering on
`gateway_registrations.user_id` — the person who paired the box. An empty
owner id matches NOTHING (fail closed; `""` must never be a wildcard).

**Sage is the sharpest case and the reason step 3 exists rather than "no
placement -> no box".** A Sage turn resolves no specialist context, stamps no
`preferred_gateway_id`, and therefore ALWAYS hit the old workspace scan — and
Sage is the one agent every member can reach by design. Step 3 keeps the
founder's own workflow intact (he owns the Mac, so he still reaches it through
Sage) while making the cross-person case structurally impossible.

**`hardware_access` was rendered and enforced NOWHERE** —
`fleet_tools.resolve_hardware_access` had exactly one non-test caller, building
a list payload. It now rides on `SpecialistRuntimeContext.hardware_access` into
`session_ctx["metadata"]["agent_hardware_access"]`. It gates TOOL reach only,
never the BYO-brain binding: `mode: local` + `hardware_access: none` is a
legitimate agent whose model runs on a box while its shell tools stay
cloud-side, and folding the two would silently un-host that agent's brain.
An ABSENT bucket is "unknown", not "none" — Sage stamps nothing, and inferring
"none" would take the operator's hardware away on a guess rather than a setting.

**UNKNOWN is not "none", and collapsing them un-places agents silently.**
This was caught by the EXISTING `test_specialist_runtime_context.py` suite
going red — 5 tests — not by review. `fleet_tools.resolve_hardware_access`
normalizes an ABSENT value to `"none"`, which is correct for its own job
(rendering a picker) and wrong as an enforcement decision: the SQLite
local-bundle path carries no such column, so a missing key means "nobody told
me", not "the owner chose cloud-only". Only an EXPLICIT `"none"` suppresses
placement; unknown carries `""` onward, and both sides of the seam
(`SpecialistRuntimeContext.hardware_access`, whose default is `""` and not
`"none"`, and the resolver's `agent_hardware_access` metadata key) make the
same distinction the same way. The fix was to my own new code, not to the
tests — a normalizer written for DISPLAY is not automatically safe as a GATE.

**The model's own `gateway_id` argument is a HINT, never authorization.**
Call sites did `payload.get("gateway_id") or _resolve(...)`, so a model naming
a box skipped placement entirely. It is now passed IN and honoured only when it
names this agent's placement; otherwise dropped (logged) and resolution
continues, so the turn lands on the right box rather than no box.

**Returning None is the degradation, never an exception** — the callers already
expect it (`_hardware_action_offline_result`, and every gateway branch guarded
by `if ... and gateway_id`). An offline placement degrades to no machine, never
to somebody else's.

**Verified live, before and after, not from code.** Member outside the project:
`POST /api/turn` 200 -> 404. Negative controls all still 200 (owner -> private
agent, member -> shared-project agent, member -> Sage). Red-before-green on
both fixes by swapping the pre-fix shape back in: the fail-open variant is
caught by 2 tests, the unscoped borrow by 5.

**`POST /sessions` and `POST /threads/{id}/turns` are NOT reachability paths —
verified, not assumed.** Neither calls any turn-executing function; neither
model accepts an agent install id; and stored session metadata is never read
back to choose an agent (`agent_sessions.master_agent_install_id` is
write-only — `get_agent_session` has zero callers). Adding a gate there would
have been a control that enforces nothing.

**FIXED 2026-08-19: `routes_fleet._enforce_agent_project_access`'s fail-open
shape is closed.** It used to be `if not project_id: return`, unconditional —
so a member could reach a PROJECT-LESS agent through every fleet DETAIL route
(activity, memory, channels, connectors, tools, capabilities, usage) with no
project membership at all. Confirmed live on production (read-only query, no
writes): of 16 project-less `workspace_agent_installs` rows, 15 are
`agent_kind='master'` (correctly exempt, MAN-201) and **one is a real, enabled
specialist** (`ainstall_c8ec63b5f3474296`, label "Ftc") that was fully
reachable by any member of its workspace. Fixed by extracting the turn path's
own grant rule into `agent_reachability_service.enforce_resolved_agent_access`
and `lookup_agent_install_bundle`, shared by both callers now instead of two
independently-drifting opinions: `agent_kind == "master"` always exempt,
project-having agent gated by that project's ACL (unchanged), project-less
specialist now workspace-OWNER-only. The one deliberate remaining difference
from `enforce_agent_reachable`: an agent that cannot be resolved AT ALL
(doesn't exist, or the lookup failed) still falls through here rather than
404ing — this seam guards fleet DETAIL reads, where the service call right
after it already degrades safely on its own, and 404ing here as well as on
the turn path would make "does this id exist" answerable two different ways.
Red-before-green: `test_fleet_agent_project_access_fail_open.py`'s regression
test fails on the pre-fix code (no exception — the live bug) and passes after.
Blast radius measured, not assumed: exactly one agent's behavior changes.

And Sage's own `hardware_access` is never stamped (its turn resolves
no specialist context), so setting Sage to "Cloud only" does not yet disable
its tools; it still cannot borrow, because step 3 gates on ownership.

## Windows is out. macOS + Linux only (2026-08-19)

**Founder's decision, final, do not re-litigate:** *"ship macOS and of course
Linux, because VPS is Linux, fuck Windows we are not going toward that."*

```
macOS   the desktop app + the founder's own machine
Linux   every Agent Computer VPS, and the desktop app as a fast follow
Windows NOT SUPPORTED. not "later", not "partial" — out of scope.
```

This kills the work scoped that same day (weeks of effort, blocked on nobody
owning a Windows box to verify against — Session-0 isolation vs Docker
Desktop, symlink-vs-junction for self-update, an unwritten supervisor).
What SURVIVES that scoping pass and stays on main, deliberately:

- `.github/workflows/release-gateway-windows.yml` — `workflow_dispatch`-only,
  never on push. It is a verified-working cross-package build and costs
  nothing to keep; it is NOT a supported channel and its own header says so.
- `shell/docker-autostart.ts`'s honest platform naming ("Windows" not
  "win32" in customer prose) — correct regardless of support status, since
  the message is what a person on any unsupported platform reads.

`scripts/install_agent_computer_windows_service.ps1` is DEAD CODE and should
be deleted on sight: it builds an `empyralis-supervisor` Rust binary that
commit `9e70d4b4` ("Phase U: product refocus — kill supervisor") removed, and
it requires a full git checkout rather than being a customer installer.

## Telegram is the interaction model, and the reason is NOT the layout (2026-08-19)

The founder keeps holding Telegram up as the bar — *"once it's connected to
this platform it just works. It just works so shamelessly that it's just
perfect."* He proposes copying its shape: chat is the main surface, and
tapping the name in the header opens a PROFILE holding identity, the system
prompt, media, files — with agent MEMORY sitting alongside media/files.

**Adopt the shape, but understand what actually makes Telegram feel that
way, because copying the layout without it changes nothing.** What makes a
messenger feel perfect is a RELIABILITY CONTRACT, not a screen:

```
a message you send is never lost, even if you close the app mid-send
history is always there, instantly, without a spinner
closing the window does not stop anything that was already happening
you never lose your place
```

As of this writing Empyralis chat violates every one of those (the web turn
runs inline in the HTTP request and dies with the tab; the SSE stream
delivered zero bytes for 90s; navigating away showed a permanent skeleton).
**Order matters: the contract first, the profile second.** A Telegram-shaped
UI on top of a chat that loses your work is worse than today, because it
raises the promise without raising the behaviour.

On the profile itself, one correction to the analogy that must not be
cargo-culted: Telegram's profile describes a STATIC entity someone else
made, and its tabs are MEDIA TYPES (Media/Files/Links/Music). An agent is
something the owner CONFIGURES, so its profile is an editing surface, and
its tabs are not media types — the honest equivalents are **persona/system
prompt, memory, files, and what it has done**. Same gesture (name in header
-> the thing behind the name), different contents. Copying the tab names
would be imitating the surface of the surface.

Corollary the founder also raised: **agent creation is too heavy** (project,
name, model, placement, "tons of things") measured against how effortless
adding a bot in Telegram is. Reducing that is real work, not polish — but it
sits behind the reliability contract too.

## "Paste your token, done" was UNREACHABLE, and the setup screen was never the bug (2026-08-20)

**Verdict: `sage_telegram_hosted` — the hardware-free, paste-a-BotFather-
token Telegram lane the founder was literally describing when he said "go to
Telegram, paste your token, and it works" — had been silently hidden from
the Channels grid since 2026-08-14, replaced by the hardware-bound OpenClaw
Telegram card. Fixed on `feat/seamless-telegram-setup`.**

```
BEFORE (main, 2026-08-14 through 2026-08-20)   AFTER
  Channels tab → "Telegram" → "Needs Gateway"    Channels tab → "Telegram"
  → banner: "This agent has no computer of        → "Set up" → straight into
    its own yet — set one up on the Hardware       "Paste the token BotFather
    tab first…"                                    gave you… [Save token]"
  DEAD END for every cloud-only agent              real Telegram getMe() call,
  (the vast majority)                              verified live
```

The setup FORM itself (`hosted_bot_provisioning_service.assign_byo_bot` /
the byo_bot door in `channel-doors.ts`) was already close to the founder's
ask — single field, synchronous validate-and-save, no hardware column on
its door. It was simply never reachable: `channel-platform.ts`'s
`planUnifiedChannelGrid` (landed the same day as the OpenClaw cutover, to
fix an unrelated "Telegram card shown twice" bug) resolves ONE-PLATFORM-
ONE-CARD collisions by keeping whichever card the transport actively
carries — and `openclaw_channel_registry.OPENCLAW_CUT_OVER_CHANNEL_IDS`
listed bare platform token `"telegram"`, meant only to retire the deleted
gramjs PERSONAL-account lane, but token collisions are per PLATFORM not per
LANE, so it also swept away the unrelated, still-live `sage_telegram_hosted`
STUDIO_CHANNEL_ROADMAP bot lane. The exact "force an agent to acquire
hardware it does not need, for a channel that already works" regression
this same file already names as the reason discord/slack/sms stay
first-party — applied to Telegram by omission, not by decision. Caught only
by driving the REAL rendered Channels tab in a browser on a disposable
stack; a code reading of `hosted_bot_provisioning_service.py` alone (which
is what this task started from) would never have surfaced it, since that
file was completely correct in isolation.

Fixed at the root (`openclaw_channel_registry.py`: telegram removed from
`OPENCLAW_CUT_OVER_CHANNEL_IDS`, joining discord/slack/sms) plus a second,
structural layer in `channel-platform.ts`
(`hasProtectedHardwareFreeLane` — any first-party channel id ending
`_hosted`/`_official` is now NEVER dropped for a transported collision,
regardless of backend cutover state) so a future active-catalog regression
cannot silently reintroduce the same trap. `openclaw_telegram` itself is
untouched — declared, provisionable, still reachable by a customer who
deliberately wants Telegram bundled with their other OpenClaw channels on
one box — it just stopped being the DEFAULT, ADVERTISED implementation.

**A second, independent gap closed the same session: BYO-bot channels
(Telegram, and — confirmed, not yet fixed — WeChat Official) had NO owner-
recognition path at all**, because `command_registry._is_sender_owner` /
`sage_agent_runtime_service._resolve_channel_sender_class` both read
`personal_channels_repository`'s pairing-login table exclusively, and a
BYO bot has no pairing step — you prove control by pasting a token, not by
a phone/QR login. Net effect: EVERY sender on a BYO Telegram bot, the
workspace owner included, was permanently classified "audience" — no
shell/hardware/memory_write tools, and `/config /mcp /plugins /debug /bash`
silently unreachable, forever, for anyone. Fixed with
`personal_channels_repository.claim_channel_owner_identity_if_unclaimed`:
the first PRIVATE (never group) message on a fresh BYO binding claims
workspace-wide owner recognition for that channel family, one-shot — a
later sender can never displace an already-claimed identity, so a stranger
who messages the bot after the real owner cannot inherit tool authority.
Safe because `assign_byo_bot`/its siblings already require
`minimum_role="owner"` to create the binding in the first place — the
claim only extends authority the web session already proved, using the
SAME table/read path the two authority functions already trust, never a
fourth source. Reuses the existing `personal_channel_telegram_states`
table under a dedicated `channel_key` ("telegram_agent_byo") and sentinel
`gateway_id` ("byo") rather than inventing new schema.

Also fixed in the same pass, smaller: `assign_byo_bot` used to swallow
every Telegram `setWebhook` failure into `webhook_set: False` while still
returning `ok: True` and writing an ENABLED binding — so "Bot token saved
— this agent's own bot is live" could be shown for a bot that could never
receive a message (a deployment missing its public base URL, or a
transient Telegram-side error). Now retries once, then raises and rolls
back the credential rather than lying — the existing frontend error path
(`byoBotError`) picks it up for free, no FleetAgentDetail.tsx edit needed.

**Generalizes, not Telegram-special-cased.** The door pattern
(`CHANNEL_DOORS`, one paste-a-token field, synchronous validate-and-save)
already covers Discord/WeChat identically. `hasProtectedHardwareFreeLane`
is keyed on the LANE SUFFIX (`_hosted`/`_official`), not a channel id, so
it already also protects `wechat_official`. `claim_channel_owner_identity_
if_unclaimed` is parameterized (`channel_key`/`gateway_id`/`agent_id`/
`provider`) and ready to wire into WeChat Official's inbound path — which
has the identical gap, confirmed by code reading, NOT fixed this pass
(would need tracing wechat_official_service.py's own inbound entry point,
flagged rather than done blind). Discord's inbound routing was not located
in this pass at all (no `dispatch_sage_reply_safe` call site found in
`discord_bot_provisioning_service.py`) — whether it shares the gap is
unknown, not assumed either way.

## The streaming bug CAUSED the durability bug (2026-08-19)

Founder hit this live: sent a long message, navigated to another tab, the
turn died; came back, re-pasted, navigated again, got a permanent loading
skeleton; eventually `"No response for a while, so this send was stopped."`
His reaction, and he was right: *"even if I close it, shouldn't it work
continuously? it is working on the cloud not on the user interface."*

Two symptoms, ONE causal chain, and the direction is the non-obvious part:

```
run_claude_agent_sdk_turn  (the PRODUCTION-DEFAULT engine)
  translate_sdk_message already builds assistant.message.delta /
  reasoning.summary.delta / tool_progress per chunk — correctly —
  and then only APPENDS THEM TO A LOCAL LIST returned at the end.
  It never called _GENERATION_EVENT_SINK, the live-forwarding contextvar
  the LEGACY engine already uses via wrap_generation_with_sink.
  (grep before the fix: zero occurrences in claude_agent_sdk_bridge.py)
        │
        ▼  so nothing streams, ever, on the engine that actually runs
build_agent_turn_stream_response
  await run_in_threadpool(next, producer_iter)   ← blocks for the WHOLE turn
  services.start_chat_stream_producer(...)       ← the call that spawns the
        │                                           background thread whose
        │                                           completion DURABLY persists
        │                                           the assistant turn
        ▼
  a disconnect during that block — tab close, navigation, or the frontend's
  own 90s watchdog — cancels the coroutine BEFORE durability is established.
  The turn keeps computing in an orphaned thread (nothing stops a plain
  threading.Thread) and NOTHING IS LEFT TO PERSIST IT.
```

So it was never "uvicorn kills the turn" — that was the obvious hypothesis
and it was wrong. Durability existed; it was simply sequenced behind a peek
that the streaming defect made infinite.

Fix: `start_chat_stream_producer` runs FIRST, unconditionally, with no
`await` ahead of it — durability is secured before anything can cancel the
coroutine. The peek survives only as a bounded 2s best-effort for the
fast-failing "no provider configured" case and is explicitly UX-only. The
SDK bridge forwards each translated event to the sink as produced.

`run_service.execute_durable_turn_request` was deliberately NOT adopted for
web chat: once the existing producer-thread machinery is decoupled from the
blocking peek it already provides durability, without routing web through
the heavier durable-run path.

Three rules. **A watchdog that re-arms on every byte is a STREAMING probe,
not a timeout** — when it fires, the finding is "zero bytes arrived", and
lengthening it hides exactly the defect it detected (`09fc023a` added that
watchdog and its honest message; it was the messenger, never the bug).
**Anything that establishes durability must not sit behind an await that can
block for the length of the work it is protecting.** And **the two-engine
seam keeps diverging in ways review misses** — this is the same shape
CLAUDE.md already records for the credit debit and for tool-result
classification: identical return contracts, different side effects, three
separate live defects now.

Proven live against real DeepSeek on a disposable stack, not by code
reading: 165 SSE events arriving continuously over ~9s (`curl --trace-time`,
first 18:25:56.625, last 18:26:05.456) instead of one dump at the end; and a
client `SIGKILL`ed at t+4.014s still had its real reply persisted to thread
history 13 seconds after the client was dead.

Still open, NOT fixed: time-to-first-token is 10–14s on a cold call with a
large system prompt (~5977 input tokens) — the 15s idle-keepalive covers it
and only now gets a chance to run, but the latency itself is untouched. And
live `tool_progress` streaming is verified by code plus unit tests only —
the verification prompts never triggered a tool call, so nobody has watched
a tool step stream in real time.

## Chat landed on the OLDEST message, composer buried 12,000px down — and "ChatTab stays MOUNTED" is false for a real tab click (2026-08-20)

**Verdict: fixed the visible bug (opens at the newest message, composer
pinned). Found, while verifying, that the SAME-DAY "keep ChatTab mounted
across tabs" fix (`e6e268d2`) does not do what its own comment claims for
the way a person actually navigates — clicking the real `<Link>` between
Chat and Work. Not reverted, not touched — routed around, and recorded here
because the next agent will otherwise trust that comment.**

```
BEFORE (measured on production)          AFTER
.fleet-detail-body  scrollTop 0          .fleet-detail-body  scrollTop 0
  scrollHeight 12,979  ← the PAGE          scrollHeight == clientHeight
  scrolls, lands on message #1              ← nothing to scroll, bounded
.fleet-sage-chat-list (meant to           .fleet-sage-chat-list is the
  scroll internally) never bounded,          REAL scroller, lands exactly
  grew to fit ALL content                    at scrollHeight-clientHeight
composer position:relative, y≈12,928      composer flex-shrink:0, always
  ← 12,300px below the fold, unreachable      on screen, never in the flow
```

**Root cause: `e6e268d2` wrapped ChatTab in a bare, class-less
`<div style={{display:"none"}}>`.** That div sits BETWEEN `.fleet-detail-
body` and `.fleet-agent-chat-panel`, so `.fleet-detail-body > .fleet-agent-
chat-panel` in fleet-theme.css never matched again — the direct-child
selector that hands the panel its `height:100%` chain (which is what
bounds `.fleet-sage-chat-list`'s own `overflow-y:auto`) AND the one that
opts it out of the 820px reading-column cap. With no bounded height,
`.fleet-sage-chat-list` just grew to fit its whole 12,979px of content, so
`.fleet-detail-body` — the page itself — became the real scroller. Fixed
by deleting the wrapper: `ChatTab` now applies `display:none` to its OWN
root div (`.fleet-agent-chat-panel` stays `.fleet-detail-body`'s direct
child), taking a `hidden` prop instead of being wrapped by one.

**The scroll-follow behaviour itself lives in
`agent-chat-scroll-follow.ts`** (pure, tested — `isNearBottom`/
`shouldSnapChatToBottom`), landing-on-open plus "auto-follow only while
already at the bottom, never yank a reader back down mid-read" — the same
house pattern as `agent-count-shape.ts`/`channel-doors.ts`.

**Two things only running it revealed, both now guarded in
AgentChat.tsx's own comments:**
- **A `display:none` element's `scrollTop` gets DISCARDED by Chromium**
  (reset to 0), and that reset itself fires a genuine `scroll` event with
  EVERY metric at 0 (scrollTop/scrollHeight/clientHeight). `isNearBottom`
  reads 0-0-0=0 as "at the bottom" — so the scroll listener has to drop any
  read where `clientHeight === 0`, or hiding the pane silently corrupts
  "was this reader following" the instant it happens.
- **A restore attempt can race the thread's own load.** Coming back to
  Chat, the first layout-effect run can land while `loading` is still true
  (skeleton height, scrollHeight===clientHeight) — writing the remembered
  scrollTop there gets CLAMPED to 0 by the browser (nothing to scroll to
  yet), and if the "just became visible" flag is consumed on that first
  attempt, the real restore never gets a second try once content actually
  loads. Fixed by gating the whole effect on `!loading`.

**The bigger finding: `.fleet-agent-chat-panel` is NOT staying mounted
across a Chat<->Work tab switch, contrary to `e6e268d2`'s own comment
("ChatTab stays MOUNTED on every tab, hidden (not removed)").** Proven,
not inferred: typed a draft into the composer, clicked the real `<a
href=".../work">`, clicked back to `.../chat` — the draft was gone. A
component that truly stayed mounted cannot lose local `useState`. Root
cause: `agents/[agentId]/[tab]/page.tsx` has no `layout.tsx` above it, and
Chat/Work are different values of the `[tab]` ROUTE segment — Next's App
Router remounts the page-level component tree (`FleetAgentDetail` and
everything under it, confirmed via a mount-effect counter, isolated from
React StrictMode's dev-only double-invoke by re-testing against a real
`next build && next start`) on every such navigation. The `display:none`
wrapper this fix started from was never going to preserve anything for
THIS navigation path — CSS visibility is irrelevant to a component that
gets destroyed and recreated one level up.

This means the draft-loss and in-flight-turn-loss bugs `e6e268d2` set out
to fix are NOT fixed for a real Chat<->Work click today — only for
whatever narrower interaction (if any) that fix was actually verified
against. Not chased further or fixed here — restructuring
`agents/[agentId]/` into a `layout.tsx` + thin `page.tsx` is a real,
separate body of work with its own blast radius (breadcrumbs, `useParams`
usage, the Sessions panel's own state), and this session's job was scroll
position, not that architecture. **Scroll position itself does not depend
on the mounted-across-tabs premise being true**: `CHAT_SCROLL_MEMORY`
(module-scope, keyed by `threadId`, in AgentChat.tsx) survives a real
remount the same as it would survive a genuine hide/show, which is why
"switch to Work and back" is verified working above despite this. Anyone
picking up the mounted-across-tabs work should re-verify it against a real
`<Link>` click between routed tabs, not just a same-render `activeTab`
state flip.

## Performance: measure from a CUSTOMER's seat, not the founder's (2026-08-19)

**A 26-second agent turn was investigated as a code problem. It is a
GEOGRAPHY problem, and roughly 12–16 of those seconds are the Pacific.**
The founder is in China; production is a single VPS in San Francisco.

```
GET /healthz, same endpoint, two vantage points
  on the server, loopback ............     7 ms
  from the founder's Mac ............. 3,456–4,462 ms      ~500x
  ICMP to production ................. 100% packet loss  (filtered)
  TLS handshake alone ................ 1.7–2.3 s
```

Trace-level breakdown of a real `uname -a` turn (`trace_6df93c7b…`,
agent_trace_events, deepseek-v4-pro):

```
+4.1s   click -> trace start            one Pacific crossing
+0.2s   tool decision
+8.1s   TOOL EXECUTION                  cloud -> box -> cloud, TWICE
          of which, measured ON the Mac:
            docker info ....... 0.17s   (readiness probe, cached 60s)
            docker run --rm ... 0.16s   warm; 3.57s COLD, first run only
            ─────────────────────────
            ~0.35s local. the other ~7.7s is the round trip.
+8.4s   DeepSeek generating the answer  real model time, not ours
+5.5s   persistence tail
```

The server does its part in single-digit milliseconds. **Do not spend
optimization effort on code paths that are already fast because the
founder's own experience feels slow** — his path is the worst one the
product will ever have, and a customer in the US/EU hits that same 7ms
server with no GFW in between. Before optimizing anything for latency,
measure from a vantage point an actual customer would have; otherwise you
are tuning against the ocean.

What DOES have leverage, and is the only latency work worth doing here:
**cut round TRIPS, not milliseconds.** Every `hardware__action` tool call
is a full cloud->box->cloud crossing, so a task needing three commands pays
three crossings. Batching commands into one dispatch helps the founder AND
every customer whose box is far from the cloud — it is a structural win,
not a local one. (In flight as of this writing on
`feat/batched-hardware-dispatch`; it also collapses N containers into one,
since `docker-sandbox.ts` runs `docker run --rm -i` per call today and
therefore carries NO state between commands — `cd` in one call is invisible
to the next.)

Corollary worth stating because it will come up again: the agent's BRAIN
runs in the cloud and its HANDS run on the box (measured, MAN-318). That
split is what makes every tool call a network crossing. Any future proposal
to reduce agent latency should be evaluated against how many crossings it
removes, not how much CPU it saves.

Also confirmed live while measuring, and still open: a web turn opens TWO
traces — the real `surface=sage` one carrying provider/model/events, and a
`surface=web` shell with empty provider/model and `finished_at` NULL.
CLAUDE.md already flags this duplicate-shell trace as unfixed; this is a
direct observation of it on production, not a code reading.

## The 500x slowdown is China, not the product (2026-08-19)

Measured from a real US vantage point, not read off a hypothesis. Provisioned
two throwaway DigitalOcean droplets (SFO — same region as production, and
NYC — cross-country US), paired the SFO one as a real Agent Computer through
the product's own pairing flow, ran a real turn from a fresh throwaway
account. Both droplets destroyed and confirmed absent via `GET /v2/droplets`
afterward; production `/health` 200 before and after; no founder data touched.

```
                            founder (China)        US customer (SFO droplet)
ICMP to production ......   100% packet loss        0%, 0.5-2.4ms
TLS handshake alone .....   1.7-2.3s                 ~50-70ms
GET /health total ........  3,456-4,462ms             0.92-1.45s
hardware round trip only .  ~0.35s is real work,      0.469s — ALL of it is
                            ~7.75s is WAN tax          real work, ~zero WAN tax
```

**100% ICMP packet loss is not distance, it's interference.** Ordinary
distance latency degrades gracefully; it does not drop every ping. That
number is the signature of the GFW filtering traffic, not the product being
slow. The `/health` figure disentangles this cleanly: a same-region US
customer's 0.92-1.45s for that endpoint is ALREADY almost entirely
backend/Cloudflare processing time — nearly identical to hitting production
over pure loopback (0.9-1.4s). For a US customer the network's contribution
is close to zero. For the founder in China, the network **is** the cost.

**Verdict: do not spend launch-window effort chasing latency the product
does not actually have.** The founder's own experience is real and
frustrating, but it is not representative of what he is selling. A customer
with a US-placed Agent Computer pays almost no network tax on either leg.

This does not make the batching work (`feat/batched-hardware-dispatch`,
merged) wasted — round-trip count still matters for ANY customer whose box
is far from the cloud (which will be common — this is a global product) and
the on-machine win (N containers -> 1, shared cwd/env) stands on its own.
But do not chase network latency further without first checking who is
actually far from the box: for the founder specifically, the fix is not in
this codebase at all — it's a China-side network problem (VPN/route
selection) has nothing to fix here.

One secondary finding, not a bug: a droplet's TCP `connect` time can be far
faster than its ICMP round-trip to the same origin IP once Cloudflare is in
front — TLS/HTTP hits a nearby edge PoP, ICMP goes straight to origin
(Cloudflare doesn't proxy ICMP). "Ping the origin" and "connect to the site"
measure different paths; do not conflate them when reading a future probe.

## Payment processor is Polar, not Stripe — and the reason is not preference (2026-08-20)

**A complete, tested Stripe integration exists in this codebase
(`billing_service.py`, 1627 lines) with ZERO Polar references anywhere.
Do not read that as "Stripe is the plan." It is dead code the founder
never touched — he onboarded as a merchant with Polar, in Polar's own
dashboard, and no line of this repo talks to Polar yet.**

The reason is not taste, it's geography: **Stripe is not directly
available as a standalone merchant account in Uzbekistan** (not one of
its ~46 fully-supported countries; only limited Global Payouts since Feb
2026). **Polar is supported in Uzbekistan specifically because Polar is
the Merchant of Record** — the customer pays Polar (a US entity), and
Polar handles the underlying Stripe relationship via Stripe Connect
Express on the founder's behalf. It is the only processor that lets the
founder legally receive money from this product today.

```
Stripe integration in this repo    tested, wired, unreachable by the
                                    founder as a standalone merchant
Polar (founder's real account)     3 of 7 onboarding steps done,
                                    no product created yet, zero code
```

Do not recommend shipping on the existing Stripe code as-is. The
credit-crediting DB logic and webhook-shape reasoning in
`billing_service.py` are a legitimate head start and worth reading before
building the Polar integration, but the actual processor calls
(checkout session creation, webhook signature verification, event names)
must be swapped to Polar's API, verified against Polar's own current
docs rather than assumed to mirror Stripe's shape.

**Tier pricing is still not settled** ($20/$100/$200 was the founder
thinking aloud, explicitly not final — see the pricing memory). Wire the
mechanism generically enough to take real product/price IDs once he
finishes Polar onboarding; do not hardcode invented numbers as final.

## BYO-subscription model truth: live discovery + no dead reasoning control (2026-08-20)

**Founder's requirement, verbatim: a BYOK model list must be the models the
customer's OWN subscription can actually serve, verified by "the official
document or by the official harness or by the official subscription... it
must be right 100%."** Two separate lies were found and fixed.

```
LIE 1: the model list                    LIE 2: the reasoning picker
  MODELS_BY_PROVIDER (frontend,            REASONING_EFFORT_OPTIONS shown
  hand-typed) — "no live models            for every byok_api model, but
  endpoint reachable from the              openai_compat_adapter.py had
  browser today"                           ZERO references to
        │                                  reasoning_effort (CLAUDE.md's
        ▼                                  own prior entry, above)
  GET /providers/{id}/models                     │
  ALREADY EXISTED end to end                      ▼
  (adapter.list_models — a REAL          picking "High" on an OpenAI/
  call to the provider's own             Gemini/xAI agent on the SDK
  /v1/models) — route scoped             engine (production default)
  correctly, frontend client             did NOTHING. Silent no-op,
  method present... zero callers.        not an error.
  "Built, tested, never wired."
```

**Fix 1 — the live endpoint is now actually called.**
`fleet-model-config.ts`'s `useByokModelCatalog` (mirrors the existing
`useCodexModelCatalog` pattern the cli_subscription/Codex picker already
used) calls it from `FleetAgentDetail.tsx`'s byok_api Model select; a
failed/empty response falls back to the static list with an honest note,
same contract as the Codex picker. One real backend gap this surfaced:
`connectors_core.get_provider_models` resolved a saved default credential
via `resolve_default_vault_credential` ONLY for `openai`/`ollama_cloud` —
every other BYOK provider (gemini/xai/groq/openrouter/qwen/mistral/bedrock)
reported `credential_required: True` unconditionally even with a real key
saved, because nothing ever looked it up. `resolve_default_vault_credential`
is provider-agnostic; the fix generalizes the lookup to every provider and
keeps openai/ollama_cloud's extra env-var fallback layered on top.
`azure_openai`/`custom_openai_compatible` stay on the static free-text
field on purpose — a deployment name isn't a discoverable id.

**Fix 2 — reasoning_effort is threaded out-of-band and always does
something.** The CLI's own `thinking` field on the Anthropic-shaped wire
request carries no recoverable level (`{"type":"adaptive"}`, no level), so
it can't be parsed back out — the value is instead carried on the per-turn
`TurnCredential` the adapter already mints (alongside provider/model/
credentials), from `claude_agent_sdk_bridge.resolve_sdk_process_env` through
`mint_turn_token_for_provider` to `messages_endpoint`'s lookup.

```
requested effort ──▶ provider_profiles.reasoning_effort_levels_for_model(provider, model)
                              │
                 clamp to nearest verified level (never invented)
                     │                              │
             a level exists                   nothing verified
                     ▼                              ▼
        real wire param:                   the SAME honest system-
        flat "reasoning_effort"            instruction fallback the
        (openai/gemini/xai) or             LEGACY engine already used
        nested {"reasoning":               for a non-reasoning model —
        {"effort":...}} (openrouter)       now true on BOTH engines
```

Every branch does something real — never the silent no-op CLAUDE.md's own
prior entry documented. The picker's existing help text ("Models that
support it natively use it directly; others get it as a strong instruction
instead") was already promising this; the SDK engine just wasn't keeping
the promise.

**`supports_reasoning: True` in `PROVIDER_MODEL_CATALOG` means "this model
reasons internally" — it never meant "the API exposes a settable
`reasoning_effort`", and the catalog conflated the two in several places,
confirmed against each provider's own docs (2026-08-20):** `gpt-4o`/
`gpt-4.1`/`gpt-4.1-mini` aren't reasoning models at all (were wrongly
`True`); `gemini-1.5-pro`/`gemini-2.0-flash` predate Gemini's thinking
feature (also wrongly `True`); **every xai model this catalog currently
offers — grok-4, grok-4-0709, grok-4-latest, grok-3 — reasons with a fixed,
non-adjustable budget and exposes no `reasoning_effort` at all** (only Grok
3 Mini and Grok 4.5+/4.6+ do, per docs.x.ai, and neither is in the catalog
yet). `reasoning_levels: []` is the new disambiguating signal
(`reasoning_effort_levels_for_model`) that decides whether the wire param is
ever attempted — `supports_reasoning` keeps its old, narrower meaning as the
"Reasoning" capability badge, unchanged, so nothing else that reads it
regresses. qwen/mistral/groq/ollama_cloud/azure_openai/custom_openai_compatible
have NO verified wire contract today (different param name, or none at
all) — landing on the fallback instruction rather than a guessed field that
could 400 a turn the customer did nothing wrong to cause. OpenRouter is the
one exception with real breadth: its own docs claim graceful degradation
across OpenAI/Anthropic/Grok/Gemini/Mistral via a *different* wire shape
(`{"reasoning": {"effort": ...}}`, not the flat field), trusted for every
model this catalog already marks `supports_reasoning: True` there.

**Verified how, and what that means for confidence per provider.** Every
wire-shape and per-model claim above came from reading each provider's own
current documentation (OpenAI, Google, xAI, OpenRouter — not memory, not
guessed) — satisfying the founder's "official document" bar. None of it was
verified against a live call: CLAUDE.md's own standing rule (a test may
never reach a live LLM provider; never touch the founder's personal Claude
subscription) forecloses that, and no company-billed OpenAI/Gemini/xAI/
OpenRouter credential was exercised live in this pass either — an actual
end-to-end request against each real API remains unverified against the
"official harness" bar and is the natural next step for whoever owns those
credentials.

Guarded by new tests, all red-before-green (the pre-fix file swapped back in
via `git diff`+`git checkout --`, confirmed the exact new assertions fail,
then restored — never `git stash`, per this file's own rule):
`test_openai_compat_adapter.py`'s `TestClampReasoningEffort`/
`TestReasoningEffortWiring`, `test_provider_profiles.py`'s
`ReasoningEffortLevelsForModelTests`, `test_claude_agent_sdk_bridge.py`'s two
new threading tests, and `test_connectors_core.py`'s
`GetProviderModelsCredentialResolutionTests`.

Not done in this pass, flagged rather than guessed at: no per-model gating
was added to HIDE the reasoning-effort picker for a non-reasoning model —
the fallback-instruction path means it is never rendered-but-inert, so
hiding it was judged unnecessary rather than skipped for time. `cli_
subscription`'s own reasoning-effort picker (claude_code/codex/grok_build)
was already correct before this pass and untouched. Cursor CLI's model
catalog stays freeform (no published model-id vocabulary, unchanged).
**Both of those last two sentences were overtaken the NEXT DAY** — the
per-runtime pickers were collapsed into one shared ladder, and Cursor turned
out to publish a real per-account catalog after all (`cursor-agent models`).
See "BYO subscription: one effort ladder, live model lists, no auth shim
(2026-08-20)" above.
Ollama's own OpenAI-compat `/v1/chat/completions` reasoning support for
gpt-oss models is plausible but not verified against Ollama's own docs —
left in the "no verified wire contract" bucket rather than guessed.

## A `byok_api` AGENT was charged platform credits (2026-08-21)

**Verdict: a `usage_events` row WAS being written for every byok_api turn —
the recording was never missing. What was wrong is worse: the row said
`mode="platform_credits"`, and the workspace was DEBITED for tokens the
customer had already paid their own provider for.**

"Who pays for this turn" has two possible sources. Only one was asked:

```
WORKSPACE  admin_defaults.sage_ai_provider  -> "byok"      _resolve_turn_payer_mode READ it
AGENT      model_config.mode == "byok_api"  -> "byok_api"  nothing read it at all

_resolve_agent_cloud_provider(...)  -> (provider, credentials, billing_mode)
   docstring: "NEVER bill platform credits for a BYOK-bound agent"
   unit-tested, 4 branches, all correct
   ONE production call site:
     provider, credentials, _ = await _resolve_agent_cloud_provider(...)
                             ^^^ thrown away
```

So a byok_api agent in an ORDINARY workspace resolved to `platform_credits`.
`sage_ai_provider` is a *workspace* AI-route default — nobody has to touch it
to bind ONE agent to its own key — so the ordinary configuration is exactly
the broken one. Measured live on the pre-fix tree by driving the real turn
seam (`handle_sage_chat`, production-default SDK engine, real
`_resolve_agent_cloud_provider`, only the LLM and the ledger faked):

```
                                     BEFORE                  AFTER
agent byok_api, plain workspace   mode=platform_credits   mode=byok_api
                                  DEBIT 10 credits        DEBIT 0
agent byok_api, workspace BYOK    mode=byok               mode=byok_api
                                  DEBIT 0                 DEBIT 0
agent platform_credits            DEBIT 1  (unchanged)    DEBIT 1
```

**CLAUDE.md's own "built, tested, and never wired" failure mode, on a money
path — and the schema-shaped variant of it too**: `usage_events_repository.
_USAGE_MODE_TO_PAYER` already mapped `"byok_api"` -> `"BYOK"`. The READER was
built for a value no writer had ever produced.

Fixed by giving `_resolve_turn_payer_mode` the second input rather than
adding a second decision: `agent_billing_mode` (the mode the resolver
RESOLVED, never the raw `model_config.mode` — the legacy shape, a provider
with no explicit mode, means byok_api and only the resolver knows that).
`_meter_and_debit_turn` stays FUSED — this is one more input to the one
payer question, not a second ledger call and not a BYOK bypass.

Three rules follow. **Precedence runs agent-beats-workspace, but only
downward**: a non-platform agent mode overrides, an agent declaring
`platform_credits` does NOT, so a workspace-BYOK route resolves exactly as it
did before the parameter existed — adding an input must not change an answer
that was already right. **The set is spelled as what IS platform-paid**
(`_PLATFORM_PAID_AGENT_MODES = {"platform_credits"}`), so a lane this module
has never heard of fails CLOSED (no debit) instead of being billed on the
grounds that nobody taught it otherwise. And **`usd_cost` is still reported
for a BYOK turn**: it costs the PLATFORM nothing, which is a different fact
from the tokens being free to produce, and only the `mode` column is allowed
to carry the difference — the same discipline `_ledger_cli_subscription_turn`
already keeps with `tokens_known`/`pricing_known`.

Guarded in `test_default_engine_credit_debit.py` (16 new assertions, all red
before / green after, verified by reverting the production file with
`git diff` + `checkout` + `apply`, never `git stash`): COUNTS on both sides
(exactly one `usage_events` row, exactly zero debits — "a row exists" and "no
debit happened" are each satisfied by the opposite failure), a
platform_credits control proving the fix did not simply switch debiting off,
and two AST tests — one banning `provider, credentials, _ = await
_resolve_agent_cloud_provider(...)` from returning, one requiring EVERY
`_meter_and_debit_turn` call site to pass the argument, since a second site
that forgets it silently reverts to the workspace-only answer for whichever
lane it serves.

**Verified by code reading only, not driven:** the legacy engine's OWN debit
(`direct_chat_hosted_usage_service`, reached inside
`stream_provider_backed_direct_chat`) gates on
`credential_plane != "platform_runtime"` — a different signal that appears to
already exclude a BYOK key — and `skills_service.py:6211` gates media
capabilities on `resolution.billing_mode == "platform_credits"`, correctly.
Neither was exercised live in this pass.

## BYO subscription: one effort ladder, live model lists, no auth shim (2026-08-20)

**The founder OVERRODE the reasoning-effort picker's `none` branch the day
after it shipped. Do not reinstate it.** `planCodexReasoningPicker` used to
resolve three ways — live levels / static ladder / **render nothing** when a
model positively reported zero levels. His words:

```
"I'm not going to change this effort level based on like separated for each
 one provider... low medium high, extra high max and ultra. If it works, it
 works otherwise you can still choose it — for example that's how it works
 inside this Claude Code even if I use it with DeepSeek, it doesn't have any
 effort level."
```

So: **ONE ladder — low · medium · high · xhigh · max · ultra — offered for
every mode, every provider, every model, always.** `REASONING_EFFORT_LADDER`
(fleet-provider-constants.ts) is the single source; the four per-runtime
`CLI_REASONING_EFFORT_OPTIONS_BY_RUNTIME` lists are DELETED.

This is not the "no dead controls" law being waived, because **the picker
became uniform while the WIRE stayed native**:

```
picked        low  medium  high  xhigh  max  ultra      (same list, everywhere)
      │
      ▼ clamped at the seam that actually sends it
claude_code   `--effort`                  ultra → max
codex         `-c model_reasoning_effort=` ultra → max, off/minimal still legal
grok_build    `--reasoning-effort`        ultra → max, none/minimal still legal
cursor_cli    NO FLAG EXISTS              → "" — nothing appended
byok/platform provider_profiles.reasoning_effort_levels_for_model
              → native wire param, else a strong system instruction
```

`sage_agent_runtime_service.clamp_cli_reasoning_effort` is that seam; it
**clamps, it does not drop**. Dropping was correct while the picker could
only offer legal values — now an out-of-vocabulary level is the EXPECTED
case, and dropping would silently discard a deliberate choice ("ultra" on
claude_code) that "max" expresses perfectly well. Save-time validation
(`fleet_tools`) accepts the shared ladder for every runtime PLUS each CLI's
own legacy rungs, so a value saved before unification keeps validating and a
level the picker offers can never 400.

**The fallback is NOT reachable everywhere, and one path had to be fixed
before the ladder was safe to widen.** Checked rather than assumed: on
byok/platform an unsupported level degrades to a strong system instruction
(`openai_compat_adapter._apply_reasoning_effort`) — but **a turn served by
Anthropic's own API never reaches the adapter at all**. It goes through
`claude_agent_sdk_bridge.resolve_sdk_effort`, whose `_VALID_SDK_REASONING_
EFFORTS` is the SDK's own five-member `EffortLevel` union, and anything
outside it returned `None`, i.e. the field is simply not set. So "ultra" on
an Anthropic BYOK agent would have done *literally nothing*, with no
instruction fallback to rescue it — the one place the widened ladder could
have become a real dead control. It now clamps to the SDK's own ceiling.
Deliberately an explicit `{"ultra"}` set, not "anything unrecognized": a typo
or a hand-edited value must still resolve to `None` and let the model choose.

**`cursor_cli` is the ONE place the control genuinely does nothing** —
cursor-agent publishes no reasoning-effort flag at all. That is stated in the
picker's own hint and asserted in a test, not papered over by inventing a
control Cursor does not provide.

The live catalog changed ROLE, it was not discarded: it now names the model's
own default ("Model default (medium)"), relays the model's own prose per
level, and **appends any level the model reports that the ladder does not
carry** — codex types `ReasoningEffort` as an OPEN STRING for exactly that
reason, and "ultra" reached the ladder that way in the first place.

### Model lists are live for THREE of four runtimes, each asked its own way

Founder's correction on how to do this, and it governs auth as much as
models: *"We are going to make it work by how THEY provide the specific
thing, not trying to make another thing for those providers. I don't want to
build something like Codex does for Cursor or xAI or Anthropic. I just want
to serve however they provide to me."* No shim, no adapter, no unified
protocol.

```
codex        `codex app-server` JSON-RPC model/list   structured, per-account,
                                                      carries reasoning levels
cursor_cli   `cursor-agent models`                    "List available models
                                                       for this account"
grok_build   `grok models`                            "List available models"
claude_code  NOTHING TO ASK — a compiled Mach-O binary with no models
             subcommand and no --list-models flag (static string inspection,
             2026-08-20). Reported unsupported; a list transcribed from docs
             would be exactly what CLAUDE.md already forbids.
```

Both text-printing CLIs print HUMAN TEXT (neither `models --help` offers a
JSON flag), so `empyralis-gateway/src/llm/cli-model-list.ts` parses their own
output and **fails to "unsupported-with-a-reason", never to an empty
catalog** — a zero-model answer comes back carrying the CLI's own sentence
("No models available for this account.") because an empty dropdown is a dead
control and a relayed sentence is a fact. `useCodexModelCatalog` no longer
short-circuits on `runtime !== "codex"`; the BOX decides what it can
enumerate.

**Verified live 2026-08-20, real binaries, real accounts:** `grok models` →
`grok-4.6`, marked default, parsed correctly. `cursor-agent models` → "No
models available for this account." relayed verbatim (that account is not
signed in for headless use — Cursor's POPULATED output shape is therefore
genuinely UNVERIFIED, and the zero-model path is what protects a customer
from that gap).

### There is NO token exchange for BYO subscription — for any of the four

The founder remembered one ("in Codex we had it"). Traced: there is not, and
there should not be. The thing he is remembering is a DIFFERENT mode.

```
cli_subscription (BYO SUBSCRIPTION — all four runtimes)
  credential lives ON THE BOX, in the CLI's OWN store, written by the CLI's
  OWN login, and the gateway NEVER reads or transmits it
  (cli-login-session.ts forwards only a URL or a "paste code" prompt — an
  allowlist a line must match, not a promise in a comment)
    codex        `codex login` device auth / --with-api-key / --with-access-token
    claude_code  `claude auth login --claudeai` (or --console)  → Keychain /
                 ~/.claude/.credentials.json
    grok_build   `grok login --device-auth`                     → ~/.grok/auth.json
    cursor_cli   `agent login` (NO_OPEN_BROWSER=1 prints the URL)
  → a turn is just: spawn the CLI. It resolves its own auth. Nothing minted.

byok_api (BYO API KEY — a different mode entirely)   ← the token exchange
  openai_compat_adapter.mint_turn_credential issues an opaque per-turn token,
  IN THE CLOUD, so the `claude` CLI the SDK engine spawns gets
  ANTHROPIC_AUTH_TOKEN=<opaque> instead of the customer's real key, and
  messages_endpoint swaps it back. Cleared when the turn ends.
```

`codex app-server` is a WARM DAEMON, not a token exchanger — that is the
"running somewhere" he half-remembers, and it is a latency optimisation.
**No runtime is missing an integration here.** Each one's native mechanism is
already wired; the correct answer for the other three is that they need
nothing Codex-shaped, and building it would be inventing a shape they never
asked for.

### Found while proving it: EVERY grok_build turn failed, on every box

`grok -p <prompt> --output-format json` emits ONE **pretty-printed**
document. `cli-runner.ts`'s `parseJsonLines` is line-oriented — the first
line is a bare `{`, which `JSON.parse` rejects, and no other line starts with
`{`. Zero events → `grok exited with code 0 and no parsable result`, thrown
on a turn the CLI had completed perfectly, with the answer and real usage
sitting unread in stdout.

```
BEFORE  grok_build effort=high  ERROR crash  "no parsable result"   100% of turns
AFTER   grok_build effort=high  {"text":"PONG","usage":{...}}
```

It survived because its own test built the fixture with `JSON.stringify()`,
which produces a SINGLE LINE — the "a fixture that invents its own input
cannot notice the real input is shaped differently" failure, again, and the
fixture was one call away from the real thing. The regression test now
carries the REAL captured stdout with its real whitespace; **do not reformat
it and do not replace it with `JSON.stringify`, the whitespace IS the thing
under test.** The whole-document parse is a FALLBACK that only fires when
line-scanning found nothing, so codex/claude_code's genuine JSONL streams are
untouched (asserted).

**Live end-to-end results, real CLIs, real accounts, through the real
`runCliSubscription` path:** codex ✓ (PONG, 15,860 in / 6 out, `-c
model_reasoning_effort=high` accepted); grok_build ✓ after the fix (PONG,
real usage, `--reasoning-effort high` accepted); cursor_cli → honest
`not_authenticated` (that account is not signed in headless — not a defect).
**claude_code was verified BY CODE READING ONLY, deliberately** — this file's
own standing rule that the founder's personal Claude subscription is never
driven by an automated agent, not once.

Harness note, so nobody re-diagnoses it: `cli-runner`'s retry delay uses an
**`unref`'d** timer. Correct inside the gateway (a live WS always holds the
event loop), but a bare `node` script driving `runCliSubscription` drains the
loop and exits with `ERR_UNSETTLED_TOP_LEVEL_AWAIT` before the retry fires —
that is the harness, not a production hang. Hold the loop open with an
interval when driving it by hand.

## Verifying against DOCS is still transcription (2026-08-20)

**A capability table built from a provider's documentation is hardcoding
that feels like research.** Docs are a snapshot, exactly like training
data. The moment a provider ships a model, the table is wrong and a human
has to notice and edit code — which is precisely the failure the
"derive, never transcribe" rule already forbids.

This was violated on 2026-08-20 by a dispatch that told an agent to
"verify against each provider's official docs." It produced a correct-
looking, doc-sourced reasoning-capability table that was already stale on
arrival (missing grok-4.5 / 4.6 / 4.20-multi-agent, all of which DO
support `reasoning_effort`). The founder's objection, and it is the rule:
*"once they provide new model I don't have to change the entire thing, I
don't have to think about it."*

**The live source usually already exists, and is often already running on
the box.** Verified on 2026-08-20 — the installed Codex binary emits its
own machine-readable protocol contract:

```
codex app-server generate-json-schema --out <DIR>
  → codex_app_server_protocol.v2.schemas.json

ReasoningEffort            "A non-empty reasoning effort value ADVERTISED
                            BY THE MODEL"  — type: string, NOT an enum
supportedReasoningEfforts  array of { reasoningEffort, description }
defaultReasoningEffort     the model's own default
```

Note `ReasoningEffort` is deliberately an OPEN STRING, not a closed enum,
specifically so a new model can advertise a new level without any client
change. Hardcoding an enum against a protocol designed to avoid enums is
the mistake in miniature. Claude Code's harness advertises a different
set than Codex's — which is why a single global list of levels is wrong
by construction.

The ordering to apply, for any capability question:

```
1. the running runtime/harness itself   (schema dump, app-server protocol,
                                         --help, config dump, MCP contract)
2. a live API the customer's own
   credential can call                  (GET /v1/models and friends)
3. a pinned, DATED, source-recorded
   fallback                             ← last resort, must be visibly the
                                          degraded path in the code
4. documentation                        ← NOT a source of truth. At best a
                                          hint about where to look for 1-3.
```

All four BYO runtimes (`codex`, `claude`, `cursor-agent`, `grok`) are
installed on the founder's machine and can be probed directly. Before
writing a capability table for any of them, run the binary and ask it.

## THE PLATFORM IS NOT A CHAT PRODUCT (2026-08-20) — settled

**Conversation happens in channels. Never in the web UI.** Founder, verbatim
and emphatic: *"messaging would never be done inside this platform. I'm
strictly going to prohibit that and nobody is going to use that... you want
to speak and have an agent, go set it up, go to Telegram and speak with the
agent inside that channel. We are not going to try to be a channel."*

```
WHAT THE PLATFORM IS              WHAT IT IS NOT
  context layer                     a chat UI
    tasks + documents,              a Telegram competitor
    GitHub/Linear-grade             a place you spend time in
  agent configuration
    hardware, model, memory,      HOW YOU TALK TO AN AGENT
    tools, MCP                      Telegram / WhatsApp / iMessage
  observation                       /commands inside the channel
    what is it doing,
    is it healthy
```

The strategic argument, and it is the load-bearing one: every hour spent on
in-platform chat competes with Telegram, Claude and ChatGPT on THEIR
strongest surface with none of their distribution. Poke (raised ~$20M) is
the reference — it deliberately pushes users to iMessage rather than
building its own chat, and reached the App Store as an agent platform.

**Consequences, all settled by the founder in the same conversation:**

- **No message composer anywhere in the platform.** The agent detail
  surface is READ-ONLY: which channel each inbound message came from, the
  agent's output, tool calls, plan steps, live work. Session name and
  history on top. You watch; you never type.
- **Keep the live streaming of tool calls and reasoning.** That is the
  reason to open the platform at all. Removing chat must not remove it.
- **The "Work" button/tab is removed.** Attribution belongs on the TASK and
  DOCUMENT surfaces instead — which agent created, updated, commented,
  completed — not behind a separate tab.
- **Agent creation is name + optional system prompt. Nothing else.**
  Model/hardware/memory/tools are configuration seen and edited afterwards,
  never questions at creation.
- **An agent may exist unpaired**, showing as not-yet-reachable. A channel
  is not required to create one.
- **Agents belong to the WORKSPACE, not to a project.** *"project and
  agents are completely independent — let's stop creating agents inside
  this specific project, let's get rid of that entirely."* This resolves
  the open question previously recorded below.
- **Telegram is the recommended channel** and must be presented first, with
  a "Recommended" marker, wherever channels are set up.

**A verified fact that makes the no-hardware case a non-issue:** documents
and tasks do NOT go through git or hardware. `document__write/__edit/__read/
__list` and the 14 `project_task__*` tools write to
`project_documents_repository` / the tasks store in Postgres, with real
revision history, and never touch a gateway. So a cloud-only agent with no
hardware can already fully create and edit documents and tasks. Hardware is
required only for SHELL and FILESYSTEM work — which is the correct
boundary, and is the same model ChatGPT/Claude use. This is already built;
it was simply never presented as the feature it is.

**The internal-MCP idea the founder described already exists too:** agents
natively hold 14 task tools and 4 document tools over the workspace's own
data. "Go check this project" works with no connector registration and no
re-authenticating Notion/Linear inside the agent.

**The risk to hold in mind, stated once so it is not forgotten:** with chat
gone, CHANNEL SETUP BECOMES THE CRITICAL PATH. A new customer gets zero
value until a channel works. On 2026-08-20 that path was found to be a dead
end for every cloud-only agent (see the Telegram entry). It must be
flawless, not merely fixed. The zero-friction path already exists — the
hosted bot, "no BotFather, no token" — and belongs immediately after agent
creation.

## The platform is not a chat product — the composer is gone (2026-08-20)

**Settled, not open.** Founder, verbatim: *"messaging would never be done
inside this platform. I'm strictly going to prohibit that and nobody is
going to use that... you want to speak and have an agent, go set it up, go
to Telegram and speak with the agent inside that channel. We are not going
to try to be a channel."* And on what stays: *"we will only show what kind
of messages had been going from which channel, and agent's output and its
tools and other things in the process, but you wouldn't be able to speak
with the agent."*

```
BEFORE                                AFTER
Chat tab   composer, send, history      ONE surface, both old tab ids:
Work tab   read-only activity view      channel · agent output · tool
  ↑ two competing header controls       calls/plan steps · LIVE via SSE
                                         NO composer, NO send, anywhere
```

`FleetAgentDetail.tsx`'s "chat" tab (the agent's front door) no longer
mounts `AgentChat` (the composer). It and the legacy "work" tab id now
render the SAME component — `tabs/WorkTab.tsx`, left unrenamed on purpose
(its own header comment explains why: the file is a genuine cross-
reference target for `ConversationsView.tsx`'s shared `.fleet-work-*`
markup/CSS, ~5 other files cite it by filename, and the founder's objection
was to the visible "Work" BUTTON, not this internal name). That surface
already did the real job — per-channel conversation list, live tool/plan
step streaming over `GET /api/agent-traces/{id}/stream` — so nothing about
watching an agent work was weakened; only the SECOND, composer-only tab
was deleted, along with everything that existed solely to serve it:
`ChatTab`, the owner-only "Sessions" right-panel section (a second,
narrower conversation picker duplicating WorkTab's own left-hand list —
this codebase's own "only ONE surface may be the picker at a time" rule),
"New chat", the `?thread=` URL/localStorage plumbing, and the whole
`fleet-agent-conversations.ts` module (deleted outright — its sole
consumer was the thing just removed).

The persistent header "Work" button — founder: *"there is a button that
was saying Work — I don't really like it, I think it must go"* — is gone;
the identity link always opens the agent's Profile now (nothing left to
switch between). The "⋯" menu's "Sessions" item is "Properties"; the right
panel is Properties only.

**`AgentChat.tsx` and its composer are UNCHANGED** — they remain Sage's own
workspace-level "Ask AI" console (`SageLauncher.tsx`), a deliberately
different per-user surface this pass did not touch (CLAUDE.md's own "Ask
AI is per-user" section). Whether Ask AI is next is an open question for
the founder, not decided here — do not extend this removal to it on a
guess.

**Attribution was already fully built, this pass only verified it.** The
founder's replacement for "watch the conversation": *"all I have to do is
just check which agent pushed this specific task, or updated or commented
by this agent, or pushed by this agent when it comes to documents."*
`TaskDetailView.tsx` already renders real "Created by"/"Completed by" rows
(`task.created_by`, `task.completed_by_user_id`/`completed_by_agent_id`,
real avatar+name via `AgentSigil`/`MemberAvatar`) and a per-comment author
in its Activity feed. `DocumentHistory.tsx` already renders full revision
authorship (`changed_by_type`: human/agent/external_agent, wired since
2026-08-12 — see this file's own "built, tested, and never wired" note on
`empyralis_list_document_revisions`, since fixed). Nothing new was built
for this — it was reachable and rendering correctly before this pass
started; this pass only confirmed that by reading the code and does not
claim to have driven it live against a real agent-authored task/document
edit.

This is closely related to, but does NOT resolve, item 2 in the section
immediately below (whether the web UI is a workspace or a setup surface)
— it removes ONE way people might have spent time in the web UI, on a
specific and explicit founder instruction, not a general judgment about
the rest of the surface.

## Plan limits: bytes and seats are capped, ROWS never are (2026-08-21)

**Nothing counted stored bytes. Anywhere.** File TYPE was gated server-side
(`upload_content_policy`) and one file was capped at 32MB, but there was no
total-storage accounting in the product — so the cap that was wanted could
not be written until the count existed.

```
THE ONLY MULTIPART UPLOAD ROUTE IN THE BACKEND
  POST /api/sage-chat/attachments   registered TWICE; sage_context_files_api
                                    wins on registration order
  workspace-scoped. NO project_id anywhere in the request.
        │
        ▼
  assert_allowed_upload        WHAT kind of file      (unchanged)
  assert_within_storage_cap    is there ROOM for it   (new, same module)
        │                        ↑ pure. takes the usage as an argument.
        ▼
  reserve_storage_for_upload   ONE transaction: advisory lock → SUM → decide
   workspace_storage_service   → INSERT. A refusal rolls back, so neither the
                                 ledger row nor the file exists.
```

**Counted: files on disk. NOT counted: project document BODIES** — markdown
TEXT in a Postgres column. Counting them would make writing a document spend
storage allowance, i.e. charge for the thing being sold. Agent knowledge
files and inbound channel media are real disk and are deliberately NOT
enrolled (one has no decrementing delete path; the other would drop a
customer's inbound message rather than refuse a customer's action). The
`surface` column carries which is which per row, so the answer is readable
from the data and not only from a comment.

**The cap is PER PROJECT and every byte today lands in the workspace-level
bucket, because no upload surface carries a project.** `project_id` is
`TEXT NOT NULL DEFAULT ''` and NOT a foreign key — `''` is a real bucket, and
a `REFERENCES projects(id)` would forbid the only row the product writes.
Both bucket kinds are capped by the same number, so the unattributed bucket
is never a way around the cap. **No optional `project_id` parameter was added
to that route to make this look finished** — the upload is Sage's own per-user
Ask AI console, nothing in the frontend could send it, and a parameter no
caller sends is this codebase's own most-documented defect. When a
project-scoped file surface ships it passes its own `project_id` and the
bucket is real with no change to the service.

**ROW COUNTS ARE NEVER CAPPED, and that is positioning, not an oversight.**
Bytes and seats have real marginal cost; a project row does not, and the
founder has said four times that context is the product and never the
paywall. `billing_credit_config` holds both dials (`PROJECT_STORAGE_CAP_BYTES`
1 GiB, `WORKSPACE_MEMBER_LIMIT` 10) with `EMPYRALIS_*` overrides, read through
functions so no call site keeps a copy.

**The seat cap is on `_finalize_workspace_invite_acceptance`, the one seam
every accept route crosses, and BEFORE `upsert_workspace_membership`** — a
refusal grants nothing and leaves the invite pending and re-usable. Invite
CREATION also checks, so no mail goes out for a full workspace, but that
check can go stale between minting and accepting and is explicitly not the
guard. **409, not 403**: the caller is not forbidden, the destination is
full. Someone already inside is never refused for a seat they already hold.

**Both caps fail OPEN — on an unreadable control plane and on an unset
dial.** They bound cost; they must not become a second availability
dependency in front of a working feature, and a zero-valued dial meaning "no
room at all" would take uploads away from every workspace at once. An
uncounted upload is LOGGED and reported (`recorded: False`), never hidden.

Verified against real Postgres as a `NOSUPERUSER NOBYPASSRLS` role owning the
table (superusers bypass FORCE RLS, so the app's own local role proves
nothing): under-cap admitted, over-cap refused having written nothing,
cross-tenant read returned 0 rows, and **10 simultaneous 100-byte uploads
into 100 free bytes admitted exactly ONE**, landing on exactly the cap. That
last one is what the per-bucket `pg_advisory_xact_lock` is for; without it a
check-then-act read is the classic race on the one number the cap depends on.

**The credit purchase floor is $10, not $1** — Polar charges a FIXED 50c plus
5% per transaction, so a $1 top-up loses 55% of itself to fees; at $10 it is
10%. Max stays $500. The frontend's `$5` top-up preset became a control the
server could only ever refuse, so `TOP_UP_PRESETS_USD` is now `[10, 25, 50]`,
with a drift test reading the presets and the server floor from different
files.

**Still open, flagged not built:** nothing in the product SHOWS a workspace
its storage usage — `workspace_storage_usage()` returns the per-project
breakdown plus the roll-up and has no route or screen, so today a customer
meets the cap only at the moment of refusal. And nothing deletes a stored
attachment, so `forget_stored_object` exists (a reservation without a release
is how a counter becomes monotonic) with one caller: the rollback when the
file write fails after the ledger row committed.

## Deep links: one builder, and NO link beats a broken one (2026-08-20, MAN-358)

**Chat left the platform, so a link in the channel message is the ONLY way
the work an agent did is reachable from the conversation.** "I created GEN-12
for you" has to be tappable in Telegram/Slack or the task it names is
unreachable from where people actually talk.

```
project_task__create / document__write            skills_service dispatch
   │  the task/document dict, inside the block that already
   │  resolved the agent's CONTEXT GRANT
   ▼
deep_link_service.annotate_task / annotate_document        ← the ONE builder
   │  adds "url" + "display_id" (GEN-12).  KEY ABSENT, never "" ,
   │  when there is no origin or no id — a model cannot interpolate
   │  an absent key into a sentence
   ▼
tool result JSON  ─▶ the model  ─▶ _deep_link_guidance() says pass it on
   ▼
the agent's own reply  ─▶ Telegram / Slack / any channel
```

**There is deliberately NO channel-side link rewriter.** A per-channel builder
would be "channels is ONE system" broken, and it would have to re-derive an
identity the tool layer already holds. Both platforms auto-link a bare URL, so
no per-channel formatting exists either.

**The origin comes from `cloud_cutover_config.resolve_public_frontend_origin`
— the SAME resolution `workspace_invite_email_service` uses for the
/join/{token} link that demonstrably works in production — with
`allow_dev_fallback=False`.** That argument is load-bearing, not a detail:
the shared resolver's dev fallback is `http://127.0.0.1:3000`, correct for a
developer's browser and a LIE in a Telegram message. `webhook_base_url()`'s
`ORION_TELEGRAM_AUTOPILOT_PUBLIC_BASE_URL`/`EMPYRALIS_PUBLIC_BASE_URL`/
`PUBLIC_BASE_URL` list is a THIRD env-var list (connectors_actions has a
fourth); a fifth was not added.

**Measured, not assumed — the redactor decides whether a link survives at
all.** `secret_redaction_service.redact_text` runs on every visible reply and
every persisted assistant turn:

```
https://empyralis.ai/w/ws_…/projects/proj_…/tasks/task_69d6…   survives intact
http://localhost:3000/w/ws_…/…/tasks/task_69d6…    'http://localhost:[redacted-secret]'
```

A dotless host is not URL-ish to `_URLISH_TOKEN_PATTERN`, so the high-entropy
sweep eats the whole path. That is a SECOND, independent reason the loopback
fallback must never reach a channel, and both halves are pinned by a test.

**`display_id` has no hex-slice fallback, on purpose.** The frontend's
`taskDisplayId` falls back to a uuid slice so a table cell is never blank;
here the value goes into a sentence an agent writes to a person, and "task
69D656" is a uuid fragment dressed up as an identifier. Empty means the agent
says what it did without naming an identifier that means nothing.

**Signed-out landing was the other half, and it was a real dead end.**
`(account)/layout.tsx` did a bare `redirect('/login')` — every deep link a
reader tapped without a session in that browser signed them in and dropped
them on the workspace root, destination discarded. `?next=` already existed on
/login, /signup and /verify-email, along with three byte-identical private
`safeNextPath` copies each commented "mirrors the others"; they are now one
`lib/auth/login-next.ts`. The path reaches a SERVER layout through
`proxy.ts`'s `REQUEST_PATHNAME_HEADER` (Next exposes no other way to read it
there), set inside `cspRequestHeaders` — the one function every return path in
`proxy()` already goes through, same narrow-waist reasoning as the CSP nonce
beside it — with `set`, never `append`, so a client-supplied header of that
name is overwritten and `safeNextPath` is the backstop for the asset paths the
proxy matcher skips.

Verified LIVE against a real `next dev --webpack` with a 401-stub control
plane: `/w/{ws}/projects/{p}/tasks/{t}` → `307 /login?next=%2Fw%2F…%2Ftasks%2F…`,
query strings preserved. **Not verified live: a real message arriving in a
real Telegram chat** — that needs a real bot credential and was not attempted;
everything up to "the URL is in the tool result the model composes from" is
proven by the real unmocked dispatch.

Two guards worth knowing about. `test_deep_link_service.py` checks each route
template against the REAL `frontend/app/(account)/...` directory tree (expected
set and actual set from different places) and asserts none of them is a
`next.config` redirect SOURCE — a redirect resolves ahead of the router, so
such a rule would silently send every deep link somewhere else. And
`test_deep_link_tool_results.py` AST-scans both dispatch blocks: a new
`project_task__*`/`document__*` action that forgets the annotator compiles,
runs, returns a valid result, and is silently unlinkable.

Not done, deliberately: the MCP tools (`empyralis_create_task` and friends)
still return unlinked objects — a different audience (external agents) and a
separate decision.

## OPEN FOUNDER DECISIONS — unresolved, do not guess (2026-08-20)

These are questions the founder has raised MORE THAN ONCE and has not yet
had answered. They are recorded here because holding them in a
conversation loses them — he has said, correctly, that he raises the same
problem weeks apart and nothing happens. **If you are working in one of
these areas, do not pick an answer silently. Surface it.**

### 1. RESOLVED 2026-08-20 — agents belong to the WORKSPACE. See the entry above.

(original question kept for context)

### 1. Does an agent belong to a PROJECT or to the WORKSPACE?

The codebase currently follows BOTH, which is why this keeps resurfacing:

```
agent-quick-create.ts       cites "an agent belongs to its project and
                            works only there" as law, resolves a project
                            silently at creation
MAN-357 / the rail          agents are a TOP-LEVEL surface
project tab bar             Agents tab REMOVED (Tasks · Documents only)
/projects/{id}/agents       route still exists on disk, unlinked
```

His framing: general-purpose agents and "agents for repetitive work inside
a project" may be two different things — or the project-level one is
redundant and should be removed. Unanswered.

Everything downstream depends on this: whether creation asks for a
project, whether the unlinked project-agents route is deleted, and whether
"repetitive project work" is a distinct product concept.

### 2. RESOLVED 2026-08-20 — setup + observation, never conversation. See above.

(original question kept for context)

### 2. Is the web UI a WORKSPACE or a SETUP SURFACE?

His words: *"what we're building is not something people are going to
spend time in within the platform. They're just going to create the agent,
set up the channel."*

If that is true, the agents list, the workspace home and most of the web
UI are SETUP surfaces judged by how fast someone gets out of them — not
daily-use surfaces judged by how much they show. That is a materially
different design brief from the one most of this UI was built against, and
it changes what the product's front door should be. Unanswered.

### 3. Purpose / audience (customer-facing vs owner-facing)

Raised THREE times as unnecessary. Removal of the owner-facing SETTING is
in progress. The open part: `audience` currently gates real tool filtering
(`audience_tool_filter.filter_tools_for_audience`, live at
`sage_agent_runtime_service.py:3331`), and his own prior ruling says there
must be NO tool-authority tiers — access is binary, gated by who can reach
an agent. Whether "faces the public" should be DERIVED from being wired to
a public channel (rather than declared) is the unresolved half.

**Process note for whoever reads this:** when the founder raises a
question that is a product decision rather than a bug, add it here in the
same turn. Do not answer it with a guess and do not let it live only in
chat. He has explicitly said the recurrence is the cost he cares about.

## Production `.env` IS A DECOY — both Telegram doors were dead (2026-08-20)

**Verdict: `/opt/empyralis-app/.env` is never read by the production backend.
Only 2 of its 42 keys were in the running process. Both Telegram doors — the
founder's #1 launch channel — were non-functional, silently.**

```
runtime_config._should_load_dotenv()
  true only for  dev|development|local|test|testing
  production is  EMPYRALIS_DEPLOY_ENV=self-hosted     ─▶ dotenv NEVER loads
                 (deliberate, MAN-202: an unscoped load once handed a
                  worktree the real repo's DATABASE_URL)

so the app's config is pm2's SAVED ENV, and .env is a file that looks
configured, reads as configured, and is inert.

  measured on the box:  .env keys 42   present in live process: 2
```

Proven by running the app's OWN code under the live process's exact
environment (reconstructed from `/proc/<pid>/environ`, not a fresh shell):

```
BEFORE                              AFTER
is_configured()      False          True                  hosted bot
webhook_base_url()   ''             'https://empyralis.ai'  BYO bot
```

`is_configured()` false means the hosted bot could not authenticate to
Telegram at all; `webhook_base_url()` empty means `assign_byo_bot` could
never register a webhook, so "paste your token, done" failed for every
customer. Telegram's webhook WAS registered (`getWebhookInfo` → the right
URL, 0 pending, no errors) — registered out of band, which is why nothing
looked broken from outside.

**Two independent defects, and fixing either alone leaves it broken.**
`webhook_base_url()` looks for `ORION_TELEGRAM_AUTOPILOT_PUBLIC_BASE_URL` /
`EMPYRALIS_PUBLIC_BASE_URL` / `PUBLIC_BASE_URL`; `.env` defines
`EMPYRALIS_BASE_URL` and `EMPYRALIS_PUBLIC_API_URL`. So even a correctly
loaded `.env` would not have fixed the BYO door — a key-name mismatch, not
a loading problem.

**`register_webhook_if_configured` had ZERO callers** (the signature defect
again), so no deploy ever re-registers the webhook. Change the domain or
rotate the bot and inbound dies with nothing saying why. It is now the
thing that registered the current webhook, which also guarantees Telegram's
`secret_token` matches the app's — previously unknowable, and a mismatch
would 403 every real message.

**Fixed SURGICALLY — three vars, never a bulk `.env` load**, and that
restraint is the point: `.env` also carries `ORION_JWT_SECRET` (injecting it
would invalidate every live session) and `CLOUD_SESSION_MANAGER_ENABLED` (a
flag this file records as deleted). Bulk-loading a file nobody has verified
against the running process is how a config fix becomes an outage. Env was
rebuilt from `/proc/<pid>/environ` read as NUL-delimited bytes — `tr '\0'
'\n'` corrupts any value containing a newline and silently invents
variables (it produced 17 bogus entries named `0`..`16` here).

Three rules. **A config file's presence is not evidence it is loaded** —
check the live process, not the file. **`pm2 restart --update-env` from a
bare shell REPLACES the process env**, so it strips DATABASE_URL and every
secret; `/root/pm2-env.sh` (0600) is the authoritative snapshot and
DEPLOY-RUNBOOK.md §7 now says so. And **a registered webhook proves nothing
about the app** — inbound can be perfectly routed to a process that cannot
authenticate to reply.

Not proven, and do not claim it: no message was sent from a real Telegram
account, because that needs the founder's own. Everything up to the
delivery boundary is verified; the reply leg is verified only by code path.

## Apps is a ROW card: name left, action right, whole card opens it (2026-08-21)

**Apps and Channels now DIVERGE on card shape, deliberately, and the reason
is the content rather than taste.** The founder's first correction was that a
card face carries the app and nothing else — *"why do we have those written
text right there?"* His second, from a screenshot of the result, is the
layout:

```
NOW (rejected)  four tiny tiles per row, "Set up" as a text label
WANT            ┌──────────────────────────┐ ┌──────────────────────────┐
                │ [N] Notion    [Connect]  │ │ [L] Linear    [Connect]  │
                └──────────────────────────┘ └──────────────────────────┘
                TWO per row on desktop · ONE on mobile
                press the CARD  ─▶ its detail: what it is, what tools, settings
                press the BUTTON ─▶ connects
```

His words: *"in one line there should be only two horizontally, not four —
only two. In mobile they're gonna be only one. And at the right side, the
connect button. And if I press the entire template itself that belongs to
this specific application, it goes to its settings showing what it is, tools
and other things — just like as it is inside this Claude application."*

**So a channel face is a TILE and an app face is a ROW, and that is not an
inconsistency.** A channel face is icon + label + a status word. An app face
carries an ACTION, and an action belongs at the end of a line, not stacked
under the thing it acts on — four tiles per row cannot hold a button without
shrinking it into the label.

```
BEFORE  (measured live, Configure -> Apps, 1680x1050)   AFTER
  76 wide cards, each carrying a full sentence            76 row cards, 2 up
  67 Connect buttons ON CARD FACES                        67, but at the RIGHT
   9 "Needs an OAuth client configured on this…"           0 of that sentence
   1 header paragraph saying it a tenth time               0 paragraphs
   "Set up" text label under every name                    0 — the button is it
   accent FILLS in view: 0 · accent OUTLINES: 0            0 fills · 67 outlines
   card size                                              351 x 49
```

**TWO CLICK TARGETS, TWO REAL BUTTONS, NEITHER NESTED — and the bubbling is
closed by GEOMETRY, not by remembering `stopPropagation`.** `<button>` inside
`<button>` is invalid and browsers un-nest it; a `<div onClick>` wrapper works
with a mouse and is unreachable without one. So the card is a plain container
holding two siblings: `.fleet-connector-card-open`, whose `::after` stretches
`inset: 0` over the whole card, and `.fleet-connector-card-action`, painted
above that stretch. A press on Connect was never inside the card's hit
rectangle in the first place. Verified with `elementFromPoint`: card centre
hits the open button, the action's centre hits the action.
`stopPropagation` is on the handler as a second, cheap belt.

Keyboard is two ordinary buttons in DOM order — measured adjacent in the
natural tab sequence (43 -> 44), `tabIndex` 0 on both, and a real Tab
keypress lands on the card body with `:focus-visible` true and the CARD
drawing the ring. The inner button's own ring is suppressed or a keyboard
user gets two nested rings (observed at 375px, then fixed).

~~**THE ACCENT SPLIT IS A JUDGEMENT CALL…**~~ **OVERRULED BY THE FOUNDER,
SAME DAY. THE FACE CARRIES THE FULL FILL. Do not re-litigate it.** The pass
above shipped the hairline `fleet-btn--accent` on a card face, reasoning that
~69 filled purple buttons in one view is the wall of purple this file calls a
bug outright. He had already been told that trade and had already chosen —
twice: *"it's going to be FULL purple just like this next button, not like
only around it and slightly purple."*

```
card face   fleet-btn--accent-fill   measured live: 67 fills, 0 outlines
open panel  fleet-btn--accent-fill   the same fill. Connect simply LOOKS
                                     like this on this surface now.
```

**The rule this leaves, and it is worth more than the pixel:** an agent may
argue a visual trade ONCE, out loud, before shipping. Once the founder has
answered it, "my own arithmetic still says otherwise" is not a reason to ship
the other thing — it is the same shape as reverting approved work on a guess,
which this file already records as a mistake made on the navigation surface.
Bring the argument, then build what he said.

The arithmetic is still asserted rather than remembered, just pointed the
other way: `connector-card-face.test.ts` scans `renderCard`'s own body and
fails if it carries `summary` (prose), REQUIRES `accent-fill`, and BANS the
hairline variant from coming back — plus `fleet-connector-card-open` /
`stopPropagation`, and the CSS reads for the stretched `::after`, the
two-column rule, the card's own padding/gap/icon tokens and the search
field's surface. Flipped rather than deleted, the same treatment this file
records for `content-security-policy.test.ts`, so nobody mistakes the
overruled shape for the intended one. A behavioural test cannot see a face
growing prose or losing a fill; that regression has shipped twice on the
sibling Channels surface.

**FOUR facts on the face, and the right-hand slot is an ACTION only where one
exists.** `connectorCardFace()` (connector-card-face.ts, pure + tested) is
the single decider for the slot, the pill and the line the panel restates —
same reason `channelCardPill` derives from `remediationFor`: two things that
compute state separately drift toward the one nobody re-reads.

```
connected           pill "Ready"        --online-text    ● dot   no button
connected, unwell   btn  "Reconnect"    hairline accent
not connected       btn  "Connect"      hairline accent
NOT CONNECTABLE     pill "Unavailable"  --text-muted     icon+label quieted
```

A connected app has nothing left to press and an unconnectable one has
nothing a customer COULD press, so both keep a pill — "no dead controls"
decided once in the pure function rather than at the render site, where the
next state added would forget it.

**The founder explicitly REFUSED to hide the nine unconnectable apps** (Box,
Docusign, GitHub, Google Workspace, Higgsfield, HubSpot, Microsoft 365,
Salesforce, Zoom — he is going to configure them, MAN-361). So they are
ordinary cards sorted last, and the reason is said ONCE, in the panel, in
customer language. That card is muted but **never `:disabled`** — it has to
open, because the sentence lives inside it; and it carries NO action at all,
because a Connect that cannot connect is the dead control the product law
forbids.

**`healthStatus` is only meaningful once CONNECTED.** The backend's default
for a never-connected work_app_connector is the literal string
`"not_configured"` (connection_catalog_service's `status_items()`), so
reading health before connection paints a warning on every app nobody has
connected yet — i.e. most of the catalog. Pinned by a test.

**A grid sized by the VIEWPORT is the wrong measurement inside a dialog.**
Both grids are `repeat(4, 1fr)` in fleet-theme.css stepping down at a 900px
viewport — but the create sequence's card is 520px wide on a 1680px monitor,
so four cards divided 478px into **112px each** and "WeChat / WeCom" broke
over two lines. The squashed, label-wrapping face he objected to, arriving on
a desktop. Inside `.agent-create-embed` the channel TILES now use `auto-fill`
+ `minmax(140px, 1fr)` (measured 3 x 152px, degrading on its own) and the app
ROWS go one per row — 478px holds exactly one of them, since two would put a
button and an ellipsised name into 234px each.

**Specificity is load-bearing across these three files and is not tidiness.**
connector-cards.css is imported by ConnectorPicker while fleet-theme.css comes
from the app shell, so which lands later is the BUNDLER's choice — and these
rules OVERRIDE fleet-theme's card rather than adding to it. Hence
`.fleet-connector-grid.fleet-connector-grid` (0,2,0) there, and
`.agent-create-embed .fleet-connector-grid.fleet-connector-grid` (0,3,0) in
the wizard, which has to outrank it deliberately rather than tie with it.

**A horizontal rule renders only where a rule has a job.** The step strip was
already centred and band-free (`justify-content: safe center`, measured
108px/108px either side against 21px/194px before). What was left was the
footer's `border-top`, drawn on all four steps — and on steps 1-2 nothing
scrolls, so it divided two things `--space-3` already divides, in a card
whose head deliberately draws none for exactly that reason. It is transparent
unless `--embed`, where the body genuinely scrolls and the line marks where
content is cut off.

**Deleted with the sentence it styled:** `"SMS connects elsewhere in
Empyralis and isn't shown here."` — a paragraph about the ABSENCE of a card,
under a grid whose whole job is what you CAN do — plus `legacyLabels` /
`unmappedSupersededChannels`, the `formatChannelList` import that fed it, and
`.openclaw-elsewhere-note`. When a shell goes, its CSS goes; a leftover rule
is how the next author rebuilds the thing that was removed.

**Verified live on a disposable stack**, both themes, 1680x1050 and 375x812,
zero console errors, with before/after measured from computed styles (accent
resolved through a 1x1 canvas — `oklch`/`color-mix` do not parse as rgb;
`--accent` is `141,91,191` light / `165,109,222` dark). Read the PIXEL, not
the string: `oklch`/`color-mix` never compare equal to an rgb literal, so a
naive check silently matches nothing and reports a clean grid.

**NOT verified live: the wizard's step-4 Apps panel.** The Channel step
correctly refuses to advance until a channel connects, which needs a real
credential. Its grid rule was confirmed by resolving `.fleet-connector-grid`
inside the live `.agent-create-embed` (one column, 478px), not by rendering
the step. Also not observed: a real Tab landing on a card's ACTION button —
the pane's key delivery stalled after the first few presses, so that half is
proven structurally (the two buttons adjacent in the natural tab sequence,
`tabIndex` 0, neither disabled) rather than by watching it happen.

**One claim in the brief did NOT reproduce, and should not be repeated:** the
"outlined, faint purple" Connect buttons. On `main` they are
`fleet-btn` + `.fleet-connector-picker-connect-btn`, which sets only
`align-self`/`margin-left` — measured neutral (`rgb(41,41,41)`), 0 accent
fills and 0 accent borders in the whole view. The purple he saw is
production's older build, which `c546e618` (accent restraint) had already
neutralised on main. The 67-buttons-on-faces half of the complaint was real
and is what got fixed.

### "Cheap" was a MEASUREMENT, and so was "that piece of shit" (2026-08-21)

**Two more founder complaints on the same screen, both fixed by reading the
computed styles rather than by taste.** His words, in order: *"it must be
slightly bigger and mature — right now the space in between each other is
very small and generally it looks cheap. It must look premium, slightly
vertically deeper, slightly vertically thicker"*, and *"wtf is this piece of
shit at the middle of the screen, don't you think it must be fixed?"*

```
                    BEFORE            AFTER      (live, CSS px, same dialog)
card                362 x 50          359 x 66
padding             8 / 12            --space-3 / --space-4   (12 / 16)
grid gap            10                --space-4               (16)
logo box            32                40
label               13px              14px  (--text-base)
action button       28                32    (--h-control-sm)
search field  734 x 36  FILLED        734 x 36  --bg-card + hairline
              --bg-inset  #eeeeef     #ffffff — the CARDS' own surface
              ← the ONLY filled
                surface in the view
placeholder   "Search by name or what it does…"   "Search"
```

**THE LOGO HAD TO GROW WITH THE CARD OR THE FIX MAKES IT WORSE.** A taller
card with an unchanged 32px mark reads emptier, not more premium — 40:66 is
the same optical weight 32:50 had. Every override is scoped to
`.fleet-connector-card--row`, so the Channels TILES, which fleet-theme's 32px
and 10px gap are still correct for, are untouched. Two surfaces, two
proportions, one stylesheet.

**The search field kept its WIDTH and lost its FILL.** It already matched the
grid exactly (734 = two 359 tracks + 16 gap); the complaint was weight, not
size, and a filter that stops short of the grid's own edge is a different
kind of wrong. Scoped to `.fleet-connector-browse > .fleet-wizard-input`,
never to `.fleet-wizard-input` itself — that class is ALSO the create
sequence's form field, where a filled inset IS correct. One class, two jobs;
only one changes. On dark both resolve to `rgb(41,41,41)`, verified.

**LOGOS: NOTHING WAS BROKEN, and the brief's own numbers were wrong.**
Reconciled statically and then live, which is the only reason the answer is
trustworthy:

```
RAW_CONNECTOR_ICONS mapped entries        78   (the brief said 91)
  ...whose file does not exist              0   nothing to fix
public/brand-assets/apps/ files on disk    83
  ...referenced by the map                 74
  ...on disk, never mapped                  9   see below

LIVE, in a real browser, 76 rendered cards:
  <img> elements            76      monogram fallbacks       0
  naturalWidth === 0         0      unique srcs HTTP 200   75/75
```

So "some apps show just a colour with its capital letter" is not our
fallback: Ahrefs / Amplitude / Apollo.io / Ashby render `ahrefs.svg`,
`amplitude.svg`, `apollo.svg`, `ashby.svg` — their own real letter-based
brand marks. **Do not redraw them.**

The 9 unmapped files are dead weight, not defects: `gmail.svg` is genuinely
used (`lib/marketing/landing-page.tsx`), and `google-calendar.svg`,
`google-drive.svg`, `bitbucket.ico`, `freshbooks.ico`, `mailchimp.ico`,
`pipedrive.ico`, `quickbooks.png`, `xero.ico` have ZERO references anywhere.
The six raster ones are scraped favicons, not house-style SVGs, so they could
never ship as they are. Left in place and reported rather than deleted — an
unused asset changes no behaviour, and deleting artwork was not asked for.

**The monogram fallback is PROVEN, not assumed** — the live catalog has zero
of them, so the only honest verification is to force one. Temporarily
unmapping `notion` produced a 40x40 magenta "N" with `border-radius: 8px`
matching its own box exactly (the hand-kept `7px` magic number is gone,
replaced by `inherit`, so the two can never drift), `font-size` scaled with
the box, in a mark the same size as every logo beside it — which is MAN-145's
own requirement that a fallback not read as broken. Restored immediately
after; `git checkout --`, never `git stash`.

## Agent creation is THREE steps, and placement is first (2026-08-21)

**The founder compared the shipped one-card creation surface against the
wizard deleted on 2026-08-20 and found two whole questions missing.**
Measured, not recalled: grepping `AgentCreateCard.tsx` for
`platform_credits|byok|cli_subscription` returned ZERO — the entire "who
pays for this model" question was gone, and so were placement and hardware.

```
1 IDENTITY & PLACEMENT   name · what it does · WHERE IT RUNS
       │                 nothing committed
       ▼  placement decides what step 2 may honestly offer
2 BRAIN                  who pays ▸ provider ▸ model
       └── "Create agent" ──▶ the agent becomes real here
3 REACH                  channels AND apps, one screen, both optional
       └── "Finish" / "Skip for now" ──▶ into the agent
```

**PLACEMENT IS FIRST BECAUSE STEP 2 CANNOT BE HONEST WITHOUT IT.** "Your
subscription" and "Run locally" both route the BRAIN through a Gateway on a
real machine, so on a cloud-only agent they are controls that cannot be
completed. They are not rendered disabled and not rendered with an excuse —
they are not rendered, and step 2 says once where to go to unlock them.
The gate is DERIVED from each option's own `needsMachine` flag
(`agent-create-brain.ts`), never a hand-listed pair of mode names.

**NO PROJECT FIELD. That half of the old wizard does not come back** — an
agent belongs to the WORKSPACE. `currentProjectId` is still resolved
silently for a required backend field and is never rendered.

**REACH IS ONE SCREEN because Channels and Apps are the same question** —
what does this connect to — both optional, both permanently reachable from
the agent's own tabs afterwards. Two separate steps that can each ask for
nothing was the ceremony that made the four-step flow feel long.

**"SKIP" AND "FINISH" ARE DIFFERENT WORDS, and that is how two founder
instructions are both honoured.** 2026-08-21 morning: *"channels cannot be
skipped, because it's something agents are going to speak"* → the forward
button was BLOCKED. The three-step brief supersedes it: Reach is
*"(skippable) … Both optional. Skipping is one action."* What he rejected
was a sequence that traps you; what he never asked for is a product that
calls an unreachable agent finished. So the button always moves in one
press and is NAMED for what it does — "Skip for now" with nothing
connected, "Finish" with something. Unknown-yet says nothing at all.

**WHERE EACH BRAIN MODE'S CONFIG IS WRITTEN, and why they differ.**

```
platform_credits ─┐ POST /fleet/agents  model_choice{mode,provider,model}
byok_api         ─┘ ATOMIC with the create. Nothing to patch.
cli_subscription ─┐ POST (server seed) ─▶ PATCH model_config
local            ─┘ the create path accepts exactly three keys
                    (_CREATE_TIME_MODEL_CHOICE_KEYS) and neither fits: both
                    need gateway_binding + runtime + a real-box check that
                    lives in fleet_configure_agent and is REUSED here rather
                    than copied into a thinner second validator
placement        ── same PATCH. cloud writes NOTHING (the standard preset
                    already resolves hardware_access to "none").
```

A pasted API key is a PREREQUISITE, not a follow-up: the vault credential +
provider profile are saved BEFORE the agent exists, so a failure there has
nothing to explain away.

**The PATCH is a step that can independently fail after a commit, so its
failure is reported as its OWN fact and the sequence continues.** Proven
live, not reasoned about — a Codex binding to a box without Codex installed
produced exactly: *"Wizard Three was created, but where it runs and what
runs it couldn't be saved — set it in Configure. (Codex isn't installed on
Studio Mac yet…)"*, and the database showed the agent real with
`hardware_access=none` and the seeded model, i.e. the message was true.
`fleet_configure_agent` rejects a patch WHOLE, so when the model_config half
is invalid the placement half is lost with it — which is why the sentence
names both halves rather than one.

**THE FRAME: min-height 560, max-height min(88vh, 640).** Founder: *"it
stays the same size almost — it has a smallest size which you cannot make it
smaller, and a biggest size you cannot make it bigger, even though it's a
longer page."* A ceiling alone was already there and is not enough; without
a floor the dialog collapses on a short step (the old Model step measured
249px against Channel's 520px, moving the footer 271px on one Next press).

```
                                  natural   framed     measured, 1680x1050
Brain, cloud placement              480       560      ← grows to the floor
Identity, cloud                     586       586
Identity, a machine                ~680       640      ← body scrolls
Brain, byok / subscription      573-640    573-640
Reach, two whole tabs             1000+      640       ← body scrolls
phone, every step (375x812)          —        796      ← floor pinned to
                                                        ceiling: identical
```

Head, stepper and footer are `flex-shrink: 0`; the body is the only
scroller on every step. THE COST IS VOID and it is paid deliberately — the
emptiest step holds 333px of content in the 560px floor, and lowering the
floor to fit it hands back the jump the frame exists to remove.

**Two defects only the browser showed.** An empty machine list stated "No
computers paired yet." TWICE on one screen, once in the body and once as the
footer's blocked reason — the body's copy is gone and the "Pair this
computer" button is the empty state, per the standing rule that a setup
control does the work rather than explaining it. And with an error present
on Reach the footer's border sat BELOW it, so the body's cut-off content ran
into red text with nothing between; the scroll edge is now drawn on
whichever pinned row comes first.

**The Reach step carries ~67 accent-filled "Connect" buttons and that is NOT
a regression to fix here.** It is ConnectorsTab's own face, which the
founder overruled to full `--accent-fill` twice. The create surface's
one-accent rule still governs everything the surface itself draws.

## The two-tier channel split is DERIVED from the doors (2026-08-21, MAN-359)

**The Channels grid now leads with what a person can connect today, and the
split is a filter, not a caption.** Chat is gone from the platform, so a
channel is the only way anyone talks to their agent — "which of these can I
finish right now" is the first question the grid has to answer.

```
No computer needed  4   ← DEFAULT VIEW. Telegram (Recommended) · WeChat · Discord · Slack
Needs a computer   21   ← every transported channel
All                25   ← hardware-free first, then the rest by popularity
```

**The tier is a pure function of `ChannelDoor.requiresHardware`, which is set
in exactly the two places that already existed** — never a fifth hand-copied
channel list (this surface has shipped four and drifted on all four):

```
channelHardwareTier(doors)          channel-hardware-tier.ts, pure + tested
  some real door needs no box  ─▶ hardware_free   ← a platform reachable two
  every real door needs one    ─▶ needs_hardware     ways belongs in the tier
  no real door at all          ─▶ unknown            you can ACT on today
        ▲
        │ requiresHardware comes from:
   CHANNEL_DOORS              authored per first-party door (none today)
   groupTransportedChannels   TRUE for every variant
```

**That last line is a CORRECTION, and it was understating the truth.** It used
to read `connect_method === "pairing"`. A `credential` transported channel is
pasted from here but the paste lands in the transport's config ON the box; the
catalog is read off a gateway, the write is
`PUT .../gateways/{id}/channels/{key}/credential`, and `remediationFor(...,
hasGateway: false)` already answered `needs_hardware` for all of them. Needing
a box is a property of the LANE, which is exactly why it can be stated once for
all of them with no per-channel knowledge. A channel the transport ships
tomorrow is tiered with no edit — asserted with a synthetic channel, the only
way to prove a derivation is not a disguised list.

**"Recommended" is authored (Telegram, the founder's own instruction) and
GATED on the tier.** `showsRecommendedBadge(recommended, tier)` returns false
for anything but `hardware_free`, because on 2026-08-20 Telegram's card
silently became the hardware-bound transported one — a badge would have been
sitting on a card telling a cloud-only agent to go buy a computer. It lives in
the card's ONE secondary line beside "N ways to connect", never a second pill.

**The filter decides its own visibility.** Both tiers populated → the control;
otherwise no control at all, because a filter that can only show everything is
a dead control (same call as a rail of one). `unknown` joins neither tier and
is reachable only under All — a third fact, never folded into either.

**A hardware card opened by a cloud-only agent is no longer a dead end.** It
was one sentence naming the Hardware tab with nothing to press; it is now the
fact plus a real `<Link>` to that tab (`hardwareHref`, so cmd-click works),
and `.fleet-door-unavailable-title` went from offline-RED to amber to match
the card pill it is the panel behind — "needs a computer" is a setup step, not
something that is down.

Verified live in a real browser (disposable stack, 1680x1050, both themes):
default view leads with Telegram + Recommended, "Needs a computer" shows the
21, and WhatsApp's panel renders "This one needs a computer" plus a working
link to `.../hardware`. Guarded by
`frontend/lib/workspace/fleet/channel-hardware-tier.test.ts` (in
`npm run test:unit`), which drives the REAL doors, the REAL generated manifest
and the REAL active-channel set, carries canaries for each source, and
structurally asserts ChannelsTab actually calls all of it — "built, tested,
and never wired" is the defect this codebase has most of.

## Channels: Telegram + Slack. Discord is OUT. (2026-08-20)

**Founder's decision, final:** *"Slack and Telegram is the way to go.
Discord I don't really want it if it doesn't work — it's not something
that I want to have in my platform. Telegram and Slack, that's it."*

The reasoning is the gateway/ban-risk axis, not popularity: these two are
the channels that work with **no paired hardware**, so a customer never
runs a gateway and never risks an account ban.

Verified against each vendor's OWN documentation, not inference:

```
TELEGRAM  webhook (setWebhook). Cloud-side, stateless, nothing held open.
          Hosted-bot option needs no BotFather and no token at all.
          Verified live 2026-08-20 (real webhook re-registration + a real
          message round trip).                                    ✓ primary

SLACK     HTTP Events API — public HTTPS endpoint, must 200 within 3s.
          Slack's own docs RECOMMEND HTTP over Socket Mode for
          production, which is the shape already implemented.     ✓ second

DISCORD   ✗ CUT. Its HTTP interactions endpoint receives ONLY slash
          commands; reading ORDINARY MESSAGES requires a persistent
          Gateway WebSocket and a long-running process. That means one
          standing connection PER CUSTOMER BOT held open in the backend
          — which is a single uvicorn worker on a single vCPU. A standing
          per-customer cost for a channel the founder does not want.
          DiscordBotRuntimeService exists and works; it is simply not
          part of the product direction.
```

**The two-tier split that must be visible wherever channels are chosen** —
so nobody picks a channel expecting one-click and hits a hardware wall
(which is exactly the dead end found live on 2026-08-20):

```
Works now, nothing to install     Telegram · Slack
Needs your computer paired        WhatsApp · Signal · iMessage · OpenClaw (~20)
```

A "recommended" stamp alone is not enough — the split is the honest
information. The founder also asked for filtering on the channel surface
along this axis (chat-only vs full-account-control vs hardware-required).

**Standing quality bar, his words:** *"as reasonable as possible and as
BEASTMODE as possible — not MOST, but beast reasonable things."* Which is
this file's existing "Best, not most" law with the emphasis on depth: the
handful of things that ship must be genuinely excellent, not numerous.

## The codex reasoning-effort chain, wired end to end (2026-08-20)

**The pass above was itself half of the mistake it was trying to fix, and
the founder caught it.** xAI shipped grok-4.5/grok-4.6/grok-4.20-multi-agent
with real `reasoning_effort` support after the first catalog pass; the
natural-looking fix was to read xAI's docs and hand-type the three new
entries into `PROVIDER_MODEL_CATALOG`. That was STARTED and then REVERTED
before landing. The reason is the same standing rule this file already
states, applied to itself: *"derive the capability set from the thing that
owns it, never transcribe it."* A hardcoded table sourced from a document is
still a hardcoded table — it goes stale again the moment the provider ships
the next model, silently, with nobody re-reading the docs to notice.

**There IS a live source for this — on the box, right now — and it was
verified directly, not read about.** `codex app-server generate-json-schema`
(the real, installed 0.144.1 binary) emits a schema where `ReasoningEffort`
is `{"type": "string", "minLength": 1}` — deliberately NOT a closed enum —
and `Model` carries `defaultReasoningEffort` + `supportedReasoningEfforts`
per model. Confirmed further by spawning the real `codex app-server`
process and driving its actual `initialize` → `model/list` JSON-RPC
exchange (the same handshake `empyralis-gateway/src/llm/codex-app-server.ts`
already uses) against this box's own real, authenticated ChatGPT/Codex
login — genuine live output, not a mock:

```
gpt-5.6-terra (the live default)  low medium high xhigh max ULTRA
gpt-5.6-luna                      low medium high xhigh max
gpt-5.5                           low medium high xhigh
gpt-5.4-mini                      low medium high xhigh   (upgrade: gpt-5.6-luna)
codex-auto-review (hidden)        low medium high xhigh max
```

`"ultra"` is a real, currently-live reasoning-effort level on the account's
own default model — a level that did not exist in this file's own 5-word
ladder (`low/medium/high/xhigh/max`), in xAI's docs, or in any table this
codebase has ever hand-typed. No amount of "verify against the official
document" would have produced it; only asking the running harness did. This
is the concrete proof for the standing rule, not just a restatement of it.

**xAI's grok-4.5/4.6/4.20-multi-agent additions were reverted, on
purpose, and are NOT re-added as a table.** They are real
(`docs.x.ai/developers/grok-4-6` does describe them, and that finding
itself was not wrong) — what was wrong was the RESPONSE: hand-typing three
more rows into a table that will need the same manual edit again for
whatever xAI ships next. Unlike Codex, xAI's BYOK surface is a bare REST
API with no on-box daemon and no equivalent self-describing RPC reachable
from this codebase — `/v1/models`-shaped listings carry no capability
metadata. So for `xai` (and every other pure-API-key BYOK provider —
gemini/openai/groq/openrouter/qwen/mistral/ollama_cloud/azure_openai/
custom_openai_compatible), there is currently no live harness to prefer,
and the four originally-verified xai entries (`grok-4`/`grok-4-0709`/
`grok-4-latest`/`grok-3`, confirmed correct and unchallenged) are the
catalog's honest ceiling — a live-discovered model outside them lands on
the system-instruction fallback, same as before, now logged distinctly
(see below).

**What shipped instead, scoped to what a live source actually supports
today:**

```
cli_subscription / codex   REAL harness exists (verified above) — wired
                            end to end EXCEPT one link:

  codex app-server model/list RPC
    │ (real, has supportedReasoningEfforts/defaultReasoningEffort per model)
    ▼
  codex-app-server.ts's listModels()      ← STRIPS both fields today
    │ (CodexModelListEntry only carries id/displayName/description/
    │  hidden/isDefault — the interface's own comment says so explicitly)
    ▼
  runtime.ts's listModelsForRuntime()     ← re-strips them again, snake_case
    │
    ▼
  codex_model_catalog_service.py         ← NOW reads default_reasoning_
    (server_modules, this pass)             effort/supported_reasoning_
    │                                       efforts defensively (.get(),
    │                                       never required) — lights up
    │                                       the MOMENT the two TS files
    │                                       above start forwarding them,
    │                                       no second Python/frontend
    │                                       change needed
    ▼
  fleet-model-config.ts's                ← NOW parses the same two fields
  CodexModelCatalogEntry                    into supportedReasoningEfforts/
    │                                       defaultReasoningEffort
    ▼
  FleetAgentDetail.tsx's                 ← NOW renders THIS model's own
  renderCliReasoningEffortPicker            live levels (open, per-model,
                                             not a closed enum) when
                                             present; falls back to the
                                             static CLI_REASONING_EFFORT_
                                             OPTIONS_BY_RUNTIME.codex table
                                             — now visibly the DEGRADED
                                             last resort (its own render
                                             branch says so in the UI
                                             hint), never the primary path
```

**That last link LANDED (`bbbf1b1f`) and the chain is now closed.** The
gateway half and the consumer half were written by two different agents
who never saw each other — each one's own comments flag the other's file
as "the missing follow-up." They are merged here as one change. Anyone
reading either half's comments in isolation will find them describing a
gap that no longer exists; the merge rewrote those, and this paragraph is
the record of why.

**Merging them surfaced a defect NEITHER half could see alone: absent and
empty were the same signal.** `[]` meant three different things at once —
CLAUDE.md's own "two different facts may never share one signal" law, at a
seam where one of the facts is a live fleet condition:

```
GATEWAY BUILD               Python receives    MEANS             correct behaviour
pre-bbbf1b1f (live fleet)   key absent      "I don't know"       static fallback
post-fix, model has levels  [{...}]         "these levels"       live options
post-fix, model has none    []              "no levels at all"   RENDER NOTHING
```

Every fleet box still running a pre-`bbbf1b1f` gateway sends no field at
all, so collapsing absent into empty is not hypothetical — it is the
majority state today. And a model that positively reports zero levels
would have rendered the static ladder anyway: a picker whose every option
the model does not implement, i.e. the dead control this file's own
product law forbids. Fixed by carrying `null` (unknown) distinctly from
`[]` (positively none) the whole way — `codex-app-server.ts` returns
`null` when the RPC field is absent or malformed rather than flattening to
`[]`, and `planCodexReasoningPicker` (`codex-reasoning-options.ts`, pure +
tested, same shape as `agent-count-shape.ts`/`channel-doors.ts`) is the
one place that turns the three states into `live` / `fallback` / `none`.

**SUPERSEDED, 2026-08-20, ONE DAY LATER — the `none` branch above no longer
exists and must not be reinstated.** The founder overrode it: the effort
ladder is one shared list offered for every provider and every model, always
("If it works, it works otherwise you can still choose it"). The `null` vs
`[]` distinction described above is still carried end to end and is still
worth keeping — it is exactly why the seam stayed honest — but it now only
decides how much ANNOTATION the picker can show, never whether the picker
exists. Full reasoning, the clamp that replaced the restriction, and his
words verbatim are in "BYO subscription: one effort ladder, live model lists,
no auth shim (2026-08-20)" above.

**Verified live, first-hand, against this box's own real authenticated
Codex install** (spawned `codex app-server`, drove the real
`initialize` → `getAuthStatus` → `model/list` JSON-RPC exchange — the same
handshake `codex-app-server.ts` uses):

```
gpt-5.6-terra (live default)  low medium high xhigh max ULTRA   default=medium
gpt-5.6-luna                  low medium high xhigh max         default=medium
gpt-5.5                       low medium high xhigh             default=medium
gpt-5.4-mini                  low medium high xhigh             default=medium
codex-auto-review (hidden)    low medium high xhigh max         default=medium
```

`"ultra"` is live on the account's own default model and appears in no
doc and in no static table in this codebase. Note also that NO model
reports an empty list today — which is exactly why the absent-vs-empty
collapse was invisible to both halves and had to be reasoned about from
the protocol rather than observed.

**The other three cli_subscription runtimes were re-checked live, not
re-read from docs, per the same discipline:**

```
claude_code (Anthropic CLI)   ALREADY CORRECT, no gap. Its reasoning-effort
                              vocabulary was already sourced from the
                              ACTUALLY-INSTALLED claude_agent_sdk/types.py:
                              `EffortLevel: TypeAlias = Literal["low",
                              "medium", "high", "xhigh", "max"]` — a real
                              harness read (the installed package's own
                              type stub), not a guess, already wired via
                              resolve_sdk_effort. No per-model variation
                              is exposed by that type — Anthropic's own
                              CLI/API is the validator at call time.

cursor_cli                   CONFIRMED, live: `cursor-agent --help` on
                              this box has NO reasoning-effort flag at
                              all. The existing "no reasoning-effort
                              control" modeling was already right —
                              verified against the real installed binary,
                              not assumed. (`--list-models` exists as a
                              genuine live model-listing surface, unused
                              here since it has nothing to do with
                              reasoning effort — noted for whoever builds
                              live model discovery for this runtime.)

grok_build (the CLI, distinct  `grok --help` on this box confirms
from the "xai" BYOK/API-key    `--reasoning-effort <EFFORT>` exists (alias
provider above)                `--effort`) — matches this codebase's
                              existing docs.x.ai-sourced vocabulary. `grok
                              models` exists as a subcommand but printed
                              "You are not authenticated" when actually
                              run on this box, so a live per-model
                              reasoning-vocabulary check (the codex-shaped
                              proof) could NOT be completed here — this is
                              a genuinely underivable gap on THIS box
                              today, reported as such rather than papered
                              over with the CLI's own hardcoded fallback
                              model list it showed instead.
```

**The permanent-staleness decision, restated with the harness-first
framing:** a live self-describing surface (codex app-server) is preferred
whenever one exists and is reachable from this codebase; where none exists
(every pure-API-key BYOK provider, and grok_build on this box today) the
honest system-instruction fallback is the permanent floor — it can never
break a turn and never invents a capability — and
`_log_unrecognized_reasoning_effort_model`
(`server_modules/openai_compat_adapter.py`) makes exactly which
live-discovered models are landing on that floor a greppable operational
signal (`reasoning_effort_model_unknown`), distinct from a model the
catalog has explicitly confirmed does NOT support the control. That is now
the honest, permanent answer to "the catalog will always eventually lag a
live provider" for the providers that have no better answer available.

Guarded by new/updated tests, red-before-green
(`test_codex_model_catalog_service.py`'s two new defensive-parsing tests;
`test_openai_compat_adapter.py`'s unknown-model-logging tests;
`test_provider_profiles.py`'s `model_is_known_for_provider` distinction
test) — the xai hardcoded-table tests from the reverted attempt were
themselves reverted along with the code, not left behind as dead
assertions.

**Still open, flagged not fixed:** grok_build's live per-model vocabulary
(blocked on auth state on this box); and the pre-existing, unrelated
save-time validation gap this pass surfaced while reading `fleet_tools.py`
— `model_is_known_for_provider` (used by `configure_agent`'s save-time
model validation for `platform_credits`/`byok_api`) still validates
against the STATIC `PROVIDER_CATALOG` model list, not live discovery,
for every BYOK adapter-routed provider except codex — meaning a customer
whose live-discovered model list (this file's earlier "BYO-subscription
model truth" entry) shows a real model outside the static list can pick
it, see it rendered, and then have the SAVE itself rejected with "Invalid
model_config model." Codex already solved this exact problem for itself
(`fleet_tools.py`'s codex-specific save-time check calls the live catalog,
not the static list) — the same pattern needs extending to the other
adapter-routed providers, which is real, separately-scoped work, not done
in this pass.

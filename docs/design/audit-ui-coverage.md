# Audit: UI Coverage of This Week's Backend Work

Read-only, HEAD `ce97dc404` (current prod). Question: of everything built this
week, what can a user actually see and use today, where exactly, and what is
backend-only? Every row is file:line-verified — frontend claims are grep-
verified against the actual fetch/endpoint calls, not inferred from the
backend route existing. Left-rail north star: Inbox · Conversations ·
Projects · Agents · Hardware (`frontend/lib/workspace/fleet/PrimaryRail.tsx:40-44`,
confirmed live). Agent detail: 9 tabs — Overview, Model, Work, Channels,
Connectors, Tools, Capabilities, Hardware, Memory
(`frontend/lib/workspace/fleet/FleetAgentDetail.tsx:173-182`), plus a
separate Chat view.

---

## The scoreboard

| # | Feature | Backend | UI surface | Verdict |
|---|---|---|---|---|
| 1 | Project tasks | Built — `project_tasks_service.py` (505 lines) + 4 REST routes: `GET/POST /api/w/{ws}/fleet/tasks`, `PATCH .../tasks/{id}`, `POST .../tasks/{id}/assign` (`routes_fleet.py:243,277,314,353`) | **NONE.** Grep of `frontend/app` + `frontend/lib` for `fleet/tasks` or `project_tasks` returns zero hits. `WorkTab.tsx`'s "Plan" section (`tabs/WorkTab.tsx:312-345,651-688`) looks similar but is a different, ephemeral data model — a live snapshot of the current trace's `plan.updated` event, not the persisted `project_tasks` row | **INVISIBLE** |
| 2 | Workspace members + invites | Built — `routes_workspaces.py`: `POST/GET /workspaces/{id}/invites` (create/list, :904,958), `GET /workspaces/{id}/members` (:986), `POST /workspaces/invites/accept` (:1012). Commit `fdbbc7b09` | **NONE**, on either side of the flow. Settings page (`app/(account)/w/[workspaceId]/settings/page.tsx`, read in full) has Emergency Stop, Billing link, MCP servers, MCP API keys — no Members section, no invite button. No page anywhere calls `/invites/accept` or reads an invite token (grepped `invite_token`/`inviteToken`/`invites/accept` — zero hits). The one `invite/[code]/page.tsx` that exists is a **different, unrelated system** — pilot-program account invites (`/api/pilot/invites/validate`, `routes_pilot.py`), not workspace membership | **INVISIBLE** |
| 3 | Project documents / cookbook | **Not built.** `FleetProject` (`fleet-data.ts:54-68`) is exactly `{id, name, description?, agent_count?, status?, is_default?, icon?, tint?, metadata?, created_at?}` — `description` is one line, `metadata` is an opaque untyped bag with no document shape defined anywhere in the codebase | NONE | **NOT BUILT** (no backend to be invisible) |
| 4 | Agent-to-agent messaging | Built to explicitly refuse — `fleet_message_agent` (`fleet_tools.py:1614-1667`) always returns `{"ok": false, "error": "...not implemented..."}` and logs a `fleet_control` ledger event, `action="message_agent_refused"` | The refusal **does surface** workspace-wide: `fleet_control` is included (not filtered) in `/api/activity/timeline`, consumed by the Inbox page, `FleetHome.tsx`, and the rail's own Inbox badge (`fleet-data.ts:799-810`, `NOISE_EVENT_CLASSES = ["system_activity"]` only). Humanized title: "Message not deliverable (not implemented)" (`fleet_tools.py:251`). But the **per-agent Overview tab activity feed explicitly excludes `fleet_control`** (`fleet_tools.py:817-825`, `WHERE event_class != 'fleet_control'`) — so the one place a user would look at *that specific agent* to see why its message vanished shows nothing. No compose-a-message-to-another-agent UI exists anywhere | **PARTIAL** — failure is visible workspace-wide in Inbox, invisible on the agent's own page, no compose UI |
| 5 | Sub-agent delegation | Built, substantial (`runs_delegation.py` + `runtime_run_delegation_service.py` + `runs_execution.py`, ~7,700 lines; depth cap, role typing, retry, Rust routing gate) | **NONE.** No frontend file, `ToolDescriptor`, or product script calls it — its only caller anywhere is `scripts/empyralis_core_smoke.sh` (a bash smoke test). The `subagents_enabled` flag exists as a field on `FleetAgent` (`fleet-data.ts:47`) but nothing reads or writes it from any component — no toggle in Model/Capabilities tab or the create wizard | **INVISIBLE** (engine has no live caller at all, gated flag has no UI) |
| 6 | Memory (provenance/tiers/redaction/index) | Built — `derive_trust_tier` (`memory_service.py:1058`), `secret_redaction_service` redaction on the native write path, per-file 200-line/25KB caps, topic-file count caps | Memory tab (`tabs/MemoryTab.tsx`, read in full) is a genuine, working file browser: lists MEMORY.md + topic files, preview/edit/delete, file size. It shows **only raw file contents** — no trust-tier badge, no provenance ("who/what wrote this line"), no redaction indicator, no retrieval-honesty envelope, nothing about the self-maintaining index's own health | **PARTIAL** — the tab is real and useful, but is a plain file editor, not a provenance/trust view |
| 7 | Compaction / context-window state | Built and (per commit `65933432c`) now actually fires — `compaction_service.py`, `compaction_summary` rows, `agent_trace_service.emit_compaction_skipped` | **NONE** as a status surface. What *does* exist: a static per-model context-window size label next to the model picker (`model-capabilities.ts`, e.g. "200k"), and an owner-configurable **policy picker** — "Compact — summarize and continue" vs "fresh session" (`FleetAgentDetail.tsx:2817-2851`, `on_context_full`). Neither shows current fullness % or "compaction ran at 3:14pm." MAN-51's context-window view stays parked, matching memory notes | **INVISIBLE** (as a status view; a related config knob exists) |
| 8 | Per-agent spend by period | Built — `summarize_usage(scope, period)` (`usage_events_repository.py:231-243`) explicitly supports `period ∈ {day, week, month}`, grouping buckets server-side, at `GET /api/w/{ws}/fleet/usage` | **Every** frontend caller — `FleetAgentDetail.tsx:304,2789`, `WorkTab.tsx:60`, `agents/page.tsx:117`, `projects/page.tsx:147`, `projects/[projectId]/page.tsx:95,108`, `billing/page.tsx:92,121`, `PrimaryRail.tsx:191` — passes `period=day` literally. Billing page's 7d/30d toggle (`billing/page.tsx:34-37,69`) is a **client-side sum of daily buckets**, not a real `period=week`/`month` call, and it only exists on that one page. Every other page (agent Overview, Work tab) shows a single "today" number with no period control at all | **PARTIAL** — real, live, wired, but only ever shows "today" outside one page's client-computed 7/30-day chart |
| 9 | Agent liveness | Built — `deriveAgentStatus` (`gateway-box-picker.tsx:254-293`) is genuinely honest: derives `working/ready/degraded/offline/stopped/error` from `current_run_id` + real CLI/gateway runtime state, not just a heartbeat | **Shown** — status dot in `PrimaryRail.tsx:183,356`, `FleetCard.tsx:32`, `AgentsList.tsx:545`, and the agent detail header (`FleetAgentDetail.tsx:344`). Per-conversation "waiting" (needs approval/input) exists **only inside** `WorkTab.tsx`'s `classifyThreadStatus` (per-thread, not per-agent). No aggregate "N agents need you" badge exists anywhere — grepped Inbox, `PrimaryRail`, `FleetHome` for approval/pending language, zero hits | **PARTIAL** — real honest working/ready/offline/degraded/stopped, but no cross-agent "needs me" surface |
| 10 | BYO subscription providers (Grok Build, Cursor CLI) | Built — commit `4ee962f21`, device-code login, provider profiles | **Visible.** Both are entries in `SUBSCRIPTION_PROVIDERS` (`fleet-provider-constants.ts:22-29`), the single catalog file explicitly shared by "both the create-agent wizard and the agent detail Model tab" (file header comment), including reasoning-effort awareness for `grok_build` (`FleetAgentDetail.tsx:3488`) | **VISIBLE** |
| 11 | MCP (servers + 17 `empyralis_*` tools incl. 4 task tools) | Built — `mcp_server.py`, external-agent identity/roster (`mcp_external_agent_roster_service.py`), 17 `empyralis_*`-prefixed tools including `empyralis_list_my_tasks`/`empyralis_comment_on_task` (commit `fbd3076b1`) | Settings page has two real, functioning sections: **"MCP servers"** (`McpServersSection.tsx`, outbound — connect this workspace's agents OUT to a remote MCP server, with per-tool approve/deny) and **"MCP API keys"** (inbound — mint scoped keys for external clients like Claude Desktop to connect IN). The inbound key list shows only `label` / read-or-write / `created_at` — **no enumeration of the 17 tools a key actually grants**, no external-agent roster view | **PARTIAL** — both directions have working CRUD UI; neither shows what an external agent can actually do with a key |
| 12 | Workspace identity/name | Built — `name` column, settable at creation, `PATCH /workspaces/{id}` exists (`routes_workspaces.py:382-383`) | Rendered small, at the top of the left rail: a one-letter avatar + name text (`PrimaryRail.tsx:256-269`). Settable **once**, at workspace creation, via the onboarding "Workspace name" field (`workspace-setup-form.tsx:155-165`, required, placeholder "Acme Deal Room"). **No rename control anywhere afterward** — grepped for any frontend `PATCH .../workspaces/{id}` call, zero hits. Falls back to generic "Empyralis" whenever a workspace's name equals its raw id (never explicitly named) | **PARTIAL** — real, but small, set-once, never Linear-style prominent or switchable |

---

## INVISIBLE — built but unreachable, ranked

Ranked by (user value × how little UI it would take to surface):

1. **Per-agent spend, real week/month** (item 8) — not fully invisible, but
   the backend capability (`period=week`/`month`) is 100% unused; every page
   hardcodes `period=day`. High value (spend visibility is a stated
   priority), essentially zero backend work.
2. **Project tasks** (item 1) — a complete task object
   (title/description/status/assignee/due date/assign-to-agent) with 4 live
   REST endpoints and zero UI. High value for the "tasks-to-agents" story;
   needs a real view (list or kanban) on the project page, not a one-liner.
3. **Workspace members + invites** (item 2) — the entire Phase-1 "second
   human enters a workspace" feature (commit `fdbbc7b09`, today's date) has
   no UI on either end: no "invite someone" button, no roster, no accept
   page. High value for the multiplayer-projects push; needs one new
   section plus one new landing page.
4. **`subagents_enabled` flag + the fact that the delegation engine has no
   live caller** (item 5) — the flag already round-trips through the API
   with nothing reading it in the UI, and the 7,700-line delegation engine
   it would gate has no product caller at all. Exposing the toggle without
   wiring the engine first would be a lie in the other direction — flag
   this as "don't build the toggle yet," not a normal cheap win.
5. **Compaction / context-fullness status** (item 7) — real backend event
   (`compaction_summary`) with zero status surface; MAN-51's context-window
   view stays parked.
6. **MCP inbound key tool catalog** (item 11) — the 17 `empyralis_*` tools,
   including the 4 brand-new task tools, are wired end-to-end but a key's
   detail view never lists them.
7. **Agent-to-agent messaging on the agent's own page** (item 4) — the
   refusal is visible workspace-wide already; it's specifically invisible
   on the one page (agent Overview) where a user would look to debug it.
8. **"Needs me" cross-agent surface** (item 9) — no aggregate view of which
   agents are blocked/waiting; requires a new backend aggregation query,
   not just a frontend change.
9. **Project documents/cookbook** (item 3) — highest conceptual value of
   the list, but there's no backend primitive yet at all (see the
   `audit-agent-to-agent.md` design: reuse the `vault_credentials.project_id`
   pattern), so it can't be called "invisible," it has to be called "not
   started."

---

## The cheapest wins

Ranked by least frontend work for the value delivered — all three use data
an existing page already fetches from an endpoint it already calls:

1. **Real week/month spend, not client-summed daily buckets.** Every
   `/fleet/usage` caller already exists; `summarize_usage` already accepts
   `period=week|month` and returns pre-aggregated buckets server-side
   (`usage_events_repository.py:231-243`). Changing the query string (and
   adding the period toggle the Billing page already has, in the two places
   that don't — Overview/Work tab) is close to a pure frontend change.
2. **Surface `subagents_enabled` as a read-only fact, not a control** — the
   field is already in every `FleetAgent` API response
   (`fleet-data.ts:47`); showing it (even just in Properties, even
   read-only) costs nothing new from the backend. Caveat: label it
   accurately ("not yet usable — no delegation path exists") rather than
   implying it does something today.
3. **Workspace name rename control in Settings** — `PATCH
   /workspaces/{id}` already exists and already accepts `name`
   (`routes_workspaces.py:382-441`); Settings page already renders a
   similar pattern for other one-field edits. This is the smallest true
   gap-closer on the list — no new backend route, no new data model,
   just a form field on a page that already exists.
4. **Message-agent refusals inside the target agent's own Overview** — the
   ledger row already exists and is already queried per-agent
   (`fleet_get_agent_activity`); the only change is not excluding
   `event_class = 'fleet_control'` for this one `action`, or adding a
   second, narrower query. Slightly more than pure frontend (one backend
   query tweak), but the data and the UI slot both already exist.
5. **MCP API key detail: which of the 17 tools it can call** — the tool
   list itself is close to static (it's the fixed `empyralis_*` registry in
   `mcp_server.py`), so this is mostly a frontend addition to the existing,
   already-expandable key row in `McpServersSection`-adjacent UI, once the
   backend exposes per-key tool scope (may already be derivable from the
   `writes_enabled` flag plus the fixed registry, without new storage).

---

## Dead ends and broken seams

- **`fleet_message_agent`, by design, always fails.** This is not a bug —
  the commit message (`a9e8d27cc`) documents it as an intentional
  "stop lying" fix (it used to silently write to a `fleet_inbox` nothing
  ever read and return `ok: true`). Any future UI that lets an owner
  "send a message to another agent" would need the scheduler-based
  delivery path the design doc (`docs/design/audit-agent-to-agent.md`)
  recommends — building a compose box against the *current* tool would
  ship a feature that provably cannot work.
- **`admin-surfaces.spec.ts` (e2e test) calls a route that doesn't exist.**
  `frontend/tests/e2e/admin-surfaces.spec.ts:21` posts to
  `/api/workspaces/${TEST_WORKSPACE_ID}/members/invites` to fabricate a
  second workspace member for a test fixture. The real, only registered
  invite-creation route is `POST /workspaces/{workspace_id}/invites`
  (`routes_workspaces.py:904`) — there is no `/members/invites` path
  anywhere in `server_modules`. This test calls the API directly (not
  through any UI), so it's not evidence of a working invite flow anywhere
  — if anything it's a second, independent confirmation that nobody has
  wired a real "invite a member" UI, because even the test fixture had to
  guess a URL rather than click through one.
- **Per-agent Overview activity feed silently drops `fleet_control`
  events** (`fleet_tools.py:817-825`) **while the workspace Inbox shows
  them** (`fleet-data.ts:799-810`) — same underlying ledger table, two
  different filters, so "did my agent's message get refused" reads as
  "no activity" on the one page a user would check first, and only shows
  up if they separately check the Inbox and recognize an unrelated-looking
  agent's row. Not broken in the sense of an error, but a genuine
  same-data-different-answer trap.
- **Billing page's "totals"/"by_agent" fields are honestly not
  period-filtered** (`billing/page.tsx:20-27` documents this explicitly)
  — `summarize_usage()` only date-truncates `buckets`, so any future UI
  work that reads `totals` directly for a "this week" or "this month"
  number would silently show an all-time total. The current code already
  works around this by summing `buckets` client-side; any new caller of
  this endpoint needs the same care.

---

## What the left rail and project page would need to expose all of it

**Left rail (`PrimaryRail.tsx`) — currently Inbox · Conversations ·
Projects · Agents · Hardware:**
- A workspace-wide "needs you" count (blocked/waiting agents + pending
  invites) would need a new small aggregation endpoint — nothing today
  computes "agents with an unresolved `approval.requested`" across the
  whole workspace, only per-thread inside one agent's Work tab.
- The workspace brand mark already renders the real name
  (`PrimaryRail.tsx:256-269`) — no rail change needed there, just a rename
  affordance elsewhere (see cheapest wins #3).

**Project detail page (`projects/[projectId]/page.tsx`) — currently an
agent list scoped to the project, a cost rollup + sparkline, and an "Add
agent" wizard:**
- **Tasks**: needs an entirely new section/tab — list or kanban by status
  (`open/in_progress/blocked/awaiting_input/done`), an assign-to-agent
  picker (the assign endpoint already exists), calling the 4 endpoints
  that already exist and have zero callers today.
- **Documents/cookbook**: needs both a new backend primitive (a
  `project_documents` table, per the existing `vault_credentials.project_id`
  pattern already used for connector credentials) and a new UI section —
  this is the one item on the list that isn't just a wiring gap.
- **Members**: needs an "Invite" button (calls the existing `POST
  .../invites`), a pending-invites list (existing `GET .../invites`), and a
  roster (existing `GET .../members`) — all three backend calls already
  exist with zero frontend callers.
- **Real period-aware spend**: swap the existing `period=day` calls for a
  period selector using the already-supported `week`/`month` values.

**Agent detail (9 tabs) — smallest add-ons:**
- Model/Capabilities tab: a read-only `subagents_enabled` indicator
  (data already returned).
- Memory tab: a provenance/trust-tier chip per file or per section.
  `derive_trust_tier` is real (`memory_service.py:1058`) but lives in a
  different module (`memory_service.py`) than the one the Memory tab's
  endpoints call (`agent_memory_tree_service.list_tree`/`read_file`,
  grepped for `trust_tier` — zero hits) — this needs both a backend change
  (thread the tier through the tree/file responses) and the frontend chip,
  not a pure frontend add-on.
- Overview tab: stop excluding `fleet_control` for `message_agent_refused`
  specifically, so a failed outbound message is visible where the user
  would actually look for it.

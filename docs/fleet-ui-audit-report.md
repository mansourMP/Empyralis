# Fleet UI Audit Report — Q1–Q4

**Date:** 2026-07-08 | **Branch:** `verify` | **Scope:** Read-only

---

## Q1 — The Reflow Bug (Properties Panel Squishes Main Content)

### Which component renders the panel, and what toggles it?

- **Panel component:** `FleetRightPanel` in `frontend/lib/workspace/fleet/FleetRightPanel.tsx:42-48`
- **Toggle hook:** `usePanelOpenState(storageKey)` in `FleetRightPanel.tsx:8-34` — persists open/closed state per-page to `sessionStorage` keyed as `fleet:panel:<storageKey>`. Returns `[open, toggle]`.
- **On the Projects list page:** `projects/page.tsx:23` calls `usePanelOpenState("projects")`. The toggle is wired to the `FleetToolbar` at line 37: `<FleetToolbar panelOpen={panelOpen} onTogglePanel={togglePanel} />`, which renders a circular `PanelRight` icon button at `FleetToolbar.tsx:165-175`.

### Is the panel a flex SIBLING of the main content?

**Yes.** The panel is a direct flex sibling of `.fleet-content-main`.

The layout in `projects/page.tsx:40-81`:

```jsx
<div className="fleet-content-with-panel">   {/* line 40 — flex row */}
  <div className="fleet-content-main">       {/* line 41 — flex: 1, min-width: 0 */}
    {/* project list cards */}
  </div>
  <FleetRightPanel open={panelOpen}>         {/* line 75 — flex-shrink: 0 */}
    <PanelSection title="Properties">
      <PanelRow label="Projects" value={projects.length} />
      <PanelRow label="Agents" value={totalAgents} />
    </PanelSection>
  </FleetRightPanel>
</div>
```

The exact CSS that makes them siblings (`fleet-theme.css`):

| Rule | Lines | Key properties |
|------|-------|---------------|
| `.fleet-content-with-panel` | 1078–1083 | `display: flex; flex: 1; min-height: 0; overflow: hidden;` |
| `.fleet-content-main` | 1085–1090 | `flex: 1; min-width: 0; overflow-y: auto; padding: 28px 32px;` |
| `.fleet-right-panel` | 1092–1098 | `flex-shrink: 0; width: 0; overflow: hidden; border-left: 1px solid transparent;` |
| `.fleet-right-panel.is-open` | 1100–1103 | `width: 280px; border-left: 1px solid var(--border);` |

### Why does the card get clipped?

`.fleet-content-main` has **`min-width: 0`** (line 1087). This is the root cause. With `min-width: 0`, the flex item can shrink to zero — it has **no lower bound**. When the panel opens to `280px`, the flex container subtracts exactly 280px from the main column, and since there is no `min-width` guard, the column shrinks below the natural width of the project list rows.

The project list rows themselves (`.fleet-list-row`, lines 3269–3283) have no `min-width` either — they use `display: flex; gap: var(--space-3); padding: var(--space-3) var(--space-4);` and will clip their content with `overflow: hidden` (inherited from `.fleet-content-with-panel` at line 1083). The row's name/title (`.fleet-list-row-main` at line 3286) has `min-width: 0` too, so text truncates via `text-overflow: ellipsis`. The agent-count meta (`.fleet-list-row-meta--icon` at line 3293) has `flex-shrink: 0` — it refuses to shrink — so it gets clipped/hidden off-screen first.

**Summary:** Opening the 280px panel steals 280px from the main column. With `min-width: 0` on the main column and no fixed geometry, the column collapses. Flex-shrink:0 meta elements get clipped off-screen because the container is too narrow.

---

## Q2 — Header Collision (Panel Toggle Overlaps Primary Action)

### Where is the header/action row rendered?

The topbar (breadcrumbs + primary action slot) is rendered by `FleetContentFrame` at `FleetContentFrame.tsx:27-33`:

```jsx
<header className="fleet-shell-topbar">
  <Breadcrumbs workspaceId={workspaceId} />
  <div className="fleet-topbar-action" ref={setActionSlot} />
</header>
```

The primary action button ("New project" / "New agent") is rendered by each page via `<HeaderAction>` (from `Breadcrumbs.tsx:117-121`), which portals its children into the `.fleet-topbar-action` DOM node. For example:
- Projects page: `projects/page.tsx:29-33` — `<HeaderAction><button>+ New project</button></HeaderAction>`
- Agents page: `agents/page.tsx:119-123` — `<HeaderAction><button>+ New agent</button></HeaderAction>`

The panel-toggle circular icon button is rendered by `FleetToolbar` at `FleetToolbar.tsx:165-175`, positioned inside `.fleet-toolbar-actions` (line 91).

### Why do they collide?

**They share one right edge with zero right padding on both rows — by design, but at a cost.**

| Element | CSS | Lines |
|---------|-----|-------|
| `.fleet-shell-topbar` | `padding: 0 0 0 var(--space-5);` — **zero right padding** | 111 |
| `.fleet-topbar-action` | `margin-left: auto;` — pushes flush-right | 116–119 |
| `.fleet-content-toolbar` | `padding: 0 0 0 32px;` — **zero right padding** | 902–904 |
| `.fleet-toolbar-actions` | `margin-left: auto;` — pushes flush-right | 936–942 |
| `.fleet-icon-btn--panel-toggle` | 28×28px circle, `border-radius: 50%`, no margin-right | 978–987 |

The design intent is documented at CSS line 108: *"No right inset: the primary action right-aligns flush to the panel/content edge, exactly under the panel-toggle below it and the right panel itself — one shared right edge (Linear)."*

Both the "New project"/"New agent" button and the panel-toggle circle sit at `right: 0` relative to the content panel's right border. There is no right padding (`padding-right: 0`) on either the topbar or the toolbar row. This means:

1. The **primary action button** extends to the absolute right edge of the floating panel.
2. The **panel-toggle circle** (28×28px) sits directly below it at the same right edge.
3. When the primary action is a text+icon button (e.g. "Chat with this agent" on the agent detail page at `FleetAgentDetail.tsx:251-256`), its text label extends further right than the 28px circle below, creating a visual **overhang** — the wider button above appears to clip or overlap the circle below.
4. There is no `gap`, no `padding-right`, no `margin-right` on either element to create breathing room from the panel edge.

**Not a z-index or absolute-positioning issue** — it is a **spacing/padding** defect. Both elements are in normal flow (separate rows), but zero right padding makes them feel jammed against the edge, and the wider button overhangs the narrower circle.

---

## Q3 — DATA INVENTORY

### Agents Surface (FleetHome / AgentsPage / AgentRow)

| Field | Source Hook/Endpoint | Real or Stub in Local Dev | Example Value |
|-------|---------------------|--------------------------|---------------|
| **Label / Name** | `useFleetAgents` → `GET /fleet/agents` → `fleet_list_agents()` (`fleet_tools.py:403-426`) | **Real** — from `agent_installs.label` in SQLite | `"Customer Support Bot"` |
| **Role** | Same | **Real** — resolved by `resolve_agent_role()` from install metadata | `"customer_facing"` |
| **Purpose preset** | Same | **Real** — set at wizard creation, stored in install metadata | `"customer_facing"` |
| **Capability preset** | Same | **Real** — from install metadata | `"standard"` |
| **Status (enabled/disabled)** | Same | **Real** — `agent_installs.status` in SQLite | `"active"` |
| **Hardware status** | Same → heartbeats from `_resolve_hardware_status()` (`fleet_tools.py:394-396`) | **Stub (usually)** — resolves to `"unknown"` if no gateway heartbeat exists | `"Not deployed"` |
| **Last heartbeat** | Same | **Stub (usually)** — `null` without a running gateway | `null` |
| **Last activity** | Same → `_fetch_latest_activity()` (`fleet_tools.py:384`) | **Real if runs happened** — queries `activity_ledger_events` for most recent `created_at` per agent. Empty/null if never run. | `"2026-07-08T12:00:00Z"` or `null` |
| **Activity preview** | Declared in `FleetAgent` type (`fleet-data.ts:16`) | **Always empty** — never set by backend (`fleet_tools.py:403-426` has no `activity_preview` key) | `undefined` |
| **Channel** | Same → `_fetch_agent_channels()` (`fleet_tools.py:385`) | **Real if configured** — resolves the first connected channel binding to a display name. Empty string if none. | `"Telegram"` or `""` |
| **Project ID** | Same | **Real** — from `agent_installs.project_id` | `"project_a1b2c3..."` |
| **Model config** | Same → `resolve_model_config()` | **Real** — from install metadata/model_config | `{ model: "claude-sonnet-5" }` |
| **Runtime target** | Same → `_resolve_runtime_target()` | **Real** — from install metadata/runtime_target | `"cloud"` |
| **Hardware access** | Same | **Real** — from install metadata | `"none"` |
| **Instructions** | Same | **Real** — from install metadata | `"You are a helpful..."` |
| **Subagents enabled** | Same | **Real** — from install metadata | `false` |
| **Context policy** | Same | **Real** — from install metadata | `{ max_context_tokens: 100000 }` |
| **Cost today (per agent)** | `GET /fleet/usage?scope=workspace&period=day` → `summarize_usage()` (`usage_events_repository.py:202-281`) | **Real if LLM calls happened** — queries `usage_events` table with `sum(usd_cost)`. Zero with no usage. | `$0.0234` or `0` |
| **Connected channels** | `useFleetAgentChannels` → `GET /fleet/agent-channels` (`routes_fleet.py:406-455`) | **Real catalog, stubbed connections** — catalog entries appear (Telegram, Discord) but `connected: false` unless a real bot is configured | `connected: false` |
| **Connected connectors** | `useFleetAgentConnectors` → `GET /fleet/agent-connectors` (`routes_fleet.py:458-500`) | **Real catalog, stubbed connections** — entries appear (Google Workspace, etc.) but `connected: false` unless OAuth/MCP creds configured | `connected: false` |
| **Agent tools** | `useFleetAgentTools` → `GET /fleet/agent-tools` (`fleet-data.ts:287-312`) | **Real** — from tool registry | `[{ id: "...", label: "Web Search", enabled: true }]` |
| **Run count** | Not directly surfaced — `usage_events` has `events` count in `summarize_usage()` | **Real if usage exists** — `totals.events` = count of usage rows | `42` |
| **Token spend** | `GET /fleet/usage` → `summarize_usage()` | **Real if LLM calls happened** — `totals.total_tokens` from `sum(usage_events.total_tokens)` | `152000` |
| **Recent activity (agent)** | `useFleetAgentActivity` → `GET /fleet/agent-activity` (`fleet_tools.py:437-485`) | **Real if runs happened** — queries `activity_ledger_events WHERE actor_id = agent_id`. Empty `[]` with no runs. | `[{ event_id, action, title, status, created_at }]` |

### Agent Detail — Overview Tab (`FleetAgentDetail.tsx:282-349`)

| Field | Source | Real or Stub | Example |
|-------|--------|-------------|---------|
| **Status** | `deriveStatus(agent.hardware_status)` → renders `StatusChip` | **Stub** — "Not deployed" without gateway | `"Not deployed"` |
| **Placement** | `derivePlacement(agent.runtime_target, deployed)` | **Real** — from install metadata | `"Cloud"` or `"Ready to configure"` |
| **Role** | `agent.role` | **Real** | `"customer_facing"` |
| **Instructions (persona)** | `PersonaEditor` — reads/writes `agent.instructions` via PATCH | **Real** | `"You are a support agent..."` |
| **Recent activity (detail)** | `useFleetAgentActivity` → `events` array | **Empty in fresh local dev** — no runs = no ledger rows | `[]` |

### Agent Detail — Properties Panel (`FleetAgentDetail.tsx:153-168`)

| Field | Source | Real or Stub | Example |
|-------|--------|-------------|---------|
| **Status** | `deriveStatus()` | **Stub** | `"Not deployed"` |
| **Preset** | `agent.capability_preset \|\| agent.purpose_preset` | **Real** | `"standard"` |
| **Model** | `agent.model_config` → resolved model name | **Real** | `"Platform default"` |
| **Project** | `projectName` prop (from URL → projects list) | **Real** | `"Customer Support"` |
| **Cost today** | `GET /fleet/usage?scope=agent&id=...&period=day` | **Real if usage exists** | `$0.0000` |
| **Channels** | `channels.filter(c => c.connected).length` | **Stub** — 0 without real bots | `0` |
| **Connectors** | `connectors.filter(c => c.connected).length` | **Stub** — 0 without real OAuth | `0` |

### Workspace-level (FleetHome strips)

| Field | Source Hook/Endpoint | Real or Stub | Example |
|-------|---------------------|-------------|---------|
| **Channels connected / total** | `useWorkspaceStatusStrip` → `GET /fleet/connection-summary` (`routes_fleet.py:615-629`) | **Total = real** (catalog size), **connected = stub** (0 without bots) | `0/2` |
| **Connectors connected / total** | Same | **Total = real**, **connected = stub** (0 without OAuth) | `0/5` |
| **Computers online / total** | Same → `GET /gateway/registrations` | **Stub** — 0 without a running gateway | `0/0` |
| **Recent activity (workspace)** | `useWorkspaceActivity` → `GET /api/activity/timeline` (`runtime_events_api.py:401-439` → `activity_ledger_service.py:658-709`) | **Empty in fresh local dev** — returns `[]` if no ledger events exist. Real once any agent runs, creates, or configures. | `[]` or `[{ title, event_class, action, status, created_at }]` |
| **Project cost (month)** | `GET /fleet/usage?scope=project&id=...&period=month` | **Zero without usage** — `usd_cost: 0.0` | `$0.0000` |
| **Project tokens** | Same | **Zero without usage** | `0` |
| **Project LLM calls** | Same — `totals.events` | **Zero without usage** | `0` |
| **Project activity** | `GET /fleet/project-activity` (`routes_fleet.py:276-296` → `fleet_tools.py:497`) | **Empty without runs** — queries `activity_ledger_events WHERE project_id = ...` | `[]` |

### Billing / Usage page

| Endpoint | Data | Real or Stub |
|----------|------|-------------|
| `GET /fleet/usage?scope=workspace&period=day\|week\|month` | `totals.usd_cost`, `totals.total_tokens`, `totals.events`, `by_agent[]`, `buckets[]` | **Real** — `usage_events` table in SQLite. Populated only when actual LLM inference runs occur and the usage accounting pipeline records them. In local dev with no agent runs, all values are zero. |

### Key finding for Q3

**The data pipeline is substantially wired.** Most fields carry real data from SQLite — agent metadata, project structure, model config. The gaps are:

1. **Hardware status** — always "unknown"/"Not deployed" without a running gateway (normal for local dev).
2. **Channel/connector connected counts** — always 0 without real external bot/OAuth configuration.
3. **Usage/cost/tokens** — all zero until an agent actually runs LLM inference through the system.
4. **Activity ledger** — empty until agents perform actions (runs, config changes, channel messages).
5. **`activity_preview`** — declared in the frontend type but **never populated** by the backend (dead field).
6. **Run counts** — not surfaced as a dedicated field on the agent object; only available via the usage rollup (`totals.events`).

---

## Q4 — The Rail: Nav Items in `PrimaryRail.tsx`

### Nav items rendered, in order

| # | Key | Label | Icon | Route segment | Chord | Line |
|---|-----|-------|------|---------------|-------|------|
| 1 | `inbox` | Inbox | `Inbox` (lucide) | `inbox` | `G I` | 30 |
| 2 | `projects` | Projects | `FolderKanban` (lucide) | `projects` | `G P` | 31 |
| 3 | `agents` | Agents | `Bot` (lucide) | `agents` | `G A` | 32 |
| 4 | `hardware` | Hardware | `Cpu` (lucide) | `hardware` | `G H` | 33 |

**Total: 4 nav items.** Rendered via `.map()` at `PrimaryRail.tsx:129-151`.

### Billing and Settings — NOT in the rail

**Billing** and **Settings** are **not** rail nav items. They were moved into the **account menu popover** (the bottom avatar block):

- **Settings** → `PrimaryRail.tsx:253-256` — `<Link href={settingsHref}>` inside `AccountMenu` popover, links to `/w/{ws}/settings`
- **Billing** → `PrimaryRail.tsx:257-259` — `<Link href={creditsHref}>` inside `AccountMenu` popover, links to `/w/{ws}/billing`

The code comment at line 42-44 explicitly documents this decision: *"Billing lives in the account menu instead — it's a look-up-occasionally screen, not a nav destination."*

### Platform Map discrepancy

`docs/PLATFORM-MAP.md:580` states: *"Persistent left rail (Inbox, Projects, Agents, Hardware, Billing, Settings) with keyboard chords."*

This is **out of date**. The actual rail has only 4 items. Billing and Settings were removed from the rail and relocated to the account menu popover. The platform map should be updated to reflect this.

### What IS in the rail besides nav items

Below the nav items, the rail renders (in `PrimaryRail.tsx:154-181`):

1. **Theme toggle** — Sun/Moon icon button (line 155-163)
2. **Collapse toggle** — PanelLeftClose/PanelLeftOpen icon button (line 164-173)
3. **Account menu** — `<AccountMenu>` component (line 175-181) containing: Settings link, Billing link, Log out button

---

## Appendix: File Reference Index

| File | Key content |
|------|-----------|
| `frontend/lib/workspace/fleet/FleetRightPanel.tsx` | Panel component + `usePanelOpenState` hook |
| `frontend/lib/workspace/fleet/FleetToolbar.tsx` | Toolbar row with panel-toggle button |
| `frontend/lib/workspace/fleet/FleetContentFrame.tsx` | Topbar (breadcrumbs + action slot) |
| `frontend/lib/workspace/fleet/Breadcrumbs.tsx` | `HeaderAction` portal + `HeaderActionSlotProvider` |
| `frontend/lib/workspace/fleet/fleet-theme.css` | All layout CSS (`.fleet-content-with-panel:1078`, `.fleet-content-main:1085`, `.fleet-right-panel:1092`, `.fleet-shell-topbar:103`, `.fleet-toolbar-row:886`) |
| `frontend/lib/workspace/fleet/fleet-data.ts` | All data hooks + TypeScript types |
| `frontend/lib/workspace/fleet/FleetHome.tsx` | Agents grid page (no panel toggle) |
| `frontend/lib/workspace/fleet/FleetAgentDetail.tsx` | Agent detail with Overview tab + properties panel |
| `frontend/lib/workspace/fleet/AgentsList.tsx` | Agent row rendering |
| `frontend/lib/workspace/fleet/PrimaryRail.tsx` | Rail nav items + account menu |
| `frontend/lib/workspace/fleet/FleetShell.tsx` | Shell root (rail + content panel) |
| `frontend/app/(account)/w/[workspaceId]/projects/page.tsx` | Projects list page (panel reflow bug) |
| `frontend/app/(account)/w/[workspaceId]/projects/[projectId]/page.tsx` | Project detail page (panel reflow bug) |
| `frontend/app/(account)/w/[workspaceId]/agents/page.tsx` | Agents list page (header collision) |
| `frontend/app/(account)/w/[workspaceId]/projects/[projectId]/agents/[agentId]/[tab]/page.tsx` | Routed agent detail page |
| `server_modules/routes_fleet.py` | All fleet API endpoints |
| `server_modules/fleet_tools.py` | `fleet_list_agents()` + `fleet_get_agent_activity()` |
| `server_modules/usage_events_repository.py` | `summarize_usage()` — SQLite-backed usage rollups |
| `server_modules/activity_ledger_service.py` | `list_activity_timeline_payload()` |
| `server_modules/runtime_events_api.py` | `/activity/timeline` endpoint |
| `docs/PLATFORM-MAP.md` | Outdated rail description (line 580) |

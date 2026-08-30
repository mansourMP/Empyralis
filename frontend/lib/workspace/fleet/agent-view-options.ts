/**
 * VIEW OPTIONS FOR THE WORKSPACE AGENTS PAGE — Board/List layout, grouping,
 * ordering, display properties.
 *
 * TWO LAYOUTS, AND LIST IS THE DEFAULT. Tasks has two (board | list) because
 * a task list and a task board are the only two shapes that surface has;
 * Agents now matches it exactly, on the founder's own reversal, 2026-08-30:
 * *"i do not want cards thing default should be list ... i only want to see
 * list and board!"* A third layout — the CARD GRID (AgentCards.tsx) — used to
 * be the default here. It is DELETED, not merely un-defaulted: the component,
 * its `"cards"` layout value, and the CSS rules only it used are all gone.
 * agent-card-face.ts is NOT that grid — it is the shared face logic (state +
 * reach) both remaining renderings still read, and it is untouched.
 *
 * `layout: "cards"` used to be a real value rather than being spelled `list`
 * + `grouping: "none"`, which is how the first build of this file expressed
 * it — back when the ungrouped List branch rendered the flat table
 * (AgentsList.tsx) and later the card grid, so a popover whose "List" chip
 * lit up over a grid of cards was a control describing a view that was not on
 * screen. That history is why `layout` stays its own value rather than being
 * re-derived from grouping now that only List and Board remain: the same
 * confusion would recur the instant a third layout returns.
 *
 * A PARALLEL build to task-view-options.ts / TaskViewOptions.tsx, not a
 * generalization of it. The founder's YC demo video already shows the task
 * board working; refactoring that shared code to also serve agents would risk
 * a regression there for zero benefit to this feature. So this file, its
 * companion AgentViewOptions.tsx, and AgentsBoard.tsx / AgentsGroupedList.tsx
 * are their own code path: own vocabulary, own localStorage key
 * (fleet:agent-view:*, never fleet:task-view:*), own CSS classes
 * (.fleet-agent-view-options*, .fleet-agent-board*, .fleet-agent-glist* —
 * mirroring .fleet-view-options* / .fleet-board* / .fleet-glist* BY NAME
 * ONLY, not by sharing a rule). Nothing in lib/workspace/fleet/task-*.ts or
 * Tasks*.tsx is imported or edited by this feature.
 *
 * A few pure helpers below (agentBrainLabel, agentMoney,
 * parseAgentChannelField) are intentional, documented DUPLICATES of private
 * (non-exported) functions already living in AgentsList.tsx. They are not
 * re-exported from there because touching AgentsList.tsx at all is
 * unnecessary risk to a table that must keep behaving exactly as it does
 * today — see that file. A one-line drift between that table's formatting and
 * this file's is a real bug to fix by hand if it ever happens, which is a
 * cheaper failure mode than a shared import breaking a table this feature was
 * never supposed to touch.
 *
 * `agentActivityPreviewText` USED TO LIVE HERE AND IS DELETED, not merely
 * unused. It returned `activity_preview` — a LIFECYCLE VERB — and both
 * renderings this file feeds drew it as their one secondary line, so every
 * board card and every list row read "Created". That is true of every agent
 * that has ever existed, so a column of it distinguishes nothing; it is the
 * exact line agent-card-face.ts was written to replace on the card grid, and
 * shipping it on two more surfaces put it straight back on screen one click
 * away. Both now call that module's own `agentCardReach` instead, so the
 * three renderings cannot say different things about the same agent. The
 * helper is gone rather than left exported because an exported helper is how
 * it comes back.
 *
 * WHAT AN AGENT'S "STATUS" HONESTLY IS, AND WHY THE BOARD HAS FOUR COLUMNS.
 * An agent has no backlog/todo/in_progress/done lifecycle — it isn't a task,
 * and borrowing that vocabulary for it would be exactly the "dishonest
 * label" CLAUDE.md's no-dead-controls rule warns against. What an agent
 * really has is what fleet_list_agents (server_modules/fleet_tools.py)
 * returns and gateway-box-picker.deriveAgentStatus already reduces to a
 * single tone:
 *   · current_run_id     — a run in progress right now (gateway/VPS agents)
 *   · hardware_status     — "online" | "offline" | "unknown" | "error", the
 *     last meaning a CLOUD agent whose model_config can't currently produce
 *     a turn (dead/missing key, exhausted entitlement — see
 *     _resolve_cloud_agent_readiness) — a real config-honesty failure, not
 *     an invented state.
 *   · stopped.active       — an owner-issued stop; wins over everything else.
 *   · model_config.mode    — for a cli_subscription/local (brain-bound)
 *     agent, deriveAgentStatus folds in the bound computer's own live state
 *     too (CLI not installed, needs sign-in, computer offline/disconnected,
 *     model not loaded).
 * deriveAgentStatus() already reduces all of that to one AgentStatusTone
 * (working / ready / offline / unknown / error / stopped / degraded — see
 * fleet-presentation.ts). agentStatusGroup() below folds that tone one level
 * coarser, into the four groups the founder asked for: Working, Idle, Needs
 * attention (a real "error" or "degraded" tone — never rendered unless the
 * data actually produced one), Offline or stopped. No fifth bucket, no
 * invented state.
 */

import {
  deriveAgentStatus,
  gatewayId,
  gatewayIsCloudVps,
  hardwarePlacementIsBrainBound,
  type FleetGateway,
} from "./gateway-box-picker";
import type { FleetAgent } from "./fleet-data";
import { timeAgo, type AgentStatusTone } from "./fleet-presentation";
import { formatUsd } from "../../ui/money";

// ── Vocabulary ──────────────────────────────────────────────────────────────

/** Two renderings, and both exist on screen today. "Grouped" is NOT a third
 *  — it is the List with a grouping switched on, which is why grouping is a
 *  separate axis (see agentSurfaceFor). */
export type AgentLayout = "list" | "board";

/** Rendered by AgentViewOptions.tsx in this order. Data, not literals in the
 *  JSX, so the popover and `readAgentViewOptions`' own accepted-value list
 *  can never disagree about what a layout is. */
export const AGENT_LAYOUT_OPTIONS: { value: AgentLayout; label: string }[] = [
  { value: "list", label: "List" },
  { value: "board", label: "Board" },
];

// "project" was REMOVED 2026-08-30 (founder hard rule: "an agent is
// completely independent of any project... that's the hard rule!"). An
// agent's project_id is a nullable, never-backfilled column that never
// meant ownership; grouping by it taught the opposite lesson on the one
// surface built to show every agent across the whole workspace. See
// groupAgents' own removal note below for the full history.
export type AgentGrouping = "none" | "status" | "placement";

/** Three keys, and "status"/"group" are deliberately absent: they are what
 *  AgentGrouping already does, and offering the same reshaping two ways in one
 *  popover is a duplicate control. (The page's pre-2026-08-22 "Sort by"
 *  dropdown, which this set was originally cut against, no longer exists —
 *  that surface was replaced by the card grid.) */
export type AgentOrdering = "last_active" | "cost" | "name";

export type AgentOrderDirection = "asc" | "desc";

/** The six fields a Board card and a List row can draw beside an agent's
 *  name — both layouts offer all six; see AgentSurface below. */
export type AgentDisplayProperty = "brain" | "placement" | "channels" | "lastActive" | "cost" | "status";

/** Which rendering is on screen — one per layout, so this is the layout
 *  itself rather than a derived value. It survives as its own type because
 *  `displayPropertiesFor` answers a question about a RENDERING ("does this
 *  surface actually draw that field"), and keeping the two names apart is
 *  what stops a future third layout silently inheriting another's column set
 *  — the exact trap the deleted Cards layout used to be the exception to
 *  (`displayPropertiesFor("cards")` was always `[]`, since a card face is two
 *  facts and refuses a third). With Cards gone, both surfaces get every
 *  field, but the type is kept separate rather than collapsed into
 *  AgentLayout for that same reason. */
export type AgentSurface = AgentLayout;

export type AgentDisplayState = Record<AgentDisplayProperty, boolean>;

export type AgentViewOptions = {
  layout: AgentLayout;
  grouping: AgentGrouping;
  ordering: AgentOrdering;
  direction: AgentOrderDirection;
  display: AgentDisplayState;
};

export const AGENT_GROUPING_OPTIONS: { value: AgentGrouping; label: string }[] = [
  { value: "none", label: "No grouping" },
  { value: "status", label: "Status" },
  { value: "placement", label: "Hardware placement" },
];

export const AGENT_ORDERING_OPTIONS: { value: AgentOrdering; label: string }[] = [
  { value: "last_active", label: "Last active" },
  { value: "cost", label: "Cost" },
  { value: "name", label: "Name" },
];

/** Same reasoning as task-view-options.ts's own table: the direction that
 *  reads right for a key is not always "ascending". */
export const AGENT_ORDERING_DEFAULT_DIRECTION: Record<AgentOrdering, AgentOrderDirection> = {
  last_active: "desc",
  cost: "desc",
  name: "asc",
};

export function orderDirectionLabel(ordering: AgentOrdering, direction: AgentOrderDirection): string {
  const asc = direction === "asc";
  switch (ordering) {
    case "cost":
      return asc ? "Lowest first" : "Highest first";
    case "name":
      return asc ? "A to Z" : "Z to A";
    default:
      return asc ? "Oldest first" : "Newest first";
  }
}

/**
 * Every display toggle, and the surfaces each is real on. All six are real on
 * "board" and "list" and on neither of them is anything exempt: unlike tasks,
 * an agent's board card has no interactive control living inside any of these
 * (there is no drag-and-drop here; see AgentsBoard.tsx for why), so nothing
 * needs the exemption the task board's status ring gets.
 */
export const AGENT_DISPLAY_PROPERTIES: {
  key: AgentDisplayProperty;
  label: string;
  surfaces: AgentSurface[];
}[] = [
  { key: "brain", label: "Brain", surfaces: ["board", "list"] },
  { key: "placement", label: "Placement", surfaces: ["board", "list"] },
  { key: "channels", label: "Channels", surfaces: ["board", "list"] },
  { key: "lastActive", label: "Last active", surfaces: ["board", "list"] },
  { key: "cost", label: "Cost", surfaces: ["board", "list"] },
  { key: "status", label: "Status", surfaces: ["board", "list"] },
];

const ALL_DISPLAY_KEYS = AGENT_DISPLAY_PROPERTIES.map((p) => p.key);

/** Everything on, LIST layout, no grouping — the founder's own reversal,
 *  2026-08-30 ("default should be list"), so a reader who never opens the
 *  popover lands on the plain row list, ungrouped, every field showing. */
export const DEFAULT_AGENT_VIEW_OPTIONS: AgentViewOptions = {
  layout: "list",
  grouping: "none",
  ordering: "last_active",
  direction: "desc",
  display: Object.fromEntries(ALL_DISPLAY_KEYS.map((k) => [k, true])) as AgentDisplayState,
};

export function agentSurfaceFor(options: AgentViewOptions): AgentSurface {
  return options.layout;
}

export function displayPropertiesFor(surface: AgentSurface) {
  return AGENT_DISPLAY_PROPERTIES.filter((p) => p.surfaces.includes(surface));
}

/** Layout is excluded, same reasoning as task-view-options.ts's own
 *  isDefaultTaskViewOptions: Board-or-List is a view the reader is standing
 *  in, not a preference Reset should yank them out of. */
export function isDefaultAgentViewOptions(options: AgentViewOptions): boolean {
  return (
    options.grouping === DEFAULT_AGENT_VIEW_OPTIONS.grouping &&
    options.ordering === DEFAULT_AGENT_VIEW_OPTIONS.ordering &&
    options.direction === DEFAULT_AGENT_VIEW_OPTIONS.direction &&
    ALL_DISPLAY_KEYS.every((k) => options.display[k] === true)
  );
}

export function resetAgentViewOptions(options: AgentViewOptions): AgentViewOptions {
  return {
    ...DEFAULT_AGENT_VIEW_OPTIONS,
    layout: options.layout,
    display: { ...DEFAULT_AGENT_VIEW_OPTIONS.display },
  };
}

// ── Persistence ─────────────────────────────────────────────────────────────

/** BUMPED 1 -> 2 (2026-08-29). A v1 blob's `layout: "list"` meant "the card
 *  grid", because back then the ungrouped List branch rendered the card grid;
 *  in v2 "list" means the row list. Reading a v1 blob under the v2 vocabulary
 *  would silently move a reader into a view they never picked, so the old key
 *  is simply not read — the default (Cards, at the time) is what a v1 reader
 *  saw anyway. NOT bumped again for the 2026-08-30 Cards removal: `oneOf`'s
 *  allow-list already drops any stored `"cards"` and falls back to the
 *  CURRENT default (List) on its own — see readAgentViewOptions below. */
const STORAGE_VERSION = 2;

/** `fleet:agent-view:*` — its OWN namespace, never `fleet:task-view:*`, so
 *  the two features can never read or clobber each other's blob even though
 *  they share the `fleet:` prefix convention. Per-workspace, matching every
 *  other view preference in this directory. */
export function agentViewStorageKey(workspaceId: string): string {
  return `fleet:agent-view:v${STORAGE_VERSION}:${workspaceId}`;
}

function oneOf<T extends string>(value: unknown, allowed: readonly T[], fallback: T): T {
  return allowed.includes(value as T) ? (value as T) : fallback;
}

export function readAgentViewOptions(workspaceId: string): AgentViewOptions {
  const fallback = { ...DEFAULT_AGENT_VIEW_OPTIONS, display: { ...DEFAULT_AGENT_VIEW_OPTIONS.display } };
  if (!workspaceId) return fallback;
  try {
    const raw = window.localStorage.getItem(agentViewStorageKey(workspaceId));
    if (!raw) return fallback;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return fallback;
    const display = { ...DEFAULT_AGENT_VIEW_OPTIONS.display };
    const storedDisplay = parsed.display;
    if (storedDisplay && typeof storedDisplay === "object") {
      for (const key of ALL_DISPLAY_KEYS) {
        if (typeof storedDisplay[key] === "boolean") display[key] = storedDisplay[key];
      }
    }
    return {
      layout: oneOf<AgentLayout>(
        parsed.layout,
        AGENT_LAYOUT_OPTIONS.map((o) => o.value),
        DEFAULT_AGENT_VIEW_OPTIONS.layout,
      ),
      grouping: oneOf<AgentGrouping>(
        parsed.grouping,
        AGENT_GROUPING_OPTIONS.map((o) => o.value),
        DEFAULT_AGENT_VIEW_OPTIONS.grouping,
      ),
      ordering: oneOf<AgentOrdering>(
        parsed.ordering,
        AGENT_ORDERING_OPTIONS.map((o) => o.value),
        DEFAULT_AGENT_VIEW_OPTIONS.ordering,
      ),
      direction: oneOf<AgentOrderDirection>(parsed.direction, ["asc", "desc"], DEFAULT_AGENT_VIEW_OPTIONS.direction),
      display,
    };
  } catch {
    return fallback;
  }
}

export function writeAgentViewOptions(workspaceId: string, options: AgentViewOptions): void {
  if (!workspaceId) return;
  try {
    window.localStorage.setItem(agentViewStorageKey(workspaceId), JSON.stringify(options));
  } catch {
    /* localStorage unavailable (private mode, quota) — the view still works
       for this session, it just won't come back after a reload. */
  }
}

// ── Status grouping (Board columns, and "Status" in the List grouping) ──────

export type AgentStatusGroup = "working" | "idle" | "needs_attention" | "offline";

export const AGENT_STATUS_GROUPS: { value: AgentStatusGroup; label: string }[] = [
  { value: "working", label: "Working" },
  { value: "idle", label: "Idle" },
  { value: "needs_attention", label: "Needs attention" },
  { value: "offline", label: "Offline or stopped" },
];

/** The exact parameter shape deriveAgentStatus takes, without re-declaring
 *  it — that type isn't exported from gateway-box-picker.tsx, and doesn't
 *  need to be: FleetAgent already carries every field it asks for. */
type AgentBrainStatusLike = Parameters<typeof deriveAgentStatus>[0];

/** The one thing agent-card-face.ts's own (private) taskIsActive checks,
 *  duplicated here rather than importing that whole module for one line —
 *  same "small duplicate over a wholesale cross-import" call this file's own
 *  header already makes for AgentsList.tsx's helpers. Structural, so a real
 *  FleetTask (or the card-face module's own AgentCardTaskInput) satisfies it
 *  without a cast. */
function hasActiveTask(tasks: readonly { status?: string | null }[]): boolean {
  return tasks.some((t) => String(t.status || "").trim().toLowerCase() === "in_progress");
}

/**
 * deriveAgentStatus's own tone/label, enriched with the SAME task-based
 * "Working" signal agent-card-face.ts already established as this product's
 * one definition (see that module's header) and PrimaryRail's footer pulse
 * was already fixed to read instead of `current_run_id` alone (CLAUDE.md:
 * "'Working' now has ONE definition, in that module... once the cards
 * counted tasks the rail and the grid said different things about the same
 * fleet on the same screen"). `current_run_id` — deriveAgentStatus's own
 * signal for "working" — is de-facto always null for a real fleet agent; an
 * assigned task sitting in_progress is the signal that actually occurs.
 *
 * Board/Grouped-List are a THIRD surface rendering this same fact and must
 * not disagree with the card grid one click away — without this, an agent
 * with a real in-progress task read "Ready" on its own Board card while
 * sitting in a column literally labelled "Working" one row up. Confirmed
 * live against a seeded agent before this fix (Support Desk: an in_progress
 * task, `tone: "ready"` from deriveAgentStatus alone) and after (tone
 * upgraded to "working", card and column agree).
 *
 * Never invents a label: an agent already reporting "working" (a real,
 * heartbeat-derived current_run_id) keeps deriveAgentStatus's own tone/label
 * untouched; everything else either upgrades to the exact {tone, label} pair
 * agent-card-face.ts uses for this case, or passes through unchanged.
 */
export function agentDisplayStatus(
  agent: AgentBrainStatusLike,
  gateways: FleetGateway[],
  tasks: readonly { status?: string | null }[] = [],
): { tone: AgentStatusTone; label: string } {
  const status = deriveAgentStatus(agent, gateways);
  if (status.tone === "working") return status;
  return hasActiveTask(tasks) ? { tone: "working", label: "Working" } : status;
}

/**
 * One agent's real status, folded into one of the four board columns.
 * "needs_attention" fires ONLY for "error" and "degraded" tones — both
 * already real, config-honesty failures the backend computed (see the file
 * header) — never as a made-up fifth state. Every other tone maps to a
 * column that was already true of the agent. `tasks` is this ONE agent's own
 * tasks (see groupTasksByAgent in agent-card-face.ts) — optional and
 * defaulted to `[]` so an existing call site that predates the Working fix
 * above still compiles, though every real caller in this codebase now passes
 * it (AgentsBoard.tsx, AgentsGroupedList.tsx, both via groupAgents below).
 */
export function agentStatusGroup(
  agent: AgentBrainStatusLike,
  gateways: FleetGateway[],
  tasks: readonly { status?: string | null }[] = [],
): AgentStatusGroup {
  const { tone } = agentDisplayStatus(agent, gateways, tasks);
  if (tone === "working") return "working";
  // "online" is deriveAgentStatus's own doc comment: reserved for the
  // Hardware page's box-reachability chip, never actually returned here —
  // kept as a safe fallback rather than assuming that invariant forever.
  if (tone === "ready" || tone === "online") return "idle";
  if (tone === "error" || tone === "degraded") return "needs_attention";
  return "offline"; // offline | stopped | unknown (never paired/deployed)
}

// ── Placement categorization ─────────────────────────────────────────────────

/** Mirrors AgentsList.tsx's own (non-exported) resolvePlacementBadge —
 *  intentionally duplicated, not imported; see the file header. */
export type AgentPlacementCategory = "cloud" | "vps" | "device";

export const AGENT_PLACEMENT_LABELS: Record<AgentPlacementCategory, string> = {
  cloud: "Cloud",
  vps: "VPS",
  device: "Device",
};

export function agentPlacementCategory(agent: FleetAgent, gateways: FleetGateway[]): AgentPlacementCategory {
  if (hardwarePlacementIsBrainBound(agent.model_config)) {
    const brainGatewayId = String(agent.model_config?.gateway_binding || "").trim();
    const match = brainGatewayId ? gateways.find((g) => gatewayId(g) === brainGatewayId) : undefined;
    return match && gatewayIsCloudVps(match) ? "vps" : "device";
  }
  const access = (agent.hardware_access || "none").toLowerCase();
  if (access === "none") return "cloud";
  return access === "vps" ? "vps" : "device";
}

// ── Grouping ────────────────────────────────────────────────────────────────

/**
 * One section of a grouped list (or, when built from the fixed
 * AGENT_STATUS_GROUPS order, one board column).
 *
 * "project" grouping — and the `projectId`/`empty` fields that existed
 * only to support it — was REMOVED 2026-08-30 (founder hard rule: "an
 * agent is completely independent of any project... that's the hard
 * rule!"). It bucketed agents by `project_id`, a nullable, never-backfilled
 * column that never meant ownership, and taught exactly the opposite
 * lesson on the one surface built to show every agent across the whole
 * workspace. `projectById` is gone from this function's options for the
 * same reason — nothing left here reads a project map.
 */
export type AgentGroup = {
  key: string;
  label: string;
  agents: FleetAgent[];
  count: number;
  statusGroup?: AgentStatusGroup;
  placementCategory?: AgentPlacementCategory;
  /** The single synthetic bucket grouping "none" produces. It is not a section
   *  anybody chose — it holds every agent, so its heading would name the one
   *  thing already on screen and its collapse control's only effect would be to
   *  hide the entire list. AgentsGroupedList draws its rows and NO header. The
   *  flag lives here rather than being sniffed from `key === "all"` at the
   *  render site so the two cannot drift. */
  ungrouped?: boolean;
};

export function groupAgents(
  agents: FleetAgent[],
  grouping: AgentGrouping,
  {
    gateways = [],
    tasksByAgent = new Map<string, { status?: string | null }[]>(),
  }: {
    gateways?: FleetGateway[];
    /** Per-agent tasks (agent-card-face.ts's groupTasksByAgent output) — only
     *  read for grouping "status", to fold an in-progress task into the same
     *  "Working" bucket agentDisplayStatus/agentStatusGroup above now use. */
    tasksByAgent?: Map<string, { status?: string | null }[]>;
  } = {},
): AgentGroup[] {
  if (grouping === "none") {
    return [{ key: "all", label: "All agents", agents, count: agents.length, ungrouped: true }];
  }

  if (grouping === "status") {
    const buckets = new Map<AgentStatusGroup, FleetAgent[]>(AGENT_STATUS_GROUPS.map((g) => [g.value, []]));
    for (const agent of agents) {
      buckets.get(agentStatusGroup(agent, gateways, tasksByAgent.get(agent.agent_id) || []))?.push(agent);
    }
    return AGENT_STATUS_GROUPS.filter((g) => (buckets.get(g.value) || []).length > 0).map((g) => ({
      key: g.value,
      label: g.label,
      statusGroup: g.value,
      agents: buckets.get(g.value) || [],
      count: (buckets.get(g.value) || []).length,
    }));
  }

  // grouping === "placement" — the only value left once "none"/"status" have
  // each returned above (AgentGrouping is a 3-member union).
  const buckets = new Map<AgentPlacementCategory, FleetAgent[]>();
  for (const agent of agents) {
    const cat = agentPlacementCategory(agent, gateways);
    if (!buckets.has(cat)) buckets.set(cat, []);
    buckets.get(cat)!.push(agent);
  }
  return (["cloud", "vps", "device"] as AgentPlacementCategory[])
    .filter((c) => (buckets.get(c) || []).length > 0)
    .map((c) => ({
      key: c,
      label: AGENT_PLACEMENT_LABELS[c],
      placementCategory: c,
      agents: buckets.get(c) || [],
      count: (buckets.get(c) || []).length,
    }));
}

// ── Ordering ────────────────────────────────────────────────────────────────

function timeValue(value: string | null | undefined): number {
  if (!value) return NaN;
  const t = new Date(value).getTime();
  return Number.isNaN(t) ? NaN : t;
}

/** `null` means THE AGENT HAS NOTHING TO SORT BY (never active) and always
 *  sinks to the bottom in both directions — same rule sortTasks's own
 *  orderValue documents, so flipping the direction never floats an agent
 *  that has never run to the top of the list. Cost is NOT given this
 *  treatment: $0.0000 is a real fact about an agent (it has spent nothing),
 *  not missing data, so "lowest first" genuinely means it first. */
function orderValue(agent: FleetAgent, ordering: AgentOrdering, cost: Map<string, number>): number | string | null {
  switch (ordering) {
    case "last_active": {
      const t = timeValue(agent.last_activity);
      return Number.isNaN(t) ? null : t;
    }
    case "cost":
      return cost.get(agent.agent_id) || 0;
    case "name":
      return (agent.label || "").trim().toLowerCase();
    default:
      return null;
  }
}

/**
 * Order a list of agents for the Board and List surfaces. Not mutating — same
 * reasoning as sortTasks: callers hold `agents` straight off the polled cache.
 *
 * The deleted Cards grid never ran through this: planAgentCards ranks by
 * attention (blocked > working > unfinished setup > stopped > healthy,
 * alphabetical within), which was a settled decision for that surface —
 * "never recency... a card's position is stable" — and Ordering was not even
 * offered while Cards was the layout, for exactly that reason. That rank is
 * still computed (planAgentCards is still the shared filter — see
 * agent-card-face.ts), but nothing renders it any more: page.tsx re-sorts the
 * filtered set through THIS function before handing it to either remaining
 * layout, so both are ordered by the reader's own Ordering choice, never by
 * attention rank.
 */
export function sortAgentsForView(
  agents: FleetAgent[],
  ordering: AgentOrdering,
  direction: AgentOrderDirection,
  cost: Map<string, number>,
): FleetAgent[] {
  const sign = direction === "asc" ? 1 : -1;
  return [...agents].sort((a, b) => {
    const av = orderValue(a, ordering, cost);
    const bv = orderValue(b, ordering, cost);
    if (av === null && bv === null) return tiebreak(a, b);
    if (av === null) return 1;
    if (bv === null) return -1;
    let primary = 0;
    if (typeof av === "string" || typeof bv === "string") {
      primary = String(av).localeCompare(String(bv));
    } else {
      primary = av - bv;
    }
    if (primary !== 0) return primary * sign;
    return tiebreak(a, b);
  });
}

function tiebreak(a: FleetAgent, b: FleetAgent): number {
  return (a.label || "").localeCompare(b.label || "") || String(a.agent_id).localeCompare(String(b.agent_id));
}

// ── Card/row presentation helpers ────────────────────────────────────────────
// Intentional duplicates of AgentsList.tsx's own (non-exported) helpers — see
// the file header for why these are copied rather than imported.

/** model_config → short brand-family label, verbatim copy of AgentsList.tsx's
 *  brainLabel. */
export function agentBrainLabel(config?: Record<string, any>): string {
  const raw = String(config?.model || config?.resolved_model || "").toLowerCase();
  const provider = String(config?.provider || "").toLowerCase();
  if (raw.includes("deepseek") || provider === "deepseek") return "DeepSeek";
  if (raw.includes("sonnet-5") || raw === "claude-sonnet-5") return "Sonnet 5";
  if (raw.includes("sonnet")) return "Sonnet";
  if (raw.includes("opus")) return "Opus";
  if (raw.includes("haiku")) return "Haiku";
  if (raw.includes("gpt-5")) return "GPT-5";
  if (raw.includes("gpt-4")) return "GPT-4";
  if (raw.includes("llama")) return "Llama";
  if (raw.includes("ollama") || provider === "ollama") return "Ollama";
  if (provider === "anthropic") return "Claude";
  if (provider === "openai") return "GPT";
  if (raw) return raw.length > 14 ? `${raw.slice(0, 14)}…` : raw;
  return "";
}

/** 4 decimal places, matching AgentsList.tsx's own `money` — real per-turn
 *  costs are fractions of a cent. */
export function agentMoney(n: number): string {
  return formatUsd(n);
}

/** Splits fleet_tools._fetch_agent_channels' "key +N" string back apart —
 *  verbatim copy of AgentsList.tsx's parseChannelField. */
export function parseAgentChannelField(raw: string): { key: string; extra: number } {
  const trimmed = (raw || "").trim();
  const m = trimmed.match(/^(.*?)\s+\+(\d+)$/);
  if (m) return { key: m[1].trim(), extra: parseInt(m[2], 10) || 0 };
  return { key: trimmed, extra: 0 };
}

/**
 * THE CAPABILITY-PRESET BADGE, AND WHEN IT IS WORTH DRAWING AT ALL.
 *
 * `capability_preset` has exactly two creatable values (server:
 * capability_presets.CREATABLE_CAPABILITY_PRESETS) and "standard" is the
 * DEFAULT every agent gets unless someone deliberately picks the other one.
 * Measured on a seeded 40-agent workspace: 40 of 40 read "Standard". A badge
 * on every row distinguishes nothing — the same test agent-card-face.ts
 * applies to kill `activity_preview` ("Created is true of every agent that has
 * ever existed"), and the same one CLAUDE.md records the founder applying to
 * the amber reach line once it turned out to be the majority state.
 *
 * So: "" for the default and for anything unset, and the real word for a
 * genuinely non-default preset — which is the only case a reader can act on.
 * Returning "" rather than hiding it at the render site keeps the rule in one
 * place; both renderings already skip an empty badge.
 */
export function agentPresetBadge(preset: string | undefined | null): string {
  const token = String(preset || "").trim().toLowerCase();
  if (!token || token === "standard") return "";
  const words = token.replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export { timeAgo as agentRelativeActivity };

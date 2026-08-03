/**
 * VIEW OPTIONS FOR THE WORKSPACE AGENTS PAGE — Board/List layout, grouping,
 * ordering, display properties.
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
 * parseAgentChannelField, agentActivityPreviewText) are intentional,
 * documented DUPLICATES of private (non-exported) functions already living
 * in AgentsList.tsx. They are not re-exported from there because touching
 * AgentsList.tsx at all is unnecessary risk to a table that must keep
 * behaving exactly as it does today — see that file. A one-line drift
 * between the flat table's formatting and this file's is a real bug to fix
 * by hand if it ever happens, which is a cheaper failure mode than a shared
 * import breaking the table this feature was never supposed to touch.
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
import type { FleetAgent, FleetProject } from "./fleet-data";
import { timeAgo } from "./fleet-presentation";

// ── Vocabulary ──────────────────────────────────────────────────────────────

/** Two views, the same split tasks make: Board is spatial, List is one line
 *  (or one row inside a section) per agent. "Grouped" is not a third view —
 *  it is the List with a grouping switched on (see agentSurfaceFor). */
export type AgentLayout = "board" | "list";

export type AgentGrouping = "none" | "status" | "project" | "placement";

/** Deliberately a SUBSET of the workspace Agents page's existing sort
 *  dropdown (agents/page.tsx's SORT_OPTIONS: last_active/status/cost/
 *  name/group) — "status" and "group" are dropped because they are now
 *  what AgentGrouping does, and offering the same reshaping two different
 *  ways in two different controls is the confusing-duplicate-control this
 *  file avoids by cutting rather than by unifying (see AgentViewOptions.tsx
 *  for how this coexists on-screen with that unchanged legacy dropdown). */
export type AgentOrdering = "last_active" | "cost" | "name";

export type AgentOrderDirection = "asc" | "desc";

/** The six columns the existing flat table already draws (AgentsList.tsx's
 *  Brain/Placement/Channels/Last active/Cost/Status), offered as a toggle
 *  set for the two NEW renderings only (Board card, Grouped-list row) — the
 *  flat table itself is not gated by this at all, it keeps showing all six
 *  unconditionally exactly as it does today. */
export type AgentDisplayProperty = "brain" | "placement" | "channels" | "lastActive" | "cost" | "status";

/** Which rendering is on screen. "list" (the flat, ungrouped table) is
 *  included so agentSurfaceFor/displayPropertiesFor have a total answer, but
 *  no AgentDisplayProperty below ever lists "list" among its surfaces — the
 *  flat table's columns are not toggleable, so displayPropertiesFor("list")
 *  is always []. AgentViewOptions.tsx reads that empty array as "hide the
 *  Display properties section", not as "render an empty one". */
export type AgentSurface = "board" | "grouped" | "list";

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
  { value: "project", label: "Project" },
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
 * Every display toggle, and the surfaces each is real on. Both are "board"
 * and "grouped" for all six — unlike tasks, an agent's board card has no
 * interactive control living inside any of these (there is no drag-and-drop
 * here; see AgentsBoard.tsx for why), so nothing needs to be exempted from
 * the toggle the way the task board's status ring is.
 */
export const AGENT_DISPLAY_PROPERTIES: {
  key: AgentDisplayProperty;
  label: string;
  surfaces: AgentSurface[];
}[] = [
  { key: "brain", label: "Brain", surfaces: ["board", "grouped"] },
  { key: "placement", label: "Placement", surfaces: ["board", "grouped"] },
  { key: "channels", label: "Channels", surfaces: ["board", "grouped"] },
  { key: "lastActive", label: "Last active", surfaces: ["board", "grouped"] },
  { key: "cost", label: "Cost", surfaces: ["board", "grouped"] },
  { key: "status", label: "Status", surfaces: ["board", "grouped"] },
];

const ALL_DISPLAY_KEYS = AGENT_DISPLAY_PROPERTIES.map((p) => p.key);

/** Everything on, List layout, no grouping — exactly what the Agents page has
 *  always shown, so a reader who never opens the popover sees no change. */
export const DEFAULT_AGENT_VIEW_OPTIONS: AgentViewOptions = {
  layout: "list",
  grouping: "none",
  ordering: "last_active",
  direction: "desc",
  display: Object.fromEntries(ALL_DISPLAY_KEYS.map((k) => [k, true])) as AgentDisplayState,
};

export function agentSurfaceFor(options: AgentViewOptions): AgentSurface {
  if (options.layout === "board") return "board";
  return options.grouping === "none" ? "list" : "grouped";
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

const STORAGE_VERSION = 1;

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
      layout: oneOf<AgentLayout>(parsed.layout, ["board", "list"], DEFAULT_AGENT_VIEW_OPTIONS.layout),
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

/**
 * One agent's real status, folded into one of the four board columns.
 * "needs_attention" fires ONLY for deriveAgentStatus's "error" and
 * "degraded" tones — both already real, config-honesty failures the backend
 * computed (see the file header) — never as a made-up fifth state. Every
 * other tone maps to a column that was already true of the agent.
 */
export function agentStatusGroup(agent: AgentBrainStatusLike, gateways: FleetGateway[]): AgentStatusGroup {
  const { tone } = deriveAgentStatus(agent, gateways);
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
 * AGENT_STATUS_GROUPS order, one board column). `empty` marks the catch-all
 * bucket (an agent with no project) — drawn quieter and always last, same
 * convention groupTasks() in task-view-options.ts uses for Unassigned/No
 * label.
 */
export type AgentGroup = {
  key: string;
  label: string;
  agents: FleetAgent[];
  count: number;
  statusGroup?: AgentStatusGroup;
  projectId?: string;
  placementCategory?: AgentPlacementCategory;
  empty?: boolean;
};

export function groupAgents(
  agents: FleetAgent[],
  grouping: AgentGrouping,
  {
    gateways = [],
    projectById = new Map<string, FleetProject>(),
  }: { gateways?: FleetGateway[]; projectById?: Map<string, FleetProject> } = {},
): AgentGroup[] {
  if (grouping === "none") {
    return [{ key: "all", label: "All agents", agents, count: agents.length }];
  }

  if (grouping === "status") {
    const buckets = new Map<AgentStatusGroup, FleetAgent[]>(AGENT_STATUS_GROUPS.map((g) => [g.value, []]));
    for (const agent of agents) buckets.get(agentStatusGroup(agent, gateways))?.push(agent);
    return AGENT_STATUS_GROUPS.filter((g) => (buckets.get(g.value) || []).length > 0).map((g) => ({
      key: g.value,
      label: g.label,
      statusGroup: g.value,
      agents: buckets.get(g.value) || [],
      count: (buckets.get(g.value) || []).length,
    }));
  }

  if (grouping === "placement") {
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

  // grouping === "project" — genuinely valuable here specifically because
  // this page is cross-project (each agent carries its own project_id); the
  // per-project Agents tab has no equivalent need for this grouping.
  const buckets = new Map<string, AgentGroup>();
  for (const agent of agents) {
    const key = agent.project_id || "__none__";
    if (!buckets.has(key)) {
      const proj = agent.project_id ? projectById.get(agent.project_id) : undefined;
      buckets.set(key, {
        key,
        // "Ungrouped" matches the exact word AgentsList.tsx's own
        // group-by-project sort mode already uses for the same bucket — one
        // word for "no project" across both surfaces.
        label: proj?.name || "Ungrouped",
        projectId: agent.project_id || undefined,
        agents: [],
        count: 0,
        empty: !agent.project_id,
      });
    }
    const group = buckets.get(key)!;
    group.agents.push(agent);
    group.count += 1;
  }
  return [...buckets.values()].sort((a, b) => {
    if (Boolean(a.empty) !== Boolean(b.empty)) return a.empty ? 1 : -1;
    return a.label.localeCompare(b.label);
  });
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
 * Order a list of agents for the Board and Grouped-list surfaces. Not
 * mutating — same reasoning as sortTasks: callers hold `agents` straight off
 * the polled cache. The FLAT table's own ordering is untouched by this
 * function; it keeps using agents/page.tsx's existing sortAgents (the
 * pre-existing "Sort by" dropdown), unchanged — see AgentViewOptions.tsx for
 * why the two don't compete on screen.
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
  return `$${n.toFixed(4)}`;
}

/** Splits fleet_tools._fetch_agent_channels' "key +N" string back apart —
 *  verbatim copy of AgentsList.tsx's parseChannelField. */
export function parseAgentChannelField(raw: string): { key: string; extra: number } {
  const trimmed = (raw || "").trim();
  const m = trimmed.match(/^(.*?)\s+\+(\d+)$/);
  if (m) return { key: m[1].trim(), extra: parseInt(m[2], 10) || 0 };
  return { key: trimmed, extra: 0 };
}

/** Display-time guard against pre-2026-07-09 raw internal titles — verbatim
 *  copy of AgentsList.tsx's own RAW_INTERNAL_TITLE + activityPreviewText. */
const RAW_INTERNAL_TITLE = /^Fleet:\s|ainstall_[a-z0-9]|(?:^|[\s:])ws_[a-z0-9]/i;

export function agentActivityPreviewText(agent: FleetAgent): string {
  const preview = (agent.activity_preview || "").trim();
  if (!preview || RAW_INTERNAL_TITLE.test(preview)) return "No activity yet";
  return preview;
}

export { timeAgo as agentRelativeActivity };

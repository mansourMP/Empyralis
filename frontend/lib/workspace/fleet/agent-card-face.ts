import type { AgentStatusTone } from "./fleet-presentation";
import { taskDisplayId } from "./task-status";
import { parseAgentChannelField } from "./agent-view-options";
import { CHANNEL_LABELS } from "./fleet-icons";

/**
 * WHAT ONE AGENT CARD SAYS ABOUT ITSELF, AND IN WHICH SLOT.
 *
 * The workspace Agents surface used to be a scrolling picker column beside a
 * mostly-empty pane reading "Pick an agent to watch it work." That shape is
 * left over from a feature that no longer exists: the pane existed because you
 * used to CHAT with an agent there, and chat left the platform entirely
 * (CLAUDE.md, "THE PLATFORM IS NOT A CHAT PRODUCT" — conversation happens in
 * channels; the platform is setup and observation). What survived was a
 * Telegram-shaped contact list for agents nobody can talk to, plus a second
 * picker in the content area while the rail already says "Agents" — which is
 * the one thing "the rail is where you pick; the content is what you picked"
 * forbids.
 *
 * The founder, looking at it live with 19 agents: *"it acts like something
 * like telegram still but it shouldn't look like that… maybe just like as we
 * show this project we would show also the agents as well."*
 *
 * What he was actually looking at, and why every row read the same:
 *
 * ```
 *   Support Desk      Created          3m       ← lifecycle, true of all 19
 *   Billing Watch     Created          3m
 *   Docs Keeper       Configured       1h
 *   Standup Bot       Standup Bot chat completed   2d
 * ```
 *
 * `activity_preview` is a LIFECYCLE verb. "Created" is true of every agent
 * that has ever existed, so a column of it distinguishes nothing. This module
 * replaces it with the two facts that actually separate one agent from
 * another, and refuses to invent a third.
 *
 * ```
 *   SLOT 1  state   is it working RIGHT NOW, and can it run at all
 *   SLOT 2  reach   what is it on, or where does it answer — or NEITHER
 * ```
 *
 * THE TWO SLOTS ARE ALLOWED TO DISAGREE, AND THAT IS THE POINT. A perfectly
 * healthy agent that nothing can reach reads "Ready" in slot 1 and "No channel
 * or tasks yet" in slot 2, because both are true: its brain works, and nothing
 * will ever ask it anything. Collapsing those into one word would have to lie
 * about one of them — the same "two different facts may never share one
 * signal" law CLAUDE.md already states for delivery outcomes and invite mail.
 * Slot 2 carries the attention styling in that case, because slot 2 is the
 * half a person can act on.
 *
 * NOTHING HERE IS FABRICATED. Every value below is read off data the surface
 * already has:
 *
 * ```
 *   status   deriveAgentStatus(agent, gateways)   gateway-box-picker.tsx
 *              PASSED IN, never recomputed — it is the one status vocabulary
 *              every other agent surface renders, and it already folds
 *              brain-readiness (Needs sign-in / Computer offline / Model not
 *              loaded) that a bare deriveStatus() would report as "Ready".
 *   tasks    useFleetWorkspaceTasks(ws)           fleet-data.ts:1504
 *              ONE existing call for the whole workspace, already polled by
 *              PrimaryRail/Inbox/My work through the same shared cache — so
 *              this surface adds zero network. Grouped here by
 *              assignee_agent_id.
 *   channel  agent.channel                        fleet_tools._fetch_agent_channels
 *              "slack" / "sage_telegram_hosted +2" — the primary enabled
 *              channel binding plus a count. Parsed by the EXISTING
 *              parseAgentChannelField rather than a fourth copy of that regex.
 * ```
 *
 * `current_run_id` is deliberately NOT the working signal on its own. It is
 * truthful but de-facto always null: it is only ever populated from a
 * `runtime_profiles.machine_id` heartbeat, and nothing in the fleet UI ever
 * points an agent at a machine-bound profile (gateway-box-picker.tsx says the
 * same thing out loud — "the runtime_profile heartbeat, which real Fleet
 * agents never update"). A card built on it alone could never say "Working"
 * about anything. It is still honoured when set — it just is not the only
 * evidence; an assigned task sitting in `in_progress` is the other, and it is
 * the one that actually occurs.
 *
 * Pure, no React, no CSS import — same discipline as agent-count-shape.ts /
 * channel-doors.ts / connector-card-face.ts, so agent-card-face.test.ts drives
 * the REAL rule the component renders against instead of re-deriving it.
 */

/** The runtime status this module is handed. Structurally
 *  `deriveAgentStatus`'s own return type — declared rather than imported so
 *  this module never pulls in gateway-box-picker.tsx (a .tsx file carrying
 *  React). The TONE VOCABULARY is imported, so it cannot fork. */
export type AgentRuntimeStatus = { tone: AgentStatusTone; label: string };

export type AgentCardAgentInput = {
  agent_id: string;
  label?: string;
  /** fleet_tools._fetch_agent_channels' "key +N" string, or "". */
  channel?: string;
};

/** The three fields of a workspace task this card reads. Structural, so the
 *  test can drive it with a literal and the component can pass a real
 *  `FleetTask` unchanged. */
export type AgentCardTaskInput = {
  id: string;
  title?: string;
  status?: string | null;
  assignee_agent_id?: string | null;
  number?: number | null;
  project_task_key?: string | null;
};

export type AgentCardState = "stopped" | "blocked" | "working" | "idle";

export type AgentCardReachKind = "task" | "tasks" | "channel" | "none";

export type AgentCardReach = {
  kind: AgentCardReachKind;
  /** The whole second line, ready to render. */
  label: string;
  /** Set only for kind "channel" — the icon lookup key
   *  (fleet-icons.channelIconSrc). "" everywhere else, never a guess. */
  channelKey: string;
};

export type AgentCardFace = {
  state: AgentCardState;
  /** Slot 1's word. Always the runtime status's OWN label when the status is
   *  what decided the state, so this surface never invents a sixth word for a
   *  condition the rest of the product already names. */
  stateLabel: string;
  tone: AgentStatusTone;
  reach: AgentCardReach;
  /** Slot 2 is something to go and fix. Drives the amber treatment; never a
   *  colour decision made at the render site. */
  needsAttention: boolean;
  /** Scan order — see planAgentCards below. */
  rank: number;
};

/** A task nobody has finished. `done` is the only terminal status in
 *  FLEET_TASK_STATUSES, so this is "not done" rather than an enumerated list
 *  that would silently stop covering a status added later. */
function taskIsOpen(task: AgentCardTaskInput): boolean {
  return String(task.status || "").trim().toLowerCase() !== "done";
}

function taskIsActive(task: AgentCardTaskInput): boolean {
  return String(task.status || "").trim().toLowerCase() === "in_progress";
}

/** Every task in the workspace, bucketed by the agent it is assigned to.
 *  Built ONCE per render by the caller and handed to every card, rather than
 *  each card filtering the whole workspace list — 19 agents x N tasks is the
 *  kind of quadratic scan that is invisible at 3 agents and measurable at 60.
 *  Tasks with no agent assignee (a human's, or nobody's) are dropped here, so
 *  a card can never claim work that was never given to it. */
export function groupTasksByAgent<T extends AgentCardTaskInput>(
  tasks: readonly T[],
): Map<string, T[]> {
  const byAgent = new Map<string, T[]>();
  for (const task of tasks) {
    const agentId = String(task.assignee_agent_id || "").trim();
    if (!agentId) continue;
    const bucket = byAgent.get(agentId);
    if (bucket) bucket.push(task);
    else byAgent.set(agentId, [task]);
  }
  return byAgent;
}

/** Tones that mean "this agent cannot currently run a turn". `unknown`
 *  ("Not deployed") is in here deliberately: it is what an install with no
 *  runtime placement row resolves to, and such an agent genuinely cannot run —
 *  reporting it as calm would be the "Ready" lie one level up. */
const BLOCKED_TONES: ReadonlySet<AgentStatusTone> = new Set<AgentStatusTone>([
  "offline",
  "error",
  "degraded",
  "unknown",
]);

/** The one line for slot 2, in the order the brief states: the task it is on,
 *  the tasks waiting for it, where it answers — or, plainly, neither.
 *
 *  WORK BEATS CHANNEL, and that ordering is a judgement worth naming. A
 *  channel is where an agent CAN be reached; a task is what it has actually
 *  been given. An agent answering on Telegram with a task in flight is, right
 *  now, about the task — and the channel is one click away on its own page.
 *  The reverse ordering would bury the only fact that changes hour to hour
 *  under one that changes once. */
export function agentCardReach(
  agent: AgentCardAgentInput,
  tasks: readonly AgentCardTaskInput[],
): AgentCardReach {
  const open = tasks.filter(taskIsOpen);
  const active = open.filter(taskIsActive);

  if (active.length > 0) {
    // WHICH of several in-flight tasks gets named is decided by a stable key,
    // not by the order the list happened to arrive in. `fleet_list_tasks`
    // makes no ordering promise, so `active[0]` would let a card's own line
    // change from one 30s poll to the next with nothing having happened — the
    // same "a card's position is STABLE" property the sort below exists for,
    // applied to its text.
    const first = [...active].sort((a, b) => a.id.localeCompare(b.id))[0];
    const displayId = taskDisplayId({
      id: first.id,
      number: first.number ?? null,
      project_task_key: first.project_task_key ?? null,
    });
    const title = String(first.title || "").trim() || "Untitled task";
    const more = active.length - 1;
    return {
      kind: "task",
      label: `${displayId} ${title}${more > 0 ? ` +${more}` : ""}`,
      channelKey: "",
    };
  }

  if (open.length > 0) {
    return {
      kind: "tasks",
      label: `${open.length} task${open.length === 1 ? "" : "s"} waiting`,
      channelKey: "",
    };
  }

  const { key, extra } = parseAgentChannelField(agent.channel || "");
  if (key) {
    // CHANNEL_LABELS is the shared key->name map every other channel surface
    // reads. An unmapped key falls back to the key itself rather than to a
    // guessed pretty name — a channel we have not taught this map about is
    // still a real channel, and naming it wrong is worse than naming it raw.
    const name = CHANNEL_LABELS[key] || key;
    return {
      kind: "channel",
      label: `Answers on ${name}${extra > 0 ? ` +${extra}` : ""}`,
      channelKey: key,
    };
  }

  // The state the founder's own fleet is mostly in, and it is a real finding
  // rather than an empty cell: nothing can reach this agent and nothing has
  // been given to it. Said once, in plain words, with no instruction attached
  // — a professional tool labels, it does not lecture.
  return { kind: "none", label: "No channel or tasks yet", channelKey: "" };
}

export function planAgentCardFace(
  agent: AgentCardAgentInput,
  status: AgentRuntimeStatus,
  tasks: readonly AgentCardTaskInput[],
): AgentCardFace {
  const reach = agentCardReach(agent, tasks);
  const hasActiveTask = tasks.filter(taskIsOpen).some(taskIsActive);

  let state: AgentCardState;
  if (status.tone === "stopped") state = "stopped";
  else if (BLOCKED_TONES.has(status.tone)) state = "blocked";
  else if (status.tone === "working" || hasActiveTask) state = "working";
  else state = "idle";

  // "Working" is this module's own word for exactly one case: the runtime
  // status says Ready, and a task assigned to this agent is in progress. Every
  // other state re-uses the status's own label, so Stopped / Offline / Needs
  // sign-in / Not deployed read identically here and on the agent's own page.
  const stateLabel = state === "working" ? "Working" : status.label;
  const tone: AgentStatusTone = state === "working" ? "working" : status.tone;

  return {
    state,
    stateLabel,
    tone,
    reach,
    // A stopped agent with no reach is NOT attention — it was stopped on
    // purpose, and painting a deliberate choice as a defect is how a warning
    // colour stops meaning anything.
    needsAttention: state === "blocked" || (state === "idle" && reach.kind === "none"),
    rank: cardRank(state, reach.kind),
  };
}

/** Scan order, most-worth-looking-at first — the same read-by-urgency
 *  ordering the Projects page's own status summary already uses.
 *
 *  Deliberately NOT recency. Recency is a CHAT LIST's ordering, and it is the
 *  ordering the surface this replaces used; it answers "who did I last talk
 *  to", which is a question about a product that no longer exists here. A card
 *  grid answers "which one do I care about", and the honest sort for that is
 *  by what needs a person. Alphabetical within a rank keeps a card's position
 *  STABLE — nothing moves under the cursor because a timestamp ticked. */
function cardRank(state: AgentCardState, reachKind: AgentCardReachKind): number {
  if (state === "blocked") return 0; // something is wrong
  if (state === "working") return 1; // in flight, worth watching
  if (state === "idle" && reachKind === "none") return 2; // unfinished setup
  if (state === "stopped") return 3; // deliberate, calm
  return 4; // healthy and reachable
}

/** Case-insensitive substring match over the two things the face actually
 *  shows — the agent's name and its reach line. Searching text a card does not
 *  display produces a filtered grid whose survivors have no visible reason to
 *  be there. A blank query matches everything. */
export function matchesAgentCardQuery(
  agent: AgentCardAgentInput,
  face: AgentCardFace,
  query: string,
): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return `${agent.label || ""} ${face.reach.label}`.toLowerCase().includes(q);
}

export type PlannedAgentCard<T extends AgentCardAgentInput> = { agent: T; face: AgentCardFace };

/** The whole grid in one call: face every agent, keep the ones that match the
 *  query, then sort. Filter BEFORE sort for the same reason planConversationList
 *  did — a narrowed result must not be ordered by tie-breaks belonging to rows
 *  that were already excluded. */
export function planAgentCards<T extends AgentCardAgentInput>(
  agents: readonly T[],
  statusFor: (agent: T) => AgentRuntimeStatus,
  tasksByAgent: Map<string, AgentCardTaskInput[]>,
  query: string,
): PlannedAgentCard<T>[] {
  const planned: PlannedAgentCard<T>[] = [];
  for (const agent of agents) {
    const face = planAgentCardFace(agent, statusFor(agent), tasksByAgent.get(agent.agent_id) || []);
    if (matchesAgentCardQuery(agent, face, query)) planned.push({ agent, face });
  }
  planned.sort((a, b) => {
    if (a.face.rank !== b.face.rank) return a.face.rank - b.face.rank;
    return (a.agent.label || "").localeCompare(b.agent.label || "");
  });
  return planned;
}

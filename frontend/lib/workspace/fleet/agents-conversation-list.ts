import { timeAgo } from "./fleet-presentation";

/**
 * THE WORKSPACE-LEVEL AGENTS SURFACE, TAKE TWO (2026-08-20).
 *
 * 2026-08-19 moved Agents onto the primary rail: pressing "Agents" morphed
 * the WHOLE rail (Inbox/My work/Projects all vanished) into a bare list of
 * agent names. The founder tried it and rejected it the same session it
 * shipped: "we have to find a way, some better way to show this agent —
 * not on this left rail but something else... I really don't like the
 * design of it right now. But at the same time I should be able to see my
 * conversations."
 *
 * Two complaints, one root cause. The rail-morph made the picker cost the
 * REST OF THE APP — you could not see Inbox or Projects while browsing
 * agents, which is a worse trade than the thing it replaced. And a bare
 * name + a status dot is not a conversation: no sense of which agent you
 * last talked to, or what was last said.
 *
 * The fix moves the picker OFF the primary rail and INTO the content area,
 * as a genuine second pane beside the chat — the exact shape this codebase
 * already uses for Inbox/Work/Memory (`.fleet-inbox-list` /
 * `.fleet-work-list` / `.fleet-memory-file-list`, "a hairline-divided list,
 * never cards... per the Linear reference"), and the same shape the
 * founder himself specified and had built once already for a project's own
 * agent list before it was folded into the rail — Telegram's mechanic: the
 * list stays put, the pane beside it swaps. "The rail is where you pick"
 * (2026-08-16) does not forbid this: its point was never ONE PICKER, ONE
 * PLACE — it was ONE PICKER AT A TIME. Here the primary rail stops being a
 * picker for agents at all (just a normal flat nav row, like Projects); the
 * new pane is the only surface answering "which agent" — never two at once.
 *
 * Each row IS a conversation — the agent's own recent activity, which this
 * codebase already tracks per-agent (`FleetAgent.activity_preview` /
 * `last_activity`, `fleet_tools`'s activity ledger, already rendered by
 * AgentsList.tsx's flat table) — surfaced here the way a messenger surfaces
 * a chat list: name, a one-line preview of what last happened, how long
 * ago. No fabricated data: an agent with no activity yet reads as exactly
 * that, never a guessed timestamp.
 *
 * Pure, dependency-light (only fleet-presentation.ts's own zero-React
 * `timeAgo`) — same discipline as agent-count-shape.ts / channel-doors.ts /
 * primary-rail-space.ts: a plain `tsx` test imports these functions
 * directly rather than re-deriving the sort/filter/format rules inline in
 * the component.
 */

export type ConversationAgent = {
  agent_id: string;
  label: string;
  last_activity?: string | null;
  activity_preview?: string;
};

/** Same guard AgentsList.tsx's own activityPreviewText already applies —
 *  display-time protection against pre-2026-07-09 rows that still carry a
 *  raw internal title ("Fleet: {action} → {id}", a bare ainstall_/ws_ id).
 *  Duplicated rather than imported: that function is private to
 *  AgentsList.tsx and this module stays free of any component-file
 *  dependency, same reason primary-rail-space.ts stays free of fleet-data. */
const RAW_INTERNAL_TITLE = /^Fleet:\s|ainstall_[a-z0-9]|(?:^|[\s:])ws_[a-z0-9]/i;

/** The list row's preview line. Never fabricated: an agent nobody has
 *  talked to or that has done nothing yet reads as "No activity yet", not
 *  a blank line or an invented sentence. */
export function conversationPreview(agent: ConversationAgent): string {
  const preview = (agent.activity_preview || "").trim();
  if (!preview || RAW_INTERNAL_TITLE.test(preview)) return "No activity yet";
  return preview;
}

/** Compact relative time for a list row ("3m", "2h", "Aug 12") — timeAgo's
 *  own format with the trailing " ago" trimmed, the same transform
 *  AgentsList.tsx's private compactAgo already applies, standalone here so
 *  this module has no component-file dependency. Empty string (never a
 *  fabricated "never") when there is no activity to date — the row's own
 *  "No activity yet" preview already says that; a second "never" stamp
 *  beside it would repeat the same fact in two places. */
export function conversationTimestamp(iso: string | null | undefined): string {
  if (!iso) return "";
  return timeAgo(iso).replace(/\s+ago$/, "");
}

/** Most-recently-active conversation first — the one universal ordering
 *  every messenger's chat list uses, and the only one this surface offers
 *  (no board/grouped/sort-by picker here; see this file's own header for
 *  why those were retired rather than carried over). Agents with no
 *  activity yet sort after every agent that has some, in that group
 *  ordered alphabetically by label so a freshly-created agent has a STABLE
 *  position instead of jumping around as `last_activity` stays null. */
export function sortAgentsByRecency<T extends ConversationAgent>(agents: readonly T[]): T[] {
  const withActivity: T[] = [];
  const withoutActivity: T[] = [];
  for (const a of agents) {
    (a.last_activity ? withActivity : withoutActivity).push(a);
  }
  withActivity.sort((a, b) => new Date(b.last_activity!).getTime() - new Date(a.last_activity!).getTime());
  withoutActivity.sort((a, b) => (a.label || "").localeCompare(b.label || ""));
  return [...withActivity, ...withoutActivity];
}

/** Case-insensitive substring match against the agent's name and its
 *  latest activity — a blank query matches everything (the search box's
 *  own empty state), never a guess at fuzzy matching. */
export function matchesConversationQuery(agent: ConversationAgent, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  const haystack = `${agent.label || ""} ${conversationPreview(agent)}`.toLowerCase();
  return haystack.includes(q);
}

/** The whole list-pane pipeline in one call: filter by query, then sort by
 *  recency — filtering BEFORE sorting so a narrowed result is never padded
 *  with the sort's own tie-break ordering of rows that were already
 *  excluded. */
export function planConversationList<T extends ConversationAgent>(agents: readonly T[], query: string): T[] {
  return sortAgentsByRecency(agents.filter((a) => matchesConversationQuery(a, query)));
}

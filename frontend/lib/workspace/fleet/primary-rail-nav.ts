import { FolderKanban, Inbox, type LucideIcon } from "lucide-react";

/**
 * The primary rail's top-level destinations — pulled into its own pure,
 * dependency-light module (no next/navigation, no React hooks) so a plain
 * `tsx` test can import the REAL list PrimaryRail.tsx renders instead of
 * re-typing it — the same "expected and actual come from the same place"
 * discipline every other pure-rule module here follows (agent-count-shape.ts,
 * channel-doors.ts).
 *
 * PROJECT IS THE SPINE (CLAUDE.md, "an agent belongs to its project and
 * works only there" + the 2026-08-13 navigation decision): Conversations and
 * Agents are GONE from this list, not merely reordered. Both used to be
 * top-level rows aggregating across every project's agents — Conversations
 * listed every agent's channel threads, Agents listed every agent in the
 * workspace — which is exactly the boundary CLAUDE.md's own project-scoping
 * rule says an agent-related surface must not reach past. An agent is now
 * reached by opening the project it lives in and its Agents section there
 * (see project-agents-rail-shape.ts) — never by a workspace-wide list.
 *
 * The underlying routes (`/w/{id}/agents`, `/w/{id}/conversations`) are
 * DELIBERATELY still live — several entries in next.config.ts's
 * LEGACY_REDIRECTS point AT `/agents`, and turning it into a redirect target
 * itself risks the exact "a redirect runs ahead of the router and makes a
 * real page unreachable" trap CLAUDE.md documents. They are simply no longer
 * linked from here or from the command palette's "Go to" section — a
 * deliberate choice to leave a working page reachable by URL rather than
 * force a route move neither this change nor its risk profile calls for.
 *
 * Inbox stays: "what needs me" genuinely spans every project, so it is not
 * an aggregation ACROSS the project boundary in the same sense — it is the
 * one surface whose entire job is to look across everything at once.
 * Projects is the spine itself, not an aggregation of agents at all.
 */
export type RailNavItem = {
  key: string;
  label: string;
  segment: string;
  icon: LucideIcon;
  chord: string;
  /** MAN-317: true for a surface that exists to aggregate AGENTS. With zero
   *  real agents there is nothing to aggregate — every event class the
   *  activity ledger knows about is agent- or gateway-driven, so Inbox hides
   *  itself from the rail at agent-count-shape.ts's "none" mode (see
   *  visibleRailItems below). Projects is deliberately NOT tagged: it is the
   *  workspace's own data (CLAUDE.md positioning — "the WORKSPACE is the
   *  product"), not a view OF the fleet, so it never hides for having zero
   *  agents. */
  aggregatesAgents?: boolean;
};

export const RAIL_ITEMS: RailNavItem[] = [
  { key: "inbox", label: "Inbox", segment: "inbox", icon: Inbox, chord: "i", aggregatesAgents: true },
  { key: "projects", label: "Projects", segment: "projects", icon: FolderKanban, chord: "p" },
];

/** The rail items actually shown — `hideAggregations` true at
 *  agent-count-shape.ts's "none" mode (nothing for an aggregating surface to
 *  show), false otherwise. Reversible for free: recomputed from the live
 *  agent count on every render by the caller, same as every other count-shape
 *  consumer here. */
export function visibleRailItems(hideAggregations: boolean): RailNavItem[] {
  return hideAggregations ? RAIL_ITEMS.filter((item) => !item.aggregatesAgents) : RAIL_ITEMS;
}

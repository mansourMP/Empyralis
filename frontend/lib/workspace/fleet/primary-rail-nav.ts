import { FolderKanban, Inbox, Library, ListChecks, type LucideIcon } from "lucide-react";

/**
 * The primary rail's destinations — pulled into its own pure,
 * dependency-light module (no next/navigation, no React hooks) so a plain
 * `tsx` test can import the REAL list PrimaryRail.tsx renders instead of
 * re-typing it — the same "expected and actual come from the same place"
 * discipline every other pure-rule module here follows (agent-count-shape.ts,
 * channel-doors.ts, my-work.ts).
 *
 * THE RAIL IS PLACES YOU GO; THE PAGE IS THINGS YOU DO (founder, 2026-08-15).
 * That one line settles what belongs here and what does not, and it is the
 * rule the two previous shapes each broke from a different side:
 *
 *   2026-08-13   the rail NESTED an open project's Tasks/Documents/Agents
 *                sections under its row. One of a project's three peers
 *                (Agents) then lived in navigation while the other two lived
 *                in the page — an asymmetry with no reason behind it, and
 *                the thing that actually read as broken.
 *   2026-08-14   the rail SWAPPED ENTIRELY inside a project: Inbox and
 *                Projects vanished and the project's agent names took over.
 *                Same asymmetry, louder — agents were now the only thing the
 *                rail could show about a project, and getting anywhere else
 *                went through a Back control.
 *   2026-08-15   FLAT. Inbox · My work · the project list · New project.
 *                Opening a project changes the page, never the rail;
 *                Tasks/Documents/Agents/People are that page's own tabs,
 *                which is where Tasks and Documents already lived.
 *   2026-08-16   WORK ONLY. Settings left this list: it had a rail row AND
 *                an account-menu row — two doors to one room, and the rail
 *                door was the one that didn't earn its place ("most
 *                configuration is set once and does not deserve equal
 *                billing with the things people look at daily"). The
 *                account menu keeps its Settings link; the /settings routes
 *                are untouched. Removing the row also removed the whole
 *                "footer placement" machinery, which existed for that one
 *                row and nothing else.
 *
 * The SAME day added the rail's one conditional shape: a SPACE
 * (primary-rail-space.ts — founder: "the rail is where you pick; the
 * content is what you picked"). Inside Settings the rail swaps this flat
 * list for the space's own pick-list with a "‹ Back" row on top. That is a
 * refinement of 2026-08-15, not a reversal: merely opening a project still
 * never changes the rail — a space exists only where the content's whole
 * job used to be a second nav column.
 *
 * PROJECT IS STILL THE SPINE (CLAUDE.md, "an agent belongs to its project and
 * works only there"): Conversations and Agents remain GONE as TOP-LEVEL rows.
 * Both used to aggregate across every project's agents, which is exactly the
 * boundary that rule says a nav surface must not reach past. Their routes
 * (`/w/{id}/agents`, `/w/{id}/conversations`) are DELIBERATELY still live —
 * several entries in next.config.ts's LEGACY_REDIRECTS point AT `/agents`,
 * and turning it into a redirect target itself risks the exact "a redirect
 * runs ahead of the router and makes a real page unreachable" trap CLAUDE.md
 * documents. They are simply not linked from here.
 *
 * "My work" answers "what is assigned to me, across every project" — a
 * question that had no surface at all before: you opened each project and
 * scanned. Like Inbox, it looks across everything at once BY DESIGN rather
 * than by accident; unlike Conversations and Agents it aggregates TASKS
 * (workspace data), not AGENTS, so it does not reach past the project
 * boundary that rule is about. See my-work.ts for what lands in it.
 *
 * "Context" answers "where is that document" — every document in the
 * workspace, in one GitHub-shaped tree (workspace = org, project = repo,
 * document.path = file path; see document-tree.ts). It sits beside Projects
 * for the same reason Projects does and My work does not disqualify itself:
 * it aggregates workspace DATA, never AGENTS, so it does not reach past the
 * boundary the project-as-spine rule is about. It earns a top-level place
 * because until it existed a document could only be found by first
 * remembering which project it was filed in — CLAUDE.md's own positioning
 * puts the team's accumulated CONTEXT at the centre of the product, and it
 * had no front door.
 *
 * NO ACTIVITY ROW, deliberately. It was floated and dropped in the same
 * conversation: per-agent activity already exists on the agent's own Work
 * tab, and CLAUDE.md's "a surface must earn its place" is explicit that an
 * unused top-level route is the failure mode, not the safe default. Recorded
 * here rather than left as a silence, so nobody re-derives it as an
 * oversight.
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
   *  visibleRailItems below).
   *
   *  Projects is deliberately NOT tagged: it is the workspace's own data
   *  (CLAUDE.md positioning — "the WORKSPACE is the product"), not a view OF
   *  the fleet. Neither is My work, for the same reason one level down — a
   *  task assigned to a PERSON exists whether or not the workspace has ever
   *  had an agent, so hiding it at zero agents would hide real work. */
  aggregatesAgents?: boolean;
};

export const RAIL_ITEMS: RailNavItem[] = [
  { key: "inbox", label: "Inbox", segment: "inbox", icon: Inbox, chord: "i", aggregatesAgents: true },
  { key: "my-work", label: "My work", segment: "my-work", icon: ListChecks, chord: "m" },
  { key: "projects", label: "Projects", segment: "projects", icon: FolderKanban, chord: "p" },
  { key: "context", label: "Context", segment: "context", icon: Library, chord: "c" },
];

/** The rail items actually shown — `hideAggregations` true at
 *  agent-count-shape.ts's "none" mode (nothing for an aggregating surface to
 *  show), false otherwise. Reversible for free: recomputed from the live
 *  agent count on every render by the caller, same as every other count-shape
 *  consumer here. */
export function visibleRailItems(hideAggregations: boolean): RailNavItem[] {
  return hideAggregations ? RAIL_ITEMS.filter((item) => !item.aggregatesAgents) : RAIL_ITEMS;
}

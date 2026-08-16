import { planAgentCountShape } from "./agent-count-shape";

/**
 * Whether a project's Agents section shows an agent pick-LIST at all — the
 * gate PrimaryRail.tsx composes before morphing into the project-agents
 * space (primary-rail-space.ts, 2026-08-16: "the rail is where you pick;
 * the content is what you picked"). The list used to render as an
 * in-content column beside the chat (ProjectAgentsRail.tsx +
 * agents/layout.tsx, both deleted with that change); the RULE is unchanged
 * and lives here so the space and any future caller share one answer.
 *
 * COMPOSED from agent-count-shape.ts's planAgentCountShape, never
 * re-derived — CLAUDE.md's own instruction: "reuse planAgentCountShape; do
 * not add a second, parallel rule."
 *
 *   0 agents  → "none"  → no list. Nothing to pick; the project's own
 *                          FirstAgentEmpty fills the whole pane, and the
 *                          rail stays flat.
 *   1 agent   → "solo"  → no list. A list of one is worse than no list —
 *                          the same call agent-count-shape.ts's own doc
 *                          comment already makes for a table of one. The
 *                          bare /agents index redirects straight into that
 *                          one agent's chat instead; the rail stays flat.
 *   2+ agents → "fleet" → the rail morphs: "‹ Back" (to the project), the
 *                          project's name, one row per agent. The content
 *                          pane holds only the chat. The rail never
 *                          remounts across navigation, so switching agents
 *                          keeps the list's scroll position and data.
 */
export function showsProjectAgentsRail(projectAgentCount: number): boolean {
  return planAgentCountShape(projectAgentCount) === "fleet";
}

/**
 * Whether the project-agents rail SPACE is actually active right now — the
 * one fact two different surfaces both need and must never compute
 * independently:
 *
 *   - PrimaryRail.tsx: whether to morph into the project-agents pick-list
 *     instead of staying the flat rail.
 *   - The project's own content view (ProjectDetailPage): whether to hide
 *     its Tasks/Documents/Agents tab strip, because rendering that strip
 *     while the rail is ALSO showing a pick-list is two navigation surfaces
 *     both claiming to be "where you pick an agent" at once — the exact
 *     violation "the rail is where you pick" (2026-08-16) exists to
 *     prevent.
 *
 * `onAgentsRoute` is each caller's own route read — PrimaryRail's
 * `railSpaceFromPathname` regex match, the project page's own
 * `view === "agents"` — this module deliberately knows nothing about
 * pathnames or React, same reason showsProjectAgentsRail doesn't either.
 * The count gate is composed from showsProjectAgentsRail, never
 * re-derived, so both callers are one call away from disagreeing only if
 * their OWN route read disagrees — not if the count rule ever changes.
 */
export function projectAgentsSpaceIsActive(onAgentsRoute: boolean, projectAgentCount: number): boolean {
  return onAgentsRoute && showsProjectAgentsRail(projectAgentCount);
}

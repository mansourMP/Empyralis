import { planAgentCountShape } from "./agent-count-shape";

/**
 * Project-as-spine nav (CLAUDE.md, 2026-08-13): an agent belongs to its
 * project and is reached only there. Inside a project's Agents section the
 * founder wants a compact list rail beside the content, Telegram-style —
 * click an agent and its chat fills the pane, the rail stays put so
 * switching agents never loses your place.
 *
 * Whether that rail renders is COMPOSED from agent-count-shape.ts's
 * planAgentCountShape, never re-derived — CLAUDE.md's own instruction:
 * "reuse planAgentCountShape; do not add a second, parallel rule."
 *
 *   0 agents  → "none"  → no rail. Nothing to list; the project's own
 *                          FirstAgentEmpty fills the whole pane, unchanged.
 *   1 agent   → "solo"  → no rail. A rail of one is worse than no rail —
 *                          the same call agent-count-shape.ts's own doc
 *                          comment already makes for a table of one. The
 *                          bare /agents index redirects straight into that
 *                          one agent's chat instead.
 *   2+ agents → "fleet" → the rail renders, and stays mounted (via the
 *                          agents/layout.tsx route layout) across every
 *                          navigation within the section.
 */
export function showsProjectAgentsRail(projectAgentCount: number): boolean {
  return planAgentCountShape(projectAgentCount) === "fleet";
}

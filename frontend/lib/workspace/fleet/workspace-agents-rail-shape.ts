import { planAgentCountShape } from "./agent-count-shape";

/**
 * Whether the top-level Agents rail item shows a pick-LIST at all — the
 * gate PrimaryRail.tsx composes before morphing into the workspace-agents
 * space (primary-rail-space.ts, 2026-08-19: the founder moved agents back
 * to the rail after deciding they should not live inside a specific
 * project). Same rule as project-agents-rail-shape.ts's own
 * showsProjectAgentsRail, at the wider scope: the agent count here is
 * WORKSPACE-WIDE (every real agent across every project), never a single
 * project's own count.
 *
 * COMPOSED from agent-count-shape.ts's planAgentCountShape, never
 * re-derived — the same instruction CLAUDE.md gives for the project-scoped
 * version applies here: reuse planAgentCountShape, do not add a second,
 * parallel rule.
 *
 *   0 agents  → "none"  → no list. The Agents rail row itself is hidden
 *                          (it carries `aggregatesAgents`, see
 *                          primary-rail-nav.ts) — nothing to pick.
 *   1 agent   → "solo"  → no list. A list of one is worse than no list. The
 *                          bare /agents index redirects straight into that
 *                          one agent's chat instead (AgentsPage's own
 *                          existing solo redirect); the rail stays flat.
 *   2+ agents → "fleet" → the rail morphs: "‹ Back", "Agents", one row per
 *                          real agent, workspace-wide. The content pane
 *                          holds only the chat (or a quiet prompt while
 *                          none is picked) — never a grid, never a board,
 *                          never a list rendered a second time in the
 *                          content area.
 */
export function showsWorkspaceAgentsRail(realAgentCount: number): boolean {
  return planAgentCountShape(realAgentCount) === "fleet";
}

/**
 * Whether the workspace-agents rail SPACE is actually active right now —
 * the one fact PrimaryRail.tsx (whether to morph the rail) and the
 * workspace Agents page (whether to hide its own grid/board/list and show
 * a quiet "select an agent" prompt instead) must never compute
 * independently. Exactly project-agents-rail-shape.ts's own
 * projectAgentsSpaceIsActive, at the workspace scope — see that function's
 * doc comment for why two surfaces sharing one predicate is the whole
 * point: rendering a picker in the rail AND a second one in the content
 * area at the same time is the "only one surface may be the picker"
 * violation CLAUDE.md documents.
 */
export function workspaceAgentsSpaceIsActive(onAgentsRoute: boolean, realAgentCount: number): boolean {
  return onAgentsRoute && showsWorkspaceAgentsRail(realAgentCount);
}

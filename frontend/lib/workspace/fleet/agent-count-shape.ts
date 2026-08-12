/**
 * The agent-COUNT shape — MAN-317: the UI collapses to what actually
 * exists, and the COUNT decides the shape, never a tier check, a plan
 * lookup, or a feature flag. Same instinct as channel-doors.ts's
 * planDoors/planChannelDoors: a pure function taking a count and
 * returning a MODE that every renderer switches on, so the expected
 * shape and the actual shape can never disagree — a test imports this
 * function directly instead of re-deriving the thresholds.
 *
 * THE COUNT IS OF REAL AGENTS — SAGE/THE OPERATOR NEVER COUNTS
 * --------------------------------------------------------------
 * Every caller here must pass the already Sage-filtered count (see
 * fleet-presentation.ts's isSageAgent/findSageAgent — the same exclusion
 * FleetHome/PrimaryRail/AgentsPage already apply before counting for
 * every OTHER purpose). Passing the raw fleet-agents length would mean
 * "none" can never be reached: confirmed empirically 2026-08-13 against a
 * brand-new seeded account — `fleet_list_agents` calls
 * `list_workspace_agent_installs(..., include_master=True)`, so a
 * workspace's Operator install is already in the response before a
 * customer ever creates their first real agent. FleetHome's own header
 * count and InboxPage's `freshWorkspace` gate were both reading the raw,
 * Sage-included length — neither could ever see zero, which is why the
 * grid rendered as a blank void instead of the "start your first agent"
 * teaching state, and why the Inbox never showed its onboarding empty
 * state either. Both are fixed alongside this module landing.
 *
 *   count === 0  →  "none"   The surfaces that exist to aggregate agents
 *                             (Inbox, Conversations, the Agents table)
 *                             have nothing to aggregate — hidden from the
 *                             rail. FleetHome teaches instead of
 *                             surveying an empty fleet.
 *   count === 1  →  "solo"   One thing exists. A table of one is worse
 *                             than no table (the issue's own words) — the
 *                             Agents rail entry and the workspace root
 *                             both route straight to that one agent
 *                             instead of wrapping it in a fleet table
 *                             whose filters/sort/cost-comparison controls
 *                             have nothing to act on.
 *   count >= 2   →  "fleet"  Today's behaviour, unchanged. Once there is
 *                             something to compare, the survey tools earn
 *                             their place back automatically — no setting
 *                             anywhere flips it, the count alone does.
 *
 * Never a tier/plan lookup: this takes a number and nothing else. A
 * workspace on any tier that happens to own zero or one agent gets this
 * shape; a workspace that grows past two gets the fleet shape back the
 * instant the count crosses it.
 */
export type AgentCountMode = "none" | "solo" | "fleet";

export function planAgentCountShape(realAgentCount: number): AgentCountMode {
  if (realAgentCount <= 0) return "none";
  if (realAgentCount === 1) return "solo";
  return "fleet";
}

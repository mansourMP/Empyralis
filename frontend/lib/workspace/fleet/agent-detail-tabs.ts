/**
 * The agent detail surface's TAB VOCABULARY — the one list that decides
 * which `[tab]` route segments are real, shared by the surface that OFFERS
 * the tabs and by both routes that have to ACCEPT them.
 *
 * WHY THIS MODULE EXISTS (MAN-369 follow-up, 2026-08-27): it didn't, and
 * the Context tab was unreachable because of it. `context` — the per-agent
 * CONTEXT GRANT, which CLAUDE.md records as the founder's own core decision
 * ("an agent belongs to the WORKSPACE... context is GRANTED, never
 * inherited") — was declared in FleetAgentDetail's TabId, listed in its
 * TABS, grouped under Reach in CONFIGURE_GROUPS, and genuinely RENDERED by
 * `activeTab === "context" && <ContextTab .../>`. The backend shipped, the
 * tab shipped, and both route files' own hand-kept `VALID_TABS` arrays had
 * never been told about it — so every navigation to `.../agents/{id}/context`,
 * whether a deep link or a click on "Context" inside the Configure sheet's
 * own rail, was silently coerced to "chat". A control that is offered and
 * cannot be reached is a DEAD CONTROL, which this product's laws forbid
 * outright, and nothing anywhere said why.
 *
 * That was three hand-maintained copies of one list drifting apart — the
 * "channel list copied into a third place" failure mode this codebase has
 * already been bitten by more than once. The fix is NOT a fourth copy: it
 * is one source with three consumers, the same discipline the OpenClaw
 * channel manifest uses (derive from the thing that owns it, never
 * transcribe) and that RAIL_ITEMS uses to drive the keyboard-shortcuts
 * page.
 *
 * THE COMPILER IS THE REAL GUARD, not just the sibling test. FleetAgentDetail's
 * `TABS` is typed `{ id: TabId; ... }[]` where `TabId` is this module's
 * `AgentDetailTabId` — so adding a row to TABS with an id that is not in
 * AGENT_DETAIL_TAB_IDS below is a TYPE ERROR, not a silent unroutable tab.
 * A new tab therefore cannot be offered without first being made routable,
 * which is exactly the sequence that failed here. The test beside this file
 * covers what the compiler can't see: that neither route file has grown its
 * own private list again.
 *
 * Kept as its own pure module (no React, no lucide-react) rather than
 * exported from FleetAgentDetail.tsx, so the two route modules can import
 * the vocabulary without pulling a ~5k-line component and its icon set into
 * their bundle — the same reason agent-profile-shape.ts sits beside it
 * instead of inside it.
 */

/**
 * Every `[tab]` segment the agent detail routes accept, in the order
 * FleetAgentDetail's own TABS declares them, with the two exceptions below
 * last.
 *
 * This is the ROUTABLE vocabulary, which is deliberately WIDER than the set
 * of tabs a person is ever shown:
 *
 *   - `work` is routable but has no label/icon and is absent from TABS. It
 *     is a retired surface kept alive as an alias — `activeTab === "work"`
 *     renders the identical observation view as "chat", so an old bookmark
 *     still works instead of 404ing (the same "dead-but-live" treatment
 *     CLAUDE.md documents for /agents and /conversations). Removing it here
 *     would break those links, not tidy them.
 *
 * What is NOT here is equally deliberate: `overview` (deleted outright,
 * founder 2026-08-13) and `tools` (deleted with the tool-authority tier,
 * 2026-08-21) are absent on purpose. Neither is a dead page — an
 * unrecognized tab is coerced to "chat", the agent's front door, by each
 * route's own fallback, so a stale bookmark to either lands somewhere real
 * rather than on a blank pane. Do not "restore" them here.
 */
export const AGENT_DETAIL_TAB_IDS = [
  "chat",
  "persona",
  "general",
  "model",
  "skills",
  "channels",
  "connectors",
  "context",
  "capabilities",
  "hardware",
  "memory",
  "work",
] as const;

export type AgentDetailTabId = (typeof AGENT_DETAIL_TAB_IDS)[number];

const AGENT_DETAIL_TAB_ID_SET: ReadonlySet<string> = new Set(AGENT_DETAIL_TAB_IDS);

/**
 * Whether a raw `[tab]` URL segment is one this surface can actually
 * render. Both route files call THIS instead of testing a local array —
 * that call site is the whole point of the module.
 *
 * A `false` answer is never an error: each route coerces it to "chat", so a
 * typo'd or retired tab lands on the agent's front door.
 */
export function isAgentDetailTab(value: unknown): value is AgentDetailTabId {
  return typeof value === "string" && AGENT_DETAIL_TAB_ID_SET.has(value);
}

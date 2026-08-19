/**
 * The agent PROFILE surface — Telegram's "tap the name in a chat header to
 * see who you're talking to" pattern, applied to an agent (founder,
 * 2026-08-19): "on top there is a profile of this specific chat... a
 * specific prompt like system prompt... media and other files also going to
 * be there — memory files is going to be just alongside media and others."
 *
 * The one correction that must not be lost: Telegram's profile describes a
 * STATIC entity someone else made, and its tabs are MEDIA TYPES (Media /
 * Files / Links / Music). An agent is something the OWNER configures, so
 * its profile is an editing surface and its segments are not media types —
 * they're Persona (the system prompt, editable) and Memory & Files (what it
 * knows, together, per the founder's own instruction to put them
 * side by side). Copying Telegram's tab NAMES would be imitating the
 * surface of the surface rather than the gesture it exists for.
 *
 * PROFILE_TAB_IDS mirrors CONFIGURE_TAB_IDS's own role in
 * FleetAgentDetail.tsx (a set of [tab] route segments that render inside a
 * sheet instead of as a top-level tab) — kept in its own pure module rather
 * than inlined so a test can assert the membership directly instead of
 * re-deriving it, the same discipline project-agents-rail-shape.ts already
 * applies one surface over.
 */
export type AgentProfileSegmentId = "persona" | "memory";

export const PROFILE_TAB_IDS: ReadonlySet<AgentProfileSegmentId> = new Set(["persona", "memory"]);

export function isProfileTab(tabId: string): tabId is AgentProfileSegmentId {
  return (PROFILE_TAB_IDS as ReadonlySet<string>).has(tabId);
}

/**
 * Which segments the Profile sheet actually shows, and in what order.
 *
 * Persona is a per-agent customization — the workspace master (Sage/the
 * Operator) has no "instructions" field a person edits here (same
 * `!isMaster` guard PersonaEditor's own caller, GeneralTab, already used
 * before this pass; unchanged, just re-homed). Rendering a Persona editor
 * for Sage would be a dead control: nothing it saves is ever read (see
 * specialist_runtime_context.py's own resolve_specialist_runtime_context,
 * which returns None — "run as the workspace master unchanged" — before
 * persona resolution ever runs for that install). So Sage's profile is
 * Memory-only, no segmented picker to show since there is only one thing to
 * pick.
 */
export function planAgentProfileSegments(isMaster: boolean): AgentProfileSegmentId[] {
  return isMaster ? ["memory"] : ["persona", "memory"];
}

/** The first (default) segment a fresh Profile open should land on. */
export function defaultAgentProfileSegment(isMaster: boolean): AgentProfileSegmentId {
  return planAgentProfileSegments(isMaster)[0];
}

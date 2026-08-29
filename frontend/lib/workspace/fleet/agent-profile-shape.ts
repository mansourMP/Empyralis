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
 * ── SKILLS MOVED HERE FROM CONFIGURE ▸ BRAIN, 2026-08-28 ────────────────
 * The founder went looking for Skills and could not find it. It had been
 * grouped with Model and Capabilities under "Brain", which is described in
 * FleetAgentDetail's own comment as "what it thinks with" — and a skill is
 * not that. `SkillsTab` is a name, a description and a body of instructions,
 * delivered to the engine as a real SKILL.md: authored prose about what this
 * agent knows how to do. That is the same KIND of thing as the two segments
 * already here, and the three of them finally read as one question:
 *
 * ```
 *   Persona          who it is          the system prompt
 *   Skills           what it knows how to do   procedures it follows
 *   Memory & Files   what it knows      notes, facts, files
 * ```
 *
 * Configure keeps the set-once TECHNICAL configuration it was described as
 * holding — Model, Capabilities, Channels, Apps, Context, Hardware. No new
 * group was invented to hold Skills, because none of Brain / Reach ("how
 * it's reached, and what it can reach out to") / Compute ("what it runs on")
 * fits it, and a fourth group holding one item is a surface that has not
 * earned its place. This is also the more FINDABLE home: the Profile opens
 * by tapping the agent's own name in the header, rather than sitting three
 * levels down a "⋯" menu.
 *
 * The MASTER keeps Skills, and that asymmetry with Persona is deliberate
 * rather than an oversight: Persona is excluded for the master because
 * nothing ever reads what it saves (specialist_runtime_context returns None
 * for that install), which would make it a dead control. Skills had no such
 * guard in Configure and rendered for every agent — so keeping it for the
 * master is preserving the behaviour that shipped, not extending it.
 *
 * PROFILE_TAB_IDS mirrors CONFIGURE_TAB_IDS's own role in
 * FleetAgentDetail.tsx (a set of [tab] route segments that render inside a
 * sheet instead of as a top-level tab) — kept in its own pure module rather
 * than inlined so a test can assert the membership directly instead of
 * re-deriving it, the same discipline primary-rail-nav.ts already applies
 * one surface over.
 */
export type AgentProfileSegmentId = "persona" | "skills" | "memory";

export const PROFILE_TAB_IDS: ReadonlySet<AgentProfileSegmentId> = new Set([
  "persona",
  "skills",
  "memory",
]);

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
  return isMaster ? ["skills", "memory"] : ["persona", "skills", "memory"];
}

/** The first (default) segment a fresh Profile open should land on. */
export function defaultAgentProfileSegment(isMaster: boolean): AgentProfileSegmentId {
  return planAgentProfileSegments(isMaster)[0];
}

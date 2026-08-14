/**
 * The primary rail has exactly two modes — not one rail with a project's
 * sections nested underneath it. Founder's own framing (2026-08-14, verified
 * against his live screenshot before this was built): "opening a specific
 * project switches the rail ENTIRELY — Inbox and Projects disappear, that
 * project's agent names take over the rail, and a Back control at the top
 * returns to Inbox + Projects."
 *
 * Before this: PrimaryRail.tsx kept Inbox and Projects always on screen and
 * nested the OPEN project's Tasks/Documents/Agents SECTIONS underneath its
 * own row (see the PROJECT_SECTIONS comment there, dated 2026-08-13 — a real,
 * deliberate design that this supersedes rather than a bug). The founder
 * looked at that shipped nesting and asked for something else: not sections,
 * AGENT NAMES, and not nested, a full swap.
 *
 * Pulled into its own pure, dependency-light module (no next/navigation, no
 * React) for the same reason primary-rail-nav.ts and
 * project-agents-rail-shape.ts already are — a plain test can import the
 * REAL boundary-detection function PrimaryRail.tsx renders against, instead
 * of re-typing the regex and risking the test and the component drifting
 * apart on what counts as "inside a project."
 */

/** The active project id from the current pathname, or null when the rail
 *  should be in its normal (Inbox + Projects) mode. Matches ANY route under
 *  `/w/{workspaceId}/projects/{projectId}/...` — the project's own bare
 *  index, its Tasks/Documents/Agents section, and every agent's page beneath
 *  it all count as "inside a project" for the rail's purposes. Only the bare
 *  `/w/{workspaceId}/projects` list (no id segment) reads as normal mode. */
export function activeProjectIdFromPathname(pathname: string): string | null {
  const match = pathname.match(/\/projects\/([^/]+)/);
  if (!match) return null;
  const id = decodeURIComponent(match[1]);
  return id ? id : null;
}

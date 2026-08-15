/**
 * "AM I INSIDE A PROJECT?" — one question, one answer, used to mark the
 * current row in the rail's flat project list.
 *
 * THE RAIL NO LONGER HAS MODES, and this module's name is now historical.
 * It was written on 2026-08-14 for a two-mode rail (opening a project
 * swapped Inbox and Projects out for that project's agent names, with a Back
 * control at the top), which itself superseded a 2026-08-13 rail that nested
 * the open project's Tasks/Documents/Agents sections under its row. Both are
 * gone: the rail is FLAT (2026-08-15, founder-approved — "the rail is places
 * you go, the page is things you do"), so opening a project changes the PAGE
 * and the only thing the rail does with this answer is highlight which
 * project row is current. See primary-rail-nav.ts's header for the full
 * shape. The function's CONTRACT is unchanged, which is why it survived the
 * rewrite intact rather than being reimplemented inline.
 *
 * Pulled into its own pure, dependency-light module (no next/navigation, no
 * React) for the same reason primary-rail-nav.ts, my-work.ts and
 * project-agents-rail-shape.ts already are — a plain test can import the
 * REAL boundary-detection function PrimaryRail.tsx renders against, instead
 * of re-typing the regex and risking the test and the component drifting
 * apart on what counts as "inside a project."
 */

/** The active project id from the current pathname, or null when the reader
 *  is not inside a project at all. Matches ANY route under
 *  `/w/{workspaceId}/projects/{projectId}/...` — the project's own bare
 *  index, each of its Tasks/Documents/Agents/People tabs, and every agent's
 *  page beneath it all count as "inside a project". Only the bare
 *  `/w/{workspaceId}/projects` list (no id segment) reads as outside one. */
export function activeProjectIdFromPathname(pathname: string): string | null {
  const match = pathname.match(/\/projects\/([^/]+)/);
  if (!match) return null;
  const id = decodeURIComponent(match[1]);
  return id ? id : null;
}

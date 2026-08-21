import { Building2, Keyboard, Plug, UserCog, type LucideIcon } from "lucide-react";

import { DEFAULT_SETTINGS_SECTION, SETTINGS_SECTIONS, isSettingsSection, type SettingsSection } from "./settings-sections";

/**
 * RAIL SPACES — the founder's rule, 2026-08-16, verbatim intent: "the rail
 * is where you pick; the content is what you picked." A list of navigation
 * choices rendered inside the content area is a second rail pretending to be
 * content, and it is wrong.
 *
 * So the primary rail has exactly two shapes:
 *
 *   DEFAULT   the flat workspace rail (primary-rail-nav.ts — Inbox, My work,
 *             Projects, Agents, Context). Unchanged; still the 2026-08-15
 *             shape.
 *   SPACE     inside a surface that has its own pick-list, the rail shows
 *             THAT list instead: a "‹ Back" row on top (a real link), then
 *             the space's own destinations. The content pane holds only the
 *             thing that was picked.
 *
 * This refines — does not reverse — the 2026-08-15 "flat rail" decision.
 * What that decision rejected was the rail changing when you merely OPEN a
 * project (the page's own Tasks/Documents tabs are the page's job). What
 * 2026-08-16 added is: when a surface's whole content used to BE a second
 * nav column — Settings' in-content section sidebar — that column belongs
 * in the rail, because it is a picker.
 *
 * THERE IS EXACTLY ONE SPACE, `settings`, AND THAT IS THE WHOLE UNION.
 *
 *   settings   /w/{id}/settings and beneath. Back → where the person came
 *              from (workspace root as fallback).
 *
 * ── TWO SPACES HAVE BEEN DELETED FROM THIS FILE. Neither is an oversight,
 *    and neither may come back without the founder reversing himself. ──
 *
 * `workspace-agents` lived here 2026-08-19 → 2026-08-20. Pressing the
 * top-level Agents rail row morphed the WHOLE primary rail into a bare list
 * of agent names — Inbox/My work/Projects all disappeared while browsing
 * agents. The founder tried it and rejected it the same session it shipped:
 * "we have to find a way, some better way to show this agent — not on this
 * left rail but something else... I really don't like the design of it right
 * now. But at the same time I should be able to see my conversations." The
 * picker moved OFF the primary rail and INTO the content area instead — a
 * genuine second pane beside the chat (AgentConversationList.tsx, wired by
 * app/(account)/w/[workspaceId]/agents/layout.tsx), the same master-detail
 * shape this codebase already uses for Inbox/Work/Memory. That did NOT
 * reopen the "rail is where you pick" question: the rule was never "the rail
 * is the ONLY place a picker may live" — it was "only ONE picker at a time."
 *
 * `project-agents` lived here 2026-08-16 → 2026-08-21, and it is deleted for
 * a bigger reason than layout: IT ENCODED A DATA MODEL THE FOUNDER HAS
 * SUPERSEDED. It matched /w/{ws}/projects/{pid}/agents and replaced the rail
 * with "‹ Back / {project} / one row per agent of THAT project" — i.e. it
 * asserted that an agent belongs to a project and is browsed through it. On
 * 2026-08-20 he settled the opposite, and called it core: an agent belongs
 * to the WORKSPACE, and its reach into projects is a per-agent GRANT, never
 * a location ("an agent belongs to the workspace not to the project it's
 * correct and I want you to remember that"). His reasoning against exactly
 * this navigation shape, in the same conversation: "it's fundamentally
 * wrong... what we are building is not kind of like Telegram surface."
 *
 * A project-scoped agent picker cannot be made correct under that model —
 * there is no longer any such thing as "this project's agents" to pick from
 * — so the space is gone rather than restyled. The ROUTE it used to decorate
 * (/w/{ws}/projects/{pid}/agents and the agent pages beneath it) is
 * deliberately still live and simply unlinked, the same treatment CLAUDE.md
 * records for /people and /conversations: deleting a route strands bookmarks,
 * and turning one into a redirect target risks the "a redirect runs ahead of
 * the router" trap that file already documents costing this product its own
 * front door for months.
 *
 * primary-rail-space.test.ts asserts the deletion structurally (both spaces'
 * pathnames now parse as null; PrimaryRail's own source contains no
 * project-agents pick-list) rather than merely dropping the old assertions,
 * so nobody re-adds either space believing it was forgotten.
 *
 * Pure and dependency-light (no React, no next/navigation) for the same
 * reason primary-rail-nav.ts is: a plain `tsx` test imports the REAL
 * derivation PrimaryRail.tsx renders against, instead of re-typing it.
 */

export type RailSpace = {
  kind: "settings";
  workspaceId: string;
  /** The section the URL names, or null when it names none (the bare
   *  /settings redirect moment) or names one that does not exist. */
  activeSection: SettingsSection | null;
};

/** One row of a space's pick-list. Every row is a REAL link — cmd-click and
 *  middle-click must work — and exactly one is active at a time, marked the
 *  same way the rail already marks its active row. */
export type RailSpaceLink = {
  key: string;
  label: string;
  href: string;
  active: boolean;
  icon?: LucideIcon;
};

// Matches /w/{workspaceId}/settings and anything beneath it. The workspace
// segment arrives URL-encoded (pathnames always do); it is decoded here once
// and re-encoded by every href builder below, so a workspace id with unsafe
// characters round-trips instead of double-encoding.
const SETTINGS_SPACE_RE = /^\/w\/([^/]+)\/settings(?:\/([^/]+))?(?:\/|$)/;

/** Which space — if any — the current pathname is inside. Null means the
 *  rail keeps its default flat shape, which is now every pathname in the
 *  product except Settings (see this file's header for the two spaces that
 *  used to also match here and why neither does any more). */
export function railSpaceFromPathname(pathname: string): RailSpace | null {
  const settings = pathname.match(SETTINGS_SPACE_RE);
  if (settings) {
    const rawSection = settings[2] ? decodeURIComponent(settings[2]) : null;
    return {
      kind: "settings",
      workspaceId: decodeURIComponent(settings[1]),
      activeSection: rawSection && isSettingsSection(rawSection) ? rawSection : null,
    };
  }
  return null;
}

function workspaceRoot(workspaceId: string): string {
  return `/w/${encodeURIComponent(workspaceId)}`;
}

// Labels/icons for the four Settings destinations. These moved here from
// SettingsShell.tsx's own SECTION_LABEL/SECTION_ICON when the in-content
// sidebar was deleted — the rail is the one place the pick-list renders now,
// so this is the one place its vocabulary lives.
const SETTINGS_SECTION_LABEL: Record<SettingsSection, string> = {
  account: "Account",
  workspace: "Workspace",
  connections: "Connections",
  shortcuts: "Keyboard shortcuts",
};

const SETTINGS_SECTION_ICON: Record<SettingsSection, LucideIcon> = {
  account: UserCog,
  workspace: Building2,
  connections: Plug,
  shortcuts: Keyboard,
};

/** The Settings space's pick-list, derived from the same SETTINGS_SECTIONS
 *  the routes themselves validate against — never a second hand-kept list.
 *  A bare-/settings moment (activeSection null) marks the DEFAULT section
 *  active, because that is where the redirect is about to land; anything
 *  else would flash a list with no current row. */
export function settingsSpaceLinks(space: RailSpace): RailSpaceLink[] {
  const base = `${workspaceRoot(space.workspaceId)}/settings`;
  const active = space.activeSection ?? DEFAULT_SETTINGS_SECTION;
  return SETTINGS_SECTIONS.map((id) => ({
    key: id,
    label: SETTINGS_SECTION_LABEL[id],
    href: `${base}/${id}`,
    active: id === active,
    icon: SETTINGS_SECTION_ICON[id],
  }));
}

/**
 * Where the "‹ Back" row points. It is a real link, so this must resolve to
 * a concrete href at render time — never a router.back() that could walk
 * into another workspace or out of the app entirely.
 *
 * `cameFrom` is the last pathname the rail saw OUTSIDE any space (tracked by
 * PrimaryRail across its own persistent life). It is honoured only when it
 * belongs to this same workspace and is not itself inside a space; anything
 * else — a direct load, a bookmark, a stale path from another workspace —
 * falls back to the workspace root.
 */
export function spaceBackHref(space: RailSpace, cameFrom: string | null | undefined): string {
  const root = workspaceRoot(space.workspaceId);
  if (
    cameFrom &&
    (cameFrom === root || cameFrom.startsWith(`${root}/`)) &&
    railSpaceFromPathname(cameFrom) === null
  ) {
    return cameFrom;
  }
  return root;
}

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
 * project (the page's own Tasks/Documents/Agents tabs are the page's job).
 * What 2026-08-16 added is: when a surface's whole content used to BE
 * a second nav column — Settings' in-content section sidebar, a project's
 * own Agents section's in-content agent list — that column belongs in the
 * rail, because it is a picker.
 *
 * Two spaces exist:
 *   settings          /w/{id}/settings and beneath. Back → where the person
 *                     came from (workspace root as fallback).
 *   project-agents    /w/{id}/projects/{pid}/agents and beneath. Back → the
 *                     project. Whether the rail actually morphs there is
 *                     ADDITIONALLY gated by the agent count, through
 *                     project-agents-rail-shape.ts's showsProjectAgentsRail —
 *                     the same 0/1/2+ rule as always, never a second one.
 *                     This module only answers "is this pathname that space";
 *                     the caller composes the count gate.
 *
 * CORRECTION, 2026-08-20 — a THIRD space, `workspace-agents`, briefly lived
 * here (2026-08-19 through 2026-08-20). Pressing the top-level Agents rail
 * row morphed the WHOLE primary rail into a bare list of agent names —
 * Inbox/My work/Projects all disappeared while browsing agents. The
 * founder tried it and rejected it the same session it shipped: "we have
 * to find a way, some better way to show this agent — not on this left
 * rail but something else... I really don't like the design of it right
 * now. But at the same time I should be able to see my conversations."
 *
 * The picker moved OFF the primary rail and INTO the content area instead
 * — a genuine second pane beside the chat (AgentConversationList.tsx,
 * wired by app/(account)/w/[workspaceId]/agents/layout.tsx), the same
 * master-detail shape this codebase already uses for Inbox/Work/Memory
 * (`.fleet-inbox-list` / `.fleet-work-list`, "a hairline-divided list,
 * never cards... per the Linear reference"). This does NOT reopen the
 * "rail is where you pick" question: that rule was never "the rail is the
 * ONLY place a picker may live" — it was "only ONE picker at a time." The
 * primary rail stops being an agent picker entirely (Agents is now a
 * normal flat row, exactly like Projects); the content-area pane is the
 * only surface answering "which agent," never two at once. See
 * agents-conversation-list.ts for the rest of the reasoning and the
 * pure sort/filter/preview logic the new pane runs on.
 *
 * Pure and dependency-light (no React, no next/navigation) for the same
 * reason primary-rail-nav.ts and project-agents-rail-shape.ts are: a plain
 * `tsx` test imports the REAL derivation PrimaryRail.tsx renders against
 * (primary-rail-space.test.ts), instead of re-typing it.
 */

export type RailSpace =
  | {
      kind: "settings";
      workspaceId: string;
      /** The section the URL names, or null when it names none (the bare
       *  /settings redirect moment) or names one that does not exist. */
      activeSection: SettingsSection | null;
    }
  | {
      kind: "project-agents";
      workspaceId: string;
      projectId: string;
      /** The agent whose page is open, or null at the bare /agents index. */
      activeAgentId: string | null;
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

// Matches /w/{ws}/projects/{pid}/agents, and the agent pages beneath it —
// the same "…/agents/{agentId}/…" id read ProjectDetailPage's own solo
// redirect builds hrefs for. The workspace-level /w/{ws}/agents tree is
// DELIBERATELY not matched by anything in this module any more (see this
// file's own 2026-08-20 correction above) — it is a normal routed page
// now, not a rail space.
const PROJECT_AGENTS_SPACE_RE = /^\/w\/([^/]+)\/projects\/([^/]+)\/agents(?:\/([^/]+))?(?:\/|$)/;

/** Which space — if any — the current pathname is inside. Null means the
 *  rail keeps its default flat shape. */
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
  const projectAgents = pathname.match(PROJECT_AGENTS_SPACE_RE);
  if (projectAgents) {
    return {
      kind: "project-agents",
      workspaceId: decodeURIComponent(projectAgents[1]),
      projectId: decodeURIComponent(projectAgents[2]),
      activeAgentId: projectAgents[3] ? decodeURIComponent(projectAgents[3]) : null,
    };
  }
  return null;
}

function workspaceRoot(workspaceId: string): string {
  return `/w/${encodeURIComponent(workspaceId)}`;
}

// Labels/icons for the three Settings destinations. These moved here from
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
export function settingsSpaceLinks(space: Extract<RailSpace, { kind: "settings" }>): RailSpaceLink[] {
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

/** The project-agents space's pick-list — one row per agent of THIS project,
 *  in the caller's given order, each linking straight into that agent's
 *  chat (the same href ProjectDetailPage's own solo redirect uses). Rows
 *  carry no icon here: the component renders each agent's own sigil and
 *  status, which a LucideIcon field cannot express. */
export function projectAgentsSpaceLinks(
  space: Extract<RailSpace, { kind: "project-agents" }>,
  agents: readonly { agent_id: string; label?: string | null }[],
): RailSpaceLink[] {
  const base = `${workspaceRoot(space.workspaceId)}/projects/${encodeURIComponent(space.projectId)}/agents`;
  return agents.map((agent) => ({
    key: agent.agent_id,
    label: agent.label || "Unnamed agent",
    href: `${base}/${encodeURIComponent(agent.agent_id)}/chat`,
    active: agent.agent_id === space.activeAgentId,
  }));
}

/**
 * Where the "‹ Back" row points. It is a real link, so this must resolve to
 * a concrete href at render time — never a router.back() that could walk
 * into another workspace or out of the app entirely.
 *
 * settings — `cameFrom` is the last pathname the rail saw OUTSIDE any space
 * (tracked by PrimaryRail across its own persistent life). It is honoured
 * only when it belongs to this same workspace and is not itself inside a
 * space; anything else — a direct load, a bookmark, a stale path from
 * another workspace — falls back to the workspace root.
 *
 * project-agents — always the project itself, never `cameFrom`: an agent
 * belongs to its project, so leaving its space lands on the project that
 * owns it (founder's own back list — "inbox / my work / projects" — is one
 * more hop from there, in the flat rail this returns to).
 */
export function spaceBackHref(space: RailSpace, cameFrom: string | null | undefined): string {
  const root = workspaceRoot(space.workspaceId);
  if (space.kind === "project-agents") {
    return `${root}/projects/${encodeURIComponent(space.projectId)}`;
  }
  if (
    cameFrom &&
    (cameFrom === root || cameFrom.startsWith(`${root}/`)) &&
    railSpaceFromPathname(cameFrom) === null
  ) {
    return cameFrom;
  }
  return root;
}

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
 *             Projects, Settings). Unchanged; still the 2026-08-15 shape.
 *   SPACE     inside a surface that has its own pick-list, the rail shows
 *             THAT list instead: a "‹ Back" row on top (a real link), then
 *             the space's own destinations. The content pane holds only the
 *             thing that was picked.
 *
 * This refines — does not reverse — the 2026-08-15 "flat rail" decision.
 * What that decision rejected was the rail changing when you merely OPEN a
 * project (the page's own Tasks/Documents/Agents tabs are the page's job).
 * What tonight's decision adds is: when a surface's whole content used to BE
 * a second nav column — Settings' in-content section sidebar, the project
 * Agents section's in-content agent list — that column belongs in the rail,
 * because it is a picker.
 *
 * Three spaces exist:
 *   settings          /w/{id}/settings and beneath. Back → where the person
 *                     came from (workspace root as fallback).
 *   project-agents    /w/{id}/projects/{pid}/agents and beneath. Back → the
 *                     project. Whether the rail actually morphs there is
 *                     ADDITIONALLY gated by the agent count, through
 *                     project-agents-rail-shape.ts's showsProjectAgentsRail —
 *                     the same 0/1/2+ rule as always, never a second one.
 *                     This module only answers "is this pathname that space";
 *                     the caller composes the count gate.
 *   workspace-agents  /w/{id}/agents (the bare index only — real agent
 *                     pages live under a project's own URL, see below).
 *                     Back → where the person came from, same as settings.
 *                     Added 2026-08-19: the founder moved agents back onto
 *                     the rail — "agents should be just open, not inside
 *                     this specific project... move all those agents under
 *                     one button that would say Agents; the moment I press
 *                     Agents [they] appear on this left rail" — after
 *                     watching Grok's app. Gated the same way as
 *                     project-agents, through workspace-agents-rail-shape.ts's
 *                     showsWorkspaceAgentsRail, except the count is
 *                     WORKSPACE-WIDE (every real agent, across every
 *                     project) rather than one project's own.
 *
 *                     This is deliberately a THIRD, independent kind, not a
 *                     rename or a widening of project-agents: a project's
 *                     own Agents tab still exists and still opens the
 *                     project-agents space, scoped to that project, exactly
 *                     as it always has (CLAUDE.md, "adding a third... reuse
 *                     the mechanism, do not invent a second one" — reuse
 *                     means the SHAPE, not a merge of the two). An agent's
 *                     own chat page still lives at its existing URL
 *                     (…/projects/{pid}/agents/{agentId}/chat — reused
 *                     unchanged, see projectAgentsSpaceLinks), so clicking a
 *                     row in the workspace-agents pick-list navigates OUT of
 *                     this space and, if that agent's own project happens to
 *                     also carry 2+ agents, INTO the project-agents space —
 *                     the same quiet hand-off that already happens today
 *                     when a reader clicks from a project's Agents tab into
 *                     one of many siblings. Nothing under
 *                     /projects/{pid}/agents/… was repointed at this new
 *                     kind: doing that would have changed what
 *                     project-agents already matches, which
 *                     primary-rail-space.test.ts pins byte-for-byte.
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
    }
  | {
      kind: "workspace-agents";
      workspaceId: string;
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
// redirect builds hrefs for.
const PROJECT_AGENTS_SPACE_RE = /^\/w\/([^/]+)\/projects\/([^/]+)\/agents(?:\/([^/]+))?(?:\/|$)/;

// Matches the bare workspace-level /w/{ws}/agents index only — NOT
// /projects/{pid}/agents/…, which PROJECT_AGENTS_SPACE_RE already owns (see
// this module's header for why the two are kept disjoint). An agent's own
// chat page is never reached under this prefix, so there is no third id
// segment to read here the way project-agents reads activeAgentId.
const WORKSPACE_AGENTS_SPACE_RE = /^\/w\/([^/]+)\/agents(?:\/|$)/;

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
  const workspaceAgents = pathname.match(WORKSPACE_AGENTS_SPACE_RE);
  if (workspaceAgents) {
    return {
      kind: "workspace-agents",
      workspaceId: decodeURIComponent(workspaceAgents[1]),
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

/** The workspace-agents space's pick-list — one row per real agent in the
 *  WHOLE workspace, across every project, in the caller's given order, each
 *  linking straight into that agent's existing chat URL. Callers resolve
 *  each agent's project id BEFORE calling this (fleet-data.ts's
 *  resolveAgentProjectId, the same fallback an agent's own project_id
 *  already goes through everywhere else it's linked) — this module stays
 *  free of that dependency, same reason it takes plain agent records rather
 *  than importing fleet-data.ts. No row is ever marked active: this space
 *  is only ever entered at the bare /agents index, before any agent has
 *  been picked (see WORKSPACE_AGENTS_SPACE_RE's own comment — the moment a
 *  row is clicked, the pathname leaves this space's territory entirely).
 *  Rows carry no icon, same reason projectAgentsSpaceLinks' don't: the
 *  component renders each agent's own sigil and status. */
export function workspaceAgentsSpaceLinks(
  space: Extract<RailSpace, { kind: "workspace-agents" }>,
  agents: readonly { agent_id: string; label?: string | null; project_id: string }[],
): RailSpaceLink[] {
  const base = workspaceRoot(space.workspaceId);
  return agents.map((agent) => ({
    key: agent.agent_id,
    label: agent.label || "Unnamed agent",
    href: `${base}/projects/${encodeURIComponent(agent.project_id)}/agents/${encodeURIComponent(agent.agent_id)}/chat`,
    active: false,
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
 *
 * workspace-agents — same `cameFrom` rule as settings, not the project-agents
 * rule: agents are workspace-wide here by the founder's own instruction, so
 * there is no single owning project to fall back to.
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

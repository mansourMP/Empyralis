/**
 * Zero-decision agent creation — the founder's own Telegram framing:
 * *"if I simply go to Telegram, it's just so fucking perfect... you don't
 * configure a bot in order to talk to it."* Landed 2026-08-19, replacing
 * FleetCreateAgentWizard's 4-step modal (Placement -> Brain -> Channels ->
 * Connections) as the "New agent" entry point everywhere in the app —
 * never added ALONGSIDE the wizard as a second, faster path, per the
 * founder's own correction mid-task: *"simplify the platform in a way
 * where Steve Jobs would"* means removing the ceremony, not offering a
 * shortcut around it while the ceremony stays live for someone else to hit.
 *
 * Reference calibration, per the founder's explicit instruction to look at
 * a real product rather than invent a shape: claude.ai's own "New chat"
 * costs zero decisions — no name, no project, no model picker up front,
 * all of it inferred or defaulted and changed later from a control that
 * already exists. This module is that shape applied to an Empyralis agent.
 *
 * Verified before cutting anything (CLAUDE.md's own standing rule: don't
 * remove a field until you've confirmed it has somewhere left to go) that
 * every single thing the old wizard asked for at creation time already has
 * a real, independently-working home in the agent's own Configure sheet
 * (FleetAgentDetail.tsx's CONFIGURE_GROUPS — Brain: General/Model, Reach:
 * Channels/Connectors, Compute: Hardware) — none of those tabs were built
 * BY the wizard; they already exist and already work without it. So this
 * cut removes a redundant front door, not a capability.
 *
 * What is silently defaulted, and why each default is safe to never ask:
 *
 *   name       -- fleet_create_agent's own server-side fallback when the
 *                 posted name is blank (a curated pool, collision-checked
 *                 per workspace) — already exercised by every wizard
 *                 session that accepted its pre-filled suggestion rather
 *                 than typing over it. Renamed anytime from Configure >
 *                 General (AgentTitle).
 *   project     -- resolveAgentProjectId's EXISTING default-project logic
 *                 (fleet-data.ts): the project this was opened from, else
 *                 the workspace's own is_default project. The identical
 *                 resolution the wizard already used — this module only
 *                 removes the screen that made a person confirm it, never
 *                 the logic that picks it. Project reassignment after
 *                 creation is deliberately NOT built: CLAUDE.md's "an
 *                 agent belongs to its project and works only there" law
 *                 makes that a non-feature by design, the same posture
 *                 documents took for "move to another project" (flagged,
 *                 not shipped, for the identical reason — a project is a
 *                 collaboration boundary, not a filing cabinet).
 *   capability  -- "standard", the only non-reserved creatable preset.
 *   placement   -- left UNSET on the create call entirely.
 *                 fleet_create_agent's own capability-preset defaults
 *                 already resolve hardware_access to "none" (cloud-only)
 *                 for "standard" — there is nothing to PATCH after create
 *                 for the common case (the old wizard's separate
 *                 hardware_access PATCH was a no-op whenever Placement's
 *                 own default, "Cloud only", was accepted). Configure >
 *                 Hardware covers every other placement afterward.
 *   purpose /   -- "internal_assistant" / "owner", the codebase's own
 *   audience       existing safe default (sage_command_dispatcher.py's own
 *                 documented reasoning: "the safer, less-blaming
 *                 assumption"). Flagged, not quietly accepted: grepping
 *                 this frontend tree found ZERO post-creation controls
 *                 that ever write purpose_preset/audience — the wizard was
 *                 the only writer — so a person who later wants a
 *                 customer-facing agent has no UI path back from this
 *                 default today. That gap belongs in Configure > General
 *                 (FleetAgentDetail.tsx), which a concurrent change owns;
 *                 reported rather than worked around with a second
 *                 creation surface here.
 *   model       -- whatever capability_presets seeds by default for
 *                 "standard" — editable immediately from Configure >
 *                 Model, same as before this change.
 *   channels /  -- untouched at creation; both tabs are fully functional
 *   connectors     the moment the agent exists (Configure > Channels /
 *                 Connectors — the same components the wizard's own
 *                 Channels/Connections steps reused, not owned by it).
 */

import { buildCookieAuthHeaders } from "@/lib/auth/csrf";
import { getErrorMessage } from "@/lib/ui/api-error";
import { fleetAuthorizedFetch } from "@/lib/workspace/fleet/fleet-authorized-fetch";

import type { FleetProject } from "./fleet-data";
import { refreshFleetAgents, resolveAgentProjectId } from "./fleet-data";

export type QuickCreateAgentPayload = {
  name: string;
  capability_preset: string;
  project_id: string;
  purpose_preset: string;
  audience: string;
};

/** The project a zero-decision "New agent" resolves to — current project
 *  when invoked from inside one, else the workspace's own default project.
 *  A thin, deliberately trivial wrapper over fleet-data's
 *  resolveAgentProjectId so this module has exactly ONE way to answer
 *  "which project", the same one the rest of the fleet surface already
 *  uses for a missing project_id — never a second resolver that can drift
 *  from the first. */
export function resolveQuickCreateProjectId(
  currentProjectId: string | undefined,
  projects: FleetProject[],
): string {
  return resolveAgentProjectId(currentProjectId, projects);
}

/** The POST /fleet/agents body for a zero-decision create. A pure function
 *  of the resolved project id alone — everything else is a fixed, safe
 *  default (see this module's header comment for why each one is safe to
 *  omit a screen for). */
export function buildQuickCreateAgentPayload(projectId: string): QuickCreateAgentPayload {
  return {
    name: "",
    capability_preset: "standard",
    project_id: projectId,
    purpose_preset: "internal_assistant",
    audience: "owner",
  };
}

/** Where to land after creation — straight into the new agent's Chat, the
 *  same path FleetCreateAgentWizard's own old finish() used (kept
 *  byte-for-byte so no deep-link shape regresses), including its identical
 *  guard: an unresolvable project segment would otherwise swallow the
 *  agent id in the URL (a blank [projectId] makes "agents" itself get
 *  consumed as that segment, stranding the real agent id — a 404 via the
 *  global not-found page), so an empty project routes to the flat agents
 *  list instead of a link already known to be broken. */
export function quickCreateAgentChatPath(opts: {
  workspaceId: string;
  projectId: string;
  agentId: string;
}): string {
  const base = `/w/${encodeURIComponent(opts.workspaceId)}`;
  const projSeg = (opts.projectId || "").trim();
  const agentSeg = (opts.agentId || "").trim();
  if (!agentSeg) return `${base}/agents`;
  return projSeg
    ? `${base}/projects/${encodeURIComponent(projSeg)}/agents/${encodeURIComponent(agentSeg)}/chat`
    : `${base}/agents`;
}

/** The ONE network call this whole flow needs — POST .../fleet/agents with
 *  buildQuickCreateAgentPayload's fixed body, resolving the project first
 *  via resolveQuickCreateProjectId so every caller (rail, command palette,
 *  workspace home, a project's own empty state) creates through the exact
 *  same path instead of five independently-drifting fetch calls. Not a
 *  hook — every call site here is a one-shot action (a click), never
 *  something a component needs to re-render against. Navigation is the
 *  CALLER's job (quickCreateAgentChatPath gives it the URL), because where
 *  "New agent" is invoked from — and therefore what should happen to the
 *  screen the person was already looking at — differs by caller in a way
 *  this function has no business deciding.
 *
 *  It DOES do one more thing before returning, and it is load-bearing:
 *  awaits refreshFleetAgents. Found live 2026-08-19 — without it, the very
 *  next page (the new agent's own Chat, which every caller here navigates
 *  into) reads the SAME shared, cross-page agents cache that PrimaryRail/
 *  SageLauncher/the command palette already populated before this call ran,
 *  and that cache does not know this agent exists yet (see
 *  refreshFleetAgents's own doc comment for the full mechanism). Its
 *  symptom was silent and easy to misread as a naming bug: the new agent's
 *  header showed "Unnamed agent" / "Not deployed" for up to 30 seconds,
 *  even though the create had fully succeeded (label "Basalt" was already
 *  in Postgres, confirmed via a direct read) and the destination page never
 *  errors — it just renders the fallback text for a `find()` that misses
 *  in stale data. Awaiting the refresh here means every caller gets the fix
 *  for free rather than each needing to remember it. */
export async function createAgentQuickly(
  workspaceId: string,
  currentProjectId: string | undefined,
  projects: FleetProject[],
): Promise<{ agentId: string; projectId: string }> {
  const projectId = resolveQuickCreateProjectId(currentProjectId, projects);
  const res = await fleetAuthorizedFetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/agents`, {
    method: "POST",
    credentials: "include",
    headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
    body: JSON.stringify(buildQuickCreateAgentPayload(projectId)),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data?.ok === false) {
    throw new Error(getErrorMessage(data, `Could not create agent (HTTP ${res.status})`));
  }
  await refreshFleetAgents(workspaceId);
  return {
    agentId: String(data.agent_id || ""),
    // fleet_create_agent's own response is the source of truth for which
    // project the agent actually landed in (it may mint a brand-new one —
    // see this module's header comment — when projectId resolved blank),
    // never the value this function computed before the call.
    projectId: String(data.project_id || projectId || ""),
  };
}

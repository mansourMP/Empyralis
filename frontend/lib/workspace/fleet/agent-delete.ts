/**
 * DELETING AN AGENT — the one rule module, and the one network call.
 *
 * ── Why this file exists ─────────────────────────────────────────────────
 * Delete was fully built and then went UNREACHABLE. The backend never moved
 * (`fleet_tools.fleet_delete_agent`, `DELETE .../fleet/agents/{id}` — both
 * live, both correct); the 2026-08-20 agents-page redesign simply stopped
 * importing `AgentsList.tsx`, which was the only caller, and took the door
 * with it. Agents could be created and never removed. That is this
 * codebase's own most-documented defect ("built, tested, and never wired")
 * pointed at a destructive action, which is the worst direction for it.
 *
 * The fix is a shared module rather than a second delete path: AgentsList
 * had the fetch, the dialog and the Operator guard written inline "so it
 * doesn't collide with other in-flight edits to shared fleet files" (its
 * own comment). Reviving that by copying it into the agent detail header
 * would have made two, which is exactly how the channel list ended up in
 * four places. One rule, one call, two call sites.
 *
 * ── Three outcomes, never two ────────────────────────────────────────────
 * CLAUDE.md's outcome-honesty law, and a delete is where it bites hardest —
 * a row that vanishes on a lie is unrecoverable from the person's side:
 *
 * ```
 * deleted      the server said so. Only then does anything disappear.
 * refused      the server answered, definitively, no. Its own words.
 * unconfirmed  the REQUEST never got an answer (offline, dropped socket,
 *              timeout). The delete may well have committed with only the
 *              response lost — so we go and LOOK before speaking, and say
 *              "couldn't confirm, safe to try again" when looking cannot
 *              settle it either.
 * ```
 *
 * Nothing here is optimistic. The caller is expected to keep the row on
 * screen until `deleted` comes back.
 *
 * ── The shared agents cache is refreshed BEFORE `deleted` is returned ────
 * FOUND LIVE, 2026-08-21, and it is the exact mirror of the bug
 * createAgentQuickly already documents for creation. PrimaryRail, the
 * command palette and every agents route read ONE shared, cross-page agents
 * cache; a caller that navigates to the agents list the instant the DELETE
 * returns arrives while that cache still contains the agent it just
 * removed. At one remaining agent that list REDIRECTS straight into the
 * solo agent (agent-count-shape.ts), so the person was bounced back onto
 * the detail page of the agent they had just deleted — rendering "Unnamed
 * agent · Not deployed", which reads exactly like a delete that half-worked.
 * Observed on a real stack before the fix, gone after it.
 *
 * Awaiting the refresh here means every caller gets the fix rather than each
 * needing to remember it — the same argument, and the same placement,
 * createAgentQuickly makes for its own refresh.
 */

import { buildCookieAuthHeaders } from "@/lib/auth/csrf";
import { getErrorMessage } from "@/lib/ui/api-error";
import { fleetAuthorizedFetch } from "@/lib/workspace/fleet/fleet-authorized-fetch";

import { refreshFleetAgents } from "./fleet-data";

export type AgentDeleteOutcome =
  | { status: "deleted" }
  | { status: "refused"; message: string }
  | { status: "unconfirmed"; message: string };

/**
 * The workspace's operator (Sage) can never be deleted — `fleet_delete_agent`
 * refuses it outright ("Cannot delete the workspace's operator agent"),
 * because a workspace with no operator is stranded and because that install
 * id resolves to the WORKSPACE memory root rather than an agent's own (see
 * that function's docstring). So the control is not RENDERED for it: a menu
 * item whose only possible outcome is an error is a dead control, which this
 * codebase's product law forbids.
 *
 * KEYED ON `agent_kind`, NEVER ON `role`, and that is not a stylistic
 * preference — it is a bug found by looking at real data. The guard this was
 * lifted from (AgentsList.tsx) compared `agent.role === "operator"`. Measured
 * live against a real workspace on 2026-08-21, the operator install comes
 * back as:
 *
 * ```
 *   { label: "Sage", role: "specialist", agent_kind: "master" }
 *                    ^^^^^^^^^^^^^^^^^^  ^^^^^^^^^^^^^^^^^^^^
 *                    the old guard's key  what the server guards on
 * ```
 *
 * so that check matched NOTHING and would have offered Delete on the one
 * agent the server always refuses — the dead control this guard exists to
 * prevent, shipped inside the guard itself. `agent_kind == "master"` is the
 * same key CLAUDE.md's MAN-201 entry already settled on for the same
 * question, and the same fact `fleet_delete_agent` resolves via
 * `get_workspace_master_agent_install`.
 *
 * `role` is still checked as a second disjunct, because it costs nothing and
 * a deployment that DOES stamp role="operator" must not become deletable on
 * a technicality. Never as the only check.
 */
export const UNDELETABLE_AGENT_KIND = "master";
export const UNDELETABLE_AGENT_ROLE = "operator";

export function canDeleteAgent(agent: { agent_kind?: string | null; role?: string | null } | null | undefined): boolean {
  const kind = String(agent?.agent_kind || "").trim().toLowerCase();
  if (kind === UNDELETABLE_AGENT_KIND) return false;
  return String(agent?.role || "").trim().toLowerCase() !== UNDELETABLE_AGENT_ROLE;
}

/** What a post-failure look at reality found. */
export type AgentDeleteRecoveryState = "absent" | "present" | "unreadable";

/**
 * Turns "the request never answered, and here is what the world looks like
 * now" into one of the three outcomes.
 *
 * `absent` is a real success and must be reported as one — telling someone
 * their delete failed when the agent is demonstrably gone sends them to
 * retry something already done, which this codebase has shipped three times
 * in one night and written a law about.
 *
 * `present` is deliberately NOT reported as a definitive failure. A delete
 * that is still in flight server-side looks exactly like one that never
 * started, and claiming certainty we do not have is the same lie pointed
 * the other way.
 */
export function classifyAgentDeleteRecovery(
  state: AgentDeleteRecoveryState,
  agentName: string,
): AgentDeleteOutcome {
  if (state === "absent") return { status: "deleted" };
  return {
    status: "unconfirmed",
    message: `Couldn't confirm whether ${agentName} was deleted. It's safe to try again.`,
  };
}

async function agentStillExists(workspaceId: string, agentId: string): Promise<AgentDeleteRecoveryState> {
  try {
    const res = await fleetAuthorizedFetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/agents`, {
      credentials: "include",
    });
    if (!res.ok) return "unreadable";
    const data = await res.json().catch(() => null);
    if (!data || !Array.isArray(data.agents)) return "unreadable";
    return data.agents.some((a: { agent_id?: string }) => a?.agent_id === agentId) ? "present" : "absent";
  } catch {
    return "unreadable";
  }
}

/**
 * DELETE .../fleet/agents/{agentId} — owner-only, irreversible.
 *
 * A rejected fetch is not a failure, it is an UNKNOWN (see
 * `MutateNetworkError`'s own doc comment in lib/workspace/mutation-outcome.ts
 * for the full argument); the recovery read below is this call site's
 * bespoke answer to "what would be true if it had worked", which that file
 * deliberately leaves to each caller. A non-ok response IS the server's own
 * definitive answer and is relayed in its own words.
 */
export async function deleteFleetAgent(
  workspaceId: string,
  agentId: string,
  agentName: string,
): Promise<AgentDeleteOutcome> {
  let res: Response;
  try {
    res = await fleetAuthorizedFetch(
      `/api/w/${encodeURIComponent(workspaceId)}/fleet/agents/${encodeURIComponent(agentId)}`,
      { method: "DELETE", credentials: "include", headers: buildCookieAuthHeaders("DELETE") },
    );
  } catch {
    const outcome = classifyAgentDeleteRecovery(await agentStillExists(workspaceId, agentId), agentName);
    if (outcome.status === "deleted") await refreshFleetAgents(workspaceId).catch(() => {});
    return outcome;
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data?.ok === false) {
    return { status: "refused", message: getErrorMessage(data, `Couldn't delete ${agentName} (HTTP ${res.status}).`) };
  }
  // Best-effort by design: a stale cache is a much smaller harm than turning
  // a confirmed delete into a reported failure, so a refresh that itself
  // fails never changes the outcome.
  await refreshFleetAgents(workspaceId).catch(() => {});
  return { status: "deleted" };
}

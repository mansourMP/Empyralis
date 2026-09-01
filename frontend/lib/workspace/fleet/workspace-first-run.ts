/**
 * WHETHER THE PROJECTS PAGE'S ZERO-PROJECTS EMPTY STATE OWNS THE ACCENT.
 *
 * Originally "WHERE A BRAND-NEW CUSTOMER IS OFFERED THEIR FIRST AGENT" — see
 * git history for that story (a dead end where no reachable control could
 * create an agent at all, closed 2026-09-01 by dropping the "General"
 * auto-bootstrap so `projects.length === 0` fires honestly on a genuinely
 * new workspace).
 *
 * That closure left a SECOND state on this page: a project exists (the
 * person's own, or an inherited pre-2026-09-01 "General") and the workspace
 * still has no agent. This module used to answer that with a "band" —
 * CreateFirstAgentEmpty's `variant="band"`, sitting above the real project
 * list, offering to create an agent. REMOVED 2026-09-01 (founder, on seeing
 * it live: "inside this project I am seeing a button that says create your
 * first agent even though project is something that must be related to the
 * projects, not agents"). A project's own surfaces are about that project's
 * tasks and documents; agent creation lives in the Agents section of the
 * rail, full stop, with no "but a project already exists" exception. See
 * first-agent-empty.tsx's own history for FirstAgentBand, deleted with it.
 *
 * What is left is exactly the one state this page still answers for itself:
 *
 * ```
 *   full   the pane is genuinely empty (no projects at all) — the centred
 *          "No projects yet / Create your first project" state.
 *   none   there IS a project (so the real list is what's on screen), OR we
 *          have not been told yet.
 * ```
 *
 * The `projectsKnown` latch is STICKY on purpose and must not be simplified
 * to a live `!loading` — fleet-data.ts's shared cache sets `loading` true
 * again on EVERY 30s background refetch (runSharedFetch), so a live
 * `!loading` would flash "No projects yet" at an established workspace
 * twice a minute. "Nothing here" and "haven't been told yet" are different
 * facts; this says which one we are in. Same distinction agent-setup-
 * steps.ts, agent-create-placement.ts and channel-doors.ts all make for the
 * same reason.
 *
 * Pure, no React, no CSS import — so workspace-first-run.test.ts drives the
 * real rule the page renders against, the same discipline
 * agent-count-shape.ts / create-accent.ts / channel-doors.ts follow.
 */

export type FirstAgentPrompt = "full" | "none";

export type FirstAgentPromptState = {
  /** Has the PROJECT list ever settled for this workspace? Sticky — not a
   *  live `!loading`, which goes true again on every background poll. */
  projectsKnown: boolean;
  projectCount: number;
};

export function planFirstAgentPrompt(state: FirstAgentPromptState): FirstAgentPrompt {
  // Nothing is claimed about a list nobody has answered for yet.
  if (!state.projectsKnown) return "none";
  return state.projectCount <= 0 ? "full" : "none";
}

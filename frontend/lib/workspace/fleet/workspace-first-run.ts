/**
 * WHERE A BRAND-NEW CUSTOMER IS OFFERED THEIR FIRST AGENT.
 *
 * ── The dead end this exists to close, walked end to end on a real signup ──
 *
 * Three individually-correct decisions composed into a workspace with no
 * reachable way to create an agent at all:
 *
 * ```
 * signup ─▶ /w/{ws} ─▶ redirects to /w/{ws}/projects        page.tsx
 *   the rail HIDES Inbox and Agents at zero agents          primary-rail-nav.ts
 *   the Projects page shows CreateFirstAgentEmpty ONLY
 *     when projects.length === 0                            projects/page.tsx
 *   ...but every workspace bootstraps a "General" project    routes_workspaces.py
 *   ─▶ the teaching state can NEVER fire, and the rail row that would
 *      reach the real one (at /w/{ws}/agents) is hidden
 * ```
 *
 * Confirmed by reading every interactive element on that landing page: the
 * only escape hatches were the command palette's "New agent" and typing the
 * URL, neither of which a first-time customer knows exists.
 *
 * ── WHY A BAND AND NOT THE FULL EMPTY STATE ──────────────────────────────
 *
 * The obvious fix — key the existing centred empty state on the AGENT count
 * instead of the project count — renders "No projects yet" over a workspace
 * that demonstrably has one. That is the outcome-honesty law pointed at an
 * empty state: the General project is real, it is on the API response, and
 * telling somebody it does not exist to make room for a call to action is
 * the same class of lie as reporting failure on success.
 *
 * So the list still renders the truth, and the offer sits above it as a
 * first-run band carrying ONE control — the same "the single next thing
 * that unblocks you, never a menu" shape agent-setup-steps.ts already
 * settled after the founder rejected a row of optional links ("it acts like
 * a button, not step-by-step... I don't want to have that").
 *
 * ```
 *   full   the pane is otherwise empty (no projects at all) — today's
 *          centred CreateFirstAgentEmpty, byte-identical, unchanged.
 *   band   there ARE projects and no agents — the offer above the real list.
 *   none   an agent already exists, OR we have not been told yet.
 * ```
 *
 * ── "NO AGENTS" AND "I HAVE NOT ASKED YET" ARE DIFFERENT FACTS ────────────
 *
 * Both lists return `[]` while loading, so a plan keyed on length alone
 * flashes a first-run band at an established workspace on every cold paint —
 * and, worse, on every 30s poll, because fleet-data.ts's shared cache sets
 * `loading` true again on each background refetch (runSharedFetch). The two
 * `*Known` inputs are therefore STICKY latches owned by the caller ("has a
 * fetch for this list ever settled"), never a live `!loading`. Same
 * distinction agent-setup-steps.ts, agent-create-placement.ts and
 * channel-doors.ts all make for the same reason.
 *
 * ── Reversible for free ───────────────────────────────────────────────────
 *
 * Recomputed from the live counts on every render, exactly like
 * agent-count-shape.ts's own consumers: the moment the first agent exists
 * this returns "none" and the band is gone, with no flag anywhere to unset.
 *
 * Pure, no React, no CSS import — so workspace-first-run.test.ts drives the
 * real rule the page renders against, the same discipline
 * agent-count-shape.ts / create-accent.ts / channel-doors.ts follow.
 */

export type FirstAgentPrompt = "full" | "band" | "none";

export type FirstAgentPromptState = {
  /** Has the PROJECT list ever settled for this workspace? Sticky — not a
   *  live `!loading`, which goes true again on every background poll. */
  projectsKnown: boolean;
  projectCount: number;
  /** Has the AGENT list ever settled for this workspace? Same stickiness. */
  agentsKnown: boolean;
  /** Sage/the Operator NEVER counts — every workspace carries one from the
   *  moment it exists (`fleet_list_agents(..., include_master=True)`), so a
   *  raw length can never reach zero and this would never fire. Callers pass
   *  the already-Sage-filtered count, exactly as agent-count-shape.ts
   *  requires of its own. */
  realAgentCount: number;
};

export function planFirstAgentPrompt(state: FirstAgentPromptState): FirstAgentPrompt {
  // Nothing is claimed about a list nobody has answered for yet.
  if (!state.projectsKnown) return "none";

  // The pane is genuinely empty. This branch is deliberately NOT gated on
  // the agent count: it is what the page already did, and keeping it
  // independent means this module can never turn an empty projects list
  // into "No projects match these filters" for a workspace that happens to
  // own an agent.
  if (state.projectCount <= 0) return "full";

  if (!state.agentsKnown) return "none";
  return state.realAgentCount <= 0 ? "band" : "none";
}

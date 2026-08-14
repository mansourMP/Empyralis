/**
 * The two shared primitives for the "outcome honesty" law (CLAUDE.md,
 * "Product laws" — "after an action, the product must tell the person what
 * actually happened"). Both came out of the 2026-08-14 sweep
 * (outcome-honesty-drift.test.ts) that found ~20 sites hand-writing a
 * version of one of these two shapes, independently, at different times —
 * which is the actual argument for putting them here rather than a
 * post-hoc tidy-up: `MutateNetworkError` had already been written twice
 * (members-data.ts's acceptWorkspaceInvite, cloud-vps-setup-panel.tsx's
 * createServer()) before this file existed, and would have been written a
 * third time for FleetCreateAgentWizard.tsx had that fix not reached for a
 * ref-based idempotency trick instead.
 *
 * These are DELIBERATELY narrow. Neither one decides what a mutation's own
 * error MEANS — that wording stays bespoke per call site ("Could not
 * create this invite" vs "Could not update this task" are legitimately
 * different sentences) — and neither one guesses at an outcome it cannot
 * know. Read each one's own doc comment before reaching past it for
 * something more general; a bigger abstraction here is exactly how a good
 * one turns into a bad one.
 */

/**
 * Runs a mutation, then a follow-up refresh whose OWN failure must never be
 * reported as the mutation failing. This is shape A of the sweep's finding
 * — a real write that already committed, followed by a plain read (a list
 * re-fetch, a bootstrap reload) that can independently fail — and it was
 * the majority shape: roughly 15 of the ~26 sites the sweep touched were
 * exactly this, each hand-split into "await the mutation in its own catch;
 * await the refresh separately, swallowed" before this function existed to
 * say it once.
 *
 * What this function does NOT do, on purpose:
 *   - It does not catch the mutation's own error. `mutate` is awaited
 *     directly; if it throws, this function's promise rejects and the
 *     caller's own try/catch (with its own, bespoke error message) handles
 *     it exactly as if this function were not here. The one and only rule
 *     this function encodes is: whatever happens to `refresh`, it happens
 *     AFTER the mutation is known to have succeeded, and it never turns
 *     that success into a reported failure.
 *   - It does not expose the refresh's own result. If a caller needs a
 *     value the refresh produced (e.g. a freshly-reloaded session object
 *     to hand to some other function), have `refresh` perform that side
 *     effect itself before resolving — see NewWorkspacePageClient.tsx's
 *     adoption for the pattern (`refresh: async () => { const session =
 *     await loadAccountShellBootstrap(); actions.replaceSession(session);
 *     }`). Threading the refresh's return type back out would double the
 *     generic parameters for a need only a couple of call sites have.
 *   - It does not retry, and it does not distinguish WHY the refresh
 *     failed. A stale list until the next natural reload is a much
 *     smaller harm than a false failure claim — that is the whole trade
 *     this function is making, not something to refine per call site.
 *   - It logs the swallowed refresh failure to the console rather than
 *     going fully silent — "best-effort" should not mean "invisible to
 *     whoever is debugging this later."
 *
 * NOT every "mutate then refresh" site fits this signature, and forcing
 * one that doesn't is worse than leaving it hand-written — see the sweep
 * commit for the sites deliberately left alone and why (a second,
 * independent best-effort follow-up beyond one refresh; a follow-up
 * failure that is deliberately still SURFACED to the person with
 * distinguishing wording rather than silently logged; a sequence of two or
 * more real mutations that each need their own message).
 */
export async function runMutationWithBestEffortRefresh<T>(
  mutate: () => Promise<T>,
  refresh?: () => Promise<unknown>,
): Promise<T> {
  const result = await mutate();
  if (refresh) {
    await refresh().catch((e) => {
      console.error("runMutationWithBestEffortRefresh: refresh failed after a successful mutation", e);
    });
  }
  return result;
}

/**
 * Thrown only when a mutation request itself never produced a response —
 * `fetch()` (or whatever wraps it) rejected outright: offline, a dropped
 * connection, a timeout, an aborted request. That is a genuinely different
 * fact from the server answering with a non-2xx status: a rejected fetch
 * means the outcome is UNKNOWN. The request may have reached the server,
 * been fully processed, and committed a real write, with only the
 * RESPONSE lost on the way back — while a non-ok response is the server's
 * own, definitive answer that nothing committed.
 *
 * THE PATTERN THIS CLASS EXISTS FOR (shape B of the sweep's finding, and
 * the reason this is a class and not just a boolean flag): a mutation
 * wrapper throws `MutateNetworkError` specifically for the fetch-level
 * failure, never for a definitive HTTP rejection. A caller that catches it
 * then has a real choice to make, which stays entirely call-site-specific
 * and is NOT something this file tries to generalize:
 *
 *   - If there is a safe, real way to check whether the mutation actually
 *     went through — re-read the thing that would exist if it did — do
 *     that BEFORE ever reporting failure, and proceed as success if it's
 *     there. See frontend/app/join/[token]/page.tsx (decodes the invite
 *     token's own workspace_id and checks real membership),
 *     cloud-vps-setup-panel.tsx's createServer() (searches the workspace's
 *     VPS records for one matching this exact attempt), and
 *     FleetCreateAgentWizard.tsx's submitPlacement (searches the
 *     workspace's agents by name+project). Each of these recovery steps is
 *     necessarily bespoke — "what would exist if this succeeded" is a
 *     different question at every call site, which is exactly why this
 *     class only offers the DETECTION primitive and not a shared recovery
 *     function.
 *   - Where no such check exists, say so honestly ("we couldn't confirm
 *     this went through — it's safe to retry") rather than a flat
 *     "failed," and offer a real retry action.
 *
 * A mutation helper wraps `fetch`/`fleetAuthorizedFetch` roughly like:
 *
 *     let response: Response;
 *     try {
 *       response = await fleetAuthorizedFetch(path, init);
 *     } catch (e) {
 *       throw new MutateNetworkError(e instanceof Error ? e.message : "Network request failed.");
 *     }
 *     if (!response.ok) throw new Error(...);  // a definitive rejection — NOT MutateNetworkError
 */
export class MutateNetworkError extends Error {}

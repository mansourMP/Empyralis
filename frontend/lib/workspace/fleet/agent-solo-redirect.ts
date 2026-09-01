/**
 * MAN-374 — whether a "you have exactly one agent, go straight to it"
 * redirect (MAN-317, agent-count-shape.ts's "solo" mode) is allowed to fire
 * RIGHT NOW. Extracted into one pure function because the identical
 * decision was independently re-typed in TWO places —
 * `app/(account)/w/[workspaceId]/agents/page.tsx` (workspace-wide) and
 * `app/(account)/w/[workspaceId]/projects/[projectId]/page.tsx`
 * (project-scoped) — and both copies made the SAME omission: neither
 * checked whether AgentCreateCard (the "New agent" wizard) was currently
 * open. A single shared rule is what stops that drift recurring the third
 * time a page grows its own solo-agent redirect.
 *
 * ── WHY `cardOpen` IS LOAD-BEARING, NOT DEFENSIVE ─────────────────────────
 * Reproduced live, twice, on a disposable stack: create a brand-new
 * workspace's FIRST agent via "Create your first agent" (or the project
 * page's own equivalent empty-state button) and watch the wizard vanish the
 * instant you press "Create agent" on step 2 (Brain) — landing straight on
 * the new agent's Chat, with the required Channels step (and Apps) never
 * shown. `AgentCreateCard.create()` calls `createAgentQuickly()`, which
 * AWAITS `fleet-data.ts`'s `refreshFleetAgents(workspaceId)` before
 * returning — a synchronous force-refetch of the SAME shared
 * `fleet-agents:{workspaceId}` cache the surrounding page's own agent list
 * is built from (both `useFleetAgents` and the project page's own agents
 * query share it). The moment that resolves, the real agent count flips
 * 0 -> 1, `planAgentCountShape` computes "solo", and — with no guard — the
 * page's own redirect effect fires `router.replace` into the new agent's
 * Chat while the wizard is still sitting on Brain, tearing the modal down
 * mid-sequence. The founder's rule that an agent cannot be created without
 * a channel (agent-create-wizard.ts's own header, "PART B") has no way to
 * hold once the component enforcing it has been unmounted by an unrelated
 * navigation.
 *
 * The wizard being open AT ALL is reason enough to leave this redirect
 * alone — regardless of which control opened it (a button, the command
 * palette's `?new=1` hand-off, or anything added later) — so gating on
 * `cardOpen` closes the whole class of entry points at once instead of
 * chasing each one with its own suppression flag.
 *
 * `suppressed` stays a SEPARATE, optional input rather than being folded
 * into `cardOpen`: the workspace-level page has TWO query-param-derived
 * suppressions of its own, both read once at mount, before this render
 * decides anything — `?new=1` (the command palette's "New agent" hand-off)
 * and `?list=1` (bug A, 2026-09-01: FleetAgentDetail's back control uses it
 * to reach the agents list even from a workspace holding exactly one agent
 * — without it, back bounced straight into the same agent and the list,
 * and "New agent" with it, were unreachable). Both inputs must hold for the
 * redirect to fire; either one being true blocks it.
 *
 * UPDATE, 2026-09-01: the project-scoped copy this header used to describe
 * (`.../projects/[projectId]/page.tsx`) is gone — the project-scoped
 * `/agents` route and its own redirect were deleted 2026-08-21/2026-08-30
 * (an agent is independent of every project; see CLAUDE.md). This module
 * now has exactly one caller. Kept as a function rather than inlined back
 * into that caller anyway — the two-places drift this guarded against is
 * one page growing a second copy away from being repeated.
 */
export function shouldRedirectToSoloAgent(input: {
  loading: boolean;
  hasSoloTarget: boolean;
  cardOpen: boolean;
  suppressed?: boolean;
}): boolean {
  return !input.loading && input.hasSoloTarget && !input.cardOpen && !(input.suppressed ?? false);
}

/**
 * The two query-derived reasons `suppressed` above can be true — split into
 * two named checks (rather than one combined boolean) because the CALLER
 * has to read them differently, not just for documentation. Both are pure,
 * string-in/boolean-out functions (rather than an inline
 * `URLSearchParams(...).get(...) === "1"` check duplicated at each call
 * site) so the mapping is unit-testable without a DOM.
 *
 * - `new=1` (isSoloRedirectNewHandoffParam) — the command palette's "New
 *   agent" hand-off. Read ONCE, at mount, by design: agents/page.tsx's
 *   `consumedNew` effect strips it from the URL the instant it opens
 *   AgentCreateCard, and re-deriving this from the live URL on every
 *   render would start redirecting again the moment the param is gone —
 *   before the async create has finished. Also consumed elsewhere to open
 *   the wizard; this function only answers the suppression question.
 *
 * - `list=1` (isSoloRedirectListBackParam) — bug A, 2026-09-01:
 *   FleetAgentDetail's back control sets this to reach the agents list
 *   even from a workspace holding exactly one agent. The CALLER must read
 *   this via `next/navigation`'s `useSearchParams()`, not
 *   `window.location.search` — proven live, by direct instrumentation, in
 *   a real browser: clicking the real back `<Link>` re-rendered
 *   agents/page.tsx with `window.location.search` still reading the OLD,
 *   query-less `/agents` for its first render (and the redirect effect
 *   that fires off that render), one or more renders BEFORE Next's own
 *   history state actually caught up to `?list=1`. A `useState(() => ...)`
 *   initializer reading `window.location.search` — the FIRST attempt at
 *   this fix, symmetric with `new=1` below — captured exactly that stale
 *   value and never recovered, so the redirect fired before the correction
 *   ever landed. `useSearchParams()` reads Next's own router state, kept
 *   in lockstep with navigation, not the imperative and occasionally-
 *   lagging `window.location`; agents/page.tsx already wraps itself in the
 *   `<Suspense>` boundary that hook requires. Unlike `new=1`, `list=1` is
 *   also never stripped from the URL — the suppression has to survive a
 *   reload of the resulting URL, so it lives in the URL itself, read live
 *   on every render, not captured once in component state.
 */
export function isSoloRedirectNewHandoffParam(search: string): boolean {
  return new URLSearchParams(search).get("new") === "1";
}

export function isSoloRedirectListBackParam(search: string): boolean {
  return new URLSearchParams(search).get("list") === "1";
}

/** OR of both suppression sources above — kept for callers (and tests)
 * that only need the combined answer and don't care which one fired. The
 * live component itself does NOT use this: it needs the two halves read at
 * different times (see the per-function docs above), so it calls the two
 * named checks directly instead. */
export function suppressSoloRedirectFromSearch(search: string): boolean {
  return isSoloRedirectNewHandoffParam(search) || isSoloRedirectListBackParam(search);
}

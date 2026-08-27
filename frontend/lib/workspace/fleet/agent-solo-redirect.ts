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
 * into `cardOpen`: the workspace-level page also has a query-param-derived
 * suppression (`?new=1`, read once at mount, BEFORE the card has actually
 * opened) that the project page has no equivalent of. Both inputs must
 * hold for the redirect to fire; either one being true blocks it.
 */
export function shouldRedirectToSoloAgent(input: {
  loading: boolean;
  hasSoloTarget: boolean;
  cardOpen: boolean;
  suppressed?: boolean;
}): boolean {
  return !input.loading && input.hasSoloTarget && !input.cardOpen && !(input.suppressed ?? false);
}

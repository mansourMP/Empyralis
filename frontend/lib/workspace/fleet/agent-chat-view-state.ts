/**
 * What AgentChat's message list actually shows — pure decision, no JSX.
 *
 * Bug, observed live by the founder: navigating away from a chat mid-turn
 * (to Work, or into Configure) unmounts ChatTab; coming back remounts it
 * and re-fetches `/api/threads/{id}` from scratch. Before this module, the
 * render was a plain ternary keyed only on `loading` and an *independently*
 * computed `showEmptyState` — so a FAILED load (a thrown fetch, a 401 that
 * survived the single retry, a network hiccup) still had `messages.length
 * === 0`, which made `showEmptyState` true regardless of the error. The
 * screen rendered the ordinary "say something to start" welcome copy, with
 * only a small paragraph of red text tucked below it — CLAUDE.md's own
 * outcome-honesty law, violated: *"'empty' and 'I could not load this' are
 * different facts and must never share one screen."* A customer coming back
 * to a real conversation that failed to load was told, visually, that
 * nothing had ever been said.
 *
 * This function is the one place that decision gets made, so the two facts
 * can never be merged back into one render again. A caller with existing
 * cached messages that then fails a BACKGROUND refresh keeps "content" —
 * losing what's already on screen because a later poll hiccupped would be
 * its own, worse violation of the same law.
 */
export type AgentChatViewState = "loading" | "error" | "empty" | "content";

export function resolveAgentChatViewState(params: {
  loading: boolean;
  error: string | null;
  messageCount: number;
  streamingActive: boolean;
}): AgentChatViewState {
  const { loading, error, messageCount, streamingActive } = params;
  if (loading) return "loading";
  const hasContent = messageCount > 0 || streamingActive;
  if (hasContent) return "content";
  // Nothing to show. Whether that is a genuinely empty, never-started
  // conversation or a load that failed before anything could render is the
  // one fact this function exists to keep distinct.
  return error ? "error" : "empty";
}

/**
 * WHICH AGENT-CREATION CONTROL OWNS THE VIEW'S ONE ACCENT FILL.
 *
 * CLAUDE.md's craft doctrine, stated once and violated three times on this
 * surface: "One accent colour, spent on the single primary action in a view.
 * Everything else is neutral. Two accent-filled buttons in one view is a
 * bug."
 *
 * There are THREE controls that can create an agent, and up to two of them
 * are on screen at the same time:
 *
 *   header       the persistent "+ New agent" in the topbar (the workspace
 *                Agents page, a project's Agents view)
 *   empty_state  FirstAgentEmpty's centred "Create your first agent"
 *   card         AgentCreateCard's own "Create agent" in its footer
 *
 * The empty-state/header pair was already handled at each call site by hand
 * (`agents.length === 0 ? " fleet-btn--accent" : " fleet-btn--accent-fill"`,
 * with a comment quoting the rule). The CARD was not: opening it left the
 * header button — or FirstAgentEmpty's own filled CTA — visible behind a 45%
 * backdrop, filled, beside the card's filled "Create agent". The founder
 * found it live; the file that broke it quotes the rule ten lines above the
 * violation, which is exactly why the judgement belongs in one tested
 * function rather than repeated as a boolean expression at four call sites.
 *
 *   card open      ─▶ the card owns it. It is the modal; everything behind
 *                     it drops to the quiet hairline variant.
 *   list is empty  ─▶ the empty state owns it. A first-run empty state is
 *                     the one moment its own big button IS the primary
 *                     action.
 *   otherwise      ─▶ the header owns it. Past first run it is the
 *                     persistent primary action used every day.
 *
 * Note there is always EXACTLY ONE owner, never zero — asserted directly in
 * agent-create-accent.test.ts, because "no accent anywhere" is a different
 * bug that reads as a page with no primary action.
 *
 * Pure, no React, no CSS import — same discipline as agent-count-shape.ts,
 * so a plain `tsx` test drives the real rule the components render against.
 */

export type AgentCreateControl = "header" | "empty_state" | "card";

export type AgentCreateAccentState = {
  /** No agents in the list this control sits on, so FirstAgentEmpty is what
   *  fills the pane. */
  listIsEmpty: boolean;
  /** AgentCreateCard is mounted. */
  createCardOpen: boolean;
};

export function agentCreateAccentOwner(state: AgentCreateAccentState): AgentCreateControl {
  if (state.createCardOpen) return "card";
  return state.listIsEmpty ? "empty_state" : "header";
}

export function agentCreateOwnsAccent(control: AgentCreateControl, state: AgentCreateAccentState): boolean {
  return agentCreateAccentOwner(state) === control;
}

/** The full className for one of those controls. `fleet-btn--accent` is the
 *  quiet hairline variant — still an accent-coloured control, still
 *  reachable, just not the filled one (no dead controls: a button that loses
 *  the fill is never removed or disabled by this rule). */
export function agentCreateButtonClass(control: AgentCreateControl, state: AgentCreateAccentState): string {
  return `fleet-btn ${agentCreateOwnsAccent(control, state) ? "fleet-btn--accent-fill" : "fleet-btn--accent"}`;
}

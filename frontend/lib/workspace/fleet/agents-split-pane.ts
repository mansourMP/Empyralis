/**
 * WHICH PANE THE WORKSPACE AGENTS SPLIT SHOWS AT PHONE WIDTH.
 *
 * agents/layout.tsx renders a master-detail row — AgentConversationList
 * beside the routed agent page — inside `.fleet-content--split`. That shell
 * is a fixed 30%-plus-320px-floor row with `max-width: none` and no narrow
 * rule of its own, so at 375px the list pane took the whole viewport and the
 * agent's own column was squeezed to the sliver left over: header, content
 * and empty state all clipped mid-word, unreachable. MEASURED live at
 * 375x812, 2026-08-21.
 *
 *   375px viewport
 *     list    flex 0 0 30%  →  min-width 320px WINS  ─▶ 320px
 *     detail  flex 1 min-width 0                     ─▶  55px, clipped
 *
 * The fix is the idiom this codebase already uses for the Inbox
 * (.fleet-inbox--detail-open, fleet-theme.css): ONE PANE AT A TIME below
 * 768px, both panes staying in the DOM so desktop never reflows. NOT
 * "hide the list" — that would make picking an agent impossible on a phone,
 * which is worse than the bug.
 *
 * The one difference from the Inbox, and it is a simplification: the Inbox
 * needs React state (`mobileDetailOpen`) because selecting a row there is
 * client state, so nothing in the URL says which pane a reader is on. Here
 * the pane IS the route —
 *
 *   /w/{ws}/agents               no agentId  ─▶ list    (the picker)
 *   /w/{ws}/agents/{id}/chat     an agentId  ─▶ detail  (what was picked)
 *
 * — so there is no second source of truth to keep in sync, and the browser
 * Back button plus the breadcrumb's own "‹" (Breadcrumbs.tsx's mobile
 * parent crumb, which for this route resolves to `/w/{ws}/agents`) already
 * return to the list with nothing new to build. A route-derived pane also
 * survives a hard reload and a shared link, which a state-derived one
 * cannot.
 *
 * Pure, no React, no CSS import — same discipline as agent-count-shape.ts
 * and agent-create-accent.ts, so a plain `tsx` test drives the real rule the
 * layout renders against, and the CSS half (which no behavioural test can
 * reach) gets its own source scan in agents-split-pane.test.ts.
 */

export type AgentsSplitPane = "list" | "detail";

/** Which of the two panes is the visible one at phone width. Exactly one,
 *  never both (the bug) and never neither (a blank screen, which would pass
 *  a "not both" test). */
export function agentsMobilePane(activeAgentId: string | null | undefined): AgentsSplitPane {
  return activeAgentId ? "detail" : "list";
}

/** The className for the split container. `fleet-content--split` is
 *  unconditional — the DESKTOP layout is untouched by this rule, and the
 *  modifier only ever feeds a `max-width: 768px` block. */
export function agentsSplitClassName(activeAgentId: string | null | undefined): string {
  const base = "fleet-content fleet-content--split";
  return agentsMobilePane(activeAgentId) === "detail" ? `${base} fleet-agents-split--detail-open` : base;
}

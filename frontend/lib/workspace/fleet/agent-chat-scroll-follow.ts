/**
 * Whether AgentChat's message list should auto-follow new content — pure
 * decision, no DOM, no JSX.
 *
 * Measured live on production: a chat with real history landed with
 * `scrollTop: 0` on a 12,979px-tall scroller (the oldest message, not the
 * newest) and the composer sat ~12,300px below the fold, inside the scroll
 * flow rather than pinned. Both symptoms trace to the same cause — see
 * FleetAgentDetail.tsx's ChatTab, which used to wrap AgentChat in a bare
 * `<div style={{display:"none"}}>` for the always-mounted-across-tabs fix.
 * That wrapper broke `.fleet-agent-chat-panel` being `.fleet-detail-body`'s
 * DIRECT child, which is what both the internal-scroll height chain and the
 * width-cap opt-out in fleet-theme.css key off — so the intended inner
 * scroller (`.fleet-sage-chat-list`) grew to fit ALL of its content instead
 * of being bounded, and `.fleet-detail-body` (the page-level scroller)
 * became the one actually scrolling.
 *
 * This module is the second half, once the DOM chain is fixed: deciding
 * WHEN to snap the now-correctly-bounded list to its bottom. Landing on
 * the newest message on open is the same "you never lose your place"
 * contract CLAUDE.md's own Telegram-comparison entry names, and a person
 * who has scrolled up mid-stream to read history must not be yanked back
 * down by the next token — the standard messenger behaviour is to follow
 * only while already at (or near) the bottom.
 */

/** Tolerance, in CSS pixels, for "close enough to the bottom to still
 *  count as following it". A few px of sub-pixel rounding (real browsers
 *  report non-integer scrollHeight under zoom/fractional DPR — observed
 *  live: 20369.8px) must never itself read as "the user scrolled away". */
export const NEAR_BOTTOM_TOLERANCE_PX = 48;

export interface ScrollMetrics {
  scrollTop: number;
  scrollHeight: number;
  clientHeight: number;
}

/**
 * True when the scroller is at, or within tolerance of, its own bottom.
 * A scroller with nothing to scroll (content fits the viewport) is always
 * "at the bottom" — there is nowhere else for it to be.
 */
export function isNearBottom(metrics: ScrollMetrics): boolean {
  const { scrollTop, scrollHeight, clientHeight } = metrics;
  const distanceFromBottom = scrollHeight - scrollTop - clientHeight;
  return distanceFromBottom <= NEAR_BOTTOM_TOLERANCE_PX;
}

/**
 * Should the next content update (a new message, a streamed token) snap
 * the list to its bottom?
 *
 * `wasFollowing` is the caller's own memory of "was I near the bottom
 * before this update landed" — captured from the scroller's OWN metrics on
 * the previous frame, via a live `scroll` listener, never recomputed from
 * the post-update DOM (by the time new content has landed, scrollHeight has
 * already grown, so re-measuring here would always say "not at the bottom
 * any more" even for someone who never moved).
 *
 * `paneHidden` is the guard for a `display:none` pane: it has zero
 * dimensions, so ANY read of its scrollHeight/clientHeight there is
 * meaningless, and writing scrollTop from that meaningless read would
 * permanently record "top" for a conversation that was actually at its
 * bottom the moment the pane is shown again. A hidden pane never snaps —
 * it catches up once, on the render where `paneHidden` next flips false
 * (AgentChat re-runs this same decision keyed on that prop).
 *
 * That "catch up" is the following-case only. Measured directly against
 * this pane: Chromium DISCARDS an element's `scrollTop` — resets it to 0 —
 * the moment it goes `display:none`, so a reader who was NOT following
 * (scrolled up to read history) would otherwise return to find themselves
 * at the very top, not back where they left off. This function only says
 * whether to snap to the bottom; AgentChat itself keeps a ref of the last
 * real scrollTop (from the same scroll listener that feeds `wasFollowing`)
 * and restores it explicitly on the same "just became visible, not
 * following" render this function returns `false` for.
 */
export function shouldSnapChatToBottom(params: {
  wasFollowing: boolean;
  paneHidden: boolean;
}): boolean {
  return params.wasFollowing && !params.paneHidden;
}

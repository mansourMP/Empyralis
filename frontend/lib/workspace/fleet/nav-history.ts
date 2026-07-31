"use client";

/**
 * Position tracking for the app-chrome back/forward buttons (‹ / ›).
 *
 * DECISION: this rides the browser's OWN History API rather than a parallel
 * app-level stack. Two things pushed that call:
 *
 *   1. It already works. FleetTabs.tsx's tab strip switches tabs with a real
 *      `router.push` (see activateTab there), every real navigation in this
 *      app (project → task, ⌘-click, a rail link) goes through Next's router
 *      — which is the History API — and FleetAgentDetail.tsx's Esc handler
 *      already calls `router.back()` to "return to wherever you came from".
 *      Browser history is the app's existing, working model for "back";
 *      this feature surfaces a control for it, it doesn't invent a new one.
 *
 *   2. An independent stack is the one design that can produce the exact
 *      failure this feature must avoid: "pressing browser-back and app-back
 *      producing contradictory states." A second, hand-maintained stack can
 *      only ever be an approximation of the real history (it has to be
 *      re-synced on every one of the dozens of router.push/replace call
 *      sites across the app, plus the browser's own back/forward, plus
 *      swipe/mouse-button navigation) — any gap between the two is exactly
 *      the "looks right, jumps somewhere unexpected" bug called out in the
 *      brief. Riding the same mechanism the browser itself uses means my
 *      button and the browser's own chevrons can never disagree — they are
 *      driving the same pointer.
 *
 * CAVEAT (investigated, accepted): this app has view state that changes
 * what's on screen WITHOUT changing the URL at all — the Overview/Agents/
 * Tasks segmented control on a project's page (`page.tsx`'s `view` state) is
 * plain `useState`, never written to the URL or history, unlike the same
 * page's status/channel/sort filters (which DO round-trip through
 * `router.replace`, see `updateFilters`). So going back from a task to its
 * project always lands on the project's default "Overview" view, even if
 * you had the "Tasks" tab open when you clicked into the task. This is a
 * pre-existing gap in the app's URL model, not something this feature
 * introduces — the browser's OWN back button has always had this exact
 * behaviour, on every page in this app, before these ‹/› buttons existed.
 * Fixing it means putting `view` in the URL the way the filters already
 * are, which is a real but separate change (see the report).
 *
 * THE ACTUAL PROBLEM THIS MODULE SOLVES: the History API has no
 * `canGoBack`/`canGoForward` — `history.length` counts entries in the whole
 * session (including ones ahead of you) and is the only thing exposed, and
 * even that isn't enough to know your own position. So this stamps a
 * monotonic index into `history.state` on every entry this tab creates (by
 * wrapping `pushState`/`replaceState` once, passively — always calling the
 * original implementation first, so Next's own router is untouched) and
 * derives canGoBack/canGoForward from comparing the current entry's index
 * against the highest index reached down this branch.
 *
 * KNOWN LIMIT: a hard reload only preserves the CURRENT entry's stamped
 * state (that's how browsers persist `history.state` across a reload); it
 * can't tell this module what the reachable forward-max was for entries
 * that existed before the reload. So a reload taken after going back at
 * least once under-reports (never over-reports) how far forward you can go
 * — "forward" may show disabled immediately after such a reload even though
 * the browser's own forward would still work. Conservative-wrong (a
 * disabled control) beats optimistic-wrong (a control that jumps somewhere
 * broken) per the brief, so this is left as is rather than solved with a
 * cross-entry index that no browser API actually offers.
 */

import { useEffect, useState } from "react";

type StampedState = { __navSeq?: number } | null;

let patched = false;
let seq = 0; // Monotonic id for the next entry this tab creates.
let currentIndex = 0; // Which entry (by that id) we're on right now.
let maxIndex = 0; // Highest id reached down the CURRENT branch — forward can reach up to this.

type Listener = () => void;
const listeners = new Set<Listener>();
const notify = () => listeners.forEach((l) => l());

function ensurePatched(): void {
  if (patched || typeof window === "undefined") return;
  patched = true;

  const origPush = window.history.pushState.bind(window.history);
  const origReplace = window.history.replaceState.bind(window.history);

  // Passive wrappers: the original call always happens, with the caller's
  // exact arguments — Next's router (and anything else on the page) sees
  // no difference. The only addition is stamping our own counter into the
  // state object afterward, merged rather than overwritten so Next's own
  // internal history state (scroll position, etc.) survives untouched.
  window.history.pushState = function patchedPushState(state, title, url) {
    seq += 1;
    currentIndex = seq;
    maxIndex = seq;
    const merged = { ...(state && typeof state === "object" ? state : {}), __navSeq: currentIndex };
    origPush(merged, title, url);
    notify();
  } as typeof window.history.pushState;

  window.history.replaceState = function patchedReplaceState(state, title, url) {
    // A replace relabels the entry we're already on — position never moves.
    const merged = { ...(state && typeof state === "object" ? state : {}), __navSeq: currentIndex };
    origReplace(merged, title, url);
    notify();
  } as typeof window.history.replaceState;

  window.addEventListener("popstate", (e) => {
    const s = e.state as StampedState;
    // No marker means we've popped onto an entry this module never
    // stamped (pre-app history, or a foreign page) — hold position rather
    // than guess, so an out-of-range entry can't corrupt canGoBack/Forward
    // for whenever the user navigates back into tracked territory.
    if (typeof s?.__navSeq === "number") currentIndex = s.__navSeq;
    notify();
  });

  // A hot-reload/dev-refresh (or a plain browser refresh mid-session)
  // preserves the CURRENT entry's history.state across the reload — so if
  // it already carries our marker, resume from it instead of resetting to
  // 0, which would falsely disable "back" on a tab that has real app
  // history behind it.
  const existing = (window.history.state as StampedState)?.__navSeq;
  if (typeof existing === "number") {
    seq = existing;
    currentIndex = existing;
    maxIndex = existing;
  } else {
    origReplace({ ...(window.history.state || {}), __navSeq: 0 }, "");
  }
}

// Patch as early as this module is ever imported, not deferred to a
// component's mount effect — a navigation that happens before the first
// render of whatever uses `useNavHistory` (e.g. an initial redirect) would
// otherwise slip past unstamped and desync the count.
if (typeof window !== "undefined") ensurePatched();

export function useNavHistory(): { canGoBack: boolean; canGoForward: boolean } {
  const [, setTick] = useState(0);
  useEffect(() => {
    ensurePatched();
    const listener = () => setTick((t) => t + 1);
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  }, []);
  return { canGoBack: currentIndex > 0, canGoForward: currentIndex < maxIndex };
}

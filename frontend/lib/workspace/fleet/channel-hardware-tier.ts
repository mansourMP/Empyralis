/**
 * THE TWO-TIER CHANNEL SPLIT — which channels a person can connect right now,
 * and which ones first need a computer paired to this agent.
 *
 *     No computer needed     Telegram · Slack · Discord · WeChat
 *     Needs a computer       WhatsApp · Signal · iMessage · ~20 more
 *
 * This is the critical path, not a nicety. Chat was removed from the platform
 * (CLAUDE.md, "THE PLATFORM IS NOT A CHAT PRODUCT"), so a channel is now the
 * ONLY way a person talks to their agent — and on 2026-08-20 the Channels tab
 * was found to be a dead end for every cloud-only agent, showing "Needs
 * Gateway" for a Telegram lane that has never needed one. A customer must be
 * able to see, at a glance and before clicking anything, which cards they can
 * finish today.
 *
 * DERIVED FROM THE DOORS. NEVER A LIST OF CHANNEL NAMES.
 * ------------------------------------------------------
 * This surface has already shipped FOUR hand-copied channel lists that drifted
 * (CLAUDE.md: the five-entry tuple, the parallel label map, the gateway array,
 * the fixture generator's own list). There is not a fifth here. The tier is a
 * pure function of the doors a card already carries:
 *
 *     some real door needs no computer   ─▶  "hardware_free"
 *     every real door needs one          ─▶  "needs_hardware"
 *     no real door at all                ─▶  "unknown"   (says so; never guesses)
 *
 * and `ChannelDoor.requiresHardware` is set in exactly two places, both of
 * which already existed:
 *
 *     CHANNEL_DOORS            authored per first-party door (none today —
 *                              every surviving first-party lane is a cloud
 *                              bot). A first-party door that binds a real
 *                              client process to the agent's own box sets it,
 *                              exactly as the deleted WhatsApp/Signal/iMessage
 *                              doors used to.
 *     groupTransportedChannels TRUE for every variant, because the transport
 *                              IS a process on the box — see that function.
 *
 * So a channel the transport ships tomorrow is tiered correctly with no edit
 * here, and a first-party lane added tomorrow is tiered by the same field its
 * own panel already reads for the hardware note. If adding a channel required
 * editing this file, it would be wired wrong.
 *
 * WHY "some door" AND NOT "every door"
 * ------------------------------------
 * A card is a PLATFORM, and a platform reachable two ways — Telegram was
 * exactly this until the 2026-08-14 cutover, and will be again — belongs in
 * the tier a person can actually act on today. The filter's question is "can I
 * connect this right now with nothing installed", and one open door answers
 * yes. The panel behind the card still states the per-door truth (see
 * channelDoorHardwareNote), so nothing is hidden by the card's summary.
 *
 * PURE DATA + PURE FUNCTIONS, IN ITS OWN MODULE — no React, no CSS import, no
 * "use client" — for the same reason channel-doors.ts and channel-platform.ts
 * are: channel-hardware-tier.test.ts runs under plain `npx tsx`, outside
 * Next.js, and drives the REAL doors and the REAL generated manifest rather
 * than re-typing either.
 */

import type { ChannelDoor } from "./channel-doors";
import { compareChannelsForGrid } from "./channel-popularity";

export type ChannelHardwareTier = "hardware_free" | "needs_hardware" | "unknown";

/** The whole rule. Takes the doors rather than a channel id, so an authored
 *  first-party card and a derived transported one go through ONE function
 *  instead of two that agree today. */
export function channelHardwareTier(candidates: readonly ChannelDoor[]): ChannelHardwareTier {
  const doors = candidates.filter((door) => door.real);
  if (doors.length === 0) return "unknown";
  return doors.some((door) => !door.requiresHardware) ? "hardware_free" : "needs_hardware";
}

// ── THE FILTER ──────────────────────────────────────────────────────────────

export type ChannelHardwareFilterId = "hardware_free" | "needs_hardware" | "all";

/** The default view leads with what a person can finish today. */
export const DEFAULT_CHANNEL_HARDWARE_FILTER: ChannelHardwareFilterId = "hardware_free";

/** Segment labels. Deliberately NOT "Works now": a hardware-free channel still
 *  wants a token pasted, and "works" would read as "already connected" beside
 *  a grid whose pills are the actual connection state. What genuinely differs
 *  between the two tiers is whether a computer has to exist first, so that is
 *  what the two labels say — and they say the same fact from both sides rather
 *  than one naming a state and the other naming a requirement. */
const FILTER_LABELS: Record<ChannelHardwareFilterId, string> = {
  hardware_free: "No computer needed",
  needs_hardware: "Needs a computer",
  all: "All",
};

export function channelHardwareFilterLabel(id: ChannelHardwareFilterId): string {
  return FILTER_LABELS[id];
}

export type ChannelHardwareFilterOption = {
  id: ChannelHardwareFilterId;
  label: string;
  count: number;
};

export type ChannelHardwareFilterPlan<T> = {
  /** EMPTY when there is nothing to filter between — and an empty list means
   *  render no control at all, not a control with one option. A filter of one
   *  is worse than no filter: it is a dead control (CLAUDE.md's product law),
   *  and it is the same call this codebase already makes for a rail of one
   *  agent and a table of one. */
  options: ChannelHardwareFilterOption[];
  /** What is actually in force — which is not always what was asked for. A
   *  persisted selection naming a tier that no longer has cards resolves to a
   *  view that does, rather than rendering an empty grid that looks broken. */
  selected: ChannelHardwareFilterId;
  visible: T[];
};

function tierMatchesFilter(tier: ChannelHardwareTier, filter: ChannelHardwareFilterId): boolean {
  if (filter === "all") return true;
  return tier === filter;
}

/**
 * Split the grid, and decide whether the split is worth showing.
 *
 * The control appears ONLY when both tiers are actually populated — on a grid
 * where every card is hardware-free (or every card needs a box) there is
 * nothing to choose between, and the honest rendering is no control.
 *
 * `unknown` deliberately joins NEITHER tier. It is a third fact — "this card's
 * ways in could not be determined" — and folding it into either one would be
 * the "two different facts sharing one signal" mistake this codebase keeps
 * re-discovering. It is reachable under "All", which is why "All" is offered
 * whenever it is the only place a card lives.
 */
export function planChannelHardwareFilter<T extends { tier: ChannelHardwareTier }>(
  cards: readonly T[],
  requested: ChannelHardwareFilterId = DEFAULT_CHANNEL_HARDWARE_FILTER,
): ChannelHardwareFilterPlan<T> {
  const count = (filter: ChannelHardwareFilterId) =>
    cards.filter((card) => tierMatchesFilter(card.tier, filter)).length;

  const free = count("hardware_free");
  const needs = count("needs_hardware");

  // Nothing to choose between: one tier (or none) carries the whole grid.
  if (free === 0 || needs === 0) {
    return { options: [], selected: "all", visible: [...cards] };
  }

  const options: ChannelHardwareFilterOption[] = (
    ["hardware_free", "needs_hardware", "all"] as ChannelHardwareFilterId[]
  ).map((id) => ({ id, label: FILTER_LABELS[id], count: count(id) }));

  const chosen = options.find((option) => option.id === requested && option.count > 0);
  const selected = chosen ? chosen.id : DEFAULT_CHANNEL_HARDWARE_FILTER;
  return {
    options,
    selected,
    visible: cards.filter((card) => tierMatchesFilter(card.tier, selected)),
  };
}

// ── THE CARD FACE, AND THE PANEL BEHIND A CARD THAT NEEDS A BOX ─────────────

/** The "Recommended" marker is only ever right on a card a person can finish
 *  today. The recommendation itself is an authored product judgment (see
 *  channel-popularity.ts's `CHANNEL_RECOMMENDED_KEYS` and the reasoning there
 *  for why a RANKING is allowed where a channel LIST is not); this is the
 *  guard that keeps it honest against the exact regression that already
 *  happened once — on 2026-08-20 Telegram's card silently became the
 *  hardware-bound transported one, and a "Recommended" badge would have been
 *  sitting on a card that told a cloud-only agent to go buy a computer. */
export function showsRecommendedBadge(
  recommended: boolean,
  tier: ChannelHardwareTier,
): boolean {
  return recommended && tier === "hardware_free";
}

export const CHANNEL_RECOMMENDED_BADGE = "Recommended";

/** What a card that needs a computer says to an agent that has none — a plain
 *  statement plus a REAL next step, never a control that cannot work.
 *
 *  `null` when the question does not arise, so a caller renders nothing rather
 *  than a reassurance nobody asked for. */
export type ChannelHardwareNextStep = { title: string; body: string; action: string };

export function channelHardwareNextStep(
  tier: ChannelHardwareTier,
  hasHardware: boolean,
): ChannelHardwareNextStep | null {
  if (tier !== "needs_hardware" || hasHardware) return null;
  return {
    title: "This one needs a computer",
    // The FACT, in the customer's terms. No mechanism: which process runs
    // where is not a thing anybody asked about, and the control below already
    // names the destination, so the sentence must not repeat it as an
    // instruction ("a professional tool labels; it does not lecture").
    body: "This agent doesn't have a computer of its own yet, and this channel runs on one.",
    action: "Set up a computer",
  };
}

/** Grid order BETWEEN the tiers: what a person can finish today comes first.
 *
 *  Only relevant under "All" (each single-tier view is uniform by
 *  construction), and it is what makes the founder's "lead with what works
 *  with no hardware" true there too rather than only in the default view.
 *  Returns 0 within a tier so the caller composes it with the popularity /
 *  recommended ordering rather than this module re-implementing either. */
const TIER_ORDER: Record<ChannelHardwareTier, number> = {
  hardware_free: 0,
  needs_hardware: 1,
  unknown: 2,
};

export function compareChannelsByHardwareTier(
  a: { tier: ChannelHardwareTier },
  b: { tier: ChannelHardwareTier },
): number {
  const rankA = TIER_ORDER[a.tier];
  const rankB = TIER_ORDER[b.tier];
  return rankA === rankB ? 0 : rankA < rankB ? -1 : 1;
}

/** THE grid order, composed once and exported once.
 *
 *  Three rules, each answering a different question and each still readable on
 *  its own: TIER (what can be finished today), then RECOMMENDED (the founder's
 *  single authored "start here"), then POPULARITY (how many people use the
 *  platform, not its first letter). The last two live in channel-popularity.ts
 *  and are reached through its own `compareChannelsForGrid` rather than
 *  reimplemented here.
 *
 *  It is ONE function rather than a composition spelled out at the call site
 *  because channel-hardware-tier.test.ts asserts the order — and a test that
 *  re-types the component's `a || b` chain is asserting its own copy of the
 *  rule, which stays green while the real grid reorders underneath it. That is
 *  the "fixture that invents its own input" failure this codebase keeps
 *  re-discovering; here the expected order and the rendered order come out of
 *  the same function. */
export function compareChannelGridCards(
  a: { tier: ChannelHardwareTier; label: string },
  b: { tier: ChannelHardwareTier; label: string },
): number {
  return compareChannelsByHardwareTier(a, b) || compareChannelsForGrid(a, b);
}

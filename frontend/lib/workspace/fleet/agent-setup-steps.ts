/**
 * WHAT IS STILL UNFINISHED ABOUT THIS AGENT — the decision behind the band
 * that renders under an agent's name right after it is created.
 *
 * THE PROBLEM IT EXISTS FOR. Agent creation is deliberately two fields — a
 * name and an optional system prompt (AgentCreateCard.tsx's own header
 * carries the founder's words: "probably its name and possibly some system
 * prompt or something like this"). The 4-step wizard that used to ask for
 * placement/brain/channels/connections was deleted on purpose and MUST NOT
 * come back. But the result, reviewed live, was that creating an agent
 * landed the person on an empty observation surface reading "No
 * conversations yet" with no path to anything: everything the wizard used to
 * ask had become invisible rather than deferred.
 *
 * So this is the deferral made visible. Not a gate, not an approval, not a
 * checklist that blocks anything — the honest state of an agent that is not
 * set up yet, with one click to each place that fixes it.
 *
 * ── WHEN THE BAND EXISTS AT ALL: while the agent CANNOT BE REACHED ────────
 *
 * The whole band is gated on "zero channels connected", not on "some step
 * is outstanding", and that is the difference between a next-actions panel
 * and a nag. Chat is gone from the platform (founder, 2026-08-19/20:
 * "messaging would never be done inside this platform... go to Telegram and
 * speak with the agent inside that channel"), so an agent with no channel is
 * literally unreachable by anybody — that, and only that, is the state worth
 * putting a band on screen for. A cloud-only agent with a channel and no
 * connectors is a perfectly finished agent and sees nothing.
 *
 *   0 channels connected  ─▶ the band, listing every outstanding step
 *   1+ channels connected ─▶ NOTHING. the agent is reachable; the rest is
 *                            ordinary configuration, one click away in
 *                            Configure like any other setting.
 *   still loading         ─▶ NOTHING. "no channel connected" and "I have not
 *                            asked yet" are different facts and may never
 *                            share one screen (CLAUDE.md's outcome-honesty
 *                            law). The channels fetch returns [] in both.
 *
 * ── NO DEAD CONTROLS, and this is where the rule actually bites ───────────
 *
 * Every step must be completable FROM THE STATE THE AGENT IS IN.
 *
 *   · The channel step is suppressed when this agent has no connectable
 *     door at all — a cloud-only agent whose only remaining channels each
 *     need a computer gets the COMPUTER step instead, which is the thing
 *     that actually unblocks it. Tiering is reused from
 *     channel-hardware-tier.ts (the same `channelHardwareTier` the Channels
 *     grid filters by), never re-derived here and never a hardcoded channel
 *     name.
 *   · The connectors step is suppressed without a project. ConnectorsTab
 *     renders "No project assigned" and no picker without `agent.project_id`
 *     — sending someone there would be a control that submits nothing.
 *   · Nothing routes to the `context` tab. It is in CONFIGURE_GROUPS but
 *     absent from both [tab]/page.tsx `VALID_TABS` whitelists, so the URL
 *     coerces to "chat" and the sheet closes instead of opening. Adding a
 *     Context step here would be a link that visibly does the wrong thing.
 *
 * ── TWO STEPS DELIBERATELY DO NOT EXIST. Do not add them back. ────────────
 *
 * MODEL. There is no honest "no model configured" state to report.
 * `seed_specialist_metadata` (fleet_tools.py) stamps every new agent with a
 * real, working `{mode: "platform_credits", model: "deepseek-v4-pro"}`, and
 * `resolve_model_config` defaults a missing mode to platform_credits on
 * read. A "pick a model" step would therefore be permanent (nothing the
 * person does makes it disappear, because nothing was ever missing) — the
 * exact dead control this file exists to avoid. Changing the model is real,
 * and it lives in Configure → Model like every other setting.
 *
 * PERSONA. Same shape, one level subtler. AgentCreateCard always sends
 * `purpose_preset: "internal_assistant"`, and fleet_tools.py's create path
 * falls back to `_PURPOSE_PRESET_INSTRUCTIONS[preset]` whenever the typed
 * instructions are empty — so `agent.instructions` is NEVER blank for a
 * card-created agent and a `!instructions` step would never render for the
 * one agent it was written to help. Detecting "still the seeded default"
 * would mean transcribing the backend's prose into the frontend, which is
 * the copied-list defect this codebase already has four instances of.
 *
 * ── PURE, IN ITS OWN MODULE ───────────────────────────────────────────────
 *
 * No React, no CSS import, no "use client" — same discipline as
 * agent-count-shape.ts and channel-doors.ts, so agent-setup-steps.test.ts
 * runs under plain `npx tsx` and drives the REAL rule FleetAgentDetail
 * renders against instead of re-typing it.
 */

import { CHANNEL_DOORS } from "./channel-doors";
import { channelHardwareTier, showsRecommendedBadge, type ChannelHardwareTier } from "./channel-hardware-tier";
import { isChannelRecommended } from "./channel-popularity";

/** The Configure/Profile route segment a step opens. Every value here is in
 *  BOTH `[tab]/page.tsx` VALID_TABS whitelists — see the `context` note in
 *  this file's header for why that matters. */
export type AgentSetupStepTab = "channels" | "hardware" | "connectors";

export type AgentSetupStepId = "channel" | "hardware" | "connectors";

export type AgentSetupStep = {
  id: AgentSetupStepId;
  /** Where the step goes. The caller turns this into `…/agents/{id}/{tab}`,
   *  which opens the Configure sheet on that section directly — the sheet is
   *  derived from the URL segment on every render, so a deep link lands on
   *  the right section on first paint rather than after a second click. */
  tab: AgentSetupStepTab;
  label: string;
  /** A FACT about the current state, never an instruction. Empty string when
   *  there is no fact worth the line. */
  hint: string;
  /** Exactly one step is primary — the view's single accent action
   *  (CLAUDE.md: "two accent-filled buttons in one view is a bug"). */
  primary: boolean;
};

/** The channel rows this rule needs, i.e. the subset of `FleetChannel` that
 *  actually decides anything here. Narrow on purpose so the test can build
 *  rows without inventing the rest of the type. */
export type AgentSetupChannelRow = {
  id: string;
  label: string;
  connected: boolean;
  requiresGateway: boolean;
  setupAvailable: boolean;
};

/**
 * Which tier a channel connects at, for THIS decision.
 *
 * Prefers the authored doors — the same `CHANNEL_DOORS` the Channels grid
 * tiers by, through the same `channelHardwareTier` function, so the band and
 * the grid can never disagree about whether Telegram needs a computer. Falls
 * back to the backend's own `requiresGateway` when a channel has no authored
 * doors, which is every transported (OpenClaw) channel: those are derived
 * from the manifest at render time in the grid, and a band that had to
 * rebuild that derivation would be a second copy of it.
 */
export function agentSetupChannelTier(row: AgentSetupChannelRow): ChannelHardwareTier {
  const authored = channelHardwareTier(CHANNEL_DOORS[row.id] ?? []);
  if (authored !== "unknown") return authored;
  return row.requiresGateway ? "needs_hardware" : "hardware_free";
}

/**
 * The channel this agent should be pointed at first, and its tier — used
 * only to write the channel step's hint, never to skip the grid. Null when
 * nothing is connectable, which is what suppresses the step entirely.
 *
 * "Recommended" is gated through `showsRecommendedBadge`, not read straight
 * off `isChannelRecommended`: a recommendation sitting on a channel that
 * needs a computer this agent does not have is exactly the regression that
 * shipped on 2026-08-20, and that guard exists to stop it.
 */
export function preferredSetupChannel(
  channels: readonly AgentSetupChannelRow[],
  hasHardware: boolean,
): { row: AgentSetupChannelRow; tier: ChannelHardwareTier; recommended: boolean } | null {
  const connectable = channels
    .filter((row) => !row.connected && row.setupAvailable)
    .map((row) => ({ row, tier: agentSetupChannelTier(row) }))
    .filter(({ tier }) => tier === "hardware_free" || hasHardware);
  if (connectable.length === 0) return null;
  const recommended = connectable.find(
    ({ row, tier }) => showsRecommendedBadge(isChannelRecommended(row.label), tier),
  );
  const hardwareFree = connectable.find(({ tier }) => tier === "hardware_free");
  const pick = recommended ?? hardwareFree ?? connectable[0];
  return { ...pick, recommended: Boolean(recommended) && pick === recommended };
}

export type AgentSetupInput = {
  /** False while the channels fetch is still in flight or came back an
   *  error — the band renders nothing rather than claiming zero. */
  channelsKnown: boolean;
  channels: readonly AgentSetupChannelRow[];
  /** How many channels are actually connected, resolved by the caller
   *  through `isChannelConnected` (Slack and hosted Telegram each answer
   *  this from a second field, not from `connected` alone). */
  connectedChannelCount: number;
  /** `agent.hardware_access` — "none" is a REAL answer (a deliberately
   *  cloud-only agent), "" is "not told", and the two are not the same. */
  hardwareAccess: string;
  /** Whether a usable box is actually resolved for this agent right now. */
  hardwareBound: boolean;
  connectorsKnown: boolean;
  connectedConnectorCount: number;
  /** ConnectorsTab needs one; without it there is no picker to reach. */
  hasProject: boolean;
};

/**
 * The whole rule. Returns [] — render nothing at all — whenever the agent is
 * reachable, or whenever we do not yet know whether it is.
 */
export function planAgentSetupSteps(input: AgentSetupInput): AgentSetupStep[] {
  if (!input.channelsKnown) return [];
  if (input.connectedChannelCount > 0) return [];

  // "none" is an explicit cloud-only choice; "" means nothing told us, and
  // guessing "none" there would offer a computer to an agent that has one.
  const cloudOnly = input.hardwareAccess === "none";
  const hasHardware = !cloudOnly && input.hardwareBound;

  const steps: AgentSetupStep[] = [];

  const channel = preferredSetupChannel(input.channels, hasHardware);
  if (channel) {
    steps.push({
      id: "channel",
      tab: "channels",
      label: "Connect a channel",
      hint: channel.recommended ? `${channel.row.label} needs no computer` : "Nobody can reach it yet",
      primary: true,
    });
  }

  if (cloudOnly) {
    steps.push({
      id: "hardware",
      tab: "hardware",
      label: "Give it a computer",
      hint: "Cloud only right now",
      primary: steps.length === 0,
    });
  } else if (input.hardwareAccess && !input.hardwareBound) {
    steps.push({
      id: "hardware",
      tab: "hardware",
      // A different problem from the offer above — this agent is CONFIGURED
      // to use a computer and does not have one resolved, which is broken
      // rather than optional. Two facts, two labels.
      label: "Pick its computer",
      hint: "No computer bound yet",
      primary: steps.length === 0,
    });
  }

  if (input.connectorsKnown && input.connectedConnectorCount === 0 && input.hasProject) {
    steps.push({
      id: "connectors",
      tab: "connectors",
      label: "Connect your tools",
      hint: "",
      primary: steps.length === 0,
    });
  }

  return steps;
}

/** The band's own heading. A name, not a sentence — and never "N of 3
 *  complete", which would turn an offer into a score. */
export function agentSetupHeading(agentName: string): string {
  const name = agentName.trim();
  return name ? `Finish setting up ${name}` : "Finish setting up this agent";
}

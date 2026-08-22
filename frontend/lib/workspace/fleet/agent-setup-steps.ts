/**
 * WHAT IS STILL UNFINISHED ABOUT THIS AGENT — the decision behind the band
 * that renders under an agent's name right after it is created.
 *
 * ── CORRECTED 2026-08-21, and the correction reverses this file's own
 *    original premise. Read this before trusting anything below. ──────────
 *
 * The paragraph that used to sit here said the 4-step creation wizard "was
 * deleted on purpose and MUST NOT come back", and that this band was the
 * deferral made visible: one row per outstanding thing, each a link into
 * Configure. The founder saw that live and rejected it, twice:
 *
 *   *"it acts like a button, not step-by-step... if you want press this
 *    button and set up your hardware, if you want this if you want that —
 *    I don't want to have that."*
 *
 * So the SEQUENCE is back, inside the creation surface itself
 * (agent-create-wizard.ts / AgentCreateCard.tsx): Identity → Model →
 * Channel → Apps, minus the old wizard's project/placement step. Channels
 * and apps are now asked for AT CREATION, in order, not offered as a
 * menu afterwards.
 *
 * ── WHAT THIS FILE IS FOR NOW ────────────────────────────────────────────
 *
 * The one case the sequence cannot cover: somebody skipped the Channel step
 * (deliberately allowed — a person with no credential to hand must be able
 * to move on) and their agent is therefore unreachable. Deleting this band
 * outright would put that agent straight back in the dead end this file was
 * originally written to fix.
 *
 * But it renders exactly ONE control now, never a row of them — see
 * agentSetupNextStep. A menu of optional links is the shape he rejected; the
 * single next thing that unblocks the agent is not a menu, it is the rest of
 * the sequence. planAgentSetupSteps below still computes the whole ordered
 * list (it is what decides WHICH one is next, and its reasoning about dead
 * controls is unchanged and still load-bearing) — the caller renders the
 * head of it.
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

import { CHANNEL_DOORS, CHANNEL_GRID_PLATFORMS } from "./channel-doors";
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
 * The PLATFORM name for a channel — the label the Channels grid shows, not
 * the sentence the backend sends.
 *
 * FOUND LIVE, and it is the "a fixture that invents its own input" failure
 * this codebase already documents four times: `FleetChannel.label` for the
 * hosted Telegram lane is the whole phrase "Talk to your agent on Telegram",
 * so `isChannelRecommended` — which normalises a label and matches its whole
 * form or its LEADING word — sees `talktoyouragentontelegram` / `talk` and
 * matches nothing. The recommendation silently never attached. The grid does
 * not have this problem because it renders `CHANNEL_GRID_PLATFORMS`' own
 * platform labels, so that is the map read here too. Falls back to the row's
 * own label for anything not in the grid's list.
 */
export function agentSetupChannelPlatformLabel(row: AgentSetupChannelRow): string {
  return CHANNEL_GRID_PLATFORMS.find((p) => p.id === row.id)?.label ?? row.label;
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
): {
  row: AgentSetupChannelRow;
  tier: ChannelHardwareTier;
  platformLabel: string;
  recommended: boolean;
} | null {
  const connectable = channels
    .filter((row) => !row.connected && row.setupAvailable)
    .map((row) => ({
      row,
      tier: agentSetupChannelTier(row),
      platformLabel: agentSetupChannelPlatformLabel(row),
    }))
    .filter(({ tier }) => tier === "hardware_free" || hasHardware);
  if (connectable.length === 0) return null;
  const recommended = connectable.find(({ platformLabel, tier }) =>
    showsRecommendedBadge(isChannelRecommended(platformLabel), tier),
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
      hint: channel.recommended ? `${channel.platformLabel} needs no computer` : "Nobody can reach it yet",
      primary: true,
    });
  }

  if (cloudOnly) {
    steps.push({
      id: "hardware",
      tab: "hardware",
      label: "Give it a computer",
      hint: "Runs in the cloud",
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
      label: "Connect your apps",
      hint: "",
      primary: steps.length === 0,
    });
  }

  return steps;
}

/**
 * THE ONE control the band renders — the next thing that actually unblocks
 * this agent, or null when there is nothing worth saying.
 *
 * `planAgentSetupSteps` orders the steps by which most unblocks the agent
 * and marks the first `primary`; this returns that one. Rendering the rest
 * beside it is what produced *"if you want this if you want that"* — a menu
 * of optional links, which is not setup. One control is the continuation of
 * the creation sequence, not an alternative to it.
 *
 * Deliberately NOT a fifth judgement of its own: it reads the same ordered
 * plan, so the band and the plan can never disagree about what comes next.
 */
export function agentSetupNextStep(input: AgentSetupInput): AgentSetupStep | null {
  return planAgentSetupSteps(input).find((step) => step.primary) ?? null;
}

/** The band's own heading. A name, not a sentence — and never "N of 3
 *  complete", which would turn an offer into a score. */
export function agentSetupHeading(agentName: string): string {
  const name = agentName.trim();
  return name ? `Finish setting up ${name}` : "Finish setting up this agent";
}

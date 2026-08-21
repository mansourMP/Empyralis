/**
 * agent-setup-steps.ts — the "what is still unfinished about this agent"
 * rule, driven directly rather than re-typed.
 *
 * Expected values here are HAND-WRITTEN; the derivation composes
 * channel-hardware-tier.ts's real `channelHardwareTier` over channel-doors.ts's
 * real `CHANNEL_DOORS` and channel-popularity.ts's real recommendation, so the
 * expected set and the actual set come from two different places — the house
 * rule that a self-confirming test is worth nothing.
 *
 * Run: npx tsx lib/workspace/fleet/agent-setup-steps.test.ts
 */

import {
  agentSetupChannelPlatformLabel,
  agentSetupChannelTier,
  agentSetupHeading,
  planAgentSetupSteps,
  preferredSetupChannel,
  type AgentSetupChannelRow,
  type AgentSetupInput,
} from "./agent-setup-steps";
import { CHANNEL_DOORS } from "./channel-doors";

let passed = 0;
let failed = 0;

function assert(condition: boolean, label: string): void {
  if (condition) {
    passed++;
  } else {
    failed++;
    console.error(`FAIL: ${label}`);
  }
}

// FIXTURES BUILT FROM THE REAL PRODUCER, not from what the argument seems to
// need. These `label` values are the exact strings a live
// GET /fleet/agent-channels returned on a disposable stack on 2026-08-21 —
// captured from the running backend, not typed from memory. It matters: the
// hosted Telegram lane's label is the whole SENTENCE "Talk to your agent on
// Telegram", and an earlier version of this file used "Telegram" here, which
// made the recommendation attach in the test and silently never attach in
// production (channelPopularityKeys normalises the whole label and its
// LEADING word — "talktoyouragentontelegram" / "talk", neither of which is
// "telegram"). Caught by driving the real screen; see
// agentSetupChannelPlatformLabel. Do not "tidy" these labels.
function channel(over: Partial<AgentSetupChannelRow> = {}): AgentSetupChannelRow {
  return {
    id: "sage_telegram_hosted",
    label: "Talk to your agent on Telegram",
    connected: false,
    requiresGateway: false,
    setupAvailable: true,
    ...over,
  };
}

const TELEGRAM = channel();
const WHATSAPP = channel({ id: "openclaw_whatsapp", label: "WhatsApp", requiresGateway: true });

// ── The platform label, which is what the recommendation is keyed on ──────
assert(
  agentSetupChannelPlatformLabel(TELEGRAM) === "Telegram",
  `the hosted Telegram lane resolves to the grid's own platform label — got "${agentSetupChannelPlatformLabel(TELEGRAM)}"`,
);
assert(
  agentSetupChannelPlatformLabel(channel({ id: "slack", label: "Slack" })) === "Slack",
  "a channel already named after its platform is unchanged",
);
assert(
  agentSetupChannelPlatformLabel(WHATSAPP) === "WhatsApp",
  "a channel outside the grid's platform list keeps its own label rather than losing one",
);
// The regression itself, pinned: reading the RAW label is what silently
// dropped the recommendation in production.
assert(
  agentSetupChannelPlatformLabel(TELEGRAM) !== TELEGRAM.label,
  "…and the raw backend label is genuinely different, which is why this function exists",
);

function input(over: Partial<AgentSetupInput> = {}): AgentSetupInput {
  return {
    channelsKnown: true,
    channels: [TELEGRAM, WHATSAPP],
    connectedChannelCount: 0,
    hardwareAccess: "none",
    hardwareBound: false,
    connectorsKnown: true,
    connectedConnectorCount: 0,
    hasProject: true,
    ...over,
  };
}

const ids = (steps: { id: string }[]) => steps.map((s) => s.id).join(",");

// ── Tiering is COMPOSED from the grid's own rule, never re-derived ────────
assert(
  agentSetupChannelTier(TELEGRAM) === "hardware_free",
  "Telegram's authored door needs no computer — the same answer the Channels grid gives",
);
assert(
  agentSetupChannelTier(WHATSAPP) === "needs_hardware",
  "a transported channel has no authored door, so requiresGateway decides — and it needs a box",
);
assert(
  agentSetupChannelTier(channel({ id: "slack", label: "Slack" })) === "hardware_free",
  "Slack's authored door needs no computer either",
);
// The composition claim itself, proven rather than assumed: every authored
// first-party door set must tier identically here and in the grid's own
// function. If this drifts, a second rule has crept in.
for (const id of Object.keys(CHANNEL_DOORS)) {
  const viaDoors = CHANNEL_DOORS[id].filter((d) => d.real).some((d) => !d.requiresHardware);
  assert(
    agentSetupChannelTier(channel({ id, requiresGateway: true })) === (viaDoors ? "hardware_free" : "needs_hardware"),
    `${id}: the authored doors decide the tier, not the backend's requiresGateway fallback`,
  );
}

// ── The band exists ONLY while the agent cannot be reached ────────────────
assert(planAgentSetupSteps(input({ channelsKnown: false })).length === 0, "nothing is claimed while the channels fetch is still in flight");
assert(planAgentSetupSteps(input({ connectedChannelCount: 1 })).length === 0, "one connected channel makes the agent reachable — the band vanishes entirely");
assert(
  planAgentSetupSteps(input({ connectedChannelCount: 1, hardwareAccess: "none", connectedConnectorCount: 0 })).length === 0,
  "a reachable cloud-only agent with no connectors is FINISHED — it is never nagged about optional configuration",
);
assert(planAgentSetupSteps(input()).length > 0, "an unreachable agent does get the band");

// ── A brand-new agent: exactly the three real next actions, channel first ─
assert(
  ids(planAgentSetupSteps(input())) === "channel,hardware,connectors",
  `a fresh agent's steps, in order — got ${ids(planAgentSetupSteps(input()))}`,
);
const fresh = planAgentSetupSteps(input());
assert(fresh.filter((s) => s.primary).length === 1, "exactly one step is primary — one accent action per view");
assert(fresh[0].primary && fresh[0].id === "channel", "the channel is the primary action: with chat gone, it is the only way to reach the agent");
assert(
  fresh.map((s) => s.tab).join(",") === "channels,hardware,connectors",
  "every step routes to a Configure section that exists in both [tab]/page.tsx VALID_TABS whitelists",
);
assert(
  !fresh.some((s) => (s.tab as string) === "context"),
  "nothing routes to `context` — it is absent from VALID_TABS, so the URL coerces to chat and the sheet closes",
);
assert(
  fresh[0].hint === "Telegram needs no computer",
  `the channel hint names the RECOMMENDED hardware-free channel by its platform name — got "${fresh[0].hint}"`,
);

// ── Steps disappear one at a time as each thing gets done ─────────────────
assert(
  ids(planAgentSetupSteps(input({ connectedConnectorCount: 2 }))) === "channel,hardware",
  "connecting a tool removes only the connectors step",
);
assert(
  ids(planAgentSetupSteps(input({ hardwareAccess: "gateway", hardwareBound: true }))) === "channel,connectors",
  "giving it a computer removes only the hardware step",
);
assert(
  ids(planAgentSetupSteps(input({ hardwareAccess: "gateway", hardwareBound: true, connectedConnectorCount: 1 }))) === "channel",
  "with hardware and tools done, only the channel remains",
);

// ── NO DEAD CONTROLS ──────────────────────────────────────────────────────
// A cloud-only agent whose only remaining channels each need a computer must
// NOT be offered "connect a channel" — the computer step is the thing that
// actually unblocks it, and it becomes primary in its place.
const boxedIn = planAgentSetupSteps(input({ channels: [WHATSAPP] }));
assert(ids(boxedIn) === "hardware,connectors", `a cloud-only agent is not offered a channel it cannot connect — got ${ids(boxedIn)}`);
assert(boxedIn[0].id === "hardware" && boxedIn[0].primary, "the computer step takes the accent when it is the real blocker");
// …and the moment that agent HAS a computer, the same channel is offerable.
const withBox = planAgentSetupSteps(input({ channels: [WHATSAPP], hardwareAccess: "gateway", hardwareBound: true }));
assert(ids(withBox) === "channel,connectors", "with a computer bound, a hardware-requiring channel becomes a real next action");
assert(withBox[0].hint === "Nobody can reach it yet", "a non-recommended pick states the plain fact instead of naming a channel");

// A channel the backend says cannot be set up on this deployment is not a
// door at all — `setupAvailable: false` (e.g. an OAuth client id/secret
// unset) must never produce a step that submits nothing.
assert(
  ids(planAgentSetupSteps(input({ channels: [channel({ setupAvailable: false })] }))) === "hardware,connectors",
  "a channel the deployment cannot set up is not offered",
);
assert(preferredSetupChannel([], false) === null, "no channels at all means no channel step");
assert(
  preferredSetupChannel([channel({ connected: true })], false) === null,
  "an already-connected channel is not a next action",
);

// Connectors need a project — ConnectorsTab renders no picker without one.
assert(
  ids(planAgentSetupSteps(input({ hasProject: false }))) === "channel,hardware",
  "no project means no connectors step — the destination has no picker to reach",
);
assert(
  ids(planAgentSetupSteps(input({ connectorsKnown: false }))) === "channel,hardware",
  "the connectors step waits for its own fetch rather than claiming zero",
);

// ── "none" and "" are different facts about hardware ─────────────────────
assert(
  planAgentSetupSteps(input()).some((s) => s.id === "hardware" && s.label === "Give it a computer"),
  "an explicitly cloud-only agent is OFFERED a computer",
);
assert(
  planAgentSetupSteps(input({ hardwareAccess: "gateway", hardwareBound: false })).some(
    (s) => s.id === "hardware" && s.label === "Pick its computer",
  ),
  "an agent configured for hardware with none resolved is BROKEN, and says so differently",
);
assert(
  !planAgentSetupSteps(input({ hardwareAccess: "", hardwareBound: false })).some((s) => s.id === "hardware"),
  "an unknown hardware_access ('' — nothing told us) never guesses 'none' and offers a computer",
);

// ── Two steps that must never come back ───────────────────────────────────
// MODEL and PERSONA both have real seeded defaults on every created agent
// (fleet_tools.py's seed_specialist_metadata and _PURPOSE_PRESET_INSTRUCTIONS),
// so a step for either could never be completed away — the exact dead control
// this module exists to avoid. See agent-setup-steps.ts's header.
const everyId = new Set(
  [
    input(),
    input({ hardwareAccess: "gateway", hardwareBound: true }),
    input({ channels: [WHATSAPP] }),
    input({ hasProject: false }),
  ].flatMap((i) => planAgentSetupSteps(i).map((s) => s.id as string)),
);
assert(!everyId.has("model"), "there is no model step: every new agent already has a working seeded model");
assert(!everyId.has("persona"), "there is no persona step: the create path seeds instructions from purpose_preset, so it is never blank");

// ── The heading ───────────────────────────────────────────────────────────
assert(agentSetupHeading("Scout") === "Finish setting up Scout", "the heading names the agent");
assert(agentSetupHeading("   ") === "Finish setting up this agent", "a blank name still reads as a sentence, never 'Finish setting up '");
assert(!/\d/.test(agentSetupHeading("Scout")), "the heading is never a score — no 'N of 3 complete'");

// ── Wired, not just built ─────────────────────────────────────────────────
// The house failure mode is complete, tested code with zero callers.
import { readFileSync } from "node:fs";
const detailSource = readFileSync(new URL("./FleetAgentDetail.tsx", import.meta.url), "utf8");
assert(
  detailSource.includes('from "./agent-setup-steps"'),
  "FleetAgentDetail imports the real rule module",
);
assert(/planAgentSetupSteps\s*\(/.test(detailSource), "FleetAgentDetail plans the steps from planAgentSetupSteps, not an inline re-check");
assert(/agentSetupHeading\s*\(/.test(detailSource), "FleetAgentDetail renders the heading from agentSetupHeading");
assert(
  /fleet-agent-setup/.test(detailSource),
  "FleetAgentDetail actually renders the band's markup",
);
const themeSource = readFileSync(new URL("./fleet-theme.css", import.meta.url), "utf8");
assert(/\.fleet-agent-setup\b/.test(themeSource), "the band has real styles, not inherited ones");
// A canary: if this file ever stops being able to read its own subjects, that
// is its own reported failure rather than a silent green.
assert(detailSource.length > 1000 && themeSource.length > 1000, "CANARY: both scanned sources were actually read");

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}

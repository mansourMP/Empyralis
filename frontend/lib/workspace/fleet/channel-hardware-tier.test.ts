/**
 * THE TWO-TIER CHANNEL SPLIT, ASSERTED AGAINST THE REAL GRID.
 *
 * The founder's requirement, verbatim from CLAUDE.md ("Channels: Telegram +
 * Slack. Discord is OUT."):
 *
 *     Works now, nothing to install     Telegram · Slack
 *     Needs your computer paired        WhatsApp · Signal · iMessage · ~20 more
 *
 * and: *"a 'recommended' stamp alone is not enough — the split is the honest
 * information"*, with Telegram first and marked Recommended.
 *
 * WHY A UNIT TEST CAN CATCH THIS AT ALL
 * -------------------------------------
 * This repo has no headless-DOM harness (no jsdom/RTL — see package.json), so
 * the grid itself cannot be rendered here. It does not need to be: the grid is
 * `[...firstParty, ...transported]` sorted and filtered by pure functions over
 * pure data, and every claim below is a property of those functions and that
 * data. The same reason channel-platform.test.ts works.
 *
 * EXPECTED AND ACTUAL COME FROM DIFFERENT SOURCES
 * -----------------------------------------------
 *   1. THE REQUIREMENT — the platform names in the split above, which are the
 *      founder's own words in CLAUDE.md. Deliberately written out here: this
 *      is the test's EXPECTATION, and an expectation has to be stated
 *      somewhere or the check derives its answer from the thing it checks and
 *      can only ever confirm itself (CLAUDE.md's own rule for
 *      preflight._check_rls).
 *   2. THE DERIVATION — `channelHardwareTier` over the REAL `CHANNEL_DOORS`
 *      and the REAL `groupTransportedChannels`, i.e. the exact functions
 *      ChannelsTab calls. Nothing is re-typed from either.
 *   3. `server_modules/openclaw_channel_manifest.json` — the checked-in
 *      manifest, GENERATED from a pinned OpenClaw install. Upstream's own
 *      channel ids and display names.
 *
 * Run: npx tsx lib/workspace/fleet/channel-hardware-tier.test.ts
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import {
  CHANNEL_GRID_PLATFORMS,
  groupTransportedChannels,
  planChannelDoors,
  planDoors,
  type ChannelDoor,
  type TransportedChannelInput,
} from "./channel-doors";
import { planUnifiedChannelGrid } from "./channel-platform";
import { GENERATED_OPENCLAW_ACTIVE_CHANNEL_IDS } from "../../../../empyralis-gateway/src/openclaw/generated-openclaw-channels";
import { isChannelRecommended } from "./channel-popularity";
import {
  CHANNEL_RECOMMENDED_BADGE,
  channelHardwareFilterLabel,
  channelHardwareNextStep,
  channelHardwareTier,
  compareChannelGridCards,
  DEFAULT_CHANNEL_HARDWARE_FILTER,
  planChannelHardwareFilter,
  showsRecommendedBadge,
  type ChannelHardwareFilterId,
  type ChannelHardwareTier,
} from "./channel-hardware-tier";

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

const repoRoot = join(__dirname, "..", "..", "..", "..");

// --- Source 3: the generated manifest. ------------------------------------

type ManifestChannel = {
  id: string;
  channel_key: string;
  label: string;
  credential_shape?: {
    selection_label?: string;
    connect_method?: "credential" | "pairing" | "plugin_absent";
  } | null;
};

const manifest = JSON.parse(
  readFileSync(join(repoRoot, "server_modules", "openclaw_channel_manifest.json"), "utf8"),
) as { channels: ManifestChannel[] };

// CANARY. Everything below is a claim about a real channel set; a manifest
// that failed to load must fail loudly rather than let an empty sweep report
// "passed" (CLAUDE.md: a check that enforces nothing still says green).
assert(
  Array.isArray(manifest.channels) && manifest.channels.length >= 20,
  `CANARY: the checked-in manifest loaded with a real channel set (got ${manifest.channels?.length})`,
);

const transportInputs: TransportedChannelInput[] = manifest.channels.map((channel) => ({
  channel_key: channel.channel_key,
  channel_id: channel.id,
  label: channel.label,
  selection_label: channel.credential_shape?.selection_label || channel.label,
  connect_method: channel.credential_shape?.connect_method || "credential",
}));

const transportedPlatforms = groupTransportedChannels(transportInputs);
assert(transportedPlatforms.length > 0, "CANARY: the manifest groups into real transported platforms");

// --- THE RULE ITSELF, on synthetic doors. --------------------------------

const freeDoor: ChannelDoor = { key: "a", label: "A", body: "", real: true };
const boxDoor: ChannelDoor = { key: "b", label: "B", body: "", real: true, requiresHardware: true };
const unrealFreeDoor: ChannelDoor = { key: "c", label: "C", body: "", real: false };

assert(channelHardwareTier([]) === "unknown", "no doors at all is an honest unknown, never a guessed tier");
assert(channelHardwareTier([freeDoor]) === "hardware_free", "one door needing nothing is hardware_free");
assert(channelHardwareTier([boxDoor]) === "needs_hardware", "one door needing a box is needs_hardware");
assert(
  channelHardwareTier([boxDoor, freeDoor]) === "hardware_free",
  "a platform reachable one way without a box belongs in the tier a person can act on today",
);
assert(
  channelHardwareTier([boxDoor, unrealFreeDoor]) === "needs_hardware",
  "a door that is not real cannot make a platform look connectable — planDoors' own rule, same filter",
);
assert(
  channelHardwareTier([unrealFreeDoor]) === "unknown",
  "only unreal doors is the same fact as no doors: unknown",
);

// --- THE REQUIREMENT, against the REAL first-party doors. -----------------
//
// Source 1 (the names, from CLAUDE.md) meets source 2 (the derivation).

const HARDWARE_FREE_BY_REQUIREMENT = ["Telegram", "Slack"];

for (const platform of CHANNEL_GRID_PLATFORMS) {
  const tier = channelHardwareTier(planChannelDoors(platform.id).doors);
  if (HARDWARE_FREE_BY_REQUIREMENT.includes(platform.label)) {
    assert(
      tier === "hardware_free",
      `${platform.label} is connectable with no computer (the founder's own split) — derived ${tier}`,
    );
  }
  assert(tier !== "unknown", `first-party ${platform.label} resolves to a real tier, never "unknown"`);
}

assert(
  CHANNEL_GRID_PLATFORMS.some((platform) => platform.id === "sage_telegram_hosted"),
  "CANARY: the hardware-free Telegram lane is still the first-party Telegram card (it was silently dropped once, 2026-08-20)",
);

// --- The transported half: needing the box is a property of the LANE. ----
//
// Every channel the transport carries runs on the agent's own computer,
// whichever way it is set up — the credential ones are pasted from here but
// land in the transport's config ON that box. Asserted across the WHOLE
// manifest rather than a sample, so a channel upstream ships tomorrow is
// covered without this file naming it.

for (const platform of transportedPlatforms) {
  assert(
    channelHardwareTier(platform.doors) === "needs_hardware",
    `transported ${platform.label} needs the agent's own computer`,
  );
  assert(
    platform.doors.every((door) => door.requiresHardware),
    `every way into transported ${platform.label} says so on its own door face, before the pick`,
  );
}

const NEEDS_BOX_BY_REQUIREMENT = ["WhatsApp", "Signal", "iMessage"];
for (const label of NEEDS_BOX_BY_REQUIREMENT) {
  const platform = transportedPlatforms.find((candidate) => candidate.label.toLowerCase() === label.toLowerCase());
  assert(Boolean(platform), `CANARY: ${label} is a real platform in the manifest (source of the expectation)`);
  if (platform) {
    assert(
      channelHardwareTier(platform.doors) === "needs_hardware",
      `${label} needs your computer paired (the founder's own split)`,
    );
  }
}

// --- A channel upstream ships tomorrow is tiered with NO edit here. ------
//
// The only way to prove a derivation is not a disguised list is to feed it
// something the list could not contain. Same proof channel-popularity.ts's
// unranked-channel assertion makes.

const futurePlatforms = groupTransportedChannels([
  ...transportInputs,
  {
    channel_key: "openclaw_perfectlynovelmessenger",
    channel_id: "perfectlynovelmessenger",
    label: "Perfectly Novel Messenger",
    selection_label: "Perfectly Novel Messenger (Bot API)",
    connect_method: "credential",
  },
]);
const futureCard = futurePlatforms.find((platform) => platform.label === "Perfectly Novel Messenger");
assert(Boolean(futureCard), "CANARY: the synthetic future channel became its own card");
assert(
  futureCard !== undefined && channelHardwareTier(futureCard.doors) === "needs_hardware",
  "a channel the transport ships tomorrow is tiered correctly with no code change here",
);

// --- THE REAL GRID: the cards ChannelsTab actually builds. ---------------
//
// The transported HALF of the grid is not the whole manifest — it is the
// backend's ACTIVE catalog, a channel the transport OWNS (one no first-party
// runtime still owns). Read from the generated gateway module, a fourth
// independent source, so the ordering and badge claims below are about the
// grid a customer sees rather than about a superset that includes cards the
// tab never renders (the transported Telegram, Slack, Discord and SMS rows
// are all superseded upstream and never reach it).

const activeChannelIds = new Set(GENERATED_OPENCLAW_ACTIVE_CHANNEL_IDS);
assert(activeChannelIds.size > 0, "CANARY: the generated active-channel set loaded");
assert(
  transportInputs.some((entry) => activeChannelIds.has(entry.channel_id)),
  "CANARY: the active set and the manifest agree on at least one channel id (a mismatch would silently empty the grid)",
);

const activeTransportedPlatforms = groupTransportedChannels(
  transportInputs.filter((entry) => activeChannelIds.has(entry.channel_id)),
);
assert(activeTransportedPlatforms.length > 0, "CANARY: the active catalog groups into real transported cards");

type GridCard = { label: string; tier: ChannelHardwareTier; recommended: boolean };

const firstPartyGrid = planUnifiedChannelGrid(CHANNEL_GRID_PLATFORMS, activeTransportedPlatforms).firstParty;

const gridCards: GridCard[] = [
  ...firstPartyGrid.map((platform) => {
    const tier = channelHardwareTier(planChannelDoors(platform.id).doors);
    return {
      label: platform.label,
      tier,
      recommended: showsRecommendedBadge(isChannelRecommended(platform.label), tier),
    };
  }),
  ...activeTransportedPlatforms.map((platform) => {
    const tier = channelHardwareTier(planDoors(platform.doors).doors);
    return {
      label: platform.label,
      tier,
      recommended: showsRecommendedBadge(isChannelRecommended(platform.label), tier),
    };
  }),
].sort(compareChannelGridCards);

// ONE PLATFORM = ONE CARD is asserted in full by channel-platform.test.ts;
// restated here only because every ordering claim below would be meaningless
// on a grid carrying the same platform twice.
assert(
  new Set(gridCards.map((card) => card.label)).size === gridCards.length,
  `CANARY: the grid this file reasons about has no duplicate cards (${gridCards.map((c) => c.label).join(", ")})`,
);

// CANARY, and the sharpest one in this file: if either tier is empty on the
// real grid, the filter renders nothing and this entire feature is inert
// while every other assertion here still passes.
assert(
  gridCards.some((card) => card.tier === "hardware_free"),
  "CANARY: the real grid actually has channels connectable with no computer",
);
assert(
  gridCards.some((card) => card.tier === "needs_hardware"),
  "CANARY: the real grid actually has channels that need one",
);
assert(
  gridCards.every((card) => card.tier !== "unknown"),
  `no card on the real grid is untierable (${gridCards.filter((c) => c.tier === "unknown").map((c) => c.label).join(", ")})`,
);

// --- ORDER: lead with what can be finished today; Telegram first. --------

assert(gridCards.length > 0 && gridCards[0].label === "Telegram", `Telegram is the first card (got ${gridCards[0]?.label})`);
assert(gridCards[0]?.recommended === true, "and it carries the Recommended marker");
assert(
  gridCards.filter((card) => card.recommended).length === 1,
  "exactly one card is marked Recommended — a grid of recommendations recommends nothing",
);

{
  const firstBoxCard = gridCards.findIndex((card) => card.tier === "needs_hardware");
  const lastFreeCard = gridCards.map((card) => card.tier).lastIndexOf("hardware_free");
  assert(
    firstBoxCard === -1 || lastFreeCard < firstBoxCard,
    "every card that works with no computer sorts above every card that needs one",
  );
}

// The badge is gated on the tier, not only on the authored recommendation —
// the guard against the exact regression that already happened once.
assert(
  showsRecommendedBadge(true, "needs_hardware") === false,
  "a recommendation never lands on a card that first demands a computer",
);
assert(showsRecommendedBadge(true, "unknown") === false, "nor on a card whose ways in are unknown");
assert(showsRecommendedBadge(true, "hardware_free") === true, "and does land on one that can be finished today");
assert(showsRecommendedBadge(false, "hardware_free") === false, "an unrecommended channel gets no badge");

// --- THE FILTER. ---------------------------------------------------------

{
  const plan = planChannelHardwareFilter(gridCards, DEFAULT_CHANNEL_HARDWARE_FILTER);
  assert(plan.options.length === 3, `the real grid offers all three views (got ${plan.options.length})`);
  assert(plan.selected === "hardware_free", "the default view leads with what works with no computer");
  assert(
    plan.visible.length > 0 && plan.visible.every((card) => card.tier === "hardware_free"),
    "and shows only those cards",
  );
  assert(plan.visible[0]?.label === "Telegram", "with Telegram still first inside it");

  const all = plan.options.find((option) => option.id === "all");
  assert(all?.count === gridCards.length, "the All count is the whole grid, not a subset");
  const free = plan.options.find((option) => option.id === "hardware_free");
  const needs = plan.options.find((option) => option.id === "needs_hardware");
  assert(
    (free?.count ?? 0) + (needs?.count ?? 0) === gridCards.length,
    "the two tiers partition the grid — nothing is counted twice and nothing is stranded",
  );
  assert((free?.count ?? 0) > 0 && (needs?.count ?? 0) > 0, "and neither side is empty");

  // NOTHING IS HIDDEN. A filter that can strand a card is worse than no
  // filter: the channel simply ceases to exist for that customer.
  const reachable = new Set(
    (["hardware_free", "needs_hardware", "all"] as ChannelHardwareFilterId[]).flatMap((id) =>
      planChannelHardwareFilter(gridCards, id).visible.map((card) => card.label),
    ),
  );
  assert(
    gridCards.every((card) => reachable.has(card.label)),
    "every card on the grid is reachable through at least one view",
  );
}

// A filter of one is a dead control — not rendered at all.
{
  const onlyFree: GridCard[] = [{ label: "Telegram", tier: "hardware_free", recommended: true }];
  const plan = planChannelHardwareFilter(onlyFree, "hardware_free");
  assert(plan.options.length === 0, "a grid with only one tier renders NO filter control");
  assert(plan.visible.length === 1, "and still shows every card");
}
{
  const onlyBox: GridCard[] = [{ label: "Signal", tier: "needs_hardware", recommended: false }];
  const plan = planChannelHardwareFilter(onlyBox, "hardware_free");
  assert(plan.options.length === 0, "same when every card needs a computer");
  assert(plan.visible.length === 1, "and the requested-but-empty tier never blanks the grid");
}
{
  const plan = planChannelHardwareFilter([] as GridCard[], "hardware_free");
  assert(plan.options.length === 0 && plan.visible.length === 0, "an empty grid offers no filter and shows nothing");
}

// An `unknown` card is reachable, and only under All — a third fact, never
// folded into either tier.
{
  const withUnknown: GridCard[] = [
    { label: "Telegram", tier: "hardware_free", recommended: true },
    { label: "Signal", tier: "needs_hardware", recommended: false },
    { label: "Mystery", tier: "unknown", recommended: false },
  ];
  const free = planChannelHardwareFilter(withUnknown, "hardware_free");
  const needs = planChannelHardwareFilter(withUnknown, "needs_hardware");
  const all = planChannelHardwareFilter(withUnknown, "all");
  assert(!free.visible.some((card) => card.tier === "unknown"), "an untierable card is not quietly counted as connectable");
  assert(!needs.visible.some((card) => card.tier === "unknown"), "nor as needing a computer");
  assert(all.visible.length === 3, "but it is reachable under All");
  assert(all.options.find((option) => option.id === "all")?.count === 3, "and counted there");
}

// A selection naming an empty tier resolves to a view with cards in it,
// rather than rendering an empty grid that reads as broken.
{
  const cards: GridCard[] = [
    { label: "Telegram", tier: "hardware_free", recommended: true },
    { label: "Signal", tier: "needs_hardware", recommended: false },
  ];
  const plan = planChannelHardwareFilter(cards, "needs_hardware");
  assert(plan.selected === "needs_hardware" && plan.visible.length === 1, "a populated selection is honoured as asked");
}

// --- THE PANEL BEHIND A CARD THAT NEEDS A BOX: never a dead end. --------

{
  const step = channelHardwareNextStep("needs_hardware", false);
  assert(Boolean(step), "a hardware channel opened by a cloud-only agent gets a real next step");
  assert((step?.title || "").trim().length > 0, "which says plainly what is missing");
  assert((step?.body || "").trim().length > 0, "in one sentence");
  assert((step?.action || "").trim().length > 0, "and offers an action, not just an explanation");
  assert(channelHardwareNextStep("needs_hardware", true) === null, "and vanishes the moment a computer exists");
  assert(channelHardwareNextStep("hardware_free", false) === null, "a hardware-free channel never asks for one");
  assert(channelHardwareNextStep("unknown", false) === null, "an untierable card does not invent a hardware demand");
}

// --- COPY: the customer's terms, never ours. ----------------------------
//
// Same rule channel-dead-end-sweep.ts applies to remediation copy, applied to
// every string this module can put on screen.

const MECHANISM = /\b(openclaw|gateway|plugin|npm|package|binary|schema|config|daemon|runtime)\b/i;
const copyStrings: [string, string][] = [
  ["Recommended badge", CHANNEL_RECOMMENDED_BADGE],
  ...(["hardware_free", "needs_hardware", "all"] as ChannelHardwareFilterId[]).map(
    (id): [string, string] => [`filter label ${id}`, channelHardwareFilterLabel(id)],
  ),
];
{
  const step = channelHardwareNextStep("needs_hardware", false);
  if (step) {
    copyStrings.push(["next-step title", step.title], ["next-step body", step.body], ["next-step action", step.action]);
  }
}
for (const [where, text] of copyStrings) {
  assert(!MECHANISM.test(text), `${where} names no mechanism: ${JSON.stringify(text)}`);
  assert(text.trim().length > 0, `${where} says something`);
}
assert(
  channelHardwareFilterLabel("hardware_free") !== channelHardwareFilterLabel("needs_hardware"),
  "the two tiers are labelled differently — the split IS the information",
);

// --- WIRED, not merely built. -------------------------------------------
//
// This codebase's single most common defect is complete, correct, tested code
// with ZERO callers (CLAUDE.md). Everything above would pass just as happily
// if ChannelsTab never imported any of it. There is no DOM harness here, so
// the check is structural: the real component source, scanned for the seams —
// with a canary, because a scan that stops matching would otherwise enforce
// nothing and still report green.

{
  const componentPath = join(__dirname, "FleetAgentDetail.tsx");
  const source = readFileSync(componentPath, "utf8");
  assert(source.length > 10_000, "CANARY: FleetAgentDetail.tsx was actually read (a failed read must not read as a pass)");
  assert(
    /className="fleet-channel-grid"/.test(source),
    "CANARY: the scan found the channel grid it is asserting about",
  );

  for (const seam of [
    "compareChannelGridCards",
    "planChannelHardwareFilter",
    "channelHardwareTier",
    "showsRecommendedBadge",
    "channelHardwareNextStep",
    "CHANNEL_RECOMMENDED_BADGE",
  ]) {
    assert(source.includes(seam), `ChannelsTab actually calls ${seam}`);
  }

  // The grid must render the FILTERED list. Rendering the unfiltered one is a
  // one-token change that type-checks, behaves identically today on the "All"
  // view, and silently turns the filter into a decoration.
  assert(
    /fleet-channel-grid[\s\S]{0,200}visibleChannelCards\.map/.test(source),
    "the grid renders the filtered cards, not the unfiltered list",
  );
  // And the next step must be a real link, not a sentence with nothing to press.
  assert(
    /<HardwareNextStep[\s\S]{0,120}href=/.test(source),
    "the needs-a-computer panel is handed a destination, so its action is a real link",
  );
}

if (failed > 0) {
  console.error(`\n${passed} passed, ${failed} failed`);
  process.exit(1);
}
console.log(`${passed} passed, 0 failed`);

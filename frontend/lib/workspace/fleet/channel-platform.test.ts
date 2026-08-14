/**
 * ONE PLATFORM = ONE CARD, ACROSS BOTH HALVES OF THE CHANNEL GRID.
 *
 * The 2026-08-14 full OpenClaw cutover shipped a Channels tab that rendered
 * "Telegram" twice (the first-party `sage_telegram_hosted` card beside the
 * transported `openclaw_telegram` one) and the WeChat family three times
 * ("WeChat / WeCom", "Weixin", "WeCom") — a direct violation of the founder's
 * standing law, live, on the agent Configure surface. Every transported card
 * had also lost its brand mark to a monogram, next to assets that were already
 * sitting in `public/`.
 *
 * WHY A UNIT TEST CAN CATCH THIS AT ALL
 * -------------------------------------
 * This repo has no headless-DOM harness (no jsdom/RTL — see package.json), so
 * the grid itself cannot be rendered here. It does not need to be: the grid is
 * `[...firstParty, ...transported]`, and "no two cards name the same platform"
 * is a property of those two lists, which are pure data. channel-platform.ts
 * exists so this file can drive the REAL rule.
 *
 * EXPECTED AND ACTUAL COME FROM DIFFERENT SOURCES
 * ------------------------------------------------
 *   1. `CHANNEL_GRID_PLATFORMS` / `groupTransportedChannels` — the REAL
 *      first-party grid and the REAL grouping ChannelsTab renders.
 *   2. `server_modules/openclaw_channel_manifest.json` — the checked-in
 *      manifest, GENERATED from a pinned OpenClaw install. Upstream's own
 *      channel ids and display names, never re-typed here.
 *   3. `server_modules/openclaw_channel_registry.py`'s
 *      `OPENCLAW_CUT_OVER_CHANNEL_IDS` — the one authored datum in the whole
 *      channel system, read out of the Python source. It is the REASON a
 *      first-party card has to go, so this test states that reason from the
 *      registry rather than from its own opinion.
 *
 * THE COLLISION SWEEP IS EXHAUSTIVE, NOT A SPOT CHECK
 * ----------------------------------------------------
 * A collision is pairwise, and the rule is monotone (a transported platform
 * can only ever REMOVE a first-party card, never add one). So sweeping the
 * empty transported set, every single-platform set, and the whole manifest at
 * once covers every pair that any real active catalog could ever contain —
 * without this test having to reconstruct which channels the backend currently
 * considers active.
 *
 * Run: npx tsx lib/workspace/fleet/channel-platform.test.ts
 */

import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";

import { CHANNEL_GRID_PLATFORMS, groupTransportedChannels, type TransportedChannelInput } from "./channel-doors";
import {
  channelGridPlatformCollisions,
  firstPartyPlatformTokens,
  planUnifiedChannelGrid,
  transportedPlatformTokens,
} from "./channel-platform";
import { CHANNEL_ICONS, channelIconSrc } from "./fleet-icons";

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

// --- Source 2: the generated manifest. ----------------------------------

type ManifestChannel = {
  id: string;
  channel_key: string;
  label: string;
  credential_shape?: { selection_label?: string; connect_method?: "credential" | "pairing" | "plugin_absent" } | null;
};

const manifest = JSON.parse(
  readFileSync(join(repoRoot, "server_modules", "openclaw_channel_manifest.json"), "utf8"),
) as { channels: ManifestChannel[] };

assert(
  Array.isArray(manifest.channels) && manifest.channels.length >= 20,
  `the checked-in manifest loaded with a real channel set (got ${manifest.channels?.length})`,
);

const transportInputs: TransportedChannelInput[] = manifest.channels.map((channel) => ({
  channel_key: channel.channel_key,
  channel_id: channel.id,
  label: channel.label,
  selection_label: channel.credential_shape?.selection_label || channel.label,
  connect_method: channel.credential_shape?.connect_method || "credential",
}));

const allPlatforms = groupTransportedChannels(transportInputs);
assert(allPlatforms.length > 0, "the manifest groups into at least one transported platform");

// --- Source 3: the cut-over decision, read out of the Python registry. ---
//
// A regex over source is fragile by nature, so it carries its own canary: if
// the parse stops finding a set, this test says so loudly rather than quietly
// asserting nothing (CLAUDE.md: a check that derives its expectations from the
// thing it checks is blind, and reports "passed").

const registrySource = readFileSync(join(repoRoot, "server_modules", "openclaw_channel_registry.py"), "utf8");
const cutOverBlock = /OPENCLAW_CUT_OVER_CHANNEL_IDS:\s*frozenset\[str\]\s*=\s*frozenset\(\s*\{([^}]*)\}/.exec(
  registrySource,
);
const cutOverIds = new Set(
  (cutOverBlock?.[1] || "").match(/"([^"]+)"/g)?.map((quoted) => quoted.slice(1, -1)) ?? [],
);
assert(
  cutOverIds.size > 0,
  "parsed OPENCLAW_CUT_OVER_CHANNEL_IDS out of openclaw_channel_registry.py (canary — a failed parse must not read as a pass)",
);
for (const id of cutOverIds) {
  assert(
    manifest.channels.some((channel) => channel.id === id),
    `cut-over id ${JSON.stringify(id)} is a real channel in the manifest`,
  );
}

// --- THE RULE: no two grid cards ever name the same platform. -----------

function collisionReport(groups: string[][]): string {
  return groups.map((group) => group.join(" + ")).join("; ");
}

/** The grid ChannelsTab actually builds, for a given transported set. */
function gridFor(transported: ReturnType<typeof groupTransportedChannels>) {
  const firstParty = planUnifiedChannelGrid(CHANNEL_GRID_PLATFORMS, transported).firstParty;
  return { firstParty, collisions: channelGridPlatformCollisions(firstParty, transported) };
}

// (a) No transport at all — the first-party grid must not collide with itself.
{
  const grid = gridFor([]);
  assert(
    grid.collisions.length === 0,
    `the first-party grid alone names each platform once (got ${collisionReport(grid.collisions)})`,
  );
  assert(
    grid.firstParty.length === CHANNEL_GRID_PLATFORMS.length,
    "with no transported channels, every first-party card is rendered — a card is only ever dropped BY a transported platform",
  );
}

// (b) Every single transported platform, one at a time. This is the sweep that
//     would have failed on the shipped 2026-08-14 grid: `{Telegram}` alone
//     produced two Telegram cards, and `{WeCom}` alone produced two WeCom-
//     naming cards.
for (const platform of allPlatforms) {
  const grid = gridFor([platform]);
  assert(
    grid.collisions.length === 0,
    `the grid names each platform once with only ${platform.label} transported (got ${collisionReport(grid.collisions)})`,
  );
  const dropped = planUnifiedChannelGrid(CHANNEL_GRID_PLATFORMS, [platform]).superseded;
  assert(
    dropped.length <= 1,
    `one transported platform (${platform.label}) never removes two different first-party cards (removed ${dropped
      .map((item) => item.entry.label)
      .join(", ")})`,
  );
  for (const item of dropped) {
    const shared = Array.from(firstPartyPlatformTokens(item.entry)).filter((token) =>
      transportedPlatformTokens(platform).has(token),
    );
    assert(
      shared.length > 0,
      `${platform.label} only removes a first-party card it genuinely shares a platform with (removed ${item.entry.label})`,
    );
  }
}

// (c) Everything transported at once — the upper bound of any active catalog.
{
  const grid = gridFor(allPlatforms);
  assert(
    grid.collisions.length === 0,
    `the grid names each platform once with the whole manifest transported (got ${collisionReport(grid.collisions)})`,
  );
}

// --- The two cards the founder actually saw duplicated. -----------------
//
// Stated against the manifest's own channels rather than by pinning ids on
// this side: find the transported platform carrying each cut-over channel,
// and require the first-party grid to yield to it.

function platformCarrying(channelId: string) {
  return allPlatforms.find((platform) => platform.variants.some((variant) => variant.channel_id === channelId));
}

for (const id of cutOverIds) {
  const platform = platformCarrying(id);
  assert(Boolean(platform), `the manifest groups cut-over channel ${id} into a card`);
  if (!platform) continue;
  const survivors = planUnifiedChannelGrid(CHANNEL_GRID_PLATFORMS, [platform]).firstParty;
  const collisions = channelGridPlatformCollisions(survivors, [platform]);
  assert(
    collisions.length === 0,
    `cut-over platform ${platform.label} leaves exactly one card for its platform (got ${collisionReport(collisions)})`,
  );
}

// Telegram specifically: the registry cut it over, so the hosted-bot card is
// the stale one and the transported card is the survivor.
{
  assert(cutOverIds.has("telegram"), "the registry still lists telegram as cut over (this test's premise)");
  const telegram = platformCarrying("telegram");
  assert(Boolean(telegram), "the manifest carries a Telegram card");
  if (telegram) {
    const plan = planUnifiedChannelGrid(CHANNEL_GRID_PLATFORMS, [telegram]);
    assert(
      plan.superseded.some((item) => item.entry.id === "sage_telegram_hosted"),
      "the first-party hosted-Telegram card yields to the transported Telegram card",
    );
    assert(
      !plan.firstParty.some((entry) => /telegram/i.test(entry.label)),
      "no first-party card still says Telegram once the transport carries it",
    );
  }
}

// --- Precision: a platform the transport does NOT carry keeps its card. --
//
// Slack and Discord are superseded upstream (their first-party runtimes still
// own those platforms), so they never reach the transported grid — and their
// cards must survive every other transported platform. This is the
// over-aggressive direction, and it is the one that would silently delete a
// working channel.
for (const id of ["slack", "discord"]) {
  assert(!cutOverIds.has(id), `${id} is deliberately NOT cut over (this test's premise)`);
  const withoutThatPlatform = allPlatforms.filter(
    (platform) => !platform.variants.some((variant) => variant.channel_id === id),
  );
  const survivors = planUnifiedChannelGrid(CHANNEL_GRID_PLATFORMS, withoutThatPlatform).firstParty;
  assert(
    survivors.some((entry) => new RegExp(id, "i").test(entry.label)),
    `the first-party ${id} card survives while the transport does not carry ${id}`,
  );
}

// --- BRAND MARKS SURVIVE A PLATFORM CHANGING LANES. ---------------------
//
// The second half of the same defect: every transported card fell back to a
// monogram because the icon table was keyed by CHANNEL KEY, so it had no row
// for `openclaw_telegram` even though `telegram.svg` was already shipping
// under `sage_telegram_hosted`. Driven off the manifest and the real
// first-party table, so it fails the moment a platform's mark stops resolving
// for one of its lanes.

for (const channel of manifest.channels) {
  const transportedSrc = channelIconSrc(channel.channel_key);
  for (const [firstPartyKey, firstPartySrc] of Object.entries(CHANNEL_ICONS)) {
    const shared = Array.from(
      firstPartyPlatformTokens({ id: firstPartyKey, label: firstPartyKey }),
    ).some((token) =>
      transportedPlatformTokens({
        key: channel.channel_key,
        label: channel.label,
        variants: [
          {
            channel_key: channel.channel_key,
            channel_id: channel.id,
            label: channel.label,
            selection_label: channel.label,
            connect_method: "credential",
          },
        ],
      }).has(token),
    );
    if (!shared) continue;
    assert(
      transportedSrc === firstPartySrc,
      `${channel.channel_key} and ${firstPartyKey} are the same platform, so they must render the same mark ` +
        `(got ${String(transportedSrc)} vs ${firstPartySrc})`,
    );
  }
}

// A floor, not a per-channel list: a wholesale regression (the table renamed,
// the resolver broken) drops this to near zero while every individual
// assertion above still passes.
const marked = manifest.channels.filter((channel) => Boolean(channelIconSrc(channel.channel_key)));
assert(
  marked.length >= 20,
  `most transported channels resolve to a real brand mark (got ${marked.length} of ${manifest.channels.length})`,
);

// The three platforms CLAUDE.md documents as deliberately unmarked — no mark
// exists (IRC), no licence exists (Yuanbao), only a wordmark exists (Synology
// Chat). A monogram is correct for these; a lookalike is worse than no mark,
// so this asserts they stay unresolved.
for (const id of ["irc", "yuanbao", "synology-chat"]) {
  const channel = manifest.channels.find((entry) => entry.id === id);
  assert(Boolean(channel), `the manifest still carries ${id}`);
  if (channel) {
    assert(
      channelIconSrc(channel.channel_key) === undefined,
      `${id} stays a monogram — no mark is sourceable for it, and a lookalike is worse than none`,
    );
  }
}

// Every mark this file hands out is a file that exists. An icon table pointing
// at a deleted asset is a broken image the type system cannot see.
{
  const resolved = new Set<string>();
  for (const channel of manifest.channels) {
    const src = channelIconSrc(channel.channel_key);
    if (src) resolved.add(src);
  }
  for (const src of Object.values(CHANNEL_ICONS)) resolved.add(src);
  for (const src of resolved) {
    const filePath = join(repoRoot, "frontend", "public", src.split("?")[0]);
    assert(existsSync(filePath), `brand asset exists on disk: ${src}`);
  }
}

console.log(`\nchannel-platform: ${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);

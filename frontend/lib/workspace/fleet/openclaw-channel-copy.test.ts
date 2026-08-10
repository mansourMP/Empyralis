/**
 * Regression guard: the word "OpenClaw" must never reach a customer's
 * screen on the channel setup panel (OpenClawChannelsPanel.tsx). Standing
 * instruction — see CLAUDE.md's fix #1 in the 2026-08-09 channel-setup-
 * screen pass. The literal string reappeared once already (four separate
 * spots: the section subtitle, the observed-error banner, two remediation
 * sentences) because it is the kind of thing a habit reintroduces silently
 * — nobody notices a name leaking into copy the way they'd notice a broken
 * button.
 *
 * DRIVEN OFF THE REAL OUTPUT, NOT A HAND-MAINTAINED LIST OF BANNED PHRASES
 * -------------------------------------------------------------------------
 * This repo has no headless-DOM test harness (no jsdom/RTL — see
 * package.json), so this cannot render <OpenClawChannelsPanel> and inspect
 * its DOM directly. Two things it CAN do without one, and does:
 *
 *   1. Drive `remediationFor` — the exported function that actually
 *      PRODUCES every piece of conditional/templated copy on the panel
 *      (install/credential/enable/ready/elsewhere/unknown states) — across
 *      every state combination it can be called with, and assert none of
 *      the resulting `label`/`detail` strings mention OpenClaw. This is
 *      exactly where the four leaks lived: inside branches, not in a
 *      static heading a human reads on every edit.
 *   2. Read the CHECKED-IN manifest
 *      (server_modules/openclaw_channel_manifest.json) that
 *      `entry.label`/`entry.selection_label` come from and are rendered
 *      into the DOM verbatim, and assert none of ITS values mention
 *      OpenClaw either — that data is upstream's own words, not ours, but
 *      it still ends up on screen.
 *
 * The full DOM-grep this test cannot do is required separately, by hand,
 * against a real running instance — see the verification note in the
 * commit/PR this file shipped with.
 *
 * A THIRD SOURCE, ADDED WITH THE DOOR PICKER (2026-08-10)
 * -------------------------------------------------------
 * The final section drives channel-doors.ts — the real door table and the
 * real rule functions ChannelsTab renders — rather than pinning their
 * strings. That module was split out of FleetAgentDetail.tsx precisely so
 * this file could import it: a check whose expected values are transcribed
 * from the thing it checks can only ever confirm itself, and the pinned
 * literals below are exactly that shape (kept only where the string lives
 * inside a React component this runner cannot load). It asserts the
 * door-COUNT rule that decides whether a picker appears at all, that a
 * picker's doors state their differing consequences on their faces, and
 * that a hardware requirement is answerable before the pick — none of
 * which any pinned sentence could express.
 *
 * Run: npx tsx lib/workspace/fleet/openclaw-channel-copy.test.ts
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import {
  channelCardPill,
  formatChannelList,
  remediationFor,
  type OpenClawChannelCatalogEntry,
  type OpenClawObservedChannel,
} from "./openclaw-channel-copy";
import {
  CHANNEL_DOORS,
  channelDoorHardwareNote,
  channelDoorHardwareState,
  channelDoorUnavailableReason,
  groupTransportedChannels,
  isChannelDoorAvailable,
  planChannelDoors,
  planDoors,
  type ChannelDoor,
  type TransportedChannelInput,
} from "./channel-doors";

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

const BANNED = /openclaw/i;

function assertNoOpenClaw(value: string | undefined, label: string): void {
  assert(!value || !BANNED.test(value), `${label} must not mention OpenClaw — got: ${JSON.stringify(value)}`);
}

// --- A full matrix of catalog entries × observed states, covering every
//     branch `remediationFor` can take. ------------------------------------

function entry(overrides: Partial<OpenClawChannelCatalogEntry> = {}): OpenClawChannelCatalogEntry {
  return {
    channel_key: "openclaw_telegram",
    channel_id: "telegram",
    label: "Telegram",
    connect_method: "credential",
    selection_label: "Telegram (Bot API)",
    docs_path: "/channels/telegram",
    fields: [{ name: "token", secret: true, type: "string", file_alternative: null }],
    requires_plugin: false,
    plugin_id: null,
    ...overrides,
  };
}

function observed(overrides: Partial<OpenClawObservedChannel> = {}): OpenClawObservedChannel {
  return {
    channel_id: "telegram",
    channel_key: "openclaw_telegram",
    installed: true,
    requires_plugin: false,
    enabled: true,
    configured: true,
    fields: [{ name: "token", secret: true, type: "string", set: true }],
    accounts: [],
    ...overrides,
  };
}

const CONNECT_METHODS: OpenClawChannelCatalogEntry["connect_method"][] = ["credential", "pairing", "plugin_absent"];

let cases = 0;
for (const connect_method of CONNECT_METHODS) {
  for (const requires_plugin of [true, false]) {
    for (const reachable of [true, false]) {
      for (const observedIsUndefined of [true, false]) {
        for (const installed of [true, false]) {
          for (const configured of [true, false]) {
            for (const enabled of [true, false]) {
              cases += 1;
              const e = entry({ connect_method, requires_plugin });
              const o = observedIsUndefined
                ? undefined
                : observed({ installed, requires_plugin, configured, enabled });
              const remediation = remediationFor(e, o, reachable);
              const caseLabel = `connect_method=${connect_method} requires_plugin=${requires_plugin} reachable=${reachable} observed=${observedIsUndefined ? "undefined" : `{installed:${installed},configured:${configured},enabled:${enabled}}`}`;
              assertNoOpenClaw(remediation.detail, `remediationFor(${caseLabel}).detail`);
              if ("label" in remediation) {
                assertNoOpenClaw(remediation.label, `remediationFor(${caseLabel}).label`);
              }
              // The one word that reaches a CARD FACE in the unified grid.
              // Same treatment as the remediation copy above and for the same
              // reason: it is produced inside a branch, not typed into a
              // heading a human re-reads on every edit.
              const pill = channelCardPill(remediation);
              assertNoOpenClaw(pill.label, `channelCardPill(${caseLabel}).label`);
              assert(
                pill.label.trim().length > 0,
                `channelCardPill(${caseLabel}) must produce a non-empty label — a card face with a blank pill says nothing`,
              );
              assert(
                ["connected", "gateway", "locked", "setup"].includes(pill.tone),
                `channelCardPill(${caseLabel}).tone must be one of the first-party pill tones, got ${JSON.stringify(pill.tone)}`,
              );
              // "Connected" is a claim this screen has no evidence for — a
              // connection is proven by a real message arriving, and nothing
              // here has seen one. The transported cards say "Ready".
              assert(
                !/connected/i.test(pill.label),
                `channelCardPill(${caseLabel}).label must not claim "Connected", got ${JSON.stringify(pill.label)}`,
              );
              // THE CUSTOMER IS NEVER SHOWN MECHANISM. Standing instruction,
              // and the reason the install row was rewritten: it read "The
              // channel's plugin is not on this computer yet." above a button
              // labelled "Install plugin" — a fact about a package on a disk,
              // handed to the person as a thing to act on. Asserted over every
              // branch rather than as a pinned sentence, because the leak was
              // inside a branch both times.
              const mechanism = /\bplugin(s)?\b|\bnpm\b|\bpackage\b|\bbinary\b/i;
              assert(
                !mechanism.test(remediation.detail),
                `remediationFor(${caseLabel}).detail must not name mechanism, got ${JSON.stringify(remediation.detail)}`,
              );
              if ("label" in remediation) {
                assert(
                  !mechanism.test(remediation.label),
                  `remediationFor(${caseLabel}).label must not name mechanism, got ${JSON.stringify(remediation.label)}`,
                );
              }
              assert(
                !mechanism.test(pill.label),
                `channelCardPill(${caseLabel}).label must not name mechanism, got ${JSON.stringify(pill.label)}`,
              );
              // A state whose whole remedy is one button carries no sentence at
              // all — the chips already say what is true and the button says
              // what happens next. This is the shape, asserted, not a comment.
              if (remediation.kind === "install" || remediation.kind === "enable") {
                assert(
                  remediation.detail === "",
                  `remediationFor(${caseLabel}) is a one-button state and must carry no explanatory sentence, got ${JSON.stringify(remediation.detail)}`,
                );
                assert(
                  remediation.label.trim().length > 0,
                  `remediationFor(${caseLabel}) must label its button — the control is the whole remedy`,
                );
              }
            }
          }
        }
      }
    }
  }
}
assert(cases > 0, "the remediationFor matrix actually ran at least one case");

// --- formatChannelList: sanity on the join logic the "already available
//     elsewhere" note is built from. Not an OpenClaw check on its own —
//     the note's own literal sentence is asserted separately below. -------

assert(formatChannelList([]) === "", "empty list formats to empty string");
assert(formatChannelList(["Telegram"]) === "Telegram", "single item passes through");
assert(formatChannelList(["Telegram", "WhatsApp"]) === "Telegram and WhatsApp", "two items joined with 'and'");
assert(
  formatChannelList(["Telegram", "WhatsApp", "Discord"]) === "Telegram, WhatsApp, and Discord",
  "three+ items get an Oxford comma",
);

// --- The checked-in OpenClaw channel manifest: `entry.label` and
//     `entry.selection_label` are rendered into the DOM verbatim (see
//     openclaw-channels.css: "Their own selection label ... Their words,
//     not ours"). Upstream's data, but it still reaches the screen, so it
//     gets the same check. --------------------------------------------

type ManifestChannel = {
  id: string;
  channel_key: string;
  label: string;
  credential_shape?: {
    selection_label?: string;
    docs_path?: string | null;
    connect_method?: "credential" | "pairing" | "plugin_absent";
  } | null;
};
type Manifest = { channels: ManifestChannel[] };

const manifestPath = join(__dirname, "..", "..", "..", "..", "server_modules", "openclaw_channel_manifest.json");
const manifest = JSON.parse(readFileSync(manifestPath, "utf8")) as Manifest;
assert(Array.isArray(manifest.channels) && manifest.channels.length > 0, "manifest loaded with at least one channel");
for (const channel of manifest.channels) {
  assertNoOpenClaw(channel.label, `manifest channel ${channel.id}.label`);
  assertNoOpenClaw(channel.credential_shape?.selection_label, `manifest channel ${channel.id}.credential_shape.selection_label`);
}

// --- The two static, non-conditional sentences this file's fix #1/#2 pass
//     rewrote — the ones a human reads on every edit, but still worth a
//     literal pin so a copy-paste regression is loud immediately rather
//     than waiting for the next full-panel browser check. ------------------

assertNoOpenClaw("Messaging channels connected through this computer.", "the fixed panel subtitle (pinned literal)");

// --- The unified Channels tab (FleetAgentDetail.tsx's ChannelsTab). This
//     screen merges the first-party channel rows (Telegram/WhatsApp/Discord/
//     Signal/iMessage/Slack/WeChat) and the OpenClaw-transported rows into
//     ONE list, in both the gateway-paired and no-gateway states — no
//     separate "OpenClawChannelsPanel" component or section any more,
//     confirmed by grep at commit time (grep -n "OpenClawChannelsPanel"
//     frontend/lib/workspace/fleet/FleetAgentDetail.tsx must show ONLY the
//     import-path string and a code comment, never a JSX usage). Since this
//     file cannot render React (see the header comment), the static strings
//     that screen shows in BOTH states are pinned here by hand, literal
//     copy-paste from FleetAgentDetail.tsx — this is the same technique the
//     panel subtitle pin above already uses, extended to the merged screen.
//     A human editing either copy site is expected to keep both in sync;
//     this test exists so a slip that reintroduces the transport's name is
//     loud immediately instead of waiting for a browser check. -------------

assertNoOpenClaw("Where people can message this agent", "unified Channels tab subtitle (both states, pinned literal)");
assertNoOpenClaw(
  "This agent's computer could not be reached, so some channels below show an unknown state. Their setup fields are still accurate.",
  "unified Channels tab observed-error banner (gateway state, pinned literal)",
);
assertNoOpenClaw("Reading this agent's computer…", "unified Channels tab loading line (gateway state, pinned literal)");
assertNoOpenClaw("connect elsewhere in Empyralis and", "unified Channels tab superseded-channel note (pinned literal fragment)");
assertNoOpenClaw("isn't shown here", "unified Channels tab superseded-channel note (pinned literal fragment)");

// The panel a transported card OPENS (2026-08-10: the grid restored, so
// everything past the pill moved in here). Same pin technique — literal
// copy-paste from FleetAgentDetail.tsx / OpenClawChannelsPanel.tsx.
assertNoOpenClaw(
  "Link this one directly on the computer — there is nothing to paste here.",
  "channel detail panel, pairing-only state (pinned literal)",
);
assertNoOpenClaw(
  "These are the fields this channel needs to connect.",
  "channel detail panel, generated credential form intro (pinned literal)",
);
assertNoOpenClaw(
  "Leave a field blank to keep what is already on the computer. Values are sent straight to it and are not stored here.",
  "channel detail panel, credential form note (pinned literal)",
);

// The first-party platform labels (CHANNEL_GRID_PLATFORMS) now render inside
// the SAME row list as the OpenClaw catalog rows — pin them too, since a
// label is exactly the kind of string this file already treats as reaching
// the DOM verbatim (see the manifest-label loop above).
const FIRST_PARTY_CHANNEL_GRID_LABELS = [
  "Telegram",
  "Slack",
  "Discord",
  "WhatsApp",
  "Signal",
  "iMessage",
  "WeChat / WeCom",
];
for (const label of FIRST_PARTY_CHANNEL_GRID_LABELS) {
  assertNoOpenClaw(label, `CHANNEL_GRID_PLATFORMS label ${JSON.stringify(label)} (pinned literal)`);
}

// --- The channel DOOR PICKER (channel-doors.ts). ------------------------
//
// Unlike every pinned literal above, this section imports the REAL door
// table and the REAL rule functions the screen renders — the expected set
// and the actual set come from different places, which is the only version
// of a conformance check that can ever fail (CLAUDE.md: "a check that
// derives its own expectations from the thing it checks is blind, and
// reports 'passed'"). Editing a door's copy in channel-doors.ts is
// therefore covered here automatically; nobody has to remember to re-type
// it into this file.

const doorChannelIds = Object.keys(CHANNEL_DOORS);
assert(doorChannelIds.length > 0, "CHANNEL_DOORS is non-empty");

for (const channelId of doorChannelIds) {
  const plan = planChannelDoors(channelId);
  const realDoors = CHANNEL_DOORS[channelId].filter((d) => d.real);

  // THE RULE, asserted as a rule rather than per channel: the door COUNT —
  // and nothing else, no channel name — decides whether a picker is shown.
  // A hardcoded "Telegram gets a picker" would rot the day Telegram gains a
  // third door and WhatsApp a second.
  assert(
    plan.mode === (realDoors.length === 0 ? "none" : realDoors.length === 1 ? "direct" : "picker"),
    `planChannelDoors(${channelId}) mode must follow the real-door count (${realDoors.length} real), got ${plan.mode}`,
  );
  assert(
    plan.doors.length === realDoors.length && plan.doors.every((d) => d.real),
    `planChannelDoors(${channelId}).doors must be exactly the REAL doors — a door that is not built is not a way to connect`,
  );
  if (plan.mode === "direct") {
    assert(
      plan.door === plan.doors[0],
      `planChannelDoors(${channelId}) direct mode must hand back the one door, so the caller never re-derives it`,
    );
  }

  // A picker that shows two doors and says nothing about how they differ is
  // an extra click for nothing. Every door on a picker must state its
  // consequence ON ITS FACE, before it is chosen — this is the whole reason
  // the intermediate step earns its place.
  if (plan.mode === "picker") {
    for (const door of plan.doors) {
      assert(
        !!door.consequence && door.consequence.text.trim().length > 0,
        `${channelId} door ${door.key} is on a PICKER and must state its consequence on its face`,
      );
    }
    const consequences = new Set(plan.doors.map((d) => d.consequence?.text));
    assert(
      consequences.size === plan.doors.length,
      `${channelId}'s picker doors must not repeat one another's consequence — identical consequences mean there is no choice to make`,
    );
  }

  for (const door of CHANNEL_DOORS[channelId]) {
    const where = `${channelId} door ${door.key}`;
    assertNoOpenClaw(door.label, `${where}.label`);
    assertNoOpenClaw(door.body, `${where}.body`);
    assertNoOpenClaw(door.consequence?.text, `${where}.consequence.text`);
    assert(door.label.trim().length > 0, `${where}.label is non-empty — a door face with no name says nothing`);
    assert(door.body.trim().length > 0, `${where}.body is non-empty`);

    if (door.consequence) {
      const text = door.consequence.text;
      assert(
        door.consequence.tone === "safe" || door.consequence.tone === "risk",
        `${where}.consequence.tone must be safe|risk, got ${JSON.stringify(door.consequence.tone)}`,
      );
      // "A professional tool labels; it does not lecture" — one clear line,
      // never a paragraph of warnings, and never shouted. These bounds are
      // the doctrine made mechanical, because the natural drift on a risk
      // warning is always toward more of it.
      assert(text.length <= 120, `${where}.consequence.text must fit one line (<=120 chars), got ${text.length}`);
      assert(!text.includes("!"), `${where}.consequence.text must not shout — no exclamation marks`);
      assert(
        text.split(".").filter((s) => s.trim().length > 0).length <= 2,
        `${where}.consequence.text must be at most two short sentences, got ${JSON.stringify(text)}`,
      );
    }

    // Hardware is a fact about a door, answered the SAME way wherever it is
    // asked, so the customer meets it before the pick rather than after.
    const noBox = { hasHardware: false, doorConnected: false };
    const withBox = { hasHardware: true, doorConnected: false };
    const alreadyOn = { hasHardware: false, doorConnected: true };
    if (door.requiresHardware) {
      assert(channelDoorHardwareState(door, noBox) === "missing", `${where} with no computer is "missing"`);
      assert(channelDoorHardwareState(door, withBox) === "ready", `${where} with a computer is "ready"`);
      // An already-connected session must stay reachable even with no
      // gateway attached right now — otherwise the customer cannot open it
      // to see or disconnect the session that already exists.
      assert(channelDoorHardwareState(door, alreadyOn) === "ready", `${where} already connected stays reachable`);
      assert(!isChannelDoorAvailable(door, noBox), `${where} is UNAVAILABLE with no computer, not a pick that fails later`);
      assert(isChannelDoorAvailable(door, withBox), `${where} is available once a computer is attached`);
    } else {
      assert(
        channelDoorHardwareState(door, noBox) === "not-required" && isChannelDoorAvailable(door, noBox),
        `${where} needs no computer, so it is available on a cloud-only agent`,
      );
    }

    assertNoOpenClaw(channelDoorUnavailableReason(door), `${where} unavailable reason`);
    assert(
      channelDoorUnavailableReason(door).trim().length > 0,
      `${where} unavailable reason must say something — an unavailable door that does not say why is a dead end`,
    );
  }
}

// An unknown / absent channel opens nothing rather than throwing or
// inventing a door.
for (const missing of [null, undefined, "", "not_a_channel"]) {
  const plan = planChannelDoors(missing as string | null | undefined);
  assert(plan.mode === "none" && plan.doors.length === 0, `planChannelDoors(${JSON.stringify(missing)}) is "none"`);
}

// The hardware note itself: something to say in both hardware states, and
// deliberately NOTHING to say when the door needs no computer — a note
// reading "no computer needed" is noise on six of the seven channels.
assertNoOpenClaw(channelDoorHardwareNote("missing") || "", "channelDoorHardwareNote(missing)");
assertNoOpenClaw(channelDoorHardwareNote("ready") || "", "channelDoorHardwareNote(ready)");
assert((channelDoorHardwareNote("missing") || "").trim().length > 0, "the missing-hardware note says something");
assert((channelDoorHardwareNote("ready") || "").trim().length > 0, "the satisfied-hardware note says something");
assert(channelDoorHardwareNote("not-required") === null, "a door needing no computer renders no hardware note");

// Telegram is the one channel with a genuine two-door choice today, and the
// two doors carry OPPOSITE consequences — that asymmetry is the product
// fact the picker exists to show, and the reason the founder's own account
// was previously banned. Asserted structurally (the tones differ, one names
// a ban) rather than as a pinned sentence, so rewording the copy is free
// and dropping the fact is not.
const telegramDoors: ChannelDoor[] = planChannelDoors("sage_telegram_hosted").doors;
assert(telegramDoors.length >= 2, "Telegram has a real two-door choice");
assert(
  new Set(telegramDoors.map((d) => d.consequence?.tone)).size === telegramDoors.length,
  "Telegram's doors must differ in consequence TONE — two doors reading the same way is not a choice",
);
const telegramFullAccount = telegramDoors.find((d) => d.key === "full_account");
assert(!!telegramFullAccount?.requiresHardware, "Telegram's full-account door declares its hardware requirement");
assert(
  /ban/i.test(telegramFullAccount?.consequence?.text || ""),
  "Telegram's full-account door names the ban risk ON THE DOOR — never in a warning after a code has been sent",
);
assert(
  telegramFullAccount?.consequence?.tone === "risk",
  "Telegram's full-account door is toned as a risk",
);
const telegramChatbot = telegramDoors.find((d) => d.key === "byo_bot");
assert(telegramChatbot?.consequence?.tone === "safe", "Telegram's chatbot door is toned as the safe one");
assert(
  !telegramChatbot?.requiresHardware,
  "Telegram's chatbot door needs no computer — that difference is half the choice",
);

// Every door that signs in AS the owner must name its consequence. This is
// the rule the WhatsApp door would otherwise quietly miss: it is a
// one-door channel today, so it has no picker face to carry the line, and
// the line rides above its form instead — but it still has to exist.
for (const channelId of doorChannelIds) {
  for (const door of CHANNEL_DOORS[channelId]) {
    if (door.key !== "full_account") continue;
    if (!/signs in as you/i.test(door.body)) continue;
    assert(
      door.consequence?.tone === "risk",
      `${channelId}'s ${door.key} door signs in as the owner and must carry a risk consequence`,
    );
  }
}

// --- ONE PLATFORM = ONE CARD (groupTransportedChannels). ----------------
//
// Driven off the CHECKED-IN MANIFEST, which is generated from the transport's
// own registry — so the expected set (what upstream ships) and the actual set
// (what the grouping produces) come from different places, and this can
// actually fail. A hand-written list of "these ids are one platform" is
// exactly the mistake this surface has already made and corrected twice.

const transportInputs: TransportedChannelInput[] = manifest.channels.map((channel) => ({
  channel_key: channel.channel_key,
  channel_id: channel.id,
  label: channel.label,
  selection_label: channel.credential_shape?.selection_label || channel.label,
  connect_method: channel.credential_shape?.connect_method || "credential",
}));

const platforms = groupTransportedChannels(transportInputs);
assert(platforms.length > 0, "the transported channels group into at least one platform");

// Every channel ends up in exactly one platform — nothing dropped, nothing
// duplicated. A grouping that loses a channel loses a way to reach an agent.
const groupedKeys = platforms.flatMap((platform) => platform.variants.map((v) => v.channel_key));
assert(
  groupedKeys.length === transportInputs.length && new Set(groupedKeys).size === transportInputs.length,
  `every transported channel appears in exactly one platform (${transportInputs.length} in, ${groupedKeys.length} out, ${new Set(groupedKeys).size} distinct)`,
);

// The founder's own example: the transport ships Zalo as three channels
// (bot API, personal account, QR bot). One platform, three doors — never
// three cards beside a Telegram that is one card with two doors.
const zaloIds = transportInputs
  .filter((entry) => /^zalo/i.test(entry.label))
  .map((entry) => entry.channel_id);
assert(zaloIds.length >= 2, `the manifest still carries several Zalo variants (got ${JSON.stringify(zaloIds)})`);
const zaloPlatforms = platforms.filter((platform) =>
  platform.variants.some((variant) => zaloIds.includes(variant.channel_id)),
);
assert(zaloPlatforms.length === 1, `Zalo's variants collapse to ONE card, got ${zaloPlatforms.length}`);
assert(
  zaloPlatforms[0].variants.length === zaloIds.length,
  `that one Zalo card carries every Zalo variant as a door (${zaloPlatforms[0].variants.length} of ${zaloIds.length})`,
);
assert(zaloPlatforms[0].label === "Zalo", `the card takes the PLATFORM's name, got ${JSON.stringify(zaloPlatforms[0].label)}`);
assert(
  planDoors(zaloPlatforms[0].doors).mode === "picker",
  "a platform with several ways in asks first — the same door-COUNT rule the first-party cards use",
);

// WeCom (WeChat Work) and Weixin (consumer WeChat) are DIFFERENT PRODUCTS —
// CLAUDE.md says so explicitly. The two axes must both keep them apart.
const wecomPlatform = platforms.find((p) => p.variants.some((v) => v.channel_id === "wecom"));
const weixinPlatform = platforms.find((p) => p.variants.some((v) => /weixin/i.test(v.channel_id)));
if (wecomPlatform && weixinPlatform) {
  assert(wecomPlatform !== weixinPlatform, "WeCom and Weixin are different products and must never share a card");
}

// A NEW upstream variant groups WITHOUT a code change. This is the whole
// claim the derivation makes; a fixture that upstream has not shipped is the
// only way to assert it, and it is a synthetic INPUT, never a synthetic
// expectation.
const futureVariant: TransportedChannelInput = {
  channel_key: "openclaw_telegrambusiness",
  channel_id: "telegrambusiness",
  label: "Telegram Business",
  selection_label: "Telegram (Business API)",
  connect_method: "credential",
};
const telegramBase: TransportedChannelInput = {
  channel_key: "openclaw_telegram",
  channel_id: "telegram",
  label: "Telegram",
  selection_label: "Telegram (Bot API)",
  connect_method: "credential",
};
const withFuture = groupTransportedChannels([...transportInputs, telegramBase, futureVariant]);
const futureHome = withFuture.find((p) => p.variants.some((v) => v.channel_id === "telegrambusiness"));
assert(!!futureHome, "a newly shipped variant lands somewhere");
assert(
  futureHome!.variants.some((v) => v.channel_id === "telegram"),
  "a newly shipped variant joins its platform's existing card with no code change",
);
assert(futureHome!.label === "Telegram", "and the card keeps the platform's own name");

// A variant that shares a label word but NOT an id family stays its own card —
// the "Google Chat" / "Google Meet" shape, which is two products.
const sameWordDifferentProduct = groupTransportedChannels([
  { channel_key: "openclaw_googlechat", channel_id: "googlechat", label: "Google Chat", selection_label: "Google Chat", connect_method: "credential" },
  { channel_key: "openclaw_googlemeet", channel_id: "googlemeet", label: "Google Meet", selection_label: "Google Meet", connect_method: "credential" },
]);
assert(
  sameWordDifferentProduct.length === 2,
  "sharing a label word is not enough to merge — the id family must agree too",
);

// Every derived door goes through the SAME rules the authored ones do.
for (const platform of platforms) {
  const plan = planDoors(platform.doors);
  assert(
    plan.mode === (platform.doors.length === 1 ? "direct" : "picker"),
    `${platform.key} follows the door-count rule (${platform.doors.length} doors, got ${plan.mode})`,
  );
  assertNoOpenClaw(platform.label, `platform ${platform.key}.label`);
  for (const door of platform.doors) {
    assertNoOpenClaw(door.label, `platform ${platform.key} door ${door.key}.label`);
    assertNoOpenClaw(door.body, `platform ${platform.key} door ${door.key}.body`);
    assert(door.label.trim().length > 0, `platform ${platform.key} door ${door.key} has a name`);
    assert(door.body.trim().length > 0, `platform ${platform.key} door ${door.key} says what it is`);
    assert(door.real, `a derived door is a door the transport actually carries`);
  }
  // The picker rule, in the form a DERIVED door can honour. An authored door
  // carries an explicit `consequence` because a human knows the specific risk
  // ("Telegram can ban your number"); the manifest cannot tell a personal
  // account login from a webhook — both arrive as `pairing` — so inventing one
  // here would be the "never invent a consequence to make a door look
  // symmetrical" this model already forbids. What the derivation CAN
  // guarantee, and what the picker actually needs, is that the faces differ:
  // each says which way in it is and where that setup happens.
  if (plan.mode === "picker") {
    const labels = new Set(platform.doors.map((d) => d.label));
    const bodies = new Set(platform.doors.map((d) => d.body));
    assert(labels.size === platform.doors.length, `${platform.key}'s doors have distinct names`);
    assert(
      bodies.size === platform.doors.length,
      `${platform.key}'s doors state distinct faces — two doors reading the same way is not a choice`,
    );
  }
}

// --- Summary ---

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}

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
  splitCredentialFields,
  openclawObservedErrorBanner,
  remediationFor,
  OPENCLAW_CAPABILITY_MISSING_BANNER_TEXT,
  OPENCLAW_UNREACHABLE_BANNER_TEXT,
  type OpenClawChannelCatalogEntry,
  type OpenClawObservedChannel,
} from "./openclaw-channel-copy";
import {
  CHANNEL_DOORS,
  CHANNEL_GRID_PLATFORMS,
  channelDoorChoiceNote,
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
import {
  CHANNEL_POPULARITY_ORDER,
  channelPopularityKeys,
  channelPopularityRank,
  compareChannelsByPopularity,
} from "./channel-popularity";
import {
  QR_NO_CODE_TEXT,
  QR_RENDER_FAILED_TEXT,
  resolveQrPanelView,
  type QrPanelInputs,
} from "./channel-qr-phase";

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
              // `hasGateway: true` throughout this matrix — it exists to
              // cover every branch REACHABLE ONLY ONCE a gateway is bound.
              // The no-gateway state (`hasGateway: false`) collapses every
              // one of these combinations to a single "needs_hardware"
              // result and is covered in its own block below, so it is not
              // part of this matrix.
              const remediation = remediationFor(e, o, reachable, true);
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

// --- `hasGateway: false`: the third fact `remediationFor` used to collapse
//     into "unknown", THE BUG THIS FILE SHIPS WITH. Before this fix, a
//     transported channel with no gateway bound at all was simply absent
//     from the grid (agentGatewayId ? [...] : [] in FleetAgentDetail.tsx),
//     so `remediationFor` was never even called in that state — there was
//     nothing to collapse because there was no card. Now that the catalog
//     loads without a gateway, "no computer paired" must read differently
//     from "a paired computer could not be reached": the first has no retry
//     that could ever help, the second is worth trying again. Run across
//     the same reachable/observed matrix as above to prove `hasGateway:
//     false` wins over EVERY other input — a card must not flicker between
//     "needs hardware" and some other state depending on stale observed data
//     left over from a previously-bound gateway. -----------------------

for (const connect_method of CONNECT_METHODS) {
  for (const requires_plugin of [true, false]) {
    for (const reachable of [true, false]) {
      for (const observedIsUndefined of [true, false]) {
        const e = entry({ connect_method, requires_plugin });
        const o = observedIsUndefined ? undefined : observed({ requires_plugin });
        const remediation = remediationFor(e, o, reachable, false);
        const caseLabel = `connect_method=${connect_method} requires_plugin=${requires_plugin} reachable=${reachable} observed=${observedIsUndefined ? "undefined" : "defined"} hasGateway=false`;

        assert(
          remediation.kind === "needs_hardware",
          `remediationFor(${caseLabel}).kind must be "needs_hardware" regardless of reachable/observed — got ${JSON.stringify(remediation.kind)}`,
        );
        assertNoOpenClaw(remediation.detail, `remediationFor(${caseLabel}).detail`);
        assert(
          !/could not be reached|unknown/i.test(remediation.detail),
          `remediationFor(${caseLabel}).detail must not borrow the "unreachable" copy — no gateway bound is a different fact, got ${JSON.stringify(remediation.detail)}`,
        );
        assert(
          /computer/i.test(remediation.detail) && /hardware/i.test(remediation.detail),
          `remediationFor(${caseLabel}).detail must point at the Hardware tab like the first-party no-gateway hint does, got ${JSON.stringify(remediation.detail)}`,
        );

        const pill = channelCardPill(remediation);
        assertNoOpenClaw(pill.label, `channelCardPill(${caseLabel}).label`);
        assert(
          pill.label === "Needs Gateway",
          `channelCardPill(${caseLabel}).label must read "Needs Gateway" — VERBATIM what channelStatePill (FleetAgentDetail.tsx) shows a first-party card in this exact state, so the two halves of the grid read as one vocabulary. Got ${JSON.stringify(pill.label)}`,
        );
        assert(
          pill.tone === "gateway",
          `channelCardPill(${caseLabel}).tone must be "gateway" — the same tone the first-party "Needs Gateway" pill uses. Got ${JSON.stringify(pill.tone)}`,
        );
        const mechanism = /\bplugin(s)?\b|\bnpm\b|\bpackage\b|\bbinary\b/i;
        assert(
          !mechanism.test(remediation.detail),
          `remediationFor(${caseLabel}).detail must not name mechanism, got ${JSON.stringify(remediation.detail)}`,
        );
      }
    }
  }
}

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

// The first-party platform labels render inside the SAME card grid as the
// transported ones, so they get the same treatment as the manifest labels
// above. NO LONGER PINNED: CHANNEL_GRID_PLATFORMS moved into channel-doors.ts
// (2026-08-11) precisely so this drives the REAL list. A pinned copy of it
// could only ever confirm itself, and would silently stop covering an eighth
// platform the moment one was added.
const FIRST_PARTY_CHANNEL_GRID_LABELS = CHANNEL_GRID_PLATFORMS.map((platform) => platform.label);
assert(FIRST_PARTY_CHANNEL_GRID_LABELS.length > 0, "CHANNEL_GRID_PLATFORMS is non-empty");
for (const label of FIRST_PARTY_CHANNEL_GRID_LABELS) {
  assertNoOpenClaw(label, `CHANNEL_GRID_PLATFORMS label ${JSON.stringify(label)}`);
  assert(label.trim().length > 0, `CHANNEL_GRID_PLATFORMS label is non-empty`);
}
// Every first-party card must have doors, or clicking it opens a panel with
// nothing in it. Two lists that were only informally the same set — now
// asserted, which is the point of moving the grid list next to the door table.
for (const platform of CHANNEL_GRID_PLATFORMS) {
  assert(
    planChannelDoors(platform.id).mode !== "none",
    `CHANNEL_GRID_PLATFORMS entry ${platform.id} must have at least one real door — a card that opens nothing is a dead control`,
  );
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

  // THE CARD FACE SAYS A CHOICE IS COMING, or the picker is a surprise. Driven
  // off the same plan the picker itself is driven off, so the face and the
  // panel behind it cannot disagree, and asserted as the RULE (a note exactly
  // when there is a picker) rather than per channel.
  const choiceNote = channelDoorChoiceNote(plan);
  if (plan.mode === "picker") {
    assert(!!choiceNote, `${channelId} opens a picker, so its card face must say a choice is coming`);
    assert(
      (choiceNote || "").includes(String(plan.doors.length)),
      `${channelId}'s card note must carry the real door count, got ${JSON.stringify(choiceNote)}`,
    );
    assertNoOpenClaw(choiceNote || "", `${channelId} card choice note`);
    assert(
      !/\bplugin(s)?\b|\bnpm\b|\bpackage\b|\bbinary\b/i.test(choiceNote || ""),
      `${channelId}'s card note must not name mechanism, got ${JSON.stringify(choiceNote)}`,
    );
    assert(
      (choiceNote || "").length <= 24,
      `${channelId}'s card note must fit a 4-across tile (<=24 chars), got ${JSON.stringify(choiceNote)}`,
    );
  } else {
    assert(
      choiceNote === null,
      `${channelId} has no choice to offer, so its card face says nothing extra — got ${JSON.stringify(choiceNote)}`,
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

// 2026-08-14 full OpenClaw channel cutover: Telegram's "full_account" door
// (real MTProto/gramjs session — the one that got the founder's own account
// banned) is DELETED along with the first-party gramjs runtime it opened.
// OpenClaw's pinned build has no personal-account credential shape for
// Telegram at all, so bot-only is now the ONLY way in — not a choice. The
// picker is gone with it: one real door means planDoors() resolves this
// card to "direct" mode, and the honest statement that Telegram runs as a
// bot here lives in the remaining door's own body text, asserted below.
const telegramDoorPlan = planChannelDoors("sage_telegram_hosted");
const telegramDoors: ChannelDoor[] = telegramDoorPlan.doors;
assert(telegramDoorPlan.mode === "direct", "Telegram is single-door (direct mode) now that full_account is gone");
assert(telegramDoors.length === 1, "Telegram has exactly one real door left");
const telegramChatbot = telegramDoors.find((d) => d.key === "byo_bot");
assert(!!telegramChatbot, "Telegram's remaining door is the chatbot");
assert(telegramChatbot?.consequence?.tone === "safe", "Telegram's chatbot door is toned as the safe one");
assert(
  !telegramChatbot?.requiresHardware,
  "Telegram's chatbot door needs no computer",
);
assert(
  /runs as a Telegram bot/i.test(telegramChatbot?.body || ""),
  "Telegram's door body plainly states it runs as a bot, not a personal account",
);
assert(
  !telegramDoors.some((d) => d.key === "full_account"),
  "Telegram's full_account door must not exist — it would route to the deleted gramjs runtime",
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
  // The card-face choice signal is ONE rule across both halves of the grid: a
  // transported platform with several ways in says so exactly like Telegram
  // does, off the same plan.
  const platformNote = channelDoorChoiceNote(plan);
  assert(
    plan.mode === "picker" ? !!platformNote : platformNote === null,
    `${platform.key}'s card note must follow the door count (${platform.doors.length} doors, note ${JSON.stringify(platformNote)})`,
  );
  assertNoOpenClaw(platformNote || "", `platform ${platform.key} card choice note`);
}

// --- GRID ORDER (channel-popularity.ts). -------------------------------
//
// The one authored table on this surface, and the reason it is allowed is that
// it is a PREFIX rather than a membership test: it decides what leads, never
// what exists. So the assertions below are about that property, not about any
// particular channel's position — reordering the founder's ranking must be free,
// and dropping the graceful-fallback property must not be.

assert(CHANNEL_POPULARITY_ORDER.length > 0, "the grid ranking is non-empty");
assert(
  new Set(CHANNEL_POPULARITY_ORDER).size === CHANNEL_POPULARITY_ORDER.length,
  "the grid ranking has no duplicate keys — a repeated key is a rank nobody can reach",
);
for (const key of CHANNEL_POPULARITY_ORDER) {
  assert(
    /^[a-z0-9]+$/.test(key),
    `ranking key ${JSON.stringify(key)} must be normalised (lowercase alphanumerics) or it can never match a label`,
  );
  assertNoOpenClaw(key, `ranking key ${JSON.stringify(key)}`);
}

// EVERY RANKED KEY MUST NAME SOMETHING THAT IS ACTUALLY RENDERED. The expected
// set (the authored ranking) and the actual set (the transport's own manifest
// plus the real first-party grid list) come from different places, so this can
// genuinely fail — which is what makes it worth having. A key left behind after
// upstream renames a channel is dead data that silently ranks nothing.
const renderedGridLabels = [
  ...FIRST_PARTY_CHANNEL_GRID_LABELS,
  ...manifest.channels.map((channel) => channel.label),
];
const reachableKeys = new Set(renderedGridLabels.flatMap((label) => channelPopularityKeys(label)));
for (const key of CHANNEL_POPULARITY_ORDER) {
  assert(
    reachableKeys.has(key),
    `ranking key ${JSON.stringify(key)} matches no channel this grid renders — a rank for nothing is stale data`,
  );
}

// The ranking's own contract, asserted directly.
assert(
  channelPopularityRank(CHANNEL_POPULARITY_ORDER[0]) === 0,
  "the first ranking key ranks first",
);
assert(
  channelPopularityRank("Not A Real Channel") === Number.POSITIVE_INFINITY,
  "an unranked channel has no rank at all, rather than a middling one",
);
// "WeChat / WeCom" is the whole reason a leading-word fallback exists: nobody
// should have to author `wechatwecom`.
assert(
  channelPopularityRank("WeChat / WeCom") < Number.POSITIVE_INFINITY,
  "a compound first-party label is ranked via its leading word",
);
// ...but the WHOLE label is tried first, so a vendor word can never silently
// rank a second product that merely shares it.
assert(
  channelPopularityKeys("Microsoft Teams")[0] === "microsoftteams",
  "the whole label is the first ranking key, ahead of the leading word",
);

// THE ACTUAL ORDERING, over the real grid contents: every first-party card plus
// every transported PLATFORM (grouped, so Zalo is one entry, not three).
const gridOrderCards = [
  ...FIRST_PARTY_CHANNEL_GRID_LABELS.map((label) => ({ label })),
  ...platforms.map((platform) => ({ label: platform.label })),
];
const sortedGrid = [...gridOrderCards].sort(compareChannelsByPopularity);

// Ranked before unranked, always — the grid leads with the messengers people
// use and ends with the ones they have not heard of.
const firstUnranked = sortedGrid.findIndex((card) => channelPopularityRank(card.label) === Number.POSITIVE_INFINITY);
if (firstUnranked >= 0) {
  assert(
    sortedGrid
      .slice(firstUnranked)
      .every((card) => channelPopularityRank(card.label) === Number.POSITIVE_INFINITY),
    "no ranked channel sorts after an unranked one — the ranking is a prefix of the grid",
  );
  // ...and the unranked tail is alphabetical among itself, so it is at least
  // scannable rather than arbitrary.
  const tail = sortedGrid.slice(firstUnranked).map((card) => card.label);
  assert(
    tail.every((label, i) => i === 0 || tail[i - 1].localeCompare(label) <= 0),
    `the unranked tail is alphabetical, got ${JSON.stringify(tail)}`,
  );
}
assert(
  channelPopularityRank(sortedGrid[0].label) === 0 ||
    sortedGrid.slice(0, 3).some((card) => /whatsapp/i.test(card.label)),
  "the grid opens with the top-ranked messenger",
);
// The two the founder actually called out on the live screen: alphabetical put
// ClickClack above Discord and Nostr above WhatsApp.
for (const [popular, obscure] of [
  ["Discord", "ClickClack"],
  ["WhatsApp", "Nostr"],
  ["Telegram", "Tlon"],
] as const) {
  const popularIndex = sortedGrid.findIndex((card) => card.label === popular);
  const obscureIndex = sortedGrid.findIndex((card) => card.label === obscure);
  if (popularIndex >= 0 && obscureIndex >= 0) {
    assert(
      popularIndex < obscureIndex,
      `${popular} must lead ${obscure} in the grid (got ${popularIndex} vs ${obscureIndex})`,
    );
  }
}

// A CHANNEL UPSTREAM HAS NOT SHIPPED STILL LANDS CORRECTLY, WITH NO CODE
// CHANGE. This is the whole claim the "prefix, not membership test" design
// makes, and a synthetic input is the only way to prove it. A synthetic INPUT,
// never a synthetic expectation.
const syntheticUnranked = { label: "Mumblewire" }; // absent from the ranking by construction
assert(
  channelPopularityRank(syntheticUnranked.label) === Number.POSITIVE_INFINITY,
  "the synthetic channel really is unranked, or this proves nothing",
);
const withSynthetic = [...gridOrderCards, syntheticUnranked].sort(compareChannelsByPopularity);
const syntheticIndex = withSynthetic.findIndex((card) => card.label === syntheticUnranked.label);
assert(
  withSynthetic
    .slice(0, syntheticIndex)
    .some((card) => channelPopularityRank(card.label) < Number.POSITIVE_INFINITY),
  "a brand-new channel sorts BELOW every ranked one with no code change",
);
assert(
  withSynthetic
    .slice(syntheticIndex + 1)
    .every((card) => channelPopularityRank(card.label) === Number.POSITIVE_INFINITY),
  "and only unranked channels follow it",
);
// Alphabetically placed among its unranked peers, not simply dumped last.
const unrankedWithSynthetic = withSynthetic
  .filter((card) => channelPopularityRank(card.label) === Number.POSITIVE_INFINITY)
  .map((card) => card.label);
assert(
  unrankedWithSynthetic.every((label, i) => i === 0 || unrankedWithSynthetic[i - 1].localeCompare(label) <= 0),
  `the unranked tail stays alphabetical once a new channel joins it, got ${JSON.stringify(unrankedWithSynthetic)}`,
);

// Two unranked channels compare as +Infinity vs +Infinity. Subtracting those
// gives NaN, which Array.prototype.sort reads as "these two are equal" and
// leaves the tail in an order that depends on the input — the comparator must
// never produce NaN.
for (const [a, b] of [
  ["Mumblewire", "Nostr"],
  ["Nostr", "Mumblewire"],
  ["Nostr", "Nostr"],
] as const) {
  assert(
    Number.isFinite(compareChannelsByPopularity({ label: a }, { label: b })),
    `comparing two unranked channels (${a}, ${b}) must produce a real number, never NaN`,
  );
}

// --- THE QR PANEL'S STATE (channel-qr-phase.ts). -----------------------
//
// The founder's bug was a spinner drawn above a button reading "Generate QR
// code" — the panel telling him to start something it had already started. The
// fix is that ONE function decides both, so the two can no longer disagree; the
// assertions below are what makes that a guarantee rather than a claim, by
// driving EVERY combination of its six inputs (2^6 = 64) and refuting the
// pairing in both directions. A behavioural check on the live screen can only
// ever cover the states that screen happened to be in.

const QR_INPUT_COUNT = 6;
const qrCases: QrPanelInputs[] = [];
for (let bits = 0; bits < 1 << QR_INPUT_COUNT; bits += 1) {
  const on = (index: number) => (bits & (1 << index)) !== 0;
  qrCases.push({
    qrImageReady: on(0),
    requestInFlight: on(1),
    // The only non-boolean input, and the string is the one a real failure
    // carries — so "a failure always says what happened" is driven by a value
    // the panel could actually hold.
    errorText: on(2) ? "Heads up: the Gateway is offline right now — check it's running and try again." : null,
    codeIssued: on(3),
    accepted: on(4),
    waitExpired: on(5),
  });
}
assert(qrCases.length === 64, "every combination of the QR panel's inputs is covered");

const qrPhasesSeen = new Set<string>();
for (const inputs of qrCases) {
  const view = resolveQrPanelView(inputs);
  const where = `QR panel ${JSON.stringify(inputs)}`;
  qrPhasesSeen.add(view.phase);

  // THE INVARIANT. Both directions, because the bug is symmetric: a spinner
  // beside a start control is the same lie whichever one is wrong.
  assert(
    !(view.showSpinner && view.startControlLabel !== null),
    `${where} must never show a spinner and a start control together — that is the contradiction this module exists to remove`,
  );
  // A control that cannot be used is NOT RENDERED (product law: no dead
  // controls). Expressed as "the label is null", never as a disabled button —
  // a disabled "Generate QR code" under a running spinner still says starting
  // it is the customer's job.
  assert(
    (view.startControlLabel !== null) === (view.phase === "idle" || view.phase === "failed"),
    `${where}: a control exists exactly in the phases where nothing is running, got ${JSON.stringify(view.startControlLabel)} in ${view.phase}`,
  );
  // An image and a spinner are the same slot — nothing is ever drawn twice over.
  assert(!(view.showQrImage && view.showSpinner), `${where} must not draw a QR and a spinner in the same frame`);
  assert(view.showQrImage === (view.phase === "ready"), `${where}: the QR shows exactly when there is one`);
  assert(
    view.showSpinner === (view.phase === "starting" || view.phase === "waiting"),
    `${where}: the spinner runs exactly while something is running`,
  );
  // A FAILURE ALWAYS SAYS WHAT HAPPENED. The wait running out carries no error
  // of its own, which is precisely the case that would otherwise present as a
  // stopped spinner with no explanation.
  assert(
    (view.failureText !== null) === (view.phase === "failed"),
    `${where}: a failure says what happened, and nothing else claims one`,
  );
  if (view.failureText !== null) {
    assert(view.failureText.trim().length > 0, `${where}: the failure line is not blank`);
  }
  // The scan instruction is only correct once there is something to scan —
  // telling someone to scan an empty frame is the same category of lie.
  assert(view.hint.trim().length > 0, `${where}: the hint is never blank`);
  assert(
    view.hint.includes("scan:") === (view.phase === "ready"),
    `${where}: only a ready panel tells the customer to scan the thing in front of them`,
  );

  // Same copy rules as every other string on this surface.
  for (const [text, what] of [
    [view.hint, "hint"],
    [view.failureText, "failure line"],
    [view.startControlLabel, "start control"],
  ] as const) {
    if (!text) continue;
    assertNoOpenClaw(text, `${where} ${what}`);
    assert(
      !/\bplugin(s)?\b|\bnpm\b|\bpackage\b|\bbinary\b/i.test(text),
      `${where} ${what} must not name mechanism, got ${JSON.stringify(text)}`,
    );
  }
}
// Every phase is reachable. A state machine with a state nothing can enter is a
// branch that has never run.
for (const phase of ["idle", "starting", "waiting", "ready", "failed"]) {
  assert(qrPhasesSeen.has(phase), `the QR panel can actually reach "${phase}"`);
}

// THE FOUNDER'S EXACT SCREEN, pinned as its own case: the request has been
// accepted, no code has come back yet, nothing of ours is in flight. This is
// the state that produced "I have not clicked this button that says generate QR
// code. Yet the QR code is already being generated right there."
const acceptedNoCodeYet = resolveQrPanelView({
  qrImageReady: false,
  requestInFlight: false,
  errorText: null,
  codeIssued: false,
  accepted: true,
  waitExpired: false,
});
assert(acceptedNoCodeYet.phase === "waiting", "an accepted request with no code yet is WAITING, not idle");
assert(acceptedNoCodeYet.showSpinner, "...so it spins");
assert(acceptedNoCodeYet.startControlLabel === null, "...and offers NO control to start what is already running");

// THE DISCONNECT -> REOPEN PATH, which is the regression this fix could most
// easily cause: the panel auto-starts on reopen, so the very first paint after
// a disconnect must already read as "starting" rather than flashing the idle
// control for a frame. The component folds its pending auto-start into
// `requestInFlight` for exactly this; assert the consequence.
function reopenedAfterDisconnect(overrides: Partial<QrPanelInputs> = {}): QrPanelInputs {
  return {
    qrImageReady: false,
    requestInFlight: true, // busy || autoStartPending, on the first render back in idle
    errorText: null,
    codeIssued: false,
    accepted: false,
    waitExpired: false,
    ...overrides,
  };
}
const reopened = resolveQrPanelView(reopenedAfterDisconnect());
assert(reopened.phase === "starting", "reopening after a disconnect starts immediately");
assert(reopened.startControlLabel === null, "...and never shows a start control over its own auto-start");
assert(reopened.showSpinner, "...it says so, rather than showing an empty frame");
// A stale error left over from the disconnect itself must not paint the new
// attempt as failed — an in-flight request outranks it. This is why the phase
// ORDER lives in the module rather than being re-derived at each element.
assert(
  resolveQrPanelView(reopenedAfterDisconnect({ errorText: "Heads up: something went wrong." })).phase === "starting",
  "a stale error never masks a request that is currently running",
);
// ...and the attempt it starts carries all the way to a rendered code, which is
// the whole point of the path: disconnect -> reopen -> QR, no restart.
assert(
  resolveQrPanelView(reopenedAfterDisconnect({ requestInFlight: false, accepted: true })).phase === "waiting",
  "the reopened attempt is accepted and waits",
);
assert(
  resolveQrPanelView(reopenedAfterDisconnect({ requestInFlight: false, accepted: true, codeIssued: true })).phase ===
    "waiting",
  "a code that has arrived but is not drawn yet still reads as work in progress",
);
const reopenedReady = resolveQrPanelView(
  reopenedAfterDisconnect({ requestInFlight: false, accepted: true, codeIssued: true, qrImageReady: true }),
);
assert(reopenedReady.phase === "ready", "...and the reopened panel reaches a QR");
assert(reopenedReady.showQrImage && !reopenedReady.showSpinner, "...with the code on screen and the spinner gone");
assert(
  reopenedReady.startControlLabel === null,
  "...and no control asking to generate the code already in front of the customer",
);

// A wait that runs out ends in a control and an explanation, never a stopped
// spinner. The customer must always have a way out.
const waitRanOut = resolveQrPanelView({
  qrImageReady: false,
  requestInFlight: false,
  errorText: null,
  codeIssued: false,
  accepted: true,
  waitExpired: true,
});
assert(waitRanOut.phase === "failed", "a wait that runs out is a failure, not an eternal spinner");
assert(!waitRanOut.showSpinner, "...the spinner stops");
assert(waitRanOut.failureText === QR_NO_CODE_TEXT, "...it says what happened, in its own words");
assert(waitRanOut.startControlLabel === "Try again", "...and the control says it is retrying, not starting");

// The two authored sentences reach the DOM verbatim, so they answer to the same
// copy rules as everything else on this surface.
for (const [text, what] of [
  [QR_NO_CODE_TEXT, "QR_NO_CODE_TEXT"],
  [QR_RENDER_FAILED_TEXT, "QR_RENDER_FAILED_TEXT"],
] as const) {
  assertNoOpenClaw(text, what);
  assert(
    !/\bplugin(s)?\b|\bnpm\b|\bpackage\b|\bbinary\b/i.test(text),
    `${what} must not name mechanism, got ${JSON.stringify(text)}`,
  );
  assert(text.trim().length > 0, `${what} is a real sentence`);
}

// --- openclawObservedErrorBanner: the 2026-08-13 audit's #2/#2b fix.
//     "the box could not be reached" and "the box answered fine but never
//     had the transport installed" are different facts, and must not share
//     copy OR a retry control. Driven off the STRUCTURED reason code, never
//     a text-match — a raw backend token must never leak through here
//     either. ---------------------------------------------------------

assert(
  openclawObservedErrorBanner(null, null) === null,
  "no observed_error at all means no banner, regardless of any stray code",
);
assert(
  openclawObservedErrorBanner("", "gateway_capability_missing") === null,
  "an empty observed_error string is not an error — falsy wins over any code",
);

{
  const capabilityMissing = openclawObservedErrorBanner(
    "gateway_capability_missing",
    "gateway_capability_missing",
  );
  assert(capabilityMissing !== null, "gateway_capability_missing produces a banner");
  assert(
    capabilityMissing!.text === OPENCLAW_CAPABILITY_MISSING_BANNER_TEXT,
    `capability-missing banner must be the dedicated sentence, got ${JSON.stringify(capabilityMissing!.text)}`,
  );
  assert(
    capabilityMissing!.retryable === false,
    "capability-missing must not be retryable — no button can install a transport that isn't there",
  );
  assertNoOpenClaw(capabilityMissing!.text, "capability-missing banner text");
  assert(
    !/\bplugin(s)?\b|\bnpm\b|\bpackage\b|\bbinary\b|\binstall\b/i.test(capabilityMissing!.text),
    `capability-missing banner must not name mechanism, got ${JSON.stringify(capabilityMissing!.text)}`,
  );
  // The raw backend token must never leak through this function either —
  // same standing rule as the rest of this file, applied to the new seam.
  assert(
    !/gateway_capability_missing/.test(capabilityMissing!.text),
    "the raw reason token must not appear in the banner text",
  );
}

for (const [code, label] of [
  ["gateway_offline", "gateway_offline"],
  ["gateway_heartbeat_stale", "gateway_heartbeat_stale"],
  ["gateway_unhealthy", "gateway_unhealthy"],
  ["gateway_capability_not_ready", "gateway_capability_not_ready (transient — retry is honest)"],
  [null, "no code at all (older response shape, or an unclassified failure)"],
  ["some_future_token_this_module_has_not_seen", "an unrecognized future token"],
] as const) {
  const banner = openclawObservedErrorBanner("some human error text from the backend", code);
  assert(banner !== null, `${label} still produces a banner when observed_error is truthy`);
  assert(
    banner!.text === OPENCLAW_UNREACHABLE_BANNER_TEXT,
    `${label} must fall back to the generic "could not be reached" copy, got ${JSON.stringify(banner!.text)}`,
  );
  assert(banner!.retryable === true, `${label} must stay retryable — re-checking is a legitimate action here`);
}

// The two banners must never collapse onto the same sentence or the same
// retryability — that collapse IS the bug this function exists to fix.
{
  const capabilityMissing = openclawObservedErrorBanner("x", "gateway_capability_missing")!;
  const offline = openclawObservedErrorBanner("x", "gateway_offline")!;
  assert(capabilityMissing.text !== offline.text, "capability-missing and offline must not share banner copy");
  assert(
    capabilityMissing.retryable !== offline.retryable,
    "capability-missing and offline must not share retryability",
  );
}

// --- THE SETUP FORM'S PRIMARY/ADVANCED SPLIT (splitCredentialFields). ----
//
// The founder, looking at Telegram's setup form: "this token web hook secret
// API host proxy and whatever ... Should be enough just pasting token?" It
// rendered all SEVEN fields OpenClaw's schema declares. Connecting Telegram is
// pasting the bot token; everything else is a mode-specific extra.
//
// Driven off the CHECKED-IN MANIFEST for the same reason the grouping check
// above is: the split is DERIVED in the generator, so the expected set (what
// upstream declares) and the actual set (what the split produces) come from
// different places. A hand-written "these fields are advanced" list per
// channel is the exact defect this whole surface exists to avoid.

type ManifestField = { name: string; secret: boolean; type: string; advanced?: boolean };
type ManifestChannelFields = { id: string; credential_shape?: { fields?: ManifestField[] } | null };

const fieldsFor = (id: string): ManifestField[] => {
  const channel = (manifest.channels as unknown as ManifestChannelFields[]).find((c) => c.id === id);
  assert(Boolean(channel), `manifest carries a channel named ${id}`);
  return channel!.credential_shape?.fields ?? [];
};

// Every field is in exactly one half — advanced HIDES fields, it never drops
// them. A field that reached neither half would be unreachable and unwritable.
for (const channel of manifest.channels as unknown as ManifestChannelFields[]) {
  const all = channel.credential_shape?.fields ?? [];
  const { primary, advanced } = splitCredentialFields(all);
  assert(
    primary.length + advanced.length === all.length,
    `${channel.id}: every credential field lands in exactly one half (${all.length} in, ${primary.length}+${advanced.length} out)`,
  );
}

// The founder's own case, stated exactly: Telegram's form asks for the bot
// token and nothing else.
{
  const { primary, advanced } = splitCredentialFields(fieldsFor("telegram"));
  assert(
    primary.length === 1 && primary[0].name === "botToken",
    `telegram's primary form is exactly botToken, got ${JSON.stringify(primary.map((f) => f.name))}`,
  );
  assert(advanced.length > 0, "telegram's other schema fields survive under Advanced, never deleted");
}

// The counterweight, and the reason the rule may not simply be "one secret".
// Feishu genuinely cannot connect without appId AND appSecret together, so a
// split that made Telegram pretty by keeping only the secret would break it.
{
  const { primary } = splitCredentialFields(fieldsFor("feishu"));
  const names = primary.map((f) => f.name).sort();
  assert(
    names.length === 2 && names[0] === "appId" && names[1] === "appSecret",
    `feishu's primary form is appId + appSecret, got ${JSON.stringify(names)}`,
  );
}

// Every channel that takes a pasted credential must still ask for at least one
// thing. A primary half emptied to zero is a form nobody can submit — the
// failure mode a too-aggressive rule produces, and it is silent.
for (const channel of manifest.channels as unknown as ManifestChannelFields[]) {
  const all = channel.credential_shape?.fields ?? [];
  if (all.length === 0) continue;
  const { primary } = splitCredentialFields(all);
  assert(primary.length > 0, `${channel.id}: a channel with credential fields keeps at least one primary field`);
}

// A field carrying no `advanced` flag is PRIMARY. A manifest generated before
// this axis existed must render as it always did, never collapse out of sight.
{
  const { primary, advanced } = splitCredentialFields([
    { name: "legacyToken", secret: true, type: "string" },
  ] as never);
  assert(primary.length === 1 && advanced.length === 0, "an unflagged field defaults to primary");
}

// --- Summary ---

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}

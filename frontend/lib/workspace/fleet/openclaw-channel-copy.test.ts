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
 * Run: npx tsx lib/workspace/fleet/openclaw-channel-copy.test.ts
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import {
  formatChannelList,
  remediationFor,
  type OpenClawChannelCatalogEntry,
  type OpenClawObservedChannel,
} from "./openclaw-channel-copy";

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
  label: string;
  credential_shape?: { selection_label?: string; docs_path?: string | null } | null;
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
  "This agent's computer could not be reached, so some rows below show an unknown state. Their setup fields are still accurate.",
  "unified Channels tab observed-error banner (gateway state, pinned literal)",
);
assertNoOpenClaw("Reading this agent's computer…", "unified Channels tab loading line (gateway state, pinned literal)");
assertNoOpenClaw("connect elsewhere in Empyralis and", "unified Channels tab superseded-channel note (pinned literal fragment)");
assertNoOpenClaw("isn't shown here", "unified Channels tab superseded-channel note (pinned literal fragment)");

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

// --- Summary ---

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}

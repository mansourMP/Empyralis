import test from "node:test";
import assert from "node:assert/strict";

import {
  activeFirstPartyPersonalChannels,
  isFirstPartyChannelOwnedByOpenClaw,
  openClawPlatformIdForChannelKey,
} from "../openclaw/transport-ownership";
import { GENERATED_OPENCLAW_ACTIVE_CHANNEL_IDS } from "../openclaw/generated-openclaw-channels";
import { WHATSAPP_PERSONAL_CHANNEL_KEY } from "../channels/whatsapp/session-store";
import { TELEGRAM_PERSONAL_CHANNEL_KEY } from "../channels/telegram/session-store";
import { LOCAL_BRIDGE_PERSONAL_CHANNEL_CONFIGS } from "../channels/local-bridge-runtime";

// ===========================================================================
// The one missing wire in index.ts's main(): first-party personal-channel
// runtime construction (WhatsAppPersonalRuntime, TelegramPersonalRuntime,
// the local-bridge family) used to be gated on ONE flag
// (config.personalChannelsEnabled) with no reference at all to
// channel_lane_contract_service.OPENCLAW_TRANSPORT_OWNERSHIP — the single
// source of truth every OTHER consumer (provisioning, inbound routing, the
// gateway's own OpenClaw-side capability advertisement and runtime
// registration) already reads through generated-openclaw-channels.ts.
//
// These tests prove two things, per the explicit ask: (1) with
// OPENCLAW_CUT_OVER_CHANNEL_IDS still empty — true today — this gate is a
// STRICT NO-OP: every first-party runtime that constructs today still
// constructs after this change, proven against the REAL generated constant,
// not a stub. (2) the gate actually fires when a platform genuinely has
// been cut over, proven by simulating that (via the optional
// activeChannelIds parameter, never by mutating the generated file) —
// without this second test, the first proves nothing about whether the
// mechanism works, only that it currently does nothing.
// ===========================================================================

const REAL_FIRST_PARTY_CHANNEL_KEYS: readonly string[] = [
  WHATSAPP_PERSONAL_CHANNEL_KEY,
  TELEGRAM_PERSONAL_CHANNEL_KEY,
  ...LOCAL_BRIDGE_PERSONAL_CHANNEL_CONFIGS.map((config) => config.channelKey),
];

test("today, with the real generated GENERATED_OPENCLAW_ACTIVE_CHANNEL_IDS (OPENCLAW_CUT_OVER_CHANNEL_IDS is empty), " +
  "every first-party personal-channel platform this gate covers is NOT owned by OpenClaw", () => {
  for (const channelKey of REAL_FIRST_PARTY_CHANNEL_KEYS) {
    assert.equal(
      isFirstPartyChannelOwnedByOpenClaw(channelKey),
      false,
      `${channelKey} must not be reported as OpenClaw-owned while the cutover list is empty`,
    );
  }
});

test("STRICT NO-OP: filtering the real first-party candidate list against the real generated constant " +
  "constructs exactly the same runtimes as before this change", () => {
  const candidates = REAL_FIRST_PARTY_CHANNEL_KEYS.map((channelKey) => ({ channelKey }));
  const active = activeFirstPartyPersonalChannels(candidates);
  assert.deepEqual(
    active.map((c) => c.channelKey),
    candidates.map((c) => c.channelKey),
    "with OPENCLAW_CUT_OVER_CHANNEL_IDS empty, the filter must drop nothing — every candidate that " +
      "would have been constructed before this change must still be constructed after it",
  );
});

test("the gate actually fires: a platform simulated as cut over (present in an active-channel-ids set) " +
  "is excluded from construction, while every other candidate is unaffected", () => {
  const candidates = [
    { channelKey: WHATSAPP_PERSONAL_CHANNEL_KEY },
    { channelKey: TELEGRAM_PERSONAL_CHANNEL_KEY },
    { channelKey: "signal_personal" },
  ];
  // Simulates the state AFTER OPENCLAW_CUT_OVER_CHANNEL_IDS gains "whatsapp"
  // and a regenerate — never by mutating the real generated file, which
  // stays untouched and still reflects today's empty cutover list.
  const simulatedActiveChannelIds = ["whatsapp"];
  const active = activeFirstPartyPersonalChannels(candidates, simulatedActiveChannelIds);
  assert.deepEqual(
    active.map((c) => c.channelKey),
    [TELEGRAM_PERSONAL_CHANNEL_KEY, "signal_personal"],
    "only the platform present in the simulated active set may be excluded",
  );
});

test("openClawPlatformIdForChannelKey: strips the _personal suffix for the ordinary case", () => {
  assert.equal(openClawPlatformIdForChannelKey(WHATSAPP_PERSONAL_CHANNEL_KEY), "whatsapp");
  assert.equal(openClawPlatformIdForChannelKey(TELEGRAM_PERSONAL_CHANNEL_KEY), "telegram");
  assert.equal(openClawPlatformIdForChannelKey("signal_personal"), "signal");
  assert.equal(openClawPlatformIdForChannelKey("imessage_personal"), "imessage");
});

test("openClawPlatformIdForChannelKey: wechat_personal maps to OpenClaw's actual id (openclaw-weixin), " +
  "not a bare suffix strip, and never to wecom (a different product)", () => {
  assert.equal(openClawPlatformIdForChannelKey("wechat_personal"), "openclaw-weixin");
  assert.notEqual(openClawPlatformIdForChannelKey("wechat_personal"), "wecom");
});

test("openClawPlatformIdForChannelKey: an unrecognized channel_key passes through rather than throwing " +
  "(fails toward keeping a channel first-party, never toward silently misclassifying it)", () => {
  assert.equal(openClawPlatformIdForChannelKey("some_future_channel"), "some_future_channel");
  assert.equal(openClawPlatformIdForChannelKey(""), "");
});

test("isFirstPartyChannelOwnedByOpenClaw: honors the suffix mapping when checking against a simulated " +
  "active set, not just the raw channel_key", () => {
  assert.equal(isFirstPartyChannelOwnedByOpenClaw("wechat_personal", ["openclaw-weixin"]), true);
  assert.equal(isFirstPartyChannelOwnedByOpenClaw("wechat_personal", ["wechat_personal"]), false);
});

test("sanity: GENERATED_OPENCLAW_ACTIVE_CHANNEL_IDS (the real, checked-in generated constant) contains " +
  "none of the platforms any current first-party personal channel maps to — the precondition every " +
  "other test in this file assumes rather than asserts directly", () => {
  const platformIds = new Set(REAL_FIRST_PARTY_CHANNEL_KEYS.map(openClawPlatformIdForChannelKey));
  for (const activeId of GENERATED_OPENCLAW_ACTIVE_CHANNEL_IDS) {
    assert.equal(
      platformIds.has(activeId),
      false,
      `${activeId} is in the real active set AND maps to a first-party channel this gate covers — ` +
        "the no-op test above should have caught this; investigate before trusting it",
    );
  }
});

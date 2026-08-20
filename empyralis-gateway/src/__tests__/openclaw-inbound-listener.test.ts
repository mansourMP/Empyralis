import test from "node:test";
import assert from "node:assert/strict";

import {
  OpenClawInboundListener,
  OPENCLAW_INBOUND_PATH,
} from "../openclaw/inbound-listener";
import { mapOpenClawInboundBody, normalizeOpenClawChannelKey } from "../openclaw/inbound-payload";
import {
  openClawTransportCapabilities,
  openClawTransportChannelKeys,
  setOpenClawTransportEnabled,
} from "../openclaw/capabilities";
import type { GatewayChannelInboundPayload } from "../protocol/types";

const TOKEN = "test-bridge-secret";

function baseBody(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    schema: "empyralis.openclaw_bridge.inbound.v1",
    channel: "line",
    accountId: "acct-1",
    conversationId: "C-999",
    senderId: "U-42",
    messageId: "M-7",
    content: "hello there",
    timestamp: 1_754_600_000_000,
    isGroup: undefined,
    wasMentioned: undefined,
    isReply: false,
    media: {},
    rawMetadata: { senderName: "Ada", channelName: undefined },
    receivedAt: "2026-08-08T10:00:00.000Z",
    ...overrides,
  };
}

class RecordingPublisher {
  readonly published: GatewayChannelInboundPayload[] = [];
  shouldThrow: Error | null = null;

  async publishEvent(type: "channel.inbound", payload: GatewayChannelInboundPayload): Promise<void> {
    assert.equal(type, "channel.inbound");
    if (this.shouldThrow) throw this.shouldThrow;
    this.published.push(payload);
  }
}

async function withListener(
  fn: (url: string, publisher: RecordingPublisher, journal: Array<[string, Record<string, unknown>]>) => Promise<void>,
): Promise<void> {
  const publisher = new RecordingPublisher();
  const journal: Array<[string, Record<string, unknown>]> = [];
  // Port 0 is not permitted by the constructor guard (a real deployment must
  // name its port), so pick a high port and retry-free bind.
  const port = 18_000 + Math.floor(Math.random() * 2_000);
  const listener = new OpenClawInboundListener({
    port,
    token: TOKEN,
    publisher,
    record: async (messageType, payload) => {
      journal.push([messageType, payload]);
    },
  });
  await listener.start();
  try {
    await fn(`http://127.0.0.1:${port}`, publisher, journal);
  } finally {
    await listener.stop();
  }
}

// ── mapping ────────────────────────────────────────────────────────────

test("mapper rejects an unrecognized schema rather than best-effort parsing it", () => {
  const result = mapOpenClawInboundBody(baseBody({ schema: "something.else.v9" }));
  assert.equal(result.ok, false);
  assert.equal(result.ok === false && result.error, "unsupported_schema");
});

test("mapper rejects a channel id that could forge a first-party channel_key", () => {
  for (const bad of ["../telegram", "telegram personal", "TELEGRAM/x", "", "a".repeat(80)]) {
    const result = mapOpenClawInboundBody(baseBody({ channel: bad }));
    assert.equal(result.ok, false, `expected rejection for ${JSON.stringify(bad)}`);
  }
  assert.equal(normalizeOpenClawChannelKey("line"), "openclaw_line");
  assert.equal(normalizeOpenClawChannelKey("../telegram"), undefined);
});

test("mapper never coerces unknown group-ness or mention to false", () => {
  const result = mapOpenClawInboundBody(baseBody());
  assert.equal(result.ok, true);
  if (!result.ok) return;
  // The whole point: absent, not false. `_OpenClawPersonalChannelHandler`
  // upgrades absent -> group; a literal false would have downgraded it to a
  // DM and skipped Gates 2 and 3.
  assert.equal("is_group" in result.payload.message, false);
  assert.equal("is_mentioned" in result.payload.message, false);
  assert.equal("is_reply_to_sage" in result.payload.message, false);
  assert.equal(result.payload.provider, "openclaw");
  assert.equal(result.payload.channel_key, "openclaw_line");
});

test("mapper passes an explicitly asserted group/mention through unchanged", () => {
  const result = mapOpenClawInboundBody(baseBody({ isGroup: true, wasMentioned: true }));
  assert.equal(result.ok, true);
  if (!result.ok) return;
  assert.equal(result.payload.message.is_group, true);
  assert.equal(result.payload.message.is_mentioned, true);
});

test("mapper never asserts is_reply_to_sage from a plain reply", () => {
  const result = mapOpenClawInboundBody(baseBody({ isReply: true, replyToId: "M-1", replyToSender: "someone" }));
  assert.equal(result.ok, true);
  if (!result.ok) return;
  assert.equal("is_reply_to_sage" in result.payload.message, false);
  assert.equal(result.payload.message.quoted_stanza_id, "M-1");
});

test("mapper derives a DETERMINISTIC external_message_id when OpenClaw omits one", () => {
  const body = baseBody({ messageId: undefined });
  const a = mapOpenClawInboundBody(body);
  const b = mapOpenClawInboundBody(body);
  assert.equal(a.ok && b.ok, true);
  if (!a.ok || !b.ok) return;
  assert.equal(a.payload.message.external_message_id, b.payload.message.external_message_id);
  assert.match(a.payload.message.external_message_id, /^openclaw-derived-[0-9a-f]{32}$/);
});

test("mapper drops media and refuses a media-only message", () => {
  const withText = mapOpenClawInboundBody(baseBody({ media: { path: "/tmp/openclaw/photo.jpg", type: "image/jpeg" } }));
  assert.equal(withText.ok, true);
  if (!withText.ok) return;
  assert.equal(withText.droppedMedia, true);
  assert.equal(withText.payload.message.media, undefined);

  const mediaOnly = mapOpenClawInboundBody(
    baseBody({ content: "   ", media: { path: "/tmp/openclaw/photo.jpg" } }),
  );
  assert.equal(mediaOnly.ok, false);
  assert.equal(mediaOnly.ok === false && mediaOnly.error, "media_only_unsupported");
});

// ── listener ───────────────────────────────────────────────────────────

test("listener refuses to exist without a token", () => {
  assert.throws(
    () =>
      new OpenClawInboundListener({
        port: 18999,
        token: "   ",
        publisher: new RecordingPublisher(),
      }),
    /refuses to start without EMPYRALIS_BRIDGE_TOKEN/,
  );
});

test("listener binds loopback only", async () => {
  const publisher = new RecordingPublisher();
  const port = 18_000 + Math.floor(Math.random() * 2_000);
  const listener = new OpenClawInboundListener({ port, token: TOKEN, publisher });
  await listener.start();
  try {
    // Asserted from the bound socket, not from a connection attempt:
    // 0.0.0.0 is routed to loopback on macOS, so a "can't reach it from
    // outside" probe would pass even for a wide-open bind.
    assert.equal(listener.boundHost(), "127.0.0.1");
    assert.equal(listener.address(), port);
    const health = await fetch(`http://127.0.0.1:${port}/openclaw/health`);
    assert.equal(health.status, 200);
  } finally {
    await listener.stop();
  }

  // And a non-loopback local interface, if this machine has one, must not
  // be listening on that port after the bind.
  const external = Object.values(await import("node:os").then((m) => m.networkInterfaces()))
    .flat()
    .find((entry) => entry && entry.family === "IPv4" && !entry.internal);
  if (external) {
    const net = await import("node:net");
    await new Promise<void>((resolve) => {
      const socket = net.connect({ host: external.address, port, timeout: 500 });
      socket.on("connect", () => {
        socket.destroy();
        assert.fail(`listener was reachable on non-loopback address ${external.address}`);
      });
      socket.on("error", () => resolve());
      socket.on("timeout", () => {
        socket.destroy();
        resolve();
      });
    });
  }
});

test("listener fails closed on an absent or wrong bearer token", async () => {
  await withListener(async (url, publisher, journal) => {
    const noAuth = await fetch(`${url}${OPENCLAW_INBOUND_PATH}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(baseBody()),
    });
    assert.equal(noAuth.status, 401);

    const wrongAuth = await fetch(`${url}${OPENCLAW_INBOUND_PATH}`, {
      method: "POST",
      headers: { "content-type": "application/json", authorization: "Bearer not-the-token" },
      body: JSON.stringify(baseBody()),
    });
    assert.equal(wrongAuth.status, 401);

    assert.equal(publisher.published.length, 0);
    const reasons = journal.filter(([type]) => type === "openclaw.inbound.unauthorized");
    assert.equal(reasons.length, 2);
    // The presented value must never be recorded.
    for (const [, payload] of reasons) {
      assert.equal(JSON.stringify(payload).includes("not-the-token"), false);
    }
  });
});

test("listener publishes an authorized event on the existing channel.inbound path", async () => {
  await withListener(async (url, publisher, journal) => {
    const response = await fetch(`${url}${OPENCLAW_INBOUND_PATH}`, {
      method: "POST",
      headers: { "content-type": "application/json", authorization: `Bearer ${TOKEN}` },
      body: JSON.stringify(baseBody()),
    });
    assert.equal(response.status, 202);
    assert.equal(publisher.published.length, 1);
    assert.equal(publisher.published[0].channel_key, "openclaw_line");
    assert.equal(publisher.published[0].message.text, "hello there");
    assert.equal(publisher.published[0].message.from_me, false);
    assert.ok(journal.some(([type]) => type === "openclaw.inbound.accepted"));
  });
});

test("listener answers 503 (not 200) when the gateway has no cloud scope", async () => {
  await withListener(async (url, publisher, journal) => {
    publisher.shouldThrow = new Error("Gateway scope is not active.");
    const response = await fetch(`${url}${OPENCLAW_INBOUND_PATH}`, {
      method: "POST",
      headers: { "content-type": "application/json", authorization: `Bearer ${TOKEN}` },
      body: JSON.stringify(baseBody()),
    });
    // 503 is what makes the plugin's own durable queue hold the event.
    // A 200 here would silently discard a real message.
    assert.equal(response.status, 503);
    assert.ok(journal.some(([type]) => type === "openclaw.inbound.publish_failed"));
  });
});

test("listener rejects a malformed body without publishing", async () => {
  await withListener(async (url, publisher) => {
    const response = await fetch(`${url}${OPENCLAW_INBOUND_PATH}`, {
      method: "POST",
      headers: { "content-type": "application/json", authorization: `Bearer ${TOKEN}` },
      body: JSON.stringify({ schema: "empyralis.openclaw_bridge.inbound.v1", channel: "line" }),
    });
    assert.equal(response.status, 400);
    assert.equal(publisher.published.length, 0);
  });
});

test("listener exposes nothing but the intake and health paths", async () => {
  await withListener(async (url) => {
    const wrongPath = await fetch(`${url}/anything-else`, {
      method: "POST",
      headers: { authorization: `Bearer ${TOKEN}` },
    });
    assert.equal(wrongPath.status, 404);
    const wrongMethod = await fetch(`${url}${OPENCLAW_INBOUND_PATH}`, {
      method: "GET",
      headers: { authorization: `Bearer ${TOKEN}` },
    });
    assert.equal(wrongMethod.status, 405);
  });
});

// ── capability advertisement ───────────────────────────────────────────

test("openclaw capabilities are advertised only when the bridge is configured", () => {
  setOpenClawTransportEnabled(false);
  assert.deepEqual(openClawTransportCapabilities(), []);
  setOpenClawTransportEnabled(true);
  const capabilities = openClawTransportCapabilities();
  assert.ok(capabilities.includes("channel.openclaw.line"));
  // Every advertised capability must correspond 1:1 to a channel_key the
  // mapper can actually produce, in the shape the cloud's
  // _registration_advertises_personal_channel accepts.
  assert.deepEqual(
    capabilities.map((c) => c.replace("channel.openclaw.", "openclaw_")),
    openClawTransportChannelKeys(),
  );
  setOpenClawTransportEnabled(false);
});

test("openclaw transport claims exactly the platforms cut over 2026-08-14, and no others still owned first-party", () => {
  setOpenClawTransportEnabled(true);
  const keys = openClawTransportChannelKeys();
  // Cut over: their first-party implementation is deleted, OpenClaw is now
  // the live route for every one of these.
  for (const cutOver of [
    "openclaw_whatsapp",
    "openclaw_signal",
    "openclaw_imessage",
    "openclaw_openclaw-weixin",
  ]) {
    assert.equal(keys.includes(cutOver), true, `${cutOver} should be claimed post-cutover`);
  }
  // NOT cut over: discord/slack/sms are Studio business-connector channels
  // (cloud-only, no Agent Computer today) — see
  // openclaw_channel_registry.OPENCLAW_CUT_OVER_CHANNEL_IDS's own comment for
  // why forcing them onto a hardware-bound transport would be a regression,
  // not an improvement.
  // telegram REJOINED this group 2026-08-20. It was cut over on 2026-08-14,
  // and that swept away the unrelated, still-live hosted-bot lane
  // (sage_telegram_hosted) along with the deleted gramjs personal-account
  // one — a token collision is per PLATFORM, not per LANE. Net effect,
  // verified live: a fresh cloud-only agent's Channels tab showed Telegram
  // as "Needs Gateway" with no route to the paste-a-BotFather-token flow at
  // all, i.e. exactly the "force an agent to acquire hardware it does not
  // need, for a channel that already works" regression that keeps
  // discord/slack/sms first-party. `openclaw_telegram` stays DECLARED and
  // reachable for someone who deliberately wants Telegram bundled with
  // their other OpenClaw channels on one box; it just never enters the live
  // lane maps. See openclaw_channel_registry.OPENCLAW_CUT_OVER_CHANNEL_IDS.
  for (const stillFirstParty of [
    "openclaw_telegram",
    "openclaw_discord",
    "openclaw_slack",
    "openclaw_sms",
  ]) {
    assert.equal(keys.includes(stillFirstParty), false, `${stillFirstParty} stays first-party`);
  }
  setOpenClawTransportEnabled(false);
});

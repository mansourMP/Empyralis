import { mkdtemp, rm } from "fs/promises";
import { tmpdir } from "os";
import path from "path";
import test from "node:test";
import assert from "node:assert/strict";

import {
  InboundDebouncer,
  coalesceInboundPayloads,
  defaultShouldBypass,
  DEFAULT_INBOUND_DEBOUNCE_WINDOW_MS,
  type DebounceScheduler,
} from "../channels/foundation/inbound-debounce";
import type { GatewayChannelInboundPayload } from "../protocol/types";
import { TelegramPersonalRuntime } from "../channels/telegram/runtime";
import { WhatsAppPersonalRuntime } from "../channels/whatsapp/runtime";
import { GatewayStateDb } from "../state/db";

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

/** A scheduler whose timers only fire when the test explicitly says so, so the
 *  sliding window and its resets are fully deterministic. */
function manualScheduler() {
  const timers = new Map<number, () => void>();
  let nextId = 1;
  let cleared = 0;
  const scheduler: DebounceScheduler = {
    set: (fn) => {
      const id = nextId++;
      timers.set(id, fn);
      return id;
    },
    clear: (handle) => {
      if (typeof handle === "number" && timers.delete(handle)) {
        cleared += 1;
      }
    },
  };
  return {
    scheduler,
    fireAll: () => {
      const fns = Array.from(timers.values());
      timers.clear();
      for (const fn of fns) fn();
    },
    liveTimers: () => timers.size,
    clearedCount: () => cleared,
  };
}

function payload(remoteJid: string, text: string, extra?: Partial<GatewayChannelInboundPayload["message"]>): GatewayChannelInboundPayload {
  return {
    channel_key: "telegram_personal",
    provider: "telegram",
    message: {
      external_message_id: `${remoteJid}-${text}`,
      remote_jid: remoteJid,
      text,
      received_at: new Date().toISOString(),
      from_me: false,
      ...extra,
    },
  };
}

// ===========================================================================
// coalesceInboundPayloads (pure)
// ===========================================================================

test("coalesce: a single payload is returned by reference, untouched", () => {
  const p = payload("chat-A", "only one");
  assert.equal(coalesceInboundPayloads([p]), p);
});

test("coalesce: multiple texts are joined into one message", () => {
  const merged = coalesceInboundPayloads([
    payload("chat-A", "one"),
    payload("chat-A", "two"),
    payload("chat-A", "three"),
  ]);
  assert.equal(merged.message.text, "one\n\ntwo\n\nthree");
  // Identity fields come from the most recent message.
  assert.equal(merged.message.external_message_id, "chat-A-three");
});

test("coalesce: media across the burst is concatenated; empty texts are skipped", () => {
  const merged = coalesceInboundPayloads([
    payload("chat-A", "look", { media: [{ kind: "image", media_id: "a.jpg", mime_type: "image/jpeg", size_bytes: 1 }] }),
    payload("chat-A", "", { media: [{ kind: "image", media_id: "b.jpg", mime_type: "image/jpeg", size_bytes: 1 }] }),
  ]);
  assert.equal(merged.message.text, "look");
  assert.equal(merged.message.media?.length, 2);
});

test("coalesce: group signals (mention / reply-to-sage) are OR'd across the burst", () => {
  const merged = coalesceInboundPayloads([
    payload("grp", "hi", { is_group: true, is_mentioned: false }),
    payload("grp", "@sage look", { is_group: true, is_mentioned: true }),
  ]);
  assert.equal(merged.message.is_group, true);
  assert.equal(merged.message.is_mentioned, true);
});

test("defaultShouldBypass: media and slash-commands bypass; plain text does not", () => {
  assert.equal(defaultShouldBypass(payload("c", "hello")), false);
  assert.equal(defaultShouldBypass(payload("c", "/reset")), true);
  assert.equal(
    defaultShouldBypass(payload("c", "", { media: [{ kind: "image", media_id: "x", mime_type: "image/jpeg", size_bytes: 1 }] })),
    true,
  );
});

// ===========================================================================
// InboundDebouncer (manual clock)
// ===========================================================================

test("debounce: a rapid burst of text messages coalesces into ONE publish", () => {
  const clock = manualScheduler();
  const published: GatewayChannelInboundPayload[] = [];
  const debouncer = new InboundDebouncer({
    publish: (p) => { published.push(p); },
    scheduler: clock.scheduler,
    windowMs: 2000,
  });

  debouncer.admit(payload("chat-A", "one"));
  debouncer.admit(payload("chat-A", "two"));
  debouncer.admit(payload("chat-A", "three"));

  assert.equal(published.length, 0, "nothing publishes while the window is open");
  assert.equal(debouncer.pendingCount(), 1, "the whole burst buffers under one conversation");

  clock.fireAll(); // window elapses

  assert.equal(published.length, 1, "the burst produced exactly one published event");
  assert.equal(published[0].message.text, "one\n\ntwo\n\nthree");
  assert.equal(debouncer.pendingCount(), 0);
});

test("debounce: each new message resets the window (only one live timer, the old is cleared)", () => {
  const clock = manualScheduler();
  const published: GatewayChannelInboundPayload[] = [];
  const debouncer = new InboundDebouncer({
    publish: (p) => { published.push(p); },
    scheduler: clock.scheduler,
  });

  debouncer.admit(payload("chat-A", "one"));
  debouncer.admit(payload("chat-A", "two"));
  debouncer.admit(payload("chat-A", "three"));

  // Two resets means the first two timers were cleared, exactly one is live.
  assert.equal(clock.liveTimers(), 1, "only the latest window timer is armed");
  assert.equal(clock.clearedCount(), 2, "each new message cleared the prior window timer");

  clock.fireAll();
  assert.equal(published.length, 1);
});

test("debounce: a media message flushes immediately without waiting for the window", () => {
  const clock = manualScheduler();
  const published: GatewayChannelInboundPayload[] = [];
  const debouncer = new InboundDebouncer({
    publish: (p) => { published.push(p); },
    scheduler: clock.scheduler,
  });

  debouncer.admit(payload("chat-A", "", { media: [{ kind: "image", media_id: "a.jpg", mime_type: "image/jpeg", size_bytes: 1 }] }));

  assert.equal(published.length, 1, "media publishes immediately");
  assert.equal(debouncer.pendingCount(), 0);
  assert.equal(clock.liveTimers(), 0, "no window timer is left armed for a bypassed message");
});

test("debounce: a media message flushes any buffered text with it (one coalesced turn)", () => {
  const clock = manualScheduler();
  const published: GatewayChannelInboundPayload[] = [];
  const debouncer = new InboundDebouncer({
    publish: (p) => { published.push(p); },
    scheduler: clock.scheduler,
  });

  debouncer.admit(payload("chat-A", "here"));          // buffered
  debouncer.admit(payload("chat-A", "", {              // media → bypass, flushes both
    media: [{ kind: "image", media_id: "a.jpg", mime_type: "image/jpeg", size_bytes: 1 }],
  }));

  assert.equal(published.length, 1);
  assert.equal(published[0].message.text, "here");
  assert.equal(published[0].message.media?.length, 1);
});

test("debounce: the explicit bypass hint flushes even when the payload no longer looks like media", () => {
  const clock = manualScheduler();
  const published: GatewayChannelInboundPayload[] = [];
  const debouncer = new InboundDebouncer({
    publish: (p) => { published.push(p); },
    scheduler: clock.scheduler,
  });

  // Mirrors a media message whose download failed → caption-only text payload,
  // but the runtime still knows it arrived as media.
  debouncer.admit(payload("chat-A", "caption only"), { bypass: true });

  assert.equal(published.length, 1, "the bypass hint forces an immediate flush");
  assert.equal(debouncer.pendingCount(), 0);
});

test("debounce: distinct conversations do not coalesce with each other", () => {
  const clock = manualScheduler();
  const published: GatewayChannelInboundPayload[] = [];
  const debouncer = new InboundDebouncer({
    publish: (p) => { published.push(p); },
    scheduler: clock.scheduler,
  });

  debouncer.admit(payload("chat-A", "a1"));
  debouncer.admit(payload("chat-B", "b1"));
  assert.equal(debouncer.pendingCount(), 2);

  clock.fireAll();
  assert.equal(published.length, 2, "each conversation publishes its own turn");
});

test("debounce: dispose() drops buffered bursts without publishing", () => {
  const clock = manualScheduler();
  const published: GatewayChannelInboundPayload[] = [];
  const debouncer = new InboundDebouncer({
    publish: (p) => { published.push(p); },
    scheduler: clock.scheduler,
  });

  debouncer.admit(payload("chat-A", "one"));
  debouncer.dispose();
  clock.fireAll();

  assert.equal(published.length, 0);
  assert.equal(debouncer.pendingCount(), 0);
});

test("debounce: the default window sits in the 1500-2500ms band", () => {
  assert.ok(DEFAULT_INBOUND_DEBOUNCE_WINDOW_MS >= 1500 && DEFAULT_INBOUND_DEBOUNCE_WINDOW_MS <= 2500);
});

// ===========================================================================
// Runtime integration — Telegram
// ===========================================================================

test("telegram runtime: rapid inbound messages coalesce into a single published channel.inbound", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-tg-debounce-"));
  try {
    const runtime = new TelegramPersonalRuntime(new GatewayStateDb(rootDir));
    const published: any[] = [];
    runtime.setPublisher({
      publishEvent: async (_type, p) => { published.push(p); },
      publishStateUpdate: async () => undefined,
    });

    await (runtime as any).handleInboundMessage({ externalMessageId: "1", remoteJid: "chat-A", text: "one", fromMe: false });
    await (runtime as any).handleInboundMessage({ externalMessageId: "2", remoteJid: "chat-A", text: "two", fromMe: false });
    await (runtime as any).handleInboundMessage({ externalMessageId: "3", remoteJid: "chat-A", text: "three", fromMe: false });

    assert.equal(published.length, 0, "the burst is still buffered inside the window");

    (runtime as any).inboundDebouncer.flushAll();
    await tick();

    assert.equal(published.length, 1, "the burst published exactly one event → one agent turn");
    assert.equal(published[0].message.text, "one\n\ntwo\n\nthree");
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});

test("telegram runtime: a media inbound message publishes immediately (bypasses the window)", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-tg-debounce-media-"));
  try {
    const runtime = new TelegramPersonalRuntime(new GatewayStateDb(rootDir));
    const published: any[] = [];
    runtime.setPublisher({
      publishEvent: async (_type, p) => { published.push(p); },
      publishStateUpdate: async () => undefined,
    });

    await (runtime as any).handleInboundMessage({
      externalMessageId: "9",
      remoteJid: "chat-B",
      text: "",
      fromMe: false,
      media: [{ kind: "image", mediaId: "telegram-media/9.jpg", mimeType: "image/jpeg", sizeBytes: 10 }],
    });
    await tick();

    assert.equal(published.length, 1, "a photo does not wait out the debounce window");
    assert.equal(published[0].message.media?.length, 1);
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});

// ===========================================================================
// Runtime integration — WhatsApp
// ===========================================================================

function waTextEnvelope(id: string, text: string, remoteJid = "15551234567@s.whatsapp.net") {
  return {
    messages: [
      {
        key: { id, remoteJid, fromMe: false },
        message: { conversation: text },
        messageTimestamp: Math.floor(Date.now() / 1000),
      },
    ],
  };
}

test("whatsapp runtime: rapid inbound messages coalesce into a single published channel.inbound", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-wa-debounce-"));
  try {
    const runtime = new WhatsAppPersonalRuntime(new GatewayStateDb(rootDir));
    const published: any[] = [];
    runtime.setPublisher({
      publishEvent: async (_type, p) => { published.push(p); },
      publishStateUpdate: async () => undefined,
    });

    await (runtime as any).handleMessagesUpsert(waTextEnvelope("1", "one"));
    await (runtime as any).handleMessagesUpsert(waTextEnvelope("2", "two"));
    await (runtime as any).handleMessagesUpsert(waTextEnvelope("3", "three"));

    assert.equal(published.length, 0, "the burst is still buffered inside the window");

    (runtime as any).inboundDebouncer.flushAll();
    await tick();

    assert.equal(published.length, 1);
    assert.equal(published[0].message.text, "one\n\ntwo\n\nthree");
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});

test("whatsapp runtime: an inbound image publishes immediately (media bypass, download mocked)", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-wa-debounce-media-"));
  try {
    const adapter = {
      downloadMedia: async () => Buffer.from("fake-image-bytes"),
    };
    const runtime = new WhatsAppPersonalRuntime(new GatewayStateDb(rootDir), { adapter: adapter as any });
    const published: any[] = [];
    runtime.setPublisher({
      publishEvent: async (_type, p) => { published.push(p); },
      publishStateUpdate: async () => undefined,
    });

    await (runtime as any).handleMessagesUpsert({
      messages: [
        {
          key: { id: "img-1", remoteJid: "15551234567@s.whatsapp.net", fromMe: false },
          message: { imageMessage: { mimetype: "image/jpeg", caption: "look", fileLength: 12 } },
          messageTimestamp: Math.floor(Date.now() / 1000),
        },
      ],
    });
    await tick();

    assert.equal(published.length, 1, "a photo does not wait out the debounce window");
    assert.equal(published[0].message.media?.length, 1);
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});

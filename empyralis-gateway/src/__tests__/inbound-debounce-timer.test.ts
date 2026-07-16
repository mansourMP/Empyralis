import { mkdtemp, rm } from "fs/promises";
import { tmpdir } from "os";
import path from "path";
import test from "node:test";
import assert from "node:assert/strict";

import { DEFAULT_INBOUND_DEBOUNCE_WINDOW_MS } from "../channels/foundation/inbound-debounce";
import { TelegramPersonalRuntime } from "../channels/telegram/runtime";
import { TELEGRAM_PERSONAL_CHANNEL_KEY, TELEGRAM_PERSONAL_PROVIDER } from "../channels/telegram/session-store";
import { WhatsAppPersonalRuntime } from "../channels/whatsapp/runtime";
import { GatewayStateDb } from "../state/db";

// ===========================================================================
// These tests exercise the REAL runtime debounce path end-to-end — the gap the
// original suite missed: handleInboundMessage → inboundDebouncer.admit → the
// actual setTimeout firing → flush → publishInbound. The prior tests fired the
// timer callback by hand (manual scheduler / flushAll), so they never armed a
// real timer and never caught that the default scheduler unref()'d it — an
// unref'd timer is skipped by libuv when it is the only handle on the loop, so
// in production the coalesced message silently never published.
// ===========================================================================

const flushMicrotasks = async () => {
  await Promise.resolve();
  await Promise.resolve();
};

function tgInbound(id: string, text: string, remoteJid = "chatA") {
  return { externalMessageId: id, remoteJid, text, fromMe: false };
}

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

// ---------------------------------------------------------------------------
// REGRESSION GUARD — fails against the buggy (unref'd) code, passes after fix.
// ---------------------------------------------------------------------------

test("regression: the runtime's armed debounce flush timer is ref'd (an unref'd timer silently never fires in production)", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-debounce-ref-"));
  const runtime = new TelegramPersonalRuntime(new GatewayStateDb(rootDir));
  runtime.setPublisher({
    publishEvent: async () => undefined,
    publishStateUpdate: async () => undefined,
  });
  try {
    // Real default scheduler (no injected/mock scheduler) → a real Node timer.
    await (runtime as any).handleInboundMessage(tgInbound("1", "hi"));

    const entry = (runtime as any).inboundDebouncer.pending.get("chatA");
    assert.ok(entry, "the message should be buffered under its conversation");
    const timer = entry.timer;
    assert.ok(timer && typeof timer.hasRef === "function", "the window must be armed with a real Node timer");
    assert.equal(
      timer.hasRef(),
      true,
      "the debounce flush timer MUST NOT be unref'd — libuv skips an unref'd timer when it is the only handle on the loop, so the coalesced message would silently never publish (the production regression)",
    );
  } finally {
    (runtime as any).inboundDebouncer.dispose(); // clear the real 2s timer
    await rm(rootDir, { recursive: true, force: true });
  }
});

// ---------------------------------------------------------------------------
// INTEGRATION — advance the real timer with fake timers, prove publish fires.
// ---------------------------------------------------------------------------

test("telegram: rapid messages → advance the window → publishInbound is called EXACTLY once with the merged payload", async (t) => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-debounce-tg-"));
  try {
    t.mock.timers.enable({ apis: ["setTimeout"] });
    const runtime = new TelegramPersonalRuntime(new GatewayStateDb(rootDir));
    const published: any[] = [];
    runtime.setPublisher({
      publishEvent: async (_type, payload) => { published.push(payload); },
      publishStateUpdate: async () => undefined,
    });

    await (runtime as any).handleInboundMessage(tgInbound("1", "one"));
    await (runtime as any).handleInboundMessage(tgInbound("2", "two"));
    await (runtime as any).handleInboundMessage(tgInbound("3", "three"));
    assert.equal(published.length, 0, "nothing may publish while the window is open");

    // Advance to just before, then across, the window boundary.
    t.mock.timers.tick(DEFAULT_INBOUND_DEBOUNCE_WINDOW_MS - 1);
    await flushMicrotasks();
    assert.equal(published.length, 0, "still buffered 1ms before the window elapses");

    t.mock.timers.tick(1);
    await flushMicrotasks();

    assert.equal(published.length, 1, "the burst produced EXACTLY one publish → one agent turn");
    assert.equal(published[0].message.text, "one\n\ntwo\n\nthree");
    assert.equal((runtime as any).inboundDebouncer.pendingCount(), 0, "buffer is drained after flush");
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});

test("telegram: a single buffered message still publishes exactly once after the window", async (t) => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-debounce-tg-single-"));
  try {
    t.mock.timers.enable({ apis: ["setTimeout"] });
    const runtime = new TelegramPersonalRuntime(new GatewayStateDb(rootDir));
    const published: any[] = [];
    runtime.setPublisher({
      publishEvent: async (_type, payload) => { published.push(payload); },
      publishStateUpdate: async () => undefined,
    });

    await (runtime as any).handleInboundMessage(tgInbound("solo-1", "just one"));
    assert.equal(published.length, 0);

    t.mock.timers.tick(DEFAULT_INBOUND_DEBOUNCE_WINDOW_MS);
    await flushMicrotasks();

    assert.equal(published.length, 1, "a lone message must still publish once the window elapses");
    assert.equal(published[0].message.text, "just one");
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});

test("whatsapp: rapid messages → advance the window → publishInbound is called EXACTLY once", async (t) => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-debounce-wa-"));
  try {
    t.mock.timers.enable({ apis: ["setTimeout"] });
    const runtime = new WhatsAppPersonalRuntime(new GatewayStateDb(rootDir));
    const published: any[] = [];
    runtime.setPublisher({
      publishEvent: async (_type, payload) => { published.push(payload); },
      publishStateUpdate: async () => undefined,
    });

    await (runtime as any).handleMessagesUpsert(waTextEnvelope("1", "one"));
    await (runtime as any).handleMessagesUpsert(waTextEnvelope("2", "two"));
    assert.equal(published.length, 0, "nothing may publish while the window is open");

    t.mock.timers.tick(DEFAULT_INBOUND_DEBOUNCE_WINDOW_MS);
    await flushMicrotasks();

    assert.equal(published.length, 1, "the burst produced EXACTLY one publish");
    assert.equal(published[0].message.text, "one\n\ntwo");
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});

// ---------------------------------------------------------------------------
// FULL LOOP — typing starts on admit, survives the window, and is cleared when
// the (now real) coalesced reply is sent.
// ---------------------------------------------------------------------------

test("telegram full loop: typing starts on admit, one coalesced publish fires, and typing clears when the reply sends", async (t) => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-debounce-loop-"));
  try {
    t.mock.timers.enable({ apis: ["setTimeout"] });
    const runtime = new TelegramPersonalRuntime(new GatewayStateDb(rootDir));
    const chatActions: Array<{ jid: string; action: string }> = [];
    const sent: Array<{ jid: string; text: string }> = [];
    (runtime as any).client = {
      sendChatAction: async (jid: string, action: string) => { chatActions.push({ jid, action }); },
      sendMessage: async (jid: string, text: string) => { sent.push({ jid, text }); return { externalMessageId: "out-1", remoteJid: jid }; },
    };
    const published: any[] = [];
    runtime.setPublisher({
      publishEvent: async (_type, payload) => { published.push(payload); },
      publishStateUpdate: async () => undefined,
    });

    await (runtime as any).handleInboundMessage(tgInbound("1", "one"));
    await (runtime as any).handleInboundMessage(tgInbound("2", "two"));

    // Typing started immediately on the first admit, coalesced to one session.
    assert.ok(
      chatActions.some((c) => c.jid === "chatA" && c.action === "typing"),
      "typing must start the instant the first message is admitted",
    );
    assert.equal((runtime as any).activeTyping.size, 1, "one typing session tracked across the window");
    assert.equal(published.length, 0, "still buffered inside the window");

    // Window elapses → exactly one coalesced publish.
    t.mock.timers.tick(DEFAULT_INBOUND_DEBOUNCE_WINDOW_MS);
    await flushMicrotasks();
    assert.equal(published.length, 1);
    assert.equal(published[0].message.text, "one\n\ntwo");

    // The backend runs its turn and sends the reply back down.
    await (runtime as any).handleChannelOutbound({
      payload: {
        channel_key: TELEGRAM_PERSONAL_CHANNEL_KEY,
        provider: TELEGRAM_PERSONAL_PROVIDER,
        operation: "send_final",
        idempotency_key: "reply-1",
        remote_jid: "chatA",
        text: "here is the answer",
      },
    });

    assert.equal(sent.length, 1, "the reply was sent");
    assert.equal((runtime as any).activeTyping.size, 0, "typing is cleared once the reply is dispatched");
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});

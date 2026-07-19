import { mkdtemp, rm } from "fs/promises";
import { tmpdir } from "os";
import path from "path";
import test from "node:test";
import assert from "node:assert/strict";

import { TelegramPersonalRuntime } from "../channels/telegram/runtime";
import { GatewayStateDb } from "../state/db";
import { mapTelegramInboundMessage } from "../channels/telegram/message-mapper";
import { TELEGRAM_PERSONAL_CHANNEL_KEY, TELEGRAM_PERSONAL_PROVIDER } from "../channels/telegram/session-store";

// FIX (the "family group bug"): Telegram previously had NO group/mention
// gate at all — handleInboundMessage only checked from_me, so EVERY message
// in EVERY group the linked account belongs to triggered a full agent turn
// and reply. This mirrors WhatsApp's existing, working pattern: in a group,
// stay silent unless @mentioned or replying to a message Sage sent. A DM
// (or the eventual self-chat case) must always pass regardless.

function buildMockAdapter() {
  const sentMessages: Array<{ remoteJid: string; text: string; replyTo?: string }> = [];
  let sendCounter = 0;
  const client = {
    setMessageHandler: (_handler: unknown) => undefined,
    sendMessage: async (remoteJid: string, text: string, replyToExternalMessageId?: string) => {
      sendCounter += 1;
      sentMessages.push({ remoteJid, text, replyTo: replyToExternalMessageId });
      return { externalMessageId: `out-${sendCounter}`, remoteJid };
    },
    sendChatAction: async (_remoteJid: string, _action: string) => undefined,
    disconnect: async () => undefined,
    exportSessionString: () => undefined,
  };
  const adapter = {
    connect: async () => ({ client, account: { userId: "u1", username: "sage_owner" } }),
  };
  return { adapter, client, sentMessages };
}

/** Gets a TelegramPersonalRuntime past preflight (api_id/api_hash + a
 *  session string on file) and through a real connectClientInternal() pass
 *  — copied from telegram-typing-bridge.test.ts's identical helper. */
async function primeAndConnect(runtime: TelegramPersonalRuntime): Promise<void> {
  const anyRuntime = runtime as any;
  await anyRuntime.configStore.patchTelegramConfig({ apiId: 123456, apiHash: "test-hash" });
  await anyRuntime.sessionStore.saveSessionString("mock-session-string");
  await anyRuntime.connectClientInternal();
}

async function withRuntime(
  run: (ctx: {
    runtime: TelegramPersonalRuntime;
    inbound: Array<{ message?: Record<string, unknown> }>;
    sentMessages: Array<{ remoteJid: string; text: string; replyTo?: string }>;
  }) => Promise<void>,
): Promise<void> {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-telegram-group-gate-"));
  let runtime: TelegramPersonalRuntime | undefined;
  try {
    const { adapter, sentMessages } = buildMockAdapter();
    runtime = new TelegramPersonalRuntime(new GatewayStateDb(rootDir), { adapter: adapter as any });
    const inbound: Array<{ message?: Record<string, unknown> }> = [];
    runtime.setPublisher({
      publishEvent: async (_type, payload) => {
        inbound.push(payload as { message?: Record<string, unknown> });
      },
      publishStateUpdate: async () => undefined,
    });
    await primeAndConnect(runtime);
    await run({ runtime, inbound, sentMessages });
  } finally {
    // A message that passes the gate starts a typing keepalive (see
    // startTypingForChat) that only self-clears after its 60s max TTL —
    // fine in production (claimed + stopped by the eventual reply) but
    // left dangling here since these tests never send a reply back through
    // sendFinalOutbound for every admitted message. Stop them explicitly so
    // the test process can exit promptly instead of hanging on live timers.
    if (runtime) {
      for (const session of (runtime as any).activeTyping.values()) {
        await session.typing.stop();
      }
    }
    await rm(rootDir, { recursive: true, force: true });
  }
}

test("Telegram group gate: an unaddressed group message never reaches the debouncer or gets published", async () => {
  await withRuntime(async ({ runtime, inbound }) => {
    await (runtime as any).handleInboundMessage({
      externalMessageId: "in-group-1",
      remoteJid: "-100555",
      text: "what time is dinner",
      fromMe: false,
      isGroup: true,
      isMentioned: false,
    });
    // The gate short-circuits handleInboundMessage BEFORE the debouncer's
    // admit() is ever called — no timing/flush needed to prove this.
    assert.equal((runtime as any).inboundDebouncer.pendingCount(), 0, "an unaddressed group message must never be admitted to the debouncer");
    assert.equal(inbound.length, 0, "an unaddressed group message must never be published");
  });
});

test("Telegram group gate: an explicit @mention is admitted and published", async () => {
  await withRuntime(async ({ runtime, inbound }) => {
    await (runtime as any).handleInboundMessage({
      externalMessageId: "in-group-2",
      remoteJid: "-100555",
      text: "@sage_owner what time is dinner",
      fromMe: false,
      isGroup: true,
      isMentioned: true,
    });
    assert.equal((runtime as any).inboundDebouncer.pendingCount(), 1, "a mentioned group message must be admitted");
    (runtime as any).inboundDebouncer.flushAll();
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(inbound.length, 1);
    const message = inbound[0].message as Record<string, unknown>;
    assert.equal(message.is_group, true);
    assert.equal(message.is_mentioned, true);
  });
});

test("Telegram group gate: replying to a message Sage sent is admitted and published even without a mention", async () => {
  await withRuntime(async ({ runtime, inbound }) => {
    // Send once through the real outbound path so sentMessageIds captures
    // this send's id (the mock adapter always returns "out-1" for its
    // first send — see buildMockAdapter).
    await (runtime as any).handleChannelOutbound({
      payload: {
        channel_key: TELEGRAM_PERSONAL_CHANNEL_KEY,
        provider: TELEGRAM_PERSONAL_PROVIDER,
        operation: "send_final",
        idempotency_key: "idem-group-1",
        remote_jid: "-100555",
        text: "dinner's at 7",
      },
    });
    assert.ok(
      (runtime as any).sentMessageIds.get("-100555")?.has("out-1"),
      "test precondition: the send above must have been tracked under its own chat",
    );

    await (runtime as any).handleInboundMessage({
      externalMessageId: "in-group-3",
      remoteJid: "-100555",
      text: "sounds good",
      fromMe: false,
      isGroup: true,
      isMentioned: false,
      replyToExternalMessageId: "out-1",
    });
    assert.equal((runtime as any).inboundDebouncer.pendingCount(), 1, "a reply to Sage's own message must be admitted");
    (runtime as any).inboundDebouncer.flushAll();
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(inbound.length, 1);
    const message = inbound[0].message as Record<string, unknown>;
    assert.equal(message.is_group, true);
    assert.equal(message.is_mentioned, false);
    assert.equal(message.is_reply_to_sage, true);
  });
});

test("Telegram group gate: replying to a DIFFERENT message (not one Sage sent) in a group is still gated", async () => {
  await withRuntime(async ({ runtime, inbound }) => {
    await (runtime as any).handleInboundMessage({
      externalMessageId: "in-group-4",
      remoteJid: "-100555",
      text: "sounds good",
      fromMe: false,
      isGroup: true,
      isMentioned: false,
      // Replies to SOME message, but not one this runtime ever sent.
      replyToExternalMessageId: "someone-elses-message-id",
    });
    assert.equal((runtime as any).inboundDebouncer.pendingCount(), 0);
    assert.equal(inbound.length, 0);
  });
});

test("Telegram group gate: a direct message always passes, mentioned or not", async () => {
  await withRuntime(async ({ runtime, inbound }) => {
    await (runtime as any).handleInboundMessage({
      externalMessageId: "in-dm-1",
      remoteJid: "555444333",
      text: "hey, are you free tonight?",
      fromMe: false,
      isGroup: false,
      isMentioned: false,
    });
    assert.equal((runtime as any).inboundDebouncer.pendingCount(), 1, "a DM must always be admitted regardless of mention state");
    (runtime as any).inboundDebouncer.flushAll();
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(inbound.length, 1);
    const message = inbound[0].message as Record<string, unknown>;
    assert.equal(message.is_group, false);
  });
});

test("Telegram group gate: isGroup omitted entirely (backward-compat / non-group chat) still passes", async () => {
  await withRuntime(async ({ runtime, inbound }) => {
    await (runtime as any).handleInboundMessage({
      externalMessageId: "in-dm-2",
      remoteJid: "555444333",
      text: "hey there",
      fromMe: false,
      // isGroup/isMentioned intentionally absent.
    });
    assert.equal((runtime as any).inboundDebouncer.pendingCount(), 1, "a message with no group signal at all must default to ungated (DM-like), not silently dropped");
    (runtime as any).inboundDebouncer.flushAll();
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(inbound.length, 1);
  });
});

test("Telegram group gate: from_me is still dropped before the group gate even runs (self-sent echoes)", async () => {
  await withRuntime(async ({ runtime, inbound }) => {
    await (runtime as any).handleInboundMessage({
      externalMessageId: "in-out-echo-1",
      remoteJid: "-100555",
      text: "dinner's at 7",
      fromMe: true,
      isGroup: true,
      isMentioned: false,
    });
    assert.equal((runtime as any).inboundDebouncer.pendingCount(), 0);
    assert.equal(inbound.length, 0);
  });
});

// ---------------------------------------------------------------------------
// Pure mapper-level coverage: mapTelegramInboundMessage's is_group/
// is_mentioned/quoted_stanza_id threading, independent of the runtime class.
// ---------------------------------------------------------------------------

test("mapTelegramInboundMessage threads isGroup/isMentioned/replyToExternalMessageId onto the wire payload", () => {
  const mapped = mapTelegramInboundMessage({
    externalMessageId: "in-1",
    remoteJid: "-100555",
    text: "hello",
    isGroup: true,
    isMentioned: true,
    replyToExternalMessageId: "out-9",
  });
  assert.ok(mapped);
  assert.equal(mapped!.message.is_group, true);
  assert.equal(mapped!.message.is_mentioned, true);
  assert.equal(mapped!.message.quoted_stanza_id, "out-9");
  // is_reply_to_sage is a placeholder here — resolved later in
  // handleInboundMessage against the runtime's own sentMessageIds.
  assert.equal(mapped!.message.is_reply_to_sage, false);
});

test("mapTelegramInboundMessage does not surface is_mentioned/quoted_stanza_id for a non-group chat", () => {
  const mapped = mapTelegramInboundMessage({
    externalMessageId: "in-2",
    remoteJid: "555444333",
    text: "hello",
    isGroup: false,
    isMentioned: true, // should be ignored/irrelevant outside a group
    replyToExternalMessageId: "out-9",
  });
  assert.ok(mapped);
  assert.equal(mapped!.message.is_group, false);
  assert.equal(mapped!.message.is_mentioned, false);
  assert.equal(mapped!.message.quoted_stanza_id, undefined);
});

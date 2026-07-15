import { mkdtemp, rm } from "fs/promises";
import { tmpdir } from "os";
import path from "path";
import test from "node:test";
import assert from "node:assert/strict";

import { TelegramPersonalRuntime } from "../channels/telegram/runtime";
import { GatewayStateDb } from "../state/db";
import { TELEGRAM_PERSONAL_CHANNEL_KEY, TELEGRAM_PERSONAL_PROVIDER } from "../channels/telegram/session-store";

// FIX: the typing indicator used to only wrap the final sendMessage network
// call, so it only blipped on for a fraction of a second right before the
// reply landed — it never covered the LLM "thinking" time that happens
// upstream, before the Gateway is even asked to send anything back. Typing
// now starts the instant an inbound message is admitted (startTypingForChat,
// called from handleInboundMessage) and is claimed + stopped by
// sendFinalOutbound when the reply actually goes out, via a small per-chat
// map (activeTyping) so a chat with multiple inbound messages before a reply
// coalesces onto one loop instead of starting duplicates.

function buildMockAdapter() {
  const chatActionCalls: Array<{ remoteJid: string; action: string }> = [];
  const client = {
    setMessageHandler: (_handler: unknown) => undefined,
    sendMessage: async (remoteJid: string, _text: string) => ({ externalMessageId: "out-1", remoteJid }),
    sendChatAction: async (remoteJid: string, action: string) => {
      chatActionCalls.push({ remoteJid, action });
    },
    disconnect: async () => undefined,
    exportSessionString: () => undefined,
  };
  const adapter = {
    connect: async () => ({ client, account: { userId: "u1", username: "sage_owner" } }),
  };
  return { adapter, client, chatActionCalls };
}

/** Gets a TelegramPersonalRuntime past preflight (api_id/api_hash + a
 *  session string on file) and through a real connectClientInternal() pass
 *  so the private fields/methods under test are exercised through their
 *  real call paths, not hand-rolled stand-ins. */
async function primeAndConnect(runtime: TelegramPersonalRuntime): Promise<void> {
  const anyRuntime = runtime as any;
  await anyRuntime.configStore.patchTelegramConfig({ apiId: 123456, apiHash: "test-hash" });
  await anyRuntime.sessionStore.saveSessionString("mock-session-string");
  await anyRuntime.connectClientInternal();
}

test("typing starts on inbound receipt, coalesces per chat, and is reused (not duplicated) by the eventual send", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-telegram-typing-bridge-"));
  try {
    const { adapter, chatActionCalls } = buildMockAdapter();
    const runtime = new TelegramPersonalRuntime(new GatewayStateDb(rootDir), { adapter: adapter as any });
    await primeAndConnect(runtime);

    await (runtime as any).handleInboundMessage({
      externalMessageId: "in-1",
      remoteJid: "12345",
      text: "hi",
      fromMe: false,
    });
    // typing.start() resolves after its first (awaited) send; give any
    // pending microtasks a turn to be safe regardless of timing.
    await new Promise((resolve) => setTimeout(resolve, 0));

    assert.equal(chatActionCalls.length, 1, "typing should start immediately on inbound receipt, before any reply is sent");
    assert.deepEqual(chatActionCalls[0], { remoteJid: "12345", action: "typing" });
    assert.equal((runtime as any).activeTyping.size, 1, "an active typing session should be tracked for this chat");

    // A second inbound message for the SAME chat before any reply must
    // coalesce onto the same loop rather than starting a duplicate one.
    await (runtime as any).handleInboundMessage({
      externalMessageId: "in-2",
      remoteJid: "12345",
      text: "still there?",
      fromMe: false,
    });
    assert.equal((runtime as any).activeTyping.size, 1, "coalesced: still exactly one active typing session for this chat");
    assert.equal(chatActionCalls.length, 1, "coalescing must not fire a second 'typing' action");

    // The reply goes out — sendFinalOutbound should claim (and eventually
    // stop) the SAME session instead of starting a fresh, redundant one.
    await (runtime as any).handleChannelOutbound({
      payload: {
        channel_key: TELEGRAM_PERSONAL_CHANNEL_KEY,
        provider: TELEGRAM_PERSONAL_PROVIDER,
        operation: "send_final",
        idempotency_key: "idem-1",
        remote_jid: "12345",
        text: "hello back",
      },
    });

    assert.equal(
      (runtime as any).activeTyping.size,
      0,
      "the claimed session should be removed from the map once the reply is dispatched",
    );
    assert.equal(
      chatActionCalls.length,
      1,
      "claiming the pre-started session must not fire a second, redundant 'typing' action at send time",
    );
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});

test("sendFinalOutbound still shows typing for a proactive send with no prior inbound (fallback path unaffected)", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-telegram-typing-bridge-fallback-"));
  try {
    const { adapter, chatActionCalls } = buildMockAdapter();
    const runtime = new TelegramPersonalRuntime(new GatewayStateDb(rootDir), { adapter: adapter as any });
    await primeAndConnect(runtime);

    await (runtime as any).handleChannelOutbound({
      payload: {
        channel_key: TELEGRAM_PERSONAL_CHANNEL_KEY,
        provider: TELEGRAM_PERSONAL_PROVIDER,
        operation: "send_final",
        idempotency_key: "idem-proactive-1",
        remote_jid: "67890",
        text: "proactive nudge",
      },
    });

    assert.ok(
      chatActionCalls.some((c) => c.remoteJid === "67890" && c.action === "typing"),
      "a send with no prior inbound-triggered session must still start typing at send time",
    );
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});

test("a typing session tagged to a since-replaced client is not reused after a reconnect", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-telegram-typing-bridge-reconnect-"));
  try {
    const { adapter } = buildMockAdapter();
    const runtime = new TelegramPersonalRuntime(new GatewayStateDb(rootDir), { adapter: adapter as any });
    await primeAndConnect(runtime);
    const firstClient = (runtime as any).client;
    assert.ok(firstClient, "test precondition: the first connect must have set a client");

    (runtime as any).startTypingForChat("999");
    assert.equal((runtime as any).activeTyping.size, 1);

    // Simulate a reconnect swapping in a new client — mirrors what
    // reconnectForConfigUpdate() (or a fresh connectClientInternal() pass
    // after a dropped connection) does to this.client.
    const { adapter: adapter2 } = buildMockAdapter();
    (runtime as any).adapter = adapter2;
    await (runtime as any).connectClientInternal();
    assert.notEqual((runtime as any).client, firstClient, "test precondition: the client must actually have been replaced");

    const claimed = (runtime as any).claimTypingForChat("999");
    assert.equal(claimed, undefined, "a session tagged to the pre-reconnect client must not be handed to the new one");
    assert.equal((runtime as any).activeTyping.size, 0, "the stale entry must still be removed from the map when evicted");
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});

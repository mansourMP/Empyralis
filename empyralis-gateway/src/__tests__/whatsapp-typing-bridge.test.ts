import { mkdtemp, rm } from "fs/promises";
import { tmpdir } from "os";
import path from "path";
import test from "node:test";
import assert from "node:assert/strict";

import { WhatsAppPersonalRuntime } from "../channels/whatsapp/runtime";
import { GatewayStateDb } from "../state/db";
import { WHATSAPP_PERSONAL_CHANNEL_KEY, WHATSAPP_PERSONAL_PROVIDER } from "../channels/whatsapp/session-store";

// FIX: the typing indicator used to only wrap the final sendMessage network
// call, so it only blipped on for a fraction of a second right before the
// reply landed — it never covered the LLM "thinking" time that happens
// upstream, before the Gateway is even asked to send anything back. Typing
// now starts the instant an inbound message is admitted (startTypingForChat,
// called from handleMessagesUpsert once a message survives the group gate)
// and is claimed + stopped by sendFinalOutbound when the reply actually goes
// out, via a small per-chat map (activeTyping) so a chat with multiple
// inbound messages before a reply coalesces onto one loop instead of
// starting duplicates.

function buildMockAdapter() {
  const presenceCalls: Array<{ remoteJid: string; action: string }> = [];
  const socket = {
    ev: { on: () => undefined },
    sendMessage: async (jid: string) => ({ key: { id: "wamid-out-1", remoteJid: jid } }),
    sendPresenceUpdate: async (action: string, jid: string) => {
      presenceCalls.push({ remoteJid: jid, action });
    },
    user: { id: "me@s.whatsapp.net", name: "Sage Owner" },
    logout: async () => undefined,
    end: () => undefined,
  };
  const adapter = {
    loadAuthState: async () => ({
      state: { creds: { registered: true } },
      saveCreds: async () => undefined,
    }),
    createSocket: () => socket,
    disconnectReason: { loggedOut: 401, restartRequired: 515 },
    browserDescriptor: () => ["Empyralis", "Chrome", "1.0"],
    fetchWaWebVersion: async () => undefined,
  };
  return { adapter, socket, presenceCalls };
}

function inboundEnvelope(id: string, text: string, remoteJid = "15551234567@s.whatsapp.net") {
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

test("typing starts on inbound receipt, coalesces per chat, and is reused (not duplicated) by the eventual send", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-whatsapp-typing-bridge-"));
  try {
    const { adapter, presenceCalls } = buildMockAdapter();
    const runtime = new WhatsAppPersonalRuntime(new GatewayStateDb(rootDir), { adapter: adapter as any });
    await (runtime as any).connectSocketInternal();

    await (runtime as any).handleMessagesUpsert(inboundEnvelope("in-1", "hi"));
    await new Promise((resolve) => setTimeout(resolve, 0));

    assert.equal(presenceCalls.length, 1, "typing should start immediately on inbound receipt, before any reply is sent");
    assert.deepEqual(presenceCalls[0], { remoteJid: "15551234567@s.whatsapp.net", action: "composing" });
    assert.equal((runtime as any).activeTyping.size, 1, "an active typing session should be tracked for this chat");

    // A second inbound message for the SAME chat before any reply must
    // coalesce onto the same loop rather than starting a duplicate one.
    await (runtime as any).handleMessagesUpsert(inboundEnvelope("in-2", "still there?"));
    assert.equal((runtime as any).activeTyping.size, 1, "coalesced: still exactly one active typing session for this chat");
    assert.equal(presenceCalls.length, 1, "coalescing must not fire a second 'composing' presence update");

    // The reply goes out — sendFinalOutbound should claim (and eventually
    // stop) the SAME session instead of starting a fresh, redundant one.
    await (runtime as any).handleChannelOutbound({
      payload: {
        channel_key: WHATSAPP_PERSONAL_CHANNEL_KEY,
        provider: WHATSAPP_PERSONAL_PROVIDER,
        operation: "send_final",
        idempotency_key: "idem-1",
        remote_jid: "15551234567@s.whatsapp.net",
        text: "hello back",
      },
    });

    assert.equal(
      (runtime as any).activeTyping.size,
      0,
      "the claimed session should be removed from the map once the reply is dispatched",
    );
    assert.equal(
      presenceCalls.filter((c) => c.action === "composing").length,
      1,
      "claiming the pre-started session must not fire a second, redundant 'composing' update at send time",
    );
    assert.ok(
      presenceCalls.some((c) => c.action === "paused"),
      "stopping the claimed session at send time should still fire WhatsApp's 'paused' stop action",
    );
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});

test("sendFinalOutbound still shows typing for a proactive send with no prior inbound (fallback path unaffected)", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-whatsapp-typing-bridge-fallback-"));
  try {
    const { adapter, presenceCalls } = buildMockAdapter();
    const runtime = new WhatsAppPersonalRuntime(new GatewayStateDb(rootDir), { adapter: adapter as any });
    await (runtime as any).connectSocketInternal();

    await (runtime as any).handleChannelOutbound({
      payload: {
        channel_key: WHATSAPP_PERSONAL_CHANNEL_KEY,
        provider: WHATSAPP_PERSONAL_PROVIDER,
        operation: "send_final",
        idempotency_key: "idem-proactive-1",
        remote_jid: "15557654321@s.whatsapp.net",
        text: "proactive nudge",
      },
    });

    assert.ok(
      presenceCalls.some((c) => c.remoteJid === "15557654321@s.whatsapp.net" && c.action === "composing"),
      "a send with no prior inbound-triggered session must still start typing at send time",
    );
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});

test("a typing session tagged to a since-replaced socket is not reused after a reconnect", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-whatsapp-typing-bridge-reconnect-"));
  try {
    const { adapter, socket: firstSocket } = buildMockAdapter();
    const runtime = new WhatsAppPersonalRuntime(new GatewayStateDb(rootDir), { adapter: adapter as any });
    await (runtime as any).connectSocketInternal();
    assert.equal((runtime as any).socket, firstSocket);

    (runtime as any).startTypingForChat("15550000000@s.whatsapp.net");
    assert.equal((runtime as any).activeTyping.size, 1);

    // Simulate a reconnect swapping in a new socket — mirrors what
    // reconnectForConfigUpdate() / handleConnectionUpdate's close branch
    // followed by a fresh connect does to this.socket.
    const { adapter: adapter2, socket: secondSocket } = buildMockAdapter();
    (runtime as any).adapter = adapter2;
    await (runtime as any).connectSocketInternal();
    assert.equal((runtime as any).socket, secondSocket);
    assert.notEqual(secondSocket, firstSocket, "test precondition: the socket must actually have been replaced");

    const claimed = (runtime as any).claimTypingForChat("15550000000@s.whatsapp.net");
    assert.equal(claimed, undefined, "a session tagged to the pre-reconnect socket must not be handed to the new one");
    assert.equal((runtime as any).activeTyping.size, 0, "the stale entry must still be removed from the map when evicted");
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});

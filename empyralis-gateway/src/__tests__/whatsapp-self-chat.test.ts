import { mkdtemp, rm } from "fs/promises";
import { tmpdir } from "os";
import path from "path";
import test from "node:test";
import assert from "node:assert/strict";

import {
  WhatsAppPersonalRuntime,
  SELF_CHAT_BREAKER_MAX_TURNS,
} from "../channels/whatsapp/runtime";
import { GatewayStateDb } from "../state/db";
import { WHATSAPP_PERSONAL_CHANNEL_KEY, WHATSAPP_PERSONAL_PROVIDER } from "../channels/whatsapp/session-store";
import { WHATSAPP_MESSAGE_LIMIT } from "../channels/foundation/message-chunker";

// FIX (self-chat echo loop): WhatsApp's is_self_chat is deliberately let
// through the from_me gate in handleMessagesUpsert (a message the owner
// sends into their own "Saved Messages" is a command channel, not noise —
// see message-mapper.ts's is_self_chat computation). But Baileys fires
// messages.upsert for the socket's OWN sends too, unconditionally, via
// emitOwnEvents (default true — see
// node_modules/@whiskeysockets/baileys/lib/Defaults/index.js:55 and
// lib/Socket/messages-send.js's
// `if (config.emitOwnEvents) { process.nextTick(() => upsertMessage(fullMsg, 'append')) }`).
// Before this fix, the ONLY gate was `from_me && !is_self_chat` — a self-chat
// echo of the runtime's own reply sailed straight through, got admitted,
// published, and re-triggered a new agent turn, whose reply echoed again,
// forever. This mirrors the exact bug class fixed for Telegram in
// telegram/runtime.ts (see telegram-self-chat.test.ts) — same guard shape,
// ported to Baileys' own message-id semantics (key.id vs GramJS message ids).
//
// The tests below cover both halves: self-chat is (and remains) reachable
// as an owner command channel, AND the agent's own reply does not re-trigger
// it.

function buildMockAdapter() {
  const sentMessages: Array<{ remoteJid: string; content: Record<string, unknown>; options?: Record<string, unknown> }> = [];
  let sendCounter = 0;
  const socket = {
    ev: { on: () => undefined },
    sendMessage: async (jid: string, content: Record<string, unknown>, options?: Record<string, unknown>) => {
      sendCounter += 1;
      sentMessages.push({ remoteJid: jid, content, options });
      return { key: { id: `wamid-out-${sendCounter}`, remoteJid: jid } };
    },
    sendPresenceUpdate: async () => undefined,
    // user.id is the identity handleMessagesUpsert/sendFinalOutbound's
    // isSelfChatJid() compares a message's remote_jid against — matches
    // SELF_CHAT_JID below, exactly like the real Baileys socket exposes the
    // linked account's own JID.
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
  return { adapter, socket, sentMessages };
}

function inboundEnvelope(id: string, text: string, remoteJid: string, fromMe: boolean) {
  return {
    messages: [
      {
        key: { id, remoteJid, fromMe },
        message: { conversation: text },
        messageTimestamp: Math.floor(Date.now() / 1000),
      },
    ],
  };
}

async function withRuntime(
  run: (ctx: {
    runtime: WhatsAppPersonalRuntime;
    inbound: Array<{ message?: Record<string, unknown> }>;
    sentMessages: Array<{ remoteJid: string; content: Record<string, unknown>; options?: Record<string, unknown> }>;
  }) => Promise<void>,
): Promise<void> {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-whatsapp-self-chat-"));
  let runtime: WhatsAppPersonalRuntime | undefined;
  try {
    const { adapter, sentMessages } = buildMockAdapter();
    runtime = new WhatsAppPersonalRuntime(new GatewayStateDb(rootDir), { adapter: adapter as any });
    const inbound: Array<{ message?: Record<string, unknown> }> = [];
    runtime.setPublisher({
      publishEvent: async (_type, payload) => {
        inbound.push(payload as { message?: Record<string, unknown> });
      },
      publishStateUpdate: async () => undefined,
    });
    await (runtime as any).connectSocketInternal();
    await run({ runtime, inbound, sentMessages });
  } finally {
    // Mirrors whatsapp-typing-bridge.test.ts's identical cleanup: a message
    // that passes the gate starts a typing keepalive that only self-clears
    // after its max TTL — stop any left running so the test process exits
    // promptly.
    if (runtime) {
      for (const session of (runtime as any).activeTyping.values()) {
        await session.typing.stop();
      }
    }
    await rm(rootDir, { recursive: true, force: true });
  }
}

const SELF_CHAT_JID = "me@s.whatsapp.net";
const OTHER_CONTACT_JID = "15551234567@s.whatsapp.net";

function outboundPayload(patch: Record<string, unknown>) {
  return {
    payload: {
      channel_key: WHATSAPP_PERSONAL_CHANNEL_KEY,
      provider: WHATSAPP_PERSONAL_PROVIDER,
      operation: "send_final",
      remote_jid: OTHER_CONTACT_JID,
      text: "",
      ...patch,
    },
  };
}

// ---------------------------------------------------------------------------
// Reachability: self-chat passes the from_me gate; an ordinary outgoing
// message to someone else still does not. (Pre-existing behavior — asserted
// here as a baseline the loop guard below must not break.)
// ---------------------------------------------------------------------------

test("self-chat reachability: a self-chat message (fromMe + remoteJid===ownedJid) passes the gate and is admitted", async () => {
  await withRuntime(async ({ runtime, inbound }) => {
    await (runtime as any).handleMessagesUpsert(
      inboundEnvelope("in-self-1", "what's on my calendar today", SELF_CHAT_JID, true),
    );
    assert.equal(
      (runtime as any).inboundDebouncer.pendingCount(),
      1,
      "a self-chat command must be admitted despite fromMe=true — this is the reachability behavior",
    );
    (runtime as any).inboundDebouncer.flushAll();
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(inbound.length, 1);
    const message = inbound[0].message as Record<string, unknown>;
    assert.equal(message.from_me, true);
    assert.equal(message.is_self_chat, true);
  });
});

test("self-chat reachability: an ordinary outgoing DM to someone else (fromMe, NOT self-chat) is still dropped", async () => {
  await withRuntime(async ({ runtime, inbound }) => {
    await (runtime as any).handleMessagesUpsert(
      inboundEnvelope("in-out-dm-1", "see you at 7", OTHER_CONTACT_JID, true),
    );
    assert.equal((runtime as any).inboundDebouncer.pendingCount(), 0);
    assert.equal(inbound.length, 0);
  });
});

// ---------------------------------------------------------------------------
// THE LOOP GUARD — the locked requirement: the agent's own reply into
// self-chat must never re-trigger a new turn.
// ---------------------------------------------------------------------------

test("LOOP GUARD: the agent's own reply into self-chat does NOT re-trigger a new turn (id-based, primary check)", async () => {
  await withRuntime(async ({ runtime, inbound }) => {
    // 1. The owner sends a real command into self-chat.
    await (runtime as any).handleMessagesUpsert(
      inboundEnvelope("in-self-cmd-1", "remind me to call mom", SELF_CHAT_JID, true),
    );
    assert.equal((runtime as any).inboundDebouncer.pendingCount(), 1, "the real owner command must be admitted");
    (runtime as any).inboundDebouncer.flushAll();
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(inbound.length, 1, "the real owner command must be published exactly once");

    // 2. The agent replies. sendFinalOutbound dispatches into the SAME
    // self-chat peer — exactly the send that (pre-guard) would echo back
    // via Baileys' messages.upsert and create an infinite loop.
    const dispatchResult = (await (runtime as any).handleChannelOutbound(
      outboundPayload({
        idempotency_key: "idem-self-1",
        remote_jid: SELF_CHAT_JID,
        text: "Reminder set for later today.",
      }),
    )) as Record<string, unknown>;
    const sentId = String(dispatchResult.external_message_id || "");
    assert.ok(sentId, "test precondition: the reply must have produced an external id");
    assert.ok(
      (runtime as any).sentMessageIds.has(sentId),
      "test precondition: the send must be tracked in sentMessageIds",
    );
    assert.equal(
      (runtime as any).activeTyping.size,
      0,
      "test precondition: the reply's typing session was claimed and stopped",
    );

    // 3. Baileys delivers messages.upsert for that SAME message back through
    // the socket's own event emitter (emitOwnEvents — see this file's header
    // comment). Same external id (key.id), same self-chat peer, fromMe:true
    // — this is the exact echo the loop guard exists to catch.
    await (runtime as any).handleMessagesUpsert(
      inboundEnvelope(sentId, "Reminder set for later today.", SELF_CHAT_JID, true),
    );

    // The echo must not be admitted, published, or even start a typing
    // session — full proof it was recognized and dropped, not merely
    // filtered downstream.
    assert.equal(
      (runtime as any).inboundDebouncer.pendingCount(),
      0,
      "the agent's own reply echo must NOT be admitted as a new turn",
    );
    assert.equal(inbound.length, 1, "publish count must stay at 1 — the echo must not trigger a second publish");
    assert.equal(
      (runtime as any).activeTyping.size,
      0,
      "the echo must not start a fresh typing session either",
    );
  });
});

test("LOOP GUARD: a same-text echo arriving before the send's id is known (race window) is still suppressed", async () => {
  await withRuntime(async ({ runtime, inbound }) => {
    // Simulates sendFinalOutbound having just STARTED a self-chat send (via
    // beginSelfChatEchoGuard) whose sendMessage() RPC has not yet resolved —
    // i.e. its id is not in sentMessageIds yet. This is the exact race
    // isOwnSelfChatEcho's second layer exists for: Baileys' own-send echo is
    // scheduled via process.nextTick right after relayMessage() resolves
    // (see messages-send.js), which can in principle reach
    // handleMessagesUpsert before the awaited sendMessage() call in
    // sendWhatsAppTextChunks returns its key.id to the caller.
    (runtime as any).beginSelfChatEchoGuard(SELF_CHAT_JID, "On it - done.");

    await (runtime as any).handleMessagesUpsert(
      inboundEnvelope("in-race-echo-1", "On it - done.", SELF_CHAT_JID, true), // a FRESH id, not yet in sentMessageIds
    );

    assert.equal(
      (runtime as any).inboundDebouncer.pendingCount(),
      0,
      "a same-text/same-chat echo mid-flight must be suppressed even before its id is known",
    );
    assert.equal(inbound.length, 0);
  });
});

test("LOOP GUARD: circuit breaker refuses further self-chat turns once the rolling cap is hit", async () => {
  await withRuntime(async ({ runtime }) => {
    // All these messages share one remoteJid (the real self-chat shape —
    // there is only one Saved Messages conversation), so the inbound
    // debouncer would coalesce a same-chat burst into a single published
    // event regardless of the breaker — publish count can't distinguish "6
    // admits coalesced into 1 publish" from "1 admit". Instrumenting admit()
    // itself observes exactly what the breaker controls: whether
    // handleMessagesUpsert lets a message reach the debouncer at all.
    const debouncer = (runtime as any).inboundDebouncer;
    let admitCount = 0;
    const originalAdmit = debouncer.admit.bind(debouncer);
    debouncer.admit = (...args: unknown[]) => {
      admitCount += 1;
      return originalAdmit(...args);
    };

    // SELF_CHAT_BREAKER_MAX_TURNS distinct, non-echo self-chat messages
    // (unique id, unique text) — none of these match sentMessageIds or any
    // pending send, so layer 1 (isOwnSelfChatEcho) admits every one of
    // them; this exercises layer 2 (the breaker) in isolation.
    for (let i = 0; i < SELF_CHAT_BREAKER_MAX_TURNS; i += 1) {
      await (runtime as any).handleMessagesUpsert(
        inboundEnvelope(`in-breaker-${i}`, `distinct command number ${i}`, SELF_CHAT_JID, true),
      );
    }
    assert.equal(
      admitCount,
      SELF_CHAT_BREAKER_MAX_TURNS,
      "test precondition: exactly the cap's worth of distinct turns reached the debouncer before the breaker trips",
    );

    // One more — still a distinct id/text, not an echo of anything sent —
    // but the rolling breaker has now seen SELF_CHAT_BREAKER_MAX_TURNS
    // admitted turns inside its window, so this one must be refused
    // regardless of isOwnSelfChatEcho's own (correct) verdict.
    await (runtime as any).handleMessagesUpsert(
      inboundEnvelope("in-breaker-overflow", "one too many", SELF_CHAT_JID, true),
    );
    assert.equal(
      admitCount,
      SELF_CHAT_BREAKER_MAX_TURNS,
      "the breaker must refuse the turn beyond the rolling cap — admit() must not be called again",
    );
  });
});

test("LOOP GUARD: a genuinely new self-chat command right after a reply cycle is still admitted normally (not over-suppressed)", async () => {
  await withRuntime(async ({ runtime, inbound }) => {
    await (runtime as any).handleMessagesUpsert(
      inboundEnvelope("in-turn-1", "what's on my calendar", SELF_CHAT_JID, true),
    );
    (runtime as any).inboundDebouncer.flushAll();
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(inbound.length, 1);

    await (runtime as any).handleChannelOutbound(
      outboundPayload({
        idempotency_key: "idem-turn-1",
        remote_jid: SELF_CHAT_JID,
        text: "Nothing scheduled today.",
      }),
    );

    // A brand-new, DISTINCT owner command — different id, different text —
    // must be admitted like any real message, not blocked by the guard.
    await (runtime as any).handleMessagesUpsert(
      inboundEnvelope("in-turn-2", "ok, add a reminder for 5pm then", SELF_CHAT_JID, true),
    );
    assert.equal(
      (runtime as any).inboundDebouncer.pendingCount(),
      1,
      "a genuinely new self-chat command must still be admitted after a reply cycle",
    );
    (runtime as any).inboundDebouncer.flushAll();
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(inbound.length, 2, "both the original command and the follow-up must have been published");
  });
});

test("LOOP GUARD: outgoing messages to a DIFFERENT chat are never guarded against or mistaken for self-chat echoes", async () => {
  await withRuntime(async ({ runtime, inbound }) => {
    // The runtime sends a reply to some OTHER contact (not self-chat).
    await (runtime as any).handleChannelOutbound(
      outboundPayload({
        idempotency_key: "idem-other-1",
        remote_jid: OTHER_CONTACT_JID,
        text: "see you soon",
      }),
    );
    // No self-chat bookkeeping should have been armed for a non-self-chat
    // send.
    assert.equal(
      (runtime as any).pendingSelfChatSends.length,
      0,
      "a send to someone else must never arm the self-chat pending-echo guard",
    );

    // A genuine, unrelated self-chat command right afterward must still
    // work normally — proves the two are never confused.
    await (runtime as any).handleMessagesUpsert(
      inboundEnvelope("in-unrelated-self-1", "what's next on my list", SELF_CHAT_JID, true),
    );
    assert.equal((runtime as any).inboundDebouncer.pendingCount(), 1);
    (runtime as any).inboundDebouncer.flushAll();
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(inbound.length, 1);
  });
});

// ---------------------------------------------------------------------------
// sentMessageIds now records EVERY dispatched id (every chunk, every media
// item), not just the "primary" one — required for the id-based loop-guard
// check to be reliable for a multi-chunk reply, and also strengthens
// is_reply_to_sage in groups for the same reason.
// ---------------------------------------------------------------------------

test("sendFinalOutbound records EVERY chunk's external id in sentMessageIds, not just the first", async () => {
  await withRuntime(async ({ runtime }) => {
    // > WHATSAPP_MESSAGE_LIMIT (4000) with no spaces forces a hard-cut split
    // into more than one chunk (see message-chunker.ts's splitLongLine).
    const longText = "a".repeat(WHATSAPP_MESSAGE_LIMIT + 1000);
    const sizeBefore = (runtime as any).sentMessageIds.size;
    await (runtime as any).handleChannelOutbound(
      outboundPayload({
        idempotency_key: "idem-chunked-1",
        remote_jid: OTHER_CONTACT_JID,
        text: longText,
      }),
    );
    const added = (runtime as any).sentMessageIds.size - sizeBefore;
    assert.ok(added >= 2, `expected at least 2 chunk ids to be tracked, got ${added}`);
  });
});

test("LOOP GUARD: a multi-chunk self-chat reply arms the race-window guard for every chunk, not just the first", async () => {
  await withRuntime(async ({ runtime, inbound }) => {
    await (runtime as any).handleMessagesUpsert(
      inboundEnvelope("in-self-long-1", "give me the full status", SELF_CHAT_JID, true),
    );
    (runtime as any).inboundDebouncer.flushAll();
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(inbound.length, 1);

    const longText = "b".repeat(WHATSAPP_MESSAGE_LIMIT + 500);
    await (runtime as any).handleChannelOutbound(
      outboundPayload({
        idempotency_key: "idem-self-long-1",
        remote_jid: SELF_CHAT_JID,
        text: longText,
      }),
    );
    assert.ok(
      (runtime as any).sentMessageIds.size >= 2,
      "both chunks of the self-chat reply must be tracked in sentMessageIds",
    );

    // Echo of the SECOND chunk arrives (Baileys fires one messages.upsert
    // per sent message) — must be suppressed exactly like the first would
    // be, proving the guard isn't only wired for a single-message reply.
    const secondChunkId = Array.from((runtime as any).sentMessageIds.values()).pop() as string;
    await (runtime as any).handleMessagesUpsert(
      inboundEnvelope(secondChunkId, longText.slice(WHATSAPP_MESSAGE_LIMIT), SELF_CHAT_JID, true),
    );
    assert.equal(
      (runtime as any).inboundDebouncer.pendingCount(),
      0,
      "the second chunk's echo must be suppressed too — every chunk id is tracked, not just the first",
    );
    assert.equal(inbound.length, 1, "publish count must stay at 1");
  });
});

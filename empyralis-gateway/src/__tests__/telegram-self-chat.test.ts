import { mkdtemp, rm } from "fs/promises";
import { tmpdir } from "os";
import path from "path";
import test from "node:test";
import assert from "node:assert/strict";

import {
  TelegramPersonalRuntime,
  SELF_CHAT_BREAKER_MAX_TURNS,
} from "../channels/telegram/runtime";
import { GatewayStateDb } from "../state/db";
import { mapTelegramInboundMessage } from "../channels/telegram/message-mapper";
import { TELEGRAM_PERSONAL_CHANNEL_KEY, TELEGRAM_PERSONAL_PROVIDER } from "../channels/telegram/session-store";

// FIX (self-chat unreachable): Telegram's GramJS event subscription used
// `new NewMessage({ incoming: true })`, which drops any event where
// message.out is true BEFORE the callback ever runs (see
// node_modules/telegram/events/NewMessage.js's filter()). A message the
// linked account sends into its own "Saved Messages" is always out: true —
// there is no other way for Telegram to mark "who wrote this" — so
// self-chat (the exact analog of WhatsApp's is_self_chat owner command
// channel) never generated an event at all. The fix drops that filter
// (receiving both directions, like WhatsApp's Baileys runtime already
// does) and does the real filtering in handleInboundMessage:
// from_me-unless-self-chat, mirroring WhatsApp's
// `from_me && !is_self_chat` gate exactly.
//
// Enabling outgoing delivery, though, means the runtime's OWN replies into
// Saved Messages now ALSO generate inbound events — without a guard, every
// reply would immediately re-admit itself as a new "command", an infinite
// loop. The tests below cover both halves: self-chat is reachable, AND the
// agent's own reply does not re-trigger it.

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
  // account.username is the identity handleChannelOutbound's
  // isSelfChatJid() compares a reply's remote_jid against — matches
  // SELF_CHAT_JID below, exactly like the real GramJS handler would
  // resolve chat.username for the owner's own Saved Messages entity.
  const adapter = {
    connect: async () => ({ client, account: { userId: "u1", username: "sage_owner" } }),
  };
  return { adapter, client, sentMessages };
}

/** Gets a TelegramPersonalRuntime past preflight (api_id/api_hash + a
 *  session string on file) and through a real connectClientInternal() pass
 *  — copied from telegram-group-gate.test.ts's identical helper. */
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
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-telegram-self-chat-"));
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
    // Mirrors telegram-group-gate.test.ts's identical cleanup: a message
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

const SELF_CHAT_JID = "sage_owner";

// ---------------------------------------------------------------------------
// Pure mapper-level coverage: is_self_chat threading.
// ---------------------------------------------------------------------------

test("mapTelegramInboundMessage threads isSelfChat onto the wire payload as is_self_chat", () => {
  const mapped = mapTelegramInboundMessage({
    externalMessageId: "in-1",
    remoteJid: SELF_CHAT_JID,
    text: "remind me to call mom",
    fromMe: true,
    isSelfChat: true,
  });
  assert.ok(mapped);
  assert.equal(mapped!.message.from_me, true);
  assert.equal(mapped!.message.is_self_chat, true);
});

test("mapTelegramInboundMessage defaults is_self_chat to false when omitted (regression: ordinary DM/group messages)", () => {
  const mapped = mapTelegramInboundMessage({
    externalMessageId: "in-2",
    remoteJid: "555444333",
    text: "hey there",
    fromMe: false,
  });
  assert.ok(mapped);
  assert.equal(mapped!.message.is_self_chat, false);
});

// ---------------------------------------------------------------------------
// Reachability: self-chat now passes the from_me gate; an ordinary outgoing
// message to someone else still does not.
// ---------------------------------------------------------------------------

test("self-chat reachability: a self-chat message (from_me + isSelfChat) passes the gate and is admitted", async () => {
  await withRuntime(async ({ runtime, inbound }) => {
    await (runtime as any).handleInboundMessage({
      externalMessageId: "in-self-1",
      remoteJid: SELF_CHAT_JID,
      text: "what's on my calendar today",
      fromMe: true,
      isSelfChat: true,
    });
    assert.equal(
      (runtime as any).inboundDebouncer.pendingCount(),
      1,
      "a self-chat command must be admitted despite from_me=true — this is the reachability fix",
    );
    (runtime as any).inboundDebouncer.flushAll();
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(inbound.length, 1);
    const message = inbound[0].message as Record<string, unknown>;
    assert.equal(message.from_me, true);
    assert.equal(message.is_self_chat, true);
  });
});

test("self-chat reachability: an ordinary outgoing DM to someone else (from_me, NOT self-chat) is still dropped", async () => {
  await withRuntime(async ({ runtime, inbound }) => {
    await (runtime as any).handleInboundMessage({
      externalMessageId: "in-out-dm-1",
      remoteJid: "444555666", // some OTHER contact — not the self-chat peer
      text: "see you at 7",
      fromMe: true,
      isSelfChat: false,
    });
    assert.equal((runtime as any).inboundDebouncer.pendingCount(), 0);
    assert.equal(inbound.length, 0);
  });
});

test("self-chat reachability: isSelfChat omitted entirely (backward-compat) behaves like false — outgoing-to-other still dropped", async () => {
  await withRuntime(async ({ runtime, inbound }) => {
    await (runtime as any).handleInboundMessage({
      externalMessageId: "in-out-dm-2",
      remoteJid: "444555666",
      text: "on my way",
      fromMe: true,
      // isSelfChat intentionally absent
    });
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
    await (runtime as any).handleInboundMessage({
      externalMessageId: "in-self-cmd-1",
      remoteJid: SELF_CHAT_JID,
      text: "remind me to call mom",
      fromMe: true,
      isSelfChat: true,
    });
    assert.equal((runtime as any).inboundDebouncer.pendingCount(), 1, "the real owner command must be admitted");
    (runtime as any).inboundDebouncer.flushAll();
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(inbound.length, 1, "the real owner command must be published exactly once");

    // 2. The agent replies. sendFinalOutbound dispatches into the SAME
    // self-chat peer — exactly the send that (pre-guard) would echo back
    // and create an infinite loop.
    const dispatchResult = (await (runtime as any).handleChannelOutbound({
      payload: {
        channel_key: TELEGRAM_PERSONAL_CHANNEL_KEY,
        provider: TELEGRAM_PERSONAL_PROVIDER,
        operation: "send_final",
        idempotency_key: "idem-self-1",
        remote_jid: SELF_CHAT_JID,
        text: "Reminder set for later today.",
      },
    })) as Record<string, unknown>;
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

    // 3. GramJS delivers the update for that SAME message back through the
    // NewMessage subscription (see runtime.ts's getAdapter — dropping
    // `incoming: true` means this now reaches handleInboundMessage just
    // like a real owner message would). Same external id, same text, same
    // self-chat peer, out:true (fromMe:true) — this is the exact echo the
    // loop guard exists to catch.
    await (runtime as any).handleInboundMessage({
      externalMessageId: sentId,
      remoteJid: SELF_CHAT_JID,
      text: "Reminder set for later today.",
      fromMe: true,
      isSelfChat: true,
    });

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
    // beginSelfChatEchoGuard) whose RPC has not yet resolved — i.e. its id
    // is not in sentMessageIds yet. This is the exact race
    // isOwnSelfChatEcho's second layer exists for: GramJS's own send-RPC
    // result and the independent update-stream push are two different
    // delivery paths (see node_modules/telegram/network/mtprotoSender.js's
    // _handleRPCResult vs _handleUpdate) with no guaranteed ordering
    // between them.
    (runtime as any).beginSelfChatEchoGuard(SELF_CHAT_JID, "On it — done.");

    await (runtime as any).handleInboundMessage({
      externalMessageId: "in-race-echo-1", // a FRESH id, not yet in sentMessageIds
      remoteJid: SELF_CHAT_JID,
      text: "On it — done.",
      fromMe: true,
      isSelfChat: true,
    });

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
    // debouncer (see inbound-debounce.ts) would coalesce a same-chat burst
    // into a single published event regardless of the breaker — publish
    // count can't distinguish "6 admits coalesced into 1 publish" from "1
    // admit". Instrumenting admit() itself observes exactly what the
    // breaker controls: whether handleInboundMessage lets a message reach
    // the debouncer at all.
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
      await (runtime as any).handleInboundMessage({
        externalMessageId: `in-breaker-${i}`,
        remoteJid: SELF_CHAT_JID,
        text: `distinct command number ${i}`,
        fromMe: true,
        isSelfChat: true,
      });
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
    await (runtime as any).handleInboundMessage({
      externalMessageId: "in-breaker-overflow",
      remoteJid: SELF_CHAT_JID,
      text: "one too many",
      fromMe: true,
      isSelfChat: true,
    });
    assert.equal(
      admitCount,
      SELF_CHAT_BREAKER_MAX_TURNS,
      "the breaker must refuse the turn beyond the rolling cap — admit() must not be called again",
    );
  });
});

test("LOOP GUARD: a genuinely new self-chat command right after a reply cycle is still admitted normally (not over-suppressed)", async () => {
  await withRuntime(async ({ runtime, inbound }) => {
    await (runtime as any).handleInboundMessage({
      externalMessageId: "in-turn-1",
      remoteJid: SELF_CHAT_JID,
      text: "what's on my calendar",
      fromMe: true,
      isSelfChat: true,
    });
    (runtime as any).inboundDebouncer.flushAll();
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(inbound.length, 1);

    await (runtime as any).handleChannelOutbound({
      payload: {
        channel_key: TELEGRAM_PERSONAL_CHANNEL_KEY,
        provider: TELEGRAM_PERSONAL_PROVIDER,
        operation: "send_final",
        idempotency_key: "idem-turn-1",
        remote_jid: SELF_CHAT_JID,
        text: "Nothing scheduled today.",
      },
    });

    // A brand-new, DISTINCT owner command — different id, different text —
    // must be admitted like any real message, not blocked by the guard.
    await (runtime as any).handleInboundMessage({
      externalMessageId: "in-turn-2",
      remoteJid: SELF_CHAT_JID,
      text: "ok, add a reminder for 5pm then",
      fromMe: true,
      isSelfChat: true,
    });
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
    await (runtime as any).handleChannelOutbound({
      payload: {
        channel_key: TELEGRAM_PERSONAL_CHANNEL_KEY,
        provider: TELEGRAM_PERSONAL_PROVIDER,
        operation: "send_final",
        idempotency_key: "idem-other-1",
        remote_jid: "444555666",
        text: "see you soon",
      },
    });
    // No self-chat bookkeeping should have been armed for a non-self-chat
    // send.
    assert.equal(
      (runtime as any).pendingSelfChatSends.length,
      0,
      "a send to someone else must never arm the self-chat pending-echo guard",
    );

    // A genuine, unrelated self-chat command right afterward must still
    // work normally — proves the two are never confused.
    await (runtime as any).handleInboundMessage({
      externalMessageId: "in-unrelated-self-1",
      remoteJid: SELF_CHAT_JID,
      text: "what's next on my list",
      fromMe: true,
      isSelfChat: true,
    });
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
    // > TELEGRAM_MESSAGE_LIMIT (4096) with no spaces forces a hard-cut split
    // into more than one chunk (see message-chunker.ts's splitLongLine).
    const longText = "a".repeat(5000);
    const sizeBefore = (runtime as any).sentMessageIds.size;
    await (runtime as any).handleChannelOutbound({
      payload: {
        channel_key: TELEGRAM_PERSONAL_CHANNEL_KEY,
        provider: TELEGRAM_PERSONAL_PROVIDER,
        operation: "send_final",
        idempotency_key: "idem-chunked-1",
        remote_jid: "444555666",
        text: longText,
      },
    });
    const added = (runtime as any).sentMessageIds.size - sizeBefore;
    assert.ok(added >= 2, `expected at least 2 chunk ids to be tracked, got ${added}`);
  });
});

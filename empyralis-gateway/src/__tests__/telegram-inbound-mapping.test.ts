import { mkdtemp, rm } from "fs/promises";
import { tmpdir } from "os";
import path from "path";
import test from "node:test";
import assert from "node:assert/strict";

import {
  hasExplicitTelegramMention,
  deriveTelegramInboundFields,
  TelegramPersonalRuntime,
} from "../channels/telegram/runtime";
import { GatewayStateDb } from "../state/db";

// THE "FAMILY GROUP" BUG, closed here:
//
// The agent runs on the OWNER's own full-account Telegram session (GramJS,
// not a bot with its own identity). In the owner's 20-person family group,
// the agent was replying to every message, translating/explaining each one
// as though the OWNER had asked about it.
//
// Root cause: getAdapter()'s NewMessage handler used to compute
// `isMentioned` directly from Telegram's raw `rawMessage.mentioned` bit.
// Per MTProto, Message.mentioned is true for an explicit @mention OR for a
// plain reply to a message the PEER sent — and for a full-account session,
// that peer is the human OWNER, not the agent. In an active family group
// where the owner is a normal, chatty participant, replies to the owner's
// own prior messages set `mentioned=true` constantly, with nothing to do
// with anyone wanting the agent's attention. The group gate
// (`is_group && !is_mentioned && !is_reply_to_sage`) was real and correctly
// wired (see telegram-group-gate.test.ts) — it just had a leaky input.
//
// The tests below prove, against GramJS-shaped fixtures (not the
// already-resolved is_group/is_mentioned booleans the gate-level tests
// use), that: (1) hasExplicitTelegramMention no longer treats a plain
// reply-to-owner as a mention, (2) deriveTelegramInboundFields correctly
// attributes a family member's group message to THAT member (never the
// owner) with the group's own identity intact, and (3) the full pipeline —
// a realistic GramJS event for an unaddressed family message — never
// reaches the debouncer/publish step, while an explicitly @-mentioned one
// still does.

function mentionEntity(offset: number, length: number) {
  return { className: "MessageEntityMention", offset, length };
}

function mentionNameEntity(offset: number, length: number, userId: string) {
  return { className: "MessageEntityMentionName", offset, length, userId };
}

const OWNER_SELF = { userId: "1001", username: "sage_owner" };

test("hasExplicitTelegramMention: a plain reply to the owner (Telegram's native `mentioned=true`, no entities) is NOT an explicit mention", () => {
  // This is the exact upstream shape of the false-positive that caused the
  // family group bug: GramJS/MTProto sets rawMessage.mentioned=true here
  // because this message replies to something the OWNER (a real
  // participant) said — but there is no @mention entity anywhere in it.
  const rawMessage = {
    message: "sounds good, see you then",
    mentioned: true,
    entities: [],
  };
  assert.equal(hasExplicitTelegramMention(rawMessage, OWNER_SELF), false);
});

test("hasExplicitTelegramMention: rawMessage.mentioned=true with entities that don't reference self is still NOT a mention", () => {
  const text = "#dinner tonight?";
  const rawMessage = {
    message: text,
    mentioned: true,
    entities: [{ className: "MessageEntityHashtag", offset: 0, length: 7 }],
  };
  assert.equal(hasExplicitTelegramMention(rawMessage, OWNER_SELF), false);
});

test("hasExplicitTelegramMention: an explicit @username entity matching self.username IS a mention", () => {
  const text = "@sage_owner what does this mean?";
  const rawMessage = {
    message: text,
    mentioned: true,
    entities: [mentionEntity(0, "@sage_owner".length)],
  };
  assert.equal(hasExplicitTelegramMention(rawMessage, OWNER_SELF), true);
});

test("hasExplicitTelegramMention: @username matching is case-insensitive", () => {
  const text = "@Sage_Owner can you check";
  const rawMessage = {
    message: text,
    entities: [mentionEntity(0, "@Sage_Owner".length)],
  };
  assert.equal(hasExplicitTelegramMention(rawMessage, OWNER_SELF), true);
});

test("hasExplicitTelegramMention: an @username entity for a DIFFERENT user is not a mention of self", () => {
  const text = "@someone_else can you check";
  const rawMessage = {
    message: text,
    mentioned: false,
    entities: [mentionEntity(0, "@someone_else".length)],
  };
  assert.equal(hasExplicitTelegramMention(rawMessage, OWNER_SELF), false);
});

test("hasExplicitTelegramMention: a MessageEntityMentionName carrying self.userId IS a mention", () => {
  const text = "Aunt Nadia can you check";
  const rawMessage = {
    message: text,
    entities: [mentionNameEntity(0, "Aunt Nadia".length, OWNER_SELF.userId)],
  };
  assert.equal(hasExplicitTelegramMention(rawMessage, OWNER_SELF), true);
});

test("hasExplicitTelegramMention: a MessageEntityMentionName for a different userId is not a mention", () => {
  const text = "Someone Else can you check";
  const rawMessage = {
    message: text,
    entities: [mentionNameEntity(0, "Someone Else".length, "9999")],
  };
  assert.equal(hasExplicitTelegramMention(rawMessage, OWNER_SELF), false);
});

test("hasExplicitTelegramMention: no self identity available (not yet resolved) never matches", () => {
  const rawMessage = {
    message: "@sage_owner hi",
    entities: [mentionEntity(0, "@sage_owner".length)],
  };
  assert.equal(hasExplicitTelegramMention(rawMessage, {}), false);
});

test("hasExplicitTelegramMention: offsets are resolved against the RAW (untrimmed) text", () => {
  // Leading whitespace shifts entity offsets if resolved against a
  // trimmed copy of the text instead of the original.
  const text = "  @sage_owner hi";
  const rawMessage = {
    message: text,
    entities: [mentionEntity(2, "@sage_owner".length)],
  };
  assert.equal(hasExplicitTelegramMention(rawMessage, OWNER_SELF), true);
});

// ---------------------------------------------------------------------------
// deriveTelegramInboundFields: the full per-event mapping, against
// synthetic GramJS-shaped chat/sender/rawMessage fixtures.
// ---------------------------------------------------------------------------

test("deriveTelegramInboundFields: a family member's unaddressed group message is attributed to THAT member, never the owner", () => {
  const derived = deriveTelegramInboundFields({
    isPrivate: false,
    chat: { id: "-100555777", title: "Family" },
    sender: { id: "2002", firstName: "Nadia" },
    rawMessage: {
      message: "Posle",
      mentioned: false,
      entities: [],
    },
    self: OWNER_SELF,
  });
  assert.equal(derived.isGroup, true);
  assert.equal(derived.isSelfChat, false);
  assert.equal(derived.isMentioned, false);
  assert.equal(derived.chatTitle, "Family");
  // The sender identity threaded onward is Nadia's own id/name — NOT the
  // owner's userId/username from `self` anywhere in this result.
  assert.equal(derived.senderJid, "2002");
  assert.equal(derived.pushName, "Nadia");
  assert.notEqual(derived.senderJid, OWNER_SELF.userId);
  assert.notEqual(derived.senderJid, OWNER_SELF.username);
});

test("deriveTelegramInboundFields: the SAME family group, but the message explicitly @mentions the owner/agent — isMentioned flips true, sender attribution unchanged", () => {
  const text = "@sage_owner what does Posle mean?";
  const derived = deriveTelegramInboundFields({
    isPrivate: false,
    chat: { id: "-100555777", title: "Family" },
    sender: { id: "2002", firstName: "Nadia" },
    rawMessage: {
      message: text,
      mentioned: true,
      entities: [mentionEntity(0, "@sage_owner".length)],
    },
    self: OWNER_SELF,
  });
  assert.equal(derived.isGroup, true);
  assert.equal(derived.isMentioned, true);
  // Still Nadia, not the owner, even though the gate would now let this
  // turn through — the CONTENT fix (owner-provenance framing) is separate
  // from this GATE fix; see personal_channel_sage_bridge_service.py.
  assert.equal(derived.senderJid, "2002");
  assert.equal(derived.pushName, "Nadia");
});

test("deriveTelegramInboundFields: a plain reply-to-owner in the group (the false-positive this bug fixed) resolves isMentioned=false", () => {
  const derived = deriveTelegramInboundFields({
    isPrivate: false,
    chat: { id: "-100555777", title: "Family" },
    sender: { id: "2002", firstName: "Nadia" },
    rawMessage: {
      message: "sounds good, see you then",
      // Telegram's own MTProto flag: true here because this replies to a
      // message the OWNER (a real participant) sent — nothing to do with
      // the agent.
      mentioned: true,
      entities: [],
      replyTo: { replyToMsgId: 42 },
    },
    self: OWNER_SELF,
  });
  assert.equal(derived.isMentioned, false);
  // replyToExternalMessageId is still threaded through unconditionally —
  // is_reply_to_sage is resolved separately (handleInboundMessage against
  // sentMessageIds), not by this function.
  assert.equal(derived.replyToExternalMessageId, "42");
});

test("deriveTelegramInboundFields: a private 1:1 DM is never a group and never self-chat", () => {
  const derived = deriveTelegramInboundFields({
    isPrivate: true,
    chat: { id: "555444333" },
    sender: { id: "555444333", firstName: "Nadia" },
    rawMessage: { message: "hey, are you free tonight?" },
    self: OWNER_SELF,
  });
  assert.equal(derived.isGroup, false);
  assert.equal(derived.isSelfChat, false);
});

test("deriveTelegramInboundFields: Saved Messages (chat.self=true) is self-chat, not a group", () => {
  const derived = deriveTelegramInboundFields({
    isPrivate: true,
    chat: { id: OWNER_SELF.userId, self: true },
    sender: { id: OWNER_SELF.userId, username: OWNER_SELF.username },
    rawMessage: { message: "remind me to call mom" },
    self: OWNER_SELF,
  });
  assert.equal(derived.isGroup, false);
  assert.equal(derived.isSelfChat, true);
});

// ---------------------------------------------------------------------------
// Full pipeline: a realistic GramJS-shaped event, through handleInboundMessage
// (via the SAME harness telegram-group-gate.test.ts uses), proving an
// unaddressed family message never reaches the debouncer/publish step end to
// end from the derived mapping, not from a hand-fed is_group/is_mentioned
// boolean.
// ---------------------------------------------------------------------------

function buildMockAdapter() {
  const client = {
    setMessageHandler: (_handler: unknown) => undefined,
    sendMessage: async (remoteJid: string, _text: string) => ({ externalMessageId: "out-1", remoteJid }),
    sendChatAction: async (_remoteJid: string, _action: string) => undefined,
    disconnect: async () => undefined,
    exportSessionString: () => undefined,
  };
  const adapter = {
    connect: async () => ({ client, account: { userId: OWNER_SELF.userId, username: OWNER_SELF.username } }),
  };
  return { adapter, client };
}

async function withRuntime(
  run: (ctx: {
    runtime: TelegramPersonalRuntime;
    inbound: Array<{ message?: Record<string, unknown> }>;
  }) => Promise<void>,
): Promise<void> {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-telegram-inbound-mapping-"));
  let runtime: TelegramPersonalRuntime | undefined;
  try {
    const { adapter } = buildMockAdapter();
    runtime = new TelegramPersonalRuntime(new GatewayStateDb(rootDir), { adapter: adapter as any });
    const inbound: Array<{ message?: Record<string, unknown> }> = [];
    runtime.setPublisher({
      publishEvent: async (_type, payload) => {
        inbound.push(payload as { message?: Record<string, unknown> });
      },
      publishStateUpdate: async () => undefined,
    });
    const anyRuntime = runtime as any;
    await anyRuntime.configStore.patchTelegramConfig({ apiId: 123456, apiHash: "test-hash" });
    await anyRuntime.sessionStore.saveSessionString("mock-session-string");
    await anyRuntime.connectClientInternal();
    await run({ runtime, inbound });
  } finally {
    if (runtime) {
      for (const session of (runtime as any).activeTyping.values()) {
        await session.typing.stop();
      }
    }
    await rm(rootDir, { recursive: true, force: true });
  }
}

test("End to end: an unaddressed family-group message, mapped from a realistic GramJS event, never reaches the debouncer or gets published", async () => {
  await withRuntime(async ({ runtime, inbound }) => {
    const derived = deriveTelegramInboundFields({
      isPrivate: false,
      chat: { id: "-100555777", title: "Family" },
      sender: { id: "2002", firstName: "Nadia" },
      rawMessage: {
        message: "Posle",
        mentioned: false,
        entities: [],
      },
      self: OWNER_SELF,
    });
    await (runtime as any).handleInboundMessage({
      externalMessageId: "in-family-1",
      remoteJid: derived.remoteJid,
      senderJid: derived.senderJid,
      pushName: derived.pushName,
      text: "Posle",
      fromMe: false,
      isGroup: derived.isGroup,
      isSelfChat: derived.isSelfChat,
      isMentioned: derived.isMentioned,
      replyToExternalMessageId: derived.replyToExternalMessageId,
      chatTitle: derived.chatTitle,
    });
    assert.equal((runtime as any).inboundDebouncer.pendingCount(), 0, "an unaddressed family-group message must never be admitted");
    assert.equal(inbound.length, 0, "an unaddressed family-group message must never be published / reach the agent");
  });
});

test("End to end: the SAME family message, but with a plain reply-to-owner shape (mentioned=true, no entities), STILL never reaches the debouncer", () => {
  return withRuntime(async ({ runtime, inbound }) => {
    // Exactly what used to break: Telegram sets mentioned=true because this
    // replies to the OWNER's own earlier message in the group.
    const derived = deriveTelegramInboundFields({
      isPrivate: false,
      chat: { id: "-100555777", title: "Family" },
      sender: { id: "2002", firstName: "Nadia" },
      rawMessage: {
        message: "sounds good, see you then",
        mentioned: true,
        entities: [],
        replyTo: { replyToMsgId: "some-earlier-owner-message" },
      },
      self: OWNER_SELF,
    });
    await (runtime as any).handleInboundMessage({
      externalMessageId: "in-family-2",
      remoteJid: derived.remoteJid,
      senderJid: derived.senderJid,
      pushName: derived.pushName,
      text: "sounds good, see you then",
      fromMe: false,
      isGroup: derived.isGroup,
      isSelfChat: derived.isSelfChat,
      isMentioned: derived.isMentioned,
      replyToExternalMessageId: derived.replyToExternalMessageId,
      chatTitle: derived.chatTitle,
    });
    assert.equal((runtime as any).inboundDebouncer.pendingCount(), 0);
    assert.equal(inbound.length, 0);
  });
});

test("End to end: an explicit @mention in the family group IS admitted and published, attributed to the actual sender", () => {
  return withRuntime(async ({ runtime, inbound }) => {
    const text = "@sage_owner what does Posle mean?";
    const derived = deriveTelegramInboundFields({
      isPrivate: false,
      chat: { id: "-100555777", title: "Family" },
      sender: { id: "2002", firstName: "Nadia" },
      rawMessage: {
        message: text,
        mentioned: true,
        entities: [mentionEntity(0, "@sage_owner".length)],
      },
      self: OWNER_SELF,
    });
    await (runtime as any).handleInboundMessage({
      externalMessageId: "in-family-3",
      remoteJid: derived.remoteJid,
      senderJid: derived.senderJid,
      pushName: derived.pushName,
      text,
      fromMe: false,
      isGroup: derived.isGroup,
      isSelfChat: derived.isSelfChat,
      isMentioned: derived.isMentioned,
      replyToExternalMessageId: derived.replyToExternalMessageId,
      chatTitle: derived.chatTitle,
    });
    assert.equal((runtime as any).inboundDebouncer.pendingCount(), 1, "an explicit @mention must still be admitted");
    (runtime as any).inboundDebouncer.flushAll();
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(inbound.length, 1);
    const message = inbound[0].message as Record<string, unknown>;
    assert.equal(message.is_group, true);
    assert.equal(message.is_mentioned, true);
    // Sender attribution in the published payload is Nadia's own jid/name —
    // never the owner's.
    assert.equal(message.sender_jid, "2002");
    assert.equal(message.push_name, "Nadia");
    assert.equal(message.chat_title, "Family");
  });
});

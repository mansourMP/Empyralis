import { strict as assert } from "node:assert";
import { isBroadcastTelegramChat } from "../src/telegram/client-factory.js";
import { buildSignedInbound } from "../src/telegram/hmac.js";
import { InboundHandler } from "../src/telegram/inbound-handler.js";
import { SessionPool } from "../src/session-pool.js";

// ── isBroadcastTelegramChat (client-factory.js) ─────────────────────────────
// Mirrors empyralis-gateway/src/channels/telegram/runtime.ts's function of
// the same name — see that one's doc for the full "comments under every
// post" incident this closes. Independent implementation (no shared
// package between the two services) but must stay behaviorally identical.

async function test_broadcastChannelDetected() {
  assert.equal(isBroadcastTelegramChat({ broadcast: true, id: "-100999", title: "Announcements" }), true);
  console.log("  ✓ broadcast:true is detected as a broadcast channel");
}

async function test_supergroupNotBroadcast() {
  // A supergroup is ALSO an Api.Channel, just with broadcast:false — must
  // not be misclassified as broadcast (it's still an ordinary group for
  // gating purposes).
  assert.equal(isBroadcastTelegramChat({ broadcast: false, megagroup: true, id: "-100555" }), false);
  console.log("  ✓ a supergroup (broadcast:false) is NOT a broadcast channel");
}

async function test_plainChatNotBroadcast() {
  assert.equal(isBroadcastTelegramChat({ id: "-100777", title: "Family" }), false);
  console.log("  ✓ a plain group chat entity (no broadcast field) is NOT a broadcast channel");
}

async function test_undefinedChatNotBroadcast() {
  // Fails closed to false (never mis-drop an ordinary chat because the
  // entity wasn't resolvable), not to true.
  assert.equal(isBroadcastTelegramChat(undefined), false);
  console.log("  ✓ an unresolved chat entity is NOT treated as broadcast");
}

// ── buildSignedInbound (hmac.js) ────────────────────────────────────────────
// Before this fix, is_group/is_mentioned/is_reply_to_sage were never put on
// the wire at all, making the backend's group/mention gate
// (personal_channels_service.py's cloud_channel_inbound) a permanent no-op
// — see that function's own "WIRE REALITY TODAY" comment.

async function test_buildSignedInboundDefaultsGroupFieldsFalse() {
  process.env.BACKEND_HMAC_SECRET = "test-secret";
  const body = buildSignedInbound({
    sessionId: "sess-1",
    messageId: "42",
    senderId: "555",
    text: "hello",
  });
  assert.equal(body.message.is_group, false);
  assert.equal(body.message.is_mentioned, false);
  assert.equal(body.message.is_reply_to_sage, false);
  console.log("  ✓ is_group/is_mentioned/is_reply_to_sage default to false when omitted (DM behavior unaffected)");
}

async function test_buildSignedInboundForwardsGroupFields() {
  process.env.BACKEND_HMAC_SECRET = "test-secret";
  const body = buildSignedInbound({
    sessionId: "sess-1",
    messageId: "42",
    senderId: "555",
    text: "@sage help",
    isGroup: true,
    isMentioned: true,
    isReplyToSage: false,
  });
  assert.equal(body.message.is_group, true);
  assert.equal(body.message.is_mentioned, true);
  assert.equal(body.message.is_reply_to_sage, false);
  console.log("  ✓ is_group/is_mentioned/is_reply_to_sage are forwarded onto the signed wire payload");
}

async function test_buildSignedInboundExistingFieldsUnchanged() {
  process.env.BACKEND_HMAC_SECRET = "test-secret";
  const body = buildSignedInbound({
    sessionId: "sess-1",
    messageId: "42",
    senderId: "555",
    senderName: "Alice",
    linkedUsername: "sage_owner",
    text: "hi",
    workspaceId: "ws-1",
  });
  assert.equal(body.session_id, "sess-1");
  assert.equal(body.workspace_id, "ws-1");
  assert.equal(body.channel_key, "telegram_personal");
  assert.equal(body.message.external_message_id, "42");
  assert.equal(body.message.sender_id, "555");
  assert.equal(body.message.sender_name, "Alice");
  assert.equal(body.message.linked_username, "sage_owner");
  assert.equal(body.message.text, "hi");
  assert.ok(body.signature);
  console.log("  ✓ pre-existing signed fields are unchanged (regression)");
}

// ── InboundHandler (inbound-handler.js) ─────────────────────────────────────

function makeHandler(overrides = {}) {
  return new InboundHandler({
    sessionId: "sess-1",
    workspaceId: "ws-1",
    linkedUserId: "1001",
    linkedUsername: "sage_owner",
    logger: undefined,
    ...overrides,
  });
}

function normalizedMessage(overrides = {}) {
  return {
    message: {
      external_message_id: "10",
      remote_jid: "-100555",
      sender_jid: "2002",
      push_name: "Nadia",
      text: "hello",
      from_me: false,
      is_self_chat: false,
      is_group: false,
      entities: [],
      reply_to_msg_id: null,
      ...overrides,
    },
  };
}

async function withMockFetch(responseBody, fn) {
  const calls = [];
  const original = globalThis.fetch;
  globalThis.fetch = async (url, opts) => {
    calls.push({ url, opts });
    return {
      ok: true,
      status: 200,
      json: async () => responseBody ?? { reply_text: "ok" },
    };
  };
  try {
    await fn(calls);
  } finally {
    globalThis.fetch = original;
  }
}

async function test_addSentMessageIdIsScopedPerChat() {
  const handler = makeHandler();
  handler.addSentMessageId("-100555", "42");
  handler.addSentMessageId("-100777", "99");

  assert.ok(handler.sentMessageIds.get("-100555")?.has("42"));
  assert.ok(!handler.sentMessageIds.get("-100777")?.has("42"), "chat -100777 must not see chat -100555's sent id");
  assert.ok(handler.sentMessageIds.get("-100777")?.has("99"));
  console.log("  ✓ addSentMessageId scopes sent ids per remoteJid, not globally");
}

async function test_replyToSageCrossChatCollisionIsGone() {
  // THE bug: Telegram message ids are small integers scoped per chat.
  // Sage sends message #42 into chat A; someone in UNRELATED chat B replies
  // to THEIR OWN message #42 (nothing to do with Sage). Before the fix this
  // incorrectly resolved isReplyToSage=true for chat B because the set was
  // flat/global.
  const handler = makeHandler();
  handler.addSentMessageId("-100-chat-A", "42");

  await withMockFetch({ reply_text: "should not happen" }, async (calls) => {
    const result = await handler.handleMessage(
      normalizedMessage({
        remote_jid: "-100-chat-B",
        is_group: true,
        text: "sounds good, see you then",
        reply_to_msg_id: "42",
        entities: [],
      })
    );
    assert.equal(result.skipped, true);
    assert.equal(result.reason, "group_no_mention");
    assert.equal(calls.length, 0, "an unaddressed cross-chat false-positive must never reach the backend");
  });
  console.log("  ✓ a reply-to-id collision across two different chats no longer false-positives isReplyToSage");
}

async function test_replyToSageWithinTheSameChatStillWorks() {
  const handler = makeHandler();
  handler.addSentMessageId("-100555", "42");

  await withMockFetch({ reply_text: "ok" }, async (calls) => {
    const result = await handler.handleMessage(
      normalizedMessage({
        remote_jid: "-100555",
        is_group: true,
        text: "sounds good",
        reply_to_msg_id: "42",
        entities: [],
      })
    );
    assert.equal(result.forwarded, true);
    assert.equal(calls.length, 1);
    const sentBody = JSON.parse(calls[0].opts.body);
    assert.equal(sentBody.message.is_group, true);
    assert.equal(sentBody.message.is_reply_to_sage, true);
    assert.equal(sentBody.message.is_mentioned, false);
  });
  console.log("  ✓ a genuine reply to Sage's own message, within the SAME chat, is still recognized and forwarded with is_reply_to_sage=true");
}

async function test_unaddressedGroupMessageNeverCallsBackend() {
  const handler = makeHandler();
  await withMockFetch(null, async (calls) => {
    const result = await handler.handleMessage(
      normalizedMessage({ is_group: true, text: "just chatting about the weekend", entities: [] })
    );
    assert.equal(result.skipped, true);
    assert.equal(result.reason, "group_no_mention");
    assert.equal(calls.length, 0);
  });
  console.log("  ✓ unaddressed group chatter never reaches the backend at all");
}

async function test_mentionedGroupMessageForwardsWithGroupFieldsSet() {
  const handler = makeHandler();
  await withMockFetch({ reply_text: "sure" }, async (calls) => {
    const result = await handler.handleMessage(
      normalizedMessage({
        is_group: true,
        text: "@sage_owner what time is dinner?",
        entities: [{ className: "MessageEntityMention", offset: 0, length: "@sage_owner".length }],
      })
    );
    assert.equal(result.forwarded, true);
    assert.equal(calls.length, 1);
    const sentBody = JSON.parse(calls[0].opts.body);
    assert.equal(sentBody.message.is_group, true);
    assert.equal(sentBody.message.is_mentioned, true);
    assert.equal(sentBody.message.is_reply_to_sage, false);
  });
  console.log("  ✓ an explicitly @mentioned group message is forwarded with is_group/is_mentioned correctly set on the wire");
}

async function test_selfChatCommandIsAdmittedWhenIsSelfChatIsTrue() {
  // Regression proof for the "dropping the owner's own Saved-Messages
  // command" bug: the root cause was client-factory.js computing
  // isSelfChat wrong (comparing chatId to senderId, which is empty for
  // outgoing messages) — inbound-handler.js's OWN from_me/is_self_chat
  // check here was always correct GIVEN correct input. This proves it is.
  const handler = makeHandler();
  await withMockFetch({ reply_text: "sure, noted" }, async (calls) => {
    const result = await handler.handleMessage(
      normalizedMessage({
        remote_jid: "1001",
        sender_jid: "1001",
        from_me: true,
        is_self_chat: true,
        text: "remind me to call mom",
      })
    );
    assert.equal(result.forwarded, true);
    assert.equal(calls.length, 1);
  });
  console.log("  ✓ a genuine self-chat command (from_me + is_self_chat) is admitted, not dropped");
}

async function test_ordinaryOutgoingMessageStillDropped() {
  // Regression guard: an ordinary outgoing message to someone else (NOT
  // self-chat) must still be dropped as an echo, unaffected by the
  // self-chat fix.
  const handler = makeHandler();
  await withMockFetch(null, async (calls) => {
    const result = await handler.handleMessage(
      normalizedMessage({
        remote_jid: "999888",
        sender_jid: "1001",
        from_me: true,
        is_self_chat: false,
        text: "sent to someone else",
      })
    );
    assert.equal(result.skipped, true);
    assert.equal(result.reason, "from_me");
    assert.equal(calls.length, 0);
  });
  console.log("  ✓ an ordinary outgoing message to someone else (not self-chat) is still dropped as an echo");
}

// ── SessionPool.sendMessage -> addSentMessageId wiring (session-pool.js) ───
// The only real call site of addSentMessageId — a fake session (no Redis
// needed) proves it now passes the target remoteJid, not just the id.

function makeFakeSessionPool(session) {
  const pool = new SessionPool({ logger: undefined });
  pool.sessions.set("sess-1", session);
  return pool;
}

async function test_sessionPoolPassesRemoteJidToAddSentMessageId() {
  const addSentMessageIdCalls = [];
  const session = {
    status: "connected",
    client: {
      getInputEntity: async () => "peer",
      sendMessage: async () => ({ id: "777" }),
    },
    rateLimiter: { consume: async () => ({ allowed: true, tokensLeft: 19 }) },
    outbox: { enqueue: async () => undefined, acknowledge: async () => undefined },
    inboundHandler: {
      addSentMessageId: (...args) => addSentMessageIdCalls.push(args),
    },
  };
  const pool = makeFakeSessionPool(session);
  const result = await pool.sendMessage("sess-1", { text: "hello", remoteJid: "-100555" });

  assert.equal(result.ok, true);
  assert.equal(addSentMessageIdCalls.length, 1);
  assert.deepEqual(addSentMessageIdCalls[0], ["-100555", "777"]);
  console.log("  ✓ SessionPool.sendMessage calls addSentMessageId(remoteJid, messageId), not just(messageId)");
}

async function test_sessionPoolSelfChatSendUsesNormalizedMeAsRemoteJid() {
  // sendMessage defaults payload.remoteJid to "me" when the caller omits
  // it (self-chat shorthand) — addSentMessageId must be scoped to that
  // SAME normalized value, not an empty/undefined jid.
  const addSentMessageIdCalls = [];
  const session = {
    status: "connected",
    client: {
      getInputEntity: async () => "peer",
      sendMessage: async () => ({ id: "778" }),
    },
    rateLimiter: { consume: async () => ({ allowed: true, tokensLeft: 19 }) },
    outbox: { enqueue: async () => undefined, acknowledge: async () => undefined },
    inboundHandler: {
      addSentMessageId: (...args) => addSentMessageIdCalls.push(args),
    },
  };
  const pool = makeFakeSessionPool(session);
  await pool.sendMessage("sess-1", { text: "remind me later" });

  assert.deepEqual(addSentMessageIdCalls[0], ["me", "778"]);
  console.log("  ✓ an omitted remoteJid (self-chat) still scopes addSentMessageId to the normalized 'me' target");
}

// ── Runner ───────────────────────────────────────────────────────────────

const tests = [
  { name: "isBroadcastTelegramChat: broadcast:true detected", fn: test_broadcastChannelDetected },
  { name: "isBroadcastTelegramChat: supergroup is not broadcast", fn: test_supergroupNotBroadcast },
  { name: "isBroadcastTelegramChat: plain chat is not broadcast", fn: test_plainChatNotBroadcast },
  { name: "isBroadcastTelegramChat: undefined chat is not broadcast", fn: test_undefinedChatNotBroadcast },
  { name: "buildSignedInbound: group fields default false", fn: test_buildSignedInboundDefaultsGroupFieldsFalse },
  { name: "buildSignedInbound: group fields forwarded", fn: test_buildSignedInboundForwardsGroupFields },
  { name: "buildSignedInbound: existing fields unchanged", fn: test_buildSignedInboundExistingFieldsUnchanged },
  { name: "InboundHandler: addSentMessageId scoped per chat", fn: test_addSentMessageIdIsScopedPerChat },
  { name: "InboundHandler: cross-chat reply-id collision gone", fn: test_replyToSageCrossChatCollisionIsGone },
  { name: "InboundHandler: same-chat reply to Sage still works", fn: test_replyToSageWithinTheSameChatStillWorks },
  { name: "InboundHandler: unaddressed group never calls backend", fn: test_unaddressedGroupMessageNeverCallsBackend },
  { name: "InboundHandler: mentioned group forwards with fields set", fn: test_mentionedGroupMessageForwardsWithGroupFieldsSet },
  { name: "InboundHandler: self-chat command admitted", fn: test_selfChatCommandIsAdmittedWhenIsSelfChatIsTrue },
  { name: "InboundHandler: ordinary outgoing still dropped", fn: test_ordinaryOutgoingMessageStillDropped },
  { name: "SessionPool: sendMessage passes remoteJid to addSentMessageId", fn: test_sessionPoolPassesRemoteJidToAddSentMessageId },
  { name: "SessionPool: self-chat send scopes to normalized 'me'", fn: test_sessionPoolSelfChatSendUsesNormalizedMeAsRemoteJid },
];

let passed = 0;
let failed = 0;

for (const { name, fn } of tests) {
  try {
    await fn();
    passed++;
  } catch (err) {
    failed++;
    console.error(`  ✗ ${name}: ${err.stack || err.message}`);
  }
}

console.log(`\n${passed} passed, ${failed} failed, ${tests.length} total`);
if (failed > 0) process.exit(1);

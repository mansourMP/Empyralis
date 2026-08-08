import { test } from "node:test";
import assert from "node:assert/strict";

import { mapInboundEvent, deriveIsGroupBestEffort } from "../inbound-mapping.js";

test("mapInboundEvent maps a plain DM correctly", () => {
  const fixedNow = () => new Date("2026-08-08T03:00:00.000Z");
  const payload = mapInboundEvent(
    {
      from: "+15555550123",
      content: "hey are you there",
      timestamp: 1_700_000_000,
      messageId: "msg-1",
      senderId: "user-1",
      sessionKey: "agent:main:+15555550123",
    },
    { channelId: "telegram", accountId: "acct-1", conversationId: "123456" },
    fixedNow,
  );
  assert.equal(payload.schema, "empyralis.openclaw_bridge.inbound.v1");
  assert.equal(payload.channel, "telegram");
  assert.equal(payload.accountId, "acct-1");
  assert.equal(payload.conversationId, "123456");
  assert.equal(payload.senderId, "user-1");
  assert.equal(payload.content, "hey are you there");
  assert.equal(payload.isGroup, undefined);
  assert.equal(payload.wasMentioned, undefined);
  assert.equal(payload.isReply, false);
  assert.equal(payload.openclawSessionKey, "agent:main:+15555550123");
  assert.equal(payload.receivedAt, "2026-08-08T03:00:00.000Z");
});

test("mapInboundEvent captures reply-to fields", () => {
  const payload = mapInboundEvent(
    {
      from: "u1",
      content: "reply text",
      replyToId: "parent-1",
      replyToSender: "bot",
    },
    {},
  );
  assert.equal(payload.isReply, true);
  assert.equal(payload.replyToId, "parent-1");
  assert.equal(payload.replyToSender, "bot");
});

test("mapInboundEvent falls back to replyToIdFull when replyToId is absent", () => {
  const payload = mapInboundEvent(
    { from: "u1", content: "x", replyToIdFull: "full-id-123" },
    {},
  );
  assert.equal(payload.isReply, true);
  assert.equal(payload.replyToId, "full-id-123");
});

test("mapInboundEvent forwards media metadata", () => {
  const payload = mapInboundEvent(
    {
      from: "u1",
      content: "",
      metadata: {
        mediaUrl: "https://example.com/a.jpg",
        mediaType: "image/jpeg",
        mediaUrls: ["https://example.com/a.jpg", "https://example.com/b.jpg"],
        mediaTypes: ["image/jpeg", "image/png"],
      },
    },
    { channelId: "whatsapp" },
  );
  assert.equal(payload.media.url, "https://example.com/a.jpg");
  assert.equal(payload.media.type, "image/jpeg");
  assert.deepEqual(payload.media.urls, ["https://example.com/a.jpg", "https://example.com/b.jpg"]);
  assert.deepEqual(payload.media.types, ["image/jpeg", "image/png"]);
});

test("mapInboundEvent forwards raw metadata untouched for downstream gating", () => {
  const metadata = { channelName: "general", guildId: "g-1", topicName: "t" };
  const payload = mapInboundEvent({ from: "u1", content: "hi", metadata }, {});
  assert.deepEqual(payload.rawMetadata, metadata);
});

test("deriveIsGroupBestEffort returns true when metadata.channelName is present", () => {
  assert.equal(
    deriveIsGroupBestEffort({ from: "u1", content: "x", metadata: { channelName: "general" } }),
    true,
  );
});

test("deriveIsGroupBestEffort returns true when metadata.guildId is present (string or number)", () => {
  assert.equal(
    deriveIsGroupBestEffort({ from: "u1", content: "x", metadata: { guildId: "g-1" } }),
    true,
  );
  assert.equal(
    deriveIsGroupBestEffort({ from: "u1", content: "x", metadata: { guildId: 42 } }),
    true,
  );
});

test("deriveIsGroupBestEffort returns undefined (not false) with no signal — unknown must never be misread as DM", () => {
  assert.equal(deriveIsGroupBestEffort({ from: "u1", content: "x" }), undefined);
  assert.equal(deriveIsGroupBestEffort({ from: "u1", content: "x", metadata: {} }), undefined);
  assert.equal(
    deriveIsGroupBestEffort({ from: "u1", content: "x", metadata: { channelName: "" } }),
    undefined,
  );
});

test("mapInboundEvent defaults content to empty string, never undefined", () => {
  // @ts-expect-error - exercising a defensive runtime path against a
  // malformed upstream event
  const payload = mapInboundEvent({ from: "u1", content: undefined }, {});
  assert.equal(payload.content, "");
});

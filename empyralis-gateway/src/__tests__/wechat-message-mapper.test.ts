import assert from "node:assert/strict";
import test from "node:test";

import { mapWeChatInboundMessage, wechatChannelKey, wechatProvider } from "../channels/wechat/message-mapper";

test("wechatChannelKey/wechatProvider differ between Official Account and WeCom", () => {
  assert.equal(wechatChannelKey("official_account"), "wechat_official");
  assert.equal(wechatChannelKey("wecom"), "wechat_work");
  assert.equal(wechatProvider("official_account"), "wechat_official_account_api");
  assert.equal(wechatProvider("wecom"), "wecom_official_api");
});

test("maps a text message to the shared GatewayChannelInboundPayload shape", () => {
  const payload = mapWeChatInboundMessage(
    {
      ToUserName: "gh_1234567890",
      FromUserName: "oABC123openid",
      CreateTime: "1348831860",
      MsgType: "text",
      Content: "hello there",
      MsgId: "1234567890123456",
    },
    "official_account",
  );
  assert.ok(payload);
  assert.equal(payload!.channel_key, "wechat_official");
  assert.equal(payload!.provider, "wechat_official_account_api");
  assert.equal(payload!.message.external_message_id, "1234567890123456");
  assert.equal(payload!.message.remote_jid, "oABC123openid");
  assert.equal(payload!.message.sender_jid, "oABC123openid");
  assert.equal(payload!.message.text, "hello there");
  assert.equal(payload!.message.from_me, false);
  assert.equal(payload!.message.received_at, new Date(1348831860 * 1000).toISOString());
});

test("maps a WeCom text message with the wecom channel key/provider", () => {
  const payload = mapWeChatInboundMessage(
    {
      FromUserName: "zhangsan",
      CreateTime: "1500000000",
      MsgType: "text",
      Content: "hi from wecom",
      MsgId: "999",
    },
    "wecom",
  );
  assert.ok(payload);
  assert.equal(payload!.channel_key, "wechat_work");
  assert.equal(payload!.provider, "wecom_official_api");
});

test("returns null for non-text message types (image/voice/event)", () => {
  assert.equal(
    mapWeChatInboundMessage({ FromUserName: "u1", MsgType: "image", MsgId: "1" }, "official_account"),
    null,
  );
  assert.equal(
    mapWeChatInboundMessage({ FromUserName: "u1", MsgType: "event", Event: "subscribe" }, "official_account"),
    null,
  );
});

test("returns null when Content or FromUserName is missing/empty", () => {
  assert.equal(
    mapWeChatInboundMessage({ FromUserName: "u1", MsgType: "text", Content: "" }, "official_account"),
    null,
  );
  assert.equal(
    mapWeChatInboundMessage({ FromUserName: "", MsgType: "text", Content: "hi" }, "official_account"),
    null,
  );
});

test("synthesizes an external_message_id when MsgId is absent", () => {
  const payload = mapWeChatInboundMessage(
    { FromUserName: "u1", CreateTime: "42", MsgType: "text", Content: "hi" },
    "official_account",
  );
  assert.ok(payload);
  assert.equal(payload!.message.external_message_id, "u1:42");
});

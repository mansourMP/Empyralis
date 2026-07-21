import assert from "node:assert/strict";
import test from "node:test";

import type { GatewayChannelInboundPayload } from "../protocol/types";
import { computeWeChatSignature } from "../channels/wechat/signature";
import { startWeChatOfficialBridge, type WeChatOfficialBridge } from "../channels/wechat/server";
import type { WeChatOfficialConfig } from "../channels/wechat/types";

const config: WeChatOfficialConfig = {
  accountKind: "official_account",
  appId: "wx-app-id",
  appSecret: "wx-secret",
  token: "verify-token",
};

function signedQuery(extra: Record<string, string> = {}): URLSearchParams {
  const timestamp = "1409659589";
  const nonce = "263014780";
  const signature = computeWeChatSignature(config.token, timestamp, nonce);
  return new URLSearchParams({ timestamp, nonce, signature, ...extra });
}

async function withBridge(
  onInboundEvent: (payload: GatewayChannelInboundPayload) => void | Promise<void>,
  run: (bridge: WeChatOfficialBridge) => Promise<void>,
): Promise<void> {
  const bridge = await startWeChatOfficialBridge({
    config,
    onInboundEvent,
    host: "127.0.0.1",
    logger: { info: () => undefined, warn: () => undefined, error: () => undefined },
  });
  try {
    await run(bridge);
  } finally {
    await bridge.close();
  }
}

test("GET server-verification handshake echoes echostr when the signature is valid", async () => {
  await withBridge(
    () => undefined,
    async (bridge) => {
      const query = signedQuery({ echostr: "the-echo-value" });
      const response = await fetch(`${bridge.url}?${query.toString()}`);
      assert.equal(response.status, 200);
      assert.equal(await response.text(), "the-echo-value");
    },
  );
});

test("GET server-verification handshake rejects an invalid signature", async () => {
  await withBridge(
    () => undefined,
    async (bridge) => {
      const query = new URLSearchParams({ timestamp: "1", nonce: "2", signature: "bad", echostr: "should-not-echo" });
      const response = await fetch(`${bridge.url}?${query.toString()}`);
      assert.equal(response.status, 403);
      assert.notEqual(await response.text(), "should-not-echo");
    },
  );
});

const TEXT_MESSAGE_XML = `<xml>
  <ToUserName><![CDATA[gh_1234567890]]></ToUserName>
  <FromUserName><![CDATA[oABC123openid]]></FromUserName>
  <CreateTime>1348831860</CreateTime>
  <MsgType><![CDATA[text]]></MsgType>
  <Content><![CDATA[hello agent]]></Content>
  <MsgId>1234567890123456</MsgId>
</xml>`;

test("POST inbound callback with a valid signature maps and publishes a text message, then acks success", async () => {
  const received: GatewayChannelInboundPayload[] = [];
  await withBridge(
    (payload) => {
      received.push(payload);
    },
    async (bridge) => {
      const query = signedQuery();
      const response = await fetch(`${bridge.url}?${query.toString()}`, {
        method: "POST",
        headers: { "content-type": "text/xml" },
        body: TEXT_MESSAGE_XML,
      });
      assert.equal(response.status, 200);
      assert.equal(await response.text(), "success");
      assert.equal(received.length, 1);
      assert.equal(received[0].channel_key, "wechat_official");
      assert.equal(received[0].message.remote_jid, "oABC123openid");
      assert.equal(received[0].message.text, "hello agent");
    },
  );
});

test("POST inbound callback with an invalid signature is rejected and never reaches onInboundEvent", async () => {
  const received: GatewayChannelInboundPayload[] = [];
  await withBridge(
    (payload) => {
      received.push(payload);
    },
    async (bridge) => {
      const query = new URLSearchParams({ timestamp: "1", nonce: "2", signature: "bad" });
      const response = await fetch(`${bridge.url}?${query.toString()}`, {
        method: "POST",
        headers: { "content-type": "text/xml" },
        body: TEXT_MESSAGE_XML,
      });
      assert.equal(response.status, 403);
      assert.equal(received.length, 0);
    },
  );
});

test("POST inbound callback for a non-text event still acks success but does not publish", async () => {
  const received: GatewayChannelInboundPayload[] = [];
  const eventXml = `<xml><ToUserName><![CDATA[gh_123]]></ToUserName><FromUserName><![CDATA[oABC]]></FromUserName><CreateTime>1</CreateTime><MsgType><![CDATA[event]]></MsgType><Event><![CDATA[subscribe]]></Event></xml>`;
  await withBridge(
    (payload) => {
      received.push(payload);
    },
    async (bridge) => {
      const query = signedQuery();
      const response = await fetch(`${bridge.url}?${query.toString()}`, {
        method: "POST",
        headers: { "content-type": "text/xml" },
        body: eventXml,
      });
      assert.equal(response.status, 200);
      assert.equal(await response.text(), "success");
      assert.equal(received.length, 0);
    },
  );
});

test("a request to an unregistered path 404s", async () => {
  await withBridge(
    () => undefined,
    async (bridge) => {
      const base = new URL(bridge.url);
      const response = await fetch(`${base.protocol}//${base.host}/not-the-callback-path`);
      assert.equal(response.status, 404);
    },
  );
});

test("still acks success when onInboundEvent throws, so Tencent does not retry-storm a downstream bug", async () => {
  await withBridge(
    () => {
      throw new Error("downstream failure");
    },
    async (bridge) => {
      const query = signedQuery();
      const response = await fetch(`${bridge.url}?${query.toString()}`, {
        method: "POST",
        headers: { "content-type": "text/xml" },
        body: TEXT_MESSAGE_XML,
      });
      assert.equal(response.status, 200);
      assert.equal(await response.text(), "success");
    },
  );
});

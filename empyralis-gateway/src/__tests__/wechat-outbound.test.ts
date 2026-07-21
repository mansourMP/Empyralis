import assert from "node:assert/strict";
import test from "node:test";

import { sendWeChatTextMessage } from "../channels/wechat/outbound";
import { WeChatAccessTokenManager } from "../channels/wechat/token-manager";
import type { WeChatOfficialConfig } from "../channels/wechat/types";

const officialAccountConfig: WeChatOfficialConfig = {
  accountKind: "official_account",
  appId: "wx-app-id",
  appSecret: "wx-secret",
  token: "verify-token",
};

const wecomConfig: WeChatOfficialConfig = {
  accountKind: "wecom",
  appId: "corp-id",
  appSecret: "corp-secret",
  token: "verify-token",
  agentId: "1000002",
};

function stubTokenManager(config: WeChatOfficialConfig, accessToken = "tok-1"): WeChatAccessTokenManager {
  const fetchFn = (async () => ({
    ok: true,
    status: 200,
    json: async () => ({ access_token: accessToken, expires_in: 7200 }),
  })) as unknown as typeof fetch;
  return new WeChatAccessTokenManager(config, fetchFn);
}

function recordingFetch(bodies: Array<Record<string, unknown>>): { fetch: typeof fetch; requests: Array<{ url: string; body: Record<string, unknown> }> } {
  const requests: Array<{ url: string; body: Record<string, unknown> }> = [];
  let index = 0;
  const fn = (async (url: string, init?: RequestInit) => {
    const body = init?.body ? JSON.parse(String(init.body)) : {};
    requests.push({ url, body });
    const responseBody = bodies[Math.min(index, bodies.length - 1)];
    index += 1;
    return { ok: true, status: 200, json: async () => responseBody } as Response;
  }) as typeof fetch;
  return { fetch: fn, requests };
}

test("sends a text message to the Official Account customer-service endpoint", async () => {
  const { fetch: fetchFn, requests } = recordingFetch([{ errcode: 0, errmsg: "ok" }]);
  const tokenManager = stubTokenManager(officialAccountConfig);
  const result = await sendWeChatTextMessage(officialAccountConfig, tokenManager, "oABC123openid", "hi there", fetchFn);
  assert.equal(result.ok, true);
  assert.match(requests[0].url, /^https:\/\/api\.weixin\.qq\.com\/cgi-bin\/message\/custom\/send\?access_token=tok-1$/);
  assert.deepEqual(requests[0].body, { touser: "oABC123openid", msgtype: "text", text: { content: "hi there" } });
});

test("sends a text message to the WeCom app message-send endpoint with agentid", async () => {
  const { fetch: fetchFn, requests } = recordingFetch([{ errcode: 0, errmsg: "ok" }]);
  const tokenManager = stubTokenManager(wecomConfig);
  const result = await sendWeChatTextMessage(wecomConfig, tokenManager, "zhangsan", "hi there", fetchFn);
  assert.equal(result.ok, true);
  assert.match(requests[0].url, /^https:\/\/qyapi\.weixin\.qq\.com\/cgi-bin\/message\/send\?access_token=tok-1$/);
  assert.deepEqual(requests[0].body, {
    touser: "zhangsan",
    msgtype: "text",
    agentid: 1000002,
    text: { content: "hi there" },
  });
});

test("returns ok:false without throwing when Tencent returns a non-zero errcode", async () => {
  const { fetch: fetchFn } = recordingFetch([{ errcode: 45015, errmsg: "response out of time limit" }]);
  const tokenManager = stubTokenManager(officialAccountConfig);
  const result = await sendWeChatTextMessage(officialAccountConfig, tokenManager, "oABC123openid", "hi", fetchFn);
  assert.equal(result.ok, false);
  assert.equal(result.errcode, 45015);
});

test("invalidates the token and retries once on a token-invalid errcode", async () => {
  const { fetch: fetchFn, requests } = recordingFetch([
    { errcode: 42001, errmsg: "access_token expired" },
    { errcode: 0, errmsg: "ok" },
  ]);
  let tokenFetchCount = 0;
  const tokenFetchFn = (async () => {
    tokenFetchCount += 1;
    return {
      ok: true,
      status: 200,
      json: async () => ({ access_token: `tok-${tokenFetchCount}`, expires_in: 7200 }),
    } as Response;
  }) as typeof fetch;
  const tokenManager = new WeChatAccessTokenManager(officialAccountConfig, tokenFetchFn);

  const result = await sendWeChatTextMessage(officialAccountConfig, tokenManager, "oABC123openid", "hi", fetchFn);
  assert.equal(result.ok, true);
  assert.equal(tokenFetchCount, 2);
  assert.match(requests[0].url, /access_token=tok-1$/);
  assert.match(requests[1].url, /access_token=tok-2$/);
});

test("rejects an empty remote_jid or text before making any network call", async () => {
  const { fetch: fetchFn, requests } = recordingFetch([{ errcode: 0 }]);
  const tokenManager = stubTokenManager(officialAccountConfig);
  const result = await sendWeChatTextMessage(officialAccountConfig, tokenManager, "", "hi", fetchFn);
  assert.equal(result.ok, false);
  assert.equal(requests.length, 0);
});

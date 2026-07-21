import assert from "node:assert/strict";
import test from "node:test";

import { WeChatAccessTokenError, WeChatAccessTokenManager } from "../channels/wechat/token-manager";
import type { WeChatOfficialConfig } from "../channels/wechat/types";

function fakeFetch(
  responses: Array<{ status?: number; body: Record<string, unknown> }>,
): { fetch: typeof fetch; calls: string[] } {
  const calls: string[] = [];
  let index = 0;
  const fn = (async (url: string) => {
    calls.push(url);
    const response = responses[Math.min(index, responses.length - 1)];
    index += 1;
    return {
      ok: (response.status ?? 200) < 400,
      status: response.status ?? 200,
      json: async () => response.body,
    } as Response;
  }) as typeof fetch;
  return { fetch: fn, calls };
}

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

test("fetches and caches an Official Account token, hitting the client_credential endpoint", async () => {
  const { fetch: fetchFn, calls } = fakeFetch([{ body: { access_token: "tok-1", expires_in: 7200 } }]);
  const manager = new WeChatAccessTokenManager(officialAccountConfig, fetchFn);
  const token = await manager.getAccessToken();
  assert.equal(token, "tok-1");
  assert.match(calls[0], /^https:\/\/api\.weixin\.qq\.com\/cgi-bin\/token\?/);
  assert.match(calls[0], /grant_type=client_credential/);
  assert.match(calls[0], /appid=wx-app-id/);
  assert.match(calls[0], /secret=wx-secret/);

  // Second call within TTL should reuse the cache, not fetch again.
  const token2 = await manager.getAccessToken();
  assert.equal(token2, "tok-1");
  assert.equal(calls.length, 1);
});

test("fetches a WeCom token from the corpid/corpsecret gettoken endpoint", async () => {
  const { fetch: fetchFn, calls } = fakeFetch([{ body: { access_token: "corp-tok-1", expires_in: 7200 } }]);
  const manager = new WeChatAccessTokenManager(wecomConfig, fetchFn);
  const token = await manager.getAccessToken();
  assert.equal(token, "corp-tok-1");
  assert.match(calls[0], /^https:\/\/qyapi\.weixin\.qq\.com\/cgi-bin\/gettoken\?/);
  assert.match(calls[0], /corpid=corp-id/);
  assert.match(calls[0], /corpsecret=corp-secret/);
});

test("refreshes the token once the cached one is past its skew-adjusted expiry", async () => {
  let nowMs = 1_000_000;
  const { fetch: fetchFn, calls } = fakeFetch([
    { body: { access_token: "tok-1", expires_in: 7200 } },
    { body: { access_token: "tok-2", expires_in: 7200 } },
  ]);
  const manager = new WeChatAccessTokenManager(officialAccountConfig, fetchFn, () => nowMs);
  assert.equal(await manager.getAccessToken(), "tok-1");
  // Jump forward past (7200 - 300)s, i.e. past the refresh skew window.
  nowMs += (7200 - 299) * 1000;
  assert.equal(await manager.getAccessToken(), "tok-2");
  assert.equal(calls.length, 2);
});

test("throws WeChatAccessTokenError on a non-zero errcode response", async () => {
  const { fetch: fetchFn } = fakeFetch([{ body: { errcode: 40013, errmsg: "invalid appid" } }]);
  const manager = new WeChatAccessTokenManager(officialAccountConfig, fetchFn);
  await assert.rejects(() => manager.getAccessToken(), (error: unknown) => {
    assert.ok(error instanceof WeChatAccessTokenError);
    assert.equal(error.errcode, 40013);
    return true;
  });
});

test("throws WeChatAccessTokenError on a non-OK HTTP status", async () => {
  const { fetch: fetchFn } = fakeFetch([{ status: 500, body: {} }]);
  const manager = new WeChatAccessTokenManager(officialAccountConfig, fetchFn);
  await assert.rejects(() => manager.getAccessToken(), WeChatAccessTokenError);
});

test("invalidate() forces the next getAccessToken() call to refetch", async () => {
  const { fetch: fetchFn, calls } = fakeFetch([
    { body: { access_token: "tok-1", expires_in: 7200 } },
    { body: { access_token: "tok-2", expires_in: 7200 } },
  ]);
  const manager = new WeChatAccessTokenManager(officialAccountConfig, fetchFn);
  assert.equal(await manager.getAccessToken(), "tok-1");
  manager.invalidate();
  assert.equal(await manager.getAccessToken(), "tok-2");
  assert.equal(calls.length, 2);
});

test("concurrent getAccessToken() calls during a fetch share one in-flight request", async () => {
  let resolveFetch!: () => void;
  const gate = new Promise<void>((resolve) => {
    resolveFetch = resolve;
  });
  let callCount = 0;
  const fetchFn = (async () => {
    callCount += 1;
    await gate;
    return {
      ok: true,
      status: 200,
      json: async () => ({ access_token: "tok-1", expires_in: 7200 }),
    } as Response;
  }) as typeof fetch;
  const manager = new WeChatAccessTokenManager(officialAccountConfig, fetchFn);
  const p1 = manager.getAccessToken();
  const p2 = manager.getAccessToken();
  resolveFetch();
  const [t1, t2] = await Promise.all([p1, p2]);
  assert.equal(t1, "tok-1");
  assert.equal(t2, "tok-1");
  assert.equal(callCount, 1);
});

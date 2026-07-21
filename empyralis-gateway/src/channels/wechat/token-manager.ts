/** access_token fetch/cache/refresh for official WeChat.
 *
 *  Both official-account variants use the same shape: a plain HTTPS GET
 *  with your credential pair, returning a short-lived bearer token
 *  (`expires_in` seconds, Tencent's real-world default is 7200s) that
 *  must be cached and reused — Tencent rate-limits token minting per
 *  credential per day, so fetching a fresh token per outbound send is
 *  not viable in production, only fine for a first correctness pass.
 *  This manager caches in memory and refreshes ahead of expiry.
 *
 *  Endpoints (both documented, stable, versioned Tencent APIs):
 *    - Official Account: GET https://api.weixin.qq.com/cgi-bin/token
 *        ?grant_type=client_credential&appid=APPID&secret=APPSECRET
 *    - WeCom:             GET https://qyapi.weixin.qq.com/cgi-bin/gettoken
 *        ?corpid=CORPID&corpsecret=CORPSECRET
 *  Both return `{"access_token": "...", "expires_in": 7200}` on success
 *  or `{"errcode": N, "errmsg": "..."}` (errcode != 0) on failure — this
 *  module treats any non-zero errcode, or a response missing
 *  access_token, as a fetch failure.
 */
import type { WeChatOfficialConfig } from "./types";

export type WeChatFetchFn = typeof fetch;

const OFFICIAL_ACCOUNT_TOKEN_URL = "https://api.weixin.qq.com/cgi-bin/token";
const WECOM_TOKEN_URL = "https://qyapi.weixin.qq.com/cgi-bin/gettoken";

/** Refresh this many seconds before Tencent's own expiry so a token
 *  in-flight-being-used never expires mid-request. */
const REFRESH_SKEW_SECONDS = 300;

interface CachedToken {
  accessToken: string;
  /** Epoch ms after which the cached token should no longer be handed out. */
  expiresAtMs: number;
}

interface WeChatTokenResponse {
  access_token?: string;
  expires_in?: number;
  errcode?: number;
  errmsg?: string;
}

export class WeChatAccessTokenError extends Error {
  constructor(message: string, public readonly errcode?: number) {
    super(message);
    this.name = "WeChatAccessTokenError";
  }
}

function buildTokenUrl(config: WeChatOfficialConfig): string {
  if (config.accountKind === "wecom") {
    const url = new URL(WECOM_TOKEN_URL);
    url.searchParams.set("corpid", config.appId);
    url.searchParams.set("corpsecret", config.appSecret);
    return url.toString();
  }
  const url = new URL(OFFICIAL_ACCOUNT_TOKEN_URL);
  url.searchParams.set("grant_type", "client_credential");
  url.searchParams.set("appid", config.appId);
  url.searchParams.set("secret", config.appSecret);
  return url.toString();
}

export class WeChatAccessTokenManager {
  private cached: CachedToken | null = null;
  private pendingFetch: Promise<string> | null = null;

  constructor(
    private readonly config: WeChatOfficialConfig,
    private readonly fetchFn: WeChatFetchFn = fetch,
    private readonly now: () => number = Date.now,
  ) {}

  /** Returns a currently-valid access_token, fetching or refreshing one
   *  if the cache is empty or within the refresh skew of expiry.
   *  Concurrent callers during a refresh share one in-flight fetch. */
  async getAccessToken(): Promise<string> {
    if (this.cached && this.cached.expiresAtMs > this.now()) {
      return this.cached.accessToken;
    }
    if (this.pendingFetch) {
      return this.pendingFetch;
    }
    this.pendingFetch = this.fetchAndCache();
    try {
      return await this.pendingFetch;
    } finally {
      this.pendingFetch = null;
    }
  }

  /** Drops the cached token, forcing the next getAccessToken() call to
   *  fetch a fresh one — call this after a send fails with an
   *  invalid/expired-token errcode (40001/42001/40014) so the manager
   *  doesn't keep handing out a token Tencent has already rejected. */
  invalidate(): void {
    this.cached = null;
  }

  private async fetchAndCache(): Promise<string> {
    const url = buildTokenUrl(this.config);
    const response = await this.fetchFn(url);
    if (!response.ok) {
      throw new WeChatAccessTokenError(`WeChat token endpoint returned HTTP ${response.status}.`);
    }
    const body = (await response.json()) as WeChatTokenResponse;
    if (body.errcode && body.errcode !== 0) {
      throw new WeChatAccessTokenError(
        `WeChat token fetch failed: ${body.errcode} ${body.errmsg || ""}`.trim(),
        body.errcode,
      );
    }
    const accessToken = String(body.access_token || "").trim();
    const expiresIn = Number(body.expires_in);
    if (!accessToken || !Number.isFinite(expiresIn) || expiresIn <= 0) {
      throw new WeChatAccessTokenError("WeChat token fetch returned no usable access_token.");
    }
    const ttlMs = Math.max(expiresIn - REFRESH_SKEW_SECONDS, 30) * 1000;
    this.cached = {
      accessToken,
      expiresAtMs: this.now() + ttlMs,
    };
    return accessToken;
  }
}

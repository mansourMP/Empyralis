/** Outbound send for official WeChat: the "customer service message" API
 *  (Official Account) / the app message-send API (WeCom). Both are plain
 *  access-token-authenticated POSTs; text is the only msgtype implemented
 *  here — see the module doc below for what's scoped out.
 *
 *  Endpoints:
 *    - Official Account: POST https://api.weixin.qq.com/cgi-bin/message/custom/send?access_token=TOKEN
 *        body: {"touser":"<openid>","msgtype":"text","text":{"content":"..."}}
 *      Only usable within Tencent's 48-hour customer-service reply window
 *      after the user's last inbound message (Tencent's constraint, not
 *      this code's) — sending outside that window returns errcode 45015.
 *    - WeCom:             POST https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token=TOKEN
 *        body: {"touser":"<userid>","msgtype":"text","agentid":<AgentId>,"text":{"content":"..."}}
 *      No 48h window restriction — WeCom is an internal/enterprise app
 *      channel, not a public-consumer one.
 *  Both return {"errcode":0,"errmsg":"ok"} on success, non-zero errcode
 *  on failure.
 *
 *  Not implemented (documented gap, matches message-mapper.ts's inbound
 *  gap): media replies (image/voice/video/file msgtype), and the
 *  template-message / subscribe-message APIs Official Account offers for
 *  sending outside the 48h window. Wiring media out needs the same
 *  media_id-with-shared-state-dir contract every other channel's outbound
 *  media already uses (see protocol/types.ts's GatewayChannelOutboundMediaItem
 *  doc comment) plus WeChat's own upload-then-reference-by-media_id flow —
 *  a reasonable next increment, not done here to keep this pass reviewable.
 */
import type { WeChatAccessTokenManager, WeChatFetchFn } from "./token-manager";
import type { WeChatAccountKind, WeChatOfficialConfig } from "./types";

const OFFICIAL_ACCOUNT_SEND_URL = "https://api.weixin.qq.com/cgi-bin/message/custom/send";
const WECOM_SEND_URL = "https://qyapi.weixin.qq.com/cgi-bin/message/send";

/** errcodes Tencent returns for an expired/invalid access_token — worth
 *  one invalidate-and-retry rather than surfacing as a hard failure,
 *  since the cached token can go stale slightly before our own skew
 *  window if Tencent revokes it early (e.g. a second process on the same
 *  credential minted a newer one, invalidating this one — WeChat access
 *  tokens are single-active-token-per-credential-pair by default). */
const TOKEN_INVALID_ERRCODES = new Set([40001, 40014, 41001, 42001]);

export interface WeChatSendResult {
  ok: boolean;
  errcode?: number;
  errmsg?: string;
  raw?: Record<string, unknown>;
}

function buildSendUrl(accountKind: WeChatAccountKind, accessToken: string): string {
  const url = new URL(accountKind === "wecom" ? WECOM_SEND_URL : OFFICIAL_ACCOUNT_SEND_URL);
  url.searchParams.set("access_token", accessToken);
  return url.toString();
}

function buildSendBody(config: WeChatOfficialConfig, remoteJid: string, text: string): Record<string, unknown> {
  if (config.accountKind === "wecom") {
    return {
      touser: remoteJid,
      msgtype: "text",
      agentid: config.agentId ? Number(config.agentId) : undefined,
      text: { content: text },
    };
  }
  return {
    touser: remoteJid,
    msgtype: "text",
    text: { content: text },
  };
}

async function postSend(
  url: string,
  body: Record<string, unknown>,
  fetchFn: WeChatFetchFn,
): Promise<{ errcode?: number; errmsg?: string; raw: Record<string, unknown> }> {
  const response = await fetchFn(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  const raw = (await response.json()) as Record<string, unknown>;
  return { errcode: raw.errcode as number | undefined, errmsg: raw.errmsg as string | undefined, raw };
}

/** Sends one plaintext message and returns a normalized result — never
 *  throws for a Tencent-side rejection (errcode != 0), only for a
 *  network/transport failure (fetch rejecting) or an inability to obtain
 *  an access_token at all (token-manager.ts's WeChatAccessTokenError
 *  propagates). On a token-invalid errcode, invalidates the cached token
 *  and retries once with a freshly fetched one before giving up. */
export async function sendWeChatTextMessage(
  config: WeChatOfficialConfig,
  tokenManager: WeChatAccessTokenManager,
  remoteJid: string,
  text: string,
  fetchFn: WeChatFetchFn = fetch,
): Promise<WeChatSendResult> {
  const trimmedRemoteJid = String(remoteJid || "").trim();
  const trimmedText = String(text || "").trim();
  if (!trimmedRemoteJid || !trimmedText) {
    return { ok: false, errmsg: "remote_jid and text are required." };
  }
  const body = buildSendBody(config, trimmedRemoteJid, trimmedText);

  const accessToken = await tokenManager.getAccessToken();
  let result = await postSend(buildSendUrl(config.accountKind, accessToken), body, fetchFn);

  if (result.errcode && TOKEN_INVALID_ERRCODES.has(result.errcode)) {
    tokenManager.invalidate();
    const refreshedToken = await tokenManager.getAccessToken();
    result = await postSend(buildSendUrl(config.accountKind, refreshedToken), body, fetchFn);
  }

  if (result.errcode && result.errcode !== 0) {
    return { ok: false, errcode: result.errcode, errmsg: result.errmsg, raw: result.raw };
  }
  return { ok: true, errcode: 0, raw: result.raw };
}

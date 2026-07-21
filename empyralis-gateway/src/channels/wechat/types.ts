/** Shared types for the official-WeChat channel module.
 *
 *  IMPORTANT — read this before wiring anything to a live network port.
 *  This module implements "official WeChat" (WeChat Official Account /
 *  公众号, or WeChat Work / WeCom / 企业微信), NOT the personal-account
 *  bridge modeled by ../local-bridge-runtime.ts's wechat_personal entry.
 *  Personal WeChat automation has no supported API and is explicitly out
 *  of scope — see docs/design/reliability-audit-2-channels.md and the
 *  founder direction that motivated this module.
 *
 *  Both official variants share one wire protocol: Tencent calls an
 *  HTTPS URL you register with them (GET for one-time server
 *  verification, POST for each inbound message), and you push replies
 *  out-of-band via an access-token-authenticated REST API. That inbound
 *  half requires a publicly reachable HTTPS endpoint. Every existing
 *  "bridge" in this package (bridges/bluebubbles-bridge.ts,
 *  bridges/signal-cli-bridge.ts, channels/local-bridge-runtime.ts) is
 *  deliberately restricted to localhost/private-network callers only
 *  (see local-bridge-runtime.ts's isPrivateBridgeHost) because the
 *  Gateway's only sanctioned network posture is an outbound WSS tunnel
 *  to the cloud with "no inbound holes" (docs/PLATFORM-MAP.md's Key
 *  Contract 7). server.ts in this directory intentionally does NOT
 *  inherit that private-only restriction, because Tencent's callers are
 *  never local — but that means putting it into production requires an
 *  explicit, separate decision to open an inbound port (or reverse-proxy
 *  one) on whatever host runs it. This module does not make that
 *  decision or provision that networking; it only implements the
 *  protocol correctly so that decision can be made deliberately. See
 *  this package's README/PR description for the full writeup.
 */

/** Which official WeChat product this config targets. Both use the same
 *  inbound XML callback shape and the same server-verification signature
 *  algorithm; they differ in token-fetch/send endpoints and in the id
 *  field name (appid+secret vs corpid+corpsecret). */
export type WeChatAccountKind = "official_account" | "wecom";

export interface WeChatOfficialConfig {
  accountKind: WeChatAccountKind;
  /** Official Account: AppID. WeCom: CorpID. */
  appId: string;
  /** Official Account: AppSecret. WeCom: the agent's Secret (corpsecret). */
  appSecret: string;
  /** The verify token configured in the WeChat/WeCom admin console — used
   *  to compute the server-verification and per-message signature. */
  token: string;
  /** WeCom only: the self-built app's AgentId, required on the send API
   *  and useful for routing multiple agents through one CorpID. Ignored
   *  for accountKind "official_account". */
  agentId?: string;
  /** Present only when the account uses WeChat's message-body encryption
   *  ("safe mode" / 安全模式). Not implemented in this pass — see
   *  crypto.ts's module doc for why and what's needed to add it. */
  encodingAesKey?: string;
}

export type WeChatInboundWireMsgType =
  | "text"
  | "image"
  | "voice"
  | "video"
  | "shortvideo"
  | "location"
  | "link"
  | "event";

/** The flat set of top-level tags WeChat/WeCom's inbound XML callback
 *  carries for the message types this module maps (text is the only type
 *  actually translated to a channel message today — see message-mapper.ts).
 *  All values are raw strings as received (CDATA-unwrapped). */
export interface WeChatInboundXmlFields {
  ToUserName?: string;
  FromUserName?: string;
  CreateTime?: string;
  MsgType?: string;
  Content?: string;
  MsgId?: string;
  AgentID?: string;
  Event?: string;
  [key: string]: string | undefined;
}

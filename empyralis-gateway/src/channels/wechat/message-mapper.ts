/** Maps a parsed WeChat/WeCom inbound XML callback into the same
 *  GatewayChannelInboundPayload shape every other channel emits (compare
 *  telegram/message-mapper.ts's mapTelegramInboundMessage and
 *  whatsapp/message-mapper.ts's mapWhatsAppInboundMessage — same
 *  contract, same null-for-"nothing to route" convention). */
import type { GatewayChannelInboundPayload } from "../../protocol/types";
import type { WeChatAccountKind, WeChatInboundXmlFields } from "./types";

export function wechatChannelKey(accountKind: WeChatAccountKind): string {
  return accountKind === "wecom" ? "wechat_work" : "wechat_official";
}

export function wechatProvider(accountKind: WeChatAccountKind): string {
  return accountKind === "wecom" ? "wecom_official_api" : "wechat_official_account_api";
}

function epochSecondsToIso(value: string | undefined): string {
  const seconds = Number.parseInt(String(value || "").trim(), 10);
  if (!Number.isFinite(seconds) || seconds <= 0) {
    return new Date().toISOString();
  }
  return new Date(seconds * 1000).toISOString();
}

/** Maps one parsed inbound XML message to the shared inbound-event shape.
 *  Returns null when there's nothing worth routing to an agent:
 *    - MsgType is anything other than "text" (image/voice/video/location/
 *      link require a separate authenticated media-download call this
 *      pass doesn't implement; "event" covers subscribe/unsubscribe/menu-
 *      click callbacks, which are account lifecycle signals, not chat
 *      messages).
 *    - Content is empty after trimming.
 *    - FromUserName (the sender's OpenID/UserID — this becomes remote_jid,
 *      the id every downstream reply is addressed to) is missing.
 *  Non-text media types are a documented, intentional gap — see this
 *  directory's module doc / the PR description for what's needed to add
 *  them (a media-download call per type before this mapper can attach a
 *  GatewayChannelInboundMediaItem). */
export function mapWeChatInboundMessage(
  fields: WeChatInboundXmlFields,
  accountKind: WeChatAccountKind,
): GatewayChannelInboundPayload | null {
  const msgType = String(fields.MsgType || "").trim().toLowerCase();
  if (msgType !== "text") {
    return null;
  }
  const remoteJid = String(fields.FromUserName || "").trim();
  const text = String(fields.Content || "").trim();
  if (!remoteJid || !text) {
    return null;
  }
  const externalMessageId = String(fields.MsgId || "").trim() || `${remoteJid}:${String(fields.CreateTime || "").trim()}`;
  return {
    channel_key: wechatChannelKey(accountKind),
    provider: wechatProvider(accountKind),
    message: {
      external_message_id: externalMessageId,
      remote_jid: remoteJid,
      sender_jid: remoteJid,
      text,
      received_at: epochSecondsToIso(fields.CreateTime),
      from_me: false,
    },
  };
}

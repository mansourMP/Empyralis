import type { GatewayChannelInboundPayload } from "../../protocol/types";
import { TELEGRAM_PERSONAL_CHANNEL_KEY, TELEGRAM_PERSONAL_PROVIDER } from "./session-store";

export interface TelegramInboundMessage {
  externalMessageId: string;
  remoteJid: string;
  senderJid?: string;
  pushName?: string;
  text: string;
  receivedAt?: string;
  fromMe?: boolean;
}

export type TelegramInboundEventPayload = GatewayChannelInboundPayload;

export function mapTelegramInboundMessage(rawMessage: TelegramInboundMessage): TelegramInboundEventPayload | null {
  const externalMessageId = String(rawMessage.externalMessageId || "").trim();
  const remoteJid = String(rawMessage.remoteJid || "").trim();
  const text = String(rawMessage.text || "").trim();
  if (!externalMessageId || !remoteJid || !text) {
    return null;
  }
  return {
    channel_key: TELEGRAM_PERSONAL_CHANNEL_KEY,
    provider: TELEGRAM_PERSONAL_PROVIDER,
    message: {
      external_message_id: externalMessageId,
      remote_jid: remoteJid,
      sender_jid: String(rawMessage.senderJid || "").trim() || undefined,
      push_name: String(rawMessage.pushName || "").trim() || undefined,
      text,
      received_at: String(rawMessage.receivedAt || "").trim() || new Date().toISOString(),
      from_me: Boolean(rawMessage.fromMe),
    },
  };
}

export function mapTelegramOutboundResult(
  outbound: {
    idempotencyKey: string;
    remoteJid: string;
    text: string;
    replyToExternalMessageId?: string;
  },
  response: Record<string, unknown> | undefined,
): Record<string, unknown> {
  return {
    channel_key: TELEGRAM_PERSONAL_CHANNEL_KEY,
    provider: TELEGRAM_PERSONAL_PROVIDER,
    idempotency_key: outbound.idempotencyKey,
    external_message_id: String(response?.externalMessageId ?? "").trim() || undefined,
    remote_jid: String(response?.remoteJid ?? outbound.remoteJid).trim() || outbound.remoteJid,
    text: outbound.text,
    reply_to_external_message_id: outbound.replyToExternalMessageId,
    delivered: true,
    delivered_at: new Date().toISOString(),
  };
}

import crypto from "crypto";

import type { GatewayChannelInboundPayload } from "../../protocol/types";
import { WHATSAPP_PERSONAL_CHANNEL_KEY, WHATSAPP_PERSONAL_PROVIDER } from "./session-store";

function pickMessageText(message: Record<string, unknown>): string {
  const conversation = String(message.conversation ?? "").trim();
  if (conversation) {
    return conversation;
  }
  const extendedText = message.extendedTextMessage as { text?: unknown } | undefined;
  const extendedTextValue = String(extendedText?.text ?? "").trim();
  if (extendedTextValue) {
    return extendedTextValue;
  }
  const imageMessage = message.imageMessage as { caption?: unknown } | undefined;
  const imageCaption = String(imageMessage?.caption ?? "").trim();
  if (imageCaption) {
    return imageCaption;
  }
  const videoMessage = message.videoMessage as { caption?: unknown } | undefined;
  return String(videoMessage?.caption ?? "").trim();
}

export type WhatsAppInboundEventPayload = GatewayChannelInboundPayload;

export function buildWhatsAppClientMessageId(idempotencyKey: string): string {
  const normalized = String(idempotencyKey || "").trim();
  const digest = crypto.createHash("sha256").update(normalized).digest("hex").toUpperCase();
  return `3EB0${digest.slice(0, 18)}`;
}

export function mapWhatsAppInboundMessage(rawMessage: Record<string, unknown>, opts?: { ownedJid?: string }): WhatsAppInboundEventPayload | null {
  const key = rawMessage.key as { id?: unknown; remoteJid?: unknown; participant?: unknown; fromMe?: unknown } | undefined;
  const message = rawMessage.message as Record<string, unknown> | undefined;
  const ownedJid = String(opts?.ownedJid ?? "").trim();
  const externalMessageId = String(key?.id ?? "").trim();
  const remoteJid = String(key?.remoteJid ?? "").trim();
  const senderJid = String(key?.participant ?? remoteJid).trim() || undefined;
  const text = pickMessageText(message ?? {});
  if (!externalMessageId || !remoteJid || !text) {
    return null;
  }
  // Group detection: Baileys group JIDs end with "@g.us"
  const isGroup = remoteJid.endsWith("@g.us");

  // Mention and reply detection from extendedTextMessage contextInfo
  let isMentioned = false;
  let quotedStanzaId: string | undefined;
  const mentionedJids: string[] = [];
  const contextInfo = (message as Record<string, unknown> | undefined)
    ?.extendedTextMessage as { contextInfo?: { mentionedJid?: string[]; participant?: string; stanzaId?: string } } | undefined;
  if (isGroup && ownedJid) {
    if (contextInfo?.contextInfo?.mentionedJid) {
      for (const jid of contextInfo.contextInfo.mentionedJid) {
        mentionedJids.push(String(jid));
        if (String(jid) === ownedJid) {
          isMentioned = true;
        }
      }
    }
    // Reply-to detection: quoted message participant matches ownedJid
    // Final verification against sentMessageIds happens in handleMessagesUpsert
    if (contextInfo?.contextInfo?.participant === ownedJid) {
      quotedStanzaId = contextInfo?.contextInfo?.stanzaId;
    }
  }

  return {
    channel_key: WHATSAPP_PERSONAL_CHANNEL_KEY,
    provider: WHATSAPP_PERSONAL_PROVIDER,
    message: {
      external_message_id: externalMessageId,
      remote_jid: remoteJid,
      sender_jid: senderJid,
      push_name: String(rawMessage.pushName ?? "").trim() || undefined,
      text,
      received_at: new Date(
        Number(rawMessage.messageTimestamp ?? Date.now() / 1000) * 1000,
      ).toISOString(),
      from_me: Boolean(key?.fromMe),
      is_self_chat: ownedJid ? remoteJid === ownedJid : false,
      is_group: isGroup,
      is_mentioned: isMentioned,
      quoted_stanza_id: quotedStanzaId,
      is_reply_to_sage: false,  // resolved in handleMessagesUpsert with sentMessageIds
    },
  };
}

export function mapWhatsAppOutboundResult(
  outbound: {
    idempotencyKey: string;
    remoteJid: string;
    text: string;
    clientMessageId?: string;
    replyToExternalMessageId?: string;
  },
  response: Record<string, unknown> | undefined,
): Record<string, unknown> {
  const key = response?.key as { id?: unknown; remoteJid?: unknown } | undefined;
  return {
    channel_key: WHATSAPP_PERSONAL_CHANNEL_KEY,
    provider: WHATSAPP_PERSONAL_PROVIDER,
    idempotency_key: outbound.idempotencyKey,
    external_message_id: String(key?.id ?? outbound.clientMessageId ?? "").trim() || undefined,
    remote_jid: String(key?.remoteJid ?? outbound.remoteJid).trim() || outbound.remoteJid,
    text: outbound.text,
    reply_to_external_message_id: outbound.replyToExternalMessageId,
    delivered: true,
    delivered_at: new Date().toISOString(),
  };
}

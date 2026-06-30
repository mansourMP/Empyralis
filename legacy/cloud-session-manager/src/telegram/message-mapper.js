const CHANNEL_KEY = "telegram_personal";
const PROVIDER = "telegram_gramjs";

/**
 * Convert a GramJS NewMessage event into a normalized inbound message.
 * Extracted from empyralis-gateway/src/channels/telegram/message-mapper.ts.
 */
export function mapTelegramInboundMessage(rawMessage) {
  const externalMessageId = String(rawMessage.externalMessageId || "").trim();
  const remoteJid = String(rawMessage.remoteJid || "").trim();
  const text = String(rawMessage.text || "").trim();
  if (!externalMessageId || !remoteJid || !text) {
    return null;
  }
  return {
    channel_key: CHANNEL_KEY,
    provider: PROVIDER,
    message: {
      external_message_id: externalMessageId,
      remote_jid: remoteJid,
      sender_jid: String(rawMessage.senderJid || "").trim() || undefined,
      push_name: String(rawMessage.pushName || "").trim() || undefined,
      text,
      received_at: String(rawMessage.receivedAt || "").trim() || new Date().toISOString(),
      from_me: Boolean(rawMessage.fromMe),
        is_self_chat: Boolean(rawMessage.isSelfChat),
        is_group: Boolean(rawMessage.isGroup),
        entities: rawMessage.entities || [],
        reply_to_msg_id: rawMessage.replyToMsgId || null,
    },
  };
}

/**
 * Map a send result to a normalized outbound delivery record.
 */
export function mapTelegramOutboundResult(outbound, response) {
  return {
    channel_key: CHANNEL_KEY,
    provider: PROVIDER,
    idempotency_key: outbound.idempotencyKey,
    external_message_id: String(response?.externalMessageId ?? "").trim() || undefined,
    remote_jid: String(response?.remoteJid ?? outbound.remoteJid).trim() || outbound.remoteJid,
    text: outbound.text,
    reply_to_external_message_id: outbound.replyToExternalMessageId,
    delivered: true,
    delivered_at: new Date().toISOString(),
  };
}

import crypto from "node:crypto";
import { CONFIG } from "../config.js";

/**
 * Sign a payload with HMAC-SHA256.
 * Returns the hex signature for inclusion in the X-Signature header.
 */
export function signPayload(payload) {
  const secret = CONFIG.backendHmacSecret;
  if (!secret) {
    throw new Error("BACKEND_HMAC_SECRET is not configured");
  }
  const data = JSON.stringify(payload);
  return crypto.createHmac("sha256", secret).update(data).digest("hex");
}

/**
 * Build a signed request body for forwarding to the backend.
 * Signs {sessionId, messageId, timestamp} as specified.
 *
 * isGroup/isMentioned/isReplyToSage are the backend's group/mention gate
 * signal (server_modules/personal_channels_service.py's cloud_channel_inbound,
 * "Group/mention gate (backend safety net)"). That gate has always existed
 * server-side but was a permanent no-op because this function never put the
 * three fields on the wire — the signed message body was exactly
 * {external_message_id, sender_id, sender_name, linked_username, text,
 * received_at}. inbound-handler.js already computes all three (it has its
 * OWN gate that drops an unaddressed group message before this function is
 * even called), so this just forwards what it already knows instead of
 * discarding it. Defaulting to false/false/false when omitted preserves the
 * existing behavior for any caller that hasn't been updated (a DM, the only
 * shape this wire format has ever actually sent, must never be affected).
 */
export function buildSignedInbound({ sessionId, messageId, channelKey, senderId, senderName, linkedUsername, workspaceId, text, timestamp, isGroup, isMentioned, isReplyToSage }) {
  const ts = timestamp || new Date().toISOString();
  const payload = { sessionId, messageId, timestamp: ts };
  const workspace_id = workspaceId || "default";
  const signature = signPayload(payload);

  return {
    session_id: sessionId,
    workspace_id,
    channel_key: channelKey || "telegram_personal",
    message: {
      external_message_id: messageId,
      sender_id: senderId || "",
      sender_name: senderName || "",
      linked_username: linkedUsername || "",
      text: text || "",
      received_at: ts,
      is_group: Boolean(isGroup),
      is_mentioned: Boolean(isMentioned),
      is_reply_to_sage: Boolean(isReplyToSage),
    },
    signature,
    signed_fields: "sessionId,messageId,timestamp",
    signed_payload: payload,
  };
}

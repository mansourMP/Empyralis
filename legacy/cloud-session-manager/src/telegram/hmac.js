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
 */
export function buildSignedInbound({ sessionId, messageId, channelKey, senderId, senderName, linkedUsername, workspaceId, text, timestamp }) {
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
    },
    signature,
    signed_fields: "sessionId,messageId,timestamp",
    signed_payload: payload,
  };
}

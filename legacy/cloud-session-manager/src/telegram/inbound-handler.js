import { CONFIG } from "../config.js";
import { buildSignedInbound } from "./hmac.js";

/**
 * InboundHandler subscribes to NewMessage events on a GramJS client
 * and forwards normalized messages to the Empyralis backend.
 */
export class InboundHandler {
  constructor({ sessionId, workspaceId, linkedUserId, linkedUsername, logger }) {
    this.sessionId = sessionId;
    this.workspaceId = workspaceId || "default";
    this.linkedUserId = linkedUserId;
    this.linkedUsername = linkedUsername || "";
    this.logger = logger;
    this.messageCount = 0;
    /** @type {Set<string>} Telegram message IDs sent by Sage (for reply-to detection) */
    this.sentMessageIds = new Set();
  }

  /**
   * Track a message ID that Sage sent (called by session-pool after sendMessage).
   * @param {string} messageId - Telegram message ID
   */
  addSentMessageId(messageId) {
    if (messageId) {
      this.sentMessageIds.add(String(messageId));
      // Keep set bounded (last 500 sent messages)
      if (this.sentMessageIds.size > 500) {
        const iterator = this.sentMessageIds.values();
        for (let i = 0; i < 100; i++) iterator.next();
        // Can't easily trim oldest from Set — just clear and start over if too large
        this.sentMessageIds.clear();
      }
    }
  }

  /**
   * Handle a normalized inbound message from GramJS.
   * The normalized format has { channel_key, provider, message: { text, from_me, ... } }.
   */
  async handleMessage(normalized) {
    try {
      // Unwrap from normalized format
      const msg = normalized?.message || normalized || {};
      const text = String(msg.text || "").trim();
      if (!text) return { skipped: true, reason: "empty_text" };

      // Handle /compact command — trigger compaction without a full Sage turn.
      // The backend routes_sage_telegram_hosted.py also handles this, but doing
      // it here avoids forwarding the message to Sage at all.
      if (text.toLowerCase().startsWith("/compact")) {
        // Forward to backend as a normal message — the backend's /compact handler
        // in routes_sage_telegram_hosted.py will process it.
        // We still forward so the backend can access the workspace thread/turns.
        this.logger?.info?.("forwarding /compact command to backend");
      }

      // Skip our own messages to avoid echo loops.
      // Exception: self-chat (Saved Messages) where sender === chat owner.
      // In self-chats, fromMe is always true but the user is intentionally
      // messaging themselves — not an echo-loop risk.
      const fromMe = Boolean(msg.from_me ?? normalized?.fromMe ?? false);
      const isSelfChat = Boolean(msg.is_self_chat ?? normalized?.message?.is_self_chat ?? false);
      if (fromMe && !isSelfChat) {
        this.logger?.info?.({ text: text.slice(0, 40) }, "skipping own message (fromMe)");
        return { skipped: true, reason: "from_me" };
      }

      // Skip messages from the hosted bot to avoid dual-path processing.
      // The hosted bot handles its own messages via Telegram polling;
      // the CSM should only process personal DMs / Saved Messages.
      const HOSTED_BOT_ID = "8870032163";
      const senderJid = String(msg.sender_jid || msg.senderJid || msg.remote_jid || msg.remoteJid || "").trim();
      if (senderJid === HOSTED_BOT_ID) {
        this.logger?.info?.("skipping message from hosted bot (dual-path prevention)");
        return { skipped: true, reason: "hosted_bot_message" };
      }

      // Group gate: skip group messages unless Sage is mentioned or replied to.
      // Prevents credit drain and Telegram ban risk from replying to every message.
      const isGroup = Boolean(msg.is_group ?? normalized?.message?.is_group ?? false);
      if (isGroup) {
        const entities = msg.entities || normalized?.message?.entities || [];

        // Check if linked account is mentioned (via @username or user ID)
        let isMentioned = false;
        const linkedUser = String(this.linkedUsername || "").toLowerCase().trim();
        const linkedId = String(this.linkedUserId || "").trim();
        if (linkedUser || linkedId) {
          for (const ent of entities) {
            // MessageEntityMention: @username mention
            if (ent?.className === "MessageEntityMention" || ent?._ === "messageEntityMention") {
              // The mention offset/length refers to the text; check if linkedUsername is in that span
              const offset = Number(ent.offset) || 0;
              const length = Number(ent.length) || 0;
              const mentioned = text.slice(offset, offset + length).toLowerCase().trim();
              if (mentioned === linkedUser || mentioned === "@" + linkedUser) {
                isMentioned = true;
                break;
              }
            }
            // MessageEntityMentionName: user ID mention
            if (ent?.className === "MessageEntityMentionName" || ent?._ === "messageEntityMentionName") {
              if (String(ent.userId || "") === linkedId) {
                isMentioned = true;
                break;
              }
            }
          }
        }

        // Check if this message is a reply to one of Sage's prior messages
        const replyToMsgId = String(msg.reply_to_msg_id ?? normalized?.message?.reply_to_msg_id ?? "").trim();
        const isReplyToSage = replyToMsgId && this.sentMessageIds.has(replyToMsgId);

        if (!isMentioned && !isReplyToSage) {
          this.logger?.info?.(
            { text: text.slice(0, 40), isGroup, isMentioned, isReplyToSage },
            "skipping group message (no mention / not reply to Sage)"
          );
          return { skipped: true, reason: "group_no_mention" };
        }
      }

      this.messageCount++;

      const messageId = String(msg.external_message_id || msg.externalMessageId || `${Date.now()}-${this.messageCount}`).trim();
      const senderId = String(msg.sender_jid || msg.senderJid || msg.remote_jid || msg.remoteJid || "").trim();
      const senderName = String(msg.push_name || msg.pushName || "").trim();
      const receivedAt = String(msg.received_at || msg.receivedAt || new Date().toISOString()).trim();

      const body = buildSignedInbound({
        sessionId: this.sessionId,
        workspaceId: this.workspaceId,
        messageId,
        channelKey: "telegram_personal",
        senderId,
        senderName,
        linkedUsername: this.linkedUsername,
        text,
        timestamp: receivedAt,
      });

      const backendUrl = `${CONFIG.backendUrl}/api/personal-channels/cloud/inbound`;
      this.logger?.info?.(
        { messageId, text: text.slice(0, 60) },
        "forwarding inbound message to backend"
      );

      const response = await fetch(backendUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Signature": body.signature,
          "X-Session-Id": this.sessionId,
        },
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(15000),
      });

      const responseBody = await response.json().catch(() => ({}));
      if (!response.ok) {
        this.logger?.warn?.(
          { status: response.status, body: responseBody },
          "backend rejected inbound message"
        );
        return { forwarded: false, status: response.status, error: responseBody };
      }

      this.logger?.info?.(
        { messageId, status: response.status, reply: String(responseBody?.reply_text || "").slice(0, 60) },
        "inbound message forwarded — reply received"
      );
      return { forwarded: true, status: response.status, body: responseBody };

    } catch (err) {
      this.logger?.error?.({ err: err?.message }, "failed to forward inbound message");
      return { forwarded: false, error: err?.message || "unknown_error" };
    }
  }
}

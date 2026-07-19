import crypto from "crypto";

import type { GatewayChannelInboundPayload, GatewayChannelOutboundPayload } from "../../protocol/types";
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

// ---------------------------------------------------------------------------
// Media contract -- shared shape every channel's inbound mapper publishes on
// message.media[] / accepts on outbound dispatch's media[] (see the WhatsApp
// media build brief). Kept local to this file (rather than protocol/types.ts)
// as a structural extension of GatewayChannelInboundPayload/
// GatewayChannelOutboundPayload -- both are assignable wherever the base
// type is expected, so nothing outside whatsapp/ needs to change.
// ---------------------------------------------------------------------------

export type WhatsAppMediaKind = "image" | "voice" | "audio" | "video" | "file";

export interface WhatsAppInboundMediaItem {
  kind: WhatsAppMediaKind;
  media_id: string;
  mime_type: string;
  filename?: string;
  size_bytes: number;
  duration_sec?: number;
}

export type WhatsAppInboundEventPayload = GatewayChannelInboundPayload & {
  message: GatewayChannelInboundPayload["message"] & { media?: WhatsAppInboundMediaItem[] };
};

/** Metadata-only description of a WhatsApp message's media attachment,
 *  detected synchronously from the raw Baileys message (no network I/O).
 *  runtime.ts uses this to decide whether/how to download, then supplies
 *  the resolved WhatsAppInboundMediaItem (media_id/size_bytes filled in
 *  post-download) to mapWhatsAppInboundMessage. */
export interface WhatsAppInboundMediaDescriptor {
  kind: WhatsAppMediaKind;
  mimeType: string;
  filename?: string;
  durationSec?: number;
  /** WhatsApp's own declared file size, when present -- lets the caller
   *  skip an obviously-oversized download before spending any bandwidth. */
  declaredSizeBytes?: number;
}

function toFiniteNonNegative(value: unknown): number | undefined {
  const num = Number(value);
  return Number.isFinite(num) && num >= 0 ? num : undefined;
}

/** Detects a media attachment on a raw WhatsApp message and describes it --
 *  mirrors OpenClaw's resolveInboundMediaMimetype() fallback table (some
 *  WhatsApp clients omit mimetype). Returns undefined for pure-text
 *  messages. Stickers map to "image": the contract's kind enum has no
 *  dedicated sticker value and a WhatsApp sticker is a static/animated webp
 *  image. Scoped to the same (unwrapped) message shape pickMessageText()
 *  above already handles -- ephemeral/view-once wrapper unwrapping is a
 *  pre-existing gap in text extraction too, not one this adds for media. */
export function detectWhatsAppInboundMedia(
  message: Record<string, unknown> | undefined,
): WhatsAppInboundMediaDescriptor | undefined {
  if (!message) {
    return undefined;
  }
  const image = message.imageMessage as Record<string, unknown> | undefined;
  if (image) {
    return {
      kind: "image",
      mimeType: String(image.mimetype || "").trim() || "image/jpeg",
      declaredSizeBytes: toFiniteNonNegative(image.fileLength),
    };
  }
  const video = message.videoMessage as Record<string, unknown> | undefined;
  if (video) {
    return {
      kind: "video",
      mimeType: String(video.mimetype || "").trim() || "video/mp4",
      durationSec: toFiniteNonNegative(video.seconds),
      declaredSizeBytes: toFiniteNonNegative(video.fileLength),
    };
  }
  const audio = message.audioMessage as Record<string, unknown> | undefined;
  if (audio) {
    const isVoiceNote = Boolean(audio.ptt);
    return {
      kind: isVoiceNote ? "voice" : "audio",
      mimeType: String(audio.mimetype || "").trim() || "audio/ogg; codecs=opus",
      durationSec: toFiniteNonNegative(audio.seconds),
      declaredSizeBytes: toFiniteNonNegative(audio.fileLength),
    };
  }
  const document = message.documentMessage as Record<string, unknown> | undefined;
  if (document) {
    return {
      kind: "file",
      mimeType: String(document.mimetype || "").trim() || "application/octet-stream",
      filename: String(document.fileName || "").trim() || undefined,
      declaredSizeBytes: toFiniteNonNegative(document.fileLength),
    };
  }
  const sticker = message.stickerMessage as Record<string, unknown> | undefined;
  if (sticker) {
    return {
      kind: "image",
      mimeType: String(sticker.mimetype || "").trim() || "image/webp",
      declaredSizeBytes: toFiniteNonNegative(sticker.fileLength),
    };
  }
  return undefined;
}

export function buildWhatsAppClientMessageId(idempotencyKey: string): string {
  const normalized = String(idempotencyKey || "").trim();
  const digest = crypto.createHash("sha256").update(normalized).digest("hex").toUpperCase();
  return `3EB0${digest.slice(0, 18)}`;
}

export function mapWhatsAppInboundMessage(
  rawMessage: Record<string, unknown>,
  opts?: { ownedJid?: string; media?: WhatsAppInboundMediaItem[] },
): WhatsAppInboundEventPayload | null {
  const key = rawMessage.key as { id?: unknown; remoteJid?: unknown; participant?: unknown; fromMe?: unknown } | undefined;
  const message = rawMessage.message as Record<string, unknown> | undefined;
  const ownedJid = String(opts?.ownedJid ?? "").trim();
  const externalMessageId = String(key?.id ?? "").trim();
  const remoteJid = String(key?.remoteJid ?? "").trim();
  const senderJid = String(key?.participant ?? remoteJid).trim() || undefined;
  const text = pickMessageText(message ?? {});
  const media = opts?.media && opts.media.length > 0 ? opts.media : undefined;
  // A media message with no caption (a bare voice note, a bare photo) has
  // empty text -- only messages with NEITHER text NOR a resolved attachment
  // are meaningless and dropped.
  if (!externalMessageId || !remoteJid || (!text && !media)) {
    return null;
  }
  // FIX (group-gate bypass): WhatsApp Status updates ("status@broadcast",
  // the one fixed JID for every status/story) and Channels/newsletters
  // (JIDs ending "@newsletter") are one-to-many broadcasts, not
  // conversations -- there is no real person on the other end of
  // remoteJid to reply to. Neither ends in "@g.us", so without this check
  // they fell through every group branch below with is_group=false and
  // is_self_chat=false and were processed exactly like an ungated 1:1 DM,
  // auto-replying into a status feed or a channel that has no "reply to
  // the bot" semantics at all. Hard-dropped here, before any
  // classification, so nothing downstream (the self-chat loop guard, the
  // group gate, the debouncer) ever sees them.
  if (remoteJid === "status@broadcast" || remoteJid.endsWith("@newsletter")) {
    return null;
  }
  // Group detection: Baileys group JIDs end with "@g.us". This also
  // correctly covers WhatsApp Community/announcement groups -- Baileys
  // represents a Community's own announcement group as an ordinary
  // "@g.us" JID (just with announce/isCommunity metadata attached), so it
  // is not a distinct suffix to special-case here; it keeps the same group
  // gate as any other group via this same check.
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
      media,
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

// ---------------------------------------------------------------------------
// Outbound media -- server -> gateway dispatch contract.
// ---------------------------------------------------------------------------

export const WHATSAPP_VOICE_MIMETYPE = "audio/ogg; codecs=opus";

export interface WhatsAppOutboundMediaItem {
  kind: WhatsAppMediaKind;
  source_path?: string;
  source_url?: string;
  mime_type?: string;
  caption?: string;
  as_voice?: boolean;
}

export type WhatsAppOutboundDispatchPayload = GatewayChannelOutboundPayload & {
  media?: unknown;
};

/** Type-based fallback mimetype when the server didn't supply one for an
 *  outbound item -- mirrors detectWhatsAppInboundMedia()'s inbound fallback
 *  table. */
export function defaultWhatsAppMimeTypeForKind(kind: WhatsAppMediaKind): string {
  switch (kind) {
    case "image":
      return "image/jpeg";
    case "video":
      return "video/mp4";
    case "voice":
      return WHATSAPP_VOICE_MIMETYPE;
    case "audio":
      return "audio/mpeg";
    case "file":
    default:
      return "application/octet-stream";
  }
}

const WHATSAPP_OUTBOUND_MEDIA_KINDS = new Set<WhatsAppMediaKind>(["image", "voice", "audio", "video", "file"]);

function normalizeWhatsAppOutboundMediaItem(raw: unknown): WhatsAppOutboundMediaItem | undefined {
  if (!raw || typeof raw !== "object") {
    return undefined;
  }
  const item = raw as Record<string, unknown>;
  const kind = String(item.kind ?? "").trim() as WhatsAppMediaKind;
  if (!WHATSAPP_OUTBOUND_MEDIA_KINDS.has(kind)) {
    return undefined;
  }
  const sourcePath = String(item.source_path ?? "").trim() || undefined;
  const sourceUrl = String(item.source_url ?? "").trim() || undefined;
  if (!sourcePath && !sourceUrl) {
    return undefined;
  }
  return {
    kind,
    source_path: sourcePath,
    source_url: sourceUrl,
    mime_type: String(item.mime_type ?? "").trim() || undefined,
    caption: String(item.caption ?? "").trim() || undefined,
    as_voice: item.as_voice === true,
  };
}

/** Validates/normalizes the server's outbound media[] array off the wire --
 *  unknown or malformed entries (bad kind, neither source_path nor
 *  source_url) are dropped rather than failing the whole dispatch, so one
 *  bad attachment can't sink an otherwise-good text+media turn. */
export function normalizeWhatsAppOutboundMediaList(raw: unknown): WhatsAppOutboundMediaItem[] {
  if (!Array.isArray(raw)) {
    return [];
  }
  const items: WhatsAppOutboundMediaItem[] = [];
  for (const entry of raw) {
    const normalized = normalizeWhatsAppOutboundMediaItem(entry);
    if (normalized) {
      items.push(normalized);
    }
  }
  return items;
}

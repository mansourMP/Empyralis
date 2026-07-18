import type {
  GatewayChannelInboundPayload,
  GatewayChannelMediaKind,
} from "../../protocol/types";
import { TELEGRAM_PERSONAL_CHANNEL_KEY, TELEGRAM_PERSONAL_PROVIDER } from "./session-store";

/** camelCase mirror of GatewayChannelInboundMediaItem — this is the shape
 *  the Telegram adapter (runtime.ts's downloadAndStoreTelegramMedia) builds
 *  after downloading+persisting the bytes; mapTelegramInboundMessage below
 *  translates it to the snake_case wire shape the server expects. */
export interface TelegramInboundMediaItem {
  kind: GatewayChannelMediaKind;
  mediaId: string;
  mimeType: string;
  filename?: string;
  sizeBytes: number;
  durationSec?: number;
}

export interface TelegramInboundMessage {
  externalMessageId: string;
  remoteJid: string;
  senderJid?: string;
  pushName?: string;
  text: string;
  receivedAt?: string;
  fromMe?: boolean;
  /** Zero or more media attachments already downloaded and saved to disk
   *  by the adapter before the message handler is invoked (see
   *  runtime.ts's NewMessage event handler). Empty/absent for text-only
   *  messages. */
  media?: TelegramInboundMediaItem[];
  /** Group/mention/reply metadata — mirrors WhatsApp's message-mapper
   *  contract (see whatsapp/message-mapper.ts's mapWhatsAppInboundMessage).
   *  isGroup/isMentioned are resolved synchronously in runtime.ts's GramJS
   *  event handler (event.isPrivate / rawMessage.mentioned, both reliable
   *  server-computed signals — no local entity parsing needed).
   *  replyToExternalMessageId is the raw replyTo.replyToMsgId off the wire;
   *  is_reply_to_sage itself is resolved AFTER mapping, in
   *  handleInboundMessage, against this runtime's own sentMessageIds (the
   *  mapper has no access to that set) — same two-step split WhatsApp uses
   *  for quoted_stanza_id -> is_reply_to_sage. */
  isGroup?: boolean;
  isMentioned?: boolean;
  replyToExternalMessageId?: string;
}

export type TelegramInboundEventPayload = GatewayChannelInboundPayload;

export function mapTelegramInboundMessage(rawMessage: TelegramInboundMessage): TelegramInboundEventPayload | null {
  const externalMessageId = String(rawMessage.externalMessageId || "").trim();
  const remoteJid = String(rawMessage.remoteJid || "").trim();
  const text = String(rawMessage.text || "").trim();
  const media = Array.isArray(rawMessage.media) ? rawMessage.media.filter((item): item is TelegramInboundMediaItem => Boolean(item)) : [];
  // A media message with no caption has empty text — only drop the message
  // if there's neither text NOR any attached media.
  if (!externalMessageId || !remoteJid || (!text && media.length === 0)) {
    return null;
  }
  const isGroup = Boolean(rawMessage.isGroup);
  const quotedStanzaId = isGroup ? String(rawMessage.replyToExternalMessageId || "").trim() || undefined : undefined;
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
      is_group: isGroup,
      is_mentioned: isGroup && Boolean(rawMessage.isMentioned),
      quoted_stanza_id: quotedStanzaId,
      is_reply_to_sage: false, // resolved in handleInboundMessage with sentMessageIds
      ...(media.length > 0
        ? {
            media: media.map((item) => ({
              kind: item.kind,
              media_id: item.mediaId,
              mime_type: item.mimeType,
              filename: item.filename,
              size_bytes: item.sizeBytes,
              duration_sec: item.durationSec,
            })),
          }
        : {}),
    },
  };
}

export function mapTelegramOutboundResult(
  outbound: {
    idempotencyKey: string;
    remoteJid: string;
    text: string;
    replyToExternalMessageId?: string;
    /** Per-item send results when the dispatch included media — omitted
     *  entirely (not an empty array) for text-only sends. */
    media?: Array<{ kind: GatewayChannelMediaKind; external_message_id?: string }>;
  },
  response: Record<string, unknown> | undefined,
): Record<string, unknown> {
  const result: Record<string, unknown> = {
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
  if (outbound.media && outbound.media.length > 0) {
    result.media = outbound.media;
  }
  return result;
}

const TELEGRAM_OUTBOUND_MEDIA_KINDS: ReadonlySet<string> = new Set(["image", "voice", "audio", "video", "file"]);

/** camelCase mirror of GatewayChannelOutboundMediaItem, consumed by
 *  TelegramAdapterClient.sendMedia (runtime.ts). */
export interface TelegramOutboundMediaItem {
  kind: GatewayChannelMediaKind;
  sourcePath?: string;
  sourceUrl?: string;
  mimeType?: string;
  caption?: string;
  asVoice?: boolean;
}

/** Loosely-typed wire shape accepted by mapTelegramOutboundMediaItem — every
 *  field is `unknown` because this is untrusted input from the outbound
 *  payload's `media` array and gets defensively coerced below. Deliberately
 *  NOT `Record<string, unknown>`: a plain `interface` (like
 *  GatewayChannelOutboundMediaItem, the type this is actually called with)
 *  has no index signature, so TS won't structurally accept it where an
 *  index-signature type is expected even though every property lines up. */
interface TelegramOutboundMediaItemInput {
  kind?: unknown;
  source_path?: unknown;
  source_url?: unknown;
  mime_type?: unknown;
  caption?: unknown;
  as_voice?: unknown;
}

/** Validates+normalizes one entry of the outbound `media` array (as received
 *  over the wire, snake_case) into the shape the Telegram adapter's
 *  sendMedia() consumes. Throws on a malformed item — the caller
 *  (runtime.ts's sendFinalOutbound) lets that reject the whole
 *  channel.outbound call rather than silently dropping an attachment the
 *  server explicitly asked to send. */
export function mapTelegramOutboundMediaItem(raw: TelegramOutboundMediaItemInput): TelegramOutboundMediaItem {
  const kind = String(raw?.kind || "").trim().toLowerCase();
  if (!TELEGRAM_OUTBOUND_MEDIA_KINDS.has(kind)) {
    throw new Error(`Unsupported Telegram outbound media kind: ${String(raw?.kind ?? "")}`);
  }
  const sourcePath = String(raw?.source_path || "").trim() || undefined;
  const sourceUrl = String(raw?.source_url || "").trim() || undefined;
  if (!sourcePath && !sourceUrl) {
    throw new Error("Telegram outbound media item requires source_path or source_url.");
  }
  return {
    kind: kind as GatewayChannelMediaKind,
    sourcePath,
    sourceUrl,
    mimeType: String(raw?.mime_type || "").trim() || undefined,
    caption: String(raw?.caption || "").trim() || undefined,
    asVoice: Boolean(raw?.as_voice),
  };
}

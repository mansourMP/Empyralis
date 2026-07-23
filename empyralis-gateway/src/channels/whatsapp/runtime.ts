import { promises as fs } from "fs";
import { execFile } from "child_process";
import crypto from "crypto";
import os from "os";
import path from "path";
import { promisify } from "util";
import pino from "pino";

import { GatewayStateDb } from "../../state/db";
import { redactCredentials } from "../foundation/credential-redactor";
import { DraftManager, normalizeChannelOutboundOperation } from "../foundation/draft-manager";
import { chunkMessage, WHATSAPP_MESSAGE_LIMIT } from "../foundation/message-chunker";
import { renderMarkdownToWhatsApp } from "../foundation/channel-markdown";
import { DEFAULT_INBOUND_DEBOUNCE_WINDOW_MS, InboundDebouncer } from "../foundation/inbound-debounce";
import type {
  GatewayChannelInboundPayload,
  GatewayChannelOutboundPayload,
  GatewayRequestEnvelope,
  GatewayScope,
  GatewayToolInvokePayload,
} from "../../protocol/types";
import {
  buildWhatsAppPairingCodeState,
  buildWhatsAppPreflightState,
  loadWhatsAppLoginConfig,
  type WhatsAppLoginConfig,
} from "./login";
import { buildWhatsAppQrPayload } from "./qr-login";
import {
  buildWhatsAppClientMessageId,
  defaultWhatsAppMimeTypeForKind,
  detectWhatsAppInboundMedia,
  mapWhatsAppInboundMessage,
  mapWhatsAppOutboundResult,
  normalizeWhatsAppOutboundMediaList,
  WHATSAPP_VOICE_MIMETYPE,
  type WhatsAppInboundMediaDescriptor,
  type WhatsAppInboundMediaItem,
  type WhatsAppOutboundDispatchPayload,
  type WhatsAppOutboundMediaItem,
} from "./message-mapper";
import {
  WHATSAPP_TYPING_KEEPALIVE_MS,
  WhatsAppOutboundStore,
  WhatsAppTypingKeepalive,
  type WhatsAppPresenceAction,
} from "./outbound";
import {
  DEFAULT_WHATSAPP_RECONNECT_POLICY,
  computeWhatsAppReconnectDelay,
  resolveWhatsAppReconnectState,
} from "./reconnect";
import {
  WhatsAppSessionSnapshot,
  WhatsAppSessionStore,
  WHATSAPP_PERSONAL_CHANNEL_KEY,
  WHATSAPP_PERSONAL_PROVIDER,
} from "./session-store";
import {
  DEFAULT_CREDS_CODEC,
  hasPendingAuthWrite,
  waitForAuthWriteIdle,
  WhatsAppAuthPersistence,
  type WhatsAppCredsCodec,
} from "./auth-persistence";
import { PersonalChannelConfigStore } from "../personal-config-store";
import type {
  PersonalChannelCapabilityManifest,
  PersonalChannelHealthSnapshot,
} from "../personal-runtime";

const execFileAsync = promisify(execFile);

type DynamicImport = <T>(specifier: string) => Promise<T>;
const dynamicImport = new Function("specifier", "return import(specifier)") as DynamicImport;

interface BaileysAuthBundle {
  state: {
    creds?: {
      registered?: boolean;
      // Baileys creds carry a lot more than `registered` (noise key,
      // signed identity key, account signature, etc) -- our own atomic
      // writer needs the whole object, not just the field the pairing-code
      // check below cares about.
      [key: string]: unknown;
    };
  };
  saveCreds: () => Promise<void> | void;
}

interface BaileysSocketLike {
  ev: {
    on: (eventName: string, handler: (payload: any) => void | Promise<void>) => void;
  };
  sendMessage: (
    jid: string,
    content: Record<string, unknown>,
    options?: { messageId?: string },
  ) => Promise<Record<string, unknown> | undefined>;
  // Widened beyond WhatsAppPresenceAction ("composing"|"paused", the typing
  // indicator's own vocabulary) to also accept "available" -- the value
  // runHealthCheck's active probe sends (see its doc comment). Real Baileys
  // sockets accept the same "available"/"unavailable"/"composing"/
  // "recording"/"paused" set either way (see
  // @whiskeysockets/baileys/lib/Socket/chats.js's sendPresenceUpdate); jid
  // is optional for "available"/"unavailable" (Baileys ignores it for
  // those, sending a global presence stanza instead of a per-chat one).
  sendPresenceUpdate?: (type: WhatsAppPresenceAction | "available" | "unavailable", jid?: string) => Promise<void> | void;
  requestPairingCode?: (phoneNumber: string, customPairingCode?: string) => Promise<string>;
  user?: { id?: string; name?: string };
  /** Tells WhatsApp's servers to unlink this device (a real logout, not
   *  just closing our local socket) — used by disconnect/reset so a wiped
   *  local auth state doesn't leave a stale "linked device" entry on the
   *  account's own WhatsApp settings. */
  logout?: () => Promise<void>;
  end?: (error: Error | undefined) => void;
  /** Re-requests a fresh download URL for expired media (Baileys throws a
   *  410/404-style error when a media message's original URL has expired)
   *  -- passed as downloadMediaMessage()'s ctx.reuploadRequest so a media
   *  message downloaded a while after receipt can still be fetched. */
  updateMediaMessage?: (msg: Record<string, unknown>) => Promise<Record<string, unknown>>;
  /** Fetches a group's metadata (subject/participants/etc) directly from
   *  WhatsApp -- used ONLY for a best-effort chat_title lookup on inbound
   *  group messages that already passed the mention/reply gate (see
   *  handleMessagesUpsert). Optional: older/mocked adapters that don't
   *  implement it simply never populate chat_title. */
  groupMetadata?: (jid: string) => Promise<{ subject?: string } | undefined>;
}

interface WhatsAppBaileysAdapter {
  loadAuthState: (folder: string) => Promise<BaileysAuthBundle>;
  createSocket: (config: Record<string, unknown>) => BaileysSocketLike;
  disconnectReason: { loggedOut?: number; restartRequired?: number; connectionReplaced?: number };
  browserDescriptor: (appName: string) => unknown;
  /** Fetches the currently-live WhatsApp Web client version directly from
   *  web.whatsapp.com (not Baileys' own bundled default, which goes stale
   *  between package releases and is a documented cause of clean
   *  connection rejections — see WhiskeySockets/Baileys#2679). Returns
   *  undefined on fetch failure so the caller can fall back to Baileys'
   *  own default rather than fail the whole connection over this. */
  fetchWaWebVersion: () => Promise<[number, number, number] | undefined>;
  /** Baileys' own Buffer<->base64 (de)serializer for creds.json, sourced
   *  from the SAME loaded module so our atomic writer's output is
   *  byte-for-byte what Baileys' own reader expects. */
  credsCodec: WhatsAppCredsCodec;
  /** Downloads a WhatsApp media message's bytes via Baileys'
   *  downloadMediaMessage. Baileys itself enforces no size limit -- the
   *  caller (downloadInboundMedia) applies WHATSAPP_MEDIA_MAX_BYTES both
   *  before (declared size) and after (actual buffer length) the call. */
  downloadMedia: (
    rawMessage: Record<string, unknown>,
    ctx: { logger: unknown; reuploadRequest?: (msg: Record<string, unknown>) => Promise<unknown> },
  ) => Promise<Buffer>;
}

export interface WhatsAppGatewayPublisher {
  publishEvent: (type: "channel.inbound", payload: GatewayChannelInboundPayload) => Promise<void>;
  publishStateUpdate: (payload: Record<string, unknown>) => Promise<void>;
}

export interface WhatsAppRuntimeDependencies {
  publisher?: WhatsAppGatewayPublisher;
  adapter?: WhatsAppBaileysAdapter;
}

// qrCode/pairingCode are deliberately NOT redacted: toGatewayStatePayload()'s
// output (below) is exactly what the owner's authenticated status endpoint
// serves so their browser can render the QR/pairing code to scan — that's a
// single-use pairing intent, not durable session material, and redacting it
// here would make WhatsApp pairing permanently impossible. The real Baileys
// auth material (creds/keys/signal identities) lives in a separate directory
// (see authStateDir()) and is never part of this snapshot's fields at all —
// sessionString/sessionToken/the object keys below are kept as defense in
// depth in case a future field is ever added under one of these names.
const WHATSAPP_REDACT_STRING_KEYS = ["sessionString", "sessionToken"] as const;
const WHATSAPP_REDACT_OBJECT_KEYS = ["creds", "keys", "authState", "signalIdentities", "preKeys", "signedPreKey"] as const;

export function redactWhatsAppCredentials(state: Record<string, unknown>): Record<string, unknown> {
  return redactCredentials(state, WHATSAPP_REDACT_STRING_KEYS, WHATSAPP_REDACT_OBJECT_KEYS);
}

// TTL for the typing session started the instant an inbound message is
// admitted (see WhatsAppPersonalRuntime.startTypingForChat), which needs to
// stay alive for the length of a full agent turn — not just the one
// sendMessage call WHATSAPP_TYPING_MAX_TTL_MS (60s, imported from
// ./outbound) is sized for. Sized to safely exceed this platform's own
// worst-case turn budget: server_modules/runtime_config.py's
// ORION_RUN_TIMEOUT_SECONDS defaults to 300s, and
// ORION_WHATSAPP_AUTOPILOT_RUN_TIMEOUT_SECONDS defaults to 180s.
const WHATSAPP_INBOUND_TYPING_MAX_TTL_MS = 5 * 60_000;

/**
 * How often a LIVE socket is actively re-probed (a real sendPresenceUpdate
 * round trip over the WebSocket) to catch a silently-dead connection --
 * mirrors TelegramPersonalRuntime's identically-purposed
 * TELEGRAM_HEALTH_CHECK_INTERVAL_MS (same 3-minute cadence) and closes the
 * same class of gap: connection.update's "close" event is Baileys' OWN
 * best-effort signal, but a half-dead network path (no close frame ever
 * received by the client) can leave `this.socket` pointing at a WebSocket
 * that's already unusable without Baileys ever telling us. Without an
 * active probe, the persisted session status stays "connected" -- and every
 * UI reading it keeps showing a false green checkmark -- until the next
 * real inbound/outbound traffic finally surfaces the failure, which for an
 * otherwise-idle DM channel could be a long time.
 */
const WHATSAPP_HEALTH_CHECK_INTERVAL_MS = 3 * 60 * 1000;

// Inbound/outbound media size ceiling -- generous enough for WhatsApp's own
// client-side media limits (images/voice/most documents) while bounding
// memory use per attachment; matches the media contract's "size-capped
// (~25MB)" requirement in both directions.
const WHATSAPP_MEDIA_MAX_BYTES = 25 * 1024 * 1024;
// Wall-clock ceiling on a single ffmpeg transcode (voice-note Opus/OGG
// conversion) so a pathological input can't hang the gateway process.
const WHATSAPP_FFMPEG_TIMEOUT_MS = 60_000;
// Subdirectory (under the gateway state dir root) inbound media bytes are
// written to -- also the leading path segment of every inbound media_id.
// Sibling of the existing whatsapp/auth dir (see session-store.ts), keeping
// all WhatsApp on-disk state namespaced under whatsapp/. The Telegram
// channel uses a top-level telegram-media/ instead; the relative-to-root
// media_id contract (below) is identical either way.
const WHATSAPP_MEDIA_SUBDIR = "whatsapp/media";

// ── Self-chat loop-guard tuning (see WhatsAppPersonalRuntime's
// pendingSelfChatSends/selfChatTurnTimestamps fields and
// isOwnSelfChatEcho/admitSelfChatTurnOrTrip for the mechanisms these tune).
// Mirrors telegram/runtime.ts's identically-named/valued constants exactly
// — same class of bug (an owner "Saved Messages" self-chat channel whose
// own replies can echo back in as new inbound events), same fix shape.

/** How long a "we just started sending this text into self-chat" bookkeeping
 *  entry stays eligible to match an inbound echo. Generous relative to a
 *  single sendMessage RPC round trip (normally well under a second) so the
 *  race window it exists for is comfortably covered; short enough that a
 *  genuinely new owner message which happens to repeat earlier text is only
 *  at risk of a false match for a brief window, not indefinitely. Exported
 *  for direct reference from tests (see whatsapp-self-chat.test.ts) instead
 *  of duplicating the value as a magic number that could silently drift. */
export const SELF_CHAT_PENDING_SEND_TTL_MS = 20_000;

/** Rolling window for the self-chat circuit breaker. Exported for tests —
 *  see SELF_CHAT_PENDING_SEND_TTL_MS's doc for why. */
export const SELF_CHAT_BREAKER_WINDOW_MS = 60_000;

/** Max self-chat turns admitted (i.e. that passed isOwnSelfChatEcho) within
 *  SELF_CHAT_BREAKER_WINDOW_MS before the breaker refuses further ones. Well
 *  above any plausible human-paced back-and-forth (each turn already
 *  absorbs a burst of rapid messages via the inbound debouncer) — a real
 *  runaway loop with no human pacing it would blow past this within
 *  seconds, tripping fast; a legitimate conversation should never get
 *  close. Exported for tests — see SELF_CHAT_PENDING_SEND_TTL_MS's doc for
 *  why. */
export const SELF_CHAT_BREAKER_MAX_TURNS = 6;

/** Pulls the Baileys-assigned `key.id` off a sendMessage() response —
 *  shared by sendWhatsAppTextChunks and sendOutboundMediaItems so every
 *  dispatched chunk/media item reports its id back through the same shape,
 *  regardless of which helper sent it. */
function extractWhatsAppSentMessageId(response: Record<string, unknown> | undefined): string | undefined {
  const key = response?.key as { id?: unknown } | undefined;
  return String(key?.id ?? "").trim() || undefined;
}

export class WhatsAppPersonalRuntime {
  private readonly configStore: PersonalChannelConfigStore;
  private readonly sessionStore: WhatsAppSessionStore;
  private readonly outboundStore: WhatsAppOutboundStore;
  private readonly logger = pino({ level: "silent" });
  private publisher?: WhatsAppGatewayPublisher;
  private adapter?: WhatsAppBaileysAdapter;
  private socket: BaileysSocketLike | null = null;
  private authBundle: BaileysAuthBundle | null = null;
  /** Owns atomic/backed-up persistence of creds.json for the CURRENT
   *  connect cycle -- (re)created at the top of connectSocketInternal()
   *  alongside authBundle/socket, so the staleness guard on this.socket
   *  already protects it from a late event off an abandoned socket. */
  private authPersistence: WhatsAppAuthPersistence | null = null;
  private started = false;
  private reconnectTimer: NodeJS.Timeout | null = null;
  /** Drives runHealthCheck's active re-probe of a live socket -- see
   *  WHATSAPP_HEALTH_CHECK_INTERVAL_MS's doc comment. Mirrors
   *  TelegramPersonalRuntime's identically-purposed healthCheckTimer field
   *  exactly (armed on a fresh "open", cleared on close/stop/disconnect). */
  private healthCheckTimer: NodeJS.Timeout | null = null;
  private pairingCodeRequested = false;
  private connectPromise: Promise<void> | null = null;
  private reconnectAttempts = 0;
  /**
   * External message IDs this runtime has sent — EVERY chunk of a
   * multi-part text reply and every media item, not just the "primary" one
   * returned to the caller (see sendFinalOutbound). Used to resolve
   * is_reply_to_sage (someone replying to a message Sage sent counts as
   * "addressed" in a group, same as an explicit @mention) AND, since
   * is_self_chat is deliberately let through the from_me gate below (see
   * handleMessagesUpsert), reused as the primary self-chat loop-guard
   * signal: an inbound self-chat event whose external_message_id is
   * already in this set is unambiguously our own reply resurfacing, not a
   * new owner command (see isOwnSelfChatEcho). Mirrors telegram/runtime.ts's
   * identically-named/purposed field exactly. Capped so a long-lived
   * connection can't grow this unboundedly.
   */
  private sentMessageIds = new Set<string>();
  /**
   * LOOP-GUARD state (see isOwnSelfChatEcho / admitSelfChatTurnOrTrip and
   * handleMessagesUpsert's block comment). Two layers, both scoped to
   * self-chat sends only — mirrors telegram/runtime.ts's identically-named
   * fields exactly:
   *  1. pendingSelfChatSends — the plain (pre-markdown-render) text of a
   *     self-chat reply chunk, recorded the instant its send begins (see
   *     beginSelfChatEchoGuard, called from sendWhatsAppTextChunks via
   *     sendFinalOutbound's hooks). Covers a race where Baileys' own-send
   *     echo (messages.upsert fired via emitOwnEvents — see
   *     @whiskeysockets/baileys/lib/Socket/messages-send.js's
   *     `if (config.emitOwnEvents) { process.nextTick(...upsertMessage...) }`)
   *     reaches handleMessagesUpsert before sendMessage()'s returned
   *     key.id lands in sentMessageIds above.
   *  2. selfChatTurnTimestamps — a rolling count of self-chat turns
   *     actually admitted (i.e. that got past layer 1), capped at
   *     SELF_CHAT_BREAKER_MAX_TURNS per SELF_CHAT_BREAKER_WINDOW_MS. Pure
   *     backstop: bounds the worst case even if layer 1 somehow misses a
   *     real echo, instead of trusting it blindly.
   */
  private readonly pendingSelfChatSends: Array<{ remoteJid: string; text: string; startedAt: number }> = [];
  private selfChatTurnTimestamps: number[] = [];
  private readonly draftManager = new DraftManager();
  /**
   * Coalesces a rapid burst of inbound messages on one chat into a single
   * published channel.inbound event (one agent turn → one reply) instead of
   * one turn per message. Sits AFTER the group gate in handleMessagesUpsert,
   * so only admitted messages are buffered; typing (below) is untouched and
   * still fires the instant a message is admitted.
   */
  private readonly inboundDebouncer: InboundDebouncer;
  /**
   * Typing sessions started on inbound receipt (see startTypingForChat),
   * keyed by remoteJid, waiting to be claimed and stopped by whichever
   * sendFinalOutbound() call eventually dispatches that chat's reply.
   */
  private readonly activeTyping = new Map<
    string,
    { typing: WhatsAppTypingKeepalive; startedAt: number; socket: BaileysSocketLike }
  >();

  constructor(
    private readonly db: GatewayStateDb,
    dependencies: WhatsAppRuntimeDependencies = {},
  ) {
    this.configStore = new PersonalChannelConfigStore(db);
    this.sessionStore = new WhatsAppSessionStore(db);
    this.outboundStore = new WhatsAppOutboundStore(db);
    this.publisher = dependencies.publisher;
    this.adapter = dependencies.adapter;
    this.inboundDebouncer = new InboundDebouncer({
      windowMs: DEFAULT_INBOUND_DEBOUNCE_WINDOW_MS,
      publish: (payload) => this.publishInbound(payload),
    });
  }

  requestedCapabilities(): string[] {
    return [
      "channel.whatsapp.personal",
      "channel.whatsapp.personal.inbound",
      "channel.whatsapp.personal.outbound",
      "channel.whatsapp.personal.configure",
      "channel.whatsapp.personal.disconnect",
    ];
  }

  supportsCapability(capabilityId: string): boolean {
    const id = String(capabilityId || "").trim();
    return id === "channel.whatsapp.personal.configure" || id === "channel.whatsapp.personal.disconnect";
  }

  async handleCapabilityInvoke(
    frame: GatewayRequestEnvelope<GatewayToolInvokePayload>,
  ): Promise<Record<string, unknown>> {
    const payload = frame.payload;
    const capabilityId = String(payload.capability_id || "").trim();
    if (capabilityId === "channel.whatsapp.personal.disconnect") {
      return this.handleDisconnect();
    }
    if (capabilityId !== "channel.whatsapp.personal.configure") {
      throw new Error(`Unsupported WhatsApp personal capability: ${capabilityId || "unknown"}`);
    }
    const argumentsPayload =
      payload.arguments && typeof payload.arguments === "object" && !Array.isArray(payload.arguments)
        ? (payload.arguments as Record<string, unknown>)
        : {};
    return this.handleConfigure(argumentsPayload);
  }

  /** Full reset — see TelegramPersonalRuntime.handleDisconnect()'s doc
   *  comment for why this exists. Also clears the Baileys multi-file auth
   *  state directory (the real credential material, never part of the
   *  JSON snapshot) so a subsequent QR/pairing-code attempt is genuinely
   *  a fresh device link, not a resume of a broken one. */
  private async handleDisconnect(): Promise<Record<string, unknown>> {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.stopHealthCheck();
    this.reconnectAttempts = 0;
    this.pairingCodeRequested = false;
    if (this.socket) {
      try {
        await Promise.resolve(this.socket.logout?.());
      } catch {
        // Best-effort — the auth state directory is being wiped regardless.
      }
      try {
        this.socket.end?.(undefined);
      } catch {
        // Already gone.
      }
    }
    this.socket = null;
    this.authBundle = null;
    // Let any in-flight creds write finish before recursively deleting the
    // directory it's writing into -- avoids a write's rename racing the
    // delete. Harmless either way (we're wiping everything regardless) but
    // keeps the sequence clean rather than relying on the write's own
    // best-effort error handling to absorb an ENOENT from a deleted parent.
    await waitForAuthWriteIdle(this.sessionStore.authStateDir());
    this.authPersistence = null;
    await this.sessionStore.clearAuthStateDir();
    await this.configStore.clearWhatsAppConfig();
    await this.sessionStore.save({
      status: "idle",
      qrCode: undefined,
      loginHint: undefined,
      pairingCode: undefined,
      pairingCodeGeneratedAt: undefined,
      linkedJid: undefined,
      linkedName: undefined,
      connectedAt: undefined,
      retryable: true,
      lastDisconnectReason: undefined,
      lastDisconnectCode: undefined,
    });
    await this.flushState();
    return { status: "disconnected", channel_key: WHATSAPP_PERSONAL_CHANNEL_KEY };
  }

  supportsChannel(channelKey: string): boolean {
    return String(channelKey || "").trim() === WHATSAPP_PERSONAL_CHANNEL_KEY;
  }

  getManifest(): PersonalChannelCapabilityManifest {
    return {
      channelKey: WHATSAPP_PERSONAL_CHANNEL_KEY,
      label: "WhatsApp Personal",
      provider: WHATSAPP_PERSONAL_PROVIDER,
      runtimeLane: "personal_gateway",
      stage: "live",
      status: "live",
      liveCapable: true,
      requiresAgentComputer: true,
      sessionOwner: "paired_gateway",
      setupKind: "qr_pairing",
      capabilities: ["configure", "inbound", "outbound", "text", "groups"],
      chatTypes: ["dm", "group"],
      media: { text: true, images: true, files: true, reactions: false, voice: true },
      safety: {
        ownerPairingRequired: true,
        allowlistRequired: false,
        studioBusinessAllowed: false,
        customerPublicSendAllowed: false,
      },
      notes: ["Owner/private channel for Sage through Agent Computer. Use WhatsApp Business for Studio."],
    };
  }

  async getHealthSnapshot(): Promise<PersonalChannelHealthSnapshot> {
    const snapshot = await this.sessionStore.load();
    // A creds.json write landing right now means the file on disk doesn't
    // necessarily reflect `snapshot.status` yet -- report the transitional
    // "unstable" state rather than confidently asserting connected/
    // disconnected. Checked via the standalone path-keyed lookup (not
    // this.authPersistence) so this is correct even before any connect
    // attempt has ever constructed an instance, and immune to that field
    // being nulled out mid-teardown elsewhere.
    const authWriteInFlight = hasPendingAuthWrite(this.sessionStore.authStateDir());
    const status: PersonalChannelHealthSnapshot["status"] = authWriteInFlight
      ? "unstable"
      : snapshot.status;
    return {
      channelKey: WHATSAPP_PERSONAL_CHANNEL_KEY,
      provider: WHATSAPP_PERSONAL_PROVIDER,
      status,
      running: this.started,
      connected: !authWriteInFlight && Boolean(this.socket) && snapshot.status === "connected",
      reconnectAttempts: this.reconnectAttempts,
      lastEventAt: snapshot.updatedAt,
      lastError: snapshot.lastDisconnectReason,
      issues: authWriteInFlight
        ? ["whatsapp_personal_auth_write_in_flight"]
        : snapshot.status === "connected"
          ? []
          // "conflict" gets its own issue tag (in addition to the distinct
          // status value above) -- another device holding the connection
          // slot is a materially different problem from an ordinary drop,
          // and a consumer that only scans `issues` (rather than switching
          // on `status`) should still be able to tell them apart.
          : snapshot.status === "conflict"
            ? ["whatsapp_personal_connection_conflict"]
            : ["whatsapp_personal_not_connected"],
    };
  }

  setPublisher(publisher: WhatsAppGatewayPublisher): void {
    this.publisher = publisher;
  }

  async start(): Promise<void> {
    if (this.started) {
      return;
    }
    this.started = true;
    await this.connectSocket();
  }

  async stop(): Promise<void> {
    this.started = false;
    this.connectPromise = null;
    this.pairingCodeRequested = false;
    // Drop any buffered inbound burst (don't publish after teardown).
    this.inboundDebouncer.dispose();
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.stopHealthCheck();
    if (this.socket && typeof this.socket.sendMessage === "function") {
      // No-op close path; Baileys exposes end/logout on some surfaces, but phase 4 keeps this minimal.
    }
    this.socket = null;
  }

  async handleGatewayConnected(_scope: GatewayScope): Promise<void> {
    await this.flushState();
  }

  async handleGatewayDisconnected(_reason: string): Promise<void> {
    return;
  }

  async handleChannelOutbound(
    frame: GatewayRequestEnvelope<GatewayChannelOutboundPayload>,
  ): Promise<Record<string, unknown>> {
    const payload = frame.payload;
    if (!this.supportsChannel(String(payload.channel_key || ""))) {
      throw new Error(`Unsupported personal channel key: ${payload.channel_key}`);
    }
    if (!this.socket) {
      throw new Error("WhatsApp personal runtime is not connected.");
    }
    const operation = normalizeChannelOutboundOperation(payload.operation);
    if (operation !== "send_final") {
      return this.handleDraftOutbound(frame, operation);
    }
    return this.sendFinalOutbound(payload);
  }

  private async handleDraftOutbound(
    frame: GatewayRequestEnvelope<GatewayChannelOutboundPayload>,
    operation: "draft_start" | "draft_delta" | "draft_final",
  ): Promise<Record<string, unknown>> {
    const payload = frame.payload;
    return this.draftManager.handleDraftOutbound(
      {
        draftId: String(payload.draft_id || payload.idempotency_key || "").trim(),
        remoteJid: String(payload.remote_jid || "").trim(),
        idempotencyKey: String(payload.idempotency_key || "").trim(),
        sequence: Number.isFinite(Number(payload.sequence)) ? Number(payload.sequence) : 0,
        operation,
        text: String(payload.text || ""),
        delta: String(payload.delta || ""),
        replyToExternalMessageId: String(payload.reply_to_external_message_id || "").trim() || undefined,
        channelKey: WHATSAPP_PERSONAL_CHANNEL_KEY,
        provider: WHATSAPP_PERSONAL_PROVIDER,
      },
      (augmented) => this.sendFinalOutbound(augmented as unknown as GatewayChannelOutboundPayload),
    );
  }

  private async sendFinalOutbound(payload: GatewayChannelOutboundPayload): Promise<Record<string, unknown>> {
    const socket = this.socket;
    if (!socket) {
      throw new Error("WhatsApp personal runtime is not connected.");
    }
    const idempotencyKey = String(payload.idempotency_key || "").trim();
    const remoteJid = String(payload.remote_jid || "").trim();
    const text = String(payload.text || "").trim();
    const mediaItems = normalizeWhatsAppOutboundMediaList((payload as WhatsAppOutboundDispatchPayload).media);
    if (!idempotencyKey || !remoteJid || (!text && mediaItems.length === 0)) {
      throw new Error("channel.outbound requires idempotency_key, remote_jid, and text and/or media.");
    }
    // A long reply is split into sequential messages at natural boundaries
    // (see sendWhatsAppTextChunks) rather than rejected.
    const clientMessageId = buildWhatsAppClientMessageId(idempotencyKey);
    // Self-chat loop guard (see handleMessagesUpsert's block comment): a
    // reply landing back in the owner's own WhatsApp "Saved Messages" is the
    // one case that can echo back through messages.upsert and re-trigger
    // itself. isSelfChatJid is false for every ordinary DM/group send, so
    // this adds no bookkeeping overhead outside self-chat.
    const isSelfChatTarget = this.isSelfChatJid(remoteJid);
    const now = new Date().toISOString();
    const existing = await this.outboundStore.beginSend(
      idempotencyKey,
      {
        idempotencyKey,
        remoteJid,
        text,
        clientMessageId,
        replyToExternalMessageId: String(payload.reply_to_external_message_id || "").trim() || undefined,
        status: "pending" as const,
        attemptCount: 0,
        createdAt: now,
        updatedAt: now,
      },
    );
    if (existing.status === "delivered") {
      return {
        channel_key: WHATSAPP_PERSONAL_CHANNEL_KEY,
        provider: WHATSAPP_PERSONAL_PROVIDER,
        idempotency_key: existing.idempotencyKey,
        external_message_id: existing.externalMessageId ?? existing.clientMessageId,
        remote_jid: existing.remoteJid,
        text: existing.text,
        delivered: true,
      };
    }
    const outboundRecord = (await this.outboundStore.markAttemptStarted(idempotencyKey))!;
    // Reuse the typing session started when the inbound message that
    // triggered this reply was admitted (see startTypingForChat) so the
    // indicator has been running since the agent started "thinking"
    // instead of blipping on for just this network call. Falls back to a
    // fresh session — the old behavior — when none is active for this
    // chat (a proactive send, or the inbound session went stale).
    const claimedTyping = this.claimTypingForChat(remoteJid);
    const typing = claimedTyping ?? new WhatsAppTypingKeepalive(
      socket.sendPresenceUpdate
        ? (action) => socket.sendPresenceUpdate?.(action, remoteJid)
        : undefined,
    );
    if (!claimedTyping) {
      await typing.start();
    }
    // Every id this call actually dispatches — every media item AND every
    // text chunk, not just the "primary" one mapped below. A long reply
    // split into several chunks previously only remembered its FIRST
    // chunk's id, silently missing the rest for both is_reply_to_sage and
    // (now) the self-chat echo guard. Mirrors telegram/runtime.ts's
    // sendFinalOutbound exactly.
    const sentIds: string[] = [];
    try {
      const response = mediaItems.length > 0
        ? await this.sendOutboundMediaItems(
            socket,
            remoteJid,
            text,
            mediaItems,
            outboundRecord.clientMessageId || clientMessageId,
            { afterItemSent: (id) => { if (id) sentIds.push(id); } },
          )
        : await this.sendWhatsAppTextChunks(
            socket,
            remoteJid,
            text,
            outboundRecord.clientMessageId || clientMessageId,
            {
              // Layer 1 of the loop guard (see beginSelfChatEchoGuard) —
              // only armed when this send actually targets self-chat, so an
              // ordinary DM/group reply never touches pendingSelfChatSends.
              beforeChunkSend: isSelfChatTarget
                ? (chunkText) => this.beginSelfChatEchoGuard(remoteJid, chunkText)
                : undefined,
              afterChunkSent: (id) => { if (id) sentIds.push(id); },
            },
          );
      const mapped = mapWhatsAppOutboundResult(
        {
          idempotencyKey,
          remoteJid,
          text,
          clientMessageId: outboundRecord.clientMessageId || clientMessageId,
          replyToExternalMessageId: String(payload.reply_to_external_message_id || "").trim() || undefined,
        },
        response,
      );
      await this.outboundStore.markDelivered(
        idempotencyKey,
        String(mapped.external_message_id || "").trim() || undefined,
      );
      // Track every sent message id for reply-to detection in the group
      // gate (is_reply_to_sage) AND the self-chat loop guard's primary,
      // durable check (isOwnSelfChatEcho).
      for (const id of sentIds) {
        this.sentMessageIds.add(id);
      }
      // Evict oldest (Set iteration order == insertion order) rather than
      // wiping the whole cache -- a full-wipe would drop is_reply_to_sage /
      // isOwnSelfChatEcho matching for every message sent moments ago,
      // right as the cache fills up under normal steady-state traffic.
      while (this.sentMessageIds.size > 500) {
        const oldest = this.sentMessageIds.values().next().value;
        if (oldest === undefined) {
          break;
        }
        this.sentMessageIds.delete(oldest);
      }
      return mapped;
    } finally {
      await typing.stop();
    }
  }

  /**
   * Sends a text reply as one or more messages: chunked at natural boundaries
   * when it exceeds WHATSAPP_MESSAGE_LIMIT, each chunk rendered from markdown
   * to WhatsApp's native inline formatting (bold/italic/strike/monospace).
   * Only the first chunk carries the deterministic idempotency messageId (and
   * is the primary send result); the rest are best-effort follow-ups.
   *
   * `hooks` lets the caller observe each chunk without duplicating the
   * chunking logic itself: `beforeChunkSend` fires with the chunk's plain
   * (pre-markdown-render) text right before that chunk's send begins —
   * sendFinalOutbound uses it to arm the self-chat loop guard (see
   * beginSelfChatEchoGuard) — and `afterChunkSent` fires with each chunk's
   * resulting external message id, however many chunks there were.
   */
  private async sendWhatsAppTextChunks(
    socket: BaileysSocketLike,
    remoteJid: string,
    text: string,
    primaryMessageId: string,
    hooks?: {
      beforeChunkSend?: (chunkText: string) => void;
      afterChunkSent?: (externalMessageId: string | undefined) => void;
    },
  ): Promise<Record<string, unknown> | undefined> {
    const chunks = chunkMessage(text, WHATSAPP_MESSAGE_LIMIT);
    if (chunks.length === 0) {
      return undefined;
    }
    let primaryResponse: Record<string, unknown> | undefined;
    for (let index = 0; index < chunks.length; index += 1) {
      hooks?.beforeChunkSend?.(chunks[index]);
      const response = await socket.sendMessage(
        remoteJid,
        { text: renderMarkdownToWhatsApp(chunks[index]) },
        index === 0 ? { messageId: primaryMessageId } : undefined,
      );
      hooks?.afterChunkSent?.(extractWhatsAppSentMessageId(response));
      if (index === 0) {
        primaryResponse = response;
      }
    }
    return primaryResponse;
  }

  /**
   * Sends a media-bearing outbound dispatch -- one Baileys sendMessage per
   * attachment (WhatsApp supports exactly one media item per message, same
   * as inbound). The FIRST item is sent with the caller's messageId (so
   * idempotency/ack tracking lines up with the existing single-message
   * contract) and carries `text` as its caption when the item has none of
   * its own; further items are best-effort follow-ups. Voice notes don't
   * support a caption bubble in WhatsApp's UI, so a voice item's caption
   * (or the primary item's leftover `text`) goes out as a separate
   * plain-text follow-up instead of being silently dropped.
   *
   * `hooks.afterItemSent` fires with EVERY actual socket.sendMessage call's
   * resulting external message id -- the primary item, every follow-up
   * item, the voice-caption follow-up, and the final degrade-to-text
   * fallback -- so the caller (sendFinalOutbound) can record all of them,
   * not just the primary one, in sentMessageIds.
   */
  private async sendOutboundMediaItems(
    socket: BaileysSocketLike,
    remoteJid: string,
    text: string,
    mediaItems: WhatsAppOutboundMediaItem[],
    primaryMessageId: string,
    hooks?: { afterItemSent?: (externalMessageId: string | undefined) => void },
  ): Promise<Record<string, unknown> | undefined> {
    let primaryResponse: Record<string, unknown> | undefined;
    for (let index = 0; index < mediaItems.length; index += 1) {
      const item = mediaItems[index];
      const isPrimary = index === 0;
      const caption = item.caption ?? (isPrimary ? (text || undefined) : undefined);
      // Captions carry the same markdown → WhatsApp native formatting as a
      // standalone text reply (WhatsApp renders *bold*/_italic_ inside a media
      // caption too). Plain captions come back unchanged.
      const formattedCaption = caption ? renderMarkdownToWhatsApp(caption) : caption;
      const content = await this.buildOutboundMediaContent(item, formattedCaption);
      if (!content) {
        // Best-effort -- an unresolvable attachment (missing file, bad URL,
        // over the size cap) must not sink the rest of the dispatch.
        continue;
      }
      const isVoice = item.kind === "voice" || item.as_voice === true;
      const response = await socket.sendMessage(
        remoteJid,
        content,
        isPrimary ? { messageId: primaryMessageId } : undefined,
      );
      hooks?.afterItemSent?.(extractWhatsAppSentMessageId(response));
      if (isPrimary) {
        primaryResponse = response;
      }
      if (isVoice && formattedCaption) {
        const captionResponse = await socket.sendMessage(remoteJid, { text: formattedCaption });
        hooks?.afterItemSent?.(extractWhatsAppSentMessageId(captionResponse));
      }
    }
    if (!primaryResponse && text) {
      // Every attachment failed to resolve but there's still text to send --
      // degrade to a text-only message rather than losing the turn entirely.
      primaryResponse = await socket.sendMessage(
        remoteJid,
        { text: renderMarkdownToWhatsApp(text) },
        { messageId: primaryMessageId },
      );
      hooks?.afterItemSent?.(extractWhatsAppSentMessageId(primaryResponse));
    }
    return primaryResponse;
  }

  /** Resolves one outbound media item to a Baileys sendMessage content
   *  object (image/video/audio/document), transcoding to voice-note
   *  Opus/OGG first when the item is a voice send and its source isn't
   *  already Opus/OGG. Returns undefined when the source can't be resolved
   *  (missing/oversized file, failed fetch, transcode failure) so the
   *  caller can degrade gracefully instead of failing the whole dispatch. */
  private async buildOutboundMediaContent(
    item: WhatsAppOutboundMediaItem,
    caption: string | undefined,
  ): Promise<Record<string, unknown> | undefined> {
    const resolved = await this.resolveOutboundMediaBytes(item);
    if (!resolved) {
      return undefined;
    }
    let buffer = resolved.buffer;
    const mimeType = resolved.mimeType;
    const normalizedMime = mimeType.split(";")[0]?.trim().toLowerCase() ?? "";
    const wantsVoice = item.kind === "voice" || item.as_voice === true;

    if (wantsVoice) {
      if (normalizedMime !== "audio/ogg" && normalizedMime !== "audio/opus") {
        try {
          buffer = await this.transcodeToWhatsAppVoiceOpus(buffer);
        } catch {
          return undefined;
        }
      }
      return { audio: buffer, ptt: true, mimetype: WHATSAPP_VOICE_MIMETYPE };
    }
    if (item.kind === "audio" || normalizedMime.startsWith("audio/")) {
      return { audio: buffer, mimetype: mimeType };
    }
    if (item.kind === "image" || normalizedMime.startsWith("image/")) {
      return { image: buffer, caption, mimetype: mimeType };
    }
    if (item.kind === "video" || normalizedMime.startsWith("video/")) {
      return { video: buffer, caption, mimetype: mimeType };
    }
    // "file" (document) -- Baileys requires an explicit mimetype for document sends.
    return {
      document: buffer,
      mimetype: mimeType || "application/octet-stream",
      fileName: this.resolveOutboundDocumentFileName(item, mimeType),
      caption,
    };
  }

  /** Reads an outbound media item's bytes from source_path (local disk on
   *  this same box) or source_url (HTTP/S fetch), capped at
   *  WHATSAPP_MEDIA_MAX_BYTES either way. source_path wins when both are
   *  present. */
  private async resolveOutboundMediaBytes(
    item: WhatsAppOutboundMediaItem,
  ): Promise<{ buffer: Buffer; mimeType: string } | undefined> {
    let buffer: Buffer | undefined;
    if (item.source_path) {
      try {
        const stat = await fs.stat(item.source_path);
        if (!stat.isFile() || stat.size > WHATSAPP_MEDIA_MAX_BYTES) {
          return undefined;
        }
        buffer = await fs.readFile(item.source_path);
      } catch {
        return undefined;
      }
    } else if (item.source_url) {
      try {
        const response = await fetch(item.source_url);
        if (!response.ok) {
          return undefined;
        }
        const arrayBuffer = await response.arrayBuffer();
        if (arrayBuffer.byteLength > WHATSAPP_MEDIA_MAX_BYTES) {
          return undefined;
        }
        buffer = Buffer.from(arrayBuffer);
      } catch {
        return undefined;
      }
    }
    if (!buffer || buffer.length === 0) {
      return undefined;
    }
    const mimeType = item.mime_type || defaultWhatsAppMimeTypeForKind(item.kind);
    return { buffer, mimeType };
  }

  private resolveOutboundDocumentFileName(item: WhatsAppOutboundMediaItem, mimeType: string): string {
    if (item.source_path) {
      const base = path.basename(item.source_path);
      if (base) {
        return base;
      }
    }
    if (item.source_url) {
      try {
        const base = path.basename(new URL(item.source_url).pathname);
        if (base) {
          return decodeURIComponent(base);
        }
      } catch {
        // fall through to the mimetype-derived default below
      }
    }
    const ext = mimeType.split("/")[1]?.split(";")[0]?.trim();
    return ext ? `file.${ext}` : "file";
  }

  /** Transcodes arbitrary audio to WhatsApp's native voice-note format
   *  (mono Opus in an OGG container, 48kHz/64kbps) by shelling out to
   *  ffmpeg -- mirrors OpenClaw's outbound-media-contract.ts transcode
   *  recipe (-c:a libopus -ar 48000 -b:a 64k -f ogg). Requires a system
   *  `ffmpeg` binary; throws if it's missing or the process fails, which
   *  the caller (buildOutboundMediaContent) treats as "this attachment
   *  can't be sent" rather than crashing the whole dispatch. */
  private async transcodeToWhatsAppVoiceOpus(buffer: Buffer): Promise<Buffer> {
    const workDir = await fs.mkdtemp(path.join(os.tmpdir(), "empyralis-wa-voice-"));
    try {
      const inputPath = path.join(workDir, "input.audio");
      const outputPath = path.join(workDir, "voice.ogg");
      await fs.writeFile(inputPath, buffer);
      await execFileAsync(
        "ffmpeg",
        [
          "-hide_banner",
          "-loglevel", "error",
          "-y",
          "-i", inputPath,
          "-vn",
          "-c:a", "libopus",
          "-ar", "48000",
          "-b:a", "64k",
          "-f", "ogg",
          outputPath,
        ],
        { timeout: WHATSAPP_FFMPEG_TIMEOUT_MS },
      );
      return await fs.readFile(outputPath);
    } finally {
      await fs.rm(workDir, { recursive: true, force: true }).catch(() => undefined);
    }
  }

  private async connectSocket(): Promise<void> {
    if (this.connectPromise) {
      return this.connectPromise;
    }
    const task = this.connectSocketInternal().finally(() => {
      if (this.connectPromise === task) {
        this.connectPromise = null;
      }
    });
    this.connectPromise = task;
    return task;
  }

  private async connectSocketInternal(): Promise<void> {
    const loginConfig = {
      ...loadWhatsAppLoginConfig(),
      ...(await this.configStore.loadWhatsAppConfig()),
    };
    const preflightState = buildWhatsAppPreflightState(loginConfig);
    if (preflightState) {
      await this.sessionStore.save(preflightState);
      await this.flushState();
      return;
    }
    const adapter = await this.getAdapter();
    const authDir = await this.sessionStore.ensureAuthStateDir();
    // Barrier: if a previous connect cycle's creds write is still draining
    // (e.g. a fast reconnect right on the heels of a creds.update), wait
    // for it before handing the directory to Baileys, so
    // useMultiFileAuthState() can never read creds.json mid-write.
    await waitForAuthWriteIdle(authDir);
    this.authPersistence = new WhatsAppAuthPersistence(authDir, {
      codec: adapter.credsCodec ?? DEFAULT_CREDS_CODEC,
    });
    // Repair a corrupted/truncated creds.json from creds.json.bak BEFORE
    // Baileys ever reads it -- useMultiFileAuthState() has no knowledge of
    // our backup and would otherwise silently treat unparseable JSON as "no
    // creds" (Baileys' own readData() returns null on any parse error),
    // generating a brand-new identity -- i.e. forcing a fresh QR/pairing
    // scan even though the account never actually logged out.
    await this.authPersistence.restoreFromBackupIfNeeded();
    this.authBundle = await adapter.loadAuthState(authDir);
    this.pairingCodeRequested = false;
    await this.sessionStore.save({
      status: "connecting",
      qrCode: undefined,
      loginHint: undefined,
      pairingCode: undefined,
      pairingCodeGeneratedAt: undefined,
      lastDisconnectReason: undefined,
      lastDisconnectCode: undefined,
      retryable: true,
    });
    await this.flushState();
    // Baileys' own bundled default WA Web version goes stale between package
    // releases and is a documented cause of clean connection rejections
    // (WhiskeySockets/Baileys#2679) — fetch the currently-live one instead.
    // Falls back to Baileys' own default (by simply omitting `version`) if
    // the fetch fails, rather than failing the whole connection over it.
    const waWebVersion = await adapter.fetchWaWebVersion();
    const socket = adapter.createSocket({
      auth: this.authBundle.state,
      browser: adapter.browserDescriptor("Empyralis"),
      logger: this.logger,
      printQRInTerminal: false,
      syncFullHistory: false,
      markOnlineOnConnect: false,
      ...(waWebVersion ? { version: waWebVersion } : {}),
    });
    this.socket = socket;
    socket.ev.on("creds.update", async () => {
      // Mirrors connection.update's staleness guard directly below: Baileys
      // keeps emitting on this socket's own event emitter even after we've
      // abandoned it (e.g. handleDisconnect() or reconnectForConfigUpdate()
      // nulled this.socket and moved on to a fresh one) — a late
      // creds.update from the OLD socket would otherwise overwrite the
      // on-disk auth state with stale creds, clobbering whatever the
      // current, live socket has already advanced past. Only the socket we
      // currently own may persist creds.
      if (this.socket !== socket) {
        return;
      }
      // Bypasses Baileys' own bundled saveCreds() (a bare non-atomic
      // writeFile with no backup -- the exact gap this module exists to
      // close) in favor of our atomic-write-with-backup path. getCreds is a
      // getter so the enqueued write always reads whatever the CURRENT live
      // creds object is when it actually runs, not a snapshot captured at
      // enqueue time -- Baileys mutates state.creds in place across rapid
      // updates.
      await this.authPersistence?.saveCreds(() => this.authBundle?.state?.creds);
    });
    socket.ev.on("connection.update", (update) => {
      // Baileys keeps emitting on this socket's own event emitter even
      // after we've abandoned it (e.g. handleDisconnect() nulled
      // this.socket and moved on) — a late "close" event from the old
      // socket would otherwise clobber the fresh idle state with a stale
      // logged_out/retryable:false. Only the socket we currently own may
      // update state.
      if (this.socket !== socket) {
        return;
      }
      void this.handleConnectionUpdate(update);
    });
    socket.ev.on("messages.upsert", (event) => {
      void this.handleMessagesUpsert(event);
    });
    void this.maybeRequestPairingCode(socket, loginConfig);
  }

  private async handleConnectionUpdate(update: Record<string, unknown>): Promise<void> {
    const qr = String(update.qr ?? "").trim();
    if (qr) {
      const qrPayload = buildWhatsAppQrPayload(qr);
      await this.sessionStore.save({
        status: qrPayload.status,
        qrCode: qrPayload.qrCode,
        loginHint: undefined,
        pairingCode: undefined,
        pairingCodeGeneratedAt: undefined,
        retryable: true,
        lastDisconnectReason: undefined,
        lastDisconnectCode: undefined,
      });
      await this.flushState();
      return;
    }
    const connection = String(update.connection ?? "").trim();
    if (connection === "open") {
      this.reconnectAttempts = 0;
      this.pairingCodeRequested = false;
      await this.sessionStore.save({
        status: "connected",
        qrCode: undefined,
        loginHint: undefined,
        pairingCode: undefined,
        pairingCodeGeneratedAt: undefined,
        linkedJid: String(this.socket?.user?.id ?? "").trim() || undefined,
        linkedName: String(this.socket?.user?.name ?? "").trim() || undefined,
        connectedAt: new Date().toISOString(),
        retryable: true,
        lastDisconnectReason: undefined,
        lastDisconnectCode: undefined,
      });
      await this.flushState();
      // Start actively re-probing this now-live socket -- see
      // WHATSAPP_HEALTH_CHECK_INTERVAL_MS's doc for why connection.update's
      // own "close" event isn't sufficient on its own. Mirrors
      // TelegramPersonalRuntime.connectClient's identical call on its own
      // successful connect.
      this.scheduleHealthCheck();
      return;
    }
    if (connection === "close") {
      await this.handleSocketClose(update.lastDisconnect);
    }
  }

  /**
   * Classifies a disconnect (via resolveWhatsAppReconnectState) and persists
   * the resulting state -- the single place that tears down a dead socket,
   * whether Baileys itself reported it via connection.update's "close" event
   * (the ordinary path) or runHealthCheck's active probe found one
   * connection.update never fired for (see that method's doc comment).
   * Mirrors TelegramPersonalRuntime.handleConnectionFailure's identical
   * dual-caller shape.
   *
   * `lastDisconnect` is passed straight through to
   * resolveWhatsAppReconnectState -- either Baileys' own real
   * `update.lastDisconnect` object (close-event path) or a synthetic
   * `{ error: { message } }` runHealthCheck builds from its probe's thrown
   * error (statusCode absent, so it falls through resolveWhatsAppReconnectState's
   * generic reconnectable-with-status:"disconnected" branch -- correct,
   * since an active-probe failure carries no Baileys disconnect code to
   * classify against).
   */
  private async handleSocketClose(lastDisconnect: unknown): Promise<void> {
    this.stopHealthCheck();
    const adapter = await this.getAdapter();
    const reconnectState = resolveWhatsAppReconnectState(lastDisconnect, adapter.disconnectReason);
    const abandonedSocket = this.socket as unknown as {
      end?: (error?: unknown) => void;
      ws?: { close?: () => void };
    } | null;
    this.socket = null;
    this.authBundle = null;
    this.pairingCodeRequested = false;
    // Release the abandoned socket's own transport/timers -- deliberately
    // NOT logout(), unlike handleDisconnect()'s full reset. This teardown
    // fires both for a real Baileys "close" (harmless no-op there; Baileys
    // already tore its own socket down) and for runHealthCheck()'s
    // half-dead-but-not-closed case (the actual leak: nothing else ever
    // released that socket's resources -- see
    // whatsapp-health-check-teardown-leak.test.ts). logout() would
    // actively deauthorize the linked device over the network, which is
    // wrong here regardless of whether reconnectState.shouldReconnect --
    // even the loggedOut/401 branch below already handles "must relink" by
    // wiping the local auth state, not by calling logout() on a socket
    // that may itself be the reason we got here.
    try {
      abandonedSocket?.end?.(undefined);
    } catch {
      // Already gone -- nothing to close.
    }
    try {
      abandonedSocket?.ws?.close?.();
    } catch {
      // Already gone.
    }
    if (!reconnectState.shouldReconnect) {
      // Genuine logout only -- shouldReconnect is false exclusively for
      // Baileys' loggedOut/401 code (see resolveWhatsAppReconnectState in
      // ./reconnect.ts), matching what OpenClaw treats as "must relink"
      // (extensions/whatsapp/src/connection-controller.ts). Every other
      // disconnect code (badSession, restartRequired, connectionClosed,
      // connectionReplaced/conflict, etc) reconnects with the existing
      // creds intact -- this branch was already scoped correctly; what was
      // missing was safety on the delete itself, which clearAuthStateDir()
      // now provides. Drain any in-flight write first so the delete isn't
      // racing a rename.
      await waitForAuthWriteIdle(this.sessionStore.authStateDir());
      this.authPersistence = null;
      await this.sessionStore.clearAuthStateDir();
    }
    await this.sessionStore.save({
      status: reconnectState.status,
      retryable: reconnectState.shouldReconnect,
      qrCode: undefined,
      pairingCode: undefined,
      pairingCodeGeneratedAt: undefined,
      lastDisconnectReason: reconnectState.reason,
      lastDisconnectCode: reconnectState.statusCode,
    });
    await this.flushState();
    if (reconnectState.shouldReconnect && this.started) {
      // A "conflict" (another device holding the connection slot right now
      // -- see resolveWhatsAppReconnectState's connectionReplaced/440
      // branch) backs off to the policy's ceiling delay instead of the
      // normal fast-start ramp, so a persistent conflict doesn't turn into
      // a tight retry loop racing the other device — see
      // scheduleReconnect's forceMaxDelay doc.
      this.scheduleReconnect({ forceMaxDelay: reconnectState.status === "conflict" });
    }
  }

  /** Schedules the next active health-probe (see
   *  WHATSAPP_HEALTH_CHECK_INTERVAL_MS's doc). Idempotent -- always clears
   *  any existing timer first, so it's safe to call from runHealthCheck's
   *  own re-schedule as well as right after a fresh "open". Mirrors
   *  TelegramPersonalRuntime.scheduleHealthCheck exactly, including the
   *  unref() so this background probe never keeps the process alive on its
   *  own. */
  private scheduleHealthCheck(): void {
    this.stopHealthCheck();
    this.healthCheckTimer = setTimeout(() => {
      this.healthCheckTimer = null;
      void this.runHealthCheck();
    }, WHATSAPP_HEALTH_CHECK_INTERVAL_MS);
    this.healthCheckTimer.unref?.();
  }

  private stopHealthCheck(): void {
    if (this.healthCheckTimer) {
      clearTimeout(this.healthCheckTimer);
      this.healthCheckTimer = null;
    }
  }

  /**
   * Actively probes the live socket with a real `sendPresenceUpdate`
   * ("available") round trip over the WebSocket and re-schedules itself on
   * success. See WHATSAPP_HEALTH_CHECK_INTERVAL_MS's doc for why this is
   * needed alongside connection.update's own "close" event: Baileys'
   * sendRawMessage throws the instant its underlying WebSocket is no longer
   * open (see @whiskeysockets/baileys/lib/Socket/socket.js), so this catches
   * a socket that's gone dead WITHOUT Baileys ever having fired its own
   * close event for it (e.g. a half-dead network path where no close frame
   * was ever received) -- exactly the gap TelegramPersonalRuntime's
   * checkAuthorized probe closes for GramJS's own silent-death case.
   *
   * A single failure is enough to hand off to handleSocketClose -- no
   * debounce/retry-count needed here, mirroring
   * TelegramPersonalRuntime.runHealthCheck's identical reasoning: that
   * method already schedules its own reconnect for anything reconnectable,
   * so a one-off blip still self-heals via the normal reconnect path.
   */
  private async runHealthCheck(): Promise<void> {
    const socket = this.socket;
    if (!socket || typeof socket.sendPresenceUpdate !== "function") {
      // No live socket, or an adapter/mock build too old to support the
      // probe -- nothing to check right now; try again next interval rather
      // than spinning the loop down entirely (a reconnect that lands later
      // would otherwise never get probed).
      this.scheduleHealthCheck();
      return;
    }
    try {
      await socket.sendPresenceUpdate("available", String(socket.user?.id ?? "").trim() || undefined);
      // A reconnect could have already replaced this.socket while the probe
      // above was in flight -- the new socket's own connection.update
      // handling already governs scheduling in that case, so don't
      // re-arm on top of it.
      if (this.socket === socket) {
        this.scheduleHealthCheck();
      }
    } catch (error) {
      if (this.socket !== socket) {
        // A reconnect already replaced this socket while the probe was in
        // flight -- the NEW socket's own state already governs; don't let a
        // stale probe's failure clobber it (mirrors connection.update's own
        // `if (this.socket !== socket) return;` staleness guard elsewhere in
        // this file).
        return;
      }
      await this.handleSocketClose({
        error: { message: error instanceof Error ? error.message : String(error ?? "whatsapp_health_check_failed") },
      });
    }
  }

  private async handleMessagesUpsert(event: Record<string, unknown>): Promise<void> {
    const ownedJid = String(this.socket?.user?.id ?? "").trim() || undefined;
    const messages = Array.isArray(event.messages) ? event.messages : [];
    for (const entry of messages) {
      if (!entry || typeof entry !== "object") {
        continue;
      }
      const rawEntry = entry as Record<string, unknown>;
      const mediaDescriptor = detectWhatsAppInboundMedia(rawEntry.message as Record<string, unknown> | undefined);
      const resolvedMedia = mediaDescriptor
        ? await this.downloadInboundMedia(rawEntry, mediaDescriptor)
        : undefined;
      const mapped = mapWhatsAppInboundMessage(rawEntry, {
        ownedJid,
        media: resolvedMedia ? [resolvedMedia] : undefined,
      });
      if (!mapped || (mapped.message.from_me && !mapped.message.is_self_chat)) {
        continue;
      }
      if (mapped.message.is_self_chat) {
        // ── LOOP GUARD ──────────────────────────────────────────────────
        // is_self_chat is deliberately let through the from_me gate above
        // (a message the owner sends into their own WhatsApp "Saved
        // Messages" is a command channel, not noise) — but Baileys'
        // emitOwnEvents (default true, never overridden by createSocket()
        // above; see @whiskeysockets/baileys/lib/Defaults/index.js and
        // lib/Socket/messages-send.js's
        // `if (config.emitOwnEvents) { process.nextTick(...upsertMessage...) }`)
        // fires this SAME messages.upsert event for every message WE send
        // too, including our own replies into that same self-chat. Without
        // a guard, every reply Sage sends into self-chat would immediately
        // re-admit itself as a new inbound "command", producing an
        // infinite reply loop (same class as a prior group-spam incident).
        // isOwnSelfChatEcho recognizes (and drops) our own echo;
        // admitSelfChatTurnOrTrip is the backstop circuit breaker in case
        // that ever misses one. Neither applies to a plain inbound message
        // from someone else, or to a group — only self-chat, which is the
        // only case that can ever echo the runtime's own send back to
        // itself. Mirrors telegram/runtime.ts's handleInboundMessage
        // exactly.
        if (this.isOwnSelfChatEcho(mapped.message)) {
          continue;
        }
        if (!this.admitSelfChatTurnOrTrip()) {
          continue;
        }
      }
      // Resolve is_reply_to_sage: quoted stanza was sent by Sage
      if (mapped.message.is_group && mapped.message.quoted_stanza_id) {
        mapped.message.is_reply_to_sage = this.sentMessageIds.has(String(mapped.message.quoted_stanza_id));
      }
      // Group gate: REMOVED as a gateway-side DECISION (2026-07-23,
      // group_policy build). This runtime still computes the raw mention
      // FACTS above (is_mentioned via mapWhatsAppInboundMessage's
      // mentionedJid scan, is_reply_to_sage just above) — unavoidably
      // platform-specific, stays here. What moved is WHO DECIDES shouldSkip
      // from those facts: personal_channels_service.py's
      // mention_gating_service.resolve_inbound_mention_decision is now the
      // ONE shared resolver (mirrors OpenClaw's resolveInboundMentionDecision
      // — see docs/OpenClaw.md's GROUP/MENTION GATING section), replacing
      // what used to be TWO independent copies of the same shouldSkip
      // decision (this "primary gate" here, plus an inline backend
      // safety-net check in personal_channels_service.py). Every group
      // message is now always forwarded with its computed facts; the
      // backend decides — see personal_channels_service.py's
      // DEFAULT_REQUIRE_MENTION doc comment for the default (OFF — Ruling A
      // "see-and-decide") this now defers to.
      //
      // Group subject lookup now runs for EVERY group message (previously
      // only for ones that passed the gate above) — this is intentional,
      // not a leftover: the group context (is_group/chat_title) the model
      // needs to exercise its own see-and-decide judgment (Ruling A) is
      // needed on every group turn now, not just addressed ones. Threaded
      // through to the server as chat_title so the owner-unified activity
      // feed's mirrored "[sent to WhatsApp · <subject>]" entries are
      // legible instead of a bare remote_jid (see
      // personal_channel_sage_bridge_service.py). A failed/slow lookup
      // must never drop or delay the message itself — chat_title just
      // stays unset and the server falls back to the channel label alone.
      if (mapped.message.is_group && typeof this.socket?.groupMetadata === "function") {
        try {
          const groupMetadata = await this.socket.groupMetadata(mapped.message.remote_jid);
          const subject = String(groupMetadata?.subject ?? "").trim();
          if (subject) {
            mapped.message.chat_title = subject;
          }
        } catch {
          // best-effort — group subject is cosmetic, never load-bearing
        }
      }
      // Typing starts NOW (before the debouncer) so the indicator is live for
      // the whole coalesce window and the agent's think-time. The publish is
      // coalesced: a burst of rapid messages becomes one channel.inbound event
      // / one agent turn / one reply. Media and command messages bypass the
      // window and flush immediately (see InboundDebouncer).
      this.startTypingForChat(mapped.message.remote_jid);
      // A message that arrived AS media bypasses the debounce window even if
      // its download failed/was skipped (mapped then carries only the caption
      // text) — a photo shouldn't wait, whether or not we could fetch it.
      this.inboundDebouncer.admit(mapped, { bypass: Boolean(mediaDescriptor) });
    }
  }

  /**
   * True when `jid` is the connected account's OWN identity — i.e. a send
   * to this remoteJid lands in the owner's WhatsApp "Saved Messages", not a
   * DM with someone else or a group. Reads the live socket's own user id
   * fresh on every call (rather than caching it once, the way
   * telegram/runtime.ts's isSelfChatJid caches selfIdentity) since Baileys
   * exposes it directly on the socket at all times and a reconnect can swap
   * in a new socket/identity between calls — using the exact same
   * comparison message-mapper.ts's mapWhatsAppInboundMessage uses for
   * is_self_chat (`remoteJid === ownedJid`) so both sides always agree on
   * the same chat.
   */
  private isSelfChatJid(jid: string): boolean {
    const trimmed = String(jid || "").trim();
    const ownedJid = String(this.socket?.user?.id ?? "").trim();
    if (!trimmed || !ownedJid) {
      return false;
    }
    return trimmed === ownedJid;
  }

  /**
   * Records that a self-chat send is about to go out with this exact
   * (pre-markdown-render) text — layer 1 of the loop guard's two send-side
   * hooks (see the pendingSelfChatSends field doc). Called from
   * sendFinalOutbound (via sendWhatsAppTextChunks's beforeChunkSend hook)
   * only when the send targets the self-chat peer. No matching "end" call
   * on purpose: entries are cheap, capped, and pruned by TTL the next time
   * isOwnSelfChatEcho runs — precise removal would need to survive send
   * failures/retries too, and isn't worth the bookkeeping for a low-volume,
   * single-conversation guard. Mirrors telegram/runtime.ts's
   * beginSelfChatEchoGuard exactly.
   */
  private beginSelfChatEchoGuard(remoteJid: string, text: string): void {
    const trimmed = String(text || "").trim();
    if (!trimmed) {
      return;
    }
    this.pendingSelfChatSends.push({ remoteJid, text: trimmed, startedAt: Date.now() });
    // Defensive cap, mirroring sentMessageIds's own bound above — self-chat
    // traffic is low-volume so this should never really fill up.
    if (this.pendingSelfChatSends.length > 20) {
      this.pendingSelfChatSends.splice(0, this.pendingSelfChatSends.length - 20);
    }
  }

  /**
   * True when this inbound self-chat message is actually OUR OWN reply
   * resurfacing through messages.upsert, not a new owner command — see
   * handleMessagesUpsert's LOOP GUARD block comment for why this exists.
   * Two independent checks, either one is sufficient:
   *
   *  1. external_message_id already in sentMessageIds — the durable,
   *     precise signal: this EXACT message (every chunk of every reply
   *     this runtime has sent is recorded there, see sendFinalOutbound) is
   *     one we sent ourselves.
   *  2. text+remoteJid matches a still-pending send recorded by
   *     beginSelfChatEchoGuard — covers the race where Baileys' own-send
   *     echo (emitOwnEvents, fired via process.nextTick right after
   *     relayMessage resolves — see messages-send.js) reaches
   *     handleMessagesUpsert before sendMessage()'s returned key.id is
   *     known to the caller. Stale entries (older than
   *     SELF_CHAT_PENDING_SEND_TTL_MS) are pruned opportunistically on
   *     every call. Mirrors telegram/runtime.ts's isOwnSelfChatEcho
   *     exactly.
   */
  private isOwnSelfChatEcho(message: { external_message_id: string; remote_jid: string; text: string }): boolean {
    const externalId = String(message.external_message_id || "").trim();
    if (externalId && this.sentMessageIds.has(externalId)) {
      return true;
    }
    const text = String(message.text || "").trim();
    const remoteJid = String(message.remote_jid || "").trim();
    const now = Date.now();
    let matched = false;
    for (let i = this.pendingSelfChatSends.length - 1; i >= 0; i -= 1) {
      const entry = this.pendingSelfChatSends[i];
      if (now - entry.startedAt > SELF_CHAT_PENDING_SEND_TTL_MS) {
        this.pendingSelfChatSends.splice(i, 1);
        continue;
      }
      if (!matched && entry.remoteJid === remoteJid && entry.text === text) {
        matched = true;
        this.pendingSelfChatSends.splice(i, 1);
      }
    }
    return matched;
  }

  /**
   * Rolling circuit breaker — layer 2 of the loop guard, independent of
   * isOwnSelfChatEcho. Returns true (and records the turn) when it's safe
   * to admit one more self-chat turn right now; false (refusing, loudly)
   * once SELF_CHAT_BREAKER_MAX_TURNS have already been admitted within the
   * last SELF_CHAT_BREAKER_WINDOW_MS. This is a pure backstop: it bounds
   * the worst case to a handful of extra turns — instead of an unbounded
   * reply loop hammering the WhatsApp API — even in a future scenario
   * where isOwnSelfChatEcho somehow fails to recognize a genuine echo.
   * Mirrors telegram/runtime.ts's admitSelfChatTurnOrTrip exactly.
   */
  private admitSelfChatTurnOrTrip(): boolean {
    const now = Date.now();
    this.selfChatTurnTimestamps = this.selfChatTurnTimestamps.filter(
      (ts) => now - ts < SELF_CHAT_BREAKER_WINDOW_MS,
    );
    if (this.selfChatTurnTimestamps.length >= SELF_CHAT_BREAKER_MAX_TURNS) {
      this.logger?.error?.(
        { windowMs: SELF_CHAT_BREAKER_WINDOW_MS, maxTurns: SELF_CHAT_BREAKER_MAX_TURNS },
        "whatsapp self-chat loop-guard circuit breaker tripped — refusing further self-chat turns",
      );
      return false;
    }
    this.selfChatTurnTimestamps.push(now);
    return true;
  }

  /** Downloads a detected media attachment's bytes via Baileys, capped at
   *  WHATSAPP_MEDIA_MAX_BYTES, and stores them under this gateway's own
   *  state dir (<stateDir>/whatsapp/media/<basename>).
   *
   *  media_id is that stored file's path RELATIVE to the gateway state dir
   *  root (GatewayStateDb.rootDirPath()), NOT a bare/opaque token -- the
   *  exact same contract the Telegram channel uses (see
   *  build/telegram-media's downloadAndStoreTelegramMedia and
   *  protocol/types.ts GatewayChannelInboundMediaItem). The gateway and the
   *  co-located server share the state-dir filesystem on the paired Agent
   *  Computer box, so the server resolves media_id by joining it back onto
   *  that same root it already has -- there is no HTTP fetch route. (The
   *  only difference from Telegram is the subdir: whatsapp/media, sibling of
   *  the existing whatsapp/auth dir, vs Telegram's top-level telegram-media.)
   *
   *  Best-effort: any failure (declared size over the cap, a download error,
   *  an oversized actual payload) returns undefined rather than dropping the
   *  whole inbound message -- a message with a caption but an unfetchable
   *  attachment should still reach the agent as text. */
  private async downloadInboundMedia(
    rawMessage: Record<string, unknown>,
    descriptor: WhatsAppInboundMediaDescriptor,
  ): Promise<WhatsAppInboundMediaItem | undefined> {
    if (
      typeof descriptor.declaredSizeBytes === "number"
      && descriptor.declaredSizeBytes > WHATSAPP_MEDIA_MAX_BYTES
    ) {
      return undefined;
    }
    const socket = this.socket;
    const reuploadRequest = socket?.updateMediaMessage
      ? (msg: Record<string, unknown>) => socket.updateMediaMessage!(msg)
      : undefined;
    let buffer: Buffer;
    try {
      const adapter = await this.getAdapter();
      buffer = await adapter.downloadMedia(rawMessage, { logger: this.logger, reuploadRequest });
    } catch {
      return undefined;
    }
    if (!buffer || buffer.length === 0 || buffer.length > WHATSAPP_MEDIA_MAX_BYTES) {
      return undefined;
    }
    // Sanitize the WhatsApp stanza id (sender-influenced) down to filesystem-
    // safe chars before using it as a basename prefix -- the randomUUID()
    // suffix already guarantees uniqueness, so this prefix is purely for
    // debuggability and must never be able to escape the media dir.
    const rawExternalId = String((rawMessage.key as { id?: unknown } | undefined)?.id ?? "").trim();
    const safeExternalId = rawExternalId.replace(/[^A-Za-z0-9_-]/g, "").slice(0, 64) || "media";
    const baseName = `${safeExternalId}-${crypto.randomUUID()}`;
    const mediaId = `${WHATSAPP_MEDIA_SUBDIR}/${baseName}`;
    const absolutePath = path.join(this.db.rootDirPath(), "whatsapp", "media", baseName);
    await fs.mkdir(path.dirname(absolutePath), { recursive: true });
    await fs.writeFile(absolutePath, buffer, { mode: 0o600 });
    return {
      kind: descriptor.kind,
      media_id: mediaId,
      mime_type: descriptor.mimeType,
      filename: descriptor.filename,
      size_bytes: buffer.length,
      duration_sec: descriptor.durationSec,
    };
  }

  private async publishInbound(payload: GatewayChannelInboundPayload): Promise<void> {
    await this.publisher?.publishEvent("channel.inbound", payload);
  }

  /**
   * Starts a typing keepalive the instant an inbound message is admitted
   * (and has passed the group gate above), so the indicator covers the LLM
   * "thinking" time (which happens upstream, before the Gateway is ever
   * asked to send a reply) instead of blipping on for only the fraction of
   * a second around the final sendMessage call. claimTypingForChat() hands
   * this same session to sendFinalOutbound() when the reply for this chat
   * is ready to go out.
   *
   * Coalesced per chat and tagged with the socket it was started against:
   * a second inbound message for the same remoteJid before the reply goes
   * out reuses the running loop instead of starting a duplicate one, and a
   * session left over from a since-replaced socket (reconnect) is treated
   * as stale rather than reused.
   */
  private startTypingForChat(remoteJid: string): void {
    const jid = String(remoteJid || "").trim();
    if (!jid) {
      return;
    }
    const socket = this.socket;
    if (!socket?.sendPresenceUpdate) {
      return;
    }
    const existing = this.activeTyping.get(jid);
    if (
      existing
      && existing.socket === socket
      && Date.now() - existing.startedAt < WHATSAPP_INBOUND_TYPING_MAX_TTL_MS
    ) {
      return;
    }
    const typing = new WhatsAppTypingKeepalive(
      (action) => socket.sendPresenceUpdate?.(action, jid),
      WHATSAPP_TYPING_KEEPALIVE_MS,
      WHATSAPP_INBOUND_TYPING_MAX_TTL_MS,
    );
    this.activeTyping.set(jid, { typing, startedAt: Date.now(), socket });
    void typing.start();
  }

  /**
   * Hands the caller the typing session startTypingForChat() started for
   * this chat, removing it from the map so it can't be claimed twice.
   * Returns undefined — leaving a stale entry's own loop to run down on
   * its own TTL — when there's nothing usable to claim: no session was
   * ever started (e.g. a proactive/unprompted send), it was started
   * against a socket that's since been replaced by a reconnect, or it
   * already exceeded its TTL. The caller is expected to start a fresh one
   * itself in that case.
   */
  private claimTypingForChat(remoteJid: string): WhatsAppTypingKeepalive | undefined {
    const jid = String(remoteJid || "").trim();
    if (!jid) {
      return undefined;
    }
    const existing = this.activeTyping.get(jid);
    if (!existing) {
      return undefined;
    }
    this.activeTyping.delete(jid);
    if (
      existing.socket !== this.socket
      || Date.now() - existing.startedAt >= WHATSAPP_INBOUND_TYPING_MAX_TTL_MS
    ) {
      void existing.typing.stop();
      return undefined;
    }
    return existing.typing;
  }

  private scheduleReconnect(options: { forceMaxDelay?: boolean } = {}): void {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
    }
    if (this.reconnectAttempts >= DEFAULT_WHATSAPP_RECONNECT_POLICY.maxAttempts) {
      void this.sessionStore
        .save({
          retryable: false,
          lastDisconnectReason: "reconnect_exhausted",
        })
        .then(() => this.flushState())
        .catch(() => undefined);
      return;
    }
    // A WhatsApp "stream conflict" (DisconnectReason.connectionReplaced/440
    // -- another device linked to this same account is active right now,
    // see resolveWhatsAppReconnectState) means an immediate retry at the
    // ramp's normal fast-start delay would just race the other device for
    // the same connection slot over and over. Jump straight to the policy's
    // own ceiling delay instead -- still bounded by the same maxAttempts
    // exhaustion check above, so a conflict that never clears still
    // eventually surfaces as "reconnect_exhausted" rather than retrying
    // forever.
    const delayMs = options.forceMaxDelay
      ? DEFAULT_WHATSAPP_RECONNECT_POLICY.maxDelayMs
      : computeWhatsAppReconnectDelay(this.reconnectAttempts);
    this.reconnectAttempts += 1;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      if (!this.started) {
        return;
      }
      void this.connectSocket();
    }, delayMs);
  }

  private async flushState(): Promise<void> {
    if (!this.publisher) {
      return;
    }
    const snapshot = await this.sessionStore.load();
    try {
      await this.publisher.publishStateUpdate(
        redactWhatsAppCredentials(this.sessionStore.toGatewayStatePayload(snapshot)),
      );
    } catch {
      return;
    }
  }

  private async maybeRequestPairingCode(
    socket: BaileysSocketLike,
    loginConfig: WhatsAppLoginConfig,
  ): Promise<void> {
    if (this.pairingCodeRequested) {
      return;
    }
    if (this.authBundle?.state?.creds?.registered) {
      return;
    }
    if (!loginConfig.phoneNumber || typeof socket.requestPairingCode !== "function") {
      return;
    }
    this.pairingCodeRequested = true;
    try {
      const pairingCode = await socket.requestPairingCode(
        loginConfig.phoneNumber,
        loginConfig.customPairingCode,
      );
      await this.sessionStore.save(buildWhatsAppPairingCodeState(loginConfig, pairingCode));
      await this.flushState();
    } catch (error) {
      this.pairingCodeRequested = false;
      // Previously silent — status stayed wherever it was (typically still
      // "connecting"), so the UI spun forever with no indication the
      // request had actually failed (e.g. a phone number WhatsApp rejects).
      // "disconnected" + retryable:true lands on the same idle/retry view
      // PersonalChannelConnectPanel already shows for any other rest state.
      const reason = error instanceof Error ? error.message : String(error ?? "pairing_code_request_failed");
      await this.sessionStore.save({
        status: "disconnected",
        retryable: true,
        lastDisconnectReason: reason,
      });
      await this.flushState();
    }
  }

  private async getAdapter(): Promise<WhatsAppBaileysAdapter> {
    if (this.adapter) {
      return this.adapter;
    }
    const baileysModule = await dynamicImport<Record<string, any>>("@whiskeysockets/baileys");
    const makeWASocket = typeof baileysModule.default === "function" ? baileysModule.default : baileysModule.makeWASocket;
    if (typeof makeWASocket !== "function") {
      throw new Error("Baileys socket factory is unavailable.");
    }
    const useMultiFileAuthState = baileysModule.useMultiFileAuthState;
    if (typeof useMultiFileAuthState !== "function") {
      throw new Error("Baileys multi-file auth state helper is unavailable.");
    }
    const Browsers = baileysModule.Browsers ?? {};
    const fetchLatestWaWebVersion = baileysModule.fetchLatestWaWebVersion;
    // Baileys serializes creds.json with its own Buffer<->base64 replacer/
    // reviver (BufferJSON: {type:"Buffer",data:"<base64>"}) -- plain
    // JSON.stringify/parse would silently mangle Buffer-typed key material
    // (noiseKey, signedIdentityKey, etc -- Node's default Buffer#toJSON()
    // produces a numeric-array shape Baileys' own reviver does not revive
    // back into a Buffer). Sourcing it from this SAME loaded module
    // guarantees our atomic writer's output is byte-for-byte what Baileys'
    // own reader expects. Falls back to plain JSON only in the
    // (theoretical) case a future Baileys release drops the export --
    // better a loud parse failure than silently skipping persistence.
    const bufferJson = baileysModule.BufferJSON as
      | { replacer: (key: string, value: unknown) => unknown; reviver: (key: string, value: unknown) => unknown }
      | undefined;
    const credsCodec: WhatsAppCredsCodec =
      bufferJson && typeof bufferJson.replacer === "function" && typeof bufferJson.reviver === "function"
        ? {
            stringify: (value: unknown) => JSON.stringify(value, bufferJson.replacer),
            parse: (raw: string) => JSON.parse(raw, bufferJson.reviver),
          }
        : DEFAULT_CREDS_CODEC;
    this.adapter = {
      loadAuthState: async (folder: string) => useMultiFileAuthState(path.resolve(folder)),
      createSocket: (config: Record<string, unknown>) => makeWASocket(config),
      disconnectReason: baileysModule.DisconnectReason ?? {},
      browserDescriptor: (appName: string) =>
        typeof Browsers.macOS === "function" ? Browsers.macOS(appName) : ["Empyralis", "Safari", "1.0.0"],
      fetchWaWebVersion: async () => {
        if (typeof fetchLatestWaWebVersion !== "function") {
          return undefined;
        }
        try {
          const result = await fetchLatestWaWebVersion();
          return Array.isArray(result?.version) ? (result.version as [number, number, number]) : undefined;
        } catch {
          return undefined;
        }
      },
      credsCodec,
      downloadMedia: async (rawMessage, ctx) => {
        const downloadMediaMessage = baileysModule.downloadMediaMessage;
        if (typeof downloadMediaMessage !== "function") {
          throw new Error("Baileys downloadMediaMessage is unavailable.");
        }
        return downloadMediaMessage(
          rawMessage,
          "buffer",
          {},
          ctx.reuploadRequest ? { reuploadRequest: ctx.reuploadRequest, logger: ctx.logger } : undefined,
        );
      },
    };
    return this.adapter;
  }

  private async handleConfigure(argumentsPayload: Record<string, unknown>): Promise<Record<string, unknown>> {
    const patch: { phoneNumber?: string; customPairingCode?: string } = {};
    if ("phone_number" in argumentsPayload) {
      const token = String(argumentsPayload.phone_number ?? "").trim();
      if (!token) {
        throw new Error("phone_number is required when provided.");
      }
      patch.phoneNumber = token;
    }
    if ("custom_pairing_code" in argumentsPayload) {
      const token = String(argumentsPayload.custom_pairing_code ?? "").trim();
      if (!token) {
        throw new Error("custom_pairing_code is required when provided.");
      }
      patch.customPairingCode = token;
    }
    // Unlike Telegram, WhatsApp's QR path needs no fields at all -- an empty
    // call is a valid "begin/retry" request, not an error. This closes the
    // gap that stranded the wizard after disconnect(): the runtime sat idle
    // forever with no UI-reachable way to try again short of restarting the
    // whole Gateway process.
    const storedConfig = Object.keys(patch).length > 0
      ? await this.configStore.patchWhatsAppConfig(patch)
      : await this.configStore.loadWhatsAppConfig();
    let reconnectRequested = false;
    const currentState = await this.sessionStore.load();
    if (this.started && currentState.status !== "connected") {
      reconnectRequested = true;
      await this.reconnectForConfigUpdate();
    } else {
      await this.flushState();
    }
    const nextState = await this.sessionStore.load();
    return {
      status: "updated",
      reconnect_requested: reconnectRequested,
      config: {
        has_phone_number: Boolean(storedConfig.phoneNumber),
      },
      state: {
        status: nextState.status,
        login_hint: nextState.loginHint,
        pairing_code: nextState.pairingCode,
      },
    };
  }

  private async reconnectForConfigUpdate(): Promise<void> {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    const socket = this.socket as unknown as {
      logout?: () => Promise<void> | void;
      end?: (error?: unknown) => void;
      ws?: { close?: () => void };
    } | null;
    try {
      await Promise.resolve(socket?.logout?.());
    } catch {
      // best effort
    }
    try {
      socket?.end?.();
    } catch {
      // best effort
    }
    try {
      socket?.ws?.close?.();
    } catch {
      // best effort
    }
    this.socket = null;
    this.authBundle = null;
    this.pairingCodeRequested = false;
    this.reconnectAttempts = 0;
    await this.connectSocket();
  }
}

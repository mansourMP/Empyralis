import { promises as fs } from "fs";
import path from "path";
import crypto from "crypto";
import pino from "pino";

import { GatewayStateDb } from "../../state/db";
import { redactCredentials } from "../foundation/credential-redactor";
import { DraftManager, normalizeChannelOutboundOperation } from "../foundation/draft-manager";
import type {
  GatewayChannelInboundPayload,
  GatewayChannelMediaKind,
  GatewayChannelOutboundPayload,
  GatewayRequestEnvelope,
  GatewayScope,
  GatewayToolInvokePayload,
} from "../../protocol/types";
import { buildTelegramConnectedState, buildTelegramPreflightState, loadTelegramLoginConfig, maskPhoneNumber, type TelegramLinkedAccount, type TelegramLoginConfig } from "./login";
import {
  mapTelegramInboundMessage,
  mapTelegramOutboundMediaItem,
  mapTelegramOutboundResult,
  type TelegramInboundMediaItem,
  type TelegramInboundMessage,
  type TelegramOutboundMediaItem,
} from "./message-mapper";
import {
  TELEGRAM_TYPING_KEEPALIVE_MS,
  TelegramOutboundStore,
  TelegramTypingKeepalive,
  type TelegramChatAction,
} from "./outbound";
import {
  DEFAULT_TELEGRAM_RECONNECT_POLICY,
  computeTelegramReconnectDelay,
  resolveTelegramReconnectState,
} from "./reconnect";
import {
  TELEGRAM_PERSONAL_CHANNEL_KEY,
  TELEGRAM_PERSONAL_PROVIDER,
  TelegramSessionStore,
} from "./session-store";
import { PersonalChannelConfigStore } from "../personal-config-store";
import type {
  PersonalChannelCapabilityManifest,
  PersonalChannelHealthSnapshot,
} from "../personal-runtime";

type DynamicImport = <T>(specifier: string) => Promise<T>;
const dynamicImport = new Function("specifier", "return import(specifier)") as DynamicImport;

export interface TelegramGatewayPublisher {
  publishEvent: (type: "channel.inbound", payload: GatewayChannelInboundPayload) => Promise<void>;
  publishStateUpdate: (payload: Record<string, unknown>) => Promise<void>;
}

export interface TelegramAdapterClient {
  setMessageHandler: (handler: (message: TelegramInboundMessage) => void | Promise<void>) => void;
  sendMessage: (
    remoteJid: string,
    text: string,
    replyToExternalMessageId?: string,
  ) => Promise<Record<string, unknown> | undefined>;
  /** Sends one media attachment (image/voice/audio/video/file). Optional so
   *  existing adapter mocks (and any future adapter that only ever supports
   *  text) keep compiling — sendFinalOutbound throws a clear error if the
   *  server asks for media and the connected adapter doesn't implement it. */
  sendMedia?: (
    remoteJid: string,
    media: TelegramOutboundMediaItem,
    replyToExternalMessageId?: string,
  ) => Promise<Record<string, unknown> | undefined>;
  sendChatAction?: (remoteJid: string, action: TelegramChatAction) => Promise<void> | void;
  disconnect?: () => Promise<void> | void;
  exportSessionString?: () => Promise<string> | string;
}

export interface TelegramRuntimeAdapter {
  requestCode?: (
    config: TelegramLoginConfig & { sessionString?: string },
  ) => Promise<{ phoneCodeHash: string; isCodeViaApp?: boolean; sessionString?: string }>;
  connect: (
    config: TelegramLoginConfig & { sessionString?: string },
  ) => Promise<{ client: TelegramAdapterClient; account?: TelegramLinkedAccount }>;
}

export interface TelegramRuntimeDependencies {
  publisher?: TelegramGatewayPublisher;
  adapter?: TelegramRuntimeAdapter;
}

const TELEGRAM_REDACT_STRING_KEYS = ["apiHash", "phoneNumber", "sessionString", "sessionToken"] as const;
const TELEGRAM_REDACT_OBJECT_KEYS = ["authState", "creds", "keys"] as const;

export function redactTelegramCredentials(state: Record<string, unknown>): Record<string, unknown> {
  return redactCredentials(state, TELEGRAM_REDACT_STRING_KEYS, TELEGRAM_REDACT_OBJECT_KEYS);
}

// ───────────────────────────────────────────────────────────────────────
// Inbound/outbound media (photo/voice/audio/video/document/sticker).
//
// Contract with the server: inbound media is downloaded via GramJS
// (client.downloadMedia) and written to disk under
// <gateway state dir>/telegram-media/; the published channel.inbound
// event's message.media[].media_id is that file's path RELATIVE to the
// gateway state dir root (see state/db.ts's GatewayStateDb.rootDirPath()).
// There is no HTTP media-fetch route on the gateway for this — the gateway
// and the server share a filesystem / state-dir mount on a paired Agent
// Computer box, so the server resolves media_id by joining it onto the
// same state dir root it already has. See GatewayChannelInboundMediaItem's
// doc comment in protocol/types.ts for the wire-shape side of this.
//
// Outbound mirrors this: GatewayChannelOutboundMediaItem.source_path is a
// path the gateway process reads directly (same shared-filesystem
// assumption); source_url is handed to GramJS as-is and it fetches/streams
// the file via Telegram's own servers.
// ───────────────────────────────────────────────────────────────────────

/** Subdirectory of the gateway state dir that inbound Telegram media is
 *  written under — also the leading path segment of every media_id. */
export const TELEGRAM_MEDIA_SUBDIR = "telegram-media";

/** ~25MB, matching the task's inbound size cap. Checked against the
 *  declared size BEFORE downloading (skips the download entirely for an
 *  oversized file) and again against the actual downloaded buffer length
 *  (defense in depth against a missing/lying declared size). Overridable
 *  for ops/testing via EMPYRALIS_TELEGRAM_MAX_MEDIA_BYTES — read directly
 *  from process.env, the same pattern EMPYRALIS_TELEGRAM_PATH already uses
 *  in connectClientInternal() below, rather than threaded through
 *  GatewayConfig. */
export function resolveTelegramMaxMediaBytes(env: NodeJS.ProcessEnv = process.env): number {
  const raw = Number.parseInt(String(env.EMPYRALIS_TELEGRAM_MAX_MEDIA_BYTES || "").trim(), 10);
  return Number.isFinite(raw) && raw > 0 ? raw : 25 * 1024 * 1024;
}

const TELEGRAM_MIME_EXTENSIONS: Readonly<Record<string, string>> = {
  "image/jpeg": ".jpg",
  "image/png": ".png",
  "image/webp": ".webp",
  "image/gif": ".gif",
  "image/heic": ".heic",
  "image/heif": ".heif",
  "video/mp4": ".mp4",
  "video/webm": ".webm",
  "video/quicktime": ".mov",
  "audio/ogg": ".ogg",
  "audio/mpeg": ".mp3",
  "audio/mp4": ".m4a",
  "audio/x-m4a": ".m4a",
  "audio/wav": ".wav",
  "audio/x-wav": ".wav",
  "application/pdf": ".pdf",
  "application/zip": ".zip",
};

/** Picks a filesystem extension for a downloaded media item: a known-mime
 *  lookup first, then whatever extension the original filename already
 *  had, then the mime subtype itself, finally a generic fallback. Exported
 *  for direct unit testing. */
export function extensionForTelegramMime(mimeType: string | undefined, fallbackName?: string): string {
  const mime = String(mimeType || "").trim().toLowerCase();
  if (mime && TELEGRAM_MIME_EXTENSIONS[mime]) {
    return TELEGRAM_MIME_EXTENSIONS[mime];
  }
  const name = String(fallbackName || "").trim();
  const dot = name.lastIndexOf(".");
  if (dot > 0 && dot < name.length - 1) {
    return name.slice(dot).toLowerCase();
  }
  const subtype = mime.split("/")[1]?.split(";")[0]?.trim();
  if (subtype) {
    return `.${subtype.replace(/[^a-z0-9]/gi, "")}`;
  }
  return ".bin";
}

/** Structural mirror of GramJS's Api.Message.file getter
 *  (node_modules/telegram/tl/custom/file.js) — deliberately loose/minimal
 *  rather than importing GramJS's real class, so tests can pass plain mock
 *  objects instead of constructing real Api.* instances. */
export interface TelegramRawMediaFile {
  name?: string;
  mimeType?: string;
  size?: number;
  duration?: number;
}

/** Structural mirror of the subset of GramJS's Api.Message convenience
 *  getters (node_modules/telegram/tl/custom/message.js) used to classify
 *  inbound media. */
export interface TelegramRawMediaMessage {
  photo?: unknown;
  voice?: unknown;
  video?: unknown;
  videoNote?: unknown;
  gif?: unknown;
  audio?: unknown;
  sticker?: unknown;
  document?: unknown;
  file?: TelegramRawMediaFile;
}

export interface TelegramMediaClassification {
  kind: GatewayChannelMediaKind;
  file: TelegramRawMediaFile;
}

/** Classifies a raw GramJS message's media (if any) into one of the
 *  contract's five kinds. Order matters: voice notes, round videos, GIFs,
 *  and stickers are all technically GramJS "documents" too, so the more
 *  specific getters must be checked before falling back to the generic
 *  `.document`. Pure — exported for direct unit testing without touching
 *  GramJS at all. */
export function classifyTelegramInboundMedia(
  rawMessage: TelegramRawMediaMessage | null | undefined,
): TelegramMediaClassification | null {
  const file = rawMessage?.file;
  if (!rawMessage || !file) {
    return null;
  }
  if (rawMessage.photo) {
    return { kind: "image", file };
  }
  if (rawMessage.voice) {
    return { kind: "voice", file };
  }
  if (rawMessage.videoNote || rawMessage.video || rawMessage.gif) {
    return { kind: "video", file };
  }
  if (rawMessage.audio) {
    return { kind: "audio", file };
  }
  if (rawMessage.sticker) {
    // Static stickers are webp images; animated/video stickers still report
    // their real mime type (e.g. video/webm) via file.mimeType regardless —
    // the contract has no dedicated "sticker" kind, so this buckets with
    // images rather than dropping them.
    return { kind: "image", file };
  }
  if (rawMessage.document) {
    return { kind: "file", file };
  }
  return null;
}

/** Structural mirror of the one GramJS TelegramClient method this module
 *  calls — again deliberately minimal so tests can mock it without a real
 *  GramJS client. */
export interface TelegramMediaDownloader {
  downloadMedia: (messageOrMedia: unknown) => Promise<Buffer | string | undefined>;
}

/** Downloads one classified media item via GramJS (client.downloadMedia —
 *  called with no `thumb` argument, which is what makes GramJS download the
 *  largest photo variant rather than a thumbnail; see
 *  node_modules/telegram/client/downloads.js's getThumb(), which sorts
 *  ascending and pops the last/largest entry when thumb is undefined) and
 *  persists it under <stateDir>/telegram-media/. Returns null (never
 *  throws) on any failure — an unavailable/oversized/corrupt attachment
 *  should never sink the whole inbound message when there's still a
 *  caption (or other attachments) worth delivering. */
export async function downloadAndStoreTelegramMedia(params: {
  client: TelegramMediaDownloader;
  rawMessage: unknown;
  classification: TelegramMediaClassification;
  stateDir: string;
  externalMessageId: string;
  maxBytes?: number;
  logger?: { warn?: (...args: any[]) => unknown };
}): Promise<TelegramInboundMediaItem | null> {
  const { client, rawMessage, classification, stateDir, externalMessageId, logger } = params;
  const maxBytes = params.maxBytes ?? resolveTelegramMaxMediaBytes();
  const declaredSize = Number(classification.file.size);
  if (Number.isFinite(declaredSize) && declaredSize > maxBytes) {
    logger?.warn?.(
      { externalMessageId, declaredSize, maxBytes },
      "telegram inbound media exceeds size cap — skipping download",
    );
    return null;
  }
  let downloaded: Buffer | string | undefined;
  try {
    downloaded = await client.downloadMedia(rawMessage);
  } catch (error) {
    logger?.warn?.({ externalMessageId, error }, "telegram inbound media download failed");
    return null;
  }
  // GramJS's downloadMedia only returns a string when called with an
  // `outputFile` path argument (see downloads.d.ts) — we never pass one, so
  // a string here means nothing usable was downloaded.
  if (!downloaded || typeof downloaded === "string" || downloaded.length === 0) {
    return null;
  }
  if (downloaded.length > maxBytes) {
    logger?.warn?.(
      { externalMessageId, size: downloaded.length, maxBytes },
      "telegram inbound media exceeded size cap after download — discarding",
    );
    return null;
  }
  const mimeType = String(classification.file.mimeType || "").trim() || "application/octet-stream";
  const filename = String(classification.file.name || "").trim() || undefined;
  const ext = extensionForTelegramMime(mimeType, filename);
  const baseName = `${externalMessageId}-${crypto.randomUUID()}${ext}`;
  const mediaId = `${TELEGRAM_MEDIA_SUBDIR}/${baseName}`;
  const absolutePath = path.join(stateDir, TELEGRAM_MEDIA_SUBDIR, baseName);
  await fs.mkdir(path.dirname(absolutePath), { recursive: true });
  await fs.writeFile(absolutePath, downloaded);
  try {
    await fs.chmod(absolutePath, 0o600);
  } catch {
    // Best-effort — OK on filesystems that don't support unix permissions.
  }
  const durationSec = Number.isFinite(Number(classification.file.duration))
    ? Number(classification.file.duration)
    : undefined;
  return {
    kind: classification.kind,
    mediaId,
    mimeType,
    filename,
    sizeBytes: downloaded.length,
    durationSec,
  };
}

// GramJS's own update loop — and half a dozen other internal call sites
// (node_modules/telegram/client/{updates,downloads,users,auth,messageParse,
// dialogs}.js, node_modules/telegram/events/{NewMessage,Album}.js) — call
// exactly six methods on `client._log`: canSend(level), warn(message),
// info(message), debug(message), error(message), and (via
// client.setLogLevel()) setLevel(level). GramJS assigns `this._log` exactly
// once, inside TelegramBaseClient's constructor
// (node_modules/telegram/client/telegramBaseClient.js:63-68 —
// `clientParams.baseLogger` if truthy, else `new extensions/Logger()`), and
// never reassigns it afterwards anywhere in the package. So a guard that
// runs once, synchronously, immediately after `new TelegramClient(...)` —
// before `connect()`/`start()` is ever called, i.e. before the update loop
// can tick even once — covers that client's *entire* lifetime, including
// every reconnect. There is no later point where GramJS swaps the logger
// out from under us, so there is no "point of use" inside library code we
// would need to guard instead.
//
// Why the existing fix (newGramLogger(), below) isn't that guarantee on its
// own: it constructs a real GramJS Logger and hands it in as `baseLogger`,
// trusting that `typeof telegram.Logger === "function"` being true means
// the *instance* it just built behaves like GramJS's own default Logger.
// That trust is exactly the gap — it verifies the class resolved, never the
// object GramJS actually ends up storing in `client._log`. It also
// silently falls back to `undefined` (letting GramJS build its own
// default) the moment `telegram.Logger` isn't a constructor, with no
// verification that the fallback is any better. That matters here because
// package.json pins GramJS via `"telegram": "^2.0.0"` while
// scripts/install-agent-computer.sh provisions boxes with `npm install`
// (not `npm ci`), so different boxes can silently resolve different 2.x.y
// GramJS builds over time as new versions publish — consistent with this
// bug report citing GramJS "2.26.21" while the committed
// package-lock.json pins "2.26.22" (two labels for builds that don't even
// agree with themselves: GramJS's own internal Version_1.version string
// reads "2.26.21" on the exact tarball npm calls "2.26.22"). If some
// resolved build ever renames/removes the top-level `Logger` export, wraps
// it differently, or reshapes what `_log` needs to expose, the `typeof`
// check degrades silently instead of failing loudly — and we'd have no way
// of knowing until the update loop crashed again.
//
// ensureGramLoggerShape() removes that trust entirely: it inspects the
// actual object GramJS ended up with in `client._log` — not the class it
// was built from — and repairs it if anything required is missing or not a
// function. That stays correct regardless of *why* `_log` might be broken:
// a module-resolution mismatch, GramJS version drift, a future GramJS
// release reshaping Logger, or a future code path that constructs a
// TelegramClient without going through newGramLogger() at all.
const GRAM_LOGGER_METHODS = ["canSend", "warn", "info", "debug", "error", "setLevel"] as const;

function hasWorkingGramLogger(candidate: unknown): candidate is Record<string, (...args: unknown[]) => unknown> {
  if (!candidate || typeof candidate !== "object") {
    return false;
  }
  const record = candidate as Record<string, unknown>;
  return GRAM_LOGGER_METHODS.every((method) => typeof record[method] === "function");
}

// A minimal, dependency-free stand-in for GramJS's extensions/Logger.Logger,
// used only if neither the client's own `_log` nor a freshly built GramJS
// Logger actually has the required shape. canSend() always returns true so
// genuine errors stay visible in the gateway's logs instead of being
// silently swallowed — this guard exists to stop a missing logger method
// from crashing the process, not to hide the errors it was trying to report.
function createGramLoggerShim(): Record<string, (...args: unknown[]) => unknown> {
  const emit = (level: string, message: unknown): void => {
    console.log(`[telegram:${level}]`, message);
  };
  return {
    canSend: () => true,
    warn: (message: unknown) => emit("warn", message),
    info: (message: unknown) => emit("info", message),
    debug: (message: unknown) => emit("debug", message),
    error: (message: unknown) => emit("error", message),
    setLevel: () => undefined,
    log: (level: unknown, message: unknown) => emit(String(level ?? "log"), message),
  };
}

/**
 * Guarantees `client._log` has a working canSend/warn/info/debug/error/
 * setLevel — installing a real rebuilt GramJS Logger if possible, else a
 * minimal shim — before the client is ever connected. See the block
 * comment above for why running this once, right after construction, is
 * sufficient for the client's entire lifetime. Exported for direct unit
 * testing.
 */
export function ensureGramLoggerShape(client: { _log?: unknown }, buildGramLogger: () => unknown): void {
  if (hasWorkingGramLogger(client._log)) {
    return;
  }
  const rebuilt = buildGramLogger();
  client._log = hasWorkingGramLogger(rebuilt) ? rebuilt : createGramLoggerShim();
}

// TTL for the typing session started the instant an inbound message is
// admitted (see TelegramPersonalRuntime.startTypingForChat), which needs to
// stay alive for the length of a full agent turn — not just the one
// sendMessage call TELEGRAM_TYPING_MAX_TTL_MS (60s, imported from
// ./outbound) is sized for. Sized to safely exceed this platform's own
// worst-case turn budget: server_modules/runtime_config.py's
// ORION_RUN_TIMEOUT_SECONDS defaults to 300s, and
// ORION_TELEGRAM_AUTOPILOT_RUN_TIMEOUT_SECONDS defaults to 180s.
const TELEGRAM_INBOUND_TYPING_MAX_TTL_MS = 5 * 60_000;

export class TelegramPersonalRuntime {
  private readonly configStore: PersonalChannelConfigStore;
  private readonly sessionStore: TelegramSessionStore;
  private readonly outboundStore: TelegramOutboundStore;
  private readonly logger = pino({ level: "silent" });
  private publisher?: TelegramGatewayPublisher;
  private adapter?: TelegramRuntimeAdapter;
  private client: TelegramAdapterClient | null = null;
  private started = false;
  private reconnectTimer: NodeJS.Timeout | null = null;
  private connectPromise: Promise<void> | null = null;
  private reconnectAttempts = 0;
  private readonly draftManager = new DraftManager();
  /**
   * Typing sessions started on inbound receipt (see startTypingForChat),
   * keyed by remoteJid, waiting to be claimed and stopped by whichever
   * sendFinalOutbound() call eventually dispatches that chat's reply.
   */
  private readonly activeTyping = new Map<
    string,
    { typing: TelegramTypingKeepalive; startedAt: number; client: TelegramAdapterClient }
  >();

  constructor(
    private readonly db: GatewayStateDb,
    dependencies: TelegramRuntimeDependencies = {},
  ) {
    this.configStore = new PersonalChannelConfigStore(db);
    this.sessionStore = new TelegramSessionStore(db);
    this.outboundStore = new TelegramOutboundStore(db);
    this.publisher = dependencies.publisher;
    this.adapter = dependencies.adapter;
  }

  requestedCapabilities(): string[] {
    return [
      "channel.telegram.personal",
      "channel.telegram.personal.inbound",
      "channel.telegram.personal.outbound",
      "channel.telegram.personal.configure",
      "channel.telegram.personal.disconnect",
    ];
  }

  supportsCapability(capabilityId: string): boolean {
    const id = String(capabilityId || "").trim();
    return id === "channel.telegram.personal.configure" || id === "channel.telegram.personal.disconnect";
  }

  async handleCapabilityInvoke(
    frame: GatewayRequestEnvelope<GatewayToolInvokePayload>,
  ): Promise<Record<string, unknown>> {
    const payload = frame.payload;
    const capabilityId = String(payload.capability_id || "").trim();
    if (capabilityId === "channel.telegram.personal.disconnect") {
      return this.handleDisconnect();
    }
    if (capabilityId !== "channel.telegram.personal.configure") {
      throw new Error(`Unsupported Telegram personal capability: ${capabilityId || "unknown"}`);
    }
    const argumentsPayload =
      payload.arguments && typeof payload.arguments === "object" && !Array.isArray(payload.arguments)
        ? (payload.arguments as Record<string, unknown>)
        : {};
    return this.handleConfigure(argumentsPayload);
  }

  /** Full reset: tears down any live/in-flight connection and clears every
   *  persisted trace of the previous attempt (session string, pending
   *  login, and the full config — api_id/api_hash/phone_number included),
   *  so a subsequent configure() starts genuinely fresh rather than
   *  inheriting a stuck or stale pending-login. This is the exact gap that
   *  let a bad first attempt jam a phone number in a `code_required` ->
   *  PHONE_CODE_INVALID -> `code_required` retry loop with no way out
   *  short of an operator hand-editing Gateway state files. */
  private async handleDisconnect(): Promise<Record<string, unknown>> {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.reconnectAttempts = 0;
    if (this.client) {
      try {
        await Promise.resolve(this.client.disconnect?.());
      } catch {
        // Best-effort — the session is being wiped regardless.
      }
    }
    this.client = null;
    await this.sessionStore.clearSessionString();
    await this.sessionStore.clearPendingLogin();
    await this.configStore.clearTelegramConfig();
    await this.sessionStore.save({
      status: "idle",
      loginHint: undefined,
      linkedUserId: undefined,
      linkedUsername: undefined,
      linkedPhone: undefined,
      linkedName: undefined,
      connectedAt: undefined,
      codeRequestedAt: undefined,
      retryable: true,
      lastDisconnectReason: undefined,
      lastDisconnectCode: undefined,
    });
    await this.flushState();
    return { status: "disconnected", channel_key: TELEGRAM_PERSONAL_CHANNEL_KEY };
  }

  supportsChannel(channelKey: string): boolean {
    return String(channelKey || "").trim() === TELEGRAM_PERSONAL_CHANNEL_KEY;
  }

  getManifest(): PersonalChannelCapabilityManifest {
    return {
      channelKey: TELEGRAM_PERSONAL_CHANNEL_KEY,
      label: "Telegram Personal",
      provider: TELEGRAM_PERSONAL_PROVIDER,
      runtimeLane: "personal_gateway",
      stage: "live",
      status: "live",
      liveCapable: true,
      requiresAgentComputer: true,
      sessionOwner: "paired_gateway",
      setupKind: "phone_login",
      capabilities: ["configure", "inbound", "outbound", "text", "groups"],
      chatTypes: ["dm", "group"],
      media: { text: true, images: true, files: true, reactions: false, voice: true },
      safety: {
        ownerPairingRequired: true,
        allowlistRequired: false,
        studioBusinessAllowed: false,
        customerPublicSendAllowed: false,
      },
      notes: ["Owner/private channel for Sage through Agent Computer. Not a Studio business channel."],
    };
  }

  async getHealthSnapshot(): Promise<PersonalChannelHealthSnapshot> {
    const snapshot = await this.sessionStore.load();
    return {
      channelKey: TELEGRAM_PERSONAL_CHANNEL_KEY,
      provider: TELEGRAM_PERSONAL_PROVIDER,
      status: snapshot.status,
      running: this.started,
      connected: Boolean(this.client) && snapshot.status === "connected",
      reconnectAttempts: this.reconnectAttempts,
      lastEventAt: snapshot.updatedAt,
      lastError: snapshot.lastDisconnectReason,
      issues: snapshot.status === "connected" ? [] : ["telegram_personal_not_connected"],
    };
  }

  setPublisher(publisher: TelegramGatewayPublisher): void {
    this.publisher = publisher;
  }

  async start(): Promise<void> {
    if (this.started) {
      return;
    }
    this.started = true;
    await this.connectClient();
  }

  async stop(): Promise<void> {
    this.started = false;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    await Promise.resolve(this.client?.disconnect?.());
    this.client = null;
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
    if (!this.client) {
      throw new Error("Telegram personal runtime is not connected.");
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
        channelKey: TELEGRAM_PERSONAL_CHANNEL_KEY,
        provider: TELEGRAM_PERSONAL_PROVIDER,
      },
      (augmented) => this.sendFinalOutbound(augmented as unknown as GatewayChannelOutboundPayload),
    );
  }

  private async sendFinalOutbound(payload: GatewayChannelOutboundPayload): Promise<Record<string, unknown>> {
    const client = this.client;
    if (!client) {
      throw new Error("Telegram personal runtime is not connected.");
    }
    const idempotencyKey = String(payload.idempotency_key || "").trim();
    const remoteJid = String(payload.remote_jid || "").trim();
    const text = String(payload.text || "").trim();
    const rawMediaItems = Array.isArray(payload.media) ? payload.media.filter(Boolean) : [];
    if (!idempotencyKey || !remoteJid || (!text && rawMediaItems.length === 0)) {
      throw new Error("channel.outbound requires idempotency_key, remote_jid, and text or media.");
    }
    if (text.length > 4096) {
      throw new Error(
        `Telegram message exceeds maximum length of 4096 characters (got ${text.length}).`,
      );
    }
    // Validate/normalize every media item up front — a malformed item
    // (unknown kind, missing source) rejects the whole call before any send
    // is attempted, instead of partially delivering and then throwing.
    const mediaItems = rawMediaItems.map((item) => mapTelegramOutboundMediaItem(item));
    if (mediaItems.length > 0 && typeof client.sendMedia !== "function") {
      throw new Error("Telegram adapter does not support sending media.");
    }
    const now = new Date().toISOString();
    const existing = await this.outboundStore.beginSend(
      idempotencyKey,
      {
        idempotencyKey,
        remoteJid,
        text,
        replyToExternalMessageId: String(payload.reply_to_external_message_id || "").trim() || undefined,
        status: "pending" as const,
        attemptCount: 0,
        createdAt: now,
        updatedAt: now,
      },
    );
    if (existing.status === "delivered") {
      return {
        channel_key: TELEGRAM_PERSONAL_CHANNEL_KEY,
        provider: TELEGRAM_PERSONAL_PROVIDER,
        idempotency_key: existing.idempotencyKey,
        external_message_id: existing.externalMessageId,
        remote_jid: existing.remoteJid,
        text: existing.text,
        delivered: true,
      };
    }
    await this.outboundStore.markAttemptStarted(idempotencyKey);
    // Reuse the typing session started when the inbound message that
    // triggered this reply was admitted (see startTypingForChat) so the
    // indicator has been running since the agent started "thinking"
    // instead of blipping on for just this network call. Falls back to a
    // fresh session — the old behavior — when none is active for this
    // chat (a proactive send, or the inbound session went stale).
    const claimedTyping = this.claimTypingForChat(remoteJid);
    const typing = claimedTyping ?? new TelegramTypingKeepalive(
      client.sendChatAction
        ? (action) => client.sendChatAction?.(remoteJid, action)
        : undefined,
    );
    if (!claimedTyping) {
      await typing.start();
    }
    try {
      const replyTo = String(payload.reply_to_external_message_id || "").trim() || undefined;
      // Media first, then text — each media item carries its own optional
      // caption (contract: {kind, source_path|source_url, mime_type,
      // caption?, as_voice?}), so `text` here is a distinct, additional
      // message rather than a caption double-send. Most dispatches will set
      // exactly one of text/media; sending both is supported, not assumed.
      const mediaResults: Array<{ kind: GatewayChannelMediaKind; external_message_id?: string }> = [];
      let response: Record<string, unknown> | undefined;
      for (const item of mediaItems) {
        // Guarded above (mediaItems.length > 0 implies sendMedia exists);
        // non-null assertion documents that instead of re-checking per item.
        response = await client.sendMedia!(remoteJid, item, replyTo);
        mediaResults.push({
          kind: item.kind,
          external_message_id: String(response?.externalMessageId ?? "").trim() || undefined,
        });
      }
      if (text) {
        response = await client.sendMessage(remoteJid, text, replyTo);
      }
      const mapped = mapTelegramOutboundResult(
        {
          idempotencyKey,
          remoteJid,
          text,
          replyToExternalMessageId: replyTo,
          media: mediaResults.length > 0 ? mediaResults : undefined,
        },
        response,
      );
      await this.outboundStore.markDelivered(
        idempotencyKey,
        String(mapped.external_message_id || "").trim() || undefined,
      );
      return mapped;
    } finally {
      await typing.stop();
    }
  }

  private async connectClient(): Promise<void> {
    if (this.connectPromise) {
      return this.connectPromise;
    }
    const task = this.connectClientInternal().finally(() => {
      if (this.connectPromise === task) {
        this.connectPromise = null;
      }
    });
    this.connectPromise = task;
    return task;
  }

  private async connectClientInternal(): Promise<void> {
    // ── Phase M: single-path gating ────────────────────────────────────
    // EMPYRALIS_TELEGRAM_PATH: "gateway" (default) | "csm" | "both"
    // When "csm", the Cloud Session Manager handles Telegram; Gateway skips.
    const telegramPath = (process.env.EMPYRALIS_TELEGRAM_PATH || "gateway").trim().toLowerCase();
    if (telegramPath === "csm") {
      this.logger?.info?.("telegram path=csm — Gateway GramJS listener disabled");
      await this.sessionStore.save({
        status: "disabled",
        loginHint: "telegram_path_csm",
        retryable: false,
        lastDisconnectReason: "Telegram path set to CSM; Gateway listener disabled by EMPYRALIS_TELEGRAM_PATH=csm",
      });
      await this.flushState();
      return;
    }
    // "gateway" or "both" — proceed normally
    this.logger?.info?.({ telegramPath }, "telegram Gateway listener starting");

    await this.sessionStore.ensureRuntimeDir();
    const loginConfig = loadTelegramLoginConfig();
    const persistedConfig = await this.configStore.loadTelegramConfig();
    const persistedSession = await this.sessionStore.loadSessionString();
    const pendingLogin = await this.sessionStore.loadPendingLogin();
    const pendingPhoneNumber = String(pendingLogin.phoneNumber || "").trim();
    const resolvedPhoneNumber = String(persistedConfig.phoneNumber ?? loginConfig.phoneNumber ?? "").trim();
    const pendingMatchesPhone = Boolean(
      pendingPhoneNumber
      && resolvedPhoneNumber
      && pendingPhoneNumber === resolvedPhoneNumber,
    );
    const resolvedConfig: TelegramLoginConfig & { sessionString?: string } = {
      ...loginConfig,
      apiId: persistedConfig.apiId ?? loginConfig.apiId,
      apiHash: persistedConfig.apiHash ?? loginConfig.apiHash,
      phoneNumber: resolvedPhoneNumber || undefined,
      phoneCodeHash: pendingMatchesPhone ? pendingLogin.phoneCodeHash : undefined,
      isCodeViaApp: pendingMatchesPhone ? pendingLogin.isCodeViaApp : undefined,
      loginCode: persistedConfig.loginCode ?? loginConfig.loginCode,
      password: persistedConfig.password ?? loginConfig.password,
      sessionString: persistedSession || (pendingMatchesPhone ? pendingLogin.sessionString : undefined) || loginConfig.sessionString,
    };
    const preflight = buildTelegramPreflightState(resolvedConfig);
    if (preflight) {
      await this.sessionStore.save({
        ...preflight,
        codeRequestedAt: undefined,
      });
      await this.flushState();
      return;
    }

    if (!resolvedConfig.sessionString && !resolvedConfig.phoneCodeHash && !resolvedConfig.loginCode) {
      const adapter = await this.getAdapter();
      if (typeof adapter.requestCode !== "function") {
        throw new Error("telegram_code_request_not_supported");
      }
      const codeRequest = await adapter.requestCode(resolvedConfig);
      const requestedAt = new Date().toISOString();
      await this.sessionStore.savePendingLogin({
        phoneNumber: String(resolvedConfig.phoneNumber || "").trim(),
        phoneCodeHash: String(codeRequest.phoneCodeHash || "").trim(),
        isCodeViaApp: Boolean(codeRequest.isCodeViaApp),
        sessionString: String(codeRequest.sessionString || "").trim() || undefined,
        codeRequestedAt: requestedAt,
      });
      await this.sessionStore.save({
        status: "code_required",
        loginHint: maskPhoneNumber(resolvedConfig.phoneNumber) || "login_code_required",
        codeRequestedAt: requestedAt,
        retryable: false,
        lastDisconnectReason: undefined,
        lastDisconnectCode: undefined,
      });
      await this.flushState();
      return;
    }

    if (resolvedConfig.phoneCodeHash && !resolvedConfig.loginCode) {
      await this.sessionStore.save({
        status: "code_required",
        loginHint: maskPhoneNumber(resolvedConfig.phoneNumber) || "login_code_required",
        codeRequestedAt: String(pendingLogin.codeRequestedAt || "").trim() || new Date().toISOString(),
        retryable: false,
      });
      await this.flushState();
      return;
    }

    await this.sessionStore.save({
      status: "connecting",
        loginHint: undefined,
        codeRequestedAt: undefined,
        retryable: true,
      });
    await this.flushState();

    try {
      const adapter = await this.getAdapter();
      const { client, account } = await adapter.connect(resolvedConfig);
      this.client = client;
      this.client.setMessageHandler((message) => {
        void this.handleInboundMessage(message);
      });
      const exportedSession = await Promise.resolve(this.client.exportSessionString?.());
      if (exportedSession) {
        await this.sessionStore.saveSessionString(exportedSession);
      }
      await this.sessionStore.clearPendingLogin();
      this.reconnectAttempts = 0;
      await this.configStore.clearTelegramSecrets();
      await this.sessionStore.save(buildTelegramConnectedState(account || {}));
      await this.flushState();
    } catch (error) {
      const reconnectState = resolveTelegramReconnectState(error);
      if (!reconnectState.shouldReconnect) {
        await this.sessionStore.clearSessionString();
      }
      const isRejectedCode = reconnectState.loginHint === "phone_code_invalid" || reconnectState.loginHint === "phone_code_expired";
      if (isRejectedCode) {
        // The code (and the phoneCodeHash it was checked against) is done —
        // clear both so the NEXT connectClientInternal() pass falls through
        // to "no code on file" and requests a fresh one, instead of
        // resolvedConfig picking the same rejected loginCode back up from
        // configStore and failing identically forever (the original bug).
        await this.sessionStore.clearPendingLogin();
        await this.configStore.clearTelegramSecrets();
      }
      await this.sessionStore.save({
        status: reconnectState.status,
        loginHint: reconnectState.loginHint,
        codeRequestedAt: reconnectState.status === "code_required" ? new Date().toISOString() : undefined,
        retryable: reconnectState.shouldReconnect,
        lastDisconnectReason: reconnectState.reason,
        lastDisconnectCode: reconnectState.statusCode,
      });
      await this.flushState();
      if (reconnectState.shouldReconnect && this.started) {
        this.scheduleReconnect();
      }
    }
  }

  private async handleInboundMessage(message: TelegramInboundMessage): Promise<void> {
    const mapped = mapTelegramInboundMessage(message);
    if (!mapped || mapped.message.from_me) {
      return;
    }
    this.startTypingForChat(mapped.message.remote_jid);
    await this.publishInbound(mapped);
  }

  private async publishInbound(payload: GatewayChannelInboundPayload): Promise<void> {
    await this.publisher?.publishEvent("channel.inbound", payload);
  }

  /**
   * Starts a typing keepalive the instant an inbound message is admitted,
   * so the indicator covers the LLM "thinking" time (which happens
   * upstream, before the Gateway is ever asked to send a reply) instead of
   * blipping on for only the fraction of a second around the final
   * sendMessage call. claimTypingForChat() hands this same session to
   * sendFinalOutbound() when the reply for this chat is ready to go out.
   *
   * Coalesced per chat and tagged with the client it was started against:
   * a second inbound message for the same remoteJid before the reply goes
   * out reuses the running loop instead of starting a duplicate one, and a
   * session left over from a since-replaced client (reconnect) is treated
   * as stale rather than reused.
   */
  private startTypingForChat(remoteJid: string): void {
    const jid = String(remoteJid || "").trim();
    if (!jid) {
      return;
    }
    const client = this.client;
    if (!client?.sendChatAction) {
      return;
    }
    const existing = this.activeTyping.get(jid);
    if (
      existing
      && existing.client === client
      && Date.now() - existing.startedAt < TELEGRAM_INBOUND_TYPING_MAX_TTL_MS
    ) {
      return;
    }
    const typing = new TelegramTypingKeepalive(
      (action) => client.sendChatAction?.(jid, action),
      TELEGRAM_TYPING_KEEPALIVE_MS,
      TELEGRAM_INBOUND_TYPING_MAX_TTL_MS,
    );
    this.activeTyping.set(jid, { typing, startedAt: Date.now(), client });
    void typing.start();
  }

  /**
   * Hands the caller the typing session startTypingForChat() started for
   * this chat, removing it from the map so it can't be claimed twice.
   * Returns undefined — leaving a stale entry's own loop to run down on
   * its own TTL — when there's nothing usable to claim: no session was
   * ever started (e.g. a proactive/unprompted send), it was started
   * against a client that's since been replaced by a reconnect, or it
   * already exceeded its TTL. The caller is expected to start a fresh one
   * itself in that case.
   */
  private claimTypingForChat(remoteJid: string): TelegramTypingKeepalive | undefined {
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
      existing.client !== this.client
      || Date.now() - existing.startedAt >= TELEGRAM_INBOUND_TYPING_MAX_TTL_MS
    ) {
      void existing.typing.stop();
      return undefined;
    }
    return existing.typing;
  }

  private async flushState(): Promise<void> {
    if (!this.publisher) {
      return;
    }
    const snapshot = await this.sessionStore.load();
    try {
      await this.publisher.publishStateUpdate(
        redactTelegramCredentials(this.sessionStore.toGatewayStatePayload(snapshot)),
      );
    } catch {
      return;
    }
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
    }
    const attempt = this.reconnectAttempts;
    if (attempt >= DEFAULT_TELEGRAM_RECONNECT_POLICY.maxAttempts) {
      void this.sessionStore.save({
        status: "disconnected",
        retryable: false,
        lastDisconnectReason: "telegram_reconnect_attempts_exhausted",
      }).then(() => this.flushState());
      return;
    }
    const delayMs = computeTelegramReconnectDelay(attempt);
    this.reconnectAttempts += 1;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      void this.connectClient();
    }, delayMs);
  }

  private async getAdapter(): Promise<TelegramRuntimeAdapter> {
    if (this.adapter) {
      return this.adapter;
    }
    const telegram = await dynamicImport<Record<string, unknown>>("telegram");
    // "telegram/sessions" is a bare directory import — resolves fine under
    // CommonJS (implicit index) but fails under native ESM resolution
    // (no exports map in this package, and sessions/ has no index.js of its
    // own). Import the specific file that actually defines StringSession.
    const sessions = await dynamicImport<Record<string, unknown>>("telegram/sessions/StringSession.js");
    const TelegramClient = telegram.TelegramClient as new (...args: unknown[]) => any;
    const NewMessage = telegram.NewMessage as new (...args: unknown[]) => any;
    const StringSession = sessions.StringSession as new (value: string) => any;
    if (!TelegramClient || !StringSession) {
      throw new Error("telegram_package_missing");
    }

    // Best-effort first attempt: hand GramJS a real GramJS Logger as
    // baseLogger instead of the gateway's own logger (which lacks
    // GramJS-specific methods like canSend and used to crash the update
    // loop). ensureGramLoggerShape() below — called right after each
    // `new TelegramClient(...)` — is what actually guarantees the result;
    // see the comment on ensureGramLoggerShape for why this typeof check
    // alone isn't sufficient.
    const GramLogger = telegram.Logger as (new (...args: unknown[]) => any) | undefined;
    const newGramLogger = (): unknown =>
      typeof GramLogger === "function" ? new GramLogger() : undefined;

    this.adapter = {
      requestCode: async (config) => {
        const sessionString = String(config.sessionString || "").trim();
        const apiId = Number(config.apiId);
        const apiHash = String(config.apiHash || "").trim();
        const phoneNumber = String(config.phoneNumber || "").trim();
        if (!apiId || !apiHash) {
          throw new Error("api_credentials_required");
        }
        if (!phoneNumber) {
          throw new Error("phone_number_required");
        }
        const client = new TelegramClient(
          new StringSession(sessionString),
          apiId,
          apiHash,
          { connectionRetries: 5, baseLogger: newGramLogger() },
        );
        ensureGramLoggerShape(client, newGramLogger);
        await client.connect();
        try {
          const code = await client.sendCode({ apiId, apiHash }, phoneNumber);
          const phoneCodeHash = String(code.phoneCodeHash || "").trim();
          if (!phoneCodeHash) {
            throw new Error("telegram_phone_code_hash_missing");
          }
          return {
            phoneCodeHash,
            isCodeViaApp: Boolean(code.isCodeViaApp),
            sessionString: String(client.session?.save?.() || "").trim() || undefined,
          };
        } finally {
          await client.disconnect();
        }
      },
      connect: async (config) => {
        const sessionString = String(config.sessionString || "").trim();
        const apiId = Number(config.apiId);
        const apiHash = String(config.apiHash || "").trim();
        const client = new TelegramClient(
          new StringSession(sessionString),
          apiId,
          apiHash,
          { connectionRetries: 5, baseLogger: newGramLogger() },
        );
        ensureGramLoggerShape(client, newGramLogger);
        const phoneNumber = String(config.phoneNumber || "").trim();
        const loginCode = String(config.loginCode || "").trim();
        const password = String(config.password || "").trim();
        if (config.phoneCodeHash) {
          if (!phoneNumber) {
            throw new Error("phone_number_required");
          }
          if (!loginCode) {
            throw new Error("login_code_required");
          }
          await client.connect();
          const Api = (telegram as { Api?: Record<string, any> }).Api;
          const signInClass = Api?.auth?.SignIn;
          if (!signInClass) {
            throw new Error("telegram_sign_in_unavailable");
          }
          try {
            const result = await client.invoke(
              new signInClass({
                phoneNumber,
                phoneCodeHash: String(config.phoneCodeHash || "").trim(),
                phoneCode: loginCode,
              }),
            );
            const signUpRequiredClass = Api?.auth?.AuthorizationSignUpRequired;
            if (signUpRequiredClass && result instanceof signUpRequiredClass) {
              throw new Error("telegram_registration_required");
            }
          } catch (error: any) {
            const token = String(error?.errorMessage || error?.message || error || "").trim().toUpperCase();
            if (token.includes("SESSION_PASSWORD_NEEDED")) {
              if (!password) {
                throw new Error("password_required");
              }
              await client.signInWithPassword(
                { apiId, apiHash },
                {
                  password: async () => password,
                  onError: (passwordError: unknown) => {
                    throw passwordError;
                  },
                },
              );
            } else {
              throw error;
            }
          }
        } else {
          await client.start({
            phoneNumber: async () => {
              if (!phoneNumber) {
                throw new Error("phone_number_required");
              }
              return phoneNumber;
            },
            phoneCode: async () => {
              if (!loginCode) {
                throw new Error("login_code_required");
              }
              return loginCode;
            },
            password: async () => {
              if (!password) {
                throw new Error("password_required");
              }
              return password;
            },
            onError: (error: unknown) => {
              throw error;
            },
          });
        }

        let messageHandler: (message: TelegramInboundMessage) => void | Promise<void> = () => undefined;
        client.addEventHandler(
          async (event: any) => {
            const rawMessage = event?.message;
            const text = String(rawMessage?.message ?? "").trim();
            const externalMessageId = String(rawMessage?.id ?? "").trim();
            if (!externalMessageId) {
              return;
            }
            // A media message with no caption has empty `text` — classify
            // media BEFORE the emptiness check below so a bare photo/voice/
            // video/etc. isn't silently dropped the way it would be if this
            // still required non-empty text unconditionally.
            const classification = classifyTelegramInboundMedia(rawMessage as TelegramRawMediaMessage | undefined);
            if (!text && !classification) {
              return;
            }
            const chat = typeof event?.getChat === "function" ? await event.getChat() : undefined;
            const sender = typeof event?.getSender === "function" ? await event.getSender() : undefined;
            const remoteJid = String(
              chat?.username
              ?? chat?.id
              ?? rawMessage?.peerId?.channelId
              ?? rawMessage?.peerId?.chatId
              ?? rawMessage?.peerId?.userId
              ?? "",
            ).trim();
            if (!remoteJid) {
              return;
            }
            const senderJid = String(sender?.username ?? sender?.id ?? remoteJid).trim() || undefined;
            const pushName = (
              [sender?.firstName, sender?.lastName].filter(Boolean).join(" ").trim()
              || String(sender?.username ?? chat?.title ?? "").trim()
              || undefined
            );
            let media: TelegramInboundMediaItem[] | undefined;
            if (classification) {
              // Never let a download/disk-write failure sink the whole
              // inbound message — downloadAndStoreTelegramMedia already
              // returns null (not a throw) for a failed/oversized download;
              // this catches the remaining failure mode (fs errors while
              // persisting the bytes) the same way.
              const stored = await downloadAndStoreTelegramMedia({
                client,
                rawMessage,
                classification,
                stateDir: this.db.rootDirPath(),
                externalMessageId,
                logger: this.logger,
              }).catch(() => null);
              if (stored) {
                media = [stored];
              }
            }
            await messageHandler({
              externalMessageId,
              remoteJid,
              senderJid,
              pushName,
              text,
              receivedAt: new Date(
                Number(rawMessage?.date ?? Math.floor(Date.now() / 1000)) * 1000,
              ).toISOString(),
              fromMe: Boolean(rawMessage?.out),
              media,
            });
          },
          NewMessage ? new NewMessage({ incoming: true }) : undefined,
        );
        const me = await client.getMe();
        const account: TelegramLinkedAccount = {
          userId: String(me?.id ?? "").trim() || undefined,
          username: String(me?.username ?? "").trim() || undefined,
          phone: String(me?.phone ?? "").trim() || undefined,
          name: (
            [me?.firstName, me?.lastName].filter(Boolean).join(" ").trim()
            || String(me?.username ?? "").trim()
            || undefined
          ),
        };
        return {
          account,
          client: {
            setMessageHandler: (handler) => {
              messageHandler = handler;
            },
            sendMessage: async (remoteJid, text, replyToExternalMessageId) => {
              const sendArgs: Record<string, unknown> = { message: text };
              const numericReplyTo = Number.parseInt(String(replyToExternalMessageId || "").trim(), 10);
              if (replyToExternalMessageId) {
                sendArgs.replyTo = Number.isFinite(numericReplyTo) ? numericReplyTo : replyToExternalMessageId;
              }
              const sent = await client.sendMessage(remoteJid, sendArgs);
              return {
                externalMessageId: String(sent?.id ?? "").trim() || undefined,
                remoteJid: String(sent?.chatId ?? remoteJid).trim() || remoteJid,
              };
            },
            sendMedia: async (remoteJid, media, replyToExternalMessageId) => {
              const fileArg = media.sourcePath || media.sourceUrl;
              if (!fileArg) {
                throw new Error("telegram_media_source_required");
              }
              const sendArgs: Record<string, unknown> = { file: fileArg };
              if (media.caption) {
                sendArgs.caption = media.caption;
              }
              const numericReplyTo = Number.parseInt(String(replyToExternalMessageId || "").trim(), 10);
              if (replyToExternalMessageId) {
                sendArgs.replyTo = Number.isFinite(numericReplyTo) ? numericReplyTo : replyToExternalMessageId;
              }
              if (media.kind === "voice" || media.asVoice) {
                // Telegram's compact round-waveform voice message.
                sendArgs.voiceNote = true;
              } else if (media.kind !== "image") {
                // video/audio/file -> Telegram document, per contract
                // ("video/file→sendFile as document"). GramJS infers the
                // photo-vs-document split for "image" from forceDocument
                // being left unset/false, same as an ordinary sendFile call.
                sendArgs.forceDocument = true;
              }
              const sent = await client.sendFile(remoteJid, sendArgs);
              return {
                externalMessageId: String(sent?.id ?? "").trim() || undefined,
                remoteJid: String(sent?.chatId ?? remoteJid).trim() || remoteJid,
              };
            },
            sendChatAction: async (remoteJid, action) => {
              const sendChatAction = (client as { sendChatAction?: (...args: unknown[]) => Promise<unknown> | unknown })
                .sendChatAction;
              if (typeof sendChatAction === "function") {
                await Promise.resolve(sendChatAction.call(client, remoteJid, action));
                return;
              }
              const Api = (telegram as { Api?: Record<string, any> }).Api;
              if (action !== "typing") {
                return;
              }
              const actionClass = Api?.SendMessageTypingAction;
              const requestClass = Api?.messages?.SetTyping;
              if (!actionClass || !requestClass || typeof client.invoke !== "function") {
                return;
              }
              await client.invoke(
                new requestClass({
                  peer: remoteJid,
                  action: new actionClass({}),
                }),
              );
            },
            disconnect: () => client.disconnect(),
            exportSessionString: () => client.session?.save?.(),
          },
        };
      },
    };
    return this.adapter;
  }

  private async handleConfigure(argumentsPayload: Record<string, unknown>): Promise<Record<string, unknown>> {
    const patch: {
      apiId?: number;
      apiHash?: string;
      phoneNumber?: string;
      loginCode?: string;
      password?: string;
    } = {};
    if ("api_id" in argumentsPayload) {
      const raw = String(argumentsPayload.api_id ?? "").trim();
      if (!raw) {
        throw new Error("api_id is required when provided.");
      }
      const parsed = Number.parseInt(raw, 10);
      if (!Number.isFinite(parsed) || parsed <= 0) {
        throw new Error("api_id must be a positive integer.");
      }
      patch.apiId = parsed;
    }
    if ("api_hash" in argumentsPayload) {
      const token = String(argumentsPayload.api_hash ?? "").trim();
      if (!token) {
        throw new Error("api_hash is required when provided.");
      }
      patch.apiHash = token;
    }
    if ("phone_number" in argumentsPayload) {
      const token = String(argumentsPayload.phone_number ?? "").trim();
      if (!token) {
        throw new Error("phone_number is required when provided.");
      }
      patch.phoneNumber = token;
    }
    if ("login_code" in argumentsPayload) {
      const token = String(argumentsPayload.login_code ?? "").trim();
      if (!token) {
        throw new Error("login_code is required when provided.");
      }
      patch.loginCode = token;
    }
    if ("password" in argumentsPayload) {
      const token = String(argumentsPayload.password ?? "").trim();
      if (!token) {
        throw new Error("password is required when provided.");
      }
      patch.password = token;
    }
    if (Object.keys(patch).length === 0) {
      throw new Error("At least one Telegram personal setup field is required.");
    }

    const storedConfig = await this.configStore.patchTelegramConfig(patch);
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
        has_api_id: Boolean(storedConfig.apiId),
        has_api_hash: Boolean(storedConfig.apiHash),
        has_phone_number: Boolean(storedConfig.phoneNumber),
      },
      state: {
        status: nextState.status,
        login_hint: nextState.loginHint,
        code_requested_at: nextState.codeRequestedAt,
      },
    };
  }

  private async reconnectForConfigUpdate(): Promise<void> {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.reconnectAttempts = 0;
    await Promise.resolve(this.client?.disconnect?.());
    this.client = null;
    await this.connectClient();
  }
}

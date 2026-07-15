import path from "path";
import pino from "pino";

import { GatewayStateDb } from "../../state/db";
import { redactCredentials } from "../foundation/credential-redactor";
import { DraftManager, normalizeChannelOutboundOperation } from "../foundation/draft-manager";
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
  mapWhatsAppInboundMessage,
  mapWhatsAppOutboundResult,
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
  sendPresenceUpdate?: (type: WhatsAppPresenceAction, jid: string) => Promise<void> | void;
  requestPairingCode?: (phoneNumber: string, customPairingCode?: string) => Promise<string>;
  user?: { id?: string; name?: string };
  /** Tells WhatsApp's servers to unlink this device (a real logout, not
   *  just closing our local socket) — used by disconnect/reset so a wiped
   *  local auth state doesn't leave a stale "linked device" entry on the
   *  account's own WhatsApp settings. */
  logout?: () => Promise<void>;
  end?: (error: Error | undefined) => void;
}

interface WhatsAppBaileysAdapter {
  loadAuthState: (folder: string) => Promise<BaileysAuthBundle>;
  createSocket: (config: Record<string, unknown>) => BaileysSocketLike;
  disconnectReason: { loggedOut?: number; restartRequired?: number };
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
  private pairingCodeRequested = false;
  private connectPromise: Promise<void> | null = null;
  private reconnectAttempts = 0;
  private sentMessageIds = new Set<string>();
  private readonly draftManager = new DraftManager();
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
      media: { text: true, images: false, files: false, reactions: false, voice: false },
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
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
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
    if (!idempotencyKey || !remoteJid || !text) {
      throw new Error("channel.outbound requires idempotency_key, remote_jid, and text.");
    }
    if (text.length > 65536) {
      throw new Error(
        `WhatsApp message exceeds maximum length of 65536 characters (got ${text.length}).`,
      );
    }
    const clientMessageId = buildWhatsAppClientMessageId(idempotencyKey);
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
    try {
      const response = await socket.sendMessage(
        remoteJid,
        { text },
        { messageId: outboundRecord.clientMessageId || clientMessageId },
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
      // Track sent message ID for reply-to detection in group gate
      const sentId = String(mapped.external_message_id || "").trim();
      if (sentId) {
        this.sentMessageIds.add(sentId);
        if (this.sentMessageIds.size > 500) this.sentMessageIds.clear();
      }
      return mapped;
    } finally {
      await typing.stop();
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
      return;
    }
    if (connection === "close") {
      const adapter = await this.getAdapter();
      const reconnectState = resolveWhatsAppReconnectState(
        update.lastDisconnect,
        adapter.disconnectReason,
      );
      this.socket = null;
      this.authBundle = null;
      this.pairingCodeRequested = false;
      if (!reconnectState.shouldReconnect) {
        // Genuine logout only -- shouldReconnect is false exclusively for
        // Baileys' loggedOut/401 code (see resolveWhatsAppReconnectState in
        // ./reconnect.ts), matching what OpenClaw treats as "must relink"
        // (extensions/whatsapp/src/connection-controller.ts). Every other
        // disconnect code (badSession, restartRequired, connectionClosed,
        // etc) reconnects with the existing creds intact -- this branch was
        // already scoped correctly; what was missing was safety on the
        // delete itself, which clearAuthStateDir() now provides. Drain any
        // in-flight write first so the delete isn't racing a rename.
        await waitForAuthWriteIdle(this.sessionStore.authStateDir());
        this.authPersistence = null;
        await this.sessionStore.clearAuthStateDir();
      }
      await this.sessionStore.save({
        status: reconnectState.shouldReconnect ? "disconnected" : "logged_out",
        retryable: reconnectState.shouldReconnect,
        qrCode: undefined,
        pairingCode: undefined,
        pairingCodeGeneratedAt: undefined,
        lastDisconnectReason: reconnectState.reason,
        lastDisconnectCode: reconnectState.statusCode,
      });
      await this.flushState();
      if (reconnectState.shouldReconnect && this.started) {
        this.scheduleReconnect();
      }
    }
  }

  private async handleMessagesUpsert(event: Record<string, unknown>): Promise<void> {
    const ownedJid = String(this.socket?.user?.id ?? "").trim() || undefined;
    const messages = Array.isArray(event.messages) ? event.messages : [];
    for (const entry of messages) {
      if (!entry || typeof entry !== "object") {
        continue;
      }
      const mapped = mapWhatsAppInboundMessage(entry as Record<string, unknown>, { ownedJid });
      if (!mapped || (mapped.message.from_me && !mapped.message.is_self_chat)) {
        continue;
      }
      // Resolve is_reply_to_sage: quoted stanza was sent by Sage
      if (mapped.message.is_group && mapped.message.quoted_stanza_id) {
        mapped.message.is_reply_to_sage = this.sentMessageIds.has(String(mapped.message.quoted_stanza_id));
      }
      // Group gate: skip group messages unless mentioned or replying to Sage
      if (mapped.message.is_group && !mapped.message.is_mentioned && !mapped.message.is_reply_to_sage) {
        continue;
      }
      this.startTypingForChat(mapped.message.remote_jid);
      await this.publishInbound(mapped);
    }
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

  private scheduleReconnect(): void {
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
    const delayMs = computeWhatsAppReconnectDelay(this.reconnectAttempts);
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

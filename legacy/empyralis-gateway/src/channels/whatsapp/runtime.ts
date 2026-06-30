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
import { WhatsAppOutboundStore, WhatsAppTypingKeepalive, type WhatsAppPresenceAction } from "./outbound";
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
}

interface WhatsAppBaileysAdapter {
  loadAuthState: (folder: string) => Promise<BaileysAuthBundle>;
  createSocket: (config: Record<string, unknown>) => BaileysSocketLike;
  disconnectReason: { loggedOut?: number; restartRequired?: number };
  browserDescriptor: (appName: string) => unknown;
}

export interface WhatsAppGatewayPublisher {
  publishEvent: (type: "channel.inbound", payload: GatewayChannelInboundPayload) => Promise<void>;
  publishStateUpdate: (payload: Record<string, unknown>) => Promise<void>;
}

export interface WhatsAppRuntimeDependencies {
  publisher?: WhatsAppGatewayPublisher;
  adapter?: WhatsAppBaileysAdapter;
}

const WHATSAPP_REDACT_STRING_KEYS = ["qrCode", "pairingCode", "sessionString", "sessionToken"] as const;
const WHATSAPP_REDACT_OBJECT_KEYS = ["creds", "keys", "authState", "signalIdentities", "preKeys", "signedPreKey"] as const;

export function redactWhatsAppCredentials(state: Record<string, unknown>): Record<string, unknown> {
  return redactCredentials(state, WHATSAPP_REDACT_STRING_KEYS, WHATSAPP_REDACT_OBJECT_KEYS);
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
  private started = false;
  private reconnectTimer: NodeJS.Timeout | null = null;
  private pairingCodeRequested = false;
  private connectPromise: Promise<void> | null = null;
  private reconnectAttempts = 0;
  private sentMessageIds = new Set<string>();
  private readonly draftManager = new DraftManager();

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
    ];
  }

  supportsCapability(capabilityId: string): boolean {
    return String(capabilityId || "").trim() === "channel.whatsapp.personal.configure";
  }

  async handleCapabilityInvoke(
    frame: GatewayRequestEnvelope<GatewayToolInvokePayload>,
  ): Promise<Record<string, unknown>> {
    const payload = frame.payload;
    const capabilityId = String(payload.capability_id || "").trim();
    if (capabilityId !== "channel.whatsapp.personal.configure") {
      throw new Error(`Unsupported WhatsApp personal capability: ${capabilityId || "unknown"}`);
    }
    const argumentsPayload =
      payload.arguments && typeof payload.arguments === "object" && !Array.isArray(payload.arguments)
        ? (payload.arguments as Record<string, unknown>)
        : {};
    return this.handleConfigure(argumentsPayload);
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
    return {
      channelKey: WHATSAPP_PERSONAL_CHANNEL_KEY,
      provider: WHATSAPP_PERSONAL_PROVIDER,
      status: snapshot.status,
      running: this.started,
      connected: Boolean(this.socket) && snapshot.status === "connected",
      reconnectAttempts: this.reconnectAttempts,
      lastEventAt: snapshot.updatedAt,
      lastError: snapshot.lastDisconnectReason,
      issues: snapshot.status === "connected" ? [] : ["whatsapp_personal_not_connected"],
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
    const typing = new WhatsAppTypingKeepalive(
      socket.sendPresenceUpdate
        ? (action) => socket.sendPresenceUpdate?.(action, remoteJid)
        : undefined,
    );
    await typing.start();
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
    const socket = adapter.createSocket({
      auth: this.authBundle.state,
      browser: adapter.browserDescriptor("Empyralis"),
      logger: this.logger,
      printQRInTerminal: false,
      syncFullHistory: false,
      markOnlineOnConnect: false,
    });
    this.socket = socket;
    socket.ev.on("creds.update", async () => {
      await Promise.resolve(this.authBundle?.saveCreds?.());
    });
    socket.ev.on("connection.update", (update) => {
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
      await this.publishInbound(mapped);
    }
  }

  private async publishInbound(payload: GatewayChannelInboundPayload): Promise<void> {
    await this.publisher?.publishEvent("channel.inbound", payload);
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
    } catch {
      this.pairingCodeRequested = false;
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
    this.adapter = {
      loadAuthState: async (folder: string) => useMultiFileAuthState(path.resolve(folder)),
      createSocket: (config: Record<string, unknown>) => makeWASocket(config),
      disconnectReason: baileysModule.DisconnectReason ?? {},
      browserDescriptor: (appName: string) =>
        typeof Browsers.macOS === "function" ? Browsers.macOS(appName) : ["Empyralis", "Safari", "1.0.0"],
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
    if (Object.keys(patch).length === 0) {
      throw new Error("At least one WhatsApp personal setup field is required.");
    }
    const storedConfig = await this.configStore.patchWhatsAppConfig(patch);
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

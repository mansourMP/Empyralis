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
import { buildTelegramConnectedState, buildTelegramPreflightState, loadTelegramLoginConfig, type TelegramLinkedAccount, type TelegramLoginConfig } from "./login";
import { mapTelegramInboundMessage, mapTelegramOutboundResult, type TelegramInboundMessage } from "./message-mapper";
import { TelegramOutboundStore, TelegramTypingKeepalive, type TelegramChatAction } from "./outbound";
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
    ];
  }

  supportsCapability(capabilityId: string): boolean {
    return String(capabilityId || "").trim() === "channel.telegram.personal.configure";
  }

  async handleCapabilityInvoke(
    frame: GatewayRequestEnvelope<GatewayToolInvokePayload>,
  ): Promise<Record<string, unknown>> {
    const payload = frame.payload;
    const capabilityId = String(payload.capability_id || "").trim();
    if (capabilityId !== "channel.telegram.personal.configure") {
      throw new Error(`Unsupported Telegram personal capability: ${capabilityId || "unknown"}`);
    }
    const argumentsPayload =
      payload.arguments && typeof payload.arguments === "object" && !Array.isArray(payload.arguments)
        ? (payload.arguments as Record<string, unknown>)
        : {};
    return this.handleConfigure(argumentsPayload);
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
      media: { text: true, images: false, files: false, reactions: false, voice: false },
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
    if (!idempotencyKey || !remoteJid || !text) {
      throw new Error("channel.outbound requires idempotency_key, remote_jid, and text.");
    }
    if (text.length > 4096) {
      throw new Error(
        `Telegram message exceeds maximum length of 4096 characters (got ${text.length}).`,
      );
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
    const typing = new TelegramTypingKeepalive(
      client.sendChatAction
        ? (action) => client.sendChatAction?.(remoteJid, action)
        : undefined,
    );
    await typing.start();
    try {
      const response = await client.sendMessage(
        remoteJid,
        text,
        String(payload.reply_to_external_message_id || "").trim() || undefined,
      );
      const mapped = mapTelegramOutboundResult(
        {
          idempotencyKey,
          remoteJid,
          text,
          replyToExternalMessageId: String(payload.reply_to_external_message_id || "").trim() || undefined,
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
        loginHint: String(resolvedConfig.phoneNumber || "").trim() || "login_code_required",
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
        loginHint: String(resolvedConfig.phoneNumber || "").trim() || "login_code_required",
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
    await this.publishInbound(mapped);
  }

  private async publishInbound(payload: GatewayChannelInboundPayload): Promise<void> {
    await this.publisher?.publishEvent("channel.inbound", payload);
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
    const sessions = await dynamicImport<Record<string, unknown>>("telegram/sessions");
    const TelegramClient = telegram.TelegramClient as new (...args: unknown[]) => any;
    const NewMessage = telegram.NewMessage as new (...args: unknown[]) => any;
    const StringSession = sessions.StringSession as new (value: string) => any;
    if (!TelegramClient || !StringSession) {
      throw new Error("telegram_package_missing");
    }

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
          { connectionRetries: 5, baseLogger: this.logger },
        );
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
          { connectionRetries: 5, baseLogger: this.logger },
        );
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
            if (!text || !externalMessageId) {
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

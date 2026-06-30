import type {
  GatewayChannelInboundPayload,
  GatewayChannelOutboundPayload,
  GatewayRequestEnvelope,
  GatewayScope,
  GatewayToolInvokePayload,
} from "../protocol/types";
import type {
  PersonalChannelCapabilityManifest,
  PersonalChannelGatewayPublisher,
  PersonalChannelHealthSnapshot,
  PersonalChannelRuntime,
} from "./personal-runtime";

type BridgeSetupKind = "local_bridge" | "mac_bridge";

export interface LocalBridgeRuntimeConfig {
  channelKey: string;
  label: string;
  provider: string;
  setupKind: BridgeSetupKind;
  envPrefix: string;
  chatTypes: string[];
  notes: string[];
}

interface LocalBridgeResolvedConfig {
  baseUrl: string;
  token?: string;
  pollIntervalMs: number;
}

function trimTrailingSlash(value: string): string {
  return value.replace(/\/+$/, "");
}

function envFlagEnabled(name: string): boolean {
  const value = String(process.env[name] || "").trim().toLowerCase();
  return value === "1" || value === "true" || value === "yes" || value === "allow";
}

function isPrivateBridgeHost(hostname: string): boolean {
  const host = hostname.toLowerCase();
  if (host === "localhost" || host === "localhost.localdomain" || host.endsWith(".local")) {
    return true;
  }
  if (host === "::1" || host.startsWith("127.")) {
    return true;
  }
  if (host.startsWith("10.") || host.startsWith("192.168.") || host.startsWith("169.254.")) {
    return true;
  }
  const match = /^172\.(\d{1,2})\./.exec(host);
  if (match) {
    const second = Number(match[1]);
    return second >= 16 && second <= 31;
  }
  if (host.startsWith("fc") || host.startsWith("fd") || host.startsWith("fe80:")) {
    return true;
  }
  return false;
}

function normalizeLocalBridgeBaseUrl(value: string, envPrefix: string): string {
  const raw = String(value || "").trim();
  if (!raw) {
    throw new Error(`${envPrefix}_URL is required.`);
  }
  const parsed = new URL(raw);
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    throw new Error(`${envPrefix}_URL must use http or https.`);
  }
  if (!envFlagEnabled("EMPYRALIS_ALLOW_PUBLIC_LOCAL_BRIDGE_URLS") && !isPrivateBridgeHost(parsed.hostname)) {
    throw new Error(`${envPrefix}_URL must point to a localhost, .local, or private-network Agent Computer bridge.`);
  }
  return trimTrailingSlash(parsed.toString());
}

function readPositiveInt(value: string | undefined, fallback: number): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? Math.round(parsed) : fallback;
}

function buildLocalBridgeManifest(config: LocalBridgeRuntimeConfig): PersonalChannelCapabilityManifest {
  return {
    channelKey: config.channelKey,
    label: config.label,
    provider: config.provider,
    runtimeLane: "personal_gateway",
    stage: "live",
    status: "not_configured",
    liveCapable: true,
    requiresAgentComputer: true,
    sessionOwner: "paired_gateway",
    setupKind: config.setupKind,
    capabilities: ["manifest", "health", "inbound", "outbound", "text"],
    chatTypes: config.chatTypes,
    media: { text: true, images: false, files: false, reactions: false, voice: false },
    safety: {
      ownerPairingRequired: true,
      allowlistRequired: true,
      studioBusinessAllowed: false,
      customerPublicSendAllowed: false,
    },
    notes: config.notes,
  };
}

export const LOCAL_BRIDGE_PERSONAL_CHANNEL_CONFIGS: LocalBridgeRuntimeConfig[] = [
  {
    channelKey: "signal_personal",
    label: "Signal Personal",
    provider: "signal_local_bridge",
    setupKind: "local_bridge",
    envPrefix: "EMPYRALIS_SIGNAL_BRIDGE",
    chatTypes: ["dm", "group"],
    notes: ["Requires a user-owned Agent Computer bridge such as signal-cli or an equivalent local adapter."],
  },
  {
    channelKey: "imessage_personal",
    label: "iMessage Personal",
    provider: "bluebubbles_local_bridge",
    setupKind: "mac_bridge",
    envPrefix: "EMPYRALIS_IMESSAGE_BRIDGE",
    chatTypes: ["dm", "group"],
    notes: ["Requires a user-owned Mac Agent Computer bridge."],
  },
  {
    channelKey: "wechat_personal",
    label: "WeChat Personal",
    provider: "wechat_local_bridge",
    setupKind: "local_bridge",
    envPrefix: "EMPYRALIS_WECHAT_BRIDGE",
    chatTypes: ["dm", "group"],
    notes: ["Requires a user-owned Agent Computer bridge. This is not a Studio business/customer channel."],
  },
];

export const LOCAL_BRIDGE_PERSONAL_CHANNEL_MANIFESTS: PersonalChannelCapabilityManifest[] =
  LOCAL_BRIDGE_PERSONAL_CHANNEL_CONFIGS.map(buildLocalBridgeManifest);

export class LocalBridgePersonalChannelRuntime implements PersonalChannelRuntime {
  private readonly manifest: PersonalChannelCapabilityManifest;
  private publisher?: PersonalChannelGatewayPublisher;
  private started = false;
  private pollTimer: NodeJS.Timeout | null = null;
  private lastEventAt?: string;
  private lastError?: string;
  private readonly seenInboundEventIds: string[] = [];
  private readonly seenInboundEventSet = new Set<string>();

  constructor(private readonly config: LocalBridgeRuntimeConfig) {
    this.manifest = buildLocalBridgeManifest(config);
  }

  requestedCapabilities(): string[] {
    return [];
  }

  supportsCapability(_capabilityId: string): boolean {
    return false;
  }

  async handleCapabilityInvoke(
    _frame: GatewayRequestEnvelope<GatewayToolInvokePayload>,
  ): Promise<Record<string, unknown>> {
    throw new Error(`${this.config.label} is configured through its local Agent Computer bridge, not cloud capability invoke.`);
  }

  supportsChannel(channelKey: string): boolean {
    return String(channelKey || "").trim() === this.config.channelKey;
  }

  async handleChannelOutbound(
    frame: GatewayRequestEnvelope<GatewayChannelOutboundPayload>,
  ): Promise<Record<string, unknown>> {
    const bridge = this.resolveBridgeConfig();
    if (!bridge) {
      throw new Error(`${this.config.label} bridge is not configured. Set ${this.config.envPrefix}_URL on Agent Computer.`);
    }
    const payload = frame.payload || {};
    const response = await this.fetchJson(`${bridge.baseUrl}/messages`, {
      method: "POST",
      token: bridge.token,
      body: {
        channel_key: this.config.channelKey,
        provider: this.config.provider,
        remote_jid: payload.remote_jid,
        text: payload.text,
        idempotency_key: payload.idempotency_key,
        reply_to_external_message_id: payload.reply_to_external_message_id,
        metadata: payload.metadata || {},
      },
    });
    this.lastEventAt = new Date().toISOString();
    return {
      channel_key: this.config.channelKey,
      provider: this.config.provider,
      delivered: response.delivered !== false,
      external_message_id: typeof response.external_message_id === "string" ? response.external_message_id : undefined,
      status: typeof response.status === "string" ? response.status : "sent",
      bridge: "local_agent_computer",
    };
  }

  async handleGatewayConnected(_scope: GatewayScope): Promise<void> {
    await this.start();
  }

  async handleGatewayDisconnected(_reason: string): Promise<void> {
    await this.stop();
  }

  async start(): Promise<void> {
    this.started = true;
    let bridge: LocalBridgeResolvedConfig | null = null;
    try {
      bridge = this.resolveBridgeConfig();
    } catch (error) {
      this.lastError = error instanceof Error ? error.message : String(error);
      return;
    }
    if (!bridge || bridge.pollIntervalMs <= 0 || this.pollTimer) {
      return;
    }
    this.pollTimer = setInterval(() => {
      void this.pollInboundEvents();
    }, bridge.pollIntervalMs);
    this.pollTimer.unref?.();
  }

  async stop(): Promise<void> {
    this.started = false;
    if (this.pollTimer) {
      clearInterval(this.pollTimer);
      this.pollTimer = null;
    }
  }

  setPublisher(publisher: PersonalChannelGatewayPublisher): void {
    this.publisher = publisher;
  }

  getManifest(): PersonalChannelCapabilityManifest {
    return this.manifest;
  }

  async getHealthSnapshot(): Promise<PersonalChannelHealthSnapshot> {
    let bridge: LocalBridgeResolvedConfig | null = null;
    try {
      bridge = this.resolveBridgeConfig();
    } catch (error) {
      this.lastError = error instanceof Error ? error.message : String(error);
      return {
        channelKey: this.config.channelKey,
        provider: this.config.provider,
        status: "invalid_config",
        running: this.started,
        connected: false,
        reconnectAttempts: 0,
        lastError: this.lastError,
        issues: [`${this.config.channelKey}_bridge_url_invalid`],
      };
    }
    if (!bridge) {
      return {
        channelKey: this.config.channelKey,
        provider: this.config.provider,
        status: "not_configured",
        running: false,
        connected: false,
        reconnectAttempts: 0,
        lastError: this.lastError,
        issues: [`${this.config.channelKey}_bridge_not_configured`],
      };
    }
    try {
      const response = await this.fetchJson(`${bridge.baseUrl}/health`, {
        method: "GET",
        token: bridge.token,
      });
      const connected = response.connected !== false;
      this.lastError = undefined;
      return {
        channelKey: this.config.channelKey,
        provider: this.config.provider,
        status: typeof response.status === "string" ? response.status : connected ? "connected" : "disconnected",
        running: this.started,
        connected,
        reconnectAttempts: 0,
        lastEventAt: this.lastEventAt,
        issues: Array.isArray(response.issues) ? response.issues.map(String) : [],
      };
    } catch (error) {
      this.lastError = error instanceof Error ? error.message : String(error);
      return {
        channelKey: this.config.channelKey,
        provider: this.config.provider,
        status: "unavailable",
        running: this.started,
        connected: false,
        reconnectAttempts: 0,
        lastError: this.lastError,
        issues: [`${this.config.channelKey}_bridge_unavailable`],
      };
    }
  }

  private resolveBridgeConfig(): LocalBridgeResolvedConfig | null {
    const baseUrl = String(process.env[`${this.config.envPrefix}_URL`] || "").trim();
    if (!baseUrl) {
      return null;
    }
    return {
      baseUrl: normalizeLocalBridgeBaseUrl(baseUrl, this.config.envPrefix),
      token: String(process.env[`${this.config.envPrefix}_TOKEN`] || "").trim() || undefined,
      pollIntervalMs: readPositiveInt(process.env[`${this.config.envPrefix}_POLL_MS`], 5000),
    };
  }

  private async pollInboundEvents(): Promise<void> {
    if (!this.publisher) {
      return;
    }
    let bridge: LocalBridgeResolvedConfig | null = null;
    try {
      bridge = this.resolveBridgeConfig();
    } catch (error) {
      this.lastError = error instanceof Error ? error.message : String(error);
      return;
    }
    if (!bridge) {
      return;
    }
    try {
      const response = await this.fetchJson(`${bridge.baseUrl}/events?channel_key=${encodeURIComponent(this.config.channelKey)}`, {
        method: "GET",
        token: bridge.token,
      });
      const events = Array.isArray(response.items) ? response.items : [];
      for (const item of events) {
        if (!item || typeof item !== "object") {
          continue;
        }
        const event = this.mapInboundEvent(item as Record<string, unknown>);
        if (event) {
          const eventKey = this.inboundEventKey(event);
          if (!this.rememberInboundEvent(eventKey)) {
            continue;
          }
          await this.publisher.publishEvent("channel.inbound", event);
          this.lastEventAt = event.message.received_at;
        }
      }
      this.lastError = undefined;
    } catch (error) {
      this.lastError = error instanceof Error ? error.message : String(error);
    }
  }

  private mapInboundEvent(item: Record<string, unknown>): GatewayChannelInboundPayload | null {
    const externalMessageId = String(item.external_message_id || item.id || "").trim();
    const remoteJid = String(item.remote_jid || item.peer_id || item.chat_id || "").trim();
    const text = String(item.text || item.message || "").trim();
    if (!externalMessageId || !remoteJid || !text) {
      return null;
    }
    return {
      channel_key: this.config.channelKey,
      provider: this.config.provider,
      message: {
        external_message_id: externalMessageId,
        remote_jid: remoteJid,
        sender_jid: String(item.sender_jid || item.sender_id || "").trim() || undefined,
        push_name: String(item.push_name || item.sender_name || "").trim() || undefined,
        text,
        received_at: String(item.received_at || "").trim() || new Date().toISOString(),
        from_me: item.from_me === true,
      },
    };
  }

  private inboundEventKey(event: GatewayChannelInboundPayload): string {
    const message = event.message;
    return [
      this.config.channelKey,
      message.remote_jid,
      message.external_message_id,
    ].join(":");
  }

  private rememberInboundEvent(eventKey: string): boolean {
    if (this.seenInboundEventSet.has(eventKey)) {
      return false;
    }
    this.seenInboundEventSet.add(eventKey);
    this.seenInboundEventIds.push(eventKey);
    while (this.seenInboundEventIds.length > 1000) {
      const oldest = this.seenInboundEventIds.shift();
      if (oldest) {
        this.seenInboundEventSet.delete(oldest);
      }
    }
    return true;
  }

  private async fetchJson(
    url: string,
    options: { method: "GET" | "POST"; token?: string; body?: Record<string, unknown> },
  ): Promise<Record<string, unknown>> {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15_000);
    try {
      const response = await fetch(url, {
        method: options.method,
        headers: {
          "content-type": "application/json",
          ...(options.token ? { authorization: `Bearer ${options.token}` } : {}),
        },
        body: options.body ? JSON.stringify(options.body) : undefined,
        signal: controller.signal,
      });
      if (!response.ok) {
        throw new Error(`${this.config.label} bridge returned HTTP ${response.status}`);
      }
      const payload = await response.json();
      return payload && typeof payload === "object" ? payload as Record<string, unknown> : {};
    } finally {
      clearTimeout(timeout);
    }
  }
}

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
import { TypingKeepalive } from "./foundation/typing-keepalive";

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

// Same values as WhatsApp's/Telegram's own typing keepalive (see
// whatsapp/outbound.ts's WHATSAPP_TYPING_KEEPALIVE_MS/MAX_TTL_MS and
// telegram/outbound.ts's identical constants) — refresh every 3s, hard-stop
// after 60s even if handleChannelOutbound's claim never arrives (e.g. the
// backend reply never comes back).
const LOCAL_BRIDGE_TYPING_KEEPALIVE_MS = 3_000;
const LOCAL_BRIDGE_TYPING_MAX_TTL_MS = 60_000;

export function buildLocalBridgeManifest(config: LocalBridgeRuntimeConfig): PersonalChannelCapabilityManifest {
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
  // Typing sessions started the instant an inbound message is admitted (see
  // startTypingForChat), keyed by remote_jid, so handleChannelOutbound can
  // claim and stop the SAME session once the real reply is ready to send —
  // same start-on-admit/stop-on-send shape as WhatsApp's/Telegram's own
  // activeTyping map (see whatsapp/runtime.ts's startTypingForChat), just
  // without their draft-streaming coordination since local-bridge replies
  // are generated backend-side, not gateway-side. Best-effort throughout:
  // a bridge that doesn't implement /typing (BlueBubbles, WeChat today)
  // simply never gets a successful call — see sendTypingAction — and
  // TypingKeepalive's own maxTtlMs self-expires a session even if stop()
  // is never claimed (e.g. the reply never arrives), so this can never
  // leak an interval past that ceiling.
  private readonly activeTyping = new Map<string, TypingKeepalive>();

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
    // Claim (and stop) whatever typing session startTypingForChat began for
    // this same remote_jid when the triggering inbound message was admitted
    // — the real reply is about to be sent, so the "thinking" indicator's
    // job is done. Fire-and-forget: never let a cosmetic typing-stop call
    // delay or fail the actual send below.
    void this.claimTypingForChat(String(payload.remote_jid || ""))?.stop();
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
    for (const typing of this.activeTyping.values()) {
      void typing.stop();
    }
    this.activeTyping.clear();
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
      // Bridges that maintain their own live connection out to a
      // third-party service (signal-cli's SSE stream today -- see
      // signal-cli-bridge.ts's SignalSseState/runSignalCliEventLoop) report
      // their own in-progress reconnect count via reconnect_attempts on
      // /health. Previously hardcoded to 0 regardless -- a bridge stuck
      // mid-backoff after a drop looked identical to one that had never
      // dropped at all. Bridges that don't track this (BlueBubbles/WeChat
      // today) simply omit the field, so this falls back to 0 exactly as
      // before.
      const reconnectAttempts = Number(response.reconnect_attempts);
      return {
        channelKey: this.config.channelKey,
        provider: this.config.provider,
        status: typeof response.status === "string" ? response.status : connected ? "connected" : "disconnected",
        running: this.started,
        connected,
        reconnectAttempts: Number.isFinite(reconnectAttempts) && reconnectAttempts >= 0 ? reconnectAttempts : 0,
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
          // Group gate: REMOVED as a gateway-side DECISION (see
          // personal_channels_service.py's _resolve_local_bridge_agent_id /
          // CHANNEL-GATEWAY-PLAN.md "the last unscoped channels" — Signal/
          // iMessage/WeChat-personal now resolve a real per-agent identity
          // the same way WhatsApp/Telegram already did when their own
          // equivalent gate was removed from telegram/runtime.ts and
          // whatsapp/runtime.ts on 2026-07-23). This runtime still forwards
          // the raw mention FACTS the bridge computed (is_group/is_mentioned/
          // is_reply_to_sage) — that computation is unavoidably
          // platform-specific and stays here (see mapInboundEvent below).
          // What moved is WHO DECIDES shouldSkip from those facts:
          // personal_channels_service.py's _enforce_group_policy (via
          // mention_gating_service.resolve_inbound_mention_decision) is the
          // ONE shared resolver for every personal-gateway channel, backend
          // group_policy/require_mention config included — this local-bridge
          // family was the last channel family still deciding it here,
          // inconsistently with WhatsApp/Telegram, and (until identity
          // resolved for it) with no owner-facing way to configure it either
          // way. The event is still remembered (above) so a re-poll never
          // re-publishes the same external_message_id twice.
          // Start the "thinking" typing indicator the instant a message is
          // admitted — before waiting on the backend round-trip that
          // actually produces (or withholds) a reply. Claimed and stopped
          // in handleChannelOutbound once that reply is ready to send. See
          // startTypingForChat's doc comment for why this only ever
          // best-effort no-ops on bridges that don't support it.
          this.startTypingForChat(event.message.remote_jid, bridge);
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
        // Optional — absent/false unless the bridge behind this HTTP
        // contract actually computes them (see the two first-party bridges
        // in ../bridges/ for the reference implementation of each field).
        // is_self_chat lets a from_me message still reach the backend as an
        // owner command (see personal_channels_service.py's
        // _handle_local_bridge_gateway_channel_inbound) — only
        // signal-cli-bridge.ts computes it today.
        is_self_chat: item.is_self_chat === true,
        is_group: item.is_group === true,
        is_mentioned: item.is_mentioned === true,
        is_reply_to_sage: item.is_reply_to_sage === true,
      },
    };
  }

  /** Starts a typing keepalive the instant an inbound message clears the
   *  group gate — mirrors WhatsApp's/Telegram's own startTypingForChat (see
   *  whatsapp/runtime.ts) but triggered by bridge-poll admission instead of
   *  a live socket event, since polling is this family's own inbound path.
   *  A second inbound message for the same remoteJid while one is already
   *  active reuses it rather than starting a duplicate. */
  private startTypingForChat(remoteJid: string, bridge: LocalBridgeResolvedConfig): void {
    const jid = String(remoteJid || "").trim();
    if (!jid || this.activeTyping.has(jid)) {
      return;
    }
    const typing = new TypingKeepalive(
      (action) => this.sendTypingAction(bridge, jid, action),
      {
        startAction: "start",
        stopAction: "stop",
        keepaliveMs: LOCAL_BRIDGE_TYPING_KEEPALIVE_MS,
        maxTtlMs: LOCAL_BRIDGE_TYPING_MAX_TTL_MS,
      },
    );
    this.activeTyping.set(jid, typing);
    void typing.start();
  }

  /** Hands the caller the typing session startTypingForChat started for
   *  this remoteJid (if one is still active) and stops tracking it here —
   *  same claim-and-release contract as WhatsApp's claimTypingForChat. */
  private claimTypingForChat(remoteJid: string): TypingKeepalive | undefined {
    const jid = String(remoteJid || "").trim();
    if (!jid) {
      return undefined;
    }
    const typing = this.activeTyping.get(jid);
    if (typing) {
      this.activeTyping.delete(jid);
    }
    return typing;
  }

  /** POSTs a start/stop typing action to the bridge's /typing endpoint.
   *  Best-effort by design: typing indicators are cosmetic, and a bridge
   *  that doesn't implement /typing at all (BlueBubbles, WeChat today —
   *  only signal-cli-bridge.ts does) must never turn a missing feature
   *  into a logged error or a failed send. */
  private async sendTypingAction(bridge: LocalBridgeResolvedConfig, remoteJid: string, action: string): Promise<void> {
    try {
      await this.fetchJson(`${bridge.baseUrl}/typing`, {
        method: "POST",
        token: bridge.token,
        body: {
          channel_key: this.config.channelKey,
          remote_jid: remoteJid,
          action,
        },
      });
    } catch {
      // Swallowed — see doc comment above.
    }
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

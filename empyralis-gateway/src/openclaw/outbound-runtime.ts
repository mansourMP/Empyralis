/**
 * The gateway half of OpenClaw outbound (the OpenClaw channel adoption step 3).
 *
 * WHY THIS IS A PersonalChannelRuntime AND NOT A NEW DISPATCH PATH
 * ---------------------------------------------------------------
 * The cloud already routes an agent's reply for an `openclaw_*` channel
 * through the SAME machinery Signal/iMessage/WeChat-personal use:
 * `_OpenClawPersonalChannelHandler` extends `_LocalBridgePersonalChannelHandler`,
 * so `_deliver_local_bridge_personal_reply` / `send_local_bridge_personal_message`
 * call `gateway_protocol_service.dispatch_channel_outbound(...)`, which sends
 * a `channel.outbound` request frame down the already-paired cloud WS.
 * `GatewayWsClient.handleServerRequest` then looks the channel up with
 * `personalChannelRuntimes.runtimeForChannel(channel_key)` — and before this
 * file existed, that lookup returned undefined and every OpenClaw reply died
 * with "Unsupported personal channel key: openclaw_feishu".
 *
 * So the whole outbound stack already existed; the only missing piece was a
 * runtime registered under these channel keys. That is all this is. There is
 * deliberately NO second outbound path, no new frame type, no new cloud
 * route.
 *
 * WHAT DIFFERED from the first-party local-bridge family this transport
 * superseded (deleted 2026-08-14, full OpenClaw channel cutover — see git
 * history to recover): those POST to a user-owned HTTP bridge; this invokes
 * OpenClaw's `message.action` over a live authenticated WS session (see
 * ./openclaw-gateway-client.ts for why HTTP is not an option).
 *
 * FAILURE IS NEVER SILENT. Every outcome is journaled here, and a
 * non-delivery THROWS, which `handleServerRequest` turns into an `ok:false`
 * response frame; the cloud's `dispatch_channel_outbound` raises on that, so
 * the outbound row is never marked delivered and the inbound message is
 * never marked processed. A message we could not deliver stays visibly
 * undelivered instead of being recorded as sent.
 */

import type {
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
} from "../channels/personal-runtime";
import { OPENCLAW_PROVIDER } from "./inbound-payload";
import { OPENCLAW_TRANSPORT_CHANNEL_IDS } from "./capabilities";
import { OpenClawGatewayClient } from "./openclaw-gateway-client";
import { mapOpenClawOutboundPayload, wouldBridgePluginCancel } from "./outbound-payload";

/** Human labels, kept in step with
 *  `personal_channels_service.OPENCLAW_PERSONAL_CHANNELS`. A channel id with
 *  no entry here still works; it just shows its raw id. */
const OPENCLAW_CHANNEL_LABELS: Record<string, string> = {
  feishu: "Feishu",
  line: "LINE",
  qq: "QQ",
  zalo: "Zalo",
  msteams: "Microsoft Teams",
};

export function buildOpenClawManifest(
  channelId: string,
  configured: boolean,
): PersonalChannelCapabilityManifest {
  const label = OPENCLAW_CHANNEL_LABELS[channelId] ?? channelId;
  return {
    channelKey: `openclaw_${channelId}`,
    label: `${label} (OpenClaw)`,
    provider: OPENCLAW_PROVIDER,
    runtimeLane: "personal_gateway",
    stage: "live",
    // `not_configured` when this box has no OpenClaw gateway credential, so
    // the Hardware surface says why nothing sends. Note this is one of the
    // statuses `_assert_gateway_advertised_personal_channel` deliberately
    // does NOT treat as inbound-blocking — inbound still works without the
    // outbound socket, and must not be suppressed by it.
    status: configured ? "live" : "not_configured",
    liveCapable: true,
    requiresAgentComputer: true,
    sessionOwner: "paired_gateway",
    setupKind: "local_bridge",
    capabilities: ["manifest", "health", "inbound", "outbound", "text"],
    chatTypes: ["dm", "group"],
    // Media is not carried on this lane in either direction yet — inbound
    // drops it (inbound-payload.ts's detectMedia) and outbound refuses a
    // media-only reply (outbound-payload.ts). Advertised honestly.
    media: { text: true, images: false, files: false, reactions: false, voice: false },
    safety: {
      ownerPairingRequired: true,
      allowlistRequired: true,
      studioBusinessAllowed: false,
      customerPublicSendAllowed: false,
    },
    notes: [
      "Transported by an OpenClaw gateway running alongside Agent Computer on this machine.",
      "Group messages stay silent until the owner allowlists the chat — OpenClaw's message_received hook carries no group/mention facts, so Empyralis assumes 'group' and fails closed.",
    ],
  };
}

export class OpenClawPersonalChannelRuntime implements PersonalChannelRuntime {
  private readonly channelKey: string;
  private readonly manifest: PersonalChannelCapabilityManifest;
  private lastEventAt?: string;
  private lastError?: string;

  constructor(
    private readonly channelId: string,
    /** Null when this box has no OpenClaw gateway credential configured.
     *  The runtime is STILL registered in that case, on purpose: a
     *  registered runtime that refuses with a named reason is loud, while an
     *  unregistered one produces the generic "Unsupported personal channel
     *  key" and tells nobody why. */
    private readonly client: OpenClawGatewayClient | null,
    private readonly record?: (messageType: string, payload: Record<string, unknown>) => Promise<unknown>,
  ) {
    this.channelKey = `openclaw_${channelId}`;
    this.manifest = buildOpenClawManifest(channelId, client !== null);
  }

  requestedCapabilities(): string[] {
    // Channel transport only. Configuration happens on the OpenClaw side
    // (provisioning, step 4), never through a cloud capability invoke —
    // mirrors _OpenClawPersonalChannelHandler.configure()'s refusal.
    return [];
  }

  supportsCapability(_capabilityId: string): boolean {
    return false;
  }

  async handleCapabilityInvoke(
    _frame: GatewayRequestEnvelope<GatewayToolInvokePayload>,
  ): Promise<Record<string, unknown>> {
    throw new Error(
      `${this.manifest.label} is configured on the OpenClaw gateway running alongside Agent Computer, not through cloud capability invoke.`,
    );
  }

  supportsChannel(channelKey: string): boolean {
    return String(channelKey || "").trim() === this.channelKey;
  }

  private async journal(messageType: string, payload: Record<string, unknown>): Promise<void> {
    try {
      await this.record?.(messageType, payload);
    } catch {
      // Auditing must never take delivery down.
    }
  }

  async handleChannelOutbound(
    frame: GatewayRequestEnvelope<GatewayChannelOutboundPayload>,
  ): Promise<Record<string, unknown>> {
    const payload = frame.payload;

    if (!this.client) {
      const error =
        `${this.manifest.label} cannot send: this Agent Computer has no OpenClaw gateway credential configured ` +
        "(set EMPYRALIS_OPENCLAW_GATEWAY_TOKEN, and EMPYRALIS_OPENCLAW_GATEWAY_URL if it is not on the default loopback port).";
      this.lastError = error;
      await this.journal("openclaw.outbound.not_configured", {
        channel_key: this.channelKey,
        idempotency_key: String(payload?.idempotency_key || ""),
      });
      throw new Error(error);
    }

    const mapped = mapOpenClawOutboundPayload(payload);
    if (!mapped.ok) {
      this.lastError = mapped.error;
      await this.journal("openclaw.outbound.rejected", {
        channel_key: this.channelKey,
        idempotency_key: String(payload?.idempotency_key || ""),
        reason: mapped.error,
        detail: mapped.detail ?? null,
      });
      throw new Error(`OpenClaw outbound refused: ${mapped.error}${mapped.detail ? ` (${mapped.detail})` : ""}`);
    }

    // Collision guard with our own bridge plugin's `message_sending` cancel
    // predicate. That hook fires on EVERY OpenClaw outbound delivery,
    // including the one this call is about to originate — so text carrying
    // both of its markers would be cancelled inside OpenClaw and the user
    // would silently receive nothing. Refuse it here instead: a loud,
    // journaled rejection the cloud sees beats an invisible cancellation.
    // See OPENCLAW_CANCEL_PREDICATE_MARKERS' comment for the drift test.
    if (wouldBridgePluginCancel(String(mapped.request.params.message ?? ""))) {
      this.lastError = "openclaw_outbound_collides_with_cancel_predicate";
      await this.journal("openclaw.outbound.rejected", {
        channel_key: this.channelKey,
        idempotency_key: mapped.request.idempotencyKey,
        reason: "openclaw_outbound_collides_with_cancel_predicate",
      });
      throw new Error(
        "OpenClaw outbound refused: this reply's text matches the bridge plugin's credential-less-failure suppression predicate, " +
          "so OpenClaw would cancel it on the way out. Refusing rather than sending a message that would be silently dropped.",
      );
    }

    if (mapped.droppedMedia.length > 0) {
      await this.journal("openclaw.outbound.media_dropped", {
        channel_key: this.channelKey,
        idempotency_key: mapped.request.idempotencyKey,
        media_kinds: mapped.droppedMedia.map((item) => String((item as { kind?: unknown }).kind ?? "unknown")),
      });
    }

    const outcome = await this.client.sendMessageAction(mapped.request);
    if (outcome.status !== "delivered") {
      this.lastError = `${outcome.code}: ${outcome.message}`;
      await this.journal("openclaw.outbound.failed", {
        channel_key: this.channelKey,
        idempotency_key: mapped.request.idempotencyKey,
        // Structured code first — this is what anything downstream should
        // ever branch on. The sentence is for a human reading the journal.
        outcome: outcome.status,
        code: outcome.code,
        detail: outcome.message,
        // How long OpenClaw asked us to wait, when it said. Journaled because
        // it is the difference between "this box is briefly busy" and "the
        // platform is rate-limiting this account", and nothing downstream can
        // tell those apart from the sentence. See
        // OpenClawGatewayClient.waitBeforeRetry: a request longer than one
        // in-band wait STOPS the retry rather than shortening it.
        retry_after_ms: outcome.retryAfterMs ?? null,
      });
      throw new Error(
        `OpenClaw refused delivery for ${this.channelKey} (${outcome.status}/${outcome.code}): ${outcome.message}`,
      );
    }

    this.lastEventAt = new Date().toISOString();
    this.lastError = undefined;
    await this.journal("openclaw.outbound.delivered", {
      channel_key: this.channelKey,
      idempotency_key: mapped.request.idempotencyKey,
      external_message_id: outcome.externalMessageId ?? null,
      media_dropped: mapped.droppedMedia.length > 0,
    });

    return {
      channel_key: this.channelKey,
      provider: OPENCLAW_PROVIDER,
      delivered: true,
      external_message_id: outcome.externalMessageId,
      status: "sent",
      bridge: "openclaw_gateway",
      // Surfaced to the cloud, which stores the whole dispatch result on the
      // outbound row's metadata — so a partial delivery is discoverable
      // later without reading this box's journal.
      media_dropped: mapped.droppedMedia.length > 0 ? mapped.droppedMedia.length : undefined,
    };
  }

  async handleGatewayConnected(_scope: GatewayScope): Promise<void> {
    await this.start();
  }

  async handleGatewayDisconnected(_reason: string): Promise<void> {
    // Deliberately does NOT stop the shared OpenClaw session: one client is
    // shared by every OpenClaw channel runtime, and index.ts owns its
    // lifecycle. Tearing it down from one channel's disconnect callback
    // would drop the other four.
  }

  async start(): Promise<void> {
    await this.client?.start();
  }

  async stop(): Promise<void> {
    // Same reason as handleGatewayDisconnected: the client is shared and
    // stopped once, by index.ts's cleanup.
  }

  setPublisher(_publisher: PersonalChannelGatewayPublisher): void {
    // Inbound for this lane arrives through OpenClawInboundListener, which
    // publishes on the GatewayWsClient directly. Nothing to hold here.
  }

  getManifest(): PersonalChannelCapabilityManifest {
    return this.manifest;
  }

  getHealthSnapshot(): PersonalChannelHealthSnapshot {
    if (!this.client) {
      return {
        channelKey: this.channelKey,
        provider: OPENCLAW_PROVIDER,
        status: "not_configured",
        running: false,
        connected: false,
        reconnectAttempts: 0,
        lastError: this.lastError,
        issues: ["openclaw_gateway_not_configured"],
      };
    }
    const state = this.client.getState();
    return {
      channelKey: this.channelKey,
      provider: OPENCLAW_PROVIDER,
      // DELIBERATELY never "disconnected"/"unavailable"/"error" while
      // configured. `_assert_gateway_advertised_personal_channel` treats
      // those health values as a reason to REJECT an inbound message, and
      // inbound does not depend on this socket at all — it arrives from the
      // bridge plugin over loopback HTTP. Reporting an idle or reconnecting
      // outbound socket with an inbound-blocking word would drop messages
      // that already arrived. `connected` is reported truthfully, and
      // lastError/issues carry the real detail.
      status: state.connected ? "connected" : "connecting",
      running: true,
      connected: state.connected,
      reconnectAttempts: state.reconnectAttempts,
      lastEventAt: this.lastEventAt,
      lastError: this.lastError ?? state.lastError,
      issues: state.connected ? [] : ["openclaw_gateway_session_not_established"],
    };
  }
}

/** One runtime per OpenClaw-transported channel, all sharing ONE session to
 *  the local OpenClaw gateway. Channel ids come from the same list the
 *  capability advertisement uses, so the set this box says it can carry and
 *  the set it can actually send on cannot drift. */
export function buildOpenClawPersonalChannelRuntimes(
  client: OpenClawGatewayClient | null,
  record?: (messageType: string, payload: Record<string, unknown>) => Promise<unknown>,
): OpenClawPersonalChannelRuntime[] {
  return OPENCLAW_TRANSPORT_CHANNEL_IDS.map(
    (channelId) => new OpenClawPersonalChannelRuntime(channelId, client, record),
  );
}

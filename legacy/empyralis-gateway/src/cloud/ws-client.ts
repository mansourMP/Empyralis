import crypto from "crypto";

import { GatewayConfig, assertWebSocketUrl } from "../config";
import { GatewayCheckpoints } from "../state/checkpoints";
import { GatewayStateDb } from "../state/db";
import { GatewayJournal } from "../state/journal";
import { GatewayOutbox, GatewayOutboxItem } from "../state/outbox";
import {
  GatewayDeviceIdentity,
  persistDeviceIdentityScope,
} from "../pairing/device-identity";
import { GatewayTokenStore } from "../pairing/token-store";
import { encodeFrame, decodeFrame, SUPPORTED_PROTOCOL_VERSIONS } from "../protocol/codec";
import type {
  GatewayChannelInboundPayload,
  GatewayChannelOutboundPayload,
  GatewayEventEnvelope,
  GatewayRegistrationPayload,
  GatewayRequestEnvelope,
  GatewayResponseEnvelope,
  GatewayScope,
  GatewaySessionPayload,
  GatewayToolInterruptPayload,
  GatewayToolInvokePayload,
} from "../protocol/types";
import { PROTOCOL_VERSION } from "../protocol/types";
import { GatewayRuntimeMetadata } from "../runtime/runtime-metadata";
import { HeartbeatLoop } from "./heartbeat";
import { ReconnectBackoff, classifyReconnectError, classifyCloseCode, sleep, type CloseCodeContext } from "./reconnect";
import { GatewayCapabilityRouter } from "../supervisor/capability-router";
import { PersonalChannelRuntimeRegistry } from "../channels/personal-runtime";
import { buildGatewayHeartbeatPayload } from "./heartbeat-payload";
import {
  applyLocalRunnerReadiness,
  buildFastPassiveInventorySnapshot,
  collectPassiveInventorySnapshot,
  type PassiveInventorySnapshot,
} from "../health/service-inventory";

/**
 * Message types that are safe to replay automatically.
 * Only idempotent read-like or declarative state updates.
 */
const SAFE_REPLAY_MESSAGE_TYPES: ReadonlySet<GatewayRequestEnvelope["type"]> = new Set([
  "gateway.heartbeat",
  "gateway.state.update",
]);

/**
 * Message types that MUST NOT replay automatically unless an explicit
 * idempotencyKey is provided.  These actions are destructive or side-effectful.
 */
const DANGEROUS_MESSAGE_TYPES: ReadonlySet<GatewayRequestEnvelope["type"]> = new Set([
  "tool.invoke",
  "tool.interrupt",
  "channel.outbound",
]);

interface PendingResponse {
  messageType: GatewayRequestEnvelope["type"];
  replayable: boolean;
  timeoutHandle: NodeJS.Timeout;
  resolve: (frame: GatewayResponseEnvelope) => void;
  reject: (error: Error) => void;
}

interface RequestDispatchOptions {
  requestId?: string;
  replayable?: boolean;
  persistOutbox?: boolean;
  timeoutMs?: number;
}

interface GatewayRunOptions {
  afterConnected?: () => Promise<void>;
}

function compactGatewayResponsePayload(payload?: Record<string, unknown>): Record<string, unknown> | undefined {
  if (!payload || payload.capability_id !== "screenshot.capture") {
    return payload;
  }
  const result = payload.result && typeof payload.result === "object"
    ? { ...(payload.result as Record<string, unknown>) }
    : {};
  const images = Array.isArray(result.images) ? result.images : [];
  result.images = images
    .filter((item): item is Record<string, unknown> => Boolean(item && typeof item === "object"))
    .map((item) => {
      const compact = { ...item };
      if (typeof compact.data_base64 === "string" && compact.data_base64.length > 0) {
        compact.data_truncated = true;
        compact.byte_size_estimate = Math.floor((compact.data_base64.length * 3) / 4);
      }
      delete compact.data_base64;
      delete compact.base64;
      delete compact.data;
      return compact;
    });
  return {
    ...payload,
    result,
  };
}

export class GatewayWsClient {
  private socket: WebSocket | null = null;
  private readonly heartbeatLoop = new HeartbeatLoop();
  private readonly reconnect: ReconnectBackoff;
  private readonly pendingResponses = new Map<string, PendingResponse>();
  private activeScope: GatewayScope | null = null;
  private socketFailureReason: string | null = null;
  private _connectionStartedAt: number | null = null;
  private _lastHeartbeatResponseAt: number | null = null;
  private passiveInventorySnapshot: PassiveInventorySnapshot | null = null;
  private passiveInventoryRefresh: Promise<void> | null = null;

  constructor(
    private readonly config: GatewayConfig,
    private readonly db: GatewayStateDb,
    private readonly journal: GatewayJournal,
    private readonly outbox: GatewayOutbox,
    private readonly checkpoints: GatewayCheckpoints,
    private readonly tokenStore: GatewayTokenStore,
    private readonly capabilityRouter: GatewayCapabilityRouter,
    private readonly personalChannelRuntimes = new PersonalChannelRuntimeRegistry(),
  ) {
    this.reconnect = new ReconnectBackoff({
      minDelayMs: this.config.reconnectMinDelayMs,
      maxDelayMs: this.config.reconnectMaxDelayMs,
    });
  }

  async registerFromPairing(
    pairingToken: string,
    identity: GatewayDeviceIdentity,
    runtimeMetadata: GatewayRuntimeMetadata,
  ): Promise<GatewayRegistrationPayload> {
    const response = await fetch(`${this.config.apiBaseUrl}/gateway/registrations`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        pairing_token: pairingToken,
        device_id: identity.deviceId,
        gateway_id: identity.gatewayId,
        display_name: this.config.displayName,
        platform: runtimeMetadata.platform,
        capabilities: runtimeMetadata.requestedCapabilities,
        metadata: runtimeMetadata.deviceMetadata,
      }),
    });
    if (!response.ok) {
      const rawBody = await response.text().catch(() => "");
      let detail = rawBody.trim();
      if (detail) {
        try {
          const parsed = JSON.parse(detail) as { detail?: unknown };
          detail = typeof parsed.detail === "string" ? parsed.detail : detail;
        } catch {
          // Keep the raw body when the runtime does not return JSON.
        }
      }
      throw new Error(
        `Gateway registration failed with status ${response.status}${detail ? `: ${detail}` : ""}`,
      );
    }
    const payload = (await response.json()) as GatewayRegistrationPayload;
    await this.db.writeJson("registration.json", payload.gateway);
    await persistDeviceIdentityScope(this.db, {
      gatewayId: String(payload.gateway.gateway_id || identity.gatewayId),
      deviceId: String(payload.gateway.device_id || identity.deviceId),
      tenantId: String(payload.scope.tenant_id || ""),
      workspaceId: String(payload.scope.workspace_id || ""),
      userId: String(payload.scope.user_id || ""),
    });
    await this.tokenStore.save({
      pairingToken: undefined,
      gatewayToken: payload.gateway_token,
    });
    return payload;
  }

  async createSession(gatewayId: string): Promise<GatewaySessionPayload> {
    const tokens = await this.tokenStore.load();
    if (!tokens.gatewayToken) {
      throw new Error("Gateway token is missing. Pairing/registration is required first.");
    }
    const response = await fetch(`${this.config.apiBaseUrl}/gateway/sessions`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        gateway_id: gatewayId,
        gateway_token: tokens.gatewayToken,
      }),
    });
    if (!response.ok) {
      throw new Error(`Gateway session establishment failed with status ${response.status}`);
    }
    const payload = (await response.json()) as GatewaySessionPayload;
    await this.tokenStore.save({
      sessionId: payload.session_id,
      sessionToken: payload.session_token,
    });
    await this.checkpoints.save({
      sessionId: payload.session_id,
      sessionExpiresAt: payload.expires_at,
    });
    await persistDeviceIdentityScope(this.db, {
      gatewayId,
      tenantId: String(payload.scope.tenant_id || ""),
      workspaceId: String(payload.scope.workspace_id || ""),
      userId: String(payload.scope.user_id || ""),
    });
    return payload;
  }

  async connect(
    identity: GatewayDeviceIdentity,
    runtimeMetadata: GatewayRuntimeMetadata,
  ): Promise<GatewaySessionPayload> {
    const session = await this.createSession(identity.gatewayId);
    try {
      const wsUrl = assertWebSocketUrl(session.ws_url);
      this.socket = await this.openSocket(wsUrl, session.session_token);
      this._connectionStartedAt = Date.now();
      this.socket.onmessage = (event) => {
        void this.handleIncomingFrame(typeof event.data === "string" ? event.data : String(event.data));
      };
      this.socket.onclose = async (event) => {
        const closeCode = Number(event.code || 1000);
        const closeContext: CloseCodeContext = {
          connectionAgeMs: this._connectionStartedAt ? Date.now() - this._connectionStartedAt : undefined,
          heartbeatAgeMs: this._lastHeartbeatResponseAt ? Date.now() - this._lastHeartbeatResponseAt : undefined,
        };
        const classification = classifyCloseCode(closeCode, closeContext);
        const reason =
          this.socketFailureReason ||
          String(event.reason || "").trim() ||
          classification.reason;
        this.socketFailureReason = null;
        this.heartbeatLoop.stop();
        this.activeScope = null;
        this.socket = null;
        this._connectionStartedAt = null;
        this._lastHeartbeatResponseAt = null;
        await this.journal.append("system", "gateway.socket.closed", {
          code: closeCode,
          reason: classification.reason,
          probableCause: classification.probableCause,
          connectionAgeMs: closeContext.connectionAgeMs,
          heartbeatAgeMs: closeContext.heartbeatAgeMs,
        });
        await this.handleSocketFailure(reason);
        await this.personalChannelRuntimes.handleGatewayDisconnected(reason);
      };

      const connectResponse = await this.sendRequest(
        "gateway.connect",
        {
          gateway_version: runtimeMetadata.gatewayVersion,
          device_metadata: runtimeMetadata.deviceMetadata,
          requested_capabilities: runtimeMetadata.requestedCapabilities,
          journal_cursor: await this.journal.lastCursor(),
          checkpoint_cursor: (await this.checkpoints.load()).lastAck ?? 0,
        },
        session.scope,
        {
          replayable: false,
          persistOutbox: false,
          timeoutMs: this.requestTimeoutMsFor("gateway.connect", session.heartbeat_interval_seconds * 1000),
        },
      );
      if (!connectResponse.ok) {
        throw new Error(connectResponse.error?.message || "Gateway connect request was rejected.");
      }
      const serverProtocolVersion = connectResponse.protocolVersion ??
        (connectResponse.payload as Record<string, unknown> | undefined)?.["protocol_version"];
      if (serverProtocolVersion && !SUPPORTED_PROTOCOL_VERSIONS.includes(serverProtocolVersion as typeof SUPPORTED_PROTOCOL_VERSIONS[number])) {
        const versionStr = String(serverProtocolVersion ?? "undefined");
        this.socketFailureReason = `unsupported_protocol_version:${versionStr}`;
        this.socket.close();
        throw new Error(
          `Gateway server protocol version "${versionStr}" is not supported. Supported versions: ${SUPPORTED_PROTOCOL_VERSIONS.join(", ")}`,
        );
      }
      this.activeScope = session.scope;
      this.heartbeatLoop.start({
        intervalMs: session.heartbeat_interval_seconds * 1000,
        timeoutMs: this.requestTimeoutMsFor("gateway.heartbeat", session.heartbeat_interval_seconds * 1000),
        maxConsecutiveFailures: 2,
        sendHeartbeat: () => this.sendHeartbeat(session.scope, runtimeMetadata),
        onHeartbeatFailure: async (error, consecutiveFailures) => {
          await this.checkpoints.saveHealthState("degraded", {
            lastDisconnectReason: `heartbeat_failure:${error.message}`,
            lastOutboxError: error.message,
            pendingOutboxCount: (await this.outbox.summarize()).pending,
          });
          if (consecutiveFailures >= 2) {
            await this.terminateSocket(`heartbeat_failure:${error.message}`);
          }
        },
        onHeartbeatRecovered: async () => {
          await this.checkpoints.saveHealthState("online", {
            lastOutboxError: undefined,
          });
        },
      });
      await this.replayPendingOutbox(session.scope);
      await this.personalChannelRuntimes.handleGatewayConnected(session.scope);
      await this.checkpoints.markRecovered({
        sessionId: session.session_id,
        sessionExpiresAt: session.expires_at,
        healthState: "online",
        pendingOutboxCount: (await this.outbox.summarize()).pending,
      });
      this.reconnect.reset();
      return session;
    } catch (error) {
      this.heartbeatLoop.stop();
      this.activeScope = null;
      await this.tokenStore.clearSession();
      if (this.socket) {
        this.socketFailureReason = this.socketFailureReason || "connect_failed";
        try {
          this.socket.close();
        } catch {
          // ignore socket close failures during reconnect setup
        }
      }
      this.socket = null;
      throw error;
    }
  }

  async run(
    identity: GatewayDeviceIdentity,
    runtimeMetadata: GatewayRuntimeMetadata,
    options: GatewayRunOptions = {},
  ): Promise<void> {
    let afterConnectedCompleted = false;
    while (true) {
      try {
        await this.connect(identity, runtimeMetadata);
        if (options.afterConnected && !afterConnectedCompleted) {
          await options.afterConnected();
          afterConnectedCompleted = true;
        }
        await this.awaitSocketClose();
        await this.checkpoints.saveHealthState("reconnecting", {
          pendingOutboxCount: (await this.outbox.summarize()).pending,
        });
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        const decision = classifyReconnectError(error);
        await this.journal.append("system", "gateway.reconnect.error", {
          message,
          retryable: decision.retryable,
          reason: decision.reason,
        });
        if (!decision.retryable) {
          throw error;
        }
        await this.checkpoints.saveHealthState("reconnecting", {
          lastDisconnectReason: message,
          pendingOutboxCount: (await this.outbox.summarize()).pending,
        });
      }
      const delayMs = this.reconnect.nextDelayMs();
      await sleep(delayMs);
    }
  }

  async sendHeartbeat(scope: GatewayScope, runtimeMetadata: GatewayRuntimeMetadata): Promise<void> {
    const checkpoints = await this.checkpoints.load();
    const outboxSummary = await this.outbox.summarize();
    const localRunnerReady = await this.checkLocalRunnerHealth();
    const inventory = applyLocalRunnerReadiness(this.passiveInventorySnapshot ?? buildFastPassiveInventorySnapshot({
      requestedCapabilities: runtimeMetadata.requestedCapabilities,
      localRunnerReady,
    }), localRunnerReady);
    void this.refreshPassiveInventorySnapshot(runtimeMetadata.requestedCapabilities, localRunnerReady);
    const payload = buildGatewayHeartbeatPayload({
      runtimeMetadata,
      inventory,
      journalCursor: await this.journal.lastCursor(),
      checkpointCursor: checkpoints.lastAck ?? 0,
      queueDepthSummary: { ...outboxSummary },
    });
    await this.sendRequest(
      "gateway.heartbeat",
      payload,
      scope,
      {
        replayable: false,
        persistOutbox: false,
        timeoutMs: this.requestTimeoutMsFor("gateway.heartbeat", this.config.heartbeatIntervalMs),
      },
    );
    this._lastHeartbeatResponseAt = Date.now();
    await this.checkpoints.saveHealthState("online", {
      lastOutboxError: undefined,
      pendingOutboxCount: outboxSummary.pending,
    });
  }

  private async checkLocalRunnerHealth(): Promise<boolean> {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), Math.min(this.config.supervisorTimeoutMs, 750));
    try {
      const response = await fetch(`${this.config.supervisorUrl}/health`, {
        method: "GET",
        signal: controller.signal,
      });
      return response.ok;
    } catch {
      return false;
    } finally {
      clearTimeout(timeout);
    }
  }

  private async refreshPassiveInventorySnapshot(
    requestedCapabilities: string[],
    localRunnerReady: boolean,
  ): Promise<void> {
    if (this.passiveInventoryRefresh) {
      return this.passiveInventoryRefresh;
    }
    this.passiveInventoryRefresh = collectPassiveInventorySnapshot({
      requestedCapabilities,
      localRunnerReady,
    })
      .then((snapshot) => {
        this.passiveInventorySnapshot = snapshot;
      })
      .catch(async (error) => {
        await this.journal.append("system", "gateway.inventory.refresh_failed", {
          error: error instanceof Error ? error.message : String(error),
        });
      })
      .finally(() => {
        this.passiveInventoryRefresh = null;
      });
    return this.passiveInventoryRefresh;
  }

  async sendStateUpdate(scope: GatewayScope, payload: Record<string, unknown>): Promise<void> {
    await this.sendRequest("gateway.state.update", payload, scope);
  }

  async publishStateUpdate(payload: Record<string, unknown>): Promise<void> {
    if (!this.activeScope) {
      throw new Error("Gateway scope is not active.");
    }
    await this.sendStateUpdate(this.activeScope, payload);
  }

  async publishEvent(
    type: "channel.inbound",
    payload: GatewayChannelInboundPayload | Record<string, unknown>,
  ): Promise<void> {
    if (!this.activeScope) {
      await this.journal.append("outbound", type, {
        type,
        payload,
        offline: true,
        reason: "missing_active_scope",
      });
      throw new Error("Gateway scope is not active.");
    }
    const checkpoints = await this.checkpoints.load();
    const nextSeq = Math.max(Number(checkpoints.lastClientSeq ?? 0), 0) + 1;
    const frame: GatewayEventEnvelope = {
      kind: "event",
      protocolVersion: PROTOCOL_VERSION,
      type,
      seq: nextSeq,
      ack: checkpoints.lastServerSeq ?? checkpoints.lastAck ?? 0,
      ts: new Date().toISOString(),
      scope: this.activeScope,
      payload: payload as Record<string, unknown>,
    };
    await this.journal.append("outbound", type, frame as unknown as Record<string, unknown>);
    await this.checkpoints.save({ lastClientSeq: nextSeq });
    const requestId = `event:${type}:${nextSeq}`;
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
      await this.outbox.enqueue(requestId, type, payload as Record<string, unknown>, {
        replayable: true,
      });
      await this.outbox.markForReplay(requestId, "Gateway socket is not connected.");
      return;
    }
    const encoded = encodeFrame(frame);
    if (typeof encoded === "string") {
      try {
        this.socket.send(encoded);
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        await this.outbox.enqueue(requestId, type, payload as Record<string, unknown>, {
          replayable: true,
        });
        await this.outbox.markForReplay(requestId, message);
        throw error;
      }
    }
  }

  async disconnect(scope: GatewayScope, reason = "shutdown"): Promise<void> {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
      return;
    }
    try {
      await this.sendRequest("gateway.disconnect", { reason }, scope, {
        replayable: false,
        persistOutbox: false,
        timeoutMs: this.requestTimeoutMsFor("gateway.disconnect", this.config.heartbeatIntervalMs),
      });
    } finally {
      this.heartbeatLoop.stop();
      this.activeScope = null;
      this.socketFailureReason = reason;
      this.socket.close();
      this.socket = null;
      await this.tokenStore.clearSession();
      await this.personalChannelRuntimes.handleGatewayDisconnected(reason);
      await this.checkpoints.saveHealthState("offline", {
        lastDisconnectReason: reason,
        pendingOutboxCount: (await this.outbox.summarize()).pending,
        resumeReady: false,
      });
    }
  }

  private async openSocket(url: string, sessionToken: string): Promise<WebSocket> {
    return new Promise<WebSocket>((resolve, reject) => {
      const socket = new WebSocket(url, [
        "empyralis.gateway.v1",
        `empyralis.gateway.session.${sessionToken}`,
      ]);
      const timeout = setTimeout(() => {
        socket.onopen = null;
        socket.onerror = null;
        socket.close();
        reject(new Error(`WebSocket connection timed out for ${url}`));
      }, 10_000);
      socket.onopen = () => {
        clearTimeout(timeout);
        resolve(socket);
      };
      socket.onerror = () => {
        clearTimeout(timeout);
        reject(new Error(`WebSocket connection failed for ${url}`));
      };
    });
  }

  private async awaitSocketClose(): Promise<void> {
    if (!this.socket) {
      return;
    }
    await new Promise<void>((resolve) => {
      const socket = this.socket;
      if (!socket) {
        resolve();
        return;
      }
      let resolved = false;
      const finish = () => {
        if (resolved) {
          return;
        }
        resolved = true;
        clearInterval(closePoll);
        resolve();
      };
      const closePoll = setInterval(() => {
        if (!this.socket || socket.readyState === WebSocket.CLOSED || socket.readyState === WebSocket.CLOSING) {
          finish();
        }
      }, 1_000);
      const previousOnClose = socket.onclose;
      socket.onclose = (event) => {
        if (!previousOnClose) {
          finish();
          return;
        }
        void Promise.resolve(previousOnClose.call(socket, event)).finally(finish);
      };
    });
  }

  private async sendRequest(
    messageType: GatewayRequestEnvelope["type"],
    payload: Record<string, unknown>,
    scope: GatewayScope,
    options: RequestDispatchOptions = {},
  ): Promise<GatewayResponseEnvelope> {
    const replayable =
      options.replayable ??
      SAFE_REPLAY_MESSAGE_TYPES.has(messageType);
    if (replayable && DANGEROUS_MESSAGE_TYPES.has(messageType) && options.replayable === true) {
      // Explicitly requested replay for a dangerous type — require idempotencyKey
      const idempotencyKey =
        typeof payload === "object" && payload !== null && "idempotency_key" in payload
          ? String((payload as Record<string, unknown>).idempotency_key || "").trim()
          : "";
      if (!idempotencyKey) {
        throw new Error(
          `Replayable dangerous request "${messageType}" requires an idempotency_key in the payload.`,
        );
      }
    }
    const persistOutbox = options.persistOutbox ?? replayable;
    const requestId = String(options.requestId || crypto.randomUUID()).trim();
    const frame: GatewayRequestEnvelope = {
      kind: "request",
      protocolVersion: PROTOCOL_VERSION,
      id: requestId,
      type: messageType,
      ts: new Date().toISOString(),
      scope,
      payload,
    };
    return this.dispatchRequestFrame(frame, {
      replayable,
      persistOutbox,
      timeoutMs: options.timeoutMs,
    });
  }

  private async dispatchRequestFrame(
    frame: GatewayRequestEnvelope,
    options: {
      replayable: boolean;
      persistOutbox: boolean;
      timeoutMs?: number;
    },
  ): Promise<GatewayResponseEnvelope> {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
      throw new Error("Gateway socket is not connected.");
    }
    if (options.persistOutbox) {
      const current = await this.outbox.get(frame.id);
      if (current) {
        await this.outbox.markAttemptStarted(frame.id);
      } else {
        await this.outbox.enqueue(frame.id, frame.type, frame.payload, {
          replayable: options.replayable,
        });
      }
    }
    await this.journal.append("outbound", frame.type, frame as unknown as Record<string, unknown>);
    const responsePromise = this.trackPendingResponse(
      frame.id,
      frame.type,
      options.replayable,
      options.persistOutbox,
      options.timeoutMs,
    );
    const encoded = encodeFrame(frame);
    if (typeof encoded !== "string") {
      const message = encoded.ok === false ? encoded.error : "Frame encoding failed";
      this.clearPendingResponse(frame.id, new Error(message));
      if (options.persistOutbox) {
        if (options.replayable) {
          await this.outbox.markForReplay(frame.id, message);
        } else {
          await this.outbox.markAttemptFailed(frame.id, message);
        }
      }
      throw new Error(message);
    }
    try {
      this.socket.send(encoded);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      this.clearPendingResponse(frame.id, new Error(message));
      if (options.persistOutbox) {
        if (options.replayable) {
          await this.outbox.markForReplay(frame.id, message);
        } else {
          await this.outbox.markAttemptFailed(frame.id, message);
        }
      }
      throw error;
    }
    return responsePromise;
  }

  private trackPendingResponse(
    requestId: string,
    messageType: GatewayRequestEnvelope["type"],
    replayable: boolean,
    persistedOutbox: boolean,
    timeoutMs?: number,
  ): Promise<GatewayResponseEnvelope> {
    return new Promise<GatewayResponseEnvelope>((resolve, reject) => {
      const timeout = setTimeout(() => {
        this.pendingResponses.delete(requestId);
        const error = new Error(`Gateway request timed out: ${messageType}`);
        reject(error);
        void this.handleRequestTimeout(requestId, replayable, persistedOutbox, error);
      }, this.requestTimeoutMsFor(messageType, timeoutMs));
      this.pendingResponses.set(requestId, {
        messageType,
        replayable,
        timeoutHandle: timeout,
        resolve,
        reject,
      });
    });
  }

  private clearPendingResponse(requestId: string, error: Error): void {
    const pending = this.pendingResponses.get(requestId);
    if (!pending) {
      return;
    }
    clearTimeout(pending.timeoutHandle);
    this.pendingResponses.delete(requestId);
    pending.reject(error);
  }

  private async handleRequestTimeout(
    requestId: string,
    replayable: boolean,
    persistedOutbox: boolean,
    error: Error,
  ): Promise<void> {
    this.clearPendingResponse(requestId, error);
    if (persistedOutbox) {
      if (replayable) {
        await this.outbox.markForReplay(requestId, error.message);
      } else {
        await this.outbox.markUncertain(requestId, `Request timed out: ${error.message}`);
      }
    }
    await this.checkpoints.saveHealthState("degraded", {
      lastDisconnectReason: `request_timeout:${requestId}`,
      lastOutboxError: error.message,
      pendingOutboxCount: (await this.outbox.summarize()).pending,
    });
  }

  private async terminateSocket(reason: string): Promise<void> {
    if (!this.socket) {
      return;
    }
    this.socketFailureReason = reason;
    try {
      this.socket.close();
    } catch {
      // ignore close races while the reconnect loop is already taking over
    }
  }

  private async replayPendingOutbox(scope: GatewayScope): Promise<void> {
    const replayableItems = await this.outbox.listReplayablePending();
    if (!replayableItems.length) {
      return;
    }
    await this.journal.append("system", "gateway.outbox.replay.start", {
      count: replayableItems.length,
    });
    for (const item of replayableItems) {
      if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
        throw new Error("Gateway socket closed before pending outbox replay finished.");
      }
      await this.replayOutboxItem(item, scope);
    }
    await this.journal.append("system", "gateway.outbox.replay.complete", {
      count: replayableItems.length,
    });
  }

  private async replayOutboxItem(item: GatewayOutboxItem, scope: GatewayScope): Promise<void> {
    await this.outbox.markAttemptStarted(item.requestId);
    if (item.messageType === "channel.inbound") {
      const checkpoints = await this.checkpoints.load();
      const nextSeq = Math.max(Number(checkpoints.lastClientSeq ?? 0), 0) + 1;
      const frame: GatewayEventEnvelope = {
        kind: "event",
        protocolVersion: PROTOCOL_VERSION,
        type: "channel.inbound",
        seq: nextSeq,
        ack: checkpoints.lastServerSeq ?? checkpoints.lastAck ?? 0,
        ts: new Date().toISOString(),
        scope,
        payload: dict(item.payload),
      };
      await this.journal.append("outbound", "channel.inbound", frame as unknown as Record<string, unknown>);
      await this.checkpoints.save({ lastClientSeq: nextSeq });
      const encoded = encodeFrame(frame);
      if (typeof encoded !== "string") {
        const message = encoded.ok === false ? encoded.error : "Frame encoding failed";
        await this.outbox.markForReplay(item.requestId, message);
        throw new Error(message);
      }
      this.socket?.send(encoded);
      await this.outbox.acknowledge(item.requestId);
      return;
    }
    const frame: GatewayRequestEnvelope = {
      kind: "request",
      protocolVersion: PROTOCOL_VERSION,
      id: item.requestId,
      type: item.messageType as GatewayRequestEnvelope["type"],
      ts: new Date().toISOString(),
      scope,
      payload: dict(item.payload),
    };
    await this.dispatchRequestFrame(frame, {
      replayable: item.replayable,
      persistOutbox: true,
      timeoutMs: this.requestTimeoutMsFor(frame.type),
    });
  }

  private requestTimeoutMsFor(
    messageType: GatewayRequestEnvelope["type"],
    explicitTimeoutMs?: number,
  ): number {
    const minimum = Math.max(this.config.heartbeatIntervalMs, 10_000);
    if (Number.isFinite(explicitTimeoutMs) && Number(explicitTimeoutMs) > 0) {
      return Math.max(Number(explicitTimeoutMs), minimum);
    }
    if (messageType === "gateway.connect" || messageType === "gateway.disconnect") {
      return Math.max(minimum, 15_000);
    }
    if (messageType === "gateway.heartbeat") {
      return Math.max(minimum, 12_000);
    }
    return Math.max(minimum, 20_000);
  }

  private async handleSocketFailure(reason: string): Promise<void> {
    await this.tokenStore.clearSession();
    const pending = [...this.pendingResponses.entries()];
    this.pendingResponses.clear();
    for (const [requestId, entry] of pending) {
      clearTimeout(entry.timeoutHandle);
      entry.reject(new Error(`Gateway socket closed before response: ${reason}`));
      if (entry.replayable) {
        await this.outbox.markForReplay(requestId, `Gateway socket closed before response: ${reason}`);
      } else if (await this.outbox.get(requestId)) {
        await this.outbox.markUncertain(requestId, `Gateway socket closed before response: ${reason}`);
      }
    }
    const summary = await this.outbox.summarize();
    await this.checkpoints.saveHealthState("offline", {
      lastDisconnectReason: reason,
      pendingOutboxCount: summary.pending + summary.failed,
      uncertainOutboxCount: summary.uncertain,
      lastOutboxError: reason,
      resumeReady: false,
    });
  }

  private async handleIncomingFrame(raw: string): Promise<void> {
    const result = decodeFrame(raw);
    if (!result.ok) {
      await this.journal.append("system", "gateway.frame.invalid", {
        error: result.error,
      });
      return;
    }
    const frame = result.frame;
    if (frame.kind === "request") {
      await this.handleServerRequest(frame);
      return;
    }
    if (frame.kind === "response") {
      await this.journal.append("inbound", "response", frame as unknown as Record<string, unknown>);
      await this.outbox.acknowledge(frame.id);
      const pending = this.pendingResponses.get(frame.id);
      if (pending) {
        clearTimeout(pending.timeoutHandle);
        this.pendingResponses.delete(frame.id);
        pending.resolve(frame);
      }
      return;
    }
    if (frame.kind === "event") {
      await this.handleEvent(frame);
    }
  }

  private async handleServerRequest(frame: GatewayRequestEnvelope): Promise<void> {
    await this.journal.append("inbound", frame.type, frame as unknown as Record<string, unknown>);
    try {
      if (frame.type === "tool.invoke") {
        const payload = await this.capabilityRouter.handleToolInvoke(
          frame as unknown as GatewayRequestEnvelope<GatewayToolInvokePayload>,
        );
        await this.sendResponse(frame.id, true, payload);
        return;
      }
      if (frame.type === "gateway.probe") {
        await this.sendResponse(frame.id, true, {
          status: "ready",
        });
        return;
      }
      if (frame.type === "tool.interrupt") {
        const payload = await this.capabilityRouter.handleToolInterrupt(
          frame as unknown as GatewayRequestEnvelope<GatewayToolInterruptPayload>,
        );
        await this.sendResponse(frame.id, true, payload);
        return;
      }
      if (frame.type === "channel.outbound") {
        const channelPayload = frame as unknown as GatewayRequestEnvelope<GatewayChannelOutboundPayload>;
        const channelKey = String(channelPayload.payload?.channel_key || "").trim();
        const runtime = this.personalChannelRuntimes.runtimeForChannel(channelKey);
        if (!runtime) {
          throw new Error(`Unsupported personal channel key: ${channelKey || "unknown"}`);
        }
        const payload = await runtime.handleChannelOutbound(channelPayload);
        await this.sendResponse(frame.id, true, payload ?? {});
        return;
      }
      await this.sendResponse(frame.id, false, undefined, {
        code: "unsupported_message_type",
        message: `Unsupported message type: ${frame.type}`,
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      await this.sendResponse(frame.id, false, undefined, {
        code: frame.type,
        message,
      });
    }
  }

  private async sendResponse(
    requestId: string,
    ok: boolean,
    payload?: Record<string, unknown>,
    error?: GatewayResponseEnvelope["error"],
  ): Promise<void> {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
      throw new Error("Gateway socket is not connected.");
    }
    const frame: GatewayResponseEnvelope = {
      kind: "response",
      protocolVersion: PROTOCOL_VERSION,
      id: requestId,
      ok,
      ts: new Date().toISOString(),
    };
    if (ok) {
      frame.payload = compactGatewayResponsePayload(payload) ?? {};
    } else {
      frame.error = error ?? { message: "Unknown gateway request failure." };
    }
    await this.journal.append("outbound", "response", frame as unknown as Record<string, unknown>);
    const encoded = encodeFrame(frame);
    if (typeof encoded === "string") {
      this.socket.send(encoded);
    }
  }

  private async handleEvent(frame: GatewayEventEnvelope): Promise<void> {
    await this.journal.append("inbound", frame.type, frame as unknown as Record<string, unknown>);
    await this.checkpoints.save({
      lastAck: frame.ack ?? frame.seq,
      lastServerSeq: frame.seq,
    });
    if (frame.type === "gateway.presence") {
      await this.db.writeJson("presence.json", frame.payload);
    }
    if (frame.type === "gateway.hello") {
      await this.db.writeJson("hello.json", frame.payload);
      const helloVersion = frame.protocolVersion ??
        (frame.payload as Record<string, unknown> | undefined)?.["protocol_version"];
      if (helloVersion && !SUPPORTED_PROTOCOL_VERSIONS.includes(helloVersion as typeof SUPPORTED_PROTOCOL_VERSIONS[number])) {
        const versionStr = String(helloVersion ?? "undefined");
        const error = new Error(
          `Gateway hello event protocol version "${versionStr}" is not supported. Supported versions: ${SUPPORTED_PROTOCOL_VERSIONS.join(", ")}`,
        );
        this.socketFailureReason = `unsupported_protocol_version:${versionStr}`;
        await this.journal.append("system", "gateway.protocol.version.mismatch", {
          serverVersion: versionStr,
          supportedVersions: SUPPORTED_PROTOCOL_VERSIONS,
          error: error.message,
        });
        if (this.socket) {
          this.socket.close();
        }
      }
    }
  }
}

function dict(value: Record<string, unknown> | undefined): Record<string, unknown> {
  return { ...(value || {}) };
}

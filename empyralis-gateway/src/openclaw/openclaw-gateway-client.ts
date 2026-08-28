/**
 * A live, authenticated WebSocket session to the OpenClaw gateway running
 * on this same machine, used for exactly one thing: invoking
 * `message.action` to deliver an Empyralis agent's reply out through an
 * OpenClaw-transported channel (the OpenClaw channel adoption step 3).
 *
 * WHY A PERSISTENT SOCKET AND NOT AN HTTP CALL
 * --------------------------------------------
 * OpenClaw's `admin-http-rpc` allowlist contains no send/message method, so
 * a stateless cloud caller cannot deliver over plain HTTP at all. Delivery
 * is only reachable through the Gateway WS RPC, from a process on the same
 * box. That is this file. Verified against openclaw@2026.6.10's shipped
 * bundle and live against a real gateway under an isolated `--profile`.
 *
 * THE HANDSHAKE (transcribed from `dist/client-*.js` + `dist/server-ws-runtime-*.js`)
 * -----------------------------------------------------------------------
 *   socket opens
 *     <- {type:"event", event:"connect.challenge", payload:{nonce, ts}}
 *     -> {type:"req", id, method:"connect", params:{minProtocol,maxProtocol,
 *          client:{...}, role:"operator", scopes:[...], auth:{token}}}
 *     <- {type:"res", id, ok:true, payload:<hello-ok>}
 * The `device` block of ConnectParams is optional and deliberately omitted:
 * it exists for device-identity pairing, and this client authenticates with
 * the gateway's own shared token instead. `scopes` asks for exactly
 * `operator.write` — see OPENCLAW_REQUIRED_SCOPES for why never admin.
 *
 * SECURITY POSTURE (same boundary the inbound listener documents, other way)
 * -----------------------------------------------------------------------
 *   loopback  the URL is REFUSED unless its host is loopback. There is no
 *             env escape hatch: an OpenClaw gateway reachable off-box is
 *             the exact configuration that leaked tens of thousands of
 *             installs' API keys and chat history.
 *   auth      token REQUIRED. No token, no client, no advertisement — the
 *             same fail-closed shape as OpenClawInboundListener's
 *             constructor guard.
 *   logging   the token is never logged, never journaled, never echoed in
 *             an error. `redactForLog` below is the only formatter used.
 *   trust     OpenClaw is authenticated, not trusted. Everything it says
 *             about an inbound message is re-decided cloud-side; everything
 *             it says about an outbound send is reduced to a closed set of
 *             structured outcomes (see outbound-payload.ts).
 *
 * RECONNECT VOCABULARY is the gateway's own: ../cloud/reconnect.ts's
 * ReconnectBackoff and classifyCloseCode, the same primitives
 * GatewayWsClient uses. Same process, same package — reused directly rather
 * than re-derived. (What is NOT reused is anything from OpenClaw's own
 * client: that lives in another process, ships as a bundled dist file with
 * no stable entry point, and vendoring their core is explicitly forbidden.)
 */

import crypto from "crypto";
import WebSocket from "ws";

import { ReconnectBackoff, classifyCloseCode } from "../cloud/reconnect";
import {
  OPENCLAW_MESSAGE_ACTION_METHOD,
  OPENCLAW_REQUIRED_SCOPES,
  classifyOpenClawSendResponse,
  type OpenClawMessageActionParams,
  type OpenClawResponseFrame,
  type OpenClawSendOutcome,
} from "./outbound-payload";

/** Matches OpenClaw's own default gateway bind (`ws://127.0.0.1:18789`). */
export const OPENCLAW_GATEWAY_DEFAULT_URL = "ws://127.0.0.1:18789";

/** `PROTOCOL_VERSION` / `MIN_CLIENT_PROTOCOL_VERSION` are both 4 in
 *  openclaw@2026.6.10. Sending a window rather than a point value is what
 *  their own client does, and it is what lets a patch release widen the
 *  range without us shipping a change. */
const OPENCLAW_MIN_PROTOCOL = 4;
const OPENCLAW_MAX_PROTOCOL = 4;

const CONNECT_TIMEOUT_MS = 10_000;
const DEFAULT_REQUEST_TIMEOUT_MS = 20_000;
/** Delivery retries share ONE idempotencyKey, which OpenClaw dedupes on
 *  (`resolveGatewayInflightRequest`), so a retry can never double-send.
 *  Bounded and small: the cloud is synchronously waiting on this response
 *  inside its own `channel.outbound` request timeout. */
const MAX_SEND_ATTEMPTS = 3;
const RETRY_BASE_DELAY_MS = 400;
/** Ceiling on the backoff THIS CLIENT invents when OpenClaw did not say how
 *  long to wait. It is deliberately NOT a ceiling on a wait OpenClaw asked
 *  for — see waitBeforeRetry. */
const RETRY_MAX_SELF_BACKOFF_MS = 2_000;
/** The longest this client will hold the cloud's `channel.outbound` request
 *  open for ONE in-band retry wait. When OpenClaw asks for longer than this
 *  we stop retrying and hand the outcome back — never retry earlier than we
 *  were told to. See waitBeforeRetry for why that direction is the only safe
 *  one on a messaging transport. */
const RETRY_HONOUR_BUDGET_MS = 5_000;
/** How long handleChannelOutbound will wait for a socket that is mid-
 *  (re)connect before giving up. Short on purpose — a send that waits
 *  longer than this is better reported as unavailable than left hanging. */
const CONNECTION_WAIT_MS = 5_000;

const LOOPBACK_HOSTS = new Set(["127.0.0.1", "localhost", "::1", "[::1]", "0:0:0:0:0:0:0:1"]);

export interface OpenClawGatewayClientOptions {
  /** `ws://127.0.0.1:<port>`. Loopback only — enforced, not advised. */
  url: string;
  /** The OpenClaw gateway's own auth token (its `--token`), NOT the
   *  Empyralis bridge secret. Required and non-empty. Never logged. */
  token: string;
  /** Structured audit sink — the gateway's own GatewayJournal.append. */
  record?: (messageType: string, payload: Record<string, unknown>) => Promise<unknown>;
  logger?: { info?: (msg: string) => void; error?: (msg: string) => void };
  /** Test seam. Defaults to the real `ws` WebSocket. */
  createSocket?: (url: string) => WebSocket;
  requestTimeoutMs?: number;
}

export interface OpenClawConnectionState {
  configured: true;
  connected: boolean;
  /** Consecutive failed connection attempts since the last hello-ok. */
  reconnectAttempts: number;
  lastConnectedAt?: string;
  lastError?: string;
  /** OpenClaw's advertised RPC method list from hello-ok, used only to
   *  detect a gateway that genuinely cannot send (see assertCanSend). */
  serverMethods?: string[];
}

interface PendingRequest {
  resolve: (frame: OpenClawResponseFrame) => void;
  reject: (error: Error) => void;
  timer: NodeJS.Timeout;
}

/** Removes the configured token from any string before it is logged or
 *  journaled. Belt and braces: nothing in this file ever puts the token in
 *  a message, and this makes an accidental future one harmless. */
export function redactOpenClawToken(value: string, token: string): string {
  const secret = String(token || "");
  if (!secret) return value;
  return String(value ?? "").split(secret).join("<redacted>");
}

export function assertLoopbackWebSocketUrl(rawUrl: string): URL {
  let parsed: URL;
  try {
    parsed = new URL(String(rawUrl || "").trim());
  } catch {
    throw new Error(`OpenClaw gateway URL is not a valid URL: ${rawUrl}`);
  }
  if (parsed.protocol !== "ws:" && parsed.protocol !== "wss:") {
    throw new Error(`OpenClaw gateway URL must be ws:// or wss://. Got: ${parsed.protocol}`);
  }
  const host = parsed.hostname.toLowerCase();
  if (!LOOPBACK_HOSTS.has(host)) {
    throw new Error(
      `OpenClaw gateway URL must point at loopback (got host "${parsed.hostname}"). ` +
        "An OpenClaw gateway reachable off-box is never an acceptable configuration — see the OpenClaw channel adoption's SECURITY LOCKDOWN.",
    );
  }
  return parsed;
}

export class OpenClawGatewayClient {
  private socket: WebSocket | null = null;
  private helloOk = false;
  private started = false;
  private closing = false;
  private nextRequestId = 1;
  private readonly pending = new Map<string, PendingRequest>();
  private readonly backoff = new ReconnectBackoff({ minDelayMs: 1_000, maxDelayMs: 30_000 });
  private reconnectTimer: NodeJS.Timeout | null = null;
  private connectionWaiters: Array<() => void> = [];
  private state: OpenClawConnectionState = { configured: true, connected: false, reconnectAttempts: 0 };
  private connectionOpenedAt = 0;

  constructor(private readonly options: OpenClawGatewayClientOptions) {
    if (!String(options.token || "").trim()) {
      // Fail closed at construction, exactly like OpenClawInboundListener.
      // There is no "no token configured, connect anyway" mode.
      throw new Error(
        "OpenClaw gateway client refuses to start without a gateway auth token — an unauthenticated control connection is never acceptable, even on loopback.",
      );
    }
    assertLoopbackWebSocketUrl(options.url);
  }

  getState(): OpenClawConnectionState {
    return { ...this.state, serverMethods: this.state.serverMethods ? [...this.state.serverMethods] : undefined };
  }

  isConnected(): boolean {
    return this.helloOk && this.socket !== null && this.socket.readyState === WebSocket.OPEN;
  }

  private safeLog(message: string): string {
    return redactOpenClawToken(message, this.options.token);
  }

  private async record(messageType: string, payload: Record<string, unknown>): Promise<void> {
    try {
      await this.options.record?.(messageType, payload);
    } catch {
      // Auditing must never take delivery down.
    }
  }

  async start(): Promise<void> {
    if (this.started) return;
    this.started = true;
    this.closing = false;
    this.openSocket();
  }

  async stop(): Promise<void> {
    this.started = false;
    this.closing = true;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.failAllPending(new Error("OpenClaw gateway client stopped."));
    const socket = this.socket;
    this.socket = null;
    this.helloOk = false;
    this.state = { ...this.state, connected: false };
    this.releaseConnectionWaiters();
    if (socket) {
      try {
        socket.close(1000, "shutdown");
      } catch {
        // already closing
      }
    }
  }

  private failAllPending(error: Error): void {
    for (const [, pending] of this.pending) {
      clearTimeout(pending.timer);
      pending.reject(error);
    }
    this.pending.clear();
  }

  private releaseConnectionWaiters(): void {
    const waiters = this.connectionWaiters;
    this.connectionWaiters = [];
    for (const waiter of waiters) waiter();
  }

  private openSocket(): void {
    if (this.closing || this.socket) return;
    let socket: WebSocket;
    try {
      socket = this.options.createSocket
        ? this.options.createSocket(this.options.url)
        : new WebSocket(this.options.url);
    } catch (error) {
      this.onConnectionFailure(error);
      return;
    }
    this.socket = socket;
    this.helloOk = false;
    this.connectionOpenedAt = Date.now();

    const connectTimer = setTimeout(() => {
      if (!this.helloOk) {
        // Never reached hello-ok. Close and let the backoff path retry;
        // OpenClaw itself closes an unauthenticated socket on its own
        // handshake timeout, so this only fires when it went silent.
        try {
          socket.close(1008, "connect timeout");
        } catch {
          // ignore
        }
      }
    }, CONNECT_TIMEOUT_MS);
    connectTimer.unref?.();

    socket.on("message", (data: WebSocket.RawData) => {
      this.handleFrame(String(data));
    });
    socket.on("error", (error: Error) => {
      this.state = { ...this.state, lastError: this.safeLog(error.message) };
    });
    socket.on("close", (code: number) => {
      clearTimeout(connectTimer);
      if (this.socket !== socket) return;
      this.socket = null;
      const wasConnected = this.helloOk;
      this.helloOk = false;
      this.state = { ...this.state, connected: false };
      this.failAllPending(new Error(`OpenClaw gateway connection closed (code ${code}).`));
      this.releaseConnectionWaiters();
      const classification = classifyCloseCode(code, { connectionAgeMs: Date.now() - this.connectionOpenedAt });
      void this.record("openclaw.outbound.disconnected", {
        code,
        reason: classification.reason,
        probable_cause: classification.probableCause,
        was_connected: wasConnected,
      });
      this.scheduleReconnect();
    });
  }

  private onConnectionFailure(error: unknown): void {
    const message = this.safeLog(error instanceof Error ? error.message : String(error));
    this.state = {
      ...this.state,
      connected: false,
      reconnectAttempts: this.state.reconnectAttempts + 1,
      lastError: message,
    };
    void this.record("openclaw.outbound.connect_failed", { error: message });
    this.releaseConnectionWaiters();
    this.scheduleReconnect();
  }

  private scheduleReconnect(): void {
    if (!this.started || this.closing || this.reconnectTimer) return;
    const delay = this.backoff.nextDelayMs();
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.openSocket();
    }, delay);
    this.reconnectTimer.unref?.();
  }

  private handleFrame(raw: string): void {
    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(raw) as Record<string, unknown>;
    } catch {
      return;
    }
    const type = String(parsed.type || "");
    if (type === "event") {
      if (String(parsed.event || "") === "connect.challenge") {
        const payload = (parsed.payload || {}) as Record<string, unknown>;
        const nonce = String(payload.nonce || "").trim();
        if (!nonce) {
          // Their own client treats this as fatal for the socket; so do we.
          try {
            this.socket?.close(1008, "connect challenge missing nonce");
          } catch {
            // ignore
          }
          return;
        }
        void this.sendConnect(nonce);
      }
      return;
    }
    if (type === "res") {
      const id = String(parsed.id || "");
      const pending = this.pending.get(id);
      if (!pending) return;
      this.pending.delete(id);
      clearTimeout(pending.timer);
      pending.resolve(parsed as OpenClawResponseFrame);
    }
  }

  private async sendConnect(nonce: string): Promise<void> {
    // `nonce` is echoed back only inside the optional `device` signature
    // block, which this client does not use (token auth). It is still read
    // and required, because a challenge without one means the peer is not
    // an OpenClaw gateway.
    void nonce;
    const params = {
      minProtocol: OPENCLAW_MIN_PROTOCOL,
      maxProtocol: OPENCLAW_MAX_PROTOCOL,
      client: {
        // `gateway-client` is one of ConnectParams' enumerated client ids;
        // `backend` one of its enumerated modes. Both from their schema.
        id: "gateway-client",
        displayName: "Empyralis Gateway",
        version: "1",
        platform: process.platform,
        mode: "backend",
      },
      caps: [] as string[],
      role: "operator",
      scopes: [...OPENCLAW_REQUIRED_SCOPES],
      auth: { token: this.options.token },
    };
    try {
      const response = await this.request("connect", params, CONNECT_TIMEOUT_MS);
      if (response.ok !== true) {
        const error = (response.error || {}) as Record<string, unknown>;
        const message = this.safeLog(String(error.message || "connect rejected"));
        this.state = {
          ...this.state,
          connected: false,
          reconnectAttempts: this.state.reconnectAttempts + 1,
          lastError: `connect rejected (${String(error.code || "unknown")}): ${message}`,
        };
        // Journaled every attempt, deliberately, and deliberately still
        // retried. GatewayWsClient's classifyReconnectError treats a CLOUD
        // auth failure as non-retryable, but this peer is different: the
        // OpenClaw gateway is a local process that provisioning (step 4) may
        // re-key or restart at any moment, and a client that gave up
        // permanently on one token mismatch would stay dead until someone
        // restarted the Empyralis gateway too. Capped backoff plus a journal
        // line per attempt keeps it self-healing AND visible — never silent.
        // Verified live 2026-08-08: a wrong token produces exactly this
        // ("unauthorized: gateway token mismatch"), never a hang.
        await this.record("openclaw.outbound.connect_rejected", {
          code: String(error.code || "unknown"),
          message,
        });
        try {
          this.socket?.close(1008, "connect failed");
        } catch {
          // ignore
        }
        return;
      }
      const payload = (response.payload || {}) as Record<string, unknown>;
      const features = (payload.features || {}) as Record<string, unknown>;
      const methods = Array.isArray(features.methods) ? features.methods.map(String) : undefined;
      this.helloOk = true;
      this.backoff.reset();
      this.state = {
        configured: true,
        connected: true,
        reconnectAttempts: 0,
        lastConnectedAt: new Date().toISOString(),
        lastError: undefined,
        serverMethods: methods,
      };
      this.releaseConnectionWaiters();
      this.options.logger?.info?.("openclaw gateway session established (scopes: operator.write)");
      await this.record("openclaw.outbound.connected", {
        // Deliberately no token, no url credentials, no auth payload.
        advertises_message_action: methods ? methods.includes(OPENCLAW_MESSAGE_ACTION_METHOD) : null,
      });
    } catch (error) {
      this.onConnectionFailure(error);
      try {
        this.socket?.close(1008, "connect failed");
      } catch {
        // ignore
      }
    }
  }

  /** Sends one request frame and awaits its response frame. Rejects only on
   *  transport failure/timeout; a protocol-level `ok:false` resolves, so the
   *  caller can classify it structurally. */
  private request(
    method: string,
    params: Record<string, unknown>,
    timeoutMs: number,
  ): Promise<OpenClawResponseFrame> {
    const socket = this.socket;
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      return Promise.reject(new Error("OpenClaw gateway socket is not connected."));
    }
    const id = `empyralis-${this.nextRequestId++}-${crypto.randomBytes(6).toString("hex")}`;
    return new Promise<OpenClawResponseFrame>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`OpenClaw gateway request timed out after ${timeoutMs}ms (${method}).`));
      }, timeoutMs);
      timer.unref?.();
      this.pending.set(id, { resolve, reject, timer });
      try {
        socket.send(JSON.stringify({ type: "req", id, method, params }));
      } catch (error) {
        this.pending.delete(id);
        clearTimeout(timer);
        reject(error instanceof Error ? error : new Error(String(error)));
      }
    });
  }

  /** Resolves once a session exists, or after CONNECTION_WAIT_MS. Never
   *  throws — the caller decides what an absent session means. */
  private async waitForConnection(): Promise<void> {
    if (this.isConnected()) return;
    if (!this.started) await this.start();
    await new Promise<void>((resolve) => {
      let settled = false;
      const done = () => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        resolve();
      };
      const timer = setTimeout(done, CONNECTION_WAIT_MS);
      timer.unref?.();
      this.connectionWaiters.push(done);
    });
  }

  /**
   * Invokes `message.action` with a bounded retry on transient outcomes,
   * always under the caller's own idempotency key so a retry can never
   * double-send. Returns a structured outcome — this method does not throw
   * for a rejection; only the runtime above it decides what a rejection
   * means to the cloud.
   */
  async sendMessageAction(request: OpenClawMessageActionParams): Promise<OpenClawSendOutcome> {
    const timeoutMs = this.options.requestTimeoutMs ?? DEFAULT_REQUEST_TIMEOUT_MS;
    let lastOutcome: OpenClawSendOutcome | null = null;

    for (let attempt = 1; attempt <= MAX_SEND_ATTEMPTS; attempt += 1) {
      await this.waitForConnection();
      if (!this.isConnected()) {
        lastOutcome = {
          status: "transient",
          code: "OPENCLAW_GATEWAY_DISCONNECTED",
          message:
            "No live session to the local OpenClaw gateway. It is not running, not reachable on loopback, or rejected this client's token.",
        };
      } else {
        try {
          const frame = await this.request(
            OPENCLAW_MESSAGE_ACTION_METHOD,
            request as unknown as Record<string, unknown>,
            timeoutMs,
          );
          lastOutcome = classifyOpenClawSendResponse(frame);
        } catch (error) {
          // Transport-level failure (socket died mid-request, or the request
          // timed out). Transient by construction — the idempotency key
          // makes the retry safe even if OpenClaw actually delivered.
          lastOutcome = {
            status: "transient",
            code: "OPENCLAW_GATEWAY_TRANSPORT_ERROR",
            message: this.safeLog(error instanceof Error ? error.message : String(error)),
          };
        }
      }

      if (lastOutcome.status === "delivered" || lastOutcome.status === "rejected") {
        return lastOutcome;
      }
      await this.record("openclaw.outbound.attempt_failed", {
        channel: request.channel,
        idempotency_key: request.idempotencyKey,
        attempt,
        code: lastOutcome.code,
        // OpenClaw's own sentence, kept for a human reading the journal.
        // Never matched on anywhere.
        detail: this.safeLog(lastOutcome.message),
      });
      if (attempt < MAX_SEND_ATTEMPTS) {
        const waited = await this.waitBeforeRetry(attempt, lastOutcome.retryAfterMs);
        if (!waited) {
          // OpenClaw asked us to wait longer than one in-band retry may hold
          // the cloud's request open. Stop here and return the outcome (which
          // carries retryAfterMs) instead of sleeping less and trying again.
          await this.record("openclaw.outbound.retry_deferred", {
            channel: request.channel,
            idempotency_key: request.idempotencyKey,
            attempt,
            code: lastOutcome.code,
            retry_after_ms: lastOutcome.retryAfterMs ?? null,
          });
          break;
        }
      }
    }

    return (
      lastOutcome ?? {
        status: "transient",
        code: "OPENCLAW_GATEWAY_NO_OUTCOME",
        message: "OpenClaw send produced no outcome.",
      }
    );
  }

  /**
   * Waits before the next `message.action` attempt, or refuses to retry.
   *
   * A SERVER-SUPPLIED `retryAfterMs` IS A FLOOR, NEVER A CEILING.
   * ------------------------------------------------------------
   * This method used to compute `Math.min(retryAfterMs ?? backoff,
   * RETRY_MAX_DELAY_MS)` — i.e. it read OpenClaw's own structured backoff
   * signal and then CLAMPED IT DOWNWARD to 2s. Told "wait 30 seconds", it
   * waited two and tried again. On a messaging transport that is the one
   * behaviour that turns a rate-limit into an account action: it is exactly
   * the shape of the incident OpenClaw's own
   * `extensions/telegram/src/sendchataction-401-backoff.ts` was written for
   * ("the infinite loop that caused Telegram to delete bots"), and adopting
   * their transport specifically to inherit that hardening while overriding
   * their backoff downward at our own seam would be the appearance of the
   * protection without the substance.
   *
   * `retryable`/`retryAfterMs` are the two structural fields this whole lane
   * is built on reading rather than string-matching (see outbound-payload.ts).
   * Honouring one and discarding the other is not a partial contract; it is
   * the wrong contract.
   *
   *   asked  <= budget   ─▶ wait max(asked, our own backoff), then retry
   *   asked  >  budget   ─▶ DO NOT RETRY. return false; the caller returns
   *                         the transient outcome (carrying retryAfterMs) and
   *                         the cloud's at-least-once layer owns the wait.
   *   not asked          ─▶ our own capped exponential backoff, as before
   *
   * Refusing to retry is strictly LESS traffic than the old clamp produced,
   * never more, so it cannot push the cloud's `channel.outbound` request past
   * its own timeout — the failure mode a longer sleep here would have had.
   *
   * @returns true when it waited and the caller should retry; false when the
   *          caller must stop retrying.
   */
  private async waitBeforeRetry(attempt: number, retryAfterMs: number | undefined): Promise<boolean> {
    const selfBackoff = Math.min(RETRY_BASE_DELAY_MS * Math.pow(2, attempt - 1), RETRY_MAX_SELF_BACKOFF_MS);
    const requested = typeof retryAfterMs === "number" && retryAfterMs > 0 ? retryAfterMs : undefined;
    if (requested !== undefined && requested > RETRY_HONOUR_BUDGET_MS) return false;
    const delay = Math.max(requested ?? 0, selfBackoff);
    if (delay <= 0) return true;
    await new Promise<void>((resolve) => {
      const timer = setTimeout(resolve, delay);
      timer.unref?.();
    });
    return true;
  }
}

/**
 * The loopback intake the OpenClaw bridge plugin POSTs to.
 *
 * CHANNEL-ADOPTION-PLAN.md step 2, gateway half. The plugin runs inside
 * OpenClaw's process (third-party code, on the customer's machine) and has
 * NO Empyralis cloud credential by design — see the bridge plugin's
 * src/config.ts. It hands each mapped inbound event to this listener over
 * loopback with a low-privilege shared secret; this listener validates,
 * maps, and republishes on the gateway's OWN already-durable
 * `channel.inbound` path (GatewayWsClient.publishEvent -> journal +
 * outbox + replay-on-reconnect). Nothing about retries, sequencing, or
 * cloud auth is reimplemented here.
 *
 * SECURITY POSTURE (this is a boundary between two processes with
 * different trust levels, on a machine we do not control):
 *
 *   bind      127.0.0.1 ONLY, never 0.0.0.0 and never a hostname that
 *             could resolve off-box.
 *   auth      Bearer token, REQUIRED. An empty/unset token means the
 *             listener does not start at all — it never degrades to
 *             "no token configured, allow everything" the way
 *             bridges/signal-cli-bridge.ts's authorizeRequest does.
 *   compare   timing-safe.
 *   logging   the token value is never logged, never journaled, never
 *             echoed in an error body.
 *   trust     the plugin is authenticated, NOT trusted. Every addressing
 *             fact it sends is re-decided by the cloud's own three gates
 *             (personal_channels_service._OpenClawPersonalChannelHandler),
 *             which is exactly the mistake OpenClaw's own CVE record shows
 *             the cost of: a client-asserted `senderIsOwner` believed
 *             because it arrived over loopback.
 */

import crypto from "crypto";
import http from "http";

import { mapOpenClawInboundBody } from "./inbound-payload";
import type { GatewayChannelInboundPayload } from "../protocol/types";

export const OPENCLAW_INBOUND_PATH = "/openclaw/inbound";
export const OPENCLAW_HEALTH_PATH = "/openclaw/health";

/** Loopback only. A value outside this set is a configuration error, not
 *  something to normalize into something "close enough". */
const LOOPBACK_HOST = "127.0.0.1";

const MAX_BODY_BYTES = 256 * 1024;

export interface OpenClawInboundPublisher {
  publishEvent(type: "channel.inbound", payload: GatewayChannelInboundPayload): Promise<void>;
}

export interface OpenClawInboundListenerOptions {
  port: number;
  /** Shared secret with the bridge plugin (`EMPYRALIS_BRIDGE_TOKEN`).
   *  Required and non-empty — see the class doc. */
  token: string;
  publisher: OpenClawInboundPublisher;
  /** Structured audit sink — the gateway's own GatewayJournal.append. Every
   *  accepted, rejected, and dropped event is recorded here, so a silently
   *  missing message is always explainable after the fact. */
  record?: (messageType: string, payload: Record<string, unknown>) => Promise<unknown>;
  logger?: { info?: (msg: string) => void; error?: (msg: string) => void };
}

function timingSafeEqualString(a: string, b: string): boolean {
  // Compare fixed-width digests so length alone leaks nothing: both sides
  // are hashed to 32 bytes before the constant-time compare.
  const left = crypto.createHash("sha256").update(a, "utf8").digest();
  const right = crypto.createHash("sha256").update(b, "utf8").digest();
  return crypto.timingSafeEqual(left, right);
}

function readBearerToken(request: http.IncomingMessage): string | undefined {
  const header = String(request.headers.authorization || "").trim();
  if (!header.toLowerCase().startsWith("bearer ")) return undefined;
  const token = header.slice("bearer ".length).trim();
  return token.length > 0 ? token : undefined;
}

function readBody(request: http.IncomingMessage): Promise<string> {
  return new Promise((resolve, reject) => {
    let body = "";
    request.setEncoding("utf8");
    request.on("data", (chunk: string) => {
      body += chunk;
      if (Buffer.byteLength(body, "utf8") > MAX_BODY_BYTES) {
        reject(new Error("request_body_too_large"));
        request.destroy();
      }
    });
    request.on("end", () => resolve(body));
    request.on("error", reject);
  });
}

function sendJson(response: http.ServerResponse, statusCode: number, payload: Record<string, unknown>): void {
  const body = JSON.stringify(payload);
  response.writeHead(statusCode, {
    "content-type": "application/json",
    "content-length": Buffer.byteLength(body, "utf8"),
    // This endpoint is machine-to-machine on loopback; no browser should
    // ever reach it, and nothing here should be cached or framed.
    "cache-control": "no-store",
  });
  response.end(body);
}

export class OpenClawInboundListener {
  private server: http.Server | null = null;

  constructor(private readonly options: OpenClawInboundListenerOptions) {
    if (!String(options.token || "").trim()) {
      // Fail closed at construction rather than starting an unauthenticated
      // listener. index.ts only constructs this when a token is present;
      // this is the second, unconditional guard.
      throw new Error(
        "OpenClaw inbound listener refuses to start without EMPYRALIS_BRIDGE_TOKEN — an unauthenticated local intake is never acceptable.",
      );
    }
    if (!Number.isInteger(options.port) || options.port <= 0 || options.port > 65535) {
      throw new Error(`OpenClaw inbound listener port is invalid: ${options.port}`);
    }
  }

  private async record(messageType: string, payload: Record<string, unknown>): Promise<void> {
    try {
      await this.options.record?.(messageType, payload);
    } catch {
      // Auditing must never take the intake down.
    }
  }

  private async handle(request: http.IncomingMessage, response: http.ServerResponse): Promise<void> {
    const url = new URL(String(request.url || "/"), `http://${LOOPBACK_HOST}`);

    if (request.method === "GET" && url.pathname === OPENCLAW_HEALTH_PATH) {
      // Deliberately unauthenticated and deliberately empty of any state:
      // it exists so provisioning can prove the port is ours, and it says
      // nothing an unauthenticated caller should not already know.
      sendJson(response, 200, { ok: true, service: "empyralis-openclaw-inbound" });
      return;
    }

    if (url.pathname !== OPENCLAW_INBOUND_PATH) {
      sendJson(response, 404, { ok: false, error: "not_found" });
      return;
    }
    if (request.method !== "POST") {
      sendJson(response, 405, { ok: false, error: "method_not_allowed" });
      return;
    }

    const presented = readBearerToken(request);
    if (!presented || !timingSafeEqualString(presented, this.options.token)) {
      // No detail about why. Never echo or log the presented value.
      await this.record("openclaw.inbound.unauthorized", {
        reason: presented ? "token_mismatch" : "token_absent",
        remote: request.socket.remoteAddress ?? null,
      });
      sendJson(response, 401, { ok: false, error: "unauthorized" });
      return;
    }

    let raw: string;
    try {
      raw = await readBody(request);
    } catch (error) {
      sendJson(response, 413, {
        ok: false,
        error: error instanceof Error && error.message === "request_body_too_large" ? "body_too_large" : "body_read_failed",
      });
      return;
    }

    let parsed: unknown;
    try {
      parsed = JSON.parse(raw || "null");
    } catch {
      sendJson(response, 400, { ok: false, error: "invalid_json" });
      return;
    }

    const mapped = mapOpenClawInboundBody(parsed);
    if (!mapped.ok) {
      // 400 and NOT retryable-looking: the bridge plugin's BoundedRetryQueue
      // retries any non-2xx, so a permanently-malformed body would loop
      // until it hits maxAttempts. That is bounded and visible, which is
      // the tradeoff we want over silently swallowing a message we could
      // not understand.
      await this.record("openclaw.inbound.rejected", { reason: mapped.error });
      sendJson(response, 400, { ok: false, error: mapped.error });
      return;
    }

    if (mapped.droppedMedia) {
      await this.record("openclaw.inbound.media_dropped", {
        channel_key: mapped.payload.channel_key,
        external_message_id: mapped.payload.message.external_message_id,
      });
    }

    try {
      await this.options.publisher.publishEvent("channel.inbound", mapped.payload);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      // publishEvent only throws when the gateway has no active cloud scope
      // (not yet paired/connected) or the socket send itself failed. Both
      // are genuinely transient, so answer 503 and let the plugin's own
      // durable queue hold the event — that queue is the retry mechanism,
      // and duplicating it here would be a second one.
      await this.record("openclaw.inbound.publish_failed", {
        channel_key: mapped.payload.channel_key,
        error: message,
      });
      sendJson(response, 503, { ok: false, error: "publish_unavailable" });
      return;
    }

    await this.record("openclaw.inbound.accepted", {
      channel_key: mapped.payload.channel_key,
      external_message_id: mapped.payload.message.external_message_id,
      // The gate facts as forwarded, so an "why did my agent stay silent"
      // question is answerable from the gateway journal alone.
      is_group: mapped.payload.message.is_group ?? null,
      is_mentioned: mapped.payload.message.is_mentioned ?? null,
    });
    sendJson(response, 202, { ok: true });
  }

  async start(): Promise<void> {
    if (this.server) return;
    const server = http.createServer((request, response) => {
      void this.handle(request, response).catch((error) => {
        const message = error instanceof Error ? error.message : String(error);
        this.options.logger?.error?.(`openclaw inbound listener handler failed: ${message}`);
        try {
          sendJson(response, 500, { ok: false, error: "internal_error" });
        } catch {
          // response already sent/destroyed
        }
      });
    });
    await new Promise<void>((resolve, reject) => {
      server.once("error", reject);
      server.listen(this.options.port, LOOPBACK_HOST, () => {
        server.removeListener("error", reject);
        resolve();
      });
    });
    this.server = server;
    this.options.logger?.info?.(
      `openclaw inbound listener on http://${LOOPBACK_HOST}:${this.options.port}${OPENCLAW_INBOUND_PATH}`,
    );
    await this.record("openclaw.inbound.listener_started", {
      host: LOOPBACK_HOST,
      port: this.options.port,
      path: OPENCLAW_INBOUND_PATH,
    });
  }

  async stop(): Promise<void> {
    const server = this.server;
    if (!server) return;
    this.server = null;
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }

  /** The bound port — useful when a test listens on an ephemeral port. */
  address(): number | null {
    const addr = this.server?.address();
    return addr && typeof addr === "object" ? addr.port : null;
  }

  /** The interface actually bound. Must always be loopback; exposed so a
   *  test can assert that rather than infer it from a connection attempt
   *  (0.0.0.0 is routed to loopback on macOS, so a connect-based check
   *  proves nothing). */
  boundHost(): string | null {
    const addr = this.server?.address();
    return addr && typeof addr === "object" ? addr.address : null;
  }
}

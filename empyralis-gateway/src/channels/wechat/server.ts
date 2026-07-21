/** HTTP server implementing WeChat's/WeCom's callback contract: Tencent
 *  performs a one-time GET to verify you control the URL, then POSTs each
 *  inbound message to the same URL going forward.
 *
 *  Structurally this mirrors bridges/bluebubbles-bridge.ts's
 *  startBlueBubblesBridge (an http.createServer wrapped in a small
 *  start/close handle) more than it mirrors any "personal channel"
 *  runtime — see this directory's types.ts module doc for why this isn't
 *  registered through channels/personal-runtime.ts's
 *  PersonalChannelRuntimeRegistry: this isn't a per-box personal-account
 *  bridge, it's a shared business-credential bot channel, same category
 *  as the (currently unimplemented anywhere in this repo) telegram_bot /
 *  sage_telegram_hosted catalog entries.
 *
 *  CRITICAL DIFFERENCE from bluebubbles-bridge.ts / signal-cli-bridge.ts /
 *  local-bridge-runtime.ts: those three deliberately refuse to talk to
 *  anything outside localhost/private-network (see
 *  local-bridge-runtime.ts's isPrivateBridgeHost) because their callers
 *  are trusted local companion processes. This server's caller is
 *  Tencent's public infrastructure, so it CANNOT be restricted that way —
 *  its only authentication is the query-string signature check
 *  (signature.ts), exactly as Tencent's spec provides. Whoever starts
 *  this server is responsible for making sure something (this box's
 *  firewall, a reverse proxy, a load balancer) actually lets Tencent's
 *  servers reach whatever port this binds — that is a deployment/infra
 *  decision this module does not and cannot make for you. See this
 *  directory's PR description for the specific tension with
 *  docs/PLATFORM-MAP.md's "WSS reverse tunnel ... no inbound holes"
 *  Gateway network posture.
 */
import http from "node:http";

import type { GatewayChannelInboundPayload } from "../../protocol/types";
import { mapWeChatInboundMessage } from "./message-mapper";
import { verifyWeChatServerSignature } from "./signature";
import type { WeChatAccountKind, WeChatOfficialConfig } from "./types";
import { parseWeChatXml } from "./xml";

const MAX_BODY_BYTES = 512 * 1024;

export interface WeChatBridgeLogger {
  info: (msg: string, meta?: Record<string, unknown>) => void;
  warn: (msg: string, meta?: Record<string, unknown>) => void;
  error: (msg: string, meta?: Record<string, unknown>) => void;
}

const consoleLogger: WeChatBridgeLogger = {
  info: (msg, meta) => console.log(`[wechat-bridge] ${msg}`, meta ?? ""),
  warn: (msg, meta) => console.warn(`[wechat-bridge] ${msg}`, meta ?? ""),
  error: (msg, meta) => console.error(`[wechat-bridge] ${msg}`, meta ?? ""),
};

export interface WeChatOfficialBridgeOptions {
  config: WeChatOfficialConfig;
  /** Called once per inbound message the mapper successfully translated.
   *  Mirrors PersonalChannelGatewayPublisher.publishEvent's shape used by
   *  every other channel runtime (personal-runtime.ts). A rejection here
   *  is logged but never surfaces to Tencent as a callback failure — see
   *  module doc: acking "success" regardless is deliberate so a transient
   *  downstream error doesn't turn into a Tencent retry storm. */
  onInboundEvent: (payload: GatewayChannelInboundPayload) => void | Promise<void>;
  host?: string;
  port?: number;
  /** URL path Tencent's console is configured to call. Defaults to
   *  "/wechat/callback"; must match whatever's registered in the
   *  Official Account / WeCom admin console exactly (path only — query
   *  string is Tencent's, not configurable). */
  path?: string;
  logger?: WeChatBridgeLogger;
}

export interface WeChatOfficialBridge {
  url: string;
  accountKind: WeChatAccountKind;
  close: () => Promise<void>;
}

function readBody(request: http.IncomingMessage): Promise<string> {
  return new Promise((resolve, reject) => {
    let body = "";
    request.setEncoding("utf8");
    request.on("data", (chunk) => {
      body += chunk;
      if (Buffer.byteLength(body) > MAX_BODY_BYTES) {
        reject(new Error("request_body_too_large"));
        request.destroy();
      }
    });
    request.on("end", () => resolve(body));
    request.on("error", reject);
  });
}

function sendText(response: http.ServerResponse, statusCode: number, text: string): void {
  response.writeHead(statusCode, { "content-type": "text/plain; charset=utf-8" });
  response.end(text);
}

export async function startWeChatOfficialBridge(
  options: WeChatOfficialBridgeOptions,
): Promise<WeChatOfficialBridge> {
  const path = options.path || "/wechat/callback";
  const host = options.host || "0.0.0.0";
  const logger = options.logger || consoleLogger;
  const { config, onInboundEvent } = options;

  const server = http.createServer(async (request, response) => {
    try {
      const url = new URL(request.url || "/", `http://${request.headers.host || "localhost"}`);
      if (url.pathname !== path) {
        sendText(response, 404, "not_found");
        return;
      }

      const signature = url.searchParams.get("signature") || url.searchParams.get("msg_signature") || "";
      const timestamp = url.searchParams.get("timestamp") || "";
      const nonce = url.searchParams.get("nonce") || "";
      const verified = verifyWeChatServerSignature({ token: config.token, timestamp, nonce, signature });

      if (request.method === "GET") {
        // One-time server-verification handshake: echo echostr back
        // verbatim iff the signature checks out, else refuse.
        const echostr = url.searchParams.get("echostr") || "";
        if (!verified) {
          logger.warn("server verification failed signature check");
          sendText(response, 403, "invalid_signature");
          return;
        }
        sendText(response, 200, echostr);
        return;
      }

      if (request.method === "POST") {
        if (!verified) {
          logger.warn("inbound callback failed signature check");
          sendText(response, 403, "invalid_signature");
          return;
        }
        const rawBody = await readBody(request);
        const fields = parseWeChatXml(rawBody);
        const payload = mapWeChatInboundMessage(fields, config.accountKind);
        if (payload) {
          try {
            await onInboundEvent(payload);
          } catch (error) {
            // Deliberately still ACK "success" below — see module doc.
            logger.error("onInboundEvent handler threw", { error: String(error) });
          }
        }
        // Tencent requires a 200 with body "success" (or a valid synchronous
        // reply XML, not used here) within ~5s or it will retry the same
        // message. ACKing unconditionally past signature verification
        // avoids a retry storm on messages this pass doesn't route
        // (non-text MsgType, or a downstream publish failure above).
        sendText(response, 200, "success");
        return;
      }

      sendText(response, 405, "method_not_allowed");
    } catch (error) {
      logger.error("request handling failed", { error: String(error) });
      sendText(response, 500, "internal_error");
    }
  });

  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(options.port || 0, host, resolve);
  });

  const address = server.address();
  const port = typeof address === "object" && address ? address.port : options.port || 0;

  return {
    url: `http://${host}:${port}${path}`,
    accountKind: config.accountKind,
    close: () =>
      new Promise<void>((resolve, reject) => {
        server.close((error) => (error ? reject(error) : resolve()));
      }),
  };
}

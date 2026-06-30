import http from "node:http";
import { randomUUID } from "node:crypto";

const SIGNAL_CHANNEL_KEY = "signal_personal";
const SIGNAL_PROVIDER = "signal_local_bridge";

type BridgeEvent = {
  external_message_id: string;
  remote_jid: string;
  sender_jid?: string;
  push_name?: string;
  text: string;
  received_at: string;
  from_me?: boolean;
};

type JsonObject = Record<string, unknown>;

export interface SignalCliBridgeOptions {
  host?: string;
  port?: number;
  signalCliRpcUrl: string;
  account?: string;
  token?: string;
  connectEvents?: boolean;
}

export interface SignalCliBridge {
  url: string;
  close: () => Promise<void>;
}

function trimTrailingSlash(value: string): string {
  return value.replace(/\/+$/, "");
}

function normalizeSignalCliBaseUrl(value: string): string {
  const token = trimTrailingSlash(String(value || "").trim());
  if (!token) {
    throw new Error("EMPYRALIS_SIGNAL_CLI_RPC_URL is required for the Signal bridge.");
  }
  const parsed = new URL(token);
  if (!["http:", "https:"].includes(parsed.protocol)) {
    throw new Error("EMPYRALIS_SIGNAL_CLI_RPC_URL must be an HTTP(S) URL.");
  }
  return token;
}

function normalizeChannelKey(value: unknown): string {
  return String(value || "").trim() || SIGNAL_CHANNEL_KEY;
}

function parseJsonBody(request: http.IncomingMessage): Promise<JsonObject> {
  return new Promise((resolve, reject) => {
    let body = "";
    request.setEncoding("utf8");
    request.on("data", (chunk) => {
      body += chunk;
      if (body.length > 256 * 1024) {
        reject(new Error("request_body_too_large"));
        request.destroy();
      }
    });
    request.on("end", () => {
      if (!body.trim()) {
        resolve({});
        return;
      }
      try {
        const parsed = JSON.parse(body);
        resolve(parsed && typeof parsed === "object" ? parsed as JsonObject : {});
      } catch (error) {
        reject(error);
      }
    });
    request.on("error", reject);
  });
}

function sendJson(response: http.ServerResponse, statusCode: number, payload: JsonObject): void {
  response.writeHead(statusCode, { "content-type": "application/json" });
  response.end(JSON.stringify(payload));
}

function authorizeRequest(request: http.IncomingMessage, token?: string): boolean {
  if (!token) {
    return true;
  }
  const header = String(request.headers.authorization || "").trim();
  return header === `Bearer ${token}`;
}

function buildSignalCliSendParams(account: string | undefined, remoteJid: string, text: string): JsonObject {
  const params: JsonObject = {
    message: text,
  };
  if (account) {
    params.account = account;
  }
  if (remoteJid.startsWith("group:")) {
    params.groupId = remoteJid.slice("group:".length);
  } else {
    params.recipient = [remoteJid];
  }
  return params;
}

async function callSignalCliRpc(
  signalCliBaseUrl: string,
  method: string,
  params: JsonObject,
): Promise<JsonObject> {
  const response = await fetch(`${signalCliBaseUrl}/api/v1/rpc`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      jsonrpc: "2.0",
      method,
      id: randomUUID(),
      params,
    }),
  });
  if (!response.ok) {
    throw new Error(`signal-cli JSON-RPC returned HTTP ${response.status}`);
  }
  const payload = await response.json() as JsonObject;
  if (payload.error && typeof payload.error === "object") {
    const error = payload.error as JsonObject;
    throw new Error(String(error.message || "signal-cli JSON-RPC error"));
  }
  return payload.result && typeof payload.result === "object" ? payload.result as JsonObject : {};
}

function eventText(value: unknown): string {
  return String(value || "").trim();
}

function eventTimestamp(value: unknown): string {
  const numeric = typeof value === "number" ? value : Number(value);
  if (Number.isFinite(numeric) && numeric > 0) {
    return new Date(numeric).toISOString();
  }
  const token = eventText(value);
  return token || new Date().toISOString();
}

function asObject(value: unknown): JsonObject {
  return value && typeof value === "object" ? value as JsonObject : {};
}

function signalEnvelope(notification: JsonObject): JsonObject {
  const params = asObject(notification.params);
  const wrappedResult = asObject(params.result);
  return asObject(wrappedResult.envelope || params.envelope);
}

export function mapSignalCliReceiveNotification(notification: JsonObject): BridgeEvent | null {
  if (eventText(notification.method) !== "receive") {
    return null;
  }
  const envelope = signalEnvelope(notification);
  const dataMessage = asObject(envelope.dataMessage);
  const syncMessage = asObject(envelope.syncMessage);
  const sentMessage = asObject(syncMessage.sentMessage);
  const incomingText = eventText(dataMessage.message);
  const syncText = eventText(sentMessage.message);
  const text = incomingText || syncText;
  if (!text) {
    return null;
  }
  const fromMe = !incomingText && Boolean(syncText);
  const source = eventText(envelope.sourceNumber || envelope.source || envelope.sourceUuid);
  const destination = eventText(sentMessage.destinationNumber || sentMessage.destination || sentMessage.destinationUuid);
  const remoteJid = fromMe ? destination : source;
  if (!remoteJid) {
    return null;
  }
  const timestamp = envelope.timestamp || dataMessage.timestamp || sentMessage.timestamp;
  return {
    external_message_id: eventText(timestamp) || randomUUID(),
    remote_jid: remoteJid,
    sender_jid: source || undefined,
    push_name: eventText(envelope.sourceName) || undefined,
    text,
    received_at: eventTimestamp(timestamp),
    from_me: fromMe,
  };
}

function parseSseChunk(chunk: string): JsonObject[] {
  const payloads: JsonObject[] = [];
  for (const block of chunk.split(/\n\n+/)) {
    const dataLines = block
      .split(/\r?\n/)
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice("data:".length).trim())
      .filter(Boolean);
    if (!dataLines.length) {
      continue;
    }
    try {
      const parsed = JSON.parse(dataLines.join("\n"));
      if (parsed && typeof parsed === "object") {
        payloads.push(parsed as JsonObject);
      }
    } catch {
      // Ignore malformed third-party SSE records; health will surface reconnect state.
    }
  }
  return payloads;
}

async function connectSignalCliEvents(
  signalCliBaseUrl: string,
  enqueue: (event: BridgeEvent) => void,
  controller: AbortController,
): Promise<void> {
  const response = await fetch(`${signalCliBaseUrl}/api/v1/events`, {
    method: "GET",
    headers: { accept: "text/event-stream" },
    signal: controller.signal,
  });
  if (!response.ok || !response.body) {
    throw new Error(`signal-cli events returned HTTP ${response.status}`);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (!controller.signal.aborted) {
    const { value, done } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    const lastBoundary = buffer.lastIndexOf("\n\n");
    if (lastBoundary < 0) {
      continue;
    }
    const complete = buffer.slice(0, lastBoundary + 2);
    buffer = buffer.slice(lastBoundary + 2);
    for (const notification of parseSseChunk(complete)) {
      const event = mapSignalCliReceiveNotification(notification);
      if (event) {
        enqueue(event);
      }
    }
  }
}

export async function startSignalCliBridge(options: SignalCliBridgeOptions): Promise<SignalCliBridge> {
  const host = options.host || "127.0.0.1";
  const signalCliBaseUrl = normalizeSignalCliBaseUrl(options.signalCliRpcUrl);
  const account = String(options.account || "").trim() || undefined;
  const token = String(options.token || "").trim() || undefined;
  const eventsByChannel = new Map<string, BridgeEvent[]>();
  const eventController = new AbortController();

  const enqueue = (event: BridgeEvent): void => {
    const items = eventsByChannel.get(SIGNAL_CHANNEL_KEY) || [];
    items.push(event);
    eventsByChannel.set(SIGNAL_CHANNEL_KEY, items);
  };

  if (options.connectEvents !== false) {
    void connectSignalCliEvents(signalCliBaseUrl, enqueue, eventController).catch(() => undefined);
  }

  const server = http.createServer(async (request, response) => {
    try {
      if (!authorizeRequest(request, token)) {
        sendJson(response, 401, { error: "unauthorized" });
        return;
      }
      const url = new URL(request.url || "/", `http://${host}`);
      if (request.method === "GET" && url.pathname === "/health") {
        try {
          const check = await fetch(`${signalCliBaseUrl}/api/v1/check`);
          sendJson(response, check.ok ? 200 : 503, {
            status: check.ok ? "connected" : "unavailable",
            connected: check.ok,
            provider: SIGNAL_PROVIDER,
            channel_keys: [SIGNAL_CHANNEL_KEY],
            account_configured: Boolean(account),
            issues: check.ok ? [] : ["signal_cli_check_failed"],
          });
        } catch (error) {
          sendJson(response, 503, {
            status: "unavailable",
            connected: false,
            provider: SIGNAL_PROVIDER,
            channel_keys: [SIGNAL_CHANNEL_KEY],
            account_configured: Boolean(account),
            last_error: error instanceof Error ? error.message : String(error),
            issues: ["signal_cli_unavailable"],
          });
        }
        return;
      }
      if (request.method === "POST" && url.pathname === "/messages") {
        const body = await parseJsonBody(request);
        const channelKey = normalizeChannelKey(body.channel_key);
        const remoteJid = eventText(body.remote_jid);
        const text = eventText(body.text);
        if (channelKey !== SIGNAL_CHANNEL_KEY) {
          sendJson(response, 400, { error: "unsupported_channel" });
          return;
        }
        if (!remoteJid || !text) {
          sendJson(response, 400, { error: "remote_jid_and_text_required" });
          return;
        }
        const result = await callSignalCliRpc(
          signalCliBaseUrl,
          "send",
          buildSignalCliSendParams(account, remoteJid, text),
        );
        const timestamp = result.timestamp || result.timestamps;
        sendJson(response, 200, {
          delivered: true,
          status: "sent",
          channel_key: SIGNAL_CHANNEL_KEY,
          provider: SIGNAL_PROVIDER,
          external_message_id: eventText(timestamp) || `signal-${Date.now()}`,
        });
        return;
      }
      if (request.method === "GET" && url.pathname === "/events") {
        const channelKey = normalizeChannelKey(url.searchParams.get("channel_key"));
        if (channelKey !== SIGNAL_CHANNEL_KEY) {
          sendJson(response, 200, { items: [] });
          return;
        }
        const items = eventsByChannel.get(SIGNAL_CHANNEL_KEY) || [];
        eventsByChannel.set(SIGNAL_CHANNEL_KEY, []);
        sendJson(response, 200, { items });
        return;
      }
      sendJson(response, 404, { error: "not_found" });
    } catch (error) {
      sendJson(response, 500, { error: error instanceof Error ? error.message : String(error) });
    }
  });

  await new Promise<void>((resolve) => {
    server.listen(options.port || 0, host, resolve);
  });
  const address = server.address();
  const port = typeof address === "object" && address ? address.port : options.port || 0;
  return {
    url: `http://${host}:${port}`,
    close: async () => {
      eventController.abort();
      await new Promise<void>((resolve, reject) => {
        server.close((error) => (error ? reject(error) : resolve()));
      });
    },
  };
}

async function main(): Promise<void> {
  const port = Number(process.env.EMPYRALIS_SIGNAL_BRIDGE_PORT || "8901");
  const bridge = await startSignalCliBridge({
    port,
    signalCliRpcUrl: String(process.env.EMPYRALIS_SIGNAL_CLI_RPC_URL || "").trim(),
    account: String(process.env.EMPYRALIS_SIGNAL_CLI_ACCOUNT || "").trim() || undefined,
    token: String(process.env.EMPYRALIS_SIGNAL_BRIDGE_TOKEN || "").trim() || undefined,
  });
  console.log(`Signal Agent Computer bridge listening on ${bridge.url}`);
  console.log(`export EMPYRALIS_SIGNAL_BRIDGE_URL=${bridge.url}`);
  if (process.env.EMPYRALIS_SIGNAL_BRIDGE_TOKEN) {
    console.log("EMPYRALIS_SIGNAL_BRIDGE_TOKEN is configured; set the same value in the gateway environment.");
  }
}

if (require.main === module) {
  void main().catch((error) => {
    console.error(error);
    process.exit(1);
  });
}

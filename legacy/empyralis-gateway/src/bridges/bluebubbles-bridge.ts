import http from "node:http";
import { randomUUID } from "node:crypto";

const IMESSAGE_CHANNEL_KEY = "imessage_personal";
const IMESSAGE_PROVIDER = "bluebubbles_local_bridge";

type JsonObject = Record<string, unknown>;

type BridgeEvent = {
  external_message_id: string;
  remote_jid: string;
  sender_jid?: string;
  push_name?: string;
  text: string;
  received_at: string;
  from_me?: boolean;
};

export interface BlueBubblesBridgeOptions {
  host?: string;
  port?: number;
  serverUrl: string;
  password: string;
  token?: string;
  timeoutMs?: number;
}

export interface BlueBubblesBridge {
  url: string;
  close: () => Promise<void>;
}

function trimTrailingSlash(value: string): string {
  return value.replace(/\/+$/, "");
}

function normalizeBlueBubblesUrl(value: string): string {
  const token = String(value || "").trim();
  if (!token) {
    throw new Error("EMPYRALIS_BLUEBUBBLES_SERVER_URL is required for the iMessage bridge.");
  }
  const withScheme = /^https?:\/\//i.test(token) ? token : `http://${token}`;
  return trimTrailingSlash(withScheme);
}

function text(value: unknown): string {
  return String(value || "").trim();
}

function asObject(value: unknown): JsonObject {
  return value && typeof value === "object" && !Array.isArray(value) ? value as JsonObject : {};
}

function objectOrNull(value: unknown): JsonObject | null {
  return value && typeof value === "object" && !Array.isArray(value) ? value as JsonObject : null;
}

function parseJsonBody(request: http.IncomingMessage): Promise<JsonObject> {
  return new Promise((resolve, reject) => {
    let body = "";
    request.setEncoding("utf8");
    request.on("data", (chunk) => {
      body += chunk;
      if (body.length > 512 * 1024) {
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
        resolve(asObject(parsed));
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
  return text(request.headers.authorization) === `Bearer ${token}`;
}

function blueBubblesUrl(baseUrl: string, path: string, password: string): string {
  const url = new URL(path, `${baseUrl}/`);
  url.searchParams.set("password", password);
  return url.toString();
}

async function fetchBlueBubbles(
  url: string,
  init: RequestInit,
  timeoutMs: number,
): Promise<Response> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...init, signal: controller.signal });
  } finally {
    clearTimeout(timeout);
  }
}

function extractMessageId(payload: unknown): string {
  const record = asObject(payload);
  const data = asObject(record.data);
  return (
    text(record.guid)
    || text(record.messageGuid)
    || text(record.messageId)
    || text(record.id)
    || text(data.guid)
    || text(data.messageGuid)
    || text(data.messageId)
    || text(data.id)
    || "ok"
  );
}

function normalizeHandle(value: string): string {
  return value.replace(/[^\d+@a-zA-Z._-]/g, "").trim();
}

function chatGuidFromRecord(record: JsonObject): string {
  return text(record.chatGuid) || text(record.guid) || text(record.chat_guid) || text(record.identifier);
}

function participantAddresses(record: JsonObject): string[] {
  const raw = Array.isArray(record.participants)
    ? record.participants
    : Array.isArray(record.handles)
      ? record.handles
      : [];
  const out: string[] = [];
  for (const entry of raw) {
    if (typeof entry === "string") {
      out.push(entry);
      continue;
    }
    const item = asObject(entry);
    const candidate = text(item.address) || text(item.handle) || text(item.id) || text(item.identifier);
    if (candidate) {
      out.push(candidate);
    }
  }
  return out;
}

async function queryExistingChatGuid(
  baseUrl: string,
  password: string,
  remoteJid: string,
  timeoutMs: number,
): Promise<string> {
  if (remoteJid.includes(";")) {
    return remoteJid;
  }
  const normalized = normalizeHandle(remoteJid);
  if (!normalized) {
    return "";
  }
  const response = await fetchBlueBubbles(
    blueBubblesUrl(baseUrl, "/api/v1/chat/query", password),
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ limit: 500, offset: 0, with: ["participants"] }),
    },
    timeoutMs,
  );
  if (!response.ok) {
    return "";
  }
  const payload = await response.json().catch(() => null);
  const rows = Array.isArray(asObject(payload).data) ? asObject(payload).data as unknown[] : [];
  for (const row of rows) {
    const record = asObject(row);
    const guid = chatGuidFromRecord(record);
    if (!guid) {
      continue;
    }
    if (guid.includes(normalized)) {
      return guid;
    }
    const participants = participantAddresses(record).map(normalizeHandle);
    if (participants.includes(normalized)) {
      return guid;
    }
  }
  return "";
}

async function sendBlueBubblesMessage(
  baseUrl: string,
  password: string,
  remoteJid: string,
  message: string,
  timeoutMs: number,
): Promise<string> {
  const chatGuid = await queryExistingChatGuid(baseUrl, password, remoteJid, timeoutMs);
  if (!chatGuid) {
    const response = await fetchBlueBubbles(
      blueBubblesUrl(baseUrl, "/api/v1/chat/new", password),
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          addresses: [remoteJid],
          message,
          tempGuid: `empyralis-${randomUUID()}`,
        }),
      },
      timeoutMs,
    );
    if (!response.ok) {
      throw new Error(`BlueBubbles create chat returned HTTP ${response.status}`);
    }
    return extractMessageId(await response.json().catch(() => null));
  }

  const response = await fetchBlueBubbles(
    blueBubblesUrl(baseUrl, "/api/v1/message/text", password),
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        chatGuid,
        tempGuid: `empyralis-${randomUUID()}`,
        message,
      }),
    },
    timeoutMs,
  );
  if (!response.ok) {
    throw new Error(`BlueBubbles message send returned HTTP ${response.status}`);
  }
  return extractMessageId(await response.json().catch(() => null));
}

function firstChatGuid(message: JsonObject): string {
  const chats = Array.isArray(message.chats) ? message.chats : [];
  for (const entry of chats) {
    const guid = chatGuidFromRecord(asObject(entry));
    if (guid) {
      return guid;
    }
  }
  return "";
}

function handleRecord(message: JsonObject): JsonObject {
  return objectOrNull(message.handle) ?? objectOrNull(message.sender) ?? {};
}

export function mapBlueBubblesWebhookPayload(payload: JsonObject): BridgeEvent | null {
  const event = objectOrNull(payload.data) ?? payload;
  const message = objectOrNull(event.message) ?? event;
  const body = text(message.text) || text(message.body) || text(message.message);
  const attachments = Array.isArray(message.attachments) ? message.attachments : [];
  const fallbackText = attachments.length ? `<media:attachment> (${attachments.length})` : "";
  const messageText = body || fallbackText;
  if (!messageText) {
    return null;
  }
  const handle = handleRecord(message);
  const sender = text(handle.address) || text(handle.handle) || text(handle.id) || text(message.senderId) || text(message.sender);
  const chatGuid = text(message.chatGuid) || text(message.chat_guid) || firstChatGuid(message);
  const remoteJid = chatGuid || sender;
  if (!remoteJid) {
    return null;
  }
  return {
    external_message_id: text(message.guid) || text(message.id) || text(payload.guid) || randomUUID(),
    remote_jid: remoteJid,
    sender_jid: sender || undefined,
    push_name: text(handle.displayName) || text(handle.name) || text(message.senderName) || undefined,
    text: messageText,
    received_at: text(message.dateCreated) || text(message.date) || new Date().toISOString(),
    from_me: message.isFromMe === true || message.fromMe === true,
  };
}

export async function startBlueBubblesBridge(options: BlueBubblesBridgeOptions): Promise<BlueBubblesBridge> {
  const host = options.host || "127.0.0.1";
  const baseUrl = normalizeBlueBubblesUrl(options.serverUrl);
  const password = text(options.password);
  if (!password) {
    throw new Error("EMPYRALIS_BLUEBUBBLES_PASSWORD is required for the iMessage bridge.");
  }
  const token = text(options.token) || undefined;
  const timeoutMs = Number.isFinite(options.timeoutMs) && Number(options.timeoutMs) > 0
    ? Math.round(Number(options.timeoutMs))
    : 10_000;
  const eventsByChannel = new Map<string, BridgeEvent[]>();

  const enqueue = (event: BridgeEvent): void => {
    const items = eventsByChannel.get(IMESSAGE_CHANNEL_KEY) || [];
    items.push(event);
    eventsByChannel.set(IMESSAGE_CHANNEL_KEY, items);
  };

  const server = http.createServer(async (request, response) => {
    try {
      if (!authorizeRequest(request, token)) {
        sendJson(response, 401, { error: "unauthorized" });
        return;
      }
      const url = new URL(request.url || "/", `http://${host}`);
      if (request.method === "GET" && url.pathname === "/health") {
        const ping = await fetchBlueBubbles(
          blueBubblesUrl(baseUrl, "/api/v1/ping", password),
          { method: "GET" },
          timeoutMs,
        );
        sendJson(response, ping.ok ? 200 : 503, {
          status: ping.ok ? "connected" : "unavailable",
          connected: ping.ok,
          provider: IMESSAGE_PROVIDER,
          channel_keys: [IMESSAGE_CHANNEL_KEY],
          issues: ping.ok ? [] : ["bluebubbles_ping_failed"],
        });
        return;
      }
      if (request.method === "POST" && url.pathname === "/messages") {
        const body = await parseJsonBody(request);
        const channelKey = text(body.channel_key) || IMESSAGE_CHANNEL_KEY;
        const remoteJid = text(body.remote_jid);
        const message = text(body.text);
        if (channelKey !== IMESSAGE_CHANNEL_KEY) {
          sendJson(response, 400, { error: "unsupported_channel" });
          return;
        }
        if (!remoteJid || !message) {
          sendJson(response, 400, { error: "remote_jid_and_text_required" });
          return;
        }
        const messageId = await sendBlueBubblesMessage(baseUrl, password, remoteJid, message, timeoutMs);
        sendJson(response, 200, {
          delivered: true,
          status: "sent",
          channel_key: IMESSAGE_CHANNEL_KEY,
          provider: IMESSAGE_PROVIDER,
          external_message_id: messageId,
        });
        return;
      }
      if (request.method === "GET" && url.pathname === "/events") {
        const channelKey = text(url.searchParams.get("channel_key")) || IMESSAGE_CHANNEL_KEY;
        if (channelKey !== IMESSAGE_CHANNEL_KEY) {
          sendJson(response, 200, { items: [] });
          return;
        }
        const items = eventsByChannel.get(IMESSAGE_CHANNEL_KEY) || [];
        eventsByChannel.set(IMESSAGE_CHANNEL_KEY, []);
        sendJson(response, 200, { items });
        return;
      }
      if (request.method === "POST" && url.pathname === "/webhook") {
        const body = await parseJsonBody(request);
        const event = mapBlueBubblesWebhookPayload(body);
        if (event) {
          enqueue(event);
        }
        sendJson(response, 200, { ok: true, accepted: Boolean(event) });
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
      await new Promise<void>((resolve, reject) => {
        server.close((error) => (error ? reject(error) : resolve()));
      });
    },
  };
}

async function main(): Promise<void> {
  const port = Number(process.env.EMPYRALIS_IMESSAGE_BRIDGE_PORT || "8902");
  const bridge = await startBlueBubblesBridge({
    port,
    serverUrl: String(process.env.EMPYRALIS_BLUEBUBBLES_SERVER_URL || "").trim(),
    password: String(process.env.EMPYRALIS_BLUEBUBBLES_PASSWORD || "").trim(),
    token: String(process.env.EMPYRALIS_IMESSAGE_BRIDGE_TOKEN || "").trim() || undefined,
  });
  console.log(`iMessage BlueBubbles Agent Computer bridge listening on ${bridge.url}`);
  console.log(`export EMPYRALIS_IMESSAGE_BRIDGE_URL=${bridge.url}`);
  if (process.env.EMPYRALIS_IMESSAGE_BRIDGE_TOKEN) {
    console.log("EMPYRALIS_IMESSAGE_BRIDGE_TOKEN is configured; set the same value in the gateway environment.");
  }
}

if (require.main === module) {
  void main().catch((error) => {
    console.error(error);
    process.exit(1);
  });
}

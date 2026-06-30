import http from "node:http";
import { randomUUID } from "node:crypto";

type BridgeEvent = {
  external_message_id: string;
  remote_jid: string;
  sender_jid?: string;
  push_name?: string;
  text: string;
  received_at: string;
  from_me?: boolean;
};

type SentMessage = {
  channel_key: string;
  provider?: string;
  remote_jid: string;
  text: string;
  idempotency_key: string;
  reply_to_external_message_id?: string;
  metadata?: Record<string, unknown>;
};

export interface LocalBridgeHarnessOptions {
  port?: number;
  host?: string;
  channelKeys?: string[];
}

export interface LocalBridgeHarness {
  url: string;
  close: () => Promise<void>;
  sentMessages: () => SentMessage[];
  enqueueEvent: (channelKey: string, event: Partial<BridgeEvent>) => void;
}

const DEFAULT_CHANNELS = ["signal_personal", "imessage_personal", "wechat_personal"];

function parseJsonBody(request: http.IncomingMessage): Promise<Record<string, unknown>> {
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
        resolve(parsed && typeof parsed === "object" ? parsed as Record<string, unknown> : {});
      } catch (error) {
        reject(error);
      }
    });
    request.on("error", reject);
  });
}

function sendJson(response: http.ServerResponse, statusCode: number, payload: Record<string, unknown>): void {
  response.writeHead(statusCode, { "content-type": "application/json" });
  response.end(JSON.stringify(payload));
}

function normalizeChannelKey(value: unknown, fallback = "signal_personal"): string {
  const token = String(value || "").trim();
  return token || fallback;
}

export async function startLocalBridgeHarness(options: LocalBridgeHarnessOptions = {}): Promise<LocalBridgeHarness> {
  const host = options.host || "127.0.0.1";
  const channelKeys = options.channelKeys?.length ? options.channelKeys : DEFAULT_CHANNELS;
  const sent: SentMessage[] = [];
  const eventsByChannel = new Map<string, BridgeEvent[]>();

  const server = http.createServer(async (request, response) => {
    try {
      const url = new URL(request.url || "/", `http://${host}`);
      if (request.method === "GET" && url.pathname === "/health") {
        sendJson(response, 200, {
          status: "connected",
          connected: true,
          issues: [],
          channel_keys: channelKeys,
        });
        return;
      }
      if (request.method === "POST" && url.pathname === "/messages") {
        const body = await parseJsonBody(request);
        const channelKey = normalizeChannelKey(body.channel_key);
        const message: SentMessage = {
          channel_key: channelKey,
          provider: typeof body.provider === "string" ? body.provider : undefined,
          remote_jid: String(body.remote_jid || "").trim(),
          text: String(body.text || "").trim(),
          idempotency_key: String(body.idempotency_key || randomUUID()).trim(),
          reply_to_external_message_id: typeof body.reply_to_external_message_id === "string"
            ? body.reply_to_external_message_id
            : undefined,
          metadata: body.metadata && typeof body.metadata === "object"
            ? body.metadata as Record<string, unknown>
            : undefined,
        };
        if (!message.remote_jid || !message.text) {
          sendJson(response, 400, { error: "remote_jid_and_text_required" });
          return;
        }
        sent.push(message);
        sendJson(response, 200, {
          delivered: true,
          status: "sent",
          external_message_id: `harness-${channelKey}-${sent.length}`,
        });
        return;
      }
      if (request.method === "GET" && url.pathname === "/events") {
        const channelKey = normalizeChannelKey(url.searchParams.get("channel_key"));
        const items = eventsByChannel.get(channelKey) || [];
        eventsByChannel.set(channelKey, []);
        sendJson(response, 200, { items });
        return;
      }
      if (request.method === "POST" && url.pathname === "/events") {
        const body = await parseJsonBody(request);
        const channelKey = normalizeChannelKey(body.channel_key);
        const item: BridgeEvent = {
          external_message_id: String(body.external_message_id || `evt-${randomUUID()}`).trim(),
          remote_jid: String(body.remote_jid || "owner").trim(),
          sender_jid: typeof body.sender_jid === "string" ? body.sender_jid : undefined,
          push_name: typeof body.push_name === "string" ? body.push_name : undefined,
          text: String(body.text || "").trim(),
          received_at: String(body.received_at || new Date().toISOString()).trim(),
          from_me: body.from_me === true,
        };
        if (!item.text) {
          sendJson(response, 400, { error: "text_required" });
          return;
        }
        const items = eventsByChannel.get(channelKey) || [];
        items.push(item);
        eventsByChannel.set(channelKey, items);
        sendJson(response, 200, { queued: true, channel_key: channelKey, external_message_id: item.external_message_id });
        return;
      }
      if (request.method === "GET" && url.pathname === "/sent") {
        sendJson(response, 200, { items: sent });
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
    close: () => new Promise<void>((resolve, reject) => {
      server.close((error) => (error ? reject(error) : resolve()));
    }),
    sentMessages: () => [...sent],
    enqueueEvent: (channelKey: string, event: Partial<BridgeEvent>) => {
      const key = normalizeChannelKey(channelKey);
      const items = eventsByChannel.get(key) || [];
      items.push({
        external_message_id: event.external_message_id || `evt-${randomUUID()}`,
        remote_jid: event.remote_jid || "owner",
        sender_jid: event.sender_jid,
        push_name: event.push_name,
        text: event.text || "hello from harness",
        received_at: event.received_at || new Date().toISOString(),
        from_me: event.from_me,
      });
      eventsByChannel.set(key, items);
    },
  };
}

async function main(): Promise<void> {
  const port = Number(process.env.EMPYRALIS_LOCAL_BRIDGE_HARNESS_PORT || "8899");
  const harness = await startLocalBridgeHarness({ port });
  console.log(`Local bridge harness listening on ${harness.url}`);
  console.log(`export EMPYRALIS_SIGNAL_BRIDGE_URL=${harness.url}`);
  console.log(`export EMPYRALIS_IMESSAGE_BRIDGE_URL=${harness.url}`);
  console.log(`export EMPYRALIS_WECHAT_BRIDGE_URL=${harness.url}`);
}

if (require.main === module) {
  void main().catch((error) => {
    console.error(error);
    process.exit(1);
  });
}

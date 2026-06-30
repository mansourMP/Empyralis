import assert from "node:assert/strict";
import http from "node:http";
import test from "node:test";

import {
  LOCAL_BRIDGE_PERSONAL_CHANNEL_CONFIGS,
  LocalBridgePersonalChannelRuntime,
} from "../channels/local-bridge-runtime";
import { startBlueBubblesBridge } from "../bridges/bluebubbles-bridge";

type JsonObject = Record<string, unknown>;

async function eventually(assertion: () => void | Promise<void>, timeoutMs = 1_000): Promise<void> {
  const startedAt = Date.now();
  let lastError: unknown;
  while (Date.now() - startedAt < timeoutMs) {
    try {
      await assertion();
      return;
    } catch (error) {
      lastError = error;
      await new Promise((resolve) => setTimeout(resolve, 25));
    }
  }
  if (lastError instanceof Error) {
    throw lastError;
  }
  throw new Error("Timed out waiting for assertion.");
}

function parseJsonBody(request: http.IncomingMessage): Promise<JsonObject> {
  return new Promise((resolve, reject) => {
    let body = "";
    request.setEncoding("utf8");
    request.on("data", (chunk) => {
      body += chunk;
    });
    request.on("end", () => {
      resolve(body.trim() ? JSON.parse(body) as JsonObject : {});
    });
    request.on("error", reject);
  });
}

async function startFakeBlueBubblesServer(): Promise<{
  url: string;
  close: () => Promise<void>;
  sentMessages: () => JsonObject[];
}> {
  const sent: JsonObject[] = [];
  const server = http.createServer(async (request, response) => {
    const url = new URL(request.url || "/", "http://127.0.0.1");
    if (request.method === "GET" && url.pathname === "/api/v1/ping") {
      response.writeHead(200, { "content-type": "application/json" });
      response.end(JSON.stringify({ status: "ok" }));
      return;
    }
    if (request.method === "POST" && url.pathname === "/api/v1/chat/query") {
      response.writeHead(200, { "content-type": "application/json" });
      response.end(JSON.stringify({
        data: [
          {
            guid: "iMessage;-;+15557654321",
            participants: [{ address: "+15557654321" }],
          },
        ],
      }));
      return;
    }
    if (request.method === "POST" && url.pathname === "/api/v1/message/text") {
      const body = await parseJsonBody(request);
      sent.push(body);
      response.writeHead(200, { "content-type": "application/json" });
      response.end(JSON.stringify({ data: { guid: "imsg-out-1" } }));
      return;
    }
    response.writeHead(404, { "content-type": "application/json" });
    response.end(JSON.stringify({ error: "not_found" }));
  });

  await new Promise<void>((resolve) => {
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  const port = typeof address === "object" && address ? address.port : 0;
  return {
    url: `http://127.0.0.1:${port}`,
    close: () => new Promise<void>((resolve, reject) => {
      server.close((error) => (error ? reject(error) : resolve()));
    }),
    sentMessages: () => [...sent],
  };
}

test("BlueBubbles Agent Computer bridge sends and publishes inbound iMessage events", async () => {
  const imessageConfig = LOCAL_BRIDGE_PERSONAL_CHANNEL_CONFIGS.find(
    (item) => item.channelKey === "imessage_personal",
  );
  assert.ok(imessageConfig);
  const upstream = await startFakeBlueBubblesServer();
  const bridge = await startBlueBubblesBridge({
    serverUrl: upstream.url,
    password: "secret",
  });
  const previousUrl = process.env.EMPYRALIS_IMESSAGE_BRIDGE_URL;
  const previousPollMs = process.env.EMPYRALIS_IMESSAGE_BRIDGE_POLL_MS;
  process.env.EMPYRALIS_IMESSAGE_BRIDGE_URL = bridge.url;
  process.env.EMPYRALIS_IMESSAGE_BRIDGE_POLL_MS = "25";
  const runtime = new LocalBridgePersonalChannelRuntime(imessageConfig);
  const inbound: unknown[] = [];
  runtime.setPublisher({
    publishStateUpdate: async () => undefined,
    publishEvent: async (_type, payload) => {
      inbound.push(payload);
    },
  });

  try {
    const health = await runtime.getHealthSnapshot();
    assert.equal(health.connected, true);

    const result = await runtime.handleChannelOutbound({
      id: "req-imessage-1",
      kind: "request",
      protocolVersion: "v1alpha2",
      type: "channel.outbound",
      ts: new Date().toISOString(),
      scope: { tenant_id: "t1", workspace_id: "w1", user_id: "u1", device_id: "d1", gateway_id: "g1" },
      payload: {
        channel_key: "imessage_personal",
        provider: "bluebubbles_local_bridge",
        idempotency_key: "imessage-idem-1",
        remote_jid: "+15557654321",
        text: "hello imessage",
      },
    });
    assert.equal(result.delivered, true);
    assert.equal(result.external_message_id, "imsg-out-1");
    assert.equal(upstream.sentMessages()[0].chatGuid, "iMessage;-;+15557654321");
    assert.equal(upstream.sentMessages()[0].message, "hello imessage");

    await fetch(`${bridge.url}/webhook`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        type: "message",
        data: {
          message: {
            guid: "imsg-in-1",
            text: "incoming imessage",
            chats: [{ guid: "iMessage;-;+15557654321" }],
            handle: { address: "+15557654321", displayName: "Owner" },
          },
        },
      }),
    });
    await runtime.start();
    await eventually(() => {
      assert.equal(inbound.length, 1);
      const payload = inbound[0] as { channel_key?: string; message?: { remote_jid?: string; text?: string } };
      assert.equal(payload.channel_key, "imessage_personal");
      assert.equal(payload.message?.remote_jid, "iMessage;-;+15557654321");
      assert.equal(payload.message?.text, "incoming imessage");
    });
  } finally {
    await runtime.stop();
    await bridge.close();
    await upstream.close();
    if (previousUrl === undefined) {
      delete process.env.EMPYRALIS_IMESSAGE_BRIDGE_URL;
    } else {
      process.env.EMPYRALIS_IMESSAGE_BRIDGE_URL = previousUrl;
    }
    if (previousPollMs === undefined) {
      delete process.env.EMPYRALIS_IMESSAGE_BRIDGE_POLL_MS;
    } else {
      process.env.EMPYRALIS_IMESSAGE_BRIDGE_POLL_MS = previousPollMs;
    }
  }
});

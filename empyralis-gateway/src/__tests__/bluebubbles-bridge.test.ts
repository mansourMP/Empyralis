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

/** Shared setup for the group-gate tests below — same real bridge + real
 *  LocalBridgePersonalChannelRuntime wiring as the happy-path test above,
 *  so the gate is exercised through its full call path (webhook POST ->
 *  mapBlueBubblesWebhookPayload -> pollInboundEvents -> publishEvent). */
async function withBlueBubblesGroupGateHarness(
  run: (ctx: {
    runtime: LocalBridgePersonalChannelRuntime;
    bridge: Awaited<ReturnType<typeof startBlueBubblesBridge>>;
    inbound: Array<{ message?: Record<string, unknown> }>;
  }) => Promise<void>,
): Promise<void> {
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
  const runtime = new LocalBridgePersonalChannelRuntime(imessageConfig!);
  const inbound: Array<{ message?: Record<string, unknown> }> = [];
  runtime.setPublisher({
    publishStateUpdate: async () => undefined,
    publishEvent: async (_type, payload) => {
      inbound.push(payload as { message?: Record<string, unknown> });
    },
  });
  try {
    await runtime.start();
    await run({ runtime, bridge, inbound });
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
}

// UPDATED (channel-gate hardening, see CHANNEL-GATEWAY-PLAN.md "the last
// unscoped channels"): this runtime's own shouldSkip drop was REMOVED —
// personal_channels_service.py's Signal/iMessage/WeChat-personal identity
// resolution (_resolve_local_bridge_agent_id) landed, so the backend's
// _enforce_group_policy is now the ONE shared resolver for this decision,
// exactly like it already was for WhatsApp/Telegram (see
// telegram-group-gate.test.ts's identical 2026-07-23 update). This
// runtime's job is now only to compute and always forward the raw mention
// FACTS (is_group/is_mentioned/is_reply_to_sage) — never to withhold a
// message based on them.
test("iMessage group gate: an unaddressed group chat message is still published, with is_mentioned=false so the backend resolver can decide", async () => {
  await withBlueBubblesGroupGateHarness(async ({ bridge, inbound }) => {
    await fetch(`${bridge.url}/webhook`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        type: "message",
        data: {
          message: {
            guid: "imsg-group-in-1",
            text: "what time is dinner",
            // style 43 === group chat (verified against BlueBubbles'
            // Chat.ts entity — see isBlueBubblesGroupChat's comment).
            chats: [{ guid: "iMessage;+;chat-family", style: 43, participants: [{ address: "+15557654321" }, { address: "+15557654322" }] }],
            handle: { address: "+15557654321", displayName: "Family Member" },
          },
        },
      }),
    });
    await eventually(() => {
      assert.equal(inbound.length, 1, "an unaddressed group chat message must still reach publishEvent — this runtime no longer decides shouldSkip");
    });
    const message = inbound[0].message as Record<string, unknown>;
    assert.equal(message.is_group, true);
    assert.equal(message.is_mentioned, false);
    assert.equal(message.is_reply_to_sage, false);
  });
});

test("iMessage group gate: an inline reply to a message Sage sent is published", async () => {
  await withBlueBubblesGroupGateHarness(async ({ runtime, bridge, inbound }) => {
    // Send once through the real outbound path so the bridge's own
    // sentMessageIds set captures this send's guid (the fake upstream's
    // /api/v1/message/text handler always returns { data: { guid: "imsg-out-1" } }).
    await runtime.handleChannelOutbound({
      id: "req-imessage-group-1",
      kind: "request",
      protocolVersion: "v1alpha2",
      type: "channel.outbound",
      ts: new Date().toISOString(),
      scope: { tenant_id: "t1", workspace_id: "w1", user_id: "u1", device_id: "d1", gateway_id: "g1" },
      payload: {
        channel_key: "imessage_personal",
        provider: "bluebubbles_local_bridge",
        idempotency_key: "imessage-group-idem-1",
        remote_jid: "iMessage;+;chat-family",
        text: "dinner's at 7",
      },
    });

    await fetch(`${bridge.url}/webhook`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        type: "message",
        data: {
          message: {
            guid: "imsg-group-in-2",
            text: "sounds good",
            chats: [{ guid: "iMessage;+;chat-family", style: 43, participants: [{ address: "+15557654321" }, { address: "+15557654322" }] }],
            handle: { address: "+15557654321", displayName: "Family Member" },
            threadOriginatorGuid: "imsg-out-1",
          },
        },
      }),
    });
    await eventually(() => {
      assert.equal(inbound.length, 1);
      const message = inbound[0].message as Record<string, unknown>;
      assert.equal(message.is_group, true);
      assert.equal(message.is_mentioned, false);
      assert.equal(message.is_reply_to_sage, true);
    });
  });
});

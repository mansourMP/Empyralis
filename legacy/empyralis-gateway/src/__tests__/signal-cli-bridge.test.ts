import assert from "node:assert/strict";
import http from "node:http";
import test from "node:test";

import {
  LOCAL_BRIDGE_PERSONAL_CHANNEL_CONFIGS,
  LocalBridgePersonalChannelRuntime,
} from "../channels/local-bridge-runtime";
import { startSignalCliBridge } from "../bridges/signal-cli-bridge";

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

async function startFakeSignalCliDaemon(): Promise<{
  url: string;
  close: () => Promise<void>;
  rpcRequests: () => JsonObject[];
  eventClientCount: () => number;
  emitReceive: (payload: JsonObject) => void;
}> {
  const rpcRequests: JsonObject[] = [];
  const eventClients = new Set<http.ServerResponse>();
  const server = http.createServer(async (request, response) => {
    const url = new URL(request.url || "/", "http://127.0.0.1");
    if (request.method === "GET" && url.pathname === "/api/v1/check") {
      response.writeHead(200, { "content-type": "text/plain" });
      response.end("OK");
      return;
    }
    if (request.method === "GET" && url.pathname === "/api/v1/events") {
      response.writeHead(200, {
        "content-type": "text/event-stream",
        "cache-control": "no-cache",
        connection: "keep-alive",
      });
      eventClients.add(response);
      request.on("close", () => {
        eventClients.delete(response);
      });
      return;
    }
    if (request.method === "POST" && url.pathname === "/api/v1/rpc") {
      const body = await parseJsonBody(request);
      rpcRequests.push(body);
      response.writeHead(200, { "content-type": "application/json" });
      response.end(JSON.stringify({
        jsonrpc: "2.0",
        id: body.id,
        result: { timestamp: 1_714_000_000_000 },
      }));
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
      for (const client of eventClients) {
        client.end();
      }
      server.close((error) => (error ? reject(error) : resolve()));
    }),
    rpcRequests: () => [...rpcRequests],
    eventClientCount: () => eventClients.size,
    emitReceive: (payload: JsonObject) => {
      for (const client of eventClients) {
        client.write(`data: ${JSON.stringify(payload)}\n\n`);
      }
    },
  };
}

test("Signal Agent Computer bridge sends through signal-cli JSON-RPC and receives SSE messages", async () => {
  const signalConfig = LOCAL_BRIDGE_PERSONAL_CHANNEL_CONFIGS.find(
    (item) => item.channelKey === "signal_personal",
  );
  assert.ok(signalConfig);
  const signalCli = await startFakeSignalCliDaemon();
  const bridge = await startSignalCliBridge({
    signalCliRpcUrl: signalCli.url,
    account: "+15551234567",
  });
  const previousUrl = process.env.EMPYRALIS_SIGNAL_BRIDGE_URL;
  const previousPollMs = process.env.EMPYRALIS_SIGNAL_BRIDGE_POLL_MS;
  process.env.EMPYRALIS_SIGNAL_BRIDGE_URL = bridge.url;
  process.env.EMPYRALIS_SIGNAL_BRIDGE_POLL_MS = "25";
  const runtime = new LocalBridgePersonalChannelRuntime(signalConfig);
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
      id: "req-signal-1",
      kind: "request",
      protocolVersion: "v1alpha2",
      type: "channel.outbound",
      ts: new Date().toISOString(),
      scope: { tenant_id: "t1", workspace_id: "w1", user_id: "u1", device_id: "d1", gateway_id: "g1" },
      payload: {
        channel_key: "signal_personal",
        provider: "signal_local_bridge",
        idempotency_key: "signal-idem-1",
        remote_jid: "+15557654321",
        text: "hello signal",
      },
    });
    assert.equal(result.delivered, true);
    assert.equal(result.external_message_id, "1714000000000");
    assert.equal(signalCli.rpcRequests()[0].method, "send");
    assert.deepEqual(signalCli.rpcRequests()[0].params, {
      account: "+15551234567",
      message: "hello signal",
      recipient: ["+15557654321"],
    });

    await runtime.start();
    await eventually(() => {
      assert.equal(signalCli.eventClientCount(), 1);
    });
    signalCli.emitReceive({
      jsonrpc: "2.0",
      method: "receive",
      params: {
        envelope: {
          sourceNumber: "+15557654321",
          sourceName: "Owner",
          timestamp: 1_714_000_000_999,
          dataMessage: {
            timestamp: 1_714_000_000_999,
            message: "incoming signal",
          },
        },
      },
    });
    await eventually(() => {
      assert.equal(inbound.length, 1);
      const payload = inbound[0] as { channel_key?: string; message?: { remote_jid?: string; text?: string } };
      assert.equal(payload.channel_key, "signal_personal");
      assert.equal(payload.message?.remote_jid, "+15557654321");
      assert.equal(payload.message?.text, "incoming signal");
    });
  } finally {
    await runtime.stop();
    await bridge.close();
    await signalCli.close();
    if (previousUrl === undefined) {
      delete process.env.EMPYRALIS_SIGNAL_BRIDGE_URL;
    } else {
      process.env.EMPYRALIS_SIGNAL_BRIDGE_URL = previousUrl;
    }
    if (previousPollMs === undefined) {
      delete process.env.EMPYRALIS_SIGNAL_BRIDGE_POLL_MS;
    } else {
      process.env.EMPYRALIS_SIGNAL_BRIDGE_POLL_MS = previousPollMs;
    }
  }
});

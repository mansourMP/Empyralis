import assert from "node:assert/strict";
import http from "node:http";
import test from "node:test";

import {
  LOCAL_BRIDGE_PERSONAL_CHANNEL_CONFIGS,
  LocalBridgePersonalChannelRuntime,
} from "../channels/local-bridge-runtime";
import { mapSignalCliReceiveNotification, startSignalCliBridge } from "../bridges/signal-cli-bridge";

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

/** Shared setup for the group-gate tests below: real bridge + real
 *  LocalBridgePersonalChannelRuntime wired together exactly like the happy
 *  path test above, so the gate is exercised through its full, real call
 *  path (SSE receive -> mapSignalCliReceiveNotification -> pollInboundEvents
 *  -> publishEvent) rather than by calling an internal function directly. */
async function withSignalGroupGateHarness(
  run: (ctx: {
    runtime: LocalBridgePersonalChannelRuntime;
    signalCli: Awaited<ReturnType<typeof startFakeSignalCliDaemon>>;
    inbound: Array<{ message?: Record<string, unknown> }>;
  }) => Promise<void>,
): Promise<void> {
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
  const runtime = new LocalBridgePersonalChannelRuntime(signalConfig!);
  const inbound: Array<{ message?: Record<string, unknown> }> = [];
  runtime.setPublisher({
    publishStateUpdate: async () => undefined,
    publishEvent: async (_type, payload) => {
      inbound.push(payload as { message?: Record<string, unknown> });
    },
  });
  try {
    await runtime.start();
    await eventually(() => {
      assert.equal(signalCli.eventClientCount(), 1);
    });
    await run({ runtime, signalCli, inbound });
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
}

test("Signal group gate: an unaddressed group message is never published", async () => {
  await withSignalGroupGateHarness(async ({ signalCli, inbound }) => {
    signalCli.emitReceive({
      jsonrpc: "2.0",
      method: "receive",
      params: {
        envelope: {
          sourceNumber: "+15557654321",
          sourceName: "Family Member",
          timestamp: 1_714_000_100_001,
          dataMessage: {
            timestamp: 1_714_000_100_001,
            message: "what time is dinner",
            groupInfo: { groupId: "Z3JvdXAtaWQ=", type: "DELIVER" },
          },
        },
      },
    });
    // Negative assertion: give the (fast, 25ms) poll loop several cycles to
    // have picked this up if it were going to, then confirm it never did.
    await new Promise((resolve) => setTimeout(resolve, 200));
    assert.equal(inbound.length, 0, "an unaddressed group message must never reach publishEvent");
  });
});

test("Signal group gate: an explicit @mention of this account is published", async () => {
  await withSignalGroupGateHarness(async ({ signalCli, inbound }) => {
    signalCli.emitReceive({
      jsonrpc: "2.0",
      method: "receive",
      params: {
        envelope: {
          sourceNumber: "+15557654321",
          sourceName: "Family Member",
          timestamp: 1_714_000_100_002,
          dataMessage: {
            timestamp: 1_714_000_100_002,
            message: "hey ￼ are you there",
            groupInfo: { groupId: "Z3JvdXAtaWQ=", type: "DELIVER" },
            mentions: [{ number: "+15551234567", start: 4, length: 1 }],
          },
        },
      },
    });
    await eventually(() => {
      assert.equal(inbound.length, 1);
      const message = inbound[0].message as Record<string, unknown>;
      assert.equal(message.remote_jid, "group:Z3JvdXAtaWQ=");
      assert.equal(message.is_group, true);
      assert.equal(message.is_mentioned, true);
    });
  });
});

test("Signal group gate: a reply to a message Sage sent is published even without a mention", async () => {
  await withSignalGroupGateHarness(async ({ runtime, signalCli, inbound }) => {
    // Send once through the real outbound path so the bridge's own
    // sentMessageIds set captures this send's timestamp (the fake daemon's
    // /api/v1/rpc handler always returns { timestamp: 1_714_000_000_000 }).
    await runtime.handleChannelOutbound({
      id: "req-signal-group-1",
      kind: "request",
      protocolVersion: "v1alpha2",
      type: "channel.outbound",
      ts: new Date().toISOString(),
      scope: { tenant_id: "t1", workspace_id: "w1", user_id: "u1", device_id: "d1", gateway_id: "g1" },
      payload: {
        channel_key: "signal_personal",
        provider: "signal_local_bridge",
        idempotency_key: "signal-group-idem-1",
        remote_jid: "group:Z3JvdXAtaWQ=",
        text: "dinner's at 7",
      },
    });

    // A group member quotes that exact message (quote.id === the timestamp
    // signal-cli returned for our send above) without @-mentioning Sage.
    signalCli.emitReceive({
      jsonrpc: "2.0",
      method: "receive",
      params: {
        envelope: {
          sourceNumber: "+15557654321",
          sourceName: "Family Member",
          timestamp: 1_714_000_100_003,
          dataMessage: {
            timestamp: 1_714_000_100_003,
            message: "sounds good",
            groupInfo: { groupId: "Z3JvdXAtaWQ=", type: "DELIVER" },
            quote: { id: 1_714_000_000_000, authorNumber: "+15557654321" },
          },
        },
      },
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

// ---------------------------------------------------------------------------
// Self-chat ("Note to Self") — the Signal analog of WhatsApp's/Telegram's
// is_self_chat owner command channel. Unit-level: mapSignalCliReceiveNotification
// is exported and pure, so these exercise it directly rather than through the
// full bridge+runtime — precise control over the exact envelope shape.
// ---------------------------------------------------------------------------

test("self-chat reachability: a fromMe sync into the bridge's own account is marked is_self_chat", () => {
  const event = mapSignalCliReceiveNotification(
    {
      jsonrpc: "2.0",
      method: "receive",
      params: {
        envelope: {
          timestamp: 1_714_000_200_001,
          syncMessage: {
            sentMessage: {
              destinationNumber: "+15551234567",
              timestamp: 1_714_000_200_001,
              message: "remind me to call mom",
            },
          },
        },
      },
    },
    { account: "+15551234567" },
  );
  assert.ok(event);
  assert.equal(event?.from_me, true);
  assert.equal(event?.is_self_chat, true);
  assert.equal(event?.remote_jid, "+15551234567");
});

test("self-chat reachability: an ordinary outgoing sync to someone else (fromMe, NOT self-chat) stays is_self_chat=false", () => {
  const event = mapSignalCliReceiveNotification(
    {
      jsonrpc: "2.0",
      method: "receive",
      params: {
        envelope: {
          timestamp: 1_714_000_200_002,
          syncMessage: {
            sentMessage: {
              destinationNumber: "+15557654321",
              timestamp: 1_714_000_200_002,
              message: "see you at 7",
            },
          },
        },
      },
    },
    { account: "+15551234567" },
  );
  assert.ok(event);
  assert.equal(event?.from_me, true);
  assert.equal(event?.is_self_chat, false);
});

test("self-chat reachability: no account configured never claims is_self_chat (safe default)", () => {
  const event = mapSignalCliReceiveNotification(
    {
      jsonrpc: "2.0",
      method: "receive",
      params: {
        envelope: {
          timestamp: 1_714_000_200_003,
          syncMessage: {
            sentMessage: {
              destinationNumber: "+15551234567",
              timestamp: 1_714_000_200_003,
              message: "hi self",
            },
          },
        },
      },
    },
    {},
  );
  assert.ok(event);
  assert.equal(event?.is_self_chat, false);
});

// ---------------------------------------------------------------------------
// LOOP GUARD — the locked requirement mirrored from WhatsApp's/Telegram's own
// self-chat echo guards (see whatsapp-self-chat.test.ts): signal-cli syncs
// EVERY send this bridge itself makes back through this same "receive" path,
// including Sage's own reply into self-chat. Without suppressing that echo,
// it would re-admit as a fresh is_self_chat=true command and re-trigger a new
// agent turn forever.
// ---------------------------------------------------------------------------

test("LOOP GUARD: a self-chat sync echo whose timestamp is already in sentMessageIds is suppressed entirely (returns null)", () => {
  const sentMessageIds = new Set(["1714000300001"]);
  const event = mapSignalCliReceiveNotification(
    {
      jsonrpc: "2.0",
      method: "receive",
      params: {
        envelope: {
          timestamp: 1_714_000_300_001,
          syncMessage: {
            sentMessage: {
              destinationNumber: "+15551234567",
              timestamp: 1_714_000_300_001,
              message: "Reminder set for later today.",
            },
          },
        },
      },
    },
    { account: "+15551234567", sentMessageIds },
  );
  assert.equal(event, null, "an echo of our own prior send must never be surfaced as a new message at all");
});

test("LOOP GUARD: a genuinely new self-chat command (fresh timestamp, not in sentMessageIds) is still admitted normally", () => {
  const sentMessageIds = new Set(["1714000300001"]);
  const event = mapSignalCliReceiveNotification(
    {
      jsonrpc: "2.0",
      method: "receive",
      params: {
        envelope: {
          timestamp: 1_714_000_300_099,
          syncMessage: {
            sentMessage: {
              destinationNumber: "+15551234567",
              timestamp: 1_714_000_300_099,
              message: "ok, add a reminder for 5pm too",
            },
          },
        },
      },
    },
    { account: "+15551234567", sentMessageIds },
  );
  assert.ok(event, "a genuinely new self-chat command must not be over-suppressed by the loop guard");
  assert.equal(event?.is_self_chat, true);
});

test("LOOP GUARD: the same known-sent timestamp targeting someone else (not self-chat) is unaffected by the guard", () => {
  // The guard only ever applies to the isSelfChat branch — an ordinary
  // fromMe-to-someone-else sync was already dropped downstream by the plain
  // from_me gate regardless, so this proves the guard doesn't accidentally
  // widen to non-self-chat traffic.
  const sentMessageIds = new Set(["1714000300005"]);
  const event = mapSignalCliReceiveNotification(
    {
      jsonrpc: "2.0",
      method: "receive",
      params: {
        envelope: {
          timestamp: 1_714_000_300_005,
          syncMessage: {
            sentMessage: {
              destinationNumber: "+15557654321",
              timestamp: 1_714_000_300_005,
              message: "see you soon",
            },
          },
        },
      },
    },
    { account: "+15551234567", sentMessageIds },
  );
  assert.ok(event, "a non-self-chat sync must still be returned (the plain from_me gate handles it downstream)");
  assert.equal(event?.is_self_chat, false);
});

// ---------------------------------------------------------------------------
// Attachment-only messages (no caption) — without a text fallback these
// would fail local-bridge-runtime.ts's `if (!text) return null` filter and
// vanish entirely. Mirrors bluebubbles-bridge.ts's identical
// `<media:attachment> (N)` placeholder for iMessage.
// ---------------------------------------------------------------------------

test("an attachment-only message (no caption) produces a <media:attachment> placeholder instead of vanishing", () => {
  const event = mapSignalCliReceiveNotification({
    jsonrpc: "2.0",
    method: "receive",
    params: {
      envelope: {
        sourceNumber: "+15557654321",
        sourceName: "Family Member",
        timestamp: 1_714_000_400_001,
        dataMessage: {
          timestamp: 1_714_000_400_001,
          message: "",
          attachments: [{ id: "att-1", contentType: "image/jpeg" }],
        },
      },
    },
  });
  assert.ok(event);
  assert.equal(event?.text, "<media:attachment> (1)");
});

test("an attachment WITH a caption keeps the caption text (no placeholder appended)", () => {
  const event = mapSignalCliReceiveNotification({
    jsonrpc: "2.0",
    method: "receive",
    params: {
      envelope: {
        sourceNumber: "+15557654321",
        sourceName: "Family Member",
        timestamp: 1_714_000_400_002,
        dataMessage: {
          timestamp: 1_714_000_400_002,
          message: "check this out",
          attachments: [{ id: "att-2", contentType: "image/jpeg" }],
        },
      },
    },
  });
  assert.ok(event);
  assert.equal(event?.text, "check this out");
});

test("a message with neither text nor attachments still returns null (unchanged pre-existing behavior)", () => {
  const event = mapSignalCliReceiveNotification({
    jsonrpc: "2.0",
    method: "receive",
    params: {
      envelope: {
        sourceNumber: "+15557654321",
        timestamp: 1_714_000_400_003,
        dataMessage: { timestamp: 1_714_000_400_003, message: "" },
      },
    },
  });
  assert.equal(event, null);
});

// ---------------------------------------------------------------------------
// Typing indicator — full E2E through the real bridge + LocalBridgePersonalChannelRuntime,
// mirroring whatsapp-typing-bridge.test.ts's "starts on inbound receipt,
// claimed and stopped by the eventual send" shape.
// ---------------------------------------------------------------------------

test("typing starts on inbound receipt via signal-cli's sendTyping RPC, and is claimed+stopped by the eventual reply", async () => {
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
          timestamp: 1_714_000_500_001,
          dataMessage: { timestamp: 1_714_000_500_001, message: "you there?" },
        },
      },
    });
    await eventually(() => {
      assert.equal(inbound.length, 1);
    });
    // Typing must have started (a "sendTyping" RPC, no stop flag) before the
    // reply is ever sent — proves it covers the "thinking" gap, not just the
    // final send, exactly like WhatsApp's/Telegram's own typing bridge.
    await eventually(() => {
      const typingCalls = signalCli.rpcRequests().filter((r) => r.method === "sendTyping");
      assert.ok(typingCalls.length >= 1, "expected at least one sendTyping RPC call on inbound receipt");
      assert.equal((typingCalls[0].params as Record<string, unknown>).stop, undefined);
    });

    // The reply goes out — handleChannelOutbound should claim (and stop) the
    // SAME typing session via a "stop" sendTyping RPC call.
    await runtime.handleChannelOutbound({
      id: "req-signal-typing-1",
      kind: "request",
      protocolVersion: "v1alpha2",
      type: "channel.outbound",
      ts: new Date().toISOString(),
      scope: { tenant_id: "t1", workspace_id: "w1", user_id: "u1", device_id: "d1", gateway_id: "g1" },
      payload: {
        channel_key: "signal_personal",
        provider: "signal_local_bridge",
        idempotency_key: "signal-typing-idem-1",
        remote_jid: "+15557654321",
        text: "still here",
      },
    });
    await eventually(() => {
      const stopCalls = signalCli.rpcRequests().filter(
        (r) => r.method === "sendTyping" && (r.params as Record<string, unknown>).stop === true,
      );
      assert.ok(stopCalls.length >= 1, "expected a stop=true sendTyping RPC call once the reply was sent");
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

import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test from "node:test";

import { LOCAL_BRIDGE_PERSONAL_CHANNEL_CONFIGS, type LocalBridgeRuntimeConfig } from "../channels/local-bridge-runtime";
import { ImsgIMessagePersonalChannelRuntime } from "../channels/imsg-imessage-runtime";
import type { ImsgChildProcessLike, ImsgExecImpl, ImsgSpawnImpl } from "../bridges/imsg-imessage-client";
import type { GatewayChannelInboundPayload, GatewayRequestEnvelope, GatewayChannelOutboundPayload } from "../protocol/types";

async function eventually(assertion: () => void | Promise<void>, timeoutMs = 1_000): Promise<void> {
  const startedAt = Date.now();
  let lastError: unknown;
  while (Date.now() - startedAt < timeoutMs) {
    try {
      await assertion();
      return;
    } catch (error) {
      lastError = error;
      await new Promise((resolve) => setTimeout(resolve, 10));
    }
  }
  if (lastError instanceof Error) {
    throw lastError;
  }
  throw new Error("Timed out waiting for assertion.");
}

interface FakeImsgChild {
  child: ImsgChildProcessLike;
  writtenLines: string[];
  emitStdoutLine: (payload: unknown) => void;
  emitClose: (code: number | null, signal: NodeJS.Signals | null) => void;
  emitError: (err: NodeJS.ErrnoException) => void;
  requestsFor: (method: string) => { id: number; method: string; params: Record<string, unknown> }[];
}

function makeFakeImsgChild(): FakeImsgChild {
  const stdout = new EventEmitter();
  const stderr = new EventEmitter();
  const stdin = new EventEmitter();
  const proc = new EventEmitter();
  const writtenLines: string[] = [];
  const fake: FakeImsgChild = {
    child: {
      stdin: {
        write: (chunk: string, callback?: (err?: Error | null) => void) => {
          writtenLines.push(chunk);
          callback?.();
          return true;
        },
        end: () => undefined,
        on: (event: string, listener: (...args: unknown[]) => void) => stdin.on(event, listener),
      },
      stdout: {
        on: (event: string, listener: (...args: unknown[]) => void) => stdout.on(event, listener),
      },
      stderr: {
        on: (event: string, listener: (...args: unknown[]) => void) => stderr.on(event, listener),
      },
      on: (event: string, listener: (...args: unknown[]) => void) => proc.on(event, listener),
      kill: () => true,
      killed: false,
    } as unknown as ImsgChildProcessLike,
    writtenLines,
    emitStdoutLine: (payload) => stdout.emit("data", `${JSON.stringify(payload)}\n`),
    emitClose: (code, signal) => proc.emit("close", code, signal),
    emitError: (err) => proc.emit("error", err),
    requestsFor: (method) =>
      writtenLines
        .map((line) => JSON.parse(line) as { id: number; method: string; params: Record<string, unknown> })
        .filter((req) => req.method === method),
  };
  return fake;
}

/** Auto-responds to watch.subscribe with {subscription:1} and to
 *  chats.list with {chats:[]} the instant each request line lands, so
 *  tests only need to react to "send" requests (or inbound notifications
 *  they push themselves) explicitly. */
function wireAutoRespond(fake: FakeImsgChild): void {
  const responded = new Set<number>();
  const originalWrite = fake.child.stdin!.write.bind(fake.child.stdin);
  fake.child.stdin!.write = ((chunk: string, callback?: (err?: Error | null) => void) => {
    const result = originalWrite(chunk, callback);
    const req = JSON.parse(chunk) as { id: number; method: string };
    if (!responded.has(req.id)) {
      if (req.method === "watch.subscribe") {
        responded.add(req.id);
        setImmediate(() => fake.emitStdoutLine({ jsonrpc: "2.0", id: req.id, result: { subscription: 1 } }));
      } else if (req.method === "chats.list") {
        responded.add(req.id);
        setImmediate(() => fake.emitStdoutLine({ jsonrpc: "2.0", id: req.id, result: { chats: [] } }));
      }
    }
    return result;
  }) as NonNullable<typeof fake.child.stdin>["write"];
}

function imessageConfig(): LocalBridgeRuntimeConfig {
  const config = LOCAL_BRIDGE_PERSONAL_CHANNEL_CONFIGS.find((item) => item.channelKey === "imessage_personal");
  assert.ok(config);
  return config!;
}

function outboundFrame(payload: Partial<GatewayChannelOutboundPayload>): GatewayRequestEnvelope<GatewayChannelOutboundPayload> {
  return {
    id: "req-imsg-1",
    kind: "request",
    protocolVersion: "v1alpha2",
    type: "channel.outbound",
    ts: new Date().toISOString(),
    scope: { tenant_id: "t1", workspace_id: "w1", user_id: "u1", device_id: "d1", gateway_id: "g1" },
    payload: {
      channel_key: "imessage_personal",
      provider: "bluebubbles_local_bridge",
      idempotency_key: "idem-1",
      remote_jid: "chat_guid:iMessage;-;+15557654321",
      text: "hello imessage",
      ...payload,
    },
  };
}

test("start(): subscribes over imsg rpc and publishes an inbound DM through the shared publisher", async () => {
  const fake = makeFakeImsgChild();
  wireAutoRespond(fake);
  const spawnImpl: ImsgSpawnImpl = () => fake.child;
  const runtime = new ImsgIMessagePersonalChannelRuntime(imessageConfig(), { spawnImpl });
  const inbound: GatewayChannelInboundPayload[] = [];
  runtime.setPublisher({
    publishStateUpdate: async () => undefined,
    publishEvent: async (_type, payload) => {
      inbound.push(payload);
    },
  });

  try {
    await runtime.start();
    await eventually(() => {
      assert.equal(runtime.isWatchReady(), true);
    });
    fake.emitStdoutLine({
      method: "message",
      params: {
        id: 501,
        guid: "imsg-in-1",
        text: "hi sage",
        sender: "+15557654321",
        chat_guid: "iMessage;-;+15557654321",
        is_from_me: false,
      },
    });
    await eventually(() => {
      assert.equal(inbound.length, 1);
      assert.equal(inbound[0].channel_key, "imessage_personal");
      assert.equal(inbound[0].provider, "bluebubbles_local_bridge");
      assert.equal(inbound[0].message.remote_jid, "chat_guid:iMessage;-;+15557654321");
      assert.equal(inbound[0].message.text, "hi sage");
    });
  } finally {
    await runtime.stop();
  }
});

// UPDATED (channel-audit Defect 3): this runtime's own shouldSkip drop was
// REMOVED — it was a second, parallel copy of the exact gate
// local-bridge-runtime.ts's LocalBridgePersonalChannelRuntime already had
// removed in commit 53e3abf40, left behind because this in-process `imsg`
// RPC transport is a distinct code path from that class's BlueBubbles-over-
// HTTP one (see telegram-group-gate.test.ts's identical 2026-07-23 update,
// and bluebubbles-bridge.test.ts / signal-cli-bridge.test.ts's matching
// 53e3abf40 update for the other two local-bridge transports). The backend's
// _enforce_group_policy is now the ONE shared resolver for this decision —
// this runtime's job is only to compute and always forward the raw mention
// FACTS (is_group/is_mentioned/is_reply_to_sage), never to withhold a
// message based on them.
test("group gate: an unaddressed group message is still published, with is_mentioned=false so the backend resolver can decide", async () => {
  const fake = makeFakeImsgChild();
  wireAutoRespond(fake);
  const runtime = new ImsgIMessagePersonalChannelRuntime(imessageConfig(), { spawnImpl: () => fake.child });
  const inbound: GatewayChannelInboundPayload[] = [];
  runtime.setPublisher({
    publishStateUpdate: async () => undefined,
    publishEvent: async (_type, payload) => {
      inbound.push(payload);
    },
  });

  try {
    await runtime.start();
    await eventually(() => assert.equal(runtime.isWatchReady(), true));
    fake.emitStdoutLine({
      method: "message",
      params: {
        id: 601,
        guid: "imsg-group-in-1",
        text: "what time is dinner",
        sender: "+15557654321",
        chat_guid: "iMessage;+;chat-family",
        is_group: true,
      },
    });
    await eventually(() => {
      assert.equal(inbound.length, 1, "an unaddressed group message must still reach publishEvent — this runtime no longer decides shouldSkip");
    });
    const message = inbound[0].message as Record<string, unknown>;
    assert.equal(message.is_group, true);
    assert.equal(message.is_mentioned, false);
    assert.equal(message.is_reply_to_sage, false);
  } finally {
    await runtime.stop();
  }
});

test("handleChannelOutbound sends over the live watch client and returns the imsg-assigned message id", async () => {
  const fake = makeFakeImsgChild();
  wireAutoRespond(fake);
  const runtime = new ImsgIMessagePersonalChannelRuntime(imessageConfig(), { spawnImpl: () => fake.child });
  runtime.setPublisher({ publishStateUpdate: async () => undefined, publishEvent: async () => undefined });

  try {
    await runtime.start();
    await eventually(() => assert.equal(runtime.isWatchReady(), true));

    const resultPromise = runtime.handleChannelOutbound(outboundFrame({}));
    await eventually(() => assert.equal(fake.requestsFor("send").length, 1));
    const sendReq = fake.requestsFor("send")[0];
    assert.equal(sendReq.params.text, "hello imessage");
    assert.equal(sendReq.params.chat_guid, "iMessage;-;+15557654321");
    fake.emitStdoutLine({ jsonrpc: "2.0", id: sendReq.id, result: { guid: "imsg-out-1" } });

    const result = await resultPromise;
    assert.equal(result.delivered, true);
    assert.equal(result.external_message_id, "imsg-out-1");
    assert.equal(result.channel_key, "imessage_personal");
    assert.equal(result.provider, "bluebubbles_local_bridge");
  } finally {
    await runtime.stop();
  }
});

test("handleChannelOutbound fails fast with an actionable error when the imsg bridge never connected", async () => {
  const runtime = new ImsgIMessagePersonalChannelRuntime(imessageConfig(), {
    spawnImpl: () => makeFakeImsgChild().child,
  });
  await assert.rejects(
    () => runtime.handleChannelOutbound(outboundFrame({})),
    /imsg bridge is not connected/,
  );
});

test("getHealthSnapshot: reports not_configured when the imsg binary is missing", async () => {
  const execImpl: ImsgExecImpl = async () => {
    throw Object.assign(new Error("spawn imsg ENOENT"), { code: "ENOENT" });
  };
  const runtime = new ImsgIMessagePersonalChannelRuntime(imessageConfig(), { execImpl });
  const health = await runtime.getHealthSnapshot();
  assert.equal(health.status, "not_configured");
  assert.equal(health.connected, false);
  assert.deepEqual(health.issues, ["imessage_personal_imsg_not_installed"]);
});

test("getHealthSnapshot: reports connected when every probe stage passes", async () => {
  const execImpl: ImsgExecImpl = async (_command, args) => {
    const key = args.join(" ");
    if (key.startsWith("rpc --help")) {
      return { code: 0, stdout: "usage: imsg rpc", stderr: "" };
    }
    if (key.startsWith("status --json")) {
      return { code: 0, stdout: JSON.stringify({ advanced_features: true, v2_ready: true }), stderr: "" };
    }
    throw new Error(`unexpected exec: ${key}`);
  };
  const fake = makeFakeImsgChild();
  wireAutoRespond(fake);
  const runtime = new ImsgIMessagePersonalChannelRuntime(imessageConfig(), {
    execImpl,
    spawnImpl: () => fake.child,
  });
  const health = await runtime.getHealthSnapshot();
  assert.equal(health.status, "connected");
  assert.equal(health.connected, true);
});

test("restart recovery: after the watch client dies, the runtime resubscribes with since_rowid at the last dispatched rowid", async () => {
  const first = makeFakeImsgChild();
  const second = makeFakeImsgChild();
  wireAutoRespond(first);
  wireAutoRespond(second);
  const children = [first, second];
  const spawnImpl: ImsgSpawnImpl = () => (children.shift() ?? second).child;
  const runtime = new ImsgIMessagePersonalChannelRuntime(imessageConfig(), { spawnImpl });
  runtime.setPublisher({ publishStateUpdate: async () => undefined, publishEvent: async () => undefined });

  try {
    await runtime.start();
    await eventually(() => assert.equal(runtime.isWatchReady(), true));
    assert.equal(first.requestsFor("watch.subscribe").length, 1);
    assert.equal(first.requestsFor("watch.subscribe")[0].params.since_rowid, undefined);

    first.emitStdoutLine({
      method: "message",
      params: { id: 777, guid: "imsg-in-recover-1", text: "hi", sender: "+15557654321", chat_guid: "iMessage;-;+15557654321" },
    });
    await new Promise((resolve) => setImmediate(resolve));

    // Simulate the imsg process dying unexpectedly (crash, Mac slept, etc.).
    first.emitClose(1, null);

    await eventually(
      () => {
        assert.equal(second.requestsFor("watch.subscribe").length, 1);
      },
      2_000,
    );
    assert.equal(second.requestsFor("watch.subscribe")[0].params.since_rowid, 777);
  } finally {
    await runtime.stop();
  }
});

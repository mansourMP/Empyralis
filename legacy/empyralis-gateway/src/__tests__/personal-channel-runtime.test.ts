import assert from "node:assert/strict";
import test from "node:test";

import {
  PersonalChannelRuntimeRegistry,
  type PersonalChannelCapabilityManifest,
  type PersonalChannelRuntime,
} from "../channels/personal-runtime";
import {
  LOCAL_BRIDGE_PERSONAL_CHANNEL_CONFIGS,
  LOCAL_BRIDGE_PERSONAL_CHANNEL_MANIFESTS,
  LocalBridgePersonalChannelRuntime,
} from "../channels/local-bridge-runtime";
import { startLocalBridgeHarness } from "../dev/local-bridge-harness";

async function eventually(assertion: () => void | Promise<void>, timeoutMs = 500): Promise<void> {
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

function liveManifest(): PersonalChannelCapabilityManifest {
  return {
    channelKey: "telegram_personal",
    label: "Telegram Personal",
    provider: "telegram_gramjs",
    runtimeLane: "personal_gateway",
    stage: "live",
    status: "live",
    liveCapable: true,
    requiresAgentComputer: true,
    sessionOwner: "paired_gateway",
    setupKind: "phone_login",
    capabilities: ["configure", "inbound", "outbound"],
    chatTypes: ["dm", "group"],
    media: { text: true },
    safety: {
      ownerPairingRequired: true,
      allowlistRequired: true,
      studioBusinessAllowed: false,
      customerPublicSendAllowed: false,
    },
  };
}

function fakeLiveRuntime(): PersonalChannelRuntime {
  const manifest = liveManifest();
  return {
    requestedCapabilities: () => [
      "channel.telegram.personal.configure",
      "channel.telegram.personal.outbound",
    ],
    supportsCapability: (capabilityId: string) => capabilityId === "channel.telegram.personal.configure",
    handleCapabilityInvoke: async () => ({ ok: true }),
    supportsChannel: (channelKey: string) => channelKey === manifest.channelKey,
    handleChannelOutbound: async () => ({ ok: true }),
    handleGatewayConnected: async () => undefined,
    handleGatewayDisconnected: async () => undefined,
    start: async () => undefined,
    stop: async () => undefined,
    getManifest: () => manifest,
    getHealthSnapshot: () => ({
      channelKey: manifest.channelKey,
      provider: manifest.provider,
      status: "connected",
      running: true,
      connected: true,
      reconnectAttempts: 0,
    }),
  };
}

test("personal channel registry exposes live and local-bridge channel manifests", async () => {
  const signalConfig = LOCAL_BRIDGE_PERSONAL_CHANNEL_CONFIGS.find(
    (item) => item.channelKey === "signal_personal",
  );
  assert.ok(signalConfig);
  const registry = new PersonalChannelRuntimeRegistry([
    fakeLiveRuntime(),
    new LocalBridgePersonalChannelRuntime(signalConfig),
  ]);

  assert.deepEqual(registry.requestedCapabilities(), ["channel.telegram.personal.configure"]);
  assert.equal(registry.runtimeForChannel("telegram_personal")?.getManifest?.().liveCapable, true);
  assert.equal(registry.runtimeForChannel("signal_personal")?.getManifest?.().liveCapable, true);

  const manifests = registry.channelManifests();
  assert.equal(manifests.length, 2);
  assert.equal(manifests.find((item) => item.channelKey === "signal_personal")?.status, "not_configured");

  const health = await registry.healthSnapshots();
  assert.equal(health.find((item) => item.channelKey === "telegram_personal")?.connected, true);
  assert.deepEqual(
    health.find((item) => item.channelKey === "signal_personal")?.issues,
    ["signal_personal_bridge_not_configured"],
  );
});

test("local bridge personal channel runtime fails closed until bridge is configured", async () => {
  const wechatConfig = LOCAL_BRIDGE_PERSONAL_CHANNEL_CONFIGS.find(
    (item) => item.channelKey === "wechat_personal",
  );
  assert.ok(wechatConfig);
  const runtime = new LocalBridgePersonalChannelRuntime(wechatConfig);

  assert.equal(runtime.supportsChannel("wechat_personal"), true);
  assert.equal(runtime.supportsCapability("channel.wechat.personal.outbound"), false);
  await assert.rejects(
    () =>
      runtime.handleChannelOutbound({
        id: "req-1",
        kind: "request",
        protocolVersion: "v1alpha2",
        type: "channel.outbound",
        ts: new Date().toISOString(),
        scope: { tenant_id: "t1", workspace_id: "w1", user_id: "u1", device_id: "d1", gateway_id: "g1" },
        payload: {
          channel_key: "wechat_personal",
          provider: "wechat_local_bridge",
          idempotency_key: "idem-1",
          remote_jid: "peer",
          text: "hello",
        },
      }),
    /bridge is not configured/,
  );
});

test("local bridge personal channel runtime rejects public bridge URLs by default", async () => {
  const wechatConfig = LOCAL_BRIDGE_PERSONAL_CHANNEL_CONFIGS.find(
    (item) => item.channelKey === "wechat_personal",
  );
  assert.ok(wechatConfig);
  const previousUrl = process.env.EMPYRALIS_WECHAT_BRIDGE_URL;
  process.env.EMPYRALIS_WECHAT_BRIDGE_URL = "https://public-bridge.example.com";
  try {
    const runtime = new LocalBridgePersonalChannelRuntime(wechatConfig);
    const health = await runtime.getHealthSnapshot();

    assert.equal(health.status, "invalid_config");
    assert.deepEqual(health.issues, ["wechat_personal_bridge_url_invalid"]);
    await assert.rejects(
      () =>
        runtime.handleChannelOutbound({
          id: "req-public-bridge",
          kind: "request",
          protocolVersion: "v1alpha2",
          type: "channel.outbound",
          ts: new Date().toISOString(),
          scope: { tenant_id: "t1", workspace_id: "w1", user_id: "u1", device_id: "d1", gateway_id: "g1" },
          payload: {
            channel_key: "wechat_personal",
            provider: "wechat_local_bridge",
            idempotency_key: "idem-public",
            remote_jid: "peer",
            text: "hello",
          },
        }),
      /private-network Agent Computer bridge/,
    );
  } finally {
    if (previousUrl === undefined) {
      delete process.env.EMPYRALIS_WECHAT_BRIDGE_URL;
    } else {
      process.env.EMPYRALIS_WECHAT_BRIDGE_URL = previousUrl;
    }
  }
});

test("local bridge personal channel runtime sends through configured bridge", async () => {
  const signalConfig = LOCAL_BRIDGE_PERSONAL_CHANNEL_CONFIGS.find(
    (item) => item.channelKey === "signal_personal",
  );
  assert.ok(signalConfig);
  const previousUrl = process.env.EMPYRALIS_SIGNAL_BRIDGE_URL;
  process.env.EMPYRALIS_SIGNAL_BRIDGE_URL = "https://signal-bridge.local";
  const previousFetch = globalThis.fetch;
  const calls: Array<{ url: string; body: Record<string, unknown> }> = [];
  globalThis.fetch = (async (url: string | URL | Request, init?: RequestInit) => {
    calls.push({
      url: String(url),
      body: JSON.parse(String(init?.body || "{}")) as Record<string, unknown>,
    });
    return new Response(JSON.stringify({ delivered: true, external_message_id: "sig-msg-1" }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  }) as typeof fetch;
  try {
    const runtime = new LocalBridgePersonalChannelRuntime(signalConfig);
    const result = await runtime.handleChannelOutbound({
      id: "req-1",
      kind: "request",
      protocolVersion: "v1alpha2",
      type: "channel.outbound",
      ts: new Date().toISOString(),
      scope: { tenant_id: "t1", workspace_id: "w1", user_id: "u1", device_id: "d1", gateway_id: "g1" },
      payload: {
        channel_key: "signal_personal",
        provider: "signal_local_bridge",
        idempotency_key: "idem-1",
        remote_jid: "peer",
        text: "hello",
      },
    });

    assert.equal(result.delivered, true);
    assert.equal(result.external_message_id, "sig-msg-1");
    assert.equal(calls[0].url, "https://signal-bridge.local/messages");
    assert.equal(calls[0].body.channel_key, "signal_personal");
  } finally {
    globalThis.fetch = previousFetch;
    if (previousUrl === undefined) {
      delete process.env.EMPYRALIS_SIGNAL_BRIDGE_URL;
    } else {
      process.env.EMPYRALIS_SIGNAL_BRIDGE_URL = previousUrl;
    }
  }
});

test("local bridge personal channel runtime suppresses duplicate inbound bridge events", async () => {
  const signalConfig = LOCAL_BRIDGE_PERSONAL_CHANNEL_CONFIGS.find(
    (item) => item.channelKey === "signal_personal",
  );
  assert.ok(signalConfig);
  const previousUrl = process.env.EMPYRALIS_SIGNAL_BRIDGE_URL;
  const previousPoll = process.env.EMPYRALIS_SIGNAL_BRIDGE_POLL_MS;
  const previousFetch = globalThis.fetch;
  process.env.EMPYRALIS_SIGNAL_BRIDGE_URL = "http://127.0.0.1:9988";
  process.env.EMPYRALIS_SIGNAL_BRIDGE_POLL_MS = "25";
  let eventFetches = 0;
  globalThis.fetch = (async (url: string | URL | Request) => {
    if (String(url).includes("/events")) {
      eventFetches += 1;
      return new Response(JSON.stringify({
        items: [
          {
            external_message_id: "same-event",
            remote_jid: "peer-1",
            text: "same message",
            received_at: "2026-06-07T00:00:00.000Z",
          },
        ],
      }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }
    return new Response(JSON.stringify({ connected: true, status: "connected" }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  }) as typeof fetch;

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
      assert.ok(eventFetches >= 2);
      assert.equal(inbound.length, 1);
    }, 300);
  } finally {
    await runtime.stop();
    globalThis.fetch = previousFetch;
    if (previousUrl === undefined) {
      delete process.env.EMPYRALIS_SIGNAL_BRIDGE_URL;
    } else {
      process.env.EMPYRALIS_SIGNAL_BRIDGE_URL = previousUrl;
    }
    if (previousPoll === undefined) {
      delete process.env.EMPYRALIS_SIGNAL_BRIDGE_POLL_MS;
    } else {
      process.env.EMPYRALIS_SIGNAL_BRIDGE_POLL_MS = previousPoll;
    }
  }
});

test("local bridge manifest list includes Signal, iMessage, and WeChat as live-capable", () => {
  const byKey = new Map(LOCAL_BRIDGE_PERSONAL_CHANNEL_MANIFESTS.map((item) => [item.channelKey, item]));
  assert.equal(byKey.get("signal_personal")?.liveCapable, true);
  assert.equal(byKey.get("imessage_personal")?.liveCapable, true);
  assert.equal(byKey.get("wechat_personal")?.liveCapable, true);
});

test("local bridge harness proves health, outbound, and inbound event contract for all bridge channels", async () => {
  const harness = await startLocalBridgeHarness();
  const previousEnv = new Map<string, string | undefined>();
  const runtimes: LocalBridgePersonalChannelRuntime[] = [];
  try {
    for (const config of LOCAL_BRIDGE_PERSONAL_CHANNEL_CONFIGS) {
      previousEnv.set(`${config.envPrefix}_URL`, process.env[`${config.envPrefix}_URL`]);
      previousEnv.set(`${config.envPrefix}_POLL_MS`, process.env[`${config.envPrefix}_POLL_MS`]);
      process.env[`${config.envPrefix}_URL`] = harness.url;
      process.env[`${config.envPrefix}_POLL_MS`] = "25";

      const runtime = new LocalBridgePersonalChannelRuntime(config);
      runtimes.push(runtime);
      const inbound: unknown[] = [];
      runtime.setPublisher({
        publishStateUpdate: async () => undefined,
        publishEvent: async (_type, payload) => {
          inbound.push(payload);
        },
      });

      const health = await runtime.getHealthSnapshot();
      assert.equal(health.connected, true);
      assert.equal(health.status, "connected");

      const result = await runtime.handleChannelOutbound({
        id: `req-${config.channelKey}`,
        kind: "request",
        protocolVersion: "v1alpha2",
        type: "channel.outbound",
        ts: new Date().toISOString(),
        scope: { tenant_id: "t1", workspace_id: "w1", user_id: "u1", device_id: "d1", gateway_id: "g1" },
        payload: {
          channel_key: config.channelKey,
          provider: config.provider,
          idempotency_key: `idem-harness-${config.channelKey}`,
          remote_jid: `${config.channelKey}-peer`,
          text: `hello through ${config.channelKey}`,
        },
      });
      assert.equal(result.delivered, true);
      const sent = harness.sentMessages().find((item) => item.channel_key === config.channelKey);
      assert.equal(sent?.text, `hello through ${config.channelKey}`);

      await runtime.start();
      const eventResponse = await fetch(`${harness.url}/events`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          channel_key: config.channelKey,
          external_message_id: `incoming-${config.channelKey}`,
          remote_jid: `${config.channelKey}-peer`,
          push_name: "Mansur",
          text: `hello sage from ${config.channelKey}`,
        }),
      });
      assert.equal(eventResponse.ok, true);
      await eventually(() => {
        assert.equal(inbound.length, 1);
        const payload = inbound[0] as { channel_key?: string; message?: { text?: string } };
        assert.equal(payload.channel_key, config.channelKey);
        assert.equal(payload.message?.text, `hello sage from ${config.channelKey}`);
      });
    }
  } finally {
    for (const runtime of runtimes) {
      await runtime.stop();
    }
    await harness.close();
    for (const [key, value] of previousEnv.entries()) {
      if (value === undefined) {
        delete process.env[key];
      } else {
        process.env[key] = value;
      }
    }
  }
});

import test from "node:test";
import assert from "node:assert/strict";

import { GatewayWsClient } from "../cloud/ws-client";

function buildClient(): GatewayWsClient {
  const mockConfig = {
    apiBaseUrl: "http://localhost:8001/api",
    stateDir: "/tmp/test-state",
    heartbeatIntervalMs: 20000,
    reconnectMinDelayMs: 1,
    reconnectMaxDelayMs: 1,
    supervisorUrl: "http://localhost:7788",
    supervisorTimeoutMs: 10000,
    browserPythonExecutable: "python3",
    browserProjectRoot: "/tmp",
    supervisorSecret: undefined,
    pairingToken: undefined,
    gatewayId: undefined,
    deviceId: undefined,
    gatewayToken: undefined,
    displayName: "test",
  };
  const mockDb = {
    ensureReady: async () => {},
    filePath: (name: string) => "/tmp/" + name,
    rootDirPath: () => "/tmp",
    readJson: async () => ({}),
    writeJson: async (_name: string, _value: unknown) => _value,
    appendNdjson: async () => {},
  };
  const mockJournal = {
    journalFilePath: () => "/tmp/journal.ndjson",
    append: async () => ({ cursor: 1 }),
    lastCursor: async () => 0,
  };
  const mockOutbox = {
    summarize: async () => ({ total: 0, pending: 0, failed: 0, acknowledged: 0 }),
    listReplayablePending: async () => [],
  };
  const mockCheckpoints = {
    load: async () => ({}),
    save: async (s: Record<string, unknown>) => s,
    saveHealthState: async () => ({}),
    markRecovered: async () => ({}),
  };
  const mockTokenStore = {
    load: async () => ({ sessionId: "sess-1", sessionToken: "tok-1" }),
    save: async () => {},
    clearSession: async () => {},
  };
  const mockCapabilityRouter = {
    supportedCapabilities: () => [],
    handleToolInvoke: async () => ({}),
    handleToolInterrupt: async () => ({}),
  };
  const mockPersonalChannelRuntimes = {
    handleGatewayDisconnected: async () => {},
    handleGatewayConnected: async () => {},
    runtimeForChannel: () => undefined,
    runtimeForCapability: () => undefined,
  };
  return new GatewayWsClient(
    mockConfig as any,
    mockDb as any,
    mockJournal as any,
    mockOutbox as any,
    mockCheckpoints as any,
    mockTokenStore as any,
    mockCapabilityRouter as any,
    mockPersonalChannelRuntimes as any,
  );
}

test("afterConnected hook runs only after gateway connect succeeds", async () => {
  const client = buildClient();
  const events: string[] = [];
  const harness = client as unknown as {
    connect: (...args: unknown[]) => Promise<void>;
    awaitSocketClose: () => Promise<void>;
  };
  harness.connect = async () => {
    events.push("connect");
  };
  harness.awaitSocketClose = async () => {
    events.push("wait");
    throw new Error("status 401");
  };

  await assert.rejects(
    () =>
      client.run({ gatewayId: "gw-1", deviceId: "dev-1", createdAt: "now", updatedAt: "now" }, {} as any, {
        afterConnected: async () => {
          events.push("startAll");
        },
      }),
    /status 401/,
  );

  assert.deepEqual(events, ["connect", "startAll", "wait"]);
});

test("afterConnected hook does not run when gateway connect fails", async () => {
  const client = buildClient();
  const events: string[] = [];
  const harness = client as unknown as {
    connect: (...args: unknown[]) => Promise<void>;
    awaitSocketClose: () => Promise<void>;
  };
  harness.connect = async () => {
    events.push("connect");
    throw new Error("status 401");
  };
  harness.awaitSocketClose = async () => {
    events.push("wait");
  };

  await assert.rejects(
    () =>
      client.run({ gatewayId: "gw-1", deviceId: "dev-1", createdAt: "now", updatedAt: "now" }, {} as any, {
        afterConnected: async () => {
          events.push("startAll");
        },
      }),
    /status 401/,
  );

  assert.deepEqual(events, ["connect"]);
});

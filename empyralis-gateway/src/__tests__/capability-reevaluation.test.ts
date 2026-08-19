import test from "node:test";
import assert from "node:assert/strict";

import type { GatewayRuntimeMetadata } from "../runtime/runtime-metadata";

/**
 * Regression tests for reliability audit §5 (docs/design/reliability-audit-1
 * -gateway-health.md): requestedCapabilities used to be computed exactly
 * once at startup and never recomputed, so a capability that became
 * available only after the process started (Docker installed, Ollama
 * started, a CLI signed in) stayed invisible to the backend until a full
 * restart. GatewayWsClient.syncRequestedCapabilities() (cloud/ws-client.ts,
 * called at the top of every sendHeartbeat() tick) is the fix: it re-calls
 * GatewayCapabilityRouter.supportedCapabilities() every heartbeat and, when
 * the advertised SET changed, mutates the shared runtimeMetadata object in
 * place so the next heartbeat payload (and the next gateway.connect on
 * reconnect) picks it up without a restart.
 */

function makeRuntimeMetadata(requestedCapabilities: string[]): GatewayRuntimeMetadata {
  return {
    gatewayVersion: "0.1.0",
    buildFingerprint: null,
    launchUpdatability: null,
    hostname: "agent-box",
    platform: "darwin-arm64",
    pid: 123,
    startedAt: "2026-07-20T00:00:00Z",
    requestedCapabilities,
    nativeRuntime: {
      os: "darwin",
      arch: "arm64",
      release: "test",
      hostname: "agent-box",
      desktop_session: "user_session",
      system_service_mode: false,
    },
    deviceMetadata: {},
  };
}

const mockConfig = {
  apiBaseUrl: "http://localhost:8001/api",
  stateDir: "/tmp/test-state",
  heartbeatIntervalMs: 20000,
  reconnectMinDelayMs: 1000,
  reconnectMaxDelayMs: 5000,
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

function makeMockDb() {
  return {
    ensureReady: async () => {},
    filePath: (name: string) => "/tmp/" + name,
    rootDirPath: () => "/tmp",
    readJson: async () => ({}),
    writeJson: async (_name: string, value: unknown) => value,
    appendNdjson: async () => {},
  };
}

function makeMockOutbox() {
  return {
    list: async () => [],
    get: async () => null,
    enqueue: async () => ({}),
    markAttemptStarted: async () => {},
    acknowledge: async () => {},
    markAttemptFailed: async () => {},
    markForReplay: async () => {},
    listReplayablePending: async () => [],
    summarize: async () => ({ total: 0, pending: 0, failed: 0, acknowledged: 0 }),
    prune: async () => 0,
  };
}

function makeMockCheckpoints() {
  return {
    load: async () => ({}),
    save: async (s: Record<string, unknown>) => s,
    saveHealthState: async (_h: string, _s?: Record<string, unknown>) => ({}),
    markRecovered: async () => ({}),
    flush: async () => {},
    currentHealthState: () => "online" as const,
  };
}

function makeMockTokenStore() {
  return {
    load: async () => ({ sessionId: "sess-1", sessionToken: "tok-1" }),
    save: async () => {},
    clearSession: async () => {},
  };
}

function makeMockPersonalChannelRuntimes() {
  return {
    all: () => [],
    requestedCapabilities: () => [],
    runtimeForCapability: () => undefined,
    runtimeForChannel: () => undefined,
    setPublisher: () => {},
    startAll: async () => {},
    stopAll: async () => {},
    handleGatewayConnected: async () => {},
    handleGatewayDisconnected: async () => {},
  };
}

async function buildClient(supportedCapabilities: () => string[]) {
  const { GatewayWsClient } = await import("../cloud/ws-client");
  const journalEntries: Array<{ category: string; type: string; payload: unknown }> = [];
  const mockJournal = {
    journalFilePath: () => "/tmp/journal.ndjson",
    append: async (category: string, type: string, payload: unknown) => {
      journalEntries.push({ category, type, payload });
      return { cursor: journalEntries.length };
    },
    lastCursor: async () => 0,
  };
  const mockCapabilityRouter = {
    supportedCapabilities,
    handleToolInvoke: async () => ({}),
    handleToolInterrupt: async () => ({}),
  };

  const client = new GatewayWsClient(
    mockConfig as any,
    makeMockDb() as any,
    mockJournal as any,
    makeMockOutbox() as any,
    makeMockCheckpoints() as any,
    makeMockTokenStore() as any,
    mockCapabilityRouter as any,
    makeMockPersonalChannelRuntimes() as any,
    undefined, // webSocketImpl: unused by these tests, real default is fine
    // dockerAutostart: several tests below drive sendHeartbeat() with
    // "shell.execute" in requestedCapabilities, which (since docker-
    // autostart.ts landed) reaches this dependency whenever the real,
    // unmocked docker probe inside refreshPassiveInventorySnapshot()
    // reports Docker not ready. Left at the real default this fake would
    // spawn `open -a Docker` on whatever machine runs this suite —
    // forbidden outright (CLAUDE.md: never touch the founder's own Docker
    // state as a test side effect). This suite has nothing to do with
    // Docker autostart at all, so a no-op fake that is never even meant to
    // be asserted on is the correct stand-in.
    async () => ({ kind: "not_installed" }),
  );
  return { client, journalEntries };
}

test("syncRequestedCapabilities leaves runtimeMetadata untouched when the advertised set hasn't changed", async () => {
  const { client, journalEntries } = await buildClient(() => ["shell.execute", "browser.session.start"]);
  const runtimeMetadata = makeRuntimeMetadata(["browser.session.start", "shell.execute"]);
  const originalArrayRef = runtimeMetadata.requestedCapabilities;

  const sync = (client as unknown as {
    syncRequestedCapabilities: (rm: GatewayRuntimeMetadata) => void;
  }).syncRequestedCapabilities.bind(client);
  sync(runtimeMetadata);

  assert.equal(runtimeMetadata.requestedCapabilities, originalArrayRef, "must not reassign when order-insensitive contents are identical");
  assert.equal(journalEntries.length, 0, "must not journal a no-op");
});

test("syncRequestedCapabilities picks up a NEWLY available capability without a restart (Docker/Ollama/CLI becoming ready)", async () => {
  // Simulates the exact gap from the reliability audit: llm.generate wasn't
  // ready at startup (Ollama wasn't running yet), so it was excluded from
  // requestedCapabilities. The router now reports it — the client must
  // notice on its own, without index.ts ever being re-run.
  const { client, journalEntries } = await buildClient(() => ["shell.execute", "llm.generate"]);
  const runtimeMetadata = makeRuntimeMetadata(["shell.execute"]);

  const sync = (client as unknown as {
    syncRequestedCapabilities: (rm: GatewayRuntimeMetadata) => void;
  }).syncRequestedCapabilities.bind(client);
  sync(runtimeMetadata);

  assert.deepEqual(
    [...runtimeMetadata.requestedCapabilities].sort(),
    ["llm.generate", "shell.execute"],
  );
  assert.equal(journalEntries.length, 1);
  assert.equal(journalEntries[0].type, "gateway.capabilities.updated");
  assert.deepEqual((journalEntries[0].payload as any).added, ["llm.generate"]);
  assert.deepEqual((journalEntries[0].payload as any).removed, []);
});

test("syncRequestedCapabilities also reports a capability that disappeared", async () => {
  const { client, journalEntries } = await buildClient(() => ["shell.execute"]);
  const runtimeMetadata = makeRuntimeMetadata(["shell.execute", "llm.generate"]);

  const sync = (client as unknown as {
    syncRequestedCapabilities: (rm: GatewayRuntimeMetadata) => void;
  }).syncRequestedCapabilities.bind(client);
  sync(runtimeMetadata);

  assert.deepEqual(runtimeMetadata.requestedCapabilities, ["shell.execute"]);
  assert.deepEqual((journalEntries[0].payload as any).removed, ["llm.generate"]);
});

test("sendHeartbeat calls syncRequestedCapabilities before building the heartbeat payload, so a live socket tick re-advertises automatically", async () => {
  let capabilitiesNow = ["shell.execute"];
  const { client } = await buildClient(() => capabilitiesNow);
  const runtimeMetadata = makeRuntimeMetadata(["shell.execute"]);

  const sentPayloads: Array<Record<string, unknown>> = [];
  // Stub sendRequest so sendHeartbeat() doesn't need a real socket — this
  // proves the ordering (sync happens before the payload used in the
  // request is built) without needing a full WS round trip.
  (client as unknown as {
    sendRequest: (type: string, payload: Record<string, unknown>, ...rest: unknown[]) => Promise<unknown>;
  }).sendRequest = async (_type: string, payload: Record<string, unknown>) => {
    sentPayloads.push(payload);
    return { ok: true };
  };

  // First tick: capability set matches runtimeMetadata already.
  await client.sendHeartbeat({} as any, runtimeMetadata);
  assert.deepEqual((sentPayloads[0].capability_readiness as any).requested, ["shell.execute"]);

  // A capability becomes available between ticks (e.g. Docker just finished installing).
  capabilitiesNow = ["shell.execute", "shell_sandbox.exec"];
  await client.sendHeartbeat({} as any, runtimeMetadata);
  assert.deepEqual(
    [...(sentPayloads[1].capability_readiness as any).requested].sort(),
    ["shell.execute", "shell_sandbox.exec"],
  );
});

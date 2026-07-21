import test from "node:test";
import assert from "node:assert/strict";

import { decodeFrame } from "../protocol/codec";
import type { GatewayRequestEnvelope } from "../protocol/types";
import type { GatewayRuntimeMetadata } from "../runtime/runtime-metadata";

// ---------------------------------------------------------------------------
// CONFIRMED-HEALS / resource-safety check (reliability wave verification).
//
// The audit brief specifically asks: "Resource safety under REPEATED
// failure: are timers/intervals cleared... on repeated reconnect/retry
// cycles (a self-heal that leaks handles is a slow death)." No existing
// ws-client test drives GatewayWsClient through multiple full
// connect -> drop -> reconnect cycles while watching for a leaked timer, so
// this fills that gap.
//
// The one interval in this file's reconnect path is awaitSocketClose()'s
// `closePoll` (cloud/ws-client.ts:686-690) -- a fallback poller in case the
// onclose event handler is ever silently swallowed. It's created fresh on
// every call and is only supposed to be cleared exactly once, by finish()
// (either via the wrapped onclose firing, or immediately if the socket was
// already closed by the time awaitSocketClose() is called). This test
// proves that holds across N repeated cycles: total setInterval calls must
// equal total clearInterval calls once every cycle has finished closing.
// ---------------------------------------------------------------------------

interface ScriptedSocketInstance {
  readyState: number;
  onopen: (() => void) | null;
  onerror: ((event: { message?: string }) => void) | null;
  onclose: ((event: { code: number; reason: string }) => void) | null;
  onmessage: ((event: { data: string }) => void) | null;
  send(data: string): void;
  close(): void;
}

let lastSocket: ScriptedSocketInstance | null = null;
let socketInstanceCount = 0;
// When flipped true, the NEXT "gateway.connect" request gets rejected with
// a non-retryable error (matching classifyReconnectError's "credentials are
// invalid" pattern, cloud/reconnect.ts:46) -- the only way this test can
// make run()'s otherwise-infinite reconnect loop actually settle, so the
// test can await it and end cleanly instead of leaving live timers behind.
const failNextConnect = { value: false };

function buildScriptedWebSocketClass() {
  class ScriptedWebSocket implements ScriptedSocketInstance {
    static readonly CONNECTING = 0;
    static readonly OPEN = 1;
    static readonly CLOSING = 2;
    static readonly CLOSED = 3;

    readyState: number = ScriptedWebSocket.CONNECTING;
    onopen: (() => void) | null = null;
    onerror: ((event: { message?: string }) => void) | null = null;
    onclose: ((event: { code: number; reason: string }) => void) | null = null;
    onmessage: ((event: { data: string }) => void) | null = null;

    constructor(_url: string, _protocols?: string[]) {
      lastSocket = this;
      socketInstanceCount += 1;
      setTimeout(() => {
        this.readyState = ScriptedWebSocket.OPEN;
        this.onopen?.();
      }, 0);
    }

    send(data: string): void {
      const decoded = decodeFrame(data);
      if (!decoded.ok || decoded.frame.kind !== "request") {
        return;
      }
      const request = decoded.frame as GatewayRequestEnvelope;
      if (request.type === "gateway.connect" && failNextConnect.value) {
        setTimeout(() => {
          this.onmessage?.({
            data: JSON.stringify({
              kind: "response",
              id: request.id,
              ok: false,
              ts: new Date().toISOString(),
              error: { message: "credentials are invalid" },
            }),
          });
        }, 0);
        return;
      }
      setTimeout(() => {
        this.onmessage?.({
          data: JSON.stringify({
            kind: "response",
            id: request.id,
            ok: true,
            ts: new Date().toISOString(),
            payload: {},
          }),
        });
      }, 0);
    }

    close(): void {
      if (this.readyState === ScriptedWebSocket.CLOSED) {
        return;
      }
      this.readyState = ScriptedWebSocket.CLOSED;
      this.onclose?.({ code: 1006, reason: "simulated drop" });
    }
  }
  return ScriptedWebSocket;
}

async function buildHarness() {
  lastSocket = null;
  socketInstanceCount = 0;
  const { GatewayWsClient } = await import("../cloud/ws-client");
  const ScriptedWebSocket = buildScriptedWebSocketClass();

  const mockConfig = {
    apiBaseUrl: "http://localhost:8001/api",
    stateDir: "/tmp/test-state",
    heartbeatIntervalMs: 999_999,
    // Tiny reconnect backoff so N cycles finish in milliseconds.
    reconnectMinDelayMs: 1,
    reconnectMaxDelayMs: 2,
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

  const healthStates: string[] = [];
  const mockDb = {
    ensureReady: async () => {},
    filePath: (name: string) => "/tmp/" + name,
    rootDirPath: () => "/tmp",
    readJson: async <T,>(_name: string, fallback: T) => fallback,
    writeJson: async (_name: string, value: unknown) => value,
    appendNdjson: async () => {},
  };
  const mockJournal = {
    journalFilePath: () => "/tmp/journal.ndjson",
    append: async () => ({ cursor: 0 }),
    lastCursor: async () => 0,
  };
  const mockOutbox = {
    list: async () => [],
    get: async () => null,
    enqueue: async () => ({}),
    markAttemptStarted: async () => {},
    acknowledge: async () => {},
    markAttemptFailed: async () => {},
    markForReplay: async () => {},
    markUncertain: async () => {},
    listReplayablePending: async () => [],
    summarize: async () => ({ total: 0, pending: 0, failed: 0, acknowledged: 0, uncertain: 0 }),
    prune: async () => 0,
  };
  const mockCheckpoints = {
    load: async () => ({}),
    save: async (s: Record<string, unknown>) => s,
    saveHealthState: async (state: string) => {
      healthStates.push(state);
      return {};
    },
    markRecovered: async () => ({}),
    flush: async () => {},
    currentHealthState: () => "online" as const,
  };
  const mockTokenStore = {
    load: async () => ({ gatewayToken: "gt_test" }),
    save: async () => {},
    clearSession: async () => {},
  };
  const mockCapabilityRouter = {
    supportedCapabilities: () => [],
    handleToolInvoke: async () => ({}),
    handleToolInterrupt: async () => ({}),
  };
  const mockPersonalChannelRuntimes = {
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

  const scope = { tenant_id: "t1", workspace_id: "w1", user_id: "u1", device_id: "dev-1", gateway_id: "gw-1" };
  const sessionPayload = {
    session_id: "sess-1",
    gateway_id: "gw-1",
    session_token: "tok-1",
    ws_url: "ws://localhost:8080",
    heartbeat_interval_seconds: 999_999,
    scope,
    gateway: {},
    expires_at: new Date(Date.now() + 3_600_000).toISOString(),
  };
  const identity = { gatewayId: "gw-1", deviceId: "dev-1" };
  const runtimeMetadata = {
    gatewayVersion: "0.1.0",
    hostname: "test-host",
    platform: "linux-x64",
    pid: 1,
    startedAt: new Date().toISOString(),
    requestedCapabilities: [],
    nativeRuntime: {
      os: "linux",
      arch: "x64",
      release: "t",
      hostname: "test-host",
      desktop_session: "user_session",
      system_service_mode: false,
    },
    deviceMetadata: {},
  } as unknown as GatewayRuntimeMetadata;

  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async () => ({
    ok: true,
    status: 200,
    json: async () => sessionPayload,
    text: async () => JSON.stringify(sessionPayload),
  })) as unknown as typeof fetch;

  const client = new GatewayWsClient(
    mockConfig as any,
    mockDb as any,
    mockJournal as any,
    mockOutbox as any,
    mockCheckpoints as any,
    mockTokenStore as any,
    mockCapabilityRouter as any,
    mockPersonalChannelRuntimes as any,
    ScriptedWebSocket as any,
  );

  return {
    client,
    identity,
    runtimeMetadata,
    healthStates,
    restoreFetch: () => {
      globalThis.fetch = originalFetch;
    },
  };
}

async function waitFor(predicate: () => boolean, timeoutMs = 2_000): Promise<void> {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    if (predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 2));
  }
  throw new Error("waitFor timed out");
}

test(
  "CONFIRMED-HEALS: repeated connect -> drop -> reconnect cycles via run() never leak a closePoll setInterval " +
    "(awaitSocketClose's fallback poller, cloud/ws-client.ts:667-700)",
  async () => {
    const harness = await buildHarness();
    const originalSetInterval = global.setInterval;
    const originalClearInterval = global.clearInterval;
    let intervalsCreated = 0;
    let intervalsCleared = 0;
    (global as any).setInterval = (...args: Parameters<typeof setInterval>) => {
      intervalsCreated += 1;
      return originalSetInterval(...args);
    };
    (global as any).clearInterval = (...args: Parameters<typeof clearInterval>) => {
      intervalsCleared += 1;
      return originalClearInterval(...args);
    };

    const DROP_CYCLES = 4;
    let cyclesSeen = 0;

    try {
      // run() never returns on its own while reconnects keep being
      // retryable -- drive it in the background, drop the socket out from
      // under it DROP_CYCLES times (each one a full connect ->
      // awaitSocketClose -> backoff -> reconnect lap), then make the FINAL
      // connect attempt fail with a non-retryable error so the loop
      // actually throws and settles. This is the only way to observe
      // run() to completion without leaving live timers behind for
      // node:test to hang on.
      const runPromise = harness.client.run(harness.identity as any, harness.runtimeMetadata);

      for (let i = 0; i < DROP_CYCLES; i += 1) {
        await waitFor(() => socketInstanceCount === i + 1);
        await waitFor(() => lastSocket !== null && lastSocket.readyState === 1 /* OPEN */);
        // Give connect()'s gateway.connect round trip a beat to finish
        // (its response is delivered via a setTimeout(...,0) in the fake).
        await new Promise((resolve) => setTimeout(resolve, 10));
        lastSocket?.close();
        cyclesSeen += 1;
        // Small pause so the reconnect backoff (1-2ms) elapses before the
        // next iteration polls for the next socket instance.
        await new Promise((resolve) => setTimeout(resolve, 10));
      }

      // Final lap: let the next connect happen, then fail it fatally.
      await waitFor(() => socketInstanceCount === DROP_CYCLES + 1);
      failNextConnect.value = true;

      await assert.rejects(
        runPromise,
        /credentials are invalid/,
        "run() must actually exit (not hot-loop) once a non-retryable error is hit, proving the loop can settle cleanly",
      );

      assert.equal(cyclesSeen, DROP_CYCLES);
      assert.ok(
        intervalsCreated >= DROP_CYCLES,
        `expected at least ${DROP_CYCLES} closePoll intervals across ${DROP_CYCLES} successful-connect cycles, saw ${intervalsCreated}`,
      );
      assert.equal(
        intervalsCleared,
        intervalsCreated,
        "every closePoll interval created during a reconnect cycle must be cleared -- an imbalance here means a leaked timer " +
          "that would accumulate forever across a long-lived gateway process's reconnect cycles",
      );
      assert.ok(
        harness.healthStates.includes("reconnecting"),
        "the reconnecting health state must actually be observed across these cycles, not silently skipped",
      );
    } finally {
      global.setInterval = originalSetInterval;
      global.clearInterval = originalClearInterval;
      harness.restoreFetch();
    }
  },
);

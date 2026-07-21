import test from "node:test";
import assert from "node:assert/strict";

import { decodeFrame } from "../protocol/codec";
import type { GatewayRequestEnvelope } from "../protocol/types";
import type { GatewayWsClient } from "../cloud/ws-client";
import type { GatewayRuntimeMetadata } from "../runtime/runtime-metadata";

/**
 * Regression coverage for two reliability-audit-1 fixes to GatewayWsClient
 * (cloud/ws-client.ts):
 *
 * 1. connect() now wires a real, purpose-built `onerror` handler onto the
 *    live socket right alongside `onmessage`/`onclose`. Before this fix,
 *    the only `onerror` ever attached was the connect-handshake's own
 *    reject-closure (from openSocket()), left in place — never reassigned
 *    or cleared — once the handshake succeeded, so a post-connect 'error'
 *    event silently no-op'd instead of being recorded anywhere. The new
 *    handler must never throw, and must journal the error with context;
 *    actual recovery is still left to the existing
 *    onclose -> handleSocketFailure() -> run() reconnect loop, since `ws`
 *    always follows an 'error' event with a 'close' event (see
 *    emitErrorAndClose in ws/lib/websocket.js).
 *
 * 2. sendHeartbeat() now threads GatewayCheckpoints.currentHealthState()
 *    into the heartbeat payload's `health_state` field instead of the
 *    literal "online" that buildGatewayHeartbeatPayload() used to
 *    hardcode (cloud/heartbeat-payload.ts).
 */

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

/** A minimal stand-in for `ws`'s WebSocket: opens itself asynchronously,
 *  and auto-answers any outbound request frame with an `ok:true` response
 *  on the next tick — just enough to let GatewayWsClient.connect() (and,
 *  optionally, a later sendHeartbeat() call) reach their success paths
 *  without a real server. `onRequestFrame`, if given, is called with every
 *  decoded outbound request frame before it's auto-answered, so a test can
 *  inspect exactly what GatewayWsClient sent. */
function buildScriptedWebSocketClass(onRequestFrame?: (frame: GatewayRequestEnvelope) => void) {
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
      onRequestFrame?.(request);
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
      this.readyState = ScriptedWebSocket.CLOSED;
      this.onclose?.({ code: 1000, reason: "test close" });
    }
  }
  return ScriptedWebSocket;
}

interface Harness {
  client: GatewayWsClient;
  runtimeMetadata: GatewayRuntimeMetadata;
  scope: { tenant_id: string; workspace_id: string; user_id: string; device_id: string; gateway_id: string };
  journalEvents: { category: string; type: string; payload: unknown }[];
  socket: ScriptedSocketInstance;
  restoreFetch: () => void;
}

/** Builds a GatewayWsClient wired to fully-mocked dependencies plus a
 *  scripted WebSocket, sufficient to drive connect() through a real
 *  success path (session fetch -> WS open -> gateway.connect round-trip).
 *  `currentHealthState` lets a test control what
 *  GatewayCheckpoints.currentHealthState() reports without exercising the
 *  real debounced-write class. */
async function buildHarness(options: {
  currentHealthState?: () => "online" | "offline" | "reconnecting" | "degraded";
  onRequestFrame?: (frame: GatewayRequestEnvelope) => void;
} = {}): Promise<Harness> {
  lastSocket = null;
  const { GatewayWsClient } = await import("../cloud/ws-client");
  const ScriptedWebSocket = buildScriptedWebSocketClass(options.onRequestFrame);

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

  const mockDb = {
    ensureReady: async () => {},
    filePath: (name: string) => "/tmp/" + name,
    rootDirPath: () => "/tmp",
    // Unlike a real disk-backed store, always hand back the caller's own
    // fallback — good enough for connect()'s reads (checkpoints.json,
    // pending-responses.json) which all need an empty-but-well-typed value.
    readJson: async <T,>(_name: string, fallback: T) => fallback,
    writeJson: async (_name: string, value: unknown) => value,
    appendNdjson: async () => {},
  };

  const journalEvents: { category: string; type: string; payload: unknown }[] = [];
  const mockJournal = {
    journalFilePath: () => "/tmp/journal.ndjson",
    append: async (category: string, type: string, payload: unknown) => {
      journalEvents.push({ category, type, payload });
      return { cursor: journalEvents.length };
    },
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
    saveHealthState: async () => ({}),
    markRecovered: async () => ({}),
    flush: async () => {},
    currentHealthState: options.currentHealthState ?? (() => "online" as const),
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

  const scope = {
    tenant_id: "t1",
    workspace_id: "w1",
    user_id: "u1",
    device_id: "dev-1",
    gateway_id: "gw-1",
  };
  const sessionPayload = {
    session_id: "sess-1",
    gateway_id: "gw-1",
    session_token: "tok-1",
    ws_url: "ws://localhost:8080",
    // Deliberately huge so HeartbeatLoop's first tick (chained setTimeout)
    // never fires on its own during a short-lived test.
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

  await client.connect(identity as any, runtimeMetadata);

  const socket = lastSocket;
  if (!socket) {
    throw new Error("test harness bug: connect() did not construct a scripted socket");
  }

  return {
    client,
    runtimeMetadata,
    scope,
    journalEvents,
    socket,
    restoreFetch: () => {
      globalThis.fetch = originalFetch;
    },
  };
}

test("connect() wires a post-connect onerror handler that journals context and never throws", async () => {
  const unhandledExceptions: unknown[] = [];
  const onUncaught = (err: unknown) => unhandledExceptions.push(err);
  process.on("uncaughtException", onUncaught);

  const harness = await buildHarness();
  try {
    assert.equal(
      typeof harness.socket.onerror,
      "function",
      "connect() must wire a real onerror handler on the live socket, not leave it unset",
    );

    harness.journalEvents.length = 0;
    const socketOnError = harness.socket.onerror;
    if (!socketOnError) {
      throw new Error("test harness bug: onerror was not a function despite the assertion above");
    }
    assert.doesNotThrow(() => {
      socketOnError({ message: "simulated ECONNRESET" });
    }, "the post-connect onerror handler must never throw synchronously");

    // Give the handler's async journal.append a tick to land.
    await new Promise((resolve) => setTimeout(resolve, 10));

    assert.ok(
      harness.journalEvents.some(
        (event) => event.type === "gateway.socket.error" && /simulated ECONNRESET/.test(JSON.stringify(event.payload)),
      ),
      "the socket error should be journaled with the underlying message as context",
    );

    // Give any stray promise a chance to surface before asserting silence.
    await new Promise((resolve) => setTimeout(resolve, 20));
    assert.deepEqual(
      unhandledExceptions,
      [],
      "a WebSocket error event must never surface as an uncaughtException",
    );

    await harness.client.disconnect(harness.scope as any);
  } finally {
    process.off("uncaughtException", onUncaught);
    harness.restoreFetch();
  }
});

test("sendHeartbeat() transmits the gateway's real health state, not a hardcoded literal", async () => {
  const sentHeartbeatPayloads: Record<string, unknown>[] = [];
  const harness = await buildHarness({
    currentHealthState: () => "degraded",
    onRequestFrame: (frame) => {
      if (frame.type === "gateway.heartbeat") {
        sentHeartbeatPayloads.push(frame.payload as Record<string, unknown>);
      }
    },
  });

  try {
    await harness.client.sendHeartbeat(harness.scope as any, harness.runtimeMetadata);

    assert.equal(sentHeartbeatPayloads.length, 1, "sendHeartbeat() should have sent exactly one gateway.heartbeat frame");
    assert.equal(
      sentHeartbeatPayloads[0]?.health_state,
      "degraded",
      "the heartbeat payload must carry GatewayCheckpoints.currentHealthState(), not a hardcoded \"online\"",
    );

    await harness.client.disconnect(harness.scope as any);
  } finally {
    harness.restoreFetch();
  }
});

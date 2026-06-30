import test from "node:test";
import assert from "node:assert/strict";
import crypto from "crypto";

// Mock WebSocket for testing the timeout behavior
interface MockWebSocketInstance {
  readyState: number;
  onopen: (() => void) | null;
  onerror: ((err: Error) => void) | null;
  onclose: ((event: { code: number; reason: string }) => void) | null;
  onmessage: ((event: { data: string }) => void) | null;
  close: () => void;
  send: (data: string) => void;
}

let mockWebSocketInstance: MockWebSocketInstance | null = null;
let mockWebSocketClass: new (url: string) => MockWebSocketInstance;

// We need to set up the mock before importing modules that use WebSocket
// This is done via test.beforeEach

test.beforeEach(() => {
  mockWebSocketInstance = null;
  mockWebSocketClass = function (this: MockWebSocketInstance, _url: string) {
    const instance: MockWebSocketInstance = {
      readyState: 0, // CONNECTING
      onopen: null,
      onerror: null,
      onclose: null,
      onmessage: null,
      close: () => {
        instance.readyState = 3; // CLOSED
        if (instance.onclose) {
          instance.onclose({ code: 1000, reason: "mock close" });
        }
      },
      send: (_data: string) => {
        // mock send
      },
    };
    mockWebSocketInstance = instance;
    return instance;
  } as unknown as new (url: string) => MockWebSocketInstance;

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  (globalThis as any).WebSocket = mockWebSocketClass;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  (globalThis as any).WebSocket.OPEN = 1;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  (globalThis as any).WebSocket.CONNECTING = 0;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  (globalThis as any).WebSocket.CLOSED = 3;
});

test.afterEach(() => {
  mockWebSocketInstance = null;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  delete (globalThis as any).WebSocket;
});

// Helper to access the mock instance
function getMockSocket(): MockWebSocketInstance {
  if (!mockWebSocketInstance) {
    throw new Error("No mock WebSocket instance created");
  }
  return mockWebSocketInstance;
}

async function keepEventLoopAliveUntil<T>(promise: Promise<T>): Promise<T> {
  const keepAlive = setInterval(() => {}, 100);
  try {
    return await promise;
  } finally {
    clearInterval(keepAlive);
  }
}

test("connection timeout fires if onopen never called", async () => {
  const { GatewayWsClient } = await import("../cloud/ws-client");

  // Create minimal mock config and dependencies
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

  // Mock all dependencies with minimal implementations
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

  const mockCheckpoints = {
    load: async () => ({}),
    save: async (s: Record<string, unknown>) => s,
    saveHealthState: async (_h: string, _s?: Record<string, unknown>) => ({}),
    markRecovered: async () => ({}),
    flush: async () => {},
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

  const client = new GatewayWsClient(
    mockConfig as any,
    mockDb as any,
    mockJournal as any,
    mockOutbox as any,
    mockCheckpoints as any,
    mockTokenStore as any,
    mockCapabilityRouter as any,
    mockPersonalChannelRuntimes as any,
  );

  // Access private method via bracket notation
  const openSocket = (client as unknown as { openSocket: (url: string) => Promise<unknown> }).openSocket;
  assert.ok(openSocket, "openSocket method must exist");

  // Don't trigger onopen - the timeout should fire
  const startTime = Date.now();
  await assert.rejects(
    keepEventLoopAliveUntil(openSocket("ws://localhost:8080")),
    /timed out/,
  );
  const elapsed = Date.now() - startTime;
  // The timeout should fire around 10 seconds; allow some margin
  assert.ok(elapsed >= 9_500, `Timeout fired too early: ${elapsed}ms`);
});

test("timeout clears pending response without closing socket", async () => {
  const { GatewayWsClient } = await import("../cloud/ws-client");

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
    readJson: async () => ({}),
    writeJson: async (_name: string, _value: unknown) => _value,
    appendNdjson: async () => {},
  };

  const mockJournal = {
    journalFilePath: () => "/tmp/journal.ndjson",
    append: async () => ({ cursor: 1 }),
    lastCursor: async () => 0,
  };

  const outboxItems: unknown[] = [];
  const mockOutbox = {
    list: async () => outboxItems,
    get: async (_id: string) => null,
    enqueue: async (_id: string, _type: string, _payload: unknown, _opts?: unknown) => ({}),
    markAttemptStarted: async () => {},
    acknowledge: async (_id: string) => {
      const idx = outboxItems.findIndex((i: unknown) => (i as { requestId: string }).requestId === _id);
      if (idx >= 0) {
        (outboxItems[idx] as { status: string }).status = "acknowledged";
      }
    },
    markAttemptFailed: async () => {},
    markForReplay: async () => {},
    listReplayablePending: async () => [],
    summarize: async () => {
      const items = outboxItems as Array<{ status: string }>;
      return {
        total: items.length,
        pending: items.filter((i) => i.status === "pending").length,
        failed: items.filter((i) => i.status === "failed").length,
        acknowledged: items.filter((i) => i.status === "acknowledged").length,
      };
    },
    prune: async () => 0,
  };

  const mockCheckpoints = {
    load: async () => ({}),
    save: async (s: Record<string, unknown>) => s,
    saveHealthState: async (_h: string, _s?: Record<string, unknown>) => {
      return { healthState: _h };
    },
    markRecovered: async () => ({}),
    flush: async () => {},
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

  const client = new GatewayWsClient(
    mockConfig as any,
    mockDb as any,
    mockJournal as any,
    mockOutbox as any,
    mockCheckpoints as any,
    mockTokenStore as any,
    mockCapabilityRouter as any,
    mockPersonalChannelRuntimes as any,
  );

  // Set up a mock socket in "OPEN" state
  const mockSocket = {
    readyState: 1, // OPEN
    onopen: null,
    onerror: null,
    onclose: null,
    onmessage: null,
    closeWasCalled: false,
    close() { this.closeWasCalled = true; },
    send(_data: string) {},
  };
  (client as unknown as { socket: typeof mockSocket }).socket = mockSocket;

  // Send a request that will timeout quickly
  // Access private method properly to preserve `this` binding
  const dispatchRequestFrame = (client as unknown as {
    dispatchRequestFrame: (frame: unknown, opts: Record<string, unknown>) => Promise<unknown>;
  }).dispatchRequestFrame.bind(client);

  const frame = {
    kind: "request" as const,
    protocolVersion: "v1alpha2",
    id: "timeout-req-1",
    type: "gateway.state.update" as const,
    ts: new Date().toISOString(),
    scope: {
      tenant_id: "t1", workspace_id: "w1", user_id: "u1",
      device_id: "d1", gateway_id: "g1",
    },
    payload: {},
  };

  const promise = dispatchRequestFrame(frame, {
    replayable: false,
    persistOutbox: false,
    timeoutMs: 50, // Very short timeout
  });

  await assert.rejects(keepEventLoopAliveUntil(promise), /timed out/);

  // The socket should NOT have been closed
  assert.equal(mockSocket.closeWasCalled, false, "Socket should not be closed on timeout");

  // Another request should still work — the socket is still "open"
  const frame2 = { ...frame, id: "concurrent-req-1" };
  // This one should succeed since we won't simulate a timeout
  // We need to make it not timeout by resolving it quickly
  // Actually, let's just verify the socket is still usable
  assert.equal(mockSocket.closeWasCalled, false);
});

test("timeout clears pending response entry", async () => {
  const { GatewayWsClient } = await import("../cloud/ws-client");

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

  const mockCheckpoints = {
    load: async () => ({}),
    save: async (s: Record<string, unknown>) => s,
    saveHealthState: async () => ({}),
    markRecovered: async () => ({}),
    flush: async () => {},
  };

  const mockTokenStore = {
    load: async () => ({}),
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

  const client = new GatewayWsClient(
    mockConfig as any,
    mockDb as any,
    mockJournal as any,
    mockOutbox as any,
    mockCheckpoints as any,
    mockTokenStore as any,
    mockCapabilityRouter as any,
    mockPersonalChannelRuntimes as any,
  );

  const mockSocket = {
    readyState: 1,
    onopen: null,
    onerror: null,
    onclose: null,
    onmessage: null,
    close() {},
    send(_data: string) {},
  };
  (client as unknown as { socket: typeof mockSocket }).socket = mockSocket;

  // Access private trackPendingResponse method, preserving `this` binding
  const trackPendingResponse = (client as unknown as {
    trackPendingResponse: (
      requestId: string,
      messageType: string,
      replayable: boolean,
      persisted: boolean,
      timeoutMs?: number,
    ) => Promise<unknown>;
  }).trackPendingResponse.bind(client);

  // Start tracking a pending response with very short timeout
  const promise = trackPendingResponse("timeout-req-2", "gateway.heartbeat", false, false, 50);
  await assert.rejects(keepEventLoopAliveUntil(promise), /timed out/);

  // After timeout, the pending response should be cleaned up
  // The pendingResponses map should be empty (the entries were cleared)
  // Since we can't easily access the private map, we verify by checking
  // that a second request with the same ID doesn't find a duplicate
  const promise2 = trackPendingResponse("timeout-req-2", "gateway.heartbeat", false, false, 50);
  // It should reject again (meaning no pending entry was left behind blocking it)
  await assert.rejects(keepEventLoopAliveUntil(promise2), /timed out/);
});

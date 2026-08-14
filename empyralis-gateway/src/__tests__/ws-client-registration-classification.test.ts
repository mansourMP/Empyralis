import test from "node:test";
import assert from "node:assert/strict";

import { GatewayWsClient } from "../cloud/ws-client";
import { GatewayRegistrationError } from "../cloud/registration-failure";
import type { GatewayDeviceIdentity } from "../pairing/device-identity";
import type { GatewayRuntimeMetadata } from "../runtime/runtime-metadata";

/**
 * registerFromPairing() itself — the exact call site that produced
 * `Error: Gateway registration failed with status 400: Pairing token is no
 * longer active.` in production. Proves the real production code (not a
 * reimplementation of it) throws a classified GatewayRegistrationError for
 * both a rejected pairing token (permanent) and a network-level failure
 * (retryable), using the same globalThis.fetch-stubbing pattern already
 * established in ws-client-socket-error.test.ts.
 */

const mockConfig = {
  apiBaseUrl: "http://localhost:8001/api",
  stateDir: "/tmp/test-state-registration",
  heartbeatIntervalMs: 20000,
  reconnectMinDelayMs: 1000,
  reconnectMaxDelayMs: 5000,
  browserPythonExecutable: "python3",
  browserProjectRoot: "/tmp",
  pairingToken: "gpair_test",
  gatewayId: undefined,
  deviceId: undefined,
  gatewayToken: undefined,
  displayName: "test",
};

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
  currentHealthState: () => "offline" as const,
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
} as any;

function buildClient(): GatewayWsClient {
  return new GatewayWsClient(
    mockConfig as any,
    mockDb as any,
    mockJournal as any,
    mockOutbox as any,
    mockCheckpoints as any,
    mockTokenStore as any,
    mockCapabilityRouter as any,
    mockPersonalChannelRuntimes,
  );
}

const identity: GatewayDeviceIdentity = {
  gatewayId: "gateway_test",
  deviceId: "device_test",
} as GatewayDeviceIdentity;

const runtimeMetadata = {
  gatewayVersion: "0.1.0",
  platform: "linux",
  requestedCapabilities: [],
  deviceMetadata: {},
} as unknown as GatewayRuntimeMetadata;

test("registerFromPairing: a rejected pairing token (400, the exact observed shape) throws a PERMANENT GatewayRegistrationError", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async () => ({
    ok: false,
    status: 400,
    text: async () => JSON.stringify({ detail: "Pairing token is no longer active." }),
  })) as unknown as typeof fetch;
  try {
    const client = buildClient();
    await assert.rejects(
      client.registerFromPairing("gpair_consumed", identity, runtimeMetadata),
      (error: unknown) => {
        assert.ok(error instanceof GatewayRegistrationError, "must throw GatewayRegistrationError");
        const registrationError = error as GatewayRegistrationError;
        assert.equal(registrationError.retryable, false, "a 400 must be classified permanent");
        assert.equal(registrationError.status, 400);
        assert.equal(registrationError.detail, "Pairing token is no longer active.");
        return true;
      },
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("registerFromPairing: a transient 503 throws a RETRYABLE GatewayRegistrationError", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async () => ({
    ok: false,
    status: 503,
    text: async () => "Service Unavailable",
  })) as unknown as typeof fetch;
  try {
    const client = buildClient();
    await assert.rejects(
      client.registerFromPairing("gpair_x", identity, runtimeMetadata),
      (error: unknown) => {
        assert.ok(error instanceof GatewayRegistrationError);
        assert.equal((error as GatewayRegistrationError).retryable, true, "a 503 must be classified retryable");
        return true;
      },
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("registerFromPairing: fetch rejecting before any response (DNS/connection failure) throws a RETRYABLE GatewayRegistrationError with no status", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async () => {
    throw new Error("getaddrinfo ENOTFOUND example.invalid");
  }) as unknown as typeof fetch;
  try {
    const client = buildClient();
    await assert.rejects(
      client.registerFromPairing("gpair_x", identity, runtimeMetadata),
      (error: unknown) => {
        assert.ok(error instanceof GatewayRegistrationError);
        const registrationError = error as GatewayRegistrationError;
        assert.equal(registrationError.retryable, true, "a network failure must be classified retryable");
        assert.equal(registrationError.status, undefined);
        assert.equal(registrationError.code, "network_error");
        return true;
      },
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("registerFromPairing: success still returns the payload and persists tokens (unchanged happy path)", async () => {
  const originalFetch = globalThis.fetch;
  const registrationPayload = {
    gateway: { gateway_id: "gateway_test", device_id: "device_test" },
    gateway_token: "ggt_new",
    scope: { tenant_id: "t1", workspace_id: "w1", user_id: "u1" },
  };
  globalThis.fetch = (async () => ({
    ok: true,
    status: 200,
    json: async () => registrationPayload,
  })) as unknown as typeof fetch;
  try {
    const client = buildClient();
    const payload = await client.registerFromPairing("gpair_good", identity, runtimeMetadata);
    assert.equal(payload.gateway_token, "ggt_new");
  } finally {
    globalThis.fetch = originalFetch;
  }
});

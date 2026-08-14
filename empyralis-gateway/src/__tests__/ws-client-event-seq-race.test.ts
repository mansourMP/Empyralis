import test from "node:test";
import assert from "node:assert/strict";

import { decodeFrame } from "../protocol/codec";
import { GatewayCheckpoints } from "../state/checkpoints";
import type { GatewayEventEnvelope } from "../protocol/types";
import type { GatewayRuntimeMetadata } from "../runtime/runtime-metadata";

/**
 * Regression coverage for the concurrent-`publishEvent` seq collision found
 * live on 2026-08-08, on the first Empyralis-provisioned OpenClaw instance.
 *
 * `publishEvent` used to do an unguarded read-modify-write:
 *
 *     const checkpoints = await this.checkpoints.load();   // <-- yields
 *     const nextSeq = (checkpoints.lastClientSeq ?? 0) + 1;
 *     ...
 *     await this.checkpoints.save({ lastClientSeq: nextSeq });
 *     this.socket.send(encoded);
 *
 * It is called once per inbound HTTP POST from the OpenClaw bridge plugin
 * (src/openclaw/inbound-listener.ts), so two channel messages arriving in the
 * same tick genuinely run concurrently: both read the same `lastClientSeq`,
 * both sent `seq: 1`. The cloud's strictly-increasing-seq guard
 * (server_modules/gateway_protocol_service.py, "gateway frame replay
 * detected") answers a repeated seq by CLOSING the socket with code 4408 —
 * and the second message is lost outright, because it had already been
 * written to the socket and so was never enqueued in the outbox, while the
 * bridge plugin's own durable queue had already been 202'd and dropped it.
 *
 * Observed live: journal cursors 209/211, two events 23ms apart, both
 * `seq: 1`, socket closed 4408 25ms later, only the first ever reaching
 * `handle_gateway_channel_inbound`. Two messages in, one delivered, nothing
 * anywhere reporting the loss.
 *
 * The assertions below are about the ONE invariant the cloud enforces: every
 * frame this client writes to the socket carries a strictly greater `seq`
 * than the frame written before it, no matter how the calls interleave.
 */

interface RecordingSocket {
  readyState: number;
  onopen: (() => void) | null;
  onerror: ((event: { message?: string }) => void) | null;
  onclose: ((event: { code: number; reason: string }) => void) | null;
  onmessage: ((event: { data: string }) => void) | null;
  send(data: string): void;
  close(): void;
}

let lastSocket: RecordingSocket | null = null;
const sentEventFrames: GatewayEventEnvelope[] = [];

function buildRecordingWebSocketClass() {
  class RecordingWebSocket implements RecordingSocket {
    static readonly CONNECTING = 0;
    static readonly OPEN = 1;
    static readonly CLOSING = 2;
    static readonly CLOSED = 3;

    readyState: number = RecordingWebSocket.CONNECTING;
    onopen: (() => void) | null = null;
    onerror: ((event: { message?: string }) => void) | null = null;
    onclose: ((event: { code: number; reason: string }) => void) | null = null;
    onmessage: ((event: { data: string }) => void) | null = null;

    constructor(_url: string, _protocols?: string[]) {
      lastSocket = this;
      setTimeout(() => {
        this.readyState = RecordingWebSocket.OPEN;
        this.onopen?.();
      }, 0);
    }

    send(data: string): void {
      const decoded = decodeFrame(data);
      if (!decoded.ok) return;
      if (decoded.frame.kind === "event") {
        sentEventFrames.push(decoded.frame as GatewayEventEnvelope);
        return;
      }
      if (decoded.frame.kind !== "request") return;
      const request = decoded.frame;
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
      this.readyState = RecordingWebSocket.CLOSED;
      this.onclose?.({ code: 1000, reason: "test close" });
    }
  }
  return RecordingWebSocket;
}

/**
 * The REAL GatewayCheckpoints over an in-memory db.
 *
 * Deliberately not a stand-in. A hand-written stub whose `save()` takes effect
 * immediately makes this test pass against code that is still broken in
 * production, because half of the bug lives inside GatewayCheckpoints itself:
 * `save()` is debounced by 100ms, so `load()` inside that window returns the
 * value from BEFORE the write, and a perfectly serialized second caller still
 * reads the same number back. That is CLAUDE.md's "a mock protects a seam, not
 * a path" — measured here: with a stub that writes through, the pre-fix client
 * fails on the read-modify-write race alone and then passes again as soon as
 * the race is closed, while the live gateway kept emitting duplicate seqs.
 */
function buildRealCheckpoints() {
  const files = new Map<string, unknown>();
  const db = {
    ensureReady: async () => {},
    filePath: (name: string) => "/tmp/" + name,
    rootDirPath: () => "/tmp",
    readJson: async <T,>(name: string, fallback: T) => (files.has(name) ? (files.get(name) as T) : fallback),
    writeJson: async (name: string, value: unknown) => {
      files.set(name, value);
      return value;
    },
    appendNdjson: async () => {},
  };
  return new GatewayCheckpoints(db as any);
}

async function buildClient() {
  lastSocket = null;
  sentEventFrames.length = 0;
  const { GatewayWsClient } = await import("../cloud/ws-client");
  const RecordingWebSocket = buildRecordingWebSocketClass();

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
    heartbeat_interval_seconds: 999_999,
    scope,
    gateway: {},
    expires_at: new Date(Date.now() + 3_600_000).toISOString(),
  };

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
    readJson: async <T,>(_name: string, fallback: T) => fallback,
    writeJson: async (_name: string, value: unknown) => value,
    appendNdjson: async () => {},
  };
  const mockJournal = {
    journalFilePath: () => "/tmp/journal.ndjson",
    append: async () => ({ cursor: 0 }),
    lastCursor: async () => 0,
  };
  const outboxEnqueued: string[] = [];
  const mockOutbox = {
    list: async () => [],
    get: async () => null,
    enqueue: async (requestId: string) => {
      outboxEnqueued.push(requestId);
      return {};
    },
    markAttemptStarted: async () => {},
    acknowledge: async () => {},
    markAttemptFailed: async () => {},
    markForReplay: async () => {},
    markUncertain: async () => {},
    listReplayablePending: async () => [],
    summarize: async () => ({ total: 0, pending: 0, failed: 0, acknowledged: 0, uncertain: 0 }),
    prune: async () => 0,
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

  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async () => ({
    ok: true,
    status: 200,
    json: async () => sessionPayload,
    text: async () => JSON.stringify(sessionPayload),
  })) as unknown as typeof fetch;

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

  const client = new GatewayWsClient(
    mockConfig as any,
    mockDb as any,
    mockJournal as any,
    mockOutbox as any,
    buildRealCheckpoints() as any,
    mockTokenStore as any,
    mockCapabilityRouter as any,
    mockPersonalChannelRuntimes as any,
    RecordingWebSocket as any,
  );
  await client.connect({ gatewayId: "gw-1", deviceId: "dev-1" } as any, runtimeMetadata);
  return {
    client,
    scope,
    outboxEnqueued,
    restoreFetch: () => {
      globalThis.fetch = originalFetch;
    },
  };
}

function inboundPayload(externalMessageId: string) {
  return {
    channel_key: "openclaw_line",
    provider: "openclaw",
    message: {
      external_message_id: externalMessageId,
      remote_jid: "conversation-1",
      sender_jid: "sender-1",
      text: "hello",
      from_me: false,
      received_at: new Date().toISOString(),
    },
  };
}

test("concurrent publishEvent calls never write the same seq twice", async () => {
  const harness = await buildClient();
  try {
    // Fired without awaiting the first — exactly how two bridge-plugin POSTs
    // land on OpenClawInboundListener within the same tick.
    await Promise.all([
      harness.client.publishEvent("channel.inbound", inboundPayload("msg-1") as any),
      harness.client.publishEvent("channel.inbound", inboundPayload("msg-2") as any),
      harness.client.publishEvent("channel.inbound", inboundPayload("msg-3") as any),
    ]);

    assert.equal(
      sentEventFrames.length,
      3,
      "every published event must reach the socket; a dropped one is a lost customer message",
    );
    assert.deepEqual(
      harness.outboxEnqueued,
      [],
      "the socket was open, so nothing should have been diverted to the outbox",
    );

    const seqs = sentEventFrames.map((frame) => frame.seq);
    assert.equal(new Set(seqs).size, seqs.length, `duplicate seq written to the socket: ${seqs.join(", ")}`);
    for (let index = 1; index < seqs.length; index += 1) {
      assert.ok(
        Number(seqs[index]) > Number(seqs[index - 1]),
        `seq must be strictly increasing in WRITE order (the cloud closes the socket with 4408 otherwise); got ${seqs.join(", ")}`,
      );
    }

    // The three messages must all still be there, and in the order they were
    // handed over — a lock that reorders would satisfy the seq rule while
    // scrambling a conversation.
    assert.deepEqual(
      sentEventFrames.map((frame) => (frame.payload as any).message.external_message_id),
      ["msg-1", "msg-2", "msg-3"],
    );

    // connect() starts HeartbeatLoop with a non-unref'd setTimeout (correct
    // for a real long-lived gateway process -- production wants this timer
    // to hold the process open). It is only ever cleared by disconnect(),
    // same as every other ws-client-*.test.ts harness that calls connect()
    // (see ws-client-socket-error.test.ts). Skipping this teardown doesn't
    // fail an assertion -- it leaves the process holding a real, pending
    // Timeout with nothing left to observe it, so `node --test` never
    // decides the run is over and hangs forever after the checkmark prints.
    await harness.client.disconnect(harness.scope as any);
  } finally {
    harness.restoreFetch();
  }
});

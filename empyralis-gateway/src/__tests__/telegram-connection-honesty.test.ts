import { mkdtemp, rm } from "fs/promises";
import { tmpdir } from "os";
import path from "path";
import test, { mock } from "node:test";
import assert from "node:assert/strict";

import { TelegramPersonalRuntime, TELEGRAM_HEALTH_CHECK_TIMEOUT_MS } from "../channels/telegram/runtime";
import { GatewayStateDb } from "../state/db";

// ===========================================================================
// OUTCOME-HONESTY regression coverage.
//
// Observed live, 2026-08-14, on a real Agent Computer: the gateway journal
// showed GramJS's own `_updateLoop` (node_modules/telegram/client/updates.js)
// repeatedly throwing `Error: TIMEOUT` from its internal ping keepalive,
// forever, on a ~40s cycle (9s sleep + 3 ping attempts x ~10s each) — the
// live connection was dead. The product still showed "Connected".
//
// Root cause, traced through the vendored GramJS source: `client.
// checkAuthorized()` (this runtime's own honesty probe, built for a DIFFERENT
// failure mode — a revoked auth key) is `await client.getMe()` with NO
// timeout of its own. GramJS's `send()` (network/MTProtoSender.js) returns a
// promise that resolves ONLY when a real response arrives — there is no
// built-in ceiling anywhere in GramJS's invoke()/send() path (confirmed by
// the fact _updateLoop has to wrap its OWN ping in an ad hoc Promise.race
// timeout to avoid hanging forever). So the exact failure mode this runtime's
// own health check exists to catch (a connection that queues sends but never
// gets a response) could make the health check itself hang forever,
// silently, leaving "connected" persisted with no error anywhere. These
// tests prove the fix: withTimeout() bounds the probe, and a bounded probe
// failure is treated exactly like any other live-connection death — the
// EXISTING handleConnectionFailure/reconnect machinery downgrades the
// persisted status honestly, the same mechanism already proven for a
// revoked auth key.
// ===========================================================================

/** A real macrotask tick (not just a microtask) — flushes every pending
 *  microtask AND lets any already-scheduled setTimeout(0)-shaped continuation
 *  run, which a bare chain of `await Promise.resolve()` calls doesn't
 *  reliably do once a resolved inner promise has to propagate back up
 *  through an async function's own await points plus a `.finally()`. */
const flush = async (times = 3): Promise<void> => {
  for (let i = 0; i < times; i += 1) {
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
};

function buildMockClient(overrides: Record<string, unknown> = {}) {
  let connectionIssueHandler: ((error: unknown) => void) | undefined;
  const checkAuthorizedCalls: number[] = [];
  const client = {
    setMessageHandler: (_handler: unknown) => undefined,
    sendMessage: async (remoteJid: string, text: string) => ({ externalMessageId: "out-1", remoteJid }),
    disconnect: async () => undefined,
    exportSessionString: () => undefined,
    checkAuthorized: async () => {
      checkAuthorizedCalls.push(Date.now());
      // Never resolves — models the exact live symptom: the connection
      // accepts the RPC but no response ever arrives.
      await new Promise(() => undefined);
    },
    setConnectionIssueHandler: (handler: (error: unknown) => void) => {
      connectionIssueHandler = handler;
    },
    ...overrides,
  };
  return {
    client,
    checkAuthorizedCalls,
    fireConnectionIssue: (error: unknown) => connectionIssueHandler?.(error),
    hasConnectionIssueHandler: () => Boolean(connectionIssueHandler),
  };
}

/** Mirrors telegram-self-chat.test.ts's identical helper: gets a runtime
 *  past preflight and through a real connectClientInternal() pass. */
async function primeAndConnect(runtime: TelegramPersonalRuntime): Promise<void> {
  const anyRuntime = runtime as any;
  await anyRuntime.configStore.patchTelegramConfig({ apiId: 123456, apiHash: "test-hash" });
  await anyRuntime.sessionStore.saveSessionString("mock-session-string");
  await anyRuntime.connectClientInternal();
}

test(
  "runHealthCheck: a checkAuthorized() call that never resolves is treated as a failure once " +
    "TELEGRAM_HEALTH_CHECK_TIMEOUT_MS elapses, and the persisted status is honestly downgraded " +
    "away from 'connected' instead of hanging forever",
  async () => {
    const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-telegram-honesty-"));
    try {
      const { client } = buildMockClient();
      const adapter = { connect: async () => ({ client, account: { userId: "u1", username: "owner" } }) };
      const runtime = new TelegramPersonalRuntime(new GatewayStateDb(rootDir), { adapter: adapter as any });
      runtime.setPublisher({ publishEvent: async () => undefined, publishStateUpdate: async () => undefined });

      await primeAndConnect(runtime);
      const connectedState = await (runtime as any).sessionStore.load();
      assert.equal(connectedState.status, "connected", "test precondition: connect must have succeeded first");

      // Enable fake timers only now — connectClientInternal already
      // scheduled its own real (unref'd) 3-minute periodic timer above;
      // faking that too would just be extra surface this test doesn't need.
      mock.timers.enable({ apis: ["setTimeout"] });
      try {
        const healthCheckPromise = (runtime as any).runHealthCheck();
        mock.timers.tick(TELEGRAM_HEALTH_CHECK_TIMEOUT_MS);
        await healthCheckPromise;
      } finally {
        mock.timers.reset();
      }

      const afterState = await (runtime as any).sessionStore.load();
      assert.notEqual(
        afterState.status,
        "connected",
        "a health probe that can never succeed must not leave the persisted status as 'connected' forever",
      );
      assert.equal(afterState.retryable, true, "a generic timeout is reconnectable, same as any other transient drop");

      const snapshot = await runtime.getHealthSnapshot();
      assert.equal(snapshot.connected, false);
      assert.deepEqual(snapshot.issues, ["telegram_personal_not_connected"]);
    } finally {
      await rm(rootDir, { recursive: true, force: true }).catch(() => undefined);
    }
  },
);

test(
  "handleLiveConnectionIssue: GramJS's own client.onError hook (wired via setConnectionIssueHandler) " +
    "triggers an immediate health probe instead of waiting for the next scheduled one",
  async () => {
    const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-telegram-honesty-"));
    try {
      let authorizedCalls = 0;
      const { client, fireConnectionIssue, hasConnectionIssueHandler } = buildMockClient({
        // This time checkAuthorized actually succeeds — the point of this
        // test is proving the PROBE FIRES on demand, not that it fails.
        checkAuthorized: async () => {
          authorizedCalls += 1;
        },
      });
      const adapter = { connect: async () => ({ client, account: { userId: "u1", username: "owner" } }) };
      const runtime = new TelegramPersonalRuntime(new GatewayStateDb(rootDir), { adapter: adapter as any });
      runtime.setPublisher({ publishEvent: async () => undefined, publishStateUpdate: async () => undefined });

      await primeAndConnect(runtime);
      assert.ok(
        hasConnectionIssueHandler(),
        "connectClientInternal must register a connection-issue handler on every successful connect",
      );
      assert.equal(authorizedCalls, 0, "no probe should have run yet — only the scheduled 3-minute timer exists so far");

      fireConnectionIssue(new Error("TIMEOUT"));
      // handleLiveConnectionIssue kicks off runHealthCheck() but doesn't
      // await it (it's a fire-and-forget signal, like a real GramJS
      // onError callback firing asynchronously) — flush pending tasks.
      await flush();

      assert.equal(
        authorizedCalls,
        1,
        "a live connection-level error must trigger an accelerated probe immediately, not after up to 3 minutes",
      );

      const state = await (runtime as any).sessionStore.load();
      assert.equal(state.status, "connected", "a probe that succeeds must leave the honest 'connected' status alone");
    } finally {
      await rm(rootDir, { recursive: true, force: true }).catch(() => undefined);
    }
  },
);

test(
  "handleLiveConnectionIssue: repeated onError signals while a probe is already in flight do not queue " +
    "up overlapping checkAuthorized() calls",
  async () => {
    const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-telegram-honesty-"));
    try {
      let authorizedCalls = 0;
      let resolveProbe: (() => void) | undefined;
      const { client, fireConnectionIssue } = buildMockClient({
        checkAuthorized: async () => {
          authorizedCalls += 1;
          await new Promise<void>((resolve) => {
            resolveProbe = resolve;
          });
        },
      });
      const adapter = { connect: async () => ({ client, account: { userId: "u1", username: "owner" } }) };
      const runtime = new TelegramPersonalRuntime(new GatewayStateDb(rootDir), { adapter: adapter as any });
      runtime.setPublisher({ publishEvent: async () => undefined, publishStateUpdate: async () => undefined });
      await primeAndConnect(runtime);

      // GramJS reports the same underlying failure on every ~9s ping tick
      // while it's failing to reconnect — three signals in a row, exactly
      // like a real dead connection would produce, before the first probe
      // has even resolved.
      fireConnectionIssue(new Error("TIMEOUT"));
      fireConnectionIssue(new Error("TIMEOUT"));
      fireConnectionIssue(new Error("TIMEOUT"));
      await flush();

      assert.equal(authorizedCalls, 1, "overlapping onError signals must share ONE in-flight probe, not fire one each");

      resolveProbe?.();
      await flush();

      // Once the in-flight probe has settled, a NEW signal is free to start
      // a fresh one — the dedupe must not wedge the mechanism shut forever.
      fireConnectionIssue(new Error("TIMEOUT"));
      await flush();
      assert.equal(authorizedCalls, 2, "a signal after the prior probe settled must be free to start a new one");
      resolveProbe?.();
    } finally {
      await rm(rootDir, { recursive: true, force: true }).catch(() => undefined);
    }
  },
);

test(
  "the real, installed GramJS package genuinely exposes the public client.onError setter this fix relies on",
  async () => {
    // Exercises the actual dependency (not just a hand-rolled stub), the
    // same discipline telegram-logger-guard.test.ts already uses for
    // ensureGramLoggerShape — proving the assumption getAdapter()'s
    // setConnectionIssueHandler depends on (TelegramBaseClient's `set
    // onError`, node_modules/telegram/client/telegramBaseClient.js) is real
    // today, against the real installed version, not merely asserted.
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const telegramPkg = require("telegram") as {
      TelegramClient?: new (...args: unknown[]) => any;
      sessions?: unknown;
    };
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const { StringSession } = require("telegram/sessions/StringSession.js") as { StringSession: new (v: string) => any };
    const TelegramClient = telegramPkg.TelegramClient;
    assert.equal(typeof TelegramClient, "function", "test precondition: the telegram package must export TelegramClient");

    // Constructing a client does no network I/O — connect()/start() are
    // never called, so this never touches Telegram's servers or needs real
    // credentials, matching this task's hard constraint against ever using
    // real Telegram credentials.
    const client = new TelegramClient!(new StringSession(""), 12345, "fake-hash-for-shape-check-only", {
      connectionRetries: 1,
    });
    // `onError` is declared on TelegramBaseClient.prototype, one level up
    // from TelegramClient.prototype (TelegramClient extends it) — walk the
    // chain rather than checking only the immediate prototype.
    let onErrorSetter: unknown;
    for (let proto = Object.getPrototypeOf(client); proto && !onErrorSetter; proto = Object.getPrototypeOf(proto)) {
      onErrorSetter = Object.getOwnPropertyDescriptor(proto, "onError")?.set;
    }
    assert.equal(typeof onErrorSetter, "function", "test precondition: TelegramBaseClient must still declare a public onError setter");

    let received: unknown;
    client.onError = async (error: unknown) => {
      received = error;
    };
    assert.equal(
      typeof client._errorHandler,
      "function",
      "setting onError must populate the internal _errorHandler GramJS's own loops call",
    );

    // This is the literal call GramJS's _updateLoop makes on a ping timeout
    // (node_modules/telegram/client/updates.js) and MTProtoSender's
    // send/recv loops make on their own failures — proving OUR handler is
    // what actually runs when GramJS internals fail.
    const probeError = new Error("TIMEOUT");
    await client._errorHandler(probeError);
    assert.equal(received, probeError, "the error GramJS's internals report must reach the handler wired via onError");
  },
);

import test from "node:test";
import assert from "node:assert/strict";
import { ensureGramLoggerShape } from "../channels/telegram/runtime";

// Regression coverage for the prod crash:
//   TypeError: client._log.canSend is not a function
//   at node_modules/telegram/client/updates.js:205
//
// GramJS's update loop (and several other internal call sites — see
// node_modules/telegram/client/{updates,downloads,users,auth,messageParse,
// dialogs}.js and node_modules/telegram/events/{NewMessage,Album}.js) call
// client._log.canSend/.warn/.info/.debug/.error unconditionally, and
// client.setLogLevel() calls client._log.setLevel. If any of those six are
// missing, GramJS throws and nothing upstream catches it — the whole
// gateway process dies. ensureGramLoggerShape() is the guard that runs
// immediately after every `new TelegramClient(...)` to make that
// impossible, regardless of whether the "hand GramJS a real Logger"
// best-effort attempt (newGramLogger()) actually produced a working one.

const REQUIRED_METHODS = ["canSend", "warn", "info", "debug", "error", "setLevel"] as const;

function assertFullyWorkingLogger(log: unknown): asserts log is Record<string, (...args: unknown[]) => unknown> {
  assert.ok(log && typeof log === "object", "_log must be an object");
  for (const method of REQUIRED_METHODS) {
    assert.equal(typeof (log as Record<string, unknown>)[method], "function", `${method} must be a function`);
  }
  // This is the exact call GramJS's update loop makes at updates.js:205 —
  // proving it is callable without throwing is the core guarantee.
  assert.doesNotThrow(() => (log as Record<string, (...args: unknown[]) => unknown>).canSend("error"));
  for (const method of REQUIRED_METHODS) {
    assert.doesNotThrow(
      () => (log as Record<string, (...args: unknown[]) => unknown>)[method]("probe"),
      `${method}(...) must not throw`,
    );
  }
}

test("ensureGramLoggerShape: a client with no _log at all gets a fully working logger installed", () => {
  const client: { _log?: unknown } = {};
  ensureGramLoggerShape(client, () => undefined);
  assertFullyWorkingLogger(client._log);
});

test("ensureGramLoggerShape: a client whose _log lacks canSend (the original bug) is repaired", () => {
  // Mirrors the exact original bug described in runtime.ts: passing the
  // gateway's own application logger (info/warn/debug/error, like pino) as
  // baseLogger — it has no GramJS-specific canSend/setLevel.
  const appLoggerShape = {
    info: () => undefined,
    warn: () => undefined,
    debug: () => undefined,
    error: () => undefined,
  };
  const client: { _log?: unknown } = { _log: appLoggerShape };
  ensureGramLoggerShape(client, () => undefined);
  assert.notEqual(client._log, appLoggerShape, "the broken logger must be replaced, not left in place");
  assertFullyWorkingLogger(client._log);
});

test("ensureGramLoggerShape: canSend present but not callable (e.g. clobbered to a non-function) is repaired", () => {
  const client: { _log?: unknown } = {
    _log: { canSend: "not-a-function", warn() {}, info() {}, debug() {}, error() {}, setLevel() {} },
  };
  ensureGramLoggerShape(client, () => undefined);
  assertFullyWorkingLogger(client._log);
});

test("ensureGramLoggerShape: prefers a freshly rebuilt working logger over the shim when the rebuild succeeds", () => {
  const rebuiltWorkingLogger = {
    canSend: () => true,
    warn: () => undefined,
    info: () => undefined,
    debug: () => undefined,
    error: () => undefined,
    setLevel: () => undefined,
  };
  const client: { _log?: unknown } = { _log: undefined };
  ensureGramLoggerShape(client, () => rebuiltWorkingLogger);
  assert.equal(client._log, rebuiltWorkingLogger, "must adopt the rebuilt logger rather than falling through to the shim");
});

test("ensureGramLoggerShape: falls back to the shim when the rebuild attempt ALSO fails", () => {
  // This is exactly the failure mode that could defeat the previous fix
  // silently: typeof telegram.Logger !== "function" (version drift, a
  // different module resolution, a future GramJS reshaping the export), so
  // newGramLogger() returns undefined.
  const clientA: { _log?: unknown } = { _log: undefined };
  ensureGramLoggerShape(clientA, () => undefined);
  assertFullyWorkingLogger(clientA._log);

  // Even a rebuild that returns *something* object-shaped but still
  // incomplete (e.g. an incompatible Logger from a different GramJS build)
  // must not be trusted blindly.
  const clientB: { _log?: unknown } = { _log: undefined };
  ensureGramLoggerShape(clientB, () => ({ debug: () => undefined }));
  assertFullyWorkingLogger(clientB._log);
});

test("ensureGramLoggerShape: leaves an already-working logger untouched and never calls buildGramLogger", () => {
  const workingLogger = {
    canSend: () => true,
    warn: () => undefined,
    info: () => undefined,
    debug: () => undefined,
    error: () => undefined,
    setLevel: () => undefined,
  };
  const client: { _log?: unknown } = { _log: workingLogger };
  ensureGramLoggerShape(client, () => {
    throw new Error("buildGramLogger must not be invoked when the existing logger already works");
  });
  assert.equal(client._log, workingLogger, "a healthy logger must not be replaced");
});

test("ensureGramLoggerShape: shim's canSend returns true so real errors stay visible instead of being swallowed", () => {
  const client: { _log?: unknown } = {};
  ensureGramLoggerShape(client, () => undefined);
  const log = client._log as Record<string, (...args: unknown[]) => unknown>;
  assert.equal(log.canSend("error"), true);
});

test("ensureGramLoggerShape: integrates with the real GramJS Logger class from the installed telegram package", () => {
  // Exercises the actual dependency (not just a hand-rolled stub) to prove
  // the guard cooperates with a genuine GramJS Logger when one is
  // available — this is what newGramLogger() in runtime.ts hands in as
  // buildGramLogger when `telegram.Logger` resolves correctly.
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  const telegramPkg = require("telegram") as { Logger?: new () => unknown };
  const RealLogger = telegramPkg.Logger;
  assert.equal(typeof RealLogger, "function", "test precondition: the telegram package must export a real Logger class");

  const client: { _log?: unknown } = {};
  ensureGramLoggerShape(client, () => (typeof RealLogger === "function" ? new RealLogger() : undefined));
  assert.ok(client._log instanceof (RealLogger as new () => unknown), "should install a genuine GramJS Logger instance");
  assertFullyWorkingLogger(client._log);

  // This is the literal call GramJS's update loop makes today
  // (node_modules/telegram/client/updates.js:205 —
  // `client._log.canSend(Logger_1.LogLevel.ERROR)`, and LogLevel.ERROR is
  // the string "error" per node_modules/telegram/extensions/Logger.js),
  // against the real, installed dependency version.
  assert.doesNotThrow(() => (client._log as { canSend: (level: string) => boolean }).canSend("error"));
});

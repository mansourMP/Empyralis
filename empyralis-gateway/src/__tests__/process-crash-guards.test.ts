import test from "node:test";
import assert from "node:assert/strict";

import { isFatalProcessError, handleProcessCrashCondition, installProcessCrashGuards } from "../index";

/**
 * Regression coverage for reliability-audit-1's process-level crash safety
 * fix: before this, there was no process.on("uncaughtException"/
 * "unhandledRejection") handler anywhere in gateway production code, so
 * any error that slipped past a local mitigation (e.g. a transient
 * WebSocket error) crashed the whole process with no supervisor on most
 * boxes to bring it back. installProcessCrashGuards() (src/index.ts) must
 * log-and-continue for ordinary/transient errors, and only exit for the
 * narrow, explicit set of stack/heap-corruption signatures where
 * continuing is genuinely unsafe.
 *
 * These tests deliberately never emit a real process "uncaughtException"/
 * "unhandledRejection" event: node:test installs its own listeners for
 * exactly those events (to detect a test crashing the whole run), and a
 * manual process.emit(...) collides with that — the runner's listener
 * runs first and re-throws before a user listener added later even gets a
 * chance to run, which makes the emitted error appear to "escape" the test
 * regardless of what our own handler does. handleProcessCrashCondition()
 * is exported from src/index.ts specifically so this decision logic is
 * unit-testable directly, without going anywhere near the real event.
 */

test("isFatalProcessError classifies stack/heap corruption signatures as fatal", () => {
  assert.equal(isFatalProcessError(new RangeError("Maximum call stack size exceeded")), true);
  assert.equal(
    isFatalProcessError(new Error("FATAL ERROR: Reached heap limit Allocation failed - JavaScript heap out of memory")),
    true,
  );
  assert.equal(isFatalProcessError(new Error("Array buffer allocation failed")), true);
});

test("isFatalProcessError treats ordinary/transient errors as non-fatal", () => {
  // These are exactly the shapes of the two already-documented,
  // individually-patched live crashes this file's guards now backstop
  // (ws-client.ts openSocket() ~554-563 and dispatchRequestFrame() ~700-711).
  assert.equal(isFatalProcessError(new Error("WebSocket connection failed for ws://example")), false);
  assert.equal(isFatalProcessError(new TypeError("Cannot read properties of null (reading 'send')")), false);
  assert.equal(isFatalProcessError(new Error("connect ECONNRESET")), false);
  assert.equal(isFatalProcessError("a plain string rejection reason"), false);
  assert.equal(isFatalProcessError(undefined), false);
});

function withStubbedExitAndLogging<T>(run: (exitCalls: Array<number | undefined>) => T): T {
  const originalExit = process.exit;
  const originalConsoleError = console.error;
  // The fatal path under test does `process.exitCode = 1;` *before* calling
  // process.exit() — stubbing only .exit() isn't enough, since that plain
  // property assignment happens for real either way. Left unrestored, it
  // leaks past this test and makes the whole test *file* exit non-zero at
  // the end of the run, which node:test reports as the file itself having
  // failed even though every individual test passed.
  const originalExitCode = process.exitCode;
  const exitCalls: Array<number | undefined> = [];
  process.exit = ((code?: number) => {
    exitCalls.push(code);
    return undefined as never;
  }) as typeof process.exit;
  console.error = () => {};
  try {
    return run(exitCalls);
  } finally {
    console.error = originalConsoleError;
    process.exit = originalExit;
    process.exitCode = originalExitCode;
  }
}

test("handleProcessCrashCondition keeps the process alive (never exits) for a non-fatal uncaughtException", () => {
  withStubbedExitAndLogging((exitCalls) => {
    assert.doesNotThrow(() => {
      handleProcessCrashCondition("uncaughtException", new Error("transient WebSocket hiccup"));
    });
    assert.deepEqual(exitCalls, [], "a non-fatal uncaughtException must not call process.exit");
  });
});

test("handleProcessCrashCondition never exits for any unhandledRejection, fatal-looking message or not", () => {
  withStubbedExitAndLogging((exitCalls) => {
    handleProcessCrashCondition("unhandledRejection", new Error("orphaned promise rejection"));
    handleProcessCrashCondition("unhandledRejection", new RangeError("Maximum call stack size exceeded"));
    assert.deepEqual(exitCalls, [], "unhandledRejection is never treated as fatal, regardless of message shape");
  });
});

test("handleProcessCrashCondition exits(1) for a fatal stack/heap corruption uncaughtException", () => {
  withStubbedExitAndLogging((exitCalls) => {
    handleProcessCrashCondition("uncaughtException", new RangeError("Maximum call stack size exceeded"));
    assert.deepEqual(exitCalls, [1], "a stack/heap corruption signature must exit(1) for supervisor restart");
  });
});

test("handleProcessCrashCondition never throws, even for a non-Error rejection reason", () => {
  withStubbedExitAndLogging((exitCalls) => {
    assert.doesNotThrow(() => {
      handleProcessCrashCondition("unhandledRejection", "a plain string rejection reason");
      handleProcessCrashCondition("unhandledRejection", undefined);
    });
    assert.deepEqual(exitCalls, []);
  });
});

test("installProcessCrashGuards registers exactly one uncaughtException and one unhandledRejection listener", () => {
  // Registration-only check — deliberately does not emit either event (see
  // the file-level doc comment above for why). No matching uninstall
  // function exists, so remove exactly the listener(s) this call added
  // afterward and leave anything node:test or another test file already
  // had untouched.
  const beforeUncaught = process.listeners("uncaughtException");
  const beforeRejection = process.listeners("unhandledRejection");

  installProcessCrashGuards();

  const newUncaughtListeners = process
    .listeners("uncaughtException")
    .filter((listener) => !beforeUncaught.includes(listener));
  const newRejectionListeners = process
    .listeners("unhandledRejection")
    .filter((listener) => !beforeRejection.includes(listener));

  try {
    assert.equal(newUncaughtListeners.length, 1, "installProcessCrashGuards should add exactly one uncaughtException listener");
    assert.equal(newRejectionListeners.length, 1, "installProcessCrashGuards should add exactly one unhandledRejection listener");
  } finally {
    newUncaughtListeners.forEach((listener) => process.off("uncaughtException", listener as NodeJS.UncaughtExceptionListener));
    newRejectionListeners.forEach((listener) => process.off("unhandledRejection", listener as NodeJS.UnhandledRejectionListener));
  }
});

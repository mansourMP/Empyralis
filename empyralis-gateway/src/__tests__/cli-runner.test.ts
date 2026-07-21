import test from "node:test";
import assert from "node:assert/strict";
import { EventEmitter } from "node:events";

import {
  runCliSubscription,
  CliRunError,
  type CliChildProcessLike,
  type CliRunParams,
  type CliSpawnImpl,
} from "../llm/cli-runner";

interface FakeChild {
  child: CliChildProcessLike;
  emitStdout: (chunk: string) => void;
  emitStderr: (chunk: string) => void;
  emitClose: (code: number | null, signal: NodeJS.Signals | null) => void;
  emitError: (err: NodeJS.ErrnoException) => void;
  killCalls: (NodeJS.Signals | undefined)[];
  /** When set, kill() schedules an async 'close' — simulating a real process
   *  that actually terminates in response to the signal. */
  dieOnKill?: { code: number | null; signal: NodeJS.Signals };
}

function makeFakeChild(): FakeChild {
  const stdout = new EventEmitter();
  const stderr = new EventEmitter();
  const proc = new EventEmitter();
  const killCalls: (NodeJS.Signals | undefined)[] = [];
  const fake: FakeChild = {
    child: {
      stdout: stdout as unknown as CliChildProcessLike["stdout"],
      stderr: stderr as unknown as CliChildProcessLike["stderr"],
      on: (event: string, listener: (...args: unknown[]) => void) => proc.on(event, listener),
      kill: (signal?: NodeJS.Signals) => {
        killCalls.push(signal);
        // Only "dies" when sent the SPECIFIC signal configured — this is what
        // lets the timeout tests distinguish "ignored SIGTERM, needed SIGKILL"
        // from "SIGTERM alone was enough" instead of always dying on the first
        // kill() call regardless of which signal was actually sent.
        if (fake.dieOnKill && signal === fake.dieOnKill.signal) {
          setImmediate(() => proc.emit("close", fake.dieOnKill!.code, fake.dieOnKill!.signal));
        }
        return true;
      },
    } as unknown as CliChildProcessLike,
    emitStdout: (chunk) => stdout.emit("data", chunk),
    emitStderr: (chunk) => stderr.emit("data", chunk),
    emitClose: (code, signal) => proc.emit("close", code, signal),
    emitError: (err) => proc.emit("error", err),
    killCalls,
  };
  return fake;
}

function baseParams(overrides: Partial<CliRunParams> = {}): CliRunParams {
  return {
    runtime: "claude_code",
    prompt: "hi",
    timeoutMs: 5_000,
    ...overrides,
  };
}

function spawnImplReturning(fake: FakeChild): CliSpawnImpl {
  return () => fake.child;
}

/** Zero-delay stand-in for CliRunnerConfig.delayImpl so retry/backoff/
 *  recovery tests run instantly instead of actually sleeping through real
 *  backoff windows (500ms-4000ms per the module's own schedule). */
async function noDelay(): Promise<void> {
  // intentionally empty — resolves immediately
}

function emitCrash(fake: FakeChild, message: string, exitCode = 1): void {
  fake.emitStderr(`${message}\n`);
  fake.emitClose(exitCode, null);
}

/** Drives a MULTI-attempt runCliSubscription call: each retry/recovery
 *  re-spawn gets its own fresh fake child (exactly like a real re-spawn
 *  would), and each attempt's script runs once that attempt's spawnImpl is
 *  actually invoked — via queueMicrotask, which is guaranteed to run only
 *  after the synchronous spawnAndCollect() call that attaches this attempt's
 *  stdout/stderr listeners has finished, so the script can never race ahead
 *  of listener attachment. Throws if runCliSubscription ever calls spawnImpl
 *  more times than scripts were provided, so a test asserting N attempts
 *  fails loudly instead of hanging if the module tries an (N+1)th. */
function scriptedSpawnImpl(scripts: Array<(fake: FakeChild) => void>): { spawnImpl: CliSpawnImpl; callCount: () => number } {
  let index = 0;
  const spawnImpl: CliSpawnImpl = () => {
    const script = scripts[index];
    if (!script) {
      throw new Error(`scriptedSpawnImpl: unexpected spawn attempt #${index + 1} (only ${scripts.length} scripted)`);
    }
    const fake = makeFakeChild();
    index += 1;
    queueMicrotask(() => script(fake));
    return fake.child;
  };
  return { spawnImpl, callCount: () => index };
}

// ---- Happy path -------------------------------------------------------

test("claude_code: parses the stream-json result event into {text, usage}", async () => {
  const fake = makeFakeChild();
  const promise = runCliSubscription(baseParams({ runtime: "claude_code" }), {
    spawnImpl: spawnImplReturning(fake),
  });
  fake.emitStdout(`${JSON.stringify({ type: "system", subtype: "init" })}\n`);
  fake.emitStdout(
    `${JSON.stringify({
      type: "assistant",
      message: { content: [{ type: "text", text: "hello there" }] },
    })}\n`,
  );
  fake.emitStdout(
    `${JSON.stringify({
      type: "result",
      subtype: "success",
      is_error: false,
      result: "hello there",
      usage: { input_tokens: 42, output_tokens: 8 },
    })}\n`,
  );
  fake.emitClose(0, null);
  const result = await promise;
  assert.equal(result.text, "hello there");
  assert.deepEqual(result.usage, { input_tokens: 42, output_tokens: 8 });
});

test("codex: parses turn.completed + the preceding agent_message into {text, usage}", async () => {
  const fake = makeFakeChild();
  const promise = runCliSubscription(baseParams({ runtime: "codex", prompt: "hi" }), {
    spawnImpl: spawnImplReturning(fake),
  });
  fake.emitStdout(`${JSON.stringify({ type: "thread.started", thread_id: "t1" })}\n`);
  fake.emitStdout(`${JSON.stringify({ type: "turn.started" })}\n`);
  fake.emitStdout(
    `${JSON.stringify({ type: "item.completed", item: { id: "item_0", type: "agent_message", text: "PONG" } })}\n`,
  );
  fake.emitStdout(
    `${JSON.stringify({
      type: "turn.completed",
      usage: { input_tokens: 17155, cached_input_tokens: 5888, output_tokens: 6, reasoning_output_tokens: 0 },
    })}\n`,
  );
  fake.emitClose(0, null);
  const result = await promise;
  assert.equal(result.text, "PONG");
  assert.deepEqual(result.usage, { input_tokens: 17155, output_tokens: 6 });
});

// ---- Not installed ------------------------------------------------------

test("binary not found (ENOENT) classifies as not_installed for either runtime", async () => {
  for (const runtime of ["claude_code", "codex"] as const) {
    const fake = makeFakeChild();
    const promise = runCliSubscription(baseParams({ runtime }), { spawnImpl: spawnImplReturning(fake) });
    const enoent = Object.assign(new Error("spawn ENOENT"), { code: "ENOENT" }) as NodeJS.ErrnoException;
    fake.emitError(enoent);
    await assert.rejects(promise, (err: unknown) => {
      assert.ok(err instanceof CliRunError);
      assert.equal((err as CliRunError).kind, "not_installed");
      return true;
    });
  }
});

// ---- Not authenticated ---------------------------------------------------

test("claude_code: 'Not logged in' result classifies as not_authenticated (after the one bounded recovery re-spawn also fails identically)", async () => {
  // Since auth_expired now gets exactly one bounded, silent recovery
  // re-spawn (see the session-expired recovery tests below), a scenario
  // where the CLI is genuinely not logged in must script BOTH attempts —
  // otherwise the module's second (recovery) spawn would hang waiting for
  // stdio events a single-attempt test never sends.
  const notLoggedInScript = (fake: FakeChild) => {
    fake.emitStdout(
      `${JSON.stringify({
        type: "assistant",
        message: { content: [{ type: "text", text: "Not logged in · Please run /login" }] },
        error: "authentication_failed",
      })}\n`,
    );
    fake.emitStdout(
      `${JSON.stringify({
        type: "result",
        subtype: "success",
        is_error: true,
        result: "Not logged in · Please run /login",
        usage: { input_tokens: 0, output_tokens: 0 },
      })}\n`,
    );
    fake.emitClose(1, null);
  };
  const { spawnImpl } = scriptedSpawnImpl([notLoggedInScript, notLoggedInScript]);
  const promise = runCliSubscription(baseParams({ runtime: "claude_code" }), { spawnImpl, delayImpl: noDelay });
  await assert.rejects(promise, (err: unknown) => {
    assert.ok(err instanceof CliRunError);
    assert.equal((err as CliRunError).kind, "not_authenticated");
    return true;
  });
});

test("codex: a 401 Unauthorized turn.failed classifies as not_authenticated (after the one bounded recovery re-spawn also fails identically)", async () => {
  const unauthorizedScript = (fake: FakeChild) => {
    fake.emitStdout(`${JSON.stringify({ type: "thread.started", thread_id: "t1" })}\n`);
    fake.emitStdout(
      `${JSON.stringify({
        type: "error",
        message: "Reconnecting... 1/5 (unexpected status 401 Unauthorized: Missing bearer or basic authentication in header)",
      })}\n`,
    );
    fake.emitStdout(
      `${JSON.stringify({
        type: "turn.failed",
        error: { message: "unexpected status 401 Unauthorized: Missing bearer or basic authentication in header" },
      })}\n`,
    );
    fake.emitClose(1, null);
  };
  const { spawnImpl } = scriptedSpawnImpl([unauthorizedScript, unauthorizedScript]);
  const promise = runCliSubscription(baseParams({ runtime: "codex" }), { spawnImpl, delayImpl: noDelay });
  await assert.rejects(promise, (err: unknown) => {
    assert.ok(err instanceof CliRunError);
    assert.equal((err as CliRunError).kind, "not_authenticated");
    return true;
  });
});

// ---- Timeout --------------------------------------------------------------

test("a hung process is SIGTERM'd, then SIGKILL'd, and rejects as timeout", async () => {
  const fake = makeFakeChild();
  fake.dieOnKill = { code: null, signal: "SIGKILL" };
  const promise = runCliSubscription(
    baseParams({ runtime: "claude_code", timeoutMs: 15 }),
    { spawnImpl: spawnImplReturning(fake), killGraceMs: 15 },
  );
  await assert.rejects(promise, (err: unknown) => {
    assert.ok(err instanceof CliRunError);
    assert.equal((err as CliRunError).kind, "timeout");
    return true;
  });
  assert.deepEqual(fake.killCalls, ["SIGTERM", "SIGKILL"]);
});

test("a process that dies on SIGTERM never needs SIGKILL, still reports timeout", async () => {
  const fake = makeFakeChild();
  fake.dieOnKill = { code: null, signal: "SIGTERM" };
  const promise = runCliSubscription(
    baseParams({ runtime: "claude_code", timeoutMs: 15 }),
    { spawnImpl: spawnImplReturning(fake), killGraceMs: 5_000 },
  );
  await assert.rejects(promise, (err: unknown) => {
    assert.ok(err instanceof CliRunError);
    assert.equal((err as CliRunError).kind, "timeout");
    return true;
  });
  assert.deepEqual(fake.killCalls, ["SIGTERM"]);
});

// ---- Crash / generic failure ------------------------------------------

test("claude_code: a non-auth 'overloaded' error classifies as crash/overloaded, not not_authenticated, and retries are bounded", async () => {
  // "Overloaded" is retryable (failureClass "overloaded") — so this exercises
  // BOTH the original classification intent (never confuse this with an auth
  // failure) and the new bounded-retry policy: with the default maxRetries=2,
  // exactly 3 total spawn attempts should occur, each seeing the identical
  // overloaded result, before the module gives up and surfaces the failure.
  const overloadedScript = (fake: FakeChild) => {
    fake.emitStdout(
      `${JSON.stringify({
        type: "result",
        subtype: "error_during_execution",
        is_error: true,
        result: "The model is temporarily overloaded.",
      })}\n`,
    );
    fake.emitClose(1, null);
  };
  const { spawnImpl, callCount } = scriptedSpawnImpl([overloadedScript, overloadedScript, overloadedScript]);
  const promise = runCliSubscription(baseParams({ runtime: "claude_code" }), { spawnImpl, delayImpl: noDelay });
  await assert.rejects(promise, (err: unknown) => {
    assert.ok(err instanceof CliRunError);
    assert.equal((err as CliRunError).kind, "crash");
    assert.equal((err as CliRunError).failureClass, "overloaded");
    return true;
  });
  assert.equal(callCount(), 3, "expected exactly 3 attempts (1 initial + 2 bounded retries)");
});

test("codex: a non-auth turn.failed (bad model) classifies as crash", async () => {
  const fake = makeFakeChild();
  const promise = runCliSubscription(baseParams({ runtime: "codex" }), { spawnImpl: spawnImplReturning(fake) });
  fake.emitStdout(
    `${JSON.stringify({
      type: "turn.failed",
      error: { message: '{"type":"error","status":400,"error":{"type":"invalid_request_error","message":"model not supported"}}' },
    })}\n`,
  );
  fake.emitClose(1, null);
  await assert.rejects(promise, (err: unknown) => {
    assert.ok(err instanceof CliRunError);
    assert.equal((err as CliRunError).kind, "crash");
    return true;
  });
});

test("no parsable output at all + non-zero exit classifies as crash/fatal (unrecognized signal, never retried)", async () => {
  const { spawnImpl, callCount } = scriptedSpawnImpl([
    (fake) => emitCrash(fake, "segmentation fault", 139),
  ]);
  const promise = runCliSubscription(baseParams({ runtime: "claude_code" }), { spawnImpl, delayImpl: noDelay });
  await assert.rejects(promise, (err: unknown) => {
    assert.ok(err instanceof CliRunError);
    assert.equal((err as CliRunError).kind, "crash");
    assert.equal((err as CliRunError).failureClass, "fatal");
    return true;
  });
  assert.equal(callCount(), 1, "an unrecognized/fatal crash must never be retried");
});

// ---- Invocation shape ---------------------------------------------------

test("claude_code argv: -p, stream-json, --verbose, --tools \"\", stdin ignored", async () => {
  const captured: { command: string; args: string[]; options: Record<string, unknown> } = {
    command: "", args: [], options: {},
  };
  const fake = makeFakeChild();
  const promise = runCliSubscription(
    baseParams({ runtime: "claude_code", prompt: "explain photosynthesis", systemPrompt: "be terse", model: "opus" }),
    {
      spawnImpl: (command, args, options) => {
        captured.command = command;
        captured.args = args;
        captured.options = options;
        return fake.child;
      },
    },
  );
  fake.emitStdout(`${JSON.stringify({ type: "result", is_error: false, result: "ok", usage: {} })}\n`);
  fake.emitClose(0, null);
  await promise;
  assert.equal(captured.command, "claude");
  assert.deepEqual(captured.args, [
    "-p", "explain photosynthesis",
    "--output-format", "stream-json",
    "--verbose",
    "--tools", "",
    "--system-prompt", "be terse",
    "--model", "opus",
  ]);
  assert.deepEqual(captured.options.stdio, ["ignore", "pipe", "pipe"]);
});

test("codex argv: exec, --json, --skip-git-repo-check, --sandbox read-only", async () => {
  const captured: { command: string; args: string[] } = { command: "", args: [] };
  const fake = makeFakeChild();
  const promise = runCliSubscription(baseParams({ runtime: "codex", prompt: "say hi", model: "o4" }), {
    spawnImpl: (command, args) => {
      captured.command = command;
      captured.args = args;
      return fake.child;
    },
  });
  fake.emitStdout(
    `${JSON.stringify({ type: "item.completed", item: { type: "agent_message", text: "hi" } })}\n`
    + `${JSON.stringify({ type: "turn.completed", usage: { input_tokens: 1, output_tokens: 1 } })}\n`,
  );
  fake.emitClose(0, null);
  await promise;
  assert.equal(captured.command, "codex");
  assert.deepEqual(captured.args, ["exec", "say hi", "--json", "--skip-git-repo-check", "--sandbox", "read-only", "--model", "o4"]);
});

// ---- Reasoning effort (Phase 1: reasoning-effort control) ----------------
// Two DIFFERENT flags/enums, verified live against each CLI's own --help —
// never flattened to one shared shape.

test("claude_code argv: reasoningEffort appends --effort <level>", async () => {
  const captured: { args: string[] } = { args: [] };
  const fake = makeFakeChild();
  const promise = runCliSubscription(
    baseParams({ runtime: "claude_code", prompt: "hi", reasoningEffort: "xhigh" }),
    {
      spawnImpl: (_command, args) => {
        captured.args = args;
        return fake.child;
      },
    },
  );
  fake.emitStdout(`${JSON.stringify({ type: "result", is_error: false, result: "ok", usage: {} })}\n`);
  fake.emitClose(0, null);
  await promise;
  assert.deepEqual(captured.args, [
    "-p", "hi", "--output-format", "stream-json", "--verbose", "--tools", "", "--effort", "xhigh",
  ]);
});

test("codex argv: reasoningEffort appends -c model_reasoning_effort=<level>", async () => {
  const captured: { args: string[] } = { args: [] };
  const fake = makeFakeChild();
  const promise = runCliSubscription(
    baseParams({ runtime: "codex", prompt: "hi", reasoningEffort: "off" }),
    {
      spawnImpl: (_command, args) => {
        captured.args = args;
        return fake.child;
      },
    },
  );
  fake.emitStdout(
    `${JSON.stringify({ type: "item.completed", item: { type: "agent_message", text: "hi" } })}\n`
    + `${JSON.stringify({ type: "turn.completed", usage: { input_tokens: 1, output_tokens: 1 } })}\n`,
  );
  fake.emitClose(0, null);
  await promise;
  assert.deepEqual(captured.args, [
    "exec", "hi", "--json", "--skip-git-repo-check", "--sandbox", "read-only", "-c", "model_reasoning_effort=off",
  ]);
});

test("reasoningEffort unset: neither runtime appends any effort flag (CLI's own default applies)", async () => {
  for (const runtime of ["claude_code", "codex"] as const) {
    const captured: { args: string[] } = { args: [] };
    const fake = makeFakeChild();
    const promise = runCliSubscription(baseParams({ runtime, prompt: "hi" }), {
      spawnImpl: (_command, args) => {
        captured.args = args;
        return fake.child;
      },
    });
    if (runtime === "claude_code") {
      fake.emitStdout(`${JSON.stringify({ type: "result", is_error: false, result: "ok", usage: {} })}\n`);
    } else {
      fake.emitStdout(
        `${JSON.stringify({ type: "item.completed", item: { type: "agent_message", text: "hi" } })}\n`
        + `${JSON.stringify({ type: "turn.completed", usage: { input_tokens: 1, output_tokens: 1 } })}\n`,
      );
    }
    fake.emitClose(0, null);
    await promise;
    assert.ok(!captured.args.includes("--effort"), `${runtime}: --effort must not appear when unset`);
    assert.ok(!captured.args.some((a) => a.startsWith("model_reasoning_effort=")), `${runtime}: model_reasoning_effort= must not appear when unset`);
  }
});

test("CLAUDE_CLI_PATH / CODEX_CLI_PATH env overrides pick a different binary", async () => {
  const fake = makeFakeChild();
  let capturedCommand = "";
  const promise = runCliSubscription(baseParams({ runtime: "codex" }), {
    spawnImpl: (command) => {
      capturedCommand = command;
      return fake.child;
    },
    env: { ...process.env, CODEX_CLI_PATH: "/opt/custom/codex" },
  });
  fake.emitStdout(
    `${JSON.stringify({ type: "item.completed", item: { type: "agent_message", text: "hi" } })}\n`
    + `${JSON.stringify({ type: "turn.completed", usage: { input_tokens: 1, output_tokens: 1 } })}\n`,
  );
  fake.emitClose(0, null);
  await promise;
  assert.equal(capturedCommand, "/opt/custom/codex");
});

// ---- Reliability: retry policy (rate_limited / overloaded / transient) ---

test("codex: a rate-limited (429) failure retries and succeeds on the second attempt", async () => {
  const { spawnImpl, callCount } = scriptedSpawnImpl([
    (fake) => emitCrash(fake, "Error: 429 Too Many Requests — rate limit exceeded, please retry later"),
    (fake) => {
      fake.emitStdout(
        `${JSON.stringify({ type: "item.completed", item: { type: "agent_message", text: "ok now" } })}\n`
        + `${JSON.stringify({ type: "turn.completed", usage: { input_tokens: 3, output_tokens: 2 } })}\n`,
      );
      fake.emitClose(0, null);
    },
  ]);
  const result = await runCliSubscription(baseParams({ runtime: "codex" }), { spawnImpl, delayImpl: noDelay });
  assert.equal(result.text, "ok now");
  assert.equal(callCount(), 2, "expected the rate-limited first attempt plus one successful retry");
});

test("claude_code: a transient (ECONNRESET-shaped) crash retries and succeeds on the second attempt", async () => {
  const { spawnImpl, callCount } = scriptedSpawnImpl([
    (fake) => emitCrash(fake, "FetchError: request to https://api.anthropic.com failed, reason: ECONNRESET"),
    (fake) => {
      fake.emitStdout(`${JSON.stringify({ type: "result", is_error: false, result: "recovered", usage: { input_tokens: 1, output_tokens: 1 } })}\n`);
      fake.emitClose(0, null);
    },
  ]);
  const result = await runCliSubscription(baseParams({ runtime: "claude_code" }), { spawnImpl, delayImpl: noDelay });
  assert.equal(result.text, "recovered");
  assert.equal(callCount(), 2);
});

test("a transient local spawn error (EAGAIN) is retried; an unrecognized spawn error is not", async () => {
  // EAGAIN simulates the OS momentarily refusing to fork (resource limits) —
  // genuinely transient, unlike ENOENT (not_installed, never retried).
  let attempt = 0;
  const spawnImpl: CliSpawnImpl = () => {
    attempt += 1;
    const fake = makeFakeChild();
    if (attempt === 1) {
      queueMicrotask(() => fake.emitError(Object.assign(new Error("spawn EAGAIN"), { code: "EAGAIN" }) as NodeJS.ErrnoException));
    } else {
      queueMicrotask(() => {
        fake.emitStdout(`${JSON.stringify({ type: "result", is_error: false, result: "ok", usage: {} })}\n`);
        fake.emitClose(0, null);
      });
    }
    return fake.child;
  };
  const result = await runCliSubscription(baseParams({ runtime: "claude_code" }), { spawnImpl, delayImpl: noDelay });
  assert.equal(result.text, "ok");
  assert.equal(attempt, 2, "EAGAIN must be treated as transient and retried once");
});

test("an unrecognized (non-EAGAIN-class) spawn error is fatal and never retried", async () => {
  const { spawnImpl, callCount } = scriptedSpawnImpl([
    (fake) => fake.emitError(Object.assign(new Error("spawn EACCES"), { code: "EACCES" }) as NodeJS.ErrnoException),
  ]);
  const promise = runCliSubscription(baseParams({ runtime: "claude_code" }), { spawnImpl, delayImpl: noDelay });
  await assert.rejects(promise, (err: unknown) => {
    assert.ok(err instanceof CliRunError);
    assert.equal((err as CliRunError).kind, "crash");
    assert.equal((err as CliRunError).failureClass, "fatal");
    return true;
  });
  assert.equal(callCount(), 1);
});

// ---- Reliability: session-expired ("auth_expired") recovery -------------

test("claude_code: auth_expired self-heals — a not-logged-in first attempt followed by a clean second attempt succeeds silently", async () => {
  const { spawnImpl, callCount } = scriptedSpawnImpl([
    (fake) => {
      fake.emitStdout(
        `${JSON.stringify({
          type: "result", subtype: "success", is_error: true,
          result: "Not logged in · Please run /login", usage: { input_tokens: 0, output_tokens: 0 },
        })}\n`,
      );
      fake.emitClose(1, null);
    },
    (fake) => {
      // A fresh process re-reads ~/.claude from scratch — this simulates the
      // CLI's own token having been refreshed (by itself, or a concurrent
      // invocation) between the two spawns, entirely without human input.
      fake.emitStdout(`${JSON.stringify({ type: "result", is_error: false, result: "back online", usage: { input_tokens: 2, output_tokens: 2 } })}\n`);
      fake.emitClose(0, null);
    },
  ]);
  const result = await runCliSubscription(baseParams({ runtime: "claude_code" }), { spawnImpl, delayImpl: noDelay });
  assert.equal(result.text, "back online");
  assert.equal(callCount(), 2, "expected exactly one silent recovery re-spawn, not zero and not more");
});

test("codex: auth_expired recovery attempt ALSO fails — surfaces not_authenticated after exactly 2 attempts, never loops", async () => {
  const authFailScript = (fake: FakeChild) => {
    fake.emitStdout(
      `${JSON.stringify({ type: "turn.failed", error: { message: "unexpected status 401 Unauthorized: Missing bearer or basic authentication in header" } })}\n`,
    );
    fake.emitClose(1, null);
  };
  const { spawnImpl, callCount } = scriptedSpawnImpl([authFailScript, authFailScript]);
  const promise = runCliSubscription(baseParams({ runtime: "codex" }), { spawnImpl, delayImpl: noDelay });
  await assert.rejects(promise, (err: unknown) => {
    assert.ok(err instanceof CliRunError);
    assert.equal((err as CliRunError).kind, "not_authenticated");
    assert.equal((err as CliRunError).failureClass, "auth_expired");
    return true;
  });
  assert.equal(callCount(), 2, "must attempt exactly one bounded recovery re-spawn, then stop — never a retry loop");
});

test("not_installed (ENOENT) is never retried, not even once", async () => {
  const { spawnImpl, callCount } = scriptedSpawnImpl([
    (fake) => fake.emitError(Object.assign(new Error("spawn ENOENT"), { code: "ENOENT" }) as NodeJS.ErrnoException),
  ]);
  const promise = runCliSubscription(baseParams({ runtime: "codex" }), { spawnImpl, delayImpl: noDelay });
  await assert.rejects(promise, (err: unknown) => {
    assert.ok(err instanceof CliRunError);
    assert.equal((err as CliRunError).kind, "not_installed");
    return true;
  });
  assert.equal(callCount(), 1);
});

// ---- Reliability: no-output watchdog, distinct from the overall timeout --

test("a process producing zero output trips the no-output watchdog well before the overall timeout, and is classified transient", async () => {
  const fake = makeFakeChild();
  fake.dieOnKill = { code: null, signal: "SIGKILL" };
  const startedAt = Date.now();
  const promise = runCliSubscription(
    baseParams({ runtime: "claude_code", timeoutMs: 5_000 }),
    { spawnImpl: spawnImplReturning(fake), killGraceMs: 5, noOutputTimeoutMs: 20, maxRetries: 0 },
  );
  await assert.rejects(promise, (err: unknown) => {
    assert.ok(err instanceof CliRunError);
    assert.equal((err as CliRunError).kind, "timeout");
    assert.equal((err as CliRunError).failureClass, "transient");
    assert.match((err as CliRunError).message, /no output/i);
    assert.match((err as CliRunError).message, /watchdog/i);
    return true;
  });
  assert.ok(Date.now() - startedAt < 5_000, "the watchdog (20ms) must fire well before the 5s overall timeout");
  assert.deepEqual(fake.killCalls, ["SIGTERM", "SIGKILL"]);
});

test("the no-output watchdog resets on ANY stdout/stderr activity — a slow-but-talking process is not killed early", async () => {
  const fake = makeFakeChild();
  const promise = runCliSubscription(
    baseParams({ runtime: "claude_code", timeoutMs: 5_000 }),
    { spawnImpl: spawnImplReturning(fake), noOutputTimeoutMs: 30 },
  );
  // Keep feeding small chunks of a still-incomplete stream, each well inside
  // the 30ms no-output window, so the watchdog keeps getting reset instead
  // of ever tripping.
  for (let i = 0; i < 4; i += 1) {
    await new Promise((resolve) => setTimeout(resolve, 12));
    fake.emitStdout(" ");
  }
  fake.emitStdout(`${JSON.stringify({ type: "result", is_error: false, result: "done", usage: { input_tokens: 1, output_tokens: 1 } })}\n`);
  fake.emitClose(0, null);
  const result = await promise;
  assert.equal(result.text, "done");
});

test("a no-output watchdog trip is retried and can succeed on the next attempt", async () => {
  const { spawnImpl, callCount } = scriptedSpawnImpl([
    (fake) => {
      // Deliberately emits nothing — the watchdog must trip on its own. Dies
      // on the watchdog's own SIGTERM so the attempt actually resolves
      // (a real CLI process terminates on SIGTERM; this fake must too).
      fake.dieOnKill = { code: null, signal: "SIGTERM" };
    },
    (fake) => {
      fake.emitStdout(`${JSON.stringify({ type: "result", is_error: false, result: "second try worked", usage: { input_tokens: 1, output_tokens: 1 } })}\n`);
      fake.emitClose(0, null);
    },
  ]);
  const result = await runCliSubscription(
    baseParams({ runtime: "claude_code", timeoutMs: 5_000 }),
    { spawnImpl, delayImpl: noDelay, noOutputTimeoutMs: 15 },
  );
  assert.equal(result.text, "second try worked");
  assert.equal(callCount(), 2);
});

test("no-output watchdog is not armed when noOutputTimeoutMs is not strictly less than timeoutMs (overall timeout alone governs, fatal, no retry)", async () => {
  // Mirrors the existing "hung process" timeout tests: with the DEFAULT
  // noOutputTimeoutMs (45s) and a short custom timeoutMs (15ms), the overall
  // timer must fire first and the result must be the ORIGINAL single-attempt
  // "fatal" timeout, not a watchdog-classified retryable one.
  const fake = makeFakeChild();
  fake.dieOnKill = { code: null, signal: "SIGKILL" };
  const { spawnImpl, callCount } = (() => {
    let attempts = 0;
    const impl: CliSpawnImpl = () => {
      attempts += 1;
      return fake.child;
    };
    return { spawnImpl: impl, callCount: () => attempts };
  })();
  const promise = runCliSubscription(
    baseParams({ runtime: "claude_code", timeoutMs: 15 }),
    { spawnImpl, killGraceMs: 5, delayImpl: noDelay },
  );
  await assert.rejects(promise, (err: unknown) => {
    assert.ok(err instanceof CliRunError);
    assert.equal((err as CliRunError).kind, "timeout");
    assert.equal((err as CliRunError).failureClass, "fatal");
    assert.doesNotMatch((err as CliRunError).message, /watchdog/i);
    return true;
  });
  assert.equal(callCount(), 1, "the overall-timeout path must never be retried");
});

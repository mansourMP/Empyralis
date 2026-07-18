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

test("claude_code: 'Not logged in' result classifies as not_authenticated", async () => {
  const fake = makeFakeChild();
  const promise = runCliSubscription(baseParams({ runtime: "claude_code" }), {
    spawnImpl: spawnImplReturning(fake),
  });
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
  await assert.rejects(promise, (err: unknown) => {
    assert.ok(err instanceof CliRunError);
    assert.equal((err as CliRunError).kind, "not_authenticated");
    return true;
  });
});

test("codex: a 401 Unauthorized turn.failed classifies as not_authenticated", async () => {
  const fake = makeFakeChild();
  const promise = runCliSubscription(baseParams({ runtime: "codex" }), { spawnImpl: spawnImplReturning(fake) });
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

test("claude_code: a non-auth error result classifies as crash, not not_authenticated", async () => {
  const fake = makeFakeChild();
  const promise = runCliSubscription(baseParams({ runtime: "claude_code" }), {
    spawnImpl: spawnImplReturning(fake),
  });
  fake.emitStdout(
    `${JSON.stringify({
      type: "result",
      subtype: "error_during_execution",
      is_error: true,
      result: "The model is temporarily overloaded.",
    })}\n`,
  );
  fake.emitClose(1, null);
  await assert.rejects(promise, (err: unknown) => {
    assert.ok(err instanceof CliRunError);
    assert.equal((err as CliRunError).kind, "crash");
    return true;
  });
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

test("no parsable output at all + non-zero exit classifies as crash", async () => {
  const fake = makeFakeChild();
  const promise = runCliSubscription(baseParams({ runtime: "claude_code" }), {
    spawnImpl: spawnImplReturning(fake),
  });
  fake.emitStderr("segmentation fault\n");
  fake.emitClose(139, null);
  await assert.rejects(promise, (err: unknown) => {
    assert.ok(err instanceof CliRunError);
    assert.equal((err as CliRunError).kind, "crash");
    return true;
  });
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

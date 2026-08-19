import test from "node:test";
import assert from "node:assert/strict";

import {
  buildBatchDriverScript,
  computeBatchTimeoutSeconds,
  interpretBatchResults,
  parseBatchCommandsArgument,
  type BatchCommandSpec,
  type RawCommandFileState,
} from "../shell/batch-shell";

// ── parseBatchCommandsArgument: pure validation, no I/O ──

test("parseBatchCommandsArgument accepts a plain array of strings", () => {
  const specs = parseBatchCommandsArgument(["echo one", "echo two"]);
  assert.equal(specs.length, 2);
  assert.equal(specs[0].command, "echo one");
  assert.equal(specs[0].index, 0);
  assert.equal(specs[1].command, "echo two");
  assert.equal(specs[1].index, 1);
});

test("parseBatchCommandsArgument accepts {command, timeout_seconds} objects and honors a per-command timeout", () => {
  const specs = parseBatchCommandsArgument([{ command: "sleep 1", timeout_seconds: 5 }, "echo two"], {
    defaultTimeoutSeconds: 60,
  });
  assert.equal(specs[0].timeoutSeconds, 5);
  assert.equal(specs[1].timeoutSeconds, 60);
});

test("parseBatchCommandsArgument mixes string and object entries in one array", () => {
  const specs = parseBatchCommandsArgument(["echo one", { command: "echo two", timeout_seconds: 10 }]);
  assert.equal(specs[0].timeoutSeconds, 60); // default
  assert.equal(specs[1].timeoutSeconds, 10);
});

test("parseBatchCommandsArgument clamps a per-command timeout to the max", () => {
  const specs = parseBatchCommandsArgument([{ command: "echo one", timeout_seconds: 99999 }], {
    maxTimeoutSeconds: 300,
  });
  assert.equal(specs[0].timeoutSeconds, 300);
});

test("parseBatchCommandsArgument rejects a non-array", () => {
  assert.throws(() => parseBatchCommandsArgument("echo one"), /must be an array/);
  assert.throws(() => parseBatchCommandsArgument({ command: "echo one" }), /must be an array/);
  assert.throws(() => parseBatchCommandsArgument(undefined), /must be an array/);
});

test("parseBatchCommandsArgument rejects an empty array", () => {
  assert.throws(() => parseBatchCommandsArgument([]), /must not be empty/);
});

test("parseBatchCommandsArgument rejects a batch over the max size", () => {
  const commands = Array.from({ length: 21 }, (_, i) => `echo ${i}`);
  assert.throws(() => parseBatchCommandsArgument(commands, { maxCommands: 20 }), /exceeds the maximum batch size of 20/);
});

test("parseBatchCommandsArgument rejects an empty command string", () => {
  assert.throws(() => parseBatchCommandsArgument(["echo ok", "   "]), /commands\[1\] is empty/);
});

test("parseBatchCommandsArgument rejects a non-string, non-object entry", () => {
  assert.throws(() => parseBatchCommandsArgument([42]), /commands\[0\] must be a string or an object/);
  assert.throws(() => parseBatchCommandsArgument([["echo", "x"]]), /commands\[0\] must be a string or an object/);
});

// ── computeBatchTimeoutSeconds ──

test("computeBatchTimeoutSeconds sums per-command budgets plus overhead", () => {
  const specs: BatchCommandSpec[] = [
    { index: 0, command: "a", timeoutSeconds: 10 },
    { index: 1, command: "b", timeoutSeconds: 20 },
  ];
  // 10 + 20 + 5 base + 2*2 per-command = 39
  assert.equal(computeBatchTimeoutSeconds(specs), 39);
});

test("computeBatchTimeoutSeconds is capped at the hard ceiling regardless of how large the sum gets", () => {
  const specs: BatchCommandSpec[] = Array.from({ length: 20 }, (_, i) => ({
    index: i,
    command: `cmd${i}`,
    timeoutSeconds: 300,
  }));
  const total = computeBatchTimeoutSeconds(specs);
  assert.ok(total <= 900, `expected <= 900, got ${total}`);
  assert.equal(total, 900);
});

// ── buildBatchDriverScript: pure string generation ──

test("buildBatchDriverScript emits one dot-sourced, non-forking block per command", () => {
  const specs: BatchCommandSpec[] = [
    { index: 0, command: "cd /tmp", timeoutSeconds: 30 },
    { index: 1, command: "pwd", timeoutSeconds: 30 },
  ];
  const script = buildBatchDriverScript(specs, { stopOnFailure: true, batchDirPath: "/workspace/.batch/x" });
  assert.match(script, /^#!\/bin\/sh/);
  // Each command is sourced with `.` inside a `{ }` GROUP (never a `( )`
  // subshell) — that's the mechanism that lets cd/export from one command
  // reach the next.
  assert.match(script, /\{ \. '\/workspace\/\.batch\/x\/cmd_0\.sh'; \} > '\/workspace\/\.batch\/x\/out_0' 2> '\/workspace\/\.batch\/x\/err_0'/);
  assert.match(script, /\{ \. '\/workspace\/\.batch\/x\/cmd_1\.sh'; \}/);
  assert.doesNotMatch(script, /\(\s*\.\s+'\/workspace/); // never wrapped in a real subshell
});

test("buildBatchDriverScript sets STOP on failure only when stopOnFailure is true", () => {
  const specs: BatchCommandSpec[] = [{ index: 0, command: "false", timeoutSeconds: 30 }];
  const withStop = buildBatchDriverScript(specs, { stopOnFailure: true, batchDirPath: "/d" });
  assert.match(withStop, /if \[ "\$code" != "0" \]; then STOP=1; fi/);
  const withoutStop = buildBatchDriverScript(specs, { stopOnFailure: false, batchDirPath: "/d" });
  assert.doesNotMatch(withoutStop, /STOP=1/);
});

test("buildBatchDriverScript quotes paths built from caller-controlled content defensively", () => {
  // Directory paths are always uuid + fixed literals in production, but the
  // quoting itself should still be correct if that ever changes.
  const specs: BatchCommandSpec[] = [{ index: 0, command: "echo hi", timeoutSeconds: 30 }];
  const script = buildBatchDriverScript(specs, { stopOnFailure: true, batchDirPath: "/tmp/o'brien" });
  assert.match(script, /'\/tmp\/o'\\''brien\/cmd_0\.sh'/);
});

// ── interpretBatchResults: the outcome-honesty seam ──

function state(partial: Partial<RawCommandFileState>): RawCommandFileState {
  return { status: "", exitCode: null, stdout: "", stderr: "", ...partial };
}

test("a command that ran and exited 0 is 'success', ran=true", () => {
  const specs: BatchCommandSpec[] = [{ index: 0, command: "echo hi", timeoutSeconds: 30 }];
  const states = new Map([[0, state({ status: "done", exitCode: 0, stdout: "hi", stderr: "" })]]);
  const { results, stoppedEarly } = interpretBatchResults(specs, states, { stopOnFailure: true, batchTimedOut: false });
  assert.equal(results[0].status, "success");
  assert.equal(results[0].ran, true);
  assert.equal(results[0].exit_code, 0);
  assert.equal(results[0].reason, null);
  assert.equal(stoppedEarly, false);
});

test("a command that ran and exited non-zero is 'failed', distinct from 'skipped' or 'not_run'", () => {
  const specs: BatchCommandSpec[] = [{ index: 0, command: "false", timeoutSeconds: 30 }];
  const states = new Map([[0, state({ status: "done", exitCode: 1, stdout: "", stderr: "boom" })]]);
  const { results } = interpretBatchResults(specs, states, { stopOnFailure: true, batchTimedOut: false });
  assert.equal(results[0].status, "failed");
  assert.equal(results[0].ran, true);
  assert.equal(results[0].exit_code, 1);
  assert.equal(results[0].stderr, "boom");
});

test("a command skipped after an earlier failure is 'skipped', ran=false, with a reason — never confused with 'failed'", () => {
  const specs: BatchCommandSpec[] = [
    { index: 0, command: "false", timeoutSeconds: 30 },
    { index: 1, command: "echo never", timeoutSeconds: 30 },
  ];
  const states = new Map([
    [0, state({ status: "done", exitCode: 1 })],
    [1, state({ status: "skipped" })],
  ]);
  const { results, stoppedEarly } = interpretBatchResults(specs, states, { stopOnFailure: true, batchTimedOut: false });
  assert.equal(results[1].status, "skipped");
  assert.equal(results[1].ran, false);
  assert.equal(results[1].exit_code, null);
  assert.match(String(results[1].reason), /an earlier command in this batch failed/);
  assert.equal(stoppedEarly, true);
});

test("a command in-flight when the batch timed out is 'timed_out', distinct from 'failed' — exit code is unknown, not guessed", () => {
  const specs: BatchCommandSpec[] = [{ index: 0, command: "sleep 999", timeoutSeconds: 30 }];
  const states = new Map([[0, state({ status: "started", stdout: "partial output" })]]);
  const { results, stoppedEarly } = interpretBatchResults(specs, states, { stopOnFailure: true, batchTimedOut: true });
  assert.equal(results[0].status, "timed_out");
  assert.equal(results[0].ran, true);
  assert.equal(results[0].exit_code, null);
  assert.equal(results[0].stdout, "partial output");
  assert.match(String(results[0].reason), /shared time budget ran out/);
  assert.equal(stoppedEarly, true);
});

test("a command never reached at all (no status file) is 'not_run', with a batch-timeout-specific reason when that's why", () => {
  const specs: BatchCommandSpec[] = [{ index: 0, command: "echo never", timeoutSeconds: 30 }];
  const states = new Map<number, RawCommandFileState>(); // nothing written at all
  const timedOut = interpretBatchResults(specs, states, { stopOnFailure: true, batchTimedOut: true });
  assert.equal(timedOut.results[0].status, "not_run");
  assert.match(String(timedOut.results[0].reason), /shared time budget ran out before this command could start/);

  const notTimedOut = interpretBatchResults(specs, states, { stopOnFailure: true, batchTimedOut: false });
  assert.equal(notTimedOut.results[0].status, "not_run");
  assert.doesNotMatch(String(notTimedOut.results[0].reason), /time budget/);
});

test("stop_on_failure=false never produces 'skipped' — every command that was reached actually ran", () => {
  const specs: BatchCommandSpec[] = [
    { index: 0, command: "false", timeoutSeconds: 30 },
    { index: 1, command: "echo still runs", timeoutSeconds: 30 },
  ];
  const states = new Map([
    [0, state({ status: "done", exitCode: 1 })],
    [1, state({ status: "done", exitCode: 0, stdout: "still runs" })],
  ]);
  const { results, stoppedEarly } = interpretBatchResults(specs, states, { stopOnFailure: false, batchTimedOut: false });
  assert.equal(results[0].status, "failed");
  assert.equal(results[1].status, "success");
  assert.equal(stoppedEarly, false);
});

test("success/failed/skipped/timed_out/not_run are five genuinely distinct statuses — no two ever collapse to the same value for different underlying facts", () => {
  const specs: BatchCommandSpec[] = [
    { index: 0, command: "ok", timeoutSeconds: 30 },
    { index: 1, command: "bad", timeoutSeconds: 30 },
    { index: 2, command: "skipped-one", timeoutSeconds: 30 },
    { index: 3, command: "in-flight", timeoutSeconds: 30 },
    { index: 4, command: "never-reached", timeoutSeconds: 30 },
  ];
  const states = new Map<number, RawCommandFileState>([
    [0, state({ status: "done", exitCode: 0 })],
    [1, state({ status: "done", exitCode: 1 })],
    [2, state({ status: "skipped" })],
    [3, state({ status: "started" })],
    // index 4: nothing written
  ]);
  const { results } = interpretBatchResults(specs, states, { stopOnFailure: true, batchTimedOut: true });
  const statuses = results.map((r) => r.status);
  assert.deepEqual(statuses, ["success", "failed", "skipped", "timed_out", "not_run"]);
  assert.equal(new Set(statuses).size, 5);
});

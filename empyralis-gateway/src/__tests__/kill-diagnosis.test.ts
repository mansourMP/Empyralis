import test from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

import { diagnoseKill, killDiagnosisFields, SIGKILL_EXIT_CODE } from "../shell/kill-diagnosis";
import { interpretBatchResults, type RawCommandFileState } from "../shell/batch-shell";

// The behaviour that made this necessary, reproduced before it was fixed:
// a command the system kills reports exit_code null (host) or 137 (container)
// with an empty stderr, and nothing anywhere says why. A real annual ledger
// read into pandas inside a 512 MB container lands exactly there.

test("our own timeout is never diagnosed as anything else", () => {
  // The single most important ordering in the module. We send SIGKILL on our
  // own deadline, so every signal below is ambiguous until this is excluded —
  // and a timeout reported as a memory problem sends somebody to shrink a
  // file that was never too big.
  assert.equal(
    diagnoseKill({ exitCode: null, signal: "SIGKILL", timedOut: true, isolation: "host" }),
    null,
  );
  assert.equal(
    diagnoseKill({
      exitCode: SIGKILL_EXIT_CODE,
      timedOut: true,
      isolation: "sandbox",
      memoryLimitMb: 512,
    }),
    null,
  );
});

test("an ordinary exit is left completely alone", () => {
  // A normal failure must stay byte-for-byte the shape it has always been.
  for (const code of [0, 1, 2, 127, 255]) {
    assert.equal(
      diagnoseKill({ exitCode: code, timedOut: false, isolation: "sandbox", memoryLimitMb: 512 }),
      null,
      `exit ${code} is the command speaking for itself`,
    );
  }
  assert.deepEqual(killDiagnosisFields(null), {}, "and contributes no keys at all");
});

test("a capped container that kills a command names the limit", () => {
  const d = diagnoseKill({
    exitCode: SIGKILL_EXIT_CODE,
    timedOut: false,
    isolation: "sandbox",
    memoryLimitMb: 512,
  });
  assert.ok(d);
  assert.equal(d.reason, "memory_limit_exceeded");
  assert.equal(d.memoryLimitMb, 512);
  // The limit is IN the sentence, so the claim is checkable rather than
  // asserted — that is the whole reason naming a cause here is honest.
  assert.match(d.statement, /512 MB/);
  assert.match(d.statement, /pieces|where it sits/, "says what would actually help");
});

test("on the host it never claims a limit nobody set", () => {
  const d = diagnoseKill({
    exitCode: null,
    signal: "SIGKILL",
    timedOut: false,
    isolation: "host",
  });
  assert.ok(d);
  assert.equal(d.reason, "killed_by_the_system");
  assert.equal(d.memoryLimitMb, undefined);
  assert.doesNotMatch(d.statement, /\bMB\b/, "no invented number");
  assert.deepEqual(
    Object.keys(killDiagnosisFields(d)).sort(),
    ["kill_reason", "kill_statement", "killed"],
    "and reports no memory_limit_mb key at all",
  );
});

test("a sandbox with no known limit degrades to the weaker, true answer", () => {
  // Never the stronger one. A limit we cannot name is a limit we cannot blame.
  for (const limit of [undefined, 0, -1]) {
    const d = diagnoseKill({
      exitCode: SIGKILL_EXIT_CODE,
      timedOut: false,
      isolation: "sandbox",
      memoryLimitMb: limit,
    });
    assert.ok(d);
    assert.equal(d.reason, "killed_by_the_system", `limit=${limit}`);
  }
});

test("no statement names a mechanism the person cannot act on", () => {
  const statements = [
    diagnoseKill({ exitCode: 137, timedOut: false, isolation: "sandbox", memoryLimitMb: 512 })!.statement,
    diagnoseKill({ exitCode: null, signal: "SIGKILL", timedOut: false, isolation: "host" })!.statement,
  ];
  for (const s of statements) {
    for (const banned of ["docker", "cgroup", "SIGKILL", "OOM", "137", "apt", "pip", "duckdb", "pandas"]) {
      assert.ok(!s.toLowerCase().includes(banned.toLowerCase()), `statement must not name ${banned}: ${s}`);
    }
  }
});

test("a killed command in a batch is not reported as a failed one", () => {
  const specs = [
    { index: 0, command: "python3 read_ledger.py", statusPath: "", stdoutPath: "", stderrPath: "" } as never,
  ];
  const states = new Map<number, RawCommandFileState>([
    [0, { status: "done", exitCode: SIGKILL_EXIT_CODE, stdout: "read 4000 of 260000 rows\n", stderr: "" }],
  ]);
  const { results } = interpretBatchResults(specs, states, {
    stopOnFailure: true,
    batchTimedOut: false,
    isolation: "sandbox",
    memoryLimitMb: 512,
  });
  assert.equal(results[0].status, "killed", "'failed' would send somebody to debug a working command");
  assert.equal(results[0].exit_code, SIGKILL_EXIT_CODE);
  assert.match(String(results[0].reason), /512 MB/);
  // The partial output is still real and must survive.
  assert.match(results[0].stdout, /4000 of 260000/);
});

test("a genuinely failed command in a batch still reads as failed", () => {
  const specs = [{ index: 0, command: "false", statusPath: "", stdoutPath: "", stderrPath: "" } as never];
  const states = new Map<number, RawCommandFileState>([
    [0, { status: "done", exitCode: 1, stdout: "", stderr: "boom" }],
  ]);
  const { results } = interpretBatchResults(specs, states, {
    stopOnFailure: true,
    batchTimedOut: false,
    isolation: "sandbox",
    memoryLimitMb: 512,
  });
  assert.equal(results[0].status, "failed");
  assert.equal(results[0].reason, null);
});

// ── the wiring, structurally ────────────────────────────────────────────
// A pure module with a perfect test and no caller is this repo's most common
// defect, and these fields are invisible until a box actually kills something.
// Resolved from the COMPILED test's own location (dist/__tests__), back to
// the real source tree — the same shape the sibling installer-drift test
// uses. The canary below is what fails loudly if this ever stops landing on
// real source, rather than the scan quietly enforcing nothing.
const SRC_DIR = path.resolve(__dirname, "..", "..", "src");
const RUNTIME_TS = fs.readFileSync(path.join(SRC_DIR, "shell", "runtime.ts"), "utf8");

test("CANARY: runtime.ts was read and is the real executor", () => {
  assert.ok(RUNTIME_TS.includes("executeShell"), "the scan reached real source");
  assert.ok(RUNTIME_TS.length > 10_000);
});

test("both shell.execute branches carry the diagnosis", () => {
  const calls = RUNTIME_TS.match(/killDiagnosisFields\(/g) ?? [];
  assert.ok(calls.length >= 2, `sandbox and host both diagnose; found ${calls.length}`);
  assert.match(RUNTIME_TS, /isolation:\s*"sandbox",\s*\n\s*memoryLimitMb: this\.config\.memoryMb/);
});

test("runOnHost reads the signal it is handed", () => {
  // The bug in one line: `child.on("close", (code) => ...)` threw away the
  // second argument, which was the only thing that explained the row.
  assert.match(RUNTIME_TS, /child\.on\("close",\s*\(code,\s*signal\)/);
  assert.ok(
    !/child\.on\("close",\s*\(code\)\s*=>\s*\{\s*\n\s*if \(settled\) return;\s*\n\s*settled = true;\s*\n\s*clearTimeout\(timer\);\s*\n\s*resolve\(\{ exitCode: code, stdout/.test(RUNTIME_TS),
    "the signal-discarding close handler is gone",
  );
});

// ── the real thing, not a fixture ───────────────────────────────────────
test("end to end: a SIGKILLed command produces a real diagnosis", async () => {
  // Drives a real child process to the same state a kernel OOM kill produces
  // — SIGKILL, unblockable, no chance to write anything — and runs the real
  // diagnosis over what the close handler is actually handed. A hand-built
  // {exitCode: null, signal: "SIGKILL"} object would prove the function and
  // not the platform.
  const observed = await new Promise<{ code: number | null; signal: NodeJS.Signals | null }>(
    (resolve, reject) => {
      const child = spawn("/bin/sh", ["-lc", "printf 'read 4000 rows\\n'; kill -9 $$"], {
        stdio: ["ignore", "pipe", "pipe"],
      });
      child.stdout.resume();
      child.stderr.resume();
      child.on("error", reject);
      child.on("close", (code, signal) => resolve({ code, signal }));
    },
  );

  assert.equal(observed.code, null, "a signal death reports no exit code at all");
  assert.equal(observed.signal, "SIGKILL");

  const d = diagnoseKill({
    exitCode: observed.code,
    signal: observed.signal,
    timedOut: false,
    isolation: "host",
  });
  assert.ok(d, "the thing that used to reach the agent as exit_code:null and nothing else");
  assert.equal(d.reason, "killed_by_the_system");
});

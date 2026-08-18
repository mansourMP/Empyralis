import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

import { execFileWithTimeout } from "../shell/exec-file-with-timeout";

// ---------------------------------------------------------------------------
// RESOURCE-LEAK REGRESSION: a probe timeout that did not actually time out.
//
// `child_process.execFile`'s `timeout` option is not a timeout. At the deadline
// Node sends `killSignal` (SIGTERM) exactly once and never escalates, and the
// execFile CALLBACK still only fires on the child's 'close'. So for any child
// that does not die on SIGTERM:
//
//   - the promise wrapping execFile NEVER settles, and
//   - the child's ProcessWrap plus its stdio PipeWraps stay refcounted on the
//     event loop for the rest of the process's life.
//
// Not theoretical. `docker info` on macOS ignores SIGTERM while waiting on an
// unresponsive Docker Desktop socket. health/service-inventory.ts probes Docker
// at boot (index.ts), on every shell.execute (shell/runtime.ts's isDockerReady)
// and from the heartbeat (cloud/ws-client.ts) -- and its 60s result cache is
// only WRITTEN after the probe resolves, so a wedged probe also means the cache
// never fills and the next caller spawns yet another immortal child. A gateway
// that had been up three days was found holding four of them, with fifty more
// reparented to init from earlier gateway processes; the oldest was over a day
// old. The same hang is why `node --test` stopped exiting.
//
// The three execFile call sites now go through shell/exec-file-with-timeout.ts,
// which owns the deadline itself and escalates SIGTERM -> SIGKILL.
// ---------------------------------------------------------------------------

/** A child that installs a no-op SIGTERM handler and then refuses to exit --
 *  exactly the shape `docker info` presents to a probe. */
const SIGTERM_DEAF_CHILD = ["-e", "process.on('SIGTERM', () => {}); setInterval(() => {}, 1000);"];

function pidAlive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

/** Direct children of this process, read from the OS rather than tracked in JS,
 *  so a child the helper deliberately abandoned is still visible. */
function ownChildPids(): number[] {
  try {
    return execFileSync("ps", ["-eo", "pid,ppid"], { encoding: "utf8" })
      .split("\n")
      .slice(1)
      .map((line) => line.trim().split(/\s+/))
      .filter((cols) => cols.length >= 2 && Number(cols[1]) === process.pid)
      .map((cols) => Number(cols[0]))
      .filter((pid) => Number.isInteger(pid));
  } catch {
    return [];
  }
}

async function waitForPidToDie(pid: number, timeoutMs: number): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (!pidAlive(pid)) {
      return true;
    }
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  return !pidAlive(pid);
}

test("execFileWithTimeout settles at its OWN deadline, even when the child ignores SIGTERM", async () => {
  const started = Date.now();
  const NEVER = Symbol("never-settled");
  const outcome = await Promise.race([
    execFileWithTimeout(process.execPath, SIGTERM_DEAF_CHILD, 700, {}, 300),
    // Generous slack, but finite: with execFile's own `timeout` option this
    // promise never settles at all, and this race is what turns "the whole
    // suite hangs forever" into a readable failed assertion.
    new Promise<typeof NEVER>((resolve) => {
      setTimeout(() => resolve(NEVER), 6_000).unref();
    }),
  ]);

  assert.notStrictEqual(
    outcome,
    NEVER,
    "execFileWithTimeout must resolve at its own deadline; whether the child ever dies is the child's business",
  );
  const result = outcome as Awaited<ReturnType<typeof execFileWithTimeout>>;
  assert.equal(result.timedOut, true);
  assert.equal(result.exitCode, null);
  assert.ok(
    Date.now() - started < 4_000,
    "the deadline must be honoured promptly, not whenever the child happens to exit",
  );
});

test("a SIGTERM-deaf child is escalated to SIGKILL, not left running for the process's lifetime", async () => {
  const before = new Set(ownChildPids());
  const result = await execFileWithTimeout(process.execPath, SIGTERM_DEAF_CHILD, 500, {}, 250);
  assert.equal(result.timedOut, true);

  const spawned = ownChildPids().filter((pid) => !before.has(pid));
  assert.ok(spawned.length > 0, "expected to observe the spawned child; the leak assertion below is meaningless otherwise");

  for (const pid of spawned) {
    assert.ok(
      await waitForPidToDie(pid, 5_000),
      `child pid ${pid} survived its timeout — one SIGTERM was never enough, which is the entire bug`,
    );
  }
});

test("a completed command still reports its real exit code, stdout and stderr", async () => {
  const ok = await execFileWithTimeout(
    process.execPath,
    ["-e", "process.stdout.write('hi'); process.stderr.write('warn');"],
    10_000,
  );
  assert.equal(ok.timedOut, false);
  assert.equal(ok.exitCode, 0);
  assert.equal(ok.stdout, "hi");
  assert.equal(ok.stderr, "warn");

  const failed = await execFileWithTimeout(process.execPath, ["-e", "process.exit(3);"], 10_000);
  assert.equal(failed.timedOut, false);
  assert.equal(failed.exitCode, 3);

  const missing = await execFileWithTimeout("empyralis-no-such-binary-ever", [], 10_000);
  assert.equal(missing.timedOut, false);
  assert.equal(missing.error?.code, "ENOENT");
});

test("no gateway module reintroduces execFile's own `timeout` option", () => {
  // A behavioural test cannot catch this reintroduction: passing `timeout`
  // looks correct, type-checks, and behaves fine against every child that dies
  // on SIGTERM. It only fails against a wedged one, in production, silently.
  // So this is a drift assertion in the same spirit as the local-bridge
  // channel-map ones — the option is banned in src/, and the one place allowed
  // to spawn against a deadline is exec-file-with-timeout.ts.
  // Resolve the real SOURCE tree, never `__dirname/..`. `npm test` runs the
  // COMPILED suite from dist/__tests__, so `__dirname/..` is dist/ — which
  // holds only .js and is skipped by the `.ts` filter below, meaning this
  // scan examined ZERO files and passed unconditionally. It had been inert
  // since it was written; CLAUDE.md meanwhile cited it as the guard banning
  // raw execFile timeouts (the ~50 immortal-process leak). A drift test that
  // cannot fail is worse than none: it reports the rule as enforced.
  // The canary below fails loudly if this root ever stops holding sources.
  const srcDir = (() => {
    for (const candidate of [
      path.resolve(__dirname, ".."),                  // running from src/
      path.resolve(__dirname, "..", "..", "src"),     // running from dist/__tests__
    ]) {
      if (fs.existsSync(path.join(candidate, "shell", "exec-file-with-timeout.ts"))) {
        return candidate;
      }
    }
    throw new Error(
      "exec-file drift scan could not locate the TypeScript source tree — "
      + "the guard would silently scan nothing. Fix the resolver, do not delete this test.",
    );
  })();
  const allowed = path.join(srcDir, "shell", "exec-file-with-timeout.ts");
  const offenders: string[] = [];

  const walk = (dir: string): void => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        if (entry.name === "node_modules") continue;
        walk(full);
        continue;
      }
      if (!entry.name.endsWith(".ts") || full === allowed) continue;
      // Strip comments BEFORE matching. This file's own siblings document the
      // old bad pattern in prose ("this used to be execFileSync(..., { timeout:
      // 3_000 })"), and a scanner that reads prose flags the very comment
      // explaining the fix — a false positive that trains people to delete the
      // guard. A drift test must scan CODE.
      const source = fs
        .readFileSync(full, "utf8")
        .replace(/\/\*[\s\S]*?\*\//g, "")
        .replace(/(^|[^:])\/\/.*$/gm, "$1");
      if (!/\bexecFile(Sync)?\s*\(/.test(source)) continue;
      // Only the options-object form matters; `timeoutMs`/`timeoutSeconds`
      // parameters are this codebase's own naming and are not the trap.
      if (/(^|[^A-Za-z])timeout\s*:/m.test(source)) {
        offenders.push(path.relative(srcDir, full));
      }
    }
  };
  walk(srcDir);

  assert.deepEqual(
    offenders,
    [],
    "execFile's `timeout` option sends one SIGTERM, never escalates, and leaves the promise pending forever if "
      + "the child ignores it. Use execFileWithTimeout (shell/exec-file-with-timeout.ts). Offenders: "
      + offenders.join(", "),
  );
});

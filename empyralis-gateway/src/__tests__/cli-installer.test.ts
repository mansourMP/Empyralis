import test from "node:test";
import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import fs from "fs";
import os from "os";
import path from "path";

import {
  installCliSubscriptionRuntime,
  CliInstallError,
  type CliInstallChildProcessLike,
  type CliInstallSpawnImpl,
} from "../llm/cli-installer";

interface FakeChild {
  child: CliInstallChildProcessLike;
  emitStdout: (chunk: string) => void;
  emitStderr: (chunk: string) => void;
  emitClose: (code: number | null) => void;
  emitError: (err: NodeJS.ErrnoException) => void;
  killCalls: (NodeJS.Signals | undefined)[];
  /** When set, kill() schedules an async 'close' — simulating a real process
   *  that actually terminates in response to the signal. Without this, a
   *  fake child left running after kill() never resolves the caller's
   *  promise, since nothing else emits 'close'. */
  dieOnKill?: { code: number | null };
}

function makeFakeChild(): FakeChild {
  const stdout = new EventEmitter();
  const stderr = new EventEmitter();
  const proc = new EventEmitter();
  const killCalls: (NodeJS.Signals | undefined)[] = [];
  const fake: FakeChild = {
    child: {
      stdout: stdout as unknown as CliInstallChildProcessLike["stdout"],
      stderr: stderr as unknown as CliInstallChildProcessLike["stderr"],
      on: (event: string, listener: (...args: unknown[]) => void) => proc.on(event, listener),
      kill: (signal?: NodeJS.Signals) => {
        killCalls.push(signal);
        if (fake.dieOnKill) {
          setImmediate(() => proc.emit("close", fake.dieOnKill!.code));
        }
        return true;
      },
    } as unknown as CliInstallChildProcessLike,
    emitStdout: (chunk) => stdout.emit("data", chunk),
    emitStderr: (chunk) => stderr.emit("data", chunk),
    emitClose: (code) => proc.emit("close", code),
    emitError: (err) => proc.emit("error", err),
    killCalls,
  };
  return fake;
}

/** Returns a fresh fake child for each call (install does an `npm install`
 *  call, then a separate `npm ls` confirmation call) — a queue lets a test
 *  script each call's behavior independently. */
function spawnImplQueue(children: FakeChild[]): CliInstallSpawnImpl {
  let i = 0;
  return () => {
    const next = children[Math.min(i, children.length - 1)];
    i += 1;
    return next.child;
  };
}

function commandExistsAlways(binaryOnPath: Record<string, string | null> = {}) {
  return (command: string) => (command in binaryOnPath ? binaryOnPath[command] : `/usr/bin/${command}`);
}

/** installCliSubscriptionRuntime does TWO sequential spawns (npm install,
 *  then a confirmation npm ls) — the second spawnImpl call only happens on
 *  the microtask AFTER the first spawnAndCollect's promise resolves, so a
 *  test driving both fake children must yield once in between, or the
 *  second fake's emit* calls fire before its listeners are even attached. */
function flushMicrotasks(): Promise<void> {
  return new Promise((resolve) => setImmediate(resolve));
}

test("throws npm_missing before spawning anything when npm is not on PATH", async () => {
  let spawned = false;
  const promise = installCliSubscriptionRuntime(
    { runtime: "claude_code", timeoutMs: 5_000 },
    {
      spawnImpl: () => {
        spawned = true;
        throw new Error("should not be called");
      },
      commandExists: () => null,
      env: { PATH: "/usr/bin" },
    },
  );
  await assert.rejects(promise, (err: unknown) => {
    assert.ok(err instanceof CliInstallError);
    assert.equal(err.kind, "npm_missing");
    return true;
  });
  assert.equal(spawned, false, "must not spawn when the npm preflight already fails");
});

test("successful install: npm install exits 0, binary confirmed on PATH, version parsed", async () => {
  const installChild = makeFakeChild();
  const lsChild = makeFakeChild();
  const promise = installCliSubscriptionRuntime(
    { runtime: "claude_code", timeoutMs: 5_000 },
    {
      spawnImpl: spawnImplQueue([installChild, lsChild]),
      commandExists: commandExistsAlways(),
      env: { PATH: "/usr/bin" },
    },
  );
  installChild.emitClose(0);
  await flushMicrotasks();
  lsChild.emitStdout("+-- @anthropic-ai/claude-code@2.1.205\n");
  lsChild.emitClose(0);
  const result = await promise;
  assert.equal(result.installed, true);
  assert.equal(result.runtime, "claude_code");
  assert.equal(result.package, "@anthropic-ai/claude-code");
  assert.equal(result.version, "2.1.205");
});

test("codex install uses the codex package and binary", async () => {
  const installChild = makeFakeChild();
  const lsChild = makeFakeChild();
  const promise = installCliSubscriptionRuntime(
    { runtime: "codex", timeoutMs: 5_000 },
    {
      spawnImpl: spawnImplQueue([installChild, lsChild]),
      commandExists: commandExistsAlways(),
      env: { PATH: "/usr/bin" },
    },
  );
  installChild.emitClose(0);
  await flushMicrotasks();
  lsChild.emitClose(0);
  const result = await promise;
  assert.equal(result.package, "@openai/codex");
});

test("classifies EACCES stderr as permission_denied, not a generic crash", async () => {
  const installChild = makeFakeChild();
  const promise = installCliSubscriptionRuntime(
    { runtime: "claude_code", timeoutMs: 5_000 },
    {
      spawnImpl: spawnImplQueue([installChild]),
      commandExists: commandExistsAlways(),
      env: { PATH: "/usr/bin" },
    },
  );
  installChild.emitStderr("npm ERR! code EACCES\nnpm ERR! syscall mkdir\n");
  installChild.emitClose(1);
  await assert.rejects(promise, (err: unknown) => {
    assert.ok(err instanceof CliInstallError);
    assert.equal(err.kind, "permission_denied");
    return true;
  });
});

test("classifies a network failure distinctly from a generic crash", async () => {
  const installChild = makeFakeChild();
  const promise = installCliSubscriptionRuntime(
    { runtime: "codex", timeoutMs: 5_000 },
    {
      spawnImpl: spawnImplQueue([installChild]),
      commandExists: commandExistsAlways(),
      env: { PATH: "/usr/bin" },
    },
  );
  installChild.emitStderr("npm ERR! network ETIMEDOUT\n");
  installChild.emitClose(1);
  await assert.rejects(promise, (err: unknown) => {
    assert.ok(err instanceof CliInstallError);
    assert.equal(err.kind, "network_error");
    return true;
  });
});

test("times out and force-kills a hung npm install", async () => {
  const installChild = makeFakeChild();
  installChild.dieOnKill = { code: null };
  const promise = installCliSubscriptionRuntime(
    { runtime: "claude_code", timeoutMs: 10 },
    {
      spawnImpl: spawnImplQueue([installChild]),
      commandExists: commandExistsAlways(),
      env: { PATH: "/usr/bin" },
    },
  );
  await assert.rejects(promise, (err: unknown) => {
    assert.ok(err instanceof CliInstallError);
    assert.equal(err.kind, "timeout");
    return true;
  });
  assert.ok(installChild.killCalls.includes("SIGTERM"));
});

test("exit 0 but binary still missing from PATH is reported honestly, not silently accepted", async () => {
  const installChild = makeFakeChild();
  const lsChild = makeFakeChild();
  const promise = installCliSubscriptionRuntime(
    { runtime: "claude_code", timeoutMs: 5_000 },
    {
      spawnImpl: spawnImplQueue([installChild, lsChild]),
      // "claude" never resolves, even after a reported-successful install.
      commandExists: commandExistsAlways({ claude: null }),
      env: { PATH: "/usr/bin" },
    },
  );
  installChild.emitClose(0);
  await flushMicrotasks();
  lsChild.emitClose(0);
  await assert.rejects(promise, (err: unknown) => {
    assert.ok(err instanceof CliInstallError);
    assert.equal(err.kind, "crash");
    assert.match(err.message, /not on PATH/);
    return true;
  });
});

test("ENOENT spawn error is reported as npm_missing, not a generic crash", async () => {
  const installChild = makeFakeChild();
  const promise = installCliSubscriptionRuntime(
    { runtime: "claude_code", timeoutMs: 5_000 },
    {
      spawnImpl: spawnImplQueue([installChild]),
      commandExists: commandExistsAlways(),
      env: { PATH: "/usr/bin" },
    },
  );
  const enoent = new Error("spawn npm ENOENT") as NodeJS.ErrnoException;
  enoent.code = "ENOENT";
  installChild.emitError(enoent);
  await assert.rejects(promise, (err: unknown) => {
    assert.ok(err instanceof CliInstallError);
    assert.equal(err.kind, "npm_missing");
    return true;
  });
});

test("real detection: npm preflight and post-install verification both find binaries in ~/.local/bin even when not on PATH", async (t) => {
  // commandExists is deliberately NOT overridden below — this exercises the
  // real, production defaultCommandExists()/resolveCommandPath() fallback-dir
  // logic (shared with health/service-inventory.ts's passive detection and
  // llm/cli-login-session.ts's sign-in spawn), not a test double of it.
  // Without this fix, `npm install -g` could genuinely succeed and still be
  // reported back to the user as a crash ("still not on PATH afterward")
  // purely because the freshly-linked binary landed in ~/.local/bin, which
  // the Gateway's own process PATH never included.
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "empyralis-cli-installer-detect-"));
  t.after(() => fs.rmSync(home, { recursive: true, force: true }));
  const localBin = path.join(home, ".local", "bin");
  fs.mkdirSync(localBin, { recursive: true });
  fs.writeFileSync(path.join(localBin, "npm"), "#!/bin/sh\necho fake-npm\n", { mode: 0o755 });
  fs.writeFileSync(path.join(localBin, "claude"), "#!/bin/sh\necho fake-claude\n", { mode: 0o755 });

  const installChild = makeFakeChild();
  const lsChild = makeFakeChild();
  const promise = installCliSubscriptionRuntime(
    { runtime: "claude_code", timeoutMs: 5_000 },
    {
      spawnImpl: spawnImplQueue([installChild, lsChild]),
      // Deliberately narrow — does NOT include localBin — reproducing the
      // real Gateway-process-PATH gap, not a full interactive shell PATH.
      env: { HOME: home, PATH: "/usr/bin:/bin" },
    },
  );
  installChild.emitClose(0);
  await flushMicrotasks();
  lsChild.emitStdout("+-- @anthropic-ai/claude-code@2.1.205\n");
  lsChild.emitClose(0);
  const result = await promise;
  assert.equal(result.installed, true);
  assert.equal(result.version, "2.1.205");
});

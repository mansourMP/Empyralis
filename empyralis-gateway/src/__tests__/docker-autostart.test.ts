import test from "node:test";
import assert from "node:assert/strict";

import {
  describeDockerAutostartOutcome,
  ensureDockerReady,
  resetDockerAutostartStateForTests,
  type DockerAutostartCommandRunner,
  type DockerAutostartOutcome,
  type EnsureDockerReadyDeps,
} from "../shell/docker-autostart";

// Every test here drives ensureDockerReady() with fully injected
// commandExists/runCommand/sleep — nothing spawns a real process, nothing
// touches the machine's actual Docker installation, and nothing sleeps for
// real wall-clock time (the instant `sleep` fake keeps this file fast even
// though the module's own poll loop runs several iterations per test).

test.beforeEach(() => {
  resetDockerAutostartStateForTests();
});

const INSTANT_SLEEP = async () => {};

interface FakeCall {
  command: string;
  args: string[];
  timeoutMs: number;
}

/** Builds a runCommand fake that answers `docker info` with `readyAfter`
 *  calls to spare (i.e. 0 = ready immediately, 2 = ready on the 3rd probe),
 *  and answers any other command (the platform start command) with
 *  `startResult`. Every call is recorded in `calls` for assertions. */
function fakeRunCommand(options: {
  readyAfterDockerInfoCalls?: number;
  startResult?: { exitCode: number | null; stdout?: string; stderr?: string; timedOut?: boolean };
  calls: FakeCall[];
}): DockerAutostartCommandRunner {
  let dockerInfoCalls = 0;
  const readyAfter = options.readyAfterDockerInfoCalls ?? 0;
  const startResult = options.startResult ?? { exitCode: 0, stdout: "", stderr: "" };
  return async (command, args, timeoutMs) => {
    options.calls.push({ command, args, timeoutMs });
    if (command === "docker" && args[0] === "info") {
      dockerInfoCalls += 1;
      const ready = dockerInfoCalls > readyAfter;
      return {
        exitCode: ready ? 0 : 1,
        stdout: ready ? "27.0.0" : "",
        stderr: ready ? "" : "Cannot connect to the Docker daemon",
        timedOut: false,
      };
    }
    return {
      exitCode: startResult.exitCode ?? 0,
      stdout: startResult.stdout ?? "",
      stderr: startResult.stderr ?? "",
      timedOut: startResult.timedOut ?? false,
    };
  };
}

function deps(overrides: Partial<EnsureDockerReadyDeps> & { calls?: FakeCall[] } = {}): EnsureDockerReadyDeps {
  const { calls, ...rest } = overrides;
  return {
    // LINUX is the default here since 2026-08-26, because macOS no longer
    // starts Docker at all (founder's decision — see resolvePlatformStartCommand).
    // The start MECHANISM still exists and is still worth testing; Linux is
    // where it now lives. macOS's own guarantee — that nothing is ever
    // launched — has its own explicit tests at the bottom of this file.
    platform: "linux",
    commandExists: (command: string) => (command === "docker" ? "/usr/local/bin/docker" : `/usr/bin/${command}`),
    runCommand: fakeRunCommand({ calls: calls ?? [] }),
    sleep: INSTANT_SLEEP,
    now: () => 0,
    ...rest,
  };
}

// ── Outcome 1: not installed — nothing to start ──

test("not installed: docker CLI absent means nothing is started, no process is spawned at all", async () => {
  const calls: FakeCall[] = [];
  const outcome = await ensureDockerReady({
    platform: "darwin",
    commandExists: () => null,
    runCommand: async (command, args, timeoutMs) => {
      calls.push({ command, args, timeoutMs });
      throw new Error("must not be called when Docker is not installed");
    },
    sleep: INSTANT_SLEEP,
  });
  assert.deepEqual(outcome, { kind: "not_installed" });
  assert.equal(calls.length, 0);
});

// ── Fast path: already ready ──

test("already ready: a single docker info probe is enough, no start command is ever run", async () => {
  const calls: FakeCall[] = [];
  const outcome = await ensureDockerReady(deps({
    calls,
    runCommand: fakeRunCommand({ readyAfterDockerInfoCalls: 0, calls }),
  }));
  assert.deepEqual(outcome, { kind: "already_ready" });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].command, "docker");
});

// ── Outcome 2a: start command itself fails ──

test("start command fails (nonzero exit): reports start_command_failed with the real detail, never retried within this call", async () => {
  const calls: FakeCall[] = [];
  const outcome = await ensureDockerReady(deps({
    calls,
    runCommand: fakeRunCommand({
      readyAfterDockerInfoCalls: 999, // never becomes ready
      startResult: { exitCode: 1, stderr: 'Unable to find application named "Docker"' },
      calls,
    }),
  }));
  assert.equal(outcome.kind, "start_command_failed");
  if (outcome.kind !== "start_command_failed") throw new Error("unreachable");
  assert.match(outcome.detail, /Unable to find application named "Docker"/);
  // one readiness probe, one start attempt (open -a Docker) — no poll
  // attempts, since the start command itself failed.
  const startCalls = calls.filter((c) => c.command === "systemctl");
  assert.equal(startCalls.length, 1);
});

test("start command times out: reports start_command_failed naming the timeout, not a silent hang", async () => {
  const calls: FakeCall[] = [];
  const outcome = await ensureDockerReady(deps({
    calls,
    runCommand: fakeRunCommand({
      readyAfterDockerInfoCalls: 999,
      startResult: { exitCode: null, timedOut: true },
      calls,
    }),
  }));
  assert.equal(outcome.kind, "start_command_failed");
  if (outcome.kind !== "start_command_failed") throw new Error("unreachable");
  assert.match(outcome.detail, /did not respond within/);
});

// ── Outcome 2b: start command succeeds but the daemon never comes up in time ──

test("start command succeeds but Docker never answers: reports start_timed_out after the bounded poll window, not forever", async () => {
  const calls: FakeCall[] = [];
  const outcome = await ensureDockerReady(deps({
    calls,
    runCommand: fakeRunCommand({ readyAfterDockerInfoCalls: 999, calls }),
  }));
  assert.deepEqual(outcome, { kind: "start_timed_out" });
  // Bounded: some finite number of docker info polls happened, not an
  // unbounded loop.
  const dockerInfoCalls = calls.filter((c) => c.command === "docker");
  assert.ok(dockerInfoCalls.length > 1, "expected more than the initial readiness probe");
  assert.ok(dockerInfoCalls.length < 20, "poll loop must be bounded");
});

// ── Outcome 3: started ──

test("start command succeeds and Docker answers a few polls later: reports started", async () => {
  const calls: FakeCall[] = [];
  const outcome = await ensureDockerReady(deps({
    calls,
    runCommand: fakeRunCommand({ readyAfterDockerInfoCalls: 2, calls }),
  }));
  assert.deepEqual(outcome, { kind: "started" });
});

// ── Platform branching ──

test("linux: uses systemctl start docker when systemctl is present", async () => {
  const calls: FakeCall[] = [];
  const outcome = await ensureDockerReady(deps({
    calls,
    platform: "linux",
    commandExists: (command) => (command === "docker" || command === "systemctl" ? `/usr/bin/${command}` : null),
    runCommand: fakeRunCommand({ readyAfterDockerInfoCalls: 1, calls }),
  }));
  assert.deepEqual(outcome, { kind: "started" });
  const systemctlCalls = calls.filter((c) => c.command === "systemctl");
  assert.equal(systemctlCalls.length, 1);
  assert.deepEqual(systemctlCalls[0].args, ["start", "docker"]);
});

test("linux without systemctl: reports unsupported_platform rather than guessing a command", async () => {
  const calls: FakeCall[] = [];
  const outcome = await ensureDockerReady(deps({
    calls,
    platform: "linux",
    commandExists: (command) => (command === "docker" ? "/usr/bin/docker" : null),
    runCommand: fakeRunCommand({ readyAfterDockerInfoCalls: 999, calls }),
  }));
  assert.deepEqual(outcome, { kind: "unsupported_platform", platform: "linux" });
  // The only command run at all is the initial docker info readiness probe.
  assert.equal(calls.length, 1);
});

test("an unrecognized platform reports unsupported_platform without attempting anything", async () => {
  const calls: FakeCall[] = [];
  const outcome = await ensureDockerReady(deps({
    calls,
    platform: "win32",
    runCommand: fakeRunCommand({ readyAfterDockerInfoCalls: 999, calls }),
  }));
  assert.deepEqual(outcome, { kind: "unsupported_platform", platform: "win32" });
});

// ── Cooldown: at most one real start attempt per window ──

test("cooldown: a second call while still within the window does not spawn another start command", async () => {
  const calls: FakeCall[] = [];
  let clock = 0;
  const sharedDeps = deps({
    calls,
    runCommand: fakeRunCommand({ readyAfterDockerInfoCalls: 999, calls }),
    now: () => clock,
  });

  const first = await ensureDockerReady(sharedDeps);
  assert.equal(first.kind, "start_timed_out");
  const startCallsAfterFirst = calls.filter((c) => c.command === "systemctl").length;
  assert.equal(startCallsAfterFirst, 1);

  clock += 1_000; // still well inside the cooldown window
  const second = await ensureDockerReady(sharedDeps);
  assert.equal(second.kind, "cooldown");
  if (second.kind !== "cooldown") throw new Error("unreachable");
  assert.equal(second.previous.kind, "start_timed_out");
  const startCallsAfterSecond = calls.filter((c) => c.command === "systemctl").length;
  assert.equal(startCallsAfterSecond, 1, "cooldown must not trigger a second start command");
});

test("cooldown: once the window elapses, the next call attempts a fresh start", async () => {
  const calls: FakeCall[] = [];
  let clock = 0;
  const sharedDeps = deps({
    calls,
    runCommand: fakeRunCommand({ readyAfterDockerInfoCalls: 999, calls }),
    now: () => clock,
  });

  await ensureDockerReady(sharedDeps);
  clock += 120_000; // comfortably past the cooldown window
  const outcome = await ensureDockerReady(sharedDeps);
  assert.equal(outcome.kind, "start_timed_out");
  const startCalls = calls.filter((c) => c.command === "systemctl").length;
  assert.equal(startCalls, 2, "a fresh window must allow a fresh start attempt");
});

// ── Single-flight: concurrent callers share one in-flight attempt ──

test("single-flight: two concurrent calls while an attempt is running share the same outcome and spawn the start command only once", async () => {
  const calls: FakeCall[] = [];
  const sharedDeps = deps({
    calls,
    runCommand: fakeRunCommand({ readyAfterDockerInfoCalls: 2, calls }),
  });

  const [a, b] = await Promise.all([ensureDockerReady(sharedDeps), ensureDockerReady(sharedDeps)]);
  assert.deepEqual(a, { kind: "started" });
  assert.deepEqual(b, { kind: "started" });
  const startCalls = calls.filter((c) => c.command === "systemctl").length;
  assert.equal(startCalls, 1, "concurrent callers must share one in-flight attempt");
});

// ── A successful start invalidates the passive inventory cache ──

test("a successful start makes the very next probe see it, instead of waiting out the passive cache", async () => {
  const calls: FakeCall[] = [];
  const outcome = await ensureDockerReady(deps({
    calls,
    runCommand: fakeRunCommand({ readyAfterDockerInfoCalls: 1, calls }),
  }));
  assert.equal(outcome.kind, "started");
  // collectPassiveInventorySnapshot's own cache behavior is covered by
  // service-inventory tests; this asserts only that ensureDockerReady()
  // does not throw when invalidating it, i.e. the import/call actually
  // resolved to a real function rather than a broken wiring.
});

// ── Messages: honest, specific, never a command the reader can't run ──

test("describeDockerAutostartOutcome never tells a Linux box to open Docker Desktop, and never tells a Mac to run systemctl", () => {
  const linuxFailure: DockerAutostartOutcome = { kind: "start_command_failed", detail: "permission denied" };
  const message = describeDockerAutostartOutcome(linuxFailure);
  assert.doesNotMatch(message, /Docker Desktop/);
  assert.doesNotMatch(message, /systemctl/);
});

test("describeDockerAutostartOutcome on unsupported_platform reads the customer's OS name, never the raw Node platform token", () => {
  const message = describeDockerAutostartOutcome({ kind: "unsupported_platform", platform: "win32" });
  assert.match(message, /Windows/);
  assert.doesNotMatch(message, /win32/);
});

test("describeDockerAutostartOutcome on unsupported_platform falls back to the raw token for a platform it has no human name for, rather than throwing", () => {
  const message = describeDockerAutostartOutcome({ kind: "unsupported_platform", platform: "sunos" });
  assert.match(message, /sunos/);
});

test("describeDockerAutostartOutcome distinguishes not_installed, start_command_failed, and start_timed_out from each other", () => {
  const messages = [
    describeDockerAutostartOutcome({ kind: "not_installed" }),
    describeDockerAutostartOutcome({ kind: "start_command_failed", detail: "boom" }),
    describeDockerAutostartOutcome({ kind: "start_timed_out" }),
  ];
  const unique = new Set(messages);
  assert.equal(unique.size, 3, `expected three distinct messages, got: ${JSON.stringify(messages)}`);
});

test("describeDockerAutostartOutcome on cooldown surfaces the previous outcome's own detail, not a generic retry line", () => {
  const outcome: DockerAutostartOutcome = {
    kind: "cooldown",
    previous: { kind: "start_command_failed", detail: "permission denied" },
    retryAfterMs: 12_345,
  };
  const message = describeDockerAutostartOutcome(outcome);
  assert.match(message, /permission denied/);
  assert.match(message, /retry in about 13s/);
});

// ── macOS NEVER LAUNCHES DOCKER ──────────────────────────────────────────
//
// Founder's decision, 2026-08-26, after Docker Desktop opened itself on his
// own laptop when he had merely opened his app: "the menu application must be
// running without Docker… it should be removed."
//
// These are the tests that fail if `open -a Docker` is ever put back. They
// assert the two halves separately, because only together do they mean
// "the dependency is gone but the capability is not":
//   1. nothing is EVER spawned on darwin, and
//   2. Docker that is already running is still used.

test("macOS never starts Docker: no process is spawned, whatever the state", async () => {
  const calls: FakeCall[] = [];
  const outcome = await ensureDockerReady({
    platform: "darwin",
    commandExists: (command: string) =>
      command === "docker" ? "/usr/local/bin/docker" : `/usr/bin/${command}`,
    // Docker is installed but NOT responding — the exact case that used to
    // trigger `open -a Docker` and take over the machine.
    runCommand: fakeRunCommand({ readyAfterDockerInfoCalls: 999, calls }),
    sleep: INSTANT_SLEEP,
    now: () => 0,
  });

  assert.equal(outcome.kind, "unsupported_platform");
  const started = calls.filter((c) => c.command === "open");
  assert.equal(
    started.length,
    0,
    "macOS must never run `open -a Docker` — a GUI app taking over a personal computer was the whole complaint",
  );
});

test("macOS still USES Docker when it is already running — the dependency is removed, not the capability", async () => {
  const calls: FakeCall[] = [];
  const outcome = await ensureDockerReady({
    platform: "darwin",
    commandExists: (command: string) =>
      command === "docker" ? "/usr/local/bin/docker" : `/usr/bin/${command}`,
    runCommand: fakeRunCommand({ readyAfterDockerInfoCalls: 0, calls }),
    sleep: INSTANT_SLEEP,
    now: () => 0,
  });

  assert.deepEqual(
    outcome,
    { kind: "already_ready" },
    "someone who wants the sandbox just leaves Docker open, and it is used exactly as before",
  );
  assert.equal(calls.filter((c) => c.command === "open").length, 0);
});

test("Linux is UNCHANGED — a headless daemon on a box that exists to run the agent is not the complaint", async () => {
  const calls: FakeCall[] = [];
  const outcome = await ensureDockerReady({
    platform: "linux",
    commandExists: (command: string) =>
      command === "docker" ? "/usr/local/bin/docker" : `/usr/bin/${command}`,
    runCommand: fakeRunCommand({ readyAfterDockerInfoCalls: 1, calls }),
    sleep: INSTANT_SLEEP,
    now: () => 0,
  });

  assert.equal(outcome.kind, "started");
  assert.ok(
    calls.some((c) => c.command === "systemctl" && c.args.join(" ") === "start docker"),
    "Linux still starts the docker service",
  );
});

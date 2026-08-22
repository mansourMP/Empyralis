import test from "node:test";
import assert from "node:assert/strict";
import fs from "fs";
import os from "os";
import path from "path";

import { execFileWithTimeout } from "../shell/exec-file-with-timeout";
import { GatewayShellRuntime, resolveExecutionMode, type GatewayShellRuntimeConfig } from "../shell/runtime";
import type { DockerAutostartOutcome } from "../shell/docker-autostart";
import type { GatewayRequestEnvelope, GatewayToolInvokePayload } from "../protocol/types";

let dockerAvailability: Promise<boolean> | null = null;

/**
 * This gate used to be `execFileSync("docker", ["info", ...], { timeout: 3_000 })`,
 * and it is why this file hung forever after printing every passing assertion.
 *
 * spawnSync's `timeout` sends killSignal (SIGTERM) once and then goes back to
 * WAITING for the child to exit — it never escalates. `docker info` against a
 * wedged Docker Desktop socket ignores SIGTERM, so execFileSync blocked the
 * whole process synchronously: no timer fired, no handle dump was reachable,
 * and even `process.getActiveResourcesInfo()` was unobservable because the
 * event loop never turned again. The async sibling of the same mistake is the
 * production leak fixed in shell/exec-file-with-timeout.ts.
 *
 * Memoized: the probe is a per-machine fact, so paying it once beats paying it
 * per test, and the result is identical.
 */
function realDockerAvailable(): Promise<boolean> {
  dockerAvailability ??= execFileWithTimeout("docker", ["info", "--format", "{{.ServerVersion}}"], 3_000)
    .then((result) => !result.timedOut && !result.error && result.exitCode === 0);
  return dockerAvailability;
}

const SAGE_AUTHORIZED_POLICY = {
  runtime_access_mode: "full_access",
  empyralis_approved: true,
  agent_scope: "sage",
  policy: { mode: "full_access", full_access_warning_acknowledged: true },
};

// Every test in this file that reaches the sandbox-mode Docker check must
// never spawn a real "open -a Docker" / "systemctl start docker" — that
// would touch the machine running the suite (forbidden — see docker-
// autostart.test.ts for the real, injected-fake coverage of that module).
// baseConfig() therefore always supplies a fake dockerAutostart that never
// runs a real process; individual tests below override it to exercise the
// specific outcome they care about.
const NEVER_STARTS_DOCKER: () => Promise<DockerAutostartOutcome> = async () => ({ kind: "not_installed" });

function baseConfig(overrides: Partial<GatewayShellRuntimeConfig> = {}): GatewayShellRuntimeConfig {
  return {
    stateDir: fs.mkdtempSync(path.join(os.tmpdir(), "empyralis-shell-runtime-test-")),
    fullAccessLocallyEnabled: false,
    dockerAutostart: NEVER_STARTS_DOCKER,
    ...overrides,
  };
}

function makeInvokeFrame(
  capabilityId: string,
  args: Record<string, unknown>,
  extra: Partial<GatewayToolInvokePayload> = {},
): GatewayRequestEnvelope<GatewayToolInvokePayload> {
  return {
    kind: "request",
    id: "req-1",
    type: "tool.invoke",
    ts: new Date().toISOString(),
    payload: {
      capability_id: capabilityId,
      arguments: args,
      run_id: "run-1",
      trace_id: "trace-1",
      workspace_id: "ws-1",
      ...extra,
    },
  };
}

// ── resolveExecutionMode: pure logic, no I/O ──

test("resolveExecutionMode defaults to sandbox when full_access is not locally enabled", () => {
  const decision = resolveExecutionMode(
    { capability_id: "shell.execute", arguments: {}, run_id: "r", trace_id: "t", workspace_id: "w", ...SAGE_AUTHORIZED_POLICY },
    { stateDir: "/tmp", fullAccessLocallyEnabled: false },
  );
  assert.equal(decision.mode, "sandbox");
});

test("resolveExecutionMode defaults to sandbox when locally enabled but server did not authorize", () => {
  const decision = resolveExecutionMode(
    { capability_id: "shell.execute", arguments: {}, run_id: "r", trace_id: "t", workspace_id: "w" },
    { stateDir: "/tmp", fullAccessLocallyEnabled: true },
  );
  assert.equal(decision.mode, "sandbox");
});

test("resolveExecutionMode requires agent_scope to be exactly sage", () => {
  const decision = resolveExecutionMode(
    {
      capability_id: "shell.execute",
      arguments: {},
      run_id: "r",
      trace_id: "t",
      workspace_id: "w",
      runtime_access_mode: "full_access",
      empyralis_approved: true,
      agent_scope: "studio_agent",
      policy: { mode: "full_access", full_access_warning_acknowledged: true },
    },
    { stateDir: "/tmp", fullAccessLocallyEnabled: true },
  );
  assert.equal(decision.mode, "sandbox");
});

test("resolveExecutionMode requires the full_access_warning_acknowledged flag", () => {
  const decision = resolveExecutionMode(
    {
      capability_id: "shell.execute",
      arguments: {},
      run_id: "r",
      trace_id: "t",
      workspace_id: "w",
      runtime_access_mode: "full_access",
      empyralis_approved: true,
      agent_scope: "sage",
      policy: { mode: "full_access", full_access_warning_acknowledged: false },
    },
    { stateDir: "/tmp", fullAccessLocallyEnabled: true },
  );
  assert.equal(decision.mode, "sandbox");
});

test("resolveExecutionMode only resolves full_access when BOTH local opt-in AND full server authorization agree", () => {
  const decision = resolveExecutionMode(
    { capability_id: "shell.execute", arguments: {}, run_id: "r", trace_id: "t", workspace_id: "w", ...SAGE_AUTHORIZED_POLICY },
    { stateDir: "/tmp", fullAccessLocallyEnabled: true },
  );
  assert.equal(decision.mode, "full_access");
});

// ── Policy checks run before anything else, in every mode ──

test("hard-blocked commands are rejected before any Docker-readiness or execution-mode concern", async () => {
  const runtime = new GatewayShellRuntime(baseConfig());
  const frame = makeInvokeFrame("shell.execute", { command: "rm -rf /" });
  await assert.rejects(runtime.handleCapabilityInvoke(frame), /permanently blocked/);
});

test("hard-protected filesystem paths are rejected in sandbox mode before touching Docker", async () => {
  const runtime = new GatewayShellRuntime(baseConfig());
  const frame = makeInvokeFrame("filesystem.read_write", { path: "/etc/empyralis/config.json", mode: "read" });
  await assert.rejects(runtime.handleCapabilityInvoke(frame), /permanently protected/);
});

test("hard-protected filesystem paths are rejected in full_access mode too", async () => {
  const runtime = new GatewayShellRuntime(baseConfig({ fullAccessLocallyEnabled: true }));
  const frame = makeInvokeFrame(
    "filesystem.read_write",
    { path: "/etc/empyralis/config.json", mode: "write", content: "malicious" },
    SAGE_AUTHORIZED_POLICY,
  );
  await assert.rejects(runtime.handleCapabilityInvoke(frame), /permanently protected/);
});

// ── INVERTED 2026-08-22: no Docker means HOST, not "I can't" ──────────
// Four tests used to live here asserting that sandbox mode without a ready
// Docker daemon threw "requires Docker ... or an explicitly enabled and
// authorized full_access mode — neither is available", and treating that
// dead end as the safe outcome. The founder's ruling reverses it: "Docker is
// not something that is going to degrade what we do... not like 'this is
// impossible to run here' or 'because you don't have Docker I don't have any
// permission to run it'. I don't want to hear any of those things from my
// agent."
//
// They are turned around rather than deleted, so this file is now what fails
// if the wall comes back. Docker readiness is injected (never read from the
// machine running the suite) so these are deterministic; the real, non-mocked
// Docker integration test further below covers an actual daemon.

test("no Docker: the command actually RUNS, on the host, and returns real output", async () => {
  const runtime = new GatewayShellRuntime(baseConfig({ dockerReadyCheck: async () => false }));
  const frame = makeInvokeFrame("shell.execute", { command: "echo hello-from-host" });
  const result = await runtime.handleCapabilityInvoke(frame);
  assert.equal(result.exit_code, 0);
  assert.equal(result.stdout, "hello-from-host");
  assert.equal(result.timed_out, false);
});

test("no Docker: the run is labelled `host`, with the plain statement, and is NOT dressed up as full_access", async () => {
  const runtime = new GatewayShellRuntime(baseConfig({ dockerReadyCheck: async () => false }));
  const frame = makeInvokeFrame("shell.execute", { command: "echo hi" });
  const result = await runtime.handleCapabilityInvoke(frame);
  assert.equal(result.execution_mode, "host");
  assert.equal(result.isolation, "host");
  assert.equal(result.isolation_statement, "Commands run directly on this computer, because Docker isn't running here.");
  // A host run must never claim the full_access authorization nobody granted:
  // that token is what the control plane's own policy vocabulary keys on, and
  // fusing the two would make a real escalation unreadable in every log.
  assert.notEqual(result.execution_mode, "full_access");
  // No alarm: this is the ordinary state of a computer without Docker, not an
  // error and not a warning banner. `warning` stays exclusive to full_access.
  assert.equal(result.warning, undefined);
});

test("no Docker: the statement names no shell command, no install step, and no permission language", async () => {
  const runtime = new GatewayShellRuntime(baseConfig({ dockerReadyCheck: async () => false }));
  const frame = makeInvokeFrame("shell.execute", { command: "echo hi" });
  const result = await runtime.handleCapabilityInvoke(frame);
  const statement = String(result.isolation_statement);
  for (const forbidden of ["docker run", "brew ", "apt", "systemctl", "open -a", "install", "permission", "cannot", "can't run", "unable"]) {
    assert.ok(
      !statement.toLowerCase().includes(forbidden),
      `isolation_statement must not contain ${JSON.stringify(forbidden)}: ${statement}`,
    );
  }
});

test("Docker ready: the same call is labelled `sandbox`, so the two states are distinguishable from the result alone", async () => {
  // Only the LABELLING is asserted here — actually spawning a container is
  // the real-Docker integration test's job. resolveExecution() is the single
  // decider both paths go through (shell/execution-isolation.ts).
  const { resolveExecution } = await import("../shell/execution-isolation");
  const sandboxed = resolveExecution("sandbox", true);
  assert.equal(sandboxed.mode, "sandbox");
  assert.equal(sandboxed.isolation, "sandbox");
  assert.equal(sandboxed.statement, "Commands run isolated in a container on this computer.");
  const host = resolveExecution("sandbox", false);
  assert.notEqual(sandboxed.statement, host.statement);
});

test("filesystem.read_write without Docker also works, on the host, labelled honestly", async () => {
  const runtime = new GatewayShellRuntime(baseConfig({ dockerReadyCheck: async () => false }));
  const write = await runtime.handleCapabilityInvoke(
    makeInvokeFrame("filesystem.read_write", { path: "no-docker.txt", mode: "write", content: "written anyway" }),
  );
  assert.equal(write.execution_mode, "host");
  assert.equal(write.warning, undefined);
  const read = await runtime.handleCapabilityInvoke(
    makeInvokeFrame("filesystem.read_write", { path: "no-docker.txt", mode: "read" }),
  );
  assert.equal(read.content, "written anyway");
  assert.equal(read.execution_mode, "host");
});

test("no Docker does NOT bypass command policy — the hard-blocked list still refuses", async () => {
  // The one thing that must NOT have widened. Without a container there is no
  // second boundary left, so command-policy.ts is the whole of it.
  const runtime = new GatewayShellRuntime(baseConfig({ dockerReadyCheck: async () => false }));
  await assert.rejects(
    runtime.handleCapabilityInvoke(makeInvokeFrame("shell.execute", { command: "rm -rf /" })),
    /permanently blocked/,
  );
  await assert.rejects(
    runtime.handleCapabilityInvoke(
      makeInvokeFrame("filesystem.read_write", { path: "/etc/empyralis/config.json", mode: "read" }),
    ),
    /permanently protected/,
  );
});

// ── Docker autostart: still tried before concluding this box has no sandbox.
// These pin GatewayShellRuntime's own wiring of docker-autostart.ts's
// outcomes. What changed is where the outcome LANDS: it used to be the thrown
// refusal message, and is now the internal `reason` on a host run that
// succeeded. docker-autostart.test.ts covers the module's internal behavior
// (platform branching, cooldown, single-flight) against an injected command
// runner. Nothing here ever spawns a real process. ──

test("Docker not ready: an autostart attempt is still made, exactly once, before falling back to the host", async () => {
  let autostartCalls = 0;
  const runtime = new GatewayShellRuntime(baseConfig({
    dockerReadyCheck: async () => false,
    dockerAutostart: async () => {
      autostartCalls += 1;
      return { kind: "not_installed" };
    },
  }));
  const result = await runtime.handleCapabilityInvoke(makeInvokeFrame("shell.execute", { command: "echo hi" }));
  assert.equal(autostartCalls, 1, "the bounded autostart attempt must not be skipped just because host execution is available");
  assert.equal(result.execution_mode, "host");
});

test("Docker not ready but autostart SUCCEEDS: the call goes back to the sandbox path, not the host", async () => {
  let autostartCalls = 0;
  const runtime = new GatewayShellRuntime(baseConfig({
    dockerReadyCheck: async () => false,
    dockerAutostart: async () => {
      autostartCalls += 1;
      return { kind: "started" };
    },
  }));
  const frame = makeInvokeFrame("shell.execute", { command: "echo recovered" });
  try {
    const result = await runtime.handleCapabilityInvoke(frame);
    // A real `docker run` may or may not succeed on the machine executing
    // this suite; what this pins is that a box whose Docker was merely
    // asleep gets a CONTAINER, never quietly downgraded to a host run.
    assert.equal(result.execution_mode, "sandbox");
  } catch {
    // Downstream `docker run` failure is out of scope — the decision above
    // it is what this test owns.
  }
  assert.equal(autostartCalls, 1);
});

test("the autostart outcome detail survives as the internal reason, so a host run is still diagnosable", async () => {
  const { resolveExecution } = await import("../shell/execution-isolation");
  const notInstalled = resolveExecution("sandbox", false, "Docker is not installed on this computer, so there is nothing to start.");
  assert.match(notInstalled.reason, /Docker is not installed on this computer, so there is nothing to start/);
  const startFailed = resolveExecution("sandbox", false, 'Docker could not be started automatically (Unable to find application named "Docker")');
  assert.match(startFailed.reason, /could not be started automatically \(Unable to find application named "Docker"\)/);
  const timedOut = resolveExecution("sandbox", false, "Docker was asked to start and may still be starting up. Wait a bit and try again.");
  assert.match(timedOut.reason, /was asked to start and may still be starting up.*Wait a bit and try again/);
  // ...and it stays OUT of the customer-facing statement, which is one plain
  // fact rather than a diagnosis to act on.
  assert.equal(notInstalled.statement, timedOut.statement);
  assert.ok(!notInstalled.statement.includes("not installed"));
});

test("Docker autostart is never consulted when full_access mode is authorized — Docker's state is irrelevant to that path", async () => {
  let autostartCalls = 0;
  const runtime = new GatewayShellRuntime(baseConfig({
    fullAccessLocallyEnabled: true,
    dockerReadyCheck: async () => false,
    dockerAutostart: async () => {
      autostartCalls += 1;
      return { kind: "started" };
    },
  }));
  const frame = makeInvokeFrame("shell.execute", { command: "echo full-access" }, SAGE_AUTHORIZED_POLICY);
  const result = await runtime.handleCapabilityInvoke(frame);
  assert.equal(result.execution_mode, "full_access");
  assert.equal(autostartCalls, 0);
});

test("Docker already ready: dockerAutostart is never called at all", async () => {
  let autostartCalls = 0;
  const runtime = new GatewayShellRuntime(baseConfig({
    dockerReadyCheck: async () => true,
    dockerAutostart: async () => {
      autostartCalls += 1;
      return { kind: "started" };
    },
  }));
  const frame = makeInvokeFrame("shell.execute", { command: "echo already-ready" });
  await runtime.handleCapabilityInvoke(frame);
  assert.equal(autostartCalls, 0);
});

// ── full_access mode: direct host execution, no Docker required ──

test("full_access mode executes shell commands directly on the host", async () => {
  const runtime = new GatewayShellRuntime(baseConfig({ fullAccessLocallyEnabled: true }));
  const frame = makeInvokeFrame("shell.execute", { command: "echo hello-full-access" }, SAGE_AUTHORIZED_POLICY);
  const result = await runtime.handleCapabilityInvoke(frame);
  assert.equal(result.exit_code, 0);
  assert.equal(result.stdout, "hello-full-access");
  assert.equal(result.execution_mode, "full_access");
  assert.match(String(result.warning), /FULL HOST ACCESS/);
});

test("full_access mode filesystem write/read round-trips real file content", async () => {
  const runtime = new GatewayShellRuntime(baseConfig({ fullAccessLocallyEnabled: true }));
  const writeFrame = makeInvokeFrame(
    "filesystem.read_write",
    { path: "notes.txt", mode: "write", content: "hello from full access" },
    SAGE_AUTHORIZED_POLICY,
  );
  const writeResult = await runtime.handleCapabilityInvoke(writeFrame);
  assert.equal(writeResult.execution_mode, "full_access");

  const readFrame = makeInvokeFrame("filesystem.read_write", { path: "notes.txt", mode: "read" }, SAGE_AUTHORIZED_POLICY);
  const readResult = await runtime.handleCapabilityInvoke(readFrame);
  assert.equal(readResult.content, "hello from full access");
});

test("unsupported capability id throws", async () => {
  const runtime = new GatewayShellRuntime(baseConfig({ fullAccessLocallyEnabled: true }));
  const frame = makeInvokeFrame("computer_control.click", {}, SAGE_AUTHORIZED_POLICY);
  await assert.rejects(runtime.handleCapabilityInvoke(frame), /Unsupported shell_sandbox capability/);
});

test("requestedCapabilities and supportsCapability report exactly shell.execute and filesystem.read_write", () => {
  const runtime = new GatewayShellRuntime(baseConfig());
  assert.deepEqual(runtime.requestedCapabilities(), ["shell.execute", "filesystem.read_write"]);
  assert.equal(runtime.supportsCapability("shell.execute"), true);
  assert.equal(runtime.supportsCapability("filesystem.read_write"), true);
  assert.equal(runtime.supportsCapability("screenshot.capture"), false);
});

// ── Real Docker integration: these run against the actual local daemon and
// skip (not fail) when Docker isn't available, since CI/dev boxes vary. ──

test("real Docker: sandbox mode runs a command in an actual hardened container and round-trips output", async (t) => {
  if (!(await realDockerAvailable())) {
    t.skip("Docker daemon is not available on this machine");
    return;
  }
  const runtime = new GatewayShellRuntime(baseConfig({ dockerReadyCheck: async () => true }));
  const frame = makeInvokeFrame("shell.execute", { command: "echo real-sandbox-proof && hostname" });
  const result = await runtime.handleCapabilityInvoke(frame);
  assert.equal(result.exit_code, 0);
  assert.match(String(result.stdout), /real-sandbox-proof/);
  assert.equal(result.execution_mode, "sandbox");
  assert.equal((result.sandbox as Record<string, unknown>).mode, "docker");
});

test("real Docker: two back-to-back calls each get a fresh container — no state bleed outside the workspace mount", async (t) => {
  if (!(await realDockerAvailable())) {
    t.skip("Docker daemon is not available on this machine");
    return;
  }
  const config = baseConfig({ dockerReadyCheck: async () => true });
  const runtime = new GatewayShellRuntime(config);

  // Call 1: write a marker file OUTSIDE the workspace mount (in the
  // container's own /tmp, which is a fresh per-container tmpfs) and one
  // INSIDE the workspace mount (which is expected to persist, since it's
  // the same host-backed directory for the same workspace/mount).
  const writeFrame = makeInvokeFrame("shell.execute", {
    command: "echo outside-workspace > /tmp/marker.txt && echo inside-workspace > marker.txt && cat /tmp/marker.txt",
  });
  const first = await runtime.handleCapabilityInvoke(writeFrame);
  assert.match(String(first.stdout), /outside-workspace/);

  // Call 2: a fresh container should NOT see /tmp/marker.txt from call 1
  // (proves per-run isolation), but SHOULD see marker.txt in the workspace
  // mount (proves the workspace volume, not the container, is what persists).
  const checkFrame = makeInvokeFrame("shell.execute", {
    command: "test -f /tmp/marker.txt && echo TMP_LEAKED || echo TMP_CLEAN; cat marker.txt",
  });
  const second = await runtime.handleCapabilityInvoke(checkFrame);
  assert.match(String(second.stdout), /TMP_CLEAN/);
  assert.doesNotMatch(String(second.stdout), /TMP_LEAKED/);
  assert.match(String(second.stdout), /inside-workspace/);
});

test("real Docker: the pre-execution filter rejects a hard-blocked command before Docker is ever invoked", async (t) => {
  if (!(await realDockerAvailable())) {
    t.skip("Docker daemon is not available on this machine");
    return;
  }
  const runtime = new GatewayShellRuntime(baseConfig({ dockerReadyCheck: async () => true }));
  const frame = makeInvokeFrame("shell.execute", { command: "rm -rf /" });
  await assert.rejects(runtime.handleCapabilityInvoke(frame), /permanently blocked/);
});

// filesystem.read_write's sandbox WRITE/APPEND mode pipes content through
// the container's stdin (runtime.ts's filesystemInnerArgs: `cat > "$1"` /
// `cat >> "$1"`) — unlike shell.execute, which passes the command as argv
// and never touches stdin. That made it a genuinely different code path
// from every other "real Docker" test above, and it silently wrote 0-byte
// files (reported as success) until buildDockerRunArgs started passing
// `-i`. Real Docker only: a mocked dockerReadyCheck proves nothing about
// whether `docker run`'s own stdin plumbing is wired correctly.
test("real Docker: filesystem.read_write sandbox mode round-trips real content through container stdin", async (t) => {
  if (!(await realDockerAvailable())) {
    t.skip("Docker daemon is not available on this machine");
    return;
  }
  const runtime = new GatewayShellRuntime(baseConfig({ dockerReadyCheck: async () => true }));
  const writeFrame = makeInvokeFrame("filesystem.read_write", {
    path: "sandbox-note.txt",
    mode: "write",
    content: "real docker sandbox stdin round-trip",
  });
  const writeResult = await runtime.handleCapabilityInvoke(writeFrame);
  assert.equal(writeResult.execution_mode, "sandbox");
  assert.equal((writeResult.sandbox as Record<string, unknown>).mode, "docker");

  // Read back in a FRESH container (per-call isolation, same as shell.execute
  // above) — this only sees the content if it landed on the bind-mounted
  // workspace volume, not merely inside the writing container's own layer.
  const readFrame = makeInvokeFrame("filesystem.read_write", { path: "sandbox-note.txt", mode: "read" });
  const readResult = await runtime.handleCapabilityInvoke(readFrame);
  assert.equal(readResult.execution_mode, "sandbox");
  assert.equal(readResult.content, "real docker sandbox stdin round-trip");

  const appendFrame = makeInvokeFrame("filesystem.read_write", {
    path: "sandbox-note.txt",
    mode: "append",
    content: " — appended",
  });
  await runtime.handleCapabilityInvoke(appendFrame);
  const readAfterAppend = await runtime.handleCapabilityInvoke(
    makeInvokeFrame("filesystem.read_write", { path: "sandbox-note.txt", mode: "read" }),
  );
  assert.equal(readAfterAppend.content, "real docker sandbox stdin round-trip — appended");
});

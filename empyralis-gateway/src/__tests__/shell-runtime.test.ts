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

// ── Sandbox mode without Docker: absent, not silently degraded ──
// Docker readiness is injected here (not read from the real machine) so this
// test is deterministic regardless of whether Docker Desktop happens to be
// running on the box executing the suite — see the real, non-mocked Docker
// integration test further below for proof against an actual daemon.

test("sandbox mode without a ready Docker daemon fails closed with a clear error, no unsandboxed fallback", async () => {
  const runtime = new GatewayShellRuntime(baseConfig({ dockerReadyCheck: async () => false }));
  const frame = makeInvokeFrame("shell.execute", { command: "echo hello" });
  await assert.rejects(runtime.handleCapabilityInvoke(frame), /requires Docker.*or an explicitly enabled and authorized full_access/);
});

// Docker-down is one failure; WHY full_access isn't covering for it is a
// second, distinct fact the box operator needs to act on — these two tests
// pin the error message to each of the two independent reasons
// resolveExecutionMode can give for staying in sandbox, so a customer never
// hits a bare "neither is available" with no idea which of the two
// full_access keys (the local box flag, or the server-side authorization)
// is the one missing.
test("Docker-down error names the LOCAL flag as the reason when full_access isn't enabled on this box at all", async () => {
  const runtime = new GatewayShellRuntime(baseConfig({ dockerReadyCheck: async () => false, fullAccessLocallyEnabled: false }));
  const frame = makeInvokeFrame("shell.execute", { command: "echo hello" }, SAGE_AUTHORIZED_POLICY);
  await assert.rejects(runtime.handleCapabilityInvoke(frame), /full_access is not active because: full_access is not enabled locally on this box/);
});

test("Docker-down error names the SERVER authorization as the reason when the local flag is on but the server didn't authorize this call", async () => {
  const runtime = new GatewayShellRuntime(baseConfig({ dockerReadyCheck: async () => false, fullAccessLocallyEnabled: true }));
  const frame = makeInvokeFrame("shell.execute", { command: "echo hello" }); // no SAGE_AUTHORIZED_POLICY
  await assert.rejects(runtime.handleCapabilityInvoke(frame), /full_access is not active because: server did not authorize full_access for this call/);
});

// ── Docker autostart: sandbox mode gets ONE lever before giving up. These
// pin GatewayShellRuntime's own wiring of docker-autostart.ts's outcomes
// into the thrown message — docker-autostart.test.ts covers the module's
// internal behavior (platform branching, cooldown, single-flight) against
// an injected command runner. Nothing here ever spawns a real process. ──

test("Docker not ready: an autostart attempt is made, and a successful start moves past the availability gate", async () => {
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
    await runtime.handleCapabilityInvoke(frame);
  } catch (error) {
    // This runtime's stateDir has no real Docker guaranteed to be present
    // OR running on the machine executing this suite — whatever happens
    // downstream of the availability gate (a real `docker run` spawn) is
    // out of scope here. What this test pins is that the gate itself did
    // NOT refuse the call once autostart reported "started".
    assert.doesNotMatch(
      String((error as Error).message),
      /requires Docker.*or an explicitly enabled and authorized full_access/,
    );
  }
  assert.equal(autostartCalls, 1);
});

test("Docker not installed: the refusal says there is nothing to start, and names no start command", async () => {
  const runtime = new GatewayShellRuntime(baseConfig({
    dockerReadyCheck: async () => false,
    dockerAutostart: async () => ({ kind: "not_installed" }),
  }));
  const frame = makeInvokeFrame("shell.execute", { command: "echo hello" });
  await assert.rejects(
    runtime.handleCapabilityInvoke(frame),
    /Docker is not installed on this computer, so there is nothing to start/,
  );
});

test("Docker installed but the start command failed: the refusal carries the real failure detail", async () => {
  const runtime = new GatewayShellRuntime(baseConfig({
    dockerReadyCheck: async () => false,
    dockerAutostart: async () => ({
      kind: "start_command_failed",
      detail: "Unable to find application named \"Docker\"",
    }),
  }));
  const frame = makeInvokeFrame("shell.execute", { command: "echo hello" });
  await assert.rejects(
    runtime.handleCapabilityInvoke(frame),
    /could not be started automatically \(Unable to find application named "Docker"\)/,
  );
});

test("Docker installed and asked to start, but not ready in time: the refusal says it may still be starting, distinct from not_installed and start_command_failed", async () => {
  const runtime = new GatewayShellRuntime(baseConfig({
    dockerReadyCheck: async () => false,
    dockerAutostart: async () => ({ kind: "start_timed_out" }),
  }));
  const frame = makeInvokeFrame("shell.execute", { command: "echo hello" });
  await assert.rejects(
    runtime.handleCapabilityInvoke(frame),
    /was asked to start and may still be starting up.*Wait a bit and try again/,
  );
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

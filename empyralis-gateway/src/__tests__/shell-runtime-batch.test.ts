import test from "node:test";
import assert from "node:assert/strict";
import fs from "fs";
import os from "os";
import path from "path";

import { execFileWithTimeout } from "../shell/exec-file-with-timeout";
import { GatewayShellRuntime, type GatewayShellRuntimeConfig } from "../shell/runtime";
import type { DockerAutostartOutcome } from "../shell/docker-autostart";
import { HOST_STATEMENT } from "../shell/execution-isolation";
import type { GatewayRequestEnvelope, GatewayToolInvokePayload } from "../protocol/types";

// Same memoized real-Docker probe pattern as shell-runtime.test.ts — see
// that file's header comment for why this must go through
// execFileWithTimeout rather than execFileSync's own (non-functional)
// timeout option.
let dockerAvailability: Promise<boolean> | null = null;
function realDockerAvailable(): Promise<boolean> {
  dockerAvailability ??= execFileWithTimeout("docker", ["info", "--format", "{{.ServerVersion}}"], 3_000)
    .then((result) => !result.timedOut && !result.error && result.exitCode === 0);
  return dockerAvailability;
}

const NEVER_STARTS_DOCKER: () => Promise<DockerAutostartOutcome> = async () => ({ kind: "not_installed" });

function baseConfig(overrides: Partial<GatewayShellRuntimeConfig> = {}): GatewayShellRuntimeConfig {
  return {
    stateDir: fs.mkdtempSync(path.join(os.tmpdir(), "empyralis-shell-batch-test-")),
    fullAccessLocallyEnabled: false,
    dockerAutostart: NEVER_STARTS_DOCKER,
    ...overrides,
  };
}

const SAGE_AUTHORIZED_POLICY = {
  runtime_access_mode: "full_access",
  empyralis_approved: true,
  agent_scope: "sage",
  policy: { mode: "full_access", full_access_warning_acknowledged: true },
};

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
      workspace_id: "ws-batch-1",
      ...extra,
    },
  };
}

type CmdResult = { index: number; status: string; ran: boolean; exit_code: number | null; stdout: string; stderr: string; reason: string | null };

// ── Gating: refuse the WHOLE batch before executing anything ──

test("a batch with one policy-violating command refuses the whole batch — mocked Docker never even consulted", async () => {
  let dockerChecked = false;
  const runtime = new GatewayShellRuntime(
    baseConfig({
      dockerReadyCheck: async () => {
        dockerChecked = true;
        return true;
      },
    }),
  );
  const frame = makeInvokeFrame("shell.execute", { commands: ["echo fine", "rm -rf /", "echo also fine"] });
  await assert.rejects(runtime.handleCapabilityInvoke(frame), /Batch refused.*commands\[1\].*permanently blocked/s);
  assert.equal(dockerChecked, false, "policy gating must run before Docker readiness is even checked");
});

test("commands and command together are rejected as ambiguous, not silently resolved", async () => {
  const runtime = new GatewayShellRuntime(baseConfig());
  const frame = makeInvokeFrame("shell.execute", { command: "echo one", commands: ["echo two"] });
  await assert.rejects(runtime.handleCapabilityInvoke(frame), /received both `command` and `commands`/);
});

// INVERTED 2026-08-22: this used to assert the batch path FAILED CLOSED
// without Docker ("requires Docker ... or an explicitly enabled and
// authorized full_access"). A batch is just shell.execute with a `commands`
// array, so it inherits the same ruling as the single-command path: Docker
// chooses the isolation, not whether the work happens. The scratch-directory
// cleanup half of the original test is KEPT and still asserted — that was
// always a separate, still-correct fact about the `finally` block.
test("a batch without a ready Docker daemon RUNS on the host, labelled honestly, and leaves no scratch directory behind", async () => {
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), "empyralis-shell-batch-nodocker-"));
  const runtime = new GatewayShellRuntime(baseConfig({ stateDir, dockerReadyCheck: async () => false }));
  const frame = makeInvokeFrame("shell.execute", { commands: ["echo one", "echo two"] });
  const result = await runtime.handleCapabilityInvoke(frame);

  assert.equal(result.execution_mode, "host");
  assert.equal(result.isolation, "host");
  assert.equal(result.isolation_statement, HOST_STATEMENT);
  assert.equal(result.warning, undefined, "a host batch is the ordinary state of a Docker-less box, not an alarm");
  const commands = result.commands as CmdResult[];
  assert.equal(commands.length, 2);
  assert.equal(commands[0].ran, true);
  assert.equal(commands[0].exit_code, 0);
  assert.equal(commands[0].stdout.trim(), "one");
  assert.equal(commands[1].stdout.trim(), "two");

  // The batch scratch directory is created (to write cmd_*.sh files) before
  // Docker availability is even checked — cleanup in the `finally` must
  // still remove it on EVERY path, success included.
  const mountsRoot = path.join(stateDir, "mounts", "default", "ws-batch-1", ".empyralis-batch");
  const leftover = fs.existsSync(mountsRoot) ? fs.readdirSync(mountsRoot) : [];
  assert.deepEqual(leftover, [], "expected no leftover batch scratch directories");
});

test("an empty commands array is rejected before touching Docker", async () => {
  const runtime = new GatewayShellRuntime(baseConfig());
  const frame = makeInvokeFrame("shell.execute", { commands: [] });
  // Falls through to the single-command path (commands.length === 0 is not
  // "hasCommands"), and command is also absent — the single-command path's
  // own "command is required" error is the right outcome here.
  await assert.rejects(runtime.handleCapabilityInvoke(frame), /command is required/);
});

// ── full_access mode: no Docker needed, exercises the driver script directly on the host ──

test("full_access batch: cd in one command is visible to the next — the isolation-semantics change this exists for", async () => {
  const runtime = new GatewayShellRuntime(baseConfig({ fullAccessLocallyEnabled: true }));
  const frame = makeInvokeFrame(
    "shell.execute",
    { commands: ["mkdir -p sub && cd sub", "pwd"] },
    SAGE_AUTHORIZED_POLICY,
  );
  const result = await runtime.handleCapabilityInvoke(frame);
  const commands = result.commands as CmdResult[];
  assert.equal(commands[0].status, "success");
  assert.equal(commands[1].status, "success");
  assert.match(commands[1].stdout, /\/sub$/);
});

test("full_access batch: export in one command is visible to the next", async () => {
  const runtime = new GatewayShellRuntime(baseConfig({ fullAccessLocallyEnabled: true }));
  const frame = makeInvokeFrame(
    "shell.execute",
    { commands: ["export EMP_TEST_VAR=hello-batch", "echo \"got:$EMP_TEST_VAR\""] },
    SAGE_AUTHORIZED_POLICY,
  );
  const result = await runtime.handleCapabilityInvoke(frame);
  const commands = result.commands as CmdResult[];
  assert.equal(commands[1].stdout, "got:hello-batch");
});

test("full_access batch: stop_on_failure (default true) skips remaining commands after a failure, and says why", async () => {
  const runtime = new GatewayShellRuntime(baseConfig({ fullAccessLocallyEnabled: true }));
  const frame = makeInvokeFrame(
    "shell.execute",
    { commands: ["echo first", "false", "echo third-should-not-run"] },
    SAGE_AUTHORIZED_POLICY,
  );
  const result = await runtime.handleCapabilityInvoke(frame);
  const commands = result.commands as CmdResult[];
  assert.equal(commands[0].status, "success");
  assert.equal(commands[1].status, "failed");
  assert.equal(commands[1].exit_code, 1);
  assert.equal(commands[2].status, "skipped");
  assert.equal(commands[2].ran, false);
  assert.match(String(commands[2].reason), /earlier command in this batch failed/);
  assert.equal(result.stopped_early, true);
});

test("full_access batch: stop_on_failure=false runs every command regardless of earlier failures", async () => {
  const runtime = new GatewayShellRuntime(baseConfig({ fullAccessLocallyEnabled: true }));
  const frame = makeInvokeFrame(
    "shell.execute",
    { commands: ["echo first", "false", "echo third-does-run"], stop_on_failure: false },
    SAGE_AUTHORIZED_POLICY,
  );
  const result = await runtime.handleCapabilityInvoke(frame);
  const commands = result.commands as CmdResult[];
  assert.equal(commands[0].status, "success");
  assert.equal(commands[1].status, "failed");
  assert.equal(commands[2].status, "success");
  assert.equal(commands[2].stdout, "third-does-run");
  assert.equal(result.stopped_early, false);
});

test("full_access batch: each command's own stdout/stderr stay separated, never interleaved", async () => {
  const runtime = new GatewayShellRuntime(baseConfig({ fullAccessLocallyEnabled: true }));
  const frame = makeInvokeFrame(
    "shell.execute",
    { commands: ["echo out-1 && echo err-1 1>&2", "echo out-2 && echo err-2 1>&2"] },
    SAGE_AUTHORIZED_POLICY,
  );
  const result = await runtime.handleCapabilityInvoke(frame);
  const commands = result.commands as CmdResult[];
  assert.equal(commands[0].stdout, "out-1");
  assert.equal(commands[0].stderr, "err-1");
  assert.equal(commands[1].stdout, "out-2");
  assert.equal(commands[1].stderr, "err-2");
});

test("full_access batch: a command that exits the shell ends the batch there, and later commands report not_run", async () => {
  const runtime = new GatewayShellRuntime(baseConfig({ fullAccessLocallyEnabled: true }));
  const frame = makeInvokeFrame(
    "shell.execute",
    { commands: ["echo before", "exit 0", "echo after-should-not-run"] },
    SAGE_AUTHORIZED_POLICY,
  );
  const result = await runtime.handleCapabilityInvoke(frame);
  const commands = result.commands as CmdResult[];
  assert.equal(commands[0].status, "success");
  assert.equal(commands[2].status, "not_run");
});

// ── Sandbox mode: real Docker, real hardened container ──

test("real Docker: sandbox batch runs multiple commands in ONE container — cd carries across commands", async (t) => {
  if (!(await realDockerAvailable())) {
    t.skip("Docker daemon is not available on this machine");
    return;
  }
  const runtime = new GatewayShellRuntime(baseConfig({ dockerReadyCheck: async () => true }));
  const frame = makeInvokeFrame("shell.execute", {
    commands: ["mkdir -p project && cd project", "pwd", "echo marker > note.txt", "cat note.txt"],
  });
  const result = await runtime.handleCapabilityInvoke(frame);
  const commands = result.commands as CmdResult[];
  assert.equal(result.execution_mode, "sandbox");
  assert.equal((result.sandbox as Record<string, unknown>).mode, "docker");
  assert.equal(commands.every((c) => c.status === "success"), true);
  assert.match(commands[1].stdout, /\/project$/);
  assert.equal(commands[3].stdout, "marker");
});

test("real Docker: two separate batch calls each get a FRESH container — no state bleed between batches", async (t) => {
  if (!(await realDockerAvailable())) {
    t.skip("Docker daemon is not available on this machine");
    return;
  }
  const runtime = new GatewayShellRuntime(baseConfig({ dockerReadyCheck: async () => true }));
  const first = await runtime.handleCapabilityInvoke(
    makeInvokeFrame("shell.execute", { commands: ["export EMP_LEAK=should-not-survive", "echo set"] }),
  );
  assert.equal((first.commands as CmdResult[])[1].status, "success");

  const second = await runtime.handleCapabilityInvoke(
    makeInvokeFrame("shell.execute", { commands: ["echo \"leaked:${EMP_LEAK:-clean}\""] }),
  );
  assert.equal((second.commands as CmdResult[])[0].stdout, "leaked:clean");
});

test("real Docker: stop_on_failure default stops the batch and reports the exact command that failed plus what never ran", async (t) => {
  if (!(await realDockerAvailable())) {
    t.skip("Docker daemon is not available on this machine");
    return;
  }
  const runtime = new GatewayShellRuntime(baseConfig({ dockerReadyCheck: async () => true }));
  const result = await runtime.handleCapabilityInvoke(
    makeInvokeFrame("shell.execute", { commands: ["echo ok", "cat /does/not/exist", "echo never"] }),
  );
  const commands = result.commands as CmdResult[];
  assert.equal(commands[0].status, "success");
  assert.equal(commands[1].status, "failed");
  assert.notEqual(commands[1].exit_code, 0);
  assert.match(commands[1].stderr, /No such file/i);
  assert.equal(commands[2].status, "skipped");
});

test("real Docker: a batch whose shared time budget elapses reports the in-flight command as timed_out and later ones as not_run — not as 'failed'", { timeout: 30_000 }, async (t) => {
  if (!(await realDockerAvailable())) {
    t.skip("Docker daemon is not available on this machine");
    return;
  }
  const runtime = new GatewayShellRuntime(baseConfig({ dockerReadyCheck: async () => true }));
  const result = await runtime.handleCapabilityInvoke(
    makeInvokeFrame("shell.execute", {
      commands: [
        "echo quick",
        { command: "sleep 30", timeout_seconds: 1 },
        "echo should-not-run",
      ],
    }),
  );
  const commands = result.commands as CmdResult[];
  assert.equal(result.batch_timed_out, true);
  assert.equal(commands[0].status, "success");
  assert.equal(commands[1].status, "timed_out");
  assert.equal(commands[1].exit_code, null);
  assert.equal(commands[2].status, "not_run");
  assert.match(String(commands[2].reason), /shared time budget/);
});

test("real Docker: the killed container is actually gone afterward — no orphaned container left running", { timeout: 30_000 }, async (t) => {
  if (!(await realDockerAvailable())) {
    t.skip("Docker daemon is not available on this machine");
    return;
  }
  const runtime = new GatewayShellRuntime(baseConfig({ dockerReadyCheck: async () => true }));
  const result = await runtime.handleCapabilityInvoke(
    makeInvokeFrame("shell.execute", {
      commands: [{ command: "sleep 30", timeout_seconds: 1 }],
    }),
  );
  const containerName = ((result.sandbox as Record<string, unknown>).container_name as string) ?? "";
  assert.ok(containerName, "expected a container_name on the batch sandbox result");
  // Give docker a moment to actually finish tearing the container down.
  await new Promise((resolve) => setTimeout(resolve, 500));
  const inspect = await execFileWithTimeout("docker", ["inspect", "-f", "{{.State.Running}}", containerName], 5_000);
  // A gone (--rm'd) container 404s from `docker inspect`; a still-running
  // one would print "true". Either non-zero exit or a body that isn't
  // "true" both prove it is not still running.
  const stillRunning = inspect.exitCode === 0 && inspect.stdout.trim() === "true";
  assert.equal(stillRunning, false, `container ${containerName} is still running after the batch timeout killed it`);
});

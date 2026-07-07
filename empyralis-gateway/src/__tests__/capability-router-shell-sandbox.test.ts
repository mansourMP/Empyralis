import test from "node:test";
import assert from "node:assert/strict";
import fs from "fs";
import os from "os";
import path from "path";

import { GatewayCapabilityRouter } from "../supervisor/capability-router";
import { GatewayShellRuntime } from "../shell/runtime";
import { PersonalChannelRuntimeRegistry } from "../channels/personal-runtime";
import { setShellSandboxDockerReady } from "../runtime/desktop-permissions";
import type { GatewayRequestEnvelope, GatewayToolInterruptPayload, GatewayToolInvokePayload } from "../protocol/types";

function makeShellRuntime(): GatewayShellRuntime {
  return new GatewayShellRuntime({
    stateDir: fs.mkdtempSync(path.join(os.tmpdir(), "empyralis-router-shell-test-")),
    fullAccessLocallyEnabled: true,
    dockerReadyCheck: async () => false, // irrelevant here: full_access + sage authorization below skips Docker
  });
}

// The router's assertCapabilityPermissionReady() gate (invoke-time, before
// any executor dispatch) checks the SAME shell_sandbox permission this file's
// last test toggles directly. Default it to ready here so the dispatch-
// routing tests above are testing routing, not permission gating — the
// permission-gating behavior itself has its own dedicated test at the bottom
// and in desktop-permissions-shell-sandbox.test.ts.
test.beforeEach(() => {
  setShellSandboxDockerReady(true);
});

const SAGE_AUTHORIZED_POLICY = {
  runtime_access_mode: "full_access",
  empyralis_approved: true,
  agent_scope: "sage",
  policy: { mode: "full_access", full_access_warning_acknowledged: true },
};

function makeInvokeFrame(capabilityId: string, args: Record<string, unknown>): GatewayRequestEnvelope<GatewayToolInvokePayload> {
  return {
    kind: "request",
    id: "req-1",
    type: "tool.invoke",
    ts: new Date().toISOString(),
    payload: {
      capability_id: capabilityId,
      arguments: args,
      run_id: "run-shell-1",
      trace_id: "trace-1",
      workspace_id: "ws-1",
      ...SAGE_AUTHORIZED_POLICY,
    },
  };
}

function makeInterruptFrame(runId: string): GatewayRequestEnvelope<GatewayToolInterruptPayload> {
  return {
    kind: "request",
    id: "req-2",
    type: "tool.interrupt",
    ts: new Date().toISOString(),
    payload: { run_id: runId, trace_id: "trace-1", workspace_id: "ws-1" },
  };
}

test("router dispatches shell.execute to the shell_sandbox executor", async () => {
  const router = new GatewayCapabilityRouter(undefined, new PersonalChannelRuntimeRegistry(), undefined, makeShellRuntime());
  const result = await router.handleToolInvoke(makeInvokeFrame("shell.execute", { command: "echo router-dispatch-proof" }));
  assert.equal(result.capability_id, "shell.execute");
  const inner = result.result as Record<string, unknown>;
  assert.match(String(inner.stdout), /router-dispatch-proof/);
});

test("router dispatches filesystem.read_write to the shell_sandbox executor", async () => {
  const router = new GatewayCapabilityRouter(undefined, new PersonalChannelRuntimeRegistry(), undefined, makeShellRuntime());
  await router.handleToolInvoke(makeInvokeFrame("filesystem.read_write", { path: "note.txt", mode: "write", content: "hi" }));
  const result = await router.handleToolInvoke(makeInvokeFrame("filesystem.read_write", { path: "note.txt", mode: "read" }));
  const inner = result.result as Record<string, unknown>;
  assert.equal(inner.content, "hi");
});

test("router falls through to the unsupported-capability error when shell_sandbox does not recognize the capability", async () => {
  const router = new GatewayCapabilityRouter(undefined, new PersonalChannelRuntimeRegistry(), undefined, makeShellRuntime());
  await assert.rejects(
    router.handleToolInvoke(makeInvokeFrame("computer_control.click", {})),
    /No executor available for capability/,
  );
});

test("interrupt for a shell_sandbox run returns the not-yet-implemented stub, not a crash", async () => {
  const router = new GatewayCapabilityRouter(undefined, new PersonalChannelRuntimeRegistry(), undefined, makeShellRuntime());
  await router.handleToolInvoke(makeInvokeFrame("shell.execute", { command: "echo hi" }));
  const result = await router.handleToolInterrupt(makeInterruptFrame("run-shell-1"));
  assert.equal(result.interrupted, false);
  assert.match(String(result.error), /shell_sandbox interrupt not yet implemented/);
});

test("supportedCapabilities() only advertises shell_sandbox capabilities when Docker is confirmed ready", () => {
  const router = new GatewayCapabilityRouter(undefined, new PersonalChannelRuntimeRegistry(), undefined, makeShellRuntime());

  setShellSandboxDockerReady(false);
  const withoutDocker = router.supportedCapabilities();
  assert.equal(withoutDocker.includes("shell.execute"), false);
  assert.equal(withoutDocker.includes("filesystem.read_write"), false);

  setShellSandboxDockerReady(true);
  const withDocker = router.supportedCapabilities();
  assert.ok(withDocker.includes("shell.execute"));
  assert.ok(withDocker.includes("filesystem.read_write"));

  setShellSandboxDockerReady(false); // restore default for any other tests sharing this process
});

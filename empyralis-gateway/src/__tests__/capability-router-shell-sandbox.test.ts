import test from "node:test";
import assert from "node:assert/strict";
import fs from "fs";
import os from "os";
import path from "path";

import { GatewayCapabilityRouter } from "../supervisor/capability-router";
import { GatewayShellRuntime } from "../shell/runtime";
import { PersonalChannelRuntimeRegistry } from "../channels/personal-runtime";
import { setShellSandboxDockerReady } from "../runtime/desktop-permissions";
import { buildFastPassiveInventorySnapshot } from "../health/service-inventory";
import { buildRuntimeMetadata } from "../runtime/runtime-metadata";
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

test("supportedCapabilities() advertises shell_sandbox capabilities regardless of live Docker readiness", () => {
  // Regression test: this used to assert the OPPOSITE — that
  // supportedCapabilities() dropped shell.execute/filesystem.read_write
  // entirely whenever Docker wasn't (yet) confirmed ready. That was the bug:
  // this exact array becomes runtimeMetadata.requestedCapabilities
  // (index.ts), which is what seeds capability_readiness.requested on every
  // heartbeat (health/service-inventory.ts) — so when Docker wasn't ready,
  // shell.execute/filesystem.read_write weren't just reported "restricted",
  // they were flat-out MISSING from capability_readiness.permission_states,
  // indistinguishable from a gateway that never supported them at all. The
  // backend's gateway_capability_not_ready check had no key to find. See
  // capability-router.ts's supportedCapabilities() doc comment on the
  // shell_sandbox line for the full trace.
  //
  // Advertising unconditionally is safe: it does not grant early execution.
  // handleToolInvoke() below still calls assertCapabilityPermissionReady(),
  // which independently blocks the call while Docker isn't ready — see
  // desktop-permissions-shell-sandbox.test.ts. Advertisement and
  // authorization are two different questions; only the first one is what
  // this test covers.
  const router = new GatewayCapabilityRouter(undefined, new PersonalChannelRuntimeRegistry(), undefined, makeShellRuntime());

  setShellSandboxDockerReady(false);
  const withoutDocker = router.supportedCapabilities();
  assert.ok(withoutDocker.includes("shell.execute"));
  assert.ok(withoutDocker.includes("filesystem.read_write"));

  setShellSandboxDockerReady(true);
  const withDocker = router.supportedCapabilities();
  assert.ok(withDocker.includes("shell.execute"));
  assert.ok(withDocker.includes("filesystem.read_write"));

  setShellSandboxDockerReady(false); // restore default for any other tests sharing this process
});

test("shell.execute invocation is still blocked while Docker is not ready, even though it's advertised", async () => {
  // Companion to the advertisement test above: proves advertising the
  // capability unconditionally did NOT quietly widen what's actually
  // runnable. The gate moved entirely to invoke time (assertCapabilityPermissionReady
  // inside handleToolInvoke), not away.
  setShellSandboxDockerReady(false);
  const router = new GatewayCapabilityRouter(undefined, new PersonalChannelRuntimeRegistry(), undefined, makeShellRuntime());
  await assert.rejects(
    router.handleToolInvoke({
      kind: "request",
      id: "req-blocked-1",
      type: "tool.invoke",
      ts: new Date().toISOString(),
      payload: {
        capability_id: "shell.execute",
        arguments: { command: "echo should-not-run" },
        run_id: "run-blocked-1",
        trace_id: "trace-1",
        workspace_id: "ws-1",
        // Deliberately NOT the SAGE_AUTHORIZED_POLICY used elsewhere in this
        // file — full_access is a separate escalation path; this proves the
        // ordinary/default sandbox path is blocked while Docker isn't ready.
      },
    } as GatewayRequestEnvelope<GatewayToolInvokePayload>),
    /blocked\/local_permission_denied/,
  );
  setShellSandboxDockerReady(true);
});

test("the real startup pipeline (router -> buildRuntimeMetadata -> passive inventory) reports shell.execute readiness instead of omitting it", () => {
  // This is the gap the isolated heartbeat-service-inventory.test.ts case
  // (line ~25) does NOT cover: that test hand-writes
  // `requestedCapabilities: ["shell.execute"]` and a hand-written
  // capability_readiness object, which only proves the downstream mapping
  // (capabilityPermissionStatus -> permission_states) is correct GIVEN the
  // right input. It says nothing about whether the real gateway ever
  // produces that input.
  //
  // This test instead wires the actual production call chain end to end,
  // with nothing hand-supplied except the Docker-readiness flag itself:
  //   GatewayCapabilityRouter.supportedCapabilities()          (supervisor/capability-router.ts)
  //     -> buildRuntimeMetadata(version, capabilities)          (runtime/runtime-metadata.ts, called from index.ts)
  //       -> buildFastPassiveInventorySnapshot({ requestedCapabilities }) (health/service-inventory.ts, called from cloud/ws-client.ts every heartbeat)
  //         -> capability_readiness.requested / .blocked / .permission_states
  //
  // Before the fix, step 1 silently dropped shell.execute/
  // filesystem.read_write from its output whenever Docker wasn't ready,
  // so permission_states never even got a chance to report "restricted" —
  // the key was just absent, which is exactly what the founder's live
  // registration query showed.
  const router = new GatewayCapabilityRouter(undefined, new PersonalChannelRuntimeRegistry(), undefined, makeShellRuntime());

  setShellSandboxDockerReady(false);
  const runtimeMetadataDockerDown = buildRuntimeMetadata("0.1.0-test", router.supportedCapabilities());
  const snapshotDockerDown = buildFastPassiveInventorySnapshot({
    requestedCapabilities: runtimeMetadataDockerDown.requestedCapabilities,
  });
  assert.ok(
    "shell.execute" in snapshotDockerDown.capability_readiness.permission_states,
    "shell.execute must be a reported key even while Docker is not ready — not silently absent",
  );
  assert.ok(
    "filesystem.read_write" in snapshotDockerDown.capability_readiness.permission_states,
    "filesystem.read_write must be a reported key even while Docker is not ready — not silently absent",
  );
  assert.equal(snapshotDockerDown.capability_readiness.permission_states["shell.execute"].state, "restricted");
  assert.ok(snapshotDockerDown.capability_readiness.blocked.includes("shell.execute"));
  assert.ok(snapshotDockerDown.capability_readiness.blocked.includes("filesystem.read_write"));

  setShellSandboxDockerReady(true);
  const runtimeMetadataDockerUp = buildRuntimeMetadata("0.1.0-test", router.supportedCapabilities());
  const snapshotDockerUp = buildFastPassiveInventorySnapshot({
    requestedCapabilities: runtimeMetadataDockerUp.requestedCapabilities,
  });
  assert.equal(snapshotDockerUp.capability_readiness.permission_states["shell.execute"].state, "granted");
  assert.ok(snapshotDockerUp.capability_readiness.ready.includes("shell.execute"));
  assert.ok(snapshotDockerUp.capability_readiness.ready.includes("filesystem.read_write"));

  setShellSandboxDockerReady(false); // restore default for any other tests sharing this process
});

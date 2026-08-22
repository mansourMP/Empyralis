import test from "node:test";
import assert from "node:assert/strict";
import fs from "fs";
import os from "os";
import path from "path";

import { GatewayCapabilityRouter } from "../supervisor/capability-router";
import { GatewayShellRuntime } from "../shell/runtime";
import { PersonalChannelRuntimeRegistry } from "../channels/personal-runtime";
import { buildFastPassiveInventorySnapshot } from "../health/service-inventory";
import { buildRuntimeMetadata } from "../runtime/runtime-metadata";
import type { GatewayRequestEnvelope, GatewayToolInterruptPayload, GatewayToolInvokePayload } from "../protocol/types";

function makeShellRuntime(): GatewayShellRuntime {
  return new GatewayShellRuntime({
    stateDir: fs.mkdtempSync(path.join(os.tmpdir(), "empyralis-router-shell-test-")),
    fullAccessLocallyEnabled: true,
    dockerReadyCheck: async () => false,
    // MUST be injected, and this is not belt-and-braces: since 2026-08-22 an
    // ordinary (non-full_access) invoke is no longer refused at the permission
    // layer, so it now reaches the executor and consults Docker autostart for
    // real. The default is ensureDockerReady(), which spawns `open -a Docker`
    // / `systemctl start docker` on the machine running this suite and then
    // waits for it — 10x'd the whole gateway suite's runtime before this was
    // added. Same guard shell-runtime.test.ts's baseConfig() already had.
    dockerAutostart: async () => ({ kind: "not_installed" }),
  });
}

// The router's assertCapabilityPermissionReady() gate (invoke-time, before
// any executor dispatch) checks the SAME shell_sandbox permission this file's
// last test toggles directly. Default it to ready here so the dispatch-
// routing tests above are testing routing, not permission gating — the
// permission-gating behavior itself has its own dedicated test at the bottom
// and in desktop-permissions-shell-sandbox.test.ts.
test.beforeEach(() => {
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

  const advertised = router.supportedCapabilities();
  assert.ok(advertised.includes("shell.execute"));
  assert.ok(advertised.includes("filesystem.read_write"));
});

// INVERTED 2026-08-22. This test used to assert that shell.execute was
// REFUSED at invoke time whenever Docker was not ready ("blocked/
// local_permission_denied"), and called that the safe half of advertising
// the capability unconditionally. That refusal is exactly what the founder
// rejected: a Docker-less computer could never run a command at all, so the
// agent reported it had no permission to act. Docker now chooses the
// isolation (container vs. the machine itself, see shell/execution-
// isolation.ts); it no longer chooses whether anything runs.
test("shell.execute invocation is NOT blocked by the permission layer — Docker readiness is not a gate here", async () => {
  const router = new GatewayCapabilityRouter(undefined, new PersonalChannelRuntimeRegistry(), undefined, makeShellRuntime());
  const result = await router.handleToolInvoke({
    kind: "request",
    id: "req-unblocked-1",
    type: "tool.invoke",
    ts: new Date().toISOString(),
    payload: {
      capability_id: "shell.execute",
      arguments: { command: "echo ran" },
      run_id: "run-unblocked-1",
      trace_id: "trace-1",
      workspace_id: "ws-1",
      // Deliberately NOT the SAGE_AUTHORIZED_POLICY used elsewhere in this
      // file: this is the ORDINARY, unescalated path, which is precisely the
      // one that used to dead-end. Reaching the executor here must not be
      // read as full_access — that escalation's two-part opt-in is unchanged.
    },
  } as GatewayRequestEnvelope<GatewayToolInvokePayload>);
  assert.ok(result, "the invoke must reach the shell executor rather than being refused by the permission layer");
});

// The env override remains the one real way to turn shell access off on a
// box, and it must still refuse at invoke time — otherwise removing the
// Docker gate would have removed the only off switch with it.
test("an explicit env restriction still blocks the invoke", async () => {
  const previous = process.env.EMPYRALIS_AGENT_COMPUTER_PERMISSION_SHELL_SANDBOX;
  process.env.EMPYRALIS_AGENT_COMPUTER_PERMISSION_SHELL_SANDBOX = "denied";
  try {
    const router = new GatewayCapabilityRouter(undefined, new PersonalChannelRuntimeRegistry(), undefined, makeShellRuntime());
    await assert.rejects(
      router.handleToolInvoke({
        kind: "request",
        id: "req-denied-1",
        type: "tool.invoke",
        ts: new Date().toISOString(),
        payload: {
          capability_id: "shell.execute",
          arguments: { command: "echo should-not-run" },
          run_id: "run-denied-1",
          trace_id: "trace-1",
          workspace_id: "ws-1",
        },
      } as GatewayRequestEnvelope<GatewayToolInvokePayload>),
      /blocked\/local_permission_denied/,
    );
  } finally {
    if (previous === undefined) {
      delete process.env.EMPYRALIS_AGENT_COMPUTER_PERMISSION_SHELL_SANDBOX;
    } else {
      process.env.EMPYRALIS_AGENT_COMPUTER_PERMISSION_SHELL_SANDBOX = previous;
    }
  }
});

test("the real startup pipeline (router -> buildRuntimeMetadata -> passive inventory) reports shell.execute READY, not blocked", () => {
  // This wires the actual production call chain end to end, with nothing
  // hand-supplied:
  //   GatewayCapabilityRouter.supportedCapabilities()          (supervisor/capability-router.ts)
  //     -> buildRuntimeMetadata(version, capabilities)          (runtime/runtime-metadata.ts, called from index.ts)
  //       -> buildFastPassiveInventorySnapshot({ requestedCapabilities }) (health/service-inventory.ts, called from cloud/ws-client.ts every heartbeat)
  //         -> capability_readiness.requested / .ready / .blocked / .permission_states
  //
  // It has been inverted TWICE, and both inversions matter. Originally it
  // proved the capability was not silently ABSENT when Docker was down (the
  // founder's live registration query showed exactly that). It then asserted
  // "restricted" + present in `blocked` — honest reporting of a real gate.
  // As of 2026-08-22 there is no gate: the control plane's own dispatch check
  // (gateway_execution_service.gateway_registration_execution_readiness ->
  // gateway_capability_not_ready) reads THIS array, so anything but `ready`
  // here means a Docker-less box still gets refused one layer up, before the
  // executor that would have run it on the host ever sees the call.
  const router = new GatewayCapabilityRouter(undefined, new PersonalChannelRuntimeRegistry(), undefined, makeShellRuntime());
  const runtimeMetadata = buildRuntimeMetadata("0.1.0-test", router.supportedCapabilities());
  const snapshot = buildFastPassiveInventorySnapshot({
    requestedCapabilities: runtimeMetadata.requestedCapabilities,
  });

  assert.ok(
    "shell.execute" in snapshot.capability_readiness.permission_states,
    "shell.execute must be a reported key, never silently absent",
  );
  assert.ok(
    "filesystem.read_write" in snapshot.capability_readiness.permission_states,
    "filesystem.read_write must be a reported key, never silently absent",
  );
  assert.equal(snapshot.capability_readiness.permission_states["shell.execute"].state, "granted");
  assert.ok(snapshot.capability_readiness.ready.includes("shell.execute"));
  assert.ok(snapshot.capability_readiness.ready.includes("filesystem.read_write"));
  assert.ok(!snapshot.capability_readiness.blocked.includes("shell.execute"));
  assert.ok(!snapshot.capability_readiness.blocked.includes("filesystem.read_write"));
});

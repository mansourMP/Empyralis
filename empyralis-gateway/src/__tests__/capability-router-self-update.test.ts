import test from "node:test";
import assert from "node:assert/strict";
import fs from "fs";
import os from "os";
import path from "path";

import { GatewayCapabilityRouter } from "../supervisor/capability-router";
import { PersonalChannelRuntimeRegistry } from "../channels/personal-runtime";
import { GatewaySelfUpdateRuntime } from "../update/gateway-self-update-runtime";
import type { GatewayRequestEnvelope, GatewayToolInterruptPayload, GatewayToolInvokePayload } from "../protocol/types";

function makeRuntime(overrides: Partial<ConstructorParameters<typeof GatewaySelfUpdateRuntime>[0]> = {}) {
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), "empyralis-router-self-update-test-"));
  let shutdownCalls = 0;
  const runtime = new GatewaySelfUpdateRuntime({
    currentVersion: "0.1.0",
    stateDir,
    requestShutdown: () => {
      shutdownCalls += 1;
    },
    preShutdownDelayMs: 0,
    detectSupervisor: () => "systemd", // supervised path: no handoff spawn needed for this file's tests
    ...overrides,
  });
  return { runtime, getShutdownCalls: () => shutdownCalls };
}

function makeInvokeFrame(capabilityId: string, args: Record<string, unknown>): GatewayRequestEnvelope<GatewayToolInvokePayload> {
  return {
    kind: "request",
    id: "req-1",
    type: "tool.invoke",
    ts: new Date().toISOString(),
    payload: {
      capability_id: capabilityId,
      arguments: args,
      run_id: "run-self-update-1",
      trace_id: "trace-1",
      workspace_id: "ws-1",
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

test("router advertises gateway.self_update in supportedCapabilities()", () => {
  const { runtime } = makeRuntime();
  const router = new GatewayCapabilityRouter(undefined, new PersonalChannelRuntimeRegistry(), undefined, undefined, undefined, undefined, runtime);
  assert.ok(router.supportedCapabilities().includes("gateway.self_update"));
});

test("router dispatches gateway.self_update to the self_update executor (no-op-safe path)", async () => {
  const { runtime, getShutdownCalls } = makeRuntime();
  const router = new GatewayCapabilityRouter(undefined, new PersonalChannelRuntimeRegistry(), undefined, undefined, undefined, undefined, runtime);
  const result = await router.handleToolInvoke(
    makeInvokeFrame("gateway.self_update", { target_version: "0.1.0", artifact_url: "https://example.invalid/artifact.tar.gz" }),
  );
  assert.equal(result.capability_id, "gateway.self_update");
  const inner = result.result as Record<string, unknown>;
  assert.equal(inner.updated, false);
  assert.equal(inner.reason, "already_current");
  assert.equal(getShutdownCalls(), 0, "a no-op update must never trigger shutdown");
});

test("router routes tool.interrupt for a self_update run to a not-applicable response, not an unknown-executor error", async () => {
  const { runtime } = makeRuntime();
  const router = new GatewayCapabilityRouter(undefined, new PersonalChannelRuntimeRegistry(), undefined, undefined, undefined, undefined, runtime);
  await router.handleToolInvoke(
    makeInvokeFrame("gateway.self_update", { target_version: "0.1.0", artifact_url: "https://example.invalid/artifact.tar.gz" }),
  );
  const result = await router.handleToolInterrupt(makeInterruptFrame("run-self-update-1"));
  assert.equal(result.interrupted, false);
  assert.match(String(result.error), /not applicable/);
});

test("router without a self-update runtime rejects gateway.self_update with the standard unknown-executor error", async () => {
  const router = new GatewayCapabilityRouter(undefined, new PersonalChannelRuntimeRegistry());
  await assert.rejects(
    router.handleToolInvoke(makeInvokeFrame("gateway.self_update", { target_version: "1.2.3", artifact_url: "https://example.invalid/a.tar.gz" })),
    /No executor available for capability "gateway.self_update"/,
  );
});

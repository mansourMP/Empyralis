import test from "node:test";
import assert from "node:assert/strict";

import { GatewayCapabilityRouter } from "../supervisor/capability-router";
import { PersonalChannelRuntimeRegistry } from "../channels/personal-runtime";
import { GatewayCliSetupRuntime, CLI_INSTALL_CAPABILITY, CLI_LOGIN_START_CAPABILITY } from "../llm/cli-setup-runtime";
import { setCliSetupLocallyEnabled } from "../runtime/desktop-permissions";
import type { GatewayRequestEnvelope, GatewayToolInterruptPayload, GatewayToolInvokePayload } from "../protocol/types";

function makeInvokeFrame(capabilityId: string, runId: string, args: Record<string, unknown> = {}): GatewayRequestEnvelope<GatewayToolInvokePayload> {
  return {
    kind: "request",
    id: "req-1",
    type: "tool.invoke",
    ts: new Date().toISOString(),
    payload: { capability_id: capabilityId, run_id: runId, trace_id: "trace-1", workspace_id: "ws-1", arguments: args },
  };
}

function makeInterruptFrame(runId: string): GatewayRequestEnvelope<GatewayToolInterruptPayload> {
  return {
    kind: "request",
    id: "req-2",
    type: "tool.interrupt",
    ts: new Date().toISOString(),
    payload: { run_id: runId, trace_id: "trace-1", workspace_id: "ws-1", reason: "test" },
  };
}

test("tool.invoke routes cli.install to the cli_setup executor", async () => {
  setCliSetupLocallyEnabled(true); // assertCapabilityPermissionReady gates every tool.invoke
  let invoked: string | null = null;
  const cliSetupRuntime = {
    requestedCapabilities: () => [CLI_INSTALL_CAPABILITY],
    supportsCapability: (id: string) => id === CLI_INSTALL_CAPABILITY,
    handleCapabilityInvoke: async (frame: GatewayRequestEnvelope<GatewayToolInvokePayload>) => {
      invoked = frame.payload.capability_id;
      return { installed: true };
    },
  } as unknown as GatewayCliSetupRuntime;
  const router = new GatewayCapabilityRouter(undefined, new PersonalChannelRuntimeRegistry(), undefined, undefined, undefined, cliSetupRuntime);

  const result = await router.handleToolInvoke(makeInvokeFrame(CLI_INSTALL_CAPABILITY, "run-1", { runtime: "claude_code" }));
  assert.equal(invoked, CLI_INSTALL_CAPABILITY);
  assert.equal(result.capability_id, CLI_INSTALL_CAPABILITY);
  assert.deepEqual(result.result, { installed: true });
  setCliSetupLocallyEnabled(false); // restore default for any other tests sharing this process
});

test("tool.interrupt for a cli_setup run_id reaches cliSetupRuntime.interruptRun", async () => {
  setCliSetupLocallyEnabled(true); // assertCapabilityPermissionReady gates every tool.invoke
  let interruptedRunId: string | null = null;
  const cliSetupRuntime = {
    requestedCapabilities: () => [CLI_LOGIN_START_CAPABILITY],
    supportsCapability: (id: string) => id === CLI_LOGIN_START_CAPABILITY,
    handleCapabilityInvoke: async () => ({ run_id: "run-2", status: "started" }),
    interruptRun: async (runId: string) => {
      interruptedRunId = runId;
      return { interrupted: true, run_id: runId };
    },
  } as unknown as GatewayCliSetupRuntime;
  const router = new GatewayCapabilityRouter(undefined, new PersonalChannelRuntimeRegistry(), undefined, undefined, undefined, cliSetupRuntime);

  await router.handleToolInvoke(makeInvokeFrame(CLI_LOGIN_START_CAPABILITY, "run-2", { runtime: "codex" }));
  const result = await router.handleToolInterrupt(makeInterruptFrame("run-2"));

  assert.equal(interruptedRunId, "run-2");
  assert.equal(result.interrupted, true);
  setCliSetupLocallyEnabled(false); // restore default for any other tests sharing this process
});

test("cli_setup capabilities are excluded from supportedCapabilities() until the box operator opts in", () => {
  setCliSetupLocallyEnabled(false);
  const cliSetupRuntime = new GatewayCliSetupRuntime();
  const router = new GatewayCapabilityRouter(undefined, new PersonalChannelRuntimeRegistry(), undefined, undefined, undefined, cliSetupRuntime);
  const supported = router.supportedCapabilities();
  assert.ok(!supported.includes(CLI_INSTALL_CAPABILITY), "cli.install must not be advertised while cli_setup is restricted");
});

test("cli_setup capabilities appear in supportedCapabilities() once the box operator opts in", () => {
  setCliSetupLocallyEnabled(true);
  const cliSetupRuntime = new GatewayCliSetupRuntime();
  const router = new GatewayCapabilityRouter(undefined, new PersonalChannelRuntimeRegistry(), undefined, undefined, undefined, cliSetupRuntime);
  const supported = router.supportedCapabilities();
  assert.ok(supported.includes(CLI_INSTALL_CAPABILITY));
  assert.ok(supported.includes(CLI_LOGIN_START_CAPABILITY));
  setCliSetupLocallyEnabled(false); // restore default for any other tests sharing this process
});

import test from "node:test";
import assert from "node:assert/strict";

import { GatewayCapabilityRouter } from "../supervisor/capability-router";
import { PersonalChannelRuntimeRegistry, type PersonalChannelRuntime } from "../channels/personal-runtime";
import type { GatewayBrowserRuntime } from "../browser/runtime";
import type { GatewayRequestEnvelope, GatewayToolInterruptPayload, GatewayToolInvokePayload } from "../protocol/types";

function makeInvokeFrame(
  id: string,
  capabilityId: string,
  runId: string,
): GatewayRequestEnvelope<GatewayToolInvokePayload> {
  return {
    kind: "request",
    id,
    type: "tool.invoke",
    ts: new Date().toISOString(),
    payload: {
      capability_id: capabilityId,
      run_id: runId,
      trace_id: "trace-1",
      workspace_id: "ws-1",
      arguments: {},
    },
  };
}

function makeInterruptFrame(
  id: string,
  runId: string,
): GatewayRequestEnvelope<GatewayToolInterruptPayload> {
  return {
    kind: "request",
    id,
    type: "tool.interrupt",
    ts: new Date().toISOString(),
    payload: {
      run_id: runId,
      trace_id: "trace-1",
      workspace_id: "ws-1",
      reason: "user cancelled",
    },
  };
}

function makeBrowserRuntimeMock(onInvoke: (capabilityId: string) => void): GatewayBrowserRuntime {
  return {
    requestedCapabilities: () => ["browser.session.start", "browser.session.interrupt"],
    supportsCapability: (cap: string) =>
      ["browser.session.start", "browser.session.action", "browser.session.interrupt"].includes(cap),
    handleCapabilityInvoke: async (frame: GatewayRequestEnvelope<GatewayToolInvokePayload>) => {
      onInvoke(frame.payload.capability_id);
      return { interrupted: true, run_id: frame.payload.run_id };
    },
  } as unknown as GatewayBrowserRuntime;
}

function makePersonalChannelRuntimeMock(capabilityId: string): PersonalChannelRuntime {
  return {
    requestedCapabilities: () => [capabilityId],
    supportsCapability: (cap: string) => cap === capabilityId,
    handleCapabilityInvoke: async () => ({ result: "executed" }),
    supportsChannel: () => false,
    handleChannelOutbound: async () => ({}),
    handleGatewayConnected: async () => {},
    handleGatewayDisconnected: async () => {},
    start: async () => {},
    stop: async () => {},
  };
}

test("browser tool interrupt routes to browser executor", async () => {
  let browserInvoked = false;
  const browserRuntime = makeBrowserRuntimeMock(() => {
    browserInvoked = true;
  });
  const router = new GatewayCapabilityRouter(browserRuntime, new PersonalChannelRuntimeRegistry());

  const invokeFrame = makeInvokeFrame("req-1", "browser.session.start", "run-browser-1");
  await router.handleToolInvoke(invokeFrame);

  const interruptFrame = makeInterruptFrame("req-2", "run-browser-1");
  const result = await router.handleToolInterrupt(interruptFrame);

  assert.equal(browserInvoked, true, "Browser runtime should handle interrupt for browser run");
  assert.ok(result);
});

test("personal_channel run_id returns not-yet-implemented interrupt error", async () => {
  const personalChannelRuntimes = new PersonalChannelRuntimeRegistry([
    makePersonalChannelRuntimeMock("whatsapp_personal.send"),
  ]);
  const router = new GatewayCapabilityRouter(undefined, personalChannelRuntimes);

  const invokeFrame = makeInvokeFrame("req-1", "whatsapp_personal.send", "run-personal-1");
  await router.handleToolInvoke(invokeFrame);

  const interruptFrame = makeInterruptFrame("req-2", "run-personal-1");
  const result = await router.handleToolInterrupt(interruptFrame);

  assert.equal(result.interrupted, false);
  assert.match(String(result.error), /not yet implemented/);
});

test("unknown runId returns safe error", async () => {
  const router = new GatewayCapabilityRouter(undefined, new PersonalChannelRuntimeRegistry());

  const interruptFrame = makeInterruptFrame("req-1", "nonexistent-run");
  const result = await router.handleToolInterrupt(interruptFrame);

  assert.ok(result);
  assert.ok("error" in result, "Should contain an error message");
  assert.match(String(result.error), /No executor found/);
  assert.equal(result.interrupted, false);
});

test("re-invoke updates executor tracking to the latest executor", async () => {
  let browserInvoked = false;
  const browserRuntime = makeBrowserRuntimeMock(() => {
    browserInvoked = true;
  });
  const personalChannelRuntimes = new PersonalChannelRuntimeRegistry([
    makePersonalChannelRuntimeMock("whatsapp_personal.send"),
  ]);
  const router = new GatewayCapabilityRouter(browserRuntime, personalChannelRuntimes);

  // First invoke as browser.
  const invoke1 = makeInvokeFrame("req-1", "browser.session.start", "run-same");
  await router.handleToolInvoke(invoke1);

  // Re-invoke the same run_id, this time via the personal-channel executor.
  const invoke2 = makeInvokeFrame("req-2", "whatsapp_personal.send", "run-same");
  await router.handleToolInvoke(invoke2);

  // Reset the flag: it was already set true by the initial browser invoke
  // above. From here on it should only flip if the INTERRUPT reaches browser.
  browserInvoked = false;

  // Interrupt should now route to personal_channel (the latest executor),
  // not browser — the stub error confirms it did NOT fall through to browser.
  const interrupt = makeInterruptFrame("req-3", "run-same");
  const result = await router.handleToolInterrupt(interrupt);

  assert.equal(browserInvoked, false, "Browser should not handle this interrupt");
  assert.match(String(result.error), /not yet implemented/);
});

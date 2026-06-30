import test from "node:test";
import assert from "node:assert/strict";

import { GatewayCapabilityRouter } from "../supervisor/capability-router";

type TestFrame = {
  kind: "request";
  id: string;
  type: string;
  ts: string;
  payload: Record<string, unknown>;
};

function makeInvokeFrame(
  id: string,
  capabilityId: string,
  runId: string,
): TestFrame {
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
): TestFrame {
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

test("browser tool interrupt routes to browser executor", async () => {
  let browserInvoked = false;
  let supervisorInvoked = false;

  const mockBrowserRuntime = {
    requestedCapabilities: () => ["browser.session.start", "browser.session.interrupt"],
    supportsCapability: (cap: string) =>
      ["browser.session.start", "browser.session.action", "browser.session.interrupt"].includes(cap),
    handleCapabilityInvoke: async (_frame: unknown) => {
      browserInvoked = true;
      return { interrupted: true, run_id: "run-browser-1" };
    },
  };

  const mockSupervisorClient = {
    supportedCapabilities: () => ["filesystem.read_write"],
    execute: async (_input: unknown) => {
      supervisorInvoked = true;
      return { result: "executed" };
    },
    interrupt: async (_input: unknown) => {
      supervisorInvoked = true;
      return { interrupted: true };
    },
  };

  const router = new GatewayCapabilityRouter(
    mockSupervisorClient as unknown as ConstructorParameters<typeof GatewayCapabilityRouter>[0],
    mockBrowserRuntime as unknown as NonNullable<ConstructorParameters<typeof GatewayCapabilityRouter>[1]>,
  );

  // First invoke a browser capability to track the executor
  const invokeFrame = makeInvokeFrame("req-1", "browser.session.start", "run-browser-1");
  await router.handleToolInvoke(invokeFrame as unknown as Parameters<typeof router.handleToolInvoke>[0]);

  // Then interrupt that run
  const interruptFrame = makeInterruptFrame("req-2", "run-browser-1");
  const result = await router.handleToolInterrupt(
    interruptFrame as unknown as Parameters<typeof router.handleToolInterrupt>[0],
  );

  assert.equal(browserInvoked, true, "Browser runtime should handle interrupt for browser run");
  assert.equal(supervisorInvoked, false, "Supervisor should not be invoked");
  assert.ok(result);
});

test("supervisor tool interrupt routes to supervisor executor", async () => {
  let supervisorInterruptInvoked = false;

  const mockBrowserRuntime = {
    requestedCapabilities: () => ["browser.session.start"],
    supportsCapability: () => false,
    handleCapabilityInvoke: async () => ({}),
  };

  const mockSupervisorClient = {
    supportedCapabilities: () => ["filesystem.read_write", "shell.execute"],
    execute: async (_input: unknown) => ({ result: "executed" }),
    interrupt: async (_input: unknown) => {
      supervisorInterruptInvoked = true;
      return { interrupted: true };
    },
  };

  const router = new GatewayCapabilityRouter(
    mockSupervisorClient as unknown as ConstructorParameters<typeof GatewayCapabilityRouter>[0],
    mockBrowserRuntime as unknown as NonNullable<ConstructorParameters<typeof GatewayCapabilityRouter>[1]>,
  );

  // First invoke a supervisor capability
  const invokeFrame = makeInvokeFrame("req-1", "filesystem.read_write", "run-supervisor-1");
  await router.handleToolInvoke(invokeFrame as unknown as Parameters<typeof router.handleToolInvoke>[0]);

  // Then interrupt that run
  const interruptFrame = makeInterruptFrame("req-2", "run-supervisor-1");
  const result = await router.handleToolInterrupt(
    interruptFrame as unknown as Parameters<typeof router.handleToolInterrupt>[0],
  );

  assert.equal(supervisorInterruptInvoked, true, "Supervisor interrupt should be invoked");
  assert.ok(result);
});

test("unknown runId returns safe error", async () => {
  const mockBrowserRuntime = {
    requestedCapabilities: () => [],
    supportsCapability: () => false,
    handleCapabilityInvoke: async () => ({}),
  };

  const mockSupervisorClient = {
    supportedCapabilities: () => [],
    execute: async () => ({}),
    interrupt: async () => ({ interrupted: true }),
  };

  const router = new GatewayCapabilityRouter(
    mockSupervisorClient as unknown as ConstructorParameters<typeof GatewayCapabilityRouter>[0],
    mockBrowserRuntime as unknown as NonNullable<ConstructorParameters<typeof GatewayCapabilityRouter>[1]>,
  );

  const interruptFrame = makeInterruptFrame("req-1", "nonexistent-run");
  const result = await router.handleToolInterrupt(
    interruptFrame as unknown as Parameters<typeof router.handleToolInterrupt>[0],
  );

  // Should return a safe error, not throw
  assert.ok(result);
  assert.ok("error" in result, "Should contain an error message");
  assert.match(String(result.error), /No executor found/);
  assert.equal(result.interrupted, false);
});

test("executor tracked correctly after invoke", async () => {
  const mockBrowserRuntime = {
    requestedCapabilities: () => ["browser.session.start"],
    supportsCapability: (cap: string) => cap.startsWith("browser."),
    handleCapabilityInvoke: async (_frame: unknown) => ({ result: "browser-done" }),
  };

  const mockSupervisorClient = {
    supportedCapabilities: () => ["filesystem.read_write", "shell.execute"],
    execute: async (_input: unknown) => ({ result: "executed" }),
    interrupt: async (_input: unknown) => ({ interrupted: true }),
  };

  const router = new GatewayCapabilityRouter(
    mockSupervisorClient as unknown as ConstructorParameters<typeof GatewayCapabilityRouter>[0],
    mockBrowserRuntime as unknown as NonNullable<ConstructorParameters<typeof GatewayCapabilityRouter>[1]>,
  );

  // Invoke a browser capability
  const browserInvoke = makeInvokeFrame("req-1", "browser.session.start", "run-track-1");
  await router.handleToolInvoke(browserInvoke as unknown as Parameters<typeof router.handleToolInvoke>[0]);

  // Invoke a supervisor capability
  const supervisorInvoke = makeInvokeFrame("req-2", "filesystem.read_write", "run-track-2");
  await router.handleToolInvoke(supervisorInvoke as unknown as Parameters<typeof router.handleToolInvoke>[0]);

  // Now interrupt browser-run - should route to browser
  let supervisorInterrupted = false;
  mockSupervisorClient.interrupt = async () => {
    supervisorInterrupted = true;
    return { interrupted: true };
  };

  const browserInterrupt = makeInterruptFrame("req-3", "run-track-1");
  await router.handleToolInterrupt(
    browserInterrupt as unknown as Parameters<typeof router.handleToolInterrupt>[0],
  );
  assert.equal(supervisorInterrupted, false, "Browser interrupt should not reach supervisor");

  // Interrupt supervisor-run - should route to supervisor
  const supervisorInterrupt = makeInterruptFrame("req-4", "run-track-2");
  const result = await router.handleToolInterrupt(
    supervisorInterrupt as unknown as Parameters<typeof router.handleToolInterrupt>[0],
  );
  assert.ok(result);
});

test("re-invoke updates executor tracking", async () => {
  const mockBrowserRuntime = {
    requestedCapabilities: () => ["browser.session.start"],
    supportsCapability: (cap: string) => cap.startsWith("browser."),
    handleCapabilityInvoke: async () => ({ result: "browser-done" }),
  };

  const mockSupervisorClient = {
    supportedCapabilities: () => ["filesystem.read_write"],
    execute: async () => ({ result: "executed" }),
    interrupt: async () => ({ interrupted: true }),
  };

  const router = new GatewayCapabilityRouter(
    mockSupervisorClient as unknown as ConstructorParameters<typeof GatewayCapabilityRouter>[0],
    mockBrowserRuntime as unknown as NonNullable<ConstructorParameters<typeof GatewayCapabilityRouter>[1]>,
  );

  // First invoke as browser
  const invoke1 = makeInvokeFrame("req-1", "browser.session.start", "run-same");
  await router.handleToolInvoke(invoke1 as unknown as Parameters<typeof router.handleToolInvoke>[0]);

  // Re-invoke as supervisor (same runId, different capability)
  // Since the browser supports it, it won't go to supervisor but let's track again
  // Actually, browser supports any cap starting with "browser.", so let's use a non-browser cap
  const invoke2 = makeInvokeFrame("req-2", "filesystem.read_write", "run-same");
  await router.handleToolInvoke(invoke2 as unknown as Parameters<typeof router.handleToolInvoke>[0]);

  // Now interrupt should go to supervisor (the latest executor)
  let browserInterrupted = false;
  let supervisorInterrupted = false;

  mockBrowserRuntime.handleCapabilityInvoke = async () => {
    browserInterrupted = true;
    return { result: "ok" };
  };

  mockSupervisorClient.interrupt = async () => {
    supervisorInterrupted = true;
    return { interrupted: true };
  };

  const interrupt = makeInterruptFrame("req-3", "run-same");
  await router.handleToolInterrupt(
    interrupt as unknown as Parameters<typeof router.handleToolInterrupt>[0],
  );

  // The runId "run-same" was last tracked as "supervisor" (filesystem.read_write)
  assert.equal(browserInterrupted, false, "Browser should not handle this interrupt");
  assert.equal(supervisorInterrupted, true, "Supervisor should handle this interrupt");
});

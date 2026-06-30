import test from "node:test";
import assert from "node:assert/strict";

import { collectPassiveInventorySnapshot } from "../health/service-inventory";
import { buildRuntimeMetadata } from "../runtime/runtime-metadata";
import { GatewayCapabilityRouter } from "../supervisor/capability-router";
import { GatewaySupervisorClient } from "../supervisor/client";
import type { GatewayBrowserRuntime } from "../browser/runtime";

async function withServiceModeEnv<T>(
  env: Record<string, string | undefined>,
  callback: () => T | Promise<T>,
): Promise<T> {
  const keys = [
    "EMPYRALIS_AGENT_COMPUTER_SYSTEM_SERVICE_MODE",
    "EMPYRALIS_AGENT_COMPUTER_SERVICE_MODE",
    "EMPYRALIS_AGENT_COMPUTER_USER_SESSION_BRIDGE",
    "EMPYRALIS_AGENT_COMPUTER_USER_SESSION_BRIDGE_READY",
    "EMPYRALIS_AGENT_COMPUTER_PERMISSION_SCREEN_RECORDING",
    "EMPYRALIS_AGENT_COMPUTER_PERMISSION_ACCESSIBILITY",
    "EMPYRALIS_AGENT_COMPUTER_PERMISSION_BROWSER",
  ];
  const previous = Object.fromEntries(keys.map((key) => [key, process.env[key]]));
  try {
    for (const key of keys) {
      delete process.env[key];
    }
    for (const [key, value] of Object.entries(env)) {
      if (value === undefined) {
        delete process.env[key];
      } else {
        process.env[key] = value;
      }
    }
    return await callback();
  } finally {
    for (const key of keys) {
      const value = previous[key];
      if (value === undefined) {
        delete process.env[key];
      } else {
        process.env[key] = value;
      }
    }
  }
}

test("system service mode reports native runtime without desktop session", async () => {
  await withServiceModeEnv({ EMPYRALIS_AGENT_COMPUTER_SYSTEM_SERVICE_MODE: "1" }, async () => {
    const metadata = buildRuntimeMetadata("test", ["shell.execute"]);
    assert.equal(metadata.nativeRuntime.system_service_mode, true);
    assert.equal(metadata.nativeRuntime.desktop_session, "system_service");

    const snapshot = await collectPassiveInventorySnapshot({
      requestedCapabilities: ["shell.execute"],
      deps: {
        env: { EMPYRALIS_AGENT_COMPUTER_SYSTEM_SERVICE_MODE: "1" },
        platform: "linux",
        arch: "x64",
        release: "6.0-test",
        hostname: "server-agent",
        now: () => new Date("2026-05-29T00:00:00Z"),
        commandExists: () => null,
        runCommand: async () => ({ exitCode: 1, stdout: "", stderr: "unexpected" }),
        httpGetJson: async () => ({ ok: false, status: 0, error: "offline" }),
      },
    });
    assert.equal(snapshot.native_runtime.system_service_mode, true);
    assert.equal(snapshot.native_runtime.desktop_session, "system_service");
  });
});

test("system service mode does not advertise desktop-only capabilities without bridge", async () => {
  await withServiceModeEnv({ EMPYRALIS_AGENT_COMPUTER_SYSTEM_SERVICE_MODE: "1" }, () => {
    const client = new GatewaySupervisorClient({
      supervisorUrl: "http://127.0.0.1:7788",
      supervisorSecret: "secret",
      supervisorTimeoutMs: 100,
    });
    const capabilities = client.supportedCapabilities();
    assert.deepEqual(capabilities, ["filesystem.read_write", "shell.execute"]);

    const router = new GatewayCapabilityRouter(
      { supportedCapabilities: () => capabilities } as unknown as GatewaySupervisorClient,
      { requestedCapabilities: () => ["browser.session.start"] } as unknown as GatewayBrowserRuntime,
    );
    assert.deepEqual(router.supportedCapabilities(), [
      "filesystem.read_write",
      "shell.execute",
      "external_agent_proxy",
    ]);
  });
});

test("user-session bridge can restore desktop capability advertisement", async () => {
  await withServiceModeEnv({
    EMPYRALIS_AGENT_COMPUTER_SYSTEM_SERVICE_MODE: "1",
    EMPYRALIS_AGENT_COMPUTER_USER_SESSION_BRIDGE_READY: "1",
  }, () => {
    const client = new GatewaySupervisorClient({
      supervisorUrl: "http://127.0.0.1:7788",
      supervisorSecret: "secret",
      supervisorTimeoutMs: 100,
    });
    assert.ok(client.supportedCapabilities().includes("screenshot.capture"));

    const router = new GatewayCapabilityRouter(
      { supportedCapabilities: () => ["filesystem.read_write"] } as unknown as GatewaySupervisorClient,
      { requestedCapabilities: () => ["browser.session.start"] } as unknown as GatewayBrowserRuntime,
    );
    assert.ok(router.supportedCapabilities().includes("browser.session.start"));
  });
});

test("explicit desktop permission denial removes native action capabilities", async () => {
  await withServiceModeEnv({
    EMPYRALIS_AGENT_COMPUTER_PERMISSION_ACCESSIBILITY: "denied",
    EMPYRALIS_AGENT_COMPUTER_PERMISSION_BROWSER: "restricted",
  }, async () => {
    const client = new GatewaySupervisorClient({
      supervisorUrl: "http://127.0.0.1:7788",
      supervisorSecret: "secret",
      supervisorTimeoutMs: 100,
    });
    const capabilities = client.supportedCapabilities();
    assert.ok(!capabilities.includes("computer_control.click"));
    assert.ok(capabilities.includes("screenshot.capture"));

    const router = new GatewayCapabilityRouter(
      { supportedCapabilities: () => capabilities } as unknown as GatewaySupervisorClient,
      { requestedCapabilities: () => ["browser.session.start"] } as unknown as GatewayBrowserRuntime,
    );
    assert.ok(!router.supportedCapabilities().includes("browser.session.start"));

    const snapshot = await collectPassiveInventorySnapshot({
      requestedCapabilities: ["shell.execute", "computer_control.click", "browser.session.start"],
      deps: {
        env: {
          EMPYRALIS_AGENT_COMPUTER_PERMISSION_ACCESSIBILITY: "denied",
          EMPYRALIS_AGENT_COMPUTER_PERMISSION_BROWSER: "restricted",
        },
        platform: "darwin",
        arch: "arm64",
        release: "25.0-test",
        hostname: "mac-agent",
        now: () => new Date("2026-05-29T00:00:00Z"),
        commandExists: () => null,
        runCommand: async () => ({ exitCode: 1, stdout: "", stderr: "unexpected" }),
        httpGetJson: async () => ({ ok: false, status: 0, error: "offline" }),
      },
    });
    assert.deepEqual(snapshot.capability_readiness.ready, ["shell.execute"]);
    assert.deepEqual(snapshot.capability_readiness.blocked, ["computer_control.click", "browser.session.start"]);
    assert.equal(snapshot.capability_readiness.permission_states["computer_control.click"].state, "denied");
    assert.equal(snapshot.service_inventory.find((item) => item.id === "desktop_permissions")?.status, "blocked");
  });
});

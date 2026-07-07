import test from "node:test";
import assert from "node:assert/strict";

import { buildFastPassiveInventorySnapshot, collectPassiveInventorySnapshot } from "../health/service-inventory";

test("passive service inventory detects services without enabling execution", async () => {
  const snapshot = await collectPassiveInventorySnapshot({
    requestedCapabilities: ["shell.execute", "screenshot.capture"],
    deps: {
      platform: "linux",
      arch: "x64",
      release: "6.0-test",
      hostname: "agent-box",
      now: () => new Date("2026-05-29T00:00:00Z"),
      commandExists: (command) => {
        if (command === "pg_isready") return "/usr/bin/pg_isready";
        if (command === "docker") return "/usr/bin/docker";
        return null;
      },
      runCommand: async (command) => {
        if (command.endsWith("pg_isready")) return { exitCode: 0, stdout: "", stderr: "" };
        if (command.endsWith("docker")) return { exitCode: 1, stdout: "", stderr: "daemon unavailable" };
        return { exitCode: 1, stdout: "", stderr: "unexpected" };
      },
      httpGetJson: async () => ({ ok: true, status: 200, body: { models: [{ name: "llama" }] } }),
    },
  });

  const byId = Object.fromEntries(snapshot.service_inventory.map((item) => [item.id, item]));
  assert.equal(snapshot.native_runtime.desktop_session, "user_session");
  assert.equal(snapshot.native_runtime.system_service_mode, false);
  assert.equal(byId.postgres.status, "ready");
  assert.equal(byId.docker.status, "offline");
  assert.equal(byId.ollama.status, "ready");
  assert.equal(byId.codex_cli.status, "missing");
  assert.equal(byId.gpu.status, "unknown");
  for (const item of snapshot.service_inventory) {
    assert.equal(item.passive, true);
    assert.equal(item.execution_enabled, false);
  }
  assert.deepEqual(snapshot.capability_readiness.requested, ["shell.execute", "screenshot.capture"]);
  // shell.execute is gated on the shell_sandbox permission, which is only
  // granted when Docker reads ready — this mock reports Docker offline, so
  // shell.execute is correctly blocked here; screenshot.capture is unrelated
  // (screen_recording permission) and stays ready.
  assert.deepEqual(snapshot.capability_readiness.ready, ["screenshot.capture"]);
  assert.deepEqual(snapshot.capability_readiness.blocked, ["shell.execute"]);
  assert.equal(snapshot.capability_readiness.permission_states["shell.execute"].state, "restricted");
  assert.equal(snapshot.capability_readiness.service_statuses.postgres, "ready");
});

test("fast passive inventory snapshot avoids service probes for heartbeat liveness", () => {
  const snapshot = buildFastPassiveInventorySnapshot({
    requestedCapabilities: ["shell.execute", "screenshot.capture"],
    localRunnerReady: true,
    deps: {
      platform: "linux",
      arch: "x64",
      release: "6.0-test",
      hostname: "agent-box",
      now: () => new Date("2026-05-29T00:00:00Z"),
      commandExists: () => {
        throw new Error("fast snapshot must not run command probes");
      },
      runCommand: async () => {
        throw new Error("fast snapshot must not run command probes");
      },
      httpGetJson: async () => {
        throw new Error("fast snapshot must not run HTTP probes");
      },
    },
  });

  assert.equal(snapshot.native_runtime.hostname, "agent-box");
  assert.deepEqual(snapshot.capability_readiness.requested, ["shell.execute", "screenshot.capture"]);
  assert.deepEqual(snapshot.service_inventory.map((item) => item.id), ["local_runner", "desktop_permissions"]);
  assert.equal(snapshot.capability_readiness.service_statuses.local_runner, "ready");
});

test("local runner readiness blocks supervisor-backed capabilities", () => {
  // screenshot.capture and computer_control.click still depend on the old
  // local-runner/supervisor path (desktop control remains out of scope).
  // shell.execute does NOT anymore — it has its own independent,
  // Docker-gated executor now (see the shell_sandbox tests below) — so it's
  // deliberately not used as the example capability in this test.
  const snapshot = buildFastPassiveInventorySnapshot({
    requestedCapabilities: ["computer_control.click", "screenshot.capture"],
    localRunnerReady: false,
    deps: {
      platform: "linux",
      arch: "x64",
      release: "6.0-test",
      hostname: "agent-box",
      now: () => new Date("2026-05-29T00:00:00Z"),
    },
  });

  assert.deepEqual(snapshot.capability_readiness.ready, []);
  assert.deepEqual(snapshot.capability_readiness.blocked, ["computer_control.click", "screenshot.capture"]);
  assert.equal(snapshot.capability_readiness.service_statuses.local_runner, "offline");
});

test("shell_sandbox capabilities are no longer forced-blocked by local runner health", () => {
  // Confirms the collision fix directly: shell.execute must NOT be blocked
  // just because localRunnerReady is false (checkLocalRunnerHealth() in
  // ws-client.ts hardcodes false always) — its readiness now comes from the
  // shell_sandbox permission (Docker), which this test grants explicitly via
  // the env override, independent of local-runner health entirely.
  const snapshot = buildFastPassiveInventorySnapshot({
    requestedCapabilities: ["shell.execute", "screenshot.capture"],
    localRunnerReady: false,
    deps: {
      platform: "linux",
      arch: "x64",
      release: "6.0-test",
      hostname: "agent-box",
      now: () => new Date("2026-05-29T00:00:00Z"),
      env: { EMPYRALIS_AGENT_COMPUTER_PERMISSION_SHELL_SANDBOX: "granted" },
    },
  });

  assert.ok(snapshot.capability_readiness.ready.includes("shell.execute"));
  assert.equal(snapshot.capability_readiness.blocked.includes("shell.execute"), false);
});

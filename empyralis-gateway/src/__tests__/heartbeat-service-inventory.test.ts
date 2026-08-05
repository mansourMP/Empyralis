import test from "node:test";
import assert from "node:assert/strict";

import { buildGatewayHeartbeatPayload } from "../cloud/heartbeat-payload";
import type { GatewayRuntimeMetadata } from "../runtime/runtime-metadata";
import type { PassiveInventorySnapshot } from "../health/service-inventory";
import type { GatewayResourceMetrics } from "../health/resource-metrics";

const TEST_RESOURCES: GatewayResourceMetrics = {
  cpu_pct: 12.5,
  memory_used_bytes: 4_000_000_000,
  memory_total_bytes: 16_000_000_000,
  gpu_pct: null,
  temperature_c: null,
  sampled_at: "2026-05-29T00:00:00Z",
};

test("gateway heartbeat payload carries passive service inventory separately from execution capabilities", () => {
  const runtimeMetadata: GatewayRuntimeMetadata = {
    gatewayVersion: "0.1.0",
    hostname: "agent-box",
    platform: "linux-x64",
    pid: 123,
    startedAt: "2026-05-29T00:00:00Z",
    requestedCapabilities: ["shell.execute"],
    nativeRuntime: {
      os: "linux",
      arch: "x64",
      release: "6.0-test",
      hostname: "agent-box",
      desktop_session: "user_session",
      system_service_mode: false,
    },
    deviceMetadata: {},
  };
  const inventory: PassiveInventorySnapshot = {
    service_inventory: [
      {
        id: "postgres",
        label: "Postgres",
        kind: "database",
        status: "ready",
        detected: true,
        passive: true,
        execution_enabled: false,
        check: "pg_isready -q",
        summary: "ready",
        last_checked_at: "2026-05-29T00:00:00Z",
      },
    ],
    native_runtime: runtimeMetadata.nativeRuntime,
    capability_readiness: {
      requested: ["shell.execute"],
      ready: ["shell.execute"],
      blocked: [],
      permission_states: {},
      passive_services: ["postgres"],
      service_statuses: { postgres: "ready" },
      shell_full_access_locally_enabled: true,
    },
  };

  const payload = buildGatewayHeartbeatPayload({
    runtimeMetadata,
    inventory,
    journalCursor: 9,
    checkpointCursor: 7,
    queueDepthSummary: { pending: 0 },
    healthState: "online",
    resources: TEST_RESOURCES,
  });

  assert.equal(payload.health_state, "online");
  assert.equal(payload.journal_cursor, 9);
  assert.equal(payload.checkpoint_cursor, 7);
  assert.deepEqual((payload.capability_readiness as any).requested, ["shell.execute"]);
  assert.deepEqual((payload.capability_readiness as any).blocked, []);
  assert.deepEqual((payload.capability_readiness as any).permission_states, {});
  assert.deepEqual((payload.capability_readiness as any).passive_services, ["postgres"]);
  // The box operator's own live full_access opt-in must reach the wire
  // alongside the rest of capability_readiness — see heartbeat-payload.ts's
  // doc comment on this field for why it can't just be inferred from
  // runtime_access_mode server-side.
  assert.equal((payload.capability_readiness as any).shell_full_access_locally_enabled, true);
  assert.equal(((payload.service_inventory as any[])[0]).passive, true);
  assert.equal(((payload.service_inventory as any[])[0]).execution_enabled, false);
  assert.equal((payload.native_runtime as any).system_service_mode, false);
  assert.deepEqual(payload.resources, TEST_RESOURCES);
});

test("gateway heartbeat payload transmits the gateway's real health state instead of a hardcoded literal", () => {
  // Regression test: health_state used to be hardcoded to "online" in
  // buildGatewayHeartbeatPayload() (cloud/heartbeat-payload.ts) regardless
  // of the gateway's actual local state, so the backend/UI could be told
  // "online" while the gateway was actually degraded or reconnecting. It
  // must now faithfully echo whatever GatewayCheckpoints.currentHealthState()
  // reports at send time — see cloud/ws-client.ts sendHeartbeat().
  const runtimeMetadata: GatewayRuntimeMetadata = {
    gatewayVersion: "0.1.0",
    hostname: "agent-box",
    platform: "linux-x64",
    pid: 123,
    startedAt: "2026-05-29T00:00:00Z",
    requestedCapabilities: [],
    nativeRuntime: {
      os: "linux",
      arch: "x64",
      release: "6.0-test",
      hostname: "agent-box",
      desktop_session: "user_session",
      system_service_mode: false,
    },
    deviceMetadata: {},
  };
  const inventory: PassiveInventorySnapshot = {
    service_inventory: [],
    native_runtime: runtimeMetadata.nativeRuntime,
    capability_readiness: {
      requested: [],
      ready: [],
      blocked: [],
      permission_states: {},
      passive_services: [],
      service_statuses: {},
      shell_full_access_locally_enabled: false,
    },
  };

  for (const healthState of ["online", "offline", "reconnecting", "degraded"] as const) {
    const payload = buildGatewayHeartbeatPayload({
      runtimeMetadata,
      inventory,
      journalCursor: 0,
      checkpointCursor: 0,
      queueDepthSummary: {},
      healthState,
      resources: TEST_RESOURCES,
    });
    assert.equal(payload.health_state, healthState);
  }
});

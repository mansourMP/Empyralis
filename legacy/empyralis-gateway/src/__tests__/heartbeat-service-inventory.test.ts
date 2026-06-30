import test from "node:test";
import assert from "node:assert/strict";

import { buildGatewayHeartbeatPayload } from "../cloud/heartbeat-payload";
import type { GatewayRuntimeMetadata } from "../runtime/runtime-metadata";
import type { PassiveInventorySnapshot } from "../health/service-inventory";

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
    },
  };

  const payload = buildGatewayHeartbeatPayload({
    runtimeMetadata,
    inventory,
    journalCursor: 9,
    checkpointCursor: 7,
    queueDepthSummary: { pending: 0 },
  });

  assert.equal(payload.health_state, "online");
  assert.equal(payload.journal_cursor, 9);
  assert.equal(payload.checkpoint_cursor, 7);
  assert.deepEqual((payload.capability_readiness as any).requested, ["shell.execute"]);
  assert.deepEqual((payload.capability_readiness as any).blocked, []);
  assert.deepEqual((payload.capability_readiness as any).permission_states, {});
  assert.deepEqual((payload.capability_readiness as any).passive_services, ["postgres"]);
  assert.equal(((payload.service_inventory as any[])[0]).passive, true);
  assert.equal(((payload.service_inventory as any[])[0]).execution_enabled, false);
  assert.equal((payload.native_runtime as any).system_service_mode, false);
});

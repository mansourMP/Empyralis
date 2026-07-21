import type { GatewayRuntimeMetadata } from "../runtime/runtime-metadata";
import type { PassiveInventorySnapshot } from "../health/service-inventory";
import type { GatewayHealthState } from "../state/checkpoints";

export interface GatewayHeartbeatPayloadInput {
  runtimeMetadata: GatewayRuntimeMetadata;
  inventory: PassiveInventorySnapshot;
  journalCursor: number;
  checkpointCursor: number;
  queueDepthSummary: Record<string, unknown>;
  // The gateway's own last-recorded connection health (GatewayCheckpoints.
  // currentHealthState()) — online/offline/reconnecting/degraded. Threaded
  // through explicitly (not hardcoded) so the backend's freshness/staleness
  // logic (server_modules/gateway_health_service.py) and the UI stop being
  // told "online" while the gateway is actually reconnecting or degraded.
  healthState: GatewayHealthState;
}

export function buildGatewayHeartbeatPayload(input: GatewayHeartbeatPayloadInput): Record<string, unknown> {
  return {
    health_state: input.healthState,
    journal_cursor: input.journalCursor,
    checkpoint_cursor: input.checkpointCursor,
    queue_depth_summary: input.queueDepthSummary,
    capability_readiness: {
      requested: input.runtimeMetadata.requestedCapabilities,
      ready: input.inventory.capability_readiness.ready,
      blocked: input.inventory.capability_readiness.blocked,
      permission_states: input.inventory.capability_readiness.permission_states,
      passive_services: input.inventory.capability_readiness.passive_services,
      service_statuses: input.inventory.capability_readiness.service_statuses,
    },
    service_inventory: input.inventory.service_inventory,
    native_runtime: input.inventory.native_runtime,
  };
}

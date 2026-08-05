import type { GatewayRuntimeMetadata } from "../runtime/runtime-metadata";
import type { PassiveInventorySnapshot } from "../health/service-inventory";
import type { GatewayResourceMetrics } from "../health/resource-metrics";
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
  // Live CPU/memory/GPU/temperature sampled by health/resource-metrics.ts —
  // best-effort fields are null, never fabricated. See its module doc.
  resources: GatewayResourceMetrics;
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
      // The box operator's OWN live full_access opt-in (see service-inventory.ts's
      // PassiveInventorySnapshot doc comment) — reported on every heartbeat
      // alongside Docker/Ollama/CLI readiness so the control plane (and Settings >
      // Hardware) can show real local state, not just what runtime_access_mode it
      // authorized server-side.
      shell_full_access_locally_enabled: input.inventory.capability_readiness.shell_full_access_locally_enabled,
    },
    service_inventory: input.inventory.service_inventory,
    native_runtime: input.inventory.native_runtime,
    resources: input.resources,
  };
}

/**
 * connectionPresentation/connectionTone are the one shared place a box's
 * live reachability becomes a StatusChip tone+label — the Hardware list,
 * the machine detail page, and the box picker's <select> suffix all read
 * this instead of each interpreting connection_status their own way.
 *
 * This covers the new "execution_blocked" value (gateway_registry_service.
 * _gateway_connection_payload): a box can have a perfectly live WSS session
 * and heartbeat while its Docker sandbox isn't ready, which must never read
 * "Online" — that would be the exact collapsed-two-facts-into-one-light bug
 * CLAUDE.md already documents once, for the OpenClaw outbound socket.
 *
 * Run: npx tsx lib/workspace/fleet/gateway-box-picker-connection.test.ts
 */

import { connectionPresentation, connectionTone, gatewayIsOnline, type FleetGateway } from "./gateway-box-picker";

let passed = 0;
let failed = 0;

function assert(condition: boolean, label: string): void {
  if (condition) {
    passed++;
  } else {
    failed++;
    console.error(`FAIL: ${label}`);
  }
}

function gateway(connection_status: string): FleetGateway {
  return { gateway_id: "gw-1", connection_status, hardware_kind: "cloud_vps" };
}

// The founder's own bar: a box whose tools would fail must never read
// "Online" — that's the whole point of this state existing.
assert(
  connectionPresentation(gateway("execution_blocked")).label !== "Online",
  "execution_blocked never reads as plain 'Online'",
);
assert(
  connectionPresentation(gateway("execution_blocked")).label.toLowerCase().includes("tools"),
  "execution_blocked's label names the actual problem (tools), not just a vague degraded state",
);
assert(
  connectionPresentation(gateway("execution_blocked")).tone === "degraded",
  "execution_blocked renders amber (degraded), not green (online) or red (error/offline)",
);
assert(
  connectionTone(gateway("execution_blocked")) === "degraded",
  "the box picker's own tone function agrees with connectionPresentation",
);
assert(
  gatewayIsOnline(gateway("execution_blocked")) === false,
  "gatewayIsOnline is false for execution_blocked — it must not be treated as fully online anywhere that reads this helper",
);

// Untouched states still behave exactly as before — this fix only adds a
// branch, it must not change any existing behavior.
assert(connectionPresentation(gateway("online")).label === "Online", "plain online is unaffected");
assert(connectionPresentation(gateway("online")).tone === "online", "plain online tone is unaffected");
assert(connectionPresentation(gateway("degraded")).label === "Degraded", "degraded is unaffected");
assert(connectionPresentation(gateway("reconnecting")).label === "Reconnecting", "reconnecting is unaffected");
assert(connectionPresentation(gateway("revoked")).label === "Revoked", "revoked is unaffected");
assert(connectionPresentation(gateway("offline")).label === "Offline", "offline is unaffected");
assert(connectionPresentation(gateway("")).label === "Offline", "an empty/unknown status still degrades to Offline, not a crash");

// --- Summary ---

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}

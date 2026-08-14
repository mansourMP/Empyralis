/**
 * GatewayPairPanel.tsx unit tests — the "connect your own computer" pairing
 * command.
 *
 * Regression coverage for a dead link found live 2026-08-14:
 * `https://get.empyralis.com/gateway` does not resolve at all (no such
 * domain was ever provisioned — `curl` gets `Could not resolve host`), so
 * every customer who copied the pairing command got nothing. The working
 * installer is served from empyralis.ai — the exact same prebuilt-artifact
 * script the DigitalOcean cloud-init path and the "connect via SSH" path
 * already use (vps_provisioning_service.agent_installer_url() /
 * routes_gateway._remote_agent_computer_setup_command()). Piped to
 * `sudo -E bash`, not `sh`: install-agent-computer.sh opens with
 * `set -Eeuo pipefail`, which /bin/sh (dash, on Ubuntu) rejects outright —
 * this repo's own documented "MUST be bash, not sh" lesson — and the
 * installer requires root, which the old command never granted.
 *
 * Run: npx tsx lib/gateway/gateway-pair-panel.test.ts
 */

import { pairingCommand } from "./GatewayPairPanel";

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

const command = pairingCommand("gpair_test_token", "My Mac", "ws_test", false);
const commandFullAccess = pairingCommand("gpair_test_token", "My Mac", "ws_test", true);

for (const [label, cmd] of [
  ["default (sandbox)", command],
  ["full access", commandFullAccess],
] as const) {
  assert(
    !cmd.includes("get.empyralis.com"),
    `${label}: never references the dead get.empyralis.com domain`,
  );
  assert(
    cmd.includes("https://empyralis.ai/install/agent-computer.sh"),
    `${label}: references the real, live installer URL`,
  );
  assert(
    !/\|\s*sh\b/.test(cmd),
    `${label}: never pipes into a bare "sh" (install-agent-computer.sh requires bash's -o pipefail)`,
  );
  assert(
    /\|\s*sudo\s+-E\s+bash\s*$/.test(cmd),
    `${label}: pipes into "sudo -E bash" (root required; -E carries the export lines across the sudo boundary)`,
  );
  assert(
    cmd.includes(`export EMPYRALIS_GATEWAY_PAIRING_TOKEN="gpair_test_token"`),
    `${label}: still exports the pairing token`,
  );
}

assert(
  commandFullAccess.includes("export EMPYRALIS_GATEWAY_SHELL_FULL_ACCESS_ENABLED=true"),
  "full access: adds the full-access export",
);
assert(
  !command.includes("EMPYRALIS_GATEWAY_SHELL_FULL_ACCESS_ENABLED"),
  "default (sandbox): does not add the full-access export",
);

assert(
  pairingCommand("", "My Mac", "ws_test", false) === "Pairing token unavailable",
  "empty token renders the unavailable placeholder, not a broken command",
);

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}

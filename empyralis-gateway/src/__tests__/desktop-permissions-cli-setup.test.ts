import test from "node:test";
import assert from "node:assert/strict";

import {
  assertCapabilityPermissionReady,
  capabilityPermissionReady,
  capabilityPermissionStatus,
  desktopPermissionForCapability,
  setCliSetupLocallyEnabled,
} from "../runtime/desktop-permissions";

test("cli.install/cli.login.* map to the cli_setup permission", () => {
  assert.equal(desktopPermissionForCapability("cli.install"), "cli_setup");
  assert.equal(desktopPermissionForCapability("cli.login.start"), "cli_setup");
  assert.equal(desktopPermissionForCapability("cli.login.input"), "cli_setup");
});

test("cli_setup has no granted-by-default fallback — restricted until the box operator opts in", () => {
  setCliSetupLocallyEnabled(false);
  const status = capabilityPermissionStatus("cli.install", {});
  assert.equal(status.state, "restricted");
  assert.equal(capabilityPermissionReady("cli.install", {}), false);
  assert.throws(() => assertCapabilityPermissionReady("cli.install", {}), /blocked\/local_permission_denied/);
});

test("cli_setup is granted once the box operator opts in", () => {
  setCliSetupLocallyEnabled(true);
  assert.equal(capabilityPermissionStatus("cli.login.start", {}).state, "granted");
  assert.equal(capabilityPermissionReady("cli.login.start", {}), true);
  assert.doesNotThrow(() => assertCapabilityPermissionReady("cli.login.start", {}));
  setCliSetupLocallyEnabled(false); // restore default for any other tests sharing this process
});

test("an explicit env override wins over the local opt-in flag either direction", () => {
  setCliSetupLocallyEnabled(true);
  assert.equal(
    capabilityPermissionStatus("cli.install", { EMPYRALIS_AGENT_COMPUTER_PERMISSION_CLI_SETUP: "denied" }).state,
    "denied",
  );
  setCliSetupLocallyEnabled(false);
  assert.equal(
    capabilityPermissionStatus("cli.install", { EMPYRALIS_AGENT_COMPUTER_PERMISSION_CLI_SETUP: "granted" }).state,
    "granted",
  );
});

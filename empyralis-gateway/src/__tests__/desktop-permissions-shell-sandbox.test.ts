import test from "node:test";
import assert from "node:assert/strict";

import {
  assertCapabilityPermissionReady,
  capabilityPermissionReady,
  capabilityPermissionStatus,
  desktopPermissionForCapability,
  setShellSandboxDockerReady,
} from "../runtime/desktop-permissions";

test("shell.execute and filesystem.read_write map to the shell_sandbox permission", () => {
  assert.equal(desktopPermissionForCapability("shell.execute"), "shell_sandbox");
  assert.equal(desktopPermissionForCapability("filesystem.read_write"), "shell_sandbox");
});

test("shell_sandbox has no granted-by-default fallback — restricted until Docker is confirmed ready", () => {
  setShellSandboxDockerReady(false);
  const status = capabilityPermissionStatus("shell.execute", {});
  assert.equal(status.state, "restricted");
  assert.equal(capabilityPermissionReady("shell.execute", {}), false);
  assert.throws(() => assertCapabilityPermissionReady("shell.execute", {}), /blocked\/local_permission_denied/);
});

test("shell_sandbox is granted once Docker is confirmed ready", () => {
  setShellSandboxDockerReady(true);
  const status = capabilityPermissionStatus("shell.execute", {});
  assert.equal(status.state, "granted");
  assert.equal(capabilityPermissionReady("shell.execute", {}), true);
  assert.doesNotThrow(() => assertCapabilityPermissionReady("shell.execute", {}));
  setShellSandboxDockerReady(false); // restore default for any other tests sharing this process
});

test("an explicit env override wins over the live Docker-readiness flag either direction", () => {
  setShellSandboxDockerReady(true);
  assert.equal(
    capabilityPermissionStatus("shell.execute", { EMPYRALIS_AGENT_COMPUTER_PERMISSION_SHELL_SANDBOX: "denied" }).state,
    "denied",
  );
  setShellSandboxDockerReady(false);
  assert.equal(
    capabilityPermissionStatus("shell.execute", { EMPYRALIS_AGENT_COMPUTER_PERMISSION_SHELL_SANDBOX: "granted" }).state,
    "granted",
  );
});

test("a capability with no permission mapping is not_applicable, not gated by shell_sandbox", () => {
  const status = capabilityPermissionStatus("some.unrelated.capability", {});
  assert.equal(status.state, "not_applicable");
  assert.equal(capabilityPermissionReady("some.unrelated.capability", {}), true);
});

import test from "node:test";
import assert from "node:assert/strict";

import {
  assertCapabilityPermissionReady,
  capabilityPermissionReady,
  capabilityPermissionStatus,
  desktopPermissionForCapability,
} from "../runtime/desktop-permissions";

// ── INVERTED 2026-08-22 ─────────────────────────────────────────────────
// Every test below used to assert the OPPOSITE: that shell_sandbox stayed
// "restricted" until either a confirmed-ready Docker daemon or the box
// operator's own full_access opt-in unlocked it. That is the behaviour the
// founder rejected — it made Docker's absence mean "I have no permission to
// run this" at the very first of four layers, so the control plane never
// dispatched and the agent told the customer it could not act.
//
// These assertions were not weakened or deleted; they were turned around, so
// this file is now what FAILS if a Docker gate is reintroduced here. Docker
// decides HOW a command runs (shell/execution-isolation.ts), never whether.
// The two module-level flags those tests drove (setShellSandboxDockerReady /
// setShellFullAccessLocallyEnabled) no longer exist, which is why this file
// imports neither.
//
// Coverage that must NOT be lost, and is kept below: the explicit env
// override is still the one real way to turn shell access off on a box, and
// an unmapped capability is still not_applicable rather than swept into this
// permission.

test("shell.execute and filesystem.read_write map to the shell_sandbox permission", () => {
  assert.equal(desktopPermissionForCapability("shell.execute"), "shell_sandbox");
  assert.equal(desktopPermissionForCapability("filesystem.read_write"), "shell_sandbox");
});

test("shell_sandbox is granted with no Docker flag involved at all — there is no readiness gate left to fail", () => {
  const status = capabilityPermissionStatus("shell.execute", {});
  assert.equal(status.state, "granted");
  assert.equal(capabilityPermissionReady("shell.execute", {}), true);
  assert.doesNotThrow(() => assertCapabilityPermissionReady("shell.execute", {}));
});

test("filesystem.read_write is granted on the same terms", () => {
  assert.equal(capabilityPermissionStatus("filesystem.read_write", {}).state, "granted");
  assert.equal(capabilityPermissionReady("filesystem.read_write", {}), true);
  assert.doesNotThrow(() => assertCapabilityPermissionReady("filesystem.read_write", {}));
});

test("the module exports no Docker/full-access permission setter any more", async () => {
  // Structural, because a behavioural test cannot see a flag come back: a
  // reintroduced setter would compile, pass every assertion above (it would
  // default to "Docker ready" or be unused), and only bite a real customer
  // whose Docker is down. If either name returns, this fails loudly and the
  // author has to come read the comment at the top of this file.
  const mod: Record<string, unknown> = await import("../runtime/desktop-permissions");
  assert.equal(mod.setShellSandboxDockerReady, undefined);
  assert.equal(mod.setShellFullAccessLocallyEnabled, undefined);
});

test("an explicit env override still wins in both directions — this is the one real off switch", () => {
  assert.equal(
    capabilityPermissionStatus("shell.execute", { EMPYRALIS_AGENT_COMPUTER_PERMISSION_SHELL_SANDBOX: "denied" }).state,
    "denied",
  );
  assert.equal(capabilityPermissionReady("shell.execute", { EMPYRALIS_AGENT_COMPUTER_PERMISSION_SHELL_SANDBOX: "denied" }), false);
  assert.throws(
    () => assertCapabilityPermissionReady("shell.execute", { EMPYRALIS_AGENT_COMPUTER_PERMISSION_SHELL_SANDBOX: "denied" }),
    /blocked\/local_permission_denied/,
  );
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

import test from "node:test";
import assert from "node:assert/strict";

import {
  FULL_ACCESS_STATEMENT,
  HOST_STATEMENT,
  SANDBOX_STATEMENT,
  resolveExecution,
} from "../shell/execution-isolation";

// The whole point of this module is that "sandboxed and working", "no sandbox
// on this box, running on the machine", and "the owner deliberately turned on
// full access" are THREE facts that must never collapse into fewer — the same
// law CLAUDE.md states for delivery outcomes and invite mail, pointed at
// isolation.

test("the three states are distinguishable in every field that carries meaning", () => {
  const sandbox = resolveExecution("sandbox", true);
  const host = resolveExecution("sandbox", false);
  const fullAccess = resolveExecution("full_access", false);

  const modes = [sandbox.mode, host.mode, fullAccess.mode];
  assert.deepEqual(modes, ["sandbox", "host", "full_access"]);
  assert.equal(new Set(modes).size, 3, "modes must not collapse");

  const statements = [sandbox.statement, host.statement, fullAccess.statement];
  assert.equal(new Set(statements).size, 3, "statements must not collapse");

  const reasons = [sandbox.reason, host.reason, fullAccess.reason];
  assert.equal(new Set(reasons).size, 3, "reasons must not collapse");
});

test("Docker decides HOW, never WHETHER — both sandbox-authorized outcomes are runnable", () => {
  // There is no third return shape, no throw, no null: every branch of this
  // function yields a way to run. That is the founder's ruling in one
  // assertion.
  for (const available of [true, false]) {
    const resolved = resolveExecution("sandbox", available);
    assert.ok(resolved.mode === "sandbox" || resolved.mode === "host");
    assert.ok(resolved.statement.length > 0);
  }
});

test("isolation is `sandbox` ONLY inside a container", () => {
  assert.equal(resolveExecution("sandbox", true).isolation, "sandbox");
  assert.equal(resolveExecution("sandbox", false).isolation, "host");
  assert.equal(resolveExecution("full_access", false).isolation, "host");
  // full_access with Docker also ready is still host execution — the owner
  // asked for the machine, and a container would not be what they turned on.
  assert.equal(resolveExecution("full_access", true).isolation, "host");
  assert.equal(resolveExecution("full_access", true).mode, "full_access");
});

test("a Docker-less box NEVER escalates itself to full_access", () => {
  // The one silent-escalation this design must be incapable of. `host` and
  // `full_access` share an implementation (direct execution) and must stay
  // distinct in what they CLAIM: full_access carries a real two-part opt-in
  // and its own cloud-side policy semantics, and nothing about a missing
  // Docker daemon grants either.
  const host = resolveExecution("sandbox", false);
  assert.notEqual(host.mode, "full_access");
  assert.notEqual(host.statement, FULL_ACCESS_STATEMENT);
  assert.ok(!host.reason.includes("authorized"));
});

test("the statements are the customer's words — no mechanism, no instruction, no error tone", () => {
  const forbidden = [
    "docker run", "docker desktop", "install", "brew", "apt-get", "systemctl", "open -a",
    "permission", "denied", "unavailable", "cannot", "can't", "unable", "failed", "error",
    "retry", "warning",
  ];
  for (const statement of [SANDBOX_STATEMENT, HOST_STATEMENT, FULL_ACCESS_STATEMENT]) {
    const lowered = statement.toLowerCase();
    for (const token of forbidden) {
      assert.ok(!lowered.includes(token), `${JSON.stringify(statement)} must not contain ${JSON.stringify(token)}`);
    }
    assert.ok(statement.endsWith("."), "each statement is one plain sentence");
  }
  // "Docker" itself IS allowed in the host statement, and deliberately so —
  // founder: "we should be honest with the customer... most people already
  // know what Docker is." Naming the real reason is honesty; telling someone
  // to go install it is the lecture this list bans.
  assert.ok(HOST_STATEMENT.includes("Docker"));
});

test("the autostart detail enriches the internal reason and never leaks into the statement", () => {
  const withDetail = resolveExecution("sandbox", false, "Docker is not installed on this computer.");
  const withoutDetail = resolveExecution("sandbox", false);
  assert.match(withDetail.reason, /Docker is not installed on this computer\./);
  assert.notEqual(withDetail.reason, withoutDetail.reason);
  assert.equal(withDetail.statement, withoutDetail.statement);
  assert.equal(withDetail.statement, HOST_STATEMENT);
});

test("a blank or whitespace-only detail does not produce a dangling reason", () => {
  for (const detail of ["", "   ", undefined]) {
    assert.equal(resolveExecution("sandbox", false, detail).reason, "no container sandbox is available on this box");
  }
});

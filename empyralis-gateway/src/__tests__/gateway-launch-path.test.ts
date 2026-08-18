import test from "node:test";
import assert from "node:assert/strict";
import path from "path";

import {
  currentReleaseEntrypoint,
  resolveGatewayLaunchEntrypoint,
} from "../update/gateway-launch-path";
import { resolveGatewayReleaseLayout } from "../update/gateway-release-layout";

const STATE_DIR = "/var/lib/empyralis/agent-computer/gateway";
const LAYOUT = resolveGatewayReleaseLayout({ stateDir: STATE_DIR, env: {} });
const LAYOUT_ENTRY = currentReleaseEntrypoint(LAYOUT);

/** The exact shape production runs today: a fixed source-checkout path,
 *  entirely outside the release layout (MAN-355). */
const FIXED_CHECKOUT_ENTRY = "/opt/empyralis-app/empyralis-gateway/dist/index.js";

test("currentReleaseEntrypoint matches run-gateway's first candidate exactly", () => {
  assert.equal(
    LAYOUT_ENTRY,
    path.join(LAYOUT.currentSymlinkPath, "gateway", "dist", "index.js"),
  );
});

test("a gateway running from a fixed checkout is re-pointed at the release layout when it resolves", () => {
  // THE MAN-355 REGRESSION. Before the fix the supervisor unit was written
  // with the running path verbatim, pinning the fixed checkout forever.
  const resolved = resolveGatewayLaunchEntrypoint({
    runningEntryPath: FIXED_CHECKOUT_ENTRY,
    stateDir: STATE_DIR,
    env: {},
    fileExists: (p) => p === LAYOUT_ENTRY,
  });
  assert.equal(resolved, LAYOUT_ENTRY);
  assert.notEqual(resolved, FIXED_CHECKOUT_ENTRY);
});

test("a dangling or absent `current` falls back to the running entrypoint, never a path that does not exist", () => {
  // The boot-safety property: this is a launcher path on a machine nobody can
  // SSH into, so an unresolvable layout must degrade to the one path known to
  // be working right now — the one this process is running from.
  const resolved = resolveGatewayLaunchEntrypoint({
    runningEntryPath: FIXED_CHECKOUT_ENTRY,
    stateDir: STATE_DIR,
    env: {},
    fileExists: () => false,
  });
  assert.equal(resolved, FIXED_CHECKOUT_ENTRY);
});

test("a probe that throws is treated as 'not usable' and falls back rather than propagating", () => {
  const resolved = resolveGatewayLaunchEntrypoint({
    runningEntryPath: FIXED_CHECKOUT_ENTRY,
    stateDir: STATE_DIR,
    env: {},
    fileExists: () => {
      throw new Error("EACCES");
    },
  });
  assert.equal(resolved, FIXED_CHECKOUT_ENTRY);
});

test("no state dir means no layout to prefer — the running path is the whole answer", () => {
  const resolved = resolveGatewayLaunchEntrypoint({
    runningEntryPath: FIXED_CHECKOUT_ENTRY,
    stateDir: undefined,
    env: {},
    fileExists: () => {
      throw new Error("must not be probed without a state dir");
    },
  });
  assert.equal(resolved, FIXED_CHECKOUT_ENTRY);
});

test("EMPYRALIS_GATEWAY_INSTALL_ROOT is honored, matching run-gateway's own override", () => {
  const env = { EMPYRALIS_GATEWAY_INSTALL_ROOT: "/opt/custom-root" };
  const expected = path.join("/opt/custom-root", "current", "gateway", "dist", "index.js");
  const resolved = resolveGatewayLaunchEntrypoint({
    runningEntryPath: FIXED_CHECKOUT_ENTRY,
    stateDir: STATE_DIR,
    env,
    fileExists: (p) => p === expected,
  });
  assert.equal(resolved, expected);
});

test("a gateway already running THROUGH the layout keeps that path and does not self-probe into a loop", () => {
  const resolved = resolveGatewayLaunchEntrypoint({
    runningEntryPath: LAYOUT_ENTRY,
    stateDir: STATE_DIR,
    env: {},
    fileExists: () => {
      throw new Error("must not probe the path we are already running from");
    },
  });
  assert.equal(resolved, LAYOUT_ENTRY);
});

import test from "node:test";
import assert from "node:assert/strict";
import path from "path";

import {
  gatewayEntrypointFor,
  releaseDirFor,
  resolveGatewayReleaseLayout,
  sanitizeGatewayVersionSegment,
} from "../update/gateway-release-layout";

test("resolveGatewayReleaseLayout defaults to a directory next to the state dir, not the state dir itself", () => {
  const layout = resolveGatewayReleaseLayout({ stateDir: "/var/lib/empyralis/agent-computer/gateway", env: {} });
  assert.equal(layout.installRoot, "/var/lib/empyralis/agent-computer/gateway-releases");
  assert.equal(layout.releasesDir, "/var/lib/empyralis/agent-computer/gateway-releases/releases");
  assert.equal(layout.currentSymlinkPath, "/var/lib/empyralis/agent-computer/gateway-releases/current");
});

test("resolveGatewayReleaseLayout honors an explicit EMPYRALIS_GATEWAY_INSTALL_ROOT override", () => {
  const layout = resolveGatewayReleaseLayout({
    stateDir: "/home/x/.empyralis/gateway",
    env: { EMPYRALIS_GATEWAY_INSTALL_ROOT: "/opt/custom-root" },
  });
  assert.equal(layout.installRoot, "/opt/custom-root");
  assert.equal(layout.currentSymlinkPath, path.join("/opt/custom-root", "current"));
});

test("releaseDirFor / gatewayEntrypointFor build the install-agent-computer.sh-compatible shape", () => {
  const layout = resolveGatewayReleaseLayout({ stateDir: "/home/x/.empyralis/gateway", env: {} });
  const releaseDir = releaseDirFor(layout, "0.2.0");
  assert.equal(releaseDir, path.join(layout.releasesDir, "0.2.0"));
  assert.equal(gatewayEntrypointFor(releaseDir), path.join(releaseDir, "gateway", "dist", "index.js"));
});

test("sanitizeGatewayVersionSegment rejects path traversal / separators from an untrusted target_version argument", () => {
  assert.throws(() => sanitizeGatewayVersionSegment("../../etc"), /Invalid gateway version/);
  assert.throws(() => sanitizeGatewayVersionSegment("a/b"), /Invalid gateway version/);
  assert.throws(() => sanitizeGatewayVersionSegment(".."), /Invalid gateway version/);
  assert.throws(() => sanitizeGatewayVersionSegment(""), /Invalid gateway version/);
  assert.equal(sanitizeGatewayVersionSegment("0.2.0"), "0.2.0");
  assert.equal(sanitizeGatewayVersionSegment("  0.2.0  "), "0.2.0");
});

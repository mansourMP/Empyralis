import test from "node:test";
import assert from "node:assert/strict";

import {
  DESKTOP_GATEWAY_NODE_VERSION,
  desktopGatewayNodeDistUrl,
} from "../update/desktop-node-version";

test("DESKTOP_GATEWAY_NODE_VERSION is an exact x.y.z version, never a range", () => {
  assert.match(DESKTOP_GATEWAY_NODE_VERSION, /^\d+\.\d+\.\d+$/);
});

test("desktopGatewayNodeDistUrl builds the real nodejs.org dist layout for macOS (.tar.gz) and Linux (.tar.xz)", () => {
  delete process.env.EMPYRALIS_NODE_DIST_BASE_URL;
  assert.equal(
    desktopGatewayNodeDistUrl("darwin", "arm64"),
    `https://nodejs.org/dist/v${DESKTOP_GATEWAY_NODE_VERSION}/node-v${DESKTOP_GATEWAY_NODE_VERSION}-darwin-arm64.tar.gz`,
  );
  assert.equal(
    desktopGatewayNodeDistUrl("linux", "x64"),
    `https://nodejs.org/dist/v${DESKTOP_GATEWAY_NODE_VERSION}/node-v${DESKTOP_GATEWAY_NODE_VERSION}-linux-x64.tar.xz`,
  );
});

test("desktopGatewayNodeDistUrl honors EMPYRALIS_NODE_DIST_BASE_URL, same override knob as install-agent-computer.sh", () => {
  process.env.EMPYRALIS_NODE_DIST_BASE_URL = "https://mirror.example.internal/node-dist";
  try {
    assert.equal(
      desktopGatewayNodeDistUrl("linux", "arm64"),
      `https://mirror.example.internal/node-dist/v${DESKTOP_GATEWAY_NODE_VERSION}/node-v${DESKTOP_GATEWAY_NODE_VERSION}-linux-arm64.tar.xz`,
    );
  } finally {
    delete process.env.EMPYRALIS_NODE_DIST_BASE_URL;
  }
});

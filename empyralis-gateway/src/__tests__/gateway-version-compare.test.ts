import test from "node:test";
import assert from "node:assert/strict";

import { isNewerGatewayVersion } from "../update/gateway-version-compare";

test("isNewerGatewayVersion recognizes a strictly newer dotted-numeric version", () => {
  assert.equal(isNewerGatewayVersion("0.1.0", "0.2.0"), true);
  assert.equal(isNewerGatewayVersion("0.1.0", "1.0.0"), true);
  assert.equal(isNewerGatewayVersion("0.1.9", "0.1.10"), true, "must compare numerically, not lexically");
  assert.equal(isNewerGatewayVersion("1.2.3", "1.2.3.1"), true, "a longer version with an extra trailing segment is newer");
});

test("isNewerGatewayVersion is false for equal or older versions", () => {
  assert.equal(isNewerGatewayVersion("0.2.0", "0.2.0"), false);
  assert.equal(isNewerGatewayVersion("0.2.0", "0.1.9"), false);
  assert.equal(isNewerGatewayVersion("1.0.0", "0.9.9"), false);
});

test("isNewerGatewayVersion tolerates a leading 'v' and never throws on garbage input", () => {
  assert.equal(isNewerGatewayVersion("v0.1.0", "v0.2.0"), true);
  assert.equal(isNewerGatewayVersion("garbage", "0.2.0"), false);
  assert.equal(isNewerGatewayVersion("0.1.0", "garbage"), false);
  assert.equal(isNewerGatewayVersion("", ""), false);
});

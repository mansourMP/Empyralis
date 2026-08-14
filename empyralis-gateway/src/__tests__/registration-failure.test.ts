import test from "node:test";
import assert from "node:assert/strict";

import { classifyRegistrationFailure, GatewayRegistrationError } from "../cloud/registration-failure";

/**
 * Regression coverage for the "pairing token is no longer active" restart
 * loop observed live 2026-08-14 (518 restarts and counting on the box it
 * was found on). classifyRegistrationFailure is the whole classification
 * decision GatewayWsClient.registerFromPairing() and index.ts's
 * attemptGatewayPairing() hang off — see gateway-pairing-permanent-
 * failure.test.ts for the end-to-end wiring proof.
 */

test("no response received (network failure) is classified retryable", () => {
  const classification = classifyRegistrationFailure(undefined);
  assert.equal(classification.retryable, true);
  assert.equal(classification.code, "network_error");
});

test("429 (rate limited) is classified retryable", () => {
  const classification = classifyRegistrationFailure(429);
  assert.equal(classification.retryable, true);
  assert.equal(classification.code, "http_429");
});

test("every 5xx is classified retryable", () => {
  for (const status of [500, 502, 503, 504, 599]) {
    const classification = classifyRegistrationFailure(status);
    assert.equal(classification.retryable, true, `status ${status} should be retryable`);
    assert.equal(classification.code, "http_5xx");
  }
});

test("every 4xx other than 429 is classified permanent — the exact shape of the observed bug (400)", () => {
  for (const status of [400, 401, 403, 404, 409, 410, 422]) {
    const classification = classifyRegistrationFailure(status);
    assert.equal(classification.retryable, false, `status ${status} should be permanent`);
    assert.equal(classification.code, "http_4xx");
  }
});

test("an unrecognized status defaults to PERMANENT, per this repo's own standing rule for unknown codes", () => {
  for (const status of [100, 204, 301, 999]) {
    const classification = classifyRegistrationFailure(status);
    assert.equal(classification.retryable, false, `status ${status} should default to permanent`);
    assert.equal(classification.code, "http_unexpected");
  }
});

test("GatewayRegistrationError derives retryable/code from the status, never from the message text", () => {
  const sameMessageDifferentStatus400 = new GatewayRegistrationError("Gateway registration failed with status 400: Pairing token is no longer active.", {
    status: 400,
    detail: "Pairing token is no longer active.",
  });
  assert.equal(sameMessageDifferentStatus400.retryable, false);
  assert.equal(sameMessageDifferentStatus400.code, "http_4xx");
  assert.equal(sameMessageDifferentStatus400.detail, "Pairing token is no longer active.");

  // A network failure carries no status and no server-authored sentence at
  // all — classification must not depend on parsing any text.
  const networkFailure = new GatewayRegistrationError(
    "Gateway registration request failed before a response was received: fetch failed",
    { status: undefined, detail: "fetch failed" },
  );
  assert.equal(networkFailure.retryable, true);
  assert.equal(networkFailure.code, "network_error");
});

test("GatewayRegistrationError is a real Error subclass (instanceof works through a catch)", () => {
  try {
    throw new GatewayRegistrationError("boom", { status: 500, detail: "boom" });
  } catch (error) {
    assert.ok(error instanceof Error);
    assert.ok(error instanceof GatewayRegistrationError);
    if (error instanceof GatewayRegistrationError) {
      assert.equal(error.retryable, true);
    }
  }
});

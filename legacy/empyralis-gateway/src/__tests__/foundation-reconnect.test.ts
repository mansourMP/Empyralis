import test from "node:test";
import assert from "node:assert/strict";
import { computeReconnectDelay, normalizeStatusCode, DEFAULT_RECONNECT_POLICY } from "../channels/foundation/reconnect-utils";

test("computeReconnectDelay is at least initialDelayMs on first attempt", () => {
  const delay = computeReconnectDelay(0);
  assert.ok(delay >= DEFAULT_RECONNECT_POLICY.initialDelayMs,
    `delay ${delay} should be >= initialDelayMs ${DEFAULT_RECONNECT_POLICY.initialDelayMs}`);
});

test("computeReconnectDelay increases with attempts", () => {
  // Use zero-jitter policy for deterministic test
  const policy = { initialDelayMs: 1000, maxDelayMs: 30000, factor: 2, jitterRatio: 0, maxAttempts: 12 };
  const d0 = computeReconnectDelay(0, policy);
  const d3 = computeReconnectDelay(3, policy);
  assert.ok(d3 >= d0 * 2, `delay at attempt 3 (${d3}) should be >= 2x delay at attempt 0 (${d0})`);
});

test("computeReconnectDelay caps at maxDelayMs", () => {
  const delay = computeReconnectDelay(100, {
    initialDelayMs: 1000,
    maxDelayMs: 30000,
    factor: 2,
    jitterRatio: 0.1,
    maxAttempts: 200,
  });
  assert.ok(delay <= 30000, `delay ${delay} should not exceed maxDelayMs 30000`);
});

test("computeReconnectDelay respects custom policy floor", () => {
  const delay = computeReconnectDelay(0, {
    initialDelayMs: 5000,
    maxDelayMs: 60000,
    factor: 2,
    jitterRatio: 0.25,
    maxAttempts: 10,
  });
  assert.ok(delay >= 5000, `delay ${delay} should be >= custom initialDelayMs 5000`);
});

test("normalizeStatusCode returns number for finite numbers", () => {
  assert.equal(normalizeStatusCode(400), 400);
  assert.equal(normalizeStatusCode(0), 0);
});

test("normalizeStatusCode returns undefined for NaN/Infinity/undefined", () => {
  assert.equal(normalizeStatusCode(NaN), undefined);
  assert.equal(normalizeStatusCode(Infinity), undefined);
  assert.equal(normalizeStatusCode(undefined), undefined);
});

test("normalizeStatusCode returns undefined for non-number strings", () => {
  assert.equal(normalizeStatusCode("hello"), undefined);
});

test("normalizeStatusCode returns number for numeric strings", () => {
  assert.equal(normalizeStatusCode("42"), 42);
});

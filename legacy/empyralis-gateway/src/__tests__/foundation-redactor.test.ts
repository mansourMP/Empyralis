import test from "node:test";
import assert from "node:assert/strict";
import { redactCredentials } from "../channels/foundation/credential-redactor";

test("redacts stringKeys anywhere in tree", () => {
  const state = { config: { apiHash: "abc123", name: "bob" }, phoneNumber: "555" };
  const result = redactCredentials(state, ["apiHash", "phoneNumber"], []);
  assert.equal(result.phoneNumber, "[REDACTED]");
  assert.equal((result.config as Record<string, unknown>).apiHash, "[REDACTED]");
  assert.equal((result.config as Record<string, unknown>).name, "bob");
});

test("redacts objectKeys (objects and arrays)", () => {
  const state = { authState: { token: "x" }, signalIdentities: [{ id: "a" }], keys: { k: 1 } };
  const result = redactCredentials(state, [], ["authState", "signalIdentities", "keys"]);
  assert.deepStrictEqual(result.authState, { redacted: true });
  assert.deepStrictEqual(result.signalIdentities, { redacted: true });
  assert.deepStrictEqual(result.keys, { redacted: true });
});

test("does not mutate original", () => {
  const state = { apiHash: "secret" };
  const result = redactCredentials(state, ["apiHash"], []);
  assert.equal(state.apiHash, "secret");
  assert.equal(result.apiHash, "[REDACTED]");
});

test("handles null values gracefully", () => {
  const state = { token: null, apiHash: "x" };
  const result = redactCredentials(state, ["apiHash"], ["token"]);
  assert.equal(result.apiHash, "[REDACTED]");
  assert.equal(result.token, null);
});

test("handles empty state", () => {
  const result = redactCredentials({}, ["key"], ["obj"]);
  assert.deepStrictEqual(result, {});
});

test("handles empty key arrays", () => {
  const state = { apiHash: "visible" };
  const result = redactCredentials(state, [], []);
  assert.equal(result.apiHash, "visible");
});

test("handles deep nesting", () => {
  const state = { a: { b: { c: { apiHash: "deep" } } } };
  const result = redactCredentials(state, ["apiHash"], []);
  const b = (result.a as Record<string, unknown>).b as Record<string, unknown>;
  const c = b.c as Record<string, unknown>;
  assert.equal(c.apiHash, "[REDACTED]");
});

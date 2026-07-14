import test from "node:test";
import assert from "node:assert/strict";
import { resolveTelegramReconnectState } from "../channels/telegram/reconnect";

// FIX 1: a mistyped/expired login code must stop the reconnect loop and
// re-prompt for a code, instead of falling through to the generic
// disconnected+shouldReconnect:true default (the original bug — the wrong
// code got silently resubmitted forever because nothing ever recognized it
// as a *rejected code* rather than a network blip).

test("PHONE_CODE_INVALID stops reconnecting and asks for a new code", () => {
  const state = resolveTelegramReconnectState({ message: "PHONE_CODE_INVALID" });
  assert.equal(state.shouldReconnect, false, "must not auto-retry the same rejected code");
  assert.equal(state.status, "code_required");
  assert.equal(state.loginHint, "phone_code_invalid");
});

test("PHONE_CODE_EXPIRED stops reconnecting and asks for a new code", () => {
  const state = resolveTelegramReconnectState({ message: "PHONE_CODE_EXPIRED" });
  assert.equal(state.shouldReconnect, false);
  assert.equal(state.status, "code_required");
  assert.equal(state.loginHint, "phone_code_expired");
});

test("PHONE_CODE_INVALID matches case-insensitively and inside a wrapped RPC message", () => {
  // Real MTProto client libraries typically wrap the raw code, e.g.
  // "400: PHONE_CODE_INVALID (caused by auth.SignIn)" — the important part
  // is the substring match survives that wrapping, not an exact string match.
  const state = resolveTelegramReconnectState({ message: "400: PHONE_CODE_INVALID (caused by auth.SignIn)" });
  assert.equal(state.shouldReconnect, false);
  assert.equal(state.status, "code_required");
  assert.equal(state.loginHint, "phone_code_invalid");
});

test("a plain Error object (not a {message} bag) is handled the same way", () => {
  const state = resolveTelegramReconnectState(new Error("PHONE_CODE_INVALID"));
  assert.equal(state.shouldReconnect, false);
  assert.equal(state.loginHint, "phone_code_invalid");
});

test("PHONE_CODE_INVALID does not fall into an unrelated earlier branch", () => {
  // Guards the insertion point: must not be shadowed by password_required,
  // logged_out, or authorization_required, which all check broad substrings.
  const state = resolveTelegramReconnectState({ message: "PHONE_CODE_INVALID" });
  assert.notEqual(state.status, "password_required");
  assert.notEqual(state.status, "logged_out");
  assert.notEqual(state.status, "authorization_required");
});

test("a genuine unrecognized network disconnect still auto-reconnects (must not regress)", () => {
  const state = resolveTelegramReconnectState({ message: "ECONNRESET" });
  assert.equal(state.shouldReconnect, true, "real network drops must still auto-reconnect");
  assert.equal(state.status, "disconnected");
});

test("the pre-existing generic 'code required' phrase match still works (unchanged)", () => {
  const state = resolveTelegramReconnectState({ message: "login_code_required" });
  assert.equal(state.shouldReconnect, false);
  assert.equal(state.status, "code_required");
  assert.equal(state.loginHint, "login_code_required");
});

test("password_required is unaffected by the new branches", () => {
  const state = resolveTelegramReconnectState({ message: "SESSION_PASSWORD_NEEDED" });
  assert.equal(state.shouldReconnect, false);
  assert.equal(state.status, "password_required");
});

test("session revoked / logged out is unaffected by the new branches", () => {
  const state = resolveTelegramReconnectState({ message: "session revoked" });
  assert.equal(state.shouldReconnect, false);
  assert.equal(state.status, "logged_out");
});

test("authorization_required (missing credentials) is unaffected by the new branches", () => {
  const state = resolveTelegramReconnectState({ message: "api_credentials_required" });
  assert.equal(state.shouldReconnect, false);
  assert.equal(state.status, "authorization_required");
});

// FIX 2: Telegram's real RPC token for a revoked/kicked session is
// underscore-separated (AUTH_KEY_UNREGISTERED). The logged_out branch only
// matched the space-form "auth key unregistered", so a session Telegram
// actually logged out never got recognized — it fell through to the generic
// disconnected+shouldReconnect:true default, which could leave a dead
// session appearing to still be "connected" instead of prompting re-auth.

test("AUTH_KEY_UNREGISTERED is recognized as logged_out and stops reconnecting", () => {
  const state = resolveTelegramReconnectState({ message: "AUTH_KEY_UNREGISTERED" });
  assert.equal(state.shouldReconnect, false, "must not keep retrying a session Telegram already revoked");
  assert.equal(state.status, "logged_out");
});

test("AUTH_KEY_UNREGISTERED matches case-insensitively and inside a wrapped RPC message", () => {
  const state = resolveTelegramReconnectState({ message: "401: AUTH_KEY_UNREGISTERED (caused by auth.SignIn)" });
  assert.equal(state.shouldReconnect, false);
  assert.equal(state.status, "logged_out");
});

test("AUTH_KEY_UNREGISTERED as a plain Error object is handled the same way", () => {
  const state = resolveTelegramReconnectState(new Error("AUTH_KEY_UNREGISTERED"));
  assert.equal(state.shouldReconnect, false);
  assert.equal(state.status, "logged_out");
});

test("AUTH_KEY_UNREGISTERED does not fall into an unrelated earlier branch", () => {
  const state = resolveTelegramReconnectState({ message: "AUTH_KEY_UNREGISTERED" });
  assert.notEqual(state.status, "code_required");
  assert.notEqual(state.status, "password_required");
  assert.notEqual(state.status, "authorization_required");
  assert.notEqual(state.status, "disconnected");
});

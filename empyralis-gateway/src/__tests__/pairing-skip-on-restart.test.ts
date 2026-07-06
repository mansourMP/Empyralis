import test from "node:test";
import assert from "node:assert/strict";

import { shouldAttemptPairing } from "../index";

test("fresh install: pairing token present, no stored gateway token yet -> attempts pairing", () => {
  assert.equal(shouldAttemptPairing("gpair_fresh", undefined), true);
});

test("restart after successful pairing: stale pairing token still in env, gateway token already stored -> skips pairing", () => {
  // This is the crash-loop scenario: the installer never clears EMPYRALIS_PAIRING_TOKEN,
  // so it is still set on every subsequent boot even though it was already consumed
  // server-side. Once we have our own gatewayToken persisted, re-attempting pairing
  // with that stale token would fail with "no longer active" and crash the process.
  assert.equal(shouldAttemptPairing("gpair_stale_already_consumed", "ggt_persisted"), false);
});

test("no pairing token, no stored token -> does not attempt pairing", () => {
  assert.equal(shouldAttemptPairing(undefined, undefined), false);
});

test("no pairing token, already have stored token -> does not attempt pairing", () => {
  assert.equal(shouldAttemptPairing(undefined, "ggt_persisted"), false);
});

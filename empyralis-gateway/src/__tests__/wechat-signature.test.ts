import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";

import {
  computeWeChatSignature,
  isWeChatTimestampFresh,
  verifyWeChatServerSignature,
} from "../channels/wechat/signature";

function referenceSignature(token: string, timestamp: string, nonce: string): string {
  const parts = [token, timestamp, nonce].sort();
  return createHash("sha1").update(parts.join(""), "utf8").digest("hex");
}

test("computeWeChatSignature matches Tencent's documented sort+join+sha1 algorithm", () => {
  const signature = computeWeChatSignature("mytoken", "1409659589", "263014780");
  assert.equal(signature, referenceSignature("mytoken", "1409659589", "263014780"));
  // Order of the three inputs must not matter to the caller — only the
  // sorted-join order matters internally.
  assert.equal(computeWeChatSignature("mytoken", "1409659589", "263014780"), signature);
});

test("verifyWeChatServerSignature accepts a correctly computed signature", () => {
  const token = "mytoken";
  const timestamp = "1409659589";
  const nonce = "263014780";
  const signature = referenceSignature(token, timestamp, nonce);
  assert.equal(verifyWeChatServerSignature({ token, timestamp, nonce, signature }), true);
});

test("verifyWeChatServerSignature rejects a wrong signature", () => {
  assert.equal(
    verifyWeChatServerSignature({
      token: "mytoken",
      timestamp: "1409659589",
      nonce: "263014780",
      signature: "0000000000000000000000000000000000000000",
    }),
    false,
  );
});

test("verifyWeChatServerSignature rejects when the token used to compute it differs", () => {
  const signature = referenceSignature("mytoken", "1409659589", "263014780");
  assert.equal(
    verifyWeChatServerSignature({ token: "different-token", timestamp: "1409659589", nonce: "263014780", signature }),
    false,
  );
});

test("verifyWeChatServerSignature rejects missing params instead of throwing", () => {
  assert.equal(verifyWeChatServerSignature({ token: "", timestamp: "", nonce: "", signature: "" }), false);
  assert.equal(
    verifyWeChatServerSignature({ token: "mytoken", timestamp: "1409659589", nonce: "263014780", signature: "" }),
    false,
  );
});

test("isWeChatTimestampFresh accepts a current timestamp and rejects a stale one", () => {
  const nowMs = 1_700_000_000_000;
  const currentSeconds = String(Math.floor(nowMs / 1000));
  assert.equal(isWeChatTimestampFresh(currentSeconds, nowMs), true);
  const staleSeconds = String(Math.floor(nowMs / 1000) - 3600);
  assert.equal(isWeChatTimestampFresh(staleSeconds, nowMs), false);
  assert.equal(isWeChatTimestampFresh("not-a-number", nowMs), false);
});

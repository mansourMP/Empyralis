/**
 * Direct unit tests for the CSRF-failure classification/logging logic
 * control-plane-proxy.ts's validateBrowserCsrf() delegates to. See
 * control-plane-proxy.test.ts for the wiring-level proof (a real
 * NextRequest through the real forwardControlPlaneRequest); this file
 * covers the decision logic itself in isolation.
 *
 * Run: npx tsx lib/server/csrf-failure.test.ts
 */

import assert from "node:assert/strict";
import { createHash } from "node:crypto";

import {
  classifyCsrfFailure,
  countRawCookieOccurrences,
  csrfFailureResponseBody,
  fingerprintCsrfValue,
  logCsrfFailure,
} from "./csrf-failure";

let failures = 0;

function test(name: string, fn: () => void): void {
  try {
    fn();
    console.log(`ok - ${name}`);
  } catch (error) {
    failures += 1;
    console.error(`FAIL - ${name}`);
    console.error(error);
  }
}

function main(): void {
  test("both empty -> csrf_cookie_missing (checked before the header)", () => {
    assert.equal(classifyCsrfFailure("", ""), "csrf_cookie_missing");
  });

  test("cookie present, header empty -> csrf_header_missing", () => {
    assert.equal(classifyCsrfFailure("token-abc", ""), "csrf_header_missing");
  });

  test("both present but different -> csrf_mismatch", () => {
    assert.equal(classifyCsrfFailure("token-old", "token-new"), "csrf_mismatch");
  });

  test("both present and equal -> null (no failure)", () => {
    assert.equal(classifyCsrfFailure("token-same", "token-same"), null);
  });

  test("the three codes are pairwise distinct stable strings", () => {
    const codes = [
      classifyCsrfFailure("", ""),
      classifyCsrfFailure("x", ""),
      classifyCsrfFailure("x", "y"),
    ];
    assert.equal(new Set(codes).size, 3, "each branch must produce its own distinct code");
  });

  test("csrfFailureResponseBody keeps the customer-facing message generic and carries the code", () => {
    const body = csrfFailureResponseBody("csrf_mismatch");
    assert.equal(body.detail, "CSRF validation failed.");
    assert.equal(body.code, "csrf_mismatch");
  });

  test("logCsrfFailure names the failure and lists cookie NAMES, never a value", () => {
    const originalError = console.error;
    const logs: unknown[][] = [];
    console.error = (...args: unknown[]) => logs.push(args);
    try {
      logCsrfFailure("csrf_header_missing", {
        path: "/api/workspaces/ws_123",
        method: "PATCH",
        cookieNames: ["empyralis_access_token", "empyralis_csrf_token"],
        csrfCookieValue: "token-old-value",
        csrfHeaderValue: "",
        duplicateCsrfCookieCount: 1,
      });
      assert.equal(logs.length, 1, "expected exactly one console.error call");
      const [message, context] = logs[0] as [string, Record<string, unknown>];
      assert.ok(message.includes("csrf_header_missing"), "the log line must name the failure code");
      assert.deepEqual(context.cookieNames, ["empyralis_access_token", "empyralis_csrf_token"]);
      const serialized = JSON.stringify(logs[0]);
      assert.ok(!serialized.includes("token-"), "a cookie VALUE must never appear in the log");
    } finally {
      console.error = originalError;
    }
  });

  test("logCsrfFailure carries a fingerprint (hash prefix, length, whitespace flag) for each side, independently", () => {
    const originalError = console.error;
    const logs: unknown[][] = [];
    console.error = (...args: unknown[]) => logs.push(args);
    try {
      logCsrfFailure("csrf_mismatch", {
        path: "/api/workspaces/ws_123",
        method: "PATCH",
        cookieNames: ["empyralis_access_token", "empyralis_csrf_token"],
        csrfCookieValue: "token-old ",
        csrfHeaderValue: "token-new",
        duplicateCsrfCookieCount: 0,
      });
      const [, context] = logs[0] as [string, Record<string, unknown>];
      const cookieFingerprint = context.cookie as { length: number; sha256Prefix: string; hasEdgeWhitespace: boolean };
      const headerFingerprint = context.header as { length: number; sha256Prefix: string; hasEdgeWhitespace: boolean };
      assert.equal(cookieFingerprint.length, "token-old ".length);
      assert.equal(cookieFingerprint.hasEdgeWhitespace, true, "trailing space on the cookie value must be flagged");
      assert.equal(headerFingerprint.length, "token-new".length);
      assert.equal(headerFingerprint.hasEdgeWhitespace, false);
      assert.notEqual(
        cookieFingerprint.sha256Prefix,
        headerFingerprint.sha256Prefix,
        "genuinely different values must produce different hash prefixes",
      );
      assert.equal(context.duplicateCsrfCookieCount, 0);
    } finally {
      console.error = originalError;
    }
  });

  test("fingerprintCsrfValue: equal values -> equal prefix and length; a one-way hash never reveals the value", () => {
    const a = fingerprintCsrfValue("csrf-token-same-value");
    const b = fingerprintCsrfValue("csrf-token-same-value");
    assert.deepEqual(a, b);
    assert.equal(a.sha256Prefix.length, 8, "prefix must be exactly 8 hex chars, never the full digest");
    assert.equal(
      a.sha256Prefix,
      createHash("sha256").update("csrf-token-same-value", "utf8").digest("hex").slice(0, 8),
    );
    assert.ok(!JSON.stringify(a).includes("csrf-token-same-value"), "the fingerprint must never embed the raw value");
  });

  test("fingerprintCsrfValue: different values -> different prefixes (the whole point of hashing both sides)", () => {
    const a = fingerprintCsrfValue("token-aaaaaaaaaa");
    const b = fingerprintCsrfValue("token-bbbbbbbbbb");
    assert.notEqual(a.sha256Prefix, b.sha256Prefix);
  });

  test("fingerprintCsrfValue: same value differing only by whitespace -> different length, whitespace flagged, different prefix", () => {
    const clean = fingerprintCsrfValue("abc123");
    const padded = fingerprintCsrfValue(" abc123");
    assert.notEqual(clean.length, padded.length);
    assert.equal(clean.hasEdgeWhitespace, false);
    assert.equal(padded.hasEdgeWhitespace, true);
    // A leading space changes the byte sequence sha256 hashes, so the raw
    // fingerprint also tells "different bytes" apart from "same bytes,
    // whitespace-padded" -- the padded flag is what points at *which* of
    // those two facts is true.
    assert.notEqual(clean.sha256Prefix, padded.sha256Prefix);
  });

  test("fingerprintCsrfValue: empty value -> zero length, empty prefix, never flagged as whitespace", () => {
    const empty = fingerprintCsrfValue("");
    assert.equal(empty.length, 0);
    assert.equal(empty.sha256Prefix, "");
    assert.equal(empty.hasEdgeWhitespace, false);
  });

  test("countRawCookieOccurrences: single cookie -> 1", () => {
    assert.equal(
      countRawCookieOccurrences("empyralis_access_token=AAA; empyralis_csrf_token=CCC", "empyralis_csrf_token"),
      1,
    );
  });

  test("countRawCookieOccurrences: the DUPLICATE-COOKIE hypothesis -> 2", () => {
    // Two `empyralis_csrf_token` entries in one raw Cookie header is exactly
    // what a host-only cookie and a Domain-scoped cookie coexisting under
    // the same name looks like on the wire (see this file's header comment
    // on countRawCookieOccurrences for the mechanism).
    assert.equal(
      countRawCookieOccurrences(
        "empyralis_access_token=AAA; empyralis_csrf_token=host-only-value; empyralis_csrf_token=domain-value",
        "empyralis_csrf_token",
      ),
      2,
    );
  });

  test("countRawCookieOccurrences: absent cookie -> 0", () => {
    assert.equal(countRawCookieOccurrences("empyralis_access_token=AAA", "empyralis_csrf_token"), 0);
  });

  test("countRawCookieOccurrences: empty header -> 0", () => {
    assert.equal(countRawCookieOccurrences("", "empyralis_csrf_token"), 0);
  });

  test("countRawCookieOccurrences: a cookie name that merely CONTAINS the target as a substring must not count", () => {
    assert.equal(
      countRawCookieOccurrences(
        "notempyralis_csrf_token=x; empyralis_csrf_token_extra=y",
        "empyralis_csrf_token",
      ),
      0,
      "neither a prefixed nor a suffixed cookie name is the exact cookie",
    );
  });

  console.log(`\n${failures === 0 ? "All" : failures} csrf-failure test(s)${failures === 0 ? " passed." : " failed."}`);
  if (failures > 0) {
    process.exit(1);
  }
}

main();

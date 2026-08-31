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

import { classifyCsrfFailure, csrfFailureResponseBody, logCsrfFailure } from "./csrf-failure";

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

  console.log(`\n${failures === 0 ? "All" : failures} csrf-failure test(s)${failures === 0 ? " passed." : " failed."}`);
  if (failures > 0) {
    process.exit(1);
  }
}

main();

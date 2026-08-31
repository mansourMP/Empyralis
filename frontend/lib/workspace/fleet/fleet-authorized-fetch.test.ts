/**
 * Root cause of the production 403: a brand-new Google signup's first
 * authenticated request can 401 once while "the session propagates" (a
 * documented, expected condition right after login/signup — see
 * auth-client.ts's awaitBrowserAuthReady comment). fleetAuthorizedFetch is
 * the SINGLE narrow-waist wrapper ~50 call sites across the app route
 * through for exactly this (authorized-fetch-drift.test.ts enforces that),
 * including OnboardingClient's auto-submitted PATCH /api/workspaces/{id}
 * (account-workspaces-client.ts's updateWorkspace -> requestJson ->
 * fleetAuthorizedFetch).
 *
 * On a 401, it calls auth-client's refresh() and retries the SAME `init`
 * object. But a successful refresh ALWAYS rotates the CSRF cookie
 * server-side (server_modules/auth.py's issue_csrf_token() mints a fresh
 * random token on every set_auth_cookies() call, unconditionally — not
 * only when the old one had actually gone stale). The retry's
 * X-CSRF-Token header was built once, by the caller, BEFORE the refresh
 * happened; the browser's Cookie header on the retry reflects whatever is
 * in the jar NOW — the just-rotated value. Reusing the stale header
 * verbatim means the retry's header and cookie can never agree: this is
 * not a rare race, it fires on every 401-then-successful-refresh retry of
 * a CSRF-protected (non-GET) request, and control-plane-proxy.ts's
 * validateBrowserCsrf reports it as `csrf_mismatch` (see
 * control-plane-proxy.test.ts).
 *
 * This proves the retry now re-reads the CSRF cookie AFTER the refresh
 * resolves, so its header matches whatever the browser will actually send.
 *
 * Run: npx tsx lib/workspace/fleet/fleet-authorized-fetch.test.ts
 */

import assert from "node:assert/strict";

import { AUTH_CSRF_HEADER_NAME } from "@/lib/auth/csrf";

let failures = 0;

async function test(name: string, fn: () => Promise<void> | void): Promise<void> {
  try {
    await fn();
    console.log(`ok - ${name}`);
  } catch (error) {
    failures += 1;
    console.error(`FAIL - ${name}`);
    console.error(error);
  }
}

// fleetAuthorizedFetch and auth-client.refresh() are both "use client"
// modules that reach for `document.cookie` and `window.setTimeout` at call
// time -- give them a minimal browser-ish global before importing, since
// this file runs under plain `tsx` (no DOM, no bundler).
(globalThis as unknown as { document: { cookie: string } }).document = { cookie: "" };
(globalThis as unknown as { window: { setTimeout: typeof setTimeout; clearTimeout: typeof clearTimeout } }).window = {
  setTimeout,
  clearTimeout,
};

function setCookie(name: string, value: string): void {
  const doc = (globalThis as unknown as { document: { cookie: string } }).document;
  const existing = doc.cookie
    .split(";")
    .map((part) => part.trim())
    .filter((part) => part && !part.startsWith(`${name}=`));
  existing.push(`${name}=${value}`);
  doc.cookie = existing.join("; ");
}

async function main() {
  // No module-level mock of auth-client's refresh(): fleetAuthorizedFetch
  // calls it directly, and refresh() itself is just requestAuth('/api/auth/
  // refresh', ...) -- another fetch() call. Driving everything through a
  // single stubbed global.fetch (keyed by call order below) lets the REAL
  // refresh() and the REAL retry logic run their real course, rather than
  // re-implementing either.
  const { fleetAuthorizedFetch } = await import("./fleet-authorized-fetch");

  await test("retry after 401-then-refresh sends the FRESH csrf cookie value, not the stale one", async () => {
    setCookie("empyralis_csrf_token", "token-old");
    const originalFetch = global.fetch;
    const calls: { url: string; headers: Headers }[] = [];
    let fetchCount = 0;
    global.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
      fetchCount += 1;
      const headers = new Headers(init?.headers);
      calls.push({ url: String(input), headers });
      if (fetchCount === 1) {
        return new Response(null, { status: 401 });
      }
      if (fetchCount === 2) {
        // This is auth-client's refresh() call, POST /api/auth/refresh.
        // Simulate the backend rotating the CSRF cookie, exactly like
        // set_auth_cookies()/issue_csrf_token() always does on a real
        // refresh.
        setCookie("empyralis_csrf_token", "token-new");
        return new Response(JSON.stringify({ ok: true }), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }
      // The retried original request (3rd fetch call).
      return new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    };

    try {
      const response = await fleetAuthorizedFetch("/api/workspaces/ws_1", {
        method: "PATCH",
        headers: { [AUTH_CSRF_HEADER_NAME]: "token-old", accept: "application/json" },
        body: JSON.stringify({ setupCompleted: true }),
      });

      assert.equal(response.status, 200, "the retry must succeed once the session is refreshed");
      assert.equal(fetchCount, 3, "expected: first attempt (401), refresh call, retry");

      const retryHeaders = calls[2].headers;
      assert.equal(
        retryHeaders.get(AUTH_CSRF_HEADER_NAME),
        "token-new",
        "the retry's X-CSRF-Token header must match the CSRF cookie AS IT STANDS AFTER the refresh, " +
          "not the value captured before it — a stale header here is exactly the csrf_mismatch a brand-new " +
          "Google signup hit in production on the onboarding PATCH",
      );
    } finally {
      global.fetch = originalFetch;
    }
  });

  await test("a GET with no csrf header to begin with never grows one on retry", async () => {
    setCookie("empyralis_csrf_token", "token-old");
    const originalFetch = global.fetch;
    const calls: Headers[] = [];
    let fetchCount = 0;
    global.fetch = async (_input: RequestInfo | URL, init?: RequestInit) => {
      fetchCount += 1;
      calls.push(new Headers(init?.headers));
      if (fetchCount === 1) return new Response(null, { status: 401 });
      if (fetchCount === 2) {
        setCookie("empyralis_csrf_token", "token-new");
        return new Response(JSON.stringify({ ok: true }), { status: 200, headers: { "content-type": "application/json" } });
      }
      return new Response(JSON.stringify({ ok: true }), { status: 200, headers: { "content-type": "application/json" } });
    };
    try {
      await fleetAuthorizedFetch("/api/workspaces", { method: "GET", headers: { accept: "application/json" } });
      assert.equal(calls[2].has(AUTH_CSRF_HEADER_NAME), false, "must not invent a CSRF header for a request that never asked for one");
    } finally {
      global.fetch = originalFetch;
    }
  });

  await test("a first-try success never touches refresh or retries", async () => {
    const originalFetch = global.fetch;
    let fetchCount = 0;
    global.fetch = async () => {
      fetchCount += 1;
      return new Response(JSON.stringify({ ok: true }), { status: 200, headers: { "content-type": "application/json" } });
    };
    try {
      const response = await fleetAuthorizedFetch("/api/workspaces", { method: "GET" });
      assert.equal(response.status, 200);
      assert.equal(fetchCount, 1, "no 401 means no refresh and no retry");
    } finally {
      global.fetch = originalFetch;
    }
  });

  console.log(`\n${failures === 0 ? "All" : failures} fleetAuthorizedFetch CSRF-retry test(s)${failures === 0 ? " passed." : " failed."}`);
  if (failures > 0) {
    process.exit(1);
  }
}

void main();

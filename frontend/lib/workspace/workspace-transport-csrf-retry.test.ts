/**
 * WorkspaceTransportAdapter.request() (workspace-services.tsx) is the
 * OTHER browser-side transport that reads the CSRF cookie itself
 * (performRequest calls buildCookieAuthHeaders fresh on every call, so its
 * existing 401-then-refresh retry never had fleet-authorized-fetch.ts's
 * stale-header bug). But it had the SAME gap fleet-authorized-fetch.ts did
 * before this change: a bare 403 `csrf_mismatch` on the very FIRST attempt
 * (no 401 involved -- see fleet-authorized-fetch.test.ts's own header
 * comment for the production mechanism: proxy.ts's middleware rotating the
 * CSRF cookie on a concurrent qualifying GET) was not in RETRYABLE_STATUSES
 * and so was never retried at all.
 *
 * This proves request() now retries exactly once on that specific code,
 * rebuilding the header from the live cookie (performRequest already does
 * this on every call -- simply looping back is enough here, no separate
 * header-patching helper needed), and never for a genuinely different CSRF
 * failure or a method that never carries the header.
 *
 * WorkspaceTransportAdapter is `export`ed from workspace-services.tsx
 * SOLELY so this file can construct it directly under plain `tsx` (no
 * bundler, no DOM) -- the same reason fleet-authorized-fetch.test.ts fakes
 * minimal `document`/`window` globals below rather than pulling in jsdom.
 *
 * Run: npx tsx lib/workspace/workspace-transport-csrf-retry.test.ts
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

// performRequest reads document.cookie (via buildCookieAuthHeaders) and
// schedules its per-request abort timeout via window.setTimeout -- give it
// both before importing, exactly like fleet-authorized-fetch.test.ts does.
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

// Minimal stand-in for WorkspaceDisposableRegistry (not itself exported):
// request()'s retry loop only ever calls trackAbortController/trackTimeout
// on it, never the teardown methods. `any` sidesteps the real class's
// private fields making it otherwise nominally, not structurally, typed.
function stubDisposableRegistry(): any {
  return {
    trackAbortController: (controller: AbortController) => controller,
    trackTimeout: (id: number) => id,
  };
}

async function main() {
  const { WorkspaceTransportAdapter } = await import("./workspace-services");

  await test("a first-try 403 csrf_mismatch retries once against the live cookie, and succeeds", async () => {
    setCookie("empyralis_csrf_token", "token-old");
    const adapter = new WorkspaceTransportAdapter("https://api.example.test", "ws_1", stubDisposableRegistry());
    const originalFetch = global.fetch;
    const calls: Headers[] = [];
    let fetchCount = 0;
    global.fetch = async (_input: RequestInfo | URL, init?: RequestInit) => {
      fetchCount += 1;
      calls.push(new Headers(init?.headers));
      if (fetchCount === 1) {
        setCookie("empyralis_csrf_token", "token-new");
        return new Response(JSON.stringify({ detail: "CSRF validation failed.", code: "csrf_mismatch" }), {
          status: 403,
          headers: { "content-type": "application/json" },
        });
      }
      return new Response(JSON.stringify({ ok: true }), { status: 200, headers: { "content-type": "application/json" } });
    };
    try {
      const response = await adapter.request("/workspaces/ws_1", { method: "PATCH", body: "{}" });
      assert.equal(response.status, 200, "the retry must succeed once the header is rebuilt from the live cookie");
      assert.equal(fetchCount, 2, "expected: first attempt (403 csrf_mismatch), retry — no loop beyond that");
      assert.equal(
        calls[1].get(AUTH_CSRF_HEADER_NAME),
        "token-new",
        "the retry must carry the cookie as it stands now, not the stale value from the first attempt",
      );
    } finally {
      global.fetch = originalFetch;
    }
  });

  await test("a bare 403 that is NOT csrf_mismatch is never retried", async () => {
    setCookie("empyralis_csrf_token", "token-old");
    const adapter = new WorkspaceTransportAdapter("https://api.example.test", "ws_1", stubDisposableRegistry());
    const originalFetch = global.fetch;
    let fetchCount = 0;
    global.fetch = async () => {
      fetchCount += 1;
      return new Response(JSON.stringify({ detail: "CSRF validation failed.", code: "csrf_header_missing" }), {
        status: 403,
        headers: { "content-type": "application/json" },
      });
    };
    try {
      const response = await adapter.request("/workspaces/ws_1", { method: "PATCH", body: "{}" });
      assert.equal(response.status, 403);
      assert.equal(fetchCount, 1, "csrf_header_missing is not this-request-recoverable — must not retry");
    } finally {
      global.fetch = originalFetch;
    }
  });

  await test("a GET never retries a csrf_mismatch 403 (it never carries the header to begin with)", async () => {
    setCookie("empyralis_csrf_token", "token-old");
    const adapter = new WorkspaceTransportAdapter("https://api.example.test", "ws_1", stubDisposableRegistry());
    const originalFetch = global.fetch;
    let fetchCount = 0;
    global.fetch = async () => {
      fetchCount += 1;
      return new Response(JSON.stringify({ detail: "CSRF validation failed.", code: "csrf_mismatch" }), {
        status: 403,
        headers: { "content-type": "application/json" },
      });
    };
    try {
      const response = await adapter.request("/workspaces", { method: "GET" }, { retryOnStatuses: [] });
      assert.equal(response.status, 403);
      assert.equal(fetchCount, 1, "a GET is never CSRF-protected — must not retry");
    } finally {
      global.fetch = originalFetch;
    }
  });

  await test("exactly one retry: a second consecutive csrf_mismatch is surfaced honestly, not looped", async () => {
    setCookie("empyralis_csrf_token", "token-old");
    const adapter = new WorkspaceTransportAdapter("https://api.example.test", "ws_1", stubDisposableRegistry());
    const originalFetch = global.fetch;
    let fetchCount = 0;
    global.fetch = async () => {
      fetchCount += 1;
      setCookie("empyralis_csrf_token", `token-${fetchCount}`);
      return new Response(JSON.stringify({ detail: "CSRF validation failed.", code: "csrf_mismatch" }), {
        status: 403,
        headers: { "content-type": "application/json" },
      });
    };
    try {
      const response = await adapter.request("/workspaces/ws_1", { method: "PATCH", body: "{}" });
      assert.equal(response.status, 403, "a second mismatch must be surfaced honestly, not silently retried again");
      assert.equal(fetchCount, 2, "exactly one retry — no loop");
    } finally {
      global.fetch = originalFetch;
    }
  });

  console.log(`\n${failures === 0 ? "All" : failures} WorkspaceTransportAdapter CSRF-retry test(s)${failures === 0 ? " passed." : " failed."}`);
  if (failures > 0) {
    process.exit(1);
  }
}

void main();

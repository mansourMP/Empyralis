/**
 * A brand-new Google signup was 403ing on PATCH /api/workspaces/{id} from
 * /onboarding in production. control-plane-proxy.ts's CSRF check used to
 * fold three genuinely different facts — the CSRF cookie missing, the
 * X-CSRF-Token header missing, or the two disagreeing — into one identical
 * 403 body (`{ detail: 'CSRF validation failed.' }`), with no server log
 * line at all. Nobody could tell which of the three actually happened.
 *
 * This proves the three cases now produce distinct, stable `code` values
 * (csrf_cookie_missing / csrf_header_missing / csrf_mismatch — matched on
 * the code, never on prose) and a console.error naming which one fired,
 * listing only the cookie NAMES present on the request — never a cookie
 * value, since these are session credentials. classifyCsrfFailure() and
 * logCsrfFailure() themselves have their own direct unit tests in
 * csrf-failure.test.ts; this file proves the WIRING — the real
 * forwardControlPlaneRequest, given a real NextRequest, actually reaches
 * that logic and returns it faithfully.
 *
 * Run: npx tsx lib/server/control-plane-proxy.test.ts
 */

import assert from "node:assert/strict";
import { createRequire } from "node:module";
import path from "node:path";
import type { NextRequest } from "next/server";

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

// A well-formed, unexpired JWT — just enough for isAccessTokenLive() to
// treat it as a live session so hasBrowserSessionCookie() lets the request
// reach the CSRF check at all (mirrors a real browser mid-session).
function liveAccessTokenCookie(): string {
  const header = Buffer.from(JSON.stringify({ alg: "none" })).toString("base64url");
  const payload = Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString("base64url");
  return `${header}.${payload}.sig`;
}

async function readCode(response: Response): Promise<{ status: number; code?: string }> {
  const body = (await response.json()) as { code?: string };
  return { status: response.status, code: body.code };
}

async function main() {
  // control-plane-proxy.ts starts with `import 'server-only'` -- a Next.js
  // build-time marker every bundler no-ops via module aliasing. Under plain
  // `npx tsx` (no bundler, the same way every other file in this test:unit
  // suite runs) that specifier is either unresolvable (nothing installs it
  // -- it's a transitive dependency of `next` this repo's package-lock.json
  // doesn't currently pin) or, if a real install is ever added, throws
  // unconditionally outside a "react-server" module condition (its whole
  // point is to explode if it leaks into a client bundle). Redirect just
  // this one bare specifier to a real, committed, do-nothing module -- the
  // same no-op behavior a bundler already gives it server-side -- so this
  // test can exercise the REAL forwardControlPlaneRequest end to end
  // instead of re-implementing its CSRF wiring.
  const require = createRequire(import.meta.url);
  type PatchableModule = typeof import("node:module") & {
    _resolveFilename: (request: string, ...rest: unknown[]) => string;
  };
  const nodeModule = require("node:module") as PatchableModule;
  const serverOnlyStubPath = path.join(path.dirname(new URL(import.meta.url).pathname), "server-only-test-stub.cjs");
  const originalResolveFilename = nodeModule._resolveFilename;
  nodeModule._resolveFilename = function patchedResolveFilename(
    this: unknown,
    request: string,
    ...rest: unknown[]
  ): string {
    if (request === "server-only") {
      return serverOnlyStubPath;
    }
    return originalResolveFilename.apply(this, [request, ...rest] as Parameters<typeof originalResolveFilename>);
  };

  let NextRequestCtor: typeof NextRequest;
  let forwardControlPlaneRequest: typeof import("./control-plane-proxy").forwardControlPlaneRequest;
  let AUTH_ACCESS_COOKIE_NAME: string;
  let AUTH_CSRF_COOKIE_NAME: string;
  let AUTH_CSRF_HEADER_NAME: string;
  try {
    ({ NextRequest: NextRequestCtor } = await import("next/server"));
    ({ forwardControlPlaneRequest } = await import("./control-plane-proxy"));
    ({ AUTH_ACCESS_COOKIE_NAME, AUTH_CSRF_COOKIE_NAME, AUTH_CSRF_HEADER_NAME } = await import("../auth/csrf"));
  } finally {
    nodeModule._resolveFilename = originalResolveFilename;
  }

  function patchRequest(cookieHeader: string, csrfHeaderValue?: string): NextRequest {
    const headers: Record<string, string> = { cookie: cookieHeader };
    if (csrfHeaderValue !== undefined) {
      headers[AUTH_CSRF_HEADER_NAME] = csrfHeaderValue;
    }
    return new NextRequestCtor("http://localhost:3000/api/workspaces/ws_123", {
      method: "PATCH",
      headers,
    });
  }

  const access = liveAccessTokenCookie();

  await test("cookie missing, header missing -> csrf_cookie_missing (checked first)", async () => {
    const originalError = console.error;
    const logs: unknown[][] = [];
    console.error = (...args: unknown[]) => logs.push(args);
    try {
      const request = patchRequest(`${AUTH_ACCESS_COOKIE_NAME}=${access}`);
      const response = await forwardControlPlaneRequest(request, "/api/v1/workspaces/ws_123");
      const { status, code } = await readCode(response);
      assert.equal(status, 403);
      assert.equal(code, "csrf_cookie_missing");
      const logged = logs.some((args) => String(args[0]).includes("csrf_cookie_missing"));
      assert.ok(logged, "expected a console.error line naming csrf_cookie_missing");
    } finally {
      console.error = originalError;
    }
  });

  await test("cookie present, header missing -> csrf_header_missing (a DIFFERENT fact from cookie-missing)", async () => {
    const originalError = console.error;
    const logs: unknown[][] = [];
    console.error = (...args: unknown[]) => logs.push(args);
    try {
      const request = patchRequest(`${AUTH_ACCESS_COOKIE_NAME}=${access}; ${AUTH_CSRF_COOKIE_NAME}=token-abc`);
      const response = await forwardControlPlaneRequest(request, "/api/v1/workspaces/ws_123");
      const { status, code } = await readCode(response);
      assert.equal(status, 403);
      assert.equal(code, "csrf_header_missing");
      const logged = logs.some((args) => String(args[0]).includes("csrf_header_missing"));
      assert.ok(logged, "expected a console.error line naming csrf_header_missing");
      // Cookie NAMES only -- never a value -- must appear in the log.
      const namesLogged = logs.some((args) =>
        args.some((arg) => {
          if (!arg || typeof arg !== "object") return false;
          const cookieNames = (arg as Record<string, unknown>).cookieNames;
          return Array.isArray(cookieNames) && cookieNames.includes(AUTH_CSRF_COOKIE_NAME);
        }),
      );
      assert.ok(namesLogged, "expected the log to list the cookie NAMES present on the request");
      const noValueLeaked = !logs.some((args) => args.some((arg) => String(arg).includes("token-abc")));
      assert.ok(noValueLeaked, "a cookie VALUE must never be logged");
    } finally {
      console.error = originalError;
    }
  });

  await test("cookie and header both present but DIFFERENT -> csrf_mismatch", async () => {
    const originalError = console.error;
    const logs: unknown[][] = [];
    console.error = (...args: unknown[]) => logs.push(args);
    try {
      const request = patchRequest(
        `${AUTH_ACCESS_COOKIE_NAME}=${access}; ${AUTH_CSRF_COOKIE_NAME}=token-old`,
        "token-new",
      );
      const response = await forwardControlPlaneRequest(request, "/api/v1/workspaces/ws_123");
      const { status, code } = await readCode(response);
      assert.equal(status, 403);
      assert.equal(code, "csrf_mismatch");
      const logged = logs.some((args) => String(args[0]).includes("csrf_mismatch"));
      assert.ok(logged, "expected a console.error line naming csrf_mismatch");
    } finally {
      console.error = originalError;
    }
  });

  await test("cookie and header MATCH -> not a CSRF failure at all (falls through to the upstream call)", async () => {
    const originalFetch = global.fetch;
    let fetchCalled = false;
    global.fetch = async () => {
      fetchCalled = true;
      return new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    };
    try {
      const request = patchRequest(
        `${AUTH_ACCESS_COOKIE_NAME}=${access}; ${AUTH_CSRF_COOKIE_NAME}=token-match`,
        "token-match",
      );
      const response = await forwardControlPlaneRequest(request, "/api/v1/workspaces/ws_123");
      assert.equal(response.status, 200);
      assert.ok(fetchCalled, "a matching cookie/header pair must reach the upstream fetch, not short-circuit as a 403");
    } finally {
      global.fetch = originalFetch;
    }
  });

  await test("no live session cookie at all -> CSRF check is skipped entirely (unrelated to the three codes above)", async () => {
    const originalFetch = global.fetch;
    global.fetch = async () =>
      new Response(JSON.stringify({ ok: true }), { status: 200, headers: { "content-type": "application/json" } });
    try {
      const request = patchRequest("");
      const response = await forwardControlPlaneRequest(request, "/api/v1/workspaces/ws_123");
      assert.notEqual(response.status, 403);
    } finally {
      global.fetch = originalFetch;
    }
  });

  console.log(`\n${failures === 0 ? "All" : failures} control-plane-proxy CSRF instrumentation test(s)${failures === 0 ? " passed." : " failed."}`);
  if (failures > 0) {
    process.exit(1);
  }
}

void main();

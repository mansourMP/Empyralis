/**
 * The Google OAuth callback route hand-parsed each upstream Set-Cookie
 * header and re-emitted it via `NextResponse.cookies.set(name, value,
 * options)`. Next's `ResponseCookies.set()` keys by cookie NAME ALONE and
 * REPLACES — so when the backend's cookie self-heal fix (auth.py's
 * `_delete_host_only_cookie_twins`) emits two Set-Cookie headers for
 * `empyralis_csrf_token` (a host-only delete, then the real domain-scoped
 * set), this route collapsed them into one. The host-only delete never
 * reached the browser, so a browser carrying a stray host-only twin from
 * Google sign-in could never self-heal — `duplicateCsrfCookieCount` stayed
 * at 2 forever and every CSRF-protected request kept 403ing.
 *
 * The email/password auth routes never had this bug: they go through
 * forwardControlPlaneRequest (control-plane-proxy.ts), which forwards
 * upstream Set-Cookie headers verbatim via `headers.append('set-cookie', ...)`.
 * This route already had a dead, never-called `appendUpstreamCookies` helper
 * that did the same correct thing — it just wasn't wired to the actual
 * response construction below it.
 *
 * This test proves the wiring end to end: two upstream Set-Cookie headers
 * sharing one cookie name must produce two Set-Cookie headers on the
 * response Google sign-in actually returns to the browser, preserving both
 * value and every attribute (Domain, Max-Age) untouched.
 *
 * Run: npx tsx app/api/auth/google/callback/route.test.ts
 */

import assert from "node:assert/strict";
import { createRequire } from "node:module";
import path from "node:path";

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

async function main() {
  process.env.GOOGLE_AUTH_CLIENT_ID = "test-client-id";
  process.env.GOOGLE_AUTH_CLIENT_SECRET = "test-client-secret";
  delete process.env.EMPYRALIS_AUTH_ALLOWED_ORIGINS;
  delete process.env.EMPYRALIS_API_URL;
  delete process.env.ORION_API_URL;

  // This route (and its google-oauth.ts / control-plane-base-url.ts
  // imports) starts with `import 'server-only'` — redirect that one bare
  // specifier to a real, committed do-nothing module the same way
  // control-plane-proxy.test.ts does, so this test can import the REAL
  // route handler under plain `npx tsx` instead of re-implementing it.
  const require = createRequire(import.meta.url);
  type PatchableModule = typeof import("node:module") & {
    _resolveFilename: (request: string, ...rest: unknown[]) => string;
  };
  const nodeModule = require("node:module") as PatchableModule;
  const serverOnlyStubPath = path.join(
    path.dirname(new URL(import.meta.url).pathname),
    "..",
    "..",
    "..",
    "..",
    "..",
    "lib",
    "server",
    "server-only-test-stub.cjs",
  );
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

  let NextRequestCtor: typeof import("next/server").NextRequest;
  let encodeGoogleOAuthState: typeof import("@/lib/server/google-oauth").encodeGoogleOAuthState;
  let GOOGLE_OAUTH_STATE_COOKIE: string;
  let GET: typeof import("./route").GET;
  try {
    ({ NextRequest: NextRequestCtor } = await import("next/server"));
    ({ encodeGoogleOAuthState, GOOGLE_OAUTH_STATE_COOKIE } = await import("@/lib/server/google-oauth"));
    ({ GET } = await import("./route"));
  } finally {
    nodeModule._resolveFilename = originalResolveFilename;
  }

  function buildCallbackRequest(): InstanceType<typeof NextRequestCtor> {
    const state = "state-token-abc";
    const stateCookie = encodeGoogleOAuthState({ state, createdAt: Date.now() });
    const url = new URL("http://localhost:3000/api/auth/google/callback");
    url.searchParams.set("code", "auth-code-xyz");
    url.searchParams.set("state", state);
    return new NextRequestCtor(url, {
      headers: {
        cookie: `${GOOGLE_OAUTH_STATE_COOKIE}=${stateCookie}`,
      },
    });
  }

  await test(
    "two upstream Set-Cookie headers for the same cookie name -> two Set-Cookie headers on the response",
    async () => {
      const originalFetch = global.fetch;
      global.fetch = (async (input: RequestInfo | URL) => {
        const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
        if (url.includes("oauth2.googleapis.com/token")) {
          return new Response(JSON.stringify({ id_token: "fake-id-token" }), {
            status: 200,
            headers: { "content-type": "application/json" },
          });
        }
        if (url.includes("/api/v1/auth/provider-login")) {
          const upstreamHeaders = new Headers();
          upstreamHeaders.set("content-type", "application/json");
          // Mirrors auth.py's set_auth_cookies() self-heal: a host-only
          // DELETE of the stray twin, then the real domain-scoped SET —
          // two Set-Cookie headers, same cookie name, on purpose.
          upstreamHeaders.append(
            "set-cookie",
            "empyralis_csrf_token=; Path=/; Max-Age=0; SameSite=Lax",
          );
          upstreamHeaders.append(
            "set-cookie",
            "empyralis_csrf_token=fresh-csrf-value; Path=/; Domain=.test.localhost; Max-Age=86400; SameSite=Lax",
          );
          return new Response(JSON.stringify({ token: "fake-access-token" }), {
            status: 200,
            headers: upstreamHeaders,
          });
        }
        throw new Error(`Unexpected fetch to ${url}`);
      }) as typeof fetch;

      try {
        const response = await GET(buildCallbackRequest());
        assert.equal(response.status, 200, "callback should succeed and return the HTML hand-off page");

        const responseHeaders = response.headers as Headers & { getSetCookie?: () => string[] };
        const setCookies =
          typeof responseHeaders.getSetCookie === "function" ? responseHeaders.getSetCookie() : [];
        const csrfCookies = setCookies.filter((raw) => raw.startsWith("empyralis_csrf_token="));

        assert.equal(
          csrfCookies.length,
          2,
          `expected 2 Set-Cookie headers for empyralis_csrf_token, got ${csrfCookies.length}: ${JSON.stringify(setCookies)}`,
        );

        const hostOnlyDelete = csrfCookies.find((raw) => /max-age=0/i.test(raw));
        const domainScopedSet = csrfCookies.find((raw) => /domain=\.test\.localhost/i.test(raw));
        assert.ok(hostOnlyDelete, "the host-only DELETE (Max-Age=0, no Domain) must survive onto the response");
        assert.ok(!hostOnlyDelete?.toLowerCase().includes("domain="), "the host-only delete must carry no Domain attribute");
        assert.ok(domainScopedSet, "the domain-scoped SET (Domain=.test.localhost) must survive onto the response");
        assert.ok(domainScopedSet?.includes("fresh-csrf-value"), "the domain-scoped cookie's value must be forwarded untouched");
      } finally {
        global.fetch = originalFetch;
      }
    },
  );

  console.log(
    `\n${failures === 0 ? "All" : failures} google/callback Set-Cookie forwarding test(s)${failures === 0 ? " passed." : " failed."}`,
  );
  if (failures > 0) {
    process.exit(1);
  }
}

void main();

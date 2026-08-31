/**
 * Guards the CSRF-cookie fallback safety net (MAN: new-signup onboarding
 * trap, 2026-08-31).
 *
 * Production symptom: every brand-new Google sign-up landed on /onboarding,
 * which auto-submits a PATCH the instant it mounts. That PATCH always came
 * back 403 "CSRF validation failed." — trapped there forever, because
 * OnboardingClient used to `return null` on any state other than exactly
 * "authenticated" (see onboarding-blank-state-drift.test.ts for that half).
 * nginx confirmed the session itself was fine (GET /api/auth/account-shell
 * 200 right before the 403) and the backend's own uvicorn access log never
 * even saw the PATCH — the 403 was manufactured entirely in the Next.js
 * proxy layer (frontend/lib/server/control-plane-proxy.ts's
 * validateBrowserCsrf), which means the browser's `empyralis_csrf_token`
 * cookie was empty at the moment OnboardingClient's auto-submit fired.
 *
 * Investigated and RULED OUT (not guessed away — verified live in a real
 * browser, see the session's own report for the two experiments):
 *   - SameSite=Strict blocking the cookie from being STORED on a response to
 *     a cross-site top-level navigation (the accounts.google.com -> our own
 *     /api/auth/google/callback hop). A standalone two-origin probe (a real
 *     cross-site 302 followed by a Set-Cookie: ...; SameSite=Strict
 *     response, then a same-site JS redirect) proved the cookie IS stored
 *     and IS visible to document.cookie on the very next page. SameSite
 *     governs whether a cookie is SENT on a future request, never whether it
 *     can be SET.
 *   - The general multi-cookie forwarding mechanism itself. A live signup ->
 *     /onboarding run against this exact codebase, with
 *     EMPYRALIS_AUTH_COOKIE_SAMESITE forced to "strict" (production's
 *     default), completed the auto-submit PATCH successfully — proving
 *     auth.py's set_auth_cookies() (which mints all three cookies
 *     unconditionally, identically for email AND external/Google auth via
 *     the shared _login_payload_for_user) and Node's fetch/undici
 *     getSetCookie() both work correctly for this exact 3-cookie response
 *     shape, even under strict SameSite.
 *
 * What's DIFFERENT about the Google path, verified by reading (not
 * guessing): frontend/app/api/auth/google/callback/route.ts is the ONLY
 * auth route in this codebase that forwards Set-Cookie headers by hand
 * (it must return an HTML page for the CSP-nonce redirect, not a JSON
 * passthrough) instead of going through the shared, already-hardened
 * forwardControlPlaneRequest (control-plane-proxy.ts) that every other auth
 * route (login, signup, register, refresh) uses — and that shared proxy
 * already carries a defensive fallback (shouldEnsureBrowserCsrfCookie) for
 * exactly this shape: an auth cookie arriving without a CSRF cookie. The
 * Google callback route did not inherit it. It is also the one auth entry
 * point whose Set-Cookie headers cross an extra hop (a real redirect through
 * accounts.google.com, then Cloudflare/nginx back to the browser) that this
 * repo cannot reproduce outside production to catch the exact transport-
 * level moment the cookie is dropped.
 *
 * THE FIX: extract the fallback's pure decision (needsFallbackCsrfCookie)
 * and token generator (generateBrowserCsrfToken) into lib/auth/csrf.ts —
 * plain and dependency-free, unlike control-plane-proxy.ts, which opens
 * with `import 'server-only'` and cannot be imported outside Next's bundler
 * (verified: a direct tsx import throws "Cannot find module 'server-only'",
 * since it is not an installed package at all, only resolved via a Next.js
 * webpack alias) — so BOTH call sites, and this test, share one rule
 * instead of drifting. This does not touch validate_csrf's own comparison
 * anywhere (cookie === header) — it only guarantees a cookie exists to
 * compare against, closing the gap regardless of which hop dropped it.
 *
 * Run: npx tsx lib/auth/csrf-fallback-drift.test.ts
 */

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import {
  AUTH_ACCESS_COOKIE_NAME,
  AUTH_CSRF_COOKIE_NAME,
  AUTH_REFRESH_COOKIE_NAME,
  generateBrowserCsrfToken,
  needsFallbackCsrfCookie,
} from "./csrf";

const HERE = dirname(fileURLToPath(import.meta.url));
// This file lives at lib/auth/ — the frontend root is two levels up.
const FRONTEND_ROOT = join(HERE, "..", "..");

let passed = 0;
let failed = 0;

function assert(condition: boolean, label: string): void {
  if (condition) {
    passed++;
  } else {
    failed++;
    console.error(`FAIL: ${label}`);
  }
}

// ── needsFallbackCsrfCookie: pure decision fixtures ──────────────────────

assert(
  needsFallbackCsrfCookie([]) === false,
  "no cookies at all: nothing to protect, no fallback needed",
);

assert(
  needsFallbackCsrfCookie([AUTH_CSRF_COOKIE_NAME]) === false,
  "CSRF cookie alone, no auth cookie: not a live session yet, no fallback needed",
);

assert(
  needsFallbackCsrfCookie([AUTH_ACCESS_COOKIE_NAME]) === true,
  "access cookie present, CSRF cookie absent: THE production shape — must trigger the fallback",
);

assert(
  needsFallbackCsrfCookie([AUTH_REFRESH_COOKIE_NAME]) === true,
  "refresh cookie present (no access cookie), CSRF cookie absent: must still trigger the fallback",
);

assert(
  needsFallbackCsrfCookie([AUTH_ACCESS_COOKIE_NAME, AUTH_REFRESH_COOKIE_NAME]) === true,
  "both auth cookies present, CSRF cookie absent: must trigger the fallback",
);

assert(
  needsFallbackCsrfCookie([AUTH_ACCESS_COOKIE_NAME, AUTH_REFRESH_COOKIE_NAME, AUTH_CSRF_COOKIE_NAME]) === false,
  "all three cookies present (the healthy case): no fallback needed — must never fire when nothing is actually missing",
);

assert(
  needsFallbackCsrfCookie([AUTH_ACCESS_COOKIE_NAME, "some_unrelated_cookie"]) === true,
  "an unrelated cookie alongside the access cookie must not be mistaken for the CSRF cookie",
);

// ── Parity: both call sites must extract the SAME cookie name from the SAME
// raw Set-Cookie header string, or a future edit to one parser could silently
// diverge from the other — two names for one concept, exactly what CLAUDE.md
// warns against. Mirrors the two real parsing shapes in this codebase:
// control-plane-proxy.ts's shouldEnsureBrowserCsrfCookie (split on ';', then
// take everything before the first '=') and google/callback/route.ts's
// forwarding loop (identical logic, written out longhand for HttpOnly/
// Secure/SameSite/Path/Domain attribute parsing alongside it).

function nameViaProxyStyle(raw: string): string {
  return raw.trim().split("=", 1)[0];
}

function nameViaCallbackStyle(raw: string): string | null {
  const parts = raw.split(";").map((s) => s.trim());
  const [nvPair] = parts;
  const eqIdx = nvPair.indexOf("=");
  if (eqIdx <= 0) return null;
  return nvPair.slice(0, eqIdx).trim();
}

const REAL_SHAPED_SET_COOKIE_HEADERS = [
  `${AUTH_ACCESS_COOKIE_NAME}=eyJhbGciOiJIUzI1NiJ9.abc.def; Path=/; HttpOnly; SameSite=Strict; Secure; Max-Age=3600`,
  `${AUTH_REFRESH_COOKIE_NAME}=esr_abc123.def456; Path=/; HttpOnly; SameSite=Strict; Secure; Max-Age=2592000`,
  `${AUTH_CSRF_COOKIE_NAME}=6A4dkQUtL-cYZ65Nk6oLYcp7OwCMMDz9gdmO-Mwnv-A; Path=/; SameSite=Strict; Secure; Max-Age=2592000`,
];

for (const raw of REAL_SHAPED_SET_COOKIE_HEADERS) {
  const proxyName = nameViaProxyStyle(raw);
  const callbackName = nameViaCallbackStyle(raw);
  assert(
    proxyName === callbackName,
    `both parsers extract the same cookie name from ${JSON.stringify(raw.slice(0, 40))}...: got proxy=${proxyName} callback=${callbackName}`,
  );
}

assert(
  needsFallbackCsrfCookie(
    REAL_SHAPED_SET_COOKIE_HEADERS.slice(0, 2).map((raw) => nameViaCallbackStyle(raw) || ""),
  ) === true,
  "the exact real-shaped access+refresh headers, with the CSRF header genuinely absent, trigger the fallback",
);

assert(
  needsFallbackCsrfCookie(REAL_SHAPED_SET_COOKIE_HEADERS.map((raw) => nameViaCallbackStyle(raw) || "")) === false,
  "the exact real-shaped access+refresh+csrf headers together do not trigger the fallback",
);

// ── generateBrowserCsrfToken: sanity, not a hardcoded/constant stub ──────

const tokenA = generateBrowserCsrfToken();
const tokenB = generateBrowserCsrfToken();
assert(typeof tokenA === "string" && tokenA.length > 0, "generateBrowserCsrfToken returns a non-empty string");
assert(tokenA !== tokenB, "generateBrowserCsrfToken returns a fresh value on every call, not a constant");

// ── Structural: the fallback must be WIRED into the Google callback route,
// not merely defined and unused (this codebase's single most common defect
// shape per CLAUDE.md's "Recurring failure modes" — reflexively applied
// here to this session's own fix rather than assuming it).

const googleCallbackRoutePath = join(
  FRONTEND_ROOT,
  "app",
  "api",
  "auth",
  "google",
  "callback",
  "route.ts",
);
const googleCallbackSource = readFileSync(googleCallbackRoutePath, "utf8");

assert(
  /needsFallbackCsrfCookie\s*\(/.test(googleCallbackSource),
  "app/api/auth/google/callback/route.ts calls needsFallbackCsrfCookie(...) — the safety net is actually invoked, not just imported",
);

assert(
  /response\.cookies\.set\(\s*AUTH_CSRF_COOKIE_NAME\s*,\s*generateBrowserCsrfToken\(\)/.test(googleCallbackSource),
  "app/api/auth/google/callback/route.ts sets AUTH_CSRF_COOKIE_NAME from generateBrowserCsrfToken() when the fallback fires",
);

assert(
  /from ['"]@\/lib\/auth\/csrf['"]/.test(googleCallbackSource)
    && /needsFallbackCsrfCookie/.test(googleCallbackSource.split("\n").slice(0, 15).join("\n")),
  "the callback route imports needsFallbackCsrfCookie from lib/auth/csrf (the shared, plain module) near the top of the file",
);

// ── Structural: the ORIGINAL safety net (the generic proxy every other auth
// route already uses) must still exist and still route through the same
// shared decision function — proving this change didn't just relocate the
// bug rather than close it.

const controlPlaneProxyPath = join(FRONTEND_ROOT, "lib", "server", "control-plane-proxy.ts");
const controlPlaneProxySource = readFileSync(controlPlaneProxyPath, "utf8");

assert(
  /function shouldEnsureBrowserCsrfCookie/.test(controlPlaneProxySource),
  "control-plane-proxy.ts still defines shouldEnsureBrowserCsrfCookie for the generic auth-route proxy path",
);

assert(
  /return needsFallbackCsrfCookie\(cookieNames\)/.test(controlPlaneProxySource),
  "control-plane-proxy.ts's shouldEnsureBrowserCsrfCookie delegates to the SAME needsFallbackCsrfCookie as the Google callback route, rather than a second hand-rolled copy",
);

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}

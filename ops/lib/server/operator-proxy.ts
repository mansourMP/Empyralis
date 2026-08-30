import "server-only";

import { NextRequest, NextResponse } from "next/server";

/**
 * Server-side proxy from this app's own `/api/operator/*` route to the real
 * backend's `/api/internal/operator/*` (server_modules/routes_operator_
 * console.py). This is how "reuse the platform's existing session" actually
 * works: the browser's `empyralis_access_token`/`empyralis_refresh_token`/
 * `empyralis_csrf_token` cookies are issued by the SAME backend the customer
 * app authenticates against (server_modules/auth.py's `set_auth_cookies`,
 * scoped by `EMPYRALIS_AUTH_COOKIE_DOMAIN` -- production must set that to
 * the shared parent domain, e.g. `.empyralis.ai`, for a cookie set on the
 * customer app to also reach ops.empyralis.ai; see deploy/ops/README-ish
 * notes in this repo's task history). This route reads those cookies off
 * the incoming request and forwards them to the backend -- it never asks
 * the browser for credentials of its own.
 *
 * Deliberately simpler than frontend/lib/server/control-plane-proxy.ts:
 * every operator-console route is GET-only and CSRF-exempt on the wire
 * (server_modules/auth.py's `validate_csrf` skips GET/HEAD/OPTIONS
 * entirely), so this never needs the customer app's CSRF-header dance for
 * the actual data fetch -- only for the one-time background token refresh
 * below, which is itself a POST.
 *
 * On a 401 (an expired access token), this refreshes the session ONCE
 * against the backend's own `/api/v1/auth/refresh` and retries the original
 * request exactly one time -- the same reactive, single-retry shape as
 * frontend/lib/workspace/fleet/fleet-authorized-fetch.ts's
 * `fleetAuthorizedFetch`, moved server-side because this app's pages call
 * their OWN origin (`/api/operator/...`), not the backend directly, so the
 * refresh has to happen at this layer to also refresh the cookies the
 * browser is holding (via `Set-Cookie` on the response this function
 * returns). A second 401 after a successful refresh, or a failed refresh,
 * passes the ORIGINAL 401 straight through -- the caller's own
 * `planOperatorView` renders that as `forbidden`, honestly.
 */

const ACCESS_COOKIE = "empyralis_access_token";
const REFRESH_COOKIE = "empyralis_refresh_token";
const CSRF_COOKIE = "empyralis_csrf_token";
const CSRF_HEADER = "x-csrf-token";

function backendBaseUrl(): string | null {
  const raw =
    process.env.EMPYRALIS_API_URL
    ?? process.env.ORION_API_URL
    ?? (process.env.NODE_ENV === "production" ? undefined : "http://127.0.0.1:8001");
  const normalized = String(raw || "").trim().replace(/\/+$/, "");
  return normalized || null;
}

function cookieHeaderFrom(request: NextRequest): string {
  return request.cookies
    .getAll()
    .map((c) => `${c.name}=${c.value}`)
    .join("; ");
}

function splitCombinedSetCookieHeader(value: string): string[] {
  const source = String(value || "").trim();
  if (!source) return [];
  return source.split(/,(?=[^;,]+=)/g).map((item) => item.trim()).filter(Boolean);
}

function readSetCookieHeaders(headers: Headers): string[] {
  const withGetSetCookie = headers as Headers & { getSetCookie?: () => string[] };
  if (typeof withGetSetCookie.getSetCookie === "function") {
    return withGetSetCookie.getSetCookie();
  }
  return splitCombinedSetCookieHeader(headers.get("set-cookie") || "");
}

function mergeCookieHeader(existingCookieHeader: string, setCookies: string[]): string {
  const merged = new Map<string, string>();
  for (const part of existingCookieHeader.split(";")) {
    const trimmed = part.trim();
    const separatorIndex = trimmed.indexOf("=");
    if (separatorIndex > 0) merged.set(trimmed.slice(0, separatorIndex), trimmed.slice(separatorIndex + 1));
  }
  for (const cookie of setCookies) {
    const firstPart = cookie.split(";", 1)[0] ?? "";
    const separatorIndex = firstPart.indexOf("=");
    if (separatorIndex > 0) merged.set(firstPart.slice(0, separatorIndex).trim(), firstPart.slice(separatorIndex + 1));
  }
  return Array.from(merged.entries())
    .map(([name, value]) => `${name}=${value}`)
    .join("; ");
}

/** Single-use, best-effort: any failure (network, missing tokens, backend
 *  down) returns an empty array, and the caller treats that exactly like a
 *  refresh that was never attempted -- the original 401 stands. */
async function attemptSessionRefresh(request: NextRequest, baseUrl: string): Promise<string[]> {
  const refreshToken = request.cookies.get(REFRESH_COOKIE)?.value;
  const csrfToken = request.cookies.get(CSRF_COOKIE)?.value;
  if (!refreshToken || !csrfToken) return [];

  try {
    const response = await fetch(`${baseUrl}/api/v1/auth/refresh`, {
      method: "POST",
      cache: "no-store",
      headers: {
        accept: "application/json",
        "content-type": "application/json",
        cookie: cookieHeaderFrom(request),
        [CSRF_HEADER]: csrfToken,
      },
      body: JSON.stringify({ channel: "web" }),
    });
    if (!response.ok) return [];
    return readSetCookieHeaders(response.headers);
  } catch {
    return [];
  }
}

/** `upstreamPath` is always built by the route handler from a fixed prefix
 *  (`/api/internal/operator/`) plus this app's own dynamic segments -- never
 *  from anything else in the request -- so this can never become an open
 *  proxy to an arbitrary backend path. */
export async function forwardOperatorRequest(request: NextRequest, upstreamPath: string): Promise<NextResponse> {
  const baseUrl = backendBaseUrl();
  if (!baseUrl) {
    return NextResponse.json(
      { detail: "The operator console's backend URL is not configured (EMPYRALIS_API_URL)." },
      { status: 503 },
    );
  }

  const url = `${baseUrl}${upstreamPath}`;
  const fetchWithCookie = (cookieHeader: string) =>
    fetch(url, {
      method: "GET",
      cache: "no-store",
      headers: { accept: "application/json", cookie: cookieHeader },
    });

  let cookieHeader = cookieHeaderFrom(request);
  let response: Response;
  try {
    response = await fetchWithCookie(cookieHeader);
  } catch {
    return NextResponse.json(
      { detail: "Couldn't reach the operator console backend. Try again in a moment." },
      { status: 503 },
    );
  }

  let refreshedSetCookies: string[] = [];
  if (response.status === 401 && request.cookies.get(ACCESS_COOKIE)) {
    refreshedSetCookies = await attemptSessionRefresh(request, baseUrl);
    if (refreshedSetCookies.length > 0) {
      cookieHeader = mergeCookieHeader(cookieHeader, refreshedSetCookies);
      try {
        response = await fetchWithCookie(cookieHeader);
      } catch {
        // The retry itself failed to reach the server -- fall through with
        // the ORIGINAL 401 response below rather than a 503, since a real
        // response (even an expired-session one) is more informative than
        // claiming the whole backend is unreachable on this one retry.
      }
    }
  }

  const bodyText = await response.text();
  const nextResponse = new NextResponse(bodyText, {
    status: response.status,
    headers: {
      "content-type": response.headers.get("content-type") || "application/json",
      // Operator data is cross-tenant and must never be cached by an
      // intermediary or the browser's own bfcache-adjacent behavior.
      "cache-control": "no-store",
    },
  });
  for (const cookie of refreshedSetCookies) {
    nextResponse.headers.append("set-cookie", cookie);
  }
  return nextResponse;
}

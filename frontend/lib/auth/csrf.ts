export const AUTH_ACCESS_COOKIE_NAME = 'empyralis_access_token';
export const AUTH_REFRESH_COOKIE_NAME = 'empyralis_refresh_token';
export const AUTH_CSRF_COOKIE_NAME = 'empyralis_csrf_token';
export const AUTH_CSRF_HEADER_NAME = 'x-csrf-token';

export function browserCsrfProtectedMethod(method: string): boolean {
  const normalizedMethod = String(method || '').trim().toUpperCase();
  return !['GET', 'HEAD', 'OPTIONS'].includes(normalizedMethod);
}

export function readCsrfTokenFromCookie(cookieSource?: string): string | null {
  const source =
    typeof cookieSource === 'string'
      ? cookieSource
      : typeof document !== 'undefined'
        ? document.cookie
        : '';
  if (!source) {
    return null;
  }
  const prefix = `${AUTH_CSRF_COOKIE_NAME}=`;
  for (const chunk of source.split(';')) {
    const part = chunk.trim();
    if (!part.startsWith(prefix)) {
      continue;
    }
    const value = part.slice(prefix.length).trim();
    return value ? decodeURIComponent(value) : null;
  }
  return null;
}

export function buildCookieAuthHeaders(method: string, headers: HeadersInit = {}): Headers {
  const nextHeaders = new Headers(headers);
  if (!browserCsrfProtectedMethod(method)) {
    return nextHeaders;
  }
  const csrfToken = readCsrfTokenFromCookie();
  if (csrfToken) {
    nextHeaders.set(AUTH_CSRF_HEADER_NAME, csrfToken);
  }
  return nextHeaders;
}

function decodeBase64UrlJson(value: string): Record<string, unknown> | null {
  try {
    const normalized = value.replace(/-/g, '+').replace(/_/g, '/');
    const padded = normalized.padEnd(Math.ceil(normalized.length / 4) * 4, '=');
    return JSON.parse(atob(padded)) as Record<string, unknown>;
  } catch {
    return null;
  }
}

/** True only when the access-token cookie is a well-formed, unexpired JWT —
 * mirrors auth.py's _access_token_is_live(). A dead token grants no real
 * authority (the backend's own auth checks reject it independently on the
 * merits), so it must not be treated as "a live session" for CSRF purposes —
 * otherwise a browser carrying a stale access-token cookie with no matching
 * CSRF cookie is permanently locked out of signup/login/logout with no
 * in-product recovery path. */
export function isAccessTokenLive(token: string | undefined | null): boolean {
  const raw = String(token || '').trim();
  if (!raw) return false;
  const payload = decodeBase64UrlJson(raw.split('.')[1] || '');
  if (!payload) return false;
  const exp = typeof payload.exp === 'number' ? payload.exp : Number(payload.exp);
  if (!Number.isFinite(exp)) return false;
  return exp > Math.floor(Date.now() / 1000);
}

/** Cheap structural check for the opaque `esr_<session>.<secret>` refresh
 * token — mirrors auth.py's _refresh_token_is_structurally_valid(). Format
 * only, not cryptographic; true (DB-backed) validity is checked server-side
 * by the refresh handler itself. */
export function isRefreshTokenStructurallyValid(token: string | undefined | null): boolean {
  const raw = String(token || '').trim();
  if (!raw.startsWith('esr_') || !raw.includes('.')) return false;
  const rest = raw.slice(4);
  const dotIndex = rest.indexOf('.');
  if (dotIndex <= 0) return false;
  return Boolean(rest.slice(0, dotIndex).trim() && rest.slice(dotIndex + 1).trim());
}

// ── CSRF-cookie fallback safety net ─────────────────────────────────────
//
// Pure, isomorphic (no `server-only`, no DOM/Request/Response types) on
// purpose: both frontend/lib/server/control-plane-proxy.ts's
// forwardControlPlaneRequest AND frontend/app/api/auth/google/callback/
// route.ts need the identical rule -- a session that arrives with an
// access/refresh cookie but no CSRF cookie must never be left permanently
// unable to make its first same-site mutation (readCsrfTokenFromCookie
// above finds nothing, buildCookieAuthHeaders sends no x-csrf-token, and
// the backend's validate_csrf / the proxy's own validateBrowserCsrf both
// 403 forever). Google's callback route forwards cookies by hand instead of
// through the shared proxy (it must return an HTML page, not a JSON
// passthrough — see that route's own CSP-nonce comment) and is also the one
// auth entry point whose Set-Cookie headers cross an extra hop this repo
// cannot fully reproduce outside production (a real cross-site redirect
// through accounts.google.com, then Cloudflare/nginx back to the browser —
// CLAUDE.md notes Cloudflare's presence there is itself undocumented in the
// nginx config). Living here, plain and dependency-free, means both call
// sites share one rule instead of silently drifting, and it can be unit
// tested directly (importing control-plane-proxy.ts itself fails outside
// Next's bundler: it starts with `import 'server-only'`, which is not an
// installed package and only resolves via Next's own webpack alias).

/** True exactly when an auth cookie (access or refresh) is present among
 * `cookieNames` without a CSRF cookie alongside it. */
export function needsFallbackCsrfCookie(cookieNames: Iterable<string>): boolean {
  let hasAuthCookie = false;
  let hasCsrfCookie = false;
  for (const name of cookieNames) {
    if (name === AUTH_ACCESS_COOKIE_NAME || name === AUTH_REFRESH_COOKIE_NAME) {
      hasAuthCookie = true;
    }
    if (name === AUTH_CSRF_COOKIE_NAME) {
      hasCsrfCookie = true;
    }
  }
  return hasAuthCookie && !hasCsrfCookie;
}

/** Generates a fresh, non-secret-derived CSRF token for the fallback cookie
 * above. Not a security boundary in itself (the real protection is that the
 * value must round-trip: cookie === x-csrf-token header) — just needs to be
 * unpredictable enough that a cross-site request can't guess it. */
export function generateBrowserCsrfToken(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

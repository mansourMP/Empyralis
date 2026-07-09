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

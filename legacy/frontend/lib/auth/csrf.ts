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

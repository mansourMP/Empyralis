import { NextRequest, NextResponse } from 'next/server';

const AUTH_ACCESS_COOKIE_NAME = 'empyralis_access_token';
const AUTH_REFRESH_COOKIE_NAME = 'empyralis_refresh_token';
const AUTH_CSRF_COOKIE_NAME = 'empyralis_csrf_token';
const AUTH_CSRF_HEADER_NAME = 'x-csrf-token';
const AUTH_REFRESH_GRACE_SECONDS = 120;

type ControlPlaneEnv = Partial<Record<
  | 'EMPYRALIS_API_URL'
  | 'ORION_API_URL'
  | 'NEXT_PUBLIC_ORION_API_URL'
  | 'NEXT_PUBLIC_API_URL'
  | 'EMPYRALIS_DEPLOY_ENV'
  | 'NODE_ENV',
  string | undefined
>>;

function isCloudEnvironment(env: ControlPlaneEnv): boolean {
  const value = String(env.EMPYRALIS_DEPLOY_ENV ?? env.NODE_ENV ?? '').trim().toLowerCase();
  return value === 'production' || value === 'prod' || value === 'staging';
}

function controlPlaneBaseUrl(env: ControlPlaneEnv = process.env as ControlPlaneEnv): string {
  const rawValue =
    env.EMPYRALIS_API_URL
    ?? env.ORION_API_URL
    ?? env.NEXT_PUBLIC_ORION_API_URL
    ?? env.NEXT_PUBLIC_API_URL
    ?? (env.NODE_ENV === 'production' ? undefined : 'http://127.0.0.1:8001');

  const normalized = String(rawValue || '').trim().replace(/\/+$/, '');
  if (!normalized) {
    return '';
  }
  if (!isCloudEnvironment(env)) {
    return normalized;
  }
  try {
    const parsed = new URL(normalized);
    if (
      parsed.protocol !== 'https:'
      || ['127.0.0.1', 'localhost', '0.0.0.0', '::1'].includes(parsed.hostname)
    ) {
      return '';
    }
  } catch {
    return '';
  }
  return normalized;
}

function splitCombinedSetCookieHeader(value: string): string[] {
  const source = String(value || '').trim();
  if (!source) {
    return [];
  }
  return source.split(/,(?=[^;,]+=)/g).map((item) => item.trim()).filter(Boolean);
}

function readSetCookieHeaders(headers: Headers): string[] {
  const responseHeaders = headers as Headers & {
    getSetCookie?: () => string[];
  };
  if (typeof responseHeaders.getSetCookie === 'function') {
    return responseHeaders.getSetCookie();
  }
  return splitCombinedSetCookieHeader(headers.get('set-cookie') || '');
}

function setCookieNameValue(cookie: string): { name: string; value: string } | null {
  const firstPart = String(cookie || '').split(';', 1)[0]?.trim() || '';
  const separatorIndex = firstPart.indexOf('=');
  if (separatorIndex <= 0) {
    return null;
  }
  return {
    name: firstPart.slice(0, separatorIndex).trim(),
    value: firstPart.slice(separatorIndex + 1),
  };
}

function upsertCookieHeader(cookieHeader: string, name: string, value: string): string {
  const nextCookies = new Map<string, string>();
  for (const part of String(cookieHeader || '').split(';')) {
    const trimmed = part.trim();
    const separatorIndex = trimmed.indexOf('=');
    if (separatorIndex <= 0) {
      continue;
    }
    nextCookies.set(trimmed.slice(0, separatorIndex), trimmed.slice(separatorIndex + 1));
  }
  nextCookies.set(name, value);
  return Array.from(nextCookies.entries())
    .map(([cookieName, cookieValue]) => `${cookieName}=${cookieValue}`)
    .join('; ');
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

function shouldRefreshAccessToken(accessToken: string | undefined): boolean {
  const token = String(accessToken || '').trim();
  if (!token) {
    return true;
  }
  const payloadSegment = token.split('.')[1] || '';
  const payload = decodeBase64UrlJson(payloadSegment);
  const exp = typeof payload?.exp === 'number' ? payload.exp : Number(payload?.exp);
  if (!Number.isFinite(exp)) {
    return true;
  }
  return exp <= Math.floor(Date.now() / 1000) + AUTH_REFRESH_GRACE_SECONDS;
}

function shouldHandlePath(pathname: string): boolean {
  if (
    pathname.startsWith('/api/')
    || pathname.startsWith('/_next/')
    || pathname.startsWith('/login')
    || pathname.startsWith('/signup')
    || pathname.startsWith('/auth/')
    || pathname === '/favicon.ico'
  ) {
    return false;
  }
  return true;
}

export async function proxy(request: NextRequest) {
  if (request.method !== 'GET' || !shouldHandlePath(request.nextUrl.pathname)) {
    return NextResponse.next();
  }

  const refreshToken = request.cookies.get(AUTH_REFRESH_COOKIE_NAME)?.value;
  if (!refreshToken || !shouldRefreshAccessToken(request.cookies.get(AUTH_ACCESS_COOKIE_NAME)?.value)) {
    return NextResponse.next();
  }

  const csrfToken = request.cookies.get(AUTH_CSRF_COOKIE_NAME)?.value;
  const upstreamBaseUrl = controlPlaneBaseUrl();
  if (!csrfToken || !upstreamBaseUrl) {
    return NextResponse.next();
  }

  try {
    const refreshResponse = await fetch(`${upstreamBaseUrl}/api/v1/auth/refresh`, {
      method: 'POST',
      cache: 'no-store',
      headers: {
        accept: 'application/json',
        'content-type': 'application/json',
        cookie: request.headers.get('cookie') || '',
        [AUTH_CSRF_HEADER_NAME]: csrfToken,
        'x-forwarded-host': request.headers.get('host') || '',
        'x-forwarded-proto': request.nextUrl.protocol.replace(':', '') || 'http',
      },
      body: JSON.stringify({ channel: 'web' }),
    });

    if (!refreshResponse.ok) {
      return NextResponse.next();
    }

    const setCookieHeaders = readSetCookieHeaders(refreshResponse.headers);
    if (setCookieHeaders.length === 0) {
      return NextResponse.next();
    }

    const requestHeaders = new Headers(request.headers);
    let cookieHeader = requestHeaders.get('cookie') || '';
    for (const cookie of setCookieHeaders) {
      const parsedCookie = setCookieNameValue(cookie);
      if (parsedCookie) {
        cookieHeader = upsertCookieHeader(cookieHeader, parsedCookie.name, parsedCookie.value);
      }
    }
    requestHeaders.set('cookie', cookieHeader);

    const nextResponse = NextResponse.next({
      request: {
        headers: requestHeaders,
      },
    });
    for (const cookie of setCookieHeaders) {
      nextResponse.headers.append('set-cookie', cookie);
    }
    return nextResponse;
  } catch {
    return NextResponse.next();
  }
}

export const config = {
  matcher: ['/((?!_next/static|_next/image|favicon.ico).*)'],
};

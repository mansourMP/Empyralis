import 'server-only';

import { NextRequest, NextResponse } from 'next/server';

import {
  AUTH_ACCESS_COOKIE_NAME,
  AUTH_CSRF_COOKIE_NAME,
  AUTH_CSRF_HEADER_NAME,
  AUTH_REFRESH_COOKIE_NAME,
  browserCsrfProtectedMethod,
  isAccessTokenLive,
  isRefreshTokenStructurallyValid,
} from '@/lib/auth/csrf';
import { controlPlaneBaseUrl } from '@/lib/server/control-plane-base-url';

type ForwardControlPlaneRequestInit = RequestInit & {
  timeoutMs?: number;
  /** Logout is the guaranteed escape hatch from a stuck session — it must
   * always reach the backend and clear cookies, never itself be blocked by
   * the CSRF check it exists to help a user recover from. Only the logout
   * route should set this; every other route keeps full CSRF enforcement. */
  bypassCsrf?: boolean;
};

const HOP_BY_HOP_REQUEST_HEADERS = new Set([
  'connection',
  'content-length',
  'host',
  'keep-alive',
  'proxy-authenticate',
  'proxy-authorization',
  'te',
  'trailer',
  'transfer-encoding',
  'upgrade',
]);

const HOP_BY_HOP_RESPONSE_HEADERS = new Set([
  'connection',
  'content-length',
  'content-encoding',
  'keep-alive',
  'proxy-authenticate',
  'proxy-authorization',
  'te',
  'trailer',
  'transfer-encoding',
  'upgrade',
]);

const BROWSER_AUTH_UPSTREAM_PREFIX = '/api/v1/auth/';

function upstreamUnavailableResponse(error: unknown): NextResponse {
  const reason = error instanceof Error ? error.message : 'upstream_unavailable';
  return new NextResponse(
    JSON.stringify({
      detail: 'Control plane is unavailable. Check that the configured backend is reachable from this deployment or restart the local runtime.',
      reason,
    }),
    {
      status: 503,
      headers: { 'content-type': 'application/json' },
    },
  );
}

function hasBrowserSessionCookie(request: NextRequest): boolean {
  // The CSRF cookie itself must not count as a "session" signal: it can
  // outlive the access/refresh cookies it was issued alongside (e.g. after
  // they expire or are cleared individually), and treating its mere presence
  // as a live session would permanently 403 a returning browser out of
  // logging back in. Mirrors the backend's own definition of a live session
  // in validate_csrf() (access or refresh token, never the CSRF cookie).
  //
  // Presence alone isn't enough either: an access/refresh cookie that's
  // actually dead (expired JWT, garbage refresh token — the kind a browser
  // keeps echoing back for weeks after the session it belonged to is gone)
  // grants no real authority, so treating it as "live" here only serves to
  // permanently CSRF-lock the browser out of signup/login/logout with no
  // recovery path. Check liveness, matching auth.py's allow_expired_session
  // leniency — applied unconditionally here since this layer has no
  // per-route reason to keep that leniency opt-in.
  const access = request.cookies.get(AUTH_ACCESS_COOKIE_NAME)?.value;
  const refresh = request.cookies.get(AUTH_REFRESH_COOKIE_NAME)?.value;
  return isAccessTokenLive(access) || isRefreshTokenStructurallyValid(refresh);
}

function validateBrowserCsrf(request: NextRequest, bypassCsrf?: boolean): NextResponse | null {
  if (bypassCsrf) {
    return null;
  }
  if (!browserCsrfProtectedMethod(request.method)) {
    return null;
  }
  if (!hasBrowserSessionCookie(request)) {
    return null;
  }
  const csrfCookie = request.cookies.get(AUTH_CSRF_COOKIE_NAME)?.value?.trim() || '';
  const csrfHeader = request.headers.get(AUTH_CSRF_HEADER_NAME)?.trim() || '';
  if (!csrfCookie || !csrfHeader || csrfCookie !== csrfHeader) {
    return new NextResponse(JSON.stringify({ detail: 'CSRF validation failed.' }), {
      status: 403,
      headers: { 'content-type': 'application/json' },
    });
  }
  return null;
}

function splitCombinedSetCookieHeader(value: string): string[] {
  const source = String(value || '').trim();
  if (!source) {
    return [];
  }
  return source.split(/,(?=[^;,]+=)/g).map((item) => item.trim()).filter(Boolean);
}

function setCookieHeadersContain(headers: Headers, cookieName: string): boolean {
  const prefix = `${cookieName}=`;
  const responseHeaders = headers as Headers & {
    getSetCookie?: () => string[];
  };
  const setCookieValues =
    typeof responseHeaders.getSetCookie === 'function'
      ? responseHeaders.getSetCookie()
      : splitCombinedSetCookieHeader(headers.get('set-cookie') || '');
  return setCookieValues.some((cookie) => cookie.trim().startsWith(prefix));
}

function shouldEnsureBrowserCsrfCookie(upstreamPath: string, responseHeaders: Headers): boolean {
  if (!upstreamPath.startsWith(BROWSER_AUTH_UPSTREAM_PREFIX)) {
    return false;
  }
  const hasAuthCookie =
    setCookieHeadersContain(responseHeaders, AUTH_ACCESS_COOKIE_NAME)
    || setCookieHeadersContain(responseHeaders, AUTH_REFRESH_COOKIE_NAME);
  if (!hasAuthCookie) {
    return false;
  }
  return !setCookieHeadersContain(responseHeaders, AUTH_CSRF_COOKIE_NAME);
}

function generateBrowserCsrfToken(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

function copyForwardableRequestHeaders(
  request: NextRequest,
  targetHeaders: Headers,
  initHeaders?: HeadersInit,
): void {
  for (const [name, value] of request.headers.entries()) {
    const normalizedName = name.toLowerCase();
    if (HOP_BY_HOP_REQUEST_HEADERS.has(normalizedName)) {
      continue;
    }
    targetHeaders.set(name, value);
  }

  // Next.js strips the cookie header from request.headers (cookies are
  // available only via request.cookies). Reconstruct it so the upstream
  // Python backend can read session tokens from forwarded cookies.
  if (!targetHeaders.has('cookie')) {
    const cookieHeader = request.cookies.toString();
    if (cookieHeader) {
      targetHeaders.set('cookie', cookieHeader);
    }
  }

  if (!targetHeaders.has('x-forwarded-host')) {
    const host = request.headers.get('host');
    if (host) {
      targetHeaders.set('x-forwarded-host', host);
    }
  }
  if (!targetHeaders.has('x-forwarded-proto')) {
    targetHeaders.set('x-forwarded-proto', request.nextUrl.protocol.replace(':', '') || 'http');
  }

  if (!initHeaders) {
    return;
  }

  const overrideHeaders = new Headers(initHeaders);
  for (const [name, value] of overrideHeaders.entries()) {
    targetHeaders.set(name, value);
  }
}

function copyForwardableResponseHeaders(response: Response): Headers {
  const headers = new Headers();
  for (const [name, value] of response.headers.entries()) {
    const normalizedName = name.toLowerCase();
    if (HOP_BY_HOP_RESPONSE_HEADERS.has(normalizedName) || normalizedName === 'set-cookie') {
      continue;
    }
    headers.append(name, value);
  }

  const responseHeaders = response.headers as Headers & {
    getSetCookie?: () => string[];
  };
  const setCookieValues =
    typeof responseHeaders.getSetCookie === 'function'
      ? responseHeaders.getSetCookie()
      : [];
  if (setCookieValues.length > 0) {
    for (const cookie of setCookieValues) {
      headers.append('set-cookie', cookie);
    }
  } else {
    const setCookieHeader = response.headers.get('set-cookie');
    if (setCookieHeader) {
      for (const cookie of splitCombinedSetCookieHeader(setCookieHeader)) {
        headers.append('set-cookie', cookie);
      }
    }
  }

  return headers;
}

export async function forwardControlPlaneRequest(
  request: NextRequest,
  upstreamPath: string,
  init: ForwardControlPlaneRequestInit = {},
): Promise<NextResponse> {
  const csrfFailure = validateBrowserCsrf(request, init.bypassCsrf);
  if (csrfFailure) {
    return csrfFailure;
  }

  const upstreamUrl = `${controlPlaneBaseUrl()}${upstreamPath}`;
  const headers = new Headers();
  copyForwardableRequestHeaders(request, headers, init.headers);
  const timeoutMs = Number.isFinite(init.timeoutMs) ? Math.max(1, Number(init.timeoutMs)) : null;

  // Read the incoming body as raw bytes, never as text. request.text() decodes
  // the body as UTF-8 and re-encodes it when handed back to fetch() below —
  // any byte sequence that isn't valid UTF-8 (any binary upload: images,
  // PDFs, the multipart bodies attachment uploads use) gets each invalid
  // byte replaced with U+FFFD, silently corrupting the file in transit while
  // the request still returns 200. arrayBuffer() passes the exact bytes
  // through untouched. This is safe for JSON/text bodies too — an
  // ArrayBuffer round-trips any UTF-8-safe payload byte-for-byte, and the
  // original content-type header (never touched here) is what tells the
  // upstream how to interpret it.
  const body =
    init.body !== undefined
      ? init.body
      : request.method === 'GET' || request.method === 'HEAD'
        ? undefined
        : await request.arrayBuffer();
  const isEmptyBody = body === '' || (body instanceof ArrayBuffer && body.byteLength === 0);
  const controller = timeoutMs === null ? null : new AbortController();
  const timeoutHandle = controller && timeoutMs !== null
    ? setTimeout(() => {
      controller.abort();
    }, timeoutMs)
    : null;

  try {
    const response = await fetch(upstreamUrl, {
      method: init.method ?? request.method,
      cache: 'no-store',
      redirect: 'manual',
      headers,
      body: isEmptyBody ? undefined : body,
      signal: controller?.signal,
    });

    const responseHeaders = copyForwardableResponseHeaders(response);
    const contentType = response.headers.get('content-type')?.toLowerCase() || '';
    const shouldStreamResponse = contentType.includes('text/event-stream');
    const responseBody = shouldStreamResponse
      ? response.body
      : response.body === null
        ? null
        : await response.arrayBuffer();

    const nextResponse = new NextResponse(responseBody, {
      status: response.status,
      headers: responseHeaders,
    });
    if (shouldEnsureBrowserCsrfCookie(upstreamPath, responseHeaders)) {
      nextResponse.cookies.set(AUTH_CSRF_COOKIE_NAME, generateBrowserCsrfToken(), {
        httpOnly: false,
        secure: request.nextUrl.protocol === 'https:',
        sameSite: 'lax',
        path: '/',
      });
    }
    return nextResponse;
  } catch (error) {
    if (controller && error instanceof Error && error.name === 'AbortError') {
      return new NextResponse(
        JSON.stringify({ detail: `Upstream request timed out after ${timeoutMs}ms.` }),
        {
          status: 504,
          headers: { 'content-type': 'application/json' },
        },
      );
    }
    return upstreamUnavailableResponse(error);
  } finally {
    if (timeoutHandle !== null) {
      clearTimeout(timeoutHandle);
    }
  }
}

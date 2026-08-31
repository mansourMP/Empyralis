import { NextResponse, type NextRequest } from 'next/server';

import {
  AUTH_CSRF_COOKIE_NAME,
  generateBrowserCsrfToken,
  needsFallbackCsrfCookie,
} from '@/lib/auth/csrf';
import { AUTH_REQUEST_TIMEOUT_MS } from '@/lib/auth/auth-timeouts';
import { controlPlaneBaseUrl } from '@/lib/server/control-plane-base-url';
import { CSP_NONCE_REQUEST_HEADER } from '@/lib/security/content-security-policy';
import {
  decodeGoogleOAuthState,
  GOOGLE_OAUTH_STATE_COOKIE,
  GOOGLE_OAUTH_STATE_MAX_AGE_SECONDS,
  googleOAuthClientId,
  googleOAuthClientSecret,
  googleOAuthConfigured,
  googleOAuthRedirectUri,
  isAllowedAuthOrigin,
  requestOrigin,
  splitCombinedSetCookieHeader,
} from '@/lib/server/google-oauth';

export const dynamic = 'force-dynamic';

type GoogleTokenPayload = {
  id_token?: string;
  error?: string;
  error_description?: string;
};

function logGoogleAuthFailure(message: string, metadata: Record<string, unknown> = {}): void {
  console.error('[auth/google/callback]', message, metadata);
}

function redirectWithError(request: NextRequest, error: string): NextResponse {
  const url = new URL('/login', requestOrigin(request));
  url.searchParams.set('error', error);
  const response = NextResponse.redirect(url);
  response.cookies.delete(GOOGLE_OAUTH_STATE_COOKIE);
  return response;
}

function appendUpstreamCookies(target: NextResponse, sourceHeaders: Headers): void {
  const headersWithCookies = sourceHeaders as Headers & {
    getSetCookie?: () => string[];
  };
  const setCookieValues =
    typeof headersWithCookies.getSetCookie === 'function'
      ? headersWithCookies.getSetCookie()
      : splitCombinedSetCookieHeader(sourceHeaders.get('set-cookie') || '');
  for (const cookie of setCookieValues) {
    target.headers.append('set-cookie', cookie);
  }
}

function backendGoogleAuthErrorCode(status: number, detail: string): string {
  const normalized = detail.toLowerCase();
  if (status === 429) {
    return 'google_rate_limited';
  }
  if (
    normalized.includes('authentication is not configured')
    || normalized.includes('audience')
    || normalized.includes('unsupported authentication provider')
  ) {
    return 'google_runtime_not_configured';
  }
  return 'google_auth_failed';
}

async function exchangeGoogleCodeForIdToken(request: NextRequest, code: string): Promise<string> {
  const controller = new AbortController();
  const timeoutHandle = setTimeout(() => controller.abort(), AUTH_REQUEST_TIMEOUT_MS);
  try {
    const tokenResponse = await fetch('https://oauth2.googleapis.com/token', {
      method: 'POST',
      cache: 'no-store',
      signal: controller.signal,
      headers: {
        'content-type': 'application/x-www-form-urlencoded',
        accept: 'application/json',
      },
      body: new URLSearchParams({
        code,
        client_id: googleOAuthClientId(),
        client_secret: googleOAuthClientSecret(),
        redirect_uri: googleOAuthRedirectUri(request),
        grant_type: 'authorization_code',
      }),
    });
    const payload = await tokenResponse.json().catch(() => null) as GoogleTokenPayload | null;
    if (!tokenResponse.ok || !payload?.id_token) {
      throw new Error(payload?.error_description || payload?.error || 'Google token exchange failed.');
    }
    return payload.id_token;
  } finally {
    clearTimeout(timeoutHandle);
  }
}

async function loginWithGoogleIdentityToken(
  request: NextRequest,
  idToken: string,
  acquisitionToken?: string,
): Promise<Response> {
  const controller = new AbortController();
  const timeoutHandle = setTimeout(() => controller.abort(), AUTH_REQUEST_TIMEOUT_MS);
  try {
    return await fetch(`${controlPlaneBaseUrl()}/api/v1/auth/provider-login`, {
      method: 'POST',
      cache: 'no-store',
      signal: controller.signal,
      redirect: 'manual',
      headers: {
        accept: 'application/json',
        'content-type': 'application/json',
        'x-forwarded-host': request.headers.get('host') || new URL(requestOrigin(request)).host,
        'x-forwarded-proto': request.nextUrl.protocol.replace(':', '') || 'https',
      },
      body: JSON.stringify({
        provider: 'google',
        identity_token: idToken,
        channel: 'web',
        acquisition_token: acquisitionToken || undefined,
      }),
    });
  } finally {
    clearTimeout(timeoutHandle);
  }
}

export async function GET(request: NextRequest) {
  if (!isAllowedAuthOrigin(request)) {
    return redirectWithError(request, 'google_origin_not_allowed');
  }
  if (!googleOAuthConfigured()) {
    return redirectWithError(request, 'google_not_configured');
  }

  const code = String(request.nextUrl.searchParams.get('code') || '').trim();
  const state = String(request.nextUrl.searchParams.get('state') || '').trim();
  const cookieState = decodeGoogleOAuthState(request.cookies.get(GOOGLE_OAUTH_STATE_COOKIE)?.value);
  const stateAgeMs = Date.now() - Number(cookieState?.createdAt || 0);
  if (!code || !state || !cookieState || cookieState.state !== state || stateAgeMs > GOOGLE_OAUTH_STATE_MAX_AGE_SECONDS * 1000) {
    return redirectWithError(request, 'google_state_invalid');
  }

  try {
    const idToken = await exchangeGoogleCodeForIdToken(request, code);
    const upstream = await loginWithGoogleIdentityToken(request, idToken, cookieState.acquisitionToken);
    if (!upstream.ok) {
      const upstreamText = await upstream.text().catch(() => '');
      logGoogleAuthFailure('Backend provider login failed.', {
        status: upstream.status,
        detail: upstreamText.slice(0, 300),
      });
      return redirectWithError(request, backendGoogleAuthErrorCode(upstream.status, upstreamText));
    }
    const completionUrl = new URL('/auth/complete', requestOrigin(request));
    completionUrl.searchParams.set('provider', 'google');
    completionUrl.searchParams.set('next', '/');

    // Next.js strips set-cookie headers from redirect responses, so we return
    // a 200 HTML page. Set cookies server-side on the response (preserving
    // HttpOnly) so the browser receives the auth tokens correctly. The JS-only
    // approach dropped HttpOnly cookies (access/refresh tokens), breaking login.
    const upstreamCookies = upstream.headers.getSetCookie
      ? upstream.headers.getSetCookie()
      : splitCombinedSetCookieHeader(upstream.headers.get('set-cookie') || '');

    // NONCE IS REQUIRED HERE, and its absence took Google sign-in down.
    //
    // The strict CSP (sec/content-security-policy, 2026-08-13) ships
    // `script-src 'self' 'nonce-<per-request>' 'strict-dynamic'` with no
    // 'unsafe-inline'. This inline script had no nonce, so the browser
    // refused to run it — and this hand-off is the ONLY thing that moves the
    // user from the callback to /auth/complete. The result was a blank white
    // page on an otherwise SUCCESSFUL login: the token exchange worked, the
    // backend returned 200, the auth cookies were set, and the browser simply
    // never navigated. Retrying then reused a spent authorization code and
    // produced a genuine "Bad Request" from Google, which made the failure
    // look like an OAuth problem rather than a CSP one.
    //
    // `x-nonce` is set on the request by frontend/proxy.ts for exactly this.
    // If it is ever missing, fall back to a <meta refresh>, which is not a
    // script and therefore not subject to script-src — a working login
    // matters more than the prettier redirect.
    const redirectUrl = completionUrl.toString();
    const nonce = request.headers.get(CSP_NONCE_REQUEST_HEADER) || '';
    const escapedUrl = redirectUrl.replace(/&/g, '&amp;').replace(/"/g, '&quot;');
    const html = nonce
      ? `<!DOCTYPE html><html><head><meta charset="utf-8"></head><body><script nonce="${nonce}">
window.location.href = ${JSON.stringify(redirectUrl)};
</script></body></html>`
      : `<!DOCTYPE html><html><head><meta charset="utf-8"><meta http-equiv="refresh" content="0;url=${escapedUrl}"></head><body></body></html>`;

    const response = new NextResponse(html, {
      status: 200,
      headers: { 'Content-Type': 'text/html; charset=utf-8' },
    });

    // Forward Set-Cookie headers from the backend as real browser cookies
    // (server-side set, so HttpOnly is preserved — unlike document.cookie).
    const parsedCookieNames: string[] = [];
    for (const raw of upstreamCookies) {
      const parts = raw.split(';').map(s => s.trim());
      const [nvPair] = parts;
      const eqIdx = nvPair.indexOf('=');
      if (eqIdx <= 0) continue;
      const name = nvPair.slice(0, eqIdx).trim();
      const value = nvPair.slice(eqIdx + 1);
      if (!name || !value) continue;
      parsedCookieNames.push(name);

      const options: {
        httpOnly?: boolean;
        secure?: boolean;
        sameSite?: 'strict' | 'lax' | 'none';
        maxAge?: number;
        path?: string;
        domain?: string;
      } = {};
      for (const attr of parts.slice(1)) {
        const lower = attr.toLowerCase();
        if (lower === 'httponly') options.httpOnly = true;
        else if (lower === 'secure') options.secure = true;
        else if (lower.startsWith('samesite=')) {
          const v = attr.split('=')[1]?.toLowerCase();
          if (v === 'strict' || v === 'lax' || v === 'none') options.sameSite = v;
        } else if (lower.startsWith('max-age=')) {
          const n = parseInt(attr.split('=')[1], 10);
          if (Number.isFinite(n)) options.maxAge = n;
        } else if (lower.startsWith('path=')) options.path = attr.split('=')[1];
        else if (lower.startsWith('domain=')) options.domain = attr.split('=')[1];
      }
      response.cookies.set(name, value, options);
    }

    // Same safety net forwardControlPlaneRequest already applies to every
    // other auth route (control-plane-proxy.ts's shouldEnsureBrowserCsrfCookie):
    // a session that arrives with an access/refresh cookie but no CSRF
    // cookie must never be left permanently unable to make its first
    // same-site mutation (the /onboarding auto-submit PATCH right after this
    // redirect is exactly that request). This route re-forwards cookies by
    // hand instead of going through that shared proxy -- it has to return an
    // HTML page, not a JSON passthrough, per the CSP-nonce comment above --
    // so it did not inherit the fallback. Google's callback is also the one
    // auth entry point whose Set-Cookie headers cross an extra hop this repo
    // cannot fully reproduce outside production (a real cross-site redirect
    // through accounts.google.com, then Cloudflare/nginx back to the
    // browser -- CLAUDE.md notes Cloudflare's presence there is itself
    // undocumented in the nginx config); minting a fresh CSRF cookie here
    // when it is missing closes the gap regardless of which hop dropped it,
    // without weakening the CSRF check itself (validate_csrf's comparison is
    // untouched -- this only guarantees a cookie exists to compare against).
    if (needsFallbackCsrfCookie(parsedCookieNames)) {
      response.cookies.set(AUTH_CSRF_COOKIE_NAME, generateBrowserCsrfToken(), {
        httpOnly: false,
        secure: request.nextUrl.protocol === 'https:',
        sameSite: 'lax',
        path: '/',
      });
    }

    return response;
  } catch (error) {
    const errMsg = error instanceof Error ? error.message : 'unknown';
    logGoogleAuthFailure('Google OAuth callback failed.', { message: errMsg });
    // Surface the actual error in the redirect so we can see what failed
    return redirectWithError(request, `google_auth_failed: ${encodeURIComponent(errMsg)}`);
  }
}

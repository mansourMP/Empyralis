import { NextResponse, type NextRequest } from 'next/server';

import {
  encodeGoogleOAuthState,
  GOOGLE_OAUTH_STATE_COOKIE,
  GOOGLE_OAUTH_STATE_MAX_AGE_SECONDS,
  googleOAuthClientId,
  googleOAuthConfigured,
  googleOAuthRedirectUri,
  isAllowedAuthOrigin,
  requestOrigin,
} from '@/lib/server/google-oauth';

export const dynamic = 'force-dynamic';

function authErrorRedirect(request: NextRequest, error: string): NextResponse {
  const url = new URL('/login', requestOrigin(request));
  url.searchParams.set('error', error);
  return NextResponse.redirect(url);
}

export async function GET(request: NextRequest) {
  if (!isAllowedAuthOrigin(request)) {
    return authErrorRedirect(request, 'google_origin_not_allowed');
  }
  if (!googleOAuthConfigured()) {
    return authErrorRedirect(request, 'google_not_configured');
  }

  const state = crypto.randomUUID();
  const acquisitionToken = String(
    request.nextUrl.searchParams.get('channel_attribution')
      || request.nextUrl.searchParams.get('state')
      || '',
  ).trim();
  const statePayload = encodeGoogleOAuthState({
    state,
    acquisitionToken: acquisitionToken || undefined,
    createdAt: Date.now(),
  });
  const redirectUri = googleOAuthRedirectUri(request);
  const authorizationUrl = new URL('https://accounts.google.com/o/oauth2/v2/auth');
  authorizationUrl.searchParams.set('client_id', googleOAuthClientId());
  authorizationUrl.searchParams.set('redirect_uri', redirectUri);
  authorizationUrl.searchParams.set('response_type', 'code');
  authorizationUrl.searchParams.set('scope', 'openid email profile');
  authorizationUrl.searchParams.set('access_type', 'online');
  authorizationUrl.searchParams.set('prompt', 'select_account');
  authorizationUrl.searchParams.set('state', state);

  const response = NextResponse.redirect(authorizationUrl);
  response.cookies.set(GOOGLE_OAUTH_STATE_COOKIE, statePayload, {
    httpOnly: true,
    sameSite: 'lax',
    secure: request.nextUrl.protocol === 'https:',
    path: '/',
    maxAge: GOOGLE_OAUTH_STATE_MAX_AGE_SECONDS,
  });
  return response;
}

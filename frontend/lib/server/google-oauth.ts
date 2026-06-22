import 'server-only';

import type { NextRequest } from 'next/server';

export const GOOGLE_OAUTH_STATE_COOKIE = 'empyralis_google_oauth_state';
export const GOOGLE_OAUTH_STATE_MAX_AGE_SECONDS = 600;

export type GoogleOAuthStatePayload = {
  state: string;
  acquisitionToken?: string;
  createdAt: number;
};

function splitList(value: string | undefined): string[] {
  return String(value || '')
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean);
}

function originOf(value: string): string | null {
  try {
    return new URL(value).origin;
  } catch {
    return null;
  }
}

const DEFAULT_PRODUCTION_AUTH_ORIGINS = [
  'https://empyralis.ai',
  'https://www.empyralis.ai',
];

export function googleOAuthClientId(): string {
  return String(
    process.env.GOOGLE_AUTH_CLIENT_ID
      || process.env.GOOGLE_AUTH_WEB_CLIENT_ID
      || process.env.GOOGLE_OAUTH_CLIENT_ID
      || process.env.GOOGLE_WEB_CLIENT_ID
      || process.env.GOOGLE_CLIENT_ID
      || '',
  ).trim();
}

export function googleOAuthClientSecret(): string {
  return String(
    process.env.GOOGLE_AUTH_CLIENT_SECRET
      || process.env.GOOGLE_AUTH_WEB_CLIENT_SECRET
      || process.env.GOOGLE_OAUTH_CLIENT_SECRET
      || process.env.GOOGLE_WEB_CLIENT_SECRET
      || process.env.GOOGLE_CLIENT_SECRET
      || '',
  ).trim();
}

export function googleOAuthConfigured(): boolean {
  return Boolean(googleOAuthClientId() && googleOAuthClientSecret());
}

export function requestOrigin(request: NextRequest): string {
  const forwardedHost = String(request.headers.get('x-forwarded-host') || '')
    .split(',')[0]
    .trim();
  const host = forwardedHost || String(request.headers.get('host') || '').trim();
  if (!host) {
    return request.nextUrl.origin;
  }
  const forwardedProto = String(request.headers.get('x-forwarded-proto') || '')
    .split(',')[0]
    .trim()
    .replace(/:$/, '');
  const proto = forwardedProto || request.nextUrl.protocol.replace(':', '') || 'https';
  return `${proto}://${host}`;
}

export function configuredAuthOrigins(): Set<string> {
  const candidates = [
    ...DEFAULT_PRODUCTION_AUTH_ORIGINS,
    ...splitList(process.env.EMPYRALIS_AUTH_ALLOWED_ORIGINS),
    ...splitList(process.env.NEXT_PUBLIC_APP_URL),
    ...splitList(process.env.EMPYRALIS_WEB_URL),
    ...splitList(process.env.RENDER_EXTERNAL_URL),
  ];
  const origins = candidates
    .map(originOf)
    .filter((item): item is string => Boolean(item));
  return new Set(origins);
}

export function isAllowedAuthOrigin(request: NextRequest): boolean {
  const origin = requestOrigin(request);

  // In dev without an explicit allowed-origins list, allow all origins.
  // Must check BEFORE configuredAuthOrigins() because DEFAULT_PRODUCTION_AUTH_ORIGINS
  // always populates the set, making the old dev fallback unreachable.
  if (
    process.env.NODE_ENV !== 'production'
    && !process.env.EMPYRALIS_AUTH_ALLOWED_ORIGINS
  ) {
    return true;
  }

  const allowed = configuredAuthOrigins();
  if (allowed.size > 0) {
    return allowed.has(origin);
  }
  if (process.env.NODE_ENV !== 'production') {
    return true;
  }
  return false;
}

export function googleOAuthRedirectUri(request: NextRequest): string {
  const configured = String(process.env.GOOGLE_OAUTH_REDIRECT_URI || '').trim();
  if (configured) {
    return configured;
  }
  return `${requestOrigin(request)}/api/auth/google/callback`;
}

export function encodeGoogleOAuthState(payload: GoogleOAuthStatePayload): string {
  return Buffer.from(JSON.stringify(payload), 'utf8').toString('base64url');
}

export function decodeGoogleOAuthState(value: string | undefined): GoogleOAuthStatePayload | null {
  const raw = String(value || '').trim();
  if (!raw) {
    return null;
  }
  try {
    const payload = JSON.parse(Buffer.from(raw, 'base64url').toString('utf8')) as Partial<GoogleOAuthStatePayload>;
    const state = String(payload.state || '').trim();
    const createdAt = Number(payload.createdAt || 0);
    if (!state || !Number.isFinite(createdAt)) {
      return null;
    }
    return {
      state,
      acquisitionToken: String(payload.acquisitionToken || '').trim() || undefined,
      createdAt,
    };
  } catch {
    return null;
  }
}

export function splitCombinedSetCookieHeader(value: string): string[] {
  const source = String(value || '').trim();
  if (!source) {
    return [];
  }
  return source.split(/,(?=[^;,]+=)/g).map((item) => item.trim()).filter(Boolean);
}

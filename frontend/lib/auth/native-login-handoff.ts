/**
 * Native app login handoff -- the web half of "open the real site in a
 * browser, log in however you like, land back in the app signed in."
 *
 * Mirrors server_modules/native_auth_service.py's own header comment; read
 * that one first for the full authorization-code + PKCE design. This module
 * is the ONE place that decides whether a `/login` visit (or a return from
 * an external provider) is a native handoff request, and the ONE place that
 * builds the redirect URL a mint response turns into -- both `/login` and
 * `/auth/complete` (see their own comments for why there are two) go through
 * these same functions rather than each carrying its own copy of the rules.
 *
 * THE REDIRECT TARGET IS NEVER BUILT FROM A HARDCODED SCHEME HERE. It always
 * comes from the backend's own mint response (`redirect_uri`, resolved
 * server-side from `native_auth_service.NATIVE_REDIRECT_TARGETS`) -- this
 * file only decides WHETHER to ask for one and how to append `code`/`state`
 * to whatever the backend returned. That keeps the scheme authored in
 * exactly one place, matching the backend module's own "hardcoded
 * server-side, never caller-supplied" rule.
 *
 * Everything here degrades to the ordinary web-login behaviour on anything
 * malformed -- a missing/unknown target, a code_challenge that isn't a real
 * RFC 7636 S256 value, a missing state -- rather than erroring, the same
 * "reject -> fall back to the harmless default" posture safeNextPath (this
 * directory's own sibling module) already uses for open-redirect rejections.
 *
 * Run: npx tsx lib/auth/native-login-handoff.test.ts
 */

const IOS_TARGET = 'ios';

// RFC 7636 S4.2: a code_challenge (S256 method) is base64url(SHA-256(x)),
// unpadded -- always exactly 43 characters from [A-Za-z0-9_-].
const CODE_CHALLENGE_RE = /^[A-Za-z0-9_-]{43}$/;

// A CSRF nonce the APP generates; not a fixed shape by any RFC, but it
// travels through a URL and (via sessionStorage, see auth-client.ts) so it
// is bounded the same way every other opaque token in this codebase is.
const STATE_RE = /^[A-Za-z0-9._~-]{1,256}$/;

export type NativeHandoffQuery = {
  native: string | null | undefined;
  codeChallenge: string | null | undefined;
  state: string | null | undefined;
};

export type NativeHandoffPlan =
  | { kind: 'web' }
  | { kind: 'native'; target: 'ios'; codeChallenge: string; state: string };

/** Is this the shape of an S256 code_challenge? Exported so the mint request
 * itself (and its own test) can fail fast client-side on a value that could
 * never be accepted server-side, without duplicating the regex. */
export function isPlausibleCodeChallenge(value: string): boolean {
  return CODE_CHALLENGE_RE.test(value);
}

/** Decides whether a `/login` visit (native=ios&code_challenge=...&state=...
 * in the query string) or an `/auth/complete` return (the same three fields,
 * relayed through sessionStorage -- see planNativeLoginHandoffFromRecord
 * below) is a native handoff. */
export function planNativeLoginHandoff(query: NativeHandoffQuery): NativeHandoffPlan {
  const native = String(query.native || '').trim().toLowerCase();
  if (native !== IOS_TARGET) {
    return { kind: 'web' };
  }
  const codeChallenge = String(query.codeChallenge || '').trim();
  const state = String(query.state || '').trim();
  if (!isPlausibleCodeChallenge(codeChallenge) || !STATE_RE.test(state)) {
    return { kind: 'web' };
  }
  return { kind: 'native', target: 'ios', codeChallenge, state };
}

/** Same decision, applied to whatever came back out of sessionStorage on
 * `/auth/complete` -- see this file's own header comment for why the Google
 * return path needs a second entry point into the identical rule rather
 * than re-reading `/login`'s query string (which the OAuth round trip does
 * not carry). `record` is untyped on purpose: it is JSON a previous page
 * wrote and this page is reading back, which is an assumption until
 * checked, never an observation. */
export function planNativeLoginHandoffFromRecord(record: unknown): NativeHandoffPlan {
  if (!record || typeof record !== 'object') {
    return { kind: 'web' };
  }
  const candidate = record as Record<string, unknown>;
  return planNativeLoginHandoff({
    native: typeof candidate.native === 'string' ? candidate.native : null,
    codeChallenge: typeof candidate.codeChallenge === 'string' ? candidate.codeChallenge : null,
    state: typeof candidate.state === 'string' ? candidate.state : null,
  });
}

/**
 * Builds `${redirect_uri}?code=...&state=...` from a mint response.
 * Returns null (never throws) on a redirect_uri or code the backend somehow
 * left empty -- a pure function has no good way to escalate that beyond
 * "here is nothing usable," and the caller already has to handle a thrown
 * network error from the mint call itself, so a second failure shape here
 * would just be one more thing every caller has to remember to catch.
 */
export function nativeHandoffRedirectUrl(redirectUri: string, code: string, state: string): string | null {
  const cleanRedirectUri = String(redirectUri || '').trim();
  const cleanCode = String(code || '').trim();
  if (!cleanRedirectUri || !cleanCode) {
    return null;
  }
  const params = new URLSearchParams();
  params.set('code', cleanCode);
  const cleanState = String(state || '').trim();
  if (cleanState) {
    params.set('state', cleanState);
  }
  return `${cleanRedirectUri}?${params.toString()}`;
}

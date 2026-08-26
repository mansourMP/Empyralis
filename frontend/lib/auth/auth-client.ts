import { buildCookieAuthHeaders } from '@/lib/auth/csrf';
import { AUTH_REQUEST_TIMEOUT_MS, AUTH_TIMEOUT_MESSAGE } from '@/lib/auth/auth-timeouts';

type AuthRequestOptions = {
  method: 'GET' | 'POST';
  body?: Record<string, unknown>;
};

type AwaitBrowserAuthReadyOptions = {
  path?: string;
  attempts?: number;
  delayMs?: number;
};

type ExternalAuthProvider = 'google';

type ExternalAuthPendingRecord = {
  provider: ExternalAuthProvider;
  startedAt: number;
};

type ExternalAuthCompletionRecord = {
  provider: ExternalAuthProvider;
  completedAt: number;
};

export type AuthProviderOptions = {
  email?: { enabled?: boolean } | null;
  google?: { enabled?: boolean } | null;
  invite_required?: boolean;
};

const EXTERNAL_AUTH_PENDING_STORAGE_KEY = 'empyralis.external-auth.pending';
const EXTERNAL_AUTH_COMPLETION_STORAGE_KEY = 'empyralis.external-auth.complete';

// A native-app handoff request (?native=ios&code_challenge=...&state=...)
// survives on /login for the whole email/password flow -- it's the same
// page, the query string never moves. It does NOT survive the Google flow:
// googleLogin() below full-page-navigates AWAY from /login, through
// accounts.google.com, and the round trip lands on /auth/complete, a
// DIFFERENT route, carrying only what /api/auth/google/callback chose to
// put on that URL (see that route's own comment -- it does not know about
// native handoffs and this file deliberately does not teach it to). So this
// key is how /login hands the request to /auth/complete across that gap:
// sessionStorage is tab-scoped and origin-scoped, but genuinely survives a
// same-tab navigation through a third-party origin and back, which is
// exactly the shape of an OAuth redirect. See native-login-handoff.ts's own
// header for why this matters at all (a real ASWebAuthenticationSession
// Google flow never returns to /login in the tab that started it).
const NATIVE_LOGIN_HANDOFF_STORAGE_KEY = 'empyralis.native-login-handoff.pending';

function hasWindowStorage(): boolean {
  return typeof window !== 'undefined' && typeof window.localStorage !== 'undefined';
}

function hasWindowSessionStorage(): boolean {
  return typeof window !== 'undefined' && typeof window.sessionStorage !== 'undefined';
}

function parseExternalAuthProvider(value: unknown): ExternalAuthProvider | null {
  if (value === 'google') {
    return value;
  }
  return null;
}

function readStoredJson<T>(storageKey: string): T | null {
  if (!hasWindowStorage()) {
    return null;
  }
  try {
    const raw = window.localStorage.getItem(storageKey);
    if (!raw) {
      return null;
    }
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}

function writeStoredJson<T>(storageKey: string, value: T): void {
  if (!hasWindowStorage()) {
    return;
  }
  try {
    window.localStorage.setItem(storageKey, JSON.stringify(value));
  } catch {
    // ignore local storage errors in auth helpers
  }
}

export function getPendingExternalAuthProvider(): ExternalAuthProvider | null {
  const pending = readStoredJson<ExternalAuthPendingRecord>(EXTERNAL_AUTH_PENDING_STORAGE_KEY);
  return parseExternalAuthProvider(pending?.provider);
}

export function markExternalAuthPending(provider: ExternalAuthProvider): void {
  writeStoredJson<ExternalAuthPendingRecord>(EXTERNAL_AUTH_PENDING_STORAGE_KEY, {
    provider,
    startedAt: Date.now(),
  });
}

export function clearExternalAuthPending(): void {
  if (!hasWindowStorage()) {
    return;
  }
  try {
    window.localStorage.removeItem(EXTERNAL_AUTH_PENDING_STORAGE_KEY);
  } catch {
    // ignore local storage errors in auth helpers
  }
}

export function announceExternalAuthCompletion(provider: ExternalAuthProvider): void {
  writeStoredJson<ExternalAuthCompletionRecord>(EXTERNAL_AUTH_COMPLETION_STORAGE_KEY, {
    provider,
    completedAt: Date.now(),
  });
}

export function watchExternalAuthCompletion(onReady: () => void): () => void {
  if (typeof window === 'undefined') {
    return () => {};
  }

  const handleStorage = (event: StorageEvent) => {
    if (event.key === EXTERNAL_AUTH_COMPLETION_STORAGE_KEY) {
      onReady();
    }
  };
  const handleFocus = () => {
    onReady();
  };
  const handleVisibilityChange = () => {
    if (document.visibilityState === 'visible') {
      onReady();
    }
  };

  window.addEventListener('storage', handleStorage);
  window.addEventListener('focus', handleFocus);
  document.addEventListener('visibilitychange', handleVisibilityChange);

  return () => {
    window.removeEventListener('storage', handleStorage);
    window.removeEventListener('focus', handleFocus);
    document.removeEventListener('visibilitychange', handleVisibilityChange);
  };
}

export type PendingNativeLoginHandoff = {
  native: string;
  codeChallenge: string;
  state: string;
};

/** Deliberately no validation here -- this is raw storage plumbing. Whether
 * a record is well-formed is native-login-handoff.ts's own question
 * (planNativeLoginHandoffFromRecord), asked by whoever reads it back. */
export function markPendingNativeLoginHandoff(record: PendingNativeLoginHandoff): void {
  if (!hasWindowSessionStorage()) {
    return;
  }
  try {
    window.sessionStorage.setItem(NATIVE_LOGIN_HANDOFF_STORAGE_KEY, JSON.stringify(record));
  } catch {
    // Best-effort: a lost record just means /auth/complete falls back to an
    // ordinary web landing instead of handing off to the app -- the person
    // is still signed in, never worse than that.
  }
}

/** Read-and-clear, single-use like the code it stands in for -- a stale
 * record left behind after one handoff must never be replayed against a
 * later, unrelated sign-in in the same tab. */
export function consumePendingNativeLoginHandoff(): unknown {
  if (!hasWindowSessionStorage()) {
    return null;
  }
  try {
    const raw = window.sessionStorage.getItem(NATIVE_LOGIN_HANDOFF_STORAGE_KEY);
    window.sessionStorage.removeItem(NATIVE_LOGIN_HANDOFF_STORAGE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function channelAttributionToken(): string | undefined {
  if (typeof window === 'undefined') {
    return undefined;
  }
  const token = new URLSearchParams(window.location.search).get('channel_attribution');
  const normalized = String(token || '').trim();
  return normalized || undefined;
}

async function parseJson(response: Response): Promise<unknown> {
  if (response.status === 204) {
    return null;
  }
  const contentType = response.headers.get('content-type') || '';
  if (!contentType.includes('application/json')) {
    return null;
  }
  return response.json();
}

function authFailureMessage(status: number, detail: string): string {
  const normalized = detail.toLowerCase();
  if (normalized.includes('control plane is unavailable') || normalized.includes('fetch failed')) {
    return detail || 'The Empyralis control plane is unavailable.';
  }
  if (status >= 500 || /internal server|bad gateway|service unavailable|gateway timeout/.test(normalized)) {
    return 'The auth service is warming up or temporarily unavailable. Try again in a moment.';
  }
  if (status === 429) {
    return 'Too many auth attempts. Wait a minute, then try again.';
  }
  if (status === 401) {
    return detail || 'Email or password was not accepted.';
  }
  if (status === 403) {
    return detail || 'This account is not allowed to open this workspace.';
  }
  if (status === 404) {
    return 'The auth route is not available in this environment.';
  }
  return detail || `Authentication request failed with status ${status}.`;
}

/**
 * Thrown only when an auth request never produced a RESPONSE — the fetch
 * itself rejected: offline, a dropped connection, or our own 30s
 * AbortController firing. That is a genuinely different fact from the
 * server answering with a non-2xx status.
 *
 * MAN-343, second half. The first half (a hiccup in the post-signup
 * readiness poll being reported as signup failure) was fixed by splitting
 * that poll into its own try/catch — but `signup()` ITSELF can still fail
 * this way AFTER the server has already created the account, committed the
 * workspace and sent the verification mail, with only the response lost on
 * the way back. Every such failure reached the same generic catch and
 * rendered "Couldn't create the account", which is the product's own
 * outcome-honesty law being broken in the worst direction: reporting
 * failure ON SUCCESS, on the very first screen a customer touches.
 *
 * A non-ok RESPONSE stays a plain Error on purpose — that is the server's
 * own definitive answer that nothing was created, and it must keep
 * rendering as a real failure.
 *
 * Deliberately mirrors frontend/lib/workspace/mutation-outcome.ts's
 * `MutateNetworkError` rather than importing it: that one is the fleet
 * mutation layer's primitive (thrown by members-data.ts, cloud-vps-setup-
 * panel.tsx, ...), and the auth client is a separate, dependency-free
 * module that must not start reaching into workspace code. The PATTERN is
 * shared; see that file's doc comment for the full argument, including
 * why a caller must re-check real state before ever reporting failure.
 */
export class AuthNetworkError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'AuthNetworkError';
  }
}

async function requestAuth<T>(path: string, options: AuthRequestOptions): Promise<T> {
  const controller = new AbortController();
  const timeoutHandle = window.setTimeout(() => {
    controller.abort();
  }, AUTH_REQUEST_TIMEOUT_MS);
  let response: Response;
  try {
    response = await fetch(path, {
      method: options.method,
      credentials: 'include',
      signal: controller.signal,
      headers: buildCookieAuthHeaders(options.method, {
        accept: 'application/json',
        ...(options.body ? { 'content-type': 'application/json' } : {}),
      }),
      body: options.body ? JSON.stringify(options.body) : undefined,
    });
  } catch (error) {
    // No response ever arrived — the outcome is UNKNOWN, not failed.
    if (error instanceof Error && error.name === 'AbortError') {
      throw new AuthNetworkError(AUTH_TIMEOUT_MESSAGE);
    }
    throw new AuthNetworkError(error instanceof Error ? error.message : 'Network request failed.');
  } finally {
    window.clearTimeout(timeoutHandle);
  }

  const payload = await parseJson(response);
  if (!response.ok) {
    const detail =
      payload && typeof payload === 'object' && !Array.isArray(payload)
        ? String((payload as Record<string, unknown>).detail || '').trim()
        : '';
    throw new Error(authFailureMessage(response.status, detail));
  }
  return payload as T;
}

function sleep(delayMs: number): Promise<void> {
  return new Promise((resolve) => {
    window.setTimeout(resolve, delayMs);
  });
}

// Doubles per attempt off the caller's base delay, half-jittered, capped —
// avoids every retrying caller landing on the backend at the same instant.
function backoffWithJitter(baseDelayMs: number, attempt: number): number {
  const exponential = Math.min(baseDelayMs * 2 ** attempt, 8_000);
  return exponential / 2 + Math.random() * (exponential / 2);
}

// Callers (OAuth-completion pollers on a ~500ms tick, signup/login redirects)
// can invoke this many times a second while a session is settling. Without
// single-flight de-dup, each tick fired its own fetch — observed as ~80
// hits/40s against account-shell on one expired session. Keyed by path so
// concurrent callers checking the same endpoint share one in-flight sequence.
const browserAuthReadyInFlight = new Map<string, Promise<void>>();

export async function awaitBrowserAuthReady({
  path = '/api/auth/account-shell',
  attempts = 4,
  delayMs = 250,
}: AwaitBrowserAuthReadyOptions = {}): Promise<void> {
  const existing = browserAuthReadyInFlight.get(path);
  if (existing) {
    return existing;
  }

  const run = (async () => {
    let lastStatus: number | null = null;
    let consecutive401s = 0;

    for (let index = 0; index < attempts; index += 1) {
      const response = await fetch(path, {
        method: 'GET',
        credentials: 'include',
        cache: 'no-store',
        headers: buildCookieAuthHeaders('GET', {
          accept: 'application/json',
        }),
      });

      if (response.ok) {
        return;
      }

      lastStatus = response.status;

      if (response.status === 401) {
        consecutive401s += 1;
        // A just-created session can 401 once or twice while the cookie
        // propagates — that's expected right after login/signup. A 401
        // that persists past a few tries means the session is actually
        // gone, not warming up: stop instead of burning the rest of the
        // attempt budget (callers pass up to 12) hammering a dead session.
        if (consecutive401s >= 3) {
          throw new Error('Your session expired. Sign in again.');
        }
        await sleep(backoffWithJitter(delayMs, index));
        continue;
      }

      if (response.status >= 500) {
        consecutive401s = 0;
        await sleep(backoffWithJitter(delayMs, index));
        continue;
      }

      if (response.status === 403) {
        throw new Error('This workspace is not accessible for this account.');
      }

      throw new Error(`Auth readiness check failed with status ${response.status}.`);
    }

    throw new Error(
      lastStatus === null
        ? 'Auth readiness check did not complete.'
        : `Auth readiness check did not recover from status ${lastStatus}.`,
    );
  })();

  browserAuthReadyInFlight.set(path, run);
  try {
    return await run;
  } finally {
    browserAuthReadyInFlight.delete(path);
  }
}

export async function login(email: string, password: string): Promise<Record<string, unknown> | null> {
  return requestAuth<Record<string, unknown> | null>('/api/auth/login', {
    method: 'POST',
    body: {
      email,
      password,
      channel: 'web',
      acquisition_token: channelAttributionToken(),
    },
  });
}

export async function signup(
  email: string,
  password: string,
  name?: string,
  pilotInviteCode?: string,
  inviteCode?: string,
): Promise<Record<string, unknown> | null> {
  const cleanPilotInviteCode = String(pilotInviteCode || '').trim();
  const cleanInviteCode = String(inviteCode || '').trim();
  return requestAuth<Record<string, unknown> | null>('/api/auth/signup', {
    method: 'POST',
    body: {
      email,
      password,
      name,
      channel: 'web',
      acquisition_token: channelAttributionToken(),
      ...(cleanPilotInviteCode ? { pilot_invite_code: cleanPilotInviteCode } : {}),
      ...(cleanInviteCode ? { invite_code: cleanInviteCode } : {}),
    },
  });
}

export async function logout(): Promise<Record<string, unknown> | null> {
  return requestAuth<Record<string, unknown> | null>('/api/auth/logout', {
    method: 'POST',
  });
}

export type EmailVerificationStatus = {
  ok?: boolean;
  status?: 'verified' | 'pending' | 'none';
  email_verified?: boolean;
};

export async function getEmailVerificationStatus(): Promise<EmailVerificationStatus | null> {
  return requestAuth<EmailVerificationStatus | null>('/api/auth/verify-email', {
    method: 'GET',
  });
}

export async function verifyEmailCode(code: string): Promise<Record<string, unknown> | null> {
  return requestAuth<Record<string, unknown> | null>('/api/auth/verify-email', {
    method: 'POST',
    body: { code: String(code || '').trim() },
  });
}

export async function resendVerificationEmail(): Promise<Record<string, unknown> | null> {
  return requestAuth<Record<string, unknown> | null>('/api/auth/verify-email/resend', {
    method: 'POST',
  });
}

export function googleLogin(nativeHandoff?: PendingNativeLoginHandoff): void {
  markExternalAuthPending('google');
  if (nativeHandoff) {
    // Stashed here, not appended to the /api/auth/google URL: that route's
    // own query string already overloads `state` as a channel_attribution
    // fallback (a pre-existing convention, unrelated to this feature), so
    // reusing `state` for our own PKCE state would collide with it. The
    // sessionStorage relay (see NATIVE_LOGIN_HANDOFF_STORAGE_KEY's own
    // comment above) sidesteps that without touching the OAuth route at all.
    markPendingNativeLoginHandoff(nativeHandoff);
  }
  const params = new URLSearchParams();
  const attribution = channelAttributionToken();
  if (attribution) {
    params.set('channel_attribution', attribution);
  }
  const query = params.size > 0 ? `?${params.toString()}` : '';
  window.location.href = `/api/auth/google${query}`;
}

export async function me(): Promise<Record<string, unknown> | null> {
  return requestAuth<Record<string, unknown> | null>('/api/auth/me', {
    method: 'GET',
  });
}

export async function listAuthProviders(): Promise<AuthProviderOptions> {
  return requestAuth<AuthProviderOptions>('/api/auth/providers', {
    method: 'GET',
  });
}

// Refresh happens from two independent triggers — SessionRefreshTimer's
// proactive 20-minute tick, and WorkspaceTransportAdapter's reactive
// per-request 401 handler (workspace-services.tsx's refreshBrowserSession,
// which calls this same function rather than maintaining its own duplicate
// POST) — and it's normal for a browser tab to have several requests in
// flight that all 401 at once the moment the access token expires. The
// backend's refresh token is single-use (rotated in place per session row
// on every successful call — see auth.py's
// _upsert_auth_session_refresh_token_locked), so two concurrent refresh
// calls race: the loser's cookie is already stale by the time its request
// lands, and — before this — the backend's failure response cleared EVERY
// auth cookie, including the ones the winner had just set moments earlier.
// That is what "every in-flight request then 401s simultaneously, with no
// warning" traced back to. Single-flighting here (same pattern
// awaitBrowserAuthReady already uses just above, for the identical reason)
// means only ONE refresh ever crosses the wire at a time per tab; every
// concurrent caller awaits and shares that one result instead of racing a
// second one. (The backend also stopped treating a losing race as a dead
// credential — see RefreshTokenSupersededError in auth.py — for the
// narrower case of two separate tabs racing, which this alone can't cover.)
let refreshInFlight: Promise<Record<string, unknown> | null> | null = null;

export async function refresh(): Promise<Record<string, unknown> | null> {
  if (refreshInFlight) return refreshInFlight;
  const run = requestAuth<Record<string, unknown> | null>('/api/auth/refresh', {
    method: 'POST',
    body: { channel: 'web' },
  });
  refreshInFlight = run;
  try {
    return await run;
  } finally {
    if (refreshInFlight === run) refreshInFlight = null;
  }
}

export type NativeAuthHandoffMintResult = {
  code: string;
  redirect_uri: string;
  expires_in: number;
};

/**
 * Asks for a single-use handoff code, authenticated by THIS browser
 * session's own cookies (credentials: 'include', via requestAuth) -- never
 * anything the caller passes in. See server_modules/native_auth_service.py
 * for the full design: the code is worthless without the code_verifier only
 * the native app holds, and the redirect target it resolves to is decided
 * entirely server-side.
 */
export async function mintNativeAuthHandoff(params: {
  native: string;
  codeChallenge: string;
  state: string;
}): Promise<NativeAuthHandoffMintResult | null> {
  return requestAuth<NativeAuthHandoffMintResult | null>('/api/auth/native/handoff', {
    method: 'POST',
    body: {
      native: params.native,
      code_challenge: params.codeChallenge,
      code_challenge_method: 'S256',
      state: params.state,
    },
  });
}

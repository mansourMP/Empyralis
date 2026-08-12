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

function hasWindowStorage(): boolean {
  return typeof window !== 'undefined' && typeof window.localStorage !== 'undefined';
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
    if (error instanceof Error && error.name === 'AbortError') {
      throw new Error(AUTH_TIMEOUT_MESSAGE);
    }
    throw error;
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

export function googleLogin(): void {
  markExternalAuthPending('google');
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

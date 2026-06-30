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

export async function awaitBrowserAuthReady({
  path = '/api/auth/account-shell',
  attempts = 4,
  delayMs = 250,
}: AwaitBrowserAuthReadyOptions = {}): Promise<void> {
  let lastStatus: number | null = null;

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
    if (response.status === 401 || response.status >= 500) {
      await sleep(delayMs);
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
): Promise<Record<string, unknown> | null> {
  const cleanPilotInviteCode = String(pilotInviteCode || '').trim();
  return requestAuth<Record<string, unknown> | null>('/api/auth/signup', {
    method: 'POST',
    body: {
      email,
      password,
      name,
      channel: 'web',
      acquisition_token: channelAttributionToken(),
      ...(cleanPilotInviteCode ? { pilot_invite_code: cleanPilotInviteCode } : {}),
    },
  });
}

export async function logout(): Promise<Record<string, unknown> | null> {
  return requestAuth<Record<string, unknown> | null>('/api/auth/logout', {
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

export async function refresh(): Promise<Record<string, unknown> | null> {
  return requestAuth<Record<string, unknown> | null>('/api/auth/refresh', {
    method: 'POST',
    body: { channel: 'web' },
  });
}

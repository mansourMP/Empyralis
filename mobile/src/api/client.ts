import { API_BASE_URL } from './config';

/**
 * The one place an HTTP call is made.
 *
 * FOUR OUTCOMES, NEVER THREE. "the server said no", "I could not reach the
 * server", "I reached it and it answered nonsense" and "you are signed out"
 * are different facts and callers branch on them differently — a screen that
 * collapses them tells someone to check their connection when their password
 * was wrong (a real bug this product has shipped and fixed twice, per
 * CLAUDE.md's outcome-honesty law).
 */
export type ApiFailure =
  | { kind: 'http'; status: number; message: string }
  | { kind: 'network'; message: string }
  | { kind: 'decode'; message: string }
  | { kind: 'unauthorized'; message: string };

export class ApiError extends Error {
  readonly failure: ApiFailure;
  constructor(failure: ApiFailure) {
    super(failure.message);
    this.failure = failure;
  }
}

/**
 * A backend error body's `detail`/`error` is NOT always a string — FastAPI's
 * own validation shape puts an OBJECT under `error`, and `String(thatObject)`
 * is the literal text "[object Object]", which this product has rendered to
 * customers before (CLAUDE.md records the fix on the web side). So a
 * non-string is treated as absent and the caller's fallback wins.
 */
export function messageFromBody(body: unknown, fallback: string): string {
  if (!body || typeof body !== 'object') return fallback;
  const record = body as Record<string, unknown>;
  if (typeof record.detail === 'string' && record.detail.trim()) return record.detail;
  if (typeof record.error === 'string' && record.error.trim()) return record.error;
  return fallback;
}

export type RequestOptions = {
  method?: 'GET' | 'POST' | 'PATCH' | 'DELETE';
  body?: unknown;
  token?: string | null;
  /** Callers that must read a 401 body (the native exchange reports its own
   *  misconfiguration that way) opt out of the unauthorized mapping. */
  rawUnauthorized?: boolean;
};

export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, token, rawUnauthorized = false } = options;

  const headers: Record<string, string> = { Accept: 'application/json' };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (token) headers.Authorization = `Bearer ${token}`;

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (error) {
    // fetch only rejects when the request never completed. That is a
    // genuinely different fact from any status code and must stay separable.
    throw new ApiError({
      kind: 'network',
      message: error instanceof Error && error.message ? error.message : 'Could not reach Empyralis.',
    });
  }

  const text = await response.text();
  let parsed: unknown = null;
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      if (response.ok) {
        throw new ApiError({ kind: 'decode', message: 'The server sent something unreadable.' });
      }
    }
  }

  if (!response.ok) {
    if (response.status === 401 && !rawUnauthorized) {
      throw new ApiError({ kind: 'unauthorized', message: 'Your session has expired.' });
    }
    throw new ApiError({
      kind: 'http',
      status: response.status,
      message: messageFromBody(parsed, `The server answered ${response.status}.`),
    });
  }

  // Several fleet routes answer HTTP 200 with {ok:false,error} on a
  // service-level failure. An empty list alone would read as "nothing here"
  // when it actually means "couldn't ask" — the exact empty-vs-error
  // collapse this codebase treats as a bug, so it is raised, not returned.
  if (parsed && typeof parsed === 'object' && (parsed as Record<string, unknown>).ok === false) {
    throw new ApiError({
      kind: 'http',
      status: response.status,
      message: messageFromBody(parsed, 'The server could not complete that.'),
    });
  }

  return parsed as T;
}

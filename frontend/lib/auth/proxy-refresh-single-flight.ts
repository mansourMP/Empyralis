/**
 * MAN-324's fix single-flighted the BROWSER's own callers of
 * `/api/auth/refresh` (SessionRefreshTimer's proactive tick,
 * WorkspaceTransportAdapter's reactive per-401 handler, fleetAuthorizedFetch)
 * through auth-client.ts's module-scoped `refreshInFlight` — but that lock
 * lives in the browser's JS heap. `proxy.ts` makes its OWN, completely
 * separate call to the backend's `/api/v1/auth/refresh` (proactively, on
 * ordinary page/RSC GET navigations whose access-token cookie is within
 * `AUTH_REFRESH_GRACE_SECONDS` of expiry) from the Next.js SERVER process —
 * a third, uncoordinated caller of the same single-use-rotation endpoint
 * that MAN-324's fix never touched, because it runs in a different
 * execution context than the browser code that fix single-flighted.
 *
 * Concurrent qualifying GET requests are routine, not contrived: Next.js
 * fires an RSC fetch per client-side navigation, `<Link>` prefetches ahead
 * of a click, and several independently-polling panels each call
 * `router.refresh()` on their own cadence — all of these are ordinary GETs
 * that reach `proxy.ts`. When several land in the same window and the
 * access-token cookie is near expiry, each one independently decides "this
 * needs a refresh" and independently POSTs to the backend carrying the
 * SAME pre-rotation refresh_token cookie (none of them has seen any other's
 * rotation yet) — racing the backend's single-use refresh-token rotation
 * exactly like the bug MAN-324 fixed, one layer up. Reproduced live against
 * a disposable stack: a burst of 5 concurrent qualifying GETs produced 1
 * winning refresh (200) and 4 losing ones (401 "Refresh token was already
 * used by a concurrent request" — auth.py's RefreshTokenSupersededError),
 * and under sustained concurrent navigation the resulting call volume can
 * trip the backend's own `limit_refresh_requests` rate limiter, producing
 * repeated 429s that starve the refresh mechanism for a real stretch of
 * wall-clock time — captured directly: >60s of relapsing
 * 401→(401/429)→401 cycles against real fleet-adjacent endpoints
 * (`/api/v1/auth/account-shell`, `/api/workspaces/.../bootstrap`), the same
 * "every fleet call 401s simultaneously, refresh doesn't recover it" shape
 * reported live in a real browser.
 *
 * Single-flighted here, KEYED BY THE INCOMING refresh_token COOKIE VALUE —
 * never a single global lock, which would serialize every concurrent
 * refresh across every user's session sharing this Next.js process (a
 * correctness non-issue, but an unnecessary latency tax on unrelated
 * sessions). Concurrent proxy invocations carrying the same stale token
 * collapse into ONE upstream call; every one of them relays the SAME
 * winning Set-Cookie pair back to its own browser response, so a burst of
 * N concurrent navigations now costs the backend exactly one refresh call
 * instead of N — which is also what stops the rate-limit storm from
 * starting in the first place.
 *
 * This coordinates every request landing on ONE Next.js server process —
 * production today is a single VPS (docs/DEPLOY-RUNBOOK.md), so this closes
 * the race that actually occurs. It does not, and structurally cannot,
 * coordinate across multiple Next.js server replicas (a future horizontally
 * scaled deployment) or against the browser's OWN single-flighted refresh —
 * those remaining races degrade safely today via RefreshTokenSupersededError
 * (a soft 401 that never clears cookies) plus the browser's own reactive
 * retry-on-401, exactly the backstop MAN-324 already built.
 */

export type ProxyRefreshRunResult<T> = T;

const inFlightByRefreshToken = new Map<string, Promise<unknown>>();

/**
 * Runs `run()` at most once per distinct `refreshToken` value at any given
 * moment — a second call with the same token while the first is still
 * pending returns the SAME promise instead of issuing a second upstream
 * request. Distinct token values (different sessions, or the same session
 * after a rotation has completed) are never coalesced together.
 */
export async function singleFlightedProxyRefresh<T>(
  refreshToken: string,
  run: () => Promise<T>,
): Promise<T> {
  const existing = inFlightByRefreshToken.get(refreshToken);
  if (existing) {
    return existing as Promise<T>;
  }
  const attempt = run();
  inFlightByRefreshToken.set(refreshToken, attempt);
  try {
    return await attempt;
  } finally {
    if (inFlightByRefreshToken.get(refreshToken) === attempt) {
      inFlightByRefreshToken.delete(refreshToken);
    }
  }
}

/** Test-only: drops all in-flight entries so tests don't leak state into each other. */
export function _resetProxyRefreshSingleFlightForTests(): void {
  inFlightByRefreshToken.clear();
}

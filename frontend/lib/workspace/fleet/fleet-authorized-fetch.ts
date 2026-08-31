"use client";

import { refresh as refreshBrowserAuthSession } from "@/lib/auth/auth-client";
import { AUTH_CSRF_HEADER_NAME, readCsrfTokenFromCookie } from "@/lib/auth/csrf";

/**
 * fetch() that survives an access-token expiry mid-flow instead of just
 * 401ing. The create-agent wizard's Placement/Brain steps make several
 * authenticated calls in sequence (create, patch, credential, provider
 * profile) with no autosave — a 401 partway through used to just fail that
 * one call and leave whatever the owner had done unsaved, with nothing
 * attempted to recover it: "every in-flight request then 401s
 * simultaneously with no warning, losing wizard progress."
 *
 * On a 401, this refreshes the session ONCE — via auth-client's `refresh()`,
 * which is single-flighted across every caller in the app (see its own doc
 * comment for why that matters: the actual root cause here was two
 * concurrent, un-deduplicated refresh calls racing the backend's single-use
 * refresh token, and the loser's failure response used to clear the
 * winner's just-set cookies too) — and retries the original request exactly
 * once. A second 401 after a successful refresh, or a failed refresh, means
 * the session is genuinely gone; the caller's own existing error handling
 * takes it from there (an honest "HTTP 401"/message via
 * `@/lib/ui/api-error`'s getErrorMessage, not a silent stall).
 */
function withFreshCsrfHeader(init: RequestInit | undefined): RequestInit | undefined {
  if (!init?.headers) {
    return init;
  }
  const headers = new Headers(init.headers);
  // Only touch it if the caller already opted this request into CSRF
  // protection (buildCookieAuthHeaders sets this for every mutating
  // method) — never invent the header for a caller that deliberately left
  // it off (e.g. a GET).
  if (!headers.has(AUTH_CSRF_HEADER_NAME)) {
    return init;
  }
  // A successful refresh ALWAYS rotates the CSRF cookie server-side —
  // set_auth_cookies()/issue_csrf_token() in auth.py mints a brand new
  // random token on every single call, unconditionally, whether or not the
  // old one was still valid. The header above was built once, before the
  // refresh, from the cookie as it stood then; the retry's Cookie header is
  // attached fresh by the browser from whatever is in the jar right now —
  // the just-rotated value. Reusing the stale header verbatim guaranteed a
  // csrf_mismatch on EVERY 401-then-refresh retry of a mutating request,
  // not just a rare race: this is what actually produced the "403 CSRF
  // validation failed" a brand-new Google signup hit on the onboarding
  // PATCH, immediately after its first attempt 401'd while the just-created
  // session was still propagating (see awaitBrowserAuthReady's own comment
  // on that being expected right after signup).
  const freshToken = readCsrfTokenFromCookie();
  if (freshToken) {
    headers.set(AUTH_CSRF_HEADER_NAME, freshToken);
  } else {
    headers.delete(AUTH_CSRF_HEADER_NAME);
  }
  return { ...init, headers };
}

export async function fleetAuthorizedFetch(
  input: RequestInfo | URL,
  init?: RequestInit,
): Promise<Response> {
  const first = await fetch(input, init);
  if (first.status !== 401) return first;
  try {
    await refreshBrowserAuthSession();
  } catch {
    return first;
  }
  return fetch(input, withFreshCsrfHeader(init));
}

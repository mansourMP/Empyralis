"use client";

import { refresh as refreshBrowserAuthSession } from "@/lib/auth/auth-client";

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
  return fetch(input, init);
}

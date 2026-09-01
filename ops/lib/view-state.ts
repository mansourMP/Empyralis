/**
 * The one place a raw fetch outcome becomes a render state, for every page
 * in this app. Mirrors frontend/lib/workspace/fleet/platform-activation.ts's
 * `planPlatformActivationView` — the same discipline, made generic over the
 * payload type so every operator-console view (overview, accounts, account
 * detail, funnel, retention, failures, spend) shares one rule instead of
 * seven copies that can each independently drift.
 *
 * "Empty" and "could not load" are different facts (CLAUDE.md), and that
 * matters more here than anywhere: this app's whole point is telling the
 * founder the truth about the platform. A failed fetch must never render as
 * "0 users" or an empty table. So `data` only ever reaches `ready` once a
 * real response body arrived — a genuinely empty result (e.g. zero accounts
 * matching a filter) is still `ready` data with an empty array inside it;
 * that is a page-level rendering choice (see accounts.ts's own handling),
 * never something decided here.
 *
 * `forbidden` is its own state, not folded into `error`: every route in
 * routes_operator_console.py calls `has_platform_fleet_operator_access`
 * before running a query, and require_api_key alone only proves "is someone
 * logged in" (CLAUDE.md). A signed-in non-operator needs to be told THAT,
 * not shown a generic "could not load" message that reads as a bug in the
 * console rather than a fact about who they are.
 *
 * `signedOut` (401) is separate again, and for the same reason one step
 * further on. 401 and 403 are DIFFERENT FACTS: 401 is "nobody is signed in
 * here", 403 is "you are signed in and you are not an operator". Collapsing
 * them told a signed-out operator that their account lacked entitlement --
 * a false statement about their permissions, which sent the founder hunting
 * for an access problem that did not exist (2026-09-02, on an account that
 * IS in ORION_ADMIN_EMAILS). Only the 401 branch can offer a sign-in link,
 * because only it is fixable by signing in.
 *
 * Run: npx tsx lib/view-state.test.ts
 */

export type OperatorViewState<T> =
  | { kind: "loading" }
  | { kind: "signedOut" }
  | { kind: "forbidden" }
  | { kind: "error"; message: string }
  | { kind: "ready"; data: T };

export function planOperatorView<T>(input: {
  loading: boolean;
  status: number | null;
  error: string | null;
  data: T | null;
}): OperatorViewState<T> {
  if (input.loading) return { kind: "loading" };
  if (input.status === 401) return { kind: "signedOut" };
  if (input.status === 403) return { kind: "forbidden" };
  if (input.error) return { kind: "error", message: input.error };
  if (input.data === null || input.data === undefined) {
    return { kind: "error", message: "The server did not return any data." };
  }
  return { kind: "ready", data: input.data };
}

/** A backend error body's `detail` is not always a string (FastAPI's own
 *  validation-error shape is an array of objects) — coercing that straight
 *  into an Error would render the literal text "[object Object]" to an
 *  operator instead of saying nothing useful. Deliberately a small,
 *  standalone copy of frontend/lib/ui/api-error.ts's `getErrorMessage`
 *  (same ~10 lines, same rule) rather than an import — see this app's
 *  next.config.ts header for why this app never reaches across the repo
 *  boundary into frontend/. */
export function getErrorMessage(data: unknown, fallback: string): string {
  if (data && typeof data === "object" && !Array.isArray(data)) {
    const record = data as Record<string, unknown>;
    if (typeof record.detail === "string" && record.detail.trim()) return record.detail;
    if (typeof record.error === "string" && record.error.trim()) return record.error;
  }
  return fallback;
}

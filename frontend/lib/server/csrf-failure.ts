import { createHash } from 'node:crypto';

// Deliberately framework-agnostic (no `import 'server-only'`, no
// `next/server`) so this classification logic is unit-testable under plain
// `npx tsx` without a bundler. control-plane-proxy.ts is the only caller in
// production; it stays 'server-only' itself and does the actual
// NextRequest/NextResponse plumbing around this. `node:crypto` is a Node
// built-in (not the `server-only` package), so it is safe to import
// directly here -- it never touches a client bundle because this file's
// only production caller already sits behind `import 'server-only'`, and it
// runs fine under plain Node via `npx tsx` too.
//
// Three genuinely different facts used to share one signal in
// validateBrowserCsrf(): the CSRF cookie missing, the header missing, or the
// two disagreeing. Folded into one identical 403 ({ detail: 'CSRF
// validation failed.' }), nobody could tell which of the three had actually
// happened — the direct cause of a brand-new Google signup's PATCH
// /api/workspaces/{id} 403ing in production with zero way to diagnose it.
// The customer-facing message stays generic; the `code` and the log line
// below are what let an operator actually tell them apart (matched on the
// code, never on prose — CLAUDE.md's own rule). Cookie NAMES only, in both
// the code and the log line — these are session credentials and a value
// must never be logged, only which cookies were present.
export type CsrfFailureCode = 'csrf_cookie_missing' | 'csrf_header_missing' | 'csrf_mismatch';

export function classifyCsrfFailure(csrfCookie: string, csrfHeader: string): CsrfFailureCode | null {
  if (!csrfCookie) {
    return 'csrf_cookie_missing';
  }
  if (!csrfHeader) {
    return 'csrf_header_missing';
  }
  if (csrfCookie !== csrfHeader) {
    return 'csrf_mismatch';
  }
  return null;
}

// A production csrf_mismatch kept firing even through the one-shot retry
// (fleet-authorized-fetch.ts / workspace-services.tsx's csrf_mismatch
// retry) -- two identical log entries in the same second, meaning the retry
// DID fire and mismatched again. That rules out an ordinary timing race (a
// race resolves on retry); the cookie and header values disagree stably.
// The log above only ever named cookie NAMES, never enough to tell "the
// values are genuinely different" from "they're the same value mangled by
// whitespace or encoding" from "one is truncated" -- three different causes
// that would all need a different fix. A one-way SHA-256 prefix plus a
// length is exactly enough to distinguish those without ever logging a
// session credential: two equal prefixes at equal lengths mean the values
// really are equal (a raw-value leak this file must never risk), unequal
// prefixes mean genuinely different tokens, and a length/whitespace
// mismatch at equal prefixes points at trimming rather than rotation.
export type CsrfValueFingerprint = {
  /** Raw (untrimmed) value length -- classifyCsrfFailure() itself compares
   * pre-trimmed values, so a length here that differs from the trimmed
   * comparison length is exactly the leading/trailing-whitespace signal. */
  length: number;
  /** First 8 hex chars of sha256(rawValue). Never enough to invert back to
   * the token; two matching prefixes are strong evidence of equal values,
   * two differing prefixes are conclusive evidence of different values. */
  sha256Prefix: string;
  /** True when the raw value has leading or trailing whitespace -- a
   * mismatch classifyCsrfFailure() cannot see because both sides are
   * trimmed before it ever runs the comparison. */
  hasEdgeWhitespace: boolean;
};

export function fingerprintCsrfValue(rawValue: string): CsrfValueFingerprint {
  const value = String(rawValue || '');
  return {
    length: value.length,
    sha256Prefix: value ? createHash('sha256').update(value, 'utf8').digest('hex').slice(0, 8) : '',
    hasEdgeWhitespace: value.length > 0 && value.trim() !== value,
  };
}

/** Counts how many `; `-delimited entries in a raw `Cookie` request header
 * name exactly `cookieName`. A stable (non-race, non-converging) CSRF
 * mismatch is exactly what a duplicate cookie under the same name produces:
 * the browser can hold two `empyralis_csrf_token` cookies at once when they
 * differ in Domain or Path (same name + same domain + same path always
 * overwrite -- RFC 6265 -- but any difference in domain or path creates a
 * SECOND cookie). `document.cookie` and the `Cookie` request header both
 * order same-path cookies oldest-first; readCsrfTokenFromCookie
 * (lib/auth/csrf.ts) returns the FIRST match, while NextRequest's cookie
 * jar (@edge-runtime/cookies, confirmed empirically) keeps the LAST match
 * when two entries share a name -- so the browser's JS and this server read
 * opposite ends of the same duplicate pair, and no retry converges them. */
export function countRawCookieOccurrences(rawCookieHeader: string, cookieName: string): number {
  const prefix = `${cookieName}=`;
  let count = 0;
  for (const chunk of String(rawCookieHeader || '').split(';')) {
    if (chunk.trim().startsWith(prefix)) {
      count += 1;
    }
  }
  return count;
}

export function logCsrfFailure(
  failureCode: CsrfFailureCode,
  context: {
    path: string;
    method: string;
    cookieNames: string[];
    /** Raw (untrimmed) cookie value -- used ONLY to compute the safe
     * fingerprint below; never itself included in the logged object. */
    csrfCookieValue: string;
    /** Raw (untrimmed) header value -- same rule as above. */
    csrfHeaderValue: string;
    duplicateCsrfCookieCount: number;
  },
): void {
  // eslint-disable-next-line no-console -- deliberate operator-facing
  // diagnostic; see this file's header comment.
  console.error(`[control-plane-proxy] CSRF validation failed: ${failureCode}`, {
    path: context.path,
    method: context.method,
    cookieNames: context.cookieNames,
    cookie: fingerprintCsrfValue(context.csrfCookieValue),
    header: fingerprintCsrfValue(context.csrfHeaderValue),
    duplicateCsrfCookieCount: context.duplicateCsrfCookieCount,
  });
}

export function csrfFailureResponseBody(code: CsrfFailureCode): { detail: string; code: CsrfFailureCode } {
  return { detail: 'CSRF validation failed.', code };
}

// Deliberately framework-agnostic (no `import 'server-only'`, no
// `next/server`) so this classification logic is unit-testable under plain
// `npx tsx` without a bundler. control-plane-proxy.ts is the only caller in
// production; it stays 'server-only' itself and does the actual
// NextRequest/NextResponse plumbing around this.
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

export function logCsrfFailure(
  failureCode: CsrfFailureCode,
  context: { path: string; method: string; cookieNames: string[] },
): void {
  // eslint-disable-next-line no-console -- deliberate operator-facing
  // diagnostic; see this file's header comment.
  console.error(`[control-plane-proxy] CSRF validation failed: ${failureCode}`, context);
}

export function csrfFailureResponseBody(code: CsrfFailureCode): { detail: string; code: CsrfFailureCode } {
  return { detail: 'CSRF validation failed.', code };
}

/* Whether the signup verification email actually went out.
 *
 * The signup response is the only place that knows: the send happens inside
 * register_user() and is best-effort by design (an email provider must never
 * cost someone their account), so a failure there used to be logged on the
 * server and reported to the person as nothing at all. /verify-email then
 * said "We sent a 6-digit code" unconditionally, which is a claim, not a
 * fact — and it was false for every signup during the window when the sender
 * pointed at an unregistered domain.
 *
 * Three states, never two — the same rule workspace_invite_email_service
 * follows. "Nothing was sent because nothing is configured" and "the provider
 * refused this one" need different words, because only one of them is worth
 * retrying. Collapsing them tells a person to retry something that cannot
 * work; collapsing either into "sent" is the original bug.
 *
 * Carried through sessionStorage rather than a database column because signup
 * hands off with a full page load (window.location.replace), which discards
 * in-memory state but not sessionStorage — and because the alternative is a
 * migration on a live production table for a notice that only matters for the
 * few minutes between signing up and verifying.
 */

const STORAGE_KEY = 'empyralis.signup-verification-delivery.v1';

/* The SAME three names the invite path uses (see
 * test_workspace_invite_email.py). One concept, one vocabulary. */
export type VerificationDeliveryStatus = 'sent' | 'failed' | 'not_configured';

export type VerificationDelivery = {
  status: VerificationDeliveryStatus;
};

function isStatus(value: unknown): value is VerificationDeliveryStatus {
  return value === 'sent' || value === 'failed' || value === 'not_configured';
}

/** Pull the delivery outcome out of a signup response, if it carried one. */
export function readDeliveryFromSignupPayload(payload: unknown): VerificationDelivery | null {
  if (!payload || typeof payload !== 'object') return null;
  const raw = (payload as Record<string, unknown>).email_verification;
  if (!raw || typeof raw !== 'object') return null;
  const record = raw as Record<string, unknown>;
  if (!isStatus(record.status)) return null;
  return { status: record.status };
}

export function rememberVerificationDelivery(delivery: VerificationDelivery | null): void {
  if (typeof window === 'undefined' || !delivery) return;
  try {
    // Only a FAILURE is worth carrying. A successful send needs no notice, and
    // storing it would leave a stale "sent" behind for the next signup in the
    // same tab to read.
    if (delivery.status === 'sent') {
      window.sessionStorage.removeItem(STORAGE_KEY);
      return;
    }
    window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(delivery));
  } catch (_error) {
    // A browser with sessionStorage denied still gets a working signup; it
    // just loses the notice. Never let storage break the flow.
  }
}

export function readRememberedVerificationDelivery(): VerificationDelivery | null {
  if (typeof window === 'undefined') return null;
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    if (!isStatus(parsed.status)) return null;
    return { status: parsed.status };
  } catch (_error) {
    return null;
  }
}

export function clearRememberedVerificationDelivery(): void {
  if (typeof window === 'undefined') return;
  try {
    window.sessionStorage.removeItem(STORAGE_KEY);
  } catch (_error) {
    // Nothing to do — a stale notice is harmless next to a broken flow.
  }
}

/** What to tell the person. Never names mechanism — a control does the work,
 *  it does not explain the plumbing behind it. */
export function verificationDeliveryNotice(delivery: VerificationDelivery | null): string | null {
  if (!delivery || delivery.status === 'sent') return null;
  if (delivery.status === 'not_configured') {
    return 'We could not send your code, and resending will not help yet. Contact support and we will get you in.';
  }
  return 'We could not send your code. Try resending it below.';
}

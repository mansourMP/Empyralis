'use client';

import Link from 'next/link';
import { FormEvent, Suspense, useEffect, useState } from 'react';
import { ArrowRight, Mail, ShieldCheck } from 'lucide-react';

import {
  getEmailVerificationStatus,
  resendVerificationEmail,
  verifyEmailCode,
} from '@/lib/auth/auth-client';
import {
  clearRememberedVerificationDelivery,
  readRememberedVerificationDelivery,
  verificationDeliveryNotice,
  type VerificationDelivery,
} from '@/lib/auth/verification-delivery';
import { safeNextPath } from '@/lib/auth/login-next';
import { AppButton, AppInput } from '@/lib/ui/primitives';


function verifyErrorCopy(error: string): string {
  const normalized = error.trim().toLowerCase();
  if (normalized.includes('status 429') || normalized.includes('too many')) {
    return 'Too many attempts. Wait a moment, then try again.';
  }
  if (normalized.includes('expired')) {
    return 'That code expired. Request a new one below.';
  }
  if (normalized.includes('incorrect')) {
    return "That code is incorrect. Double-check your email and try again.";
  }
  if (normalized.includes('no verification code was found')) {
    return 'No code is on file for this account yet. Request one below.';
  }
  if (normalized.includes('status 401')) {
    return 'Your session expired. Sign in again to keep verifying this account.';
  }
  return error || 'Could not verify that code. Try again.';
}

function resendErrorCopy(error: string): string {
  const normalized = error.trim().toLowerCase();
  if (normalized.includes('status 503') || normalized.includes('not configured')) {
    return 'Email sending is not configured in this environment yet. Contact support to finish verifying your account.';
  }
  if (normalized.includes('status 502')) {
    return 'The email provider could not send your code just now. Try again shortly.';
  }
  if (normalized.includes('status 429') || normalized.includes('wait')) {
    return error || 'Please wait before requesting another code.';
  }
  return error || 'Could not send a new code. Try again.';
}

function VerifyEmailForm() {
  const [code, setCode] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [resendBusy, setResendBusy] = useState(false);
  const [resendNotice, setResendNotice] = useState<string | null>(null);
  const [checkingStatus, setCheckingStatus] = useState(true);
  const [nextTarget, setNextTarget] = useState('/');
  // Set only when signup TOLD us the email did not go out. Everything below
  // reads it so the screen never claims a code is on its way when none is.
  const [delivery, setDelivery] = useState<VerificationDelivery | null>(null);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    setNextTarget(safeNextPath(String(params.get('next') || '')));
    setDelivery(readRememberedVerificationDelivery());
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const status = await getEmailVerificationStatus();
        if (!cancelled && status?.email_verified) {
          window.location.replace(nextTarget);
          return;
        }
      } catch {
        // Not verified (or status check failed) -- fall through to the form.
      } finally {
        if (!cancelled) {
          setCheckingStatus(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [nextTarget]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await verifyEmailCode(code);
      clearRememberedVerificationDelivery();
      window.location.replace(nextTarget);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : 'Verification failed.');
    } finally {
      setSubmitting(false);
    }
  }

  async function handleResend() {
    setResendBusy(true);
    setResendNotice(null);
    setError(null);
    try {
      await resendVerificationEmail();
      // A resend that succeeds retires the signup-time failure notice: a code
      // really is on its way now, so leaving "we could not send your code" on
      // screen would be the same dishonesty in the other direction.
      clearRememberedVerificationDelivery();
      setDelivery(null);
      setResendNotice('A new code is on its way. It can take a minute to arrive.');
    } catch (nextError) {
      setResendNotice(resendErrorCopy(nextError instanceof Error ? nextError.message : ''));
    } finally {
      setResendBusy(false);
    }
  }

  const deliveryNotice = verificationDeliveryNotice(delivery);

  if (checkingStatus) {
    // `.app-auth-shell` (no `--centered` modifier) is a two-column grid
    // (`1fr minmax(360px,520px)`) — a single card as its only child sat in
    // the LEFT track instead of being centered, and was one line tall
    // against the real hero+form pair's full height, so the page visibly
    // both re-laid-out and grew the instant the check resolved. Reuses both
    // real sections' classNames instead of one lone card.
    return (
      <main className="app-auth-page">
        <div className="app-auth-shell">
          <section className="app-auth-hero" aria-hidden="true">
            <div className="app-auth-hero__badge">Empyralis</div>
            <div className="app-auth-hero__copy">
              <div className="fleet-skeleton-bar" style={{ width: "70%", height: 28 }} />
              <div className="fleet-skeleton-bar" style={{ width: "90%", height: 14, marginTop: 12 }} />
            </div>
          </section>
          <section className="app-auth-card app-auth-card--elevated app-auth-form" aria-busy="true" aria-label="Checking your account">
            <div className="app-auth-header">
              <span className="app-auth-kicker">Verify email</span>
              <div className="fleet-skeleton-bar" style={{ width: "60%", height: 20, marginTop: 6 }} />
              <p className="app-auth-subtitle">Checking your account…</p>
            </div>
            <div className="fleet-skeleton-bar" style={{ width: "100%", height: 40, borderRadius: 8, marginTop: 12 }} />
          </section>
        </div>
      </main>
    );
  }

  return (
    <main className="app-auth-page">
      <div className="app-auth-shell">
        <section className="app-auth-hero" aria-label="Empyralis email verification">
          <div className="app-auth-hero__badge">Empyralis</div>
          <div className="app-auth-hero__copy">
            <h1 className="app-auth-hero__title">
              {deliveryNotice ? 'We couldn’t send your code.' : 'Check your email.'}
            </h1>
            {/* The hero states the SITUATION; the alert beside the Resend
                button states what to do about it. Repeating the notice here
                verbatim just makes a person read the same sentence twice. */}
            <p className="app-auth-hero__body">
              {deliveryNotice
                ? 'Your account is created — it still needs a verified email to finish setting up.'
                : 'We sent a 6-digit code to the address you signed up with. Enter it below to finish setting up your account.'}
            </p>
          </div>
          <div className="app-auth-hero__rail">
            <div className="app-auth-hero__point">
              <ShieldCheck size={16} aria-hidden="true" />
              <span>Confirms this is really your inbox before your account gets full access.</span>
            </div>
          </div>
        </section>
        <form method="post" onSubmit={handleSubmit} className="app-auth-card app-auth-card--elevated app-auth-form">
          <div className="app-auth-header">
            <span className="app-auth-kicker">Verify email</span>
            <h2 className="app-auth-title">Enter your code</h2>
            <p className="app-auth-subtitle">The code expires in a little while — request a new one if it's stale.</p>
          </div>
          <label className="app-auth-field">
            <span className="app-auth-field__label">Verification code</span>
            <span className="app-auth-input-shell">
              <Mail className="app-auth-input-shell__icon" size={16} aria-hidden="true" />
              <AppInput
                autoComplete="one-time-code"
                inputMode="numeric"
                name="code"
                required
                minLength={6}
                maxLength={6}
                value={code}
                className="app-auth-input"
                placeholder="000000"
                onChange={(event) => setCode(event.target.value.replace(/[^0-9]/g, '').slice(0, 6))}
              />
            </span>
          </label>
          {deliveryNotice ? (
            <div role="alert" className="app-auth-error">
              <strong>No code was sent</strong>
              <span>{deliveryNotice}</span>
            </div>
          ) : null}
          {error ? (
            <div role="alert" className="app-auth-error">
              <strong>Couldn’t verify that code</strong>
              <span>{verifyErrorCopy(error)}</span>
            </div>
          ) : null}
          {resendNotice ? <p className="app-auth-provider-note">{resendNotice}</p> : null}
          <AppButton type="submit" disabled={submitting || code.length !== 6} className="app-auth-submit">
            <span>{submitting ? 'Verifying…' : 'Verify email'}</span>
            <ArrowRight size={16} aria-hidden="true" />
          </AppButton>
          <AppButton
            type="button"
            tone="secondary"
            disabled={resendBusy}
            onClick={() => void handleResend()}
          >
            <span>{resendBusy ? 'Sending…' : "Didn't get a code? Resend"}</span>
          </AppButton>
          <p className="app-auth-footer">
            Wrong account? <Link href="/login">Log in with a different one</Link>
          </p>
        </form>
      </div>
    </main>
  );
}

export default function VerifyEmailPage() {
  return (
    <Suspense fallback={null}>
      <VerifyEmailForm />
    </Suspense>
  );
}

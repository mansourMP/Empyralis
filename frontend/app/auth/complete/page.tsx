'use client';

import { Suspense, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'next/navigation';

import {
  announceExternalAuthCompletion,
  awaitBrowserAuthReady,
  clearExternalAuthPending,
  consumePendingNativeLoginHandoff,
  mintNativeAuthHandoff,
} from '@/lib/auth/auth-client';
import {
  nativeHandoffRedirectUrl,
  planNativeLoginHandoffFromRecord,
  type NativeHandoffPlan,
} from '@/lib/auth/native-login-handoff';
import { AppButton } from '@/lib/ui/primitives';

function resolveNextPath(value: string | null): string {
  const normalized = String(value || '').trim();
  if (!normalized.startsWith('/') || normalized.startsWith('//')) {
    return '/';
  }
  return normalized;
}

type NativePlan = Extract<NativeHandoffPlan, { kind: 'native' }>;

function AuthCompleteContent() {
  const searchParams = useSearchParams();
  const provider = 'google' as const;
  const nextPath = useMemo(
    () => resolveNextPath(searchParams.get('next')),
    [searchParams],
  );
  const [error, setError] = useState<string | null>(null);
  // A DIFFERENT fact from `error` above (CLAUDE.md's outcome-honesty law,
  // and the identical distinction login/page.tsx's own nativeHandoffError
  // makes): `error` means the Google sign-in itself never finished. This
  // means it finished and only the handoff back to the app did not -- the
  // person is genuinely signed in here.
  const [nativeHandoffError, setNativeHandoffError] = useState<string | null>(null);
  const [nativePlan, setNativePlan] = useState<NativePlan | null>(null);
  const [retrying, setRetrying] = useState(false);

  async function attemptNativeHandoff(plan: NativePlan): Promise<void> {
    try {
      const handoff = await mintNativeAuthHandoff({
        native: plan.target,
        codeChallenge: plan.codeChallenge,
        state: plan.state,
      });
      const redirectUrl = handoff
        ? nativeHandoffRedirectUrl(handoff.redirect_uri, handoff.code, plan.state)
        : null;
      if (!redirectUrl) {
        setNativeHandoffError('The app did not receive a usable code. Try again.');
        return;
      }
      window.location.replace(redirectUrl);
    } catch (handoffError) {
      setNativeHandoffError(
        handoffError instanceof Error ? handoffError.message : 'Could not hand off to the app.',
      );
    }
  }

  useEffect(() => {
    let cancelled = false;

    async function finalizeAuth() {
      try {
        await awaitBrowserAuthReady({ attempts: 12, delayMs: 250 });
        if (cancelled) {
          return;
        }
        clearExternalAuthPending();
        announceExternalAuthCompletion(provider);

        // A native app's Google sign-in relays its handoff request through
        // sessionStorage (auth-client.ts's markPendingNativeLoginHandoff,
        // called from login/page.tsx's startGoogleLogin) because the
        // /api/auth/google -> accounts.google.com -> /api/auth/google/
        // callback round trip lands HERE, not back on /login -- a real
        // ASWebAuthenticationSession Google flow never revisits the page
        // that started it. Read-and-clear: a later ordinary sign-in in the
        // same tab must never replay a stale handoff request.
        const plan = planNativeLoginHandoffFromRecord(consumePendingNativeLoginHandoff());
        if (plan.kind === 'native') {
          if (cancelled) {
            return;
          }
          setNativePlan(plan);
          await attemptNativeHandoff(plan);
          return;
        }

        window.location.replace(nextPath);
      } catch {
        if (cancelled) {
          return;
        }
        clearExternalAuthPending();
        setError('Authentication could not finish. Try again or use email.');
        window.setTimeout(() => {
          if (!cancelled) {
            const redirect = new URL('/login', window.location.origin);
            redirect.searchParams.set('error', `${provider}_auth_failed`);
            window.location.replace(redirect.toString());
          }
        }, 900);
      }
    }

    void finalizeAuth();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nextPath, provider]);

  if (nativeHandoffError) {
    return (
      <main className="app-auth-page">
        <div className="app-auth-shell app-auth-shell--centered">
          <div className="app-auth-card">
            <div className="app-auth-header">
              <h1 className="app-auth-title">Signed in</h1>
              <p className="app-auth-subtitle">
                Your account is signed in here — the app just did not hear back.
              </p>
            </div>
            <div role="alert" className="app-auth-error">
              <strong>Couldn&rsquo;t return to the app</strong>
              <span>{nativeHandoffError}</span>
            </div>
            <AppButton
              type="button"
              tone="primary"
              disabled={retrying}
              onClick={() => {
                if (!nativePlan) {
                  return;
                }
                setNativeHandoffError(null);
                setRetrying(true);
                void attemptNativeHandoff(nativePlan).finally(() => setRetrying(false));
              }}
              className="app-auth-submit"
            >
              <span>{retrying ? 'Trying again…' : 'Try again'}</span>
            </AppButton>
          </div>
        </div>
      </main>
    );
  }

  return (
    <main className="app-auth-page">
      <div className="app-auth-shell">
        <section className="app-auth-hero" aria-label="Empyralis sign-in completion">
          <div className="app-auth-hero__badge">Empyralis</div>
          <div className="app-auth-hero__copy">
            <h1 className="app-auth-hero__title">Finishing your sign-in.</h1>
            <p className="app-auth-hero__body">
              We are syncing your account, workspace access, and the last signed-in browser tab.
            </p>
          </div>
        </section>
        <section className="app-auth-card app-auth-card--elevated app-auth-complete-card" aria-live="polite">
          <div className="app-auth-header">
            <span className="app-auth-kicker">Almost there</span>
            <h2 className="app-auth-title">Opening Empyralis</h2>
            <p className="app-auth-subtitle">
              {error || 'Your Google sign-in succeeded. Taking you to your workspace now.'}
            </p>
          </div>
          <div className="app-auth-complete-meter" aria-hidden="true">
            <span className="app-auth-complete-meter__fill" />
          </div>
        </section>
      </div>
    </main>
  );
}

export default function AuthCompletePage() {
  return (
    <Suspense fallback={null}>
      <AuthCompleteContent />
    </Suspense>
  );
}

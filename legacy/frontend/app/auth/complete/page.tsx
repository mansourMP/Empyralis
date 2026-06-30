'use client';

import { Suspense, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'next/navigation';

import {
  announceExternalAuthCompletion,
  awaitBrowserAuthReady,
  clearExternalAuthPending,
} from '@/lib/auth/auth-client';

function resolveNextPath(value: string | null): string {
  const normalized = String(value || '').trim();
  if (!normalized.startsWith('/') || normalized.startsWith('//')) {
    return '/';
  }
  return normalized;
}

function AuthCompleteContent() {
  const searchParams = useSearchParams();
  const provider = 'google' as const;
  const nextPath = useMemo(
    () => resolveNextPath(searchParams.get('next')),
    [searchParams],
  );
  const [error, setError] = useState<string | null>(null);

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
  }, [nextPath, provider]);

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
            <h2 className="app-auth-title">Opening Sage</h2>
            <p className="app-auth-subtitle">
              {error || 'Your Google sign-in succeeded. We are returning you to Sage now.'}
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

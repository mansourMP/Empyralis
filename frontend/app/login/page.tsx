'use client';

import Link from 'next/link';
import { useSearchParams } from 'next/navigation';
import { FormEvent, Suspense, useEffect, useState } from 'react';
import { ArrowRight, Lock, Mail } from 'lucide-react';

import {
  awaitBrowserAuthReady,
  clearExternalAuthPending,
  getPendingExternalAuthProvider,
  googleLogin,
  listAuthProviders,
  login,
  type AuthProviderOptions,
  watchExternalAuthCompletion,
} from '@/lib/auth/auth-client';
import { GoogleProviderIcon } from '@/lib/auth/auth-provider-icons';
import { AppButton, AppInput } from '@/lib/ui/primitives';

function authErrorCopy(error: string): string {
  const unwrapped = error
    .trim()
    .replace(/^(login request failed|session readiness failed|authentication request failed):\s*/i, '');
  const normalized = unwrapped.toLowerCase();
  if (normalized.includes('control plane is unavailable') || normalized.includes('fetch failed')) {
    return 'This deployment cannot reach the Empyralis control plane. Open the local app or connect this deployment to a reachable backend.';
  }
  if (normalized.includes('google_not_configured')) {
    return 'Google sign-in is not configured for this environment yet.';
  }
  if (normalized.includes('google_origin_not_allowed')) {
    return 'Google sign-in is restricted to approved Empyralis domains.';
  }
  if (normalized.includes('google_runtime_not_configured')) {
    return 'Google sign-in is not fully enabled on the runtime yet. Use email for now.';
  }
  if (normalized.includes('google_rate_limited')) {
    return 'Too many Google sign-in attempts. Wait a minute, then try again.';
  }
  // WorkspaceTransportAdapter.redirectToLogin (workspace-services.tsx) lands
  // here after a 401 survives a real refresh attempt — the session is
  // genuinely gone, not merely mid-race with a concurrent refresh (see
  // auth.py's RefreshTokenSupersededError for that case, which never
  // reaches this redirect). Naming it explicitly, rather than letting it
  // fall through to the generic "could not finish" copy below, is the
  // difference between "reload and try again" (implies OUR bug) and "sign
  // in again" (tells the owner what actually happened and what to do) —
  // the dead-end a 401 used to be before this branch existed.
  if (normalized.includes('session expired') || normalized.includes('refresh token has expired')) {
    return 'Your session expired. Sign in again to continue.';
  }
  if (normalized.includes('google_state_invalid') || normalized.includes('google_auth_failed')) {
    return 'Google sign-in could not finish. Try again or use email.';
  }
  if (normalized.includes('status 401')) {
    return 'Email or password was not accepted.';
  }
  if (normalized.includes('csrf')) {
    return "Your session looks out of date. Clear this site's cookies in your browser, then try again.";
  }
  if (normalized.includes('status 403')) {
    return 'This session is not allowed to open the workspace yet. Sign in again or use an allowed account.';
  }
  if (normalized.includes('status 404')) {
    return 'The auth route is not available in this environment.';
  }
  if (normalized.includes('status 429')) {
    return 'Too many sign-in attempts. Wait a minute, then try again.';
  }
  if (/(?:status\s*)?5\d\d/.test(normalized)
    || /internal server|bad gateway|service unavailable|gateway timeout|temporarily unavailable|warming up/.test(normalized)) {
    return 'The auth service is warming up or unavailable. Try again in a moment.';
  }
  if (normalized.includes('password') || normalized.includes('credential') || normalized.includes('invalid')) {
    return 'Email or password was not accepted.';
  }
  return 'Authentication could not finish. Try again when ready.';
}

function AuthErrorNotice({ title, message }: { title: string; message: string }) {
  return (
    <div role="alert" className="app-auth-error">
      <strong>{title}</strong>
      <span>{authErrorCopy(message)}</span>
    </div>
  );
}

function safeNextPath(rawNext: string): string {
  const trimmed = rawNext.trim();
  if (!trimmed || !trimmed.startsWith('/') || trimmed.startsWith('//') || trimmed.includes('\\')) {
    return '/';
  }
  return trimmed;
}

function LoginPageContent() {
  const searchParams = useSearchParams();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isHydrated, setIsHydrated] = useState(false);
  const [providersLoaded, setProvidersLoaded] = useState(false);
  const [authRuntimeError, setAuthRuntimeError] = useState<string | null>(null);
  const [providers, setProviders] = useState<AuthProviderOptions>({
    email: { enabled: false },
    google: { enabled: false },
  });
  const agentParam = String(searchParams.get('agent') || '').trim();
  const channelAttribution = String(searchParams.get('channel_attribution') || '').trim();
  const sourceParam = String(searchParams.get('source') || '').trim();
  const channelParam = String(searchParams.get('channel') || '').trim();
  const workspaceParam = String(searchParams.get('workspace_id') || '').trim();
  const pilotCode = String(searchParams.get('pilot_code') || '').trim();
  const providerError = String(searchParams.get('error') || '').trim();
  const loginRedirectTarget = safeNextPath(String(searchParams.get('next') || ''));
  const signupSearchParams = new URLSearchParams();
  if (sourceParam) {
    signupSearchParams.set('source', sourceParam);
  }
  if (channelParam) {
    signupSearchParams.set('channel', channelParam);
  }
  if (workspaceParam) {
    signupSearchParams.set('workspace_id', workspaceParam);
  }
  if (agentParam) {
    signupSearchParams.set('agent', agentParam);
  }
  if (channelAttribution) {
    signupSearchParams.set('channel_attribution', channelAttribution);
  }
  if (pilotCode) {
    signupSearchParams.set('pilot_code', pilotCode);
  }
  const signupHref = signupSearchParams.size > 0
    ? `/signup?${signupSearchParams.toString()}`
    : '/signup';

  useEffect(() => {
    setIsHydrated(true);
    if (providerError) {
      clearExternalAuthPending();
      setError(providerError);
    }
    void listAuthProviders()
      .then((payload) => {
        setProviders({
          email: { enabled: payload?.email?.enabled !== false },
          google: { enabled: payload?.google?.enabled === true },
        });
        setAuthRuntimeError(null);
        setProvidersLoaded(true);
      })
      .catch((nextError) => {
        const message = nextError instanceof Error
          ? nextError.message
          : 'Authentication provider discovery failed.';
        setProviders({
          email: { enabled: false },
          google: { enabled: false },
        });
        setAuthRuntimeError(message);
        setProvidersLoaded(true);
      });
  }, [providerError]);

  useEffect(() => {
    if (!isHydrated) {
      return undefined;
    }
    let cancelled = false;
    let inflight = false;

    const recoverGoogleAuth = async () => {
      if (cancelled || inflight || getPendingExternalAuthProvider() !== 'google') {
        return;
      }
      inflight = true;
      try {
        await awaitBrowserAuthReady({ attempts: 2, delayMs: 200 });
        if (cancelled) {
          return;
        }
        clearExternalAuthPending();
        window.location.replace(loginRedirectTarget);
      } catch {
        // keep waiting for the callback tab or focus handoff
      } finally {
        inflight = false;
      }
    };

    void recoverGoogleAuth();
    return watchExternalAuthCompletion(() => {
      void recoverGoogleAuth();
    });
  }, [isHydrated, loginRedirectTarget]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedEmail = email.trim();
    if (!trimmedEmail || !password) {
      setError('Enter your email and password to continue.');
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await login(trimmedEmail, password);
    } catch (nextError) {
      const message = nextError instanceof Error ? nextError.message : 'Login failed.';
      setError(message);
      setSubmitting(false);
      return;
    }
    try {
      await awaitBrowserAuthReady({ attempts: 12, delayMs: 250 });
      window.location.replace(loginRedirectTarget);
    } catch {
      window.location.replace(loginRedirectTarget);
    } finally {
      setSubmitting(false);
    }
  }

  const authRuntimeUnavailable = authRuntimeError !== null;
  const emailAuthEnabled = isHydrated
    && providersLoaded
    && !authRuntimeUnavailable
    && providers.email?.enabled === true;
  const googleAuthEnabled = isHydrated
    && providersLoaded
    && !authRuntimeUnavailable
    && providers.google?.enabled === true;
  const emailSubmitEnabled = email.trim().length > 0 && password.length > 0;

  return (
    <main className="app-auth-page">
      <div className="app-auth-shell app-auth-shell--centered">
        <form method="post" onSubmit={handleSubmit} className="app-auth-card app-auth-form">
          <div className="app-auth-header">
            {/* Decorative: the h1 beneath it already names Empyralis, so an
                alt string here would be read out twice by a screen reader. */}
            <img
              src="/brand-assets/empyralis/empyralis-mark.svg"
              alt=""
              aria-hidden="true"
              width={64}
              height={64}
              className="app-auth-brand-mark"
            />
            <h1 className="app-auth-title">Log in to Empyralis</h1>
            <p className="app-auth-subtitle">Your personal control surface for autonomous work.</p>
          </div>
          {authRuntimeError ? (
            <AuthErrorNotice title="Auth unavailable" message={authRuntimeError} />
          ) : null}
          <div className="app-auth-provider-stack">
            <div className="app-auth-social-stack">
              <AppButton
                type="button"
                tone="secondary"
                className="app-auth-social"
                onClick={() => googleLogin()}
                disabled={submitting || !googleAuthEnabled}
              >
                <GoogleProviderIcon className="app-auth-provider-mark" />
                <span className="app-auth-social__content">
                  <span className="app-auth-social__title">Continue with Google</span>
                </span>
              </AppButton>
            </div>
            {!authRuntimeUnavailable && providersLoaded && providers.google?.enabled !== true && emailAuthEnabled ? (
              <p className="app-auth-provider-note">Google sign-in is unavailable right now. Use email below.</p>
            ) : null}
            <div className="app-auth-divider">
              <span>or email</span>
            </div>
          </div>
          <label className="app-auth-field">
            <span className="app-auth-field__label">Email</span>
            <span className="app-auth-input-shell">
              <Mail className="app-auth-input-shell__icon" size={16} aria-hidden="true" />
              <AppInput
                autoComplete="email"
                name="email"
                type="email"
                required
                value={email}
                className="app-auth-input"
                placeholder="Enter your email"
                disabled={submitting}
                onChange={(event) => setEmail(event.target.value)}
              />
            </span>
          </label>
          <label className="app-auth-field">
            <span className="app-auth-field__label">Password</span>
            <span className="app-auth-input-shell">
              <Lock className="app-auth-input-shell__icon" size={16} aria-hidden="true" />
              <AppInput
                autoComplete="current-password"
                name="password"
                type="password"
                required
                value={password}
                className="app-auth-input"
                placeholder="Enter your password"
                disabled={submitting}
                onChange={(event) => setPassword(event.target.value)}
              />
            </span>
          </label>
          {error ? <AuthErrorNotice title="Couldn’t sign in" message={error} /> : null}
          <AppButton type="submit" tone="primary" disabled={submitting || !emailSubmitEnabled} className="app-auth-submit">
            <span>{submitting ? 'Signing in…' : 'Continue'}</span>
            <ArrowRight size={16} aria-hidden="true" />
          </AppButton>
          <p className="app-auth-footer">
            No account? <Link href={signupHref}>Sign up</Link>
          </p>
        </form>
      </div>
    </main>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={null}>
      <LoginPageContent />
    </Suspense>
  );
}

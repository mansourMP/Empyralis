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
  me,
  type AuthProviderOptions,
  watchExternalAuthCompletion,
} from '@/lib/auth/auth-client';
import { GoogleProviderIcon } from '@/lib/auth/auth-provider-icons';
import { safeNextPath } from '@/lib/auth/login-next';
import {
  accountInitials,
  forgetAccount,
  listRememberedAccounts,
  planLoginEntry,
  rememberAccount,
  type RememberedAccount,
} from '@/lib/auth/remembered-accounts';
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
  if (normalized.includes('session not ready')) {
    return "You're signed in, but your session isn't ready yet. Press Continue again in a moment.";
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
  // Who this device has signed in as before. Read on hydrate only —
  // localStorage does not exist during SSR, and reading it during render
  // would produce a server/client mismatch on the one page nobody can
  // route around.
  const [remembered, setRemembered] = useState<RememberedAccount[]>([]);
  // `null` means "show the account chooser". A value means "we know who is
  // signing in" — the form then confirms that identity instead of asking
  // for it again.
  const [chosen, setChosen] = useState<RememberedAccount | null>(null);
  const [showChooser, setShowChooser] = useState(false);
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
    const accounts = listRememberedAccounts();
    setRemembered(accounts);
    // An explicit ?email= (an invite link, a "sign in as" hand-off) is a
    // stated intent and always beats what this device happens to remember.
    const requestedEmail = String(searchParams.get('email') || '').trim().toLowerCase();
    if (requestedEmail) {
      setEmail(requestedEmail);
      setShowChooser(false);
    } else {
      setShowChooser(planLoginEntry(accounts) === 'chooser');
    }
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
  }, [providerError, searchParams]);

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
        // Google never passes an email through this page — the browser was
        // redirected away and back — so the identity is read from the
        // session that now exists. Best-effort by design: failing to
        // remember an account must never delay or block a completed
        // sign-in, so this neither throws nor gates the redirect below.
        try {
          const account = await me();
          const user = (account as { user?: Record<string, unknown> } | null)?.user ?? account;
          rememberAccount({
            email: (user as Record<string, unknown> | null)?.email,
            name: (user as Record<string, unknown> | null)?.name,
            avatarUrl: (user as Record<string, unknown> | null)?.avatar_url,
            method: 'google',
          });
        } catch {
          /* the sign-in itself succeeded; only the convenience is lost */
        }
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
      const payload = await login(trimmedEmail, password);
      // Recorded HERE — after login() resolved and before the readiness
      // poll — because this is the exact point the credential was proven
      // correct. Recording on submit would put a typo'd address on the
      // chooser forever; recording after the poll would forget a real,
      // successful sign-in whenever cookie propagation happened to be slow.
      const user = (payload as { user?: Record<string, unknown> } | null)?.user;
      rememberAccount({
        email: trimmedEmail,
        name: user?.name,
        avatarUrl: user?.avatar_url,
        method: 'email',
      });
    } catch (nextError) {
      const message = nextError instanceof Error ? nextError.message : 'Login failed.';
      setError(message);
      setSubmitting(false);
      return;
    }
    // login() succeeded — a real session and its cookies already exist on
    // that response. This poll exists only to smooth over cookie-
    // propagation latency before the next request, exactly like signup's
    // own post-signup poll (MAN-343). The two differ on what a POLL FAILURE
    // should do next: signup proceeds to /verify-email regardless, because
    // that page works from the stored email/code and does not need a live
    // session. This page's destination (loginRedirectTarget, the workspace
    // shell) genuinely DOES need one — blindly redirecting here used to
    // send an unready session straight into a page whose own auth guard
    // would find nothing and bounce it right back to a blank /login, with
    // nothing on screen ever explaining why. A submitted login must never
    // resolve into silence: on a poll failure, stay here and say so — the
    // login itself is not in question, only whether the session has
    // finished propagating, and pressing Continue again (this same handler)
    // both re-confirms and re-polls.
    try {
      await awaitBrowserAuthReady({ attempts: 12, delayMs: 250 });
    } catch {
      setError('Session not ready.');
      setSubmitting(false);
      return;
    }
    window.location.replace(loginRedirectTarget);
    setSubmitting(false);
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

  function chooseAccount(account: RememberedAccount) {
    setError(null);
    if (account.method === 'google') {
      // Showing a password field to someone who has only ever used Google
      // would be a control they cannot satisfy — send them down the door
      // they actually came through.
      void googleLogin();
      return;
    }
    setChosen(account);
    setEmail(account.email);
    setPassword('');
    setShowChooser(false);
  }

  function removeAccount(account: RememberedAccount) {
    forgetAccount(account.email);
    const next = listRememberedAccounts();
    setRemembered(next);
    if (next.length === 0) setShowChooser(false);
  }

  function useAnotherAccount() {
    setChosen(null);
    setEmail('');
    setPassword('');
    setError(null);
    setShowChooser(false);
  }

  if (showChooser && remembered.length > 0) {
    return (
      <main className="app-auth-page">
        <div className="app-auth-shell app-auth-shell--centered">
          <div className="app-auth-card">
            <div className="app-auth-header">
              <img
                src="/brand-assets/empyralis/empyralis-mark.svg"
                alt=""
                aria-hidden="true"
                width={64}
                height={64}
                className="app-auth-brand-mark"
              />
              <h1 className="app-auth-title">Choose an account</h1>
              <p className="app-auth-subtitle">to continue to Empyralis</p>
            </div>

            <ul className="app-auth-account-list">
              {remembered.map((account) => (
                <li key={account.email} className="app-auth-account-row">
                  <button
                    type="button"
                    className="app-auth-account"
                    onClick={() => chooseAccount(account)}
                    disabled={submitting}
                  >
                    <span className="app-auth-account__avatar" aria-hidden="true">
                      {account.avatarUrl
                        ? <img src={account.avatarUrl} alt="" width={36} height={36} />
                        : <span>{accountInitials(account)}</span>}
                    </span>
                    <span className="app-auth-account__identity">
                      {account.name ? (
                        <>
                          <span className="app-auth-account__name">{account.name}</span>
                          <span className="app-auth-account__email">{account.email}</span>
                        </>
                      ) : (
                        <span className="app-auth-account__name">{account.email}</span>
                      )}
                    </span>
                  </button>
                  <button
                    type="button"
                    className="app-auth-account__remove"
                    onClick={() => removeAccount(account)}
                    aria-label={`Forget ${account.email} on this device`}
                    title="Forget on this device"
                  >
                    &times;
                  </button>
                </li>
              ))}

              <li className="app-auth-account-row">
                <button
                  type="button"
                  className="app-auth-account app-auth-account--other"
                  onClick={useAnotherAccount}
                  disabled={submitting}
                >
                  <span className="app-auth-account__avatar app-auth-account__avatar--ghost" aria-hidden="true">
                    +
                  </span>
                  <span className="app-auth-account__identity">
                    <span className="app-auth-account__name">Use another account</span>
                  </span>
                </button>
              </li>
            </ul>

            {/* Says plainly what the list IS, so nobody mistakes it for
                the server knowing who they are. */}
            <p className="app-auth-account-note">
              Accounts you&rsquo;ve used on this device. Signing in still needs your password.
            </p>

            <p className="app-auth-footer">
              No account? <Link href={signupHref}>Sign up</Link>
            </p>
          </div>
        </div>
      </main>
    );
  }

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
            <h1 className="app-auth-title">
              {chosen ? 'Welcome back' : 'Log in to Empyralis'}
            </h1>
            {/* Matches signup/page.tsx's own hero rewrite (CLAUDE.md,
                "copy that names a SCREEN goes stale... copy that names the
                WORK does not") — this line was the same stale, agent-first
                framing on the surface every returning customer sees first. */}
            {!chosen ? (
              <p className="app-auth-subtitle">Your projects, documents, and tasks in one place.</p>
            ) : null}
          </div>

          {/* IDENTITY-FIRST. When we know who is signing in, show them —
              and give them a way out. A password field under an unlabelled
              form is the moment people typo a DIFFERENT account's password
              and conclude the product is broken. */}
          {chosen ? (
            <div className="app-auth-chosen">
              <span className="app-auth-account__avatar" aria-hidden="true">
                {chosen.avatarUrl
                  ? <img src={chosen.avatarUrl} alt="" width={28} height={28} />
                  : <span>{accountInitials(chosen)}</span>}
              </span>
              <span className="app-auth-chosen__email">{chosen.email}</span>
              <button
                type="button"
                className="app-auth-chosen__switch"
                onClick={() => { setChosen(null); setPassword(''); setShowChooser(true); }}
                disabled={submitting}
              >
                Not you?
              </button>
            </div>
          ) : null}
          {authRuntimeError ? (
            <AuthErrorNotice title="Auth unavailable" message={authRuntimeError} />
          ) : null}
          {/* Both hidden once an account is chosen: the identity question
              is already answered, and re-offering Google there would be a
              second way to become a DIFFERENT person on a screen whose
              whole job is confirming one. */}
          {/* NOT the `hidden` ATTRIBUTE — these classes set an explicit
              `display`, which beats the UA stylesheet's `[hidden]` rule, so
              the attribute renders them anyway. Verified live: both the
              Google button and the email field stayed on screen. Real
              conditional rendering is the only version that actually
              removes them. */}
          {!chosen ? (
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
              <span aria-hidden="true" />
              <span>or email</span>
              <span aria-hidden="true" />
            </div>
          </div>
          ) : null}
          {!chosen ? (
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
          ) : null}
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

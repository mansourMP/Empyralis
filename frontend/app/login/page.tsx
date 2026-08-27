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
  mintNativeAuthHandoff,
  type AuthProviderOptions,
  watchExternalAuthCompletion,
} from '@/lib/auth/auth-client';
import { GoogleProviderIcon } from '@/lib/auth/auth-provider-icons';
import { classifyLoginOutcome } from '@/lib/auth/auth-error-copy';
import { safeNextPath } from '@/lib/auth/login-next';
import { nativeHandoffRedirectUrl, planNativeLoginHandoff } from '@/lib/auth/native-login-handoff';
import {
  accountInitials,
  forgetAccount,
  listRememberedAccounts,
  planLoginEntry,
  rememberAccount,
  type RememberedAccount,
} from '@/lib/auth/remembered-accounts';
import { AppButton, AppInput } from '@/lib/ui/primitives';

// classifyLoginOutcome (lib/auth/auth-error-copy.ts) is the ONE
// classification that decides both the heading and the body below — see
// its own header for why the title can no longer be hardcoded separately
// from the message it accompanies.
function AuthErrorNotice({ title, message }: { title: string; message: string }) {
  const outcome = classifyLoginOutcome(message, title);
  const isNotice = outcome.tone === 'notice';
  return (
    <div role={isNotice ? 'status' : 'alert'} className={isNotice ? 'app-auth-notice' : 'app-auth-error'}>
      <strong>{outcome.title}</strong>
      <span>{outcome.body}</span>
    </div>
  );
}

// A security review of this feature flagged that, without this, a native
// handoff visit (?native=ios&code_challenge=...&state=...) renders BYTE-
// IDENTICAL to an ordinary login — so a crafted link carrying an ATTACKER'S
// OWN code_challenge would look completely unremarkable to whoever logs in
// on it. This does not close that gap by itself (the real protection is
// that ASWebAuthenticationSession only ever hands its redirect back to the
// app that started that specific session — a link opened outside the app
// never reaches this page as a genuine app-initiated flow at all), but it
// gives a real person a visible reason to notice a login page that should
// not be app-flavoured, which nothing here rendered before this.
function NativeHandoffBanner() {
  return (
    <div role="status" className="app-auth-notice">
      <strong>Signing in for the Empyralis app</strong>
      <span>You&rsquo;ll be returned to the app once this finishes.</span>
    </div>
  );
}


function LoginPageContent() {
  const searchParams = useSearchParams();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // A DIFFERENT fact from `error` above, on purpose (CLAUDE.md's outcome-
  // honesty law): `error` means the SIGN-IN itself failed. This means the
  // sign-in succeeded and only the handoff back to the native app did not
  // -- collapsing the two would render "Couldn't sign in" about a login
  // that actually worked, on the exact account this file's own header
  // comment (AuthErrorNotice) exists to prevent.
  const [nativeHandoffError, setNativeHandoffError] = useState<string | null>(null);
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
  // A native app opened this page with ?native=ios&code_challenge=...&
  // state=... (MAN-native-ios-login). Computed fresh every render straight
  // off the URL, exactly like loginRedirectTarget above -- the query string
  // never changes while this page is up, so there is nothing to memoize.
  const nativeHandoffPlan = planNativeLoginHandoff({
    native: searchParams.get('native'),
    codeChallenge: searchParams.get('code_challenge'),
    state: searchParams.get('state'),
  });
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

  // The ONE place either successful-login path is allowed to decide where
  // this browser goes next. Both handleSubmit (below) and recoverGoogleAuth
  // (the next effect) call this instead of redirecting directly — the exact
  // "guard called once in a large function is a guard the next branch will
  // skip" trap CLAUDE.md documents, except the branches here are two
  // separate code paths in one component rather than two returns in one
  // function. A REAL ASWebAuthenticationSession Google flow does not
  // actually return here at all (see /auth/complete/page.tsx's own header
  // comment) -- this covers the email/password path fully and the narrower
  // same-tab-refocus case of the Google path; /auth/complete carries the
  // identical logic for the realistic single-session Google round trip.
  async function completeAuthenticatedRedirect(): Promise<void> {
    if (nativeHandoffPlan.kind !== 'native') {
      window.location.replace(loginRedirectTarget);
      return;
    }
    try {
      const handoff = await mintNativeAuthHandoff({
        native: nativeHandoffPlan.target,
        codeChallenge: nativeHandoffPlan.codeChallenge,
        state: nativeHandoffPlan.state,
      });
      const redirectUrl = handoff
        ? nativeHandoffRedirectUrl(handoff.redirect_uri, handoff.code, nativeHandoffPlan.state)
        : null;
      if (!redirectUrl) {
        // The mint call answered (no network/server error) but returned
        // nothing a redirect could be built from -- an honest "we minted
        // nothing usable" fact, distinct from the thrown-error branch below,
        // but reported the same way: the person IS signed in here.
        setNativeHandoffError('The app did not receive a usable code. Try again.');
        setSubmitting(false);
        return;
      }
      window.location.replace(redirectUrl);
    } catch (handoffError) {
      setNativeHandoffError(
        handoffError instanceof Error ? handoffError.message : 'Could not hand off to the app.',
      );
      setSubmitting(false);
    }
  }

  function startGoogleLogin(): void {
    googleLogin(
      nativeHandoffPlan.kind === 'native'
        ? {
            native: nativeHandoffPlan.target,
            codeChallenge: nativeHandoffPlan.codeChallenge,
            state: nativeHandoffPlan.state,
          }
        : undefined,
    );
  }

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
        await completeAuthenticatedRedirect();
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
    // resolve into silence: on a poll failure, stay here and say so.
    //
    // CORRECTION: the line that used to sit here claimed "the login itself
    // is not in question, only whether the session has finished
    // propagating" for EVERY poll failure. That is true only when
    // awaitBrowserAuthReady exhausts its attempts while still warming up —
    // it is FALSE for two of the three things that function can throw: 3
    // consecutive 401s means the session is actually gone (its own comment
    // says so), and a 403 means this account genuinely cannot open this
    // workspace. Both of those are real failures, not propagation lag, and
    // telling the customer to "press Continue again" tells someone with a
    // dead session or no access to retry forever. So the thrown message is
    // preserved and classified below (classifyLoginOutcome, via
    // AuthErrorNotice) instead of being discarded for one hardcoded
    // "press Continue" string — only the genuinely-still-warming case keeps
    // that instruction.
    try {
      await awaitBrowserAuthReady({ attempts: 12, delayMs: 250 });
    } catch (readinessError) {
      const message = readinessError instanceof Error ? readinessError.message : 'Session not ready.';
      setError(message);
      setSubmitting(false);
      return;
    }
    await completeAuthenticatedRedirect();
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
      void startGoogleLogin();
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

  // Takes priority over the chooser and the form below: this state is only
  // reachable AFTER a real, already-succeeded sign-in (see
  // completeAuthenticatedRedirect's own comment) -- there is no form left to
  // show, and re-showing one would ask this person to sign in a second time
  // for a session that already exists.
  if (nativeHandoffError) {
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
              disabled={submitting}
              onClick={() => {
                setNativeHandoffError(null);
                setSubmitting(true);
                void completeAuthenticatedRedirect().finally(() => setSubmitting(false));
              }}
              className="app-auth-submit"
            >
              <span>{submitting ? 'Trying again…' : 'Try again'}</span>
              <ArrowRight size={16} aria-hidden="true" />
            </AppButton>
          </div>
        </div>
      </main>
    );
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

            {nativeHandoffPlan.kind === 'native' ? <NativeHandoffBanner /> : null}

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
          {nativeHandoffPlan.kind === 'native' ? <NativeHandoffBanner /> : null}
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
                onClick={() => startGoogleLogin()}
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
          <p className="app-auth-provider-note">
            <Link href="/terms">Terms of Service</Link> · <Link href="/privacy">Privacy Policy</Link>
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

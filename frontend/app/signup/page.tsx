'use client';

import Link from 'next/link';
import { FormEvent, useEffect, useState } from 'react';
import { ArrowRight, Lock, Mail, User } from 'lucide-react';

import {
  AuthNetworkError,
  awaitBrowserAuthReady,
  clearExternalAuthPending,
  getPendingExternalAuthProvider,
  googleLogin,
  listAuthProviders,
  login,
  signup,
  type AuthProviderOptions,
  watchExternalAuthCompletion,
} from '@/lib/auth/auth-client';
import { GoogleProviderIcon } from '@/lib/auth/auth-provider-icons';
import {
  readDeliveryFromSignupPayload,
  rememberVerificationDelivery,
} from '@/lib/auth/verification-delivery';
import { AppButton, AppInput } from '@/lib/ui/primitives';

function authErrorCopy(error: string): string {
  const unwrapped = error
    .trim()
    .replace(/^(signup request failed|create account request failed|authentication request failed|session readiness failed):\s*/i, '');
  const normalized = unwrapped.toLowerCase();
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
  if (normalized.includes('google_state_invalid') || normalized.includes('google_auth_failed')) {
    return 'Google sign-in could not finish. Try again or use email.';
  }
  if (normalized.includes('already') || normalized.includes('exists')) {
    return 'That email is already registered. Log in or use another email.';
  }
  if (normalized.includes('invite')) {
    return 'Empyralis is invite-only right now. Enter your invite code, or ask whoever invited you for one.';
  }
  if (normalized.includes('status 401')) {
    return 'Email or password was not accepted.';
  }
  if (normalized.includes('csrf')) {
    return "Your browser has an out-of-date session cookie for this site. Clear this site's cookies, then try again.";
  }
  if (normalized.includes('status 403')) {
    return 'This account cannot open the workspace yet. Sign in again or use an allowed account.';
  }
  if (normalized.includes('status 404')) {
    return 'The auth route is not available in this environment.';
  }
  if (normalized.includes('status 429')) {
    return 'Too many account attempts. Wait a minute, then try again.';
  }
  if (/(?:status\s*)?5\d\d/.test(normalized)
    || /internal server|bad gateway|service unavailable|gateway timeout|temporarily unavailable|warming up/.test(normalized)) {
    return 'The auth service is warming up or unavailable. Try again in a moment.';
  }
  if (normalized.includes('password')) {
    return 'Use a stronger password and try again.';
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

// Mirrors /login's safeNextPath (frontend/app/login/page.tsx) — only ever
// same-origin, path-relative redirects, never an absolute/protocol-relative
// URL. Needed so the "Create an account" button on the workspace-invite
// accept page (frontend/app/join/[token]/page.tsx) can carry a visitor who
// has no account yet all the way back to /join/{token} once signup
// finishes, instead of dropping them on their own new, unrelated workspace
// (MAN-114 — without this, a brand-new teammate's invite never actually
// gets accepted after they sign up; they'd have to notice and click the
// invite link a second time).
function safeNextPath(rawNext: string): string {
  const trimmed = rawNext.trim();
  if (!trimmed || !trimmed.startsWith('/') || trimmed.startsWith('//') || trimmed.includes('\\')) {
    return '/';
  }
  return trimmed;
}

export default function SignupPage() {
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // MAN-343: "the account was not created" and "the account MAY have been
  // created, but the response was lost" are different facts. This flag is
  // what keeps them from sharing one message — see handleSubmit.
  const [ambiguous, setAmbiguous] = useState(false);
  const [isHydrated, setIsHydrated] = useState(false);
  const [providers, setProviders] = useState<AuthProviderOptions>({
    email: { enabled: true },
    google: { enabled: true },
    invite_required: false,
  });
  const [inviteCode, setInviteCode] = useState('');
  const [channelAttribution, setChannelAttribution] = useState('');
  const [agent, setAgent] = useState('');
  const [source, setSource] = useState('');
  const [channel, setChannel] = useState('');
  const [workspaceId, setWorkspaceId] = useState('');
  const [pilotCode, setPilotCode] = useState('');
  // Where to land after signup succeeds — defaults to '/' (today's
  // behavior), but a same-origin `?next=` (e.g. /join/{token} from the
  // workspace-invite accept page) takes the new account straight back to
  // finish whatever brought them here instead of stranding them on their
  // own fresh, unrelated workspace.
  const [nextTarget, setNextTarget] = useState('/');
  const loginSearchParams = new URLSearchParams();
  if (source) {
    loginSearchParams.set('source', source);
  }
  if (channel) {
    loginSearchParams.set('channel', channel);
  }
  if (workspaceId) {
    loginSearchParams.set('workspace_id', workspaceId);
  }
  if (channelAttribution) {
    loginSearchParams.set('channel_attribution', channelAttribution);
  }
  if (agent) {
    loginSearchParams.set('agent', agent);
  }
  if (pilotCode) {
    loginSearchParams.set('pilot_code', pilotCode);
  }
  if (nextTarget !== '/') {
    loginSearchParams.set('next', nextTarget);
  }
  const loginHref = loginSearchParams.size > 0
    ? `/login?${loginSearchParams.toString()}`
    : '/login';

  useEffect(() => {
    setIsHydrated(true);
    const params = new URLSearchParams(window.location.search);
    setChannelAttribution(String(params.get('channel_attribution') || '').trim());
    setAgent(String(params.get('agent') || '').trim());
    setSource(String(params.get('source') || '').trim());
    setChannel(String(params.get('channel') || '').trim());
    setWorkspaceId(String(params.get('workspace_id') || '').trim());
    setPilotCode(String(params.get('pilot_code') || '').trim());
    setNextTarget(safeNextPath(String(params.get('next') || '')));
    const providerError = String(params.get('error') || '').trim();
    if (providerError) {
      clearExternalAuthPending();
      setError(providerError);
    }
    void listAuthProviders()
      .then((payload) => {
        setProviders({
          email: { enabled: payload?.email?.enabled !== false },
          google: { enabled: payload?.google?.enabled === true },
          invite_required: payload?.invite_required === true,
        });
      })
      .catch(() => {
        setProviders({
          email: { enabled: true },
          google: { enabled: true },
          invite_required: false,
        });
      });
  }, []);

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
        window.location.replace(nextTarget);
      } catch {
        // keep waiting for callback handoff
      } finally {
        inflight = false;
      }
    };

    void recoverGoogleAuth();
    return watchExternalAuthCompletion(() => {
      void recoverGoogleAuth();
    });
  }, [isHydrated, nextTarget]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    // MAN-343: signup() and the post-signup readiness poll used to share one
    // try/catch, so a hiccup in the poll -- which runs AFTER the account is
    // already created and its session cookies already set on signup()'s own
    // response -- was reported as "Couldn't create the account" while the
    // account, its workspace, and its verification email all genuinely
    // existed. Split exactly like login/page.tsx's handleSubmit already
    // does: only a rejection from signup() itself means the account was
    // never created, so only that branch may set `error`.
    let signupPayload: Record<string, unknown> | null = null;
    try {
      signupPayload = await signup(
        email,
        password,
        name || undefined,
        pilotCode || undefined,
        inviteCode || undefined,
      );
    } catch (nextError) {
      // MAN-343, second half. A rejection here is TWO different facts and
      // they must not share one message:
      //
      //   a real HTTP answer (409 already registered, 422 weak password)
      //     -> the server says nothing was created. A flat failure is
      //        correct and is what still renders.
      //   AuthNetworkError (no response at all — abort at 30s, dropped
      //   connection)
      //     -> the outcome is UNKNOWN. The request may have reached the
      //        server, created the account, provisioned the workspace and
      //        sent the verification mail, with only the RESPONSE lost.
      //        Saying "Couldn't create the account" here is reporting
      //        failure on success, which either makes someone retry into a
      //        confusing duplicate-email error or walk away from an account
      //        that already exists.
      //
      // So before ever reporting failure, re-check the real state — the
      // same "verify what actually happened, then speak" shape
      // frontend/app/join/[token]/page.tsx already uses for invite accept.
      // The check that answers it here is one login attempt with the
      // credentials just typed: if the account got created, it succeeds and
      // also leaves this browser genuinely signed in, so the normal
      // /verify-email path is reachable exactly as if signup had returned
      // cleanly. (A lost response means Set-Cookie was lost with it, so
      // checking the session alone would report "no account" even when one
      // exists — which is why this re-checks by logging in rather than by
      // calling me().)
      if (nextError instanceof AuthNetworkError) {
        const recovered = await login(email, password).then(() => true).catch(() => false);
        if (recovered) {
          setAmbiguous(false);
          setError(null);
          window.location.replace(nextTarget === '/' ? '/verify-email' : `/verify-email?next=${encodeURIComponent(nextTarget)}`);
          setSubmitting(false);
          return;
        }
        // Still genuinely unknown: the login attempt did not confirm an
        // account. Never claim it failed — say what is actually true and
        // make retrying safe to reason about.
        setAmbiguous(true);
        setError(nextError.message);
        setSubmitting(false);
        return;
      }
      setAmbiguous(false);
      setError(nextError instanceof Error ? nextError.message : 'Signup failed.');
      setSubmitting(false);
      return;
    }
    // Carry "the verification email did NOT go out" across the full page
    // load below, so /verify-email can say so instead of claiming a code is
    // on its way. A successful send stores nothing.
    rememberVerificationDelivery(readDeliveryFromSignupPayload(signupPayload));
    // A brand-new signup goes straight to the "check your email" screen
    // before it ever sees the app -- see docs/design/email-verification-plan.md.
    // /verify-email carries `next` forward and lands the user on whatever
    // `nextTarget` this page would have gone to (e.g. a pending workspace
    // invite from /join/{token}) once the code is confirmed.
    const verifyUrl = nextTarget === '/'
      ? '/verify-email'
      : `/verify-email?next=${encodeURIComponent(nextTarget)}`;
    try {
      // Purely a readiness POLL smoothing over cookie-propagation latency
      // before /verify-email's own first request -- not authoritative, same
      // role as the identical post-login poll in login/page.tsx. A failure
      // here means the poll was unlucky, never that the account doesn't
      // exist, so it must never surface as an error -- proceed to
      // /verify-email regardless and let that page recheck the session on
      // its own.
      await awaitBrowserAuthReady({ attempts: 12, delayMs: 250 });
    } catch {
      // proceed anyway -- see comment above.
    } finally {
      window.location.replace(verifyUrl);
      setSubmitting(false);
    }
  }

  return (
    <main className="app-auth-page">
      <div className="app-auth-shell">
        <section className="app-auth-hero" aria-label="Empyralis sign up overview">
          <div className="app-auth-hero__badge">Empyralis</div>
          <div className="app-auth-hero__copy">
            {/* Leads with the WORKSPACE, never with agents. CLAUDE.md's
                positioning entry is explicit: "The WORKSPACE is the product.
                The agent layer is the second thing, not the headline," and
                "Linear never says 'AI'. They say track your issues, work
                with your teammates. Copy that."

                What was here led with "Ask AI, Build, Discover" — three
                surface names that no longer exist — and claimed fresh
                accounts land in the Agents list, which stopped being true
                the moment signup started landing in the workspace. Copy that
                names a screen is copy that goes wrong when the screen moves;
                these lines name what a team OWNS instead. */}
            <h1 className="app-auth-hero__title">Your projects, documents, and tasks in one place.</h1>
            <p className="app-auth-hero__body">
              A workspace your team actually works in — and agents that work in it alongside them.
            </p>
          </div>
          <div className="app-auth-hero__rail">
            <div className="app-auth-hero__point">
              <strong>Context that stays</strong>
              <span>Documents and decisions live where the work happens, not in a repo nobody reads.</span>
            </div>
            <div className="app-auth-hero__point">
              <strong>Work with your team</strong>
              <span>Projects, tasks and status everyone can see. Private conversations stay private.</span>
            </div>
            <div className="app-auth-hero__point">
              <strong>Bring what you already use</strong>
              <span>Google, Telegram, or your own computer — connect them when the work calls for it.</span>
            </div>
          </div>
        </section>
        <form method="post" onSubmit={handleSubmit} className="app-auth-card app-auth-card--elevated app-auth-form">
          <div className="app-auth-header">
            <span className="app-auth-kicker">Create account</span>
            <h2 className="app-auth-title">Sign up</h2>
            <p className="app-auth-subtitle">
              {channelAttribution
                ? 'Create an Empyralis account to continue from Telegram, then pick up in your workspace.'
                : 'Choose the live sign-in path you want now. You can connect the rest later from inside Empyralis.'}
            </p>
          </div>
          <div className="app-auth-provider-stack">
            <div className="app-auth-social-stack">
              <AppButton
                type="button"
                tone="secondary"
                className="app-auth-social"
                onClick={() => googleLogin()}
                disabled={submitting || !isHydrated || providers.google?.enabled !== true}
              >
                <GoogleProviderIcon className="app-auth-provider-mark" />
                <span className="app-auth-social__content">
                  <span className="app-auth-social__title">Continue with Google</span>
                  <span className="app-auth-social__meta">Live now · quickest account start</span>
                </span>
              </AppButton>
            </div>
            {providers.google?.enabled !== true ? (
              <p className="app-auth-provider-note">Google sign-up is unavailable in this environment right now. Use email below and connect other sign-in methods later.</p>
            ) : null}
            <div className="app-auth-divider">
              <span aria-hidden="true" />
              <span>or continue with email</span>
              <span aria-hidden="true" />
            </div>
          </div>
          <label className="app-auth-field">
            <span className="app-auth-field__label">Name</span>
            <span className="app-auth-input-shell">
              <User className="app-auth-input-shell__icon" size={16} aria-hidden="true" />
              <AppInput
                autoComplete="name"
                name="name"
                value={name}
                className="app-auth-input"
                placeholder="Enter your name"
                onChange={(event) => setName(event.target.value)}
              />
            </span>
          </label>
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
                onChange={(event) => setEmail(event.target.value)}
              />
            </span>
          </label>
          <label className="app-auth-field">
            <span className="app-auth-field__label">Password</span>
            <span className="app-auth-input-shell">
              <Lock className="app-auth-input-shell__icon" size={16} aria-hidden="true" />
              <AppInput
                autoComplete="new-password"
                name="password"
                type="password"
                minLength={8}
                required
                value={password}
                className="app-auth-input"
                placeholder="Enter your password"
                onChange={(event) => setPassword(event.target.value)}
              />
            </span>
          </label>
          {providers.invite_required ? (
            <label className="app-auth-field">
              <span className="app-auth-field__label">Invite code</span>
              <span className="app-auth-input-shell">
                <Lock className="app-auth-input-shell__icon" size={16} aria-hidden="true" />
                <AppInput
                  autoComplete="off"
                  name="invite_code"
                  required
                  value={inviteCode}
                  className="app-auth-input"
                  placeholder="Enter your invite code"
                  onChange={(event) => setInviteCode(event.target.value)}
                />
              </span>
              <span className="app-auth-provider-note">Empyralis is invite-only right now.</span>
            </label>
          ) : null}
          {error ? (
            ambiguous ? (
              // MAN-343: NOT "couldn't create the account" — we genuinely do
              // not know, and the honest sentence is also the useful one. A
              // hard failure claim here is what makes someone either retry
              // into a duplicate-email error or abandon an account that
              // already exists.
              <div role="alert" className="app-auth-error">
                <strong>We couldn’t confirm your account</strong>
                <span>
                  The request didn’t complete, so we can’t tell whether the account was created.
                  It’s safe to try again — if it already went through, logging in will pick it up.
                </span>
              </div>
            ) : (
              <AuthErrorNotice title="Couldn’t create the account" message={error} />
            )
          ) : null}
          <AppButton type="submit" disabled={submitting} className="app-auth-submit">
            <span>{submitting ? 'Creating account…' : 'Create account'}</span>
            <ArrowRight size={16} aria-hidden="true" />
          </AppButton>
          <p className="app-auth-footer">
            Already have an account? <Link href={loginHref}>Log in</Link>
          </p>
        </form>
      </div>
    </main>
  );
}

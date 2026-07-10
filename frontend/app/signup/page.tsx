'use client';

import Link from 'next/link';
import { FormEvent, useEffect, useState } from 'react';
import { ArrowRight, Lock, Mail, User } from 'lucide-react';

import {
  awaitBrowserAuthReady,
  clearExternalAuthPending,
  getPendingExternalAuthProvider,
  googleLogin,
  listAuthProviders,
  signup,
  type AuthProviderOptions,
  watchExternalAuthCompletion,
} from '@/lib/auth/auth-client';
import { GoogleProviderIcon } from '@/lib/auth/auth-provider-icons';
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

export default function SignupPage() {
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
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
        window.location.replace('/');
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
  }, [isHydrated]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await signup(email, password, name || undefined, pilotCode || undefined, inviteCode || undefined);
      await awaitBrowserAuthReady({ attempts: 12, delayMs: 250 });
      window.location.replace('/');
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : 'Signup failed.');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="app-auth-page">
      <div className="app-auth-shell">
        <section className="app-auth-hero" aria-label="Empyralis sign up overview">
          <div className="app-auth-hero__badge">Empyralis</div>
          <div className="app-auth-hero__copy">
            <h1 className="app-auth-hero__title">Start simple, then expand into connected work.</h1>
            <p className="app-auth-hero__body">
              Create one account for Sage, Build, Discover, and your connected apps. Start with a clean core, then add power on your terms.
            </p>
          </div>
          <div className="app-auth-hero__rail">
            <div className="app-auth-hero__point">
              <strong>Chat first</strong>
              <span>Fresh accounts land directly in Sage instead of a setup maze.</span>
            </div>
            <div className="app-auth-hero__point">
              <strong>Cheap default AI</strong>
              <span>Empyralis credits stay the normal launch path without forcing API keys.</span>
            </div>
            <div className="app-auth-hero__point">
              <strong>Connected apps later</strong>
              <span>Add Google, Telegram, or your computer when the workflow actually calls for it.</span>
            </div>
          </div>
        </section>
        <form method="post" onSubmit={handleSubmit} className="app-auth-card app-auth-card--elevated app-auth-form">
          <div className="app-auth-header">
            <span className="app-auth-kicker">Create account</span>
            <h2 className="app-auth-title">Sign up</h2>
            <p className="app-auth-subtitle">
              {channelAttribution
                ? 'Create an Empyralis account to continue from Telegram, then finish inside Sage.'
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
          {error ? <AuthErrorNotice title="Couldn’t create the account" message={error} /> : null}
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

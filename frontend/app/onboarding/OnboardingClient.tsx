'use client';

import Link from 'next/link';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';

import { ShellRecoveryActions, useSignOutAndStartOver } from '@/app/(account)/ShellRecoveryActions';
import {
  loadAccountShellBootstrap,
  updateWorkspace,
  type CreateWorkspaceInput,
} from '@/lib/account/account-workspaces-client';
import { useAccountShell } from '@/lib/shell/account-shell-context';
import {
  type WorkspaceMembershipRecord,
} from '@/lib/shell/workspace-membership-model';
import {
  DEFAULT_ROUTE_BY_PROFILE,
  WorkspaceSetupForm,
  createDefaultWorkspaceSetupValues,
} from '@/lib/workspace/workspace-setup-form';

function initialValuesForMembership(
  membership: WorkspaceMembershipRecord,
): CreateWorkspaceInput {
  return createDefaultWorkspaceSetupValues(membership.workspace.id, {
    name: membership.workspace.label,
    workspaceType:
      membership.workspace.kind === 'team'
      || membership.workspace.kind === 'professional'
      || membership.workspace.kind === 'personal'
        ? membership.workspace.kind
        : 'personal',
    preferredShellProfileId:
      membership.preferredShellProfileId === 'document_workstation_shell'
      || membership.preferredShellProfileId === 'operations_admin_shell'
        ? membership.preferredShellProfileId
        : 'personal_shell',
    defaultRoute: membership.defaultRoute,
  });
}

// Auto-submit defaults for the single-user platform — skip the setup form.
// The label is a DISPLAY value and can be a fallback ("Untitled workspace",
// and before workspace_naming landed, the workspace's own id). Persisting it
// would turn a placeholder into the stored name -- which is plausibly how a
// workspace whose stored name IS its own id came to exist. Send nothing and
// let the server keep what it minted.
function autoSubmitValuesForMembership(
  membership: WorkspaceMembershipRecord,
): CreateWorkspaceInput {
  return createDefaultWorkspaceSetupValues(membership.workspace.id, {
    name: '',
    workspaceType: 'personal',
    preferredShellProfileId: 'personal_shell',
    defaultRoute: membership.defaultRoute || `/w/${encodeURIComponent(membership.workspace.id)}`,
  });
}

export function OnboardingClient({
  targetWorkspaceId,
  requestedWorkspaceId,
}: {
  targetWorkspaceId: string | null;
  requestedWorkspaceId: string | null;
}) {
  const router = useRouter();
  const { state, actions } = useAccountShell();
  const [submitting, setSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const { isSigningOut, signOutAndStartOver } = useSignOutAndStartOver();
  // Guards the auto-submit effect below so it fires at most once per mount.
  // handleSubmit's own success path calls actions.replaceSession(), which
  // changes state.workspaceMemberships (and therefore the memoized
  // `membership` object the effect depends on) WHILE this component may
  // still be mounted awaiting the router.replace() navigation -- without
  // this guard that reference change would re-run the effect and fire a
  // second PATCH before the redirect lands.
  const autoSubmitAttempted = useRef(false);

  const membership = useMemo(
    () =>
      state.workspaceMemberships.find((item) => item.workspace.id === targetWorkspaceId)
      ?? null,
    [state.workspaceMemberships, targetWorkspaceId],
  );

  async function handleSubmit(values: CreateWorkspaceInput) {
    if (!membership) {
      return;
    }
    setSubmitting(true);
    setErrorMessage(null);
    try {
      await updateWorkspace(membership.workspace.id, {
        // OMITTED, not sent empty. This is a PATCH, so sending "" would blank
        // the name the server already minted; the auto-submit path below has
        // no real name to offer and must not overwrite one with a placeholder.
        ...(String(values.name || "").trim() ? { name: values.name } : {}),
        workspaceType: values.workspaceType,
        preferredShellProfileId: values.preferredShellProfileId,
        defaultRoute: values.defaultRoute,
        setupCompleted: true,
      });
      try {
        const session = await loadAccountShellBootstrap();
        actions.replaceSession(session);
      } catch {
        // Continue even if the session refresh is transiently unavailable.
      }
      // Fresh-workspace onboarding lands straight in the create-first-agent
      // wizard (?new=1) — not a bare landing, and not the removed Sage chat
      // route. The workspace already carries a name at this point.
      router.replace(`/w/${encodeURIComponent(membership.workspace.id)}/agents?new=1`);
      router.refresh();
    } catch (error) {
      setErrorMessage(
        error instanceof Error ? error.message : 'Workspace setup could not be saved.',
      );
    } finally {
      setSubmitting(false);
    }
  }

  // Auto-submit with defaults for single-user platform — skip the setup
  // form. Hooks must run unconditionally on every render (React's Rules of
  // Hooks): the previous version of this component called useEffect AFTER
  // two early `return`s above it, which rendered a different NUMBER of
  // hooks depending on state.status/membership -- a latent "Rendered fewer
  // hooks than expected" crash the moment either flipped between renders.
  // The gating now lives inside the effect instead.
  useEffect(() => {
    if (state.status !== 'authenticated' || !membership || autoSubmitAttempted.current) {
      return;
    }
    autoSubmitAttempted.current = true;
    void handleSubmit(autoSubmitValuesForMembership(membership));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.status, membership]);

  // "Still loading", "your session ended", and "we could not load your
  // workspace" are different facts (CLAUDE.md's outcome-honesty law) and
  // must not collapse into one bare `return null`, which used to render a
  // pure white screen with no chrome, no message, and no way out. There is
  // no genuine async gap here -- state.status resolves synchronously from
  // the server-rendered session -- so the one real way to land here is the
  // root layout's OWN independent account-shell fetch (loadAccountShellSessionSafely,
  // seeding AccountShellProvider) degrading even though this page's own
  // server-side session check already succeeded (see OnboardingPage, which
  // redirects to /login on a genuinely absent session before this component
  // ever mounts). That is a "could not load", not a "signed out" -- render
  // the same recoverable idiom app/(account)/layout.tsx already uses for
  // exactly this fact.
  if (state.status !== 'authenticated') {
    return (
      <main className="app-page-message">
        <div className="app-page-message__content">
          <h1 className="app-page-message__title">Onboarding is temporarily unavailable</h1>
          <p className="app-page-message__body">
            Empyralis could not load your account shell here. This can happen during a deploy or service warm-up.
          </p>
          <p className="app-page-message__meta">
            Reload this page and try again once the workspace shell is back.
          </p>
          <ShellRecoveryActions label="Onboarding recovery actions" />
        </div>
      </main>
    );
  }

  if (!membership) {
    return (
      <main className="app-auth-page">
        <div className="app-message-card">
          <div className="app-auth-header">
            <span className="app-auth-kicker">Onboarding</span>
            <h1 className="app-auth-title">
            {requestedWorkspaceId
              ? 'Requested workspace is unavailable'
              : 'No workspace is ready for onboarding'}
            </h1>
            <p className="app-auth-subtitle">
            {requestedWorkspaceId
              ? 'The requested workspace was not found. Choose another workspace from account home.'
              : 'The requested workspace was not found in your account shell. Create a new workspace to continue.'}
            </p>
          </div>
          <Link
            href={requestedWorkspaceId ? '/' : '/workspaces/new'}
            className="app-button app-button--primary"
          >
            {requestedWorkspaceId ? 'Return home' : 'Create workspace'}
          </Link>
        </div>
      </main>
    );
  }

  // The auto-submit PATCH failed (this is the production trap: a CSRF 403,
  // a network blip, anything) -- say so and offer a real way out, instead of
  // silently rendering nothing forever. Safe to retry: the PATCH is
  // idempotent (it sets the same defaults) and nothing was lost either way.
  if (errorMessage) {
    return (
      <main className="app-page-message">
        <div className="app-page-message__content">
          <h1 className="app-page-message__title">Workspace setup couldn&rsquo;t finish</h1>
          <p className="app-page-message__body">{errorMessage}</p>
          <p className="app-page-message__meta">This is safe to retry — nothing was lost.</p>
          <div className="app-page-message__actions" aria-label="Onboarding recovery actions">
            <button
              type="button"
              className="app-page-message__button"
              disabled={submitting || isSigningOut}
              onClick={() => {
                void handleSubmit(autoSubmitValuesForMembership(membership));
              }}
            >
              {submitting ? 'Retrying…' : 'Retry'}
            </button>
            {/* Was a plain link to the login route. That link cannot help a
                session stuck 403ing on every mutating request (the
                host-only CSRF cookie twin -- see ShellRecoveryActions.tsx's
                own comment): POST /api/auth/login is CSRF-gated too while
                the old access-token cookie is still live. Reuses the SAME
                sign-out action ShellRecoveryActions.tsx uses elsewhere, not
                a second implementation. */}
            <button
              type="button"
              className="app-page-message__button app-page-message__button--secondary"
              disabled={submitting || isSigningOut}
              onClick={signOutAndStartOver}
            >
              {isSigningOut ? 'Signing out...' : 'Sign out and start over'}
            </button>
          </div>
        </div>
      </main>
    );
  }

  // In flight (either the auto-submit effect hasn't fired yet on this very
  // first paint, or its PATCH is still pending) -- an honest "working on it"
  // rather than a blank screen. This never spins forever: it always resolves
  // to either the errorMessage branch above or a navigation away on success.
  return (
    <main className="app-page-message">
      <div className="app-page-message__content">
        <h1 className="app-page-message__title">Setting up your workspace</h1>
        <p className="app-page-message__body">This only takes a moment.</p>
      </div>
    </main>
  );
}

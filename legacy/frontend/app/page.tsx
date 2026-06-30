import { Metadata } from 'next';
import { redirect } from 'next/navigation';

import {
  isDegradedAccountShellSession,
  loadAccountShellSession,
} from '@/lib/server/load-account-shell-session';
import { resolvePrimaryProductWorkspaceId } from '@/lib/shell/workspace-membership-model';

import { LandingClient } from './landing-client';
import './landing.css';

export const metadata: Metadata = {
  title: 'Empyralis | AI workspace for governed agents',
  description:
    'A workspace for Sage, native agents, connected external agents, governed workflows, and approved computer access.',
};

export default async function LandingPage() {
  const loadedSession = await loadAccountShellSession();
  const session = isDegradedAccountShellSession(loadedSession) ? null : loadedSession;

  const workspaceId = session ? resolvePrimaryProductWorkspaceId(session.workspaceMemberships) : null;
  const platformHref = workspaceId ? `/w/${encodeURIComponent(workspaceId)}/sage` : '/workspaces/new';
  const isAuthenticated = Boolean(session);

  if (isAuthenticated) {
    redirect(platformHref);
  }

  return (
    <LandingClient
      accountHref="/login"
      accountLabel="Log in"
      primaryHref="/signup"
      primaryLabel="Create account"
      finalCtaLabel="Start with your main agent"
    />
  );
}

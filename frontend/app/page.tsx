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
  title: 'Empyralis — Agents that do the work',
  description:
    'Managed cloud agents that do real work. Create a specialized agent in minutes — it gets its own Telegram bot, its own memory, and its own tools, and handles your customers and tasks end to end.',
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
      primaryLabel="Get started"
    />
  );
}

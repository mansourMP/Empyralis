import { Metadata } from 'next';
import { redirect } from 'next/navigation';

import {
  isDegradedAccountShellSession,
  loadAccountShellSession,
} from '@/lib/server/load-account-shell-session';
import { resolvePrimaryProductWorkspaceId } from '@/lib/shell/workspace-membership-model';
import { LandingPage } from '@/lib/marketing/landing-page';

export const metadata: Metadata = {
  title: 'Empyralis — Build your empire with Empyralis',
  description:
    'Automate the work that runs your business — small tasks, customer support, MCP-driven workflows — with AI agents on hardware you control, using the AI subscription you already pay for.',
};

export default async function RootPage() {
  const loadedSession = await loadAccountShellSession();
  const session = isDegradedAccountShellSession(loadedSession) ? null : loadedSession;

  if (!session) {
    return <LandingPage />;
  }

  const workspaceId = resolvePrimaryProductWorkspaceId(session.workspaceMemberships);
  const platformHref = workspaceId ? `/w/${encodeURIComponent(workspaceId)}/sage` : '/workspaces/new';
  redirect(platformHref);
}

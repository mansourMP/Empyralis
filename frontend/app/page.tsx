import { Metadata } from 'next';
import { redirect } from 'next/navigation';

import {
  isDegradedAccountShellSession,
  loadAccountShellSession,
} from '@/lib/server/load-account-shell-session';
import {
  getWorkspaceMembership,
  indexWorkspaceMemberships,
  resolvePrimaryProductWorkspaceId,
} from '@/lib/shell/workspace-membership-model';

export const metadata: Metadata = {
  title: 'Empyralis',
  description: '',
};

export default async function RootPage() {
  const loadedSession = await loadAccountShellSession();
  const session = isDegradedAccountShellSession(loadedSession) ? null : loadedSession;

  if (!session) {
    redirect('/login');
  }

  const workspaceId = resolvePrimaryProductWorkspaceId(session.workspaceMemberships);
  if (!workspaceId) {
    redirect('/workspaces/new');
  }

  // Honor the workspace's own default route rather than hardcoding one --
  // '/sage' is a redirect into '/agents' now, not a page of its own, so
  // hardcoding it here bypassed whatever the workspace was actually set up
  // to land on and sent every visit to the bare domain into an empty
  // "No agents yet" screen.
  const membership = getWorkspaceMembership(
    indexWorkspaceMemberships(session.workspaceMemberships),
    workspaceId,
  );
  const platformHref = membership?.defaultRoute || `/w/${encodeURIComponent(workspaceId)}`;
  redirect(platformHref);
}

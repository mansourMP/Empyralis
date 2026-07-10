import { Metadata } from 'next';
import { redirect } from 'next/navigation';

import {
  isDegradedAccountShellSession,
  loadAccountShellSession,
} from '@/lib/server/load-account-shell-session';
import { resolvePrimaryProductWorkspaceId } from '@/lib/shell/workspace-membership-model';

export const metadata: Metadata = {
  title: 'Empyralis',
};

export default async function RootPage() {
  const loadedSession = await loadAccountShellSession();
  const session = isDegradedAccountShellSession(loadedSession) ? null : loadedSession;

  if (!session) {
    redirect('/login');
  }

  const workspaceId = resolvePrimaryProductWorkspaceId(session.workspaceMemberships);
  const platformHref = workspaceId ? `/w/${encodeURIComponent(workspaceId)}/sage` : '/workspaces/new';
  redirect(platformHref);
}

import { redirect } from 'next/navigation';

import { loadAccountShellSessionSafely } from '@/lib/server/load-account-shell-session';
import { resolvePrimaryProductWorkspaceId } from '@/lib/shell/workspace-membership-model';

export type AccountSettingsSection = 'account';

export async function redirectToWorkspaceSettings(section: AccountSettingsSection): Promise<never> {
  const session = await loadAccountShellSessionSafely();
  if (!session) {
    redirect('/login');
  }

  const workspaceId = resolvePrimaryProductWorkspaceId(session.workspaceMemberships);
  if (!workspaceId) {
    redirect('/workspaces/new');
  }

  redirect(`/w/${encodeURIComponent(workspaceId)}/settings?section=${encodeURIComponent(section)}`);
}

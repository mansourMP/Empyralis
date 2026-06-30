import { redirect } from 'next/navigation';
import { HostedMiniAppSurface } from '@/lib/workspace/hosted-mini-app-surface';
import { loadWorkspaceBootstrap } from '@/lib/workspace/server-workspace-bootstrap';

export default async function WorkspaceHostedMiniAppPage({
  params,
}: {
  params: Promise<{ workspaceId: string; appId: string }>;
}) {
  const { workspaceId, appId } = await params;
  const bootstrap = await loadWorkspaceBootstrap(workspaceId);
  if (bootstrap.capabilities.workspace_admin_enabled !== true) {
    redirect(`/w/${encodeURIComponent(workspaceId)}/sage`);
  }
  return <HostedMiniAppSurface workspaceId={workspaceId} appId={appId} />;
}

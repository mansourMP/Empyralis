import { WorkspaceHomeRedirect } from '@/app/(account)/w/[workspaceId]/WorkspaceHomeRedirect';

export default async function WorkspacePage({
  params,
}: {
  params: Promise<{ workspaceId: string }>;
}) {
  const { workspaceId } = await params;
  return <WorkspaceHomeRedirect workspaceId={workspaceId} />;
}

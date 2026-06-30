import { WorkspaceSurfacePage } from '../WorkspaceSurfacePage';

export default async function WorkspaceChannelsPage({
  params,
}: {
  params: Promise<{ workspaceId: string }>;
}) {
  const { workspaceId } = await params;
  return <WorkspaceSurfacePage workspaceId={workspaceId} surface="channels" />;
}

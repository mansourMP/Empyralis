import { WorkspaceSurfacePage } from '../WorkspaceSurfacePage';

type PageProps = {
  params: Promise<{ workspaceId: string }>;
};

export default async function WorkspaceTasksPage({ params }: PageProps) {
  const { workspaceId } = await params;
  return <WorkspaceSurfacePage workspaceId={workspaceId} surface="tasks" />;
}

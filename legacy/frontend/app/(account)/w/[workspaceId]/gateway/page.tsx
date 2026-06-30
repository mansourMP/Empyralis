import { redirect } from 'next/navigation';

export default async function WorkspaceGatewayPage({
  params,
}: {
  params: Promise<{ workspaceId: string }>;
}) {
  const { workspaceId } = await params;
  redirect(`/w/${encodeURIComponent(workspaceId)}/hardware`);
}

import { redirect } from "next/navigation";

// Agent detail without a tab → its Overview tab. Keeps the agent-level
// breadcrumb crumb (…/agents/{id}) a live link rather than a 404.
export default async function AgentIndexRedirect({
  params,
}: {
  params: Promise<{ workspaceId: string; projectId: string; agentId: string }>;
}) {
  const { workspaceId, projectId, agentId } = await params;
  redirect(
    `/w/${encodeURIComponent(workspaceId)}/projects/${encodeURIComponent(projectId)}/agents/${encodeURIComponent(agentId)}/overview`,
  );
}

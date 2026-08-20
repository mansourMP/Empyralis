import { redirect } from "next/navigation";

// Same redirect the project-scoped agent page already does (…/projects/{pid}
// /agents/{id}/page.tsx): no tab named → the Chat tab, the agent's front
// door. Keeps the agent-level breadcrumb crumb (…/agents/{id}) a live link
// rather than a 404, and gives a bookmark or a typed URL somewhere to land.
export default async function WorkspaceAgentIndexRedirect({
  params,
}: {
  params: Promise<{ workspaceId: string; agentId: string }>;
}) {
  const { workspaceId, agentId } = await params;
  redirect(`/w/${encodeURIComponent(workspaceId)}/agents/${encodeURIComponent(agentId)}/chat`);
}

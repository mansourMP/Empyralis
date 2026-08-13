import { redirect } from "next/navigation";

// Agent detail without a tab → its Chat tab, the agent's front door (an
// agent opens to a conversation, not a config screen — Work is a tab beside
// it, still one click away and still directly linkable; Overview is gone
// outright and Memory now lives inside Configure, see FleetAgentDetail.tsx).
// Keeps the agent-level breadcrumb crumb (…/agents/{id}) a live link rather
// than a 404.
export default async function AgentIndexRedirect({
  params,
}: {
  params: Promise<{ workspaceId: string; projectId: string; agentId: string }>;
}) {
  const { workspaceId, projectId, agentId } = await params;
  redirect(
    `/w/${encodeURIComponent(workspaceId)}/projects/${encodeURIComponent(projectId)}/agents/${encodeURIComponent(agentId)}/chat`,
  );
}

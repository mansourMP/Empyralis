"use client";

import { useParams, useRouter } from "next/navigation";

import { useFleetAgents, useFleetProjects } from "@/lib/workspace/fleet/fleet-data";
import { useBreadcrumbLabel } from "@/lib/workspace/fleet/Breadcrumbs";
import { FleetCard } from "@/lib/workspace/fleet/FleetCard";
import { toAgentSummary } from "@/lib/workspace/fleet/fleet-presentation";

export default function ProjectDetailPage() {
  const params = useParams();
  const router = useRouter();
  const workspaceId = String(params?.workspaceId || "");
  const projectId = String(params?.projectId || "");
  const base = `/w/${encodeURIComponent(workspaceId)}`;

  const { agents, loading } = useFleetAgents(workspaceId);
  const { projects } = useFleetProjects(workspaceId);
  const project = projects.find((p) => p.id === projectId);

  // Register the real project name so the breadcrumb shows it, not the id.
  useBreadcrumbLabel(projectId, project?.name);

  const inProject = agents.filter((a) => (a.project_id || "").trim() === projectId);
  const mapped = inProject.map((a, i) => toAgentSummary(a, i));
  const goToAgent = (agentId: string) =>
    router.push(`${base}/projects/${encodeURIComponent(projectId)}/agents/${encodeURIComponent(agentId)}/overview`);
  const openChat = () => {
    const sage = inProject.find((a) => (a.role || "").toLowerCase() === "operator") || inProject[0];
    if (sage) router.push(`${base}/projects/${encodeURIComponent(projectId)}/agents/${encodeURIComponent(sage.agent_id)}/chat`);
  };

  return (
    <main className="fleet-content">
      <div className="fleet-header">
        <div>
          <h1 className="fleet-title">{project?.name || "Project"}</h1>
          <p className="fleet-subtitle">
            {loading ? "Loading…" : `${mapped.length} ${mapped.length === 1 ? "agent" : "agents"}`}
            {project?.description ? ` · ${project.description}` : ""}
          </p>
        </div>
      </div>

      {!loading && mapped.length === 0 ? (
        <div className="fleet-empty">
          <div className="fleet-empty-title">No agents in this project</div>
          <div className="fleet-empty-desc">Create an agent and assign it here.</div>
        </div>
      ) : (
        <div className="fleet-grid">
          {mapped.map((a) => (
            <FleetCard key={a.id} agent={a} onSelect={goToAgent} onChat={openChat} />
          ))}
        </div>
      )}
    </main>
  );
}

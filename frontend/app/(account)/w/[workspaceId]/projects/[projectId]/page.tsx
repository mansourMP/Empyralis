"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";

import { useFleetAgents, useFleetProjects } from "@/lib/workspace/fleet/fleet-data";
import { useBreadcrumbLabel } from "@/lib/workspace/fleet/Breadcrumbs";
import { FleetCard } from "@/lib/workspace/fleet/FleetCard";
import { FleetCreateAgentWizard } from "@/lib/workspace/fleet/FleetCreateAgentWizard";
import { toAgentSummary } from "@/lib/workspace/fleet/fleet-presentation";

const money = (n: number | undefined) => `$${(n ?? 0).toFixed(4)}`;

export default function ProjectDetailPage() {
  const params = useParams();
  const router = useRouter();
  const workspaceId = String(params?.workspaceId || "");
  const projectId = String(params?.projectId || "");
  const base = `/w/${encodeURIComponent(workspaceId)}`;

  const { agents, loading, refresh } = useFleetAgents(workspaceId);
  const { projects } = useFleetProjects(workspaceId);
  const project = projects.find((p) => p.id === projectId);
  useBreadcrumbLabel(projectId, project?.name);

  const [wizardOpen, setWizardOpen] = useState(false);
  const [rollup, setRollup] = useState<{ usd_cost: number; total_tokens: number; events: number } | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch(`${base.replace("/w/", "/api/w/")}/fleet/usage?scope=project&id=${encodeURIComponent(projectId)}&period=month`, { credentials: "include" })
      .then((r) => r.json())
      .then((d) => { if (!cancelled && d?.totals) setRollup(d.totals); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [base, projectId]);

  const inProject = agents.filter((a) => (a.project_id || "").trim() === projectId);
  const mapped = inProject.map((a, i) => toAgentSummary(a, i));
  const goToAgent = (agentId: string) =>
    router.push(`${base}/projects/${encodeURIComponent(projectId)}/agents/${encodeURIComponent(agentId)}/overview`);
  const openChat = () => {
    const sage = inProject.find((a) => (a.role || "").toLowerCase() === "operator") || inProject[0];
    if (sage) goToAgent(sage.agent_id);
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
        <button type="button" className="fleet-btn fleet-btn--accent" onClick={() => setWizardOpen(true)}>
          <span className="fleet-btn-plus">+</span> New agent
        </button>
      </div>

      {rollup && (
        <div className="fleet-stat-grid">
          <div className="fleet-stat-card"><div className="fleet-stat-value">{money(rollup.usd_cost)}</div><div className="fleet-stat-label">Cost this month</div></div>
          <div className="fleet-stat-card"><div className="fleet-stat-value">{rollup.total_tokens.toLocaleString()}</div><div className="fleet-stat-label">Tokens</div></div>
          <div className="fleet-stat-card"><div className="fleet-stat-value">{rollup.events.toLocaleString()}</div><div className="fleet-stat-label">LLM calls</div></div>
        </div>
      )}

      {!loading && mapped.length === 0 ? (
        <div className="fleet-empty">
          <div className="fleet-empty-title">No agents in this project</div>
          <div className="fleet-empty-desc">Create one — it’ll be assigned here.</div>
        </div>
      ) : (
        <div className="fleet-grid" style={{ marginTop: "var(--space-4)" }}>
          {mapped.map((a) => (
            <FleetCard key={a.id} agent={a} onSelect={goToAgent} onChat={openChat} />
          ))}
        </div>
      )}

      {wizardOpen && (
        <FleetCreateAgentWizard
          workspaceId={workspaceId}
          initialProjectId={projectId}
          onClose={() => setWizardOpen(false)}
          onCreated={() => { setWizardOpen(false); refresh(); }}
        />
      )}
    </main>
  );
}

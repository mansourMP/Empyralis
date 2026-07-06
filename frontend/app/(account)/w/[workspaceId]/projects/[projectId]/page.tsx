"use client";

import { useEffect, useMemo, useState } from "react";
import { useParams, useRouter } from "next/navigation";

import { useFleetAgents, useFleetProjects, type FleetAgent } from "@/lib/workspace/fleet/fleet-data";
import { useBreadcrumbLabel } from "@/lib/workspace/fleet/Breadcrumbs";
import { AgentsList } from "@/lib/workspace/fleet/AgentsList";
import { FleetCreateAgentWizard } from "@/lib/workspace/fleet/FleetCreateAgentWizard";
import { FirstAgentEmpty } from "@/lib/workspace/fleet/first-agent-empty";
import { FleetListSkeleton } from "@/lib/workspace/fleet/fleet-states";

const money = (n: number | undefined) => `$${(n ?? 0).toFixed(4)}`;

type SortMode = "last_active" | "status" | "cost" | "name";
const STATUS_RANK: Record<string, number> = { online: 0, unknown: 1, offline: 2 };

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

  const [sort, setSort] = useState<SortMode>("last_active");
  const [wizardOpen, setWizardOpen] = useState(false);
  const [rollup, setRollup] = useState<{ usd_cost: number; total_tokens: number; events: number } | null>(null);
  const [cost, setCost] = useState<Map<string, number>>(new Map());

  useEffect(() => {
    let cancelled = false;
    fetch(`${base.replace("/w/", "/api/w/")}/fleet/usage?scope=project&id=${encodeURIComponent(projectId)}&period=month`, { credentials: "include" })
      .then((r) => r.json())
      .then((d) => { if (!cancelled && d?.totals) setRollup(d.totals); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [base, projectId]);

  useEffect(() => {
    let cancelled = false;
    fetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/usage?scope=workspace&period=day`, { credentials: "include" })
      .then((r) => r.json())
      .then((d) => {
        if (cancelled) return;
        const m = new Map<string, number>();
        for (const a of d?.by_agent || []) m.set(a.agent_install_id, a.usd_cost);
        setCost(m);
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [workspaceId]);

  const inProject = agents.filter((a) => (a.project_id || "").trim() === projectId);
  const shown = useMemo(() => sortAgents(inProject, sort, cost), [inProject, sort, cost]);

  const goToAgent = (agentId: string) =>
    router.push(`${base}/projects/${encodeURIComponent(projectId)}/agents/${encodeURIComponent(agentId)}/overview`);

  return (
    <main className="fleet-content">
      <div className="fleet-header">
        <div>
          <h1 className="fleet-title">{project?.name || "Project"}</h1>
          <p className="fleet-subtitle">
            {loading ? "Loading…" : `${shown.length} ${shown.length === 1 ? "agent" : "agents"}`}
            {project?.description ? ` · ${project.description}` : ""}
          </p>
        </div>
        <div className="fleet-toolbar">
          {inProject.length > 0 && (
            <select className="fleet-select" value={sort} onChange={(e) => setSort(e.currentTarget.value as SortMode)} aria-label="Sort">
              <option value="last_active">Last active</option>
              <option value="status">Status</option>
              <option value="cost">Cost</option>
              <option value="name">Name</option>
            </select>
          )}
          <button type="button" className="fleet-btn fleet-btn--accent" onClick={() => setWizardOpen(true)}>
            <span className="fleet-btn-plus">+</span> New agent
          </button>
        </div>
      </div>

      {rollup && (
        <div className="fleet-stat-grid">
          <div className="fleet-stat-card"><div className="fleet-stat-value">{money(rollup.usd_cost)}</div><div className="fleet-stat-label">Cost this month</div></div>
          <div className="fleet-stat-card"><div className="fleet-stat-value">{rollup.total_tokens.toLocaleString()}</div><div className="fleet-stat-label">Tokens</div></div>
          <div className="fleet-stat-card"><div className="fleet-stat-value">{rollup.events.toLocaleString()}</div><div className="fleet-stat-label">LLM calls</div></div>
        </div>
      )}

      {loading && inProject.length === 0 ? (
        <FleetListSkeleton rows={4} />
      ) : shown.length === 0 ? (
        <FirstAgentEmpty
          title="No agents in this project"
          desc="Create one — it’ll be assigned here."
          onCreate={() => setWizardOpen(true)}
        />
      ) : (
        <AgentsList agents={shown} costByAgent={cost} onSelect={goToAgent} />
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

function sortAgents(agents: FleetAgent[], sort: SortMode, cost: Map<string, number>): FleetAgent[] {
  const list = [...agents];
  if (sort === "name") {
    list.sort((a, b) => (a.label || "").localeCompare(b.label || ""));
  } else if (sort === "status") {
    list.sort((a, b) => (STATUS_RANK[a.hardware_status] ?? 1) - (STATUS_RANK[b.hardware_status] ?? 1));
  } else if (sort === "cost") {
    list.sort((a, b) => (cost.get(b.agent_id) || 0) - (cost.get(a.agent_id) || 0));
  } else {
    list.sort((a, b) => {
      const ta = a.last_activity ? new Date(a.last_activity).getTime() : 0;
      const tb = b.last_activity ? new Date(b.last_activity).getTime() : 0;
      return tb - ta;
    });
  }
  return list;
}

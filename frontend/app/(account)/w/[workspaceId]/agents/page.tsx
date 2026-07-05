"use client";

import { useEffect, useMemo, useState } from "react";
import { useParams, useRouter } from "next/navigation";

import { useFleetAgents, useFleetProjects } from "@/lib/workspace/fleet/fleet-data";
import { deriveStatus, statusClass } from "@/lib/workspace/fleet/fleet-presentation";

const money = (n: number) => `$${n.toFixed(4)}`;

export default function AgentsPage() {
  const params = useParams();
  const router = useRouter();
  const workspaceId = String(params?.workspaceId || "");
  const base = `/w/${encodeURIComponent(workspaceId)}`;

  const { agents, loading } = useFleetAgents(workspaceId);
  const { projects } = useFleetProjects(workspaceId);
  const [filter, setFilter] = useState<string>("all");
  const [cost, setCost] = useState<Map<string, number>>(new Map());

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

  const projName = useMemo(() => new Map(projects.map((p) => [p.id, p.name || p.id])), [projects]);
  const shown = filter === "all" ? agents : agents.filter((a) => (a.project_id || "") === filter);

  const goToAgent = (agentId: string, projectId: string) =>
    router.push(`${base}/projects/${encodeURIComponent(projectId)}/agents/${encodeURIComponent(agentId)}/overview`);

  return (
    <main className="fleet-content">
      <div className="fleet-header">
        <div>
          <h1 className="fleet-title">Agents</h1>
          <p className="fleet-subtitle">{loading ? "Loading…" : `${shown.length} of ${agents.length} agents`}</p>
        </div>
        <select className="fleet-select" value={filter} onChange={(e) => setFilter(e.currentTarget.value)} aria-label="Filter by project">
          <option value="all">All projects</option>
          {projects.map((p) => <option key={p.id} value={p.id}>{p.name || p.id}</option>)}
        </select>
      </div>

      <div className="fleet-list">
        {shown.map((a) => {
          const st = deriveStatus(a.hardware_status || "unknown");
          const preset = (a.capability_preset || "").toLowerCase();
          const c = cost.get(a.agent_id) || 0;
          return (
            <button key={a.agent_id} type="button" className="fleet-list-row" onClick={() => goToAgent(a.agent_id, a.project_id || "")}>
              <span className={`fleet-detail-dot ${statusClass(st.tone)}`} />
              <span className="fleet-list-row-main">
                <span className="fleet-list-row-title">{a.label}</span>
                <span className="fleet-list-row-desc">
                  {projName.get(a.project_id || "") || "Ungrouped"}
                  {preset ? ` · ${preset}` : ""}
                  {(a.role || "").toLowerCase() === "operator" ? " · operator" : ""}
                </span>
              </span>
              <span className="fleet-list-row-meta">{c > 0 ? money(c) : "—"}</span>
            </button>
          );
        })}
        {!loading && shown.length === 0 && <div className="fleet-page-state-body">No agents in this project.</div>}
      </div>
    </main>
  );
}

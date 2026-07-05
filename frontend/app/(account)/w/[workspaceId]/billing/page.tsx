"use client";

import { useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";

import { useFleetAgents, useFleetProjects } from "@/lib/workspace/fleet/fleet-data";

type ByAgent = { agent_install_id: string; events: number; total_tokens: number; usd_cost: number };
type UsageRollup = {
  ok: boolean;
  totals?: { events: number; tokens_in: number; tokens_out: number; total_tokens: number; usd_cost: number };
  by_agent?: ByAgent[];
  error?: string;
};

const PERIODS = ["day", "week", "month"] as const;
type Period = (typeof PERIODS)[number];

const money = (n: number | undefined) => `$${(n ?? 0).toFixed(4)}`;

export default function BillingPage() {
  const params = useParams();
  const workspaceId = String(params?.workspaceId || "");
  const [period, setPeriod] = useState<Period>("month");
  const [data, setData] = useState<UsageRollup | null>(null);
  const [loading, setLoading] = useState(true);

  const { agents } = useFleetAgents(workspaceId);
  const { projects } = useFleetProjects(workspaceId);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/usage?scope=workspace&period=${period}`, { credentials: "include" })
      .then((r) => r.json())
      .then((d) => { if (!cancelled) setData(d); })
      .catch(() => { if (!cancelled) setData({ ok: false, error: "Could not load usage" }); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [workspaceId, period]);

  const totals = data?.totals;

  // Group per-agent cost under its project → the "who is spending what" view.
  const grouped = useMemo(() => {
    const byAgent = new Map((data?.by_agent || []).map((a) => [a.agent_install_id, a]));
    const agentProject = new Map(agents.map((a) => [a.agent_id, a.project_id || ""]));
    const agentLabel = new Map(agents.map((a) => [a.agent_id, a.label]));
    const projName = new Map(projects.map((p) => [p.id, p.name || p.id]));
    const rows = new Map<string, { name: string; cost: number; tokens: number; agents: { id: string; label: string; cost: number; tokens: number }[] }>();
    for (const [aid, u] of byAgent) {
      const pid = agentProject.get(aid) || "ungrouped";
      if (!rows.has(pid)) rows.set(pid, { name: projName.get(pid) || (pid === "ungrouped" ? "Ungrouped" : pid), cost: 0, tokens: 0, agents: [] });
      const row = rows.get(pid)!;
      row.cost += u.usd_cost; row.tokens += u.total_tokens;
      row.agents.push({ id: aid, label: agentLabel.get(aid) || aid, cost: u.usd_cost, tokens: u.total_tokens });
    }
    return Array.from(rows.values()).sort((a, b) => b.cost - a.cost);
  }, [data, agents, projects]);

  return (
    <main className="fleet-content">
      <div className="fleet-header">
        <div>
          <h1 className="fleet-title">Billing</h1>
          <p className="fleet-subtitle">What your agents cost — by project, then by agent. Real usage, honestly.</p>
        </div>
        <div className="fleet-segmented">
          {PERIODS.map((p) => (
            <button key={p} type="button" className={`fleet-segmented-btn${period === p ? " fleet-segmented-btn--active" : ""}`} onClick={() => setPeriod(p)}>
              {p[0].toUpperCase() + p.slice(1)}
            </button>
          ))}
        </div>
      </div>

      {loading && <div className="fleet-page-state-body">Loading usage…</div>}
      {!loading && data && !data.ok && <div className="fleet-page-state-body">{data.error || "No usage data."}</div>}

      {!loading && totals && (
        <>
          <div className="fleet-stat-grid">
            <div className="fleet-stat-card">
              <div className="fleet-stat-value">{money(totals.usd_cost)}</div>
              <div className="fleet-stat-label">Total this {period}</div>
            </div>
            <div className="fleet-stat-card">
              <div className="fleet-stat-value">{totals.total_tokens.toLocaleString()}</div>
              <div className="fleet-stat-label">Tokens</div>
            </div>
            <div className="fleet-stat-card">
              <div className="fleet-stat-value">{totals.events.toLocaleString()}</div>
              <div className="fleet-stat-label">LLM calls</div>
            </div>
          </div>

          {grouped.length === 0 ? (
            <div className="fleet-empty" style={{ marginTop: "var(--space-5)" }}>
              <div className="fleet-empty-title">No usage in this period</div>
              <div className="fleet-empty-desc">Costs appear here as your agents do work.</div>
            </div>
          ) : (
            grouped.map((g) => (
              <div key={g.name} className="fleet-bill-group">
                <div className="fleet-bill-group-head">
                  <span className="fleet-bill-group-name">{g.name}</span>
                  <span className="fleet-bill-group-cost">{money(g.cost)}</span>
                </div>
                <div className="fleet-list">
                  {g.agents.sort((a, b) => b.cost - a.cost).map((a) => (
                    <div key={a.id} className="fleet-list-row">
                      <span className="fleet-list-row-main">
                        <span className="fleet-list-row-title">{a.label}</span>
                        <span className="fleet-list-row-desc">{a.tokens.toLocaleString()} tokens</span>
                      </span>
                      <span className="fleet-list-row-meta">{money(a.cost)}</span>
                    </div>
                  ))}
                </div>
              </div>
            ))
          )}
        </>
      )}
    </main>
  );
}

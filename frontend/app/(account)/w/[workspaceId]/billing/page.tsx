"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";

type UsageRollup = {
  ok: boolean;
  totals?: { events: number; tokens_in: number; tokens_out: number; total_tokens: number; usd_cost: number };
  by_agent?: { agent_install_id: string; events: number; total_tokens: number; usd_cost: number }[];
  error?: string;
};

const PERIODS = ["day", "week", "month"] as const;
type Period = (typeof PERIODS)[number];

export default function BillingPage() {
  const params = useParams();
  const workspaceId = String(params?.workspaceId || "");
  const [period, setPeriod] = useState<Period>("month");
  const [data, setData] = useState<UsageRollup | null>(null);
  const [loading, setLoading] = useState(true);

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
  const money = (n: number | undefined) => `$${(n ?? 0).toFixed(4)}`;

  return (
    <main className="fleet-content">
      <div className="fleet-header">
        <div>
          <h1 className="fleet-title">Billing</h1>
          <p className="fleet-subtitle">Usage and cost across the workspace</p>
        </div>
        <div className="fleet-segmented">
          {PERIODS.map((p) => (
            <button
              key={p}
              type="button"
              className={`fleet-segmented-btn${period === p ? " fleet-segmented-btn--active" : ""}`}
              onClick={() => setPeriod(p)}
            >
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
              <div className="fleet-stat-label">Cost this {period}</div>
            </div>
            <div className="fleet-stat-card">
              <div className="fleet-stat-value">{totals.total_tokens.toLocaleString()}</div>
              <div className="fleet-stat-label">Total tokens</div>
            </div>
            <div className="fleet-stat-card">
              <div className="fleet-stat-value">{totals.events.toLocaleString()}</div>
              <div className="fleet-stat-label">LLM calls</div>
            </div>
          </div>

          {data?.by_agent && data.by_agent.length > 0 && (
            <div className="fleet-home-activity">
              <div className="fleet-detail-section-title">By agent</div>
              <div className="fleet-list">
                {data.by_agent.map((a) => (
                  <div key={a.agent_install_id} className="fleet-list-row">
                    <span className="fleet-list-row-main">
                      <span className="fleet-list-row-title">{a.agent_install_id}</span>
                      <span className="fleet-list-row-desc">{a.total_tokens.toLocaleString()} tokens · {a.events} calls</span>
                    </span>
                    <span className="fleet-list-row-meta">{money(a.usd_cost)}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </main>
  );
}

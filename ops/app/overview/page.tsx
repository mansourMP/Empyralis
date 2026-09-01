"use client";

import { StatGrid } from "@/lib/components/StatGrid";
import { ErrorState, ForbiddenState, LoadingState, SignedOutState } from "@/lib/components/PageStates";
import { formatTimestamp } from "@/lib/format";
import { agentComputerStatusBreakdown, overviewDetailStats, overviewSummaryStats, type OperatorOverview } from "@/lib/overview";
import { useOperatorResource } from "@/lib/use-operator-resource";
import { planOperatorView } from "@/lib/view-state";

export default function OverviewPage() {
  const { data, status, loading, error, refresh } = useOperatorResource<OperatorOverview>("/api/operator/overview");
  const view = planOperatorView({ loading, status, error, data });

  return (
    <main className="ops-main">
      <div className="ops-page-header">
        <div>
          <h1 className="ops-page-title">Overview</h1>
          <p className="ops-page-subtitle">
            Is anyone actually using the platform — signed-up totals plus the numbers that measure real activation.
          </p>
        </div>
        {view.kind === "ready" && data?.generated_at && (
          <span className="ops-page-meta">Generated {formatTimestamp(data.generated_at)}</span>
        )}
      </div>

      {view.kind === "loading" && <LoadingState rows={2} />}
      {view.kind === "signedOut" && <SignedOutState />}
      {view.kind === "forbidden" && <ForbiddenState />}
      {view.kind === "error" && <ErrorState message={view.message} onRetry={refresh} />}

      {view.kind === "ready" && (
        <>
          <StatGrid stats={overviewSummaryStats(view.data)} />

          <h2 className="ops-section-title">Platform totals</h2>
          <StatGrid stats={overviewDetailStats(view.data)} />

          <h2 className="ops-section-title">Agent Computers (VPS), by status</h2>
          {view.data.agent_computers.total === 0 ? (
            <p className="ops-page-subtitle">No Agent Computers provisioned yet.</p>
          ) : (
            <div className="ops-stat-grid">
              {agentComputerStatusBreakdown(view.data.agent_computers.by_status).map((row) => (
                <div className="ops-stat-card" key={row.status}>
                  <div className="ops-stat-value">{row.count}</div>
                  <div className="ops-stat-label">{row.status}</div>
                </div>
              ))}
            </div>
          )}
          <p className="ops-bar-row-caption" style={{ marginTop: "var(--space-2)" }}>
            {view.data.agent_computers.excludes}
          </p>
        </>
      )}
    </main>
  );
}

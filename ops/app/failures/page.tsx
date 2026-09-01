"use client";

import { Suspense, useMemo } from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { BarList, type BarListRow } from "@/lib/components/BarList";
import { EmptyState, ErrorState, ForbiddenState, LoadingState, SignedOutState } from "@/lib/components/PageStates";
import { FAILURES_DEFAULT_DAYS, formatFailureEvent, formatFailureGroups, parseFailuresDays, type OperatorFailures } from "@/lib/failures";
import { useOperatorResource } from "@/lib/use-operator-resource";
import { planOperatorView } from "@/lib/view-state";

const DAY_OPTIONS = [7, 30, 90];

export default function FailuresPage() {
  return (
    <Suspense fallback={<main className="ops-main"><LoadingState rows={1} /></main>}>
      <FailuresPageInner />
    </Suspense>
  );
}

function FailuresPageInner() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const days = useMemo(() => parseFailuresDays(searchParams.get("days")), [searchParams]);

  const { data, status, loading, error, refresh } = useOperatorResource<OperatorFailures>(`/api/operator/failures?days=${days}`);
  const view = planOperatorView({ loading, status, error, data });

  function setDays(next: number) {
    router.push(next === FAILURES_DEFAULT_DAYS ? pathname : `${pathname}?days=${next}`);
  }

  return (
    <main className="ops-main">
      <div className="ops-page-header">
        <div>
          <h1 className="ops-page-title">Failures</h1>
          <p className="ops-page-subtitle">
            Provider and tool failures grouped by status, error code, and domain — where a systemic breakage shows up
            before a customer has to report it.
          </p>
        </div>
        {view.kind === "ready" && (
          <span className="ops-page-meta">{view.data.total_failures.toLocaleString("en-US")} in the window</span>
        )}
      </div>

      <div className="ops-filters">
        <span className="ops-filter-label">Window</span>
        {DAY_OPTIONS.map((opt) => (
          <button
            key={opt}
            type="button"
            className="ops-btn"
            aria-current={days === opt ? "true" : undefined}
            style={days === opt ? { borderColor: "var(--text-primary)", fontWeight: "var(--weight-semibold)" } : undefined}
            onClick={() => setDays(opt)}
          >
            {opt}d
          </button>
        ))}
      </div>

      {view.kind === "loading" && <LoadingState rows={1} />}
      {view.kind === "signedOut" && <SignedOutState />}
      {view.kind === "forbidden" && <ForbiddenState />}
      {view.kind === "error" && <ErrorState message={view.message} onRetry={refresh} />}

      {view.kind === "ready" && <FailuresView failures={view.data} />}
    </main>
  );
}

function FailuresView({ failures }: { failures: OperatorFailures }) {
  const groups = formatFailureGroups(failures.grouped);
  const rows: BarListRow[] = groups.map((g, i) => ({
    key: `${g.domainLabel}-${g.codeLabel}-${i}`,
    label: `${g.domainLabel} · ${g.codeLabel} · ${g.status}`,
    value: g.eventCount,
    caption: `${g.distinctWorkspaces} workspace${g.distinctWorkspaces === "1" ? "" : "s"}, ${g.distinctAgents} agent${g.distinctAgents === "1" ? "" : "s"} · last seen ${g.lastSeen}`,
    barWidthPct: g.barWidthPct,
  }));

  return (
    <>
      <h2 className="ops-section-title">Grouped</h2>
      {rows.length === 0 ? (
        <EmptyState>No failures in this window.</EmptyState>
      ) : (
        <div className="ops-table-wrap" style={{ padding: "var(--space-2) var(--space-3)" }}>
          <BarList rows={rows} />
        </div>
      )}

      <h2 className="ops-section-title">Recent</h2>
      {failures.recent.length === 0 ? (
        <EmptyState>No recent failure events.</EmptyState>
      ) : (
        <div className="ops-table-wrap">
          <table className="ops-table">
            <thead>
              <tr>
                <th>When</th>
                <th>Workspace</th>
                <th>Domain</th>
                <th>Action</th>
                <th>Status</th>
                <th>Error code</th>
              </tr>
            </thead>
            <tbody>
              {failures.recent.map((event) => {
                const row = formatFailureEvent(event);
                return (
                  <tr key={row.id}>
                    <td className="ops-cell-muted">{row.createdAt}</td>
                    <td>
                      <Link className="ops-row-link" href={`/accounts/${encodeURIComponent(row.workspaceId)}`}>
                        {row.workspaceId}
                      </Link>
                    </td>
                    <td>{row.domainLabel}</td>
                    <td className="ops-cell-muted">{row.actionName}</td>
                    <td>{row.status}</td>
                    <td className="ops-cell-muted">{row.codeLabel}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

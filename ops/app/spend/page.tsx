"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useMemo } from "react";

import { BarList, type BarListRow } from "@/lib/components/BarList";
import { EmptyState, ErrorState, ForbiddenState, LoadingState } from "@/lib/components/PageStates";
import { SparkBars } from "@/lib/components/SparkBars";
import { formatUsd } from "@/lib/money";
import {
  formatSpendByProviderModel,
  formatSpendByWorkspace,
  formatSpendOverTime,
  parseSpendDays,
  SPEND_DEFAULT_DAYS,
  type OperatorSpend,
} from "@/lib/spend";
import { useOperatorResource } from "@/lib/use-operator-resource";
import { planOperatorView } from "@/lib/view-state";

const DAY_OPTIONS = [7, 30, 90];

export default function SpendPage() {
  return (
    <Suspense fallback={<main className="ops-main"><LoadingState rows={1} /></main>}>
      <SpendPageInner />
    </Suspense>
  );
}

function SpendPageInner() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const days = useMemo(() => parseSpendDays(searchParams.get("days")), [searchParams]);

  const { data, status, loading, error, refresh } = useOperatorResource<OperatorSpend>(`/api/operator/spend?days=${days}`);
  const view = planOperatorView({ loading, status, error, data });

  function setDays(next: number) {
    router.push(next === SPEND_DEFAULT_DAYS ? pathname : `${pathname}?days=${next}`);
  }

  return (
    <main className="ops-main">
      <div className="ops-page-header">
        <div>
          <h1 className="ops-page-title">Spend</h1>
          <p className="ops-page-subtitle">
            Platform cost by workspace, by provider/model, and over time — AI-token spend and Agent Computer (VPS)
            runtime, unified under one ledger.
          </p>
        </div>
        {view.kind === "ready" && (
          <span className="ops-page-meta">
            {formatUsd(view.data.total_platform_cost_usd)} across {view.data.total_events.toLocaleString("en-US")} events
          </span>
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
      {view.kind === "forbidden" && <ForbiddenState />}
      {view.kind === "error" && <ErrorState message={view.message} onRetry={refresh} />}

      {view.kind === "ready" && <SpendView spend={view.data} />}
    </main>
  );
}

function SpendView({ spend }: { spend: OperatorSpend }) {
  const byWorkspace = formatSpendByWorkspace(spend.by_workspace);
  const byProviderModel = formatSpendByProviderModel(spend.by_provider_model);
  const overTime = formatSpendOverTime(spend.over_time);

  const workspaceRows: BarListRow[] = byWorkspace.map((row) => ({
    key: row.workspaceId,
    label: (
      <Link className="ops-row-link" href={`/accounts/${encodeURIComponent(row.workspaceId)}`}>
        {row.name}
      </Link>
    ),
    value: row.platformCost,
    caption: `${row.eventCount} events · ${row.creditsDebited} credits`,
    barWidthPct: row.barWidthPct,
  }));

  const providerRows: BarListRow[] = byProviderModel.map((row, i) => ({
    key: `${row.label}-${i}`,
    label: row.label,
    value: row.platformCost,
    caption: `${row.eventCount} events · ${row.creditsDebited} credits`,
    barWidthPct: row.barWidthPct,
  }));

  return (
    <>
      <h2 className="ops-section-title">Cost per day</h2>
      {overTime.length === 0 ? <EmptyState>No spend in this window.</EmptyState> : <SparkBars days={overTime} />}

      <h2 className="ops-section-title">By workspace</h2>
      {workspaceRows.length === 0 ? (
        <EmptyState>No workspace spend in this window.</EmptyState>
      ) : (
        <div className="ops-table-wrap" style={{ padding: "var(--space-2) var(--space-3)" }}>
          <BarList rows={workspaceRows} />
        </div>
      )}

      <h2 className="ops-section-title">By provider / model</h2>
      {providerRows.length === 0 ? (
        <EmptyState>No provider spend in this window.</EmptyState>
      ) : (
        <div className="ops-table-wrap" style={{ padding: "var(--space-2) var(--space-3)" }}>
          <BarList rows={providerRows} />
        </div>
      )}
    </>
  );
}

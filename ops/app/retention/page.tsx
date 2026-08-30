"use client";

import { StatGrid } from "@/lib/components/StatGrid";
import { ErrorState, ForbiddenState, LoadingState } from "@/lib/components/PageStates";
import { retentionStats, type OperatorRetention } from "@/lib/retention";
import { useOperatorResource } from "@/lib/use-operator-resource";
import { planOperatorView } from "@/lib/view-state";

export default function RetentionPage() {
  const { data, status, loading, error, refresh } = useOperatorResource<OperatorRetention>("/api/operator/retention");
  const view = planOperatorView({ loading, status, error, data });

  return (
    <main className="ops-main">
      <div className="ops-page-header">
        <div>
          <h1 className="ops-page-title">Retention</h1>
          <p className="ops-page-subtitle">
            Who is still coming back — active in the last day, week, and month, from auth_sessions.last_seen_at, touched
            on every authenticated request.
          </p>
        </div>
      </div>

      {view.kind === "loading" && <LoadingState rows={1} />}
      {view.kind === "forbidden" && <ForbiddenState />}
      {view.kind === "error" && <ErrorState message={view.message} onRetry={refresh} />}

      {view.kind === "ready" && <StatGrid stats={retentionStats(view.data)} />}
    </main>
  );
}

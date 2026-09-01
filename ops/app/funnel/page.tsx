"use client";

import { BarList, type BarListRow } from "@/lib/components/BarList";
import { ErrorState, ForbiddenState, LoadingState, SignedOutState } from "@/lib/components/PageStates";
import { funnelBars, type ActivationFunnel } from "@/lib/funnel";
import { useOperatorResource } from "@/lib/use-operator-resource";
import { planOperatorView } from "@/lib/view-state";

export default function FunnelPage() {
  const { data, status, loading, error, refresh } = useOperatorResource<ActivationFunnel>("/api/operator/activation-funnel");
  const view = planOperatorView({ loading, status, error, data });

  return (
    <main className="ops-main">
      <div className="ops-page-header">
        <div>
          <h1 className="ops-page-title">Activation funnel</h1>
          <p className="ops-page-subtitle">
            Signed up → created a project → created a task or document → installed an agent → an agent actually ran →
            invited a second member. Where the platform loses people.
          </p>
        </div>
      </div>

      {view.kind === "loading" && <LoadingState rows={1} />}
      {view.kind === "signedOut" && <SignedOutState />}
      {view.kind === "forbidden" && <ForbiddenState />}
      {view.kind === "error" && <ErrorState message={view.message} onRetry={refresh} />}

      {view.kind === "ready" && <FunnelView funnel={view.data} />}
    </main>
  );
}

function FunnelView({ funnel }: { funnel: ActivationFunnel }) {
  const bars = funnelBars(funnel.steps);
  const rows: BarListRow[] = bars.map((bar) => ({
    key: bar.step,
    label: bar.label,
    value: `${bar.count.toLocaleString("en-US")} (${bar.pct_of_signups}%)`,
    caption:
      bar.step === "signup"
        ? null
        : `${bar.drop_off_from_previous.toLocaleString("en-US")} dropped off from the previous step (${bar.drop_off_pct_from_previous}%)`,
    barWidthPct: bar.barWidthPct,
    flagged: bar.isBiggestDropOff,
  }));

  return (
    <div className="ops-table-wrap" style={{ padding: "var(--space-2) var(--space-3)" }}>
      <BarList rows={rows} />
    </div>
  );
}

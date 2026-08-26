"use client";

import { useMemo, useState } from "react";
import { Coins } from "lucide-react";

import { useCreditBalance, useCreditUsageHistory, startCreditTopUp, type CreditUsageHistoryItem } from "./credit-balance";
import { useBillingSummary, planUpgradeControl, resolvePlanDisplayLabel, startPlanCheckout, startPortalSession } from "./billing-plan";
import { MultiSeriesChart, type ChartSeries } from "./fleet-sparkline";
import { FleetSurfaceError } from "./fleet-states";
import { formatUsd } from "../../ui/money";

// Every preset must be >= the server's own floor
// (billing_service._MIN_CREDIT_PURCHASE_USD, $10). A $5 button that the
// backend answers 400 to is a dead control -- it renders, it is clickable,
// and it can only fail. The floor is $10 because Polar charges a fixed 50c
// plus 5% per transaction, so anything smaller loses most of itself to fees.
const TOP_UP_PRESETS_USD = [10, 25, 50];

function dateKey(iso: string | null): string {
  return (iso || "").slice(0, 10);
}

function lastNDays(n: number): string[] {
  const out: string[] = [];
  const now = new Date();
  for (let i = n - 1; i >= 0; i--) {
    const d = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() - i));
    out.push(d.toISOString().slice(0, 10));
  }
  return out;
}

function dayLabel(key: string): string {
  const d = new Date(`${key}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return key;
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
}

// Category grouping for the debit side of credit_transactions. "AI chat" is
// the direct per-turn debit this panel reconnects. "Hardware" is MAN-134's
// Agent Computer hourly meter (agent_computer_metering_service.py, source
// "agent_computer_hourly" — see control_plane_repository.debit_workspace_
// credits_for_turn_atomic's `source` kwarg for how a transaction gets
// stamped with this instead of the AI-chat default). Media (image/video)
// genuinely has no meter behind it yet — shown honestly at zero, per the
// platform's "no fake coming-soon" rule, rather than being omitted or faked.
function categoryForItem(item: CreditUsageHistoryItem): "AI chat" | "Hardware" | "Other" {
  const source = String(item.source || "").toLowerCase();
  if (source === "hosted_sage_ai_turn" || source === "hosted_sage_ai") return "AI chat";
  if (source === "agent_computer_hourly") return "Hardware";
  return "Other";
}

export function CreditsPanel({ workspaceId }: { workspaceId: string }) {
  const { balance, loading: balanceLoading, error: balanceError } = useCreditBalance(workspaceId);
  // `error` was already returned by this hook and simply never read here —
  // this component genuinely could not tell "spent nothing" from "the fetch
  // failed" because it had discarded the one field that says which. Both
  // rendered as "No credit usage yet" on a screen whose whole job is to show
  // money — a customer who WAS charged had no way to tell their history
  // failed to load versus actually being unbilled.
  const { history, loading: historyLoading, error: historyError, refresh: refreshHistory } = useCreditUsageHistory(workspaceId, 200);
  const { summary: billingSummary } = useBillingSummary(workspaceId);

  const [amountUsd, setAmountUsd] = useState<number>(TOP_UP_PRESETS_USD[0]);
  const [topUpState, setTopUpState] = useState<"idle" | "starting" | "not_configured" | "error">("idle");
  const [topUpMessage, setTopUpMessage] = useState<string | null>(null);
  const [planActionState, setPlanActionState] = useState<"idle" | "starting" | "not_configured" | "error">("idle");
  const [planActionMessage, setPlanActionMessage] = useState<string | null>(null);

  const credits = balance?.credit_balance_credits ?? null;
  const balanceUsd = balance?.credit_balance_usd ?? null;
  const hostedSageAi = (history?.hosted_sage_ai as Record<string, unknown> | undefined) || {};
  // Outcome-honesty fix (2026-08-20): every fresh workspace's entitlement
  // tier is internally "pro" (a generous unpaid grant, see
  // billing_service.py), so this must read the honest display label — not
  // the raw plan label — or an account that never paid shows "Pro".
  const planLabel = resolvePlanDisplayLabel(billingSummary?.subscription);
  const monthlyCreditCap = typeof hostedSageAi.monthly_credit_cap === "number" ? hostedSageAi.monthly_credit_cap : null;
  const upgradeControl = planUpgradeControl(billingSummary);

  const debitItems = useMemo(
    () => (history?.items || []).filter((item) => item.kind === "usage_debit"),
    [history],
  );

  const days = useMemo(() => lastNDays(14), []);
  const dailySeries: ChartSeries[] = useMemo(() => {
    const byDate = new Map<string, number>();
    for (const item of debitItems) {
      const key = dateKey(item.created_at);
      byDate.set(key, (byDate.get(key) || 0) + Math.abs(item.credits || 0));
    }
    return [
      {
        key: "credits",
        color: "var(--text-secondary)",
        values: days.map((d) => byDate.get(d) || 0),
      },
    ];
  }, [debitItems, days]);

  const categoryTotals = useMemo(() => {
    const totals = { "AI chat": 0, Hardware: 0 };
    for (const item of debitItems) {
      const category = categoryForItem(item);
      if (category === "AI chat") totals["AI chat"] += Math.abs(item.credits || 0);
      else if (category === "Hardware") totals.Hardware += Math.abs(item.credits || 0);
    }
    return totals;
  }, [debitItems]);

  const totalUsed14d = useMemo(
    () => dailySeries[0]?.values.reduce((a, b) => a + b, 0) ?? 0,
    [dailySeries],
  );
  const hasAnyDebit = totalUsed14d > 0;

  const handleTopUp = async () => {
    setTopUpState("starting");
    setTopUpMessage(null);
    const result = await startCreditTopUp(workspaceId, amountUsd);
    if (result.ok) {
      window.location.href = result.checkoutUrl;
      return;
    }
    if (result.notConfigured) {
      setTopUpState("not_configured");
      return;
    }
    setTopUpState("error");
    setTopUpMessage(result.message);
  };

  const handlePlanAction = async () => {
    setPlanActionState("starting");
    setPlanActionMessage(null);
    const result =
      upgradeControl.kind === "upgrade"
        ? await startPlanCheckout(workspaceId, upgradeControl.planId)
        : upgradeControl.kind === "manage"
          ? await startPortalSession(workspaceId)
          : null;
    if (!result) return;
    const url = "checkoutUrl" in result ? result.checkoutUrl : "portalUrl" in result ? result.portalUrl : null;
    if (result.ok && url) {
      window.location.href = url;
      return;
    }
    if (!result.ok && result.notConfigured) {
      setPlanActionState("not_configured");
      return;
    }
    setPlanActionState("error");
    setPlanActionMessage(!result.ok ? result.message : null);
  };

  return (
    <section className="fleet-credits-panel">
      <div className="fleet-stat-grid">
        <div className="fleet-stat-card fleet-stat-card--credits">
          <div className="fleet-stat-card-icon">
            <Coins size={16} strokeWidth={1.75} />
          </div>
          <div>
            <div className="fleet-stat-value fleet-stat-value--credits">
              {balanceLoading || credits === null ? "…" : credits.toLocaleString("en-US")}
            </div>
            <div className="fleet-stat-label">
              Credit balance{typeof balanceUsd === "number" ? ` · ${formatUsd(balanceUsd)}` : ""}
            </div>
          </div>
        </div>
        <div className="fleet-stat-card">
          <div className="fleet-stat-value">{planLabel || "—"}</div>
          <div className="fleet-stat-label">Plan</div>
        </div>
        <div className="fleet-stat-card">
          <div className="fleet-stat-value">
            {monthlyCreditCap === null ? "—" : monthlyCreditCap.toLocaleString("en-US")}
          </div>
          <div className="fleet-stat-label">Monthly allowance (credits)</div>
        </div>
      </div>

      {balanceError && <div className="fleet-credits-error">Could not load your credit balance.</div>}

      <div className="fleet-credits-topup">
        <div className="fleet-credits-topup-amounts" role="group" aria-label="Top-up amount">
          {TOP_UP_PRESETS_USD.map((preset) => (
            <button
              key={preset}
              type="button"
              className={`fleet-segmented-btn${amountUsd === preset ? " fleet-segmented-btn--active" : ""}`}
              onClick={() => setAmountUsd(preset)}
            >
              ${preset}
            </button>
          ))}
        </div>
        <button
          type="button"
          className="fleet-btn fleet-btn--mono"
          disabled={topUpState === "starting"}
          onClick={() => { void handleTopUp(); }}
        >
          {topUpState === "starting" ? "Starting…" : `Top up $${amountUsd}`}
        </button>
        {topUpState === "not_configured" && (
          <span className="fleet-credits-topup-note">
            Top-up isn't configured yet — the platform owner needs to connect Polar.
          </span>
        )}
        {topUpState === "error" && topUpMessage && (
          <span className="fleet-credits-topup-note fleet-credits-topup-note--error">{topUpMessage}</span>
        )}
      </div>

      {upgradeControl.kind !== "none" && (
        <div className="fleet-credits-topup" style={{ marginTop: "var(--space-3)" }}>
          <button
            type="button"
            className="fleet-btn fleet-btn--mono"
            disabled={planActionState === "starting"}
            onClick={() => { void handlePlanAction(); }}
          >
            {planActionState === "starting"
              ? "Starting…"
              : upgradeControl.kind === "manage"
                ? "Manage subscription"
                : "Upgrade to Pro"}
          </button>
          {planActionState === "not_configured" && (
            <span className="fleet-credits-topup-note">
              {upgradeControl.kind === "manage"
                ? "Subscription management isn't configured yet."
                : "Upgrading isn't configured yet — the platform owner needs to finish setting up Polar."}
            </span>
          )}
          {planActionState === "error" && planActionMessage && (
            <span className="fleet-credits-topup-note fleet-credits-topup-note--error">{planActionMessage}</span>
          )}
        </div>
      )}

      <div className="fleet-usage-chart-block" style={{ marginTop: "var(--space-5)" }}>
        <div className="fleet-usage-chart-title">Credits used · last 14d</div>
        {!historyLoading && historyError ? (
          // "Empty" and "could not load" are different facts and must never
          // share a screen — sharpest on a billing surface, where the wrong
          // one reads as "you weren't charged" when the truth is "we don't
          // know yet."
          <FleetSurfaceError
            title="Couldn't load usage history"
            message={historyError}
            onRetry={() => { void refreshHistory(); }}
          />
        ) : !historyLoading && !hasAnyDebit ? (
          <div className="fleet-empty" style={{ marginTop: "var(--space-3)" }}>
            <div className="fleet-empty-icon">
              <Coins size={20} strokeWidth={1.75} />
            </div>
            <div className="fleet-empty-title">No credit usage yet</div>
            <div className="fleet-empty-desc">Credits are used as your agents chat with hosted AI.</div>
          </div>
        ) : (
          <>
            <MultiSeriesChart series={dailySeries} height={120} />
            <div className="fleet-usage-chart-axis">
              <span>{dayLabel(days[0])}</span>
              <span>{dayLabel(days[days.length - 1])}</span>
            </div>
          </>
        )}
      </div>

      <div className="fleet-usage-legend fleet-usage-legend--credits" style={{ marginTop: "var(--space-4)" }}>
        <div className="fleet-usage-legend-header" aria-hidden>
          <span>Category</span>
          <span className="is-right">Credits used · 14d</span>
        </div>
        <div className="fleet-usage-legend-row">
          <span className="fleet-usage-legend-name">AI chat</span>
          <span className="fleet-agent-cell-right">{categoryTotals["AI chat"].toLocaleString("en-US")}</span>
        </div>
        <div className="fleet-usage-legend-row">
          <span className="fleet-usage-legend-name">Hardware</span>
          <span className="fleet-agent-cell-right">{categoryTotals.Hardware.toLocaleString("en-US")}</span>
        </div>
        <div className="fleet-usage-legend-row fleet-usage-legend-row--muted">
          <span className="fleet-usage-legend-name">Media (image / video)</span>
          <span className="fleet-agent-cell-right">Not metered yet</span>
        </div>
      </div>
    </section>
  );
}

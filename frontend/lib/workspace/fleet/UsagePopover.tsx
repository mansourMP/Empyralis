"use client";

import { useMemo } from "react";
import Link from "next/link";

import {
  creditDayLabel,
  dailyCreditSpend,
  useCreditBalance,
  useCreditUsageHistory,
} from "./credit-balance";

/** Matches the window CreditsPanel charts on the Billing page, and the 200-row
 *  ceiling the usage-history route enforces (routes_billing.py). */
const WINDOW_DAYS = 14;
const HISTORY_LIMIT = 200;

/**
 * The Usage icon-button's popover — the API-console pattern (DeepSeek/OpenAI/
 * Anthropic developer consoles): balance headline, a bar per day over the
 * recent window, one quiet link out to the full page. Replaces a plain
 * <Link> that threw the reader off the list page entirely; the other two
 * controls in this cluster act in place on the right-hand side and this one
 * now does too.
 *
 * Mounted ONLY while the popover is open, which is what keeps its two fetches
 * off every list-page load — the hooks live here, not in FleetToolbar.
 *
 * Reuses the Billing page's own data (useCreditBalance +
 * useCreditUsageHistory); no new endpoint. Bars are neutral by construction:
 * the accent belongs to the view's single primary action ("New agent" /
 * "New task"), which is on screen at the same time.
 */
export function UsagePopover({ workspaceId }: { workspaceId: string }) {
  const { balance, loading: balanceLoading, error: balanceError } = useCreditBalance(workspaceId);
  const { history, loading: historyLoading, error: historyError } = useCreditUsageHistory(
    workspaceId,
    HISTORY_LIMIT,
  );

  const days = useMemo(() => dailyCreditSpend(history, WINDOW_DAYS), [history]);
  const spent = useMemo(() => days.reduce((sum, d) => sum + d.credits, 0), [days]);
  const peak = useMemo(() => Math.max(0, ...days.map((d) => d.credits)), [days]);

  const credits = balance?.credit_balance_credits ?? null;
  const billingHref = `/w/${encodeURIComponent(workspaceId)}/billing`;

  return (
    <div className="fleet-toolbar-popover fleet-usage-popover">
      <div className="fleet-usage-popover-head">
        <div className="fleet-usage-popover-value">
          {balanceError ? "—" : balanceLoading || credits === null ? "…" : credits.toLocaleString("en-US")}
        </div>
        <div className="fleet-usage-popover-caption">credits left</div>
      </div>

      {/* Three honest outcomes and no fourth: a real chart, a one-line "nothing
          happened", or a one-line "couldn't load". A workspace with zero usage
          gets no empty chart frame, and a failed fetch never leaves a spinner
          behind — same fail-quiet contract as CreditBalanceChip. */}
      {historyError ? (
        <div className="fleet-usage-popover-note">Usage couldn’t load.</div>
      ) : historyLoading ? (
        <div className="fleet-usage-popover-note">Loading usage…</div>
      ) : spent <= 0 ? (
        <div className="fleet-usage-popover-note">No credits used in the last {WINDOW_DAYS} days.</div>
      ) : (
        <div className="fleet-usage-popover-chart">
          <div className="fleet-usage-popover-summary">
            <span className="fleet-usage-popover-summary-value">{spent.toLocaleString("en-US")}</span> used ·{" "}
            {WINDOW_DAYS}d
          </div>
          <div
            className="fleet-usage-popover-bars"
            role="img"
            aria-label={`Credits used per day over the last ${WINDOW_DAYS} days: ${days
              .map((d) => `${creditDayLabel(d.date)} ${d.credits}`)
              .join(", ")}`}
          >
            {days.map((d) => (
              <div
                key={d.date}
                className="fleet-usage-popover-bar-track"
                title={`${creditDayLabel(d.date)} · ${d.credits.toLocaleString("en-US")} credits`}
              >
                <div
                  className={`fleet-usage-popover-bar${d.credits > 0 ? "" : " is-empty"}`}
                  style={
                    d.credits > 0
                      ? { height: `${Math.max(10, Math.round((d.credits / peak) * 100))}%` }
                      : undefined
                  }
                />
              </div>
            ))}
          </div>
          <div className="fleet-usage-popover-axis">
            <span>{creditDayLabel(days[0].date)}</span>
            <span>{creditDayLabel(days[days.length - 1].date)}</span>
          </div>
        </div>
      )}

      <Link href={billingHref} className="fleet-usage-popover-link">
        Open billing
      </Link>
    </div>
  );
}

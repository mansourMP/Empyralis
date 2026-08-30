/**
 * Pure shaping for `/spend` -- by workspace, by provider/model, over time.
 * Backed by `GET /api/internal/operator/spend`
 * (server_modules/operator_console_service.build_spend).
 *
 * No charting library (production runs on a 1 vCPU / ~1GB box) -- the
 * over-time series renders as hand-rolled CSS bars, sized here relative to
 * the series' own peak day, the same technique failures.ts and funnel.ts
 * already use for their bars.
 *
 * Run: npx tsx lib/spend.test.ts
 */

import { formatCount, spendSourceLabel } from "./format";
import { formatCredits, formatUsd } from "./money";

export const SPEND_MIN_DAYS = 1;
export const SPEND_MAX_DAYS = 365; // mirrors routes_operator_console.py's Query(..., le=365)
export const SPEND_DEFAULT_DAYS = 30;

export function parseSpendDays(raw: string | null): number {
  const n = Number.parseInt(raw ?? "", 10);
  if (!Number.isFinite(n)) return SPEND_DEFAULT_DAYS;
  return Math.max(SPEND_MIN_DAYS, Math.min(n, SPEND_MAX_DAYS));
}

export type SpendByWorkspace = {
  workspace_id: string;
  name: string;
  total_platform_cost_usd: number;
  total_credits_debited: number;
  event_count: number;
};

export type SpendByProviderModel = {
  provider: string | null;
  model: string | null;
  credit_type: string | null;
  total_platform_cost_usd: number;
  total_credits_debited: number;
  event_count: number;
};

export type SpendOverTimeDay = {
  day: string;
  total_platform_cost_usd: number;
  total_credits_debited: number;
  event_count: number;
};

export type OperatorSpend = {
  generated_at?: string | null;
  rls_bypass_verified?: boolean;
  window_days: number;
  total_platform_cost_usd: number;
  total_credits_debited: number;
  total_events: number;
  by_workspace: SpendByWorkspace[];
  by_provider_model: SpendByProviderModel[];
  over_time: SpendOverTimeDay[];
};

export type FormattedSpendByWorkspace = {
  workspaceId: string;
  name: string;
  platformCost: string;
  creditsDebited: string;
  eventCount: string;
  barWidthPct: number;
};

/** Sorted by spend descending -- defensively re-sorted rather than trusted
 *  from the wire, same reasoning as failures.ts. */
export function formatSpendByWorkspace(rows: SpendByWorkspace[]): FormattedSpendByWorkspace[] {
  const sorted = [...rows].sort((a, b) => b.total_platform_cost_usd - a.total_platform_cost_usd || a.name.localeCompare(b.name));
  const max = sorted.reduce((m, r) => Math.max(m, r.total_platform_cost_usd), 0);
  return sorted.map((r) => ({
    workspaceId: r.workspace_id,
    name: r.name || r.workspace_id,
    platformCost: formatUsd(r.total_platform_cost_usd),
    creditsDebited: formatCredits(r.total_credits_debited),
    eventCount: formatCount(r.event_count),
    barWidthPct: max > 0 ? Math.max(0, Math.min(100, (r.total_platform_cost_usd / max) * 100)) : 0,
  }));
}

export type FormattedSpendByProviderModel = {
  label: string;
  platformCost: string;
  creditsDebited: string;
  eventCount: string;
  barWidthPct: number;
};

export function formatSpendByProviderModel(rows: SpendByProviderModel[]): FormattedSpendByProviderModel[] {
  const sorted = [...rows].sort((a, b) => b.total_platform_cost_usd - a.total_platform_cost_usd);
  const max = sorted.reduce((m, r) => Math.max(m, r.total_platform_cost_usd), 0);
  return sorted.map((r) => ({
    label: spendSourceLabel(r),
    platformCost: formatUsd(r.total_platform_cost_usd),
    creditsDebited: formatCredits(r.total_credits_debited),
    eventCount: formatCount(r.event_count),
    barWidthPct: max > 0 ? Math.max(0, Math.min(100, (r.total_platform_cost_usd / max) * 100)) : 0,
  }));
}

export type FormattedSpendDay = {
  day: string;
  dayLabel: string;
  platformCost: string;
  barHeightPct: number;
};

/** `day` arrives as a full ISO timestamp (`date_trunc('day', ...)`) --
 *  sliced to the calendar date for a compact axis label rather than run
 *  through the locale-aware formatTimestamp, which would print a time that
 *  is always midnight and therefore noise on every single bar. */
function shortDayLabel(day: string): string {
  const datePart = day.slice(0, 10);
  const parsed = new Date(`${datePart}T00:00:00Z`);
  if (Number.isNaN(parsed.getTime())) return datePart;
  return parsed.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
}

/** Height relative to the single biggest day in the series -- the same
 *  hand-rolled-bar approach as the rest of this module, oriented vertically
 *  for a day-over-day strip instead of a horizontal ranked list. Already in
 *  chronological order from the backend's own `ORDER BY day ASC`; not
 *  re-sorted here because re-sorting a time series by anything but time
 *  would misrepresent it. */
export function formatSpendOverTime(days: SpendOverTimeDay[]): FormattedSpendDay[] {
  const max = days.reduce((m, d) => Math.max(m, d.total_platform_cost_usd), 0);
  return days.map((d) => ({
    day: d.day,
    dayLabel: shortDayLabel(d.day),
    platformCost: formatUsd(d.total_platform_cost_usd),
    barHeightPct: max > 0 ? Math.max(0, Math.min(100, (d.total_platform_cost_usd / max) * 100)) : 0,
  }));
}

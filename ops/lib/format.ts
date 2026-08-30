/**
 * Small display-formatting rules shared across every operator-console view.
 * Pure, no React, no CSS import -- same discipline as
 * frontend/lib/workspace/fleet/work-ledger.ts.
 *
 * Run: npx tsx lib/format.test.ts
 */

/** A single stat tile, shared by every view that renders one (overview.ts,
 *  retention.ts, ...) -- one shape instead of each file inventing its own,
 *  so a page that renders a mix of stats from two views doesn't need two
 *  parallel types for what is visually the same component. `isActivation`
 *  is the one signal a page's styling reads to draw the "signed up" vs
 *  "actually did something" distinction (CLAUDE.md) -- never re-derived
 *  from the label text. */
export type DisplayStat = {
  key: string;
  label: string;
  value: string;
  caption: string | null;
  isActivation: boolean;
};

export function formatCount(value: number | null | undefined): string {
  const n = typeof value === "number" && Number.isFinite(value) ? value : 0;
  return n.toLocaleString("en-US");
}

/** One decimal place, `%` suffix, never `NaN%`/`Infinity%`. The backend
 *  already rounds to one decimal (operator_console_service._pct); this just
 *  guards a malformed/missing value rather than trusting the wire. */
export function formatPercent(pct: number | null | undefined): string {
  const n = typeof pct === "number" && Number.isFinite(pct) ? pct : 0;
  return `${Math.round(n * 10) / 10}%`;
}

/** `null`/absent reads as "never" -- distinct from a formatting failure,
 *  which reads as "unknown". Two different facts (CLAUDE.md): a member who
 *  has never signed in and a timestamp this function could not parse are
 *  not the same thing, and a reader would act on them differently. */
export function formatTimestamp(value: string | null | undefined, opts?: { never?: string }): string {
  if (!value) return opts?.never ?? "Never";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "Unknown";
  return parsed.toLocaleString("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

export function formatDate(value: string | null | undefined, opts?: { never?: string }): string {
  if (!value) return opts?.never ?? "Never";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "Unknown";
  return parsed.toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" });
}

export type CountEntry = { key: string; count: number };

/** Highest count first; a tie breaks alphabetically on the key so a repeated
 *  render of the same data never visibly reshuffles ties (object key order
 *  from a `jsonb_object_agg`/`GROUP BY` on the backend carries no guarantee
 *  either way). Shared by every view that renders a status/outcome
 *  breakdown -- overview.ts's agent-computer statuses, account-detail.ts's
 *  run outcomes -- so there is one sort rule instead of two that could
 *  quietly diverge. */
export function countEntriesSorted(record: Record<string, number>): CountEntry[] {
  return Object.entries(record)
    .map(([key, count]) => ({ key, count: typeof count === "number" && Number.isFinite(count) ? count : 0 }))
    .sort((a, b) => b.count - a.count || a.key.localeCompare(b.key));
}

/** `provider`/`model` are NULL on `computer_runtime` rows -- credit_ledger_
 *  events unifies AI-token spend and Agent-Computer (VPS) hardware spend
 *  under one ledger, and provider/model are simply meaningless for a VPS-
 *  runtime row (operator_console_service.py's module docstring). Shared by
 *  account-detail.ts's per-account spend breakdown and spend.ts's platform-
 *  wide by-provider-model table so the two never independently decide two
 *  different labels for the same NULL-provider row. */
export function spendSourceLabel(row: { provider: string | null; model: string | null; credit_type: string | null }): string {
  if (row.credit_type === "computer_runtime") return "Agent Computer (VPS runtime)";
  return [row.provider, row.model].filter(Boolean).join(" / ") || row.credit_type || "Unknown";
}

/** Whole days since `value`, or null when there is nothing to measure from
 *  (never signed in) or the timestamp does not parse. Negative results
 *  (clock skew, a timestamp in the future) clamp to 0 rather than reading
 *  as "-1 days ago", which is not a fact an operator can act on. */
export function daysSince(value: string | null | undefined, now: Date = new Date()): number | null {
  if (!value) return null;
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return null;
  const diffMs = now.getTime() - parsed.getTime();
  return Math.max(0, Math.floor(diffMs / 86_400_000));
}

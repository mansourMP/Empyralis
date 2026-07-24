/**
 * One small, reusable usage chart — U3-C's "instrumentation, not decoration"
 * piece. Maximum restraint: a single thin stroke in one muted color, no
 * axes, no gridlines, no legend. The current total is shown once, beside
 * it, in tabular figures — the chart is context for that number, not a
 * second competing number.
 */

import type { ReactNode } from "react";

export type UsageBucket = {
  bucket: string;
  events?: number | string;
  total_tokens?: number | string;
  usd_cost?: number | string;
};

/** API buckets arrive newest-first (SQL `ORDER BY bucket DESC`) — reverse to
 *  chronological (oldest→newest, left→right) for a sparkline read left to
 *  right like a timeline. Coerces Postgres NUMERIC-as-string values. */
export function bucketSeries(buckets: UsageBucket[], field: "usd_cost" | "total_tokens"): number[] {
  return buckets
    .slice()
    .reverse()
    .map((b) => Number(b[field] ?? 0) || 0);
}

/** Bare chart, no number — a thin line with a faint area fill under it,
 *  viewBox-scaled so it's responsive to whatever width the caller gives it.
 *  All-zero (or empty) data draws a flat baseline instead of a fake curve —
 *  an honest "nothing happened here" rather than a misleadingly busy zero
 *  line. */
export function Sparkline({
  values,
  height = 44,
}: {
  values: number[];
  height?: number;
}) {
  const w = 100;
  const h = height;
  const pad = 3;
  const max = Math.max(...values, 0);
  const isFlat = values.length === 0 || max <= 0;

  if (isFlat) {
    const y = h - pad;
    return (
      <svg
        viewBox={`0 0 ${w} ${h}`}
        preserveAspectRatio="none"
        className="fleet-sparkline"
        style={{ height }}
        aria-hidden
      >
        <line x1={0} y1={y} x2={w} y2={y} className="fleet-sparkline-baseline" />
      </svg>
    );
  }

  const step = values.length > 1 ? w / (values.length - 1) : 0;
  const points = values.map((v, i) => {
    const x = values.length > 1 ? i * step : w / 2;
    const y = h - pad - (v / max) * (h - pad * 2);
    return [x, y] as const;
  });
  const line = points.map(([x, y]) => `${x},${y}`).join(" ");
  const area = `0,${h} ${line} ${w},${h}`;

  return (
    <svg
      viewBox={`0 0 ${w} ${h}`}
      preserveAspectRatio="none"
      className="fleet-sparkline"
      style={{ height }}
      aria-hidden
    >
      <polyline points={area} className="fleet-sparkline-area" />
      <polyline points={line} className="fleet-sparkline-line" />
    </svg>
  );
}

export type ChartSeries = {
  key: string;
  /** Per-agent identity color (TINTS[tintKeyForIndex(i)].fg) — the caller
   *  owns color assignment so it can stay consistent across multiple charts
   *  and a legend, all keyed off the same agent index. */
  color: string;
  values: number[];
};

/** Multiple colored lines sharing one y-scale — the usage dashboard's
 *  per-agent daily series (cost/tokens/calls), one hue per agent. Same
 *  restraint as Sparkline: no axes, no gridlines, no per-point markers. An
 *  all-zero series draws nothing for that agent (an honest gap, not a flat
 *  line claiming activity), and an entirely-empty chart falls back to the
 *  same flat baseline Sparkline uses. */
export function MultiSeriesChart({
  series,
  height = 160,
}: {
  series: ChartSeries[];
  height?: number;
}) {
  const w = 100;
  const h = height;
  const pad = 4;
  const len = Math.max(0, ...series.map((s) => s.values.length));
  const max = Math.max(0, ...series.flatMap((s) => s.values));
  const isFlat = len === 0 || max <= 0;

  if (isFlat) {
    const y = h - pad;
    return (
      <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" className="fleet-sparkline" style={{ height }} aria-hidden>
        <line x1={0} y1={y} x2={w} y2={y} className="fleet-sparkline-baseline" />
      </svg>
    );
  }

  const step = len > 1 ? w / (len - 1) : 0;
  return (
    <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" className="fleet-sparkline" style={{ height }} aria-hidden>
      {series.map((s) => {
        if (!s.values.some((v) => v > 0)) return null;
        const points = s.values
          .map((v, i) => {
            const x = len > 1 ? i * step : w / 2;
            const y = h - pad - (v / max) * (h - pad * 2);
            return `${x},${y}`;
          })
          .join(" ");
        return <polyline key={s.key} points={points} className="fleet-multichart-line" style={{ stroke: s.color }} />;
      })}
    </svg>
  );
}

/** The composed unit used everywhere a raw Cost/Tokens number lived before:
 *  label + tabular total on one line, the chart on the next. `unit` prefixes
 *  the number (e.g. "$"); omit for a bare count (tokens). */
export function UsageStat({
  label,
  total,
  unit = "",
  values,
  formattedTotal,
  action,
}: {
  label: string;
  total: number;
  unit?: string;
  values: number[];
  /** Pre-formatted total string, if the caller needs custom decimal
   *  precision (e.g. cost's 4 decimals) — overrides `total`/`unit` display,
   *  `total` is still used to decide the muted/accent tone. */
  formattedTotal?: string;
  /** Optional small control rendered next to the label — e.g. a Day/Week/
   *  Month period toggle. Kept as a slot rather than a dedicated prop set so
   *  this stays a display component; the caller owns the control's state and
   *  its own data refetch. */
  action?: ReactNode;
}) {
  return (
    <div className="fleet-usage-stat">
      <div className="fleet-usage-stat-head">
        <span className="fleet-usage-stat-label-group">
          <span className="fleet-usage-stat-label">{label}</span>
          {action}
        </span>
        <span className={`fleet-usage-stat-total${total > 0 ? "" : " is-muted"}`}>
          {formattedTotal ?? `${unit}${total}`}
        </span>
      </div>
      <Sparkline values={values} />
    </div>
  );
}

import type { ReactNode } from "react";

export type BarListRow = {
  key: string;
  label: ReactNode;
  value: string;
  caption?: string | null;
  barWidthPct: number;
  flagged?: boolean;
};

/**
 * Hand-rolled horizontal bars -- no charting library (production runs on a
 * 1 vCPU / ~1GB box; a chart dependency was explicitly ruled out). Backs
 * the funnel, failures, and spend pages. `barWidthPct` is computed by each
 * page's own pure module (funnel.ts, failures.ts, spend.ts), never here --
 * this component only renders a number it is handed.
 */
export function BarList({ rows }: { rows: BarListRow[] }) {
  return (
    <div>
      {rows.map((row) => (
        <div className="ops-bar-row" key={row.key}>
          <div className="ops-bar-row-label">{row.label}</div>
          <div>
            <div className="ops-bar-track">
              <div
                className={`ops-bar-fill${row.flagged ? " ops-bar-fill--flagged" : ""}`}
                style={{ width: `${row.barWidthPct}%` }}
              />
            </div>
            {row.caption && <div className="ops-bar-row-caption">{row.caption}</div>}
          </div>
          <div className="ops-bar-row-value">{row.value}</div>
        </div>
      ))}
    </div>
  );
}

import type { FormattedSpendDay } from "@/lib/spend";

/** A day-over-day strip for the spend page, hand-rolled the same way
 *  BarList is -- no charting library. `title` gives a hover-visible exact
 *  value per day without needing a tooltip library. */
export function SparkBars({ days }: { days: FormattedSpendDay[] }) {
  if (days.length === 0) return null;
  return (
    <div className="ops-sparkbars" role="img" aria-label="Platform cost per day over the selected window">
      {days.map((day) => (
        <div
          key={day.day}
          className="ops-sparkbar"
          style={{ height: `${Math.max(2, day.barHeightPct)}%` }}
          title={`${day.dayLabel}: ${day.platformCost}`}
        />
      ))}
    </div>
  );
}

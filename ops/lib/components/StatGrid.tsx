import type { DisplayStat } from "@/lib/format";

export function StatGrid({ stats }: { stats: DisplayStat[] }) {
  return (
    <div className="ops-stat-grid">
      {stats.map((stat) => (
        <div key={stat.key} className={`ops-stat-card${stat.isActivation ? " ops-stat-card--activation" : ""}`}>
          <div className="ops-stat-value">{stat.value}</div>
          <div className="ops-stat-label">{stat.label}</div>
          {stat.caption && <div className="ops-stat-caption">{stat.caption}</div>}
        </div>
      ))}
    </div>
  );
}

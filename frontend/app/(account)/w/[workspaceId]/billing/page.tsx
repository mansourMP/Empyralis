"use client";

import { useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";

import { useFleetAgents } from "@/lib/workspace/fleet/fleet-data";
import { formatNumber, tintKeyForIndex, TINTS } from "@/lib/workspace/fleet/fleet-presentation";
import { MultiSeriesChart, type ChartSeries } from "@/lib/workspace/fleet/fleet-sparkline";
import { FleetListSkeleton, FleetSurfaceError } from "@/lib/workspace/fleet/fleet-states";
import { HeaderAction } from "@/lib/workspace/fleet/Breadcrumbs";

/**
 * Usage dashboard (U3-I) — a provider-console-style view (Anthropic/OpenAI
 * console as reference): daily-bucket charts for cost/tokens/calls, colored
 * one hue per agent, a period selector, and a per-agent legend table.
 *
 * /fleet/usage's `buckets` field is scoped per request (workspace/project/
 * agent), so "per-agent daily series" comes from calling it once per agent
 * with scope=agent — no new endpoint. `totals`/`by_agent` are deliberately
 * NOT used for the headline numbers: summarize_usage() doesn't date-filter
 * either of those (only `buckets` gets a date_trunc GROUP BY), so they're
 * always all-time regardless of `period` — the previous version of this
 * page passed that all-time total off as "this month". The window totals
 * here are summed from the same aligned daily buckets the charts render,
 * so the number on screen always matches what the chart shows.
 */

type UsageBucket = { bucket: string; events?: number | string; total_tokens?: number | string; usd_cost?: number | string };
type DayValue = { cost: number; tokens: number; events: number };
type AgentUsage = { agentId: string; label: string; color: string; byDate: Map<string, DayValue> };

const PERIODS = [
  { value: 7, label: "7d" },
  { value: 30, label: "30d" },
] as const;
type PeriodDays = (typeof PERIODS)[number]["value"];

const money = (n: number) => `$${n.toFixed(4)}`;

function dateKey(iso: string): string {
  return (iso || "").slice(0, 10);
}

// Oldest → newest, UTC-aligned to match the backend's date_trunc('day', …)
// bucketing (a timestamptz column truncated in the DB, not the browser's
// local timezone).
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

export default function UsagePage() {
  const params = useParams();
  const workspaceId = String(params?.workspaceId || "");
  const [period, setPeriod] = useState<PeriodDays>(7);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [bucketsByAgent, setBucketsByAgent] = useState<Map<string, UsageBucket[]>>(new Map());

  const { agents } = useFleetAgents(workspaceId);
  // Unlike the Agents list (where Sage is rightly hidden — it's the operator,
  // not a manageable worker), this page is specifically "where does the
  // money go" — excluding Sage's own real spend here would silently hide it
  // from the one surface built to show it (Truth Map, 2026-07-10).

  useEffect(() => {
    if (agents.length === 0) {
      setBucketsByAgent(new Map());
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    Promise.all(
      agents.map((a) =>
        fetch(
          `/api/w/${encodeURIComponent(workspaceId)}/fleet/usage?scope=agent&id=${encodeURIComponent(a.agent_id)}&period=day`,
          { credentials: "include" },
        )
          .then((r) => (r.ok ? r.json() : null))
          .catch(() => null),
      ),
    )
      .then((results) => {
        if (cancelled) return;
        const map = new Map<string, UsageBucket[]>();
        agents.forEach((a, i) => {
          const d = results[i];
          if (d?.ok !== false && Array.isArray(d?.buckets)) map.set(a.agent_id, d.buckets);
        });
        setBucketsByAgent(map);
        setError(null);
      })
      .catch(() => { if (!cancelled) setError("Could not load usage"); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [workspaceId, agents]);

  // 90 days fetched once (the backend's own bucket cap); slicing to 7/30
  // client-side means toggling the period never re-fetches.
  const days = useMemo(() => lastNDays(period), [period]);

  const perAgent: AgentUsage[] = useMemo(
    () => agents.map((a, i) => {
      const byDate = new Map<string, DayValue>();
      for (const b of bucketsByAgent.get(a.agent_id) || []) {
        byDate.set(dateKey(b.bucket), {
          cost: Number(b.usd_cost) || 0,
          tokens: Number(b.total_tokens) || 0,
          events: Number(b.events) || 0,
        });
      }
      return { agentId: a.agent_id, label: a.label || "Unnamed agent", color: TINTS[tintKeyForIndex(i)].fg, byDate };
    }),
    [agents, bucketsByAgent],
  );

  const legend = useMemo(
    () => perAgent
      .map((a) => {
        const totals = days.reduce(
          (acc, d) => {
            const v = a.byDate.get(d);
            if (v) { acc.cost += v.cost; acc.tokens += v.tokens; acc.events += v.events; }
            return acc;
          },
          { cost: 0, tokens: 0, events: 0 },
        );
        return { ...a, ...totals };
      })
      .filter((a) => a.cost > 0 || a.tokens > 0 || a.events > 0)
      .sort((a, b) => b.cost - a.cost),
    [perAgent, days],
  );

  const windowTotals = useMemo(
    () => legend.reduce(
      (acc, a) => ({ cost: acc.cost + a.cost, tokens: acc.tokens + a.tokens, events: acc.events + a.events }),
      { cost: 0, tokens: 0, events: 0 },
    ),
    [legend],
  );

  const seriesFor = (field: keyof DayValue): ChartSeries[] =>
    perAgent.map((a) => ({ key: a.agentId, color: a.color, values: days.map((d) => a.byDate.get(d)?.[field] || 0) }));

  const hasAnyUsage = legend.length > 0;

  return (
    <main className="fleet-content">
      {/* No page-title header — the breadcrumb already says "Usage". The
          period toggle is a real control, not a title, so it rides the
          breadcrumb row's action slot instead of a second header block. */}
      <HeaderAction>
        <div className="fleet-segmented">
          {PERIODS.map((p) => (
            <button
              key={p.value}
              type="button"
              className={`fleet-segmented-btn${period === p.value ? " fleet-segmented-btn--active" : ""}`}
              onClick={() => setPeriod(p.value)}
            >
              {p.label}
            </button>
          ))}
        </div>
      </HeaderAction>

      {loading && <FleetListSkeleton rows={5} />}
      {!loading && error && <FleetSurfaceError title="Couldn’t load usage" message={error} />}

      {!loading && !error && (
        <>
          <div className="fleet-stat-grid">
            <div className="fleet-stat-card">
              <div className="fleet-stat-value">{money(windowTotals.cost)}</div>
              <div className="fleet-stat-label">Cost · last {period}d</div>
            </div>
            <div className="fleet-stat-card">
              <div className="fleet-stat-value">{formatNumber(windowTotals.tokens)}</div>
              <div className="fleet-stat-label">Tokens · last {period}d</div>
            </div>
            <div className="fleet-stat-card">
              <div className="fleet-stat-value">{formatNumber(windowTotals.events)}</div>
              <div className="fleet-stat-label">LLM calls · last {period}d</div>
            </div>
          </div>

          {!hasAnyUsage ? (
            <div className="fleet-empty" style={{ marginTop: "var(--space-5)" }}>
              <div className="fleet-empty-title">No usage in this window</div>
              <div className="fleet-empty-desc">Costs appear here as your agents do work.</div>
            </div>
          ) : (
            <>
              {([
                { key: "cost" as const, title: "Cost" },
                { key: "tokens" as const, title: "Tokens" },
                { key: "events" as const, title: "LLM calls" },
              ]).map((chart) => (
                <div key={chart.key} className="fleet-usage-chart-block">
                  <div className="fleet-usage-chart-title">{chart.title}</div>
                  <MultiSeriesChart series={seriesFor(chart.key)} height={160} />
                  <div className="fleet-usage-chart-axis">
                    <span>{dayLabel(days[0])}</span>
                    <span>{dayLabel(days[days.length - 1])}</span>
                  </div>
                </div>
              ))}

              <div className="fleet-usage-legend">
                <div className="fleet-usage-legend-header" aria-hidden>
                  <span>Agent</span>
                  <span className="is-right">Cost</span>
                  <span className="is-right">Tokens</span>
                  <span className="is-right">Calls</span>
                </div>
                {legend.map((a) => (
                  <div key={a.agentId} className="fleet-usage-legend-row">
                    <span className="fleet-usage-legend-name">
                      <span className="fleet-tint-pip" style={{ background: a.color }} />
                      {a.label}
                    </span>
                    <span className="fleet-agent-cell-right">{money(a.cost)}</span>
                    <span className="fleet-agent-cell-right">{formatNumber(a.tokens)}</span>
                    <span className="fleet-agent-cell-right">{formatNumber(a.events)}</span>
                  </div>
                ))}
              </div>
            </>
          )}
        </>
      )}
    </main>
  );
}

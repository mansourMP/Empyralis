/**
 * Pure shaping for the `/overview` page. Backed by
 * `GET /api/internal/operator/overview`
 * (server_modules/operator_console_service.build_overview) -- see that
 * function's return statement for the exact response shape; the types below
 * are transcribed from it, not guessed.
 *
 * Same "summary before detail" discipline as
 * frontend/lib/workspace/fleet/platform-activation.ts: a small activation-
 * shaped set of stats (agents that ran vs. were only created, workspaces
 * that invited a second person) renders first and separately from the raw
 * totals, via each stat's own `isActivation` flag.
 *
 * Run: npx tsx lib/overview.test.ts
 */

import { countEntriesSorted, formatCount, formatPercent, type DisplayStat } from "./format";
import { formatCredits, formatUsd } from "./money";

export type OperatorOverview = {
  generated_at?: string | null;
  rls_bypass_verified?: boolean;
  totals: {
    users: number;
    workspaces: number;
    agents: number;
    projects: number;
    tasks: number;
    documents: number;
  };
  signups: {
    last_7_days: number;
    last_30_days: number;
    last_90_days: number;
  };
  agents_runtime: {
    total: number;
    with_runs: number;
    never_run: number;
  };
  workspaces_with_second_member: number;
  workspaces_with_second_member_pct: number;
  spend: {
    total_platform_cost_usd: number;
    total_credits_debited: number;
  };
  agent_computers: {
    total: number;
    by_status: Record<string, number>;
    source: string;
    excludes: string;
  };
};

/** Alias, not a new shape -- see format.ts's `DisplayStat` for why every
 *  view's stat tiles share one type. */
export type OverviewStat = DisplayStat;

export function overviewSummaryStats(o: OperatorOverview): OverviewStat[] {
  return [
    { key: "users", label: "Signed-up users", value: formatCount(o.totals.users), caption: null, isActivation: false },
    { key: "workspaces", label: "Workspaces", value: formatCount(o.totals.workspaces), caption: null, isActivation: false },
    {
      key: "agents-ran",
      label: "Agents that have actually run",
      value: formatCount(o.agents_runtime.with_runs),
      caption: `of ${formatCount(o.agents_runtime.total)} created`,
      isActivation: true,
    },
    {
      key: "workspaces-invited",
      label: "Workspaces with more than one member",
      value: formatCount(o.workspaces_with_second_member),
      caption: `${formatPercent(o.workspaces_with_second_member_pct)} of workspaces`,
      isActivation: true,
    },
  ];
}

export function overviewDetailStats(o: OperatorOverview): OverviewStat[] {
  return [
    { key: "projects", label: "Projects", value: formatCount(o.totals.projects), caption: null, isActivation: false },
    { key: "tasks", label: "Tasks", value: formatCount(o.totals.tasks), caption: null, isActivation: false },
    { key: "documents", label: "Documents", value: formatCount(o.totals.documents), caption: null, isActivation: false },
    {
      key: "agents-never-run",
      label: "Agents that have never run",
      value: formatCount(o.agents_runtime.never_run),
      caption: null,
      isActivation: false,
    },
    { key: "signups-7d", label: "Signups, last 7 days", value: formatCount(o.signups.last_7_days), caption: null, isActivation: false },
    { key: "signups-30d", label: "Signups, last 30 days", value: formatCount(o.signups.last_30_days), caption: null, isActivation: false },
    { key: "signups-90d", label: "Signups, last 90 days", value: formatCount(o.signups.last_90_days), caption: null, isActivation: false },
    {
      key: "platform-cost",
      label: "Total platform cost",
      value: formatUsd(o.spend.total_platform_cost_usd),
      caption: null,
      isActivation: false,
    },
    {
      key: "credits-debited",
      label: "Total credits debited",
      value: formatCredits(o.spend.total_credits_debited),
      caption: null,
      isActivation: false,
    },
    { key: "agent-computers", label: "Agent Computers (VPS)", value: formatCount(o.agent_computers.total), caption: null, isActivation: false },
  ];
}

export type StatusCount = { status: string; count: number };

/** Thin rename over format.ts's shared `countEntriesSorted` -- see that
 *  function's own comment for the sort rule (highest count first, ties
 *  broken alphabetically). */
export function agentComputerStatusBreakdown(byStatus: Record<string, number>): StatusCount[] {
  return countEntriesSorted(byStatus).map(({ key, count }) => ({ status: key, count }));
}

/**
 * Pure shaping for `/retention`. Backed by
 * `GET /api/internal/operator/retention`
 * (server_modules/operator_console_service.build_retention) -- reads
 * auth_sessions.last_seen_at, touched on every authenticated request.
 *
 * Run: npx tsx lib/retention.test.ts
 */

import { formatCount, formatPercent, type DisplayStat } from "./format";

export type RetentionWindow = { count: number; pct_of_users: number };

export type OperatorRetention = {
  generated_at?: string | null;
  rls_bypass_verified?: boolean;
  total_users: number;
  active: {
    last_1_day: RetentionWindow;
    last_7_days: RetentionWindow;
    last_30_days: RetentionWindow;
  };
};

export function retentionStats(r: OperatorRetention): DisplayStat[] {
  return [
    { key: "total-users", label: "Signed-up users", value: formatCount(r.total_users), caption: null, isActivation: false },
    {
      key: "active-1d",
      label: "Active in the last day",
      value: formatCount(r.active.last_1_day.count),
      caption: `${formatPercent(r.active.last_1_day.pct_of_users)} of users`,
      isActivation: true,
    },
    {
      key: "active-7d",
      label: "Active in the last 7 days",
      value: formatCount(r.active.last_7_days.count),
      caption: `${formatPercent(r.active.last_7_days.pct_of_users)} of users`,
      isActivation: true,
    },
    {
      key: "active-30d",
      label: "Active in the last 30 days",
      value: formatCount(r.active.last_30_days.count),
      caption: `${formatPercent(r.active.last_30_days.pct_of_users)} of users`,
      isActivation: true,
    },
  ];
}

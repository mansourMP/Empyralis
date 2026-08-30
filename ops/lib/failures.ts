/**
 * Pure shaping for `/failures` -- provider/tool failures grouped by
 * (status, error_code, action_domain) so a systemic breakage is visible,
 * plus a bounded recent list. Backed by
 * `GET /api/internal/operator/failures`
 * (server_modules/operator_console_service.list_failures).
 *
 * Run: npx tsx lib/failures.test.ts
 */

import { formatCount, formatTimestamp } from "./format";

export const FAILURES_MIN_DAYS = 1;
export const FAILURES_MAX_DAYS = 90; // mirrors routes_operator_console.py's Query(..., le=90)
export const FAILURES_DEFAULT_DAYS = 7;

/** The one place a `?days=` query param becomes the value this app actually
 *  sends to the backend -- clamped to the SAME bounds routes_operator_
 *  console.py enforces (ge=1, le=90), so a hand-edited URL degrades to a
 *  safe value instead of the backend 422ing on an out-of-range int. */
export function parseFailuresDays(raw: string | null): number {
  const n = Number.parseInt(raw ?? "", 10);
  if (!Number.isFinite(n)) return FAILURES_DEFAULT_DAYS;
  return Math.max(FAILURES_MIN_DAYS, Math.min(n, FAILURES_MAX_DAYS));
}

export type FailureGroup = {
  status: string;
  error_code: string | null;
  action_domain: string | null;
  event_count: number;
  distinct_workspaces: number;
  distinct_agents: number;
  first_seen: string;
  last_seen: string;
};

export type FailureEvent = {
  id: string;
  tenant_id: string;
  workspace_id: string;
  agent_id: string | null;
  action_domain: string | null;
  action_name: string | null;
  tool_kind: string | null;
  tool_id: string | null;
  status: string;
  error_code: string | null;
  run_id: string | null;
  trace_id: string | null;
  created_at: string;
};

export type OperatorFailures = {
  generated_at?: string | null;
  rls_bypass_verified?: boolean;
  window_days: number;
  total_failures: number;
  grouped: FailureGroup[];
  recent: FailureEvent[];
};

export type FormattedFailureGroup = {
  domainLabel: string;
  codeLabel: string;
  status: string;
  eventCount: string;
  distinctWorkspaces: string;
  distinctAgents: string;
  lastSeen: string;
  /** Width relative to the LARGEST group in this response -- lets an
   *  operator spot the dominant failure mode at a glance without a chart
   *  library, the same hand-rolled-bar approach funnel.ts uses. */
  barWidthPct: number;
};

/** Sorted by event_count descending (defensively re-sorted here rather than
 *  trusting the backend's own ORDER BY to still hold after this module's
 *  bar-width computation -- the same reasoning as accounts.ts validating
 *  sort_by against its own whitelist rather than trusting a caller). Ties
 *  break on action_domain then error_code so repeated renders of the same
 *  data don't visibly reshuffle. */
export function formatFailureGroups(grouped: FailureGroup[]): FormattedFailureGroup[] {
  const sorted = [...grouped].sort((a, b) => {
    if (b.event_count !== a.event_count) return b.event_count - a.event_count;
    const domainCmp = (a.action_domain || "").localeCompare(b.action_domain || "");
    if (domainCmp !== 0) return domainCmp;
    return (a.error_code || "").localeCompare(b.error_code || "");
  });
  const max = sorted.reduce((m, g) => Math.max(m, g.event_count), 0);

  return sorted.map((g) => ({
    domainLabel: g.action_domain || "unknown domain",
    codeLabel: g.error_code || "no error code",
    status: g.status,
    eventCount: formatCount(g.event_count),
    distinctWorkspaces: formatCount(g.distinct_workspaces),
    distinctAgents: formatCount(g.distinct_agents),
    lastSeen: formatTimestamp(g.last_seen),
    barWidthPct: max > 0 ? Math.max(0, Math.min(100, (g.event_count / max) * 100)) : 0,
  }));
}

export type FormattedFailureEvent = {
  id: string;
  workspaceId: string;
  domainLabel: string;
  actionName: string;
  codeLabel: string;
  status: string;
  createdAt: string;
};

export function formatFailureEvent(event: FailureEvent): FormattedFailureEvent {
  return {
    id: event.id,
    workspaceId: event.workspace_id,
    domainLabel: event.action_domain || "unknown domain",
    actionName: event.action_name || "—",
    codeLabel: event.error_code || "no error code",
    status: event.status,
    createdAt: formatTimestamp(event.created_at),
  };
}

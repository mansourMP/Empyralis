/**
 * Pure shaping for `/accounts/[workspaceId]` -- one workspace, everything an
 * operator needs about it in one round trip. Backed by
 * `GET /api/internal/operator/accounts/{workspace_id}`
 * (server_modules/operator_console_service.get_account_detail) -- types
 * below are transcribed from that function's SQL and return statement.
 *
 * Run: npx tsx lib/account-detail.test.ts
 */

import { countEntriesSorted, formatCount, formatTimestamp, spendSourceLabel } from "./format";
import { formatCredits, formatUsd } from "./money";

export type AccountMember = {
  user_id: string;
  email: string | null;
  display_name: string | null;
  role: string | null;
  membership_status: string | null;
  joined_at: string;
  last_active_at: string | null;
};

export type AccountAgent = {
  id: string;
  display_name: string;
  definition_name: string | null;
  agent_kind: string;
  status: string | null;
  enabled: boolean;
  created_at: string;
  last_run_started_at: string | null;
  last_run_finished_at: string | null;
  last_run_outcome: string | null;
};

export type AccountProject = {
  id: string;
  name: string;
  slug: string | null;
  archived: boolean;
  created_at: string;
  task_count: number;
  document_count: number;
};

export type AccountActivityEvent = {
  id: string;
  actor_type: string | null;
  actor_id: string | null;
  install_id: string | null;
  event_class: string | null;
  action: string | null;
  status: string | null;
  title: string | null;
  created_at: string;
};

export type AccountSpendRow = {
  provider: string | null;
  model: string | null;
  credit_type: string | null;
  event_count: number;
  total_platform_cost_usd: number;
  total_credits_debited: number;
};

export type AccountFailure = {
  id: string;
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

export type AccountDetail = {
  generated_at?: string | null;
  rls_bypass_verified?: boolean;
  tenant_id: string;
  workspace_id: string;
  name: string;
  created_at: string;
  workspace_status: string | null;
  workspace_type: string | null;
  owner: { user_id: string | null; email: string | null; display_name: string | null };
  members: AccountMember[];
  agents: AccountAgent[];
  projects: AccountProject[];
  recent_activity: AccountActivityEvent[];
  spend_breakdown: AccountSpendRow[];
  recent_failures: AccountFailure[];
  runs_by_outcome: Record<string, number>;
};

/** An agent that has genuinely completed at least one run -- distinct from
 *  merely being installed. Mirrors the backend's own EXISTS-against-
 *  agent_traces definition (never derived from `status`/`enabled`, which
 *  describe the install's current state, not whether it has ever done
 *  anything). */
export function agentHasRun(agent: Pick<AccountAgent, "last_run_started_at" | "last_run_outcome">): boolean {
  return Boolean(agent.last_run_started_at) || Boolean(agent.last_run_outcome);
}

/** The same "signed up vs. actually did something" signal accounts.ts draws
 *  at the list level, computed here from the real per-project counts this
 *  detail response carries (rather than re-fetching the list row) -- a real
 *  task or document in any project. */
export function accountDetailHasActivity(detail: Pick<AccountDetail, "projects">): boolean {
  return detail.projects.some((p) => p.task_count > 0 || p.document_count > 0);
}

/** Sum across every provider/model/credit-type row -- the header stat this
 *  response doesn't carry directly (unlike list_accounts, which precomputes
 *  it server-side; the detail query returns the breakdown and leaves the
 *  total to whoever renders it, so there is exactly one place doing the
 *  arithmetic instead of duplicating the backend's SUM). */
export function totalPlatformCost(spendBreakdown: Array<Pick<AccountSpendRow, "total_platform_cost_usd">>): number {
  return spendBreakdown.reduce((sum, row) => {
    const v = row.total_platform_cost_usd;
    return sum + (typeof v === "number" && Number.isFinite(v) ? v : 0);
  }, 0);
}

export type OutcomeCount = { outcome: string; count: string };

export function runsByOutcomeSorted(runsByOutcome: Record<string, number>): OutcomeCount[] {
  return countEntriesSorted(runsByOutcome).map(({ key, count }) => ({ outcome: key, count: formatCount(count) }));
}

export type FormattedMemberRow = {
  userId: string;
  label: string;
  role: string;
  membershipStatus: string;
  joinedAt: string;
  lastActiveAt: string;
  everActive: boolean;
};

export function formatMemberRow(member: AccountMember): FormattedMemberRow {
  return {
    userId: member.user_id,
    label: member.email || member.display_name || member.user_id,
    role: member.role || "—",
    membershipStatus: member.membership_status || "—",
    joinedAt: formatTimestamp(member.joined_at),
    lastActiveAt: formatTimestamp(member.last_active_at, { never: "Never signed in" }),
    everActive: Boolean(member.last_active_at),
  };
}

export type FormattedAgentRow = {
  id: string;
  label: string;
  kind: string;
  enabled: boolean;
  hasRun: boolean;
  lastRunOutcome: string;
  lastRunAt: string;
};

export function formatAgentRow(agent: AccountAgent): FormattedAgentRow {
  return {
    id: agent.id,
    label: agent.display_name,
    kind: agent.agent_kind,
    enabled: agent.enabled,
    hasRun: agentHasRun(agent),
    lastRunOutcome: agent.last_run_outcome || "—",
    lastRunAt: formatTimestamp(agent.last_run_started_at, { never: "Never run" }),
  };
}

export type FormattedSpendRow = {
  label: string;
  eventCount: string;
  platformCost: string;
  creditsDebited: string;
};

export function formatSpendRow(row: AccountSpendRow): FormattedSpendRow {
  return {
    label: spendSourceLabel(row),
    eventCount: formatCount(row.event_count),
    platformCost: formatUsd(row.total_platform_cost_usd),
    creditsDebited: formatCredits(row.total_credits_debited),
  };
}

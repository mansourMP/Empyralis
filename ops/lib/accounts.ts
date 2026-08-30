/**
 * Pure shaping for `/accounts` -- the PRIMARY screen (per the founder's own
 * framing: this and the funnel are the two that answer MAN-149, "105
 * signups, 5 documents"). Backed by `GET /api/internal/operator/accounts`
 * (server_modules/operator_console_service.list_accounts) -- types below are
 * transcribed from that function's SQL and return statement, not guessed.
 *
 * Sort/filter/pagination all run server-side (query params the backend
 * turns into SQL) -- this module's job is turning the URL's search params
 * into that query, and back, so the account list is a real bookmarkable/
 * shareable link like every routed page in the product, and validating
 * `sort_by` against the SAME whitelist the backend enforces (defense in
 * depth: a client that never sends an invalid column never has to see the
 * 400 the backend would otherwise answer with).
 *
 * Run: npx tsx lib/accounts.test.ts
 */

import { formatCount } from "./format";
import { formatCredits, formatUsd } from "./money";

// Mirrors operator_console_service.ACCOUNT_SORT_COLUMNS's key set exactly
// (the VALUES differ -- those are real column/expression names the backend
// substitutes into SQL and are never sent over the wire either way).
export const ACCOUNT_SORT_COLUMNS = [
  "name",
  "created_at",
  "last_active_at",
  "member_count",
  "project_count",
  "task_count",
  "document_count",
  "agent_count",
  "agents_with_runs",
  "total_platform_cost_usd",
  "total_credits_debited",
] as const;

export type AccountSortColumn = (typeof ACCOUNT_SORT_COLUMNS)[number];
export type SortDirection = "asc" | "desc";

export function isAccountSortColumn(value: string): value is AccountSortColumn {
  return (ACCOUNT_SORT_COLUMNS as readonly string[]).includes(value);
}

export const ACCOUNT_COLUMN_LABELS: Record<AccountSortColumn, string> = {
  name: "Workspace",
  created_at: "Created",
  last_active_at: "Last active",
  member_count: "Members",
  project_count: "Projects",
  task_count: "Tasks",
  document_count: "Documents",
  agent_count: "Agents",
  agents_with_runs: "Agents that ran",
  total_platform_cost_usd: "Platform cost",
  total_credits_debited: "Credits debited",
};

export type AccountOwner = {
  user_id: string | null;
  email: string | null;
  display_name: string | null;
};

export type Account = {
  workspace_id: string;
  tenant_id: string;
  name: string;
  created_at: string;
  workspace_status: string | null;
  owner: AccountOwner;
  member_count: number;
  last_active_at: string | null;
  project_count: number;
  task_count: number;
  document_count: number;
  agent_count: number;
  agents_with_runs: number;
  agents_never_run: number;
  total_platform_cost_usd: number;
  total_credits_debited: number;
};

export type AccountsResponse = {
  generated_at?: string | null;
  rls_bypass_verified?: boolean;
  total_matching: number;
  returned_count: number;
  limit: number;
  offset: number;
  sort_by: string;
  sort_dir: string;
  accounts: Account[];
};

export type AccountsQuery = {
  search: string;
  minMembers: number | null;
  hasAgents: boolean | null;
  activeWithinDays: number | null;
  sortBy: AccountSortColumn;
  sortDir: SortDirection;
  limit: number;
  offset: number;
};

export const DEFAULT_ACCOUNTS_QUERY: AccountsQuery = {
  search: "",
  minMembers: null,
  hasAgents: null,
  activeWithinDays: null,
  sortBy: "name",
  sortDir: "asc",
  limit: 200,
  offset: 0,
};

function parseIntOrNull(raw: string | null): number | null {
  if (raw === null || raw.trim() === "") return null;
  const n = Number.parseInt(raw, 10);
  return Number.isFinite(n) && n >= 0 ? n : null;
}

function parseBoolOrNull(raw: string | null): boolean | null {
  if (raw === "true") return true;
  if (raw === "false") return false;
  return null;
}

/** The one place a URL's search params become the account-list query this
 *  app fetches with. Every field is defaulted and clamped here so a
 *  hand-edited or stale URL (a bookmarked `limit=99999`, a `sort_by` from a
 *  column that no longer exists) degrades to a safe value instead of
 *  reaching fetch() malformed. */
export function parseAccountsQuery(params: URLSearchParams): AccountsQuery {
  const rawSortBy = params.get("sort_by") ?? DEFAULT_ACCOUNTS_QUERY.sortBy;
  const rawSortDir = params.get("sort_dir") ?? DEFAULT_ACCOUNTS_QUERY.sortDir;
  const rawLimit = Number.parseInt(params.get("limit") ?? "", 10);
  const rawOffset = Number.parseInt(params.get("offset") ?? "", 10);

  return {
    search: (params.get("search") ?? "").trim(),
    minMembers: parseIntOrNull(params.get("min_members")),
    hasAgents: parseBoolOrNull(params.get("has_agents")),
    activeWithinDays: parseIntOrNull(params.get("active_within_days")),
    sortBy: isAccountSortColumn(rawSortBy) ? rawSortBy : DEFAULT_ACCOUNTS_QUERY.sortBy,
    sortDir: rawSortDir === "desc" ? "desc" : "asc",
    limit: Number.isFinite(rawLimit) && rawLimit >= 1 ? Math.min(rawLimit, 1000) : DEFAULT_ACCOUNTS_QUERY.limit,
    offset: Number.isFinite(rawOffset) && rawOffset >= 0 ? rawOffset : DEFAULT_ACCOUNTS_QUERY.offset,
  };
}

/** The inverse of parseAccountsQuery -- also what both the fetch URL to the
 *  backend AND the browser's own address bar are built from, so the two
 *  never drift into showing a URL that doesn't match what was actually
 *  fetched. Omits a field entirely when it is at its default, so an
 *  unfiltered, first-page view keeps a clean `/accounts` URL. */
export function buildAccountsSearchParams(query: AccountsQuery): URLSearchParams {
  const params = new URLSearchParams();
  if (query.search) params.set("search", query.search);
  if (query.minMembers !== null) params.set("min_members", String(query.minMembers));
  if (query.hasAgents !== null) params.set("has_agents", String(query.hasAgents));
  if (query.activeWithinDays !== null) params.set("active_within_days", String(query.activeWithinDays));
  if (query.sortBy !== DEFAULT_ACCOUNTS_QUERY.sortBy) params.set("sort_by", query.sortBy);
  if (query.sortDir !== DEFAULT_ACCOUNTS_QUERY.sortDir) params.set("sort_dir", query.sortDir);
  if (query.limit !== DEFAULT_ACCOUNTS_QUERY.limit) params.set("limit", String(query.limit));
  if (query.offset !== DEFAULT_ACCOUNTS_QUERY.offset) params.set("offset", String(query.offset));
  return params;
}

/** Clicking a column header: the same column flips direction; a new column
 *  starts at ascending. Sorting always resets to the first page -- a sort
 *  change on page 3 landing on a now-unrelated page 3 of the new order
 *  would be confusing, not a feature. */
export function toggleAccountSort(
  current: Pick<AccountsQuery, "sortBy" | "sortDir">,
  column: AccountSortColumn,
): Pick<AccountsQuery, "sortBy" | "sortDir" | "offset"> {
  if (current.sortBy === column) {
    return { sortBy: column, sortDir: current.sortDir === "asc" ? "desc" : "asc", offset: 0 };
  }
  return { sortBy: column, sortDir: "asc", offset: 0 };
}

/** The visual difference between "signed up" and "actually did something"
 *  the product laws ask for, at the account-row level: any real project,
 *  task, or document. Deliberately excludes agent_count alone -- creating
 *  an agent install is a lighter-weight action than the platform-activation
 *  page's own "created a task or document" bar (see routes_operator_console
 *  .py's module docstring on the activation-funnel step ordering), so an
 *  account with only an agent and nothing else still reads as not-yet-
 *  activated here. */
export function accountHasActivity(account: Pick<Account, "project_count" | "task_count" | "document_count">): boolean {
  return account.project_count > 0 || account.task_count > 0 || account.document_count > 0;
}

export type FormattedAccountRow = {
  workspaceId: string;
  name: string;
  ownerLabel: string;
  status: string | null;
  createdAt: string;
  memberCount: string;
  projectCount: string;
  taskCount: string;
  documentCount: string;
  agentCount: string;
  agentsWithRuns: string;
  agentsNeverRun: string;
  platformCost: string;
  creditsDebited: string;
  hasActivity: boolean;
};

export function formatAccountRow(account: Account): FormattedAccountRow {
  return {
    workspaceId: account.workspace_id,
    name: account.name || account.workspace_id,
    ownerLabel: account.owner.email || account.owner.display_name || "—",
    status: account.workspace_status,
    createdAt: account.created_at,
    memberCount: formatCount(account.member_count),
    projectCount: formatCount(account.project_count),
    taskCount: formatCount(account.task_count),
    documentCount: formatCount(account.document_count),
    agentCount: formatCount(account.agent_count),
    agentsWithRuns: formatCount(account.agents_with_runs),
    agentsNeverRun: formatCount(account.agents_never_run),
    platformCost: formatUsd(account.total_platform_cost_usd),
    creditsDebited: formatCredits(account.total_credits_debited),
    hasActivity: accountHasActivity(account),
  };
}

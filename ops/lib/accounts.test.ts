import {
  accountHasActivity,
  buildAccountsSearchParams,
  DEFAULT_ACCOUNTS_QUERY,
  formatAccountRow,
  isAccountSortColumn,
  parseAccountsQuery,
  toggleAccountSort,
  type Account,
} from "./accounts";

let passed = 0;
let failed = 0;

function assert(condition: boolean, label: string): void {
  if (condition) {
    passed++;
  } else {
    failed++;
    console.error(`FAIL: ${label}`);
  }
}

// ── isAccountSortColumn / whitelist ──────────────────────────────────────

assert(isAccountSortColumn("total_credits_debited"), "a real backend sort column is recognized");
assert(!isAccountSortColumn("body"), "an arbitrary string (e.g. an injection attempt) is rejected");
assert(!isAccountSortColumn("DROP TABLE workspaces"), "SQL-shaped garbage is rejected, not just unknown words");

// ── parseAccountsQuery ───────────────────────────────────────────────────

{
  const q = parseAccountsQuery(new URLSearchParams(""));
  assert(q.search === "" && q.sortBy === "name" && q.sortDir === "asc" && q.limit === 200 && q.offset === 0, "an empty URL parses to the documented defaults");
}
{
  const q = parseAccountsQuery(new URLSearchParams("sort_by=DROP+TABLE&sort_dir=desc"));
  assert(q.sortBy === "name", "an invalid sort_by falls back to the safe default rather than reaching fetch()");
  assert(q.sortDir === "desc", "sort_dir is independent of sort_by validity");
}
{
  const q = parseAccountsQuery(new URLSearchParams("limit=999999&offset=-5"));
  assert(q.limit === 1000, "limit clamps to the backend's own cap (1000)");
  assert(q.offset === 0, "a negative offset falls back to 0, never sent negative");
}
{
  const q = parseAccountsQuery(new URLSearchParams("search=+acme+&min_members=3&has_agents=true&active_within_days=7"));
  assert(q.search === "acme", "search is trimmed");
  assert(q.minMembers === 3, "min_members parses");
  assert(q.hasAgents === true, "has_agents=true parses to a real boolean, not the string");
  assert(q.activeWithinDays === 7, "active_within_days parses");
}
{
  const q = parseAccountsQuery(new URLSearchParams("has_agents=maybe"));
  assert(q.hasAgents === null, "an unrecognized has_agents value is treated as unset, not a crash");
}

// ── buildAccountsSearchParams (round-trip) ───────────────────────────────

{
  const params = buildAccountsSearchParams(DEFAULT_ACCOUNTS_QUERY);
  assert(params.toString() === "", "the default query builds a clean, empty URL -- no filter noise on first load");
}
{
  const q = { ...DEFAULT_ACCOUNTS_QUERY, search: "acme", sortBy: "member_count" as const, sortDir: "desc" as const, offset: 200 };
  const params = buildAccountsSearchParams(q);
  const reparsed = parseAccountsQuery(params);
  assert(reparsed.search === "acme" && reparsed.sortBy === "member_count" && reparsed.sortDir === "desc" && reparsed.offset === 200, "build -> parse round-trips exactly");
}

// ── toggleAccountSort ─────────────────────────────────────────────────────

{
  const next = toggleAccountSort({ sortBy: "name", sortDir: "asc" }, "name");
  assert(next.sortDir === "desc", "clicking the already-sorted column flips direction");
  assert(next.offset === 0, "a sort change resets to the first page");
}
{
  const next = toggleAccountSort({ sortBy: "name", sortDir: "desc" }, "member_count");
  assert(next.sortBy === "member_count" && next.sortDir === "asc", "clicking a new column starts ascending, not carrying over the old direction");
}

// ── accountHasActivity / formatAccountRow ────────────────────────────────

const BASE_ACCOUNT: Account = {
  workspace_id: "ws_1",
  tenant_id: "t_1",
  name: "Acme",
  created_at: "2026-08-01T00:00:00Z",
  workspace_status: "active",
  owner: { user_id: "u_1", email: "owner@acme.com", display_name: "Owner" },
  member_count: 1,
  last_active_at: "2026-08-29T00:00:00Z",
  project_count: 0,
  task_count: 0,
  document_count: 0,
  agent_count: 2,
  agents_with_runs: 0,
  agents_never_run: 2,
  total_platform_cost_usd: 0,
  total_credits_debited: 0,
};

assert(accountHasActivity(BASE_ACCOUNT) === false, "an account that only created agents (no project/task/document) has not activated");
assert(accountHasActivity({ ...BASE_ACCOUNT, task_count: 1 }) === true, "a single real task counts as activity");
assert(accountHasActivity({ ...BASE_ACCOUNT, document_count: 1 }) === true, "a single real document counts as activity");
assert(accountHasActivity({ ...BASE_ACCOUNT, project_count: 1 }) === true, "a single real project counts as activity");

{
  const row = formatAccountRow(BASE_ACCOUNT);
  assert(row.ownerLabel === "owner@acme.com", "owner email is preferred for the row label");
  assert(row.hasActivity === false, "the formatted row carries the same activity signal, not re-derived twice");
  assert(row.platformCost === "$0.00", "zero spend renders through the shared money formatter");
  assert(row.creditsDebited === "0", "credits render through the credits formatter, never a dollar sign");
}
{
  const row = formatAccountRow({ ...BASE_ACCOUNT, owner: { user_id: null, email: null, display_name: "Fallback Name" } });
  assert(row.ownerLabel === "Fallback Name", "display_name is used when email is absent");
}
{
  const row = formatAccountRow({ ...BASE_ACCOUNT, owner: { user_id: null, email: null, display_name: null } });
  assert(row.ownerLabel === "—", "no owner info at all renders an explicit dash, never a blank cell that reads as broken");
}

if (failed > 0) {
  console.error(`\n${failed} failed, ${passed} passed`);
  process.exit(1);
} else {
  console.log(`${passed} passed`);
}

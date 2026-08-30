import {
  accountDetailHasActivity,
  agentHasRun,
  formatAgentRow,
  formatMemberRow,
  formatSpendRow,
  runsByOutcomeSorted,
  totalPlatformCost,
  type AccountAgent,
  type AccountMember,
  type AccountSpendRow,
} from "./account-detail";

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

// ── agentHasRun ───────────────────────────────────────────────────────────

assert(agentHasRun({ last_run_started_at: "2026-08-01T00:00:00Z", last_run_outcome: null }) === true, "a started run counts even with no outcome yet (still running)");
assert(agentHasRun({ last_run_started_at: null, last_run_outcome: "success" }) === true, "an outcome with no started_at still counts as having run");
assert(agentHasRun({ last_run_started_at: null, last_run_outcome: null }) === false, "no run timestamps at all means never run");

// ── accountDetailHasActivity ─────────────────────────────────────────────

assert(accountDetailHasActivity({ projects: [] }) === false, "no projects at all is not activated");
assert(accountDetailHasActivity({ projects: [{ id: "p1", name: "P", slug: null, archived: false, created_at: "x", task_count: 0, document_count: 0 }] }) === false, "a project with no tasks or documents is not activated");
assert(accountDetailHasActivity({ projects: [{ id: "p1", name: "P", slug: null, archived: false, created_at: "x", task_count: 1, document_count: 0 }] }) === true, "a real task in any project counts as activated");
assert(accountDetailHasActivity({ projects: [{ id: "p1", name: "P", slug: null, archived: true, created_at: "x", task_count: 0, document_count: 3 }] }) === true, "a real document counts even in an archived project");

// ── totalPlatformCost ─────────────────────────────────────────────────────

assert(totalPlatformCost([]) === 0, "no spend rows sums to zero");
assert(totalPlatformCost([{ total_platform_cost_usd: 1.5 }, { total_platform_cost_usd: 2.25 }]) === 3.75, "spend rows sum correctly");
assert(totalPlatformCost([{ total_platform_cost_usd: Number.NaN }, { total_platform_cost_usd: 1 }]) === 1, "a non-finite row is treated as zero rather than poisoning the whole sum");

// ── runsByOutcomeSorted ───────────────────────────────────────────────────

{
  const rows = runsByOutcomeSorted({ completed: 8, failed: 2 });
  assert(rows[0].outcome === "completed" && rows[0].count === "8", "the largest outcome bucket sorts first");
  assert(rows[1].outcome === "failed" && rows[1].count === "2", "the smaller bucket follows");
}
assert(runsByOutcomeSorted({}).length === 0, "no runs at all is an empty list");

// ── formatMemberRow ───────────────────────────────────────────────────────

const MEMBER: AccountMember = {
  user_id: "u1",
  email: "person@acme.com",
  display_name: "Person",
  role: "owner",
  membership_status: "active",
  joined_at: "2026-07-01T00:00:00Z",
  last_active_at: null,
};

{
  const row = formatMemberRow(MEMBER);
  assert(row.label === "person@acme.com", "email is the preferred label");
  assert(row.everActive === false, "a member with no last_active_at has never signed in");
  assert(row.lastActiveAt === "Never signed in", "the member-specific never-copy is used");
}
{
  const row = formatMemberRow({ ...MEMBER, last_active_at: "2026-08-29T00:00:00Z" });
  assert(row.everActive === true, "a real last_active_at reads as having been active");
}

// ── formatAgentRow ────────────────────────────────────────────────────────

const AGENT: AccountAgent = {
  id: "a1",
  display_name: "Bookkeeper",
  definition_name: "bookkeeping",
  agent_kind: "specialist",
  status: "active",
  enabled: true,
  created_at: "2026-07-01T00:00:00Z",
  last_run_started_at: null,
  last_run_finished_at: null,
  last_run_outcome: null,
};

{
  const row = formatAgentRow(AGENT);
  assert(row.hasRun === false, "an agent with no run timestamps has never run");
  assert(row.lastRunAt === "Never run", "the agent-specific never-copy is used, distinct from a member's");
  assert(row.lastRunOutcome === "—", "no outcome renders an explicit dash");
}
{
  const row = formatAgentRow({ ...AGENT, last_run_started_at: "2026-08-29T00:00:00Z", last_run_outcome: "completed" });
  assert(row.hasRun === true, "a real run timestamp reads as having run");
  assert(row.lastRunOutcome === "completed", "a real outcome passes through");
}

// ── formatSpendRow ────────────────────────────────────────────────────────

const SPEND_ROW: AccountSpendRow = {
  provider: "anthropic",
  model: "claude-sonnet-5",
  credit_type: "ai_tokens",
  event_count: 40,
  total_platform_cost_usd: 3.5,
  total_credits_debited: 350,
};

{
  const row = formatSpendRow(SPEND_ROW);
  assert(row.label === "anthropic / claude-sonnet-5", "a normal AI-token row labels provider/model");
  assert(row.platformCost === "$3.50", "spend cost renders through the money formatter");
  assert(row.creditsDebited === "350", "credits render through the credits formatter, no dollar sign");
}
{
  const row = formatSpendRow({ provider: null, model: null, credit_type: "computer_runtime", event_count: 5, total_platform_cost_usd: 1, total_credits_debited: 100 });
  assert(row.label === "Agent Computer (VPS runtime)", "a NULL provider/model row is labelled as VPS runtime, not left blank");
}
{
  const row = formatSpendRow({ provider: null, model: null, credit_type: null, event_count: 0, total_platform_cost_usd: 0, total_credits_debited: 0 });
  assert(row.label === "Unknown", "a row with no identifying fields at all still gets an honest label, never a blank cell");
}

if (failed > 0) {
  console.error(`\n${failed} failed, ${passed} passed`);
  process.exit(1);
} else {
  console.log(`${passed} passed`);
}

import { agentComputerStatusBreakdown, overviewDetailStats, overviewSummaryStats, type OperatorOverview } from "./overview";

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

const OVERVIEW: OperatorOverview = {
  generated_at: "2026-08-30T00:00:00Z",
  rls_bypass_verified: true,
  totals: { users: 105, workspaces: 105, agents: 68, projects: 166, tasks: 36, documents: 5 },
  signups: { last_7_days: 2, last_30_days: 9, last_90_days: 40 },
  agents_runtime: { total: 68, with_runs: 12, never_run: 56 },
  workspaces_with_second_member: 0,
  workspaces_with_second_member_pct: 0,
  spend: { total_platform_cost_usd: 41.2345, total_credits_debited: 4123.45 },
  agent_computers: { total: 3, by_status: { active: 2, provisioning: 1 }, source: "agent_computers", excludes: "paired personal hardware" },
};

// ── overviewSummaryStats ─────────────────────────────────────────────────

{
  const stats = overviewSummaryStats(OVERVIEW);
  const byKey = Object.fromEntries(stats.map((s) => [s.key, s]));

  assert(byKey["users"]?.value === "105", "signed-up users total is present");
  assert(byKey["users"]?.isActivation === false, "signed-up users is acquisition, not activation");

  assert(byKey["agents-ran"]?.value === "12", "agents that ran uses the runtime count, not the total install count");
  assert(byKey["agents-ran"]?.caption === "of 68 created", "the caption gives the denominator agents were measured against");
  assert(byKey["agents-ran"]?.isActivation === true, "agents that ran is flagged as activation");

  assert(byKey["workspaces-invited"]?.value === "0", "MAN-149's own headline fact: nobody has invited anybody");
  assert(byKey["workspaces-invited"]?.caption === "0% of workspaces", "the invited caption carries a real denominator");

  assert(stats.every((s) => !("tasks" in s) && !("documents" in s)), "raw totals do not leak into the activation summary");
}

// ── overviewDetailStats ──────────────────────────────────────────────────

{
  const stats = overviewDetailStats(OVERVIEW);
  const byKey = Object.fromEntries(stats.map((s) => [s.key, s]));

  assert(byKey["tasks"]?.value === "36", "raw task total is in the detail set");
  assert(byKey["documents"]?.value === "5", "raw document total is in the detail set -- the founder's own headline number");
  assert(byKey["agents-never-run"]?.value === "56", "never-run agent count passes through");
  assert(byKey["signups-90d"]?.value === "40", "the 90-day signup trend is present");
  assert(byKey["platform-cost"]?.value === "$41.23", "spend renders through the shared money formatter");
  assert(byKey["credits-debited"]?.value === "4,123.45", "credits render through the credits formatter, never a dollar sign");
  assert(stats.every((s) => s.isActivation === false), "every detail stat is a plain total, never flagged as activation");
}

// ── agentComputerStatusBreakdown ─────────────────────────────────────────

{
  const rows = agentComputerStatusBreakdown({ provisioning: 1, active: 5, error: 2 });
  assert(rows.length === 3, "every status key becomes a row");
  assert(rows[0].status === "active" && rows[0].count === 5, "highest count sorts first");
  assert(rows[2].status === "provisioning", "lowest count sorts last");
}
{
  const rows = agentComputerStatusBreakdown({ b: 3, a: 3 });
  assert(rows[0].status === "a", "a tied count breaks ties alphabetically, so re-renders don't visibly reshuffle");
}
{
  const rows = agentComputerStatusBreakdown({});
  assert(rows.length === 0, "an empty status map is an empty list, not an error");
}

if (failed > 0) {
  console.error(`\n${failed} failed, ${passed} passed`);
  process.exit(1);
} else {
  console.log(`${passed} passed`);
}

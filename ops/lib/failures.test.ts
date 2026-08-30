import { FAILURES_DEFAULT_DAYS, FAILURES_MAX_DAYS, formatFailureEvent, formatFailureGroups, parseFailuresDays, type FailureEvent, type FailureGroup } from "./failures";

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

// ── parseFailuresDays ─────────────────────────────────────────────────────

assert(parseFailuresDays(null) === FAILURES_DEFAULT_DAYS, "no param falls back to the documented default (7)");
assert(parseFailuresDays("garbage") === FAILURES_DEFAULT_DAYS, "an unparseable value falls back to the default");
assert(parseFailuresDays("30") === 30, "a valid in-range value passes through");
assert(parseFailuresDays("0") === 1, "below the backend's floor (ge=1) clamps up to 1");
assert(parseFailuresDays("-5") === 1, "a negative value clamps to the floor");
assert(parseFailuresDays("9999") === FAILURES_MAX_DAYS, "above the backend's ceiling (le=90) clamps down to 90");

// ── formatFailureGroups ───────────────────────────────────────────────────

const GROUPS: FailureGroup[] = [
  { status: "failed", error_code: "provider_rate_limited", action_domain: "shell", event_count: 12, distinct_workspaces: 3, distinct_agents: 4, first_seen: "2026-08-24T00:00:00Z", last_seen: "2026-08-29T00:00:00Z" },
  { status: "blocked", error_code: null, action_domain: "connectors", event_count: 40, distinct_workspaces: 5, distinct_agents: 6, first_seen: "2026-08-25T00:00:00Z", last_seen: "2026-08-29T12:00:00Z" },
  { status: "denied", error_code: "policy_denied", action_domain: null, event_count: 5, distinct_workspaces: 1, distinct_agents: 1, first_seen: "2026-08-28T00:00:00Z", last_seen: "2026-08-28T00:00:00Z" },
];

{
  const rows = formatFailureGroups(GROUPS);
  assert(rows.length === 3, "every group becomes a row");
  assert(rows[0].eventCount === "40", "the largest group sorts first regardless of input order");
  assert(rows[0].barWidthPct === 100, "the largest group's bar is always full width");
  assert(Math.abs(rows[1].barWidthPct - (12 / 40) * 100) < 0.001, "a smaller group's bar width is relative to the largest, not the total");
  assert(rows.find((r) => r.codeLabel === "no error code") !== undefined, "a null error_code gets an honest label, never a blank cell");
  assert(rows.find((r) => r.domainLabel === "unknown domain") !== undefined, "a null action_domain gets an honest label");
}

assert(formatFailureGroups([]).length === 0, "no failures in the window is an empty list, not an error");

{
  const single: FailureGroup[] = [{ status: "failed", error_code: "x", action_domain: "shell", event_count: 0, distinct_workspaces: 0, distinct_agents: 0, first_seen: "2026-08-01T00:00:00Z", last_seen: "2026-08-01T00:00:00Z" }];
  const rows = formatFailureGroups(single);
  assert(rows[0].barWidthPct === 0, "an all-zero group (edge case) renders a zero-width bar, never NaN");
}

// ── formatFailureEvent ────────────────────────────────────────────────────

const EVENT: FailureEvent = {
  id: "evt_1",
  tenant_id: "t_1",
  workspace_id: "ws_1",
  agent_id: "a_1",
  action_domain: "shell",
  action_name: "execute",
  tool_kind: "shell",
  tool_id: "shell.execute",
  status: "failed",
  error_code: "provider_rate_limited",
  run_id: "run_1",
  trace_id: "trace_1",
  created_at: "2026-08-29T12:00:00Z",
};

{
  const row = formatFailureEvent(EVENT);
  assert(row.workspaceId === "ws_1", "the workspace id passes through for a link to its account detail page");
  assert(row.actionName === "execute", "a real action name passes through");
  assert(row.codeLabel === "provider_rate_limited", "a real error code passes through");
}
{
  const row = formatFailureEvent({ ...EVENT, action_name: null, error_code: null });
  assert(row.actionName === "—", "a missing action name renders an explicit dash");
  assert(row.codeLabel === "no error code", "a missing error code gets the same honest label as the grouped view");
}

if (failed > 0) {
  console.error(`\n${failed} failed, ${passed} passed`);
  process.exit(1);
} else {
  console.log(`${passed} passed`);
}

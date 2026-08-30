import {
  formatSpendByProviderModel,
  formatSpendByWorkspace,
  formatSpendOverTime,
  parseSpendDays,
  SPEND_DEFAULT_DAYS,
  SPEND_MAX_DAYS,
  type SpendByProviderModel,
  type SpendByWorkspace,
  type SpendOverTimeDay,
} from "./spend";

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

// ── parseSpendDays ────────────────────────────────────────────────────────

assert(parseSpendDays(null) === SPEND_DEFAULT_DAYS, "no param falls back to the documented default (30)");
assert(parseSpendDays("garbage") === SPEND_DEFAULT_DAYS, "an unparseable value falls back to the default");
assert(parseSpendDays("90") === 90, "a valid in-range value passes through");
assert(parseSpendDays("0") === 1, "below the backend's floor clamps to 1");
assert(parseSpendDays("999999") === SPEND_MAX_DAYS, "above the backend's ceiling (le=365) clamps down to 365");

// ── formatSpendByWorkspace ────────────────────────────────────────────────

const WORKSPACES: SpendByWorkspace[] = [
  { workspace_id: "ws_a", name: "Acme", total_platform_cost_usd: 5, total_credits_debited: 500, event_count: 10 },
  { workspace_id: "ws_b", name: "Beta", total_platform_cost_usd: 40, total_credits_debited: 4000, event_count: 80 },
];

{
  const rows = formatSpendByWorkspace(WORKSPACES);
  assert(rows[0].workspaceId === "ws_b", "the biggest spender sorts first regardless of input order");
  assert(rows[0].barWidthPct === 100, "the biggest spender's bar is full width");
  assert(Math.abs(rows[1].barWidthPct - (5 / 40) * 100) < 0.001, "a smaller spender's bar is relative to the biggest");
  assert(rows[1].creditsDebited === "500", "credits render without a dollar sign");
}
{
  const rows = formatSpendByWorkspace([{ workspace_id: "ws_c", name: "", total_platform_cost_usd: 0, total_credits_debited: 0, event_count: 0 }]);
  assert(rows[0].name === "ws_c", "a blank workspace name falls back to the workspace id, never a blank cell");
  assert(rows[0].barWidthPct === 0, "zero spend across the whole window renders a zero-width bar, never NaN");
}
assert(formatSpendByWorkspace([]).length === 0, "no spend in the window is an empty list, not an error");

// ── formatSpendByProviderModel ────────────────────────────────────────────

const PROVIDERS: SpendByProviderModel[] = [
  { provider: "anthropic", model: "claude-sonnet-5", credit_type: "ai_tokens", total_platform_cost_usd: 30, total_credits_debited: 3000, event_count: 60 },
  { provider: null, model: null, credit_type: "computer_runtime", total_platform_cost_usd: 10, total_credits_debited: 1000, event_count: 5 },
];

{
  const rows = formatSpendByProviderModel(PROVIDERS);
  assert(rows[0].label === "anthropic / claude-sonnet-5", "the bigger spend row sorts first");
  assert(rows.find((r) => r.label === "Agent Computer (VPS runtime)") !== undefined, "the VPS-runtime row (null provider/model) is labelled honestly, not blank");
}

// ── formatSpendOverTime ───────────────────────────────────────────────────

const OVER_TIME: SpendOverTimeDay[] = [
  { day: "2026-08-28T00:00:00Z", total_platform_cost_usd: 2, total_credits_debited: 200, event_count: 4 },
  { day: "2026-08-29T00:00:00Z", total_platform_cost_usd: 8, total_credits_debited: 800, event_count: 16 },
  { day: "2026-08-30T00:00:00Z", total_platform_cost_usd: 0, total_credits_debited: 0, event_count: 0 },
];

{
  const rows = formatSpendOverTime(OVER_TIME);
  assert(rows.length === 3, "every day becomes a bar");
  assert(rows[0].day === "2026-08-28T00:00:00Z", "chronological order is preserved -- never re-sorted by value");
  assert(rows[1].barHeightPct === 100, "the peak day is full height");
  assert(Math.abs(rows[0].barHeightPct - (2 / 8) * 100) < 0.001, "a smaller day's bar is relative to the peak");
  assert(rows[2].barHeightPct === 0, "a zero-spend day renders a zero-height bar, never NaN");
  assert(/Aug/.test(rows[0].dayLabel), "the day label is a compact calendar date, not a raw ISO timestamp");
}
assert(formatSpendOverTime([]).length === 0, "no days in the window is an empty series, not an error");

if (failed > 0) {
  console.error(`\n${failed} failed, ${passed} passed`);
  process.exit(1);
} else {
  console.log(`${passed} passed`);
}

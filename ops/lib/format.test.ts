import { countEntriesSorted, daysSince, formatCount, formatDate, formatPercent, formatTimestamp, spendSourceLabel } from "./format";

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

// ── formatCount ───────────────────────────────────────────────────────────

assert(formatCount(105) === "105", "a plain count formats as-is");
assert(formatCount(1234) === "1,234", "thousands get a separator");
assert(formatCount(0) === "0", "zero formats plainly, never blank");
assert(formatCount(null) === "0", "a missing count never renders as NaN or blank");
assert(formatCount(undefined) === "0", "an undefined count never renders as undefined");
assert(formatCount(Number.NaN) === "0", "an actual NaN never leaks through as text");

// ── formatPercent ─────────────────────────────────────────────────────────

assert(formatPercent(3.8) === "3.8%", "one decimal place, percent suffix");
assert(formatPercent(0) === "0%", "zero formats plainly");
assert(formatPercent(100) === "100%", "100 formats plainly, no trailing .0 noise");
assert(formatPercent(null) === "0%", "a missing percent never renders as NaN%");
assert(formatPercent(Number.NaN) === "0%", "an actual NaN never leaks through as text");

// ── formatTimestamp / formatDate ─────────────────────────────────────────

assert(formatTimestamp(null) === "Never", "no timestamp reads as Never, a fact about the member, not a formatting failure");
assert(formatTimestamp(undefined) === "Never", "undefined also reads as Never");
assert(formatTimestamp("not-a-date") === "Unknown", "an unparseable timestamp reads as Unknown -- different fact from Never");
assert(formatTimestamp(null, { never: "No activity yet" }) === "No activity yet", "the never-copy is overridable per caller");
assert(/2026/.test(formatTimestamp("2026-08-30T12:00:00Z")), "a real timestamp includes its year");

assert(formatDate(null) === "Never", "formatDate mirrors formatTimestamp's Never handling");
assert(formatDate("garbage") === "Unknown", "formatDate mirrors formatTimestamp's Unknown handling");
assert(/2026/.test(formatDate("2026-08-30T12:00:00Z")), "a real date includes its year");

// ── daysSince ─────────────────────────────────────────────────────────────

const NOW = new Date("2026-08-30T00:00:00Z");
assert(daysSince(null, NOW) === null, "no timestamp has no day count");
assert(daysSince("garbage", NOW) === null, "an unparseable timestamp has no day count");
assert(daysSince("2026-08-30T00:00:00Z", NOW) === 0, "the same instant is zero days");
assert(daysSince("2026-08-20T00:00:00Z", NOW) === 10, "ten days back is 10");
assert(daysSince("2026-09-05T00:00:00Z", NOW) === 0, "a future timestamp (clock skew) clamps to 0, never negative");

// ── countEntriesSorted ────────────────────────────────────────────────────

{
  const rows = countEntriesSorted({ provisioning: 1, active: 5, error: 2 });
  assert(rows.length === 3, "every key becomes a row");
  assert(rows[0].key === "active" && rows[0].count === 5, "highest count sorts first");
  assert(rows[2].key === "provisioning", "lowest count sorts last");
}
assert(countEntriesSorted({ b: 3, a: 3 })[0].key === "a", "a tied count breaks ties alphabetically");
assert(countEntriesSorted({}).length === 0, "an empty record is an empty list, not an error");
assert(countEntriesSorted({ x: Number.NaN })[0].count === 0, "a non-finite count coerces to 0 rather than corrupting the sort");

// ── spendSourceLabel ──────────────────────────────────────────────────────

assert(spendSourceLabel({ provider: "anthropic", model: "claude-sonnet-5", credit_type: "ai_tokens" }) === "anthropic / claude-sonnet-5", "a normal AI-token row labels provider/model");
assert(spendSourceLabel({ provider: null, model: null, credit_type: "computer_runtime" }) === "Agent Computer (VPS runtime)", "a NULL provider/model computer_runtime row is labelled as VPS runtime, not blank");
assert(spendSourceLabel({ provider: null, model: null, credit_type: null }) === "Unknown", "no identifying fields at all still gets an honest label");
assert(spendSourceLabel({ provider: "anthropic", model: null, credit_type: "ai_tokens" }) === "anthropic", "a provider with no model still labels with what's known");

if (failed > 0) {
  console.error(`\n${failed} failed, ${passed} passed`);
  process.exit(1);
} else {
  console.log(`${passed} passed`);
}

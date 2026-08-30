import { formatCredits, formatUsd } from "./money";

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

assert(formatUsd(0) === "$0.00", "zero spend is a real fact, stays $0.00");
assert(formatUsd(4.2) === "$4.20", "a plain amount gets two decimals");
assert(formatUsd(1234.5) === "$1234.50", "no thousands separator needed for a plain money string");
assert(formatUsd(0.001) === "<$0.01", "real spend below the display floor never claims $0.00");
assert(formatUsd(0.004999) === "<$0.01", "just under the floor still reads as <$0.01");
assert(formatUsd(0.005) === "$0.01", "at the floor, rounds up to a real cent");
assert(formatUsd(null) === "$0.00", "a missing amount never renders as NaN");
assert(formatUsd(undefined) === "$0.00", "an undefined amount never renders as undefined");
assert(formatUsd(Number.NaN) === "$0.00", "an actual NaN never leaks through as text");
assert(formatUsd(-3.5) === "$-3.50", "a negative amount (a credit/refund) is not floored -- only positive sub-cent spend is");

// ── formatCredits ─────────────────────────────────────────────────────────

assert(formatCredits(4123.45) === "4,123.45", "credits get thousands grouping and up to two decimals, no currency symbol");
assert(formatCredits(0) === "0", "zero credits formats plainly");
assert(formatCredits(100) === "100", "a whole number of credits has no trailing .00 noise");
assert(formatCredits(null) === "0", "a missing credits amount never renders as NaN");
assert(formatCredits(Number.NaN) === "0", "an actual NaN never leaks through as text");
assert(!formatCredits(4123.45).includes("$"), "credits are never rendered with a dollar sign -- the rate is not final (CLAUDE.md)");

if (failed > 0) {
  console.error(`\n${failed} failed, ${passed} passed`);
  process.exit(1);
} else {
  console.log(`${passed} passed`);
}

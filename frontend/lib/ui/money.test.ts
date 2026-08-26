/**
 * formatUsd — behaviour, plus a drift scan.
 *
 * The scan is the half a behavioural test cannot do: `toFixed(4)` coming back
 * at a tenth call site type-checks, renders, and is silent. This module exists
 * precisely because that shape was copy-pasted into five files before anyone
 * noticed there was no single place to change it.
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { formatUsd } from "./money";

let passed = 0;
let failed = 0;

function check(name: string, cond: boolean, detail = "") {
  if (cond) {
    passed += 1;
  } else {
    failed += 1;
    console.error(`  ✗ ${name}${detail ? ` — ${detail}` : ""}`);
  }
}

// ── Founder's instruction, 2026-08-26: two decimals, not four.
check("two decimals", formatUsd(12.3456) === "$12.35", formatUsd(12.3456));
check("zero is $0.00", formatUsd(0) === "$0.00", formatUsd(0));
check("whole number keeps both decimals", formatUsd(7) === "$7.00", formatUsd(7));
// 1.005 is not representable in binary floating point — it is stored as
// 1.00499999999999989, so toFixed(2) yields "$1.00". Asserting "$1.01" here
// would be asserting a wish rather than the behaviour, and the fix would be
// to add rounding machinery to a money label for a case nobody can perceive.
// Pinned as-is so the next reader knows this was measured, not overlooked.
check("binary float floor at the half-cent", formatUsd(1.005) === "$1.00", formatUsd(1.005));
check("an unambiguous half-cent still rounds up", formatUsd(1.006) === "$1.01", formatUsd(1.006));

// ── The sub-cent case. A real agent turn can cost a fraction of a cent, and
//    "$0.00" would state ZERO for money actually spent — two different facts
//    sharing one signal, which this codebase has a standing law against.
check("real sub-cent spend never renders as zero", formatUsd(0.0003) === "<$0.01", formatUsd(0.0003));
check("just under the floor", formatUsd(0.0049) === "<$0.01", formatUsd(0.0049));
check("at the floor rounds normally", formatUsd(0.005) === "$0.01", formatUsd(0.005));
check("sub-cent and zero are DIFFERENT strings", formatUsd(0.0003) !== formatUsd(0));

// ── Defensive: these reach the UI from API payloads that may be absent.
check("null is zero", formatUsd(null) === "$0.00", formatUsd(null));
check("undefined is zero", formatUsd(undefined) === "$0.00", formatUsd(undefined));
check("NaN is zero, not '$NaN'", formatUsd(Number.NaN) === "$0.00", formatUsd(Number.NaN));
check("Infinity is zero, not '$Infinity'", formatUsd(Number.POSITIVE_INFINITY) === "$0.00");

// ── Drift scan: no call site may format money itself again.
const ROOTS = [join(__dirname, "..", "..", "lib"), join(__dirname, "..", "..", "app")];
const SELF = "lib/ui/money";

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    if (entry === "node_modules" || entry === ".next") continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) walk(full, out);
    else if (/\.tsx?$/.test(full)) out.push(full);
  }
  return out;
}

const files = ROOTS.flatMap((r) => walk(r));
// CANARY: if the scan stops reaching real source, it enforces nothing while
// reporting green — the exact preflight._check_rls trap this repo documents.
check("canary: scan reaches real files", files.length > 200, `${files.length} files`);

/**
 * Allowlist — each entry carries a WRITTEN REASON, per the house pattern.
 *
 * A UNIT RATE is not an amount spent, and formatUsd is wrong for it. A model
 * price of `~$0.003 / 1K tokens` rendered as `<$0.01 / 1K tokens` destroys
 * the one number the row exists to show — the customer is comparing rates,
 * not reading a bill. That call site deliberately widens to 3 decimals below
 * a cent, which is the correct decision for a rate and the wrong one for a
 * total. Do not "fix" it by routing it through formatUsd.
 */
const ALLOWLIST = new Set(["lib/workspace/fleet/FleetAgentDetail.tsx"]);

const offenders = files.filter((f) => {
  if (f.includes(SELF)) return false; // this module and its own test
  const rel = f.split("/frontend/")[1] ?? f;
  if (ALLOWLIST.has(rel)) return false;
  const src = readFileSync(f, "utf8");
  // A money-shaped literal built by hand rather than through formatUsd.
  return /\$\$\{[^}]*\.toFixed\(/.test(src) || /toFixed\(4\)/.test(src);
});
check(
  "no hand-rolled money formatting outside lib/ui/money",
  offenders.length === 0,
  offenders.map((f) => f.split("/frontend/")[1]).join(", "),
);

// CANARY: the matcher must actually be capable of matching.
const MATCHER_PROBE = "`$" + "${n.toFixed(4)}`";
check(
  "canary: matcher still detects the banned shape",
  /\$\$\{[^}]*\.toFixed\(/.test(MATCHER_PROBE),
);

console.log(`money: ${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);

/**
 * MAN-317: imports the REAL rule (planAgentCountShape) rather than
 * re-deriving the thresholds here — same discipline
 * openclaw-channel-copy.test.ts already applies to channel-doors.ts's
 * planDoors, for the identical reason (CLAUDE.md: "a check that derives
 * its own expectations from the thing it checks is blind, and reports
 * 'passed'"). The expected shape (this file) and the actual shape
 * (agent-count-shape.ts) come from two different places.
 *
 * Run: npx tsx lib/workspace/fleet/agent-count-shape.test.ts
 */

import { planAgentCountShape } from "./agent-count-shape";

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

// The three thresholds the whole feature hangs off.
assert(planAgentCountShape(0) === "none", "zero real agents -> none");
assert(planAgentCountShape(1) === "solo", "exactly one real agent -> solo");
assert(planAgentCountShape(2) === "fleet", "two real agents -> fleet");
assert(planAgentCountShape(5) === "fleet", "five real agents -> fleet, same as two");
assert(planAgentCountShape(200) === "fleet", "a large fleet is still just fleet — no further mode split");

// Defensive: a negative count should never occur (Array.length can't be
// negative), but the function must not silently mis-round it into "solo" —
// it reads as "nothing", same as zero.
assert(planAgentCountShape(-1) === "none", "a defensively-negative count still reads as none, never solo");

// The three modes are the only three modes — no accidental fourth string
// value can leak out for any input in the domain this is ever called with.
for (const n of [0, 1, 2, 3, 10, 1000]) {
  const mode = planAgentCountShape(n);
  assert(mode === "none" || mode === "solo" || mode === "fleet", `planAgentCountShape(${n}) returns one of the three modes, got ${mode}`);
}

// Monotonic: the mode never regresses as the count grows — reversibility
// (MAN-317: "the moment a second agent exists, the fleet shape must appear
// on its own with no setting to flip") depends on this holding for every
// count, not just the three named thresholds.
const RANK: Record<string, number> = { none: 0, solo: 1, fleet: 2 };
for (let n = 0; n < 20; n++) {
  assert(
    RANK[planAgentCountShape(n)] <= RANK[planAgentCountShape(n + 1)],
    `mode rank is non-decreasing from count ${n} to ${n + 1}`,
  );
}

// --- Summary ---

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}

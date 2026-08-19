/**
 * Imports the real rules (planAgentProfileSegments, isProfileTab) rather
 * than re-deriving them here — same discipline agent-count-shape.test.ts
 * already applies.
 *
 * Run: npx tsx lib/workspace/fleet/agent-profile-shape.test.ts
 */

import { PROFILE_TAB_IDS, defaultAgentProfileSegment, isProfileTab, planAgentProfileSegments } from "./agent-profile-shape";

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

// A specialist gets both segments, Persona first (it's what he described
// first: "a specific prompt like system prompt").
assert(
  JSON.stringify(planAgentProfileSegments(false)) === JSON.stringify(["persona", "memory"]),
  "a specialist agent's profile shows persona then memory",
);
assert(defaultAgentProfileSegment(false) === "persona", "a specialist's profile opens on persona by default");

// The workspace master has no per-agent persona field anything reads
// (specialist_runtime_context.resolve_specialist_runtime_context returns
// None for it) — a persona editor there would be a dead control.
assert(
  JSON.stringify(planAgentProfileSegments(true)) === JSON.stringify(["memory"]),
  "the master agent's profile shows memory only, never a dead persona editor",
);
assert(defaultAgentProfileSegment(true) === "memory", "the master's profile opens on memory, its only segment");

// isProfileTab / PROFILE_TAB_IDS agree with each other and with what
// planAgentProfileSegments can ever emit — the set FleetAgentDetail.tsx's
// sheetOpen check reads must never fall behind the segments actually
// rendered.
assert(isProfileTab("persona"), "persona is a profile tab");
assert(isProfileTab("memory"), "memory is a profile tab");
assert(!isProfileTab("model"), "model (a Configure tab) is not a profile tab");
assert(!isProfileTab("chat"), "chat (a top-level tab) is not a profile tab");
assert(!isProfileTab(""), "an empty string is not a profile tab");
for (const isMaster of [true, false]) {
  for (const segment of planAgentProfileSegments(isMaster)) {
    assert(PROFILE_TAB_IDS.has(segment), `every planned segment (${segment}) is in PROFILE_TAB_IDS`);
    assert(isProfileTab(segment), `every planned segment (${segment}) passes isProfileTab`);
  }
}

// --- Summary ---

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);

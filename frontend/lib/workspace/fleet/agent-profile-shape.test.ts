/**
 * Imports the real rules (planAgentProfileSegments, isProfileTab) rather
 * than re-deriving them here — same discipline agent-count-shape.test.ts
 * already applies.
 *
 * Run: npx tsx lib/workspace/fleet/agent-profile-shape.test.ts
 */

import { readFileSync } from "node:fs";
import path from "node:path";

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

// A specialist gets all three, Persona first (it's what he described first:
// "a specific prompt like system prompt"). Skills joined them 2026-08-28 —
// see agent-profile-shape.ts's header for why it left Configure > Brain.
assert(
  JSON.stringify(planAgentProfileSegments(false)) === JSON.stringify(["persona", "skills", "memory"]),
  "a specialist agent's profile shows persona, then skills, then memory",
);
assert(defaultAgentProfileSegment(false) === "persona", "a specialist's profile opens on persona by default");

// The workspace master has no per-agent persona field anything reads
// (specialist_runtime_context.resolve_specialist_runtime_context returns
// None for it) — a persona editor there would be a dead control. Skills is
// NOT excluded the same way, and that asymmetry is deliberate: it had no
// isMaster guard in Configure either, so keeping it preserves the behaviour
// that shipped rather than quietly taking a control away.
assert(
  JSON.stringify(planAgentProfileSegments(true)) === JSON.stringify(["skills", "memory"]),
  "the master agent's profile shows skills and memory, never a dead persona editor",
);
assert(defaultAgentProfileSegment(true) === "skills", "the master's profile opens on its own first segment");

// isProfileTab / PROFILE_TAB_IDS agree with each other and with what
// planAgentProfileSegments can ever emit — the set FleetAgentDetail.tsx's
// sheetOpen check reads must never fall behind the segments actually
// rendered.
assert(isProfileTab("persona"), "persona is a profile tab");
assert(isProfileTab("skills"), "skills is a profile tab");
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

// ── SKILLS ACTUALLY LEFT CONFIGURE, AND ACTUALLY ARRIVED HERE ────────────
// Two halves, and only asserting the first would leave a tab that is offered
// by nothing and a tab that is rendered by nothing — either alone is the
// dead control this move exists to fix. Read from the real surface, because
// a pure module cannot see whether anyone wired it.
const DETAIL_TSX = readFileSync(
  path.resolve(__dirname, "FleetAgentDetail.tsx"),
  "utf8",
);
assert(DETAIL_TSX.includes("CONFIGURE_GROUPS"), "CANARY: FleetAgentDetail.tsx was read and still declares CONFIGURE_GROUPS");

const brainGroup = /\{ id: "brain", label: "Brain", tabs: \[([^\]]*)\] \}/.exec(DETAIL_TSX);
assert(Boolean(brainGroup), "CANARY: the Brain group is still a literal this scan can parse");
assert(
  !/["']skills["']/.test(brainGroup?.[1] ?? "skills"),
  "Skills is no longer grouped under Brain — it is not what an agent thinks with",
);
// It must not have simply moved sideways into another Configure group either.
const configureGroupsBlock = /const CONFIGURE_GROUPS[\s\S]*?\n\];/.exec(DETAIL_TSX);
assert(Boolean(configureGroupsBlock), "CANARY: the CONFIGURE_GROUPS block is findable");
assert(
  !/tabs: \[[^\]]*["']skills["']/.test(configureGroupsBlock?.[0] ?? ""),
  "Skills is in NO Configure group — it is a Profile segment now",
);
assert(
  /skills: \{ label: "Skills"/.test(DETAIL_TSX),
  "the Profile rail has a Skills segment definition to render",
);
assert(
  /activeTab === "skills" && \(\s*<SkillsTab/.test(DETAIL_TSX),
  "SkillsTab is still actually rendered — moved, never dropped",
);

// --- Summary ---

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);

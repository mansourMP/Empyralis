/**
 * Imports the REAL rules from agent-create-card.ts rather than
 * re-deriving them here — same discipline agent-count-shape.test.ts
 * already applies to planAgentCountShape, for the identical reason
 * (CLAUDE.md: "a check that derives its own expectations from the thing
 * it checks is blind, and reports 'passed'").
 *
 * Run: npx tsx lib/workspace/fleet/agent-create-card.test.ts
 */

import { resolveAgentCreateName } from "./agent-create-card";

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

// ── resolveAgentCreateName ───────────────────────────────────────────────

assert(resolveAgentCreateName("", "Basalt") === "Basalt", "empty typed name falls back to the suggestion");
assert(resolveAgentCreateName("   ", "Basalt") === "Basalt", "whitespace-only typed name still falls back");
assert(resolveAgentCreateName("Ruby", "Basalt") === "Ruby", "a typed name always wins over the suggestion");
assert(resolveAgentCreateName("", "") === "", "no suggestion yet and nothing typed is an honest empty string");

// ── Summary ───────────────────────────────────────────────────────────────

console.log(`${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);

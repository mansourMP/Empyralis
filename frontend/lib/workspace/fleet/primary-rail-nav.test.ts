/**
 * Project-as-spine navigation: proves the primary rail's top-level
 * destinations are exactly Inbox + Projects — Conversations and Agents are
 * GONE, not reordered — by importing the REAL list (RAIL_ITEMS) PrimaryRail.tsx
 * renders, same "two independent sources" discipline agent-count-shape.test.ts
 * already applies.
 *
 * Run: npx tsx lib/workspace/fleet/primary-rail-nav.test.ts
 */

import { RAIL_ITEMS, visibleRailItems } from "./primary-rail-nav";

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

const keys = RAIL_ITEMS.map((i) => i.key);

assert(keys.includes("inbox"), "Inbox is a top-level rail destination");
assert(keys.includes("projects"), "Projects is a top-level rail destination — the spine");
assert(!keys.includes("agents"), "Agents is NOT a top-level rail destination — it lives inside its project");
assert(!keys.includes("conversations"), "Conversations is NOT a top-level rail destination — it lives inside its project");
assert(keys.length === 2, `exactly two top-level destinations remain, got ${keys.length} (${keys.join(", ")})`);

// Inbox is first — "what needs me" is the entry point; Projects (the spine)
// follows it.
assert(RAIL_ITEMS[0]?.key === "inbox", "Inbox is the first rail row");
assert(RAIL_ITEMS[1]?.key === "projects", "Projects is the second rail row");

// Aggregation tagging: Inbox still aggregates across every agent (hides at
// zero agents); Projects never does (it's the workspace's own data, not a
// view of the fleet).
assert(RAIL_ITEMS.find((i) => i.key === "inbox")?.aggregatesAgents === true, "Inbox is tagged as an agent-aggregating surface");
assert(!RAIL_ITEMS.find((i) => i.key === "projects")?.aggregatesAgents, "Projects is never tagged as agent-aggregating");

// visibleRailItems composes the SAME array — hiding aggregations at zero
// agents must not invent a third list that could drift from RAIL_ITEMS.
const hidden = visibleRailItems(true);
assert(hidden.length === 1 && hidden[0]?.key === "projects", "at zero agents, only Projects remains visible");
const shown = visibleRailItems(false);
assert(shown.length === RAIL_ITEMS.length, "with agents present, every rail item is visible");
assert(shown === RAIL_ITEMS, "the non-hidden branch returns the real array, not a copy — no drift possible");

// Every chord is unique — a duplicate would make "g <key>" ambiguous.
const chords = RAIL_ITEMS.map((i) => i.chord);
assert(new Set(chords).size === chords.length, "every rail item has a unique keyboard chord");

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}

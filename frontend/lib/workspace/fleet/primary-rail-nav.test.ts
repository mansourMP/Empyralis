/**
 * The rail's destinations, proven by importing the REAL list (RAIL_ITEMS)
 * PrimaryRail.tsx renders — same "two independent sources" discipline
 * agent-count-shape.test.ts already applies.
 *
 * Three things this file is here to hold still (see primary-rail-nav.ts's
 * own header for the reasoning): the rail's flat shape is WORK ONLY — Inbox,
 * My work, Projects; nothing that left the rail stopped being reachable
 * (Settings has the account menu and its own rail space); and the keyboard
 * shortcuts reference can never again advertise a chord that is not bound
 * (it derives from this same list).
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

assert(keys.includes("inbox"), "Inbox is a rail destination");
assert(keys.includes("my-work"), "My work is a rail destination — 'what is assigned to me, across every project'");
assert(keys.includes("projects"), "Projects is a rail destination — the spine");

// The rail holds WORK only (founder, 2026-08-16). Settings had two doors —
// a rail row and an account-menu row — and the rail one didn't earn its
// place. The /settings routes and the account-menu link are both untouched,
// and inside Settings the rail becomes the Settings space
// (primary-rail-space.ts), so nothing became unreachable.
assert(!keys.includes("settings"), "Settings is NOT a rail destination — the account menu is its door");

// Project-as-spine still holds: neither of these comes back as a TOP-LEVEL
// row. Both used to aggregate across every project's agents, which is the
// boundary "an agent belongs to its project and works only there" says a nav
// surface must not reach past. Their ROUTES are still live and unlinked.
assert(!keys.includes("agents"), "Agents is NOT a rail destination — it lives inside its project");
assert(!keys.includes("conversations"), "Conversations is NOT a rail destination");

// Deliberate omission, recorded so it is never re-derived as an oversight:
// per-agent activity already exists on the agent's own Work tab, and an
// unused top-level surface is what "a surface must earn its place" is about.
assert(!keys.includes("activity"), "Activity is deliberately NOT a rail destination");

assert(keys.length === 3, `exactly three destinations, got ${keys.length} (${keys.join(", ")})`);

// Order is the reading order of the founder's own approved sketch: what
// needs me, then what is mine, then where the work lives.
assert(RAIL_ITEMS[0]?.key === "inbox", "Inbox is the first rail row");
assert(RAIL_ITEMS[1]?.key === "my-work", "My work is the second rail row");
assert(RAIL_ITEMS[2]?.key === "projects", "Projects is the third rail row");

// ── Aggregation tagging ───────────────────────────────────────────────────
assert(RAIL_ITEMS.find((i) => i.key === "inbox")?.aggregatesAgents === true, "Inbox is tagged as an agent-aggregating surface");
assert(!RAIL_ITEMS.find((i) => i.key === "projects")?.aggregatesAgents, "Projects is never tagged as agent-aggregating");
assert(
  !RAIL_ITEMS.find((i) => i.key === "my-work")?.aggregatesAgents,
  "My work aggregates TASKS, not agents — a task assigned to a person exists with zero agents, so hiding it there would hide real work",
);

// visibleRailItems composes the SAME array — hiding aggregations at zero
// agents must not invent a second list that could drift from RAIL_ITEMS.
const hidden = visibleRailItems(true);
assert(
  hidden.map((i) => i.key).join(",") === "my-work,projects",
  `at zero agents only Inbox hides, got ${hidden.map((i) => i.key).join(",")}`,
);
const shown = visibleRailItems(false);
assert(shown.length === RAIL_ITEMS.length, "with agents present, every rail item is visible");
assert(shown === RAIL_ITEMS, "the non-hidden branch returns the real array, not a copy — no drift possible");

// ── Chords ────────────────────────────────────────────────────────────────
const chords = RAIL_ITEMS.map((i) => i.chord);
assert(new Set(chords).size === chords.length, "every rail item has a unique keyboard chord");
assert(
  RAIL_ITEMS.every((i) => i.chord.length === 1 && i.chord === i.chord.toLowerCase()),
  "every chord is a single lowercase key — the handler lowercases the event key before matching",
);

// Every row must be a real route segment, or the rail links nowhere.
assert(
  RAIL_ITEMS.every((i) => Boolean(i.segment) && !i.segment.includes("/")),
  "every rail item names a single, non-empty route segment under /w/{id}",
);

// ── A rail destination must actually RENDER ───────────────────────────────
// FleetShellDecider gates which workspace segments render their page inside
// the shell. A segment missing from its allowlist renders the rail and the
// topbar and NOTHING ELSE — no error, no warning, `next build` still lists
// the route. That fired for real when "My work" was added (blank page, rail
// link working perfectly) and had fired once before for "sage". It is fixed
// structurally — the decider now derives its rail half from RAIL_ITEMS — and
// this asserts the derivation is still there, because a behavioural test
// cannot catch someone replacing it with a transcribed list that happens to
// be correct on the day they write it.
import { readFileSync } from "node:fs";
const deciderSource = readFileSync(new URL("./FleetShellDecider.tsx", import.meta.url), "utf8");
assert(
  /RAIL_ITEMS\.map\(\s*\(\s*item\s*\)\s*=>\s*item\.segment\s*\)/.test(deciderSource),
  "FleetShellDecider derives its shell segments from RAIL_ITEMS — never a hand-copied list that renders a new rail destination blank",
);
assert(
  deciderSource.includes('from "./primary-rail-nav"'),
  "FleetShellDecider imports the real rail list rather than re-declaring one",
);
for (const item of RAIL_ITEMS) {
  assert(
    !new RegExp(`"${item.segment}",`).test(
      deciderSource.slice(deciderSource.indexOf("NON_RAIL_SHELL_SEGMENTS")),
    ),
    `"${item.segment}" is not ALSO hand-listed in NON_RAIL_SHELL_SEGMENTS — one source per segment`,
  );
}
// Settings left RAIL_ITEMS (2026-08-16), so the derivation above no longer
// covers its segment — it must be hand-listed in NON_RAIL_SHELL_SEGMENTS or
// every settings page renders the rail and a blank pane, the exact silent
// failure the derivation exists to prevent.
assert(
  /"settings",/.test(deciderSource.slice(deciderSource.indexOf("NON_RAIL_SHELL_SEGMENTS"))),
  '"settings" is hand-listed in NON_RAIL_SHELL_SEGMENTS now that it is not a rail item',
);

// ── The shortcuts reference derives from THIS list ────────────────────────
// The old hand-kept list advertised "Go to Conversations · G C" and "Go to
// Agents · G A" long after both left navigation — chords bound to nothing,
// listed as features. The reference (KeyboardShortcutsSection.tsx) must
// DERIVE its go-to rows from RAIL_ITEMS, the same array the keydown handler
// matches against, so an unbound chord is unlistable by construction. A
// behavioural test cannot catch a re-transcribed list that is correct on the
// day it is written; this structural one can.
const shortcutsSource = readFileSync(new URL("./KeyboardShortcutsSection.tsx", import.meta.url), "utf8");
assert(
  shortcutsSource.includes('from "./primary-rail-nav"'),
  "KeyboardShortcutsSection imports the real rail list",
);
assert(
  /RAIL_ITEMS\.map\(/.test(shortcutsSource),
  "KeyboardShortcutsSection derives its go-to rows from RAIL_ITEMS, never a hand-kept list",
);
assert(
  !/Go to [A-Z]/.test(shortcutsSource),
  "no hand-written 'Go to <surface>' row exists — every one is derived, so none can go stale",
);

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}

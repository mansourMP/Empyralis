/**
 * The Projects page's zero-projects-empty-state rule, driven through the
 * REAL planFirstAgentPrompt the page renders against — the same "expected
 * and actual come from different places" discipline agent-count-shape.test.ts
 * / create-accent.test.ts already apply.
 *
 * Plus structural assertions a behavioural test structurally cannot make:
 * that the page actually CALLS this (the "built, tested, and never wired"
 * defect this codebase has more of than any other), and — the other
 * direction of that same defect — that the agent-creation BAND this module
 * used to also drive is actually GONE from the page and from
 * first-agent-empty.tsx, not merely unreachable dead code left lying
 * around for the next author to revive by accident (founder ruling,
 * 2026-09-01: a project's own surfaces are about that project, never about
 * creating an agent).
 *
 * Run: npx tsx lib/workspace/fleet/workspace-first-run.test.ts
 */

import { readFileSync } from "node:fs";

import { planFirstAgentPrompt, type FirstAgentPromptState } from "./workspace-first-run";

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

const settled: FirstAgentPromptState = { projectsKnown: true, projectCount: 1 };

// ── "no projects" and "haven't asked yet" are different facts ────────────────
assert(
  planFirstAgentPrompt({ projectsKnown: false, projectCount: 0 }) === "none",
  "nothing is claimed before the project list has ever settled",
);
assert(
  planFirstAgentPrompt({ projectsKnown: true, projectCount: 0 }) === "full",
  "a settled, genuinely empty project list gets the centred empty state",
);
assert(
  planFirstAgentPrompt(settled) === "none",
  "a settled workspace with a real project (its own, or an inherited 'General') gets nothing — the real list is on screen",
);

// ── it disappears on its own ──────────────────────────────────────────────
assert(
  planFirstAgentPrompt({ projectsKnown: true, projectCount: 7 }) === "none",
  "an established workspace sees nothing — recomputed from the live count, no flag to unset",
);

// A negative count is not a state the caller can reach, but a `<=` rather
// than a `===` is the difference between degrading to the teaching state and
// falling through to a list nobody can act on.
assert(
  planFirstAgentPrompt({ projectsKnown: true, projectCount: -1 }) === "full",
  "a nonsense project count degrades to FULL",
);

/** Line and block comments stripped — same discipline create-accent.test.ts's
 *  own codeOnly() applies, so this file's and the real components' own prose
 *  (which narrates exactly the deleted names, on purpose, as history) cannot
 *  trip a guard aimed at rendered code. */
function codeOnly(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/[^\n]*/g, "$1");
}

// ── STRUCTURAL: the page actually renders this ────────────────────────────
const pageSourceRaw = readFileSync(
  new URL("../../../app/(account)/w/[workspaceId]/projects/page.tsx", import.meta.url),
  "utf8",
);
const pageSource = codeOnly(pageSourceRaw);
// Canary: if this read stops reaching the real page, every assertion below
// passes vacuously and reports green. Same discipline as
// primary-rail-nav.test.ts's FleetShellDecider scan.
assert(pageSource.includes("fleet-content-main"), "CANARY: the projects page source was actually read");

assert(
  pageSource.includes("planFirstAgentPrompt"),
  "the projects page CALLS the plan — built-and-never-wired is this codebase's most common defect",
);
assert(
  /listIsEmpty:\s*firstAgentPrompt === "full"/.test(pageSource),
  "the header's accent ownership is fed the plan's own answer, never a re-derived condition",
);
// Sticky latch, never a live `!loading` — fleet-data.ts re-raises loading on
// every background poll.
assert(/setProjectsSettled\(true\)/.test(pageSource), "the known-latch is sticky, so a 30s background poll cannot re-flash the empty state");
assert(/projectsKnown: projectsSettled/.test(pageSource), "the plan is handed the latch, not a live loading flag");
// Archived is a filtered VIEW, not the workspace being empty.
assert(/showArchived\s*\?\s*"none"/.test(pageSource), "the archived view is never offered the empty-state accent");

// ── STRUCTURAL: the agent-creation band is actually GONE, not just unused ──
// "Built, tested, and never wired" usually means dead code nobody deleted
// after its one caller stopped calling it — that trap runs BOTH directions:
// leaving CreateFirstAgentEmpty's variant="band" branch and FirstAgentBand
// sitting in first-agent-empty.tsx with zero remaining callers is exactly
// the shape a future author "revives" on some other project-adjacent page,
// reopening the bug the founder just closed.
assert(
  !/variant\s*=\s*"band"/.test(pageSource),
  "the Projects page does not render CreateFirstAgentEmpty's band variant — deleted, not merely unrendered",
);
assert(
  !/CreateFirstAgentEmpty/.test(pageSource),
  "the Projects page does not import or render CreateFirstAgentEmpty at all any more — agent creation is not this page's business",
);

const firstAgentEmptySource = codeOnly(readFileSync(new URL("./first-agent-empty.tsx", import.meta.url), "utf8"));
assert(
  firstAgentEmptySource.includes("export function CreateFirstAgentEmpty"),
  "CANARY: first-agent-empty.tsx was actually read",
);
assert(
  !/FirstAgentBand/.test(firstAgentEmptySource),
  "FirstAgentBand no longer exists — the component the band rendered is deleted, not dead-coded",
);
assert(
  !/variant/.test(firstAgentEmptySource),
  "CreateFirstAgentEmpty no longer takes a variant prop — there is only one offer left (the centred state)",
);

// CSS has no comment syntax this file's own prose would collide with the
// same way — no codeOnly() needed for it.
const cssSource = readFileSync(new URL("./fleet-theme.css", import.meta.url), "utf8");
assert(cssSource.includes("fleet-composer-foot"), "CANARY: fleet-theme.css was actually read");
assert(
  !/^\.fleet-first-run\s*\{/m.test(cssSource),
  "the band's own CSS rule is gone, not an orphaned style nothing renders any more",
);

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}

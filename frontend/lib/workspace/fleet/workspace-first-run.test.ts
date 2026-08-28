/**
 * The first-run offer's rule, driven through the REAL planFirstAgentPrompt the
 * Projects page renders against — the same "expected and actual come from
 * different places" discipline agent-count-shape.test.ts / create-accent.test.ts
 * already apply.
 *
 * Plus structural assertions a behavioural test structurally cannot make: that
 * the page actually CALLS this (the "built, tested, and never wired" defect
 * this codebase has more of than any other), that the band renders above the
 * list rather than replacing it, and that the header's "New project" stopped
 * being an unconditional accent fill.
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

const settled: FirstAgentPromptState = {
  projectsKnown: true,
  projectCount: 1,
  agentsKnown: true,
  realAgentCount: 0,
};

// ── THE BUG THIS EXISTS TO CLOSE ─────────────────────────────────────────────
// Every workspace bootstraps a "General" project, so projectCount is 1 on a
// brand-new signup and the old `projects.length === 0` gate could never fire.
// This is the exact state a customer lands in seconds after signing up.
assert(
  planFirstAgentPrompt(settled) === "band",
  "a bootstrapped workspace with its General project and no agents gets the BAND",
);

// ── "no agents" and "haven't asked yet" are different facts ──────────────────
assert(
  planFirstAgentPrompt({ ...settled, projectsKnown: false }) === "none",
  "nothing is claimed before the project list has ever settled",
);
assert(
  planFirstAgentPrompt({ ...settled, agentsKnown: false }) === "none",
  "nothing is claimed before the agent list has ever settled",
);
// The shape that would flash the band at an established workspace: both lists
// return [] while loading, and fleet-data.ts re-raises `loading` on every 30s
// poll, so a plan keyed on length alone would re-accuse twice a minute.
assert(
  planFirstAgentPrompt({ projectsKnown: false, projectCount: 0, agentsKnown: false, realAgentCount: 0 }) === "none",
  "a cold paint (both lists empty AND unknown) offers nothing at all",
);

// ── it disappears on its own ─────────────────────────────────────────────────
assert(
  planFirstAgentPrompt({ ...settled, realAgentCount: 1 }) === "none",
  "one real agent and the band is gone — recomputed from the live count, no flag to unset",
);
assert(planFirstAgentPrompt({ ...settled, realAgentCount: 19 }) === "none", "an established fleet sees nothing");

// ── the pane-is-empty branch is byte-identical to what the page already did ──
assert(
  planFirstAgentPrompt({ ...settled, projectCount: 0 }) === "full",
  "zero projects still gets the centred CreateFirstAgentEmpty, unchanged",
);
// Deliberately NOT gated on the agent count: keeping this branch independent
// is what stops an empty project list ever falling through to "No projects
// match these filters" for a workspace that happens to own an agent.
assert(
  planFirstAgentPrompt({ ...settled, projectCount: 0, realAgentCount: 4 }) === "full",
  "zero projects reads FULL even with agents present — the pane is empty either way",
);
assert(
  planFirstAgentPrompt({ ...settled, projectCount: 0, agentsKnown: false }) === "full",
  "the FULL branch never waits on the agent list it does not read",
);

// A negative count is not a state the caller can reach, but a `<=` rather than
// a `===` is the difference between degrading to the teaching state and
// falling through to a list nobody can act on.
assert(planFirstAgentPrompt({ ...settled, projectCount: -1 }) === "full", "a nonsense project count degrades to FULL");
assert(planFirstAgentPrompt({ ...settled, realAgentCount: -1 }) === "band", "a nonsense agent count degrades to BAND");

// ── The two visual states are MUTUALLY EXCLUSIVE ─────────────────────────────
// Both render an offer to create the first agent; two on one screen is the
// duplicate-primary-action bug this codebase already documents twice.
for (const projectCount of [0, 1, 7]) {
  for (const realAgentCount of [0, 1]) {
    const prompt = planFirstAgentPrompt({ ...settled, projectCount, realAgentCount });
    assert(
      prompt === "full" || prompt === "band" || prompt === "none",
      `plan returns one of three states (${projectCount}/${realAgentCount} gave ${prompt})`,
    );
  }
}

// ── STRUCTURAL: the page actually renders all of this ────────────────────────
const pageSource = readFileSync(
  new URL("../../../app/(account)/w/[workspaceId]/projects/page.tsx", import.meta.url),
  "utf8",
);
// Canary: if this read stops reaching the real page, every assertion below
// passes vacuously and reports green. Same discipline as
// primary-rail-nav.test.ts's FleetShellDecider scan.
assert(pageSource.includes("fleet-content-main"), "CANARY: the projects page source was actually read");

assert(
  pageSource.includes("planFirstAgentPrompt"),
  "the projects page CALLS the plan — built-and-never-wired is this codebase's most common defect",
);
assert(
  /firstAgentPrompt === "band"/.test(pageSource),
  "the band renders on the plan's own answer, never a re-derived condition",
);
assert(
  /variant="band"/.test(pageSource),
  "the band variant is the one requested — the full centred state stays the zero-projects answer",
);
// The band must sit ABOVE the list, not in place of it: a workspace's real
// "General" project stays on screen, because "No projects yet" over a project
// that exists is the outcome-honesty law broken in an empty state.
assert(
  pageSource.indexOf('firstAgentPrompt === "band"') < pageSource.indexOf("fleet-projects-list"),
  "the band is rendered before the list, not instead of it",
);
// Sticky latches, never a live `!loading` — fleet-data.ts re-raises loading on
// every background poll.
assert(
  /setProjectsSettled\(true\)/.test(pageSource) && /setAgentsSettled\(true\)/.test(pageSource),
  "both known-latches are sticky, so a 30s background poll cannot re-flash the band",
);
assert(
  /projectsKnown: projectsSettled/.test(pageSource) && /agentsKnown: agentsSettled/.test(pageSource),
  "the plan is handed the latches, not a live loading flag",
);
// Archived is a filtered VIEW, not the workspace being empty.
assert(/showArchived\s*\?\s*"none"/.test(pageSource), "the archived view is never offered the first-run band");

// ── STRUCTURAL: one accent fill in the view ──────────────────────────────────
// The header's "New project" was an unconditional `fleet-btn--accent-fill`,
// which put it beside CreateFirstAgentEmpty's own filled button on every
// brand-new workspace. agents/page.tsx already fixed the identical defect for
// its own "New agent"; this is that fix on the page a customer actually lands
// on.
assert(
  !/className="fleet-btn fleet-btn--accent-fill"/.test(pageSource),
  "no hardcoded accent-fill survives on this page — create-accent.ts decides",
);
assert(
  /createButtonClass\("header"/.test(pageSource),
  "the header button asks create-accent.ts who owns the fill",
);

// ── STRUCTURAL: the band component itself ────────────────────────────────────
const bandSource = readFileSync(new URL("./first-agent-empty.tsx", import.meta.url), "utf8");
assert(bandSource.includes("FirstAgentBand"), "CANARY: first-agent-empty.tsx was actually read");
assert(
  /createButtonClass\("empty_state"/.test(bandSource),
  "the band's own button goes through create-accent.ts too — never a second opinion",
);
// One creation path: the card wiring and the post-create navigation exist once,
// so a second copy cannot drift out of awaiting the agent-list refresh.
assert(
  (bandSource.match(/<AgentCreateCard/g) || []).length === 1,
  "exactly one AgentCreateCard mount serves both variants",
);
assert(
  (bandSource.match(/quickCreateAgentChatPath\(/g) || []).length === 1,
  "exactly one post-create navigation, shared by both variants",
);

// ── STRUCTURAL: the band's CSS spends no accent of its own ───────────────────
// accent-restraint.test.ts already bans that globally; asserted here too
// because this band is the one place a future author would be tempted to paint
// the surface rather than the button.
const cssSource = readFileSync(new URL("./fleet-theme.css", import.meta.url), "utf8");
const bandBlock = cssSource.slice(
  cssSource.indexOf(".fleet-first-run {"),
  cssSource.indexOf(".fleet-first-run {") + 1400,
);
assert(bandBlock.includes(".fleet-first-run-title"), "CANARY: the band's CSS block was actually located");
assert(!/--accent/.test(bandBlock), "the band's own surface spends no accent — only its button does");

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}

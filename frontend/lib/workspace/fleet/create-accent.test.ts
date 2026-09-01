/**
 * create-accent.ts — "two accent-filled buttons in one view is a bug",
 * expressed once and driven directly.
 *
 * The SOURCE SCAN at the bottom is the half that matters: the logic below
 * was never wrong at any single call site, it was that each site re-derived
 * it and every site was blind to the same case (a composer open in front of
 * the button). A behavioural test cannot catch a seventh site hardcoding
 * `fleet-btn--accent-fill` — it type-checks and looks right, and it is
 * exactly what TaskComposer and DocumentComposer did while the header button
 * behind them stayed filled too.
 *
 * Run: npx tsx lib/workspace/fleet/create-accent.test.ts
 */

import { readFileSync } from "node:fs";

import {
  composerSubmitButtonClass,
  createAccentOwner,
  createButtonClass,
  createOwnsAccent,
  type CreateControl,
} from "./create-accent";

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

const CONTROLS: CreateControl[] = ["header", "empty_state", "composer"];
const STATES = [
  { listIsEmpty: false, composerOpen: false },
  { listIsEmpty: true, composerOpen: false },
  { listIsEmpty: false, composerOpen: true },
  { listIsEmpty: true, composerOpen: true },
];

// ── EXACTLY ONE OWNER, in every reachable state ──────────────────────────
// Never two (the bug this exists for) and never zero (a page with no primary
// action, which is a different bug that would pass a "not two" test).
for (const state of STATES) {
  const owners = CONTROLS.filter((c) => createOwnsAccent(c, state));
  assert(
    owners.length === 1,
    `exactly one accent owner for ${JSON.stringify(state)} — got ${owners.join(",") || "none"}`,
  );
}

// ── The rule itself ──────────────────────────────────────────────────────
assert(
  createAccentOwner({ listIsEmpty: false, composerOpen: false }) === "header",
  "past first run the persistent header button is the primary action",
);
assert(
  createAccentOwner({ listIsEmpty: true, composerOpen: false }) === "empty_state",
  "a first-run empty state's own big button wins the fill",
);
assert(
  createAccentOwner({ listIsEmpty: false, composerOpen: true }) === "composer",
  "the open composer takes the fill from the header behind it",
);
assert(
  createAccentOwner({ listIsEmpty: true, composerOpen: true }) === "composer",
  "and from the empty state behind it — the composer wins over BOTH, which is the case that was live on Tasks and Documents",
);

// ── The className the components actually render ─────────────────────────
assert(
  createButtonClass("header", { listIsEmpty: false, composerOpen: false }) === "fleet-btn fleet-btn--accent-fill",
  "the owner gets the filled variant",
);
assert(
  createButtonClass("header", { listIsEmpty: false, composerOpen: true }) === "fleet-btn fleet-btn--accent",
  "a non-owner drops to the quiet hairline variant",
);
// A composer is only ever mounted while open, so its submit always owns —
// but through the rule, never a hardcoded literal (see the module header).
assert(
  composerSubmitButtonClass() === "fleet-btn fleet-btn--accent-fill",
  "a mounted composer's submit is the filled control",
);
assert(
  composerSubmitButtonClass() === createButtonClass("composer", { listIsEmpty: false, composerOpen: true }),
  "…and it is the SAME rule, not a second answer that happens to agree today",
);
// No dead controls: losing the accent must never mean losing the button.
for (const control of CONTROLS) {
  for (const state of STATES) {
    const cls = createButtonClass(control, state);
    assert(cls.startsWith("fleet-btn "), `${control} is still a real button in ${JSON.stringify(state)}`);
    assert(
      cls.includes("fleet-btn--accent"),
      `${control} keeps an accent variant (filled or hairline) in ${JSON.stringify(state)} — never neutral, never disabled`,
    );
  }
}

// ── The Projects page CALL SITE, not just the rule ────────────────────────
// Everything above drives createAccentOwner directly with hand-picked
// states — it cannot see a caller handing the rule the WRONG variable, only
// that the rule itself is consistent once given an input. The Projects page
// is exactly the shape that trap catches: it carries not one but TWO
// competing "make a new thing" controls (NewProjectDialog's header button
// and CreateFirstAgentEmpty's band/full offer), so "is this control's own
// list empty" and "is create-accent's rule fed the right composer state"
// have to be checked against what the page can ACTUALLY put on screen, not
// against inputs this file invents.
//
// planFirstAgentPrompt (workspace-first-run.ts) is the real, pure function
// that decides whether CreateFirstAgentEmpty renders at all and as which
// variant — imported here rather than re-derived, so this test moves when
// that rule does. `projectsPageOwners` below is the one place that composes
// it with create-accent.ts's rule; the two CANARIES after it exist because a
// composition that only ever agrees with itself is exactly the "check that
// derives its expectations from the thing it checks" trap CLAUDE.md names.
import { planFirstAgentPrompt } from "./workspace-first-run";

type ProjectsPageState = { projectCount: number; realAgentCount: number; dialogOpen: boolean; cardOpen: boolean };

// Every state the Projects page can actually be in, including the one
// "observed live" on a workspace with the bootstrapped "General" project
// and zero agents (band, not the centred empty state — projects.length is
// 1, not 0), and the sibling-composer overlaps: NewProjectDialog opened
// while the band or the full empty state is already on screen.
const PROJECTS_PAGE_STATES: ProjectsPageState[] = [
  { projectCount: 0, realAgentCount: 0, dialogOpen: false, cardOpen: false }, // brand new workspace, nothing yet
  { projectCount: 1, realAgentCount: 0, dialogOpen: false, cardOpen: false }, // the reported case: one project ("General"), no agents
  { projectCount: 3, realAgentCount: 0, dialogOpen: false, cardOpen: false }, // several projects, still no agents
  { projectCount: 1, realAgentCount: 2, dialogOpen: false, cardOpen: false }, // established workspace — past first run
  { projectCount: 1, realAgentCount: 0, dialogOpen: true, cardOpen: false }, // New Project dialog opened over the band
  { projectCount: 0, realAgentCount: 0, dialogOpen: true, cardOpen: false }, // New Project dialog opened over the full empty state
  { projectCount: 1, realAgentCount: 0, dialogOpen: false, cardOpen: true }, // AgentCreateCard opened from the band itself
];

/** Mirrors exactly what the Projects page renders and how each control's
 *  own accent state is computed — `headerListIsEmpty` and
 *  `emptyStateComposerOpen` are parameters precisely so the canaries below
 *  can swap in the WRONG binding and prove this function would have caught
 *  it, rather than hard-coding one answer this file can only agree with. */
function projectsPageOwners(
  state: ProjectsPageState,
  headerListIsEmpty: (s: ProjectsPageState) => boolean,
  emptyStateComposerOpen: (s: ProjectsPageState) => boolean,
): string[] {
  const prompt = planFirstAgentPrompt({
    projectsKnown: true,
    projectCount: state.projectCount,
    agentsKnown: true,
    realAgentCount: state.realAgentCount,
  });
  const emptyStateRendered = prompt !== "none";
  const owners: string[] = [];
  if (createOwnsAccent("header", { listIsEmpty: headerListIsEmpty(state), composerOpen: state.dialogOpen })) {
    owners.push("header");
  }
  if (
    emptyStateRendered &&
    createOwnsAccent("empty_state", { listIsEmpty: true, composerOpen: emptyStateComposerOpen(state) })
  ) {
    owners.push("empty_state (band/full)");
  }
  // NewProjectDialog's own submit always fills while mounted — it is routed
  // through composerSubmitButtonClass(), asserted filled elsewhere in this
  // file, so it is not re-derived here.
  if (state.dialogOpen) owners.push("composer (New Project dialog)");
  // AgentCreateCard is itself a FOURTH composer-like control, nested inside
  // the band/full empty state and mounted whenever cardOpen is true — its
  // own submit button (AgentCreateCard.tsx) fills for most wizard steps.
  // Not modelled via createOwnsAccent (its accent is step-gated, not this
  // module's business) but it still occupies the "a composer is open"
  // slot, so it counts here or this simulation would silently believe
  // NOBODY owns the accent while a real, filled AgentCreateCard is on
  // screen — a false failure, not a real one.
  if (state.cardOpen) owners.push("composer (AgentCreateCard)");
  return owners;
}

// The bindings this page ACTUALLY uses today (projects/page.tsx and
// first-agent-empty.tsx) — reproduced here as plain functions of the same
// state, not read from source, so the assertion is "the real rule composed
// correctly" rather than "the file contains some string".
const REAL_HEADER_LIST_IS_EMPTY = (s: ProjectsPageState) =>
  planFirstAgentPrompt({
    projectsKnown: true,
    projectCount: s.projectCount,
    agentsKnown: true,
    realAgentCount: s.realAgentCount,
  }) !== "none";
const REAL_EMPTY_STATE_COMPOSER_OPEN = (s: ProjectsPageState) => s.cardOpen || s.dialogOpen;

for (const state of PROJECTS_PAGE_STATES) {
  const owners = projectsPageOwners(state, REAL_HEADER_LIST_IS_EMPTY, REAL_EMPTY_STATE_COMPOSER_OPEN);
  assert(
    owners.length === 1,
    `Projects page: exactly one accent owner for ${JSON.stringify(state)} — got [${owners.join(", ") || "none"}]`,
  );
}

// CANARY 1: the "obviously right" binding a naive fix reaches for —
// listIsEmpty = projects.length === 0 — leaves the header ALSO filled on
// the reported case, because the band renders off the AGENT count, not the
// project count. Proves the check above can actually fail.
const naiveHeaderOwners = projectsPageOwners(
  { projectCount: 1, realAgentCount: 0, dialogOpen: false, cardOpen: false },
  (s) => s.projectCount === 0,
  REAL_EMPTY_STATE_COMPOSER_OPEN,
);
assert(
  naiveHeaderOwners.length === 2,
  `CANARY: listIsEmpty = projects.length === 0 WOULD leave two owners on the reported case — got ` +
    `[${naiveHeaderOwners.join(", ")}] (if this is not 2, the check above can no longer catch this class of bug)`,
);

// CANARY 2: the bug this change actually fixed — the band/full empty state
// watching only its OWN nested AgentCreateCard and staying blind to the
// page's sibling NewProjectDialog. Proves the check above would catch that
// direction too (two owners, not the "zero owners" the naive fix chases).
const blindEmptyStateOwners = projectsPageOwners(
  { projectCount: 1, realAgentCount: 0, dialogOpen: true, cardOpen: false },
  REAL_HEADER_LIST_IS_EMPTY,
  (s) => s.cardOpen, // pre-fix: never looked at dialogOpen
);
assert(
  blindEmptyStateOwners.length === 2,
  `CANARY: an empty-state button blind to a sibling composer WOULD leave two owners when New Project opens ` +
    `over the band — got [${blindEmptyStateOwners.join(", ")}] (if this is not 2, the check above can no longer catch this class of bug)`,
);

// ── Wired, not just built ────────────────────────────────────────────────
const SITES: { path: string; name: string }[] = [
  { path: "../../../app/(account)/w/[workspaceId]/agents/page.tsx", name: "the workspace Agents page" },
  { path: "../../../app/(account)/w/[workspaceId]/projects/[projectId]/page.tsx", name: "a project's page (Agents/Tasks/Documents)" },
  { path: "./first-agent-empty.tsx", name: "FirstAgentEmpty" },
  { path: "./AgentCreateCard.tsx", name: "AgentCreateCard" },
  { path: "./TaskComposer.tsx", name: "TaskComposer" },
  { path: "./DocumentComposer.tsx", name: "DocumentComposer" },
];

/** Line and block comments stripped, so this file's own prose — and the long
 *  design comments these components carry, several of which QUOTE the banned
 *  class by name — cannot trip a guard aimed at rendered code. Same posture
 *  as the backend's AST sweeps: scan CODE tokens, never documentation. */
function codeOnly(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/[^\n]*/g, "$1");
}

for (const site of SITES) {
  const source = readFileSync(new URL(site.path, import.meta.url), "utf8");
  assert(source.length > 500, `CANARY: ${site.name}'s source was actually read`);
  // Match the IMPORT PATH, not the bare substring: "agent-create-accent"
  // (this module's own pre-generalisation name, now deleted) contains
  // "create-accent", so a loose test would greenlight a site still pointing
  // at a module that no longer exists.
  assert(
    /from ["'](?:\.\/|@\/lib\/workspace\/fleet\/)create-accent["']/.test(source),
    `${site.name} decides its accent through the shared rule`,
  );

  const code = codeOnly(source);
  assert(code.length > 300, `CANARY: ${site.name} still has code after comments are stripped`);
  // THE GUARD. Every create control on all six of these surfaces answers to
  // create-accent.ts, so a literal accent class in rendered code is by
  // definition a control that stopped asking. Both shapes are banned
  // together on purpose: the hand-inlined ternary
  // (`tasks.length === 0 ? " fleet-btn--accent" : " fleet-btn--accent-fill"`)
  // was live on the two header buttons, and the bare hardcoded fill was live
  // on both composers and both empty states — one guard, because they are
  // one mistake wearing two costumes.
  assert(
    !/fleet-btn--accent/.test(code),
    `${site.name} does not hardcode an accent class — every create control there goes through create-accent.ts`,
  );
}

// ── siblingComposerOpen is actually THREADED, not just declared ──────────
// CANARY 2 above proves the composed rule catches an empty state that
// ignores a sibling composer — but that composition is this test file's own
// code, not the real components'. This closes the gap the same way the
// SITES loop above does: read the real files and confirm the wiring is
// actually there, so a future edit that quietly drops the prop (instead of
// mis-deriving it) still fails loudly.
const projectsPageSource = codeOnly(
  readFileSync(
    new URL("../../../app/(account)/w/[workspaceId]/projects/page.tsx", import.meta.url),
    "utf8",
  ),
);
const firstAgentEmptySource = codeOnly(readFileSync(new URL("./first-agent-empty.tsx", import.meta.url), "utf8"));

assert(
  projectsPageSource.length > 500 && firstAgentEmptySource.length > 500,
  "CANARY: projects/page.tsx and first-agent-empty.tsx were actually read",
);
assert(
  (projectsPageSource.match(/siblingComposerOpen=\{dialogOpen\}/g) || []).length === 2,
  "projects/page.tsx passes siblingComposerOpen={dialogOpen} to BOTH CreateFirstAgentEmpty renders (the band " +
    "and the full empty state) — one site quietly losing this is exactly how the two-owner bug came back",
);
assert(
  (firstAgentEmptySource.match(/composerOpen:\s*createCardOpen\s*\|\|\s*siblingComposerOpen/g) || []).length === 2,
  "BOTH FirstAgentEmpty and FirstAgentBand OR their own nested card's open state together with the page's " +
    "sibling composer — dropping either half (or fixing only one of the two components) reopens the bug this " +
    "file's own header comment documents",
);

// The canary for the guard itself: prove it can still SEE a violation, or a
// stripper bug would silently turn all six assertions above into a green
// no-op (a check that can only confirm itself is this repo's own documented
// failure mode).
assert(
  /fleet-btn--accent/.test(codeOnly('const x = "fleet-btn fleet-btn--accent-fill"; // fleet-btn--accent-fill')),
  "CANARY: the guard still detects a hardcoded accent class in real code",
);
assert(
  !/fleet-btn--accent/.test(codeOnly("/* fleet-btn--accent-fill */\n// fleet-btn--accent-fill\nconst y = 1;")),
  "CANARY: …and still ignores one that only appears in a comment",
);

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}

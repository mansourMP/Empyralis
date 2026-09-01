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
// is exactly the shape that trap catches: it carries up to TWO competing
// "make a new thing" controls on screen at once (the header's "New
// project" and, on a genuinely empty workspace, the zero-projects empty
// state's own "Create your first project"), plus NewProjectDialog itself
// once opened — so "is this control's own list empty" and "is
// create-accent's rule fed the right composer state" have to be checked
// against what the page can ACTUALLY put on screen, not against inputs this
// file invents.
//
// A THIRD control used to compete here too: CreateFirstAgentEmpty's
// variant="band", offering to create an AGENT once a project existed and
// none did. DELETED 2026-09-01 (founder: a project's own surfaces are about
// that project, never about creating an agent) — see
// workspace-first-run.test.ts for the structural guard that it stays
// deleted, not merely unrendered.
//
// planFirstAgentPrompt (workspace-first-run.ts) is the real, pure function
// that decides whether the zero-projects empty state renders at all —
// imported here rather than re-derived, so this test moves when that rule
// does. `projectsPageOwners` below is the one place that composes it with
// create-accent.ts's rule; the CANARY after it exists because a composition
// that only ever agrees with itself is exactly the "check that derives its
// expectations from the thing it checks" trap CLAUDE.md names.
import { planFirstAgentPrompt } from "./workspace-first-run";

type ProjectsPageState = { projectCount: number; dialogOpen: boolean };

const PROJECTS_PAGE_STATES: ProjectsPageState[] = [
  { projectCount: 0, dialogOpen: false }, // brand new workspace, nothing yet
  { projectCount: 1, dialogOpen: false }, // THE REPORTED CASE: a project exists, the workspace has no agent — nothing competes for the fill any more, the header owns it
  { projectCount: 3, dialogOpen: false }, // several projects — established workspace, past first run
  { projectCount: 1, dialogOpen: true }, // New Project dialog opened over a real list
  { projectCount: 0, dialogOpen: true }, // New Project dialog opened over the zero-projects empty state
];

/** Mirrors exactly what the Projects page renders and how each control's own
 *  accent state is computed — `headerListIsEmpty` is a parameter precisely
 *  so the canary below can swap in a WRONG binding and prove this function
 *  would have caught it, rather than hard-coding one answer this file can
 *  only agree with. */
function projectsPageOwners(
  state: ProjectsPageState,
  headerListIsEmpty: (s: ProjectsPageState) => boolean,
): string[] {
  const prompt = planFirstAgentPrompt({ projectsKnown: true, projectCount: state.projectCount });
  const emptyStateRendered = prompt === "full";
  const owners: string[] = [];
  if (createOwnsAccent("header", { listIsEmpty: headerListIsEmpty(state), composerOpen: state.dialogOpen })) {
    owners.push("header");
  }
  if (emptyStateRendered && createOwnsAccent("empty_state", { listIsEmpty: true, composerOpen: state.dialogOpen })) {
    owners.push("empty_state (zero-projects)");
  }
  // NewProjectDialog's own submit always fills while mounted — it is routed
  // through composerSubmitButtonClass(), asserted filled elsewhere in this
  // file, so it is not re-derived here.
  if (state.dialogOpen) owners.push("composer (New Project dialog)");
  return owners;
}

// The binding this page ACTUALLY uses today (projects/page.tsx) —
// reproduced here as a plain function of the same state, not read from
// source, so the assertion is "the real rule composed correctly" rather
// than "the file contains some string".
const REAL_HEADER_LIST_IS_EMPTY = (s: ProjectsPageState) =>
  planFirstAgentPrompt({ projectsKnown: true, projectCount: s.projectCount }) === "full";

for (const state of PROJECTS_PAGE_STATES) {
  const owners = projectsPageOwners(state, REAL_HEADER_LIST_IS_EMPTY);
  assert(
    owners.length === 1,
    `Projects page: exactly one accent owner for ${JSON.stringify(state)} — got [${owners.join(", ") || "none"}]`,
  );
}

// CANARY: the exact regression removing the band could have introduced.
// Before the fix, the header asked "is ANY first-run offer showing"
// (`firstAgentPrompt !== "none"`), which was true both when the pane was
// genuinely empty AND when the (now-deleted) agent band was showing over a
// real project list. Deleting the band's render without also updating that
// binding leaves the header still believing SOME control owns the fill on
// the reported case (a project exists, no agent) — but nothing renders
// there any more, so NOBODY does. Zero owners, not two, is the failure mode
// this task's own fix had to avoid.
const zeroOwnerRegression = projectsPageOwners({ projectCount: 1, dialogOpen: false }, () => true);
assert(
  zeroOwnerRegression.length === 0,
  `CANARY: a header binding that still assumes an agent-creation offer competes for the fill leaves ZERO owners ` +
    `now that the band is gone — got [${zeroOwnerRegression.join(", ")}] (if this is not 0, the check above can no ` +
    `longer catch the exact regression this task fixed)`,
);

// ── Wired, not just built ────────────────────────────────────────────────
const SITES: { path: string; name: string }[] = [
  { path: "../../../app/(account)/w/[workspaceId]/agents/page.tsx", name: "the workspace Agents page" },
  { path: "../../../app/(account)/w/[workspaceId]/projects/page.tsx", name: "the Projects page" },
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

// ── The zero-projects empty state's OWN composerOpen wiring ──────────────
// The one remaining "second create control on this page" case, now that the
// agent band is gone: opening New Project over the zero-projects empty
// state itself. Read the real file and confirm the wiring is actually
// there, so a future edit that quietly drops it still fails loudly.
const projectsPageSource = codeOnly(
  readFileSync(
    new URL("../../../app/(account)/w/[workspaceId]/projects/page.tsx", import.meta.url),
    "utf8",
  ),
);
assert(projectsPageSource.length > 500, "CANARY: projects/page.tsx was actually read");
assert(
  /createButtonClass\(\s*"empty_state",\s*\{\s*listIsEmpty:\s*true,\s*composerOpen:\s*dialogOpen\s*\}\s*\)/.test(
    projectsPageSource,
  ),
  "projects/page.tsx's own zero-projects empty state (\"Create your first project\") threads the page's " +
    "dialogOpen straight into create-accent.ts as its composerOpen — dropping this is a two-owner bug the moment " +
    "New Project is opened over an empty projects list",
);
// The Projects page does not render CreateFirstAgentEmpty at all any more —
// agent creation is not this page's business (see workspace-first-run.test.ts
// for the fuller structural guard on this).
assert(
  !/CreateFirstAgentEmpty/.test(projectsPageSource),
  "projects/page.tsx does not import or render CreateFirstAgentEmpty — agent creation stays off project surfaces",
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

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

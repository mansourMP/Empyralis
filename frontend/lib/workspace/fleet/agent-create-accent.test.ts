/**
 * agent-create-accent.ts — "two accent-filled buttons in one view is a bug",
 * expressed once and driven directly.
 *
 * The SOURCE SCANS at the bottom are the half that matters: the logic below
 * was never wrong at any single call site, it was that each site re-derived
 * it and the AgentCreateCard case was missed at all of them. A behavioural
 * test cannot catch a fourth site re-inlining the ternary — it type-checks
 * and looks right.
 *
 * Run: npx tsx lib/workspace/fleet/agent-create-accent.test.ts
 */

import {
  agentCreateAccentOwner,
  agentCreateButtonClass,
  agentCreateOwnsAccent,
  type AgentCreateControl,
} from "./agent-create-accent";

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

const CONTROLS: AgentCreateControl[] = ["header", "empty_state", "card"];
const STATES = [
  { listIsEmpty: false, createCardOpen: false },
  { listIsEmpty: true, createCardOpen: false },
  { listIsEmpty: false, createCardOpen: true },
  { listIsEmpty: true, createCardOpen: true },
];

// ── EXACTLY ONE OWNER, in every reachable state ──────────────────────────
// Never two (the bug this exists for) and never zero (a page with no primary
// action, which is a different bug that would pass a "not two" test).
for (const state of STATES) {
  const owners = CONTROLS.filter((c) => agentCreateOwnsAccent(c, state));
  assert(
    owners.length === 1,
    `exactly one accent owner for ${JSON.stringify(state)} — got ${owners.join(",") || "none"}`,
  );
}

// ── The rule itself ──────────────────────────────────────────────────────
assert(
  agentCreateAccentOwner({ listIsEmpty: false, createCardOpen: false }) === "header",
  "past first run the persistent header button is the primary action",
);
assert(
  agentCreateAccentOwner({ listIsEmpty: true, createCardOpen: false }) === "empty_state",
  "a first-run empty state's own big button wins the fill",
);
assert(
  agentCreateAccentOwner({ listIsEmpty: false, createCardOpen: true }) === "card",
  "the open card takes the fill from the header behind it",
);
assert(
  agentCreateAccentOwner({ listIsEmpty: true, createCardOpen: true }) === "card",
  "and from the empty state behind it — the card wins over BOTH, which is the case that was live",
);

// ── The className the components actually render ─────────────────────────
assert(
  agentCreateButtonClass("header", { listIsEmpty: false, createCardOpen: false }) === "fleet-btn fleet-btn--accent-fill",
  "the owner gets the filled variant",
);
assert(
  agentCreateButtonClass("header", { listIsEmpty: false, createCardOpen: true }) === "fleet-btn fleet-btn--accent",
  "a non-owner drops to the quiet hairline variant",
);
// No dead controls: losing the accent must never mean losing the button.
for (const control of CONTROLS) {
  for (const state of STATES) {
    const cls = agentCreateButtonClass(control, state);
    assert(cls.startsWith("fleet-btn "), `${control} is still a real button in ${JSON.stringify(state)}`);
    assert(
      cls.includes("fleet-btn--accent"),
      `${control} keeps an accent variant (filled or hairline) in ${JSON.stringify(state)} — never neutral, never disabled`,
    );
  }
}

// ── Wired, not just built ────────────────────────────────────────────────
import { readFileSync } from "node:fs";

const SITES: { path: string; name: string }[] = [
  { path: "../../../app/(account)/w/[workspaceId]/agents/page.tsx", name: "the workspace Agents page" },
  { path: "../../../app/(account)/w/[workspaceId]/projects/[projectId]/page.tsx", name: "a project's page" },
  { path: "./first-agent-empty.tsx", name: "FirstAgentEmpty" },
  { path: "./AgentCreateCard.tsx", name: "AgentCreateCard" },
];

for (const site of SITES) {
  const source = readFileSync(new URL(site.path, import.meta.url), "utf8");
  assert(source.length > 500, `CANARY: ${site.name}'s source was actually read`);
  assert(
    /agent-create-accent/.test(source),
    `${site.name} decides its accent through the shared rule`,
  );
  // The reintroduction guard, scoped to the AGENT-creation controls only.
  // This exact ternary, hand-inlined, is what was live at three of these
  // four sites and is invisible to any behavioural test — it renders
  // perfectly, it is just blind to the card being open. Scoped by a window
  // around each control's own label because the same two pages legitimately
  // render the identical ternary for their "+ New task" / "+ New document"
  // buttons, which answer to their OWN composers and are not this rule's
  // business (they carry the same unfixed bug against TaskComposer /
  // DocumentComposer — a separate change, deliberately not made here).
  const labels = ["New agent", "Create your first agent", "Create agent"];
  for (const label of labels) {
    let from = 0;
    for (;;) {
      const at = source.indexOf(label, from);
      if (at < 0) break;
      from = at + label.length;
      const window = source.slice(Math.max(0, at - 700), at);
      assert(
        !/fleet-btn--accent(-fill)?["'`\s]*(:|\?)/.test(window),
        `${site.name} does not re-inline the accent ternary on its "${label}" control`,
      );
    }
  }
}

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}

/**
 * document-conflict.ts — the UI half of the stale-write fix.
 *
 * The property that actually matters here, and the one every assertion below
 * circles: NEITHER SIDE'S TEXT IS EVER THROWN AWAY BY THE PRODUCT. The
 * backend refusing a stale write only closes half the bug; a client that
 * responded to that refusal by dropping the person's draft would be the same
 * data loss pointed the other way. So: both ways out are explicit, both are
 * labelled with what they really do, and the only one that destroys anything
 * says so on its own face.
 *
 * Same harness style as documents-data.test.ts (no jsdom/RTL in this repo —
 * see that file's header). DocumentDetailView's own wiring is verified in a
 * real browser, not here.
 *
 * Run: npx tsx lib/workspace/fleet/document-conflict.test.ts
 */

import {
  conflictActorLabel,
  diffDocumentBodies,
  planDocumentConflict,
} from "./document-conflict";

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

// ── The actor ────────────────────────────────────────────────────────────
assert(conflictActorLabel("Support Bot") === "Support Bot", "a resolved actor is named");
assert(
  conflictActorLabel(null) === "Someone else" &&
    conflictActorLabel("") === "Someone else" &&
    conflictActorLabel("   ") === "Someone else",
  "an unresolved actor reads as 'Someone else' — never a guess, never a raw id",
);

// ── The plan ─────────────────────────────────────────────────────────────
const conflict = planDocumentConflict(
  { title: "Runbook", body: "mine\n" },
  { title: "Runbook", body: "theirs\n" },
  "Support Bot",
);

assert(conflict.needsResolution, "different content needs a decision");
assert(
  conflict.headline.includes("Support Bot"),
  "the headline names who changed it — 'this changed' with no actor is half a fact",
);
assert(
  conflict.reassurance.includes("have not been saved"),
  "the person is told their draft is intact AND unsaved — a refusal is neither 'failed' nor 'saved'",
);
assert(conflict.actions.length === 2, "exactly two ways out, both explicit");
assert(
  conflict.actions.filter((a) => a.accent).length === 1,
  "one accent action only (craft doctrine: two accent-filled buttons in one view is a bug)",
);
assert(
  conflict.actions.every((a) => a.consequence.trim().length > 0),
  "every action states its own consequence — a choice between two versions of your own writing is decided on the button, not in a paragraph above it",
);

const keepMine = conflict.actions.find((a) => a.key === "keep-mine")!;
const takeTheirs = conflict.actions.find((a) => a.key === "take-theirs")!;

assert(keepMine.accent && !takeTheirs.accent, "the accent is on the option that loses nothing");
assert(
  /history/i.test(keepMine.consequence),
  "keeping mine must say the other version survives in history — that is what makes it safe to click",
);
assert(
  /discard/i.test(takeTheirs.consequence) && /cannot be recovered/i.test(takeTheirs.consequence),
  "the destructive option says it destroys, and says the loss is permanent — it was never saved, so no revision holds it",
);
assert(
  !/keep/i.test(takeTheirs.label),
  "the destructive option is never worded as if it keeps something",
);

// ── The no-op case: identical content is not a conflict ──────────────────
const identical = planDocumentConflict(
  { title: "Runbook", body: "same bytes\n" },
  { title: "Runbook", body: "same bytes\n" },
  "Support Bot",
);
assert(
  !identical.needsResolution && identical.actions.length === 0,
  "a rewrite that produced identical text is not a conflict — nobody is interrupted to resolve nothing",
);

const titleOnly = planDocumentConflict(
  { title: "Runbook", body: "same\n" },
  { title: "Deploy Runbook", body: "same\n" },
);
assert(
  titleOnly.needsResolution,
  "a title-only divergence still needs a decision — a stale save reverts a rename just as silently as a paragraph",
);

// ── The diff ─────────────────────────────────────────────────────────────
const d = diffDocumentBodies("one\ntwo\nthree\n", "one\nTWO\nthree\n");
assert(
  d.lines.some((l) => l.kind === "remove" && l.text === "two"),
  "the person's own line shows as removed (— is mine, matching 'keep mine')",
);
assert(
  d.lines.some((l) => l.kind === "add" && l.text === "TWO"),
  "the incoming line shows as added",
);
assert(
  !d.lines.some((l) => l.kind !== "context" && l.text === "one"),
  "an unchanged line is never reported as changed",
);

const noChange = diffDocumentBodies("same\n", "same\n");
assert(
  noChange.lines.every((l) => l.kind === "context"),
  "identical bodies produce no add/remove lines",
);

const longMine = Array.from({ length: 200 }, (_, i) => `mine ${i}`).join("\n");
const longTheirs = Array.from({ length: 200 }, (_, i) => `theirs ${i}`).join("\n");
const bounded = diffDocumentBodies(longMine, longTheirs, 40);
assert(
  bounded.lines.length <= 40 && bounded.truncated,
  "a whole-document rewrite renders a bounded excerpt and SAYS it is truncated, rather than a thousand rows inside a banner",
);

const contextCollapsed = diffDocumentBodies(
  ["a", "b", "c", "d", "e", "f", "g", "CHANGED", "h", "i", "j"].join("\n"),
  ["a", "b", "c", "d", "e", "f", "g", "changed", "h", "i", "j"].join("\n"),
);
assert(
  contextCollapsed.lines.length < 11,
  "long runs of unchanged lines are collapsed so the change is what the eye lands on",
);
assert(
  contextCollapsed.lines.some((l) => l.kind === "remove" && l.text === "CHANGED") &&
    contextCollapsed.lines.some((l) => l.kind === "add" && l.text === "changed"),
  "collapsing context never drops the change itself",
);

// A pathological paste must degrade to a summary rather than freeze the tab
// on an O(n*m) table.
const huge = Array.from({ length: 2500 }, (_, i) => `line ${i}`).join("\n");
const hugeOther = Array.from({ length: 2500 }, (_, i) => `other ${i}`).join("\n");
const degraded = diffDocumentBodies(huge, hugeOther);
assert(
  degraded.truncated && degraded.lines.length <= 4,
  "past the cell budget the diff degrades to a summary instead of computing a 6M-cell table in the browser",
);

// --- Summary ---

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}

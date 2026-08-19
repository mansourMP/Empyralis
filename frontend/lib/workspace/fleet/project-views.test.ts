/**
 * The project tab bar's set, proven against hand-written expectations —
 * the expected list is typed out here, the actual list is the real module
 * the page renders from (two different sources, never self-confirming).
 *
 * Run: npx tsx lib/workspace/fleet/project-views.test.ts
 */

import { PROJECT_TAB_LABEL, PROJECT_TAB_VIEWS } from "./project-views";

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

assert(
  PROJECT_TAB_VIEWS.join(",") === "tasks,documents",
  `the tab bar is exactly Tasks · Documents, in that order — got ${PROJECT_TAB_VIEWS.join(",")}`,
);

// People is not a tab: the header's member avatars + "+" are the people
// surface, and two doors to one room is one too many (founder, 2026-08-16).
// Its route stays live and unlinked — see project-views.ts's header.
assert(!(PROJECT_TAB_VIEWS as readonly string[]).includes("people"), "People is NOT a tab");

assert(
  PROJECT_TAB_VIEWS.map((v) => PROJECT_TAB_LABEL[v]).join(",") === "Tasks,Documents",
  "labels are the plain human words",
);

// Agents is NOT a tab (founder, 2026-08-19). Agents became a top-level rail
// surface the day before -- re-offering them inside one project re-cages the
// thing that was deliberately lifted out, and gives two answers to "where do
// I find my agents". Asserted as its own negative, exactly like People
// below, so a future edit that quietly re-adds the tab fails here rather
// than shipping. The per-project /agents ROUTE stays live and unlinked.
assert(!(PROJECT_TAB_VIEWS as readonly string[]).includes("agents"), "Agents is NOT a tab");

// Wired, not just built: the page must render its strip FROM this module,
// or the set here asserts nothing about what a customer sees.
import { readFileSync } from "node:fs";
const pageSource = readFileSync(
  new URL("../../../app/(account)/w/[workspaceId]/projects/[projectId]/page.tsx", import.meta.url),
  "utf8",
);
assert(
  pageSource.includes('from "@/lib/workspace/fleet/project-views"'),
  "ProjectDetailPage imports the real tab list",
);
assert(
  /PROJECT_TAB_VIEWS\.map\(/.test(pageSource),
  "ProjectDetailPage renders the strip by mapping PROJECT_TAB_VIEWS, never a hand-inlined array",
);

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}

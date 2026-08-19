/**
 * Imports the REAL rule (formatProjectWorkSummary) rather than
 * re-deriving the copy here — same discipline agent-count-shape.test.ts
 * already applies to planAgentCountShape.
 *
 * Run: npx tsx lib/workspace/fleet/project-work-summary.test.ts
 */

import { formatProjectWorkSummary } from "./project-work-summary";

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

assert(formatProjectWorkSummary(0, 0) === "No tasks or documents yet", "zero and zero -> honest empty copy");
assert(formatProjectWorkSummary(1, 0) === "1 task", "singular task, no documents");
assert(formatProjectWorkSummary(2, 0) === "2 tasks", "plural tasks, no documents");
assert(formatProjectWorkSummary(0, 1) === "1 document", "singular document, no tasks");
assert(formatProjectWorkSummary(0, 4) === "4 documents", "plural documents, no tasks");
assert(formatProjectWorkSummary(3, 1) === "3 tasks · 1 document", "both present, tasks first");
assert(formatProjectWorkSummary(12, 4) === "12 tasks · 4 documents", "founder's own observed shape, real numbers");
assert(formatProjectWorkSummary(null, undefined) === "No tasks or documents yet", "missing fields never crash, coerce to zero");
assert(formatProjectWorkSummary(-3, -1) === "No tasks or documents yet", "never a negative count");

console.log(`project-work-summary.test.ts: ${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);

/**
 * Pure-function coverage for the two helpers behind the document "⋯" menu's
 * Duplicate and Export actions (documents-data.ts) — DocumentDetailView.tsx
 * itself has no test here, same reason openclaw-channel-copy.test.ts's own
 * header gives: this repo has no headless-DOM harness (no jsdom/RTL), so a
 * component that reads state, calls the network and navigates cannot be
 * driven from a plain tsx script. What CAN be driven, and is verified in a
 * real browser separately (see the PR/commit this file shipped with), is
 * every pure decision these two functions make — the exact titles and
 * filenames a customer would see land in the menu, the network call, and
 * the download.
 *
 * Run: npx tsx lib/workspace/fleet/documents-data.test.ts
 */

import { documentExportFilename, duplicateDocumentTitle } from "./documents-data";

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

// ── duplicateDocumentTitle ──────────────────────────────────────────────
assert(
  duplicateDocumentTitle("Runbook: onboarding a new customer") === "Runbook: onboarding a new customer (copy)",
  "an ordinary title gets ' (copy)' appended",
);
assert(
  duplicateDocumentTitle("  Spec with padding  ") === "Spec with padding (copy)",
  "leading/trailing whitespace on the source title is trimmed before appending",
);
assert(
  duplicateDocumentTitle("") === "Untitled document (copy)",
  "an empty title falls back to the same 'Untitled document' label the editor's own placeholder uses",
);
assert(
  duplicateDocumentTitle("   ") === "Untitled document (copy)",
  "a whitespace-only title is treated as empty, not as a title of spaces",
);

// ── documentExportFilename ──────────────────────────────────────────────
assert(
  documentExportFilename("Runbook: onboarding a new customer", "runbook-onboarding") ===
    "Runbook- onboarding a new customer.md",
  "the colon (invalid on Windows paths) is replaced, not dropped, so words don't collide",
);
assert(
  documentExportFilename("Notes/Plans", "notes-plans") === "Notes-Plans.md",
  "a forward slash never survives into the filename -- it would read as a path separator",
);
assert(
  documentExportFilename('Q&A: "launch" <plan>', "qa-launch") === "Q&A- -launch- -plan-.md",
  "every character invalid on some OS's filesystem is replaced: : \" < >",
);
assert(
  documentExportFilename("", "fallback-slug") === "fallback-slug.md",
  "an empty title falls back to the document's own slug",
);
assert(
  documentExportFilename("", "") === "document.md",
  "an empty title AND an empty slug still produce a real filename, never a bare '.md'",
);
assert(
  documentExportFilename("   ", "   ") === "document.md",
  "whitespace-only title and slug are both treated as absent",
);
assert(
  documentExportFilename("Plain title", "plain-title").endsWith(".md"),
  "the extension is always .md, regardless of input",
);

// --- Summary ---

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}

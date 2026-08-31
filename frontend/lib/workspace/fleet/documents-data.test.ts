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

import { documentExportFilename, duplicateDocumentTitle, isGenuineDocumentNotFound } from "./documents-data";

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
  documentExportFilename("", "fallback-name.md") === "fallback-name.md",
  "an empty title falls back to the document's own filename",
);
// `slug` became `path` when the documents surface went GitHub-shaped, so the
// fallback is a PATH now. A download is one file: only the last segment can
// be the filename, and the ".md" it already carries must not be doubled.
assert(
  documentExportFilename("", "specs/api/auth.md") === "auth.md",
  "the fallback is the path's LAST SEGMENT, never the whole path flattened by the sanitizer",
);
assert(
  documentExportFilename("", "/specs//api/auth.md") === "auth.md",
  "leading and duplicated slashes in the path never leak into the filename",
);
assert(
  documentExportFilename("Auth spec.md", "specs/auth.md") === "Auth spec.md",
  "a title that already ends in .md keeps exactly one extension",
);
assert(
  documentExportFilename("", "") === "document.md",
  "an empty title AND an empty path still produce a real filename, never a bare '.md'",
);
assert(
  documentExportFilename("   ", "   ") === "document.md",
  "whitespace-only title and path are both treated as absent",
);
assert(
  documentExportFilename("Plain title", "plain-title.md").endsWith(".md"),
  "the extension is always .md, regardless of input",
);

// ── isGenuineDocumentNotFound ────────────────────────────────────────────
// The fact the document detail page's whole "not found" vs "couldn't load"
// split rests on. Reproduced live (2026-08-31, this e2e stack): a session
// that had gone stale mid-test made a real GET 401, and before this fix the
// document page rendered the exact same "This document isn't in this
// project any more. It may have been deleted…" copy it shows for an actual
// deletion -- a customer's own just-written document, reported as possibly
// gone, when the read had simply failed.
function withHttpStatus(message: string, httpStatus?: number): Error {
  const error = new Error(message);
  if (httpStatus !== undefined) (error as Error & { httpStatus?: number }).httpStatus = httpStatus;
  return error;
}

assert(
  isGenuineDocumentNotFound(withHttpStatus("Document not found.", 200)) === true,
  "HTTP 200 + the backend's own 'no' is a genuine not-found",
);
assert(
  isGenuineDocumentNotFound(withHttpStatus("Authentication required.", 401)) === false,
  "a 401 (expired/invalid session) is a failed read, never a 'deleted' answer",
);
assert(
  isGenuineDocumentNotFound(withHttpStatus("Forbidden.", 403)) === false,
  "a 403 (access denied) is a failed read too, not evidence of deletion",
);
assert(
  isGenuineDocumentNotFound(withHttpStatus("Internal error.", 500)) === false,
  "a 5xx upstream failure must not be reported as 'this document is gone'",
);
assert(
  isGenuineDocumentNotFound(withHttpStatus("Failed to fetch")) === false,
  "a network-level throw (fetch() itself failing) carries no httpStatus at all -- also not a not-found",
);
assert(
  isGenuineDocumentNotFound("not an Error instance") === false,
  "a non-Error thrown value is never treated as a genuine not-found",
);

// --- Summary ---

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}

/**
 * Run: npx tsx lib/workspace/fleet/agent-profile-files.test.ts
 */

import { extractProfileFiles, formatFileSize, isImageAttachment } from "./agent-profile-files";

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

// No turns, no attachments field, non-array attachments — all quietly
// produce an empty list, never a thrown error (this runs against whatever
// GET /api/threads/{id} actually returns, and that shape is not this
// module's to enforce).
assert(extractProfileFiles([]).length === 0, "no turns -> no files");
assert(extractProfileFiles([{ role: "user" }]).length === 0, "a turn with no attachments field -> no files");
assert(extractProfileFiles([{ role: "user", attachments: "not-an-array" as any }]).length === 0, "a non-array attachments field is ignored, not thrown on");
assert(extractProfileFiles([null, undefined, 3, "x"] as any).length === 0, "garbage turn entries are skipped, not thrown on");

// The real shape: uploadChatAttachment's response, echoed back by
// GET /threads/{id} as each turn's `attachments` array.
const turnA = {
  role: "user",
  created_at: "2026-08-10T10:00:00Z",
  attachments: [
    { file_id: "f1", filename: "notes.pdf", content_type: "application/pdf", size: 20480, url: "/api/sage-chat/attachments/f1.pdf" },
  ],
};
const turnB = {
  role: "user",
  created_at: "2026-08-15T10:00:00Z",
  attachments: [
    { file_id: "f2", filename: "photo.png", content_type: "image/png", size: 512000, url: "/api/sage-chat/attachments/f2.png" },
  ],
};
const files = extractProfileFiles([turnA, turnB]);
assert(files.length === 2, "two turns, two distinct files -> two entries");
assert(files[0].fileId === "f2", "newest turn's file sorts first");
assert(files[1].fileId === "f1", "older turn's file sorts second");
assert(files[0].fromRole === "user", "fromRole carries the turn's own role");
assert(files[0].filename === "photo.png", "filename carried through verbatim");

// A file_id repeated across turns keeps only the first-encountered
// occurrence — never a second row for the same file.
const dupe = extractProfileFiles([
  { role: "user", created_at: "2026-08-01T00:00:00Z", attachments: [{ file_id: "dup", filename: "a.txt", content_type: "text/plain", size: 10, url: "/x" }] },
  { role: "assistant", created_at: "2026-08-02T00:00:00Z", attachments: [{ file_id: "dup", filename: "a-renamed.txt", content_type: "text/plain", size: 999, url: "/y" }] },
]);
assert(dupe.length === 1, "a repeated file_id collapses to one entry");
assert(dupe[0].filename === "a.txt", "the first-encountered occurrence's metadata wins on a duplicate");

// Entries missing an id or a url are unusable (can't dedupe / can't open)
// and must not render as a dead link.
const unusable = extractProfileFiles([
  { role: "user", attachments: [{ filename: "no-id.txt", url: "/z" }] },
  { role: "user", attachments: [{ file_id: "no-url", filename: "no-url.txt" }] },
]);
assert(unusable.length === 0, "attachments missing file_id or url are dropped, not rendered as dead links");

// isImageAttachment / formatFileSize — the two small display helpers.
assert(isImageAttachment("image/png"), "image/png is an image");
assert(isImageAttachment("IMAGE/JPEG"), "content type match is case-insensitive");
assert(!isImageAttachment("application/pdf"), "a pdf is not an image");
assert(!isImageAttachment(""), "an empty content type is not an image");

assert(formatFileSize(0) === "", "zero bytes formats to nothing (never \"0 B\")");
assert(formatFileSize(-5) === "", "a negative size formats to nothing rather than a nonsense string");
assert(formatFileSize(512) === "512 B", "sub-KB sizes render in bytes");
assert(formatFileSize(20000) === "20 KB", "a clean multiple of 1000 bytes drops the trailing .0");
assert(formatFileSize(20480) === "20.5 KB", "20480 bytes renders with one decimal, rounded");
assert(formatFileSize(1_500_000) === "1.5 MB", "1.5MB renders with one decimal");

// --- Summary ---

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}

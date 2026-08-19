/**
 * Imports the REAL merge (buildWorkspaceRecentWork) rather than
 * re-deriving the ranking here — same discipline agent-count-shape.test.ts
 * / inbox-needs-you.test.ts already apply to their own pure modules.
 *
 * Run: npx tsx lib/workspace/fleet/workspace-recent-work.test.ts
 */

import { buildWorkspaceRecentWork } from "./workspace-recent-work";

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

// Newest-first merge across both sources.
{
  const docs = [{ id: "d1", created_at: "2026-08-10T10:00:00Z" }];
  const tasks = [{ id: "t1", completed_at: "2026-08-15T10:00:00Z" }];
  const merged = buildWorkspaceRecentWork(docs, tasks);
  assert(merged.length === 2, "both events survive the merge");
  assert(merged[0].kind === "task_completed" && merged[0].item.id === "t1", "newer task event sorts first");
  assert(merged[1].kind === "document" && merged[1].item.id === "d1", "older document event sorts second");
}

// A task with no completed_at is not a completion event — never fabricate one.
{
  const merged = buildWorkspaceRecentWork([], [{ id: "t2", completed_at: null }]);
  assert(merged.length === 0, "an uncompleted task contributes nothing");
}

// A document entry with no created_at is dropped rather than sorted as if it were oldest.
{
  const merged = buildWorkspaceRecentWork([{ id: "d2", created_at: null }], []);
  assert(merged.length === 0, "a timestamp-less document entry is dropped, not guessed at");
}

// The cap is honored after sorting, not before — the newest N survive.
{
  const docs = Array.from({ length: 5 }, (_, i) => ({
    id: `d${i}`,
    created_at: `2026-08-0${i + 1}T00:00:00Z`,
  }));
  const merged = buildWorkspaceRecentWork(docs, [], 2);
  assert(merged.length === 2, "capped at the requested limit");
  assert(merged[0].item.id === "d4" && merged[1].item.id === "d3", "the cap keeps the newest, not the first N encountered");
}

// Empty input is a real, common state — no crash, no phantom rows.
{
  const merged = buildWorkspaceRecentWork([], []);
  assert(merged.length === 0, "no events, no rows");
}

console.log(`workspace-recent-work.test.ts: ${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);

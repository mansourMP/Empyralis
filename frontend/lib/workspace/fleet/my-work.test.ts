/**
 * "My work" scoping, proven against the REAL rule my-work.ts exports rather
 * than a re-typed copy of it — same discipline agent-count-shape.test.ts and
 * primary-rail-nav.test.ts already apply.
 *
 * The assertions that matter are the NEGATIVE ones: this surface's whole
 * risk is over-inclusion (a teammate's work, or unclaimed work, quietly
 * filed under the reader's name), and an over-inclusive version of this
 * module would satisfy every "my task is listed" assertion perfectly.
 *
 * Run: npx tsx lib/workspace/fleet/my-work.test.ts
 */

import {
  isOpenMyWork,
  myWorkBadgeCount,
  myWorkBucket,
  selectMyWork,
  type MyWorkTaskShape,
} from "./my-work";

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

const ME = "user_me";
const OTHER = "user_other";
const AGENT = "agent_install_1";

const task = (over: Partial<MyWorkTaskShape> & { id?: string } = {}): MyWorkTaskShape & { id: string } => ({
  id: over.id || "t",
  status: "todo",
  assignee_user_id: null,
  assignee_agent_id: null,
  created_by: null,
  ...over,
});

// ── Bucket "mine": assigned to me as a person ─────────────────────────────
assert(
  myWorkBucket(task({ assignee_user_id: ME }), ME) === "mine",
  "a task assigned to me is mine",
);
assert(
  myWorkBucket(task({ assignee_user_id: OTHER }), ME) === null,
  "a task assigned to another person is NOT mine",
);

// ── Bucket "agent": work I handed to an agent ─────────────────────────────
assert(
  myWorkBucket(task({ assignee_agent_id: AGENT, created_by: ME }), ME) === "agent",
  "a task I filed and handed to an agent is in the agent bucket",
);
assert(
  myWorkBucket(task({ assignee_agent_id: AGENT, created_by: OTHER }), ME) === null,
  "a task SOMEBODY ELSE handed to an agent is not my work — this is the over-inclusion the created_by clause exists to stop",
);
assert(
  myWorkBucket(task({ assignee_agent_id: AGENT, created_by: null }), ME) === null,
  "an agent-assigned task with no known author is not claimed for me",
);

// ── Everything else is out ────────────────────────────────────────────────
assert(
  myWorkBucket(task({ created_by: ME }), ME) === null,
  "an UNASSIGNED task I created is not my work — My work is an ownership list, not a job board",
);
assert(
  myWorkBucket(task({ assignee_user_id: OTHER, created_by: ME }), ME) === null,
  "a task I created and handed to another PERSON is theirs, not mine",
);
assert(
  myWorkBucket(task({ assignee_user_id: ME }), null) === null,
  "with no resolved user identity nothing is claimed — never a fallback that guesses",
);
assert(
  myWorkBucket(task({ assignee_user_id: ME }), "  ") === null,
  "a blank user id is treated as no identity, not as a matchable value",
);

// ── The two buckets never overlap, and never both fire ────────────────────
// The backend's project_tasks_single_assignee_check guarantees a task cannot
// carry both assignee columns; this asserts the human column wins if one
// ever arrives that way, rather than the task appearing in both lists.
assert(
  myWorkBucket(task({ assignee_user_id: ME, assignee_agent_id: AGENT, created_by: ME }), ME) === "mine",
  "if both assignee columns were somehow set, the human one decides — one task, one bucket",
);

// ── selectMyWork partitions, preserving order ─────────────────────────────
const rows = [
  task({ id: "a", assignee_user_id: ME }),
  task({ id: "b", assignee_user_id: OTHER }),
  task({ id: "c", assignee_agent_id: AGENT, created_by: ME }),
  task({ id: "d", assignee_agent_id: AGENT, created_by: OTHER }),
  task({ id: "e", assignee_user_id: ME, status: "done" }),
];
const split = selectMyWork(rows, ME);
assert(split.mine.map((t) => t.id).join(",") === "a,e", "mine holds exactly the human-assigned rows, in input order");
assert(split.agent.map((t) => t.id).join(",") === "c", "agent holds exactly the rows I handed over");
assert(
  split.mine.length + split.agent.length < rows.length,
  "partitioning DROPS rows that are nobody's business here — it is a filter, not a regrouping",
);

// ── Open-ness: only `done` is terminal ────────────────────────────────────
for (const status of ["backlog", "todo", "in_progress", "awaiting_input", "blocked", "in_review"]) {
  assert(isOpenMyWork(task({ status })), `${status} is still open work`);
}
assert(!isOpenMyWork(task({ status: "done" })), "done is the only terminal status");
assert(!isOpenMyWork(task({ status: "DONE" })), "status comparison is case-insensitive");

// ── The badge counts BOTH buckets, open only ──────────────────────────────
assert(
  myWorkBadgeCount(rows, ME) === 2,
  `badge counts open rows across both buckets (a + c), got ${myWorkBadgeCount(rows, ME)}`,
);
// The same five rows, read by the other person: `b` (assigned to them) and
// `d` (they handed it to an agent) — a completely disjoint set from mine.
assert(myWorkBadgeCount(rows, OTHER) === 2, "another reader's badge counts THEIR rows, not mine");
const otherSplit = selectMyWork(rows, OTHER);
assert(
  [...otherSplit.mine, ...otherSplit.agent].every((t) => !["a", "c", "e"].includes(t.id)),
  "two readers of the same task list share no rows — the split is per-identity, not a shared view",
);
assert(myWorkBadgeCount(rows, null) === 0, "no identity resolves to no badge, never to everything");
assert(myWorkBadgeCount([], ME) === 0, "an empty workspace shows no badge — a zero badge is noise");

// The badge and the page must agree — the badge is not a second rule.
const openInPage = [...split.mine, ...split.agent].filter(isOpenMyWork).length;
assert(
  openInPage === myWorkBadgeCount(rows, ME),
  "the rail badge equals what the page actually lists as open — one rule, two readers",
);

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}

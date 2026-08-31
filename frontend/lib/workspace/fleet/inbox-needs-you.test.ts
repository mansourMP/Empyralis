/**
 * "Needs you" scoping, proven against the REAL rule inbox-needs-you.ts
 * exports rather than a re-typed copy of it — same discipline
 * my-work.test.ts / agent-count-shape.test.ts already apply.
 *
 * The assertions that matter are the NEGATIVE ones: this surface's whole
 * risk is showing something that does NOT need the reader (a teammate's
 * stuck task, someone else's read notification, a routine chat completion
 * dressed up as a "run"), which is exactly the failure the old ledger-backed
 * Inbox had.
 *
 * Run: npx tsx lib/workspace/fleet/inbox-needs-you.test.ts
 */

import {
  countUnseenBlockedRuns,
  inboxNeedsYouCount,
  isBlockedRunEvent,
  isMyStuckTask,
  notificationTitle,
  planInboxNeedsYou,
  resolveInboxNeedsYouViewState,
  type InboxBlockedRunShape,
  type InboxNotificationShape,
  type InboxTaskShape,
} from "./inbox-needs-you";

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

const task = (over: Partial<InboxTaskShape> & { id?: string } = {}): InboxTaskShape & { id: string } => ({
  id: over.id || "t1",
  title: "A task",
  status: "todo",
  assignee_user_id: null,
  project_id: "proj_1",
  ...over,
});

// ── isMyStuckTask ──────────────────────────────────────────────────────────
assert(
  isMyStuckTask(task({ assignee_user_id: ME, status: "blocked" }), ME) === true,
  "my task in blocked needs me",
);
assert(
  isMyStuckTask(task({ assignee_user_id: ME, status: "awaiting_input" }), ME) === true,
  "my task in awaiting_input needs me",
);
assert(
  isMyStuckTask(task({ assignee_user_id: ME, status: "in_progress" }), ME) === false,
  "my task merely in_progress does NOT need me — not every open task belongs here",
);
assert(
  isMyStuckTask(task({ assignee_user_id: OTHER, status: "blocked" }), ME) === false,
  "a TEAMMATE's stuck task is never mine — the over-inclusion risk this whole module exists to avoid",
);
assert(
  isMyStuckTask(task({ assignee_user_id: null, status: "blocked" }), ME) === false,
  "an unassigned stuck task does not need any one specific person",
);
assert(isMyStuckTask(task({ assignee_user_id: ME, status: "blocked" }), null) === false, "no viewer, nothing is theirs");
assert(isMyStuckTask(task({ assignee_user_id: ME, status: "blocked" }), "") === false, "blank viewer id, same as no viewer");

// ── notificationTitle ────────────────────────────────────────────────────
assert(
  notificationTitle({ id: "n1", source_event_type: "task_mention" } as InboxNotificationShape) === "Mentioned you",
  "mention gets its own label",
);
assert(
  notificationTitle({ id: "n2", source_event_type: "task_assigned" } as InboxNotificationShape) === "Assigned to you",
  "assignment gets its own label",
);
assert(
  notificationTitle({ id: "n3", source_event_type: "bogus" } as InboxNotificationShape) === "Notification",
  "an unrecognized event type falls back rather than showing a raw enum value",
);

// ── isBlockedRunEvent: STRUCTURAL, never string-matched ──────────────────
assert(
  isBlockedRunEvent({ event_class: "blocked_action", title: "Run failed" } as InboxBlockedRunShape) === true,
  "blocked_action is the real signal",
);
assert(
  isBlockedRunEvent({ event_class: "system_activity", title: "run failed to do something" } as InboxBlockedRunShape) === false,
  "the word 'failed' appearing in an unrelated title must NOT trigger this — stale string matching is the exact failure mode this avoids",
);
assert(
  isBlockedRunEvent({ event_class: "sage_activity", title: "Agent chat completed" } as InboxBlockedRunShape) === false,
  "a routine chat completion is never a blocked run",
);

// ── planInboxNeedsYou: composition + ranking ─────────────────────────────
const taskHrefFor = (id: string | null | undefined) => (id ? `/tasks/${id}` : null);
const agentHrefFor = (id: string | null | undefined) => (id ? `/agents/${id}` : null);

const stuckTasks: InboxTaskShape[] = [
  task({ id: "old", assignee_user_id: ME, status: "blocked", updated_at: "2026-08-01T00:00:00Z" }),
  task({ id: "new", assignee_user_id: ME, status: "awaiting_input", updated_at: "2026-08-18T00:00:00Z" }),
  task({ id: "not-mine", assignee_user_id: OTHER, status: "blocked", updated_at: "2026-08-19T00:00:00Z" }),
  task({ id: "not-stuck", assignee_user_id: ME, status: "todo", updated_at: "2026-08-19T00:00:00Z" }),
];

const notifications: InboxNotificationShape[] = [
  { id: "n1", source_event_type: "task_mention", body: "@you check this", task_id: "t1", created_at: "2026-08-10T00:00:00Z" },
  { id: "n2", source_event_type: "task_assigned", body: null, task_id: "t2", created_at: "2026-08-15T00:00:00Z" },
];

const blockedRuns: InboxBlockedRunShape[] = [
  { id: "e1", title: "Run failed", event_class: "blocked_action", install_id: "agent_1", created_at: "2026-08-12T00:00:00Z" },
  { id: "e2", title: "Configured", event_class: "system_activity", install_id: "agent_1", created_at: "2026-08-19T00:00:00Z" },
];

const groups = planInboxNeedsYou({ stuckTasks, notifications, blockedRuns, userId: ME, taskHrefFor, agentHrefFor });

assert(groups.tasks.length === 2, "only MY stuck tasks are included, not the teammate's or the not-stuck one");
assert(groups.tasks[0].id === "task:old", "oldest stuck task sorts FIRST — this is the founder's own 17-day complaint");
assert(groups.tasks[1].id === "task:new", "newer stuck task sorts second");
assert(groups.tasks[0].href === "/tasks/old", "task href resolves through the caller's own resolver");

assert(groups.notifications.length === 2, "both notifications pass through — this module trusts the caller's own scoping");
assert(groups.notifications[0].id === "notification:n2", "newest notification sorts first");
assert(groups.notifications[1].id === "notification:n1", "older notification sorts second");

assert(groups.runs.length === 1, "only the blocked_action event is a run — the system_activity noise row is excluded");
assert(groups.runs[0].id === "run:e1", "the real run event is present");
assert(groups.runs[0].href === "/agents/agent_1", "run href resolves to the agent via the caller's own resolver");

assert(inboxNeedsYouCount(groups) === 2 + 2 + 1, "total count is the sum of all three groups, no double counting");

const emptyGroups = planInboxNeedsYou({
  stuckTasks: [],
  notifications: [],
  blockedRuns: [],
  userId: ME,
  taskHrefFor,
  agentHrefFor,
});
assert(inboxNeedsYouCount(emptyGroups) === 0, "genuinely nothing outstanding counts as zero, not a guess");

// A task/notification with no resolvable home renders unclickable text, not
// a link to nowhere — never a dead control.
const unresolvedHrefFor = () => null;
const unresolvable = planInboxNeedsYou({
  stuckTasks: [task({ id: "t9", assignee_user_id: ME, status: "blocked" })],
  notifications: [],
  blockedRuns: [],
  userId: ME,
  taskHrefFor: unresolvedHrefFor,
  agentHrefFor,
});
assert(unresolvable.tasks[0].href === null, "an unresolvable task href is null, never a broken link string");

// ── countUnseenBlockedRuns ────────────────────────────────────────────────
const runsForBadge: InboxBlockedRunShape[] = [
  { id: "r1", event_class: "blocked_action", created_at: "2026-08-01T00:00:00Z" },
  { id: "r2", event_class: "blocked_action", created_at: "2026-08-19T00:00:00Z" },
  { id: "r3", event_class: "system_activity", created_at: "2026-08-19T00:00:00Z" },
];
assert(
  countUnseenBlockedRuns(runsForBadge, "2026-08-10T00:00:00Z") === 1,
  "only the blocked_action event AFTER the since-timestamp counts",
);
assert(
  countUnseenBlockedRuns(runsForBadge, null) === 2,
  "with no last-seen timestamp at all, every blocked_action event counts as unseen",
);
assert(countUnseenBlockedRuns([], "2026-08-01T00:00:00Z") === 0, "no events, no unseen count");

// ── A failed-run row NAMES the agent and SAYS what went wrong ─────────────
//
// The row used to be the bare word "Run failed" with `detail: null` and an
// href built from `install_id`, which the producer never set — so a person
// saw a failure, could not tell which agent, and had no route to why.
//
// Note the fixture shape: `install_id` and `summary` are what the BACKEND
// now writes (outbox_service._run_transition_metadata → the ledger row),
// not fields invented here to make an assertion pass.
const agentNameFor = (id: string | null | undefined) =>
  id === "agent_1" ? "Rey" : null;
const namedRuns: InboxBlockedRunShape[] = [
  {
    id: "e9",
    title: "Run failed",
    event_class: "blocked_action",
    install_id: "agent_1",
    summary: "provider_auth_failed: DeepSeek rejected the API key for this workspace.",
    created_at: "2026-08-29T00:00:00Z",
  },
];
const named = planInboxNeedsYou({
  stuckTasks: [],
  notifications: [],
  blockedRuns: namedRuns,
  userId: ME,
  taskHrefFor,
  agentHrefFor,
  agentNameFor,
});
assert(named.runs[0].title.includes("Rey"), "the row names the agent that failed");
assert(
  named.runs[0].detail === "provider_auth_failed: DeepSeek rejected the API key for this workspace.",
  "the row says WHY, from the event's own summary",
);
assert(named.runs[0].href === "/agents/agent_1", "the row links to the agent whose traces explain it");

// An agent the caller cannot name falls back to the plain title rather
// than inventing one — "which agent" and "an agent we could not name" are
// different facts.
const unnamed = planInboxNeedsYou({
  stuckTasks: [],
  notifications: [],
  blockedRuns: [{ id: "e10", title: "Run failed", event_class: "blocked_action", install_id: "ghost", created_at: "2026-08-29T00:00:00Z" }],
  userId: ME,
  taskHrefFor,
  agentHrefFor,
  agentNameFor,
});
assert(unnamed.runs[0].title === "Run failed", "an unnameable agent does not get a fabricated name");
assert(unnamed.runs[0].detail === null, "no summary means no detail, never an empty string");

// A row with no install_id at all still renders — as plain, unclickable
// text. That is the honest degradation, not a link to nowhere.
const orphan = planInboxNeedsYou({
  stuckTasks: [],
  notifications: [],
  blockedRuns: [{ id: "e11", event_class: "blocked_action", created_at: "2026-08-29T00:00:00Z" }],
  userId: ME,
  taskHrefFor,
  agentHrefFor,
  agentNameFor,
});
assert(orphan.runs.length === 1, "an unattributable failure is still surfaced");
assert(orphan.runs[0].href === null, "…and is unclickable rather than linked to nowhere");

// The resolvers are OPTIONAL — every pre-existing caller keeps working.
const legacy = planInboxNeedsYou({
  stuckTasks: [],
  notifications: [],
  blockedRuns: namedRuns,
  userId: ME,
  taskHrefFor,
  agentHrefFor,
});
assert(legacy.runs[0].title === "Run failed", "without agentNameFor the row keeps its plain title");
assert(legacy.runs[0].detail !== null, "the summary still reaches the row without any resolver");

// ── every row: the TITLE names a thing, the SUBTITLE names the reason ────
// This rule held for tasks and runs and was INVERTED for notifications, and
// nothing here noticed — the only assertions were on notificationTitle() in
// isolation, never on which FIELD of the row it lands in. It surfaced when the
// mobile Inbox dropped its section headers: four consecutive rows titled
// "Commented on your task" with the actual task name truncated away. A label
// carried by most rows distinguishes nothing.
{
  const g = planInboxNeedsYou({
    stuckTasks: [
      { id: "s1", title: "Ship the picker", status: "blocked", assignee_user_id: ME, updated_at: "2026-08-01T00:00:00Z" },
    ],
    notifications: [
      { id: "n1", source_event_type: "task_comment", body: 'New comment on "Ship the picker"', created_at: "2026-08-02T00:00:00Z" },
    ],
    blockedRuns: [],
    userId: ME,
    taskHrefFor,
    agentHrefFor,
  });

  assert(g.notifications.length === 1, "a notification row is produced");
  assert(
    g.notifications[0].title === 'New comment on "Ship the picker"',
    "the notification TITLE names the thing, not the category",
  );
  assert(
    g.notifications[0].detail === "Commented on your task",
    "the notification SUBTITLE carries the reason — matching task and run rows",
  );

  assert(g.tasks.length === 1 && g.tasks[0].title === "Ship the picker", "a task row titles the thing");
  assert(g.tasks[0].detail === "Blocked", "a task row puts the reason in the subtitle");
}

// ── resolveInboxNeedsYouViewState — real content outranks "no agents yet" ──
// Reproduces a bug found live: a fresh workspace (zero agents — the
// ordinary state for a customer who has only started tracking tasks, not
// an edge case) with a task assigned to the owner and stuck in
// `awaiting_input` rendered "Create your first agent" instead of that
// task, because the OLD page.tsx ternary checked `freshWorkspace` before
// `genuinelyEmpty`. These assertions are the ones that would have caught
// it: the moment `needsYouTotal` is nonzero, freshWorkspace must never win.
{
  assert(
    resolveInboxNeedsYouViewState({ stillLoading: true, totallyFailed: false, freshWorkspace: true, needsYouTotal: 0 }) === "loading",
    "still resolving every source -> loading, regardless of freshWorkspace",
  );
  assert(
    resolveInboxNeedsYouViewState({ stillLoading: false, totallyFailed: true, freshWorkspace: true, needsYouTotal: 0 }) === "error",
    "every source failed -> error, regardless of freshWorkspace",
  );
  assert(
    resolveInboxNeedsYouViewState({ stillLoading: false, totallyFailed: false, freshWorkspace: true, needsYouTotal: 3 }) === "content",
    "THE bug: a fresh (zero-agent) workspace with real needs-you content must show that content, not the onboarding nudge",
  );
  assert(
    resolveInboxNeedsYouViewState({ stillLoading: false, totallyFailed: false, freshWorkspace: false, needsYouTotal: 2 }) === "content",
    "an established workspace with content shows that content",
  );
  assert(
    resolveInboxNeedsYouViewState({ stillLoading: false, totallyFailed: false, freshWorkspace: true, needsYouTotal: 0 }) === "onboarding",
    "fresh AND genuinely empty -> the onboarding nudge is still correct here",
  );
  assert(
    resolveInboxNeedsYouViewState({ stillLoading: false, totallyFailed: false, freshWorkspace: false, needsYouTotal: 0 }) === "caught-up",
    "not fresh, nothing needs you -> caught up, never the onboarding nudge",
  );
}

if (failed > 0) {
  console.error(`\n${failed} failed, ${passed} passed`);
  process.exit(1);
} else {
  console.log(`${passed} passed`);
}

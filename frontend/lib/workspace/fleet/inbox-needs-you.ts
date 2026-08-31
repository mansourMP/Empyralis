/**
 * WHAT LANDS IN THE INBOX — the whole rule, as a pure function, same idiom
 * as agent-count-shape.ts / channel-doors.ts / my-work.ts: data and pure
 * functions in their own dependency-light module, imported directly by a
 * plain `tsx` test, so the expected set and the actual set can never come
 * from one place.
 *
 * THE PROBLEM THIS REPLACES. The old Inbox rendered the raw workspace
 * activity ledger — 50 rows sampled were almost entirely `Configured`,
 * `Created`, "<Agent> chat completed" and "<Agent> deleted", none of which
 * needs a human. Meanwhile a real task sat in `Needs input`, assigned to
 * the founder, for 17 days, and the Inbox never surfaced it once — a
 * broadcast log standing in for a "what needs me" surface, which is a
 * different question the ledger was never built to answer.
 *
 * THREE SOURCES, never a fourth invented here — each already exists and is
 * correctly scoped, this module only composes and ranks what its caller
 * fetched:
 *
 *   1. Real per-user notifications (task_notification_service.py via
 *      GET /fleet/notifications) — mentions, "assigned to you", "commented
 *      on a task you own". Recipient-scoped server-side; this module trusts
 *      whatever the caller passes in rather than re-filtering on `is_read`,
 *      so a future "show read too" view isn't silently dropped here.
 *
 *   2. The caller's OWN tasks — theirs directly, or theirs via an agent they
 *      handed it to (my-work.ts's `myWorkBucket`, reused here rather than
 *      re-derived) — stuck in `blocked` / `awaiting_input`. The exact status
 *      pair that sat unseen for 17 days. An agent-assigned task can NEVER
 *      carry `assignee_user_id` (project_tasks_service's assign_task/
 *      assign_task_to_user NULL out the other column on write — mutually
 *      exclusive by construction), so a rule that only checked the human
 *      assignee column silently excluded every task an agent was actually
 *      working — which is most of them. Still deliberately narrower than
 *      `myWorkBucket` alone: every open task that's "mine" is a candidate,
 *      but only a STUCK one needs me to actually DO something right now —
 *      `in_progress`/`todo`/`backlog` don't belong on a "needs you" surface
 *      any more than a routine chat completion does.
 *
 *   3. Blocked/failed agent runs — activity_ledger_service.py's own
 *      `blocked_action` event class (run_failed, machine_revoked,
 *      machine_enrollment_failed — see record_notification_activity's
 *      classification). Filtered on the STRUCTURAL event_class field, never
 *      on title/action text — CLAUDE.md's own "stale string matching"
 *      failure mode (an error bucket matched "ai limit"; the message was
 *      reworded and users got a generic failure for five weeks).
 *
 * RANKING IS PER-GROUP, NOT ONE CHRONOLOGICAL MERGE, and that split is
 * deliberate. Stuck tasks sort OLDEST-first: the founder's own complaint
 * was a task that had been sitting for 17 days, and a newest-first merge
 * would keep burying exactly that item under whatever moved most recently
 * — the opposite of what a "needs you" surface is for. Notifications and
 * runs sort newest-first, the ordinary "what just happened" reading. Three
 * groups with three internally-consistent orders beats one merged list that
 * would have to fake a single "urgency" score across unrelated units (a
 * comment from ten minutes ago vs. a task idle for two weeks).
 */

import { myWorkBucket } from "./my-work";

export type InboxTaskShape = {
  id: string;
  title?: string | null;
  status?: string | null;
  assignee_user_id?: string | null;
  /** The two fields myWorkBucket needs to recognize agent-owned work as
   *  mine. Both already ride every /fleet/tasks row (project_tasks_
   *  service._row_to_task) — added here, not invented. */
  assignee_agent_id?: string | null;
  created_by?: string | null;
  project_id?: string | null;
  updated_at?: string | null;
  created_at?: string | null;
};

/** The two statuses that mean "this needs a human, right now" — a subset of
 *  project_tasks_service.TASK_STATUS_ORDER, never a re-derivation of it. */
export const INBOX_STUCK_STATUSES = ["blocked", "awaiting_input"] as const;
export type InboxStuckStatus = (typeof INBOX_STUCK_STATUSES)[number];

/** "Mine" (either bucket) AND currently stuck. Ownership is delegated
 *  entirely to my-work.ts's `myWorkBucket` — the one place that rule lives —
 *  rather than re-checking `assignee_user_id` here, which is the bug this
 *  replaces: every task an agent is actually working has
 *  `assignee_user_id = NULL` by construction (assign_task NULLs it out),
 *  so a rule that only looked at that column returned false for all of
 *  them and Inbox could never show a stuck agent-owned task.
 *
 *  Both properties the old rule got right still hold, now enforced by
 *  myWorkBucket instead of duplicated here: a teammate's stuck task never
 *  surfaces under my name (myWorkBucket's "agent" bucket requires
 *  `created_by === me`, not just an agent assignee), and the status check
 *  below still keeps this narrower than "everything I own" — that's My
 *  work's job, not Inbox's. */
export function isMyStuckTask(task: InboxTaskShape, userId: string | null): boolean {
  if (myWorkBucket(task, userId) === null) return false;
  return (INBOX_STUCK_STATUSES as readonly string[]).includes(String(task.status || "").trim());
}

export type InboxNotificationShape = {
  id: string;
  source_event_type?: string | null;
  body?: string | null;
  task_id?: string | null;
  is_read?: boolean;
  created_at?: string | null;
};

const NOTIFICATION_TITLE: Record<string, string> = {
  task_mention: "Mentioned you",
  task_assigned: "Assigned to you",
  task_comment: "Commented on your task",
};

export function notificationTitle(n: InboxNotificationShape): string {
  return NOTIFICATION_TITLE[String(n.source_event_type || "").trim()] || "Notification";
}

export type InboxBlockedRunShape = {
  id?: string | null;
  title?: string | null;
  action?: string | null;
  event_class?: string | null;
  install_id?: string | null;
  created_at?: string | null;
  /** The row's own account of what went wrong. It is on the wire
   *  (WorkspaceActivityEvent carries it) and was simply not read here, so
   *  every failed run rendered as the bare word "Run failed" with the
   *  cause — which exists, in the trace — reachable from nowhere. */
  summary?: string | null;
};

/** Structural, not string-matched: activity_ledger_service.py's
 *  record_notification_activity classifies run_failed/machine_revoked/
 *  machine_enrollment_failed into event_class "blocked_action" server-side
 *  — this reads that enum field, never the human-readable title, which
 *  changes wording without notice (CLAUDE.md's own "stale string matching"
 *  failure mode). */
export function isBlockedRunEvent(event: InboxBlockedRunShape): boolean {
  return String(event.event_class || "").trim() === "blocked_action";
}

export type InboxNeedsYouKind = "task" | "notification" | "run";

export type InboxNeedsYouItem = {
  kind: InboxNeedsYouKind;
  /** Stable across polls — prefixed by kind so a task id and a notification
   *  id can never collide in a merged key space. */
  id: string;
  title: string;
  detail: string | null;
  timestamp: string | null;
  /** Null when this item's real home can't be resolved (e.g. a notification
   *  whose task isn't in the caller's project map) — rendered as plain,
   *  unclickable text rather than a link to nowhere. Never a second place
   *  the underlying thing is edited; every href points at the item's one
   *  real home (its task page, or the agent's own Work tab). */
  href: string | null;
};

export type InboxNeedsYouGroups = {
  tasks: InboxNeedsYouItem[];
  notifications: InboxNeedsYouItem[];
  runs: InboxNeedsYouItem[];
};

function sortMillis(ts?: string | null): number {
  const t = ts ? Date.parse(ts) : NaN;
  return Number.isFinite(t) ? t : 0;
}

export function planInboxNeedsYou(input: {
  stuckTasks: readonly InboxTaskShape[];
  notifications: readonly InboxNotificationShape[];
  blockedRuns: readonly InboxBlockedRunShape[];
  userId: string | null;
  taskHrefFor: (taskId: string | null | undefined) => string | null;
  agentHrefFor: (installId: string | null | undefined) => string | null;
  /** Resolves an install id to the agent's own name. A row that cannot
   *  name the agent it is about is the complaint this exists to answer. */
  agentNameFor?: (installId: string | null | undefined) => string | null;
}): InboxNeedsYouGroups {
  const {
    stuckTasks,
    notifications,
    blockedRuns,
    userId,
    taskHrefFor,
    agentHrefFor,
    agentNameFor,
  } = input;

  const tasks: InboxNeedsYouItem[] = stuckTasks
    .filter((t) => isMyStuckTask(t, userId))
    .slice()
    .sort((a, b) => sortMillis(a.updated_at || a.created_at) - sortMillis(b.updated_at || b.created_at))
    .map((t) => ({
      kind: "task" as const,
      id: `task:${t.id}`,
      title: t.title || "Untitled task",
      detail: String(t.status || "").trim() === "blocked" ? "Blocked" : "Needs your input",
      timestamp: t.updated_at || t.created_at || null,
      href: taskHrefFor(t.id),
    }));

  const notificationItems: InboxNeedsYouItem[] = notifications
    .slice()
    .sort((a, b) => sortMillis(b.created_at) - sortMillis(a.created_at))
    .map((n) => ({
      kind: "notification" as const,
      id: `notification:${n.id}`,
      // The REASON goes in the subtitle, the THING goes in the title —
      // the same shape the task and run rows above and below already use.
      // This was inverted, and with the mobile Inbox's section headers gone
      // the inversion became visible: four rows in a row titled "Commented on
      // your task" and three titled "Mentioned you", each with the actual
      // task name truncated away in the grey line. A label carried by most
      // rows distinguishes nothing — the same argument that retired
      // `activity_preview` on the agent cards and took the colour off the
      // amber reach line. `body` names a real thing ("New comment on
      // \"Review Metro watchFolders setup\""); notificationTitle is a
      // category. Falls back to the category when body is empty, because a
      // blank title is worse than a repeated one.
      title: (n.body || "").trim() || notificationTitle(n),
      detail: notificationTitle(n),
      timestamp: n.created_at || null,
      href: taskHrefFor(n.task_id),
    }));

  const runs: InboxNeedsYouItem[] = blockedRuns
    .filter(isBlockedRunEvent)
    .slice()
    .sort((a, b) => sortMillis(b.created_at) - sortMillis(a.created_at))
    .map((e) => {
      // "Run failed" names nothing and "detail: null" said nothing. Both
      // facts a person needs — WHICH agent, and WHY — are on the event and
      // were being dropped here.
      const agentName = agentNameFor ? agentNameFor(e.install_id) : null;
      const baseTitle = e.title || "An agent run failed";
      const title = agentName ? `${agentName}: ${baseTitle.toLowerCase()}` : baseTitle;
      // There is no page that opens one trace by id — only the API — so
      // the destination is the agent's own Work tab, which is where its
      // traces are read. A link to a trace route that does not exist would
      // be a dead control wearing a URL.
      return {
        kind: "run" as const,
        id: `run:${e.id || e.created_at}`,
        title,
        detail: (e.summary || "").trim() || null,
        timestamp: e.created_at || null,
        href: agentHrefFor(e.install_id),
      };
    });

  return { tasks, notifications: notificationItems, runs };
}

/** Total across all three groups — the single number both the page's own
 *  empty-state check and the rail badge must agree on, so they are never
 *  computed from two independently-drifting expressions. */
export function inboxNeedsYouCount(groups: InboxNeedsYouGroups): number {
  return groups.tasks.length + groups.notifications.length + groups.runs.length;
}

/** Blocked-run events newer than `sinceIso` — the rail badge's own slice of
 *  the run group (see PrimaryRail.tsx): "unseen since last visit" is the
 *  right question for a feed with no per-row read state, exactly the
 *  principle the old activity-ledger badge already used, now scoped to only
 *  the events that actually need attention instead of every ledger row. */
export function countUnseenBlockedRuns(
  events: readonly InboxBlockedRunShape[],
  sinceIso: string | null,
): number {
  const since = sinceIso ? Date.parse(sinceIso) : NaN;
  return events.filter((e) => {
    if (!isBlockedRunEvent(e)) return false;
    if (!Number.isFinite(since)) return true;
    const t = sortMillis(e.created_at);
    return t > since;
  }).length;
}

/** Which of the "Needs you" tab's five renders to show — pure decision, no
 *  JSX, same idiom as agent-chat-view-state.ts's resolveAgentChatViewState
 *  (same house law it exists to enforce: "'empty' and 'I could not load
 *  this' are different facts and must never share one screen" — here
 *  widened to a THIRD fact this page also has to keep separate: "there is
 *  real content, but you have not created an agent yet" is not "empty").
 *
 * Bug this replaces, found live testing task creation end to end: a brand
 * new workspace with a task assigned to the owner and stuck in
 * `awaiting_input` — textbook "needs you" content — rendered the
 * "Create your first agent" onboarding nudge instead, because page.tsx's
 * ternary checked `freshWorkspace` (zero agents) BEFORE `genuinelyEmpty`
 * (zero needs-you items). Zero agents is the ordinary state for a customer
 * who has only just started tracking tasks — the founder's own pitch is
 * "track your issues," agents are not it — so this was not a rare edge
 * case, it was the FIRST thing a new customer would hit the moment a task
 * of their own got stuck. The onboarding nudge is still correct when the
 * workspace is BOTH fresh and truly empty; it must never outrank real
 * content that already exists. */
export type InboxNeedsYouViewState = "loading" | "error" | "onboarding" | "caught-up" | "content";

export function resolveInboxNeedsYouViewState(params: {
  stillLoading: boolean;
  totallyFailed: boolean;
  freshWorkspace: boolean;
  needsYouTotal: number;
}): InboxNeedsYouViewState {
  const { stillLoading, totallyFailed, freshWorkspace, needsYouTotal } = params;
  if (stillLoading) return "loading";
  if (totallyFailed) return "error";
  // Real content always wins, regardless of how many agents exist — the
  // one line this function exists to enforce.
  if (needsYouTotal > 0) return "content";
  return freshWorkspace ? "onboarding" : "caught-up";
}

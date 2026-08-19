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
 *   2. The caller's OWN tasks stuck in `blocked` / `awaiting_input` — the
 *      exact status pair that sat unseen for 17 days. Deliberately narrower
 *      than my-work.ts's `myWorkBucket("mine")`: every open task assigned to
 *      you is "yours", but only a STUCK one needs you to actually DO
 *      something about it right now — `in_progress`/`todo`/`backlog` don't
 *      belong on a "needs you" surface any more than a routine chat
 *      completion does.
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

export type InboxTaskShape = {
  id: string;
  title?: string | null;
  status?: string | null;
  assignee_user_id?: string | null;
  project_id?: string | null;
  updated_at?: string | null;
  created_at?: string | null;
};

/** The two statuses that mean "this needs a human, right now" — a subset of
 *  project_tasks_service.TASK_STATUS_ORDER, never a re-derivation of it. */
export const INBOX_STUCK_STATUSES = ["blocked", "awaiting_input"] as const;
export type InboxStuckStatus = (typeof INBOX_STUCK_STATUSES)[number];

/** Assigned to this person AND currently stuck. Both conditions matter:
 *  dropping the assignee check would surface a teammate's stuck task under
 *  my name (my-work.ts's own over-inclusion risk, one level down); dropping
 *  the status check would turn this into "everything I own", which is
 *  My work's job, not Inbox's. */
export function isMyStuckTask(task: InboxTaskShape, userId: string | null): boolean {
  const me = String(userId || "").trim();
  if (!me) return false;
  if (String(task.assignee_user_id || "").trim() !== me) return false;
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
}): InboxNeedsYouGroups {
  const { stuckTasks, notifications, blockedRuns, userId, taskHrefFor, agentHrefFor } = input;

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
      title: notificationTitle(n),
      detail: n.body || null,
      timestamp: n.created_at || null,
      href: taskHrefFor(n.task_id),
    }));

  const runs: InboxNeedsYouItem[] = blockedRuns
    .filter(isBlockedRunEvent)
    .slice()
    .sort((a, b) => sortMillis(b.created_at) - sortMillis(a.created_at))
    .map((e) => ({
      kind: "run" as const,
      id: `run:${e.id || e.created_at}`,
      title: e.title || "An agent run failed",
      detail: null,
      timestamp: e.created_at || null,
      href: agentHrefFor(e.install_id),
    }));

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

/**
 * THE WORKSPACE HOME'S ACTIVITY FEED, REBUILT ON REAL WORK — same
 * discipline inbox-needs-you.ts already applies one surface over: data and
 * a pure merge/rank function in their own dependency-light module, so the
 * expected order and the actual order can never come from two places.
 *
 * THE PROBLEM THIS REPLACES. FleetHome's old feed rendered the raw
 * workspace activity ledger (useWorkspaceActivity) — "Configured",
 * "Created", "<Agent> chat completed ×4" — a system broadcast log with no
 * object, not a summary of work. CLAUDE.md's positioning correction is
 * explicit: the workspace is the product, and its own words for what a
 * home page should show are "humans, tasks, and CONTEXT (documents)".
 *
 * TWO SOURCES, both already real, attributed, and fetched elsewhere —
 * never a third invented here:
 *
 *   1. Document activity (project_document_revisions via
 *      GET /fleet/document-activity, useFleetDocumentActivity) — every
 *      document create/edit, real actor, real diff. Built for exactly
 *      this by the 2026-08-1x change-feed pass; this module is its second
 *      caller, not a second implementation.
 *
 *   2. Completed tasks (project_tasks_service's completed_at /
 *      completed_by_user_id / completed_by_agent_id columns, already
 *      returned on every FleetTask via useFleetWorkspaceTasks) — a task
 *      reaching `done`, real actor, real timestamp, stamped once on the
 *      actual not-done -> done transition and never fabricated.
 *
 * DELIBERATELY NOT "a task moved" in the broader sense (any status
 * change). `updated_at` moves on ANY edit — a reassign, a label change —
 * and project_tasks_service's own docstring says there is "no *actor*
 * recorded for a general update." Rendering that as a work event would
 * mean guessing who did it, which is exactly the fabrication this
 * codebase's honesty rules (tool_honesty_guard, agent_goals.attempt_count,
 * the private-memory user_id posture) exist to forbid elsewhere. A
 * completion is the one status transition this schema actually attributes
 * — so that is the one this feed shows.
 */

export type RecentDocumentEventLike = {
  id: string;
  created_at: string | null;
};

export type RecentTaskCompletionLike = {
  id: string;
  completed_at?: string | null;
};

export type RecentWorkEvent<TDoc, TTask> =
  | { kind: "document"; timestamp: string; item: TDoc }
  | { kind: "task_completed"; timestamp: string; item: TTask };

/** Merge + rank, newest first, capped at `limit`. Two lists with two
 *  different native orders (the document feed already arrives newest-first
 *  from its own ORDER BY; completed tasks do not) go through one explicit
 *  sort here rather than trusting either caller's ordering — the same
 *  "don't assume, derive" posture the rest of this codebase's pure
 *  ranking modules take. */
export function buildWorkspaceRecentWork<
  TDoc extends RecentDocumentEventLike,
  TTask extends RecentTaskCompletionLike,
>(
  documentActivity: readonly TDoc[],
  tasks: readonly TTask[],
  limit = 8,
): RecentWorkEvent<TDoc, TTask>[] {
  const docEvents: RecentWorkEvent<TDoc, TTask>[] = documentActivity
    .filter((entry): entry is TDoc & { created_at: string } => !!entry.created_at)
    .map((entry) => ({ kind: "document", timestamp: entry.created_at, item: entry }));

  const taskEvents: RecentWorkEvent<TDoc, TTask>[] = tasks
    .filter((task): task is TTask & { completed_at: string } => !!task.completed_at)
    .map((task) => ({ kind: "task_completed", timestamp: task.completed_at, item: task }));

  return [...docEvents, ...taskEvents]
    .sort((a, b) => (a.timestamp < b.timestamp ? 1 : a.timestamp > b.timestamp ? -1 : 0))
    .slice(0, Math.max(0, limit));
}

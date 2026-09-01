/**
 * The exact confirmation copy for deleting a single task — the "say what
 * actually happens" text TaskDetailView.tsx's DeleteTaskDialog renders,
 * pulled into a pure, plain-tested module for the same reason project-
 * work-summary.ts is: a destructive confirmation's wording is worth
 * reasoning about — and testing — without a component tree, and a pure
 * function is the only way the expected text and the actual text can never
 * come from two different places.
 *
 * THE THREE FACTS, each stated only when it is non-zero (a "0 labels
 * removed" clause on a task with no labels is noise, not honesty):
 *   - comments: task.metadata.comments lives ON the row itself
 *     (project_tasks_service.add_task_comment's own docstring), so they ARE
 *     destroyed with it. Verb: "deleted".
 *   - sub-tasks: parent_task_id -> this row is ON DELETE SET NULL
 *     (migrations/add_task_parent.sql) — explicitly NOT deleted, only
 *     promoted to top-level tasks. This is the one fact a founder-requested
 *     task delete must never get wrong, so it gets its own sentence rather
 *     than reusing the "deleted" verb the other two clauses use.
 *   - labels: only the ATTACHMENT (project_task_labels) is removed; the
 *     label itself is a shared workspace vocabulary entry and survives.
 *     Verb: "removed", with an explicit note that the label itself is
 *     untouched, so "removed" is never misread as "destroyed".
 *
 * Run: npx tsx lib/workspace/fleet/task-delete-summary.test.ts
 */

export type TaskDeleteCounts = {
  subtaskCount: number;
  commentCount: number;
  labelCount: number;
};

function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** The middle sentence(s) of the confirmation — everything that goes
 *  between "Delete <title>?" and "This can't be undone.", built up from
 *  only the facts that are actually true of this task. Returns "" when the
 *  task carries none of the three (a plain task with no sub-tasks, no
 *  comments, no labels), which is itself an honest, common case: "Delete
 *  <title>? This can't be undone." with nothing else to report. */
export function describeTaskDeleteEffects({
  subtaskCount,
  commentCount,
  labelCount,
}: TaskDeleteCounts): string {
  const subtasks = Math.max(0, Number(subtaskCount) || 0);
  const comments = Math.max(0, Number(commentCount) || 0);
  const labels = Math.max(0, Number(labelCount) || 0);

  const commentClause = comments > 0 ? ` ${plural(comments, "comment", "comments")} deleted with it.` : "";
  const subtaskClause =
    subtasks > 0
      ? ` ${plural(subtasks, "sub-task", "sub-tasks")} will not be deleted — ${
          subtasks === 1 ? "it becomes a top-level task" : "they become top-level tasks"
        }.`
      : "";
  const labelClause =
    labels > 0
      ? ` ${plural(labels, "label", "labels")} removed from it — the label itself is untouched.`
      : "";

  return `${commentClause}${subtaskClause}${labelClause}`;
}

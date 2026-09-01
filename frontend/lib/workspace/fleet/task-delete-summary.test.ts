/**
 * task-delete-summary — the pure text-generation half of "a task delete
 * confirmation must say what actually happens" (CLAUDE.md, and the direct
 * founder-requested job this shipped under). Pins:
 *   - each of the three facts (comments/sub-tasks/labels) appears only when
 *     genuinely non-zero, with correct singular/plural grammar;
 *   - sub-tasks are described as PROMOTED, never as deleted — the one fact
 *     this confirmation is not allowed to get wrong, since
 *     migrations/add_task_parent.sql's ON DELETE SET NULL means a deleted
 *     parent's children survive as top-level tasks;
 *   - labels are described as detached from this task, with an explicit
 *     note that the label itself survives — never phrased so it could be
 *     misread as the label vocabulary being destroyed;
 *   - a task with none of the three still gets an honest, empty-but-true
 *     answer, not a placeholder or a lie.
 *
 * Run: npx tsx lib/workspace/fleet/task-delete-summary.test.ts
 */

import { describeTaskDeleteEffects } from "./task-delete-summary";

let passed = 0;
let failed = 0;

function check(name: string, cond: boolean, detail = "") {
  if (cond) {
    passed += 1;
  } else {
    failed += 1;
    console.error(`  ✗ ${name}${detail ? ` — ${detail}` : ""}`);
  }
}

// ── Nothing to report — the common case: a plain task with no sub-tasks,
// no comments, no labels. Must be an honest empty string, not a fabricated
// "0 comments deleted" line. ──────────────────────────────────────────────
{
  const effects = describeTaskDeleteEffects({ subtaskCount: 0, commentCount: 0, labelCount: 0 });
  check("all-zero counts produce no clause at all", effects === "", effects);
}

// ── Comments: genuinely deleted with the row — "deleted" is the honest verb
// here, task.metadata.comments lives ON the task row itself. ─────────────
{
  const one = describeTaskDeleteEffects({ subtaskCount: 0, commentCount: 1, labelCount: 0 });
  check("singular comment reads '1 comment', not '1 comments'", one.includes("1 comment deleted with it."), one);
  check("singular comment clause does not say 'comments'", !one.includes("1 comments"), one);

  const many = describeTaskDeleteEffects({ subtaskCount: 0, commentCount: 3, labelCount: 0 });
  check("plural comments reads '3 comments'", many.includes("3 comments deleted with it."), many);
  check("comment clause uses the word 'deleted', the true fact", many.includes("deleted"), many);
}

// ── Sub-tasks: the one fact that MUST NOT say "deleted" — the FK is ON
// DELETE SET NULL (promoted to top-level), never a cascade. This is the
// single highest-stakes assertion in this file. ──────────────────────────
{
  const one = describeTaskDeleteEffects({ subtaskCount: 1, commentCount: 0, labelCount: 0 });
  check(
    "a single sub-task is explicitly said to survive",
    one.includes("will not be deleted") && one.includes("it becomes a top-level task"),
    one,
  );
  check("singular sub-task uses singular grammar ('it becomes'), not 'they become'", !one.includes("they become"), one);

  const many = describeTaskDeleteEffects({ subtaskCount: 4, commentCount: 0, labelCount: 0 });
  check(
    "multiple sub-tasks are explicitly said to survive, plural grammar",
    many.includes("4 sub-tasks will not be deleted") && many.includes("they become top-level tasks"),
    many,
  );
  // The negative space matters as much as the positive: nowhere in this
  // clause may the word "deleted" describe what happens TO the sub-tasks
  // themselves (only "will not be deleted" — the word appears, but negated).
  check(
    "sub-task clause never claims the sub-tasks were deleted",
    !/[^t]\bdeleted\b/.test(many.replace("will not be deleted", "")),
    many,
  );
}

// ── Labels: only the ATTACHMENT is removed — the label entity itself
// (workspace_labels) is a shared vocabulary and survives untouched. Must
// never read as "labels destroyed". ───────────────────────────────────────
{
  const one = describeTaskDeleteEffects({ subtaskCount: 0, commentCount: 0, labelCount: 1 });
  check("singular label reads '1 label removed'", one.includes("1 label removed from it"), one);
  check("label clause states the label itself is untouched", one.includes("the label itself is untouched"), one);
  check("label clause never says the label is deleted", !one.includes("label") || !one.includes("deleted"), one);

  const many = describeTaskDeleteEffects({ subtaskCount: 0, commentCount: 0, labelCount: 2 });
  check("plural labels reads '2 labels removed'", many.includes("2 labels removed from it"), many);
}

// ── All three at once — the real "task with a sub-task and a comment"
// shape the founder asked this be verified against end-to-end. Every
// clause must be present and in a stable order (comments, sub-tasks,
// labels — matching the dialog's own reading order). ──────────────────────
{
  const all = describeTaskDeleteEffects({ subtaskCount: 1, commentCount: 2, labelCount: 3 });
  check("all three facts appear together", all.includes("2 comments") && all.includes("1 sub-task") && all.includes("3 labels"), all);
  const commentsAt = all.indexOf("comments");
  const subtaskAt = all.indexOf("sub-task");
  const labelsAt = all.indexOf("labels");
  check("clause order is comments, then sub-tasks, then labels", commentsAt < subtaskAt && subtaskAt < labelsAt, all);
}

// ── Negative/garbage input never produces a negative or NaN count — same
// Math.max(0, Number(x) || 0) discipline formatProjectWorkSummary uses. ──
{
  const weird = describeTaskDeleteEffects({
    subtaskCount: -3,
    commentCount: Number.NaN,
    labelCount: -0,
  });
  check("negative/NaN input never renders a negative or NaN count", !/-\d|NaN/.test(weird), weird);
}

// CANARY: the module under test actually exports something callable —
// guards against a refactor silently turning this into a no-op import.
check("canary: describeTaskDeleteEffects is a function", typeof describeTaskDeleteEffects === "function");

console.log(`task-delete-summary: ${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);

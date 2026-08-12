/**
 * task-status unit tests — taskDisplayId, the real per-project identifier
 * ("GEN-12", Linear's shape) that migrations/add_task_sequence_numbers.sql
 * built the backend schema for and project_tasks_service.create_task /
 * projects_repository.create_project now allocate. taskShortId (the honest
 * hex-slice placeholder taskDisplayId used to be the only option) stays as
 * its fallback for a task/project predating that.
 *
 * Run: npx tsx lib/workspace/fleet/task-status.test.ts
 */

import { taskDisplayId, taskShortId } from './task-status';

let passed = 0;
let failed = 0;

function assertEqual<T>(actual: T, expected: T, label: string): void {
  if (actual === expected) {
    passed++;
  } else {
    failed++;
    console.error(`FAIL: ${label} — expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  }
}

// --- Real number + project_task_key present: the Linear shape ---

assertEqual(
  taskDisplayId({ id: 'task_69d6567ebfd3485a', number: 1, project_task_key: 'GEN' }),
  'GEN-1',
  'number + project_task_key present renders "GEN-1", not the hex slice',
);

assertEqual(
  taskDisplayId({ id: 'task_69d6567ebfd3485a', number: 42, project_task_key: 'ENG2' }),
  'ENG2-42',
  'a deduped multi-project key ("ENG2") composes the same way as a bare one',
);

// --- Missing either half falls back to the honest hex-slice handle ---

assertEqual(
  taskDisplayId({ id: 'task_69d6567ebfd3485a', number: null, project_task_key: 'GEN' }),
  taskShortId('task_69d6567ebfd3485a'),
  'number absent (task predates allocation) falls back to taskShortId',
);

assertEqual(
  taskDisplayId({ id: 'task_69d6567ebfd3485a', number: 1, project_task_key: null }),
  taskShortId('task_69d6567ebfd3485a'),
  'project_task_key absent (project predates the migration) falls back to taskShortId',
);

assertEqual(
  taskDisplayId({ id: 'task_69d6567ebfd3485a', number: undefined, project_task_key: undefined }),
  taskShortId('task_69d6567ebfd3485a'),
  'both absent (a server predating the migration entirely) falls back to taskShortId',
);

// --- number: 0 is falsy in JS but a legitimate allocation must never happen
//     in practice (task_seq starts at 0 and is incremented before use, so
//     the first real task is always 1) -- guard against a future `!task.number`
//     shortcut reintroducing an off-by-one that would silently drop "GEN-0". ---

assertEqual(
  taskDisplayId({ id: 'task_69d6567ebfd3485a', number: 0, project_task_key: 'GEN' }),
  'GEN-0',
  'number: 0 (however it arose) still renders rather than silently falling back',
);

// --- taskShortId itself: unaffected by any of the above ---

assertEqual(taskShortId('task_69d6567ebfd3485a'), '69D656', 'taskShortId slices and uppercases the first 6 hex chars');
assertEqual(taskShortId(''), '------', 'taskShortId never returns an empty string');

// --- Summary ---

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}

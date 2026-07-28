-- Sub-tasks -> a parent link on project_tasks, NOT a second table.
--
-- migrations/add_project_tasks.sql shipped v1 "trimmed ... (no labels,
-- cycles, or priorities)". add_task_priority.sql added the priority third of
-- that back; this file and its sibling add_task_labels.sql close the last
-- two gaps between this board and Linear's. Linear's card shows sub-issue
-- progress ("0/2") and label chips; ours could not, because there was no
-- column for either.
--
-- WHY A SELF-REFERENCE AND NOT A `subtasks` TABLE. A sub-task is not a
-- lesser kind of object -- it is a full task that happens to hang off
-- another one. Linear models it exactly this way (a sub-issue IS an issue),
-- and so does every product surveyed in docs/design/tasks-to-agents-
-- research.md Section 2. A separate table would immediately need its own
-- status vocabulary, its own priority, its own assignee, its own comment
-- log, its own agent tools -- a second copy of everything project_tasks
-- already has, drifting from it forever. One nullable column buys the whole
-- feature and every existing tool keeps working on sub-tasks unchanged.
--
-- SINGLE LEVEL, ENFORCED. A sub-task may not itself have sub-tasks. Linear
-- permits arbitrary nesting; the founder has not asked for it, and depth
-- makes every rollup recursive (the "0/2" badge becomes a tree walk, "is
-- this task done" stops being a local question, and a cycle in the graph
-- becomes possible). project_tasks_service.set_task_parent / create_task
-- reject parenting a task to something that itself already has a parent,
-- with a message that says so plainly. That rule needs a LOOKUP (does the
-- proposed parent have a parent?), which a CHECK constraint cannot express,
-- so it is enforced in the service layer -- the same place the status and
-- priority vocabularies are enforced beyond what their CHECKs can say. What
-- the database DOES enforce here is the one part it can: a task can never
-- be its own parent (project_tasks_parent_not_self_check). No trigger --
-- this table has none today, and a trigger is a large, invisible new moving
-- part to buy a backstop for a rule with exactly one writer.
--
-- ON DELETE SET NULL -- THE CONSERVATIVE CHOICE, DELIBERATELY. Deleting a
-- parent PROMOTES its sub-tasks to top-level tasks; it never deletes them.
-- CASCADE was the tempting alternative and is rejected outright: a
-- sub-task is real work a human or an agent wrote down, often with its own
-- assignee, its own comment history and its own persisted plan, and a
-- single click on the parent must not silently destroy an unbounded amount
-- of that. An orphaned sub-task showing up back on the top-level board is
-- visible, recoverable and obvious; a cascade-deleted one is neither. (Note
-- for completeness: `project_tasks.project_id` already cascades from
-- `projects`, so deleting a whole PROJECT still removes its tasks, parents
-- and children alike -- which is correct, because parent and child are
-- always in the same project and the project is the thing being destroyed.)
--
-- PARENT AND CHILD ALWAYS SHARE A PROJECT. Enforced in the service layer at
-- every write, for the same reason the single-level rule is: the project is
-- this product's collaboration boundary (an agent's native project_task__*
-- tools are scoped to its own project and nothing else), and a parent
-- reaching across that boundary would make a task's rollup count work an
-- agent is not allowed to see. There is no "move a task to another project"
-- API today -- `update_task` does not touch project_id -- so the invariant
-- cannot be broken from behind. If such a path is ever added, it must move
-- the whole family or detach the children first; the same-project check is
-- the seam that will fail loudly if it does not.
--
-- INDEX. Partial, on parent_task_id alone -- the only query shape is "the
-- children of THIS parent" and the parent id is already unique per row, so
-- there is nothing for a leading (tenant_id, workspace_id) to narrow. The
-- `WHERE parent_task_id IS NOT NULL` predicate keeps the index to the small
-- minority of rows that are actually sub-tasks rather than one entry per
-- task on the board. The rollup counts ("2 sub-tasks, 1 done") ride this
-- index via a single LATERAL join in project_tasks_service.py, computed in
-- the same query as the task itself -- never a per-task follow-up read.
--
-- PRODUCTION ROWS ARE SAFE. Purely additive: one nullable column, one
-- CHECK, one index. Every existing task lands on parent_task_id = NULL,
-- i.e. "a top-level task", which is exactly what all of them are today. No
-- row is rewritten and nothing is dropped. Adding a NULLable column with no
-- default is a catalog-only change on Postgres 11+, so this is fast on a
-- large table.
--
-- Idempotent, in the CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT
-- EXISTS style of every sibling migration here: the column add is
-- IF NOT EXISTS, the CHECK is dropped-then-re-added under a stable explicit
-- name (add_task_priority.sql's convention) rather than blindly added, and
-- the index is IF NOT EXISTS. Wrapped in a single DO block so that (a) a
-- database that has not yet created project_tasks is a no-op notice instead
-- of an error, and (b) a failure part-way leaves no half-constrained column.
--
-- Mirrored into server_modules/control_plane_repository.py's
-- CONTROL_PLANE_SCHEMA_SQL (new-database DDL) and its ensure_control_plane_
-- schema() migration section (existing-database self-heal), following the
-- same "standalone migration file + mirror" convention as
-- migrations/add_task_status_vocabulary.sql and
-- migrations/add_task_priority.sql.

BEGIN;

DO $$
BEGIN
    IF to_regclass('public.project_tasks') IS NULL THEN
        RAISE NOTICE 'project_tasks does not exist yet; nothing to migrate.';
        RETURN;
    END IF;

    -- 1. The column itself. NULLable with no default: every pre-existing row
    --    becomes a top-level task, which is what it already was.
    ALTER TABLE project_tasks
        ADD COLUMN IF NOT EXISTS parent_task_id TEXT NULL;

    -- 2. The self-referential FK, added separately from the column so the
    --    ADD COLUMN above stays a plain catalog change and so re-running this
    --    file does not attempt to add a duplicate constraint. ON DELETE SET
    --    NULL is the whole orphaning decision (see the header): a deleted
    --    parent promotes its children, it never destroys them.
    ALTER TABLE project_tasks DROP CONSTRAINT IF EXISTS project_tasks_parent_task_id_fkey;
    ALTER TABLE project_tasks
        ADD CONSTRAINT project_tasks_parent_task_id_fkey
        FOREIGN KEY (parent_task_id) REFERENCES project_tasks(id) ON DELETE SET NULL;

    -- 3. The one depth rule a CHECK can actually express. The real
    --    single-level constraint needs a lookup and lives in
    --    project_tasks_service.py; this catches the degenerate self-parent
    --    case at the storage layer, where it costs nothing.
    ALTER TABLE project_tasks DROP CONSTRAINT IF EXISTS project_tasks_parent_not_self_check;
    ALTER TABLE project_tasks
        ADD CONSTRAINT project_tasks_parent_not_self_check
        CHECK (parent_task_id IS NULL OR parent_task_id <> id);

    -- 4. The children-of-this-parent index the "0/2" rollup rides. Inside the
    --    DO block rather than a bare statement so the "no project_tasks yet =
    --    a notice, not an error" guarantee above covers it too.
    CREATE INDEX IF NOT EXISTS idx_project_tasks_parent
        ON project_tasks(parent_task_id)
        WHERE parent_task_id IS NOT NULL;
END
$$;

COMMIT;

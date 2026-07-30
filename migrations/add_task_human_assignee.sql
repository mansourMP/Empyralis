-- Human task assignability (MAN-64/MAN-70) -- a task must be assignable to a
-- HUMAN as well as an agent. Today's schema (add_project_tasks.sql) only has
-- `assignee_agent_id`; there is no column a human user id can go in at all.
--
-- TWO SEPARATE NULLABLE COLUMNS, NOT A POLYMORPHIC (assignee_type, assignee_id)
-- PAIR. Rejected the polymorphic shape for three concrete reasons:
--   1. `assignee_agent_id`'s existing FK to workspace_agent_installs(id) ON
--      DELETE SET NULL is real, load-bearing referential integrity --
--      deleting an agent already safely clears its assignments. A
--      polymorphic id column cannot be FK'd to two different tables at
--      once, so that guarantee would have to move into application code
--      (a trigger, or hope) instead of the database enforcing it for free.
--   2. Every existing row keeps assignee_agent_id exactly as it is -- no
--      data migration, no backfill, no rewrite of a column that already has
--      real production rows in it.
--   3. The agent-wakeup call site (project_tasks_service.assign_task ->
--      bounded_scheduler_service.schedule_task_assigned_wakeup) reads
--      assignee_agent_id directly today. A polymorphic column would force
--      every one of those call sites to branch on assignee_type before
--      touching the id; two plain columns mean the agent path is
--      completely untouched by this change (see assign_task's own
--      docstring in project_tasks_service.py for why that matters: the
--      human path must add zero behavioral drift to the agent path).
--
-- MUTUAL EXCLUSIVITY, ENFORCED BY CHECK. A task has at most one assignee --
-- either a human or an agent, never both at once. `project_tasks_single_
-- assignee_check` is the storage-level guarantee; project_tasks_service.py's
-- assign_task / assign_task_to_user each explicitly NULL out the other
-- column on write (switching a task from an agent to a human, or back,
-- clears the previous assignee rather than leaving a stale id behind), so
-- the CHECK should never actually fire in normal operation -- it exists as
-- the backstop against a future write path getting that wrong.
--
-- WHY assignee_user_id IS NOT "the same as created_by". `created_by`
-- records who FILED the task (the accountable owner of record, per Linear's
-- own "the human teammate remains the primary assignee and owner" model
-- cited in docs/design/tasks-to-agents-research.md Section 1.1/4.2) and
-- never changes after creation. `assignee_user_id` records who is currently
-- WORKING it, exactly like `assignee_agent_id` does for an agent -- the two
-- can differ (Alice files a task and hands it to Bob) and both already
-- coexist for the agent case (`created_by` a human, `assignee_agent_id` an
-- agent).
--
-- CRITICAL BEHAVIORAL RULE THIS SCHEMA CHANGE ENABLES BUT DOES NOT ITSELF
-- ENFORCE: assigning a task to a human must NEVER schedule an agent wakeup.
-- People are not woken by schedulers. That rule lives in
-- project_tasks_service.assign_task_to_user (it simply never calls
-- bounded_scheduler_service, at all, in any branch) -- not in this DDL,
-- which cannot express "do not call a scheduler."
--
-- PRODUCTION ROWS ARE SAFE. Purely additive: one nullable column, one FK,
-- one CHECK, one partial index. No existing row is rewritten -- every task
-- that exists today has assignee_user_id = NULL, which is the honest
-- current fact (nothing today can set it). On Postgres 11+ a nullable
-- column with no default is a catalog-only change, so this is fast even on
-- a large table.
--
-- Idempotent, in the CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS
-- style of every sibling migration here (add_task_priority.sql, add_task_
-- parent.sql): the column add is IF NOT EXISTS, the FK and CHECK are
-- dropped-then-re-added under stable explicit names, and the index is IF
-- NOT EXISTS. Wrapped in a single DO block so a database that has not yet
-- created project_tasks is a no-op notice instead of an error, and a
-- failure part-way leaves no half-constrained column.
--
-- Mirrored into server_modules/control_plane_repository.py's
-- CONTROL_PLANE_SCHEMA_SQL (new-database DDL) and its ensure_control_plane_
-- schema() migration section (existing-database self-heal), following the
-- same "standalone migration file + mirror" convention as every sibling
-- project_tasks migration.

BEGIN;

DO $$
BEGIN
    IF to_regclass('public.project_tasks') IS NULL THEN
        RAISE NOTICE 'project_tasks does not exist yet; nothing to migrate.';
        RETURN;
    END IF;

    -- 1. The column itself. NULLable with no default: every pre-existing row
    --    becomes "no human assignee", which is exactly what it already was
    --    (nothing before this migration could ever have set it).
    ALTER TABLE project_tasks
        ADD COLUMN IF NOT EXISTS assignee_user_id TEXT NULL;

    -- 2. The FK, added separately from the column so the ADD COLUMN above
    --    stays a plain catalog change and so re-running this file does not
    --    attempt to add a duplicate constraint. ON DELETE SET NULL mirrors
    --    assignee_agent_id's own on-delete behavior exactly: a deleted user
    --    orphans their assignments back to "unassigned" rather than taking
    --    the task down with them.
    ALTER TABLE project_tasks DROP CONSTRAINT IF EXISTS project_tasks_assignee_user_id_fkey;
    ALTER TABLE project_tasks
        ADD CONSTRAINT project_tasks_assignee_user_id_fkey
        FOREIGN KEY (assignee_user_id) REFERENCES users(id) ON DELETE SET NULL;

    -- 3. Mutual exclusivity -- see the header. "At most one of the two is
    --    set" is exactly what OR-of-IS-NULL expresses; it also permits the
    --    legitimate "neither set" (unassigned/backlog) state.
    ALTER TABLE project_tasks DROP CONSTRAINT IF EXISTS project_tasks_single_assignee_check;
    ALTER TABLE project_tasks
        ADD CONSTRAINT project_tasks_single_assignee_check
        CHECK (assignee_agent_id IS NULL OR assignee_user_id IS NULL);

    -- 4. The lookup index, mirroring idx_project_tasks_assignee's own shape
    --    exactly (same (tenant_id, workspace_id, <assignee column>) leading
    --    columns, same partial WHERE so the index only carries the minority
    --    of rows that actually have this assignee kind set).
    CREATE INDEX IF NOT EXISTS idx_project_tasks_assignee_user
        ON project_tasks(tenant_id, workspace_id, assignee_user_id)
        WHERE assignee_user_id IS NOT NULL;
END
$$;

COMMIT;

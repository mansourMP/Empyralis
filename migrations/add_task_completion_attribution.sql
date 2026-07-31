-- Review attribution (MAN-145 follow-up) -- project_tasks has no record of
-- WHO completed a task. ProjectOverview.tsx (lines 77-90) admits this
-- in-product: an agent closing its own work, another agent closing a
-- teammate's, and a human dragging the card to Done are indistinguishable
-- after the fact. This migration adds the columns that record it.
--
-- PURE ATTRIBUTION STAMP, NEVER A GATE. CLAUDE.md's standing law is "no
-- approval system" -- an agent may still self-close a task; these columns
-- only ever record that it did. There is no code anywhere that reads
-- completed_by_* to allow or block a status write. Do not add any
-- approve/deny behavior on top of this migration.
--
-- TWO SEPARATE NULLABLE COLUMNS, NOT A POLYMORPHIC (completed_by_type,
-- completed_by_id) PAIR -- the identical shape decision migrations/add_
-- task_human_assignee.sql already made for assignee_user_id/assignee_
-- agent_id, for the identical reasons: a polymorphic id column cannot be
-- FK'd to two different tables (users, workspace_agent_installs) at once,
-- and every existing row's honest state (nobody has completed anything
-- through this new machinery yet) needs no data migration either way.
--
-- MUTUAL EXCLUSIVITY, ENFORCED BY CHECK -- same shape as project_tasks_
-- single_assignee_check: at most one of completed_by_user_id/completed_by_
-- agent_id is set at a time (both NULL is the normal "we don't know, or
-- this task was never completed" state). project_tasks_service.update_task
-- is the ONE code path that writes these columns and it always writes at
-- most one of the two per call, so the CHECK should never actually fire in
-- normal operation -- it exists as the backstop against a future write
-- path getting that wrong, exactly like its sibling.
--
-- WHY THIS NEEDS TRANSITION DETECTION, NOT A BLIND STAMP. update_task is a
-- generic field patcher called on every board drag and every title edit
-- alike; a naive "status = 'done' => stamp the caller" would also stamp a
-- caller who merely re-saves a task that was ALREADY done (no real
-- transition happened) and would never clear a stale stamp when a task
-- later leaves 'done'. project_tasks_service.update_task's UPDATE statement
-- compares the incoming status against the row's own pre-update status
-- (visible to every SET expression in the same statement) to tell an actual
-- todo/in_review -> done TRANSITION apart from a no-op re-patch, and clears
-- all three columns the moment status moves away from 'done' again --
-- "reopening" a task must not leave a stale "completed by X" behind for a
-- task that is, right now, not complete.
--
-- NO BACKFILL. Every existing 'done' row gets NULL/NULL/NULL -- the honest
-- current fact, since nothing before this migration could ever have
-- recorded who closed it or when.
--
-- PRODUCTION ROWS ARE SAFE. Purely additive: three nullable columns, two
-- FKs, one CHECK. No existing row is rewritten. On Postgres 11+ a nullable
-- column with no default is a catalog-only change, so this is fast even on
-- a large table.
--
-- Idempotent, in the same CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT
-- EXISTS style as every sibling project_tasks migration: the column adds
-- are IF NOT EXISTS, the FKs and CHECK are dropped-then-re-added under
-- stable explicit names, and the whole thing is wrapped in one DO block so
-- a database that has not yet created project_tasks is a no-op notice
-- instead of an error, and a failure part-way leaves no half-constrained
-- column.
--
-- Mirrored into server_modules/control_plane_repository.py's CONTROL_
-- PLANE_SCHEMA_SQL (new-database DDL) and its ensure_control_plane_
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

    -- 1. The three columns. All NULLable with no default: every
    --    pre-existing row becomes "no recorded completion", which is
    --    exactly what it already was (nothing before this migration could
    --    ever have set it, even for a row that is currently 'done').
    ALTER TABLE project_tasks
        ADD COLUMN IF NOT EXISTS completed_by_user_id TEXT NULL;
    ALTER TABLE project_tasks
        ADD COLUMN IF NOT EXISTS completed_by_agent_id TEXT NULL;
    ALTER TABLE project_tasks
        ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ NULL;

    -- 2. The FKs, added separately from the columns so the ADD COLUMNs
    --    above stay plain catalog changes and re-running this file never
    --    attempts a duplicate constraint. ON DELETE SET NULL mirrors
    --    assignee_user_id / assignee_agent_id's own on-delete behavior
    --    exactly: a deleted user or agent orphans the historical
    --    attribution record back to "unknown" rather than taking the task
    --    (or its completion history) down with them.
    ALTER TABLE project_tasks DROP CONSTRAINT IF EXISTS project_tasks_completed_by_user_id_fkey;
    ALTER TABLE project_tasks
        ADD CONSTRAINT project_tasks_completed_by_user_id_fkey
        FOREIGN KEY (completed_by_user_id) REFERENCES users(id) ON DELETE SET NULL;

    ALTER TABLE project_tasks DROP CONSTRAINT IF EXISTS project_tasks_completed_by_agent_id_fkey;
    ALTER TABLE project_tasks
        ADD CONSTRAINT project_tasks_completed_by_agent_id_fkey
        FOREIGN KEY (completed_by_agent_id) REFERENCES workspace_agent_installs(id) ON DELETE SET NULL;

    -- 3. Mutual exclusivity -- see the header. "At most one of the two is
    --    set" is exactly what OR-of-IS-NULL expresses; it also permits the
    --    legitimate "neither set" (never completed, or completed by
    --    something this migration predates) state.
    ALTER TABLE project_tasks DROP CONSTRAINT IF EXISTS project_tasks_completed_by_single_actor_check;
    ALTER TABLE project_tasks
        ADD CONSTRAINT project_tasks_completed_by_single_actor_check
        CHECK (completed_by_agent_id IS NULL OR completed_by_user_id IS NULL);
END
$$;

COMMIT;

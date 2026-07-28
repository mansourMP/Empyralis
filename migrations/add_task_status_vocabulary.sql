-- Task status vocabulary -> Linear-style kanban columns.
--
-- migrations/add_project_tasks.sql shipped five statuses:
--   open | in_progress | blocked | awaiting_input | done
-- This forward migration replaces that with seven:
--   backlog | todo | in_progress | awaiting_input | blocked | in_review | done
--
-- What changes and why:
--
--   * ADD `backlog`. Until now "backlog" was only IMPLIED by "unassigned"
--     (the fleet board inferred it from a NULL assignee_agent_id). That
--     conflated two genuinely different facts -- "nobody owns this yet" and
--     "this is not queued for work yet" -- and made it impossible to have an
--     assigned-but-not-yet-queued task. `backlog` is now a real, first-class
--     status the board can render as its own column.
--
--   * ADD `in_review`. This is the point of the whole change. Today an agent
--     that finishes work writes `done` directly -- there is no seam where a
--     human can look at what the agent produced before the task leaves the
--     board. `in_review` is that seam: agent-completed work parks there and
--     waits for a human to approve it into `done`.
--
--   * RENAME `open` -> `todo`, to match Linear's own vocabulary. Backward
--     compatible in both directions: existing rows are migrated below, and
--     project_tasks_service.py accepts `open` as an alias for `todo` on
--     input rather than erroring, so any caller (an older client, a cached
--     agent tool schema, a saved API script) that still says `open` keeps
--     working and simply lands on `todo`.
--
--   * KEEP `in_progress`, `awaiting_input`, `blocked`, `done` unchanged.
--
-- Do existing rows become `backlog`? NO -- deliberately. It is tempting to
-- reclassify unassigned `open` rows as `backlog`, since that is roughly what
-- the board was already showing. But `backlog` is a human triage decision
-- ("not queued for work"), and inferring it from an incidental NULL assignee
-- would silently demote real, actively-tracked work that somebody put on the
-- board and may be waiting on. Every `open` row becomes `todo`, full stop.
-- Anything that genuinely belongs in the backlog can be dragged there by a
-- human, which is exactly the decision this migration is trying not to make
-- on their behalf.
--
-- Safe to run against a database holding real rows, and idempotent (matching
-- the CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS style of every
-- sibling migration here): the constraint drop is name-agnostic and
-- IF EXISTS-shaped, the row rewrite is a no-op once no `open` rows remain,
-- and the new constraint is dropped-then-re-added rather than blindly added.
-- The rewrite deliberately does NOT touch `updated_at` -- a vocabulary
-- rename is not activity on the task, and bumping it would reshuffle every
-- board sorted by recency and look like phantom edits in the UI.
--
-- Mirrored into server_modules/control_plane_repository.py's
-- CONTROL_PLANE_SCHEMA_SQL (new-database DDL) and its ensure_control_plane_
-- schema() migration section (existing-database self-heal), following the
-- same "standalone migration file + mirror" convention as
-- migrations/add_project_memberships.sql.

BEGIN;

-- 1. Drop the old CHECK. Resolved by lookup rather than by hardcoded name:
--    add_project_tasks.sql declared it inline on the column, so Postgres
--    auto-named it (`project_tasks_status_check` in practice, but the
--    suffix shifts if a second check constraint ever landed first).
DO $$
DECLARE
    existing_constraint RECORD;
BEGIN
    IF to_regclass('public.project_tasks') IS NULL THEN
        RAISE NOTICE 'project_tasks does not exist yet; nothing to migrate.';
        RETURN;
    END IF;
    FOR existing_constraint IN
        SELECT conname
        FROM pg_constraint
        WHERE conrelid = 'public.project_tasks'::regclass
          AND contype = 'c'
          AND pg_get_constraintdef(oid) ILIKE '%status%'
    LOOP
        EXECUTE format(
            'ALTER TABLE project_tasks DROP CONSTRAINT %I', existing_constraint.conname
        );
    END LOOP;
END
$$;

-- 2. Migrate the one renamed value. Runs with no CHECK in force, so 'todo'
--    is writable here even though the old constraint forbade it.
UPDATE project_tasks SET status = 'todo' WHERE status = 'open';

-- 3. New rows default to the new name for the same state.
ALTER TABLE project_tasks ALTER COLUMN status SET DEFAULT 'todo';

-- 4. Re-assert the constraint over the seven-value vocabulary, under a
--    stable explicit name so future migrations can target it directly.
ALTER TABLE project_tasks
    ADD CONSTRAINT project_tasks_status_check
    CHECK (status IN (
        'backlog', 'todo', 'in_progress', 'awaiting_input', 'blocked', 'in_review', 'done'
    ));

COMMIT;

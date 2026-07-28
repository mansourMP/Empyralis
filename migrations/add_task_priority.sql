-- Task priority -> Linear's five-level scale, on every card.
--
-- migrations/add_project_tasks.sql deliberately shipped v1 "trimmed ... (no
-- labels, cycles, or priorities)". This forward migration adds the priority
-- half of that back, matching Linear's own vocabulary and, crucially, its
-- own encoding:
--
--   0 = none  (default)
--   1 = urgent
--   2 = high
--   3 = medium
--   4 = low
--
-- Yes, 1 is the MOST urgent and 4 the least. That inversion looks wrong at
-- first glance, and it is not a mistake: it is exactly what Linear stores
-- and exactly what the Linear MCP API accepts. Matching it means an agent
-- that already speaks Linear -- and the founder's own Linear-shaped mental
-- model of the board -- needs no translation layer in between, and no
-- future integration has to remember which end of the scale this table
-- happens to use. `none` sits at 0 rather than at 5 for the same reason:
-- it is Linear's encoding, and it makes "was a priority ever set on this
-- card?" a plain `priority <> 0` rather than a magic-number comparison.
--
-- Sorting note (the one place the encoding costs something): "urgent first,
-- none last" is NOT a plain ORDER BY priority, because 0 means unset and
-- must sort to the BOTTOM, not the top. project_tasks_service.py expresses
-- that as `NULLIF(priority, 0) ASC NULLS LAST` -- 1..4 ascend as usual and
-- 0 becomes NULL and falls to the end. Deliberately no supporting index:
-- every task query here is already filtered to one (tenant, workspace,
-- project) by idx_project_tasks_project, which leaves a board-sized handful
-- of rows to sort in memory; a functional index on the NULLIF expression
-- would be pure cost with nothing to buy.
--
-- SMALLINT, not TEXT: unlike `status` (whose values are a vocabulary a human
-- reads), priority's whole job is to ORDER things, and an integer orders
-- for free. The service layer still accepts the human names ('urgent',
-- 'high', ...) on input and hands a derived `priority_label` back out on
-- read, so nothing above the database has to think in integers.
--
-- PRODUCTION ROWS ARE SAFE. This is purely additive -- no existing column is
-- touched, no row is rewritten, nothing is dropped. Every task that exists
-- today lands on 0 ('none'), which is the honest answer: nobody has
-- triaged them yet. On Postgres 11+ a NOT NULL column with a constant
-- DEFAULT is added as a catalog-only change (no table rewrite), so this is
-- fast even on a large table.
--
-- Idempotent, in the CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS
-- style of every sibling migration here: the column add is IF NOT EXISTS and
-- the CHECK is dropped-then-re-added under a stable explicit name rather
-- than blindly added. Wrapped in a single DO block so that (a) a database
-- that has not yet created project_tasks is a no-op notice instead of an
-- error, and (b) a failure part-way leaves no half-constrained column.
--
-- Mirrored into server_modules/control_plane_repository.py's
-- CONTROL_PLANE_SCHEMA_SQL (new-database DDL) and its ensure_control_plane_
-- schema() migration section (existing-database self-heal), following the
-- same "standalone migration file + mirror" convention as
-- migrations/add_task_status_vocabulary.sql and
-- migrations/add_project_memberships.sql.

BEGIN;

DO $$
BEGIN
    IF to_regclass('public.project_tasks') IS NULL THEN
        RAISE NOTICE 'project_tasks does not exist yet; nothing to migrate.';
        RETURN;
    END IF;

    -- 1. The column itself. NOT NULL DEFAULT 0 backfills every pre-existing
    --    row to 'none' without a rewrite.
    ALTER TABLE project_tasks
        ADD COLUMN IF NOT EXISTS priority SMALLINT NOT NULL DEFAULT 0;

    -- 2. Range CHECK, under a stable explicit name so a future migration can
    --    target it directly (add_project_tasks.sql's inline, auto-named
    --    status CHECK is the counter-example that made
    --    add_task_status_vocabulary.sql resolve its target by lookup).
    ALTER TABLE project_tasks DROP CONSTRAINT IF EXISTS project_tasks_priority_check;
    ALTER TABLE project_tasks
        ADD CONSTRAINT project_tasks_priority_check
        CHECK (priority BETWEEN 0 AND 4);
END
$$;

COMMIT;

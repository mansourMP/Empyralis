-- Per-project, human-readable task identifiers -- Linear's `MAN-149` shape,
-- replacing the 6-char hex slice of the task's own uuid that
-- frontend/lib/workspace/fleet/task-status.tsx's taskShortId rendered as a
-- confessed placeholder ("until the backend has a real per-project sequence
-- number"). This migration builds that sequence number.
--
-- THREE COLUMNS:
--   projects.task_key    TEXT  -- a short uppercase key derived from the
--                                 project's own `slug` (e.g. slug `general`
--                                 -> key `GEN`), unique per (tenant_id,
--                                 workspace_id). Two projects whose slugs
--                                 reduce to the same 3 letters ("General
--                                 Ops" and "General" both give GEN) do not
--                                 collide -- the later one (by created_at)
--                                 gets GEN2, then GEN3, ... deterministically.
--                                 NULLable in the schema on purpose (every
--                                 row is backfilled below, and every future
--                                 INSERT assigns one -- see
--                                 projects_repository.create_project) rather
--                                 than NOT NULL, matching this migration's
--                                 own brief.
--   projects.task_seq    INT   -- the allocation counter, NOT NULL DEFAULT 0.
--                                 Only ever increases -- see the allocation
--                                 note below for why it must never be
--                                 recomputed from MAX(number) after this
--                                 migration's one-time seed.
--   project_tasks.number INT   -- the allocated number for this task, e.g.
--                                 12 for the 12th task ever created in its
--                                 project. Combined with the OWNING
--                                 project's task_key, this is `GEN-12`.
--                                 NULLable: a task somehow missing one
--                                 renders no identifier at all (see
--                                 task-status.taskDisplayId), never a
--                                 fallback.
--
-- ALLOCATION IS RACE-FREE BY CONSTRUCTION, NOT BY THIS MIGRATION.
-- project_tasks_service.create_task allocates a number with:
--
--   UPDATE projects SET task_seq = task_seq + 1
--   WHERE id = $1 AND tenant_id = $2 AND workspace_id = $3
--   RETURNING task_seq
--
-- run on the SAME connection, inside the SAME transaction, as the
-- project_tasks INSERT that consumes the returned value -- never
-- SELECT MAX(number)+1, which two concurrent task creations (agents create
-- tasks concurrently in this product) would both read before either writes,
-- handing out the same number twice. If the INSERT fails and the
-- transaction rolls back, the UPDATE rolls back with it -- task_seq only
-- ever advances alongside a task that actually landed, so it is safe to
-- treat as "how many tasks have ever existed in this project" forever
-- after this migration seeds it once. NEVER re-derive task_seq from
-- MAX(project_tasks.number) after today: a deleted task would make that
-- computation UNDERCOUNT and the next allocation would reissue a number
-- that already exists (or once existed) on another task.
--
-- BACKFILL, in three steps below, all deterministic and safe to re-run --
-- every step is guarded to touch only rows that still need it, so
-- re-running this file, or ensure_control_plane_schema's mirrored self-heal
-- block on every process boot, is a no-op once applied:
--   1. task_key for every existing project, from its slug, oldest-created
--      first per (tenant_id, workspace_id) so an earlier "General" claims
--      the bare GEN and a later, differently-named project that also
--      reduces to GEN gets GEN2.
--   2. project_tasks.number, per project, ordered by created_at (stable --
--      re-running never reshuffles an already-numbered task, and ties are
--      broken by id so the order cannot change between runs on the same
--      data).
--   3. projects.task_seq, seeded to each project's own max backfilled
--      number (0 for a project with no tasks) -- exactly once; see the
--      allocation note above for why this must not happen again after
--      today.
--
-- NO NEW TABLES. Both target tables (projects, project_tasks) already carry
-- no RLS policy (see migrations/enable_rls.sql, which this migration does
-- NOT touch) -- every query scopes explicitly by (tenant_id, workspace_id)
-- in projects_repository.py / project_tasks_service.py, and that remains
-- true for every column added here.
--
-- PRODUCTION ROWS ARE SAFE where this ever reaches production: purely
-- additive (three new columns, one new index), and the backfill loops only
-- ever fill in a NULL, never touch an already-set value.
--
-- Mirrored into server_modules/control_plane_repository.py's
-- CONTROL_PLANE_SCHEMA_SQL (new-database DDL) and its
-- ensure_control_plane_schema() migration section (existing-database
-- self-heal), following the same "standalone migration file + mirror"
-- convention as migrations/add_task_priority.sql and
-- migrations/add_task_parent.sql.

BEGIN;

DO $$
DECLARE
    proj RECORD;
    cleaned TEXT;
    base_key TEXT;
    candidate TEXT;
    suffix INT;
BEGIN
    IF to_regclass('public.projects') IS NULL THEN
        RAISE NOTICE 'projects does not exist yet; nothing to migrate.';
        RETURN;
    END IF;

    -- 1. projects.task_key / task_seq columns. NOT NULL DEFAULT 0 on
    --    task_seq is a catalog-only change on Postgres 11+, so this is fast
    --    even on a large table.
    ALTER TABLE projects ADD COLUMN IF NOT EXISTS task_key TEXT;
    ALTER TABLE projects ADD COLUMN IF NOT EXISTS task_seq INT NOT NULL DEFAULT 0;

    -- 2. Backfill task_key, oldest project first per (tenant_id,
    --    workspace_id) so an earlier "General" claims the bare GEN and a
    --    later project that reduces to the same 3 letters (e.g. "General
    --    Ops") gets GEN2, GEN3, ... Guarded to task_key IS NULL, so a
    --    second run of this file touches nothing.
    FOR proj IN
        SELECT id, tenant_id, workspace_id, slug
        FROM projects
        WHERE task_key IS NULL
        ORDER BY tenant_id, workspace_id, created_at ASC, id ASC
    LOOP
        cleaned := upper(regexp_replace(coalesce(proj.slug, ''), '[^a-zA-Z0-9]', '', 'g'));
        base_key := substr(cleaned, 1, 3);
        IF base_key = '' THEN
            base_key := 'TSK';
        END IF;

        candidate := base_key;
        suffix := 2;
        WHILE EXISTS (
            SELECT 1 FROM projects
            WHERE tenant_id = proj.tenant_id
              AND workspace_id = proj.workspace_id
              AND task_key = candidate
        ) LOOP
            candidate := base_key || suffix::text;
            suffix := suffix + 1;
        END LOOP;

        UPDATE projects SET task_key = candidate WHERE id = proj.id;
    END LOOP;

    -- 3. Uniqueness, enforced at the storage layer. By construction step 2
    --    above never assigns a colliding key, so this cannot fail against
    --    today's data -- it exists so a FUTURE write (a bug, a raw SQL
    --    path) can never assign the same key twice in one workspace.
    CREATE UNIQUE INDEX IF NOT EXISTS uq_projects_task_key
        ON projects (tenant_id, workspace_id, task_key);

    IF to_regclass('public.project_tasks') IS NULL THEN
        RAISE NOTICE 'project_tasks does not exist yet; number/task_seq backfill skipped.';
        RETURN;
    END IF;

    -- 4. project_tasks.number column.
    ALTER TABLE project_tasks ADD COLUMN IF NOT EXISTS number INT;

    -- 5. Backfill number, per project, oldest task first -- stable, so
    --    identifiers never shuffle on a re-run. Guarded to number IS NULL
    --    and offset by whatever a project already has backfilled, so this
    --    is a no-op the second time and cannot renumber an already-numbered
    --    task.
    WITH bases AS (
        SELECT project_id, COALESCE(MAX(number), 0) AS base
        FROM project_tasks
        GROUP BY project_id
    ),
    numbered AS (
        SELECT id, project_id,
               ROW_NUMBER() OVER (
                   PARTITION BY project_id ORDER BY created_at ASC, id ASC
               ) AS rn
        FROM project_tasks
        WHERE number IS NULL
    )
    UPDATE project_tasks pt
    SET number = numbered.rn + COALESCE(bases.base, 0)
    FROM numbered
    LEFT JOIN bases ON bases.project_id = numbered.project_id
    WHERE pt.id = numbered.id;

    -- 6. Seed task_seq to each project's own max backfilled number.
    --    Guarded to task_seq = 0 (its untouched default): this is a
    --    ONE-TIME seed, and must never become a general "resync task_seq to
    --    MAX(number)" -- see the allocation note above for why (a deleted
    --    task would make that computation undercount and the next
    --    allocation would reissue a number that already exists, or once
    --    existed, on another task). Once task_seq leaves 0, this WHERE
    --    clause never touches that project again.
    UPDATE projects p
    SET task_seq = sub.mx
    FROM (
        SELECT project_id, MAX(number) AS mx
        FROM project_tasks
        GROUP BY project_id
    ) sub
    WHERE p.id = sub.project_id
      AND p.task_seq = 0
      AND sub.mx > 0;
END
$$;

COMMIT;

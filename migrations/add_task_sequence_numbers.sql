-- Per-project, human-readable task identifiers -- Linear's `MAN-149` shape,
-- replacing the 6-char hex slice of the task's own uuid that
-- frontend/lib/workspace/fleet/task-status.tsx's taskShortId rendered as a
-- confessed placeholder. This migration builds the SCHEMA for that sequence
-- number. It does NOT backfill -- see the block below, which is the whole
-- reason this file was rewritten on 2026-08-18.
--
-- THREE COLUMNS:
--   projects.task_key    TEXT  -- a short uppercase key derived from the
--                                 project's own `slug` (e.g. slug `general`
--                                 -> key `GEN`), unique per (tenant_id,
--                                 workspace_id). Two projects whose slugs
--                                 reduce to the same 3 letters ("General
--                                 Ops" and "General" both give GEN) do not
--                                 collide -- the later one gets GEN2, then
--                                 GEN3, ... deterministically. NULLable on
--                                 purpose: a project with no key renders no
--                                 identifier at all, never a wrong one.
--   projects.task_seq    INT   -- the allocation counter, NOT NULL DEFAULT 0.
--                                 Only ever increases -- see the allocation
--                                 note below.
--   project_tasks.number INT   -- the allocated number for this task, e.g.
--                                 12 for the 12th task ever created in its
--                                 project. Combined with the OWNING
--                                 project's task_key, this is `GEN-12`.
--                                 NULLable: a task somehow missing one
--                                 renders no identifier (see
--                                 task-status.taskDisplayId), never a
--                                 fabricated fallback.
--
-- ══ WHY THE BACKFILL IS NOT IN THIS FILE ANY MORE ══════════════════════════
--
-- It used to be, and it ran on production, and it touched ZERO rows without
-- reporting anything. `projects` and `project_tasks` carry FORCE ROW LEVEL
-- SECURITY (migrations/enable_rls.sql), whose policy is
-- empyralis_rls_scope_match(tenant_id, workspace_id). A psql session sets
-- none of `app.current_tenant_id` / `app.current_workspace_id` /
-- `app.rls_bypass`, and docs/DEPLOY-RUNBOOK.md step 3b requires migrations to
-- be applied as the app's own NON-SUPERUSER role (`empyralis_app`) -- which is
-- exactly the role FORCE binds. So:
--
--     ALTER TABLE / CREATE INDEX   DDL, not subject to RLS   -> APPLIED
--     SELECT / UPDATE ... projects DML, policy returns false -> 0 rows, no error
--
-- The migration exited 0, the columns and uq_projects_task_key appeared, and
-- every task_key stayed NULL with every task_seq at 0. Measured on production
-- 2026-08-18, five days later: 9 of 10 projects with task_key IS NULL, 34
-- tasks, 0 numbered. A backfill that reports no row count is indistinguishable
-- from a backfill with nothing left to do, which is why nobody noticed.
--
-- THE RULE: a migration that only ADDS COLUMNS is safe to hand to psql; a
-- migration that BACKFILLS a tenant-scoped table is not. The backfill now
-- lives in projects_repository.backfill_task_identifiers(), called from
-- control_plane_repository.ensure_control_plane_schema() -- which sets the
-- RLS scope per row, REUSES _unique_task_key (the same function
-- create_project calls, rather than a second SQL transcription of the same
-- derivation that could drift from it), and heals every database on its next
-- boot instead of on somebody remembering a psql step. Do not reintroduce a
-- DML backfill here.
--
-- ALLOCATION IS RACE-FREE BY CONSTRUCTION, NOT BY THIS MIGRATION.
-- project_tasks_service.create_task allocates a number with:
--
--   UPDATE projects SET task_seq = task_seq + 1
--   WHERE id = $1 RETURNING task_seq
--
-- Postgres serializes concurrent UPDATEs to the same row, so two tasks
-- created in the same project at the same instant still get distinct numbers
-- -- never SELECT MAX(number)+1, which both would read before either wrote.
-- NEVER re-derive task_seq from MAX(project_tasks.number) as an ongoing
-- resync: a deleted task would make that computation UNDERCOUNT and the next
-- allocation would reissue a number that already exists (or once existed) on
-- another task. The Python backfill's seed is guarded to `task_seq = 0` and
-- uses GREATEST for exactly this reason.
--
-- NO NEW TABLES, so this does not touch migrations/enable_rls.sql and does not
-- trigger preflight._check_rls_coverage's table-name two-part dance -- both
-- target tables already exist, already carry RLS, and already appear in that
-- migration. Purely additive: three columns and one index.
--
-- Mirrored into server_modules/control_plane_repository.py's
-- ensure_control_plane_schema() migration section, so a database that never
-- has this file applied by hand still gets the columns on boot. That claim
-- was FALSE until 2026-08-18 (nothing in control_plane_repository.py
-- mentioned task_key at all); it is true now, and there is a test asserting
-- it stays true.

BEGIN;

-- Belt and braces: if a future edit ever does add DML here, or if this file is
-- re-run on a database whose columns are already present, the bypass makes the
-- session's reads honest rather than silently empty. It is transaction-local
-- (SET LOCAL) and dies with the COMMIT below.
SET LOCAL app.rls_bypass = 'on';

ALTER TABLE projects ADD COLUMN IF NOT EXISTS task_key TEXT;
ALTER TABLE projects ADD COLUMN IF NOT EXISTS task_seq INT NOT NULL DEFAULT 0;

-- Uniqueness enforced at the storage layer, so a future write (a bug, a raw
-- SQL path) can never assign the same key twice in one workspace. Postgres
-- allows any number of NULLs in a unique index, so this is satisfiable while
-- projects are still awaiting their key.
CREATE UNIQUE INDEX IF NOT EXISTS uq_projects_task_key
    ON projects (tenant_id, workspace_id, task_key);

ALTER TABLE project_tasks ADD COLUMN IF NOT EXISTS number INT;

COMMIT;

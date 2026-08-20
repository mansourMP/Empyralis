-- Stored-object byte ledger: the count behind the per-project storage cap.
--
-- WHY: file TYPE has been gated server-side since upload_content_policy
-- shipped, and a single file has been capped at 32MB -- but nothing anywhere
-- counted TOTAL bytes. You cannot cap what you do not count, so this table is
-- the prerequisite half of the cap, not an optimisation of an existing
-- counter. Founder's decision: the cap is PER PROJECT.
--
-- ONE ROW PER STORED OBJECT, and usage is SUM(byte_size) computed on read.
-- Deliberately NOT a denormalised running-total column: a counter and the
-- objects it counts are two sources that WILL drift (a failed write, a
-- deleted file, a rolled-back transaction), and a drifted counter is worse
-- than none -- it refuses uploads for space that is actually free, with
-- nothing on screen able to explain why. Uploads are rare; a SUM over the
-- indexed bucket below is free.
--
-- SCOPE TUPLE: (tenant_id, workspace_id, project_id). project_id is
-- TEXT NOT NULL DEFAULT '' and is NOT a foreign key into projects(id), on
-- purpose: '' is a real bucket meaning "not attributable to a project", and
-- a REFERENCES constraint would forbid that row outright. Today EVERY byte
-- lands in that bucket, because the only upload route in the backend
-- (POST /api/sage-chat/attachments) is workspace-scoped and carries no
-- project_id anywhere in its request -- so the cap is per-project by
-- construction and per-workspace in effect. Both bucket kinds are capped by
-- the SAME number (billing_credit_config.project_storage_cap_bytes), so an
-- unattributed object is never a way around the cap.
--
-- WHAT IS COUNTED, and what is not, lives in
-- server_modules/workspace_storage_service.py's module docstring -- the
-- `surface` column carries it per row so the answer is readable from the
-- data, not only from a comment. Short version: real bytes on disk, yes;
-- project document BODIES (markdown TEXT in a Postgres column), no. CLAUDE.md
-- is explicit that context -- projects, documents, tasks -- is the product
-- and is never the paywall; counting document text would make writing a
-- document spend storage allowance, i.e. charge for the thing being sold.
--
-- DDL ONLY, NO BACKFILL, and that is a decision rather than an omission.
-- A ledger of objects written from this migration forward starts correctly
-- empty; inventing historical rows would report usage nobody can trace to a
-- file on disk. It also keeps this file clear of the trap CLAUDE.md
-- documents at length: DEPLOY-RUNBOOK step 3b applies migrations as the
-- app's own NON-SUPERUSER role, FORCE ROW LEVEL SECURITY binds it, psql sets
-- none of the app.current_tenant_id / app.current_workspace_id /
-- app.rls_bypass GUCs -- so any DML here would silently address ZERO ROWS
-- and exit 0. See migrations/add_task_sequence_numbers.sql and
-- projects_repository.backfill_task_identifiers for the two live instances
-- of exactly that.
--
-- Mirrored into control_plane_repository.CONTROL_PLANE_SCHEMA_SQL (so a
-- fresh boot creates it) and into migrations/enable_rls.sql (so the policy
-- travels with the table, never as a follow-up somebody has to remember).

BEGIN;

CREATE TABLE IF NOT EXISTS workspace_storage_objects (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    project_id TEXT NOT NULL DEFAULT '',
    surface TEXT NOT NULL,
    object_key TEXT NOT NULL,
    byte_size BIGINT NOT NULL DEFAULT 0,
    filename TEXT NULL,
    content_type TEXT NULL,
    created_by TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (workspace_id, surface, object_key)
);

CREATE INDEX IF NOT EXISTS idx_workspace_storage_objects_bucket
    ON workspace_storage_objects(tenant_id, workspace_id, project_id);

ALTER TABLE workspace_storage_objects ENABLE ROW LEVEL SECURITY;
ALTER TABLE workspace_storage_objects FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS empyralis_workspace_storage_objects_scope ON workspace_storage_objects;
CREATE POLICY empyralis_workspace_storage_objects_scope ON workspace_storage_objects
    FOR ALL
    USING (public.empyralis_rls_scope_match(tenant_id, workspace_id))
    WITH CHECK (public.empyralis_rls_scope_match(tenant_id, workspace_id));

COMMIT;

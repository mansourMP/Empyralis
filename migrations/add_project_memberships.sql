-- Real per-project ACL (MAN-115): a follow-up to the MAN-70 placeholder
-- ruling that "project member" == "workspace member" for now, with no
-- per-project ACL table. This is that table.
--
-- A row here means "this user can see/act on this project." Workspace
-- OWNERS bypass this table entirely (enforced in server_modules/auth.py's
-- enforce_project_access, not here, and not via a role check on this
-- table) -- an owner must never lose visibility into their own workspace's
-- projects just because nobody explicitly added a row for them. Every
-- other role (member/viewer) needs an explicit row per project, including
-- the workspace's own default ("General") project: there is no implicit
-- "everyone sees the default project" carve-out. That is a deliberate,
-- real restriction, not an oversight -- it is exactly the boundary asked
-- for ("only those people seeing that project").
--
-- No RLS -- scoped like `projects`/`project_tasks`/`bug_reports`: every
-- query filters by (tenant_id, workspace_id) explicitly in
-- projects_repository.py, matching that file's existing Postgres-first
-- convention (projects/project_tasks/bug_reports have no SQLite fallback
-- either -- they are control-plane concepts from the Postgres-first era).

BEGIN;

CREATE TABLE IF NOT EXISTS project_memberships (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'member',
    added_by TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(project_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_project_memberships_project
    ON project_memberships(tenant_id, workspace_id, project_id);
CREATE INDEX IF NOT EXISTS idx_project_memberships_user
    ON project_memberships(tenant_id, workspace_id, user_id);

COMMIT;

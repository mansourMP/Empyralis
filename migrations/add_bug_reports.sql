-- Bug reports (MAN-106): a small, honest "report an issue" entry point
-- reachable from anywhere in the product via a rail icon button (see
-- frontend/lib/workspace/fleet/BugReportButton.tsx). Deliberately NOT a
-- support-ticket system -- no assignment, no notifications, no SLA. Just a
-- durable row an operator can query/read; `status` exists only so a future
-- triage pass has somewhere to write "seen"/"resolved" without a second
-- migration, not because this build ships any status-changing UI.
--
-- No RLS -- scoped like `projects`/`project_tasks`: every query filters by
-- (tenant_id, workspace_id) explicitly in bug_report_service.py.

BEGIN;

CREATE TABLE IF NOT EXISTS bug_reports (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    reported_by_user_id TEXT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    page_path TEXT NOT NULL DEFAULT '',
    user_agent TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'new',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_bug_reports_workspace
    ON bug_reports(tenant_id, workspace_id, created_at DESC);

COMMIT;

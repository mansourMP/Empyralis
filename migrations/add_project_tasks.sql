-- Tasks -> Agents backend foundation (docs/design/tasks-to-agents-research.md
-- Section 4.6, steps 1-3): a first-class task object living inside a Project
-- that can be assigned to an agent, which then wakes up and works it.
--
-- Schema per the research doc's Section 4.1, trimmed to v1 (no labels,
-- cycles, or priorities): id, project_id, title, description, status,
-- assignee_agent_id (nullable -- unassigned = backlog), created_by, due_at,
-- created_at, updated_at. Two additions beyond that literal list, both
-- required by the same doc's own later sections and following every
-- sibling table's convention (projects, workspace_agent_installs,
-- workspace_inventory_items all carry a metadata jsonb column):
--   - plan JSONB: Section 4.4's "re-scope update_plan's current_plan from
--     per-turn to per-task" needs somewhere durable to live; this is that
--     seam. Holds the same {id, title, status} task-list shape the
--     update_plan tool already speaks.
--   - metadata JSONB: free-form extensibility, matching every neighboring
--     table.
--
-- No RLS here -- this table is scoped exactly like `projects` (see
-- control_plane_repository.py's CONTROL_PLANE_SCHEMA_SQL: `projects` itself
-- has no RLS policy in migrations/enable_rls.sql either), not like the more
-- recent workspace_inventory_items. project_tasks_service.py filters every
-- query by (tenant_id, workspace_id) explicitly instead, mirroring
-- projects_repository.py's own plain pool.fetch/fetchrow/execute pattern.

BEGIN;

CREATE TABLE IF NOT EXISTS project_tasks (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'in_progress', 'blocked', 'awaiting_input', 'done')),
    assignee_agent_id TEXT NULL REFERENCES workspace_agent_installs(id) ON DELETE SET NULL,
    created_by TEXT NULL,
    due_at TIMESTAMPTZ NULL,
    plan JSONB NOT NULL DEFAULT '[]'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_project_tasks_project
    ON project_tasks(tenant_id, workspace_id, project_id, status);
CREATE INDEX IF NOT EXISTS idx_project_tasks_assignee
    ON project_tasks(tenant_id, workspace_id, assignee_agent_id)
    WHERE assignee_agent_id IS NOT NULL;

COMMIT;

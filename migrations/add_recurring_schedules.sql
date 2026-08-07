-- Recurring agent wake schedules ("every morning at 9am") -- MAN: recurring
-- schedules gap. fleet_tools.py's schedule_task/propose_self_wakeup path
-- (bounded_scheduler_service.py, agent_scheduler_wake_requests) could only
-- ever schedule ONE future wake-up; _parse_when's own docstring claimed a
-- "Cron: */5 * * * *" form was supported but no branch ever matched it, so
-- a cron string silently fell through to `return None` and was rejected as
-- unparseable. This table is the generator half of the fix: it stores a
-- cron recurrence, not a wake-up itself. A background tick (the SAME
-- wake-request scanner daemon in bounded_scheduler_service.py --
-- run_wake_request_scan_forever / scan_due_wake_requests_once -- no new
-- scheduler thread) finds due rows and, for each, creates exactly ONE
-- ordinary agent_scheduler_wake_requests row (trigger_kind='recurring')
-- through the existing _persist_wakeup gate -- so every recurring fire
-- passes through the SAME quiet-hours, battery/network, and rate-cap
-- machinery every other wake-up already does. This table never bypasses
-- that; it only decides "is it time to ask for another one."
--
-- BOUNDED BY DEFAULT: max_occurrences and expires_at are both nullable, but
-- bounded_scheduler_service.create_recurring_schedule always fills in a
-- default expires_at (90 days out) when the caller supplies neither bound,
-- and clamps whatever IS supplied to a hard ceiling (365 days /
-- RECURRING_SCHEDULE_HARD_MAX_OCCURRENCES occurrences). A forgotten hourly
-- job left running forever on a 2GB VPS is a real cost, not a hypothetical
-- one -- see CLAUDE.md's own agent-worktree-disk-exhaustion incident for
-- the shape of this failure mode applied to a different resource.
--
-- agent_id is a real column (not buried in payload like agent_scheduler_
-- wake_requests' legacy agent_id) because list/cancel by agent is this
-- table's primary access pattern from day one, not something bolted on
-- after the fact.
CREATE TABLE IF NOT EXISTS agent_recurring_schedules (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    master_agent_install_id TEXT NULL REFERENCES workspace_agent_installs(id) ON DELETE SET NULL,
    agent_id TEXT NOT NULL,
    cron_expression TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'active',
    requested_by TEXT NOT NULL DEFAULT 'owner',
    next_fire_at TIMESTAMPTZ NOT NULL,
    last_fired_at TIMESTAMPTZ NULL,
    occurrence_count INTEGER NOT NULL DEFAULT 0,
    max_occurrences INTEGER NULL,
    expires_at TIMESTAMPTZ NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_agent_recurring_schedules_due
    ON agent_recurring_schedules(status, next_fire_at ASC);
CREATE INDEX IF NOT EXISTS idx_agent_recurring_schedules_scope
    ON agent_recurring_schedules(tenant_id, workspace_id, status);
CREATE INDEX IF NOT EXISTS idx_agent_recurring_schedules_agent
    ON agent_recurring_schedules(tenant_id, workspace_id, agent_id, status);

ALTER TABLE agent_recurring_schedules ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_recurring_schedules FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS empyralis_agent_recurring_schedules_scope ON agent_recurring_schedules;
CREATE POLICY empyralis_agent_recurring_schedules_scope ON agent_recurring_schedules
    FOR ALL
    USING (public.empyralis_rls_scope_match(tenant_id, workspace_id))
    WITH CHECK (public.empyralis_rls_scope_match(tenant_id, workspace_id));

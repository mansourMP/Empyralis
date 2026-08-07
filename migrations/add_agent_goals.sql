-- Durable agent goals ("agent, go negotiate with this supplier and come
-- back with a solution") -- MAN: the `/goal` gap. Today an agent gets a
-- task, wakes once, works a turn, and stops; there is no durable object
-- that says "keep working toward this outcome, adapt when an approach
-- fails, escalate after N attempts, then report." This table is that
-- object.
--
-- Modeled directly on agent_recurring_schedules
-- (migrations/add_recurring_schedules.sql), which already proved the
-- shape: a row is bookkeeping about WHEN to next ask for a wake-up, never
-- the wake-up itself. A background tick (bounded_scheduler_service.py's
-- SAME wake-request scanner daemon -- process_due_goals_once, called from
-- the SAME tick as process_due_recurring_schedules_once, no new scheduler
-- thread) finds due goals and, for each, creates exactly ONE ordinary
-- agent_scheduler_wake_requests row (trigger_kind='goal') through the
-- existing _persist_wakeup gate -- so every goal-driven wake still passes
-- through the same quiet-hours, battery/network, and rate-cap machinery
-- every other wake-up already does.
--
-- STATUS VOCABULARY: five of eight values (todo, in_progress,
-- awaiting_input, blocked, in_review, done) are lifted verbatim from
-- project_tasks_service.TASK_STATUS_ORDER -- a goal being worked by an
-- agent moves through the exact same human-legible states a task does,
-- and reusing the words means a human reading both boards needs one
-- mental model, not two. Two goal-specific terminal states were added
-- because the task vocabulary has no way to express them: `exhausted`
-- (the bounded retry/lifetime ceiling was hit WITHOUT the model ever
-- reporting success or giving up -- a system fact, not a narrative) and
-- `cancelled` (the agent or owner deliberately abandoned the goal before
-- either succeeding or exhausting its budget -- distinct from `done`,
-- which means it succeeded). See bounded_scheduler_service.py's
-- GOAL_STATUS_ORDER for the enforced set.
--
-- BOUNDED BY CONSTRUCTION, not by policy alone: unlike agent_recurring_
-- schedules (where max_occurrences/expires_at are both nullable and
-- "forever" is possible until create_recurring_schedule fills a default
-- in), a goal's max_attempts and expires_at are NOT NULL here -- every
-- goal is bounded on both axes from the moment the row exists, no
-- nullable "unbounded" branch to ever reach. attempt_count is advanced
-- ONLY by the system (bounded_scheduler_service._fire_goal, exactly once
-- per wake it actually persists) -- never by the model narrating its own
-- progress, so "how many times has this actually run" stays real,
-- queryable data (CLAUDE.md: "the goal's state must be visible as real
-- data... not a narrative the model writes about itself").
--
-- retry_policy is the backoff shape between attempts (base/max delay,
-- multiplier) -- reuses bounded_scheduler_service.RetryPolicy's existing
-- shape/JSON encoding rather than inventing a second one; max_attempts
-- stays its own top-level column (not folded into this JSON) because it
-- is the WHERE/ORDER-BY-able ceiling every scan and every honesty read
-- needs, not just a delay-computation input.
--
-- project_id is NOT NULL: a goal is attached to a project (the
-- collaboration boundary every project_task__*/document__* tool already
-- uses) and an agent, per the build's own instruction -- there is no
-- "unassigned" goal the way there is an unassigned task, because a goal
-- with nobody to wake is meaningless.
CREATE TABLE IF NOT EXISTS agent_goals (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    agent_id TEXT NOT NULL,
    master_agent_install_id TEXT NULL REFERENCES workspace_agent_installs(id) ON DELETE SET NULL,
    title TEXT NOT NULL DEFAULT '',
    goal_text TEXT NOT NULL,
    instruction TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'todo',
    requested_by TEXT NOT NULL DEFAULT 'owner',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 5,
    retry_policy JSONB NOT NULL DEFAULT '{}'::jsonb,
    next_fire_at TIMESTAMPTZ NOT NULL,
    last_fired_at TIMESTAMPTZ NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_agent_goals_due
    ON agent_goals(status, next_fire_at ASC);
CREATE INDEX IF NOT EXISTS idx_agent_goals_scope
    ON agent_goals(tenant_id, workspace_id, status);
CREATE INDEX IF NOT EXISTS idx_agent_goals_agent
    ON agent_goals(tenant_id, workspace_id, agent_id, status);
CREATE INDEX IF NOT EXISTS idx_agent_goals_project
    ON agent_goals(tenant_id, workspace_id, project_id, status);

ALTER TABLE agent_goals ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_goals FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS empyralis_agent_goals_scope ON agent_goals;
CREATE POLICY empyralis_agent_goals_scope ON agent_goals
    FOR ALL
    USING (public.empyralis_rls_scope_match(tenant_id, workspace_id))
    WITH CHECK (public.empyralis_rls_scope_match(tenant_id, workspace_id));

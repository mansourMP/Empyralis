-- Three indexes for server_modules/operator_console_service.py (the
-- ops.empyralis.ai backend). Purely additive, safe to apply any number of
-- times (IF NOT EXISTS), and NOT required for correctness -- the whole
-- module was runtime-verified against a disposable local Postgres and every
-- query returns in well under a second on today's ~105-workspace, low-
-- thousands-of-events scale even with a full sequential scan. These exist
-- for the query shape, not today's data volume: three of the operator
-- console's queries scan agent_action_events / credit_ledger_events /
-- auth_sessions with a WHERE on created_at/last_seen_at but NO tenant_id
-- equality filter (this is the one place in the codebase that
-- deliberately reads across every tenant at once), so the existing
-- (tenant_id, workspace_id, ...)-prefixed indexes on those tables
-- (control_plane_repository.py's idx_agent_action_events_status,
-- idx_credit_ledger_events_scope_created) cannot help a query with no
-- equality predicate on their leading column. As either table grows past
-- today's scale, these keep the failures/spend/retention windows a range
-- scan instead of a sequential one.

CREATE INDEX IF NOT EXISTS idx_agent_action_events_status_created_global
    ON agent_action_events (status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_credit_ledger_events_created_global
    ON credit_ledger_events (created_at DESC);

-- auth_sessions.last_seen_at is the retention/last-active signal
-- (server_modules/auth_store_repository.py's touch_auth_session, called on
-- every authenticated request) and had no index of its own before this --
-- idx_auth_sessions_user and idx_auth_sessions_expires both exist but
-- neither leads with last_seen_at. Partial (WHERE last_seen_at IS NOT
-- NULL) because a session that was created but never touched again
-- contributes nothing to any retention/last-active query and would just
-- inflate the index.
CREATE INDEX IF NOT EXISTS idx_auth_sessions_last_seen
    ON auth_sessions (last_seen_at DESC)
    WHERE last_seen_at IS NOT NULL;

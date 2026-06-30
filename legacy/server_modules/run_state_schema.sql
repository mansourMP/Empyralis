CREATE TABLE IF NOT EXISTS live_runs (
    run_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    state TEXT NOT NULL,
    payload JSONB NOT NULL,
    trace_id TEXT,
    version BIGINT NOT NULL DEFAULT 0,
    registered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS run_transitions (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL,
    from_state TEXT NOT NULL,
    to_state TEXT NOT NULL,
    actor TEXT NOT NULL,
    trace_id TEXT,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS run_approvals (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL,
    step_id TEXT NOT NULL,
    approval_id TEXT,
    status TEXT NOT NULL DEFAULT 'requested',
    requested_at TIMESTAMPTZ NOT NULL,
    resolved_at TIMESTAMPTZ,
    resolution TEXT,
    actor TEXT,
    trace_id TEXT,
    request_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    decision_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    expires_at TIMESTAMPTZ NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    version BIGINT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS local_queue_claims (
    run_id TEXT PRIMARY KEY,
    worker_id TEXT NOT NULL,
    lease_id TEXT NULL,
    claimed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_heartbeat_at TIMESTAMPTZ NULL,
    last_progress_at TIMESTAMPTZ NULL,
    ttl INTEGER NOT NULL,
    trace_id TEXT,
    last_worker_note TEXT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS local_queue_dead_letters (
    run_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    specialist_key TEXT NULL,
    reason TEXT NOT NULL,
    trace_id TEXT,
    failure_count INTEGER NOT NULL DEFAULT 0,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    first_recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS fleet_worker_registrations (
    worker_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    machine_id TEXT NOT NULL,
    runtime_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'idle',
    control_state TEXT NOT NULL DEFAULT 'active',
    current_run_id TEXT NULL,
    instance_id TEXT NULL,
    shard_key TEXT NOT NULL,
    prewarm_state TEXT NULL,
    warm_pool TEXT NULL,
    lease_seconds INTEGER NOT NULL DEFAULT 30,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    registered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_registered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_heartbeat_at TIMESTAMPTZ NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS fleet_queue_partitions (
    partition_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    specialist_key TEXT NOT NULL,
    pending_count INTEGER NOT NULL DEFAULT 0,
    claimed_count INTEGER NOT NULL DEFAULT 0,
    online_workers INTEGER NOT NULL DEFAULT 0,
    busy_workers INTEGER NOT NULL DEFAULT 0,
    idle_workers INTEGER NOT NULL DEFAULT 0,
    prewarmed_workers INTEGER NOT NULL DEFAULT 0,
    state TEXT NOT NULL DEFAULT 'healthy',
    retry_after_seconds INTEGER NOT NULL DEFAULT 0,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS run_archive (
    run_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    final_state TEXT NOT NULL,
    payload JSONB NOT NULL,
    trace_id TEXT,
    completed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS runtime_sessions (
    session_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    actor JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS runtime_outbox (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    run_id TEXT NULL,
    machine_id TEXT NULL,
    trace_id TEXT NULL,
    idempotency_key TEXT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    delivered_at TIMESTAMPTZ NULL,
    last_replayed_at TIMESTAMPTZ NULL,
    retry_count INTEGER NOT NULL DEFAULT 0,
    last_delivery_error TEXT NULL,
    last_attempted_at TIMESTAMPTZ NULL,
    next_attempt_at TIMESTAMPTZ NULL,
    poisoned_at TIMESTAMPTZ NULL,
    claim_token TEXT NULL,
    claimed_by TEXT NULL,
    claimed_at TIMESTAMPTZ NULL,
    claim_expires_at TIMESTAMPTZ NULL
);

CREATE INDEX IF NOT EXISTS idx_live_runs_workspace_state ON live_runs(workspace_id, state);
CREATE INDEX IF NOT EXISTS idx_run_transitions_run_id ON run_transitions(run_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_run_approvals_run_id ON run_approvals(run_id, requested_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_run_approvals_approval_id ON run_approvals(approval_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_run_approvals_run_step ON run_approvals(run_id, step_id);
CREATE INDEX IF NOT EXISTS idx_run_approvals_status_requested ON run_approvals(status, requested_at DESC);
CREATE INDEX IF NOT EXISTS idx_local_queue_claims_worker_id ON local_queue_claims(worker_id, claimed_at DESC);
CREATE INDEX IF NOT EXISTS idx_local_queue_claims_heartbeat ON local_queue_claims(last_heartbeat_at DESC, claimed_at DESC);
CREATE INDEX IF NOT EXISTS idx_local_queue_dead_letters_workspace ON local_queue_dead_letters(workspace_id, last_recorded_at DESC);
CREATE INDEX IF NOT EXISTS idx_local_queue_dead_letters_specialist ON local_queue_dead_letters(specialist_key, last_recorded_at DESC);
CREATE INDEX IF NOT EXISTS idx_fleet_worker_registrations_workspace_seen ON fleet_worker_registrations(workspace_id, last_heartbeat_at DESC, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_fleet_worker_registrations_shard ON fleet_worker_registrations(shard_key, status, control_state);
CREATE INDEX IF NOT EXISTS idx_fleet_worker_registrations_prewarm ON fleet_worker_registrations(prewarm_state, runtime_type, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_fleet_queue_partitions_workspace ON fleet_queue_partitions(workspace_id, state, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_fleet_queue_partitions_specialist ON fleet_queue_partitions(specialist_key, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_run_archive_workspace_state ON run_archive(workspace_id, final_state, completed_at DESC);
CREATE INDEX IF NOT EXISTS idx_runtime_sessions_workspace_expires ON runtime_sessions(workspace_id, expires_at DESC);
CREATE INDEX IF NOT EXISTS idx_runtime_outbox_due ON runtime_outbox (delivered_at, poisoned_at, next_attempt_at, created_at);
CREATE INDEX IF NOT EXISTS idx_runtime_outbox_claim_expiry ON runtime_outbox (claim_expires_at, created_at) WHERE delivered_at IS NULL;

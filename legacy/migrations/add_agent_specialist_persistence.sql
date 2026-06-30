BEGIN;

CREATE TABLE IF NOT EXISTS agent_manifests (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    agent_install_id TEXT NOT NULL REFERENCES workspace_agent_installs(id) ON DELETE CASCADE,
    manifest_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    manifest JSONB NOT NULL DEFAULT '{}'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(agent_install_id),
    UNIQUE(tenant_id, workspace_id, manifest_id)
);

CREATE TABLE IF NOT EXISTS agent_bible_versions (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    agent_install_id TEXT NOT NULL REFERENCES workspace_agent_installs(id) ON DELETE CASCADE,
    version_number INTEGER NOT NULL,
    bible_text TEXT NOT NULL DEFAULT '',
    bible_sections JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by_user_id TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(agent_install_id, version_number)
);

CREATE TABLE IF NOT EXISTS agent_skill_bindings (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    agent_install_id TEXT NOT NULL REFERENCES workspace_agent_installs(id) ON DELETE CASCADE,
    skill_id TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(agent_install_id, skill_id)
);

CREATE TABLE IF NOT EXISTS agent_connector_bindings (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    agent_install_id TEXT NOT NULL REFERENCES workspace_agent_installs(id) ON DELETE CASCADE,
    connector_key TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    binding JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(agent_install_id, connector_key)
);

CREATE TABLE IF NOT EXISTS agent_channel_bindings (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    agent_install_id TEXT NOT NULL REFERENCES workspace_agent_installs(id) ON DELETE CASCADE,
    channel_key TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    binding JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(agent_install_id, channel_key)
);

CREATE TABLE IF NOT EXISTS agent_runtime_profiles (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    agent_install_id TEXT NOT NULL REFERENCES workspace_agent_installs(id) ON DELETE CASCADE,
    runtime_profile_id TEXT NULL REFERENCES runtime_profiles(id) ON DELETE SET NULL,
    runtime_mode TEXT NOT NULL DEFAULT 'hosted_secure',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(agent_install_id)
);

CREATE TABLE IF NOT EXISTS personal_context_events (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    source_app TEXT NOT NULL,
    event_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    priority INTEGER NOT NULL DEFAULT 50,
    scope JSONB NOT NULL DEFAULT '{}'::jsonb,
    seen_by_sage_at TIMESTAMPTZ NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS agent_scheduler_wake_requests (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    master_agent_install_id TEXT NULL REFERENCES workspace_agent_installs(id) ON DELETE SET NULL,
    trigger_kind TEXT NOT NULL,
    source TEXT NOT NULL,
    requested_by TEXT NOT NULL DEFAULT 'system',
    reason TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    policy JSONB NOT NULL DEFAULT '{}'::jsonb,
    approval_required BOOLEAN NOT NULL DEFAULT FALSE,
    status TEXT NOT NULL DEFAULT 'pending',
    denial_reason TEXT NULL,
    due_at TIMESTAMPTZ NOT NULL,
    claimed_at TIMESTAMPTZ NULL,
    executed_at TIMESTAMPTZ NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS agent_channel_events (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    channel_key TEXT NOT NULL,
    endpoint_key TEXT NOT NULL,
    session_key TEXT NOT NULL,
    thread_id TEXT NULL REFERENCES agent_threads(id) ON DELETE SET NULL,
    responder_install_id TEXT NULL REFERENCES workspace_agent_installs(id) ON DELETE SET NULL,
    direction TEXT NOT NULL,
    event_type TEXT NOT NULL,
    message_id TEXT NULL,
    parent_event_id TEXT NULL,
    run_id TEXT NULL,
    actor JSONB NOT NULL DEFAULT '{}'::jsonb,
    text TEXT NOT NULL DEFAULT '',
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'logged',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS agent_secret_access_events (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    secret_kind TEXT NOT NULL,
    credential_id TEXT NULL,
    provider_id TEXT NULL,
    connector_id TEXT NULL,
    action_id TEXT NULL,
    tool_name TEXT NULL,
    run_id TEXT NULL,
    actor JSONB NOT NULL DEFAULT '{}'::jsonb,
    allowed_fields JSONB NOT NULL DEFAULT '[]'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'allowed',
    denial_code TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS agent_egress_events (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    agent_install_id TEXT NULL REFERENCES workspace_agent_installs(id) ON DELETE SET NULL,
    run_id TEXT NULL,
    runtime_mode TEXT NULL,
    tool_name TEXT NULL,
    provider_id TEXT NULL,
    connector_scope TEXT NULL,
    action_class TEXT NOT NULL DEFAULT 'read',
    request_method TEXT NOT NULL DEFAULT 'GET',
    request_url TEXT NOT NULL,
    request_host TEXT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'allowed',
    denial_code TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS agent_channel_execution_leases (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    responder_install_id TEXT NULL REFERENCES workspace_agent_installs(id) ON DELETE CASCADE,
    thread_id TEXT NOT NULL REFERENCES agent_threads(id) ON DELETE CASCADE,
    session_key TEXT NOT NULL,
    channel_key TEXT NOT NULL,
    endpoint_key TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    acquired_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS security_control_states (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    scope_type TEXT NOT NULL,
    control_kind TEXT NOT NULL DEFAULT 'kill_switch',
    scope_key TEXT NOT NULL,
    agent_install_id TEXT NULL REFERENCES workspace_agent_installs(id) ON DELETE CASCADE,
    channel_key TEXT NULL,
    endpoint_key TEXT NULL,
    connector_id TEXT NULL,
    credential_id TEXT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    status TEXT NOT NULL DEFAULT 'active',
    reason TEXT NOT NULL DEFAULT '',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by_user_id TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(tenant_id, workspace_id, scope_type, control_kind, scope_key)
);

CREATE TABLE IF NOT EXISTS security_control_events (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    control_state_id TEXT NULL REFERENCES security_control_states(id) ON DELETE SET NULL,
    scope_type TEXT NOT NULL,
    control_kind TEXT NOT NULL DEFAULT 'kill_switch',
    action TEXT NOT NULL,
    scope_key TEXT NOT NULL,
    agent_install_id TEXT NULL REFERENCES workspace_agent_installs(id) ON DELETE SET NULL,
    channel_key TEXT NULL,
    endpoint_key TEXT NULL,
    connector_id TEXT NULL,
    credential_id TEXT NULL,
    reason TEXT NOT NULL DEFAULT '',
    actor_user_id TEXT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_agent_manifests_scope ON agent_manifests(tenant_id, workspace_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_bible_versions_install_number ON agent_bible_versions(tenant_id, workspace_id, agent_install_id, version_number DESC);
CREATE INDEX IF NOT EXISTS idx_agent_skill_bindings_install ON agent_skill_bindings(tenant_id, workspace_id, agent_install_id, enabled);
CREATE INDEX IF NOT EXISTS idx_agent_connector_bindings_install ON agent_connector_bindings(tenant_id, workspace_id, agent_install_id, enabled);
CREATE INDEX IF NOT EXISTS idx_agent_channel_bindings_install ON agent_channel_bindings(tenant_id, workspace_id, agent_install_id, enabled);
CREATE INDEX IF NOT EXISTS idx_agent_runtime_profiles_install ON agent_runtime_profiles(tenant_id, workspace_id, agent_install_id);
CREATE INDEX IF NOT EXISTS idx_personal_context_events_scope_created ON personal_context_events(tenant_id, workspace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_personal_context_events_unseen ON personal_context_events(tenant_id, workspace_id, seen_by_sage_at, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_personal_context_events_type_source ON personal_context_events(tenant_id, workspace_id, event_type, source_app, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_personal_context_events_entity ON personal_context_events(tenant_id, workspace_id, entity_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_scheduler_wake_requests_scope_due ON agent_scheduler_wake_requests(tenant_id, workspace_id, status, due_at ASC);
CREATE INDEX IF NOT EXISTS idx_agent_scheduler_wake_requests_master ON agent_scheduler_wake_requests(tenant_id, workspace_id, master_agent_install_id, due_at ASC);
CREATE INDEX IF NOT EXISTS idx_agent_scheduler_wake_requests_trigger ON agent_scheduler_wake_requests(tenant_id, workspace_id, trigger_kind, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_channel_events_scope_created ON agent_channel_events(tenant_id, workspace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_channel_events_session ON agent_channel_events(tenant_id, workspace_id, channel_key, endpoint_key, session_key, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_channel_events_responder ON agent_channel_events(tenant_id, workspace_id, responder_install_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_channel_events_inbound_message
    ON agent_channel_events(tenant_id, workspace_id, channel_key, endpoint_key, direction, session_key, message_id)
    WHERE direction = 'inbound' AND message_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_agent_secret_access_events_scope_created ON agent_secret_access_events(tenant_id, workspace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_secret_access_events_run ON agent_secret_access_events(tenant_id, workspace_id, run_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_secret_access_events_credential ON agent_secret_access_events(tenant_id, workspace_id, credential_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_egress_events_scope_created ON agent_egress_events(tenant_id, workspace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_egress_events_run ON agent_egress_events(tenant_id, workspace_id, run_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_egress_events_install ON agent_egress_events(tenant_id, workspace_id, agent_install_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_egress_events_host ON agent_egress_events(tenant_id, workspace_id, request_host, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_channel_execution_leases_workspace ON agent_channel_execution_leases(tenant_id, workspace_id, expires_at);
CREATE INDEX IF NOT EXISTS idx_agent_channel_execution_leases_responder ON agent_channel_execution_leases(tenant_id, workspace_id, responder_install_id, expires_at);
CREATE INDEX IF NOT EXISTS idx_security_control_states_scope ON security_control_states(tenant_id, workspace_id, scope_type, control_kind, enabled, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_security_control_states_agent ON security_control_states(tenant_id, workspace_id, agent_install_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_security_control_states_channel ON security_control_states(tenant_id, workspace_id, channel_key, endpoint_key, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_security_control_states_connector ON security_control_states(tenant_id, workspace_id, connector_id, credential_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_security_control_events_scope ON security_control_events(tenant_id, workspace_id, scope_type, action, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_security_control_events_state ON security_control_events(tenant_id, workspace_id, control_state_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_security_control_events_actor ON security_control_events(tenant_id, workspace_id, actor_user_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_channel_execution_leases_active_thread
    ON agent_channel_execution_leases(tenant_id, workspace_id, thread_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_channel_bindings_active_inbound_owner
    ON agent_channel_bindings(tenant_id, workspace_id, channel_key, lower((binding->>'endpoint_key')))
    WHERE enabled = TRUE
      AND channel_key IN ('telegram', 'whatsapp', 'email', 'phone', 'web_chat')
      AND lower(COALESCE(binding->>'is_inbound_owner', 'false')) = 'true'
      AND NULLIF(lower(COALESCE(binding->>'endpoint_key', '')), '') IS NOT NULL;

COMMIT;

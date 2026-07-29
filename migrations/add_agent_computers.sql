-- MAN-130: provisioned agent computers (VPS records) move out of the global
-- JSON state file (vps_provisioning_service.VPS_STATE_FILE) and into Postgres.
--
-- The JSON file was a single global blob guarded by an in-process
-- threading.Lock, so multiple uvicorn workers silently lost each other's
-- writes, a truncated file erased the entire fleet on the next read, and there
-- was no way at all to count a workspace's machines (which the per-workspace
-- quota gate, MAN-132, needs).
--
-- Columns mirror vps_provisioning_service.record_vps_provision's record dict
-- one-for-one. `metadata` carries the mutable install-beacon / failure fields
-- that record_vps_install_event and mark_vps_provision_failed annotate onto a
-- record after creation (install_phase, install_error, install_terminal,
-- install_reported_at, error, cleanup_error) — the same free-form keys the JSON
-- record carried, kept in one JSONB column rather than a column per beacon
-- field. pairing_token_ciphertext / credentials_ciphertext are stored exactly
-- as the service already produced them (vault_store envelope ciphertext); this
-- migration does not change how anything is encrypted.

BEGIN;

CREATE TABLE IF NOT EXISTS agent_computers (
    vps_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    provider_resource_id TEXT NOT NULL DEFAULT '',
    public_ip TEXT NULL,
    region TEXT NOT NULL DEFAULT '',
    size TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'provisioning',
    pairing_id TEXT NULL,
    pairing_token_ciphertext TEXT NULL,
    credentials_ciphertext TEXT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- The index the per-workspace quota count reads
-- (agent_computers_repository.count_active_workspace_vps).
CREATE INDEX IF NOT EXISTS idx_agent_computers_scope_status
    ON agent_computers(tenant_id, workspace_id, status);
CREATE INDEX IF NOT EXISTS idx_agent_computers_scope_created
    ON agent_computers(tenant_id, workspace_id, created_at DESC);
-- record_vps_install_event matches an inbound beacon by decrypting each
-- non-deleted record's pairing token, so it scans by status alone.
CREATE INDEX IF NOT EXISTS idx_agent_computers_status
    ON agent_computers(status);

ALTER TABLE agent_computers ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_computers FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS empyralis_agent_computers_scope ON agent_computers;
CREATE POLICY empyralis_agent_computers_scope ON agent_computers
    FOR ALL
    USING (public.empyralis_rls_scope_match(tenant_id, workspace_id))
    WITH CHECK (public.empyralis_rls_scope_match(tenant_id, workspace_id));

COMMIT;

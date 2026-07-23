-- Mentions + identity for platform AND external agents, Step 2: identity is
-- minted by the PLATFORM at the connection boundary, never by the brain.
--
-- A platform agent's identity is its workspace_agent_installs.id (existing).
-- An EXTERNAL agent -- a Codex/Claude Code session the user runs OUTSIDE the
-- platform, connected through our MCP server at /mcp -- has no such row.
-- Its identity is minted the moment its MCP bearer key is created
-- (server_modules/mcp_server_auth.py:create_workspace_mcp_api_key): this
-- table holds that roster entry. The bearer key IS its authentication of
-- identity -- key_hash is UNIQUE and is exactly the SHA-256 hash
-- resolve_workspace_from_api_key already computes on every call, so identity
-- resolution is a single indexed lookup, not a second auth path.
--
-- Why a new small table and not the repo's other "shared resource +
-- subscription" pair (vault_credentials.project_id + agent_connector_
-- bindings)? That pair models N agents subscribing to ONE shared credential
-- (a project-scoped OAuth token several agents reuse). An external-agent
-- roster entry is the opposite shape: exactly one identity minted per bearer
-- key, 1:1, with nothing shared and nothing to subscribe to. Forcing it into
-- the credential/subscription shape would mean either storing an identity as
-- a fake "credential" with no secret to protect, or a fake "binding" with
-- nothing on the other end -- both less honest than a dedicated table that
-- matches every other single-purpose sibling (project_tasks, agent_manifests,
-- agent_bible_versions) already added the same way.
--
-- No RLS here -- scoped exactly like `projects`/`project_tasks`: every query
-- filters by (tenant_id, workspace_id) explicitly in
-- mcp_external_agent_roster_service.py instead.
--
-- `kind` is NOT a column on this table. The founder's "ONE roster view:
-- kind ∈ {platform, external}" requirement is served by a Python-level merge
-- of workspace_agent_installs (kind='platform') and this table
-- (kind='external') in mcp_external_agent_roster_service.list_unified_roster
-- -- a future @-mention resolver reads both kinds through that one function,
-- not a SQL UNION or a shared table.

BEGIN;

CREATE TABLE IF NOT EXISTS mcp_external_agent_roster (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    key_hash TEXT NOT NULL,
    mcp_key_id TEXT NULL,
    revoked BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(key_hash)
);

CREATE INDEX IF NOT EXISTS idx_mcp_external_agent_roster_workspace
    ON mcp_external_agent_roster(tenant_id, workspace_id, revoked);

COMMIT;

-- Stage 4B: Per-agent credentials + channel bindings + tool gating
--
-- Extends workspace_agent_installs with multi-agent isolation fields.
-- Existing rows get safe defaults: no silent capability loss for live agents.

-- 1. enabled_tools: text[] — list of tool names the agent is allowed to use.
--    NULL = ALL tools permitted (backward compatible for existing agents).
ALTER TABLE workspace_agent_installs
    ADD COLUMN IF NOT EXISTS enabled_tools TEXT[];

-- 2. enabled_connectors: text[] — list of connector IDs the agent can invoke.
--    NULL = ALL connectors permitted (backward compatible).
ALTER TABLE workspace_agent_installs
    ADD COLUMN IF NOT EXISTS enabled_connectors TEXT[];

-- 3. channel_bindings: jsonb — maps channel_type + bot_identifier to this agent.
--    Example: [{"channel_type":"telegram","bot_token_hash":"abc123"}]
--    Empty array = no channel bindings (agent reached via Sage fallback only).
ALTER TABLE workspace_agent_installs
    ADD COLUMN IF NOT EXISTS channel_bindings JSONB DEFAULT '[]'::jsonb;

-- 4. subagents_enabled: bool — whether this agent can spawn sub-agents.
--    Default FALSE for all existing agents (Sage is upgraded in seed logic).
ALTER TABLE workspace_agent_installs
    ADD COLUMN IF NOT EXISTS subagents_enabled BOOLEAN DEFAULT FALSE;

-- 5. hardware_access: text — 'none' | 'gateway' | 'vps' | 'all'.
--    Default 'all' for Sage (master agent), 'none' for specialists.
--    We use a CHECK constraint rather than an enum type for portability.
ALTER TABLE workspace_agent_installs
    ADD COLUMN IF NOT EXISTS hardware_access TEXT DEFAULT 'none'
        CHECK (hardware_access IN ('none', 'gateway', 'vps', 'all'));

-- 6. Backfill Sage (master agent) with full access.
--    Sage is identified by agent_kind = 'master' in agent_definitions.
UPDATE workspace_agent_installs
SET hardware_access = 'all',
    subagents_enabled = TRUE
WHERE id IN (
    SELECT wai.id
    FROM workspace_agent_installs wai
    JOIN agent_definitions ad ON ad.id = wai.agent_definition_id
    WHERE ad.agent_kind = 'master'
);

-- 7. Index for channel binding lookups (inbound routing).
--    Not a unique index — multiple agents can bind the same channel type.

COMMENT ON COLUMN workspace_agent_installs.enabled_tools
    IS 'Tool names this agent is allowed to invoke. NULL = all tools.';
COMMENT ON COLUMN workspace_agent_installs.enabled_connectors
    IS 'Connector IDs this agent can invoke. NULL = all connectors.';
COMMENT ON COLUMN workspace_agent_installs.channel_bindings
    IS 'JSON array of {channel_type, bot_identifier} for inbound routing.';
COMMENT ON COLUMN workspace_agent_installs.subagents_enabled
    IS 'Whether this agent can spawn sub-agents.';
COMMENT ON COLUMN workspace_agent_installs.hardware_access
    IS 'Hardware access level: none, gateway, vps, or all.';

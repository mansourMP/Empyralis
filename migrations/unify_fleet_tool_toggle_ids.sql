-- Unify fleet tool-toggle ids onto the canonical enforcement tool name.
--
-- workspace_agent_installs.tool_toggles has been written under three
-- different id spellings for the same logical tools: skill_registry's
-- hyphenated display ids (from manual Tools-tab toggles, e.g. "web-search"),
-- and the bare skill id for a couple of skills whose enforcement name
-- differs entirely (e.g. "browser" vs "browser__navigate"). Enforcement
-- (sage_agent_runtime_service._specialist_tool_allowed) only ever reads the
-- literal LLM tool-call name (e.g. "web__search"), so toggles stored under
-- the old ids were silently inert. See server_modules/skill_registry.py's
-- _ENFORCEMENT_TOOL_NAME map, which fleet_tools.fleet_get_agent_tools now
-- reads/writes going forward — this backfills rows written before that fix.
--
-- OR-merges into the canonical key (never turns a tool off that was on
-- under either spelling) and drops the old key. Idempotent: once a row has
-- no more old-spelling keys, re-running this is a no-op for that row.

DO $$
DECLARE
    id_pair RECORD;
BEGIN
    FOR id_pair IN
        SELECT * FROM (VALUES
            ('web-search', 'web__search'),
            ('browser', 'browser__navigate'),
            ('memory-manager', 'memory_update'),
            ('code-runner', 'shell__exec'),
            ('file-manager', 'file__read'),
            ('telegram-bot', 'telegram_bot__send_message'),
            ('fleet-create-agent', 'fleet__create_agent'),
            ('fleet-list-agents', 'fleet__list_agents'),
            ('fleet-get-agent-activity', 'fleet__get_agent_activity'),
            ('fleet-configure-agent', 'fleet__configure_agent'),
            ('fleet-message-agent', 'fleet__message_agent'),
            ('memory-read', 'memory_read'),
            ('memory-write', 'memory_write')
        ) AS t(old_key, new_key)
    LOOP
        UPDATE workspace_agent_installs
        SET tool_toggles = (tool_toggles - id_pair.old_key) || jsonb_build_object(
            id_pair.new_key,
            COALESCE((tool_toggles ->> id_pair.new_key)::boolean, false)
                OR COALESCE((tool_toggles ->> id_pair.old_key)::boolean, false)
        )
        WHERE tool_toggles ? id_pair.old_key;
    END LOOP;
END $$;

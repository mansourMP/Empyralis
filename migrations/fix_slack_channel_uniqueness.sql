-- Fix: Slack channel bindings had no DB-level uniqueness guarantee.
--
-- uq_agent_channel_bindings_inbound_owner_v2 (control_plane_repository.py)
-- enforces one-inbound-owner-per-channel-endpoint for 'telegram',
-- 'telegram_bot', 'discord', 'discord_bot', 'whatsapp', 'email', 'phone',
-- and 'web_chat' -- but 'slack' was missing from that channel_key IN (...)
-- list (routes_fleet.fleet_assign_agent_slack has carried a NOTE
-- documenting this exact gap since Slack channel binding shipped). Two
-- agents in the same workspace could each claim ownership of the same
-- Slack channel id with no rejection at write time -- only last-write-wins
-- on the column, and agent_channel_router._resolve_agent_for_inbound's
-- linear scan makes read-time resolution non-deterministic whenever more
-- than one row matches.
--
-- This migration applies the corrected predicate (the same one now baked
-- into control_plane_repository.py's CONTROL_PLANE_SCHEMA_SQL going
-- forward) to an already-provisioned database. It cannot rely on that
-- Python schema string alone: `CREATE UNIQUE INDEX IF NOT EXISTS
-- uq_agent_channel_bindings_inbound_owner_v2 ...` is a no-op on any
-- database that already has an index with that name -- true of every
-- environment that has ever booted since the Phase 3D v2 rollout --
-- regardless of whether the predicate text changed. So, like that Phase 3D
-- migration did for the v1 -> v2 rename, this drops and recreates the
-- index -- except here we control execution order directly inside this
-- script (dedupe, then drop, then recreate) instead of relying on
-- IF NOT EXISTS to skip a stale definition.
--
-- SAFETY (this is the critical part): creating a UNIQUE index fails
-- outright if any existing rows would violate it. Step 1 makes that
-- impossible. For any (tenant, workspace, slack channel id) with more than
-- one enabled, is_inbound_owner=true row -- which the DB has never
-- rejected before this migration -- it keeps only the most-recently-
-- updated row as the inbound owner and demotes the rest
-- (is_inbound_owner -> false). No row is deleted and no binding is
-- disabled; a demoted agent simply stops being treated as the exclusive
-- inbound owner of that channel until re-armed by hand, which is the
-- correct behavior since which agent should keep it is the human's call,
-- not the migration's. As of writing, Slack OAuth is not yet configured in
-- production, so this is expected to affect zero rows there -- the
-- migration does not assume that and re-derives the winner from the data.
--
-- Idempotent: safe to run more than once, and safe on a table with zero
-- rows.
--   - Step 1 (dedupe) only ever touches rows that still conflict, so a
--     second run finds nothing to demote (0 rows updated).
--   - Steps 2-3 (drop + recreate) leave the index in the same end state
--     every time; running them again after the predicate already matches
--     is a harmless rebuild of an identical index.

BEGIN;

-- 1. Demote every Slack inbound-owner binding except the most recently
--    updated one, per (tenant_id, workspace_id, channel_key, lower
--    (endpoint_key)) group that has more than one -- the exact column set
--    the new unique index covers. Scoped to channel_key = 'slack' only:
--    every other covered channel_key has been enforced by some version of
--    this index since it was introduced, so it cannot hold a live
--    conflict, and this migration should not touch data outside the bug
--    it is fixing.
WITH slack_inbound_owner_rows AS (
    SELECT
        id,
        ROW_NUMBER() OVER (
            PARTITION BY tenant_id, workspace_id, channel_key, lower(binding->>'endpoint_key')
            ORDER BY updated_at DESC, id DESC
        ) AS recency_rank
    FROM agent_channel_bindings
    WHERE enabled = TRUE
      AND channel_key = 'slack'
      AND lower(COALESCE(binding->>'is_inbound_owner', 'false')) = 'true'
      AND NULLIF(lower(COALESCE(binding->>'endpoint_key', '')), '') IS NOT NULL
)
UPDATE agent_channel_bindings AS acb
SET binding = jsonb_set(acb.binding, '{is_inbound_owner}', 'false'::jsonb),
    updated_at = NOW()
FROM slack_inbound_owner_rows AS dupes
WHERE acb.id = dupes.id
  AND dupes.recency_rank > 1;

-- 2. Drop the stale predicate. The row set is now guaranteed conflict-free
--    by step 1 (within this same transaction), so step 3 cannot fail.
DROP INDEX IF EXISTS uq_agent_channel_bindings_inbound_owner_v2;

-- 3. Recreate it with 'slack' included -- must stay byte-for-byte in sync
--    with the index definition in control_plane_repository.py.
CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_channel_bindings_inbound_owner_v2
    ON agent_channel_bindings(tenant_id, workspace_id, channel_key, lower((binding->>'endpoint_key')))
    WHERE enabled = TRUE
      AND channel_key IN ('telegram', 'telegram_bot', 'discord', 'discord_bot',
                          'whatsapp', 'email', 'phone', 'web_chat', 'slack')
      AND lower(COALESCE(binding->>'is_inbound_owner', 'false')) = 'true'
      AND NULLIF(lower(COALESCE(binding->>'endpoint_key', '')), '') IS NOT NULL;

COMMIT;

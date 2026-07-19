-- Fix: GitHub channel bindings had no DB-level uniqueness guarantee.
--
-- uq_agent_channel_bindings_inbound_owner_v2 (control_plane_repository.py)
-- enforces one-inbound-owner-per-channel-endpoint for 'telegram',
-- 'telegram_bot', 'discord', 'discord_bot', 'whatsapp', 'email', 'phone',
-- 'web_chat', and 'slack' -- but 'github' was missing from that
-- channel_key IN (...) list, even though 'github' was already present in
-- agent_specialist_repository._INBOUND_OWNER_CHANNEL_KEYS (the app-level
-- pre-check run before insert). Two agents in the same workspace could
-- each claim ownership of the same GitHub channel (endpoint) with no
-- DB-level rejection -- the app-level check-then-insert is not atomic, so
-- a race between two concurrent binds could let both win -- only
-- last-write-wins on the column, and agent_channel_router's linear scan
-- makes read-time resolution non-deterministic whenever more than one row
-- matches. This is the same class of bug fixed for Slack by
-- migrations/fix_slack_channel_uniqueness.sql.
--
-- This migration applies the corrected predicate (the same one now baked
-- into control_plane_repository.py's CONTROL_PLANE_SCHEMA_SQL going
-- forward) to an already-provisioned database. It cannot rely on that
-- Python schema string alone: `CREATE UNIQUE INDEX IF NOT EXISTS
-- uq_agent_channel_bindings_inbound_owner_v2 ...` is a no-op on any
-- database that already has an index with that name -- true of every
-- environment that has ever booted since the Phase 3D v2 rollout --
-- regardless of whether the predicate text changed. So, like
-- fix_slack_channel_uniqueness.sql did for 'slack', this drops and
-- recreates the index -- controlling execution order directly inside this
-- script (dedupe, then drop, then recreate) instead of relying on
-- IF NOT EXISTS to skip a stale definition.
--
-- SAFETY (this is the critical part): creating a UNIQUE index fails
-- outright if any existing rows would violate it. Step 1 makes that
-- impossible. For any (tenant, workspace, github channel/endpoint) with
-- more than one enabled, is_inbound_owner=true row -- which the DB has
-- never rejected before this migration -- it keeps only the most-recently-
-- updated row as the inbound owner and demotes the rest
-- (is_inbound_owner -> false). No row is deleted and no binding is
-- disabled; a demoted agent simply stops being treated as the exclusive
-- inbound owner of that channel until re-armed by hand, which is the
-- correct behavior since which agent should keep it is the human's call,
-- not the migration's. The migration does not assume production has zero
-- conflicting rows -- it re-derives the winner from the data.
--
-- Also rebuilds the predicate with 'slack' included (not just 'github'),
-- so this migration converges to the correct end state on its own even if
-- fix_slack_channel_uniqueness.sql was never run against this database --
-- matching the exact channel_key list now in
-- control_plane_repository.py's CONTROL_PLANE_SCHEMA_SQL. Running this
-- after fix_slack_channel_uniqueness.sql (in either order) is a harmless
-- no-op rebuild for the Slack portion of the predicate.
--
-- Idempotent: safe to run more than once, and safe on a table with zero
-- rows.
--   - Step 1 (dedupe) only ever touches rows that still conflict, so a
--     second run finds nothing to demote (0 rows updated).
--   - Steps 2-3 (drop + recreate) leave the index in the same end state
--     every time; running them again after the predicate already matches
--     is a harmless rebuild of an identical index.

BEGIN;

-- 1. Demote every GitHub inbound-owner binding except the most recently
--    updated one, per (tenant_id, workspace_id, channel_key, lower
--    (endpoint_key)) group that has more than one -- the exact column set
--    the unique index covers. Scoped to channel_key = 'github' only: every
--    other covered channel_key has been enforced by some version of this
--    index since it was introduced (or, for 'slack', deduped by its own
--    migration), so it cannot hold a live conflict this migration needs to
--    resolve, and this migration should not touch data outside the bug it
--    is fixing.
WITH github_inbound_owner_rows AS (
    SELECT
        id,
        ROW_NUMBER() OVER (
            PARTITION BY tenant_id, workspace_id, channel_key, lower(binding->>'endpoint_key')
            ORDER BY updated_at DESC, id DESC
        ) AS recency_rank
    FROM agent_channel_bindings
    WHERE enabled = TRUE
      AND channel_key = 'github'
      AND lower(COALESCE(binding->>'is_inbound_owner', 'false')) = 'true'
      AND NULLIF(lower(COALESCE(binding->>'endpoint_key', '')), '') IS NOT NULL
)
UPDATE agent_channel_bindings AS acb
SET binding = jsonb_set(acb.binding, '{is_inbound_owner}', 'false'::jsonb),
    updated_at = NOW()
FROM github_inbound_owner_rows AS dupes
WHERE acb.id = dupes.id
  AND dupes.recency_rank > 1;

-- 2. Drop the stale predicate. The row set is now guaranteed conflict-free
--    by step 1 (within this same transaction), so step 3 cannot fail.
DROP INDEX IF EXISTS uq_agent_channel_bindings_inbound_owner_v2;

-- 3. Recreate it with 'github' included -- must stay byte-for-byte in sync
--    with the index definition in control_plane_repository.py.
CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_channel_bindings_inbound_owner_v2
    ON agent_channel_bindings(tenant_id, workspace_id, channel_key, lower((binding->>'endpoint_key')))
    WHERE enabled = TRUE
      AND channel_key IN ('telegram', 'telegram_bot', 'discord', 'discord_bot',
                          'whatsapp', 'email', 'phone', 'web_chat', 'slack', 'github')
      AND lower(COALESCE(binding->>'is_inbound_owner', 'false')) = 'true'
      AND NULLIF(lower(COALESCE(binding->>'endpoint_key', '')), '') IS NOT NULL;

COMMIT;

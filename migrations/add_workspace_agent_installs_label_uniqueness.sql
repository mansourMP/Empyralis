-- Fix: agent display names had no DB-level uniqueness guarantee.
--
-- fleet_create_agent's auto-naming path was already collision-checked
-- against every existing label in the workspace (case-insensitive, see
-- agent_name_pool.assign_agent_name) -- but the manual rename path
-- (fleet_configure_agent's display_name PATCH, fleet_tools.py) wrote
-- straight to the workspace_agent_installs.label column with ZERO
-- collision checking, and no DB constraint backed either path. Two agents
-- in the same workspace could end up sharing a display name with nothing
-- to catch it -- ambiguous for a human reading the fleet roster AND for
-- the closed-roster mention autocomplete that has to resolve a typed name
-- back to exactly one agent_id. fleet_tools.py now rejects a colliding
-- rename at the app layer; this migration (and the identical guarded logic
-- in control_plane_repository.py's ensure_control_plane_schema, which runs
-- this same dedupe-then-constrain path automatically on every process
-- boot) is the DB-level backstop so a raw API PATCH, a future direct-SQL
-- path, or a bug elsewhere cannot bypass it.
--
-- This applies to an already-provisioned database directly (e.g. via
-- `psql <DATABASE_URL> -f migrations/add_workspace_agent_installs_label_uniqueness.sql`)
-- for any environment that wants the constraint in place without waiting
-- for a process restart to pick it up. Safe to run even if
-- ensure_control_plane_schema already applied it on this same database --
-- see idempotency notes below.
--
-- SAFETY (this is the critical part): creating a UNIQUE index fails
-- outright if any existing rows would violate it. This is a BRAND NEW
-- index -- there is no existing-name trick (unlike
-- uq_agent_channel_bindings_inbound_owner_v2) to make a bare
-- `CREATE UNIQUE INDEX IF NOT EXISTS` a guaranteed no-op on data that
-- already conflicts -- so step 1 below makes conflict impossible before
-- step 2 ever runs.
--
-- Step 1 (dedupe) is deterministic and collision-proof by construction:
-- repeatedly finds the oldest non-first row within any (tenant_id,
-- workspace_id, case-insensitive label) group that has more than one
-- member -- the earliest-created row in the group keeps its name
-- unchanged -- and renames every later duplicate to the smallest
-- "<label> N" suffix that does not already collide with ANY other label
-- currently in that workspace (checked live against the table on every
-- attempt, not just within the duplicate group), so it can never
-- manufacture a NEW collision against an unrelated agent that already
-- happens to be named e.g. "Atlas 2". No row is deleted, no agent is
-- disabled -- a demoted duplicate simply gets a disambiguating suffix,
-- exactly the same shape agent_name_pool.assign_agent_name already
-- produces when its own 50-name pool runs out.
--
-- Idempotent: safe to run more than once, and safe on a table with zero
-- rows.
--   - Step 1 only ever touches rows that still have a live duplicate, so a
--     second run finds nothing to rename (0 rows updated).
--   - Step 2 (`CREATE UNIQUE INDEX IF NOT EXISTS`) is a harmless no-op once
--     the index exists.

BEGIN;

-- 1. Dedupe: rename every non-first duplicate label within its
--    (tenant_id, workspace_id, lower(label)) group to a suffixed name that
--    is guaranteed unique in that workspace at the moment it is assigned.
DO $$
DECLARE
    dup RECORD;
    candidate_label TEXT;
    suffix INTEGER;
BEGIN
    LOOP
        SELECT id, tenant_id, workspace_id, label
        INTO dup
        FROM (
            SELECT id, tenant_id, workspace_id, label,
                   ROW_NUMBER() OVER (
                       PARTITION BY tenant_id, workspace_id, lower(label)
                       ORDER BY created_at ASC, id ASC
                   ) AS label_rank
            FROM workspace_agent_installs
            WHERE label IS NOT NULL AND label <> ''
        ) ranked
        WHERE label_rank > 1
        ORDER BY tenant_id, workspace_id, lower(label), id
        LIMIT 1;

        EXIT WHEN NOT FOUND;

        suffix := 2;
        LOOP
            candidate_label := dup.label || ' ' || suffix;
            EXIT WHEN NOT EXISTS (
                SELECT 1 FROM workspace_agent_installs w
                WHERE w.tenant_id = dup.tenant_id
                  AND w.workspace_id = dup.workspace_id
                  AND lower(w.label) = lower(candidate_label)
            );
            suffix := suffix + 1;
        END LOOP;

        UPDATE workspace_agent_installs
        SET label = candidate_label, updated_at = NOW()
        WHERE id = dup.id;
    END LOOP;
END $$;

-- 2. The row set is now guaranteed conflict-free by step 1 (within this
--    same transaction), so this cannot fail.
CREATE UNIQUE INDEX IF NOT EXISTS uq_workspace_agent_installs_label
    ON workspace_agent_installs(tenant_id, workspace_id, lower(label))
    WHERE label IS NOT NULL AND label <> '';

COMMIT;

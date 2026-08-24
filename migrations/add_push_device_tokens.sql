-- APNs device tokens: which physical devices a person has asked to be
-- pushed to, per workspace.
--
-- DDL ONLY. There is no DML anywhere in this file and nothing to backfill:
-- a device token can only ever be produced by a real device registering
-- itself, so the table starts correctly empty and inventing rows would be
-- inventing devices. That also keeps this migration clear of the
-- backfill-under-RLS trap CLAUDE.md documents (a DML statement here runs
-- as the non-superuser `empyralis_app` role with no scope GUCs set, is
-- silently filtered to zero rows, and still exits 0).
--
-- SCOPE: (tenant_id, workspace_id, user_id). RLS below expresses only the
-- standard two-column (tenant_id, workspace_id) empyralis_rls_scope_match
-- every other table here uses -- there is no third-column variant of that
-- function and inventing a per-user session GUC for one table would be a
-- second, divergent RLS mechanism. The per-person axis is enforced in
-- server_modules/push_device_repository.py, where user_id is a REQUIRED
-- keyword with NO DEFAULT on every function and is bound explicitly in
-- every WHERE clause -- the same split CLAUDE.md documents for
-- vault_credentials and the run_state_repository tables ("a scope column
-- with a default is a loaded gun", applied to user_id exactly as it is to
-- workspace_id elsewhere).
--
-- WHY device_token IS GLOBALLY UNIQUE AND NOT SCOPED. APNs issues one
-- token per (device, app install), and Apple reassigns it -- the same
-- physical device that was registered by user A can later present the same
-- token as user B (a re-install, a hand-me-down phone, a second account on
-- one device). If the uniqueness were (workspace_id, device_token) both
-- rows could be active at once and one person's notification would land on
-- the other's lock screen. A single global UNIQUE makes the latest
-- registration authoritative by construction: the upsert re-points the row
-- at whoever most recently proved possession of the device.
--
-- `active` is a soft-delete, never a DELETE: a token deactivated by APNs'
-- own 410 Unregistered response is a FACT about that token, and a deleted
-- row is a fact we would immediately re-learn the hard way by pushing to
-- it again. Deactivating rather than deleting is what makes "a dead token
-- must not be retried forever" hold across restarts.
--
-- Mirrored into control_plane_repository.CONTROL_PLANE_SCHEMA_SQL so a
-- fresh database gets the table on boot; see migrations/enable_rls.sql for
-- the ENABLE/FORCE/POLICY statements.

BEGIN;

CREATE TABLE IF NOT EXISTS push_device_tokens (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    -- The person this device belongs to. Required, never NULL -- a push
    -- target with no owner is a contradiction, not an edge case.
    user_id TEXT NOT NULL,
    -- The APNs device token, lowercase hex as the device reports it.
    device_token TEXT NOT NULL,
    platform TEXT NOT NULL DEFAULT 'ios',
    bundle_id TEXT NOT NULL DEFAULT '',
    -- 'sandbox' (a development build, api.sandbox.push.apple.com) or
    -- 'production'. These are DIFFERENT APNs hosts and a token minted
    -- against one is rejected by the other, so the environment is a
    -- property of the token and is stored with it rather than assumed
    -- from deployment config.
    environment TEXT NOT NULL DEFAULT 'production',
    -- The app's own stable per-install identifier, if it sends one. Used
    -- for display/debugging only; never a scoping input.
    device_id TEXT NOT NULL DEFAULT '',
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_push_device_tokens_token UNIQUE (device_token)
);

-- The one read pattern that matters: "every live device for THIS person in
-- THIS workspace", which is what a send fans out over.
CREATE INDEX IF NOT EXISTS idx_push_device_tokens_workspace_user
    ON push_device_tokens(workspace_id, user_id);

COMMIT;

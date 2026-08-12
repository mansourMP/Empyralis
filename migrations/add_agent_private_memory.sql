-- Agent private memory: the PER-PERSON half of the shared-vs-private memory
-- split (CLAUDE.md: "sessions/threads are private to the person (always, no
-- setting); the agent (name, config, memory, task history) is shared with
-- the project"). Before this table, agent memory had exactly ONE scope
-- tuple honored anywhere in the codebase -- (workspace_id, agent_install_id)
-- -- with no per-user dimension at all (verified by grep across
-- memory_service.py, agent_memory.py, agent_memory_tools.py,
-- unified_memory_service.py, workspace_context_memory_adapter.py: the only
-- human-identity field present anywhere near a write is `actor`/`source`,
-- used solely for an audit-trail string and a "[name via X] " display
-- marker, never as a partition key or a read filter). So a teammate using a
-- shared agent wrote into, and read out of, the exact same pool that holds
-- the owner's own accumulated context -- the founder's own words: "once I
-- have every context and evolved agent, how is it going to work once I have
-- my other person, which is also going to evolve its context window, which
-- I may not like."
--
-- SCOPE: (tenant_id, workspace_id, agent_install_id, user_id) -- the SHARED
-- pool (memory_service.py / agent_memory.py: MEMORY.md, memory/*.md topic
-- files, memory_entries) is UNCHANGED and stays the correct home for facts
-- about the work/company that every project member should benefit from.
-- This table is only the new, narrow, per-person slice: "how THIS person
-- likes to be worked with," never visible to, and never shaped by, anyone
-- else's turns.
--
-- ONE ROW PER PERSON PER AGENT, UPSERT IN PLACE -- the same "Decision B"
-- update-don't-duplicate posture agent_memory.py's memory_entries already
-- uses for its own key/value facts (see server_modules/agent_memory.py),
-- applied here at table granularity instead of per-key: a private
-- preference note is a single small, evolving blob, not a set of named
-- entries. `id` is stable and never reused (an UPDATE, never a
-- delete+reinsert), which is exactly what a revisions table needs to hang
-- snapshots off -- the identical seam project_documents/
-- project_document_revisions already established, applied here from day
-- one instead of retrofitted later.
--
-- BODY LIVES IN POSTGRES, NOT ON DISK, for the same reason project_documents
-- gives (see migrations/add_project_documents.sql): agent_memory.py's local
-- .orion-stack/memory/ files are not durable across a machine change and
-- are not the database of record. A private preference note is exactly the
-- kind of small, durable, per-person fact that belongs in the control
-- plane, RLS-scoped, alongside everything else here.
--
-- RLS. Written from day one against control_plane_repository.rls_fetch/
-- rls_fetchrow/rls_execute -- see migrations/enable_rls.sql for the actual
-- ENABLE/FORCE/POLICY statements. The RLS policy itself only expresses the
-- two-column (tenant_id, workspace_id) scope_match every other table here
-- uses (CLAUDE.md: no third-column variant of empyralis_rls_scope_match
-- exists, and inventing a per-user session GUC for one table would be a
-- second, divergent RLS mechanism). The finer per-person boundary --
-- "user A's row is never readable by user B" -- is enforced in
-- server_modules/agent_private_memory_repository.py, where every function
-- takes `user_id` as a REQUIRED keyword with NO DEFAULT and binds it
-- explicitly in the WHERE clause of every query, the same split CLAUDE.md
-- documents for vault_credentials and the run_state_repository tables
-- (RLS is the tenant/workspace backstop; application code owns the axis
-- RLS's own two-column function cannot express). "A scope column with a
-- default is a loaded gun" applies to user_id here exactly as it does to
-- workspace_id elsewhere.
--
-- Mirrored into server_modules/control_plane_repository.py's CONTROL_
-- PLANE_SCHEMA_SQL -- CREATE TABLE IF NOT EXISTS is a no-op-or-create on
-- every boot, fresh database and existing one alike.

BEGIN;

CREATE TABLE IF NOT EXISTS agent_private_memory_notes (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    agent_install_id TEXT NOT NULL,
    -- The person this note belongs to. Required, never NULL -- a private
    -- note with no owner is a contradiction, not an edge case (contrast
    -- vault_credentials' nullable workspace_id, which is load-bearing
    -- there for platform-scoped rows; there is no equivalent "unowned"
    -- private-memory concept).
    user_id TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, workspace_id, agent_install_id, user_id)
);

-- The primary read pattern: "give me THIS person's note for THIS agent" --
-- an exact-match point lookup, not a scan, so the unique constraint above
-- already is the index; this one exists for the revisions table's
-- reverse-lookup (note_id -> owning scope) without a join back through the
-- notes table on every history read.
CREATE INDEX IF NOT EXISTS idx_agent_private_memory_notes_lookup
    ON agent_private_memory_notes(tenant_id, workspace_id, agent_install_id, user_id);

-- Revision history -- the audit trail Decision B's own precedent
-- (memory_entries_history) already establishes for structured memory
-- writes: every upsert is recorded, the live row only ever holds the
-- latest content. `reason` carries the SAME free-text audit purpose
-- `_append_memory_file_version_record`'s `reason` field does for the
-- shared pool -- never a scoping input, display-only.
CREATE TABLE IF NOT EXISTS agent_private_memory_note_revisions (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    note_id TEXT NOT NULL REFERENCES agent_private_memory_notes(id) ON DELETE CASCADE,
    agent_install_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    reason TEXT NULL,
    revision_number INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (note_id, revision_number)
);

CREATE INDEX IF NOT EXISTS idx_agent_private_memory_note_revisions_note
    ON agent_private_memory_note_revisions(tenant_id, workspace_id, note_id, revision_number DESC);

COMMIT;

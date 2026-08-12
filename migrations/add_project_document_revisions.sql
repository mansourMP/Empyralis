-- Project document revisions: durable history for project_documents
-- (migrations/add_project_documents.sql), landed alongside the MCP tools
-- that let a teammate's own Claude/ChatGPT edit a document directly
-- (feat/document-mcp-tools-and-revisions). Two worries this table answers
-- at once, both the founder's own: with no hardware connected a document
-- edit "wouldn't be tracked" -- this table is the tracking; and an edit
-- must be diff/patch-native ("just like git -- write a line and push it"),
-- never a whole-blob rewrite -- see the SNAPSHOT + DIFF note below for how
-- that is expressed in this schema.
--
-- THE SEAM: project_documents_repository.py's own module docstring (see
-- also the REVISIONS section there) predicted this table back when
-- project_documents first shipped -- `id` is stable and never reused or
-- recreated by an edit (update_document is a plain in-place UPDATE, never
-- delete+reinsert), which is exactly what a revisions table needs to hang
-- snapshots off. `document_id` below is that hook.
--
-- SNAPSHOT + DIFF, NOT SNAPSHOT-ONLY: the founder's own words for the edit
-- shape this landed alongside: "to upgrade one line or one word or one
-- sentence of this specific document, agent must not rewrite the entire
-- document... just like git -- write a line and push it." Each row carries
-- BOTH a full snapshot of the document AS IT BECAME after one write
-- (create_document writes revision 1 at birth; update_document appends one
-- on every edit, whether that edit came in as a targeted old_string/
-- new_string patch or a whole-body replace) AND `diff`, a human-readable
-- unified diff against the row's immediately-PRIOR state, computed
-- server-side (project_documents_repository._compute_document_diff) at
-- write time. `diff` is the primary artifact a history read cares about
-- ("this line changed"); the snapshot is kept alongside it, not instead of
-- it, because reconstructing revision N by replaying N sequential patches
-- is O(N) per read and one corrupt/missing patch would break every later
-- reconstruction -- these documents are small markdown text (not a large
-- binary), so one extra blob per edit is cheap, and every revision stays
-- independently readable even if a diff is ever malformed. Revision
-- history is read-only either way (no restore/rollback UI -- CLAUDE.md:
-- "a surface must earn its place"; the founder asked for tracked history,
-- not a rollback flow).
--
-- WHO CHANGED IT: `changed_by_type` reuses the EXACT vocabulary
-- project_tasks_service.add_task_comment's own `author_type` already
-- established for the identical polymorphic-actor problem (human / agent /
-- external_agent / system) -- not a second vocabulary for the same
-- concept. `changed_by_id` is the same free-text, unconstrained-by-FK actor
-- id project_documents.updated_by already is (a user id, a
-- workspace_agent_installs id, or an external MCP caller's opaque
-- ext_agent_<hex> id); `changed_by_display_name` is the same optional name
-- snapshot add_task_comment's `author_display_name` takes, for the same one
-- author kind whose id resolves against neither table (an external agent).
--
-- FAIL-OPEN ON THE WRITE, DELIBERATELY: create_document/update_document
-- (project_documents_repository.py) write the document row FIRST and only
-- then attempt one revision insert, in a try/except that can never unwind
-- or block the document write -- a revisions-table outage must report
-- itself (`document["revision_recorded"] = False` +
-- `document["revision_error"]`), never silently corrupt or silently drop
-- the edit the caller was just told succeeded. This is the founder's own
-- law applied here: the live document is never allowed to be the thing
-- that goes missing because history couldn't be written.
--
-- RLS: same posture as project_documents and every other table created
-- since task_notifications -- every call site
-- (project_documents_repository._record_document_revision/
-- list_document_revisions) is written against control_plane_repository's
-- rls_fetch/rls_execute helpers from the start, so RLS ships in the same
-- change that creates the table (see migrations/enable_rls.sql).
--
-- Mirrored into server_modules/control_plane_repository.py's CONTROL_
-- PLANE_SCHEMA_SQL -- CREATE TABLE IF NOT EXISTS is a no-op-or-create on
-- every boot, fresh database and existing one alike.

BEGIN;

CREATE TABLE IF NOT EXISTS project_document_revisions (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    document_id TEXT NOT NULL REFERENCES project_documents(id) ON DELETE CASCADE,
    -- Denormalized off project_documents.project_id at write time (not a
    -- second FK lookup on every history read) -- a document never changes
    -- project after creation, so this never drifts from the live row.
    project_id TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    -- Human-readable unified diff against the PRIOR revision (NULL only for
    -- the rare no-op case _compute_document_diff itself refuses to
    -- fabricate -- see that function's docstring). The primary artifact a
    -- history read wants; `body` above is the safety-net snapshot under it.
    diff TEXT NULL,
    -- human | agent | external_agent | system -- project_tasks_service.
    -- add_task_comment's own author_type vocabulary, reused verbatim.
    changed_by_type TEXT NOT NULL DEFAULT 'unknown',
    changed_by_id TEXT NULL,
    changed_by_display_name TEXT NULL,
    -- Per-document sequence, computed server-side (COALESCE(MAX(...),0)+1
    -- in the INSERT itself -- see _record_document_revision). The UNIQUE
    -- constraint below is the backstop against two truly concurrent writers
    -- to the SAME document computing the same number: a loud INSERT
    -- failure (caught and reported by the caller) rather than a silently
    -- ambiguous history entry.
    revision_number INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (document_id, revision_number)
);

-- The primary read pattern: "this document's history, newest first" --
-- list_document_revisions' own ORDER BY revision_number DESC.
CREATE INDEX IF NOT EXISTS idx_project_document_revisions_document
    ON project_document_revisions(tenant_id, workspace_id, document_id, revision_number DESC);

COMMIT;

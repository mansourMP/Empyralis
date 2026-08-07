-- Project documents: a project's owned, markdown knowledge -- the storage
-- half of the "owned-context layer for a team" positioning (CLAUDE.md). No
-- RAG, no embeddings, no vector store (Boris Cherny/Claude Code's own
-- finding that agentic search beats RAG+vector-db, and Anthropic's Managed
-- Agents memory stores are plain files an agent reads with normal file
-- tools -- see project_documents_repository.py's module docstring). An
-- agent finds a document by listing/searching this project's set, the same
-- way it would grep a folder, not through a retrieval pipeline.
--
-- SCOPE: project, not per-agent -- every agent and every human member of a
-- project shares the same document set, mirroring project_tasks' own
-- collaboration boundary (CLAUDE.md: "Projects hold members directly").
--
-- FLAT, NOT HIERARCHICAL. No folder/path tree. A project's documents are a
-- flat list distinguished by title and a per-project-unique slug. The
-- founder's law is "a surface must earn its place," and the same applies to
-- schema: nothing today asks for nested folders, and a flat list is the
-- smaller thing that can always grow a `parent_document_id` self-reference
-- later (exactly how project_tasks added one-level sub-tasks onto an
-- already-shipped flat table, migrations/add_task_parent.sql) rather than
-- paying for a path/tree model nobody has asked to use yet.
--
-- BODY LIVES IN POSTGRES, NOT ON DISK. Two existing patterns were weighed
-- and rejected:
--   - agent_memory.py stores memory as files under .orion-stack/memory/ on
--     local disk -- flagged by audit as not durable across machines and not
--     in the database of record.
--   - knowledge_sources keeps metadata in Postgres but the raw markdown on
--     disk under .orion-stack/workspace/...  -- same durability gap for the
--     part that actually matters (the content).
-- A project document is small (markdown text, not a binary asset) and needs
-- exactly what Postgres already gives every other control-plane row here:
-- one database of record, RLS-scoped isolation, and survival of a machine
-- change without a separate backup/sync story. `body TEXT` is that choice.
--
-- REVISIONS ARE NOT BUILT IN THIS PASS, but the seam is deliberate: `id` is
-- a stable, permanent identifier untouched by an update (UPDATE is in
-- place, never delete+reinsert), which is exactly what a later
-- `project_document_revisions(document_id REFERENCES project_documents(id)
-- ...)` table needs to hang snapshots or patches off of. The founder's
-- stated future direction is diff/patch-native edits for agents, not
-- whole-blob replace -- this pass does not attempt that; it only avoids
-- doing anything (like reusing `id` across logically-different documents,
-- or hard-deleting on every edit) that would make it harder to retrofit.
--
-- RLS. Written from day one against control_plane_repository.rls_fetch/
-- rls_fetchrow/rls_execute -- there is no legacy unconverted call site to
-- sequence around, the same reasoning migrations/add_task_notifications.sql
-- gives for enabling RLS in the very first migration that creates the
-- table rather than deferring it like the original six-tables retrofit had
-- to. See migrations/enable_rls.sql for the actual ENABLE/FORCE/POLICY
-- statements (kept centralized there, matching every other table).
--
-- Mirrored into server_modules/control_plane_repository.py's CONTROL_
-- PLANE_SCHEMA_SQL -- CREATE TABLE IF NOT EXISTS is a no-op-or-create on
-- every boot, fresh database and existing one alike.

BEGIN;

CREATE TABLE IF NOT EXISTS project_documents (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    -- Per-project-unique, human-readable identifier (see the UNIQUE
    -- constraint below) -- lets a document be addressed by
    -- /projects/{id}/documents/{slug} and, later, by an agent's file-tool
    -- style reference, without exposing the raw `id`.
    slug TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    -- Free-text actor ids, unconstrained by an FK: the author/editor can be
    -- a workspace user, a workspace_agent_installs row, or an external
    -- agent's opaque ext_agent_<hex> id -- the same polymorphic-actor
    -- posture project_tasks.created_by and task_notifications.actor_id
    -- already take, for the identical reason (no single table to FK a
    -- three-kind identity against).
    created_by TEXT NULL,
    updated_by TEXT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (project_id, slug)
);

-- The primary read pattern: "list this project's documents, alphabetically"
-- -- a table-of-contents view, not a feed, so the natural default order is
-- by title rather than recency.
CREATE INDEX IF NOT EXISTS idx_project_documents_project
    ON project_documents(tenant_id, workspace_id, project_id, title);

COMMIT;

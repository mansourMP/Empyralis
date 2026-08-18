-- Documents become GitHub-shaped: a `path` with slashes, replacing `slug`.
--
-- GIT'S MODEL IS THE SPEC (founder, 2026-08-18, approving the mapping
-- org->workspace / repo->project / file path->document.path): "the perfect
-- shape is git, so I don't want to edit this thing." So there are NO FOLDER
-- OBJECTS here and there must never be. Git stores paths and INFERS
-- directories from the slashes; a `document_folders` table would be
-- inventing a concept git does not have, and every reader would then have
-- to keep two sources of truth agreeing about which folders exist.
-- `specs/api/auth.md` is one row. The folder `specs/` is a prefix, not a
-- record.
--
-- A RENAME, NOT A SECOND COLUMN. `slug` was already the agent's addressing
-- handle (project_documents_repository.get_document_by_slug, called from
-- skills_service.py's document__* dispatch) and add_project_documents.sql's
-- own header predicted "/projects/{id}/documents/{slug}" addressing. A path
-- IS a slug that may contain slashes -- so this generalizes the existing
-- name rather than adding a rival to it. Keeping both would leave two names
-- for one thing that can disagree, which is the exact failure mode CLAUDE.md
-- records repeatedly (a channel list copied into a third place; the five
-- hand-written OpenClaw channel maps). One name, no drift, and `metadata`
-- JSONB is deliberately NOT used to carry this: it is written by nothing
-- today, and a JSONB key cannot take the UNIQUE constraint this needs.
--
-- The UNIQUE(project_id, slug) constraint FOLLOWS the rename automatically
-- (Postgres renames the column, the constraint keeps its own name and now
-- covers `path`) -- so per-project uniqueness is preserved with no window
-- in which two documents could collide.
--
-- COLUMN, NOT TABLE. Deliberate, and it is what keeps this migration
-- cheap: preflight._check_rls_coverage keys on TABLE names and asks the
-- live database which tables carry tenant_id/workspace_id, so renaming a
-- column inside an already-covered table needs no enable_rls.sql change and
-- no _RLS_COVERAGE_EXCEPTIONS key. CLAUDE.md's "renaming a scoped table is
-- a TWO-PART change" warning (~3 minutes of production 502 on 2026-08-13)
-- is about table names and does not apply here -- verified by grepping the
-- exceptions list for a column-level key: there is none.
--
-- IDEMPOTENT. control_plane_repository.CONTROL_PLANE_SCHEMA_SQL runs this
-- shape on EVERY boot against both fresh and existing databases, so the
-- rename is guarded on the old column still being there and the new one not
-- -- a plain ALTER ... RENAME would raise on the second boot.

BEGIN;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'project_documents' AND column_name = 'slug'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'project_documents' AND column_name = 'path'
    ) THEN
        ALTER TABLE project_documents RENAME COLUMN slug TO path;
    END IF;
END $$;

-- Existing rows carry a flat slug ("auth"); every document in this table is
-- markdown (body TEXT rendered by MarkdownLite), so give them the extension
-- that makes the tree read like a repository. Guarded on "has no dot in the
-- final segment" so it cannot double-append on a re-run, and scoped to rows
-- with no slash so it can never rewrite a path somebody has already set.
UPDATE project_documents
   SET path = path || '.md'
 WHERE path NOT LIKE '%.%'
   AND path NOT LIKE '%/%';

COMMIT;

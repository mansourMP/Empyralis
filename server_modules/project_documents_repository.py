"""Project documents: the storage layer for a project's owned markdown
knowledge -- the compounding asset behind the "owned-context layer for a
team, with execution attached" positioning (CLAUDE.md).

NO RAG, NO EMBEDDINGS, NO VECTOR STORE. This is a deliberate, already-made
decision, not one this module relitigates: Anthropic's own Claude Code team
removed RAG in favour of agentic search ("Early versions of Claude Code
used RAG + a local vector db, but we found pretty quickly that agentic
search generally works better" -- Boris Cherny), and Anthropic's Managed
Agents memory stores are plain text files an agent reads with normal file
tools. An agent finds a document here by LISTING a project's set (this
module's own list_documents) and reading the ones it needs, the same way it
would `ls` and `cat` a folder -- never through a retrieval pipeline. Nothing
in this file ranks, embeds, or scores document content.

SCOPE: project, not per-agent. Every agent and every human member of a
project shares the same document set -- CLAUDE.md's "Projects hold members
directly," the same collaboration boundary project_tasks_service.py and
projects_repository.py already enforce for tasks and membership.

STORAGE: the markdown body lives IN POSTGRES (the `body` column), not on
disk. Two existing on-disk patterns were weighed and rejected for this
table specifically:
  - agent_memory.py stores memory as files under .orion-stack/memory/ on
    local disk -- an audit flagged this as not durable across machines and
    not in the database of record.
  - knowledge_sources keeps metadata in Postgres but the raw markdown on
    disk under .orion-stack/workspace/... -- the same durability gap for
    the part that actually matters (the content itself).
A project document is a small markdown text blob, not a binary asset --
Postgres already gives every other control-plane row here one database of
record, RLS-scoped isolation, and survival of a machine change with no
separate backup/sync story. There is no reason a document should be the one
control-plane entity that regresses to a local file.

SCHEMA: FLAT, NOT HIERARCHICAL. No folder/path tree -- a project's documents
are a flat list distinguished by `title` and a per-project-unique `slug`.
"A surface must earn its place" (CLAUDE.md) applies to schema too: nothing
today asks for nested folders, and a flat list is the smaller thing that
can always grow a `parent_document_id` self-reference later, the same way
project_tasks added one-level sub-tasks onto an already-shipped flat table
(migrations/add_task_parent.sql) rather than paying for a tree model
upfront.

REVISIONS ARE NOT BUILT HERE. `id` is stable and never reused or recreated
by an edit (update_document is a plain in-place UPDATE, never a
delete+reinsert), which is exactly the seam a later
`project_document_revisions(document_id REFERENCES project_documents(id)
...)` table needs to hang snapshots or patches off of. The founder's stated
future direction is diff/patch-native edits for agents, not whole-blob
replace -- this pass does not attempt that; it only avoids doing anything
that would make it harder to retrofit.

RLS. `project_documents` is RLS-FORCEd on (tenant_id, workspace_id) from
the migration that creates it (migrations/add_project_documents.sql +
migrations/enable_rls.sql) -- every function below routes through
control_plane_repository.rls_fetch/rls_fetchrow/rls_execute, never a bare
pool call, matching task_notifications' "written correctly from day one,
no legacy call site to sequence around" posture rather than project_tasks'
original retrofit.

Postgres-first, following projects_repository.py's own convention: when
Postgres is unavailable these functions return empty/None rather than
falling back to SQLite (documents are a control-plane concept, same era as
Projects and project_memberships).
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any, Dict, List, Optional

from server_modules import control_plane_repository

LOGGER = logging.getLogger(__name__)

_DOCUMENT_COLUMNS = (
    "id, tenant_id, workspace_id, project_id, title, slug, body, "
    "created_by, updated_by, metadata, created_at, updated_at"
)


def _new_document_id() -> str:
    return f"doc_{uuid.uuid4().hex[:16]}"


def _slugify(value: Any, *, fallback: str = "document") -> str:
    text = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")
    return text or fallback


def slugify_title(title: Any) -> str:
    """Public wrapper around `_slugify`, exposed so a caller can predict the
    slug `create_document` would derive from a title WITHOUT creating a row --
    e.g. skills_service.py's document__write dispatch uses this to check
    "does a document at this slug already exist" up front and fail with a
    clear message, rather than letting create_document's own _unique_slug
    silently disambiguate into 'title-2'. That silent-suffix behavior is
    correct for the human/UI "New document" flow (always wants a fresh row);
    it is the wrong behavior for an agent tool, where a title collision
    usually means the agent should have called document__edit instead of
    minting a near-duplicate."""
    return _slugify(title, fallback="document")


def _coerce_metadata(value: Any) -> Dict[str, Any]:
    """Postgres JSONB sometimes arrives already-decoded (dict) and sometimes
    as a raw JSON string, depending on the pool's codec setup -- same
    footgun projects_repository._coerce_metadata guards against. Handle
    both."""
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _row_to_document(row: Any, *, include_body: bool = True) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    r = dict(row)
    doc: Dict[str, Any] = {
        "id": str(r.get("id") or "").strip(),
        "tenant_id": str(r.get("tenant_id") or "").strip() or None,
        "workspace_id": str(r.get("workspace_id") or "").strip() or None,
        "project_id": str(r.get("project_id") or "").strip() or None,
        "title": str(r.get("title") or "").strip(),
        "slug": str(r.get("slug") or "").strip(),
        "created_by": str(r.get("created_by") or "").strip() or None,
        "updated_by": str(r.get("updated_by") or "").strip() or None,
        "metadata": _coerce_metadata(r.get("metadata")),
        "created_at": str(r.get("created_at") or "") or None,
        "updated_at": str(r.get("updated_at") or "") or None,
    }
    if include_body:
        doc["body"] = str(r.get("body") or "")
    return doc


async def _unique_slug(pool: Any, *, tenant_id: str, workspace_id: str, project_id: str, base: str) -> str:
    """Return a slug unique within this PROJECT (not the whole workspace --
    a document is addressed within its project, mirroring projects_
    repository._unique_slug's own per-scope suffixing but one level
    narrower), suffixing -2, -3, ... on clash."""
    rows = await control_plane_repository.rls_fetch(
        pool,
        "SELECT slug FROM project_documents WHERE tenant_id = $1 AND workspace_id = $2 AND project_id = $3",
        tenant_id,
        workspace_id,
        project_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    existing = {str(r["slug"] or "").strip() for r in (rows or [])}
    if base not in existing:
        return base
    n = 2
    while f"{base}-{n}" in existing:
        n += 1
    return f"{base}-{n}"


async def list_documents(
    *,
    tenant_id: str,
    workspace_id: str,
    project_id: str,
    include_body: bool = False,
) -> List[Dict[str, Any]]:
    """List a project's documents, alphabetically by title -- a
    table-of-contents view, not a feed. `include_body=False` by default: a
    list call is the common "what documents does this project have" read
    (a sidebar, an agent doing an initial scan) and a project can hold many
    documents, so the default response never ships every document's full
    markdown body over the wire just to render a list. Pass
    include_body=True for callers that actually need the content inline."""
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return []
    resolved_tenant_id = str(tenant_id or "").strip()
    resolved_workspace_id = str(workspace_id or "").strip()
    resolved_project_id = str(project_id or "").strip()
    columns = _DOCUMENT_COLUMNS if include_body else (
        "id, tenant_id, workspace_id, project_id, title, slug, "
        "created_by, updated_by, metadata, created_at, updated_at"
    )
    rows = await control_plane_repository.rls_fetch(
        pool,
        f"""
        SELECT {columns}
        FROM project_documents
        WHERE tenant_id = $1 AND workspace_id = $2 AND project_id = $3
        ORDER BY title ASC, created_at ASC
        """,
        resolved_tenant_id,
        resolved_workspace_id,
        resolved_project_id,
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
    )
    return [d for d in (_row_to_document(r, include_body=include_body) for r in rows) if d]


async def get_document(
    *,
    tenant_id: str,
    workspace_id: str,
    document_id: str,
) -> Optional[Dict[str, Any]]:
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return None
    resolved_tenant_id = str(tenant_id or "").strip()
    resolved_workspace_id = str(workspace_id or "").strip()
    row = await control_plane_repository.rls_fetchrow(
        pool,
        f"""
        SELECT {_DOCUMENT_COLUMNS}
        FROM project_documents
        WHERE tenant_id = $1 AND workspace_id = $2 AND id = $3
        """,
        resolved_tenant_id,
        resolved_workspace_id,
        str(document_id or "").strip(),
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
    )
    return _row_to_document(row)


async def get_document_by_slug(
    *,
    tenant_id: str,
    workspace_id: str,
    project_id: str,
    slug: str,
) -> Optional[Dict[str, Any]]:
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return None
    resolved_tenant_id = str(tenant_id or "").strip()
    resolved_workspace_id = str(workspace_id or "").strip()
    row = await control_plane_repository.rls_fetchrow(
        pool,
        f"""
        SELECT {_DOCUMENT_COLUMNS}
        FROM project_documents
        WHERE tenant_id = $1 AND workspace_id = $2 AND project_id = $3 AND slug = $4
        """,
        resolved_tenant_id,
        resolved_workspace_id,
        str(project_id or "").strip(),
        str(slug or "").strip(),
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
    )
    return _row_to_document(row)


async def create_document(
    *,
    tenant_id: str,
    workspace_id: str,
    project_id: str,
    title: str,
    body: str = "",
    slug: Optional[str] = None,
    document_id: Optional[str] = None,
    created_by: Optional[str] = None,
) -> Dict[str, Any]:
    tenant_id = str(tenant_id or "").strip()
    workspace_id = str(workspace_id or "").strip()
    project_id = str(project_id or "").strip()
    title = str(title or "").strip()
    if not tenant_id or not workspace_id:
        raise ValueError("tenant_id and workspace_id are required to create a document.")
    if not project_id:
        raise ValueError("project_id is required to create a document.")
    if not title:
        raise ValueError("Document title is required.")
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        raise control_plane_repository.runtime_db.DurableRuntimeConfigurationError(
            "Postgres is required to create a document."
        )
    base_slug = _slugify(slug or title, fallback="document")
    final_slug = await _unique_slug(
        pool, tenant_id=tenant_id, workspace_id=workspace_id, project_id=project_id, base=base_slug,
    )
    did = str(document_id or "").strip() or _new_document_id()
    clean_created_by = str(created_by or "").strip() or None
    row = await control_plane_repository.rls_fetchrow(
        pool,
        f"""
        INSERT INTO project_documents (id, tenant_id, workspace_id, project_id, title, slug, body, created_by, updated_by)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $8)
        RETURNING {_DOCUMENT_COLUMNS}
        """,
        did,
        tenant_id,
        workspace_id,
        project_id,
        title,
        final_slug,
        str(body or ""),
        clean_created_by,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    document = _row_to_document(row)
    if document is None:
        raise RuntimeError("Document insert did not return a row.")
    return document


async def update_document(
    *,
    tenant_id: str,
    workspace_id: str,
    document_id: str,
    title: Optional[str] = None,
    body: Optional[str] = None,
    updated_by: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Edit a document's title and/or body. Both fields are optional and
    independently patchable -- omitting one leaves it untouched (COALESCE),
    the same partial-update posture projects_repository.rename_project
    takes for name/description. `slug` is intentionally NOT editable here:
    it is the document's stable address (a later revisions table, and any
    external link/deep-link into a project's documents, should be able to
    rely on it not moving under a rename) -- renaming the visible `title`
    never reslugs the row. Returns None when the document does not resolve
    in this tenant/workspace (not found, or belongs to someone else)."""
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return None
    resolved_tenant_id = str(tenant_id or "").strip()
    resolved_workspace_id = str(workspace_id or "").strip()
    resolved_updated_by = str(updated_by or "").strip() or None
    row = await control_plane_repository.rls_fetchrow(
        pool,
        f"""
        UPDATE project_documents
        SET title = COALESCE(NULLIF($4, ''), title),
            body = COALESCE($5, body),
            updated_by = COALESCE($6, updated_by),
            updated_at = NOW()
        WHERE tenant_id = $1 AND workspace_id = $2 AND id = $3
        RETURNING {_DOCUMENT_COLUMNS}
        """,
        resolved_tenant_id,
        resolved_workspace_id,
        str(document_id or "").strip(),
        None if title is None else str(title).strip(),
        body,
        resolved_updated_by,
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
    )
    return _row_to_document(row)


async def delete_document(
    *,
    tenant_id: str,
    workspace_id: str,
    document_id: str,
) -> bool:
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return False
    resolved_tenant_id = str(tenant_id or "").strip()
    resolved_workspace_id = str(workspace_id or "").strip()
    result = await control_plane_repository.rls_execute(
        pool,
        """
        DELETE FROM project_documents
        WHERE tenant_id = $1 AND workspace_id = $2 AND id = $3
        """,
        resolved_tenant_id,
        resolved_workspace_id,
        str(document_id or "").strip(),
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
    )
    return str(result or "").strip().endswith(" 1")

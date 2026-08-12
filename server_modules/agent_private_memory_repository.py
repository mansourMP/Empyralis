"""Agent private memory: the storage layer for the PER-PERSON half of the
shared-vs-private memory split (CLAUDE.md: "sessions/threads are private to
the person (always, no setting); the agent (name, config, memory, task
history) is shared with the project"). See migrations/
add_agent_private_memory.sql for the full schema rationale.

WHAT THIS IS NOT: this is not a replacement for memory_service.py /
agent_memory.py, which stays the SHARED pool -- MEMORY.md, memory/*.md topic
files, daily logs, the memory_entries key/value store -- scoped by
(workspace_id, agent_install_id) and correctly readable/writable by every
project member, because that is what "the agent is shared with the project"
means. This module holds the one thing that pool structurally cannot: a
slice of memory that belongs to exactly one person and must never be
readable by, or shaped by, anyone else's turns.

SCOPE: (tenant_id, workspace_id, agent_install_id, user_id). Every public
function below takes all four as REQUIRED keywords with NO DEFAULT and
raises ValueError on a blank one -- "a scope column with a default is a
loaded gun" (CLAUDE.md), applied here to user_id exactly as it already is
to workspace_id elsewhere in this codebase. This is the actual isolation
guarantee: RLS (migrations/enable_rls.sql) only enforces the standard
two-column (tenant_id, workspace_id) scope_match every other table here
uses -- there is no third-column variant, and the finer per-person boundary
is this module's job, the same split CLAUDE.md documents for
vault_credentials and the run_state_repository tables (RLS is the
tenant/workspace backstop; application code owns the axis RLS's own
function cannot express). Every query below binds user_id explicitly in its
WHERE clause -- never inferred, never optional, never a caller-supplied
override of "whose row this is."

ONE ROW PER PERSON PER AGENT, upsert-in-place (Decision B, the same posture
agent_memory.py's memory_entries already uses for its own key/value facts,
applied here at table granularity): a private preference note is a single
small, evolving blob, not a set of named entries. `id` is stable across
updates so agent_private_memory_note_revisions can hang a snapshot off it,
mirroring project_documents_repository.py's own revision-history seam.

Postgres-first, following project_documents_repository.py's own convention:
when Postgres is unavailable, reads return None/[] rather than falling back
to SQLite or local disk (a private note is a control-plane concept, not a
local cache) -- writes raise, because "your preference was saved" must
never be told to a caller when nothing was actually written.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from server_modules import control_plane_repository

_NOTE_COLUMNS = (
    "id, tenant_id, workspace_id, agent_install_id, user_id, content, created_at, updated_at"
)


def _new_note_id() -> str:
    return f"privmem_{uuid.uuid4().hex[:16]}"


def _new_revision_id() -> str:
    return f"privmemrev_{uuid.uuid4().hex[:16]}"


def _require_scope(value: Any, label: str) -> str:
    """Every scope argument below (tenant_id, workspace_id, agent_install_id,
    user_id) goes through this -- a blank value fails LOUDLY here rather
    than silently resolving to "no filter" (the exact fail-open shape
    CLAUDE.md's `$n = '' OR` rule bans for tenant/workspace filters
    elsewhere; user_id gets the identical treatment)."""
    token = str(value or "").strip()
    if not token:
        raise ValueError(
            f"{label} is required for private memory access -- refusing to "
            "read or write with an unscoped identity."
        )
    return token


def _row_to_note(row: Any) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    r = dict(row)
    return {
        "id": str(r.get("id") or "").strip(),
        "agent_install_id": str(r.get("agent_install_id") or "").strip(),
        "user_id": str(r.get("user_id") or "").strip(),
        "content": str(r.get("content") or ""),
        "created_at": str(r.get("created_at") or "") or None,
        "updated_at": str(r.get("updated_at") or "") or None,
    }


async def get_private_note(
    *,
    tenant_id: str,
    workspace_id: str,
    agent_install_id: str,
    user_id: str,
) -> Optional[Dict[str, Any]]:
    """Read THIS user's private note for THIS agent -- and only this user's.
    The WHERE clause binds tenant_id/workspace_id (what RLS's
    empyralis_rls_scope_match also checks -- belt AND suspenders, not
    either/or) AND agent_install_id AND user_id, all four required, in the
    SAME query. There is no code path in this function that can return a
    row belonging to a different user_id than the one passed in."""
    tenant_id = _require_scope(tenant_id, "tenant_id")
    workspace_id = _require_scope(workspace_id, "workspace_id")
    agent_install_id = _require_scope(agent_install_id, "agent_install_id")
    user_id = _require_scope(user_id, "user_id")
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return None
    row = await control_plane_repository.rls_fetchrow(
        pool,
        f"""
        SELECT {_NOTE_COLUMNS} FROM agent_private_memory_notes
        WHERE tenant_id = $1 AND workspace_id = $2
          AND agent_install_id = $3 AND user_id = $4
        """,
        tenant_id,
        workspace_id,
        agent_install_id,
        user_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    return _row_to_note(row)


async def upsert_private_note(
    *,
    tenant_id: str,
    workspace_id: str,
    agent_install_id: str,
    user_id: str,
    content: str,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    """Write (create or replace) THIS user's private note. The UNIQUE
    (tenant_id, workspace_id, agent_install_id, user_id) constraint on
    agent_private_memory_notes is what makes this an upsert rather than a
    second row per call -- ON CONFLICT targets exactly that tuple, so a
    second write from the SAME user updates their one row; a write from a
    DIFFERENT user_id can never collide with it and always creates that
    user's own separate row.

    Fail-open on the revision write only (mirrors project_documents_
    repository.create_document's own posture): the primary note write
    already committed by the time the revision insert runs, so a revisions-
    table hiccup is reported on the returned dict rather than raised, which
    would otherwise tell the caller "your write failed" when it didn't.
    """
    tenant_id = _require_scope(tenant_id, "tenant_id")
    workspace_id = _require_scope(workspace_id, "workspace_id")
    agent_install_id = _require_scope(agent_install_id, "agent_install_id")
    user_id = _require_scope(user_id, "user_id")
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        raise control_plane_repository.runtime_db.DurableRuntimeConfigurationError(
            "Postgres is required to write private memory."
        )
    row = await control_plane_repository.rls_fetchrow(
        pool,
        f"""
        INSERT INTO agent_private_memory_notes
            (id, tenant_id, workspace_id, agent_install_id, user_id, content)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (tenant_id, workspace_id, agent_install_id, user_id)
        DO UPDATE SET content = EXCLUDED.content, updated_at = NOW()
        RETURNING {_NOTE_COLUMNS}
        """,
        _new_note_id(),
        tenant_id,
        workspace_id,
        agent_install_id,
        user_id,
        str(content or ""),
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    note = _row_to_note(row)
    if note is None:
        raise RuntimeError("Private memory note upsert did not return a row.")
    note["revision_recorded"] = True
    try:
        await control_plane_repository.rls_execute(
            pool,
            """
            INSERT INTO agent_private_memory_note_revisions (
                id, tenant_id, workspace_id, note_id, agent_install_id, user_id,
                content, reason, revision_number
            )
            SELECT $1, $2, $3, $4, $5, $6, $7, $8,
                   COALESCE(MAX(revision_number), 0) + 1
            FROM agent_private_memory_note_revisions
            WHERE tenant_id = $2 AND workspace_id = $3 AND note_id = $4
            """,
            _new_revision_id(),
            tenant_id,
            workspace_id,
            note["id"],
            agent_install_id,
            user_id,
            str(content or ""),
            str(reason or "").strip() or None,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )
    except Exception as exc:  # noqa: BLE001 -- never let history recording undo a real write
        note["revision_recorded"] = False
        note["revision_error"] = str(exc)
    return note


async def list_private_note_revisions(
    *,
    tenant_id: str,
    workspace_id: str,
    agent_install_id: str,
    user_id: str,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """Newest-first revision history for THIS user's note only -- same
    four-way required scope as get_private_note/upsert_private_note."""
    tenant_id = _require_scope(tenant_id, "tenant_id")
    workspace_id = _require_scope(workspace_id, "workspace_id")
    agent_install_id = _require_scope(agent_install_id, "agent_install_id")
    user_id = _require_scope(user_id, "user_id")
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return []
    bounded_limit = max(1, min(int(limit or 20), 100))
    rows = await control_plane_repository.rls_fetch(
        pool,
        """
        SELECT id, note_id, content, reason, revision_number, created_at
        FROM agent_private_memory_note_revisions
        WHERE tenant_id = $1 AND workspace_id = $2
          AND agent_install_id = $3 AND user_id = $4
        ORDER BY revision_number DESC
        LIMIT $5
        """,
        tenant_id,
        workspace_id,
        agent_install_id,
        user_id,
        bounded_limit,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    return [dict(r) for r in (rows or [])]

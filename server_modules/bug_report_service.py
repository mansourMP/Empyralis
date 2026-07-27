"""Bug reports (MAN-106): backing store for the small "report an issue"
entry point in the fleet rail (frontend/lib/workspace/fleet/
BugReportButton.tsx).

Deliberately tiny -- this is "let someone report a bug," not a support-
ticketing system. Two entry points: create_report (the POST the dialog
submits to) and list_reports (so an operator, or a future ops view, can
actually read what came in). Follows the same direct-pool access pattern as
project_tasks_service.py: plain pool.fetch/fetchrow/execute with explicit
tenant_id/workspace_id WHERE filters -- `bug_reports` carries no RLS policy,
exactly like `projects`/`project_tasks` (see migrations/add_bug_reports.sql).
Postgres-first -- when Postgres is unavailable, create_report raises (the
report must not silently vanish) and list_reports returns [] (a read-only
view degrading to "nothing to show" is honest; a write pretending to have
persisted a row would not be).
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional

from server_modules import control_plane_repository

MAX_TITLE_LENGTH = 200
MAX_DESCRIPTION_LENGTH = 5000
MAX_PAGE_PATH_LENGTH = 500
MAX_USER_AGENT_LENGTH = 500


def _new_report_id() -> str:
    return f"bugreport_{uuid.uuid4().hex[:16]}"


def _coerce_metadata(value: Any) -> Dict[str, Any]:
    """Postgres JSONB sometimes arrives already-decoded (dict) and sometimes
    as a raw JSON string, depending on the pool's codec setup -- see
    projects_repository._coerce_metadata for the same footgun."""
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _row_to_report(row: Any) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    r = dict(row)
    return {
        "id": str(r.get("id") or "").strip(),
        "tenant_id": str(r.get("tenant_id") or "").strip() or None,
        "workspace_id": str(r.get("workspace_id") or "").strip() or None,
        "reported_by_user_id": str(r.get("reported_by_user_id") or "").strip() or None,
        "title": str(r.get("title") or "").strip(),
        "description": str(r.get("description") or "").strip(),
        "page_path": str(r.get("page_path") or "").strip(),
        "user_agent": str(r.get("user_agent") or "").strip(),
        "status": str(r.get("status") or "").strip() or "new",
        "metadata": _coerce_metadata(r.get("metadata")),
        "created_at": str(r.get("created_at") or "") or None,
    }


async def create_report(
    *,
    tenant_id: str,
    workspace_id: str,
    title: str,
    description: str = "",
    reported_by_user_id: Optional[str] = None,
    page_path: str = "",
    user_agent: str = "",
    metadata: Optional[Dict[str, Any]] = None,
    report_id: Optional[str] = None,
) -> Dict[str, Any]:
    tenant_id = str(tenant_id or "").strip()
    workspace_id = str(workspace_id or "").strip()
    title = str(title or "").strip()[:MAX_TITLE_LENGTH]
    if not tenant_id or not workspace_id:
        raise ValueError("tenant_id and workspace_id are required to create a bug report.")
    if not title:
        raise ValueError("A short title describing the issue is required.")
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        raise control_plane_repository.runtime_db.DurableRuntimeConfigurationError(
            "Postgres is required to save a bug report."
        )
    rid = str(report_id or "").strip() or _new_report_id()
    row = await pool.fetchrow(
        """
        INSERT INTO bug_reports
            (id, tenant_id, workspace_id, reported_by_user_id, title, description,
             page_path, user_agent, metadata)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb)
        RETURNING id, tenant_id, workspace_id, reported_by_user_id, title, description,
                  page_path, user_agent, status, metadata, created_at
        """,
        rid,
        tenant_id,
        workspace_id,
        str(reported_by_user_id or "").strip() or None,
        title,
        str(description or "").strip()[:MAX_DESCRIPTION_LENGTH],
        str(page_path or "").strip()[:MAX_PAGE_PATH_LENGTH],
        str(user_agent or "").strip()[:MAX_USER_AGENT_LENGTH],
        json.dumps(dict(metadata or {})),
    )
    report = _row_to_report(row)
    if report is None:
        raise RuntimeError("Bug report insert did not return a row.")
    return report


async def list_reports(
    *,
    tenant_id: str,
    workspace_id: str,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return []
    capped_limit = max(1, min(int(limit or 100), 500))
    rows = await pool.fetch(
        """
        SELECT id, tenant_id, workspace_id, reported_by_user_id, title, description,
               page_path, user_agent, status, metadata, created_at
        FROM bug_reports
        WHERE tenant_id = $1 AND workspace_id = $2
        ORDER BY created_at DESC
        LIMIT $3
        """,
        str(tenant_id or "").strip(),
        str(workspace_id or "").strip(),
        capped_limit,
    )
    return [r for r in (_row_to_report(row) for row in rows) if r]

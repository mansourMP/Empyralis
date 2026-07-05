"""Phase 2: Projects repository.

A Project is a client/company/purpose grouping of agents. The hierarchy is
Workspace > Projects > Agents. Every agent install belongs to exactly one
project; each workspace has a default ("General") project for ungrouped agents.

Follows the same direct-pool access pattern as agent_registry_repository:
plain pool.fetch/execute with explicit tenant_id/workspace_id WHERE filters.
Postgres-first — when Postgres is unavailable these functions return empty /
None rather than falling back to SQLite (projects are a control-plane concept
introduced in the Postgres-first era).
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Dict, List, Optional

from server_modules import control_plane_repository

DEFAULT_PROJECT_NAME = "General"
DEFAULT_PROJECT_SLUG = "general"


def _slugify(value: Any, *, fallback: str = "project") -> str:
    text = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")
    return text or fallback


def _new_project_id() -> str:
    return f"project_{uuid.uuid4().hex[:16]}"


def _row_to_project(row: Any) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    r = dict(row)
    return {
        "id": str(r.get("id") or "").strip(),
        "tenant_id": str(r.get("tenant_id") or "").strip() or None,
        "workspace_id": str(r.get("workspace_id") or "").strip() or None,
        "name": str(r.get("name") or "").strip(),
        "slug": str(r.get("slug") or "").strip(),
        "description": str(r.get("description") or "").strip(),
        "is_default": bool(r.get("is_default")),
        "archived": bool(r.get("archived")),
        "created_at": str(r.get("created_at") or "") or None,
        "updated_at": str(r.get("updated_at") or "") or None,
    }


async def _unique_slug(pool: Any, tenant_id: str, workspace_id: str, base: str) -> str:
    """Return a slug unique within (tenant, workspace), suffixing -2, -3, ... on clash."""
    rows = await pool.fetch(
        "SELECT slug FROM projects WHERE tenant_id = $1 AND workspace_id = $2",
        tenant_id,
        workspace_id,
    )
    existing = {str(r["slug"] or "").strip() for r in (rows or [])}
    if base not in existing:
        return base
    n = 2
    while f"{base}-{n}" in existing:
        n += 1
    return f"{base}-{n}"


async def list_projects(
    *,
    tenant_id: str,
    workspace_id: str,
    include_archived: bool = False,
) -> List[Dict[str, Any]]:
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return []
    rows = await pool.fetch(
        """
        SELECT id, tenant_id, workspace_id, name, slug, description,
               is_default, archived, created_at, updated_at
        FROM projects
        WHERE tenant_id = $1
          AND workspace_id = $2
          AND ($3::bool OR archived = FALSE)
        ORDER BY is_default DESC, name ASC, created_at ASC
        """,
        str(tenant_id or "").strip(),
        str(workspace_id or "").strip(),
        bool(include_archived),
    )
    return [p for p in (_row_to_project(r) for r in rows) if p]


async def get_project(
    *,
    tenant_id: str,
    workspace_id: str,
    project_id: str,
) -> Optional[Dict[str, Any]]:
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return None
    row = await pool.fetchrow(
        """
        SELECT id, tenant_id, workspace_id, name, slug, description,
               is_default, archived, created_at, updated_at
        FROM projects
        WHERE tenant_id = $1 AND workspace_id = $2 AND id = $3
        """,
        str(tenant_id or "").strip(),
        str(workspace_id or "").strip(),
        str(project_id or "").strip(),
    )
    return _row_to_project(row)


async def create_project(
    *,
    tenant_id: str,
    workspace_id: str,
    name: str,
    description: str = "",
    slug: Optional[str] = None,
    is_default: bool = False,
    project_id: Optional[str] = None,
) -> Dict[str, Any]:
    tenant_id = str(tenant_id or "").strip()
    workspace_id = str(workspace_id or "").strip()
    name = str(name or "").strip()
    if not tenant_id or not workspace_id:
        raise ValueError("tenant_id and workspace_id are required to create a project.")
    if not name:
        raise ValueError("Project name is required.")
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        raise control_plane_repository.runtime_db.DurableRuntimeConfigurationError(
            "Postgres is required to create a project."
        )
    base_slug = _slugify(slug or name, fallback="project")
    final_slug = await _unique_slug(pool, tenant_id, workspace_id, base_slug)
    pid = str(project_id or "").strip() or _new_project_id()
    row = await pool.fetchrow(
        """
        INSERT INTO projects (id, tenant_id, workspace_id, name, slug, description, is_default)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        RETURNING id, tenant_id, workspace_id, name, slug, description,
                  is_default, archived, created_at, updated_at
        """,
        pid,
        tenant_id,
        workspace_id,
        name,
        final_slug,
        str(description or "").strip(),
        bool(is_default),
    )
    return _row_to_project(row)


async def ensure_default_project(
    *,
    tenant_id: str,
    workspace_id: str,
) -> Dict[str, Any]:
    """Get the workspace's default project, creating a 'General' one if absent."""
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        raise control_plane_repository.runtime_db.DurableRuntimeConfigurationError(
            "Postgres is required for the default project."
        )
    tenant_id = str(tenant_id or "").strip()
    workspace_id = str(workspace_id or "").strip()
    row = await pool.fetchrow(
        """
        SELECT id, tenant_id, workspace_id, name, slug, description,
               is_default, archived, created_at, updated_at
        FROM projects
        WHERE tenant_id = $1 AND workspace_id = $2 AND is_default = TRUE
        ORDER BY created_at ASC
        LIMIT 1
        """,
        tenant_id,
        workspace_id,
    )
    if row is not None:
        return _row_to_project(row)
    return await create_project(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        name=DEFAULT_PROJECT_NAME,
        slug=DEFAULT_PROJECT_SLUG,
        is_default=True,
    )


async def rename_project(
    *,
    tenant_id: str,
    workspace_id: str,
    project_id: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return None
    row = await pool.fetchrow(
        """
        UPDATE projects
        SET name = COALESCE(NULLIF($4, ''), name),
            description = COALESCE($5, description),
            updated_at = NOW()
        WHERE tenant_id = $1 AND workspace_id = $2 AND id = $3
        RETURNING id, tenant_id, workspace_id, name, slug, description,
                  is_default, archived, created_at, updated_at
        """,
        str(tenant_id or "").strip(),
        str(workspace_id or "").strip(),
        str(project_id or "").strip(),
        str(name or "").strip(),
        None if description is None else str(description).strip(),
    )
    return _row_to_project(row)


async def set_project_archived(
    *,
    tenant_id: str,
    workspace_id: str,
    project_id: str,
    archived: bool = True,
) -> Optional[Dict[str, Any]]:
    """Archive/unarchive a project. The default project cannot be archived —
    a workspace must always have a home for ungrouped agents."""
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return None
    tenant_id = str(tenant_id or "").strip()
    workspace_id = str(workspace_id or "").strip()
    project_id = str(project_id or "").strip()
    if archived:
        target = await get_project(tenant_id=tenant_id, workspace_id=workspace_id, project_id=project_id)
        if target is None:
            return None
        if target.get("is_default"):
            raise ValueError("The default project cannot be archived.")
        # Reassign this project's agents back to the default project.
        default_project = await ensure_default_project(tenant_id=tenant_id, workspace_id=workspace_id)
        await pool.execute(
            """
            UPDATE workspace_agent_installs
            SET project_id = $4, updated_at = NOW()
            WHERE tenant_id = $1 AND workspace_id = $2 AND project_id = $3
            """,
            tenant_id,
            workspace_id,
            project_id,
            default_project["id"],
        )
    row = await pool.fetchrow(
        """
        UPDATE projects
        SET archived = $4, updated_at = NOW()
        WHERE tenant_id = $1 AND workspace_id = $2 AND id = $3
        RETURNING id, tenant_id, workspace_id, name, slug, description,
                  is_default, archived, created_at, updated_at
        """,
        tenant_id,
        workspace_id,
        project_id,
        bool(archived),
    )
    return _row_to_project(row)


async def assign_install_to_project(
    *,
    tenant_id: str,
    workspace_id: str,
    install_id: str,
    project_id: str,
) -> bool:
    """Move an agent install into a project. Validates the project belongs to
    the same workspace."""
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return False
    tenant_id = str(tenant_id or "").strip()
    workspace_id = str(workspace_id or "").strip()
    target = await get_project(tenant_id=tenant_id, workspace_id=workspace_id, project_id=project_id)
    if target is None:
        raise ValueError("Project not found in this workspace.")
    result = await pool.execute(
        """
        UPDATE workspace_agent_installs
        SET project_id = $4, updated_at = NOW()
        WHERE tenant_id = $1 AND workspace_id = $2 AND id = $3
        """,
        tenant_id,
        workspace_id,
        str(install_id or "").strip(),
        str(project_id or "").strip(),
    )
    return str(result or "").endswith("1")


async def count_agents_by_project(
    *,
    tenant_id: str,
    workspace_id: str,
) -> Dict[str, int]:
    """Return {project_id: agent_count} for the workspace (installs only)."""
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return {}
    rows = await pool.fetch(
        """
        SELECT project_id, COUNT(*) AS n
        FROM workspace_agent_installs
        WHERE tenant_id = $1 AND workspace_id = $2 AND project_id IS NOT NULL
        GROUP BY project_id
        """,
        str(tenant_id or "").strip(),
        str(workspace_id or "").strip(),
    )
    return {str(r["project_id"]): int(r["n"]) for r in (rows or [])}

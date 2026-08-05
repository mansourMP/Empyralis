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

import hashlib
import json
import re
import uuid
from typing import Any, Dict, List, Optional

from server_modules import control_plane_repository

DEFAULT_PROJECT_NAME = "General"
DEFAULT_PROJECT_SLUG = "general"

# ── Phase U3-B: project identity (icon + tint) ──────────────────────────────
# A curated set, not "any lucide icon" — keeps every project visually
# distinct without turning the icon into a second, uncontrolled naming
# surface. Names are lucide-react component names in kebab-case; the
# frontend maps these 1:1 to imports. Assigned once at creation from a
# deterministic hash of the project id (stable forever), stored in
# `metadata` so an owner can override it later without a migration.
PROJECT_ICONS = [
    "rocket", "target", "compass", "flag", "star", "zap", "package", "briefcase",
    "layers", "box", "puzzle", "shield", "gem", "anchor", "globe", "flame",
]
PROJECT_TINTS = ["blue", "purple", "amber", "teal", "coral", "rose", "sky", "lime"]

# "General" is every workspace's own default project — it should look the
# same everywhere, not randomized by its (otherwise arbitrary) generated id.
DEFAULT_PROJECT_ICON = "folder-kanban"
DEFAULT_PROJECT_TINT = "blue"


def _deterministic_project_identity(project_id: str) -> Dict[str, str]:
    digest = hashlib.md5(str(project_id or "").encode("utf-8")).hexdigest()
    icon = PROJECT_ICONS[int(digest[:8], 16) % len(PROJECT_ICONS)]
    tint = PROJECT_TINTS[int(digest[8:16], 16) % len(PROJECT_TINTS)]
    return {"icon": icon, "tint": tint}


def _coerce_metadata(value: Any) -> Dict[str, Any]:
    """Postgres JSONB sometimes arrives already-decoded (dict) and sometimes
    as a raw JSON string, depending on the pool's codec setup — see the
    2026-07-09 wake-request cancel fix for the same footgun. Handle both."""
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _slugify(value: Any, *, fallback: str = "project") -> str:
    text = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")
    return text or fallback


def _new_project_id() -> str:
    return f"project_{uuid.uuid4().hex[:16]}"


def _row_to_project(row: Any) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    r = dict(row)
    metadata = _coerce_metadata(r.get("metadata"))
    identity = _deterministic_project_identity(str(r.get("id") or ""))
    return {
        "id": str(r.get("id") or "").strip(),
        "tenant_id": str(r.get("tenant_id") or "").strip() or None,
        "workspace_id": str(r.get("workspace_id") or "").strip() or None,
        "name": str(r.get("name") or "").strip(),
        "slug": str(r.get("slug") or "").strip(),
        "description": str(r.get("description") or "").strip(),
        "is_default": bool(r.get("is_default")),
        "archived": bool(r.get("archived")),
        "metadata": metadata,
        # Fall back to a live-computed identity for rows written before this
        # field existed (or ever cleared) — never render a project with no
        # icon/tint. "General" always gets the fixed default, not the hash.
        "icon": str(metadata.get("icon") or "").strip() or (
            DEFAULT_PROJECT_ICON if r.get("is_default") else identity["icon"]
        ),
        "tint": str(metadata.get("tint") or "").strip() or (
            DEFAULT_PROJECT_TINT if r.get("is_default") else identity["tint"]
        ),
        # Phase U3-K: the project's default Gateway — the box agents in this
        # project inherit for their brain hardware (cli_subscription/local
        # gateway_binding) and tool dispatch (preferred_gateway_id) when they
        # carry none of their own. See set_project_default_gateway below and
        # specialist_runtime_context.resolve_specialist_runtime_context's
        # fallback. A raw id only — routes_fleet.py resolves it to a
        # human-readable label for the API response (repo layer stays free
        # of the gateway-registry dependency).
        "default_gateway_id": str(metadata.get("default_gateway_id") or "").strip() or None,
        "created_at": str(r.get("created_at") or "") or None,
        "updated_at": str(r.get("updated_at") or "") or None,
    }


async def _unique_slug(pool: Any, tenant_id: str, workspace_id: str, base: str) -> str:
    """Return a slug unique within (tenant, workspace), suffixing -2, -3, ... on clash."""
    rows = await control_plane_repository.rls_fetch(
        pool,
        "SELECT slug FROM projects WHERE tenant_id = $1 AND workspace_id = $2",
        tenant_id,
        workspace_id,
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


async def list_projects(
    *,
    tenant_id: str,
    workspace_id: str,
    include_archived: bool = False,
) -> List[Dict[str, Any]]:
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return []
    resolved_tenant_id = str(tenant_id or "").strip()
    resolved_workspace_id = str(workspace_id or "").strip()
    rows = await control_plane_repository.rls_fetch(
        pool,
        """
        SELECT id, tenant_id, workspace_id, name, slug, description,
               is_default, archived, metadata, created_at, updated_at
        FROM projects
        WHERE tenant_id = $1
          AND workspace_id = $2
          AND ($3::bool OR archived = FALSE)
        ORDER BY is_default DESC, name ASC, created_at ASC
        """,
        resolved_tenant_id,
        resolved_workspace_id,
        bool(include_archived),
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
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
    resolved_tenant_id = str(tenant_id or "").strip()
    resolved_workspace_id = str(workspace_id or "").strip()
    row = await control_plane_repository.rls_fetchrow(
        pool,
        """
        SELECT id, tenant_id, workspace_id, name, slug, description,
               is_default, archived, metadata, created_at, updated_at
        FROM projects
        WHERE tenant_id = $1 AND workspace_id = $2 AND id = $3
        """,
        resolved_tenant_id,
        resolved_workspace_id,
        str(project_id or "").strip(),
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
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
    created_by_user_id: Optional[str] = None,
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
    # Icon + tint assigned once, here, from the new id — never recomputed
    # once stored (a rename must not visually reshuffle the project).
    identity = (
        {"icon": DEFAULT_PROJECT_ICON, "tint": DEFAULT_PROJECT_TINT}
        if is_default
        else _deterministic_project_identity(pid)
    )
    row = await control_plane_repository.rls_fetchrow(
        pool,
        """
        INSERT INTO projects (id, tenant_id, workspace_id, name, slug, description, is_default, metadata)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)
        RETURNING id, tenant_id, workspace_id, name, slug, description,
                  is_default, archived, metadata, created_at, updated_at
        """,
        pid,
        tenant_id,
        workspace_id,
        name,
        final_slug,
        str(description or "").strip(),
        bool(is_default),
        json.dumps(identity),
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    project = _row_to_project(row)
    # MAN-115: the creator gets an explicit project_memberships row so they
    # are never locked out of a project they just made — this matters even
    # though creation is owner-gated today (owners bypass the membership
    # check anyway) because it keeps the roster honest (the creator shows
    # up as a real member, not an invisible bypass) and costs nothing if a
    # future non-owner creation path ever lands. Best-effort: the project
    # itself is already committed by this point, so a membership-insert
    # failure must not undo (or appear to undo) a successful create.
    clean_creator = str(created_by_user_id or "").strip()
    if project and clean_creator:
        try:
            await add_project_member(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                project_id=project["id"],
                user_id=clean_creator,
                role="owner",
                added_by=clean_creator,
                pool=pool,
            )
        except Exception:
            pass
    return project


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
    row = await control_plane_repository.rls_fetchrow(
        pool,
        """
        SELECT id, tenant_id, workspace_id, name, slug, description,
               is_default, archived, metadata, created_at, updated_at
        FROM projects
        WHERE tenant_id = $1 AND workspace_id = $2 AND is_default = TRUE
        ORDER BY created_at ASC
        LIMIT 1
        """,
        tenant_id,
        workspace_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
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
    resolved_tenant_id = str(tenant_id or "").strip()
    resolved_workspace_id = str(workspace_id or "").strip()
    row = await control_plane_repository.rls_fetchrow(
        pool,
        """
        UPDATE projects
        SET name = COALESCE(NULLIF($4, ''), name),
            description = COALESCE($5, description),
            updated_at = NOW()
        WHERE tenant_id = $1 AND workspace_id = $2 AND id = $3
        RETURNING id, tenant_id, workspace_id, name, slug, description,
                  is_default, archived, metadata, created_at, updated_at
        """,
        resolved_tenant_id,
        resolved_workspace_id,
        str(project_id or "").strip(),
        str(name or "").strip(),
        None if description is None else str(description).strip(),
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
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
        await control_plane_repository.rls_execute(
            pool,
            """
            UPDATE workspace_agent_installs
            SET project_id = $4, updated_at = NOW()
            WHERE tenant_id = $1 AND workspace_id = $2 AND project_id = $3
            """,
            tenant_id,
            workspace_id,
            project_id,
            default_project["id"],
            tenant_id=tenant_id, workspace_id=workspace_id,
        )
    row = await control_plane_repository.rls_fetchrow(
        pool,
        """
        UPDATE projects
        SET archived = $4, updated_at = NOW()
        WHERE tenant_id = $1 AND workspace_id = $2 AND id = $3
        RETURNING id, tenant_id, workspace_id, name, slug, description,
                  is_default, archived, metadata, created_at, updated_at
        """,
        tenant_id,
        workspace_id,
        project_id,
        bool(archived),
        tenant_id=tenant_id, workspace_id=workspace_id,
    )
    return _row_to_project(row)


async def set_project_default_gateway(
    *,
    tenant_id: str,
    workspace_id: str,
    project_id: str,
    gateway_id: Optional[str],
) -> Optional[Dict[str, Any]]:
    """Set — or clear, when `gateway_id` is empty/None — this project's
    default Gateway (metadata.default_gateway_id). Projects are already the
    product's collaboration boundary (members live on the project, not a
    Teams layer above it — see CLAUDE.md); this is the project-level
    counterpart to the per-agent `model_config.gateway_binding` /
    `preferred_gateway_id` fields in fleet_tools.fleet_configure_agent,
    letting every agent in a project share its compute by default instead of
    each one needing its own separate pairing. specialist_runtime_context.
    resolve_specialist_runtime_context reads this as a fallback ONLY — an
    agent's own explicit binding always wins when set.

    Validates the id resolves to a real, active Gateway registered to this
    workspace before saving, via fleet_tools.gateway_resolves_in_workspace —
    the exact same rule and error shape fleet_configure_agent already applies
    to the agent-level field, so a project default can never point at a
    Gateway the agent-level check would have rejected. Raises ValueError on
    an unresolvable id (caught by the route the same way every other
    business-logic failure in this file is). An empty string / None clears
    the default without any validation — clearing is always safe.

    Merges into `metadata` via a single atomic jsonb_set/remove — same
    merge-not-replace discipline as project_tasks_service.add_task_comment's
    own jsonb_set — so other keys already living there (icon, tint, ...)
    survive untouched rather than being clobbered by a read-modify-write
    race with a concurrent metadata writer."""
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return None
    resolved_tenant_id = str(tenant_id or "").strip()
    resolved_workspace_id = str(workspace_id or "").strip()
    resolved_project_id = str(project_id or "").strip()
    clean_gateway_id = str(gateway_id or "").strip()

    if clean_gateway_id:
        from server_modules import fleet_tools

        check = fleet_tools.gateway_resolves_in_workspace(clean_gateway_id, resolved_workspace_id)
        if not check.get("ok"):
            raise ValueError(str(check.get("error") or "Gateway does not resolve in this workspace."))

    row = await control_plane_repository.rls_fetchrow(
        pool,
        """
        UPDATE projects
        SET metadata = CASE
                WHEN $4::text = '' THEN (COALESCE(metadata, '{}'::jsonb) - 'default_gateway_id')
                ELSE jsonb_set(COALESCE(metadata, '{}'::jsonb), '{default_gateway_id}', to_jsonb($4::text), true)
            END,
            updated_at = NOW()
        WHERE tenant_id = $1 AND workspace_id = $2 AND id = $3
        RETURNING id, tenant_id, workspace_id, name, slug, description,
                  is_default, archived, metadata, created_at, updated_at
        """,
        resolved_tenant_id,
        resolved_workspace_id,
        resolved_project_id,
        clean_gateway_id,
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
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
    result = await control_plane_repository.rls_execute(
        pool,
        """
        UPDATE workspace_agent_installs
        SET project_id = $4, updated_at = NOW()
        WHERE tenant_id = $1 AND workspace_id = $2 AND id = $3
        """,
        tenant_id,
        workspace_id,
        str(install_id or "").strip(),
        str(project_id or "").strip(),
        tenant_id=tenant_id, workspace_id=workspace_id,
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
    rows = await control_plane_repository.rls_fetch(
        pool,
        """
        SELECT project_id, COUNT(*) AS n
        FROM workspace_agent_installs
        WHERE tenant_id = $1 AND workspace_id = $2 AND project_id IS NOT NULL
        GROUP BY project_id
        """,
        str(tenant_id or "").strip(),
        str(workspace_id or "").strip(),
        tenant_id=tenant_id, workspace_id=workspace_id,
    )
    return {str(r["project_id"]): int(r["n"]) for r in (rows or [])}


# ── MAN-115: real per-project ACL ────────────────────────────────────────
# Follow-up to the MAN-70 placeholder ruling ("project member" == "workspace
# member" for now, no per-project ACL table). This is that table:
# project_memberships (migrations/add_project_memberships.sql). A row means
# "this user can see/act on this project." Workspace OWNERS bypass this
# table entirely — that check lives in auth.enforce_project_access, not
# here; every function below is a plain, unprivileged membership CRUD/read,
# same direct-pool pattern as the rest of this file (Postgres-first, no
# RLS, explicit tenant_id/workspace_id filters on every query).

def _new_membership_id() -> str:
    return f"projmember_{uuid.uuid4().hex[:16]}"


def _row_to_member(row: Any) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    r = dict(row)
    return {
        "id": str(r.get("id") or "").strip(),
        "project_id": str(r.get("project_id") or "").strip(),
        "user_id": str(r.get("user_id") or "").strip(),
        "email": str(r.get("email") or "").strip().lower() or None,
        "display_name": str(r.get("display_name") or "").strip() or None,
        "avatar_url": str(r.get("avatar_url") or "").strip() or None,
        "role": str(r.get("role") or "").strip() or "member",
        "added_by": str(r.get("added_by") or "").strip() or None,
        "created_at": str(r.get("created_at") or "") or None,
    }


async def add_project_member(
    *,
    tenant_id: str,
    workspace_id: str,
    project_id: str,
    user_id: str,
    role: str = "member",
    added_by: Optional[str] = None,
    pool: Any = None,
) -> Dict[str, Any]:
    """Grant a user access to a project. Idempotent — adding an existing
    member updates `role`/`added_by` rather than erroring (ON CONFLICT),
    since re-adding someone who's already there is a no-op from the
    caller's point of view, not a failure. `pool` may be passed by a caller
    that already resolved one (create_project's auto-add-creator path) to
    avoid a second ensure_control_plane_schema() round trip."""
    tenant_id = str(tenant_id or "").strip()
    workspace_id = str(workspace_id or "").strip()
    project_id = str(project_id or "").strip()
    user_id = str(user_id or "").strip()
    if not tenant_id or not workspace_id:
        raise ValueError("tenant_id and workspace_id are required to add a project member.")
    if not project_id:
        raise ValueError("project_id is required to add a project member.")
    if not user_id:
        raise ValueError("user_id is required to add a project member.")
    if pool is None:
        pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        raise control_plane_repository.runtime_db.DurableRuntimeConfigurationError(
            "Postgres is required to add a project member."
        )
    row = await control_plane_repository.rls_fetchrow(
        pool,
        """
        INSERT INTO project_memberships (id, tenant_id, workspace_id, project_id, user_id, role, added_by)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        ON CONFLICT (project_id, user_id) DO UPDATE
            SET role = EXCLUDED.role,
                added_by = COALESCE(EXCLUDED.added_by, project_memberships.added_by)
        RETURNING id, project_id, user_id, role, added_by, created_at
        """,
        _new_membership_id(),
        tenant_id,
        workspace_id,
        project_id,
        user_id,
        str(role or "member").strip().lower() or "member",
        str(added_by or "").strip() or None,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    member = _row_to_member(row)
    if member is None:
        raise RuntimeError("Project membership insert did not return a row.")
    return member


async def grant_invite_project_access(
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    metadata: Optional[Dict[str, Any]],
    added_by: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Grant the project membership a workspace invite's metadata carries, if
    any (MAN-70/MAN-114 follow-up). Before this, accepting a workspace invite
    granted zero project access -- add_project_member's only two callers were
    create_project (auto-adding the creator) and the UI-less
    /fleet/projects/{id}/members grant route -- so an invited teammate saw no
    projects at all until someone separately made them a workspace owner.

    Shared by BOTH acceptance paths (routes_workspaces.
    accept_workspace_invite_route's /join/{token} flow, and
    auth.accept_workspace_invites_for_user's auto-accept-at-login path) so
    the validate-then-grant behavior exists exactly once. That matters here
    specifically: this repo has two independent ways an invite gets accepted,
    and a hand-duplicated version of this logic in both files is exactly how
    a fix like this quietly ends up working through the emailed link while
    still no-op'ing for whoever happens to log in before clicking it.

    Re-validates the project against tenant_id/workspace_id via get_project
    even though create_workspace_invite_route already validated project_id
    at invite-creation time -- defense in depth against the project having
    since been deleted, or a project_id that pointed at a different tenant/
    workspace than the one this invite actually belongs to (get_project's
    WHERE clause is tenant_id AND workspace_id AND id, so a mismatched
    project_id resolves to None here rather than granting access to a
    project in someone else's tenant).

    No-ops (returns None) rather than raising when metadata carries no
    project_id, when the project doesn't resolve in this tenant/workspace,
    or on any unexpected failure -- granting workspace membership must not
    be blocked or rolled back just because a secondary project grant hit a
    problem. Idempotent via add_project_member's own ON CONFLICT handling:
    calling this twice for the same user/project is safe.
    """
    project_id = str((metadata or {}).get("project_id") or "").strip()
    if not project_id:
        return None
    clean_tenant_id = str(tenant_id or "").strip()
    clean_workspace_id = str(workspace_id or "").strip()
    clean_user_id = str(user_id or "").strip()
    if not clean_tenant_id or not clean_workspace_id or not clean_user_id:
        return None
    try:
        project = await get_project(
            tenant_id=clean_tenant_id,
            workspace_id=clean_workspace_id,
            project_id=project_id,
        )
        if project is None:
            return None
        return await add_project_member(
            tenant_id=clean_tenant_id,
            workspace_id=clean_workspace_id,
            project_id=project_id,
            user_id=clean_user_id,
            added_by=added_by,
        )
    except Exception:
        return None


async def remove_project_member(
    *,
    tenant_id: str,
    workspace_id: str,
    project_id: str,
    user_id: str,
) -> bool:
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return False
    resolved_tenant_id = str(tenant_id or "").strip()
    resolved_workspace_id = str(workspace_id or "").strip()
    result = await control_plane_repository.rls_execute(
        pool,
        """
        DELETE FROM project_memberships
        WHERE tenant_id = $1 AND workspace_id = $2 AND project_id = $3 AND user_id = $4
        """,
        resolved_tenant_id,
        resolved_workspace_id,
        str(project_id or "").strip(),
        str(user_id or "").strip(),
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
    )
    return str(result or "").endswith(" 1") or str(result or "").endswith("-1")


async def list_project_members(
    *,
    tenant_id: str,
    workspace_id: str,
    project_id: str,
) -> List[Dict[str, Any]]:
    """The USERS explicitly granted access to a project — joined against
    `users` for email/display_name/avatar_url, same shape as
    control_plane_repository.list_workspace_members. Does NOT include
    workspace owners who see the project via the bypass in
    auth.enforce_project_access — those aren't rows in this table (a role
    grant, not a membership grant), so a caller that wants "everyone who
    can actually see this project" must union this with the workspace's
    owner list separately (see routes_fleet.py's project-members route)."""
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return []
    resolved_tenant_id = str(tenant_id or "").strip()
    resolved_workspace_id = str(workspace_id or "").strip()
    rows = await control_plane_repository.rls_fetch(
        pool,
        """
        SELECT pm.id, pm.project_id, pm.user_id, pm.role, pm.added_by, pm.created_at,
               u.email, u.display_name, u.avatar_url
        FROM project_memberships pm
        JOIN users u ON u.id = pm.user_id
        WHERE pm.tenant_id = $1 AND pm.workspace_id = $2 AND pm.project_id = $3
        ORDER BY pm.created_at ASC, pm.user_id ASC
        """,
        resolved_tenant_id,
        resolved_workspace_id,
        str(project_id or "").strip(),
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
    )
    return [m for m in (_row_to_member(r) for r in rows) if m]


async def is_project_member(
    *,
    tenant_id: str,
    workspace_id: str,
    project_id: str,
    user_id: str,
) -> bool:
    """The core access-check primitive — does this user have an explicit
    project_memberships row for this project? Does NOT account for the
    workspace-owner bypass; callers that need the full "can this user see
    this project" answer must check owner role first (see
    auth.enforce_project_access, the one real caller of this for access
    control)."""
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return False
    resolved_tenant_id = str(tenant_id or "").strip()
    resolved_workspace_id = str(workspace_id or "").strip()
    row = await control_plane_repository.rls_fetchrow(
        pool,
        """
        SELECT 1 FROM project_memberships
        WHERE tenant_id = $1 AND workspace_id = $2 AND project_id = $3 AND user_id = $4
        """,
        resolved_tenant_id,
        resolved_workspace_id,
        str(project_id or "").strip(),
        str(user_id or "").strip(),
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
    )
    return row is not None


async def list_member_project_ids(
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
) -> List[str]:
    """Every project id this user has an explicit membership row for, within
    one workspace — the filter fleet_projects (list) applies for a
    non-owner caller so the project list itself doesn't leak the existence
    of projects the caller can't open."""
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return []
    resolved_tenant_id = str(tenant_id or "").strip()
    resolved_workspace_id = str(workspace_id or "").strip()
    rows = await control_plane_repository.rls_fetch(
        pool,
        """
        SELECT project_id FROM project_memberships
        WHERE tenant_id = $1 AND workspace_id = $2 AND user_id = $3
        """,
        resolved_tenant_id,
        resolved_workspace_id,
        str(user_id or "").strip(),
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
    )
    return [str(r["project_id"]).strip() for r in (rows or []) if str(r["project_id"] or "").strip()]

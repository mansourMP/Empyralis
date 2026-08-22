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
import logging
import re
import uuid
from typing import Any, Dict, List, Optional

from server_modules import control_plane_repository

LOGGER = logging.getLogger(__name__)

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
        # Per-project task identifier prefix (migrations/
        # add_task_sequence_numbers.sql), e.g. "GEN" -- combined with a
        # task's own `number` this renders as "GEN-12". None on a project
        # created before create_project started allocating one, or on a
        # database predating the migration.
        "task_key": str(r.get("task_key") or "").strip() or None,
    }


async def _unique_task_key(pool: Any, tenant_id: str, workspace_id: str, slug: str) -> str:
    """Return a task_key unique within (tenant, workspace) -- Linear's `GEN`
    shape, derived from the project's own slug. Same derivation and same
    numeric-suffix dedup as migrations/add_task_sequence_numbers.sql's own
    backfill loop, so a project made before vs. after this function existed
    gets an identically-shaped key.
    """
    cleaned = re.sub(r"[^a-zA-Z0-9]", "", str(slug or "")).upper()
    base_key = cleaned[:3] or "TSK"
    rows = await control_plane_repository.rls_fetch(
        pool,
        "SELECT task_key FROM projects WHERE tenant_id = $1 AND workspace_id = $2",
        tenant_id,
        workspace_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    existing = {str(r["task_key"] or "").strip() for r in (rows or [])}
    if base_key not in existing:
        return base_key
    n = 2
    while f"{base_key}{n}" in existing:
        n += 1
    return f"{base_key}{n}"


# ── Task identifier backfill (migrations/add_task_sequence_numbers.sql) ─────
# WHY THIS EXISTS IN PYTHON AT ALL, and it is the whole point of this block:
# that migration's own backfill ran on production and touched ZERO rows,
# silently. `projects` and `project_tasks` carry FORCE ROW LEVEL SECURITY
# (migrations/enable_rls.sql) whose policy is
# empyralis_rls_scope_match(tenant_id, workspace_id) -- and a psql session has
# none of `app.current_tenant_id` / `app.current_workspace_id` /
# `app.rls_bypass` set. DEPLOY-RUNBOOK step 3b requires migrations to be
# applied as the app's own NON-SUPERUSER role (`empyralis_app`), which is
# exactly the role FORCE binds. So:
#
#   ALTER TABLE / CREATE INDEX   DDL -- not subject to RLS  ─▶ APPLIED
#   SELECT/UPDATE ... projects   DML -- policy is false     ─▶ 0 rows, no error
#
# The migration reported success, the columns and uq_projects_task_key
# appeared, and every task_key stayed NULL with every task_seq at 0. Measured
# on production 2026-08-18: 9 of 10 projects task_key IS NULL, 34 tasks, 0
# numbered. A row-count-free backfill is indistinguishable from a backfill
# with nothing to do, which is why nobody noticed for five days.
#
# THE RULE THIS LEAVES BEHIND: a migration that only ADDS COLUMNS is safe to
# hand to psql; a migration that BACKFILLS a tenant-scoped table is not, and
# must either set app.rls_bypass itself or run through code that sets the
# scope. This function is that code, invoked from
# control_plane_repository.ensure_control_plane_schema() so every database --
# production, disposable e2e, a fresh developer box -- heals on the next boot
# rather than on somebody remembering a psql step.
#
# ORDERING: created_at ASC, id ASC. The oldest task in a project becomes
# GEN-1, which is what a person expects from an issue tracker, and the id
# tiebreak makes it stable -- two runs over the same data cannot produce a
# different assignment, so a re-run is a genuine no-op rather than a
# reshuffle. Identities appear in comments, links and agent memory; a
# RENUMBERING backfill would be worse than no backfill at all.
#
# IDEMPOTENCY is by GUARD, not by a "has this run" flag:
#   task_key   assigned only WHERE task_key IS NULL
#   number     assigned only WHERE number IS NULL, offset by that project's
#              own MAX(number), so an already-numbered task keeps its number
#              and a half-finished run resumes instead of colliding
#   task_seq   seeded only WHERE task_seq = 0 -- a ONE-TIME seed. It must
#              never become a general "resync task_seq to MAX(number)": a
#              deleted task would make that undercount and the next
#              allocation would reissue a number that already exists.
#
# THE ALLOCATOR RACE IS CLOSED BY A ROW LOCK, not by hoping. Numbering a
# project and seeding its task_seq happen in ONE transaction that begins by
# taking `SELECT ... FOR UPDATE` on that project's own row -- the same row
# project_tasks_service.create_task's `UPDATE projects SET task_seq =
# task_seq + 1 RETURNING task_seq` locks. A task created mid-backfill blocks
# until the seed commits and then allocates from the seeded value; without
# the lock it would allocate 1 while the backfill was assigning 1 to the
# oldest task.
async def backfill_task_identifiers(pool: Any) -> Dict[str, int]:
    """Fill in `projects.task_key`, `project_tasks.number` and the
    `projects.task_seq` seed for rows that predate their allocation code.

    Returns a count dict; every step is a no-op once applied. Safe to call on
    every boot and safe to call concurrently with task creation.
    """
    stats = {"projects_keyed": 0, "tasks_numbered": 0, "projects_seeded": 0}
    if pool is None:
        return stats

    # 1. Which projects still need a key. Cross-tenant, so this ONE read
    #    genuinely needs the bypass; every WRITE below is scoped to the single
    #    (tenant, workspace) the row itself names.
    unkeyed = await control_plane_repository.rls_fetch(
        pool,
        """
        SELECT id, tenant_id, workspace_id, slug
        FROM projects
        WHERE task_key IS NULL
        ORDER BY tenant_id, workspace_id, created_at ASC, id ASC
        """,
        bypass_rls=True,
    )
    for proj in unkeyed or []:
        tenant_id = str(proj["tenant_id"] or "")
        workspace_id = str(proj["workspace_id"] or "")
        # REUSE, never a second derivation: _unique_task_key is the same
        # function create_project calls, so a project keyed by this backfill
        # and a project keyed at creation are shaped identically and dedupe
        # against each other. It reads the live table under the row's own
        # scope, so a key assigned earlier in this very loop is visible to the
        # next iteration.
        key = await _unique_task_key(pool, tenant_id, workspace_id, str(proj["slug"] or ""))
        # `AND task_key IS NULL` makes the write itself idempotent, so two
        # processes booting at once cannot overwrite each other's key.
        written = await control_plane_repository.rls_execute(
            pool,
            "UPDATE projects SET task_key = $1 WHERE id = $2 AND task_key IS NULL",
            key,
            str(proj["id"] or ""),
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )
        if str(written or "").strip().endswith(" 1"):
            stats["projects_keyed"] += 1

    # 2/3. Number the tasks and seed task_seq, one project at a time, each in
    #      its own locked transaction (see the row-lock note above).
    projects_with_unnumbered = await control_plane_repository.rls_fetch(
        pool,
        """
        SELECT DISTINCT p.id, p.tenant_id, p.workspace_id
        FROM projects p
        JOIN project_tasks t ON t.project_id = p.id
        WHERE t.number IS NULL
        ORDER BY 1
        """,
        bypass_rls=True,
    )
    for proj in projects_with_unnumbered or []:
        project_id = str(proj["id"] or "")
        tenant_id = str(proj["tenant_id"] or "")
        workspace_id = str(proj["workspace_id"] or "")
        async with pool.acquire() as connection:
            async with connection.transaction():
                await control_plane_repository.apply_connection_scope(
                    connection, tenant_id=tenant_id, workspace_id=workspace_id
                )
                locked = await connection.fetchrow(
                    "SELECT task_seq FROM projects WHERE id = $1 FOR UPDATE",
                    project_id,
                )
                if locked is None:
                    continue
                numbered = await connection.fetch(
                    """
                    WITH base AS (
                        SELECT COALESCE(MAX(number), 0) AS mx
                        FROM project_tasks WHERE project_id = $1
                    ),
                    ranked AS (
                        SELECT id,
                               ROW_NUMBER() OVER (ORDER BY created_at ASC, id ASC) AS rn
                        FROM project_tasks
                        WHERE project_id = $1 AND number IS NULL
                    )
                    UPDATE project_tasks pt
                    SET number = ranked.rn + base.mx
                    FROM ranked, base
                    WHERE pt.id = ranked.id
                    RETURNING pt.id
                    """,
                    project_id,
                )
                stats["tasks_numbered"] += len(numbered or [])
                # Seed task_seq, RAISE-ONLY. Guarded on `task_seq < MAX(number)`
                # -- the invariant this restores -- never on `task_seq = 0`.
                #
                # `task_seq = 0` is the guard the original migration used, and
                # it is subtly wrong: a task created by the LIVE allocator
                # between the deploy and this repair moves task_seq to 1 while
                # 4 older tasks are still unnumbered. The backfill then numbers
                # them 2..5 (offset by MAX(number)) and the `= 0` guard SKIPS
                # the seed, leaving task_seq at 1 -- so the very next
                # create_task allocates 2 and collides with a live task. Caught
                # by test_a_task_created_before_the_backfill_keeps_its_number,
                # not by review.
                #
                # Raising is always correct and lowering is never correct:
                # every number ever issued came out of task_seq, so
                # task_seq >= MAX(number) is the invariant, and task_seq < mx
                # means it is already broken. Deleting tasks can only shrink
                # MAX(number), so this can never walk the counter backwards
                # into reissuing a number that once existed -- which is the
                # actual hazard the original "one-time seed" comment was
                # reaching for. The `< sub.mx` predicate also makes a second
                # run a true no-op rather than a same-value write, so the
                # returned counts stay honest.
                seeded = await connection.execute(
                    """
                    UPDATE projects p
                    SET task_seq = sub.mx
                    FROM (
                        SELECT COALESCE(MAX(number), 0) AS mx
                        FROM project_tasks WHERE project_id = $1
                    ) sub
                    WHERE p.id = $1 AND p.task_seq < sub.mx
                    """,
                    project_id,
                )
                if str(seeded or "").strip().endswith(" 1"):
                    stats["projects_seeded"] += 1

    if stats["projects_keyed"] or stats["tasks_numbered"] or stats["projects_seeded"]:
        LOGGER.warning(
            "TASK IDENTIFIER BACKFILL: assigned %d project task_key(s), numbered "
            "%d task(s), seeded %d project task_seq counter(s). Tasks now render "
            "their Linear-style GEN-12 identifier instead of a hex slice of their "
            "own uuid.",
            stats["projects_keyed"],
            stats["tasks_numbered"],
            stats["projects_seeded"],
        )
    return stats


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
               is_default, archived, metadata, created_at, updated_at, task_key
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
               is_default, archived, metadata, created_at, updated_at, task_key
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
    task_key = await _unique_task_key(pool, tenant_id, workspace_id, final_slug)
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
        INSERT INTO projects (id, tenant_id, workspace_id, name, slug, description, is_default, metadata, task_key)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9)
        RETURNING id, tenant_id, workspace_id, name, slug, description,
                  is_default, archived, metadata, created_at, updated_at, task_key
        """,
        pid,
        tenant_id,
        workspace_id,
        name,
        final_slug,
        str(description or "").strip(),
        bool(is_default),
        json.dumps(identity),
        task_key,
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
            # MAN-299: this used to be a bare `except Exception: pass` --
            # the project was returned as created while the membership row
            # silently never landed, with no log, no signal to the caller.
            # add_project_member's INSERT is already idempotent (ON CONFLICT
            # ... DO UPDATE), so "the creator is already a member" never
            # raises here -- there is no benign case to narrow this to.
            # Anything that does land here (a dropped connection, a
            # constraint violation, a bad pool) is a real failure, and the
            # roster becoming wrong (the creator silently missing from
            # project_memberships) is exactly the bug MAN-114's add-member
            # UI depends on this table being honest about. Still
            # best-effort by design -- the project itself is already
            # committed by this point, so this must not undo (or appear to
            # undo) a successful create -- but it must never be silent.
            LOGGER.error(
                "create_project_member_grant_failed: could not add creator "
                "user_id=%s as a member of project_id=%s (tenant_id=%s, "
                "workspace_id=%s) -- the project was created but the "
                "creator has no project_memberships row.",
                clean_creator,
                project["id"],
                tenant_id,
                workspace_id,
                exc_info=True,
            )
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
               is_default, archived, metadata, created_at, updated_at, task_key
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


class _ProjectDeleteRaced(Exception):
    """Internal: the DELETE matched no row, so the whole unit of work is
    rolled back and delete_project returns None. Never escapes this module."""


async def delete_project(
    *,
    tenant_id: str,
    workspace_id: str,
    project_id: str,
) -> Optional[Dict[str, Any]]:
    """Permanently delete a project and everything that IS the project.
    Irreversible; `set_project_archived` above is the reversible everyday
    action and stays what the UI reaches for first.

    Returns a summary of what was removed (counts, plus how many agents were
    rehomed), or None when the project doesn't resolve in this workspace.
    Raises ValueError for the default project — a workspace must always keep
    a home for ungrouped agents, exactly as `set_project_archived` refuses.

    What DIES with the project (all via the projects(id) FK's ON DELETE
    CASCADE — see control_plane_repository.CONTROL_PLANE_SCHEMA_SQL):
        project_tasks   ─▶ project_task_labels, task_notifications
        project_documents
        project_memberships
        agent_goals

    What MOVES HOME rather than being cut loose. Both columns are
    ON DELETE SET NULL, and letting the FK do that is the sharp edge here,
    which is why this reassigns them BEFORE the delete instead:

      * workspace_agent_installs.project_id — a NULL here is not merely
        untidy, it SILENTLY REVOKES the whole project-scoped toolset.
        agent_turn_runtime_service._specialist_tool_allowed grants the
        `project_task__*` / `document__*` / `goal__*` families on
        `bool(toolset["project_id"])` alone (CLAUDE.md: "project membership
        is the grant, not a connector binding"), so an agent whose project
        row vanished would just stop being offered those tools, with no
        error anywhere. Rehomed to the default project — the same thing
        set_project_archived already does, for the same reason.
      * vault_credentials.project_id — NULL is unreachable, not just
        unlisted: connectors_actions.list_project_connectors and
        subscribe_agent_to_project_credential both filter on it, and no
        surface anywhere lists project-less credentials, so a stored secret
        would linger forever with no way to see or revoke it. Rehomed
        alongside the agents that use it, keeping the two on the same
        project so subscribe_agent_to_project_credential's equality check
        still holds.

    What is deliberately LEFT ALONE:
      * usage_events.project_id (no FK) — historical billing/usage rows.
        Deleting a project must not rewrite what was already spent, and the
        rollup only reads it under an explicit scope="project" query.
      * workspace_member_invites.metadata->>'project_id' — a pending invite
        naming this project. grant_invite_project_access resolves the
        project by id and no-ops when it is gone, so acceptance degrades to
        "joined the workspace, no project grant" rather than failing.
      * Agent memory records carrying a project_id in their metadata blob:
        outside Postgres entirely (memory_service), and an agent's own
        recollection is not the project's to delete.
    """
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return None
    tenant_id = str(tenant_id or "").strip()
    workspace_id = str(workspace_id or "").strip()
    project_id = str(project_id or "").strip()

    target = await get_project(tenant_id=tenant_id, workspace_id=workspace_id, project_id=project_id)
    if target is None:
        return None
    if target.get("is_default"):
        raise ValueError("The default project cannot be deleted.")

    # Resolved BEFORE the transaction below: ensure_default_project takes
    # its own connection out of the same pool, and calling it from inside a
    # held transaction is how you deadlock a small pool.
    default_project = await ensure_default_project(tenant_id=tenant_id, workspace_id=workspace_id)
    default_project_id = str((default_project or {}).get("id") or "").strip()
    if not default_project_id:
        raise ValueError("Could not resolve this workspace's default project; nothing was deleted.")

    # ONE transaction for the count + both reassignments + the delete, on one
    # connection. Not decoration: the first browser run of this failed on the
    # vault_credentials statement (that table has no tenant_id column), and
    # with a statement-per-transaction helper the agents had ALREADY been
    # moved out of a project that then didn't get deleted — a user's agents
    # silently rehomed by an operation that reported failure. Either all of
    # it happens or none of it does.
    async def _run(connection: Any) -> Dict[str, Any]:
            await control_plane_repository.apply_connection_scope(
                connection, tenant_id=tenant_id, workspace_id=workspace_id,
            )

            # Count what the cascade is about to take, BEFORE it takes it —
            # the caller (route → activity ledger → UI) can only report
            # honestly on numbers read while the rows still exist.
            counts_row = await connection.fetchrow(
                """
                SELECT
                  (SELECT COUNT(*) FROM project_tasks
                     WHERE tenant_id = $1 AND workspace_id = $2 AND project_id = $3) AS tasks,
                  (SELECT COUNT(*) FROM project_documents
                     WHERE tenant_id = $1 AND workspace_id = $2 AND project_id = $3) AS documents,
                  (SELECT COUNT(*) FROM project_memberships
                     WHERE tenant_id = $1 AND workspace_id = $2 AND project_id = $3) AS members,
                  (SELECT COUNT(*) FROM agent_goals
                     WHERE tenant_id = $1 AND workspace_id = $2 AND project_id = $3) AS goals
                """,
                tenant_id,
                workspace_id,
                project_id,
            )
            counts = dict(counts_row) if counts_row else {}

            agents_moved = await connection.execute(
                """
                UPDATE workspace_agent_installs
                SET project_id = $4, updated_at = NOW()
                WHERE tenant_id = $1 AND workspace_id = $2 AND project_id = $3
                """,
                tenant_id,
                workspace_id,
                project_id,
                default_project_id,
            )
            # NOTE the different WHERE shape: vault_credentials has NO
            # tenant_id column at all — one of the four tables CLAUDE.md
            # calls out as carrying only ONE of the two scope columns (also
            # why empyralis_rls_scope_match can't be applied to it).
            # workspace_id + project_id is the full scope available, and it
            # is sufficient: a project id is unique across tenants, and the
            # rows whose workspace_id is deliberately NULL are the
            # platform-scoped credentials, which never carry a project_id
            # and so can never match here.
            credentials_moved = await connection.execute(
                """
                UPDATE vault_credentials
                SET project_id = $3
                WHERE workspace_id = $1 AND project_id = $2
                """,
                workspace_id,
                project_id,
                default_project_id,
            )

            deleted = await connection.execute(
                "DELETE FROM projects WHERE tenant_id = $1 AND workspace_id = $2 AND id = $3",
                tenant_id,
                workspace_id,
                project_id,
            )
            if _affected_row_count(deleted) < 1:
                # Raced with a concurrent delete. Roll the reassignments
                # back too — claiming a deletion that did not happen is the
                # dishonesty this whole path is written to avoid.
                raise _ProjectDeleteRaced()

            return {
                "counts": counts,
                "agents_moved": _affected_row_count(agents_moved),
                "credentials_moved": _affected_row_count(credentials_moved),
            }

    try:
        async with pool.acquire() as connection:
            async with connection.transaction():
                outcome = await _run(connection)
    except _ProjectDeleteRaced:
        return None

    counts = outcome["counts"]
    return {
        "id": project_id,
        "name": target.get("name") or "",
        "tasks_deleted": int(counts.get("tasks") or 0),
        "documents_deleted": int(counts.get("documents") or 0),
        "members_removed": int(counts.get("members") or 0),
        "goals_deleted": int(counts.get("goals") or 0),
        "agents_moved": outcome["agents_moved"],
        "credentials_moved": outcome["credentials_moved"],
        "moved_to_project_id": default_project_id,
        "moved_to_project_name": str((default_project or {}).get("name") or ""),
    }


def _affected_row_count(command_tag: Any) -> int:
    """asyncpg's execute() returns a command tag like 'UPDATE 3' / 'DELETE 1'.
    Parse the count, defaulting to 0 rather than guessing when the shape is
    unexpected."""
    parts = str(command_tag or "").strip().split()
    if not parts:
        return 0
    try:
        return int(parts[-1])
    except ValueError:
        return 0


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

    ALSO requires the gateway's owner to have explicitly opted this specific
    machine into project sharing (gateway_state_repository.
    gateway_project_sharing_opted_in — CLAUDE.md's "Hardware attaches to its
    owner, never to the project" law: sharing is a per-machine opt-in by the
    hardware's owner, default off). Belonging to the workspace is not
    consent; only that flag is. This is defense-in-depth, not the primary
    enforcement point — resolve_specialist_runtime_context re-checks the
    SAME flag on every turn, so even if a value already got stored here
    before this check existed (or the owner later revokes consent), it can
    never be used without live consent at resolution time either.

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
        from server_modules import fleet_tools, gateway_state_repository

        check = fleet_tools.gateway_resolves_in_workspace(clean_gateway_id, resolved_workspace_id)
        if not check.get("ok"):
            raise ValueError(str(check.get("error") or "Gateway does not resolve in this workspace."))
        if not gateway_state_repository.gateway_project_sharing_opted_in(clean_gateway_id):
            raise ValueError(
                "This machine's owner hasn't shared it with projects yet. Ask them to turn on "
                "sharing from the machine's Hardware settings, or choose a different machine."
            )

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

    Shared by BOTH acceptance paths, which now both run through
    routes_workspaces._finalize_workspace_invite_acceptance: the emailed
    /join/{token} link (POST /workspaces/invites/accept) and the in-app
    banner's Join button (POST /workspaces/invites/{id}/join). A third,
    silent path used to exist -- auth.accept_workspace_invites_for_user
    granted membership on every login and registration -- and it was deleted
    2026-08-20 because signing in must never change what you are a member of.
    The validate-then-grant behavior existing exactly once is what keeps a
    fix here from working on one path and quietly no-op'ing on the other.

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


async def default_project_id_if_exists(*, tenant_id: str, workspace_id: str) -> Optional[str]:
    """Read-only counterpart to ensure_default_project (above) — the id of
    the workspace's default ('General') project if one already exists, or
    None. Deliberately never CREATES one: this is called from a READ path
    (see backfill_default_project_access_if_never_granted below), and
    creating a project as a side effect of someone merely loading their own
    project list would be a write nobody asked for."""
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return None
    resolved_tenant_id = str(tenant_id or "").strip()
    resolved_workspace_id = str(workspace_id or "").strip()
    row = await control_plane_repository.rls_fetchrow(
        pool,
        """
        SELECT id FROM projects
        WHERE tenant_id = $1 AND workspace_id = $2 AND is_default = TRUE
        ORDER BY created_at ASC
        LIMIT 1
        """,
        resolved_tenant_id,
        resolved_workspace_id,
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
    )
    return str(row["id"]).strip() if row and row.get("id") else None


_DEFAULT_PROJECT_BACKFILL_MARKER = "default_project_backfilled_at"


async def backfill_default_project_access_if_never_granted(
    *, tenant_id: str, workspace_id: str, user_id: str
) -> bool:
    """MAN-335: a workspace invite accepted before this fix granted
    workspace membership and NOTHING ELSE (see grant_invite_project_access's
    own docstring — a workspace-level invite carries no project_id, so its
    metadata is `{}` and that function no-ops). Those members are stuck
    seeing zero projects, zero tasks, zero documents, zero agents forever,
    because nothing re-runs invite acceptance for someone who already
    accepted.

    This is the self-heal: called from _visible_project_ids
    (routes_fleet.py) the next time an affected member's own project list
    is computed. Grants the workspace's default project — never "every
    project", matching the per-project-membership model — and ONLY the
    first time for a given (tenant, workspace, user), enforced by a durable
    marker in workspace_memberships.metadata rather than by "does this
    member currently have zero project grants": the latter would silently
    UNDO a deliberate later removal from every one of their projects,
    re-granting access an owner explicitly took away. The marker makes this
    a true one-shot per member — once stamped, never reconsidered again,
    regardless of anything that happens to their project grants afterward.

    Returns True if a grant just happened (caller should include the
    project id in what it returns this request), False otherwise (already
    backfilled, no default project exists yet, or nothing to do).
    """
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return False
    resolved_tenant_id = str(tenant_id or "").strip()
    resolved_workspace_id = str(workspace_id or "").strip()
    resolved_user_id = str(user_id or "").strip()
    if not resolved_tenant_id or not resolved_workspace_id or not resolved_user_id:
        return False

    membership_row = await control_plane_repository.rls_fetchrow(
        pool,
        """
        SELECT metadata FROM workspace_memberships
        WHERE tenant_id = $1 AND workspace_id = $2 AND user_id = $3
        """,
        resolved_tenant_id,
        resolved_workspace_id,
        resolved_user_id,
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
    )
    if membership_row is None:
        return False  # not actually a member of this workspace — nothing to heal
    # Same decode control_plane_repository uses everywhere it reads a jsonb
    # metadata column back out (asyncpg hands back a JSON string, not a
    # dict, unless a codec is registered) — reused rather than
    # re-implemented here.
    existing_metadata = control_plane_repository._decode_json_object(membership_row.get("metadata"))
    if existing_metadata.get(_DEFAULT_PROJECT_BACKFILL_MARKER):
        return False  # already considered — never reconsidered again, see docstring

    default_project_id = await default_project_id_if_exists(
        tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id
    )

    # Stamp the marker REGARDLESS of whether a default project existed to
    # grant — a workspace with no projects yet at the moment this member's
    # list is first computed must not re-check on every subsequent request
    # either; a project created later is reachable the normal way (an
    # explicit per-project invite), which is the same path every OTHER
    # project beyond the default already requires.
    from datetime import datetime, timezone

    stamped_metadata = {
        **existing_metadata,
        _DEFAULT_PROJECT_BACKFILL_MARKER: datetime.now(timezone.utc).isoformat(),
    }
    await control_plane_repository.rls_execute(
        pool,
        """
        UPDATE workspace_memberships
        SET metadata = $4::jsonb, updated_at = now()
        WHERE tenant_id = $1 AND workspace_id = $2 AND user_id = $3
        """,
        resolved_tenant_id,
        resolved_workspace_id,
        resolved_user_id,
        json.dumps(stamped_metadata),
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
    )

    if not default_project_id:
        return False

    try:
        existing_grant = await list_member_project_ids(
            tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id, user_id=resolved_user_id
        )
        if default_project_id in existing_grant:
            return False  # already has it some other way (e.g. a per-project invite landed first)
        await add_project_member(
            tenant_id=resolved_tenant_id,
            workspace_id=resolved_workspace_id,
            project_id=default_project_id,
            user_id=resolved_user_id,
        )
        return True
    except Exception:
        return False

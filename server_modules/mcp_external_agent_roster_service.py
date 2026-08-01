"""External-agent roster — Step 2 of "Mentions + identity for platform AND
external agents" (see mcp_server.py's module docstring for the full picture).

THE IDENTITY RULE this module exists to enforce: identity is minted by the
PLATFORM at the connection boundary. Brains never have identity.

- A **platform agent**'s identity is its ``workspace_agent_installs.id``
  (existing, unrelated to this module).
- An **external agent** — a Codex/Claude Code session the user runs OUTSIDE
  the platform, connected through our MCP server at ``/mcp`` — has no such
  row. Its identity is minted the moment its MCP bearer key is created
  (``mcp_server_auth.create_workspace_mcp_api_key``), via
  ``register_external_agent`` below. The bearer key's SHA-256 hash IS its
  authentication of identity: ``key_hash`` is UNIQUE and is exactly the hash
  ``mcp_server_auth.resolve_workspace_from_api_key`` already computes on every
  call, so identity resolution never needs a second auth path.

Storage: a dedicated small table (``mcp_external_agent_roster``, see
``migrations/add_mcp_external_agent_roster.sql`` for the full "why not
vault_credentials + agent_connector_bindings" justification). No RLS —
scoped like ``projects``/``project_tasks``: every query here filters by
(tenant_id, workspace_id) explicitly.

``list_unified_roster`` is the ONE interface a future @-mention resolver
reads — it merges this table (``kind="external"``) with
``workspace_agent_installs`` (``kind="platform"``) so the model/UI is always
choosing from one closed roster, never free-typing an ID, regardless of
which kind of agent it's addressing.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

from server_modules import control_plane_repository

LOGGER = logging.getLogger(__name__)


def _new_external_agent_id() -> str:
    return f"ext_agent_{uuid.uuid4().hex[:16]}"


def _row_to_roster_entry(row: Any) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    r = dict(row)
    return {
        "id": str(r.get("id") or "").strip(),
        "kind": "external",
        "tenant_id": str(r.get("tenant_id") or "").strip() or None,
        "workspace_id": str(r.get("workspace_id") or "").strip() or None,
        "display_name": str(r.get("display_name") or "").strip(),
        "mcp_key_id": str(r.get("mcp_key_id") or "").strip() or None,
        "revoked": bool(r.get("revoked", False)),
        "created_at": str(r.get("created_at") or "") or None,
        "updated_at": str(r.get("updated_at") or "") or None,
    }


async def _existing_roster_names(*, tenant_id: str, workspace_id: str) -> List[str]:
    """Names already taken in this workspace, across BOTH kinds — a platform
    agent labelled "Atlas" and an external agent auto-named "Atlas" would be
    ambiguous the moment a mention resolver has to pick one, so collision
    checking spans the whole roster, not just this table (mirrors
    fleet_tools.py's own within-workspace collision check for platform
    agents, extended to the union)."""
    names: List[str] = []
    try:
        from server_modules import agent_registry_repository as installs_repo

        platform_rows = await installs_repo.list_workspace_agent_installs(
            tenant_id=tenant_id, workspace_id=workspace_id, include_master=True,
        )
        names.extend(str(i.get("label") or "") for i in (platform_rows or []))
    except Exception:
        LOGGER.debug("Could not load platform installs for roster-name collision check", exc_info=True)

    try:
        external_rows = await list_workspace_external_agents(
            tenant_id=tenant_id, workspace_id=workspace_id, include_revoked=True,
        )
        names.extend(str(e.get("display_name") or "") for e in external_rows)
    except Exception:
        LOGGER.debug("Could not load external roster for name collision check", exc_info=True)

    return names


async def register_external_agent(
    *,
    tenant_id: str,
    workspace_id: str,
    key_hash: str,
    mcp_key_id: str = "",
    display_name: str = "",
) -> Dict[str, Any]:
    """Mint (or idempotently return) the roster entry for a bearer key.

    Called at TWO points, both required for the identity rule to hold without
    a silent gap:
    1. ``mcp_server_auth.create_workspace_mcp_api_key`` — the normal path, at
       key-mint time.
    2. ``mcp_server_auth.resolve_workspace_from_api_key`` — a lazy, idempotent
       backfill for any key that predates this feature or whose mint-time
       insert failed (e.g. Postgres was briefly unreachable). Idempotent via
       ``ON CONFLICT (key_hash) DO NOTHING`` + a follow-up read, so a race
       between two concurrent backfills can never create two identities for
       the same key.

    Returns ``{ok: False, error}`` (never raises) when Postgres is
    unavailable — key creation itself must never fail because the roster
    write failed; the caller decides how loudly to surface that.
    """
    tenant = str(tenant_id or "").strip()
    ws = str(workspace_id or "").strip()
    hashed = str(key_hash or "").strip()
    if not tenant or not ws:
        return {"ok": False, "error": "tenant_id and workspace_id are required to register an external agent."}
    if not hashed:
        return {"ok": False, "error": "key_hash is required to register an external agent."}

    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return {"ok": False, "error": "Postgres is required to mint an external-agent roster entry."}

    name = str(display_name or "").strip()
    if not name:
        from server_modules import agent_name_pool

        existing_names = await _existing_roster_names(tenant_id=tenant, workspace_id=ws)
        name = agent_name_pool.assign_agent_name(existing_names)

    new_id = _new_external_agent_id()
    row = await control_plane_repository.rls_fetchrow(
        pool,
        """
        INSERT INTO mcp_external_agent_roster (id, tenant_id, workspace_id, display_name, key_hash, mcp_key_id)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (key_hash) DO NOTHING
        RETURNING id, tenant_id, workspace_id, display_name, key_hash, mcp_key_id, revoked, created_at, updated_at
        """,
        new_id,
        tenant,
        ws,
        name,
        hashed,
        str(mcp_key_id or "").strip() or None,
        tenant_id=tenant,
        workspace_id=ws,
    )
    if row is None:
        # Conflict — another writer already registered this key_hash (mint
        # + backfill race, or a re-registration attempt). Return the
        # existing row rather than a second identity. Scoped to the SAME
        # (tenant, ws) this call was invoked with -- a key_hash collision
        # under a DIFFERENT tenant is not "the same key re-registering", it
        # is a hash collision, and this must not silently hand back another
        # tenant's roster row just because RLS was asked to bypass here.
        row = await control_plane_repository.rls_fetchrow(
            pool,
            """
            SELECT id, tenant_id, workspace_id, display_name, key_hash, mcp_key_id, revoked, created_at, updated_at
            FROM mcp_external_agent_roster
            WHERE key_hash = $1
            """,
            hashed,
            tenant_id=tenant,
            workspace_id=ws,
        )
    entry = _row_to_roster_entry(row)
    if entry is None:
        return {"ok": False, "error": "Roster insert returned no row and no existing row was found."}
    return {"ok": True, **entry}


async def get_external_agent_by_key_hash(*, key_hash: str) -> Optional[Dict[str, Any]]:
    """Look up the roster entry for a bearer key's hash — the read side of
    the identity rule, called on every MCP request that resolves a bearer
    key (see mcp_server_auth.resolve_workspace_from_api_key).

    THE ONE GENUINE bypass_rls IN THIS MODULE. This is auth bootstrap: the
    whole point of the call is to discover which tenant/workspace a bearer
    key belongs to, which means no tenant/workspace scope can exist yet at
    call time — there is nothing to pass to rls_fetchrow's tenant_id/
    workspace_id that would not be circular. key_hash is a SHA-256 digest of
    a cryptographically random bearer token (mcp_server_auth._hash_key) and
    is UNIQUE in the schema (mcp_external_agent_roster.key_hash), so the
    WHERE clause is exactly as selective with the bypass as it would be
    scoped — the tenant isolation this row needs comes from "you cannot
    produce this hash without the plaintext key," not from a WHERE filter.
    Matches the same pattern control_plane_repository.py documents for
    self-hosted node auth (bearer-token-keyed lookups before a tenant is
    known)."""
    hashed = str(key_hash or "").strip()
    if not hashed:
        return None
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return None
    row = await control_plane_repository.rls_fetchrow(
        pool,
        """
        SELECT id, tenant_id, workspace_id, display_name, key_hash, mcp_key_id, revoked, created_at, updated_at
        FROM mcp_external_agent_roster
        WHERE key_hash = $1
        """,
        hashed,
        bypass_rls=True,
    )
    return _row_to_roster_entry(row)


async def set_external_agent_revoked(
    *, tenant_id: str, workspace_id: str, key_hash: str, revoked: bool = True,
) -> None:
    """Mirror a bearer key's revoke/un-revoke into the roster row (best
    effort — the actual authorization check lives in mcp_api_keys.json;
    this only keeps the roster's "who's currently active" listing honest).
    Never raises: called from revoke_workspace_mcp_api_key, which must not
    fail because this side-channel failed.

    Unlike get_external_agent_by_key_hash, this is NOT an auth-bootstrap
    call -- the caller already knows which workspace's key it is revoking
    (mcp_server_auth.revoke_workspace_mcp_api_key reads it straight off the
    matched JSON key entry before calling this) and can resolve tenant_id
    from it, so real scope is threaded here rather than reaching for
    bypass_rls."""
    hashed = str(key_hash or "").strip()
    resolved_tenant_id = str(tenant_id or "").strip()
    resolved_workspace_id = str(workspace_id or "").strip()
    if not hashed or not resolved_tenant_id or not resolved_workspace_id:
        return
    try:
        pool = await control_plane_repository.ensure_control_plane_schema()
        if pool is None:
            return
        await control_plane_repository.rls_execute(
            pool,
            "UPDATE mcp_external_agent_roster SET revoked = $2, updated_at = NOW() WHERE key_hash = $1",
            hashed,
            bool(revoked),
            tenant_id=resolved_tenant_id,
            workspace_id=resolved_workspace_id,
        )
    except Exception:
        LOGGER.warning("Failed to mirror MCP key revoke into external-agent roster", exc_info=True)


async def list_workspace_external_agents(
    *, tenant_id: str, workspace_id: str, include_revoked: bool = False,
) -> List[Dict[str, Any]]:
    """List external-agent roster rows for a workspace — the ``kind="external"``
    half of ``list_unified_roster``."""
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return []
    tenant = str(tenant_id or "").strip()
    ws = str(workspace_id or "").strip()
    if include_revoked:
        rows = await control_plane_repository.rls_fetch(
            pool,
            """
            SELECT id, tenant_id, workspace_id, display_name, key_hash, mcp_key_id, revoked, created_at, updated_at
            FROM mcp_external_agent_roster
            WHERE tenant_id = $1 AND workspace_id = $2
            ORDER BY created_at ASC
            """,
            tenant, ws,
            tenant_id=tenant, workspace_id=ws,
        )
    else:
        rows = await control_plane_repository.rls_fetch(
            pool,
            """
            SELECT id, tenant_id, workspace_id, display_name, key_hash, mcp_key_id, revoked, created_at, updated_at
            FROM mcp_external_agent_roster
            WHERE tenant_id = $1 AND workspace_id = $2 AND revoked = FALSE
            ORDER BY created_at ASC
            """,
            tenant, ws,
            tenant_id=tenant, workspace_id=ws,
        )
    return [e for e in (_row_to_roster_entry(r) for r in rows) if e]


async def list_unified_roster(
    *, tenant_id: str, workspace_id: str, include_revoked: bool = False,
) -> List[Dict[str, Any]]:
    """THE one roster view: every addressable agent in the workspace, platform
    and external, each tagged ``kind``. Written for a future @-mention
    resolver to read; it must never need to know that platform and external
    identities live in two different tables.

    MAN-66 update: the task-comment @-mention resolver that landed
    (``task_mention_service.py``) does NOT read this function for agent
    identity, and that is a deliberate scope decision, not an oversight --
    see that module's own docstring for the full reasoning. Short version:
    mention-driven WAKING must only ever target an agent
    ``project_tasks_service.assign_task`` could also address, and
    ``assign_task`` validates only against ``workspace_agent_installs``
    (``_agent_install_exists``) -- an external agent cannot be a task
    assignee today. Routing mention resolution through this function would
    mean resolving an external agent's name only to then have no scheduler
    that can actually wake it. Extending mention support to external
    agents (chip rendering without waking) is legitimate future work and
    WOULD read this function when it happens.

    2026-08-01: this function got its FIRST caller --
    ``routes_fleet.fleet_roster`` (GET /api/w/{workspace_id}/fleet/roster),
    the read the task board uses to turn an ``ext_agent_<hex16>`` author id
    into the name of the agent that actually did the work. That caller passes
    ``include_revoked=True``, because ATTRIBUTION is a different question from
    ADDRESSABILITY. A revoked key can never act again, so it must not appear
    in anything that offers a target to pick -- which is why the default stays
    False -- but the comments and tasks it already wrote are still on the
    board, and the honest name for their author is the one that agent had, not
    "Unknown". A revoked entry comes back tagged ``enabled: False`` /
    ``status: "revoked"``, so a caller wanting only addressable agents filters
    in the payload rather than by re-querying.
    """
    tenant = str(tenant_id or "").strip()
    ws = str(workspace_id or "").strip()
    unified: List[Dict[str, Any]] = []

    try:
        from server_modules import agent_registry_repository as installs_repo

        platform_rows = await installs_repo.list_workspace_agent_installs(
            tenant_id=tenant, workspace_id=ws, include_master=True,
        )
        for install in platform_rows or []:
            unified.append({
                "id": str(install.get("id") or "").strip(),
                "kind": "platform",
                "display_name": str(install.get("label") or "").strip(),
                "enabled": bool(install.get("enabled", True)),
                "status": str(install.get("status") or ""),
            })
    except Exception:
        LOGGER.warning("Failed to list platform installs for unified roster", exc_info=True)

    external_rows = await list_workspace_external_agents(
        tenant_id=tenant, workspace_id=ws, include_revoked=bool(include_revoked),
    )
    for entry in external_rows:
        unified.append({
            "id": entry["id"],
            "kind": "external",
            "display_name": entry["display_name"],
            "enabled": not entry["revoked"],
            "status": "revoked" if entry["revoked"] else "active",
        })

    return unified

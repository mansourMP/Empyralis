"""Phase 2: Agent connector/channel binding repository.

The `agent_connector_bindings` and `agent_channel_bindings` tables are the
per-agent "assignment + on/off switch" for connectors and channels. Combined
with an agent-scoped vault credential, an enabled binding row is what makes a
connector/channel "connected" for a specific agent:

    connected(agent, connector) ==
        (a vault credential scoped to agent exists) AND (binding row enabled)

Direct-pool access pattern, matching agent_registry_repository. Postgres-first.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional

from server_modules import control_plane_repository


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _binding_json(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


async def _upsert(
    *,
    table: str,
    key_col: str,
    tenant_id: str,
    workspace_id: str,
    agent_install_id: str,
    key: str,
    enabled: bool,
    binding: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return None
    row = await control_plane_repository.rls_fetchrow(
        pool,
        f"""
        INSERT INTO {table} (id, tenant_id, workspace_id, agent_install_id, {key_col}, enabled, binding)
        VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
        ON CONFLICT (agent_install_id, {key_col})
        DO UPDATE SET enabled = EXCLUDED.enabled,
                      binding = EXCLUDED.binding,
                      updated_at = NOW()
        RETURNING id, tenant_id, workspace_id, agent_install_id, {key_col} AS key, enabled, binding
        """,
        _new_id("acbind" if "connector" in table else "achbind"),
        str(tenant_id or "").strip(),
        str(workspace_id or "").strip(),
        str(agent_install_id or "").strip(),
        str(key or "").strip(),
        bool(enabled),
        json.dumps(binding or {}),
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    if row is None:
        return None
    r = dict(row)
    r["binding"] = _binding_json(r.get("binding"))
    return r


async def _list(
    *,
    table: str,
    key_col: str,
    tenant_id: str,
    workspace_id: str,
    agent_install_id: Optional[str],
    enabled_only: bool,
) -> List[Dict[str, Any]]:
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return []
    rows = await control_plane_repository.rls_fetch(
        pool,
        f"""
        SELECT id, tenant_id, workspace_id, agent_install_id, {key_col} AS key, enabled, binding
        FROM {table}
        WHERE tenant_id = $1
          AND workspace_id = $2
          AND ($3::text IS NULL OR agent_install_id = $3)
          AND ($4::bool = FALSE OR enabled = TRUE)
        """,
        str(tenant_id or "").strip(),
        str(workspace_id or "").strip(),
        str(agent_install_id).strip() if agent_install_id else None,
        bool(enabled_only),
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    out = []
    for row in rows or []:
        r = dict(row)
        r["binding"] = _binding_json(r.get("binding"))
        out.append(r)
    return out


async def _delete(*, table: str, key_col: str, tenant_id: str, workspace_id: str, agent_install_id: str, key: str) -> bool:
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return False
    result = await control_plane_repository.rls_execute(
        pool,
        f"DELETE FROM {table} WHERE tenant_id=$1 AND workspace_id=$2 AND agent_install_id=$3 AND {key_col}=$4",
        str(tenant_id or "").strip(),
        str(workspace_id or "").strip(),
        str(agent_install_id or "").strip(),
        str(key or "").strip(),
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    return str(result or "").endswith("1")


# ── Connector bindings ──────────────────────────────────────────────────────

async def upsert_connector_binding(
    *, tenant_id: str, workspace_id: str, agent_install_id: str, connector_key: str,
    enabled: bool = True, binding: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    return await _upsert(
        table="agent_connector_bindings", key_col="connector_key",
        tenant_id=tenant_id, workspace_id=workspace_id, agent_install_id=agent_install_id,
        key=connector_key, enabled=enabled, binding=binding,
    )


async def list_agent_connector_bindings(
    *, tenant_id: str, workspace_id: str, agent_install_id: str, enabled_only: bool = True,
) -> List[Dict[str, Any]]:
    return await _list(
        table="agent_connector_bindings", key_col="connector_key",
        tenant_id=tenant_id, workspace_id=workspace_id, agent_install_id=agent_install_id,
        enabled_only=enabled_only,
    )


async def list_workspace_connector_bindings(
    *, tenant_id: str, workspace_id: str, enabled_only: bool = True,
) -> List[Dict[str, Any]]:
    return await _list(
        table="agent_connector_bindings", key_col="connector_key",
        tenant_id=tenant_id, workspace_id=workspace_id, agent_install_id=None,
        enabled_only=enabled_only,
    )


async def delete_connector_binding(
    *, tenant_id: str, workspace_id: str, agent_install_id: str, connector_key: str,
) -> bool:
    return await _delete(
        table="agent_connector_bindings", key_col="connector_key",
        tenant_id=tenant_id, workspace_id=workspace_id,
        agent_install_id=agent_install_id, key=connector_key,
    )


# ── Channel bindings ────────────────────────────────────────────────────────

async def upsert_channel_binding(
    *, tenant_id: str, workspace_id: str, agent_install_id: str, channel_key: str,
    enabled: bool = True, binding: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    return await _upsert(
        table="agent_channel_bindings", key_col="channel_key",
        tenant_id=tenant_id, workspace_id=workspace_id, agent_install_id=agent_install_id,
        key=channel_key, enabled=enabled, binding=binding,
    )


async def list_agent_channel_bindings(
    *, tenant_id: str, workspace_id: str, agent_install_id: str, enabled_only: bool = True,
) -> List[Dict[str, Any]]:
    return await _list(
        table="agent_channel_bindings", key_col="channel_key",
        tenant_id=tenant_id, workspace_id=workspace_id, agent_install_id=agent_install_id,
        enabled_only=enabled_only,
    )


async def list_workspace_channel_bindings(
    *, tenant_id: str, workspace_id: str, enabled_only: bool = True,
) -> List[Dict[str, Any]]:
    return await _list(
        table="agent_channel_bindings", key_col="channel_key",
        tenant_id=tenant_id, workspace_id=workspace_id, agent_install_id=None,
        enabled_only=enabled_only,
    )


# ── Inbound-owner conflict helpers ──────────────────────────────────────────
# Shared by every channel-bind path (Slack/Discord/Telegram/GitHub/...) that
# needs to turn uq_agent_channel_bindings_inbound_owner_v2's DB-level
# guarantee (control_plane_repository.py) into a specific, human-readable
# error instead of leaking a raw asyncpg constraint-violation string to the
# frontend. discord_bot_provisioning_service.py already carried a local,
# duplicated version of the detection half of this (_is_inbound_owner_
# conflict) -- these are the shared, reusable versions, plus the soft
# pre-check and label lookup needed to name WHICH agent already owns the
# channel.

async def find_inbound_owner_conflict(
    *, tenant_id: str, workspace_id: str, channel_key: str, endpoint_key: str,
    exclude_agent_install_id: str,
) -> Optional[Dict[str, Any]]:
    """Soft pre-check: does another agent already hold inbound-owner status
    for this exact (channel_key, endpoint_key)? Returns that binding row
    (with 'agent_install_id') if so, else None.

    This is advisory only -- a friendlier, earlier error than waiting on the
    DB round-trip -- and is NOT the source of truth: two concurrent binds
    can both pass this check, so callers must still rely on the unique index
    (via is_inbound_owner_conflict on the actual write) as the race-free
    guarantee. Mirrors the pattern discord_bot_provisioning_service.
    assign_agent_discord established for Discord, generalized for reuse."""
    rows = await list_workspace_channel_bindings(
        tenant_id=tenant_id, workspace_id=workspace_id, enabled_only=True,
    )
    normalized_endpoint = str(endpoint_key or "").strip().lower()
    excluded_agent = str(exclude_agent_install_id or "").strip()
    for row in rows:
        if str(row.get("key") or "") != str(channel_key or ""):
            continue
        if str(row.get("agent_install_id") or "").strip() == excluded_agent:
            continue
        meta = row.get("binding") or {}
        if str(meta.get("endpoint_key") or "").strip().lower() != normalized_endpoint:
            continue
        if str(meta.get("is_inbound_owner") or "").strip().lower() != "true":
            continue
        return row
    return None


def is_inbound_owner_conflict(exc: BaseException) -> bool:
    """True when an exception is the inbound-owner unique-index violation
    (uq_agent_channel_bindings_inbound_owner_v2) -- the DB-level, race-free
    version of the guarantee find_inbound_owner_conflict only checks
    optimistically. Matches by substring so it survives index-name
    revisions (e.g. the Phase 3D ..._inbound_owner_v2 rebuild)."""
    constraint = str(getattr(exc, "constraint_name", "") or "")
    return "inbound_owner" in constraint or "inbound_owner" in str(exc)


class AgentInstallNotInScopeError(RuntimeError):
    """Raised when a caller-supplied agent_install_id does not resolve to an
    agent install owned by the caller's own (tenant_id, workspace_id)."""


async def agent_install_in_scope(
    agent_install_id: str, *, tenant_id: str, workspace_id: str,
) -> bool:
    """True iff agent_install_id resolves to a real agent install owned by
    exactly this (tenant_id, workspace_id) pair.

    MAN-206: every channel-bot assignment (Telegram/Discord BYO, so far)
    takes a caller-supplied agent_install_id and, on success, writes an
    agent-scoped vault credential PLUS a channel binding row -- both stamped
    with the CALLER's own tenant_id/workspace_id. Postgres RLS's
    ``INSERT ... WITH CHECK`` on those writes only verifies that the NEW
    row's tenant_id/workspace_id matches the caller's session scope, which
    it always does (assign_byo_bot/assign_agent_discord pass the caller's
    own scope there) -- it says nothing about whether agent_install_id
    itself belongs to that scope. Without this check, a caller who has (or
    leaks/guesses) another tenant's agent_install_id could plant a channel
    binding and a vault credential against that OTHER tenant's agent, and
    the hosted-bot webhook (keyed only by agent_install_id) would then route
    real inbound traffic to it. Call this BEFORE any such write and reject
    if it returns False.

    Fails closed: a missing pool, an empty id, or a lookup error all return
    False, never True -- an ownership check that can fail open is not a
    check.
    """
    aid = str(agent_install_id or "").strip()
    tid = str(tenant_id or "").strip()
    wid = str(workspace_id or "").strip()
    if not aid or not tid or not wid:
        return False
    try:
        pool = await control_plane_repository.ensure_control_plane_schema()
        if pool is None:
            return False
        row = await control_plane_repository.rls_fetchrow(
            pool,
            "SELECT 1 FROM workspace_agent_installs WHERE id = $1 AND tenant_id = $2 AND workspace_id = $3",
            aid,
            tid,
            wid,
            tenant_id=tid,
            workspace_id=wid,
        )
    except Exception:
        return False
    return row is not None


async def get_agent_install_label(
    agent_install_id: str, *, tenant_id: str, workspace_id: str,
) -> Optional[str]:
    """Best-effort, tenant/workspace-scoped lookup of an agent's display
    label -- used to enrich a channel-ownership-conflict message with WHICH
    agent already owns the channel, instead of a bare 'another agent'.
    Returns None (never raises) on any lookup failure so a label miss can
    never block the conflict error itself from being raised."""
    try:
        pool = await control_plane_repository.ensure_control_plane_schema()
        if pool is None:
            return None
        row = await control_plane_repository.rls_fetchrow(
            pool,
            "SELECT label FROM workspace_agent_installs WHERE id = $1 AND tenant_id = $2 AND workspace_id = $3",
            str(agent_install_id or "").strip(),
            str(tenant_id or "").strip(),
            str(workspace_id or "").strip(),
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )
    except Exception:
        return None
    if row is None:
        return None
    label = str(dict(row).get("label") or "").strip()
    return label or None


async def delete_channel_binding(
    *, tenant_id: str, workspace_id: str, agent_install_id: str, channel_key: str,
) -> bool:
    return await _delete(
        table="agent_channel_bindings", key_col="channel_key",
        tenant_id=tenant_id, workspace_id=workspace_id,
        agent_install_id=agent_install_id, key=channel_key,
    )


async def get_channel_binding_by_agent_unscoped(
    *, agent_install_id: str, channel_key: str,
) -> Optional[Dict[str, Any]]:
    """Resolve a channel binding from the agent_install_id alone, with no
    tenant/workspace known in advance — the shape a webhook receives (the
    URL path carries only the agent_install_id; there is no session to
    derive a tenant from). agent_install_id is already a random, effectively
    unique token, so this is the same bypass_rls=True pattern documented on
    control_plane_repository.rls_fetchrow for bearer-token/id system lookups
    (matching how the old hosted-bot-pool webhook resolved a pool_bot_id).
    Returns tenant_id/workspace_id alongside the binding so the caller can
    then do normal tenant-scoped work."""
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return None
    row = await control_plane_repository.rls_fetchrow(
        pool,
        """
        SELECT tenant_id, workspace_id, agent_install_id, channel_key AS key, enabled, binding
        FROM agent_channel_bindings
        WHERE agent_install_id = $1 AND channel_key = $2 AND enabled = TRUE
        """,
        str(agent_install_id or "").strip(),
        str(channel_key or "").strip(),
        bypass_rls=True,
    )
    if row is None:
        return None
    r = dict(row)
    r["binding"] = _binding_json(r.get("binding"))
    return r

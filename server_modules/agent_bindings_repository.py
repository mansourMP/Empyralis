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
    row = await pool.fetchrow(
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
    rows = await pool.fetch(
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
    result = await pool.execute(
        f"DELETE FROM {table} WHERE tenant_id=$1 AND workspace_id=$2 AND agent_install_id=$3 AND {key_col}=$4",
        str(tenant_id or "").strip(),
        str(workspace_id or "").strip(),
        str(agent_install_id or "").strip(),
        str(key or "").strip(),
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


async def delete_channel_binding(
    *, tenant_id: str, workspace_id: str, agent_install_id: str, channel_key: str,
) -> bool:
    return await _delete(
        table="agent_channel_bindings", key_col="channel_key",
        tenant_id=tenant_id, workspace_id=workspace_id,
        agent_install_id=agent_install_id, key=channel_key,
    )

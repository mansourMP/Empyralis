"""Phase 3B: Hosted bot pool repository.

Platform-owned Telegram bots, pre-provisioned and assignable to a single agent
each. The encrypted token lives in the vault (platform-scoped, workspace_id
NULL); this table holds the assignment state and a reference (credential_id) to
that vault entry. status ∈ {free, assigned, quarantined}.

Direct-pool access pattern, Postgres-first.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional

from server_modules import control_plane_repository


def _new_id() -> str:
    return f"poolbot_{uuid.uuid4().hex[:16]}"


def _row(row: Any) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    r = dict(row)
    meta = r.get("metadata")
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:
            meta = {}
    return {
        "id": str(r.get("id") or ""),
        "provider": str(r.get("provider") or "telegram"),
        "bot_username": str(r.get("bot_username") or ""),
        "bot_id": str(r.get("bot_id") or "") or None,
        "credential_id": str(r.get("credential_id") or ""),
        "webhook_secret": str(r.get("webhook_secret") or "") or None,
        "status": str(r.get("status") or "free"),
        "assigned_agent_install_id": str(r.get("assigned_agent_install_id") or "") or None,
        "assigned_workspace_id": str(r.get("assigned_workspace_id") or "") or None,
        "assigned_tenant_id": str(r.get("assigned_tenant_id") or "") or None,
        "assigned_at": str(r.get("assigned_at") or "") or None,
        "metadata": meta if isinstance(meta, dict) else {},
        "created_at": str(r.get("created_at") or "") or None,
    }


_COLS = (
    "id, provider, bot_username, bot_id, credential_id, webhook_secret, status, "
    "assigned_agent_install_id, assigned_workspace_id, assigned_tenant_id, "
    "assigned_at, metadata, created_at"
)


async def add_pool_bot(
    *,
    provider: str = "telegram",
    bot_username: str,
    bot_id: Optional[str],
    credential_id: str,
    webhook_secret: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return None
    row = await pool.fetchrow(
        f"""
        INSERT INTO hosted_bot_pool (id, provider, bot_username, bot_id, credential_id, webhook_secret, status, metadata)
        VALUES ($1, $2, $3, $4, $5, $6, 'free', $7::jsonb)
        ON CONFLICT (provider, bot_username) DO UPDATE
            SET credential_id = EXCLUDED.credential_id,
                bot_id = EXCLUDED.bot_id,
                webhook_secret = COALESCE(EXCLUDED.webhook_secret, hosted_bot_pool.webhook_secret),
                updated_at = NOW()
        RETURNING {_COLS}
        """,
        _new_id(),
        str(provider or "telegram").strip().lower(),
        str(bot_username or "").strip().lstrip("@"),
        str(bot_id or "").strip() or None,
        str(credential_id or "").strip(),
        str(webhook_secret or "").strip() or None,
        json.dumps(metadata or {}),
    )
    return _row(row)


async def list_pool_bots(*, provider: str = "telegram", status: Optional[str] = None) -> List[Dict[str, Any]]:
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return []
    rows = await pool.fetch(
        f"""
        SELECT {_COLS} FROM hosted_bot_pool
        WHERE provider = $1 AND ($2::text IS NULL OR status = $2)
        ORDER BY created_at ASC
        """,
        str(provider or "telegram").strip().lower(),
        str(status).strip() if status else None,
    )
    return [b for b in (_row(r) for r in rows) if b]


async def get_bot(pool_bot_id: str) -> Optional[Dict[str, Any]]:
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return None
    return _row(await pool.fetchrow(f"SELECT {_COLS} FROM hosted_bot_pool WHERE id = $1", str(pool_bot_id or "").strip()))


async def get_bot_by_username(bot_username: str, *, provider: str = "telegram") -> Optional[Dict[str, Any]]:
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return None
    return _row(await pool.fetchrow(
        f"SELECT {_COLS} FROM hosted_bot_pool WHERE provider = $1 AND lower(bot_username) = lower($2)",
        str(provider or "telegram").strip().lower(),
        str(bot_username or "").strip().lstrip("@"),
    ))


async def get_bot_for_agent(agent_install_id: str) -> Optional[Dict[str, Any]]:
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return None
    return _row(await pool.fetchrow(
        f"SELECT {_COLS} FROM hosted_bot_pool WHERE assigned_agent_install_id = $1",
        str(agent_install_id or "").strip(),
    ))


async def claim_free_bot(
    *,
    agent_install_id: str,
    workspace_id: str,
    tenant_id: str,
    provider: str = "telegram",
) -> Optional[Dict[str, Any]]:
    """Atomically claim one free bot for the agent. Returns the claimed bot, or
    None if the pool is exhausted. Idempotent: if the agent already holds a bot,
    that bot is returned without claiming another."""
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return None
    agent_install_id = str(agent_install_id or "").strip()

    existing = await get_bot_for_agent(agent_install_id)
    if existing is not None:
        return existing

    row = await pool.fetchrow(
        f"""
        UPDATE hosted_bot_pool
        SET status = 'assigned',
            assigned_agent_install_id = $1,
            assigned_workspace_id = $2,
            assigned_tenant_id = $3,
            assigned_at = NOW(),
            updated_at = NOW()
        WHERE id = (
            SELECT id FROM hosted_bot_pool
            WHERE provider = $4 AND status = 'free'
            ORDER BY created_at ASC
            LIMIT 1
            FOR UPDATE SKIP LOCKED
        )
        RETURNING {_COLS}
        """,
        agent_install_id,
        str(workspace_id or "").strip(),
        str(tenant_id or "").strip(),
        str(provider or "telegram").strip().lower(),
    )
    return _row(row)


async def release_bot(
    *,
    pool_bot_id: str,
    new_webhook_secret: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Return a bot to the pool: clear assignment, mark free, rotate the webhook
    secret (so a stale webhook registration can no longer deliver)."""
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return None
    row = await pool.fetchrow(
        f"""
        UPDATE hosted_bot_pool
        SET status = 'free',
            assigned_agent_install_id = NULL,
            assigned_workspace_id = NULL,
            assigned_tenant_id = NULL,
            assigned_at = NULL,
            webhook_secret = COALESCE($2, webhook_secret),
            updated_at = NOW()
        WHERE id = $1
        RETURNING {_COLS}
        """,
        str(pool_bot_id or "").strip(),
        str(new_webhook_secret or "").strip() or None,
    )
    return _row(row)


async def set_bot_status(*, pool_bot_id: str, status: str) -> Optional[Dict[str, Any]]:
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return None
    row = await pool.fetchrow(
        f"UPDATE hosted_bot_pool SET status=$2, updated_at=NOW() WHERE id=$1 RETURNING {_COLS}",
        str(pool_bot_id or "").strip(),
        str(status or "").strip(),
    )
    return _row(row)


async def pool_capacity(*, provider: str = "telegram") -> Dict[str, int]:
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return {"total": 0, "free": 0, "assigned": 0, "quarantined": 0}
    rows = await pool.fetch(
        "SELECT status, COUNT(*) AS n FROM hosted_bot_pool WHERE provider=$1 GROUP BY status",
        str(provider or "telegram").strip().lower(),
    )
    counts = {str(r["status"]): int(r["n"]) for r in (rows or [])}
    return {
        "total": sum(counts.values()),
        "free": counts.get("free", 0),
        "assigned": counts.get("assigned", 0),
        "quarantined": counts.get("quarantined", 0),
    }

"""Postgres-first persistence for the auth-store tables (Phase 1C).

Until Phase 1C these 11 tables lived only in the local SQLite auth file
(~/.empyralis/state/auth/users.db):

    workspace_registry, workspace_policies, tenant_policies,
    tenant_enterprise_settings, user_enterprise_security, user_auth_methods,
    user_provider_connections, user_identity_versions, auth_sessions,
    user_devices, auth_session_refresh_tokens

This module provides async, Postgres-first operations that mirror the SQLite
logic exactly. Each op:
  - acquires the shared control-plane pool via ensure_control_plane_schema();
  - if the pool is None (DATABASE_URL absent / Postgres down), returns the
    module sentinel PG_NA so the caller runs its unchanged SQLite fallback;
  - otherwise performs the work in one transaction and returns *raw row dicts*
    (or None / lists / ints). Shaping + normalization stays in auth.py, which
    applies its existing `_*_from_row` helpers to whatever these return -- so
    there is a single source of truth for row shape and zero duplication.

auth.py calls these through a sync bridge (auth._auth_store_pg) that runs the
coroutine on the shared loop machinery. Real Postgres errors propagate (fail
loud, Postgres-canonical) rather than silently falling back to SQLite.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional

from server_modules import control_plane_repository as _cpr


class _PgNotAvailable:
    """Singleton sentinel: 'the Postgres pool is not available, use SQLite'.

    Distinct from None, which is a legitimate 'no row found' result. Compared
    by identity (`is`) across threads, so it survives the sync/async bridge.
    """

    _instance: Optional["_PgNotAvailable"] = None

    def __new__(cls) -> "_PgNotAvailable":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return "<PG_NA>"


PG_NA = _PgNotAvailable()


def _now() -> int:
    return int(time.time())


# table -> (primary-key columns, columns that must NOT be overwritten on
# conflict i.e. preserve the original insert value). Everything else is
# overwritten from the incoming row on conflict, mirroring SQLite's
# INSERT OR REPLACE while keeping created_at/linked_at stable.
_UPSERT_META: Dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "workspace_registry": (("workspace_id",), ("created_at",)),
    "workspace_policies": (("workspace_id",), ()),
    "tenant_policies": (("tenant_id",), ()),
    "tenant_enterprise_settings": (("tenant_id",), ()),
    "user_enterprise_security": (("user_id",), ()),
    "user_auth_methods": (("id",), ("created_at",)),
    "user_provider_connections": (("id",), ("created_at",)),
    "user_identity_versions": (("user_id",), ()),
    "auth_sessions": (("session_id",), ("created_at",)),
    "user_devices": (("device_id",), ("linked_at",)),
    "auth_session_refresh_tokens": (("session_id",), ("created_at",)),
}


async def _pool() -> Any:
    return await _cpr.ensure_control_plane_schema()


def _upsert_sql(table: str, columns: List[str]) -> str:
    pk, preserve = _UPSERT_META[table]
    placeholders = ", ".join(f"${i + 1}" for i in range(len(columns)))
    conflict_cols = ", ".join(pk)
    updatable = [c for c in columns if c not in pk and c not in preserve]
    if updatable:
        set_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in updatable)
        conflict = f"ON CONFLICT ({conflict_cols}) DO UPDATE SET {set_clause}"
    else:
        conflict = f"ON CONFLICT ({conflict_cols}) DO NOTHING"
    return (
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "
        f"{conflict} RETURNING *"
    )


async def _upsert(conn: Any, table: str, row: Dict[str, Any]) -> Dict[str, Any]:
    columns = list(row.keys())
    sql = _upsert_sql(table, columns)
    record = await conn.fetchrow(sql, *[row[c] for c in columns])
    if record is None:
        # DO NOTHING path (all-PK/preserve table already present): re-fetch.
        pk, _ = _UPSERT_META[table]
        where = " AND ".join(f"{c} = ${i + 1}" for i, c in enumerate(pk))
        record = await conn.fetchrow(f"SELECT * FROM {table} WHERE {where}", *[row[c] for c in pk])
    return dict(record) if record is not None else {}


# --------------------------------------------------------------------------- #
# Simple reads
# --------------------------------------------------------------------------- #
async def fetch_one(table: str, **filters: Any) -> Any:
    pool = await _pool()
    if pool is None:
        return PG_NA
    where = " AND ".join(f"{col} = ${i + 1}" for i, col in enumerate(filters))
    async with _cpr._scoped_connection(bypass_rls=True) as conn:
        if conn is None:
            return PG_NA
        record = await conn.fetchrow(
            f"SELECT * FROM {table} WHERE {where} LIMIT 1", *list(filters.values())
        )
    return dict(record) if record is not None else None


async def fetch_all(table: str, *, order_by: str, **filters: Any) -> Any:
    pool = await _pool()
    if pool is None:
        return PG_NA
    where = " AND ".join(f"{col} = ${i + 1}" for i, col in enumerate(filters))
    sql = f"SELECT * FROM {table}"
    if where:
        sql += f" WHERE {where}"
    sql += f" ORDER BY {order_by}"
    async with _cpr._scoped_connection(bypass_rls=True) as conn:
        if conn is None:
            return PG_NA
        rows = await conn.fetch(sql, *list(filters.values()))
    return [dict(r) for r in rows]


async def list_auth_sessions(user_id: str, *, include_inactive: bool) -> Any:
    pool = await _pool()
    if pool is None:
        return PG_NA
    sql = "SELECT * FROM auth_sessions WHERE user_id = $1"
    if not include_inactive:
        sql += " AND status = 'active'"
    sql += " ORDER BY updated_at DESC, created_at DESC"
    async with _cpr._scoped_connection(bypass_rls=True) as conn:
        if conn is None:
            return PG_NA
        rows = await conn.fetch(sql, user_id)
    return [dict(r) for r in rows]


async def list_devices(user_id: str, *, include_inactive: bool) -> Any:
    pool = await _pool()
    if pool is None:
        return PG_NA
    sql = "SELECT * FROM user_devices WHERE user_id = $1"
    if not include_inactive:
        sql += " AND status = 'active'"
    sql += " ORDER BY updated_at DESC, linked_at DESC"
    async with _cpr._scoped_connection(bypass_rls=True) as conn:
        if conn is None:
            return PG_NA
        rows = await conn.fetch(sql, user_id)
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------- #
# Simple upserts (single row, no compound logic)
# --------------------------------------------------------------------------- #
async def upsert_row(table: str, row: Dict[str, Any]) -> Any:
    pool = await _pool()
    if pool is None:
        return PG_NA
    async with _cpr._scoped_connection(bypass_rls=True) as conn:
        if conn is None:
            return PG_NA
        return await _upsert(conn, table, row)


async def upsert_auth_method(*, row: Dict[str, Any], unset_other_primary: bool) -> Any:
    """Upsert a user_auth_method; if it is primary, clear is_primary on the
    user's other methods first (mirrors _upsert_user_auth_method_locked).
    Also bumps the user's identity auth_version, matching the caller."""
    pool = await _pool()
    if pool is None:
        return PG_NA
    async with _cpr._scoped_connection(bypass_rls=True) as conn:
        if conn is None:
            return PG_NA
        if unset_other_primary:
            await conn.execute(
                "UPDATE user_auth_methods SET is_primary = 0, updated_at = $2 WHERE user_id = $1",
                row["user_id"], int(row.get("updated_at") or _now()),
            )
        return await _upsert(conn, "user_auth_methods", row)


async def upsert_many(items: List[tuple]) -> Any:
    """Upsert several (table, row) pairs in ONE transaction. Used by
    register_user / provision_user_account, whose SQLite path writes
    user_enterprise_security + user_auth_methods + user_identity_versions
    atomically for a brand-new user."""
    pool = await _pool()
    if pool is None:
        return PG_NA
    async with _cpr._scoped_connection(bypass_rls=True) as conn:
        if conn is None:
            return PG_NA
        out: List[Dict[str, Any]] = []
        for table, row in items:
            out.append(await _upsert(conn, table, row))
        return out


# --------------------------------------------------------------------------- #
# Identity versions (read-modify-write)
# --------------------------------------------------------------------------- #
async def ensure_user_identity_versions(user_id: str) -> Any:
    pool = await _pool()
    if pool is None:
        return PG_NA
    async with _cpr._scoped_connection(bypass_rls=True) as conn:
        if conn is None:
            return PG_NA
        row = await conn.fetchrow(
            "SELECT * FROM user_identity_versions WHERE user_id = $1", user_id
        )
        if row is not None:
            return dict(row)
        ts = _now()
        row = await conn.fetchrow(
            """
            INSERT INTO user_identity_versions (user_id, membership_version, auth_version, provider_scope_version, updated_at)
            VALUES ($1, 1, 1, 1, $2)
            ON CONFLICT (user_id) DO UPDATE SET updated_at = user_identity_versions.updated_at
            RETURNING *
            """,
            user_id, ts,
        )
    return dict(row)


async def bump_user_identity_versions(
    user_id: str, *, membership: bool, auth: bool, provider_scope: bool
) -> Any:
    pool = await _pool()
    if pool is None:
        return PG_NA
    async with _cpr._scoped_connection(bypass_rls=True) as conn:
        if conn is None:
            return PG_NA
        existing = await conn.fetchrow(
            "SELECT * FROM user_identity_versions WHERE user_id = $1", user_id
        )
        ts = _now()
        base_m = int(existing["membership_version"]) if existing else 1
        base_a = int(existing["auth_version"]) if existing else 1
        base_p = int(existing["provider_scope_version"]) if existing else 1
        row = await conn.fetchrow(
            """
            INSERT INTO user_identity_versions (user_id, membership_version, auth_version, provider_scope_version, updated_at)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (user_id) DO UPDATE SET
                membership_version = EXCLUDED.membership_version,
                auth_version = EXCLUDED.auth_version,
                provider_scope_version = EXCLUDED.provider_scope_version,
                updated_at = EXCLUDED.updated_at
            RETURNING *
            """,
            user_id,
            base_m + (1 if membership else 0),
            base_a + (1 if auth else 0),
            base_p + (1 if provider_scope else 0),
            ts,
        )
    return dict(row)


# --------------------------------------------------------------------------- #
# Auth sessions (compound: create ensures identity-versions; revokes cascade)
# --------------------------------------------------------------------------- #
async def create_auth_session(*, row: Dict[str, Any]) -> Any:
    """Insert/replace a session AND ensure the user's identity-versions row,
    mirroring create_auth_session()'s two-step body, in one transaction."""
    pool = await _pool()
    if pool is None:
        return PG_NA
    async with _cpr._scoped_connection(bypass_rls=True) as conn:
        if conn is None:
            return PG_NA
        await conn.execute(
            """
            INSERT INTO user_identity_versions (user_id, membership_version, auth_version, provider_scope_version, updated_at)
            VALUES ($1, 1, 1, 1, $2)
            ON CONFLICT (user_id) DO NOTHING
            """,
            row["user_id"], _now(),
        )
        return await _upsert(conn, "auth_sessions", row)


async def touch_auth_session(session_id: str) -> Any:
    pool = await _pool()
    if pool is None:
        return PG_NA
    ts = _now()
    async with _cpr._scoped_connection(bypass_rls=True) as conn:
        if conn is None:
            return PG_NA
        existing = await conn.fetchrow("SELECT session_id FROM auth_sessions WHERE session_id = $1", session_id)
        if existing is None:
            return None
        row = await conn.fetchrow(
            "UPDATE auth_sessions SET updated_at = $2, last_seen_at = $2 WHERE session_id = $1 RETURNING *",
            session_id, ts,
        )
    return dict(row) if row is not None else None


async def revoke_auth_session(session_id: str, *, reason: str) -> Any:
    pool = await _pool()
    if pool is None:
        return PG_NA
    ts = _now()
    async with _cpr._scoped_connection(bypass_rls=True) as conn:
        if conn is None:
            return PG_NA
        existing = await conn.fetchrow("SELECT session_id FROM auth_sessions WHERE session_id = $1", session_id)
        if existing is None:
            return None
        row = await conn.fetchrow(
            """
            UPDATE auth_sessions
            SET status = 'revoked',
                trust_state = CASE WHEN device_id IS NOT NULL THEN 'revoked' ELSE trust_state END,
                updated_at = $2, revoked_at = $2, revoked_reason = $3
            WHERE session_id = $1
            RETURNING *
            """,
            session_id, ts, reason,
        )
        await conn.execute(
            """
            UPDATE auth_session_refresh_tokens
            SET updated_at = $2, revoked_at = $2, revoked_reason = $3
            WHERE session_id = $1 AND revoked_at IS NULL
            """,
            session_id, ts, reason,
        )
    return dict(row) if row is not None else None


async def revoke_user_auth_sessions(user_id: str, *, device_id: Optional[str], reason: str) -> Any:
    pool = await _pool()
    if pool is None:
        return PG_NA
    ts = _now()
    async with _cpr._scoped_connection(bypass_rls=True) as conn:
        if conn is None:
            return PG_NA
        if device_id:
            active = await conn.fetch(
                "SELECT session_id FROM auth_sessions WHERE user_id = $1 AND status = 'active' AND device_id = $2",
                user_id, device_id,
            )
            status = await conn.execute(
                """
                UPDATE auth_sessions
                SET status = 'revoked',
                    trust_state = CASE WHEN device_id IS NOT NULL THEN 'revoked' ELSE trust_state END,
                    updated_at = $2, revoked_at = $2, revoked_reason = $3
                WHERE user_id = $1 AND status = 'active' AND device_id = $4
                """,
                user_id, ts, reason, device_id,
            )
        else:
            active = await conn.fetch(
                "SELECT session_id FROM auth_sessions WHERE user_id = $1 AND status = 'active'",
                user_id,
            )
            status = await conn.execute(
                """
                UPDATE auth_sessions
                SET status = 'revoked',
                    trust_state = CASE WHEN device_id IS NOT NULL THEN 'revoked' ELSE trust_state END,
                    updated_at = $2, revoked_at = $2, revoked_reason = $3
                WHERE user_id = $1 AND status = 'active'
                """,
                user_id, ts, reason,
            )
        session_ids = [str(r["session_id"]) for r in active]
        if session_ids:
            await conn.execute(
                """
                UPDATE auth_session_refresh_tokens
                SET updated_at = $2, revoked_at = $2, revoked_reason = $3
                WHERE session_id = ANY($1::text[]) AND revoked_at IS NULL
                """,
                session_ids, ts, reason,
            )
    # asyncpg returns e.g. "UPDATE 5"
    try:
        return int(str(status).split()[-1])
    except Exception:
        return len(session_ids)


# --------------------------------------------------------------------------- #
# Refresh tokens
# --------------------------------------------------------------------------- #
async def fetch_refresh_token(session_id: str) -> Any:
    return await fetch_one("auth_session_refresh_tokens", session_id=session_id)


async def issue_refresh_token(*, session_id: str, user_id: Optional[str], token_hash: str, ttl_seconds: int) -> Any:
    """Verify the session is active, then upsert its refresh token. Returns
    {'row': <raw token row>, 'session_missing': bool, 'session_inactive': bool}
    so auth.py can raise the same HTTPExceptions it does today."""
    pool = await _pool()
    if pool is None:
        return PG_NA
    ts = _now()
    async with _cpr._scoped_connection(bypass_rls=True) as conn:
        if conn is None:
            return PG_NA
        session = await conn.fetchrow(
            "SELECT session_id, user_id, status FROM auth_sessions WHERE session_id = $1", session_id
        )
        if session is None:
            return {"row": None, "session_missing": True, "session_inactive": False}
        if str(session["status"]) != "active":
            return {"row": None, "session_missing": False, "session_inactive": True}
        resolved_user_id = str(user_id or session["user_id"] or "").strip()
        row = await conn.fetchrow(
            """
            INSERT INTO auth_session_refresh_tokens (session_id, user_id, token_hash, created_at, updated_at, expires_at, rotated_at, revoked_at, revoked_reason)
            VALUES ($1, $2, $3, $4, $4, $5, $4, NULL, NULL)
            ON CONFLICT (session_id) DO UPDATE SET
                user_id = EXCLUDED.user_id,
                token_hash = EXCLUDED.token_hash,
                updated_at = EXCLUDED.updated_at,
                expires_at = EXCLUDED.expires_at,
                rotated_at = EXCLUDED.rotated_at,
                revoked_at = EXCLUDED.revoked_at,
                revoked_reason = EXCLUDED.revoked_reason
            RETURNING *
            """,
            session_id, resolved_user_id, token_hash, ts, ts + int(ttl_seconds),
        )
    return {"row": dict(row), "session_missing": False, "session_inactive": False}


# --------------------------------------------------------------------------- #
# Devices (revoke also cascades user sessions -- handled by caller)
# --------------------------------------------------------------------------- #
async def upsert_device(*, row: Dict[str, Any]) -> Any:
    pool = await _pool()
    if pool is None:
        return PG_NA
    async with _cpr._scoped_connection(bypass_rls=True) as conn:
        if conn is None:
            return PG_NA
        await conn.execute(
            """
            INSERT INTO user_identity_versions (user_id, membership_version, auth_version, provider_scope_version, updated_at)
            VALUES ($1, 1, 1, 1, $2)
            ON CONFLICT (user_id) DO NOTHING
            """,
            row["user_id"], _now(),
        )
        return await _upsert(conn, "user_devices", row)


async def fetch_device_for_user(device_id: str, user_id: str) -> Any:
    return await fetch_one("user_devices", device_id=device_id, user_id=user_id)


async def revoke_device(user_id: str, device_id: str, *, reason: str) -> Any:
    pool = await _pool()
    if pool is None:
        return PG_NA
    ts = _now()
    async with _cpr._scoped_connection(bypass_rls=True) as conn:
        if conn is None:
            return PG_NA
        existing = await conn.fetchrow(
            "SELECT device_id FROM user_devices WHERE device_id = $1 AND user_id = $2", device_id, user_id
        )
        if existing is None:
            return None
        row = await conn.fetchrow(
            """
            UPDATE user_devices
            SET status = 'revoked', trust_state = 'revoked', updated_at = $3, last_seen_at = $3, revoked_at = $3, revoked_reason = $4
            WHERE device_id = $1 AND user_id = $2
            RETURNING *
            """,
            device_id, user_id, ts, reason,
        )
    return dict(row) if row is not None else None


async def touch_device(device_id: str) -> Any:
    pool = await _pool()
    if pool is None:
        return PG_NA
    ts = _now()
    async with _cpr._scoped_connection(bypass_rls=True) as conn:
        if conn is None:
            return PG_NA
        existing = await conn.fetchrow("SELECT device_id FROM user_devices WHERE device_id = $1", device_id)
        if existing is None:
            return None
        row = await conn.fetchrow(
            "UPDATE user_devices SET updated_at = $2, last_seen_at = $2 WHERE device_id = $1 RETURNING *",
            device_id, ts,
        )
    return dict(row) if row is not None else None


# --------------------------------------------------------------------------- #
# Garbage collection (Phase 1C task 8)
# --------------------------------------------------------------------------- #
async def gc_expired_sessions(*, now_ts: Optional[int] = None) -> Any:
    """Delete auth_sessions past expires_at. Refresh tokens cascade via FK.
    Returns the number of sessions deleted, or PG_NA when Postgres is absent."""
    pool = await _pool()
    if pool is None:
        return PG_NA
    cutoff = int(now_ts or _now())
    async with _cpr._scoped_connection(bypass_rls=True) as conn:
        if conn is None:
            return PG_NA
        status = await conn.execute("DELETE FROM auth_sessions WHERE expires_at <= $1", cutoff)
        # Also drop any orphan/expired refresh tokens whose session already went.
        await conn.execute("DELETE FROM auth_session_refresh_tokens WHERE expires_at <= $1", cutoff)
    try:
        return int(str(status).split()[-1])
    except Exception:
        return 0

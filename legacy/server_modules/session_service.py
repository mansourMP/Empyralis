from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional
import uuid

from server_modules import db as runtime_db
from server_modules import control_plane_repository
from server_modules import rust_runtime_kernel_client
from server_modules.runtime_state_store import (
    delete_runtime_session,
    get_runtime_session as get_sqlite_runtime_session,
    init_runtime_state_db,
    upsert_runtime_session,
)
from server_modules.state_paths import runtime_state_db_path


LOGGER = logging.getLogger(__name__)

DEFAULT_SESSION_TTL_SECONDS = max(60, int(str(os.getenv("ORION_SESSION_TTL_SECONDS") or "86400").strip() or "86400"))
SERVER_RUNTIME_SESSION_AUTHORITY = "postgres"
SQLITE_RUNTIME_SESSION_CHECKPOINT_ROLE = "sqlite_checkpoint"
_SCHEMA_READY = False
_SCHEMA_LOCK: asyncio.Lock = asyncio.Lock()
_RUNTIME_SESSIONS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS runtime_sessions (
    session_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    actor JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_runtime_sessions_workspace_expires
    ON runtime_sessions(workspace_id, expires_at DESC);
"""


class SessionScopeViolationError(ValueError):
    def __init__(self, detail: str, *, code: str = "session_scope_mismatch", mismatch: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(detail)
        self.code = str(code or "session_scope_mismatch").strip() or "session_scope_mismatch"
        self.detail = str(detail or "Session scope mismatch.").strip() or "Session scope mismatch."
        self.mismatch = dict(mismatch or {})


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().isoformat().replace("+00:00", "Z")


def _expires_at_iso(ttl_seconds: int) -> str:
    return (_utc_now() + timedelta(seconds=max(60, int(ttl_seconds or DEFAULT_SESSION_TTL_SECONDS)))).isoformat().replace("+00:00", "Z")


def _enforce_session_lifecycle_mutation(
    operation: str,
    *,
    session_record: Dict[str, Any],
) -> Dict[str, Any]:
    payload = {
        "operation": str(operation or "").strip(),
        "session_id": str(session_record.get("session_id") or "").strip() or None,
        "status": str(session_record.get("status") or "active").strip() or "active",
        "preset": "standard",
        "turn_count": max(0, int(session_record.get("turn_count") or 0)),
        "age_hours": max(0, int(session_record.get("age_hours") or 0)),
        "idle_seconds": max(0, int(session_record.get("idle_seconds") or 0)),
        "max_age_hours": max(1, int(session_record.get("max_age_hours") or 24)),
        "idle_timeout_seconds": max(1, int(session_record.get("idle_timeout_seconds") or 3600)),
        "force": False,
    }
    try:
        decision = dict(
            rust_runtime_kernel_client.run_runtime_kernel_enforced(
                "session-lifecycle-decision",
                payload,
                allow_approval_required=True,
            )
        )
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        reason = str(exc.reason or "session_lifecycle_denied").strip()
        raise RuntimeError(f"Rust session lifecycle kernel blocked {operation}: {reason}") from exc
    expected_next_actions = {
        "create": {"create"},
        "close": {"close"},
    }.get(str(operation or "").strip())
    next_action = str(decision.get("next_action") or "").strip()
    if expected_next_actions and next_action not in expected_next_actions:
        raise RuntimeError(f"unexpected_next_action:{next_action or 'missing'}")
    return decision


def _coerce_timestamptz(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    token = str(value or "").strip()
    if not token:
        return None
    try:
        parsed = datetime.fromisoformat(token.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _normalized_runtime_state_db_path() -> Path:
    return runtime_state_db_path().resolve()


def _coerce_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        token = value.strip()
        if not token:
            return {}
        try:
            parsed = json.loads(token)
        except ValueError:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _coerce_actor(value: Any) -> Dict[str, Any]:
    payload = _coerce_dict(value)
    return {
        "type": str(payload.get("type") or "user").strip() or "user",
        "id": str(payload.get("id") or "").strip(),
        "display_name": str(payload.get("display_name") or "").strip(),
    }


def _scope_enforcement_enabled() -> bool:
    raw = str(os.getenv("ORION_ENFORCE_SCOPED_SESSION_RESUME") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _sqlite_session_compatibility_fallback_enabled() -> bool:
    raw = str(os.getenv("ORION_ALLOW_SERVER_SESSION_SQLITE_FALLBACK") or "0").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    # In local dev without Postgres, always allow SQLite fallback
    env = str(os.getenv("ENV") or os.getenv("ENVIRONMENT") or "").strip().lower()
    if env in {"development", "dev", "local"}:
        return True
    db_url = str(os.getenv("DATABASE_URL") or "").strip()
    if not db_url:
        return True
    return False


def _metadata_binding_token(metadata: Any, key: str) -> Optional[str]:
    payload = _coerce_dict(metadata)
    token = str(payload.get(key) or "").strip()
    return token or None


def _canonical_session_record(
    *,
    session_id: str,
    workspace_id: str,
    tenant_id: str,
    channel: str,
    actor: Any,
    created_at: str,
    expires_at: str,
    metadata: Any,
    status: str = "active",
) -> Dict[str, Any]:
    normalized_metadata = _coerce_dict(metadata)
    normalized_metadata.setdefault("thread_id", str(session_id or "").strip())
    return {
        "session_id": str(session_id or "").strip(),
        "workspace_id": str(workspace_id or "default").strip() or "default",
        "tenant_id": str(tenant_id or "default").strip() or "default",
        "channel": str(channel or "web").strip().lower() or "web",
        "actor": _coerce_actor(actor),
        "created_at": str(created_at or "").strip() or _utc_now_iso(),
        "expires_at": str(expires_at or "").strip() or _expires_at_iso(DEFAULT_SESSION_TTL_SECONDS),
        "metadata": normalized_metadata,
        "status": str(status or "active").strip() or "active",
    }


def _sqlite_payload_from_session(record: Dict[str, Any]) -> Dict[str, Any]:
    actor = _coerce_actor(record.get("actor"))
    metadata = _coerce_dict(record.get("metadata"))
    metadata.setdefault("tenant_id", record.get("tenant_id"))
    metadata.setdefault("channel", record.get("channel"))
    metadata.setdefault("actor", actor)
    metadata.setdefault("expires_at", record.get("expires_at"))
    return {
        "session_id": str(record.get("session_id") or "").strip(),
        "actor_key": str(actor.get("id") or record.get("session_id") or "").strip(),
        "workspace_id": str(record.get("workspace_id") or "default").strip() or "default",
        "user_id": str(actor.get("id") or "").strip(),
        "status": str(record.get("status") or "active").strip() or "active",
        "runtime_options": {},
        "created_at": str(record.get("created_at") or "").strip() or _utc_now_iso(),
        "updated_at": _utc_now_iso(),
        "last_touched_at": _utc_now_iso(),
        "last_error": "",
        "meta": metadata,
    }


def _session_from_sqlite(row: Any) -> Optional[Dict[str, Any]]:
    payload = _coerce_dict(row)
    session_id = str(payload.get("session_id") or "").strip()
    if not session_id:
        return None
    metadata = _coerce_dict(payload.get("meta"))
    actor = _coerce_actor(metadata.get("actor"))
    if not actor.get("id"):
        actor["id"] = str(payload.get("user_id") or "").strip()
    return _canonical_session_record(
        session_id=session_id,
        workspace_id=str(payload.get("workspace_id") or "default").strip() or "default",
        tenant_id=str(metadata.get("tenant_id") or "default").strip() or "default",
        channel=str(metadata.get("channel") or "web").strip() or "web",
        actor=actor,
        created_at=str(payload.get("created_at") or "").strip() or _utc_now_iso(),
        expires_at=str(metadata.get("expires_at") or "").strip() or _expires_at_iso(DEFAULT_SESSION_TTL_SECONDS),
        metadata=metadata,
        status=str(payload.get("status") or "active").strip() or "active",
    )


def _session_from_pg(row: Any) -> Optional[Dict[str, Any]]:
    payload = dict(row or {})
    session_id = str(payload.get("session_id") or "").strip()
    if not session_id:
        return None
    return _canonical_session_record(
        session_id=session_id,
        workspace_id=str(payload.get("workspace_id") or "default").strip() or "default",
        tenant_id=str(payload.get("tenant_id") or "default").strip() or "default",
        channel=str(payload.get("channel") or "web").strip() or "web",
        actor=payload.get("actor"),
        created_at=str(payload.get("created_at") or "").strip() or _utc_now_iso(),
        expires_at=str(payload.get("expires_at") or "").strip() or _expires_at_iso(DEFAULT_SESSION_TTL_SECONDS),
        metadata=payload.get("metadata"),
    )


def _effective_thread_id(record: Dict[str, Any]) -> str:
    metadata = _coerce_dict(record.get("metadata"))
    return str(metadata.get("thread_id") or record.get("session_id") or "").strip()


def _scope_mismatch(
    record: Dict[str, Any],
    *,
    workspace_id: str,
    tenant_id: str,
    channel: str,
    thread_id: str,
) -> Dict[str, Dict[str, str]]:
    expected = {
        "workspace_id": str(workspace_id or "default").strip() or "default",
        "tenant_id": str(tenant_id or "default").strip() or "default",
        "channel": str(channel or "web").strip().lower() or "web",
        "thread_id": str(thread_id or record.get("session_id") or "").strip(),
    }
    actual = {
        "workspace_id": str(record.get("workspace_id") or "default").strip() or "default",
        "tenant_id": str(record.get("tenant_id") or "default").strip() or "default",
        "channel": str(record.get("channel") or "web").strip().lower() or "web",
        "thread_id": _effective_thread_id(record),
    }
    mismatch: Dict[str, Dict[str, str]] = {}
    for key, expected_value in expected.items():
        actual_value = actual.get(key) or ""
        if expected_value and actual_value != expected_value:
            mismatch[key] = {"expected": expected_value, "actual": actual_value}
    return mismatch


def _is_expired(record: Dict[str, Any]) -> bool:
    expires_at = str(record.get("expires_at") or "").strip()
    if not expires_at:
        return False
    try:
        return datetime.fromisoformat(expires_at.replace("Z", "+00:00")) <= _utc_now()
    except Exception:
        return False


async def _ensure_runtime_sessions_table() -> Any:
    global _SCHEMA_READY
    pool = await runtime_db.get_pool()
    if pool is None:
        return None
    if _SCHEMA_READY:
        return pool
    async with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return pool
        try:
            await pool.execute(_RUNTIME_SESSIONS_SCHEMA_SQL)
            _SCHEMA_READY = True
        except Exception as exc:
            LOGGER.warning("Failed to ensure runtime_sessions schema: %s", exc)
            return None
    return pool


def _persist_sqlite_session(record: Dict[str, Any]) -> None:
    try:
        db_path = _normalized_runtime_state_db_path()
        init_runtime_state_db(db_path)
        upsert_runtime_session(db_path, _sqlite_payload_from_session(record))
    except Exception as exc:
        LOGGER.warning("SQLite session mirror write failed for %s: %s", record.get("session_id"), exc)


def _delete_sqlite_session(session_id: str) -> None:
    try:
        db_path = _normalized_runtime_state_db_path()
        init_runtime_state_db(db_path)
        delete_runtime_session(db_path, session_id)
    except Exception as exc:
        LOGGER.warning("SQLite session mirror delete failed for %s: %s", session_id, exc)


async def get_checkpoint_session(session_id: str) -> Optional[Dict[str, Any]]:
    token = str(session_id or "").strip()
    if not token:
        return None
    try:
        db_path = _normalized_runtime_state_db_path()
        init_runtime_state_db(db_path)
        record = _session_from_sqlite(get_sqlite_runtime_session(db_path, token))
        if isinstance(record, dict):
            record["status"] = "expired" if _is_expired(record) else str(record.get("status") or "active")
        return record
    except Exception as exc:
        LOGGER.warning("SQLite checkpoint session read failed for %s: %s", token, exc)
        return None


async def _get_session_sqlite_compatibility(session_id: str, *, reason: str) -> Optional[Dict[str, Any]]:
    LOGGER.warning(
        "Authoritative runtime_sessions read fell back to SQLite compatibility mode for %s: %s",
        session_id,
        reason,
    )
    return await get_checkpoint_session(session_id)


async def create_session(
    workspace_id: str,
    tenant_id: str,
    actor: Any,
    channel: str,
    metadata: Any = None,
    *,
    session_id: Optional[str] = None,
    ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS,
) -> str:
    token = str(session_id or "").strip() or uuid.uuid4().hex
    existing = await get_session(token) if str(session_id or "").strip() else None
    _enforce_session_lifecycle_mutation(
        "create",
        session_record=existing
        if isinstance(existing, dict)
        else {
            "session_id": token if str(session_id or "").strip() else "",
            "status": "new",
        },
    )
    record = _canonical_session_record(
        session_id=token,
        workspace_id=workspace_id,
        tenant_id=tenant_id,
        channel=channel,
        actor=actor,
        created_at=_utc_now_iso(),
        expires_at=_expires_at_iso(ttl_seconds),
        metadata=metadata,
    )
    pool = await _ensure_runtime_sessions_table()
    if pool is not None:
        try:
            await pool.execute(
                """
                INSERT INTO runtime_sessions (
                    session_id, workspace_id, tenant_id, channel, actor, created_at, expires_at, metadata
                )
                VALUES ($1, $2, $3, $4, $5::jsonb, $6::timestamptz, $7::timestamptz, $8::jsonb)
                ON CONFLICT (session_id) DO UPDATE SET
                    workspace_id = EXCLUDED.workspace_id,
                    tenant_id = EXCLUDED.tenant_id,
                    channel = EXCLUDED.channel,
                    actor = EXCLUDED.actor,
                    created_at = EXCLUDED.created_at,
                    expires_at = EXCLUDED.expires_at,
                    metadata = EXCLUDED.metadata
                """,
                record["session_id"],
                record["workspace_id"],
                record["tenant_id"],
                record["channel"],
                json.dumps(record["actor"], ensure_ascii=False, separators=(",", ":"), default=str),
                _coerce_timestamptz(record["created_at"]),
                _coerce_timestamptz(record["expires_at"]),
                json.dumps(record["metadata"], ensure_ascii=False, separators=(",", ":"), default=str),
            )
        except Exception as exc:
            LOGGER.warning("Postgres create_session failed for %s: %s", token, exc)
    _persist_sqlite_session(record)
    try:
        thread_id = (
            str(record.get("metadata", {}).get("thread_id") or "").strip()
            or str(record.get("session_id") or "").strip()
        )
        await control_plane_repository.upsert_agent_session(
            session_id=record["session_id"],
            tenant_id=record["tenant_id"],
            workspace_id=record["workspace_id"],
            thread_id=thread_id,
            channel=record["channel"],
            actor=record["actor"],
            master_agent_install_id=_metadata_binding_token(record.get("metadata"), "master_agent_install_id"),
            runtime_profile_id=_metadata_binding_token(record.get("metadata"), "runtime_profile_id"),
            metadata=record.get("metadata") if isinstance(record.get("metadata"), dict) else {},
            expires_at=record["expires_at"],
            status=str(record.get("status") or "active").strip() or "active",
        )
    except Exception as exc:
        LOGGER.warning("Control-plane create_session mirror failed for %s: %s", token, exc)
    return token


async def create_local_gateway_session(
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    device_id: str,
    gateway_id: str,
    display_name: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    session_id: Optional[str] = None,
    ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS,
) -> str:
    clean_gateway_id = str(gateway_id or "").strip()
    clean_device_id = str(device_id or "").strip()
    clean_user_id = str(user_id or "").strip()
    clean_workspace_id = str(workspace_id or "").strip() or "default"
    clean_tenant_id = str(tenant_id or "").strip() or "default"
    if not clean_gateway_id or not clean_device_id or not clean_user_id:
        raise ValueError("tenant_id, workspace_id, user_id, device_id, and gateway_id are required.")
    clean_session_id = str(session_id or "").strip() or uuid.uuid4().hex
    merged_metadata = {
        "thread_id": clean_session_id,
        "tenant_id": clean_tenant_id,
        "workspace_id": clean_workspace_id,
        "user_id": clean_user_id,
        "device_id": clean_device_id,
        "gateway_id": clean_gateway_id,
        "session_kind": "local_gateway",
        **dict(metadata or {}),
    }
    return await create_session(
        clean_workspace_id,
        clean_tenant_id,
        actor={
            "type": "gateway",
            "id": clean_gateway_id,
            "display_name": str(display_name or clean_gateway_id).strip() or clean_gateway_id,
        },
        channel="local_runtime_companion",
        metadata=merged_metadata,
        session_id=clean_session_id,
        ttl_seconds=ttl_seconds,
    )


async def get_local_gateway_session(session_id: str) -> Optional[Dict[str, Any]]:
    record = await get_session(session_id)
    if isinstance(record, dict):
        return record
    return await get_checkpoint_session(session_id)


async def get_session(session_id: str) -> Optional[Dict[str, Any]]:
    token = str(session_id or "").strip()
    if not token:
        return None
    pool = await _ensure_runtime_sessions_table()
    if pool is None:
        if _sqlite_session_compatibility_fallback_enabled():
            return await _get_session_sqlite_compatibility(token, reason="authoritative_postgres_unavailable")
        LOGGER.warning(
            "Authoritative runtime_sessions store unavailable for %s; refusing silent SQLite fallback",
            token,
        )
        return None
    try:
        row = await pool.fetchrow(
            """
            SELECT session_id, workspace_id, tenant_id, channel, actor, created_at, expires_at, metadata
            FROM runtime_sessions
            WHERE session_id = $1
            LIMIT 1
            """,
            token,
        )
    except Exception as exc:
        if _sqlite_session_compatibility_fallback_enabled():
            return await _get_session_sqlite_compatibility(token, reason=f"postgres_read_error:{exc}")
        LOGGER.warning(
            "Authoritative runtime_sessions read failed for %s; refusing silent SQLite fallback: %s",
            token,
            exc,
        )
        return None
    record = _session_from_pg(row)
    if isinstance(record, dict):
        record["status"] = "expired" if _is_expired(record) else "active"
        return record
    if _sqlite_session_compatibility_fallback_enabled():
        return await _get_session_sqlite_compatibility(token, reason="postgres_row_missing")
    return None


async def list_workspace_runtime_sessions(
    workspace_id: str,
    *,
    limit: int = 50,
    active_only: bool = True,
) -> list[Dict[str, Any]]:
    resolved_workspace_id = str(workspace_id or "").strip()
    if not resolved_workspace_id:
        return []
    safe_limit = max(1, min(200, int(limit or 50)))
    pool = await _ensure_runtime_sessions_table()
    if pool is None:
        LOGGER.warning("Authoritative runtime_sessions store unavailable; workspace session list refused.")
        return []
    where_clause = "workspace_id = $1"
    if active_only:
        where_clause += " AND expires_at > NOW()"
    try:
        rows = await pool.fetch(
            f"""
            SELECT session_id, workspace_id, tenant_id, channel, actor, created_at, expires_at, metadata
            FROM runtime_sessions
            WHERE {where_clause}
            ORDER BY created_at DESC
            LIMIT $2
            """,
            resolved_workspace_id,
            safe_limit,
        )
    except Exception as exc:
        LOGGER.warning("Postgres list_workspace_runtime_sessions failed for %s: %s", resolved_workspace_id, exc)
        return []
    records: list[Dict[str, Any]] = []
    for row in rows or []:
        record = _session_from_pg(row)
        if not isinstance(record, dict):
            continue
        record["status"] = "expired" if _is_expired(record) else "active"
        records.append(record)
    return records


async def find_runtime_session_by_approval_id(
    approval_id: str,
    *,
    workspace_id: Optional[str] = None,
    active_only: bool = True,
) -> Optional[Dict[str, Any]]:
    token = str(approval_id or "").strip()
    if not token:
        return None
    pool = await _ensure_runtime_sessions_table()
    if pool is None:
        LOGGER.warning("Authoritative runtime_sessions store unavailable; approval session lookup refused.")
        return None
    resolved_workspace_id = str(workspace_id or "").strip()
    where = [
        """
        EXISTS (
            SELECT 1
            FROM jsonb_array_elements(COALESCE(metadata->'approvals', '[]'::jsonb)) AS approval
            WHERE approval->>'approval_id' = $1
        )
        """,
    ]
    params: list[Any] = [token]
    if resolved_workspace_id:
        params.append(resolved_workspace_id)
        where.append(f"workspace_id = ${len(params)}")
    if active_only:
        where.append("expires_at > NOW()")
    try:
        row = await pool.fetchrow(
            f"""
            SELECT session_id, workspace_id, tenant_id, channel, actor, created_at, expires_at, metadata
            FROM runtime_sessions
            WHERE {' AND '.join(where)}
            ORDER BY created_at DESC
            LIMIT 1
            """,
            *params,
        )
    except Exception as exc:
        LOGGER.warning("Postgres find_runtime_session_by_approval_id failed for %s: %s", token, exc)
        return None
    record = _session_from_pg(row)
    if isinstance(record, dict):
        record["status"] = "expired" if _is_expired(record) else "active"
        return record
    return None


async def get_session_scoped(
    session_id: str,
    *,
    workspace_id: str,
    tenant_id: str,
    channel: str,
    thread_id: str,
    enforce: Optional[bool] = None,
) -> Optional[Dict[str, Any]]:
    record = await get_session(session_id)
    if not isinstance(record, dict):
        return None
    mismatch = _scope_mismatch(
        record,
        workspace_id=workspace_id,
        tenant_id=tenant_id,
        channel=channel,
        thread_id=thread_id,
    )
    if not mismatch:
        return record
    LOGGER.warning("Scoped session mismatch for %s: %s", session_id, mismatch)
    if enforce is None:
        enforce = _scope_enforcement_enabled()
    if enforce:
        raise SessionScopeViolationError(
            "Provided session_id does not belong to the requested workspace, channel, or thread scope.",
            mismatch=mismatch,
        )
    shadow_record = dict(record)
    shadow_record["_scope_mismatch"] = mismatch
    return shadow_record


async def extend_session(session_id: str, *, metadata_updates: Any = None) -> Optional[Dict[str, Any]]:
    existing = await get_session(session_id)
    if not isinstance(existing, dict):
        return None
    if isinstance(metadata_updates, dict) and metadata_updates:
        merged_metadata = _coerce_dict(existing.get("metadata"))
        merged_metadata.update({key: value for key, value in metadata_updates.items() if value is not None})
        existing["metadata"] = merged_metadata
    existing["expires_at"] = _expires_at_iso(DEFAULT_SESSION_TTL_SECONDS)
    existing["status"] = "active"
    pool = await _ensure_runtime_sessions_table()
    if pool is not None:
        try:
            await pool.execute(
                """
                UPDATE runtime_sessions
                SET expires_at = $2::timestamptz, metadata = $3::jsonb
                WHERE session_id = $1
                """,
                existing["session_id"],
                _coerce_timestamptz(existing["expires_at"]),
                json.dumps(existing.get("metadata") or {}, ensure_ascii=False, separators=(",", ":"), default=str),
            )
        except Exception as exc:
            LOGGER.warning("Postgres extend_session failed for %s: %s", session_id, exc)
    _persist_sqlite_session(existing)
    try:
        thread_id = (
            str(existing.get("metadata", {}).get("thread_id") or "").strip()
            or str(existing.get("session_id") or "").strip()
        )
        await control_plane_repository.upsert_agent_session(
            session_id=existing["session_id"],
            tenant_id=existing["tenant_id"],
            workspace_id=existing["workspace_id"],
            thread_id=thread_id,
            channel=existing["channel"],
            actor=existing["actor"],
            master_agent_install_id=_metadata_binding_token(existing.get("metadata"), "master_agent_install_id"),
            runtime_profile_id=_metadata_binding_token(existing.get("metadata"), "runtime_profile_id"),
            metadata=existing.get("metadata") if isinstance(existing.get("metadata"), dict) else {},
            expires_at=existing["expires_at"],
            status="active",
        )
    except Exception as exc:
        LOGGER.warning("Control-plane extend_session mirror failed for %s: %s", session_id, exc)
    return existing


async def terminate_session(session_id: str) -> None:
    token = str(session_id or "").strip()
    if not token:
        return None
    existing = await get_session(token)
    _enforce_session_lifecycle_mutation(
        "close",
        session_record=existing
        if isinstance(existing, dict)
        else {
            "session_id": token,
            "status": "active",
        },
    )
    pool = await _ensure_runtime_sessions_table()
    if pool is not None:
        try:
            await pool.execute("DELETE FROM runtime_sessions WHERE session_id = $1", token)
        except Exception as exc:
            LOGGER.warning("Postgres terminate_session failed for %s: %s", token, exc)
    _delete_sqlite_session(token)
    try:
        if isinstance(existing, dict):
            await control_plane_repository.terminate_agent_session(
                token,
                tenant_id=str(existing.get("tenant_id") or "").strip(),
                workspace_id=str(existing.get("workspace_id") or "").strip(),
            )
    except Exception as exc:
        LOGGER.warning("Control-plane terminate_session mirror failed for %s: %s", token, exc)
    return None


async def prune_expired_sessions(workspace_id: str, *, limit: int = 50) -> Dict[str, Any]:
    resolved_workspace_id = str(workspace_id or "").strip()
    if not resolved_workspace_id:
        return {"workspace_id": "", "pruned": 0, "errors": 0}
    safe_limit = max(1, min(200, int(limit or 50)))
    sessions = await list_workspace_runtime_sessions(
        resolved_workspace_id, limit=safe_limit, active_only=False
    )
    pruned = 0
    errors = 0
    for session in sessions:
        session_id = str(session.get("session_id") or "")
        if not session_id:
            continue
        status = str(session.get("status") or "")
        if status != "expired":
            continue
        try:
            await terminate_session(session_id)
            pruned += 1
        except Exception:
            errors += 1
    return {
        "workspace_id": resolved_workspace_id,
        "scanned": len(sessions),
        "pruned": pruned,
        "errors": errors,
    }


async def renew_session(session_id: str, *, ttl_seconds: int | None = None) -> Optional[Dict[str, Any]]:
    token = str(session_id or "").strip()
    if not token:
        return None
    return await extend_session(token)

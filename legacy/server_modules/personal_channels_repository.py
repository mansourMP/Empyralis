from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


EMPYRALIS_STATE_HOME = Path(
    os.getenv("EMPYRALIS_STATE_HOME", str(Path.home() / ".empyralis" / "state"))
).expanduser()
PERSONAL_CHANNELS_DB_FILE = (
    Path(
        os.getenv(
            "EMPYRALIS_PERSONAL_CHANNELS_DB",
            EMPYRALIS_STATE_HOME / "personal_channels" / "personal-channels.sqlite3",
        )
    )
).expanduser()
_DB_LOCK = threading.Lock()

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS personal_channel_whatsapp_states (
    gateway_id TEXT NOT NULL,
    channel_key TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    status TEXT NOT NULL,
    qr_code TEXT NULL,
    linked_jid TEXT NULL,
    linked_name TEXT NULL,
    connected_at TEXT NULL,
    last_event_at TEXT NULL,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (gateway_id, channel_key)
);

CREATE TABLE IF NOT EXISTS personal_channel_telegram_states (
    gateway_id TEXT NOT NULL,
    channel_key TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    status TEXT NOT NULL,
    login_hint TEXT NULL,
    linked_user_id TEXT NULL,
    linked_username TEXT NULL,
    linked_phone TEXT NULL,
    linked_name TEXT NULL,
    connected_at TEXT NULL,
    last_event_at TEXT NULL,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (gateway_id, channel_key)
);

CREATE TABLE IF NOT EXISTS personal_channel_inbound_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gateway_id TEXT NOT NULL,
    channel_key TEXT NOT NULL,
    external_message_id TEXT NOT NULL,
    remote_jid TEXT NOT NULL,
    sender_jid TEXT NULL,
    push_name TEXT NULL,
    text TEXT NOT NULL,
    reply_idempotency_key TEXT NULL,
    processed_at TEXT NULL,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (gateway_id, channel_key, external_message_id)
);

CREATE TABLE IF NOT EXISTS personal_channel_outbound_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gateway_id TEXT NOT NULL,
    channel_key TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    remote_jid TEXT NOT NULL,
    text TEXT NOT NULL,
    reply_to_external_message_id TEXT NULL,
    external_message_id TEXT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    delivered_at TEXT NULL,
    UNIQUE (gateway_id, channel_key, idempotency_key)
);
"""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _connect(db_path: Optional[Path | str] = None) -> sqlite3.Connection:
    resolved = Path(db_path or PERSONAL_CHANNELS_DB_FILE).expanduser()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(resolved, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.executescript(SCHEMA_SQL)
    connection.commit()
    return connection


def _json_dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, sort_keys=True)


def _json_loads(value: Any, *, default: Any) -> Any:
    if not isinstance(value, str) or not value.strip():
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def init_personal_channels_db(db_path: Optional[Path | str] = None) -> Path:
    resolved = Path(db_path or PERSONAL_CHANNELS_DB_FILE).expanduser()
    with _DB_LOCK:
        connection = _connect(resolved)
        connection.close()
    return resolved


def upsert_whatsapp_state(
    *,
    gateway_id: str,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    channel_key: str,
    provider: str,
    status: str,
    qr_code: Optional[str] = None,
    linked_jid: Optional[str] = None,
    linked_name: Optional[str] = None,
    connected_at: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    db_path: Optional[Path | str] = None,
) -> Dict[str, Any]:
    now_iso = _utc_now_iso()
    with _DB_LOCK:
        connection = _connect(db_path)
        try:
            existing_row = connection.execute(
                """
                SELECT * FROM personal_channel_whatsapp_states
                WHERE gateway_id = ? AND channel_key = ?
                """,
                (str(gateway_id or "").strip(), str(channel_key or "").strip()),
            ).fetchone()
            existing_metadata = (
                _json_loads(existing_row["metadata"], default={})
                if existing_row is not None
                else {}
            )
            merged_metadata = dict(existing_metadata or {})
            merged_metadata.update(dict(metadata or {}))
            connection.execute(
                """
                INSERT INTO personal_channel_whatsapp_states (
                    gateway_id, channel_key, tenant_id, workspace_id, user_id, provider,
                    status, qr_code, linked_jid, linked_name, connected_at, last_event_at,
                    metadata, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(gateway_id, channel_key) DO UPDATE SET
                    tenant_id=excluded.tenant_id,
                    workspace_id=excluded.workspace_id,
                    user_id=excluded.user_id,
                    provider=excluded.provider,
                    status=excluded.status,
                    qr_code=excluded.qr_code,
                    linked_jid=excluded.linked_jid,
                    linked_name=excluded.linked_name,
                    connected_at=excluded.connected_at,
                    last_event_at=excluded.last_event_at,
                    metadata=excluded.metadata,
                    updated_at=excluded.updated_at
                """,
                (
                    str(gateway_id or "").strip(),
                    str(channel_key or "").strip(),
                    str(tenant_id or "").strip(),
                    str(workspace_id or "").strip(),
                    str(user_id or "").strip(),
                    str(provider or "").strip(),
                    str(status or "").strip() or "idle",
                    str(qr_code or "").strip() or None,
                    str(linked_jid or "").strip() or None,
                    str(linked_name or "").strip() or None,
                    str(connected_at or "").strip() or None,
                    now_iso,
                    _json_dumps(merged_metadata),
                    str(existing_row["created_at"] or now_iso) if existing_row is not None else now_iso,
                    now_iso,
                ),
            )
            connection.commit()
            row = connection.execute(
                """
                SELECT * FROM personal_channel_whatsapp_states
                WHERE gateway_id = ? AND channel_key = ?
                """,
                (str(gateway_id or "").strip(), str(channel_key or "").strip()),
            ).fetchone()
        finally:
            connection.close()
    return _whatsapp_state_from_row(row) or {}


def get_whatsapp_state(
    gateway_id: str,
    *,
    channel_key: str,
    db_path: Optional[Path | str] = None,
) -> Optional[Dict[str, Any]]:
    with _DB_LOCK:
        connection = _connect(db_path)
        try:
            row = connection.execute(
                """
                SELECT * FROM personal_channel_whatsapp_states
                WHERE gateway_id = ? AND channel_key = ?
                """,
                (str(gateway_id or "").strip(), str(channel_key or "").strip()),
            ).fetchone()
        finally:
            connection.close()
    return _whatsapp_state_from_row(row)


def upsert_telegram_state(
    *,
    gateway_id: str,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    channel_key: str,
    provider: str,
    status: str,
    login_hint: Optional[str] = None,
    linked_user_id: Optional[str] = None,
    linked_username: Optional[str] = None,
    linked_phone: Optional[str] = None,
    linked_name: Optional[str] = None,
    connected_at: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    db_path: Optional[Path | str] = None,
) -> Dict[str, Any]:
    now_iso = _utc_now_iso()
    with _DB_LOCK:
        connection = _connect(db_path)
        try:
            existing_row = connection.execute(
                """
                SELECT * FROM personal_channel_telegram_states
                WHERE gateway_id = ? AND channel_key = ?
                """,
                (str(gateway_id or "").strip(), str(channel_key or "").strip()),
            ).fetchone()
            existing_metadata = (
                _json_loads(existing_row["metadata"], default={})
                if existing_row is not None
                else {}
            )
            merged_metadata = dict(existing_metadata or {})
            merged_metadata.update(dict(metadata or {}))
            connection.execute(
                """
                INSERT INTO personal_channel_telegram_states (
                    gateway_id, channel_key, tenant_id, workspace_id, user_id, provider,
                    status, login_hint, linked_user_id, linked_username, linked_phone,
                    linked_name, connected_at, last_event_at, metadata, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(gateway_id, channel_key) DO UPDATE SET
                    tenant_id=excluded.tenant_id,
                    workspace_id=excluded.workspace_id,
                    user_id=excluded.user_id,
                    provider=excluded.provider,
                    status=excluded.status,
                    login_hint=excluded.login_hint,
                    linked_user_id=excluded.linked_user_id,
                    linked_username=excluded.linked_username,
                    linked_phone=excluded.linked_phone,
                    linked_name=excluded.linked_name,
                    connected_at=excluded.connected_at,
                    last_event_at=excluded.last_event_at,
                    metadata=excluded.metadata,
                    updated_at=excluded.updated_at
                """,
                (
                    str(gateway_id or "").strip(),
                    str(channel_key or "").strip(),
                    str(tenant_id or "").strip(),
                    str(workspace_id or "").strip(),
                    str(user_id or "").strip(),
                    str(provider or "").strip(),
                    str(status or "").strip() or "idle",
                    str(login_hint or "").strip() or None,
                    str(linked_user_id or "").strip() or None,
                    str(linked_username or "").strip() or None,
                    str(linked_phone or "").strip() or None,
                    str(linked_name or "").strip() or None,
                    str(connected_at or "").strip() or None,
                    now_iso,
                    _json_dumps(merged_metadata),
                    str(existing_row["created_at"] or now_iso) if existing_row is not None else now_iso,
                    now_iso,
                ),
            )
            connection.commit()
            row = connection.execute(
                """
                SELECT * FROM personal_channel_telegram_states
                WHERE gateway_id = ? AND channel_key = ?
                """,
                (str(gateway_id or "").strip(), str(channel_key or "").strip()),
            ).fetchone()
        finally:
            connection.close()
    return _telegram_state_from_row(row) or {}


def get_telegram_state(
    gateway_id: str,
    *,
    channel_key: str,
    db_path: Optional[Path | str] = None,
) -> Optional[Dict[str, Any]]:
    with _DB_LOCK:
        connection = _connect(db_path)
        try:
            row = connection.execute(
                """
                SELECT * FROM personal_channel_telegram_states
                WHERE gateway_id = ? AND channel_key = ?
                """,
                (str(gateway_id or "").strip(), str(channel_key or "").strip()),
            ).fetchone()
        finally:
            connection.close()
    return _telegram_state_from_row(row)


def record_inbound_message(
    *,
    gateway_id: str,
    channel_key: str,
    external_message_id: str,
    remote_jid: str,
    sender_jid: Optional[str],
    push_name: Optional[str],
    text: str,
    metadata: Optional[Dict[str, Any]] = None,
    db_path: Optional[Path | str] = None,
) -> Tuple[Dict[str, Any], bool]:
    now_iso = _utc_now_iso()
    created = False
    with _DB_LOCK:
        connection = _connect(db_path)
        try:
            existing_row = connection.execute(
                """
                SELECT * FROM personal_channel_inbound_messages
                WHERE gateway_id = ? AND channel_key = ? AND external_message_id = ?
                """,
                (
                    str(gateway_id or "").strip(),
                    str(channel_key or "").strip(),
                    str(external_message_id or "").strip(),
                ),
            ).fetchone()
            if existing_row is None:
                created = True
                connection.execute(
                    """
                    INSERT INTO personal_channel_inbound_messages (
                        gateway_id, channel_key, external_message_id, remote_jid, sender_jid,
                        push_name, text, reply_idempotency_key, processed_at, metadata,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?)
                    """,
                    (
                        str(gateway_id or "").strip(),
                        str(channel_key or "").strip(),
                        str(external_message_id or "").strip(),
                        str(remote_jid or "").strip(),
                        str(sender_jid or "").strip() or None,
                        str(push_name or "").strip() or None,
                        str(text or "").strip(),
                        _json_dumps(metadata or {}),
                        now_iso,
                        now_iso,
                    ),
                )
                connection.commit()
                row = connection.execute(
                    """
                    SELECT * FROM personal_channel_inbound_messages
                    WHERE gateway_id = ? AND channel_key = ? AND external_message_id = ?
                    """,
                    (
                        str(gateway_id or "").strip(),
                        str(channel_key or "").strip(),
                        str(external_message_id or "").strip(),
                    ),
                ).fetchone()
            else:
                row = existing_row
        finally:
            connection.close()
    return (_inbound_message_from_row(row) or {}, created)


def mark_inbound_processed(
    *,
    gateway_id: str,
    channel_key: str,
    external_message_id: str,
    reply_idempotency_key: str,
    db_path: Optional[Path | str] = None,
) -> Optional[Dict[str, Any]]:
    now_iso = _utc_now_iso()
    with _DB_LOCK:
        connection = _connect(db_path)
        try:
            connection.execute(
                """
                UPDATE personal_channel_inbound_messages
                SET reply_idempotency_key = ?, processed_at = ?, updated_at = ?
                WHERE gateway_id = ? AND channel_key = ? AND external_message_id = ?
                """,
                (
                    str(reply_idempotency_key or "").strip(),
                    now_iso,
                    now_iso,
                    str(gateway_id or "").strip(),
                    str(channel_key or "").strip(),
                    str(external_message_id or "").strip(),
                ),
            )
            connection.commit()
            row = connection.execute(
                """
                SELECT * FROM personal_channel_inbound_messages
                WHERE gateway_id = ? AND channel_key = ? AND external_message_id = ?
                """,
                (
                    str(gateway_id or "").strip(),
                    str(channel_key or "").strip(),
                    str(external_message_id or "").strip(),
                ),
            ).fetchone()
        finally:
            connection.close()
    return _inbound_message_from_row(row)


def create_or_get_outbound_message(
    *,
    gateway_id: str,
    channel_key: str,
    idempotency_key: str,
    remote_jid: str,
    text: str,
    reply_to_external_message_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    db_path: Optional[Path | str] = None,
) -> Tuple[Dict[str, Any], bool]:
    now_iso = _utc_now_iso()
    created = False
    with _DB_LOCK:
        connection = _connect(db_path)
        try:
            existing_row = connection.execute(
                """
                SELECT * FROM personal_channel_outbound_messages
                WHERE gateway_id = ? AND channel_key = ? AND idempotency_key = ?
                """,
                (
                    str(gateway_id or "").strip(),
                    str(channel_key or "").strip(),
                    str(idempotency_key or "").strip(),
                ),
            ).fetchone()
            if existing_row is None:
                created = True
                connection.execute(
                    """
                    INSERT INTO personal_channel_outbound_messages (
                        gateway_id, channel_key, idempotency_key, remote_jid, text,
                        reply_to_external_message_id, external_message_id, status,
                        metadata, created_at, updated_at, delivered_at
                    ) VALUES (?, ?, ?, ?, ?, ?, NULL, 'pending', ?, ?, ?, NULL)
                    """,
                    (
                        str(gateway_id or "").strip(),
                        str(channel_key or "").strip(),
                        str(idempotency_key or "").strip(),
                        str(remote_jid or "").strip(),
                        str(text or "").strip(),
                        str(reply_to_external_message_id or "").strip() or None,
                        _json_dumps(metadata or {}),
                        now_iso,
                        now_iso,
                    ),
                )
                connection.commit()
                row = connection.execute(
                    """
                    SELECT * FROM personal_channel_outbound_messages
                    WHERE gateway_id = ? AND channel_key = ? AND idempotency_key = ?
                    """,
                    (
                        str(gateway_id or "").strip(),
                        str(channel_key or "").strip(),
                        str(idempotency_key or "").strip(),
                    ),
                ).fetchone()
            else:
                row = existing_row
        finally:
            connection.close()
    return (_outbound_message_from_row(row) or {}, created)


def get_outbound_message(
    *,
    gateway_id: str,
    channel_key: str,
    idempotency_key: str,
    db_path: Optional[Path | str] = None,
) -> Optional[Dict[str, Any]]:
    with _DB_LOCK:
        connection = _connect(db_path)
        try:
            row = connection.execute(
                """
                SELECT * FROM personal_channel_outbound_messages
                WHERE gateway_id = ? AND channel_key = ? AND idempotency_key = ?
                """,
                (
                    str(gateway_id or "").strip(),
                    str(channel_key or "").strip(),
                    str(idempotency_key or "").strip(),
                ),
            ).fetchone()
        finally:
            connection.close()
    return _outbound_message_from_row(row)


def mark_outbound_delivered(
    *,
    gateway_id: str,
    channel_key: str,
    idempotency_key: str,
    external_message_id: Optional[str],
    metadata: Optional[Dict[str, Any]] = None,
    db_path: Optional[Path | str] = None,
) -> Optional[Dict[str, Any]]:
    now_iso = _utc_now_iso()
    with _DB_LOCK:
        connection = _connect(db_path)
        try:
            existing_row = connection.execute(
                """
                SELECT * FROM personal_channel_outbound_messages
                WHERE gateway_id = ? AND channel_key = ? AND idempotency_key = ?
                """,
                (
                    str(gateway_id or "").strip(),
                    str(channel_key or "").strip(),
                    str(idempotency_key or "").strip(),
                ),
            ).fetchone()
            existing_metadata = (
                _json_loads(existing_row["metadata"], default={})
                if existing_row is not None
                else {}
            )
            merged_metadata = dict(existing_metadata or {})
            merged_metadata.update(dict(metadata or {}))
            connection.execute(
                """
                UPDATE personal_channel_outbound_messages
                SET status = 'delivered',
                    external_message_id = ?,
                    metadata = ?,
                    updated_at = ?,
                    delivered_at = ?
                WHERE gateway_id = ? AND channel_key = ? AND idempotency_key = ?
                """,
                (
                    str(external_message_id or "").strip() or None,
                    _json_dumps(merged_metadata),
                    now_iso,
                    now_iso,
                    str(gateway_id or "").strip(),
                    str(channel_key or "").strip(),
                    str(idempotency_key or "").strip(),
                ),
            )
            connection.commit()
            row = connection.execute(
                """
                SELECT * FROM personal_channel_outbound_messages
                WHERE gateway_id = ? AND channel_key = ? AND idempotency_key = ?
                """,
                (
                    str(gateway_id or "").strip(),
                    str(channel_key or "").strip(),
                    str(idempotency_key or "").strip(),
                ),
            ).fetchone()
        finally:
            connection.close()
    return _outbound_message_from_row(row)


def list_recent_gateway_messages(
    gateway_id: str,
    *,
    channel_key: str,
    limit: int = 20,
    db_path: Optional[Path | str] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    with _DB_LOCK:
        connection = _connect(db_path)
        try:
            inbound_rows = connection.execute(
                """
                SELECT * FROM personal_channel_inbound_messages
                WHERE gateway_id = ? AND channel_key = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (str(gateway_id or "").strip(), str(channel_key or "").strip(), max(int(limit or 0), 1)),
            ).fetchall()
            outbound_rows = connection.execute(
                """
                SELECT * FROM personal_channel_outbound_messages
                WHERE gateway_id = ? AND channel_key = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (str(gateway_id or "").strip(), str(channel_key or "").strip(), max(int(limit or 0), 1)),
            ).fetchall()
        finally:
            connection.close()
    return {
        "inbound": [_inbound_message_from_row(row) or {} for row in inbound_rows],
        "outbound": [_outbound_message_from_row(row) or {} for row in outbound_rows],
    }


def _whatsapp_state_from_row(row: sqlite3.Row | None) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    return {
        "gateway_id": str(row["gateway_id"] or ""),
        "channel_key": str(row["channel_key"] or ""),
        "tenant_id": str(row["tenant_id"] or ""),
        "workspace_id": str(row["workspace_id"] or ""),
        "user_id": str(row["user_id"] or ""),
        "provider": str(row["provider"] or ""),
        "status": str(row["status"] or ""),
        "qr_code": str(row["qr_code"] or "").strip() or None,
        "linked_jid": str(row["linked_jid"] or "").strip() or None,
        "linked_name": str(row["linked_name"] or "").strip() or None,
        "connected_at": str(row["connected_at"] or "").strip() or None,
        "last_event_at": str(row["last_event_at"] or "").strip() or None,
        "metadata": _json_loads(row["metadata"], default={}),
        "created_at": str(row["created_at"] or ""),
        "updated_at": str(row["updated_at"] or ""),
    }


def _telegram_state_from_row(row: sqlite3.Row | None) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    return {
        "gateway_id": str(row["gateway_id"] or ""),
        "channel_key": str(row["channel_key"] or ""),
        "tenant_id": str(row["tenant_id"] or ""),
        "workspace_id": str(row["workspace_id"] or ""),
        "user_id": str(row["user_id"] or ""),
        "provider": str(row["provider"] or ""),
        "status": str(row["status"] or ""),
        "login_hint": str(row["login_hint"] or "").strip() or None,
        "linked_user_id": str(row["linked_user_id"] or "").strip() or None,
        "linked_username": str(row["linked_username"] or "").strip() or None,
        "linked_phone": str(row["linked_phone"] or "").strip() or None,
        "linked_name": str(row["linked_name"] or "").strip() or None,
        "connected_at": str(row["connected_at"] or "").strip() or None,
        "last_event_at": str(row["last_event_at"] or "").strip() or None,
        "metadata": _json_loads(row["metadata"], default={}),
        "created_at": str(row["created_at"] or ""),
        "updated_at": str(row["updated_at"] or ""),
    }


def _inbound_message_from_row(row: sqlite3.Row | None) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    return {
        "gateway_id": str(row["gateway_id"] or ""),
        "channel_key": str(row["channel_key"] or ""),
        "external_message_id": str(row["external_message_id"] or ""),
        "remote_jid": str(row["remote_jid"] or ""),
        "sender_jid": str(row["sender_jid"] or "").strip() or None,
        "push_name": str(row["push_name"] or "").strip() or None,
        "text": str(row["text"] or ""),
        "reply_idempotency_key": str(row["reply_idempotency_key"] or "").strip() or None,
        "processed_at": str(row["processed_at"] or "").strip() or None,
        "metadata": _json_loads(row["metadata"], default={}),
        "created_at": str(row["created_at"] or ""),
        "updated_at": str(row["updated_at"] or ""),
    }


def _outbound_message_from_row(row: sqlite3.Row | None) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    return {
        "gateway_id": str(row["gateway_id"] or ""),
        "channel_key": str(row["channel_key"] or ""),
        "idempotency_key": str(row["idempotency_key"] or ""),
        "remote_jid": str(row["remote_jid"] or ""),
        "text": str(row["text"] or ""),
        "reply_to_external_message_id": str(row["reply_to_external_message_id"] or "").strip() or None,
        "external_message_id": str(row["external_message_id"] or "").strip() or None,
        "status": str(row["status"] or ""),
        "metadata": _json_loads(row["metadata"], default={}),
        "created_at": str(row["created_at"] or ""),
        "updated_at": str(row["updated_at"] or ""),
        "delivered_at": str(row["delivered_at"] or "").strip() or None,
    }

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

# Sentinel agent_id for rows that predate per-agent scoping, or that were
# deliberately paired workspace-wide (Sage's own legacy Connect tab still
# does this — PersonalChannelConnectPanel.tsx's `agentGatewayId === undefined`
# path). NOT NULL so it can sit in a composite PRIMARY KEY/UNIQUE constraint
# without SQLite's NULL-is-never-equal-to-NULL semantics silently allowing
# duplicate "unscoped" rows.
LEGACY_UNSCOPED_AGENT_ID = ""

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS personal_channel_whatsapp_states (
    gateway_id TEXT NOT NULL,
    channel_key TEXT NOT NULL,
    agent_id TEXT NOT NULL DEFAULT '',
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
    PRIMARY KEY (gateway_id, channel_key, agent_id)
);

CREATE TABLE IF NOT EXISTS personal_channel_telegram_states (
    gateway_id TEXT NOT NULL,
    channel_key TEXT NOT NULL,
    agent_id TEXT NOT NULL DEFAULT '',
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
    PRIMARY KEY (gateway_id, channel_key, agent_id)
);

-- Signal/iMessage/WeChat-personal ("local-bridge" channels — an
-- externally-authenticated OS-level bridge like signal-cli or Messages.app,
-- not an in-app phone/QR login) had no per-agent identity table at all
-- until this one: unlike WhatsApp/Telegram, there is no configure_*
-- step that names an agent_id up front (see personal_channels_service.py's
-- _resolve_local_bridge_agent_id docstring for how a row here gets
-- claimed instead — either an explicit owner action for a channel that has
-- one, like iMessage's recheck/install routes, or a reverse
-- preferred_gateway_id lookup for channels that don't, like Signal).
-- Same (gateway_id, channel_key, agent_id) shape as the WhatsApp/Telegram
-- tables above so find_agent_id_for_local_bridge_session can reuse their
-- exact "most recently touched row wins" contract.
CREATE TABLE IF NOT EXISTS personal_channel_local_bridge_states (
    gateway_id TEXT NOT NULL,
    channel_key TEXT NOT NULL,
    agent_id TEXT NOT NULL DEFAULT '',
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    status TEXT NOT NULL,
    linked_identity TEXT NULL,
    connected_at TEXT NULL,
    last_event_at TEXT NULL,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (gateway_id, channel_key, agent_id)
);

CREATE TABLE IF NOT EXISTS personal_channel_inbound_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gateway_id TEXT NOT NULL,
    channel_key TEXT NOT NULL,
    agent_id TEXT NOT NULL DEFAULT '',
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
    UNIQUE (gateway_id, channel_key, agent_id, external_message_id)
);

CREATE TABLE IF NOT EXISTS personal_channel_outbound_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gateway_id TEXT NOT NULL,
    channel_key TEXT NOT NULL,
    agent_id TEXT NOT NULL DEFAULT '',
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
    UNIQUE (gateway_id, channel_key, agent_id, idempotency_key)
);
"""

# Tables that predate per-agent scoping and need a real migration (SQLite
# can't ALTER a PRIMARY KEY in place) rather than just CREATE TABLE IF NOT
# EXISTS, which only handles a fresh install. Each entry is
# (table_name, create_sql_for_the_NEW_shape, columns_to_copy_in_order).
# Pre-existing rows land under LEGACY_UNSCOPED_AGENT_ID — that's exactly the
# workspace-wide state the old (gateway_id, channel_key)-only schema always
# meant, so nothing about their behavior changes; new per-agent rows simply
# become possible alongside them.
_LEGACY_MIGRATIONS: List[Tuple[str, List[str]]] = [
    (
        "personal_channel_whatsapp_states",
        [
            "gateway_id", "channel_key", "tenant_id", "workspace_id", "user_id", "provider",
            "status", "qr_code", "linked_jid", "linked_name", "connected_at", "last_event_at",
            "metadata", "created_at", "updated_at",
        ],
    ),
    (
        "personal_channel_telegram_states",
        [
            "gateway_id", "channel_key", "tenant_id", "workspace_id", "user_id", "provider",
            "status", "login_hint", "linked_user_id", "linked_username", "linked_phone",
            "linked_name", "connected_at", "last_event_at", "metadata", "created_at", "updated_at",
        ],
    ),
    (
        "personal_channel_inbound_messages",
        [
            "gateway_id", "channel_key", "external_message_id", "remote_jid", "sender_jid",
            "push_name", "text", "reply_idempotency_key", "processed_at", "metadata",
            "created_at", "updated_at",
        ],
    ),
    (
        "personal_channel_outbound_messages",
        [
            "gateway_id", "channel_key", "idempotency_key", "remote_jid", "text",
            "reply_to_external_message_id", "external_message_id", "status", "metadata",
            "created_at", "updated_at", "delivered_at",
        ],
    ),
]


def _table_has_column(connection: sqlite3.Connection, table: str, column: str) -> bool:
    rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    return any(str(row[1]) == column for row in rows)


def _migrate_legacy_tables_to_agent_scoped(connection: sqlite3.Connection) -> None:
    """One-time, idempotent upgrade for DBs created before agent_id existed.
    CREATE TABLE IF NOT EXISTS above only covers a fresh install; an existing
    table keeps its old (gateway_id, channel_key) PRIMARY KEY forever unless
    rebuilt, since SQLite has no ALTER TABLE ... ADD/CHANGE PRIMARY KEY.
    Standard SQLite migration shape: rename old -> _legacy, CREATE the new
    schema (already agent-scoped, from SCHEMA_SQL), copy every existing row
    in under LEGACY_UNSCOPED_AGENT_ID, drop the renamed table."""
    for table, columns in _LEGACY_MIGRATIONS:
        if _table_has_column(connection, table, "agent_id"):
            continue  # already migrated (or freshly created with the new schema)
        legacy_table = f"{table}__pre_agent_scope"
        connection.execute(f"ALTER TABLE {table} RENAME TO {legacy_table}")
        connection.executescript(SCHEMA_SQL)  # recreates `table` in its new, agent-scoped shape
        column_list = ", ".join(columns)
        connection.execute(
            f"""
            INSERT INTO {table} (agent_id, {column_list})
            SELECT ?, {column_list} FROM {legacy_table}
            """,
            (LEGACY_UNSCOPED_AGENT_ID,),
        )
        connection.execute(f"DROP TABLE {legacy_table}")
        connection.commit()


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
    _migrate_legacy_tables_to_agent_scoped(connection)
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


def _norm_agent_id(agent_id: Optional[str]) -> str:
    return str(agent_id or "").strip()


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
    agent_id: str = LEGACY_UNSCOPED_AGENT_ID,
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
    normalized_agent_id = _norm_agent_id(agent_id)
    with _DB_LOCK:
        connection = _connect(db_path)
        try:
            existing_row = connection.execute(
                """
                SELECT * FROM personal_channel_whatsapp_states
                WHERE gateway_id = ? AND channel_key = ? AND agent_id = ?
                """,
                (str(gateway_id or "").strip(), str(channel_key or "").strip(), normalized_agent_id),
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
                    gateway_id, channel_key, agent_id, tenant_id, workspace_id, user_id, provider,
                    status, qr_code, linked_jid, linked_name, connected_at, last_event_at,
                    metadata, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(gateway_id, channel_key, agent_id) DO UPDATE SET
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
                    normalized_agent_id,
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
                WHERE gateway_id = ? AND channel_key = ? AND agent_id = ?
                """,
                (str(gateway_id or "").strip(), str(channel_key or "").strip(), normalized_agent_id),
            ).fetchone()
        finally:
            connection.close()
    return _whatsapp_state_from_row(row) or {}


def get_whatsapp_state(
    gateway_id: str,
    *,
    channel_key: str,
    agent_id: str = LEGACY_UNSCOPED_AGENT_ID,
    db_path: Optional[Path | str] = None,
) -> Optional[Dict[str, Any]]:
    with _DB_LOCK:
        connection = _connect(db_path)
        try:
            row = connection.execute(
                """
                SELECT * FROM personal_channel_whatsapp_states
                WHERE gateway_id = ? AND channel_key = ? AND agent_id = ?
                """,
                (str(gateway_id or "").strip(), str(channel_key or "").strip(), _norm_agent_id(agent_id)),
            ).fetchone()
        finally:
            connection.close()
    return _whatsapp_state_from_row(row)


def list_whatsapp_states_for_gateway(
    gateway_id: str,
    *,
    channel_key: str,
    db_path: Optional[Path | str] = None,
) -> List[Dict[str, Any]]:
    """Every agent's WhatsApp session on this gateway — used for the
    Channels-tab pill fix (per-agent, not "any session on this box")."""
    with _DB_LOCK:
        connection = _connect(db_path)
        try:
            rows = connection.execute(
                """
                SELECT * FROM personal_channel_whatsapp_states
                WHERE gateway_id = ? AND channel_key = ?
                """,
                (str(gateway_id or "").strip(), str(channel_key or "").strip()),
            ).fetchall()
        finally:
            connection.close()
    return [state for state in (_whatsapp_state_from_row(row) for row in rows) if state]


def upsert_telegram_state(
    *,
    gateway_id: str,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    channel_key: str,
    agent_id: str = LEGACY_UNSCOPED_AGENT_ID,
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
    normalized_agent_id = _norm_agent_id(agent_id)
    with _DB_LOCK:
        connection = _connect(db_path)
        try:
            existing_row = connection.execute(
                """
                SELECT * FROM personal_channel_telegram_states
                WHERE gateway_id = ? AND channel_key = ? AND agent_id = ?
                """,
                (str(gateway_id or "").strip(), str(channel_key or "").strip(), normalized_agent_id),
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
                    gateway_id, channel_key, agent_id, tenant_id, workspace_id, user_id, provider,
                    status, login_hint, linked_user_id, linked_username, linked_phone,
                    linked_name, connected_at, last_event_at, metadata, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(gateway_id, channel_key, agent_id) DO UPDATE SET
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
                    normalized_agent_id,
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
                WHERE gateway_id = ? AND channel_key = ? AND agent_id = ?
                """,
                (str(gateway_id or "").strip(), str(channel_key or "").strip(), normalized_agent_id),
            ).fetchone()
        finally:
            connection.close()
    return _telegram_state_from_row(row) or {}


def get_telegram_state(
    gateway_id: str,
    *,
    channel_key: str,
    agent_id: str = LEGACY_UNSCOPED_AGENT_ID,
    db_path: Optional[Path | str] = None,
) -> Optional[Dict[str, Any]]:
    with _DB_LOCK:
        connection = _connect(db_path)
        try:
            row = connection.execute(
                """
                SELECT * FROM personal_channel_telegram_states
                WHERE gateway_id = ? AND channel_key = ? AND agent_id = ?
                """,
                (str(gateway_id or "").strip(), str(channel_key or "").strip(), _norm_agent_id(agent_id)),
            ).fetchone()
        finally:
            connection.close()
    return _telegram_state_from_row(row)


def list_telegram_states_for_gateway(
    gateway_id: str,
    *,
    channel_key: str,
    db_path: Optional[Path | str] = None,
) -> List[Dict[str, Any]]:
    """Every agent's Telegram session on this gateway — same purpose as
    list_whatsapp_states_for_gateway."""
    with _DB_LOCK:
        connection = _connect(db_path)
        try:
            rows = connection.execute(
                """
                SELECT * FROM personal_channel_telegram_states
                WHERE gateway_id = ? AND channel_key = ?
                """,
                (str(gateway_id or "").strip(), str(channel_key or "").strip()),
            ).fetchall()
        finally:
            connection.close()
    return [state for state in (_telegram_state_from_row(row) for row in rows) if state]


def find_telegram_state_by_agent_across_gateways(
    agent_id: str = LEGACY_UNSCOPED_AGENT_ID,
    *,
    channel_key: str,
    db_path: Optional[Path | str] = None,
) -> Optional[Dict[str, Any]]:
    """Used by inbound routing: given only a gateway_id + channel_key from an
    inbound event, find WHICH agent (if any) owns that session. See
    find_agent_id_for_telegram_session below — this is its lower-level twin
    for the (rare) case a caller already knows the agent and wants their
    current session regardless of which gateway it's on."""
    normalized_agent_id = _norm_agent_id(agent_id)
    with _DB_LOCK:
        connection = _connect(db_path)
        try:
            row = connection.execute(
                """
                SELECT * FROM personal_channel_telegram_states
                WHERE agent_id = ? AND channel_key = ?
                ORDER BY updated_at DESC LIMIT 1
                """,
                (normalized_agent_id, str(channel_key or "").strip()),
            ).fetchone()
        finally:
            connection.close()
    return _telegram_state_from_row(row)


def find_agent_id_for_telegram_session(
    gateway_id: str,
    *,
    channel_key: str,
    remote_jid: Optional[str] = None,
    db_path: Optional[Path | str] = None,
) -> str:
    """Inbound/state-sync routing needs to go the OTHER direction from every
    other function here: an inbound Telegram event or a gateway.state.update
    push names a gateway_id (whose Gateway process this is), not an
    agent_id — the Gateway runs one singleton session per process today and
    has no concept of Empyralis agents at all (see telegram/runtime.ts).

    CURRENT (single-agent) design: return whichever agent_id most recently
    touched this gateway+channel — i.e. whoever's configure() call (or
    inbound message) last claimed it. This is well-defined as long as only
    one agent's session is live per gateway+channel, which is true until
    the Gateway itself pools multiple concurrent sessions (the
    multi-agent-per-box plan, not built yet). Deliberately NOT restricted to
    status='connected': configure_telegram_personal_gateway seeds a row
    under the real agent_id immediately, before the Gateway ever reports
    back, specifically so a mid-pairing status (code_required,
    password_required, ...) already resolves to the right agent — an
    unauthenticated attempt is still THIS agent's attempt.

    remote_jid is accepted but unused today — reserved for the multi-agent
    case, where disambiguating by which session's own linked identity most
    recently talked to that counterparty becomes necessary. Returns
    LEGACY_UNSCOPED_AGENT_ID (empty) if the gateway+channel has no rows at
    all, or genuinely ambiguous ones (a future multi-agent state this
    function doesn't try to resolve on its own)."""
    normalized_channel_key = str(channel_key or "").strip()
    with _DB_LOCK:
        connection = _connect(db_path)
        try:
            row = connection.execute(
                """
                SELECT agent_id FROM personal_channel_telegram_states
                WHERE gateway_id = ? AND channel_key = ?
                ORDER BY updated_at DESC LIMIT 1
                """,
                (str(gateway_id or "").strip(), normalized_channel_key),
            ).fetchone()
        finally:
            connection.close()
    return str(row["agent_id"] or "") if row is not None else LEGACY_UNSCOPED_AGENT_ID


def find_agent_id_for_whatsapp_session(
    gateway_id: str,
    *,
    channel_key: str,
    db_path: Optional[Path | str] = None,
) -> str:
    """WhatsApp twin of find_agent_id_for_telegram_session — see its
    docstring for the full "most recently touched row" contract."""
    normalized_channel_key = str(channel_key or "").strip()
    with _DB_LOCK:
        connection = _connect(db_path)
        try:
            row = connection.execute(
                """
                SELECT agent_id FROM personal_channel_whatsapp_states
                WHERE gateway_id = ? AND channel_key = ?
                ORDER BY updated_at DESC LIMIT 1
                """,
                (str(gateway_id or "").strip(), normalized_channel_key),
            ).fetchone()
        finally:
            connection.close()
    return str(row["agent_id"] or "") if row is not None else LEGACY_UNSCOPED_AGENT_ID


def upsert_local_bridge_state(
    *,
    gateway_id: str,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    channel_key: str,
    agent_id: str = LEGACY_UNSCOPED_AGENT_ID,
    provider: str,
    status: str,
    linked_identity: Optional[str] = None,
    connected_at: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    db_path: Optional[Path | str] = None,
) -> Dict[str, Any]:
    """Signal/iMessage/WeChat-personal twin of upsert_whatsapp_state /
    upsert_telegram_state — same shallow-merge-metadata, same
    ON CONFLICT(gateway_id, channel_key, agent_id) upsert shape.

    ONE deliberate deviation from those two twins: `linked_identity` is
    PRESERVED when the caller passes None, and cleared only on an explicit
    empty string. The WhatsApp/Telegram tables overwrite their own
    linked_jid/linked_user_id unconditionally and rely on the service layer
    to guard (personal_channels_service._resolve_linked_identity_for_sync's
    docstring records what that cost the first time nobody did). This table
    cannot use that posture, because its ONLY per-message writer —
    _resolve_local_bridge_agent_id, which re-upserts `status="linked"` on
    every inbound message that hits the slow path — has no identity to
    offer and never will: it answers "which agent owns this channel", not
    "who is the owner on it". Overwriting from there would erase the owner
    link on the next message after it was established, so a preserve-on-None
    default is what makes the column durable rather than a value with a
    half-life of one message. None means "I have nothing to say about the
    owner's identity"; "" means "clear it" and is only ever passed by the
    owner-facing clear action.
    """
    now_iso = _utc_now_iso()
    normalized_agent_id = _norm_agent_id(agent_id)
    with _DB_LOCK:
        connection = _connect(db_path)
        try:
            existing_row = connection.execute(
                """
                SELECT * FROM personal_channel_local_bridge_states
                WHERE gateway_id = ? AND channel_key = ? AND agent_id = ?
                """,
                (str(gateway_id or "").strip(), str(channel_key or "").strip(), normalized_agent_id),
            ).fetchone()
            existing_metadata = (
                _json_loads(existing_row["metadata"], default={})
                if existing_row is not None
                else {}
            )
            merged_metadata = dict(existing_metadata or {})
            merged_metadata.update(dict(metadata or {}))
            # See the docstring: None preserves, "" clears, a value sets.
            if linked_identity is None:
                resolved_linked_identity = (
                    (str(existing_row["linked_identity"] or "").strip() or None)
                    if existing_row is not None
                    else None
                )
            else:
                resolved_linked_identity = str(linked_identity).strip() or None
            connection.execute(
                """
                INSERT INTO personal_channel_local_bridge_states (
                    gateway_id, channel_key, agent_id, tenant_id, workspace_id, user_id, provider,
                    status, linked_identity, connected_at, last_event_at, metadata, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(gateway_id, channel_key, agent_id) DO UPDATE SET
                    tenant_id=excluded.tenant_id,
                    workspace_id=excluded.workspace_id,
                    user_id=excluded.user_id,
                    provider=excluded.provider,
                    status=excluded.status,
                    linked_identity=excluded.linked_identity,
                    connected_at=excluded.connected_at,
                    last_event_at=excluded.last_event_at,
                    metadata=excluded.metadata,
                    updated_at=excluded.updated_at
                """,
                (
                    str(gateway_id or "").strip(),
                    str(channel_key or "").strip(),
                    normalized_agent_id,
                    str(tenant_id or "").strip(),
                    str(workspace_id or "").strip(),
                    str(user_id or "").strip(),
                    str(provider or "").strip(),
                    str(status or "").strip() or "idle",
                    resolved_linked_identity,
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
                SELECT * FROM personal_channel_local_bridge_states
                WHERE gateway_id = ? AND channel_key = ? AND agent_id = ?
                """,
                (str(gateway_id or "").strip(), str(channel_key or "").strip(), normalized_agent_id),
            ).fetchone()
        finally:
            connection.close()
    return _local_bridge_state_from_row(row) or {}


def get_local_bridge_state(
    gateway_id: str,
    *,
    channel_key: str,
    agent_id: str = LEGACY_UNSCOPED_AGENT_ID,
    db_path: Optional[Path | str] = None,
) -> Optional[Dict[str, Any]]:
    with _DB_LOCK:
        connection = _connect(db_path)
        try:
            row = connection.execute(
                """
                SELECT * FROM personal_channel_local_bridge_states
                WHERE gateway_id = ? AND channel_key = ? AND agent_id = ?
                """,
                (str(gateway_id or "").strip(), str(channel_key or "").strip(), _norm_agent_id(agent_id)),
            ).fetchone()
        finally:
            connection.close()
    return _local_bridge_state_from_row(row)


def find_agent_id_for_local_bridge_session(
    gateway_id: str,
    *,
    channel_key: str,
    db_path: Optional[Path | str] = None,
) -> str:
    """Signal/iMessage/WeChat-personal twin of
    find_agent_id_for_telegram_session/find_agent_id_for_whatsapp_session —
    same "whoever most recently touched this gateway+channel" contract.
    Rows here are seeded by personal_channels_service._resolve_local_bridge_agent_id
    (a reverse preferred_gateway_id lookup, or an explicit owner action for
    the one local-bridge channel that has one today, iMessage's
    recheck/install routes) rather than by an in-app configure step, since
    these channels don't have one — see that function's own docstring."""
    normalized_channel_key = str(channel_key or "").strip()
    with _DB_LOCK:
        connection = _connect(db_path)
        try:
            row = connection.execute(
                """
                SELECT agent_id FROM personal_channel_local_bridge_states
                WHERE gateway_id = ? AND channel_key = ?
                ORDER BY updated_at DESC LIMIT 1
                """,
                (str(gateway_id or "").strip(), normalized_channel_key),
            ).fetchone()
        finally:
            connection.close()
    return str(row["agent_id"] or "") if row is not None else LEGACY_UNSCOPED_AGENT_ID


def list_owner_linked_channel_identities_for_workspace(
    workspace_id: str,
    *,
    db_path: Optional[Path | str] = None,
) -> Dict[str, str]:
    """The single authoritative source for "which sender id is the owner
    on which channel" for this workspace — {channel_key: linked_id},
    across all three personal-channel families (WhatsApp's linked_jid,
    Telegram's linked_user_id, and the Signal/iMessage/WeChat/OpenClaw
    local-bridge family's linked_identity).

    This is real, correct, already-populated data: written only from a
    genuine owner/self-chat signal (see _resolve_linked_identity_for_sync
    in personal_channels_service.py, and that function's own docstring on
    the clobber bug already fixed here — a stranger's sender_jid can never
    land in these columns) at connect/login time, which is exactly the
    moment "who is the owner on this channel" is actually established.
    It is what _is_owner_message (personal_channels_service.py) already
    checks for the DM-policy gate that runs before every reply.

    agent_turn_runtime_service.py's owner/audience tool-authority
    classification uses this — and ONLY this — as of the fix that added
    this function; see that module's own comment on why
    workspace.identity_links must never be consulted for the SAME
    question again beside it. Two independent answers to "is this the
    owner" is the shape that let one of them (identity_links, dead
    because nothing has ever written it) silently win by default.

    The three tables key their rows by (gateway_id, channel_key,
    agent_id), not workspace_id — but workspace_id is a stored column on
    every row, so this queries across gateways/agents for the workspace
    directly rather than requiring a gateway_id the caller may not have
    at the point this question is asked. The LAST-updated row per
    channel_key wins if more than one gateway/agent has written one for
    this workspace (mirrors find_agent_id_for_local_bridge_session's own
    "whoever most recently touched this" convention above) — a stale
    linked identity for a channel this workspace no longer actively uses
    is the acceptable failure mode, never a wrong-workspace one, since
    every row queried is already filtered to this workspace_id.
    """
    normalized_workspace_id = str(workspace_id or "").strip()
    if not normalized_workspace_id:
        return {}
    linked: Dict[str, str] = {}
    with _DB_LOCK:
        connection = _connect(db_path)
        try:
            for table, id_column in (
                ("personal_channel_whatsapp_states", "linked_jid"),
                ("personal_channel_telegram_states", "linked_user_id"),
                ("personal_channel_local_bridge_states", "linked_identity"),
            ):
                rows = connection.execute(
                    f"""
                    SELECT channel_key, {id_column} AS linked_id, updated_at
                    FROM {table}
                    WHERE workspace_id = ? AND {id_column} IS NOT NULL AND {id_column} != ''
                    ORDER BY updated_at ASC
                    """,
                    (normalized_workspace_id,),
                ).fetchall()
                for row in rows:
                    channel_key = str(row["channel_key"] or "").strip()
                    linked_id = str(row["linked_id"] or "").strip()
                    if channel_key and linked_id:
                        # ORDER BY updated_at ASC + plain dict assignment:
                        # the last (most recent) row for a given channel_key
                        # naturally wins, no separate max() pass needed.
                        linked[channel_key] = linked_id
        finally:
            connection.close()
    return linked


def list_owner_linked_channel_identities_for_agent(
    workspace_id: str,
    agent_id: str,
    *,
    db_path: Optional[Path | str] = None,
) -> Dict[str, str]:
    """Per-AGENT twin of list_owner_linked_channel_identities_for_workspace
    above — {channel_key: linked_id}, scoped to one agent's own rows only.

    agent_turn_runtime_service._resolve_channel_sender_class needs this one,
    not the workspace-wide version: two agents in the same workspace can be
    linked to two DIFFERENT owners on the very same channel_key (agent A
    handed to person 1, agent B handed to person 2 later), and the
    workspace-wide function's own docstring is explicit that "the LAST-
    updated row per channel_key wins" across every agent/gateway that has
    written one — which silently handed agent A's tool authority to person
    2 the moment agent B linked. See _resolve_channel_sender_class's own
    docstring for the full incident this closes.

    agent_id is REQUIRED and has no default that widens scope: an empty/
    unresolved agent_id returns {} rather than falling back to the
    workspace-wide set — a sender with no resolved agent to check against
    must fail CLOSED to "audience", never inherit whichever agent last
    wrote a row. This also means a legacy/ambiguous row written under
    LEGACY_UNSCOPED_AGENT_ID (agent_id == "") is never read back out as a
    specific agent's owner link, which matches that sentinel's own
    contract (see its module-level comment): unclaimed/ambiguous data
    belongs to no agent in particular.
    """
    normalized_workspace_id = str(workspace_id or "").strip()
    normalized_agent_id = str(agent_id or "").strip()
    if not normalized_workspace_id or not normalized_agent_id:
        return {}
    linked: Dict[str, str] = {}
    with _DB_LOCK:
        connection = _connect(db_path)
        try:
            for table, id_column in (
                ("personal_channel_whatsapp_states", "linked_jid"),
                ("personal_channel_telegram_states", "linked_user_id"),
                ("personal_channel_local_bridge_states", "linked_identity"),
            ):
                rows = connection.execute(
                    f"""
                    SELECT channel_key, {id_column} AS linked_id, updated_at
                    FROM {table}
                    WHERE workspace_id = ? AND agent_id = ? AND {id_column} IS NOT NULL AND {id_column} != ''
                    ORDER BY updated_at ASC
                    """,
                    (normalized_workspace_id, normalized_agent_id),
                ).fetchall()
                for row in rows:
                    channel_key = str(row["channel_key"] or "").strip()
                    linked_id = str(row["linked_id"] or "").strip()
                    if channel_key and linked_id:
                        # ORDER BY updated_at ASC + plain dict assignment:
                        # the last (most recent) row for a given channel_key
                        # naturally wins, no separate max() pass needed.
                        linked[channel_key] = linked_id
        finally:
            connection.close()
    return linked


def claim_channel_owner_identity_if_unclaimed(
    *,
    gateway_id: str,
    channel_key: str,
    agent_id: str,
    tenant_id: str,
    workspace_id: str,
    sender_id: str,
    provider: str,
    db_path: Optional[Path | str] = None,
) -> bool:
    """First-contact ownership claim for a channel family that has no
    pairing/login step of its own (a BYO bot: the customer proves control by
    pasting a real BotFather/etc token, never by a phone/QR login this table
    otherwise records).

    Writes a row into the SAME table `list_owner_linked_channel_identities_
    for_workspace` already reads (this function does not add a fourth
    identity source; it is one more genuine writer of the same authoritative
    one), but ONLY the first time — checked and inserted under `_DB_LOCK` so
    two near-simultaneous first messages cannot both win. Every row already
    written for this WORKSPACE + CHANNEL_KEY (across every gateway_id/
    agent_id — a BYO channel's bot can only ever have been created by the
    workspace OWNER, since assign_byo_bot/assign_agent_discord etc. all
    require minimum_role="owner", so a second bot's binding is still the
    same person in the overwhelming case) counts, not just this row's own
    key, or a second BYO bot in the same workspace would silently reset who
    is recognized as owner.

    Returns True if `sender_id` is (now, or already) the claimed identity;
    False if a DIFFERENT sender already holds the claim — the caller must
    never treat False as "try again", it means someone else got here first
    and this sender is correctly not the owner.

    Deliberately ONE-SHOT: unlike upsert_telegram_state, a claim already
    held by a different sender is never overwritten by a later message.
    Owner recognition must not silently migrate to whoever messaged last —
    that would let a stranger who messages the bot after the real owner
    inherit tool authority the real owner already established. Reconnecting
    the bot (assign_byo_bot again) is the only sanctioned way to reset a
    claim — see that function's own docstring.
    """
    clean_sender = str(sender_id or "").strip()
    if not clean_sender:
        return False
    now_iso = _utc_now_iso()
    with _DB_LOCK:
        connection = _connect(db_path)
        try:
            existing = connection.execute(
                """
                SELECT linked_user_id FROM personal_channel_telegram_states
                WHERE workspace_id = ? AND channel_key = ?
                  AND linked_user_id IS NOT NULL AND linked_user_id != ''
                ORDER BY updated_at ASC
                """,
                (str(workspace_id or "").strip(), str(channel_key or "").strip()),
            ).fetchall()
            for row in existing:
                held = str(row["linked_user_id"] or "").strip()
                if held:
                    return held == clean_sender
            normalized_agent_id = _norm_agent_id(agent_id)
            connection.execute(
                """
                INSERT INTO personal_channel_telegram_states (
                    gateway_id, channel_key, agent_id, tenant_id, workspace_id, user_id, provider,
                    status, login_hint, linked_user_id, linked_username, linked_phone,
                    linked_name, connected_at, last_event_at, metadata, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'connected', NULL, ?, NULL, NULL, NULL, ?, ?, '{}', ?, ?)
                ON CONFLICT(gateway_id, channel_key, agent_id) DO UPDATE SET
                    linked_user_id=excluded.linked_user_id,
                    status=excluded.status,
                    connected_at=excluded.connected_at,
                    last_event_at=excluded.last_event_at,
                    updated_at=excluded.updated_at
                """,
                (
                    str(gateway_id or "").strip(),
                    str(channel_key or "").strip(),
                    normalized_agent_id,
                    str(tenant_id or "").strip(),
                    str(workspace_id or "").strip(),
                    clean_sender,
                    str(provider or "").strip(),
                    clean_sender,
                    now_iso,
                    now_iso,
                    now_iso,
                    now_iso,
                ),
            )
            connection.commit()
            return True
        finally:
            connection.close()


def record_inbound_message(
    *,
    gateway_id: str,
    channel_key: str,
    agent_id: str = LEGACY_UNSCOPED_AGENT_ID,
    external_message_id: str,
    remote_jid: str,
    sender_jid: Optional[str],
    push_name: Optional[str],
    text: str,
    metadata: Optional[Dict[str, Any]] = None,
    db_path: Optional[Path | str] = None,
) -> Tuple[Dict[str, Any], bool]:
    now_iso = _utc_now_iso()
    normalized_agent_id = _norm_agent_id(agent_id)
    created = False
    with _DB_LOCK:
        connection = _connect(db_path)
        try:
            existing_row = connection.execute(
                """
                SELECT * FROM personal_channel_inbound_messages
                WHERE gateway_id = ? AND channel_key = ? AND agent_id = ? AND external_message_id = ?
                """,
                (
                    str(gateway_id or "").strip(),
                    str(channel_key or "").strip(),
                    normalized_agent_id,
                    str(external_message_id or "").strip(),
                ),
            ).fetchone()
            if existing_row is None:
                created = True
                connection.execute(
                    """
                    INSERT INTO personal_channel_inbound_messages (
                        gateway_id, channel_key, agent_id, external_message_id, remote_jid, sender_jid,
                        push_name, text, reply_idempotency_key, processed_at, metadata,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?)
                    """,
                    (
                        str(gateway_id or "").strip(),
                        str(channel_key or "").strip(),
                        normalized_agent_id,
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
                    WHERE gateway_id = ? AND channel_key = ? AND agent_id = ? AND external_message_id = ?
                    """,
                    (
                        str(gateway_id or "").strip(),
                        str(channel_key or "").strip(),
                        normalized_agent_id,
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
    agent_id: str = LEGACY_UNSCOPED_AGENT_ID,
    external_message_id: str,
    reply_idempotency_key: str,
    db_path: Optional[Path | str] = None,
) -> Optional[Dict[str, Any]]:
    now_iso = _utc_now_iso()
    normalized_agent_id = _norm_agent_id(agent_id)
    with _DB_LOCK:
        connection = _connect(db_path)
        try:
            connection.execute(
                """
                UPDATE personal_channel_inbound_messages
                SET reply_idempotency_key = ?, processed_at = ?, updated_at = ?
                WHERE gateway_id = ? AND channel_key = ? AND agent_id = ? AND external_message_id = ?
                """,
                (
                    str(reply_idempotency_key or "").strip(),
                    now_iso,
                    now_iso,
                    str(gateway_id or "").strip(),
                    str(channel_key or "").strip(),
                    normalized_agent_id,
                    str(external_message_id or "").strip(),
                ),
            )
            connection.commit()
            row = connection.execute(
                """
                SELECT * FROM personal_channel_inbound_messages
                WHERE gateway_id = ? AND channel_key = ? AND agent_id = ? AND external_message_id = ?
                """,
                (
                    str(gateway_id or "").strip(),
                    str(channel_key or "").strip(),
                    normalized_agent_id,
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
    agent_id: str = LEGACY_UNSCOPED_AGENT_ID,
    idempotency_key: str,
    remote_jid: str,
    text: str,
    reply_to_external_message_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    db_path: Optional[Path | str] = None,
) -> Tuple[Dict[str, Any], bool]:
    now_iso = _utc_now_iso()
    normalized_agent_id = _norm_agent_id(agent_id)
    created = False
    with _DB_LOCK:
        connection = _connect(db_path)
        try:
            existing_row = connection.execute(
                """
                SELECT * FROM personal_channel_outbound_messages
                WHERE gateway_id = ? AND channel_key = ? AND agent_id = ? AND idempotency_key = ?
                """,
                (
                    str(gateway_id or "").strip(),
                    str(channel_key or "").strip(),
                    normalized_agent_id,
                    str(idempotency_key or "").strip(),
                ),
            ).fetchone()
            if existing_row is None:
                created = True
                connection.execute(
                    """
                    INSERT INTO personal_channel_outbound_messages (
                        gateway_id, channel_key, agent_id, idempotency_key, remote_jid, text,
                        reply_to_external_message_id, external_message_id, status,
                        metadata, created_at, updated_at, delivered_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, 'pending', ?, ?, ?, NULL)
                    """,
                    (
                        str(gateway_id or "").strip(),
                        str(channel_key or "").strip(),
                        normalized_agent_id,
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
                    WHERE gateway_id = ? AND channel_key = ? AND agent_id = ? AND idempotency_key = ?
                    """,
                    (
                        str(gateway_id or "").strip(),
                        str(channel_key or "").strip(),
                        normalized_agent_id,
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
    agent_id: str = LEGACY_UNSCOPED_AGENT_ID,
    idempotency_key: str,
    db_path: Optional[Path | str] = None,
) -> Optional[Dict[str, Any]]:
    with _DB_LOCK:
        connection = _connect(db_path)
        try:
            row = connection.execute(
                """
                SELECT * FROM personal_channel_outbound_messages
                WHERE gateway_id = ? AND channel_key = ? AND agent_id = ? AND idempotency_key = ?
                """,
                (
                    str(gateway_id or "").strip(),
                    str(channel_key or "").strip(),
                    _norm_agent_id(agent_id),
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
    agent_id: str = LEGACY_UNSCOPED_AGENT_ID,
    idempotency_key: str,
    external_message_id: Optional[str],
    metadata: Optional[Dict[str, Any]] = None,
    db_path: Optional[Path | str] = None,
) -> Optional[Dict[str, Any]]:
    now_iso = _utc_now_iso()
    normalized_agent_id = _norm_agent_id(agent_id)
    with _DB_LOCK:
        connection = _connect(db_path)
        try:
            existing_row = connection.execute(
                """
                SELECT * FROM personal_channel_outbound_messages
                WHERE gateway_id = ? AND channel_key = ? AND agent_id = ? AND idempotency_key = ?
                """,
                (
                    str(gateway_id or "").strip(),
                    str(channel_key or "").strip(),
                    normalized_agent_id,
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
                WHERE gateway_id = ? AND channel_key = ? AND agent_id = ? AND idempotency_key = ?
                """,
                (
                    str(external_message_id or "").strip() or None,
                    _json_dumps(merged_metadata),
                    now_iso,
                    now_iso,
                    str(gateway_id or "").strip(),
                    str(channel_key or "").strip(),
                    normalized_agent_id,
                    str(idempotency_key or "").strip(),
                ),
            )
            connection.commit()
            row = connection.execute(
                """
                SELECT * FROM personal_channel_outbound_messages
                WHERE gateway_id = ? AND channel_key = ? AND agent_id = ? AND idempotency_key = ?
                """,
                (
                    str(gateway_id or "").strip(),
                    str(channel_key or "").strip(),
                    normalized_agent_id,
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
    agent_id: str = LEGACY_UNSCOPED_AGENT_ID,
    limit: int = 20,
    db_path: Optional[Path | str] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    normalized_agent_id = _norm_agent_id(agent_id)
    with _DB_LOCK:
        connection = _connect(db_path)
        try:
            inbound_rows = connection.execute(
                """
                SELECT * FROM personal_channel_inbound_messages
                WHERE gateway_id = ? AND channel_key = ? AND agent_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (str(gateway_id or "").strip(), str(channel_key or "").strip(), normalized_agent_id, max(int(limit or 0), 1)),
            ).fetchall()
            outbound_rows = connection.execute(
                """
                SELECT * FROM personal_channel_outbound_messages
                WHERE gateway_id = ? AND channel_key = ? AND agent_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (str(gateway_id or "").strip(), str(channel_key or "").strip(), normalized_agent_id, max(int(limit or 0), 1)),
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
        "agent_id": str(row["agent_id"] or "") or None,
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
        "agent_id": str(row["agent_id"] or "") or None,
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


def _local_bridge_state_from_row(row: sqlite3.Row | None) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    return {
        "gateway_id": str(row["gateway_id"] or ""),
        "channel_key": str(row["channel_key"] or ""),
        "agent_id": str(row["agent_id"] or "") or None,
        "tenant_id": str(row["tenant_id"] or ""),
        "workspace_id": str(row["workspace_id"] or ""),
        "user_id": str(row["user_id"] or ""),
        "provider": str(row["provider"] or ""),
        "status": str(row["status"] or ""),
        "linked_identity": str(row["linked_identity"] or "").strip() or None,
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
        "agent_id": str(row["agent_id"] or "") or None,
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
        "agent_id": str(row["agent_id"] or "") or None,
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

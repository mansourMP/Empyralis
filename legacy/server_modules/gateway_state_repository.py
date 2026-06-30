from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from server_modules import execution_mode_policy, rust_runtime_kernel_client, secret_redaction_service


EMPYRALIS_STATE_HOME = Path(
    os.getenv("EMPYRALIS_STATE_HOME", str(Path.home() / ".empyralis" / "state"))
).expanduser()
GATEWAY_STATE_DB_FILE = (
    Path(os.getenv("EMPYRALIS_GATEWAY_STATE_DB", EMPYRALIS_STATE_HOME / "gateway" / "gateway-state.sqlite3"))
).expanduser()
_DB_LOCK = threading.Lock()
_REDACTED_VALUE = "[redacted]"
_SENSITIVE_GATEWAY_EVENT_KEYS = frozenset(
    {
        "api_hash",
        "api_key",
        "authorization",
        "bot_token",
        "code",
        "credential",
        "credentials",
        "gateway_token",
        "login_code",
        "password",
        "private_key",
        "refresh_token",
        "secret",
        "session",
        "session_string",
        "session_token",
        "signal_account",
        "token",
    }
)
_PERSONAL_MESSAGE_TEXT_KEYS = frozenset({"text", "delta", "content", "body"})
_OWNER_RUNTIME_ACCESS_METADATA_KEYS = frozenset(
    {
        "runtime_access_mode",
        "runtime_access_label",
        "runtime_access_setup_warning",
        "autonomous_agent_setup_warning_acknowledged",
        "autonomous_agent_setup_warning_acknowledged_at",
    }
)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS gateway_pairing_intents (
    pairing_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'pending',
    display_name TEXT NULL,
    platform TEXT NULL,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    consumed_at TEXT NULL,
    consumed_gateway_id TEXT NULL
);

CREATE TABLE IF NOT EXISTS gateway_registrations (
    gateway_id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    device_trust_state TEXT NOT NULL DEFAULT 'verified',
    display_name TEXT NULL,
    platform TEXT NULL,
    gateway_token_hash TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}',
    capabilities TEXT NOT NULL DEFAULT '[]',
    journal_cursor INTEGER NOT NULL DEFAULT 0,
    checkpoint_cursor INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_seen_at TEXT NULL,
    last_heartbeat_at TEXT NULL,
    token_rotated_at TEXT NULL,
    revoked_at TEXT NULL,
    revoked_reason TEXT NULL
);

CREATE INDEX IF NOT EXISTS gateway_registrations_scope_idx
    ON gateway_registrations (workspace_id, user_id, device_id);

CREATE TABLE IF NOT EXISTS gateway_sessions (
    session_id TEXT PRIMARY KEY,
    gateway_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    session_token_hash TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'pending',
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    connected_at TEXT NULL,
    disconnected_at TEXT NULL,
    expires_at TEXT NOT NULL,
    last_seq INTEGER NOT NULL DEFAULT 0,
    last_ack INTEGER NOT NULL DEFAULT 0,
    last_heartbeat_at TEXT NULL
);

CREATE INDEX IF NOT EXISTS gateway_sessions_gateway_idx
    ON gateway_sessions (gateway_id, status, expires_at);

CREATE TABLE IF NOT EXISTS gateway_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gateway_id TEXT NOT NULL,
    session_id TEXT NULL,
    direction TEXT NOT NULL,
    frame_kind TEXT NOT NULL,
    message_type TEXT NOT NULL,
    seq INTEGER NULL,
    ack INTEGER NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS gateway_events_gateway_idx
    ON gateway_events (gateway_id, created_at DESC);

CREATE TABLE IF NOT EXISTS gateway_action_approvals (
    approval_id TEXT PRIMARY KEY,
    gateway_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    capability_id TEXT NOT NULL,
    request_id TEXT NULL,
    run_id TEXT NOT NULL,
    trace_id TEXT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    decision TEXT NULL,
    decision_actor TEXT NULL,
    decision_note TEXT NULL,
    requested_at TEXT NOT NULL,
    resolved_at TEXT NULL,
    executed_at TEXT NULL,
    retry_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NULL,
    request_payload TEXT NOT NULL DEFAULT '{}',
    result_payload TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS gateway_action_approvals_gateway_idx
    ON gateway_action_approvals (gateway_id, status, requested_at DESC);

CREATE TABLE IF NOT EXISTS gateway_browser_sessions (
    browser_session_id TEXT PRIMARY KEY,
    gateway_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    trace_id TEXT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    execution_target TEXT NOT NULL DEFAULT 'local_gateway',
    session_profile TEXT NULL,
    current_url TEXT NULL,
    manual_takeover INTEGER NOT NULL DEFAULT 0,
    resume_supported INTEGER NOT NULL DEFAULT 0,
    reviewed_approval_required INTEGER NOT NULL DEFAULT 0,
    reviewed_approved INTEGER NOT NULL DEFAULT 0,
    immutable_plan_hash TEXT NULL,
    execution_binding TEXT NOT NULL DEFAULT '{}',
    checkpoint_payload TEXT NOT NULL DEFAULT '{}',
    snapshot_payload TEXT NOT NULL DEFAULT '{}',
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_activity_at TEXT NULL,
    fallback_ready_at TEXT NULL,
    interrupted_at TEXT NULL
);

CREATE INDEX IF NOT EXISTS gateway_browser_sessions_gateway_idx
    ON gateway_browser_sessions (gateway_id, status, updated_at DESC);
"""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().isoformat().replace("+00:00", "Z")


def _expires_at_iso(ttl_seconds: int) -> str:
    return (_utc_now() + timedelta(seconds=max(int(ttl_seconds or 0), 1))).isoformat().replace("+00:00", "Z")


def _ensure_columns(connection: sqlite3.Connection, table_name: str, expected: Dict[str, str]) -> None:
    existing = {
        str(row[1]).strip().lower()
        for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    for column_name, definition in expected.items():
        if column_name.lower() in existing:
            continue
        connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")


def _connect(db_path: Optional[Path | str] = None) -> sqlite3.Connection:
    resolved = Path(db_path or GATEWAY_STATE_DB_FILE).expanduser()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(resolved, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA_SQL)
    _ensure_columns(
        conn,
        "gateway_registrations",
        {
            "device_trust_state": "TEXT NOT NULL DEFAULT 'verified'",
            "token_rotated_at": "TEXT NULL",
            "revoked_at": "TEXT NULL",
            "revoked_reason": "TEXT NULL",
        },
    )
    _ensure_columns(
        conn,
        "gateway_sessions",
        {
            "device_id": "TEXT",
        },
    )
    conn.commit()
    return conn


def _hash_token(token: str) -> str:
    return hashlib.sha256(str(token or "").strip().encode("utf-8")).hexdigest()


def _json_dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, sort_keys=True)


def _json_loads(value: Any, *, default: Any) -> Any:
    if not isinstance(value, str) or not value.strip():
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def _gateway_state_payload_size_bytes(payload: Optional[Dict[str, Any]]) -> int:
    try:
        return len(json.dumps(payload or {}, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8"))
    except Exception:
        return 0


def _enforce_gateway_state_decision(
    operation: str,
    *,
    tenant_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    user_id: Optional[str] = None,
    device_id: Optional[str] = None,
    gateway_id: Optional[str] = None,
    session_id: Optional[str] = None,
    actor_role: str = "system",
    payload: Optional[Dict[str, Any]] = None,
    **fields: Any,
) -> Dict[str, Any]:
    role = str(actor_role or "system").strip() or "system"
    request = {
        "operation": operation,
        "tenant_id": str(tenant_id or "").strip(),
        "workspace_id": str(workspace_id or "").strip(),
        "user_id": str(user_id or "").strip(),
        "device_id": str(device_id or "").strip(),
        "gateway_id": str(gateway_id or "").strip(),
        "session_id": str(session_id or "").strip(),
        "actor_role": role,
        "is_service": role in {"system", "service"},
        "payload_size_bytes": _gateway_state_payload_size_bytes(payload),
    }
    request.update(fields)
    decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
        "gateway-state-decision",
        request,
    )
    expected_next_actions = {
        "create_pairing_intent": {"create_pairing_intent"},
        "expire_pairing_intent": {"mark_pairing_intent_expired"},
        "register_gateway": {"consume_pairing_and_register_gateway"},
        "issue_session": {"issue_gateway_session"},
        "validate_session": {"validate_gateway_session"},
        "mark_session_connected": {"mark_gateway_session_connected"},
        "mark_session_disconnected": {"mark_gateway_session_disconnected"},
        "touch_session": {"touch_gateway_session"},
        "rotate_token": {"rotate_gateway_token"},
        "revoke_registration": {"revoke_gateway_registration"},
        "update_registration_state": {"update_gateway_registration_state"},
        "record_event": {"record_gateway_event"},
        "create_approval": {"create_gateway_action_approval"},
        "resolve_approval": {"resolve_gateway_action_approval_atomic"},
        "update_browser_session": {"upsert_gateway_browser_session"},
        "summarize_outbox": {"summarize_gateway_outbox"},
        "sweep_stale_sessions": {"sweep_stale_gateway_sessions", "noop"},
    }
    expected = expected_next_actions.get(operation)
    if expected:
        next_action = str(decision.get("next_action") or "").strip()
        if next_action not in expected:
            raise RuntimeError(f"unexpected_next_action:{next_action or 'missing'}")
    return decision
def _runtime_access_metadata_from_pairing(
    *,
    pairing_metadata: Dict[str, Any],
    existing_metadata: Dict[str, Any],
) -> Dict[str, Any]:
    candidate_mode = pairing_metadata.get("runtime_access_mode") or existing_metadata.get("runtime_access_mode")
    mode = execution_mode_policy.normalize_runtime_access_mode(candidate_mode)
    out = {
        "runtime_access_mode": mode,
        "runtime_access_label": execution_mode_policy.public_runtime_access_label(mode),
    }
    setup_warning = execution_mode_policy.runtime_access_setup_warning(mode)
    if setup_warning:
        out["runtime_access_setup_warning"] = setup_warning
        out["autonomous_agent_setup_warning_acknowledged"] = bool(
            pairing_metadata.get("autonomous_agent_setup_warning_acknowledged")
            or existing_metadata.get("autonomous_agent_setup_warning_acknowledged")
        )
        warning_acknowledged_at = (
            pairing_metadata.get("autonomous_agent_setup_warning_acknowledged_at")
            or existing_metadata.get("autonomous_agent_setup_warning_acknowledged_at")
        )
        if warning_acknowledged_at:
            out["autonomous_agent_setup_warning_acknowledged_at"] = str(warning_acknowledged_at)
    else:
        out["autonomous_agent_setup_warning_acknowledged"] = False
    return out


def _strip_owner_runtime_access_metadata(metadata: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: value
        for key, value in dict(metadata or {}).items()
        if key not in _OWNER_RUNTIME_ACCESS_METADATA_KEYS
    }


def _gateway_event_key_is_sensitive(key: str) -> bool:
    normalized_key = str(key or "").strip().lower()
    if not normalized_key:
        return False
    if normalized_key in _SENSITIVE_GATEWAY_EVENT_KEYS:
        return True
    return secret_redaction_service.is_sensitive_key(normalized_key)


def _gateway_event_is_personal_channel_payload(message_type: str, payload: Any) -> bool:
    normalized_message_type = str(message_type or "").strip().lower()
    if normalized_message_type.startswith("channel."):
        return True
    if isinstance(payload, dict):
        channel_key = str(payload.get("channel_key") or "").strip().lower()
        provider = str(payload.get("provider") or "").strip().lower()
        if channel_key.endswith("_personal") or "personal" in provider:
            return True
        nested_payload = payload.get("payload")
        if isinstance(nested_payload, dict):
            return _gateway_event_is_personal_channel_payload(message_type, nested_payload)
    return False


def _redact_gateway_event_payload_value(value: Any, *, personal_channel_payload: bool, key: str = "") -> Any:
    if _gateway_event_key_is_sensitive(key):
        return _REDACTED_VALUE
    normalized_key = str(key or "").strip().lower()
    if personal_channel_payload and normalized_key in _PERSONAL_MESSAGE_TEXT_KEYS:
        return _REDACTED_VALUE
    if isinstance(value, dict):
        return {
            str(child_key): _redact_gateway_event_payload_value(
                child_value,
                personal_channel_payload=personal_channel_payload,
                key=str(child_key),
            )
            for child_key, child_value in value.items()
        }
    if isinstance(value, list):
        return [
            _redact_gateway_event_payload_value(
                item,
                personal_channel_payload=personal_channel_payload,
                key=key,
            )
            for item in value
        ]
    if isinstance(value, str):
        return secret_redaction_service.redact_text(value)
    return value


def sanitize_gateway_event_payload(payload: Optional[Dict[str, Any]], *, message_type: str) -> Dict[str, Any]:
    raw_payload = dict(payload or {})
    return _redact_gateway_event_payload_value(
        raw_payload,
        personal_channel_payload=_gateway_event_is_personal_channel_payload(message_type, raw_payload),
    )


def _pairing_from_row(row: sqlite3.Row | None) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    return {
        "pairing_id": str(row["pairing_id"] or ""),
        "tenant_id": str(row["tenant_id"] or ""),
        "workspace_id": str(row["workspace_id"] or ""),
        "user_id": str(row["user_id"] or ""),
        "status": str(row["status"] or ""),
        "display_name": str(row["display_name"] or "").strip() or None,
        "platform": str(row["platform"] or "").strip() or None,
        "metadata": _json_loads(row["metadata"], default={}),
        "created_at": str(row["created_at"] or ""),
        "expires_at": str(row["expires_at"] or ""),
        "consumed_at": str(row["consumed_at"] or "").strip() or None,
        "consumed_gateway_id": str(row["consumed_gateway_id"] or "").strip() or None,
    }


def _registration_from_row(row: sqlite3.Row | None) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    return {
        "gateway_id": str(row["gateway_id"] or ""),
        "device_id": str(row["device_id"] or ""),
        "tenant_id": str(row["tenant_id"] or ""),
        "workspace_id": str(row["workspace_id"] or ""),
        "user_id": str(row["user_id"] or ""),
        "status": str(row["status"] or ""),
        "device_trust_state": str(row["device_trust_state"] or "verified").strip() or "verified",
        "display_name": str(row["display_name"] or "").strip() or None,
        "platform": str(row["platform"] or "").strip() or None,
        "metadata": _json_loads(row["metadata"], default={}),
        "capabilities": _json_loads(row["capabilities"], default=[]),
        "journal_cursor": int(row["journal_cursor"] or 0),
        "checkpoint_cursor": int(row["checkpoint_cursor"] or 0),
        "created_at": str(row["created_at"] or ""),
        "updated_at": str(row["updated_at"] or ""),
        "last_seen_at": str(row["last_seen_at"] or "").strip() or None,
        "last_heartbeat_at": str(row["last_heartbeat_at"] or "").strip() or None,
        "token_rotated_at": str(row["token_rotated_at"] or "").strip() or None,
        "revoked_at": str(row["revoked_at"] or "").strip() or None,
        "revoked_reason": str(row["revoked_reason"] or "").strip() or None,
    }


def _session_from_row(row: sqlite3.Row | None) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    return {
        "session_id": str(row["session_id"] or ""),
        "gateway_id": str(row["gateway_id"] or ""),
        "device_id": str(row["device_id"] or "").strip() or None,
        "tenant_id": str(row["tenant_id"] or ""),
        "workspace_id": str(row["workspace_id"] or ""),
        "user_id": str(row["user_id"] or ""),
        "status": str(row["status"] or ""),
        "metadata": _json_loads(row["metadata"], default={}),
        "created_at": str(row["created_at"] or ""),
        "updated_at": str(row["updated_at"] or ""),
        "connected_at": str(row["connected_at"] or "").strip() or None,
        "disconnected_at": str(row["disconnected_at"] or "").strip() or None,
        "expires_at": str(row["expires_at"] or ""),
        "last_seq": int(row["last_seq"] or 0),
        "last_ack": int(row["last_ack"] or 0),
        "last_heartbeat_at": str(row["last_heartbeat_at"] or "").strip() or None,
    }


def _approval_from_row(row: sqlite3.Row | None) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    return {
        "approval_id": str(row["approval_id"] or ""),
        "gateway_id": str(row["gateway_id"] or ""),
        "device_id": str(row["device_id"] or ""),
        "tenant_id": str(row["tenant_id"] or ""),
        "workspace_id": str(row["workspace_id"] or ""),
        "user_id": str(row["user_id"] or ""),
        "capability_id": str(row["capability_id"] or ""),
        "request_id": str(row["request_id"] or "").strip() or None,
        "run_id": str(row["run_id"] or ""),
        "trace_id": str(row["trace_id"] or "").strip() or None,
        "status": str(row["status"] or ""),
        "decision": str(row["decision"] or "").strip() or None,
        "decision_actor": str(row["decision_actor"] or "").strip() or None,
        "decision_note": str(row["decision_note"] or "").strip() or None,
        "requested_at": str(row["requested_at"] or ""),
        "resolved_at": str(row["resolved_at"] or "").strip() or None,
        "executed_at": str(row["executed_at"] or "").strip() or None,
        "retry_count": int(row["retry_count"] or 0),
        "last_error": str(row["last_error"] or "").strip() or None,
        "request_payload": _json_loads(row["request_payload"], default={}),
        "result_payload": _json_loads(row["result_payload"], default={}),
    }


def _browser_session_from_row(row: sqlite3.Row | None) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    return {
        "browser_session_id": str(row["browser_session_id"] or ""),
        "gateway_id": str(row["gateway_id"] or ""),
        "device_id": str(row["device_id"] or ""),
        "tenant_id": str(row["tenant_id"] or ""),
        "workspace_id": str(row["workspace_id"] or ""),
        "user_id": str(row["user_id"] or ""),
        "run_id": str(row["run_id"] or ""),
        "trace_id": str(row["trace_id"] or "").strip() or None,
        "status": str(row["status"] or "").strip() or "active",
        "execution_target": str(row["execution_target"] or "").strip() or "local_gateway",
        "session_profile": str(row["session_profile"] or "").strip() or None,
        "current_url": str(row["current_url"] or "").strip() or None,
        "manual_takeover": bool(int(row["manual_takeover"] or 0)),
        "resume_supported": bool(int(row["resume_supported"] or 0)),
        "reviewed_approval_required": bool(int(row["reviewed_approval_required"] or 0)),
        "reviewed_approved": bool(int(row["reviewed_approved"] or 0)),
        "immutable_plan_hash": str(row["immutable_plan_hash"] or "").strip() or None,
        "execution_binding": _json_loads(row["execution_binding"], default={}),
        "checkpoint": _json_loads(row["checkpoint_payload"], default={}),
        "snapshot": _json_loads(row["snapshot_payload"], default={}),
        "metadata": _json_loads(row["metadata"], default={}),
        "created_at": str(row["created_at"] or ""),
        "updated_at": str(row["updated_at"] or ""),
        "last_activity_at": str(row["last_activity_at"] or "").strip() or None,
        "fallback_ready_at": str(row["fallback_ready_at"] or "").strip() or None,
        "interrupted_at": str(row["interrupted_at"] or "").strip() or None,
    }


def _pairing_expired(record: Dict[str, Any]) -> bool:
    raw = str(record.get("expires_at") or "").strip()
    if not raw:
        return True
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return True
    return parsed <= _utc_now()


def _session_expired(record: Dict[str, Any]) -> bool:
    raw = str(record.get("expires_at") or "").strip()
    if not raw:
        return True
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return True
    return parsed <= _utc_now()


def _registration_scope_matches(
    record: Dict[str, Any],
    *,
    tenant_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    user_id: Optional[str] = None,
    device_id: Optional[str] = None,
) -> bool:
    expected = {
        "tenant_id": str(tenant_id or "").strip() or None,
        "workspace_id": str(workspace_id or "").strip() or None,
        "user_id": str(user_id or "").strip() or None,
        "device_id": str(device_id or "").strip() or None,
    }
    for key, value in expected.items():
        if value is None:
            continue
        if str(record.get(key) or "").strip() != value:
            return False
    return True


def _session_scope_matches(session: Dict[str, Any], registration: Dict[str, Any]) -> bool:
    expected_device_id = str(registration.get("device_id") or "").strip()
    return (
        str(session.get("gateway_id") or "").strip() == str(registration.get("gateway_id") or "").strip()
        and str(session.get("tenant_id") or "").strip() == str(registration.get("tenant_id") or "").strip()
        and str(session.get("workspace_id") or "").strip() == str(registration.get("workspace_id") or "").strip()
        and str(session.get("user_id") or "").strip() == str(registration.get("user_id") or "").strip()
        and (str(session.get("device_id") or "").strip() or expected_device_id) == expected_device_id
    )


def init_gateway_state_db(db_path: Optional[Path | str] = None) -> Path:
    resolved = Path(db_path or GATEWAY_STATE_DB_FILE).expanduser()
    with _DB_LOCK:
        conn = _connect(resolved)
        conn.close()
    return resolved


def create_pairing_intent(
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    ttl_seconds: int,
    display_name: Optional[str] = None,
    platform: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    max_pending_intents: Optional[int] = None,
    db_path: Optional[Path | str] = None,
) -> Dict[str, Any]:
    pairing_token = f"gpair_{secrets.token_urlsafe(24)}"
    record = {
        "pairing_id": f"gpairing_{uuid.uuid4().hex}",
        "tenant_id": str(tenant_id or "").strip() or "default",
        "workspace_id": str(workspace_id or "").strip() or "default",
        "user_id": str(user_id or "").strip() or "unknown-user",
        "status": "pending",
        "display_name": str(display_name or "").strip() or None,
        "platform": str(platform or "").strip() or None,
        "metadata": dict(metadata or {}),
        "created_at": _utc_now_iso(),
        "expires_at": _expires_at_iso(ttl_seconds),
        "consumed_at": None,
        "consumed_gateway_id": None,
    }
    _enforce_gateway_state_decision(
        "create_pairing_intent",
        tenant_id=record["tenant_id"],
        workspace_id=record["workspace_id"],
        user_id=record["user_id"],
        actor_role="user",
        payload=record,
        ttl_seconds=max(1, int(ttl_seconds or 0)),
        max_pending_intents=max_pending_intents,
    )
    with _DB_LOCK:
        conn = _connect(db_path)
        try:
            now_iso = _utc_now_iso()
            _enforce_gateway_state_decision(
                "expire_pairing_intent",
                tenant_id=record["tenant_id"],
                workspace_id=record["workspace_id"],
                user_id=record["user_id"],
                actor_role="system",
                payload={"expires_at_lte": now_iso},
                pairing_status="pending",
                bulk_expire=True,
            )
            conn.execute(
                """
                UPDATE gateway_pairing_intents
                SET status = ?, consumed_at = COALESCE(consumed_at, ?)
                WHERE tenant_id = ? AND workspace_id = ? AND user_id = ?
                  AND status = 'pending' AND expires_at <= ?
                """,
                (
                    "expired",
                    now_iso,
                    record["tenant_id"],
                    record["workspace_id"],
                    record["user_id"],
                    now_iso,
                ),
            )
            if max_pending_intents is not None:
                pending_row = conn.execute(
                    """
                    SELECT COUNT(*) AS pending_count
                    FROM gateway_pairing_intents
                    WHERE tenant_id = ? AND workspace_id = ? AND user_id = ?
                      AND status = 'pending' AND expires_at > ?
                    """,
                    (
                        record["tenant_id"],
                        record["workspace_id"],
                        record["user_id"],
                        now_iso,
                    ),
                ).fetchone()
                pending_count = int((pending_row["pending_count"] if pending_row else 0) or 0)
                if pending_count >= max(1, int(max_pending_intents or 0)):
                    raise ValueError(
                        "Too many pending gateway pairing requests. Use an existing code or wait for one to expire."
                    )
            conn.execute(
                """
                INSERT INTO gateway_pairing_intents (
                    pairing_id, tenant_id, workspace_id, user_id, token_hash, status,
                    display_name, platform, metadata, created_at, expires_at,
                    consumed_at, consumed_gateway_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["pairing_id"],
                    record["tenant_id"],
                    record["workspace_id"],
                    record["user_id"],
                    _hash_token(pairing_token),
                    record["status"],
                    record["display_name"],
                    record["platform"],
                    _json_dumps(record["metadata"]),
                    record["created_at"],
                    record["expires_at"],
                    record["consumed_at"],
                    record["consumed_gateway_id"],
                ),
            )
            conn.commit()
        finally:
            conn.close()
    record["pairing_token"] = pairing_token
    return record


def get_pairing_intent_by_token(
    pairing_token: str,
    *,
    db_path: Optional[Path | str] = None,
) -> Optional[Dict[str, Any]]:
    token_hash = _hash_token(pairing_token)
    with _DB_LOCK:
        conn = _connect(db_path)
        try:
            row = conn.execute(
                "SELECT * FROM gateway_pairing_intents WHERE token_hash = ?",
                (token_hash,),
            ).fetchone()
        finally:
            conn.close()
    return _pairing_from_row(row)


def register_gateway_from_pairing(
    *,
    pairing_token: str,
    device_id: str,
    gateway_id: Optional[str] = None,
    display_name: Optional[str] = None,
    platform: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    capabilities: Optional[List[str]] = None,
    db_path: Optional[Path | str] = None,
) -> Dict[str, Any]:
    token_hash = _hash_token(pairing_token)
    gateway_token = f"ggt_{secrets.token_urlsafe(32)}"
    now_iso = _utc_now_iso()
    device_token = str(device_id or "").strip()
    if not device_token:
        raise ValueError("device_id is required.")
    with _DB_LOCK:
        conn = _connect(db_path)
        try:
            pairing_row = conn.execute(
                "SELECT * FROM gateway_pairing_intents WHERE token_hash = ?",
                (token_hash,),
            ).fetchone()
            pairing = _pairing_from_row(pairing_row)
            if pairing is None:
                raise ValueError("Pairing token is invalid.")
            if pairing["status"] != "pending":
                raise ValueError("Pairing token is no longer active.")
            if _pairing_expired(pairing):
                _enforce_gateway_state_decision(
                    "expire_pairing_intent",
                    tenant_id=pairing["tenant_id"],
                    workspace_id=pairing["workspace_id"],
                    user_id=pairing["user_id"],
                    actor_role="gateway",
                    payload={"pairing_id": pairing["pairing_id"]},
                    pairing_id=pairing["pairing_id"],
                    pairing_status=pairing["status"],
                    pairing_expired=True,
                )
                conn.execute(
                    "UPDATE gateway_pairing_intents SET status = ?, consumed_at = ? WHERE pairing_id = ?",
                    ("expired", now_iso, pairing["pairing_id"]),
                )
                conn.commit()
                raise ValueError("Pairing token has expired.")

            existing_row = conn.execute(
                """
                SELECT * FROM gateway_registrations
                WHERE workspace_id = ? AND user_id = ? AND device_id = ?
                """,
                (pairing["workspace_id"], pairing["user_id"], device_token),
            ).fetchone()
            existing = _registration_from_row(existing_row)
            resolved_gateway_id = (
                str(gateway_id or "").strip()
                or (existing["gateway_id"] if existing else "")
                or f"gateway_{uuid.uuid4().hex}"
            )
            merged_metadata = dict(existing.get("metadata") or {}) if existing else {}
            pairing_metadata = dict(pairing.get("metadata") or {})
            merged_metadata.update(pairing_metadata)
            merged_metadata.update(_strip_owner_runtime_access_metadata(dict(metadata or {})))
            merged_metadata.update(
                _runtime_access_metadata_from_pairing(
                    pairing_metadata=pairing_metadata,
                    existing_metadata=dict(existing.get("metadata") or {}) if existing else {},
                )
            )
            merged_metadata.update(
                {
                    "tenant_id": pairing["tenant_id"],
                    "workspace_id": pairing["workspace_id"],
                    "user_id": pairing["user_id"],
                    "device_id": device_token,
                    "gateway_id": resolved_gateway_id,
                }
            )
            _enforce_gateway_state_decision(
                "register_gateway",
                tenant_id=pairing["tenant_id"],
                workspace_id=pairing["workspace_id"],
                user_id=pairing["user_id"],
                device_id=device_token,
                gateway_id=resolved_gateway_id,
                actor_role="gateway",
                payload={
                    "pairing_id": pairing["pairing_id"],
                    "metadata": merged_metadata,
                    "capabilities": list(capabilities or existing.get("capabilities") or []) if existing else list(capabilities or []),
                },
                token_valid=True,
                pairing_status=pairing["status"],
                pairing_expired=False,
                existing_gateway=bool(existing),
                capability_count=len(list(capabilities or existing.get("capabilities") or [])) if existing else len(list(capabilities or [])),
            )
            if existing:
                conn.execute(
                    """
                    UPDATE gateway_registrations
                    SET tenant_id = ?, workspace_id = ?, user_id = ?, status = ?, device_trust_state = ?,
                        display_name = ?, platform = ?, gateway_token_hash = ?, metadata = ?, capabilities = ?,
                        updated_at = ?, last_seen_at = ?, token_rotated_at = ?, revoked_at = NULL,
                        revoked_reason = NULL
                    WHERE gateway_id = ?
                    """,
                    (
                        pairing["tenant_id"],
                        pairing["workspace_id"],
                        pairing["user_id"],
                        "active",
                        "verified",
                        str(display_name or existing.get("display_name") or "").strip() or None,
                        str(platform or existing.get("platform") or "").strip() or None,
                        _hash_token(gateway_token),
                        _json_dumps(merged_metadata),
                        _json_dumps(list(capabilities or existing.get("capabilities") or [])),
                        now_iso,
                        now_iso,
                        now_iso,
                        resolved_gateway_id,
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO gateway_registrations (
                        gateway_id, device_id, tenant_id, workspace_id, user_id, status,
                        device_trust_state, display_name, platform, gateway_token_hash,
                        metadata, capabilities, journal_cursor, checkpoint_cursor,
                        created_at, updated_at, last_seen_at, last_heartbeat_at,
                        token_rotated_at, revoked_at, revoked_reason
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        resolved_gateway_id,
                        device_token,
                        pairing["tenant_id"],
                        pairing["workspace_id"],
                        pairing["user_id"],
                        "active",
                        "verified",
                        str(display_name or pairing.get("display_name") or "").strip() or None,
                        str(platform or pairing.get("platform") or "").strip() or None,
                        _hash_token(gateway_token),
                        _json_dumps(merged_metadata),
                        _json_dumps(list(capabilities or [])),
                        0,
                        0,
                        now_iso,
                        now_iso,
                        now_iso,
                        None,
                        now_iso,
                        None,
                        None,
                    ),
                )
            conn.execute(
                """
                UPDATE gateway_pairing_intents
                SET status = ?, consumed_at = ?, consumed_gateway_id = ?
                WHERE pairing_id = ?
                """,
                ("consumed", now_iso, resolved_gateway_id, pairing["pairing_id"]),
            )
            conn.execute(
                """
                INSERT INTO gateway_events (
                    gateway_id, session_id, direction, frame_kind, message_type, seq, ack, payload, created_at
                ) VALUES (?, NULL, 'system', 'event', 'gateway.registered', NULL, NULL, ?, ?)
                """,
                (
                    resolved_gateway_id,
                    _json_dumps(
                        {
                            "tenant_id": pairing["tenant_id"],
                            "workspace_id": pairing["workspace_id"],
                            "user_id": pairing["user_id"],
                            "device_id": device_token,
                        }
                    ),
                    now_iso,
                ),
            )
            conn.commit()
            registration_row = conn.execute(
                "SELECT * FROM gateway_registrations WHERE gateway_id = ?",
                (resolved_gateway_id,),
            ).fetchone()
        finally:
            conn.close()
    registration = _registration_from_row(registration_row) or {}
    registration["gateway_token"] = gateway_token
    return registration


def get_gateway_registration(
    gateway_id: str,
    *,
    db_path: Optional[Path | str] = None,
) -> Optional[Dict[str, Any]]:
    token = str(gateway_id or "").strip()
    if not token:
        return None
    with _DB_LOCK:
        conn = _connect(db_path)
        try:
            row = conn.execute(
                "SELECT * FROM gateway_registrations WHERE gateway_id = ?",
                (token,),
            ).fetchone()
        finally:
            conn.close()
    return _registration_from_row(row)


def get_latest_gateway_session(
    gateway_id: str,
    *,
    include_revoked: bool = False,
    db_path: Optional[Path | str] = None,
) -> Optional[Dict[str, Any]]:
    gateway_token = str(gateway_id or "").strip()
    if not gateway_token:
        return None
    with _DB_LOCK:
        conn = _connect(db_path)
        try:
            where = ["gateway_id = ?"]
            params: List[Any] = [gateway_token]
            if not include_revoked:
                where.append("status != 'revoked'")
            row = conn.execute(
                f"""
                SELECT * FROM gateway_sessions
                WHERE {' AND '.join(where)}
                ORDER BY updated_at DESC, created_at DESC
                LIMIT 1
                """,
                tuple(params),
            ).fetchone()
        finally:
            conn.close()
    return _session_from_row(row)


def list_gateway_sessions(
    gateway_id: str,
    *,
    include_revoked: bool = False,
    limit: int = 20,
    db_path: Optional[Path | str] = None,
) -> List[Dict[str, Any]]:
    gateway_token = str(gateway_id or "").strip()
    if not gateway_token:
        return []
    where = ["gateway_id = ?"]
    params: List[Any] = [gateway_token]
    if not include_revoked:
        where.append("status != 'revoked'")
    params.append(max(int(limit or 0), 1))
    with _DB_LOCK:
        conn = _connect(db_path)
        try:
            rows = conn.execute(
                f"""
                SELECT * FROM gateway_sessions
                WHERE {' AND '.join(where)}
                ORDER BY updated_at DESC, created_at DESC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        finally:
            conn.close()
    return [item for item in (_session_from_row(row) for row in rows) if item]


def list_workspace_gateway_registrations(
    workspace_id: str,
    *,
    tenant_id: Optional[str] = None,
    user_id: Optional[str] = None,
    include_revoked: bool = True,
    db_path: Optional[Path | str] = None,
) -> List[Dict[str, Any]]:
    workspace_token = str(workspace_id or "").strip() or "default"
    params: List[Any] = [workspace_token]
    where = ["workspace_id = ?"]
    if tenant_id:
        where.append("tenant_id = ?")
        params.append(str(tenant_id or "").strip())
    if user_id:
        where.append("user_id = ?")
        params.append(str(user_id or "").strip())
    if not include_revoked:
        where.append("status != 'revoked'")
    query = f"""
        SELECT * FROM gateway_registrations
        WHERE {' AND '.join(where)}
        ORDER BY updated_at DESC, gateway_id ASC
    """
    with _DB_LOCK:
        conn = _connect(db_path)
        try:
            rows = conn.execute(query, tuple(params)).fetchall()
        finally:
            conn.close()
    return [item for item in (_registration_from_row(row) for row in rows) if item]


def sync_gateway_registration_identity(
    *,
    gateway_id: str,
    tenant_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    user_id: Optional[str] = None,
    device_id: Optional[str] = None,
    device_trust_state: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    status: Optional[str] = None,
    db_path: Optional[Path | str] = None,
) -> Optional[Dict[str, Any]]:
    now_iso = _utc_now_iso()
    with _DB_LOCK:
        conn = _connect(db_path)
        try:
            row = conn.execute(
                "SELECT * FROM gateway_registrations WHERE gateway_id = ?",
                (str(gateway_id or "").strip(),),
            ).fetchone()
            registration = _registration_from_row(row)
            if registration is None:
                return None
            merged_metadata = dict(registration.get("metadata") or {})
            merged_metadata.update(dict(metadata or {}))
            resolved_tenant_id = str(tenant_id or registration.get("tenant_id") or "").strip()
            resolved_workspace_id = str(workspace_id or registration.get("workspace_id") or "").strip()
            resolved_user_id = str(user_id or registration.get("user_id") or "").strip()
            resolved_device_id = str(device_id or registration.get("device_id") or "").strip()
            next_status = str(status or registration.get("status") or "active").strip() or "active"
            next_device_trust_state = (
                str(device_trust_state or registration.get("device_trust_state") or "verified").strip()
                or "verified"
            )
            merged_metadata.update(
                {
                    "tenant_id": resolved_tenant_id,
                    "workspace_id": resolved_workspace_id,
                    "user_id": resolved_user_id,
                    "device_id": resolved_device_id,
                    "gateway_id": str(gateway_id or "").strip(),
                }
            )
            _enforce_gateway_state_decision(
                "update_registration_state",
                tenant_id=registration.get("tenant_id"),
                workspace_id=registration.get("workspace_id"),
                user_id=registration.get("user_id"),
                device_id=registration.get("device_id"),
                gateway_id=registration.get("gateway_id"),
                actor_role="system",
                payload=merged_metadata,
                registration_status=registration.get("status"),
                next_status=next_status,
                next_tenant_id=resolved_tenant_id,
                next_workspace_id=resolved_workspace_id,
                next_user_id=resolved_user_id,
                next_device_id=resolved_device_id,
                next_device_trust_state=next_device_trust_state,
                reason=merged_metadata.get("reason") or merged_metadata.get("revoked_reason") or merged_metadata.get("note"),
            )
            conn.execute(
                """
                UPDATE gateway_registrations
                SET tenant_id = ?, workspace_id = ?, user_id = ?, device_id = ?,
                    status = ?, device_trust_state = ?, metadata = ?, updated_at = ?
                WHERE gateway_id = ?
                """,
                (
                    resolved_tenant_id,
                    resolved_workspace_id,
                    resolved_user_id,
                    resolved_device_id,
                    next_status,
                    next_device_trust_state,
                    _json_dumps(merged_metadata),
                    now_iso,
                    str(gateway_id or "").strip(),
                ),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM gateway_registrations WHERE gateway_id = ?",
                (str(gateway_id or "").strip(),),
            ).fetchone()
        finally:
            conn.close()
    return _registration_from_row(row)


def rotate_gateway_token(
    *,
    gateway_id: str,
    tenant_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    user_id: Optional[str] = None,
    db_path: Optional[Path | str] = None,
) -> Dict[str, Any]:
    now_iso = _utc_now_iso()
    gateway_token = f"ggt_{secrets.token_urlsafe(32)}"
    with _DB_LOCK:
        conn = _connect(db_path)
        try:
            row = conn.execute(
                "SELECT * FROM gateway_registrations WHERE gateway_id = ?",
                (str(gateway_id or "").strip(),),
            ).fetchone()
            registration = _registration_from_row(row)
            if registration is None:
                raise ValueError("Gateway registration was not found.")
            if not _registration_scope_matches(
                registration,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                user_id=user_id,
            ):
                raise ValueError("Gateway registration scope mismatch.")
            if str(registration.get("status") or "").strip() == "revoked":
                raise ValueError("Gateway registration has been revoked.")
            _enforce_gateway_state_decision(
                "rotate_token",
                tenant_id=registration.get("tenant_id"),
                workspace_id=registration.get("workspace_id"),
                user_id=registration.get("user_id"),
                device_id=registration.get("device_id"),
                gateway_id=registration.get("gateway_id"),
                actor_role="system",
                registration_status=registration.get("status"),
            )
            conn.execute(
                """
                UPDATE gateway_registrations
                SET gateway_token_hash = ?, token_rotated_at = ?, updated_at = ?, last_seen_at = ?
                WHERE gateway_id = ?
                """,
                (
                    _hash_token(gateway_token),
                    now_iso,
                    now_iso,
                    registration.get("last_seen_at"),
                    str(gateway_id or "").strip(),
                ),
            )
            conn.execute(
                """
                INSERT INTO gateway_events (
                    gateway_id, session_id, direction, frame_kind, message_type, seq, ack, payload, created_at
                ) VALUES (?, NULL, 'system', 'event', 'gateway.token_rotated', NULL, NULL, ?, ?)
                """,
                (
                    str(gateway_id or "").strip(),
                    _json_dumps(
                        {
                            "tenant_id": registration.get("tenant_id"),
                            "workspace_id": registration.get("workspace_id"),
                            "user_id": registration.get("user_id"),
                            "device_id": registration.get("device_id"),
                        }
                    ),
                    now_iso,
                ),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM gateway_registrations WHERE gateway_id = ?",
                (str(gateway_id or "").strip(),),
            ).fetchone()
        finally:
            conn.close()
    payload = _registration_from_row(row) or {}
    payload["gateway_token"] = gateway_token
    return payload


def issue_gateway_session(
    *,
    gateway_id: str,
    gateway_token: str,
    ttl_seconds: int,
    metadata: Optional[Dict[str, Any]] = None,
    db_path: Optional[Path | str] = None,
) -> Dict[str, Any]:
    gateway_token_hash = _hash_token(gateway_token)
    session_token = f"gws_{secrets.token_urlsafe(32)}"
    session_id = f"gsess_{uuid.uuid4().hex}"
    now_iso = _utc_now_iso()
    with _DB_LOCK:
        conn = _connect(db_path)
        try:
            row = conn.execute(
                """
                SELECT * FROM gateway_registrations
                WHERE gateway_id = ? AND gateway_token_hash = ? AND status = 'active'
                """,
                (str(gateway_id or "").strip(), gateway_token_hash),
            ).fetchone()
            registration = _registration_from_row(row)
            if registration is None:
                raise ValueError("Gateway credentials are invalid.")
            if str(registration.get("device_trust_state") or "").strip() == "revoked":
                raise ValueError("Gateway device trust was revoked.")
            _enforce_gateway_state_decision(
                "issue_session",
                tenant_id=registration.get("tenant_id"),
                workspace_id=registration.get("workspace_id"),
                user_id=registration.get("user_id"),
                device_id=registration.get("device_id"),
                gateway_id=registration.get("gateway_id"),
                actor_role="system",
                token_valid=True,
                registration_status=registration.get("status"),
                device_trust_state=registration.get("device_trust_state"),
                ttl_seconds=ttl_seconds,
            )
            session_metadata = dict(metadata or {})
            session_metadata.update(
                {
                    "tenant_id": registration["tenant_id"],
                    "workspace_id": registration["workspace_id"],
                    "user_id": registration["user_id"],
                    "device_id": registration["device_id"],
                    "gateway_id": registration["gateway_id"],
                    "session_kind": "local_gateway",
                }
            )
            conn.execute(
                """
                INSERT INTO gateway_sessions (
                    session_id, gateway_id, device_id, tenant_id, workspace_id, user_id,
                    session_token_hash, status, metadata, created_at, updated_at,
                    connected_at, disconnected_at, expires_at, last_seq, last_ack,
                    last_heartbeat_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    registration["gateway_id"],
                    registration["device_id"],
                    registration["tenant_id"],
                    registration["workspace_id"],
                    registration["user_id"],
                    _hash_token(session_token),
                    "pending",
                    _json_dumps(session_metadata),
                    now_iso,
                    now_iso,
                    None,
                    None,
                    _expires_at_iso(ttl_seconds),
                    0,
                    0,
                    None,
                ),
            )
            conn.commit()
        finally:
            conn.close()
    return {
        "session_id": session_id,
        "gateway_id": str(gateway_id or "").strip(),
        "device_id": str(registration.get("device_id") or "").strip(),
        "session_token": session_token,
        "created_at": now_iso,
        "expires_at": _expires_at_iso(ttl_seconds),
    }


def validate_gateway_session(
    *,
    gateway_id: str,
    session_token: str,
    db_path: Optional[Path | str] = None,
) -> Dict[str, Any]:
    token_hash = _hash_token(session_token)
    with _DB_LOCK:
        conn = _connect(db_path)
        try:
            row = conn.execute(
                """
                SELECT * FROM gateway_sessions
                WHERE gateway_id = ? AND session_token_hash = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (str(gateway_id or "").strip(), token_hash),
            ).fetchone()
            session = _session_from_row(row)
            if session is None:
                raise ValueError("Gateway session is invalid.")
            registration_row = conn.execute(
                "SELECT * FROM gateway_registrations WHERE gateway_id = ?",
                (str(gateway_id or "").strip(),),
            ).fetchone()
            registration = _registration_from_row(registration_row)
        finally:
            conn.close()
    if registration is None:
        raise ValueError("Gateway registration was not found.")
    if str(registration.get("status") or "").strip() == "revoked":
        raise ValueError("Gateway registration has been revoked.")
    if str(registration.get("device_trust_state") or "").strip() == "revoked":
        raise ValueError("Gateway device trust was revoked.")
    if str(session.get("status") or "").strip() == "revoked":
        raise ValueError("Gateway session has been revoked.")
    if _session_expired(session):
        mark_gateway_session_disconnected(session["session_id"], reason="expired", db_path=db_path)
        raise ValueError("Gateway session has expired.")
    if not _session_scope_matches(session, registration):
        mark_gateway_session_disconnected(session["session_id"], reason="scope_mismatch", db_path=db_path)
        raise ValueError("Gateway session scope does not match its registration.")
    return session


def revoke_gateway_registration(
    *,
    gateway_id: str,
    tenant_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    user_id: Optional[str] = None,
    reason: Optional[str] = None,
    db_path: Optional[Path | str] = None,
) -> Optional[Dict[str, Any]]:
    now_iso = _utc_now_iso()
    clean_reason = str(reason or "").strip() or "Gateway registration revoked."
    with _DB_LOCK:
        conn = _connect(db_path)
        try:
            row = conn.execute(
                "SELECT * FROM gateway_registrations WHERE gateway_id = ?",
                (str(gateway_id or "").strip(),),
            ).fetchone()
            registration = _registration_from_row(row)
            if registration is None:
                return None
            if not _registration_scope_matches(
                registration,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                user_id=user_id,
            ):
                raise ValueError("Gateway registration scope mismatch.")
            _enforce_gateway_state_decision(
                "revoke_registration",
                tenant_id=registration.get("tenant_id"),
                workspace_id=registration.get("workspace_id"),
                user_id=registration.get("user_id"),
                device_id=registration.get("device_id"),
                gateway_id=registration.get("gateway_id"),
                actor_role="system",
                registration_status=registration.get("status"),
                reason=clean_reason,
            )
            metadata = dict(registration.get("metadata") or {})
            metadata["revoked_reason"] = clean_reason
            conn.execute(
                """
                UPDATE gateway_registrations
                SET status = 'revoked', device_trust_state = 'revoked', updated_at = ?,
                    revoked_at = ?, revoked_reason = ?, metadata = ?
                WHERE gateway_id = ?
                """,
                (
                    now_iso,
                    now_iso,
                    clean_reason,
                    _json_dumps(metadata),
                    str(gateway_id or "").strip(),
                ),
            )
            session_rows = conn.execute(
                "SELECT * FROM gateway_sessions WHERE gateway_id = ? AND status != 'revoked'",
                (str(gateway_id or "").strip(),),
            ).fetchall()
            for session_row in session_rows:
                session = _session_from_row(session_row) or {}
                session_metadata = dict(session.get("metadata") or {})
                session_metadata["disconnect_reason"] = clean_reason
                conn.execute(
                    """
                    UPDATE gateway_sessions
                    SET status = 'revoked', updated_at = ?, disconnected_at = ?, metadata = ?
                    WHERE session_id = ?
                    """,
                    (
                        now_iso,
                        now_iso,
                        _json_dumps(session_metadata),
                        str(session.get("session_id") or "").strip(),
                    ),
                )
            conn.execute(
                """
                INSERT INTO gateway_events (
                    gateway_id, session_id, direction, frame_kind, message_type, seq, ack, payload, created_at
                ) VALUES (?, NULL, 'system', 'event', 'gateway.revoked', NULL, NULL, ?, ?)
                """,
                (
                    str(gateway_id or "").strip(),
                    _json_dumps(
                        {
                            "tenant_id": registration.get("tenant_id"),
                            "workspace_id": registration.get("workspace_id"),
                            "user_id": registration.get("user_id"),
                            "device_id": registration.get("device_id"),
                            "reason": clean_reason,
                        }
                    ),
                    now_iso,
                ),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM gateway_registrations WHERE gateway_id = ?",
                (str(gateway_id or "").strip(),),
            ).fetchone()
        finally:
            conn.close()
    return _registration_from_row(row)


def mark_gateway_session_connected(
    session_id: str,
    *,
    db_path: Optional[Path | str] = None,
) -> Optional[Dict[str, Any]]:
    now_iso = _utc_now_iso()
    with _DB_LOCK:
        conn = _connect(db_path)
        try:
            row = conn.execute(
                "SELECT * FROM gateway_sessions WHERE session_id = ?",
                (str(session_id or "").strip(),),
            ).fetchone()
            session = _session_from_row(row)
            if session is None:
                return None
            _enforce_gateway_state_decision(
                "mark_session_connected",
                tenant_id=session.get("tenant_id"),
                workspace_id=session.get("workspace_id"),
                user_id=session.get("user_id"),
                device_id=session.get("device_id"),
                gateway_id=session.get("gateway_id"),
                session_id=session.get("session_id"),
                actor_role="system",
                session_status=session.get("status"),
            )
            conn.execute(
                """
                UPDATE gateway_sessions
                SET status = 'connected', connected_at = COALESCE(connected_at, ?),
                    updated_at = ?, disconnected_at = NULL
                WHERE session_id = ?
                """,
                (now_iso, now_iso, str(session_id or "").strip()),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM gateway_sessions WHERE session_id = ?",
                (str(session_id or "").strip(),),
            ).fetchone()
        finally:
            conn.close()
    return _session_from_row(row)


def mark_gateway_session_disconnected(
    session_id: str,
    *,
    reason: Optional[str] = None,
    db_path: Optional[Path | str] = None,
) -> Optional[Dict[str, Any]]:
    now_iso = _utc_now_iso()
    with _DB_LOCK:
        conn = _connect(db_path)
        try:
            row = conn.execute(
                "SELECT * FROM gateway_sessions WHERE session_id = ?",
                (str(session_id or "").strip(),),
            ).fetchone()
            session = _session_from_row(row)
            if session is None:
                return None
            metadata = dict(session.get("metadata") or {})
            if reason:
                metadata["disconnect_reason"] = str(reason or "").strip()
            _enforce_gateway_state_decision(
                "mark_session_disconnected",
                tenant_id=session.get("tenant_id"),
                workspace_id=session.get("workspace_id"),
                user_id=session.get("user_id"),
                device_id=session.get("device_id"),
                gateway_id=session.get("gateway_id"),
                session_id=session.get("session_id"),
                actor_role="system",
                session_status=session.get("status"),
                reason=str(reason or "").strip(),
            )
            conn.execute(
                """
                UPDATE gateway_sessions
                SET status = CASE WHEN status = 'revoked' THEN status ELSE 'disconnected' END,
                    disconnected_at = ?, updated_at = ?, metadata = ?
                WHERE session_id = ?
                """,
                (now_iso, now_iso, _json_dumps(metadata), str(session_id or "").strip()),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM gateway_sessions WHERE session_id = ?",
                (str(session_id or "").strip(),),
            ).fetchone()
        finally:
            conn.close()
    return _session_from_row(row)


def touch_gateway_session(
    *,
    session_id: str,
    gateway_id: str,
    seq: Optional[int] = None,
    ack: Optional[int] = None,
    health_state: Optional[str] = None,
    journal_cursor: Optional[int] = None,
    checkpoint_cursor: Optional[int] = None,
    metadata: Optional[Dict[str, Any]] = None,
    db_path: Optional[Path | str] = None,
) -> None:
    now_iso = _utc_now_iso()
    with _DB_LOCK:
        conn = _connect(db_path)
        try:
            row = conn.execute(
                "SELECT * FROM gateway_sessions WHERE session_id = ?",
                (str(session_id or "").strip(),),
            ).fetchone()
            session = _session_from_row(row)
            if session is None:
                return
            session_metadata = dict(session.get("metadata") or {})
            session_metadata.update(dict(metadata or {}))
            if health_state:
                session_metadata["health_state"] = str(health_state or "").strip()
            decision_cursor_fields: Dict[str, int] = {}
            if seq is not None:
                decision_cursor_fields["seq"] = int(seq or 0)
                decision_cursor_fields["previous_seq"] = int(session.get("last_seq") or 0)
            if ack is not None:
                decision_cursor_fields["ack"] = int(ack or 0)
                decision_cursor_fields["previous_ack"] = int(session.get("last_ack") or 0)
            _enforce_gateway_state_decision(
                "touch_session",
                tenant_id=session.get("tenant_id"),
                workspace_id=session.get("workspace_id"),
                user_id=session.get("user_id"),
                device_id=session.get("device_id"),
                gateway_id=session.get("gateway_id"),
                session_id=session.get("session_id"),
                actor_role="system",
                session_status=session.get("status"),
                **decision_cursor_fields,
            )
            conn.execute(
                """
                UPDATE gateway_sessions
                SET status = CASE WHEN status = 'pending' THEN 'connected' ELSE status END,
                    updated_at = ?, last_heartbeat_at = ?, last_seq = ?, last_ack = ?, metadata = ?
                WHERE session_id = ?
                """,
                (
                    now_iso,
                    now_iso,
                    max(int(seq or 0), int(session.get("last_seq") or 0)),
                    max(int(ack or 0), int(session.get("last_ack") or 0)),
                    _json_dumps(session_metadata),
                    str(session_id or "").strip(),
                ),
            )
            registration_row = conn.execute(
                "SELECT * FROM gateway_registrations WHERE gateway_id = ?",
                (str(gateway_id or "").strip(),),
            ).fetchone()
            registration = _registration_from_row(registration_row) or {}
            registration_metadata = dict(registration.get("metadata") or {})
            registration_metadata.update(dict(metadata or {}))
            if health_state:
                registration_metadata["health_state"] = str(health_state or "").strip()
            conn.execute(
                """
                UPDATE gateway_registrations
                SET updated_at = ?, last_seen_at = ?, last_heartbeat_at = ?, metadata = ?,
                    journal_cursor = ?, checkpoint_cursor = ?
                WHERE gateway_id = ?
                """,
                (
                    now_iso,
                    now_iso,
                    now_iso,
                    _json_dumps(registration_metadata),
                    int(journal_cursor if journal_cursor is not None else registration.get("journal_cursor") or 0),
                    int(checkpoint_cursor if checkpoint_cursor is not None else registration.get("checkpoint_cursor") or 0),
                    str(gateway_id or "").strip(),
                ),
            )
            conn.commit()
        finally:
            conn.close()


def update_gateway_registration_state(
    *,
    gateway_id: str,
    metadata: Optional[Dict[str, Any]] = None,
    metadata_keys_to_remove: Optional[List[str]] = None,
    journal_cursor: Optional[int] = None,
    checkpoint_cursor: Optional[int] = None,
    device_trust_state: Optional[str] = None,
    status: Optional[str] = None,
    db_path: Optional[Path | str] = None,
) -> Optional[Dict[str, Any]]:
    now_iso = _utc_now_iso()
    with _DB_LOCK:
        conn = _connect(db_path)
        try:
            row = conn.execute(
                "SELECT * FROM gateway_registrations WHERE gateway_id = ?",
                (str(gateway_id or "").strip(),),
            ).fetchone()
            registration = _registration_from_row(row)
            if registration is None:
                return None
            merged_metadata = dict(registration.get("metadata") or {})
            for key in metadata_keys_to_remove or []:
                clean_key = str(key or "").strip()
                if clean_key:
                    merged_metadata.pop(clean_key, None)
            merged_metadata.update(dict(metadata or {}))
            next_status = str(status or registration.get("status") or "active").strip() or "active"
            _enforce_gateway_state_decision(
                "update_registration_state",
                tenant_id=registration.get("tenant_id"),
                workspace_id=registration.get("workspace_id"),
                user_id=registration.get("user_id"),
                device_id=registration.get("device_id"),
                gateway_id=registration.get("gateway_id"),
                actor_role="system",
                registration_status=registration.get("status"),
                next_status=next_status,
                reason=merged_metadata.get("reason") or merged_metadata.get("revoked_reason") or merged_metadata.get("note"),
            )
            conn.execute(
                """
                UPDATE gateway_registrations
                SET metadata = ?, updated_at = ?, last_seen_at = ?,
                    journal_cursor = ?, checkpoint_cursor = ?, device_trust_state = ?, status = ?
                WHERE gateway_id = ?
                """,
                (
                    _json_dumps(merged_metadata),
                    now_iso,
                    now_iso,
                    int(journal_cursor if journal_cursor is not None else registration.get("journal_cursor") or 0),
                    int(checkpoint_cursor if checkpoint_cursor is not None else registration.get("checkpoint_cursor") or 0),
                    str(device_trust_state or registration.get("device_trust_state") or "verified").strip() or "verified",
                    next_status,
                    str(gateway_id or "").strip(),
                ),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM gateway_registrations WHERE gateway_id = ?",
                (str(gateway_id or "").strip(),),
            ).fetchone()
        finally:
            conn.close()
    return _registration_from_row(row)


def record_gateway_event(
    *,
    gateway_id: str,
    session_id: Optional[str],
    direction: str,
    frame_kind: str,
    message_type: str,
    payload: Optional[Dict[str, Any]],
    seq: Optional[int] = None,
    ack: Optional[int] = None,
    db_path: Optional[Path | str] = None,
) -> None:
    sanitized_payload = sanitize_gateway_event_payload(payload, message_type=message_type)
    gateway_token = str(gateway_id or "").strip()
    session_token = str(session_id or "").strip()
    with _DB_LOCK:
        conn = _connect(db_path)
        try:
            registration_row = conn.execute(
                "SELECT * FROM gateway_registrations WHERE gateway_id = ?",
                (gateway_token,),
            ).fetchone()
            registration = _registration_from_row(registration_row) or {}
            session: Dict[str, Any] = {}
            if session_token:
                session_row = conn.execute(
                    "SELECT * FROM gateway_sessions WHERE session_id = ?",
                    (session_token,),
                ).fetchone()
                session = _session_from_row(session_row) or {}
            scope = session or registration
            _enforce_gateway_state_decision(
                "record_event",
                tenant_id=scope.get("tenant_id"),
                workspace_id=scope.get("workspace_id"),
                user_id=scope.get("user_id"),
                device_id=scope.get("device_id"),
                gateway_id=gateway_token or scope.get("gateway_id"),
                session_id=session_token,
                actor_role="system",
                direction=str(direction or "").strip() or "unknown",
                frame_kind=str(frame_kind or "").strip() or "unknown",
                message_type=str(message_type or "").strip() or "unknown",
                payload=sanitized_payload,
            )
            conn.execute(
                """
                INSERT INTO gateway_events (
                    gateway_id, session_id, direction, frame_kind, message_type, seq, ack, payload, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    gateway_token,
                    session_token or None,
                    str(direction or "").strip() or "unknown",
                    str(frame_kind or "").strip() or "unknown",
                    str(message_type or "").strip() or "unknown",
                    int(seq) if seq is not None else None,
                    int(ack) if ack is not None else None,
                    _json_dumps(sanitized_payload),
                    _utc_now_iso(),
                ),
            )
            conn.commit()
        finally:
            conn.close()


def list_gateway_events(
    gateway_id: str,
    *,
    session_id: Optional[str] = None,
    limit: int = 100,
    db_path: Optional[Path | str] = None,
) -> List[Dict[str, Any]]:
    where = ["gateway_id = ?"]
    params: List[Any] = [str(gateway_id or "").strip()]
    if session_id:
        where.append("session_id = ?")
        params.append(str(session_id or "").strip())
    params.append(max(int(limit or 0), 1))
    with _DB_LOCK:
        conn = _connect(db_path)
        try:
            rows = conn.execute(
                f"""
                SELECT gateway_id, session_id, direction, frame_kind, message_type, seq, ack, payload, created_at
                FROM gateway_events
                WHERE {' AND '.join(where)}
                ORDER BY id ASC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        finally:
            conn.close()
    return [
        {
            "gateway_id": str(row["gateway_id"] or ""),
            "session_id": str(row["session_id"] or "").strip() or None,
            "direction": str(row["direction"] or ""),
            "frame_kind": str(row["frame_kind"] or ""),
            "message_type": str(row["message_type"] or ""),
            "seq": int(row["seq"]) if row["seq"] is not None else None,
            "ack": int(row["ack"]) if row["ack"] is not None else None,
            "payload": _json_loads(row["payload"], default={}),
            "created_at": str(row["created_at"] or ""),
        }
        for row in rows
    ]


def create_gateway_action_approval(
    *,
    gateway_id: str,
    device_id: str,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    capability_id: str,
    run_id: str,
    trace_id: Optional[str] = None,
    request_id: Optional[str] = None,
    request_payload: Optional[Dict[str, Any]] = None,
    approval_id: Optional[str] = None,
    db_path: Optional[Path | str] = None,
) -> Dict[str, Any]:
    now_iso = _utc_now_iso()
    resolved_approval_id = str(approval_id or f"gapproval_{uuid.uuid4().hex}").strip()
    _enforce_gateway_state_decision(
        "create_approval",
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
        device_id=device_id,
        gateway_id=gateway_id,
        actor_role="system",
        run_id=str(run_id or "").strip(),
        capability_id=str(capability_id or "").strip(),
        request_id=str(request_id or "").strip(),
        approval_id=resolved_approval_id,
        payload=request_payload,
    )
    with _DB_LOCK:
        conn = _connect(db_path)
        try:
            existing_row = conn.execute(
                "SELECT * FROM gateway_action_approvals WHERE approval_id = ?",
                (resolved_approval_id,),
            ).fetchone()
            if existing_row is not None:
                return _approval_from_row(existing_row) or {}
            conn.execute(
                """
                INSERT INTO gateway_action_approvals (
                    approval_id, gateway_id, device_id, tenant_id, workspace_id, user_id,
                    capability_id, request_id, run_id, trace_id, status, decision,
                    decision_actor, decision_note, requested_at, resolved_at, executed_at,
                    retry_count, last_error, request_payload, result_payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', NULL, NULL, NULL, ?, NULL, NULL, 0, NULL, ?, '{}')
                """,
                (
                    resolved_approval_id,
                    str(gateway_id or "").strip(),
                    str(device_id or "").strip(),
                    str(tenant_id or "").strip(),
                    str(workspace_id or "").strip(),
                    str(user_id or "").strip(),
                    str(capability_id or "").strip(),
                    str(request_id or "").strip() or None,
                    str(run_id or "").strip(),
                    str(trace_id or "").strip() or None,
                    now_iso,
                    _json_dumps(request_payload or {}),
                ),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM gateway_action_approvals WHERE approval_id = ?",
                (resolved_approval_id,),
            ).fetchone()
        finally:
            conn.close()
    return _approval_from_row(row) or {}


def get_gateway_action_approval(
    approval_id: str,
    *,
    db_path: Optional[Path | str] = None,
) -> Optional[Dict[str, Any]]:
    token = str(approval_id or "").strip()
    if not token:
        return None
    with _DB_LOCK:
        conn = _connect(db_path)
        try:
            row = conn.execute(
                "SELECT * FROM gateway_action_approvals WHERE approval_id = ?",
                (token,),
            ).fetchone()
        finally:
            conn.close()
    return _approval_from_row(row)


def list_gateway_action_approvals(
    *,
    gateway_id: str,
    status: Optional[str] = None,
    limit: int = 50,
    db_path: Optional[Path | str] = None,
) -> List[Dict[str, Any]]:
    gateway_token = str(gateway_id or "").strip()
    if not gateway_token:
        return []
    where = ["gateway_id = ?"]
    params: List[Any] = [gateway_token]
    if str(status or "").strip():
        where.append("status = ?")
        params.append(str(status or "").strip())
    params.append(max(int(limit or 0), 1))
    with _DB_LOCK:
        conn = _connect(db_path)
        try:
            rows = conn.execute(
                f"""
                SELECT * FROM gateway_action_approvals
                WHERE {' AND '.join(where)}
                ORDER BY requested_at DESC, approval_id DESC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        finally:
            conn.close()
    return [item for item in (_approval_from_row(row) for row in rows) if item]


def resolve_gateway_action_approval_atomic(
    *,
    approval_id: str,
    gateway_id: str,
    decision: str,
    actor: str,
    note: Optional[str] = None,
    db_path: Optional[Path | str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Atomically resolve a pending approval using an UPDATE with a WHERE clause
    that checks status = 'pending'. Returns the updated approval dict if the
    update succeeded, or None if the approval was already resolved (i.e. its
    status was no longer 'pending').

    This eliminates the read-check-write race condition: two concurrent callers
    both try the same atomic UPDATE, but only the first to write wins.
    """
    now_iso = _utc_now_iso()
    with _DB_LOCK:
        conn = _connect(db_path)
        try:
            row = conn.execute(
                "SELECT * FROM gateway_action_approvals WHERE approval_id = ? AND gateway_id = ?",
                (str(approval_id or "").strip(), str(gateway_id or "").strip()),
            ).fetchone()
            approval = _approval_from_row(row)
            if approval is None:
                return None
            _enforce_gateway_state_decision(
                "resolve_approval",
                tenant_id=approval.get("tenant_id"),
                workspace_id=approval.get("workspace_id"),
                user_id=approval.get("user_id"),
                device_id=approval.get("device_id"),
                gateway_id=approval.get("gateway_id"),
                actor_role="system",
                approval_id=approval.get("approval_id"),
                approval_status=approval.get("status"),
                decision=str(decision or "").strip(),
            )
            cursor = conn.execute(
                """
                UPDATE gateway_action_approvals
                SET status = ?, decision = ?, decision_actor = ?, decision_note = ?,
                    resolved_at = ?
                WHERE approval_id = ? AND gateway_id = ? AND status = 'pending'
                """,
                (
                    str(decision or "").strip(),
                    str(decision or "").strip(),
                    str(actor or "").strip(),
                    str(note or "").strip() or None,
                    now_iso,
                    str(approval_id or "").strip(),
                    str(gateway_id or "").strip(),
                ),
            )
            if cursor.rowcount == 0:
                return None  # Already resolved or does not exist
            conn.commit()
            row = conn.execute(
                "SELECT * FROM gateway_action_approvals WHERE approval_id = ?",
                (str(approval_id or "").strip(),),
            ).fetchone()
        finally:
            conn.close()
    return _approval_from_row(row)


def update_gateway_action_approval_decision(
    *,
    approval_id: str,
    gateway_id: str,
    status: str,
    decision: str,
    actor: str,
    note: Optional[str] = None,
    db_path: Optional[Path | str] = None,
) -> Optional[Dict[str, Any]]:
    now_iso = _utc_now_iso()
    with _DB_LOCK:
        conn = _connect(db_path)
        try:
            row = conn.execute(
                "SELECT * FROM gateway_action_approvals WHERE approval_id = ? AND gateway_id = ?",
                (str(approval_id or "").strip(), str(gateway_id or "").strip()),
            ).fetchone()
            approval = _approval_from_row(row)
            if approval is None:
                return None
            _enforce_gateway_state_decision(
                "resolve_approval",
                tenant_id=approval.get("tenant_id"),
                workspace_id=approval.get("workspace_id"),
                user_id=approval.get("user_id"),
                device_id=approval.get("device_id"),
                gateway_id=approval.get("gateway_id"),
                actor_role="system",
                approval_id=approval.get("approval_id"),
                approval_status=approval.get("status"),
                status=str(status or "").strip(),
                decision=str(decision or "").strip(),
            )
            conn.execute(
                """
                UPDATE gateway_action_approvals
                SET status = ?, decision = ?, decision_actor = ?, decision_note = ?, resolved_at = ?
                WHERE approval_id = ? AND gateway_id = ?
                """,
                (
                    str(status or "").strip(),
                    str(decision or "").strip(),
                    str(actor or "").strip(),
                    str(note or "").strip() or None,
                    now_iso,
                    str(approval_id or "").strip(),
                    str(gateway_id or "").strip(),
                ),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM gateway_action_approvals WHERE approval_id = ?",
                (str(approval_id or "").strip(),),
            ).fetchone()
        finally:
            conn.close()
    return _approval_from_row(row)


def mark_gateway_action_approval_execution_failed(
    *,
    approval_id: str,
    gateway_id: str,
    error_message: str,
    db_path: Optional[Path | str] = None,
) -> Optional[Dict[str, Any]]:
    with _DB_LOCK:
        conn = _connect(db_path)
        try:
            row = conn.execute(
                "SELECT * FROM gateway_action_approvals WHERE approval_id = ? AND gateway_id = ?",
                (str(approval_id or "").strip(), str(gateway_id or "").strip()),
            ).fetchone()
            approval = _approval_from_row(row)
            if approval is None:
                return None
            conn.execute(
                """
                UPDATE gateway_action_approvals
                SET status = 'approved', retry_count = ?, last_error = ?
                WHERE approval_id = ? AND gateway_id = ?
                """,
                (
                    int(approval.get("retry_count") or 0) + 1,
                    str(error_message or "").strip() or None,
                    str(approval_id or "").strip(),
                    str(gateway_id or "").strip(),
                ),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM gateway_action_approvals WHERE approval_id = ?",
                (str(approval_id or "").strip(),),
            ).fetchone()
        finally:
            conn.close()
    return _approval_from_row(row)


def mark_gateway_action_approval_executed(
    *,
    approval_id: str,
    gateway_id: str,
    result_payload: Optional[Dict[str, Any]] = None,
    db_path: Optional[Path | str] = None,
) -> Optional[Dict[str, Any]]:
    now_iso = _utc_now_iso()
    with _DB_LOCK:
        conn = _connect(db_path)
        try:
            conn.execute(
                """
                UPDATE gateway_action_approvals
                SET status = 'executed', executed_at = ?, last_error = NULL, result_payload = ?
                WHERE approval_id = ? AND gateway_id = ?
                """,
                (
                    now_iso,
                    _json_dumps(result_payload or {}),
                    str(approval_id or "").strip(),
                    str(gateway_id or "").strip(),
                ),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM gateway_action_approvals WHERE approval_id = ?",
                (str(approval_id or "").strip(),),
            ).fetchone()
        finally:
            conn.close()
    return _approval_from_row(row)


def get_gateway_browser_session(
    browser_session_id: str,
    *,
    db_path: Optional[Path | str] = None,
) -> Optional[Dict[str, Any]]:
    token = str(browser_session_id or "").strip()
    if not token:
        return None
    with _DB_LOCK:
        conn = _connect(db_path)
        try:
            row = conn.execute(
                "SELECT * FROM gateway_browser_sessions WHERE browser_session_id = ?",
                (token,),
            ).fetchone()
        finally:
            conn.close()
    return _browser_session_from_row(row)


def list_gateway_browser_sessions(
    gateway_id: str,
    *,
    status: Optional[str] = None,
    limit: int = 50,
    db_path: Optional[Path | str] = None,
) -> List[Dict[str, Any]]:
    gateway_token = str(gateway_id or "").strip()
    if not gateway_token:
        return []
    where = ["gateway_id = ?"]
    params: List[Any] = [gateway_token]
    normalized_status = str(status or "").strip()
    if normalized_status:
        where.append("status = ?")
        params.append(normalized_status)
    params.append(max(int(limit or 0), 1))
    with _DB_LOCK:
        conn = _connect(db_path)
        try:
            rows = conn.execute(
                f"""
                SELECT * FROM gateway_browser_sessions
                WHERE {' AND '.join(where)}
                ORDER BY updated_at DESC, browser_session_id DESC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        finally:
            conn.close()
    return [item for item in (_browser_session_from_row(row) for row in rows) if item]


def upsert_gateway_browser_session(
    *,
    gateway_id: str,
    device_id: str,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    run_id: str,
    browser_session_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    status: Optional[str] = None,
    execution_target: Optional[str] = None,
    session_profile: Optional[str] = None,
    current_url: Optional[str] = None,
    manual_takeover: Optional[bool] = None,
    resume_supported: Optional[bool] = None,
    reviewed_approval_required: Optional[bool] = None,
    reviewed_approved: Optional[bool] = None,
    immutable_plan_hash: Optional[str] = None,
    execution_binding: Optional[Dict[str, Any]] = None,
    checkpoint_payload: Optional[Dict[str, Any]] = None,
    snapshot_payload: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    fallback_ready_at: Optional[str] = None,
    interrupted_at: Optional[str] = None,
    db_path: Optional[Path | str] = None,
) -> Dict[str, Any]:
    resolved_browser_session_id = str(browser_session_id or f"gbsess_{uuid.uuid4().hex}").strip()
    now_iso = _utc_now_iso()
    _enforce_gateway_state_decision(
        "update_browser_session",
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
        device_id=device_id,
        gateway_id=gateway_id,
        actor_role="system",
        browser_session_id=resolved_browser_session_id,
        run_id=str(run_id or "").strip(),
        status=str(status or "active").strip() or "active",
        manual_takeover=bool(manual_takeover),
        takeover_confirmed=not bool(manual_takeover) or bool(reviewed_approved),
        payload={
            "checkpoint": checkpoint_payload or {},
            "snapshot": snapshot_payload or {},
            "metadata": metadata or {},
        },
    )
    with _DB_LOCK:
        conn = _connect(db_path)
        try:
            existing_row = conn.execute(
                "SELECT * FROM gateway_browser_sessions WHERE browser_session_id = ?",
                (resolved_browser_session_id,),
            ).fetchone()
            existing = _browser_session_from_row(existing_row)
            merged_metadata = dict(existing.get("metadata") or {}) if isinstance(existing, dict) else {}
            merged_metadata.update(dict(metadata or {}))
            merged_checkpoint = (
                dict(existing.get("checkpoint") or {}) if isinstance(existing, dict) else {}
            )
            merged_checkpoint.update(dict(checkpoint_payload or {}))
            merged_snapshot = (
                dict(existing.get("snapshot") or {}) if isinstance(existing, dict) else {}
            )
            merged_snapshot.update(dict(snapshot_payload or {}))
            resolved_session_profile = (
                str(session_profile or (existing or {}).get("session_profile") or "").strip() or None
            )
            resolved_current_url = str(current_url or (existing or {}).get("current_url") or "").strip() or None
            resolved_status = str(status or (existing or {}).get("status") or "active").strip() or "active"
            resolved_execution_target = (
                str(execution_target or (existing or {}).get("execution_target") or "local_gateway").strip()
                or "local_gateway"
            )
            resolved_plan_hash = (
                str(immutable_plan_hash or (existing or {}).get("immutable_plan_hash") or "").strip() or None
            )
            resolved_execution_binding = (
                dict(execution_binding or (existing or {}).get("execution_binding") or {})
            )
            resolved_manual_takeover = (
                bool(manual_takeover)
                if manual_takeover is not None
                else bool((existing or {}).get("manual_takeover"))
            )
            resolved_resume_supported = (
                bool(resume_supported)
                if resume_supported is not None
                else bool((existing or {}).get("resume_supported"))
            )
            resolved_reviewed_required = (
                bool(reviewed_approval_required)
                if reviewed_approval_required is not None
                else bool((existing or {}).get("reviewed_approval_required"))
            )
            resolved_reviewed_approved = (
                bool(reviewed_approved)
                if reviewed_approved is not None
                else bool((existing or {}).get("reviewed_approved"))
            )
            resolved_fallback_ready_at = (
                str(fallback_ready_at or (existing or {}).get("fallback_ready_at") or "").strip() or None
            )
            resolved_interrupted_at = (
                str(interrupted_at or (existing or {}).get("interrupted_at") or "").strip() or None
            )
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO gateway_browser_sessions (
                        browser_session_id, gateway_id, device_id, tenant_id, workspace_id, user_id,
                        run_id, trace_id, status, execution_target, session_profile, current_url,
                        manual_takeover, resume_supported, reviewed_approval_required, reviewed_approved,
                        immutable_plan_hash, execution_binding, checkpoint_payload, snapshot_payload,
                        metadata, created_at, updated_at, last_activity_at, fallback_ready_at, interrupted_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        resolved_browser_session_id,
                        str(gateway_id or "").strip(),
                        str(device_id or "").strip(),
                        str(tenant_id or "").strip(),
                        str(workspace_id or "").strip(),
                        str(user_id or "").strip(),
                        str(run_id or "").strip(),
                        str(trace_id or "").strip() or None,
                        resolved_status,
                        resolved_execution_target,
                        resolved_session_profile,
                        resolved_current_url,
                        1 if resolved_manual_takeover else 0,
                        1 if resolved_resume_supported else 0,
                        1 if resolved_reviewed_required else 0,
                        1 if resolved_reviewed_approved else 0,
                        resolved_plan_hash,
                        _json_dumps(resolved_execution_binding),
                        _json_dumps(merged_checkpoint),
                        _json_dumps(merged_snapshot),
                        _json_dumps(merged_metadata),
                        now_iso,
                        now_iso,
                        now_iso,
                        resolved_fallback_ready_at,
                        resolved_interrupted_at,
                    ),
                )
            else:
                conn.execute(
                    """
                    UPDATE gateway_browser_sessions
                    SET gateway_id = ?, device_id = ?, tenant_id = ?, workspace_id = ?, user_id = ?,
                        run_id = ?, trace_id = ?, status = ?, execution_target = ?, session_profile = ?,
                        current_url = ?, manual_takeover = ?, resume_supported = ?,
                        reviewed_approval_required = ?, reviewed_approved = ?, immutable_plan_hash = ?,
                        execution_binding = ?, checkpoint_payload = ?, snapshot_payload = ?, metadata = ?,
                        updated_at = ?, last_activity_at = ?, fallback_ready_at = ?, interrupted_at = ?
                    WHERE browser_session_id = ?
                    """,
                    (
                        str(gateway_id or "").strip(),
                        str(device_id or "").strip(),
                        str(tenant_id or "").strip(),
                        str(workspace_id or "").strip(),
                        str(user_id or "").strip(),
                        str(run_id or "").strip(),
                        str(trace_id or (existing or {}).get("trace_id") or "").strip() or None,
                        resolved_status,
                        resolved_execution_target,
                        resolved_session_profile,
                        resolved_current_url,
                        1 if resolved_manual_takeover else 0,
                        1 if resolved_resume_supported else 0,
                        1 if resolved_reviewed_required else 0,
                        1 if resolved_reviewed_approved else 0,
                        resolved_plan_hash,
                        _json_dumps(resolved_execution_binding),
                        _json_dumps(merged_checkpoint),
                        _json_dumps(merged_snapshot),
                        _json_dumps(merged_metadata),
                        now_iso,
                        now_iso,
                        resolved_fallback_ready_at,
                        resolved_interrupted_at,
                        resolved_browser_session_id,
                    ),
                )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM gateway_browser_sessions WHERE browser_session_id = ?",
                (resolved_browser_session_id,),
            ).fetchone()
        finally:
            conn.close()
    return _browser_session_from_row(row) or {}


def summarize_gateway_outbox(gateway_id: str, *, db_path: Optional[Path | str] = None) -> Dict[str, Any]:
    """Extract outbox summary from the latest gateway session heartbeat metadata."""
    latest = get_latest_gateway_session(gateway_id)
    if not isinstance(latest, dict):
        return {"pending": 0, "failed": 0, "acknowledged": 0, "uncertain": 0, "abandoned": 0}
    metadata = latest.get("metadata") if isinstance(latest.get("metadata"), dict) else {}
    queue_summary = metadata.get("queue_depth_summary")
    if isinstance(queue_summary, dict):
        return {
            "pending": int(queue_summary.get("pending") or 0),
            "failed": int(queue_summary.get("failed") or 0),
            "acknowledged": int(queue_summary.get("acknowledged") or 0),
            "uncertain": int(queue_summary.get("uncertain") or 0),
            "abandoned": int(queue_summary.get("abandoned") or 0),
        }
    return {"pending": 0, "failed": 0, "acknowledged": 0, "uncertain": 0, "abandoned": 0}


def sweep_stale_gateway_sessions(
    gateway_id: str,
    *,
    stale_threshold_seconds: int = 900,
    db_path: Optional[Path | str] = None,
) -> int:
    """Mark gateway sessions that have been disconnected/stale for too long as expired."""
    cutoff = (_utc_now() - timedelta(seconds=stale_threshold_seconds)).isoformat()
    gateway_token = str(gateway_id or "").strip()
    if not gateway_token:
        return 0
    swept = 0
    with _DB_LOCK:
        conn = _connect(db_path)
        try:
            rows = conn.execute(
                """SELECT session_id, status, updated_at, tenant_id, workspace_id, user_id, device_id
                   FROM gateway_sessions
                   WHERE gateway_id = ? AND status IN ('connected', 'disconnected')""",
                (gateway_token,),
            ).fetchall()
            stale_session_ids: list[str] = []
            for row in rows:
                updated_at = str(row[2] or "")
                if updated_at < cutoff:
                    stale_session_ids.append(str(row[0] or "").strip())
            scope_row = rows[0] if rows else None
            decision = _enforce_gateway_state_decision(
                "sweep_stale_sessions",
                tenant_id=scope_row[3] if scope_row is not None else None,
                workspace_id=scope_row[4] if scope_row is not None else None,
                user_id=scope_row[5] if scope_row is not None else None,
                device_id=scope_row[6] if scope_row is not None else None,
                gateway_id=gateway_token,
                actor_role="system",
                stale_count=len(stale_session_ids),
                candidate_count=len(rows),
            )
            if str(decision.get("next_action") or "").strip() == "noop":
                return 0
            swept_at = _utc_now_iso()
            for session_id in stale_session_ids:
                conn.execute(
                    "UPDATE gateway_sessions SET status = 'expired', metadata = json_set(coalesce(metadata, '{}'), '$.swept_at', ?) WHERE session_id = ?",
                    (swept_at, session_id),
                )
                swept += 1
            if swept > 0:
                conn.commit()
        finally:
            conn.close()
    return swept

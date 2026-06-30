from __future__ import annotations

import json
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional
import sqlite3

from server_modules import rust_runtime_kernel_client

from server_modules import rust_runtime_kernel_client

# SQLite is retained only for explicit local checkpoint and offline cache
# concerns. It is not the authoritative server-side source of truth for live
# runs, approvals, outbox delivery, local claim ownership, or runtime sessions.
# Those classes are durably sourced from Postgres; SQLite remains a local-only
# checkpoint/mirror layer for queue recovery, chat stream state, channel events,
# and worker-side recovery flows.

SQLITE_CHECKPOINT_ONLY_STATE_CLASSES = frozenset(
    {
        "local_pending_queue",
        "local_claims",
        "live_runs",
    "runtime_registrations",
    "runtime_sessions",
    "runtime_session_turns",
    "live_runs",
    "channel_events",
        "chat_stream_state",
        "notifications",
        "notification_devices",
        "notification_delivery_statuses",
    }
)

RUNTIME_CHECKPOINT_SQLITE_TABLES = (
    "live_runs",
    "local_pending_queue",
    "local_claims",
    "runtime_registrations",
    "run_history",
    "channel_events",
    "chat_stream_state",
    "runtime_sessions",
    "runtime_session_turns",
    "notifications",
    "notification_reads",
    "notification_devices",
    "notification_deliveries",
    "local_app_state",
)


def _parse_ts(raw: Any) -> float:
    if isinstance(raw, (int, float)):
        return float(raw)
    if not isinstance(raw, str):
        return time.time()
    value = raw.strip()
    if not value:
        return time.time()
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value).astimezone(timezone.utc).timestamp()
    except Exception:
        return time.time()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@contextmanager
def _connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.execute("PRAGMA foreign_keys=ON;")
    try:
        yield conn
    finally:
        conn.close()


def init_runtime_state_db(db_path: Path) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS live_runs (
                run_id TEXT PRIMARY KEY,
                status TEXT,
                engine TEXT,
                workspace_id TEXT,
                execution_target TEXT,
                created_at TEXT,
                updated_at TEXT,
                sort_ts REAL NOT NULL,
                run_json TEXT NOT NULL,
                persisted_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS local_pending_queue (
                run_id TEXT PRIMARY KEY,
                queue_order INTEGER NOT NULL,
                queued_at TEXT,
                persisted_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS local_claims (
                run_id TEXT PRIMARY KEY,
                worker_id TEXT,
                claimed_at TEXT,
                last_heartbeat_at TEXT,
                lease_seconds INTEGER,
                claim_json TEXT NOT NULL,
                persisted_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS runtime_registrations (
                runtime_id TEXT PRIMARY KEY,
                runtime_type TEXT,
                status TEXT,
                current_run_id TEXT,
                last_seen_at TEXT,
                runtime_json TEXT NOT NULL,
                persisted_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS run_history (
                run_id TEXT PRIMARY KEY,
                status TEXT,
                workspace_id TEXT,
                pack_id TEXT,
                created_at TEXT,
                updated_at TEXT,
                completed_at TEXT,
                sort_ts REAL NOT NULL,
                snapshot_json TEXT NOT NULL,
                persisted_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS channel_events (
                id TEXT PRIMARY KEY,
                ts TEXT,
                workspace_id TEXT,
                channel TEXT,
                direction TEXT,
                event_type TEXT,
                session_key TEXT,
                run_id TEXT,
                trace_id TEXT,
                action TEXT,
                sort_ts REAL NOT NULL,
                event_json TEXT NOT NULL,
                persisted_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_stream_state (
                session_id TEXT PRIMARY KEY,
                thread_id TEXT,
                request_id TEXT,
                workspace_id TEXT,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_event_seq INTEGER NOT NULL DEFAULT 0,
                partial_text TEXT NOT NULL DEFAULT '',
                final_payload_json TEXT,
                error_text TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS runtime_sessions (
                session_id TEXT PRIMARY KEY,
                actor_key TEXT NOT NULL,
                workspace_id TEXT,
                user_id TEXT,
                status TEXT NOT NULL,
                runtime_options_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_touched_at TEXT NOT NULL,
                last_error TEXT,
                meta_json TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS runtime_session_turns (
                turn_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                request_id TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT,
                input_json TEXT NOT NULL,
                final_payload_json TEXT,
                error_text TEXT,
                metrics_json TEXT NOT NULL,
                FOREIGN KEY(session_id) REFERENCES runtime_sessions(session_id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS notifications (
                id TEXT PRIMARY KEY,
                tenant_id TEXT,
                workspace_id TEXT,
                session_key TEXT,
                session_id TEXT,
                channel TEXT,
                direction TEXT,
                event_type TEXT,
                run_id TEXT,
                machine_id TEXT,
                trace_id TEXT,
                action TEXT,
                title TEXT,
                text TEXT,
                sort_ts REAL NOT NULL,
                notification_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                persisted_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS notification_reads (
                notification_id TEXT NOT NULL,
                reader_key TEXT NOT NULL,
                tenant_id TEXT,
                workspace_id TEXT,
                read_at TEXT NOT NULL,
                persisted_at TEXT NOT NULL,
                PRIMARY KEY (notification_id, reader_key),
                FOREIGN KEY(notification_id) REFERENCES notifications(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS notification_devices (
                device_id TEXT PRIMARY KEY,
                tenant_id TEXT,
                workspace_id TEXT,
                reader_key TEXT,
                provider TEXT NOT NULL,
                push_token TEXT NOT NULL,
                platform TEXT,
                app_id TEXT,
                device_name TEXT,
                status TEXT NOT NULL,
                capabilities_json TEXT NOT NULL,
                device_json TEXT NOT NULL,
                last_registered_at TEXT NOT NULL,
                last_delivered_at TEXT,
                last_error TEXT,
                persisted_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS notification_deliveries (
                notification_id TEXT NOT NULL,
                device_id TEXT NOT NULL,
                status TEXT NOT NULL,
                delivered_at TEXT,
                last_error TEXT,
                persisted_at TEXT NOT NULL,
                PRIMARY KEY (notification_id, device_id),
                FOREIGN KEY(notification_id) REFERENCES notifications(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS local_app_state (
                state_key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                persisted_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_live_runs_sort_ts ON live_runs(sort_ts DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_live_runs_workspace ON live_runs(workspace_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_local_pending_queue_order ON local_pending_queue(queue_order ASC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_local_claims_worker ON local_claims(worker_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_runtime_registrations_status ON runtime_registrations(status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_run_history_sort_ts ON run_history(sort_ts DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_runtime_registrations_seen ON runtime_registrations(last_seen_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_run_history_workspace ON run_history(workspace_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_channel_events_sort_ts ON channel_events(sort_ts DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_channel_events_workspace ON channel_events(workspace_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_channel_events_session ON channel_events(session_key)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_channel_events_run ON channel_events(run_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_stream_state_updated ON chat_stream_state(updated_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_stream_state_status ON chat_stream_state(status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_runtime_sessions_actor_key ON runtime_sessions(actor_key)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_runtime_sessions_status ON runtime_sessions(status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_runtime_sessions_touched ON runtime_sessions(last_touched_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_runtime_session_turns_session ON runtime_session_turns(session_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_runtime_session_turns_status ON runtime_session_turns(status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_runtime_session_turns_updated ON runtime_session_turns(updated_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_notifications_sort_ts ON notifications(sort_ts DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_notifications_workspace ON notifications(workspace_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_notifications_run ON notifications(run_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_notification_reads_workspace ON notification_reads(workspace_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_notification_devices_workspace ON notification_devices(workspace_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_notification_devices_status ON notification_devices(status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_notification_deliveries_status ON notification_deliveries(status)"
        )
        conn.commit()


def _json_blob(value: Any, *, fallback: str = "{}") -> str:
    if isinstance(value, str):
        token = value.strip()
        return token or fallback
    try:
        return json.dumps(value if value is not None else json.loads(fallback), ensure_ascii=False, separators=(",", ":"))
    except Exception:
        return fallback


def _parse_json_blob(raw: Any, fallback: Any) -> Any:
    if not isinstance(raw, str) or not raw.strip():
        return fallback
    try:
        parsed = json.loads(raw)
    except Exception:
        return fallback
    return parsed if parsed is not None else fallback


def _normalize_chat_stream_state_row(row: sqlite3.Row) -> Dict[str, Any]:
    final_payload = _parse_json_blob(row["final_payload_json"], None)
    if not isinstance(final_payload, dict):
        final_payload = None
    metadata = _parse_json_blob(row["metadata_json"], {})
    if not isinstance(metadata, dict):
        metadata = {}
    return {
        "session_id": str(row["session_id"] or "").strip(),
        "thread_id": str(row["thread_id"] or "").strip(),
        "request_id": str(row["request_id"] or "").strip(),
        "workspace_id": str(row["workspace_id"] or "").strip(),
        "status": str(row["status"] or "").strip(),
        "created_at": str(row["created_at"] or "").strip(),
        "updated_at": str(row["updated_at"] or "").strip(),
        "last_event_seq": int(row["last_event_seq"] or 0),
        "partial_text": str(row["partial_text"] or ""),
        "final_payload": final_payload,
        "error_text": str(row["error_text"] or "").strip(),
        "metadata": metadata,
    }


def _normalize_runtime_session_row(row: sqlite3.Row) -> Dict[str, Any]:
    runtime_options = _parse_json_blob(row["runtime_options_json"], {})
    if not isinstance(runtime_options, dict):
        runtime_options = {}
    meta = _parse_json_blob(row["meta_json"], {})
    if not isinstance(meta, dict):
        meta = {}
    return {
        "session_id": str(row["session_id"] or "").strip(),
        "actor_key": str(row["actor_key"] or "").strip(),
        "workspace_id": str(row["workspace_id"] or "").strip(),
        "user_id": str(row["user_id"] or "").strip(),
        "status": str(row["status"] or "").strip(),
        "runtime_options": runtime_options,
        "created_at": str(row["created_at"] or "").strip(),
        "updated_at": str(row["updated_at"] or "").strip(),
        "last_touched_at": str(row["last_touched_at"] or "").strip(),
        "last_error": str(row["last_error"] or "").strip(),
        "meta": meta,
    }


def _normalize_runtime_session_turn_row(row: sqlite3.Row) -> Dict[str, Any]:
    input_payload = _parse_json_blob(row["input_json"], {})
    if not isinstance(input_payload, dict):
        input_payload = {}
    final_payload = _parse_json_blob(row["final_payload_json"], None)
    if not isinstance(final_payload, dict):
        final_payload = None
    metrics = _parse_json_blob(row["metrics_json"], {})
    if not isinstance(metrics, dict):
        metrics = {}
    return {
        "turn_id": str(row["turn_id"] or "").strip(),
        "session_id": str(row["session_id"] or "").strip(),
        "request_id": str(row["request_id"] or "").strip(),
        "status": str(row["status"] or "").strip(),
        "started_at": str(row["started_at"] or "").strip(),
        "updated_at": str(row["updated_at"] or "").strip(),
        "completed_at": str(row["completed_at"] or "").strip(),
        "input": input_payload,
        "final_payload": final_payload,
        "error_text": str(row["error_text"] or "").strip(),
        "metrics": metrics,
    }


def _runtime_state_payload_bytes(item: Optional[Dict[str, Any]]) -> int:
    try:
        return len(json.dumps(item or {}, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8"))
    except Exception:
        return 0


_RUNTIME_STATE_STORE_NEXT_ACTIONS = {
    "upsert_runtime_session": {"write_runtime_session"},
    "delete_runtime_session": {"delete_runtime_session"},
    "upsert_runtime_session_turn": {"write_runtime_session_turn"},
    "delete_runtime_session_turn": {"delete_runtime_session_turn"},
    "upsert_chat_stream_state": {"write_chat_stream_state"},
    "upsert_live_run": {"write_live_run_state"},
    "delete_live_run": {"delete_live_run_state"},
    "append_channel_event": {"append_channel_event"},
    "checkpoint_snapshot": {"write_checkpoint_snapshot"},
    "upsert_notification": {"write_notification"},
    "mark_notification_read": {"mark_notification_read"},
    "register_notification_device": {"write_notification_device"},
    "update_notification_delivery": {"write_notification_delivery"},
    "prune_records": {"prune_state_records"},
}


def _enforce_runtime_state_store_decision(
    *,
    operation: str,
    state_class: str,
    item: Optional[Dict[str, Any]] = None,
    run_id: Optional[str] = None,
    session_id: Optional[str] = None,
    turn_id: Optional[str] = None,
    runtime_id: Optional[str] = None,
    notification_id: Optional[str] = None,
    device_id: Optional[str] = None,
    actor_id: Optional[str] = None,
    status: Optional[str] = None,
    retention_days: Optional[int] = None,
    records_requested: Optional[int] = None,
) -> Dict[str, Any]:
    request = {
        "operation": operation,
        "state_class": state_class,
        "storage_engine": "local_sqlite",
        "workspace_id": str((item or {}).get("workspace_id") or "default").strip() or "default",
        "tenant_id": str((item or {}).get("tenant_id") or "default").strip() or "default",
        "run_id": str(run_id or (item or {}).get("run_id") or "").strip(),
        "session_id": str(session_id or (item or {}).get("session_id") or "").strip(),
        "turn_id": str(turn_id or (item or {}).get("turn_id") or "").strip(),
        "runtime_id": str(runtime_id or (item or {}).get("runtime_id") or (item or {}).get("worker_id") or "").strip(),
        "notification_id": str(notification_id or (item or {}).get("id") or "").strip(),
        "device_id": str(device_id or (item or {}).get("device_id") or "").strip(),
        "actor_id": str(actor_id or (item or {}).get("reader_key") or (item or {}).get("user_id") or "").strip(),
        "trace_id": str((item or {}).get("trace_id") or "").strip(),
        "status": str(status or (item or {}).get("status") or (item or {}).get("state") or "unknown").strip() or "unknown",
        "payload": bool(item is not None),
        "payload_bytes": _runtime_state_payload_bytes(item),
        "durable_runtime_required": False,
        "durable_pool_available": True,
        "local_checkpoint_allowed": True,
        "workspace_access": True,
        "owner_access": True,
    }
    if retention_days is not None:
        request["retention_days"] = max(1, int(retention_days))
    if records_requested is not None:
        request["records_requested"] = max(0, int(records_requested))
    decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
        "runtime-state-store-decision",
        request,
    )
    expected_actions = _RUNTIME_STATE_STORE_NEXT_ACTIONS.get(operation)
    next_action = str(decision.get("next_action") or "").strip()
    if expected_actions is None:
        raise RuntimeError(f"unexpected runtime_state_store operation: {operation}")
    if next_action not in expected_actions:
        expected_list = ", ".join(sorted(expected_actions))
        raise RuntimeError(
            f"unexpected next_action for runtime-state-store-decision: {next_action or '<missing>'} "
            f"(expected {expected_list})"
        )
    return decision


def upsert_runtime_session(db_path: Path, item: Dict[str, Any]) -> None:
    session_id = str(item.get("session_id") or "").strip()
    if not session_id:
        return
    created_at = str(item.get("created_at") or "").strip() or _utc_now_iso()
    updated_at = str(item.get("updated_at") or "").strip() or created_at
    last_touched_at = str(item.get("last_touched_at") or "").strip() or updated_at
    _enforce_runtime_state_store_decision(
        operation="upsert_runtime_session",
        state_class="runtime_sessions",
        item=item,
        session_id=session_id,
        status=str(item.get("status") or "active"),
    )
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO runtime_sessions (
                session_id, actor_key, workspace_id, user_id, status,
                runtime_options_json, created_at, updated_at, last_touched_at,
                last_error, meta_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                actor_key = excluded.actor_key,
                workspace_id = excluded.workspace_id,
                user_id = excluded.user_id,
                status = excluded.status,
                runtime_options_json = excluded.runtime_options_json,
                created_at = excluded.created_at,
                updated_at = excluded.updated_at,
                last_touched_at = excluded.last_touched_at,
                last_error = excluded.last_error,
                meta_json = excluded.meta_json
            """,
            (
                session_id,
                str(item.get("actor_key") or "").strip() or session_id,
                str(item.get("workspace_id") or "").strip(),
                str(item.get("user_id") or "").strip(),
                str(item.get("status") or "ready").strip() or "ready",
                _json_blob(item.get("runtime_options") if isinstance(item.get("runtime_options"), dict) else {}, fallback="{}"),
                created_at,
                updated_at,
                last_touched_at,
                str(item.get("last_error") or "").strip() or None,
                _json_blob(item.get("meta") if isinstance(item.get("meta"), dict) else {}, fallback="{}"),
            ),
        )
        conn.commit()


def get_runtime_session(db_path: Path, session_id: str) -> Optional[Dict[str, Any]]:
    token = str(session_id or "").strip()
    if not token:
        return None
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT session_id, actor_key, workspace_id, user_id, status,
                   runtime_options_json, created_at, updated_at, last_touched_at,
                   last_error, meta_json
            FROM runtime_sessions
            WHERE session_id = ?
            """,
            (token,),
        ).fetchone()
    if row is None:
        return None
    return _normalize_runtime_session_row(row)


def list_runtime_sessions(
    db_path: Path,
    *,
    statuses: Optional[List[str]] = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    params: List[Any] = []
    query = """
        SELECT session_id, actor_key, workspace_id, user_id, status,
               runtime_options_json, created_at, updated_at, last_touched_at,
               last_error, meta_json
        FROM runtime_sessions
    """
    normalized_statuses = [str(item).strip() for item in (statuses or []) if str(item).strip()]
    if normalized_statuses:
        placeholders = ", ".join("?" for _ in normalized_statuses)
        query += f"\nWHERE status IN ({placeholders})"
        params.extend(normalized_statuses)
    query += "\nORDER BY last_touched_at DESC"
    if isinstance(limit, int) and limit > 0:
        query += "\nLIMIT ?"
        params.append(limit)
    with _connect(db_path) as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
    return [_normalize_runtime_session_row(row) for row in rows]


def delete_runtime_session(db_path: Path, session_id: str) -> None:
    token = str(session_id or "").strip()
    if not token:
        return
    _enforce_runtime_state_store_decision(
        operation="delete_runtime_session",
        state_class="runtime_sessions",
        session_id=token,
        status="closed",
    )
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM runtime_sessions WHERE session_id = ?", (token,))
        conn.commit()


def upsert_runtime_session_turn(db_path: Path, item: Dict[str, Any]) -> None:
    turn_id = str(item.get("turn_id") or "").strip()
    session_id = str(item.get("session_id") or "").strip()
    request_id = str(item.get("request_id") or "").strip()
    if not turn_id or not session_id or not request_id:
        return
    started_at = str(item.get("started_at") or "").strip() or _utc_now_iso()
    updated_at = str(item.get("updated_at") or "").strip() or started_at
    completed_at = str(item.get("completed_at") or "").strip() or None
    final_payload = item.get("final_payload")
    final_payload_json = (
        _json_blob(final_payload, fallback="{}")
        if isinstance(final_payload, dict)
        else (str(item.get("final_payload_json") or "").strip() or None)
    )
    _enforce_runtime_state_store_decision(
        operation="upsert_runtime_session_turn",
        state_class="runtime_session_turns",
        item=item,
        session_id=session_id,
        turn_id=turn_id,
        status=str(item.get("status") or "running"),
    )
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO runtime_session_turns (
                turn_id, session_id, request_id, status, started_at,
                updated_at, completed_at, input_json, final_payload_json,
                error_text, metrics_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(turn_id) DO UPDATE SET
                session_id = excluded.session_id,
                request_id = excluded.request_id,
                status = excluded.status,
                started_at = excluded.started_at,
                updated_at = excluded.updated_at,
                completed_at = excluded.completed_at,
                input_json = excluded.input_json,
                final_payload_json = excluded.final_payload_json,
                error_text = excluded.error_text,
                metrics_json = excluded.metrics_json
            """,
            (
                turn_id,
                session_id,
                request_id,
                str(item.get("status") or "running").strip() or "running",
                started_at,
                updated_at,
                completed_at,
                _json_blob(item.get("input") if isinstance(item.get("input"), dict) else {}, fallback="{}"),
                final_payload_json,
                str(item.get("error_text") or "").strip() or None,
                _json_blob(item.get("metrics") if isinstance(item.get("metrics"), dict) else {}, fallback="{}"),
            ),
        )
        conn.commit()


def get_runtime_session_turn(db_path: Path, turn_id: str) -> Optional[Dict[str, Any]]:
    token = str(turn_id or "").strip()
    if not token:
        return None
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT turn_id, session_id, request_id, status, started_at,
                   updated_at, completed_at, input_json, final_payload_json,
                   error_text, metrics_json
            FROM runtime_session_turns
            WHERE turn_id = ?
            """,
            (token,),
        ).fetchone()
    if row is None:
        return None
    return _normalize_runtime_session_turn_row(row)


def list_runtime_session_turns(
    db_path: Path,
    *,
    session_id: str = "",
    statuses: Optional[List[str]] = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    params: List[Any] = []
    clauses: List[str] = []
    session_token = str(session_id or "").strip()
    if session_token:
        clauses.append("session_id = ?")
        params.append(session_token)
    normalized_statuses = [str(item).strip() for item in (statuses or []) if str(item).strip()]
    if normalized_statuses:
        placeholders = ", ".join("?" for _ in normalized_statuses)
        clauses.append(f"status IN ({placeholders})")
        params.extend(normalized_statuses)
    query = """
        SELECT turn_id, session_id, request_id, status, started_at,
               updated_at, completed_at, input_json, final_payload_json,
               error_text, metrics_json
        FROM runtime_session_turns
    """
    if clauses:
        query += "\nWHERE " + " AND ".join(clauses)
    query += "\nORDER BY updated_at DESC"
    if isinstance(limit, int) and limit > 0:
        query += "\nLIMIT ?"
        params.append(limit)
    with _connect(db_path) as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
    return [_normalize_runtime_session_turn_row(row) for row in rows]


def delete_runtime_session_turn(db_path: Path, turn_id: str) -> None:
    token = str(turn_id or "").strip()
    if not token:
        return
    _enforce_runtime_state_store_decision(
        operation="delete_runtime_session_turn",
        state_class="runtime_session_turns",
        turn_id=token,
        status="closed",
    )
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM runtime_session_turns WHERE turn_id = ?", (token,))
        conn.commit()


def upsert_chat_stream_state(db_path: Path, item: Dict[str, Any]) -> None:
    session_id = str(item.get("session_id") or "").strip()
    if not session_id:
        return
    created_at = str(item.get("created_at") or "").strip() or _utc_now_iso()
    updated_at = str(item.get("updated_at") or "").strip() or created_at
    final_payload = item.get("final_payload")
    final_payload_json = None
    if isinstance(final_payload, dict):
        final_payload_json = _json_blob(final_payload, fallback="{}")
    else:
        raw_payload = str(item.get("final_payload_json") or "").strip()
        final_payload_json = raw_payload or None
    _enforce_runtime_state_store_decision(
        operation="upsert_chat_stream_state",
        state_class="chat_stream_state",
        item=item,
        session_id=session_id,
        status=str(item.get("status") or "active").strip() or "active",
    )
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO chat_stream_state (
                session_id, thread_id, request_id, workspace_id, status,
                created_at, updated_at, last_event_seq, partial_text,
                final_payload_json, error_text, metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                thread_id = excluded.thread_id,
                request_id = excluded.request_id,
                workspace_id = excluded.workspace_id,
                status = excluded.status,
                created_at = excluded.created_at,
                updated_at = excluded.updated_at,
                last_event_seq = excluded.last_event_seq,
                partial_text = excluded.partial_text,
                final_payload_json = excluded.final_payload_json,
                error_text = excluded.error_text,
                metadata_json = excluded.metadata_json
            """,
            (
                session_id,
                str(item.get("thread_id") or "").strip(),
                str(item.get("request_id") or "").strip(),
                str(item.get("workspace_id") or "").strip(),
                str(item.get("status") or "active").strip() or "active",
                created_at,
                updated_at,
                max(0, int(item.get("last_event_seq") or 0)),
                str(item.get("partial_text") or ""),
                final_payload_json,
                str(item.get("error_text") or "").strip() or None,
                _json_blob(item.get("metadata") if isinstance(item.get("metadata"), dict) else {}, fallback="{}"),
            ),
        )
        conn.commit()


def get_chat_stream_state(db_path: Path, session_id: str) -> Optional[Dict[str, Any]]:
    token = str(session_id or "").strip()
    if not token:
        return None
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT session_id, thread_id, request_id, workspace_id, status,
                   created_at, updated_at, last_event_seq, partial_text,
                   final_payload_json, error_text, metadata_json
            FROM chat_stream_state
            WHERE session_id = ?
            """,
            (token,),
        ).fetchone()
    if row is None:
        return None
    return _normalize_chat_stream_state_row(row)


def list_chat_stream_states(
    db_path: Path,
    *,
    statuses: Optional[List[str]] = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    params: List[Any] = []
    query = """
        SELECT session_id, thread_id, request_id, workspace_id, status,
               created_at, updated_at, last_event_seq, partial_text,
               final_payload_json, error_text, metadata_json
        FROM chat_stream_state
    """
    normalized_statuses = [
        str(item).strip()
        for item in (statuses or [])
        if str(item).strip()
    ]
    if normalized_statuses:
        placeholders = ", ".join("?" for _ in normalized_statuses)
        query += f"\nWHERE status IN ({placeholders})"
        params.extend(normalized_statuses)
    query += "\nORDER BY updated_at DESC"
    if isinstance(limit, int) and limit > 0:
        query += "\nLIMIT ?"
        params.append(limit)
    with _connect(db_path) as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
    return [_normalize_chat_stream_state_row(row) for row in rows]


def delete_expired_chat_stream_states(
    db_path: Path,
    *,
    older_than_seconds: int,
    now_ts: Optional[float] = None,
) -> int:
    ttl_seconds = max(1, int(older_than_seconds or 0))
    cutoff = datetime.fromtimestamp(float(now_ts if now_ts is not None else time.time()) - ttl_seconds, timezone.utc)
    cutoff_iso = cutoff.isoformat().replace("+00:00", "Z")
    with _connect(db_path) as conn:
        cursor = conn.execute(
            "DELETE FROM chat_stream_state WHERE updated_at < ?",
            (cutoff_iso,),
        )
        conn.commit()
        return int(cursor.rowcount or 0)


def upsert_live_run_state(db_path: Path, item: Dict[str, Any]) -> None:
    run_id = str(item.get("run_id") or "").strip()
    if not run_id:
        return
    context = item.get("context") if isinstance(item.get("context"), dict) else {}
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    created_at = str(item.get("created_at") or "").strip()
    updated_at = str(item.get("updated_at") or "").strip()
    sort_ts = _parse_ts(updated_at or created_at)
    _enforce_runtime_state_store_decision(
        operation="upsert_live_run",
        state_class="live_runs",
        item=item,
        run_id=run_id,
        status=str(item.get("status") or item.get("state") or "queued"),
    )
    payload = json.dumps(item, ensure_ascii=False, separators=(",", ":"))
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO live_runs (
                run_id, status, engine, workspace_id, execution_target,
                created_at, updated_at, sort_ts, run_json, persisted_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                status = excluded.status,
                engine = excluded.engine,
                workspace_id = excluded.workspace_id,
                execution_target = excluded.execution_target,
                created_at = excluded.created_at,
                updated_at = excluded.updated_at,
                sort_ts = excluded.sort_ts,
                run_json = excluded.run_json,
                persisted_at = excluded.persisted_at
            """,
            (
                run_id,
                str(item.get("status") or "").strip(),
                str(item.get("engine") or "").strip(),
                str(
                    item.get("workspace_id")
                    or context.get("workspace_id")
                    or ""
                ).strip(),
                str(
                    item.get("execution_target")
                    or metadata.get("execution_target_selected")
                    or metadata.get("execution_target_requested")
                    or ""
                ).strip(),
                created_at,
                updated_at,
                sort_ts,
                payload,
                _utc_now_iso(),
            ),
        )
        conn.commit()


def delete_live_run_state(db_path: Path, run_id: str) -> None:
    token = str(run_id or "").strip()
    if not token:
        return
    existing: Optional[Dict[str, Any]] = None
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT run_json
            FROM live_runs
            WHERE run_id = ?
            """,
            (token,),
        ).fetchone()
        if row is not None:
            raw = row["run_json"]
            try:
                parsed = json.loads(raw)
            except Exception:
                parsed = None
            if isinstance(parsed, dict):
                existing = parsed
    if not isinstance(existing, dict):
        return
    _enforce_runtime_state_store_decision(
        operation="delete_live_run",
        state_class="live_runs",
        item=existing,
        run_id=token,
        status=str(existing.get("status") or "archived").strip() or "archived",
    )
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM live_runs WHERE run_id = ?", (token,))
        conn.commit()


def list_live_run_states(db_path: Path, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    params: List[Any] = []
    query = """
        SELECT run_json
        FROM live_runs
        ORDER BY sort_ts DESC
    """
    if isinstance(limit, int) and limit > 0:
        query += "\nLIMIT ?"
        params.append(limit)
    with _connect(db_path) as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
    out: List[Dict[str, Any]] = []
    for row in rows:
        raw = row["run_json"]
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                out.append(parsed)
        except Exception:
            continue
    return out


def mark_stale_chat_stream_sessions_interrupted(
    db_path: Path,
    *,
    stale_before_ts: float,
    error_text: str,
) -> int:
    interrupted = 0
    for item in list_chat_stream_states(db_path, statuses=["active"]):
        updated_ts = _parse_ts(item.get("updated_at"))
        if updated_ts > float(stale_before_ts):
            continue
        item["status"] = "interrupted"
        item["updated_at"] = _utc_now_iso()
        item["error_text"] = str(error_text or "").strip()
        item["last_event_seq"] = max(1, int(item.get("last_event_seq") or 0) + 1)
        upsert_chat_stream_state(db_path, item)
        interrupted += 1
    return interrupted


def delete_chat_stream_sessions_older_than(db_path: Path, *, older_than_ts: float) -> int:
    deleted = 0
    target_session_ids: List[str] = []
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT session_id, updated_at, created_at
            FROM chat_stream_state
            """
        ).fetchall()
        for row in rows:
            reference = _parse_ts(row["updated_at"] or row["created_at"])
            if reference > float(older_than_ts):
                continue
            session_id = str(row["session_id"] or "").strip()
            if not session_id:
                continue
            target_session_ids.append(session_id)
        if target_session_ids:
            _enforce_runtime_state_store_decision(
                operation="prune_records",
                state_class="chat_stream_state",
                item={"workspace_id": "default"},
                status="archived",
                retention_days=30,
                records_requested=len(target_session_ids),
            )
        for session_id in target_session_ids:
            conn.execute("DELETE FROM chat_stream_state WHERE session_id = ?", (session_id,))
            deleted += 1
        conn.commit()
    return deleted


def replace_local_runtime_state(
    db_path: Path,
    *,
    pending_run_ids: List[str],
    claimed_runs: Dict[str, Dict[str, Any]],
    runtime_registrations: Dict[str, Dict[str, Any]],
) -> None:
    _enforce_runtime_state_store_decision(
        operation="checkpoint_snapshot",
        state_class="local_app_state",
        item={
            "workspace_id": "default",
            "checkpoint_kind": "local_runtime_state",
            "pending_count": len(pending_run_ids or []),
            "claimed_count": len(claimed_runs or {}),
            "runtime_registration_count": len(runtime_registrations or {}),
        },
        status="checkpoint",
    )
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM local_pending_queue")
        for idx, raw_run_id in enumerate(pending_run_ids):
            run_id = str(raw_run_id or "").strip()
            if not run_id:
                continue
            conn.execute(
                """
                INSERT INTO local_pending_queue (
                    run_id, queue_order, queued_at, persisted_at
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    run_id,
                    idx,
                    _utc_now_iso(),
                    _utc_now_iso(),
                ),
            )

        conn.execute("DELETE FROM local_claims")
        for raw_run_id, raw_claim in claimed_runs.items():
            run_id = str(raw_run_id or "").strip()
            claim = raw_claim if isinstance(raw_claim, dict) else None
            if not run_id or not isinstance(claim, dict):
                continue
            payload = json.dumps(claim, ensure_ascii=False, separators=(",", ":"))
            conn.execute(
                """
                INSERT INTO local_claims (
                    run_id, worker_id, claimed_at, last_heartbeat_at,
                    lease_seconds, claim_json, persisted_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    str(claim.get("worker_id") or "").strip(),
                    str(claim.get("claimed_at") or "").strip(),
                    str(claim.get("last_heartbeat_at") or "").strip(),
                    int(claim.get("lease_seconds") or 0),
                    payload,
                    _utc_now_iso(),
                ),
            )

        conn.execute("DELETE FROM runtime_registrations")
        for raw_runtime_id, raw_record in runtime_registrations.items():
            runtime_id = str(raw_runtime_id or "").strip()
            record = raw_record if isinstance(raw_record, dict) else None
            if not runtime_id or not isinstance(record, dict):
                continue
            payload = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
            conn.execute(
                """
                INSERT INTO runtime_registrations (
                    runtime_id, runtime_type, status, current_run_id,
                    last_seen_at, runtime_json, persisted_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    runtime_id,
                    str(record.get("runtime_type") or "").strip(),
                    str(record.get("status") or "").strip(),
                    str(record.get("current_run_id") or "").strip(),
                    str(record.get("last_seen_at") or "").strip(),
                    payload,
                    _utc_now_iso(),
                ),
            )
        conn.commit()


def load_local_runtime_state(db_path: Path) -> Dict[str, Any]:
    # This is an explicit local checkpoint restore seam. It does not define
    # server-side truth for runs, approvals, outbox rows, or runtime sessions.
    with _connect(db_path) as conn:
        queue_rows = conn.execute(
            """
            SELECT run_id
            FROM local_pending_queue
            ORDER BY queue_order ASC
            """
        ).fetchall()
        claim_rows = conn.execute(
            """
            SELECT run_id, claim_json
            FROM local_claims
            """
        ).fetchall()
        runtime_rows = conn.execute(
            """
            SELECT runtime_id, runtime_json
            FROM runtime_registrations
            """
        ).fetchall()

    pending_run_ids = [str(row["run_id"] or "").strip() for row in queue_rows if str(row["run_id"] or "").strip()]
    claimed_runs: Dict[str, Dict[str, Any]] = {}
    for row in claim_rows:
        run_id = str(row["run_id"] or "").strip()
        if not run_id:
            continue
        try:
            parsed = json.loads(row["claim_json"])
        except Exception:
            continue
        if isinstance(parsed, dict):
            claimed_runs[run_id] = parsed

    runtime_registrations: Dict[str, Dict[str, Any]] = {}
    for row in runtime_rows:
        runtime_id = str(row["runtime_id"] or "").strip()
        if not runtime_id:
            continue
        try:
            parsed = json.loads(row["runtime_json"])
        except Exception:
            continue
        if isinstance(parsed, dict):
            runtime_registrations[runtime_id] = parsed

    return {
        "pending_run_ids": pending_run_ids,
        "claimed_runs": claimed_runs,
        "runtime_registrations": runtime_registrations,
    }


def _prune_run_history(conn: sqlite3.Connection, limit: int) -> None:
    conn.execute(
        """
        DELETE FROM run_history
        WHERE run_id NOT IN (
            SELECT run_id
            FROM run_history
            ORDER BY sort_ts DESC
            LIMIT ?
        )
        """,
        (max(1, int(limit)),),
    )


def _prune_channel_events(conn: sqlite3.Connection, limit: int) -> None:
    conn.execute(
        """
        DELETE FROM channel_events
        WHERE id NOT IN (
            SELECT id
            FROM channel_events
            ORDER BY sort_ts DESC
            LIMIT ?
        )
        """,
        (max(1, int(limit)),),
    )


def upsert_run_history_item(db_path: Path, item: Dict[str, Any], limit: int) -> None:
    run_id = str(item.get("run_id") or "").strip()
    if not run_id:
        return
    created_at = str(item.get("created_at") or "").strip()
    updated_at = str(item.get("updated_at") or "").strip()
    completed_at = str(item.get("completed_at") or "").strip()
    sort_ts = _parse_ts(updated_at or completed_at or created_at)
    payload = json.dumps(item, ensure_ascii=False, separators=(",", ":"))
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO run_history (
                run_id, status, workspace_id, pack_id,
                created_at, updated_at, completed_at,
                sort_ts, snapshot_json, persisted_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                status = excluded.status,
                workspace_id = excluded.workspace_id,
                pack_id = excluded.pack_id,
                created_at = excluded.created_at,
                updated_at = excluded.updated_at,
                completed_at = excluded.completed_at,
                sort_ts = excluded.sort_ts,
                snapshot_json = excluded.snapshot_json,
                persisted_at = excluded.persisted_at
            """,
            (
                run_id,
                str(item.get("status") or "").strip(),
                str(item.get("workspace_id") or "").strip(),
                str(item.get("pack_id") or "").strip(),
                created_at,
                updated_at,
                completed_at,
                sort_ts,
                payload,
                _utc_now_iso(),
            ),
        )
        _prune_run_history(conn, limit)
        conn.commit()


def replace_run_history(db_path: Path, items: List[Dict[str, Any]], limit: int) -> None:
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM run_history")
        for item in items[: max(1, int(limit))]:
            run_id = str(item.get("run_id") or "").strip()
            if not run_id:
                continue
            created_at = str(item.get("created_at") or "").strip()
            updated_at = str(item.get("updated_at") or "").strip()
            completed_at = str(item.get("completed_at") or "").strip()
            sort_ts = _parse_ts(updated_at or completed_at or created_at)
            payload = json.dumps(item, ensure_ascii=False, separators=(",", ":"))
            conn.execute(
                """
                INSERT INTO run_history (
                    run_id, status, workspace_id, pack_id,
                    created_at, updated_at, completed_at,
                    sort_ts, snapshot_json, persisted_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    str(item.get("status") or "").strip(),
                    str(item.get("workspace_id") or "").strip(),
                    str(item.get("pack_id") or "").strip(),
                    created_at,
                    updated_at,
                    completed_at,
                    sort_ts,
                    payload,
                    _utc_now_iso(),
                ),
            )
        _prune_run_history(conn, limit)
        conn.commit()


def list_run_history(db_path: Path, limit: int) -> List[Dict[str, Any]]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT snapshot_json
            FROM run_history
            ORDER BY sort_ts DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
    out: List[Dict[str, Any]] = []
    for row in rows:
        raw = row["snapshot_json"]
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                out.append(parsed)
        except Exception:
            continue
    return out


def append_channel_event(db_path: Path, item: Dict[str, Any], limit: int) -> None:
    event_id = str(item.get("id") or "").strip()
    if not event_id:
        return
    ts = str(item.get("ts") or "").strip()
    sort_ts = _parse_ts(ts)
    payload = json.dumps(item, ensure_ascii=False, separators=(",", ":"))
    _enforce_runtime_state_store_decision(
        operation="append_channel_event",
        state_class="channel_events",
        item=item,
        run_id=str(item.get("run_id") or "").strip() or None,
        status=str(item.get("event_type") or item.get("action") or "event").strip().lower() or "event",
    )
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO channel_events (
                id, ts, workspace_id, channel, direction, event_type,
                session_key, run_id, trace_id, action,
                sort_ts, event_json, persisted_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                ts = excluded.ts,
                workspace_id = excluded.workspace_id,
                channel = excluded.channel,
                direction = excluded.direction,
                event_type = excluded.event_type,
                session_key = excluded.session_key,
                run_id = excluded.run_id,
                trace_id = excluded.trace_id,
                action = excluded.action,
                sort_ts = excluded.sort_ts,
                event_json = excluded.event_json,
                persisted_at = excluded.persisted_at
            """,
            (
                event_id,
                ts,
                str(item.get("workspace_id") or "").strip(),
                str(item.get("channel") or "").strip().lower(),
                str(item.get("direction") or "").strip().lower(),
                str(item.get("event_type") or "").strip().lower(),
                str(item.get("session_key") or "").strip(),
                str(item.get("run_id") or "").strip(),
                str(item.get("trace_id") or "").strip(),
                str(item.get("action") or "").strip().lower(),
                sort_ts,
                payload,
                _utc_now_iso(),
            ),
        )
        _prune_channel_events(conn, limit)
        conn.commit()


def replace_channel_events(db_path: Path, items: List[Dict[str, Any]], limit: int) -> None:
    first_item = items[0] if items and isinstance(items[0], dict) else {}
    _enforce_runtime_state_store_decision(
        operation="checkpoint_snapshot",
        state_class="channel_events",
        item={
            "workspace_id": str(first_item.get("workspace_id") or "default").strip() or "default",
            "trace_id": str(first_item.get("trace_id") or "").strip(),
            "checkpoint_kind": "channel_events",
            "checkpoint_item_count": min(len(items or []), max(1, int(limit))),
        },
        status="checkpoint",
    )
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM channel_events")
        for item in items[: max(1, int(limit))]:
            event_id = str(item.get("id") or "").strip()
            if not event_id:
                continue
            ts = str(item.get("ts") or "").strip()
            sort_ts = _parse_ts(ts)
            payload = json.dumps(item, ensure_ascii=False, separators=(",", ":"))
            conn.execute(
                """
                INSERT INTO channel_events (
                    id, ts, workspace_id, channel, direction, event_type,
                    session_key, run_id, trace_id, action,
                    sort_ts, event_json, persisted_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    ts,
                    str(item.get("workspace_id") or "").strip(),
                    str(item.get("channel") or "").strip().lower(),
                    str(item.get("direction") or "").strip().lower(),
                    str(item.get("event_type") or "").strip().lower(),
                    str(item.get("session_key") or "").strip(),
                    str(item.get("run_id") or "").strip(),
                    str(item.get("trace_id") or "").strip(),
                    str(item.get("action") or "").strip().lower(),
                    sort_ts,
                    payload,
                    _utc_now_iso(),
                ),
            )
        _prune_channel_events(conn, limit)
        conn.commit()


def list_channel_events(db_path: Path, limit: int) -> List[Dict[str, Any]]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT event_json
            FROM channel_events
            ORDER BY sort_ts DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
    out: List[Dict[str, Any]] = []
    for row in rows:
        raw = row["event_json"]
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                out.append(parsed)
        except Exception:
            continue
    return out


def _normalize_notification_row(row: sqlite3.Row) -> Dict[str, Any]:
    payload = _parse_json_blob(row["notification_json"], {})
    if not isinstance(payload, dict):
        payload = {}
    record = dict(payload)
    record["id"] = str(record.get("id") or row["id"] or "").strip()
    record["ts"] = str(record.get("ts") or row["created_at"] or "").strip()
    record["tenant_id"] = str(record.get("tenant_id") or row["tenant_id"] or "").strip()
    record["workspace_id"] = str(record.get("workspace_id") or row["workspace_id"] or "").strip()
    record["session_key"] = str(record.get("session_key") or row["session_key"] or "").strip()
    record["session_id"] = str(record.get("session_id") or row["session_id"] or "").strip()
    record["channel"] = str(record.get("channel") or row["channel"] or "").strip().lower()
    record["direction"] = str(record.get("direction") or row["direction"] or "").strip().lower()
    record["event_type"] = str(record.get("event_type") or row["event_type"] or "").strip().lower()
    record["run_id"] = str(record.get("run_id") or row["run_id"] or "").strip()
    record["machine_id"] = str(record.get("machine_id") or row["machine_id"] or "").strip()
    record["trace_id"] = str(record.get("trace_id") or row["trace_id"] or "").strip()
    record["action"] = str(record.get("action") or row["action"] or "").strip().lower()
    record["title"] = str(record.get("title") or row["title"] or "").strip() or None
    record["text"] = str(record.get("text") or row["text"] or "").strip()
    read_at = str(row["read_at"] or "").strip() if "read_at" in row.keys() else ""
    record["read_at"] = read_at or None
    return record


def _normalize_notification_device_row(row: sqlite3.Row) -> Dict[str, Any]:
    payload = _parse_json_blob(row["device_json"], {})
    if not isinstance(payload, dict):
        payload = {}
    record = dict(payload)
    record["device_id"] = str(record.get("device_id") or row["device_id"] or "").strip()
    record["tenant_id"] = str(record.get("tenant_id") or row["tenant_id"] or "").strip()
    record["workspace_id"] = str(record.get("workspace_id") or row["workspace_id"] or "").strip()
    record["reader_key"] = str(record.get("reader_key") or row["reader_key"] or "").strip()
    record["provider"] = str(record.get("provider") or row["provider"] or "").strip().lower()
    record["push_token"] = str(record.get("push_token") or row["push_token"] or "").strip()
    record["platform"] = str(record.get("platform") or row["platform"] or "").strip().lower() or None
    record["app_id"] = str(record.get("app_id") or row["app_id"] or "").strip() or None
    record["device_name"] = str(record.get("device_name") or row["device_name"] or "").strip() or None
    record["status"] = str(record.get("status") or row["status"] or "").strip().lower() or "active"
    record["last_registered_at"] = str(record.get("last_registered_at") or row["last_registered_at"] or "").strip()
    record["last_delivered_at"] = str(record.get("last_delivered_at") or row["last_delivered_at"] or "").strip() or None
    record["last_error"] = str(record.get("last_error") or row["last_error"] or "").strip() or None
    capabilities = _parse_json_blob(row["capabilities_json"], [])
    record["capabilities"] = capabilities if isinstance(capabilities, list) else []
    return record


def upsert_notification(db_path: Path, item: Dict[str, Any]) -> None:
    notification_id = str(item.get("id") or "").strip()
    if not notification_id:
        return
    created_at = str(item.get("ts") or item.get("created_at") or "").strip() or _utc_now_iso()
    sort_ts = _parse_ts(created_at)
    payload = json.dumps(item, ensure_ascii=False, separators=(",", ":"))
    _enforce_runtime_state_store_decision(
        operation="upsert_notification",
        state_class="notifications",
        item=item,
        notification_id=notification_id,
        status=str(item.get("event_type") or item.get("status") or "notification").strip().lower() or "notification",
    )
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO notifications (
                id, tenant_id, workspace_id, session_key, session_id, channel,
                direction, event_type, run_id, machine_id, trace_id, action,
                title, text, sort_ts, notification_json, created_at, persisted_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                tenant_id = excluded.tenant_id,
                workspace_id = excluded.workspace_id,
                session_key = excluded.session_key,
                session_id = excluded.session_id,
                channel = excluded.channel,
                direction = excluded.direction,
                event_type = excluded.event_type,
                run_id = excluded.run_id,
                machine_id = excluded.machine_id,
                trace_id = excluded.trace_id,
                action = excluded.action,
                title = excluded.title,
                text = excluded.text,
                sort_ts = excluded.sort_ts,
                notification_json = excluded.notification_json,
                created_at = excluded.created_at,
                persisted_at = excluded.persisted_at
            """,
            (
                notification_id,
                str(item.get("tenant_id") or "").strip(),
                str(item.get("workspace_id") or "").strip(),
                str(item.get("session_key") or "").strip(),
                str(item.get("session_id") or "").strip(),
                str(item.get("channel") or "").strip().lower(),
                str(item.get("direction") or "").strip().lower(),
                str(item.get("event_type") or "").strip().lower(),
                str(item.get("run_id") or "").strip(),
                str(item.get("machine_id") or "").strip(),
                str(item.get("trace_id") or "").strip(),
                str(item.get("action") or "").strip().lower(),
                str(item.get("title") or "").strip() or None,
                str(item.get("text") or ""),
                sort_ts,
                payload,
                created_at,
                _utc_now_iso(),
            ),
        )
        conn.commit()


def list_notifications(
    db_path: Path,
    *,
    reader_key: str = "",
    tenant_id: str = "",
    workspace_id: str = "",
    channel: str = "",
    session_key: str = "",
    direction: str = "",
    action: str = "",
    run_id: str = "",
    trace_id: str = "",
    limit: int = 80,
) -> List[Dict[str, Any]]:
    params: List[Any] = [str(reader_key or "").strip()]
    clauses: List[str] = []
    if str(tenant_id or "").strip():
        clauses.append("n.tenant_id = ?")
        params.append(str(tenant_id or "").strip())
    if str(workspace_id or "").strip():
        clauses.append("n.workspace_id = ?")
        params.append(str(workspace_id or "").strip())
    if str(channel or "").strip():
        clauses.append("n.channel = ?")
        params.append(str(channel or "").strip().lower())
    if str(session_key or "").strip():
        clauses.append("n.session_key = ?")
        params.append(str(session_key or "").strip())
    if str(direction or "").strip():
        clauses.append("n.direction = ?")
        params.append(str(direction or "").strip().lower())
    if str(action or "").strip():
        clauses.append("n.action = ?")
        params.append(str(action or "").strip().lower())
    if str(run_id or "").strip():
        clauses.append("n.run_id = ?")
        params.append(str(run_id or "").strip())
    if str(trace_id or "").strip():
        clauses.append("n.trace_id = ?")
        params.append(str(trace_id or "").strip())
    query = """
        SELECT n.id, n.tenant_id, n.workspace_id, n.session_key, n.session_id,
               n.channel, n.direction, n.event_type, n.run_id, n.machine_id,
               n.trace_id, n.action, n.title, n.text, n.notification_json,
               n.created_at, r.read_at
        FROM notifications n
        LEFT JOIN notification_reads r
          ON n.id = r.notification_id
         AND r.reader_key = ?
    """
    if clauses:
        query += "\nWHERE " + " AND ".join(clauses)
    query += "\nORDER BY n.sort_ts DESC\nLIMIT ?"
    params.append(max(1, int(limit or 0)))
    with _connect(db_path) as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
    return [_normalize_notification_row(row) for row in rows]


def list_notifications_by_ids(
    db_path: Path,
    *,
    reader_key: str = "",
    notification_ids: List[str],
) -> List[Dict[str, Any]]:
    normalized = [str(item or "").strip() for item in (notification_ids or []) if str(item or "").strip()]
    if not normalized:
        return []
    placeholders = ", ".join("?" for _ in normalized)
    query = f"""
        SELECT n.id, n.tenant_id, n.workspace_id, n.session_key, n.session_id,
               n.channel, n.direction, n.event_type, n.run_id, n.machine_id,
               n.trace_id, n.action, n.title, n.text, n.notification_json,
               n.created_at, r.read_at
        FROM notifications n
        LEFT JOIN notification_reads r
          ON n.id = r.notification_id
         AND r.reader_key = ?
        WHERE n.id IN ({placeholders})
        ORDER BY n.sort_ts DESC
    """
    params: List[Any] = [str(reader_key or "").strip(), *normalized]
    with _connect(db_path) as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
    return [_normalize_notification_row(row) for row in rows]


def summarize_notification_sessions(
    items: List[Dict[str, Any]],
    *,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    sessions: Dict[str, Dict[str, Any]] = {}
    ordered: List[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        session_key = (
            str(item.get("session_key") or "").strip()
            or (f"run:{str(item.get('run_id') or '').strip()}" if str(item.get("run_id") or "").strip() else "")
            or (f"machine:{str(item.get('machine_id') or '').strip()}" if str(item.get("machine_id") or "").strip() else "")
        )
        if not session_key:
            continue
        if session_key not in sessions:
            ordered.append(session_key)
            sessions[session_key] = {
                "session_key": session_key,
                "workspace_id": str(item.get("workspace_id") or "").strip(),
                "run_id": str(item.get("run_id") or "").strip() or None,
                "machine_id": str(item.get("machine_id") or "").strip() or None,
                "latest_text": str(item.get("text") or "").strip(),
                "latest_action": str(item.get("action") or "").strip() or None,
                "latest_ts": str(item.get("ts") or "").strip() or None,
                "count": 0,
                "unread_count": 0,
            }
        entry = sessions[session_key]
        entry["count"] = int(entry.get("count") or 0) + 1
        if not str(item.get("read_at") or "").strip():
            entry["unread_count"] = int(entry.get("unread_count") or 0) + 1
    out = [sessions[key] for key in ordered]
    if isinstance(limit, int) and limit > 0:
        return out[:limit]
    return out


def mark_notifications_read(
    db_path: Path,
    *,
    reader_key: str,
    notification_ids: Optional[List[str]] = None,
    tenant_id: str = "",
    workspace_id: str = "",
    mark_all: bool = False,
) -> Dict[str, Any]:
    reader_token = str(reader_key or "").strip()
    if not reader_token:
        return {"marked_count": 0, "marked_ids": [], "read_at": None}
    target_ids: List[str] = [
        str(item or "").strip()
        for item in (notification_ids or [])
        if str(item or "").strip()
    ]
    if mark_all:
        target_ids = [
            str(item.get("id") or "").strip()
            for item in list_notifications(
                db_path,
                reader_key=reader_token,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                limit=500,
            )
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        ]
    deduped: List[str] = []
    seen: set[str] = set()
    for item in target_ids:
        if not item or item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    if not deduped:
        return {"marked_count": 0, "marked_ids": [], "read_at": None}
    read_at = _utc_now_iso()
    for notification_id in deduped:
        _enforce_runtime_state_store_decision(
            operation="mark_notification_read",
            state_class="notifications",
            notification_id=notification_id,
            actor_id=reader_token,
            item={
                "tenant_id": str(tenant_id or "").strip(),
                "workspace_id": str(workspace_id or "").strip(),
                "reader_key": reader_token,
            },
            status="read",
        )
    with _connect(db_path) as conn:
        for notification_id in deduped:
            row = conn.execute(
                "SELECT tenant_id, workspace_id FROM notifications WHERE id = ?",
                (notification_id,),
            ).fetchone()
            if row is None:
                continue
            conn.execute(
                """
                INSERT INTO notification_reads (
                    notification_id, reader_key, tenant_id, workspace_id, read_at, persisted_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(notification_id, reader_key) DO UPDATE SET
                    tenant_id = excluded.tenant_id,
                    workspace_id = excluded.workspace_id,
                    read_at = excluded.read_at,
                    persisted_at = excluded.persisted_at
                """,
                (
                    notification_id,
                    reader_token,
                    str(row["tenant_id"] or "").strip(),
                    str(row["workspace_id"] or "").strip(),
                    read_at,
                    _utc_now_iso(),
                ),
            )
        conn.commit()
    return {"marked_count": len(deduped), "marked_ids": deduped, "read_at": read_at}


def upsert_notification_device(db_path: Path, item: Dict[str, Any]) -> Dict[str, Any]:
    device_id = str(item.get("device_id") or "").strip()
    push_token = str(item.get("push_token") or "").strip()
    provider = str(item.get("provider") or "expo").strip().lower() or "expo"
    if not device_id or not push_token:
        return {}
    registered_at = str(item.get("last_registered_at") or item.get("registered_at") or "").strip() or _utc_now_iso()
    capabilities = item.get("capabilities") if isinstance(item.get("capabilities"), list) else []
    payload = dict(item)
    payload["device_id"] = device_id
    payload["push_token"] = push_token
    payload["provider"] = provider
    payload["status"] = str(item.get("status") or "active").strip().lower() or "active"
    payload["last_registered_at"] = registered_at
    _enforce_runtime_state_store_decision(
        operation="register_notification_device",
        state_class="notification_devices",
        item=payload,
        device_id=device_id,
        status=str(payload.get("status") or "active").strip().lower() or "active",
    )
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO notification_devices (
                device_id, tenant_id, workspace_id, reader_key, provider, push_token,
                platform, app_id, device_name, status, capabilities_json, device_json,
                last_registered_at, last_delivered_at, last_error, persisted_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(device_id) DO UPDATE SET
                tenant_id = excluded.tenant_id,
                workspace_id = excluded.workspace_id,
                reader_key = excluded.reader_key,
                provider = excluded.provider,
                push_token = excluded.push_token,
                platform = excluded.platform,
                app_id = excluded.app_id,
                device_name = excluded.device_name,
                status = excluded.status,
                capabilities_json = excluded.capabilities_json,
                device_json = excluded.device_json,
                last_registered_at = excluded.last_registered_at,
                last_delivered_at = excluded.last_delivered_at,
                last_error = excluded.last_error,
                persisted_at = excluded.persisted_at
            """,
            (
                device_id,
                str(item.get("tenant_id") or "").strip(),
                str(item.get("workspace_id") or "").strip(),
                str(item.get("reader_key") or "").strip(),
                provider,
                push_token,
                str(item.get("platform") or "").strip().lower() or None,
                str(item.get("app_id") or "").strip() or None,
                str(item.get("device_name") or "").strip() or None,
                str(payload.get("status") or "active").strip().lower() or "active",
                _json_blob(capabilities if isinstance(capabilities, list) else [], fallback="[]"),
                _json_blob(payload, fallback="{}"),
                registered_at,
                str(item.get("last_delivered_at") or "").strip() or None,
                str(item.get("last_error") or "").strip() or None,
                _utc_now_iso(),
            ),
        )
        conn.commit()
    return payload


def list_notification_devices(
    db_path: Path,
    *,
    tenant_id: str = "",
    workspace_id: str = "",
    reader_key: str = "",
    status: str = "",
) -> List[Dict[str, Any]]:
    clauses: List[str] = []
    params: List[Any] = []
    if str(tenant_id or "").strip():
        clauses.append("tenant_id = ?")
        params.append(str(tenant_id or "").strip())
    if str(workspace_id or "").strip():
        clauses.append("workspace_id = ?")
        params.append(str(workspace_id or "").strip())
    if str(reader_key or "").strip():
        clauses.append("reader_key = ?")
        params.append(str(reader_key or "").strip())
    if str(status or "").strip():
        clauses.append("status = ?")
        params.append(str(status or "").strip().lower())
    query = """
        SELECT device_id, tenant_id, workspace_id, reader_key, provider, push_token,
               platform, app_id, device_name, status, capabilities_json, device_json,
               last_registered_at, last_delivered_at, last_error
        FROM notification_devices
    """
    if clauses:
        query += "\nWHERE " + " AND ".join(clauses)
    query += "\nORDER BY last_registered_at DESC"
    with _connect(db_path) as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
    return [_normalize_notification_device_row(row) for row in rows]


def update_notification_device_status(
    db_path: Path,
    device_id: str,
    *,
    status: Optional[str] = None,
    last_error: Optional[str] = None,
    last_delivered_at: Optional[str] = None,
) -> None:
    token = str(device_id or "").strip()
    if not token:
        return
    assignments: List[str] = []
    params: List[Any] = []
    if status is not None:
        assignments.append("status = ?")
        params.append(str(status or "").strip().lower() or "active")
    if last_error is not None:
        assignments.append("last_error = ?")
        params.append(str(last_error or "").strip() or None)
    if last_delivered_at is not None:
        assignments.append("last_delivered_at = ?")
        params.append(str(last_delivered_at or "").strip() or None)
    if not assignments:
        return
    assignments.append("persisted_at = ?")
    params.append(_utc_now_iso())
    params.append(token)
    with _connect(db_path) as conn:
        conn.execute(
            f"UPDATE notification_devices SET {', '.join(assignments)} WHERE device_id = ?",
            tuple(params),
        )
        conn.commit()


def upsert_notification_delivery(
    db_path: Path,
    *,
    notification_id: str,
    device_id: str,
    status: str,
    delivered_at: Optional[str] = None,
    last_error: Optional[str] = None,
) -> None:
    notification_token = str(notification_id or "").strip()
    device_token = str(device_id or "").strip()
    if not notification_token or not device_token:
        return
    delivery_item = {
        "notification_id": notification_token,
        "device_id": device_token,
        "status": str(status or "").strip().lower() or "pending",
    }
    _enforce_runtime_state_store_decision(
        operation="update_notification_delivery",
        state_class="notification_delivery_statuses",
        item=delivery_item,
        notification_id=notification_token,
        device_id=device_token,
        status=delivery_item["status"],
    )
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO notification_deliveries (
                notification_id, device_id, status, delivered_at, last_error, persisted_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(notification_id, device_id) DO UPDATE SET
                status = excluded.status,
                delivered_at = excluded.delivered_at,
                last_error = excluded.last_error,
                persisted_at = excluded.persisted_at
            """,
            (
                notification_token,
                device_token,
                str(status or "").strip().lower() or "pending",
                str(delivered_at or "").strip() or None,
                str(last_error or "").strip() or None,
                _utc_now_iso(),
            ),
        )
        conn.commit()


def list_notification_delivery_statuses(
    db_path: Path,
    *,
    notification_id: str,
) -> Dict[str, Dict[str, Any]]:
    notification_token = str(notification_id or "").strip()
    if not notification_token:
        return {}
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT notification_id, device_id, status, delivered_at, last_error
            FROM notification_deliveries
            WHERE notification_id = ?
            """,
            (notification_token,),
        ).fetchall()
    out: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        device_id = str(row["device_id"] or "").strip()
        if not device_id:
            continue
        out[device_id] = {
            "notification_id": notification_token,
            "device_id": device_id,
            "status": str(row["status"] or "").strip().lower() or "pending",
            "delivered_at": str(row["delivered_at"] or "").strip() or None,
            "last_error": str(row["last_error"] or "").strip() or None,
        }
    return out


def get_local_app_state(db_path: Path, state_key: str) -> Optional[Dict[str, Any]]:
    token = str(state_key or "").strip()
    if not token:
        return None
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT state_key, value_json, updated_at
            FROM local_app_state
            WHERE state_key = ?
            """,
            (token,),
        ).fetchone()
    if row is None:
        return None
    try:
        value = json.loads(str(row["value_json"] or "null"))
    except Exception:
        value = None
    return {
        "state_key": token,
        "value": value,
        "updated_at": str(row["updated_at"] or "").strip() or None,
    }


def put_local_app_state(
    db_path: Path,
    state_key: str,
    value: Any,
    *,
    updated_at: Optional[str] = None,
) -> Dict[str, Any]:
    token = str(state_key or "").strip()
    if not token:
        raise ValueError("state_key is required.")
    timestamp = str(updated_at or "").strip() or _utc_now_iso()
    serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO local_app_state (
                state_key, value_json, updated_at, persisted_at
            )
            VALUES (?, ?, ?, ?)
            ON CONFLICT(state_key) DO UPDATE SET
                value_json = excluded.value_json,
                updated_at = excluded.updated_at,
                persisted_at = excluded.persisted_at
            """,
            (
                token,
                serialized,
                timestamp,
                _utc_now_iso(),
            ),
        )
        conn.commit()
    return {
        "state_key": token,
        "value": value,
        "updated_at": timestamp,
    }


def build_runtime_checkpoint_manifest(db_path: Path) -> Dict[str, Any]:
    target = Path(db_path).expanduser().resolve()
    return {
        "store_id": "runtime_checkpoint_sqlite",
        "authority_mode": "checkpoint_restore_only",
        "db_path": str(target),
        "initializer": "server_modules.runtime_state_store.init_runtime_state_db",
        "restore_rehearsal": "server_modules.runtime_state_store.rehearse_runtime_checkpoint_restore",
        "restore_loader": "server_modules.runtime_state_store.load_local_runtime_state",
        "checkpoint_only_state_classes": sorted(SQLITE_CHECKPOINT_ONLY_STATE_CLASSES),
        "sqlite_tables": list(RUNTIME_CHECKPOINT_SQLITE_TABLES),
    }


def rehearse_runtime_checkpoint_restore(db_path: Path) -> Dict[str, Any]:
    target = Path(db_path).expanduser().resolve()
    init_runtime_state_db(target)
    restored = load_local_runtime_state(target)
    table_counts: Dict[str, int] = {}
    with _connect(target) as conn:
        for table_name in RUNTIME_CHECKPOINT_SQLITE_TABLES:
            row = conn.execute(f"SELECT COUNT(*) AS count FROM {table_name}").fetchone()
            table_counts[table_name] = int((row["count"] if row is not None else 0) or 0)
    return {
        "ok": True,
        "store_id": "runtime_checkpoint_sqlite",
        "db_path": str(target),
        "table_counts": table_counts,
        "restored_state_summary": {
            "pending_run_count": len(list(restored.get("pending_run_ids") or [])),
            "claimed_run_count": len(dict(restored.get("claimed_runs") or {})),
            "runtime_registration_count": len(dict(restored.get("runtime_registrations") or {})),
        },
        "checkpoint_only_state_classes": sorted(SQLITE_CHECKPOINT_ONLY_STATE_CLASSES),
    }

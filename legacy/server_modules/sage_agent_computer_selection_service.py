from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


EMPYRALIS_STATE_HOME = Path(
    os.getenv("EMPYRALIS_STATE_HOME", str(Path.home() / ".empyralis" / "state"))
).expanduser()
SAGE_AGENT_COMPUTER_SELECTION_DB_FILE = (
    Path(
        os.getenv(
            "EMPYRALIS_SAGE_AGENT_COMPUTER_SELECTION_DB",
            EMPYRALIS_STATE_HOME / "gateway" / "sage-agent-computer-selection.sqlite3",
        )
    )
).expanduser()
_DB_LOCK = threading.Lock()

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sage_agent_computer_selections (
    workspace_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    selected_gateway_id TEXT NOT NULL,
    selected_at TEXT NOT NULL,
    selected_by TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (workspace_id, user_id)
);
"""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _connect(db_path: Optional[Path | str] = None) -> sqlite3.Connection:
    resolved = Path(db_path or SAGE_AGENT_COMPUTER_SELECTION_DB_FILE).expanduser()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(resolved, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.executescript(SCHEMA_SQL)
    connection.commit()
    return connection


def _json_dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, sort_keys=True)


def _json_loads(value: Any) -> Any:
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        return json.loads(value)
    except Exception:
        return {}


def _selection_from_row(row: sqlite3.Row | None) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    return {
        "workspace_id": str(row["workspace_id"] or ""),
        "user_id": str(row["user_id"] or ""),
        "selected_gateway_id": str(row["selected_gateway_id"] or ""),
        "selected_at": str(row["selected_at"] or ""),
        "selected_by": str(row["selected_by"] or ""),
        "metadata": _json_loads(row["metadata"]),
    }


def get_selection(
    *,
    workspace_id: str,
    user_id: str,
    db_path: Optional[Path | str] = None,
) -> Optional[Dict[str, Any]]:
    clean_workspace_id = str(workspace_id or "").strip() or "default"
    clean_user_id = str(user_id or "").strip()
    if not clean_user_id:
        return None
    with _DB_LOCK:
        connection = _connect(db_path)
        try:
            row = connection.execute(
                """
                SELECT * FROM sage_agent_computer_selections
                WHERE workspace_id = ? AND user_id = ?
                """,
                (clean_workspace_id, clean_user_id),
            ).fetchone()
        finally:
            connection.close()
    return _selection_from_row(row)


def set_selection(
    *,
    workspace_id: str,
    user_id: str,
    selected_gateway_id: str,
    selected_by: str,
    metadata: Optional[Dict[str, Any]] = None,
    db_path: Optional[Path | str] = None,
) -> Dict[str, Any]:
    clean_workspace_id = str(workspace_id or "").strip() or "default"
    clean_user_id = str(user_id or "").strip()
    clean_gateway_id = str(selected_gateway_id or "").strip()
    clean_selected_by = str(selected_by or "").strip() or clean_user_id
    if not clean_user_id:
        raise ValueError("user_id_required")
    if not clean_gateway_id:
        raise ValueError("selected_gateway_id_required")
    selected_at = _utc_now_iso()
    with _DB_LOCK:
        connection = _connect(db_path)
        try:
            connection.execute(
                """
                INSERT INTO sage_agent_computer_selections (
                    workspace_id, user_id, selected_gateway_id, selected_at, selected_by, metadata
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(workspace_id, user_id) DO UPDATE SET
                    selected_gateway_id=excluded.selected_gateway_id,
                    selected_at=excluded.selected_at,
                    selected_by=excluded.selected_by,
                    metadata=excluded.metadata
                """,
                (
                    clean_workspace_id,
                    clean_user_id,
                    clean_gateway_id,
                    selected_at,
                    clean_selected_by,
                    _json_dumps(metadata or {}),
                ),
            )
            connection.commit()
            row = connection.execute(
                """
                SELECT * FROM sage_agent_computer_selections
                WHERE workspace_id = ? AND user_id = ?
                """,
                (clean_workspace_id, clean_user_id),
            ).fetchone()
        finally:
            connection.close()
    selection = _selection_from_row(row)
    if not selection:
        raise RuntimeError("selection_write_failed")
    return selection

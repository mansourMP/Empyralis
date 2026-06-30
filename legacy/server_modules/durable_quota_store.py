from __future__ import annotations

import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


_STATE_HOME = Path(
    os.getenv("EMPYRALIS_STATE_HOME", str(Path.home() / ".empyralis" / "state"))
).expanduser()
_DEFAULT_DB_PATH = _STATE_HOME / "quota" / "quota-windows.sqlite3"
_SCHEMA_READY = False
_SCHEMA_LOCK = threading.RLock()


@dataclass(frozen=True)
class QuotaWindowDecision:
    allowed: bool
    limit: int
    observed: int
    remaining: int
    retry_after_seconds: int
    window_seconds: int
    storage: str = "sqlite"


def use_in_memory_quota_mode() -> bool:
    value = str(os.getenv("EMPYRALIS_DEV_IN_MEMORY_QUOTAS") or "").strip().lower()
    if value in {"1", "true", "yes", "on", "dev"}:
        return True
    if os.getenv("PYTEST_CURRENT_TEST") and not os.getenv("EMPYRALIS_QUOTA_DB"):
        return True
    return False


def _db_path() -> Path:
    return Path(os.getenv("EMPYRALIS_QUOTA_DB", str(_DEFAULT_DB_PATH))).expanduser()


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=10.0, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=10000")
    return connection


def _ensure_schema(connection: sqlite3.Connection) -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS quota_windows (
                quota_key TEXT PRIMARY KEY,
                window_started_at REAL NOT NULL,
                count INTEGER NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_quota_windows_updated_at ON quota_windows(updated_at)"
        )
        _SCHEMA_READY = True


def consume_window(
    *,
    key: str,
    limit: int,
    window_seconds: int = 60,
    now: Optional[float] = None,
) -> QuotaWindowDecision:
    normalized_key = str(key or "").strip()
    if not normalized_key:
        raise ValueError("quota key is required")
    normalized_limit = max(int(limit or 0), 1)
    normalized_window = max(int(window_seconds or 0), 1)
    current = float(now if now is not None else time.time())

    connection = _connect()
    try:
        _ensure_schema(connection)
        connection.execute("BEGIN IMMEDIATE")
        try:
            row = connection.execute(
                "SELECT quota_key, window_started_at, count FROM quota_windows WHERE quota_key = ?",
                (normalized_key,),
            ).fetchone()
            if row is None or current - float(row["window_started_at"]) >= normalized_window:
                window_started_at = current
                observed = 0
            else:
                window_started_at = float(row["window_started_at"])
                observed = max(int(row["count"] or 0), 0)

            if observed >= normalized_limit:
                retry_after = max(1, int(round(window_started_at + normalized_window - current)))
                connection.commit()
                return QuotaWindowDecision(
                    allowed=False,
                    limit=normalized_limit,
                    observed=observed,
                    remaining=0,
                    retry_after_seconds=retry_after,
                    window_seconds=normalized_window,
                )

            observed += 1
            connection.execute(
                """
                INSERT INTO quota_windows (quota_key, window_started_at, count, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(quota_key) DO UPDATE SET
                    window_started_at = excluded.window_started_at,
                    count = excluded.count,
                    updated_at = excluded.updated_at
                """,
                (normalized_key, window_started_at, observed, current),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    finally:
        connection.close()

    return QuotaWindowDecision(
        allowed=True,
        limit=normalized_limit,
        observed=observed,
        remaining=max(0, normalized_limit - observed),
        retry_after_seconds=0,
        window_seconds=normalized_window,
    )


def snapshot_window(
    *,
    key: str,
    limit: int,
    window_seconds: int = 60,
    now: Optional[float] = None,
) -> QuotaWindowDecision:
    normalized_key = str(key or "").strip()
    normalized_limit = max(int(limit or 0), 1)
    normalized_window = max(int(window_seconds or 0), 1)
    current = float(now if now is not None else time.time())
    if not normalized_key:
        return QuotaWindowDecision(
            allowed=True,
            limit=normalized_limit,
            observed=0,
            remaining=normalized_limit,
            retry_after_seconds=0,
            window_seconds=normalized_window,
        )
    connection = _connect()
    try:
        _ensure_schema(connection)
        row = connection.execute(
            "SELECT window_started_at, count FROM quota_windows WHERE quota_key = ?",
            (normalized_key,),
        ).fetchone()
    finally:
        connection.close()
    if row is None or current - float(row["window_started_at"]) >= normalized_window:
        observed = 0
        retry_after = 0
    else:
        observed = max(int(row["count"] or 0), 0)
        retry_after = max(1, int(round(float(row["window_started_at"]) + normalized_window - current)))
    return QuotaWindowDecision(
        allowed=observed < normalized_limit,
        limit=normalized_limit,
        observed=observed,
        remaining=max(0, normalized_limit - observed),
        retry_after_seconds=0 if observed < normalized_limit else retry_after,
        window_seconds=normalized_window,
    )


def reset_quota_store_for_tests() -> None:
    global _SCHEMA_READY
    with _SCHEMA_LOCK:
        _SCHEMA_READY = False
    path = _db_path()
    if path.exists():
        path.unlink()

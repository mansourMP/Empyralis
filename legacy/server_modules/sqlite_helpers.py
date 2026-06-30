from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from typing import Any, Optional


class ClosingSqliteConnection(sqlite3.Connection):
    """SQLite connection whose context manager also closes the file handle."""

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        try:
            return bool(super().__exit__(exc_type, exc, traceback))
        finally:
            self.close()


def _safe_path_check(check: Any) -> bool:
    try:
        return bool(check())
    except OSError:
        return False


def connect_sqlite_rw(
    db_path: Path,
    *,
    logger: Optional[logging.Logger] = None,
    label: str = "sqlite",
) -> sqlite3.Connection:
    resolved = db_path.expanduser()
    parent = resolved.parent
    parent.mkdir(parents=True, exist_ok=True)
    connection: Optional[sqlite3.Connection] = None
    try:
        connection = sqlite3.connect(str(resolved), timeout=10.0, factory=ClosingSqliteConnection)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL;")
        connection.execute("PRAGMA synchronous=NORMAL;")
        connection.execute("PRAGMA busy_timeout=5000;")
        connection.execute("PRAGMA foreign_keys=ON;")
        return connection
    except Exception as exc:
        if connection is not None:
            connection.close()
        diagnostics = {
            "label": label,
            "db_path": str(resolved),
            "parent_path": str(parent),
            "cwd": os.getcwd(),
            "home": str(Path.home()),
            "db_exists": _safe_path_check(resolved.exists),
            "parent_exists": _safe_path_check(parent.exists),
            "parent_writable": _safe_path_check(lambda: os.access(parent, os.W_OK)),
        }
        if logger is not None:
            logger.exception("Failed to open %s database", label, extra={"sqlite_diagnostics": diagnostics})
        raise sqlite3.OperationalError(
            f"Failed to open {label} database at {resolved}: {exc}. diagnostics={diagnostics}"
        ) from exc

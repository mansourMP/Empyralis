"""
Discord workspace pairing — maps Discord user IDs to Empyralis workspaces.

Mirrors Telegram's ``get_workspace_for_chat`` pattern with a persistent
SQLite table so pairings survive restarts.
"""

from __future__ import annotations

import sqlite3
import logging
from typing import Optional

LOGGER = logging.getLogger(__name__)

# ── Table DDL ──
_DDL = """
CREATE TABLE IF NOT EXISTS discord_workspace_pairings (
    discord_user_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    paired_at TEXT NOT NULL
)
"""


def _connect() -> sqlite3.Connection:
    """Connect to the auth DB (same SQLite file as users / channel_pairing)."""
    from server_modules import auth as auth_module

    connection = auth_module._connect_auth_db()
    _ensure_table(connection)
    return connection


def _ensure_table(connection: sqlite3.Connection) -> None:
    connection.execute(_DDL)


def _coerce(value: object) -> str:
    return str(value or "").strip()


# ── Public API ──


def get_workspace_for_discord_user(discord_user_id: str) -> Optional[str]:
    """Return the workspace_id paired with this Discord user, or None."""
    uid = _coerce(discord_user_id)
    if not uid:
        return None
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT workspace_id FROM discord_workspace_pairings WHERE discord_user_id = ?",
                (uid,),
            ).fetchone()
            if row:
                return _coerce(row[0])
    except Exception:
        LOGGER.exception("Failed to look up Discord workspace pairing for %s", uid)
    return None


def pair_discord_workspace(discord_user_id: str, workspace_id: str) -> None:
    """Create or update a pairing between a Discord user and workspace."""
    uid = _coerce(discord_user_id)
    wid = _coerce(workspace_id)
    if not uid or not wid:
        raise ValueError("discord_user_id and workspace_id are required")
    try:
        from server_modules.runtime_common import _utc_now_iso

        paired_at = _utc_now_iso()
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO discord_workspace_pairings (discord_user_id, workspace_id, paired_at)
                VALUES (?, ?, ?)
                ON CONFLICT(discord_user_id) DO UPDATE SET
                    workspace_id = excluded.workspace_id,
                    paired_at = excluded.paired_at
                """,
                (uid, wid, paired_at),
            )
        LOGGER.info("Paired Discord user %s → workspace %s", uid, wid)
    except Exception:
        LOGGER.exception("Failed to pair Discord user %s → workspace %s", uid, wid)
        raise


def unpair_discord_user(discord_user_id: str) -> bool:
    """Remove the pairing for a Discord user. Returns True if a row was deleted."""
    uid = _coerce(discord_user_id)
    if not uid:
        return False
    try:
        with _connect() as conn:
            cursor = conn.execute(
                "DELETE FROM discord_workspace_pairings WHERE discord_user_id = ?",
                (uid,),
            )
            removed = cursor.rowcount > 0
            if removed:
                LOGGER.info("Unpaired Discord user %s", uid)
            return removed
    except Exception:
        LOGGER.exception("Failed to unpair Discord user %s", uid)
        return False


def is_discord_user_paired(discord_user_id: str) -> bool:
    """Return True if this Discord user is paired with a workspace."""
    return get_workspace_for_discord_user(discord_user_id) is not None


def is_workspace_paired(workspace_id: str) -> bool:
    """Return True if at least one Discord user is paired with this workspace."""
    wid = _coerce(workspace_id)
    if not wid:
        return False
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM discord_workspace_pairings WHERE workspace_id = ? LIMIT 1",
                (wid,),
            ).fetchone()
            return row is not None
    except Exception:
        LOGGER.exception("Failed to check workspace pairing for %s", wid)
        return False


def unpair_workspace(workspace_id: str) -> int:
    """Remove all Discord pairings for a workspace. Returns count removed."""
    wid = _coerce(workspace_id)
    if not wid:
        return 0
    try:
        with _connect() as conn:
            cursor = conn.execute(
                "DELETE FROM discord_workspace_pairings WHERE workspace_id = ?",
                (wid,),
            )
            removed = cursor.rowcount
            if removed:
                LOGGER.info("Unpaired %d Discord user(s) from workspace %s", removed, wid)
            return removed
    except Exception:
        LOGGER.exception("Failed to unpair workspace %s", wid)
        return 0

#!/usr/bin/env python3
"""One-shot migration: local auth SQLite -> Postgres users/auth_identities/workspace_memberships.

Copies rows from ~/.empyralis/state/auth/users.db (the SQLite store
server_modules/control_plane_repository.py's identity functions --
get_user_by_email, get_user_by_id, create_local_password_account,
list_workspace_memberships_for_user -- fall back to when DATABASE_URL is
absent) into the matching Postgres tables.

Unlike the control-plane migration, this one decomposes: SQLite's `users`
table stores a password_hash directly on the user row; Postgres splits
identity into `users` (profile only, no credential) + `auth_identities`
(one row per login method). Each SQLite user becomes one `users` row plus
one `auth_identities` row with provider='empyralis_password'.

tenant_id/workspace_id for each user are resolved from their earliest
workspace_memberships row (joined through workspace_registry for tenant_id)
-- the same "home workspace" resolution get_local_auth_identity_by_email()
already uses. A user with no membership, or a membership whose workspace_id
has no workspace_registry entry, cannot be placed and is skipped, never
guessed.

Safety rules (same as scripts/migrate_sqlite_to_pg.py):
  - Idempotent: re-running is always safe.
  - Never overwrites: no UPDATE statements anywhere in this script.
  - Dry-run by default: no writes happen unless --apply is passed.
  - Every row is logged as copied / skipped-exists / skipped-missing-fk /
    skipped-error (e.g. a unique-constraint conflict on email or on
    (provider, subject) -- reported, never resolved automatically).

Usage:
    python scripts/migrate_auth_to_pg.py            # dry-run (default)
    python scripts/migrate_auth_to_pg.py --dry-run   # explicit dry-run
    python scripts/migrate_auth_to_pg.py --apply     # actually writes
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sqlite3
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

try:
    from dotenv import load_dotenv

    load_dotenv(str(Path(__file__).resolve().parent.parent / ".env"))
except Exception:
    pass

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from server_modules.control_plane_repository import LOCAL_IDENTITY_DB_FILE  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOGGER = logging.getLogger("migrate_auth_to_pg")


@dataclass
class Stats:
    copied: int = 0
    skipped_exists: int = 0
    skipped_missing_fk: int = 0
    skipped_error: int = 0
    would_copy: int = 0


def _epoch_to_dt(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    return datetime.fromtimestamp(int(value), tz=timezone.utc)


class KnownIds:
    """Same purpose as in migrate_sqlite_to_pg.py: tracks which (table, key)
    pairs exist-or-will-exist in Postgres by the end of this run. Keyed by
    one or more columns -- auth_identities dedupes on (provider, subject)
    and workspace_memberships on (tenant_id, workspace_id, user_id), not a
    single column, since this script generates a fresh synthetic id for
    both rather than preserving a SQLite id for them."""

    def __init__(self, pg_conn: asyncpg.Connection) -> None:
        self._pg_conn = pg_conn
        self._cache: dict[tuple[str, tuple[str, ...]], set[Any]] = {}

    async def contains(self, table: str, columns: tuple[str, ...], value: Any) -> bool:
        key = (table, columns)
        if key not in self._cache:
            rows = await self._pg_conn.fetch(f"SELECT {', '.join(columns)} FROM {table}")
            if len(columns) == 1:
                self._cache[key] = {r[columns[0]] for r in rows}
            else:
                self._cache[key] = {tuple(r[c] for c in columns) for r in rows}
        return value in self._cache[key]

    def remember(self, table: str, columns: tuple[str, ...], value: Any) -> None:
        self._cache.setdefault((table, columns), set()).add(value)


def load_sqlite_auth(sqlite_conn: sqlite3.Connection) -> dict[str, Any]:
    sqlite_conn.row_factory = sqlite3.Row
    users = [dict(r) for r in sqlite_conn.execute(
        "SELECT id, email, name, password_hash, created_at, avatar_url FROM users"
    )]
    memberships = [dict(r) for r in sqlite_conn.execute(
        "SELECT user_id, workspace_id, role, created_at, updated_at FROM workspace_memberships"
    )]
    registry = {
        r["workspace_id"]: r["tenant_id"]
        for r in sqlite_conn.execute("SELECT workspace_id, tenant_id FROM workspace_registry")
    }

    # Earliest membership per user = "home" workspace, matching
    # get_local_auth_identity_by_email()'s ORDER BY created_at ASC LIMIT 1.
    home_by_user: dict[str, dict[str, Any]] = {}
    for m in sorted(memberships, key=lambda r: (r["created_at"], r["workspace_id"])):
        home_by_user.setdefault(m["user_id"], m)

    return {"users": users, "memberships": memberships, "registry": registry, "home_by_user": home_by_user}


async def migrate_users(
    pg_conn: asyncpg.Connection, data: dict[str, Any], *, apply: bool, stats: Stats, known_ids: KnownIds
) -> None:
    users = data["users"]
    registry = data["registry"]
    home_by_user = data["home_by_user"]
    LOGGER.info("--- users: %d row(s) in SQLite ---", len(users))

    for u in users:
        user_id = u["id"]
        home = home_by_user.get(user_id)
        if home is None:
            LOGGER.warning("SKIP  users id=%s email=%s: no workspace membership, cannot resolve home tenant/workspace", user_id, u["email"])
            stats.skipped_missing_fk += 1
            continue
        workspace_id = home["workspace_id"]
        tenant_id = registry.get(workspace_id)
        if not tenant_id:
            LOGGER.warning("SKIP  users id=%s email=%s: workspace_id=%s has no workspace_registry entry", user_id, u["email"], workspace_id)
            stats.skipped_missing_fk += 1
            continue

        if not apply:
            exists = await known_ids.contains("users", ("id",), user_id)
            if exists:
                LOGGER.info("DRY   users id=%s email=%s: already exists in Postgres, would skip", user_id, u["email"])
                stats.skipped_exists += 1
            else:
                LOGGER.info("DRY   users id=%s email=%s: would copy (tenant=%s workspace=%s)", user_id, u["email"], tenant_id, workspace_id)
                stats.would_copy += 1
                known_ids.remember("users", ("id",), user_id)
                known_ids.remember("users", ("email",), u["email"].strip().lower())
            continue

        created_at = _epoch_to_dt(u["created_at"])
        try:
            result = await pg_conn.fetchval(
                """
                INSERT INTO users (id, tenant_id, workspace_id, email, display_name, avatar_url, status, metadata, created_at, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, 'active', '{}'::jsonb, $7, $7)
                ON CONFLICT (id) DO NOTHING
                RETURNING id
                """,
                user_id, tenant_id, workspace_id, u["email"].strip().lower(), u["name"], u["avatar_url"], created_at,
            )
        except asyncpg.PostgresError as exc:
            LOGGER.warning("SKIP  users id=%s email=%s: Postgres rejected the row: %s", user_id, u["email"], exc)
            stats.skipped_error += 1
            continue

        known_ids.remember("users", ("id",), user_id)
        if result is not None:
            LOGGER.info("COPY  users id=%s email=%s", user_id, u["email"])
            stats.copied += 1
            known_ids.remember("users", ("email",), u["email"].strip().lower())
        else:
            LOGGER.info("SKIP  users id=%s email=%s: already exists in Postgres", user_id, u["email"])
            stats.skipped_exists += 1


async def migrate_password_identities(
    pg_conn: asyncpg.Connection, data: dict[str, Any], *, apply: bool, stats: Stats, known_ids: KnownIds
) -> None:
    users = data["users"]
    registry = data["registry"]
    home_by_user = data["home_by_user"]
    LOGGER.info("--- auth_identities (empyralis_password): %d candidate row(s) from SQLite users ---", len(users))

    for u in users:
        user_id = u["id"]
        subject = u["email"].strip().lower()

        if not await known_ids.contains("users", ("id",), user_id):
            LOGGER.warning("SKIP  auth_identities user_id=%s subject=%s: missing required FK -> users (user row wasn't migrated)", user_id, subject)
            stats.skipped_missing_fk += 1
            continue

        home = home_by_user.get(user_id)
        tenant_id = registry.get(home["workspace_id"]) if home else None
        workspace_id = home["workspace_id"] if home else None

        if not apply:
            exists = await known_ids.contains("auth_identities", ("provider", "subject"), ("empyralis_password", subject))
            if exists:
                LOGGER.info("DRY   auth_identities subject=%s: already exists in Postgres, would skip", subject)
                stats.skipped_exists += 1
            else:
                LOGGER.info("DRY   auth_identities subject=%s: would copy", subject)
                stats.would_copy += 1
                known_ids.remember("auth_identities", ("provider", "subject"), ("empyralis_password", subject))
            continue

        created_at = _epoch_to_dt(u["created_at"])
        try:
            result = await pg_conn.fetchval(
                """
                INSERT INTO auth_identities (
                    id, tenant_id, workspace_id, user_id, provider, subject, password_hash,
                    identity_role, label, status, is_primary, metadata, created_at, updated_at
                ) VALUES (
                    $1, $2, $3, $4, 'empyralis_password', $5, $6,
                    'account_access', 'Email and password', 'active', FALSE, '{}'::jsonb, $7, $7
                )
                ON CONFLICT (provider, subject) DO NOTHING
                RETURNING id
                """,
                str(uuid.uuid4()), tenant_id, workspace_id, user_id, subject, u["password_hash"], created_at,
            )
        except asyncpg.PostgresError as exc:
            LOGGER.warning("SKIP  auth_identities subject=%s: Postgres rejected the row: %s", subject, exc)
            stats.skipped_error += 1
            continue

        if result is not None:
            LOGGER.info("COPY  auth_identities subject=%s (user_id=%s)", subject, user_id)
            stats.copied += 1
            known_ids.remember("auth_identities", ("provider", "subject"), ("empyralis_password", subject))
        else:
            LOGGER.info("SKIP  auth_identities subject=%s: already exists in Postgres (provider,subject conflict)", subject)
            stats.skipped_exists += 1


async def migrate_memberships(
    pg_conn: asyncpg.Connection, data: dict[str, Any], *, apply: bool, stats: Stats, known_ids: KnownIds
) -> None:
    memberships = data["memberships"]
    registry = data["registry"]
    LOGGER.info("--- workspace_memberships: %d row(s) in SQLite ---", len(memberships))

    for m in memberships:
        user_id, workspace_id, role = m["user_id"], m["workspace_id"], m["role"]
        tenant_id = registry.get(workspace_id)
        if not tenant_id:
            LOGGER.warning("SKIP  workspace_memberships user_id=%s workspace_id=%s: no workspace_registry entry", user_id, workspace_id)
            stats.skipped_missing_fk += 1
            continue
        if not await known_ids.contains("users", ("id",), user_id):
            LOGGER.warning("SKIP  workspace_memberships user_id=%s workspace_id=%s: missing required FK -> users", user_id, workspace_id)
            stats.skipped_missing_fk += 1
            continue

        dedupe_key = (tenant_id, workspace_id, user_id)
        if not apply:
            exists = await known_ids.contains("workspace_memberships", ("tenant_id", "workspace_id", "user_id"), dedupe_key)
            if exists:
                LOGGER.info("DRY   workspace_memberships %s: already exists in Postgres, would skip", dedupe_key)
                stats.skipped_exists += 1
            else:
                LOGGER.info("DRY   workspace_memberships %s role=%s: would copy", dedupe_key, role)
                stats.would_copy += 1
                known_ids.remember("workspace_memberships", ("tenant_id", "workspace_id", "user_id"), dedupe_key)
            continue

        created_at = _epoch_to_dt(m["created_at"])
        updated_at = _epoch_to_dt(m["updated_at"]) or created_at
        try:
            result = await pg_conn.fetchval(
                """
                INSERT INTO workspace_memberships (id, tenant_id, workspace_id, user_id, role, status, metadata, created_at, updated_at)
                VALUES ($1, $2, $3, $4, $5, 'active', '{}'::jsonb, $6, $7)
                ON CONFLICT (tenant_id, workspace_id, user_id) DO NOTHING
                RETURNING id
                """,
                str(uuid.uuid4()), tenant_id, workspace_id, user_id, role, created_at, updated_at,
            )
        except asyncpg.PostgresError as exc:
            LOGGER.warning("SKIP  workspace_memberships %s: Postgres rejected the row: %s", dedupe_key, exc)
            stats.skipped_error += 1
            continue

        known_ids.remember("workspace_memberships", ("tenant_id", "workspace_id", "user_id"), dedupe_key)
        if result is not None:
            LOGGER.info("COPY  workspace_memberships %s role=%s", dedupe_key, role)
            stats.copied += 1
        else:
            LOGGER.info("SKIP  workspace_memberships %s: already exists in Postgres", dedupe_key)
            stats.skipped_exists += 1


async def run(apply: bool) -> int:
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        LOGGER.error("DATABASE_URL is not set -- nothing to migrate into. Aborting.")
        return 1
    if not LOCAL_IDENTITY_DB_FILE.exists():
        LOGGER.error("SQLite auth file not found at %s. Aborting.", LOCAL_IDENTITY_DB_FILE)
        return 1

    LOGGER.info("Mode: %s", "APPLY (writing to Postgres)" if apply else "DRY-RUN (no writes)")
    LOGGER.info("SQLite source: %s", LOCAL_IDENTITY_DB_FILE)
    LOGGER.info("Postgres target: %s", dsn.split("@")[-1] if "@" in dsn else dsn)

    sqlite_conn = sqlite3.connect(str(LOCAL_IDENTITY_DB_FILE))
    pg_conn = await asyncpg.connect(dsn=dsn)
    known_ids = KnownIds(pg_conn)

    stats_by_step: dict[str, Stats] = {}
    try:
        data = load_sqlite_auth(sqlite_conn)

        stats_by_step["users"] = Stats()
        await migrate_users(pg_conn, data, apply=apply, stats=stats_by_step["users"], known_ids=known_ids)

        stats_by_step["auth_identities"] = Stats()
        await migrate_password_identities(pg_conn, data, apply=apply, stats=stats_by_step["auth_identities"], known_ids=known_ids)

        stats_by_step["workspace_memberships"] = Stats()
        await migrate_memberships(pg_conn, data, apply=apply, stats=stats_by_step["workspace_memberships"], known_ids=known_ids)
    finally:
        sqlite_conn.close()
        await pg_conn.close()

    LOGGER.info("=" * 60)
    LOGGER.info("SUMMARY (%s)", "APPLY" if apply else "DRY-RUN")
    totals = Stats()
    for step, s in stats_by_step.items():
        totals.copied += s.copied
        totals.would_copy += s.would_copy
        totals.skipped_exists += s.skipped_exists
        totals.skipped_missing_fk += s.skipped_missing_fk
        totals.skipped_error += s.skipped_error
        LOGGER.info(
            "  %-22s copied=%-3d would_copy=%-3d skipped_exists=%-3d skipped_missing_fk=%-3d skipped_error=%-3d",
            step, s.copied, s.would_copy, s.skipped_exists, s.skipped_missing_fk, s.skipped_error,
        )
    LOGGER.info(
        "  %-22s copied=%-3d would_copy=%-3d skipped_exists=%-3d skipped_missing_fk=%-3d skipped_error=%-3d",
        "TOTAL", totals.copied, totals.would_copy, totals.skipped_exists, totals.skipped_missing_fk, totals.skipped_error,
    )
    LOGGER.info("=" * 60)
    if not apply and totals.would_copy > 0:
        LOGGER.info("Dry-run only -- re-run with --apply to write %d row(s).", totals.would_copy)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Actually write to Postgres.")
    parser.add_argument("--dry-run", action="store_true", help="Explicit dry-run (default behavior).")
    args = parser.parse_args()
    if args.apply and args.dry_run:
        parser.error("--dry-run and --apply are mutually exclusive.")
    return asyncio.run(run(apply=bool(args.apply)))


if __name__ == "__main__":
    raise SystemExit(main())

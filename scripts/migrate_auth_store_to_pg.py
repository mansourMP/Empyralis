#!/usr/bin/env python3
"""One-shot migration: the 11 auth-store tables SQLite -> Postgres (Phase 1C).

Moves rows from ~/.empyralis/state/auth/users.db into the Postgres mirrors
created by AUTH_STORE_SCHEMA_SQL:

    workspace_registry, workspace_policies, tenant_policies,
    tenant_enterprise_settings, user_enterprise_security, user_auth_methods,
    user_provider_connections, user_identity_versions, auth_sessions,
    user_devices, auth_session_refresh_tokens

Rules (same family as the other migrate_* scripts):
  - Dry-run by default; --apply to write.
  - INSERT ... ON CONFLICT (pk) DO NOTHING -- never overwrites an existing row.
  - Per-row error handling: a row Postgres rejects (e.g. a user_id / session_id
    FK that isn't present) is skipped and logged, never guessed.
  - EXPIRED SESSIONS ARE NOT COPIED: auth_sessions / auth_session_refresh_tokens
    rows with expires_at <= now are dropped (dead sessions), counted separately.
  - mansurao886@gmail.com is left untouched: its SQLite user row was not
    migrated in Phase 1B (email conflict), so its user_id is absent from
    Postgres users, and every FK-bearing auth row for it is skipped here.

Order respects FKs: user_id -> users (migrated in Phase 1B) must exist;
auth_sessions is migrated before auth_session_refresh_tokens (which FK to it).

Usage:
    python scripts/migrate_auth_store_to_pg.py            # dry-run (default)
    python scripts/migrate_auth_store_to_pg.py --apply     # actually writes
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from dotenv import load_dotenv

    load_dotenv(str(Path(__file__).resolve().parent.parent / ".env"))
except Exception:
    pass

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from server_modules.control_plane_repository import LOCAL_IDENTITY_DB_FILE  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOGGER = logging.getLogger("migrate_auth_store_to_pg")


@dataclass
class TableSpec:
    name: str
    pk: str
    columns: List[str]
    user_fk_col: Optional[str] = None       # column that must exist in users(id)
    session_fk_col: Optional[str] = None    # column that must exist in auth_sessions(session_id)
    expiry_col: Optional[str] = None        # rows with <col> <= now are expired -> skipped


SPECS: List[TableSpec] = [
    TableSpec("workspace_registry", "workspace_id",
              ["workspace_id", "tenant_id", "name", "workspace_type", "metadata_json", "created_at", "updated_at"]),
    TableSpec("workspace_policies", "workspace_id",
              ["workspace_id", "capability_allow_json", "capability_deny_json", "dangerous_allow_json",
               "dangerous_deny_json", "connector_allow_json", "connector_deny_json", "machine_enrollment_scope",
               "trusted_owner_machine_ids_json", "updated_at"]),
    TableSpec("tenant_policies", "tenant_id",
              ["tenant_id", "capability_allow_json", "capability_deny_json", "dangerous_allow_json",
               "dangerous_deny_json", "connector_allow_json", "connector_deny_json", "machine_enrollment_scope",
               "updated_at"]),
    TableSpec("tenant_enterprise_settings", "tenant_id",
              ["tenant_id", "sso_enabled", "sso_provider", "sso_issuer_url", "sso_metadata_url", "sso_client_id",
               "sso_audience", "sso_domains_json", "sso_scopes_json", "mfa_required", "mfa_methods_json",
               "mfa_grace_period_hours", "scim_enabled", "scim_base_url", "scim_provisioning_mode",
               "scim_last_token_rotation_at", "updated_at"]),
    TableSpec("user_enterprise_security", "user_id",
              ["user_id", "mfa_enrolled", "mfa_method", "mfa_enrolled_at", "mfa_last_verified_at", "auth_provider",
               "sso_subject", "provisioning_source", "external_id", "last_provisioned_at", "updated_at"],
              user_fk_col="user_id"),
    TableSpec("user_auth_methods", "id",
              ["id", "user_id", "method_type", "provider", "subject", "label", "status", "is_primary", "can_recover",
               "metadata_json", "created_at", "updated_at"],
              user_fk_col="user_id"),
    TableSpec("user_provider_connections", "id",
              ["id", "user_id", "provider", "workspace_id", "status", "label", "external_account_id", "metadata_json",
               "created_at", "updated_at"],
              user_fk_col="user_id"),
    TableSpec("user_identity_versions", "user_id",
              ["user_id", "membership_version", "auth_version", "provider_scope_version", "updated_at"],
              user_fk_col="user_id"),
    TableSpec("auth_sessions", "session_id",
              ["session_id", "user_id", "channel", "device_id", "runtime_id", "trust_state", "status",
               "session_family_id", "metadata_json", "created_at", "updated_at", "last_seen_at", "expires_at",
               "revoked_at", "revoked_reason"],
              user_fk_col="user_id", expiry_col="expires_at"),
    TableSpec("user_devices", "device_id",
              ["device_id", "user_id", "workspace_id", "channel", "display_name", "platform", "trust_state", "status",
               "session_binding_required", "metadata_json", "linked_at", "updated_at", "last_seen_at", "revoked_at",
               "revoked_reason"],
              user_fk_col="user_id"),
    TableSpec("auth_session_refresh_tokens", "session_id",
              ["session_id", "user_id", "token_hash", "created_at", "updated_at", "expires_at", "rotated_at",
               "revoked_at", "revoked_reason"],
              user_fk_col="user_id", session_fk_col="session_id", expiry_col="expires_at"),
]


@dataclass
class Stats:
    copied: int = 0
    skipped_exists: int = 0
    skipped_missing_fk: int = 0
    skipped_expired: int = 0
    skipped_error: int = 0
    would_copy: int = 0


async def migrate_table(
    sqlite_conn: sqlite3.Connection,
    pg_conn: asyncpg.Connection,
    spec: TableSpec,
    *,
    apply: bool,
    now_ts: int,
    pg_user_ids: set,
    migrated_session_ids: set,
    stats: Stats,
) -> None:
    sqlite_conn.row_factory = sqlite3.Row
    rows = sqlite_conn.execute(f"SELECT {', '.join(spec.columns)} FROM {spec.name}").fetchall()
    LOGGER.info("--- %s: %d row(s) in SQLite ---", spec.name, len(rows))

    placeholders = ", ".join(f"${i + 1}" for i in range(len(spec.columns)))
    sql = (
        f"INSERT INTO {spec.name} ({', '.join(spec.columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT ({spec.pk}) DO NOTHING RETURNING {spec.pk}"
    )

    for row in rows:
        rd = dict(row)
        pk_val = rd[spec.pk]

        if spec.expiry_col is not None:
            exp = rd.get(spec.expiry_col)
            if exp is not None and int(exp) <= now_ts:
                stats.skipped_expired += 1
                continue

        if spec.user_fk_col is not None and rd.get(spec.user_fk_col) not in pg_user_ids:
            LOGGER.warning("SKIP  %s %s=%s: user_id=%s not in Postgres users", spec.name, spec.pk, pk_val, rd.get(spec.user_fk_col))
            stats.skipped_missing_fk += 1
            continue

        if spec.session_fk_col is not None and rd.get(spec.session_fk_col) not in migrated_session_ids:
            stats.skipped_missing_fk += 1
            continue

        if not apply:
            stats.would_copy += 1
            # Track sessions that WOULD migrate so refresh-token FK checks in the
            # same dry-run are faithful (they FK to auth_sessions).
            if spec.name == "auth_sessions":
                migrated_session_ids.add(str(pk_val))
            continue

        try:
            result = await pg_conn.fetchval(sql, *[rd[c] for c in spec.columns])
        except asyncpg.PostgresError as exc:
            LOGGER.warning("SKIP  %s %s=%s: Postgres rejected the row: %s", spec.name, spec.pk, pk_val, exc)
            stats.skipped_error += 1
            continue
        if result is not None:
            stats.copied += 1
            if spec.name == "auth_sessions":
                migrated_session_ids.add(str(pk_val))
        else:
            stats.skipped_exists += 1
            if spec.name == "auth_sessions":
                migrated_session_ids.add(str(pk_val))


async def run(apply: bool) -> int:
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        LOGGER.error("DATABASE_URL is not set. Aborting.")
        return 1
    if not LOCAL_IDENTITY_DB_FILE.exists():
        LOGGER.error("SQLite auth file not found at %s. Aborting.", LOCAL_IDENTITY_DB_FILE)
        return 1

    now_ts = int(time.time())
    LOGGER.info("Mode: %s", "APPLY (writing to Postgres)" if apply else "DRY-RUN (no writes)")
    LOGGER.info("SQLite source: %s", LOCAL_IDENTITY_DB_FILE)
    LOGGER.info("Postgres target: %s", dsn.split("@")[-1] if "@" in dsn else dsn)

    sqlite_conn = sqlite3.connect(str(LOCAL_IDENTITY_DB_FILE))
    pg_conn = await asyncpg.connect(dsn=dsn)

    pg_user_ids = {r["id"] for r in await pg_conn.fetch("SELECT id FROM users")}
    LOGGER.info("Postgres users present: %d (rows whose user_id is absent are skipped)", len(pg_user_ids))
    # Pre-seed migrated_session_ids with sessions already in Postgres so refresh
    # tokens for pre-existing sessions still migrate on a re-run.
    migrated_session_ids = {str(r["session_id"]) for r in await pg_conn.fetch("SELECT session_id FROM auth_sessions")}

    stats_by_table: Dict[str, Stats] = {}
    try:
        for spec in SPECS:
            stats = Stats()
            stats_by_table[spec.name] = stats
            await migrate_table(
                sqlite_conn, pg_conn, spec, apply=apply, now_ts=now_ts,
                pg_user_ids=pg_user_ids, migrated_session_ids=migrated_session_ids, stats=stats,
            )
    finally:
        sqlite_conn.close()
        await pg_conn.close()

    LOGGER.info("=" * 78)
    LOGGER.info("SUMMARY (%s)", "APPLY" if apply else "DRY-RUN")
    tot = Stats()
    for spec in SPECS:
        s = stats_by_table[spec.name]
        for f in ("copied", "would_copy", "skipped_exists", "skipped_missing_fk", "skipped_expired", "skipped_error"):
            setattr(tot, f, getattr(tot, f) + getattr(s, f))
        LOGGER.info(
            "  %-28s copied=%-4d would=%-4d exists=%-4d missing_fk=%-4d expired=%-4d error=%-3d",
            spec.name, s.copied, s.would_copy, s.skipped_exists, s.skipped_missing_fk, s.skipped_expired, s.skipped_error,
        )
    LOGGER.info(
        "  %-28s copied=%-4d would=%-4d exists=%-4d missing_fk=%-4d expired=%-4d error=%-3d",
        "TOTAL", tot.copied, tot.would_copy, tot.skipped_exists, tot.skipped_missing_fk, tot.skipped_expired, tot.skipped_error,
    )
    LOGGER.info("=" * 78)
    if not apply and tot.would_copy:
        LOGGER.info("Dry-run only -- re-run with --apply to write %d row(s) (%d expired sessions will be dropped).",
                    tot.would_copy, tot.skipped_expired)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Actually write to Postgres.")
    parser.add_argument("--dry-run", action="store_true", help="Explicit dry-run (default).")
    args = parser.parse_args()
    if args.apply and args.dry_run:
        parser.error("--dry-run and --apply are mutually exclusive.")
    return asyncio.run(run(apply=bool(args.apply)))


if __name__ == "__main__":
    raise SystemExit(main())

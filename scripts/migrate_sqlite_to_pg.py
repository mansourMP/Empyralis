#!/usr/bin/env python3
"""One-shot migration: local control-plane SQLite -> Postgres.

Copies rows from ~/.empyralis/state/control-plane/control-plane.sqlite3
(the SQLite store server_modules/control_plane_repository.py falls back to
when DATABASE_URL is absent) into the Postgres tables of the same name.

Safety rules:
  - Idempotent: re-running is always safe. Uses INSERT ... ON CONFLICT (id)
    DO NOTHING, so a row that already exists in Postgres is never touched.
  - Never overwrites: no UPDATE statements anywhere in this script.
  - Dry-run by default: no writes happen unless --apply is passed.
  - Every row is logged as copied / skipped-exists / skipped-missing-fk.

Usage:
    python scripts/migrate_sqlite_to_pg.py            # dry-run (default)
    python scripts/migrate_sqlite_to_pg.py --dry-run   # explicit dry-run
    python scripts/migrate_sqlite_to_pg.py --apply     # actually writes
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sqlite3
import sys
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
from server_modules.control_plane_repository import LOCAL_CONTROL_PLANE_DB_FILE  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOGGER = logging.getLogger("migrate_sqlite_to_pg")


@dataclass
class TableSpec:
    columns: list[str]
    column_types: dict[str, str] = field(default_factory=dict)
    required_fks: list[tuple[str, str]] = field(default_factory=list)
    optional_fks: list[tuple[str, str]] = field(default_factory=list)


# Migration order matters: a table must come after every table it has a
# required foreign key into. workflow_versions is handled separately (see
# migrate_workflow_versions) because its Postgres schema needs columns
# (version_number, a workflow_definitions FK) that don't exist in the SQLite
# source at all -- there's no safe value to fabricate, so rows are reported,
# never guessed.
TABLE_SPECS: dict[str, TableSpec] = {
    "agent_definitions": TableSpec(
        columns=[
            "id", "tenant_id", "workspace_id", "slug", "name", "description",
            "agent_kind", "visibility", "status", "category", "icon",
            "metadata", "created_at", "updated_at",
        ],
        column_types={"metadata": "jsonb", "created_at": "timestamptz", "updated_at": "timestamptz"},
    ),
    "runtime_profiles": TableSpec(
        columns=[
            "id", "tenant_id", "workspace_id", "slug", "label", "runtime_class",
            "placement_mode", "runtime_id", "machine_id", "default_execution_target",
            "status", "metadata", "created_at", "updated_at",
        ],
        column_types={"metadata": "jsonb", "created_at": "timestamptz", "updated_at": "timestamptz"},
    ),
    "agent_definition_versions": TableSpec(
        # Postgres has no `updated_at` on this table (SQLite does) -- dropped intentionally.
        columns=[
            "id", "tenant_id", "workspace_id", "agent_definition_id", "version_number",
            "status", "manifest", "capability_manifest", "policy_manifest",
            "placement_manifest", "metadata", "created_at",
        ],
        column_types={
            "manifest": "jsonb", "capability_manifest": "jsonb", "policy_manifest": "jsonb",
            "placement_manifest": "jsonb", "metadata": "jsonb", "created_at": "timestamptz",
        },
        required_fks=[("agent_definition_id", "agent_definitions")],
    ),
    "workspace_agent_installs": TableSpec(
        columns=[
            "id", "tenant_id", "workspace_id", "agent_definition_id", "agent_definition_version_id",
            "installed_by_user_id", "install_scope", "owner_user_id", "thread_id", "label",
            "status", "enabled", "runtime_profile_id", "compiled_workflow_version_id",
            "root_folder_uri", "tool_toggles", "folder_grants", "connector_bindings",
            "memory_scope_overrides", "policy_context_overrides", "metadata",
            "created_at", "updated_at", "enabled_tools", "enabled_connectors",
            "channel_bindings", "subagents_enabled", "hardware_access",
        ],
        column_types={
            "enabled": "boolean", "subagents_enabled": "boolean",
            "tool_toggles": "jsonb", "folder_grants": "jsonb", "connector_bindings": "jsonb",
            "memory_scope_overrides": "jsonb", "policy_context_overrides": "jsonb",
            "metadata": "jsonb", "channel_bindings": "jsonb",
            "enabled_tools": "text[]", "enabled_connectors": "text[]",
            "created_at": "timestamptz", "updated_at": "timestamptz",
        },
        required_fks=[
            ("agent_definition_id", "agent_definitions"),
            ("agent_definition_version_id", "agent_definition_versions"),
        ],
        # thread_id references agent_threads, which the SQLite control-plane
        # store doesn't contain at all -- always nulled out if set.
        optional_fks=[
            ("runtime_profile_id", "runtime_profiles"),
            ("compiled_workflow_version_id", "workflow_versions"),
            ("thread_id", "agent_threads"),
        ],
    ),
    "agent_runtime_profiles": TableSpec(
        # NOTE: the SQLite source table has no runtime_profile_id column (Postgres
        # does, nullable) -- it's simply omitted here and left NULL on insert.
        columns=[
            "id", "tenant_id", "workspace_id", "agent_install_id",
            "runtime_mode", "metadata", "created_at", "updated_at",
        ],
        column_types={"metadata": "jsonb", "created_at": "timestamptz", "updated_at": "timestamptz"},
        required_fks=[("agent_install_id", "workspace_agent_installs")],
    ),
    "deployed_agents": TableSpec(
        columns=[
            "id", "tenant_id", "owner_workspace_id", "backing_install_id", "created_by_user_id",
            "name", "avatar", "persona", "system_prompt", "deployment_state", "channels",
            "knowledge_sources", "runtime_target", "billing_plan", "metadata", "operational_state",
            "last_deployed_at", "last_paused_at", "created_at", "updated_at", "is_public",
            "quality_stars", "cost_tier", "category",
        ],
        column_types={
            "channels": "jsonb", "knowledge_sources": "jsonb", "metadata": "jsonb",
            "operational_state": "jsonb", "is_public": "boolean",
            "last_deployed_at": "timestamptz", "last_paused_at": "timestamptz",
            "created_at": "timestamptz", "updated_at": "timestamptz",
        },
        required_fks=[("backing_install_id", "workspace_agent_installs")],
    ),
}

MIGRATION_ORDER = [
    "agent_definitions",
    "runtime_profiles",
    "agent_definition_versions",
    "workspace_agent_installs",
    "agent_runtime_profiles",
    "deployed_agents",
]


@dataclass
class Stats:
    copied: int = 0
    skipped_exists: int = 0
    skipped_missing_fk: int = 0
    skipped_error: int = 0
    would_copy: int = 0


def _convert(value: Any, kind: Optional[str]) -> Any:
    if value is None:
        return None
    if kind == "boolean":
        return bool(value)
    if kind == "jsonb":
        if isinstance(value, str):
            json.loads(value)  # validate; raise loudly on malformed JSON rather than insert garbage
            return value
        return json.dumps(value)
    if kind == "text[]":
        if isinstance(value, str):
            parsed = json.loads(value)
            return list(parsed) if isinstance(parsed, list) else None
        return value
    if kind == "timestamptz":
        # asyncpg's binary protocol requires an actual datetime for a
        # timestamptz-typed parameter -- a raw string fails even with an
        # explicit ::timestamptz cast in the SQL text. SQLite stores these
        # as TEXT (either "YYYY-MM-DD HH:MM:SS" from datetime('now'), or
        # full ISO8601 with a Z/offset suffix); both are naive-or-aware
        # inputs that fromisoformat() handles, so just fill in UTC when
        # there's no tzinfo (SQLite's datetime('now') is UTC).
        if isinstance(value, str):
            parsed = datetime.fromisoformat(value.strip())
            return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
        return value
    return value


def _cast_suffix(kind: Optional[str]) -> str:
    if kind == "jsonb":
        return "::jsonb"
    if kind == "timestamptz":
        return "::timestamptz"
    if kind == "boolean":
        return "::boolean"
    return ""


class KnownIds:
    """Tracks, per table, which ids exist-or-will-exist in Postgres by the end
    of this run. Seeded from live Postgres per table on first use, then kept
    in sync as rows are copied (or, in dry-run, as rows would be copied) --
    so a later table's FK check against an earlier table in MIGRATION_ORDER
    correctly sees rows that haven't been committed yet only because we
    haven't reached them, not because they're actually missing."""

    def __init__(self, pg_conn: asyncpg.Connection) -> None:
        self._pg_conn = pg_conn
        self._cache: dict[str, set[str]] = {}

    async def contains(self, table: str, row_id: str) -> bool:
        if table not in self._cache:
            rows = await self._pg_conn.fetch(f"SELECT id FROM {table}")
            self._cache[table] = {r["id"] for r in rows}
        return row_id in self._cache[table]

    def remember(self, table: str, row_id: str) -> None:
        self._cache.setdefault(table, set()).add(row_id)


async def migrate_table(
    sqlite_conn: sqlite3.Connection,
    pg_conn: asyncpg.Connection,
    table: str,
    spec: TableSpec,
    *,
    apply: bool,
    stats: Stats,
    known_ids: KnownIds,
) -> None:
    cur = sqlite_conn.execute(f"SELECT {', '.join(spec.columns)} FROM {table}")
    col_names = [d[0] for d in cur.description]
    rows = [dict(zip(col_names, raw)) for raw in cur.fetchall()]
    LOGGER.info("--- %s: %d row(s) in SQLite ---", table, len(rows))

    for row in rows:
        row_id = row["id"]

        skip_reason = None
        for fk_col, fk_table in spec.required_fks:
            fk_val = row.get(fk_col)
            if not fk_val or not await known_ids.contains(fk_table, fk_val):
                skip_reason = f"missing required FK {fk_col}={fk_val!r} -> {fk_table}"
                break
        if skip_reason:
            LOGGER.warning("SKIP  %s id=%s: %s", table, row_id, skip_reason)
            stats.skipped_missing_fk += 1
            continue

        for fk_col, fk_table in spec.optional_fks:
            fk_val = row.get(fk_col)
            if fk_val and not await known_ids.contains(fk_table, fk_val):
                LOGGER.warning(
                    "NULL  %s.%s for id=%s: %s=%r not found in Postgres %s, dropping reference",
                    table, fk_col, row_id, fk_col, fk_val, fk_table,
                )
                row[fk_col] = None

        if not apply:
            exists = await known_ids.contains(table, row_id)
            if exists:
                LOGGER.info("DRY   %s id=%s: already exists in Postgres, would skip", table, row_id)
                stats.skipped_exists += 1
            else:
                LOGGER.info("DRY   %s id=%s: would copy", table, row_id)
                stats.would_copy += 1
                known_ids.remember(table, row_id)
            continue

        values = [_convert(row[c], spec.column_types.get(c)) for c in spec.columns]
        placeholders = ", ".join(
            f"${i + 1}{_cast_suffix(spec.column_types.get(c))}" for i, c in enumerate(spec.columns)
        )
        sql = (
            f"INSERT INTO {table} ({', '.join(spec.columns)}) VALUES ({placeholders}) "
            "ON CONFLICT (id) DO NOTHING RETURNING id"
        )
        try:
            result = await pg_conn.fetchval(sql, *values)
        except asyncpg.PostgresError as exc:
            # A constraint other than the id primary key (e.g. a unique
            # (tenant_id, workspace_id, slug) index colliding with a
            # pre-existing Postgres row under a *different* id) -- this is
            # pre-existing data drift between the two stores, not something
            # to guess a resolution for. Skip this row, keep going.
            LOGGER.warning("SKIP  %s id=%s: Postgres rejected the row: %s", table, row_id, exc)
            stats.skipped_error += 1
            continue
        if result is not None:
            known_ids.remember(table, row_id)
            LOGGER.info("COPY  %s id=%s", table, row_id)
            stats.copied += 1
        else:
            known_ids.remember(table, row_id)
            LOGGER.info("SKIP  %s id=%s: already exists in Postgres", table, row_id)
            stats.skipped_exists += 1


def migrate_workflow_versions(sqlite_conn: sqlite3.Connection, stats: Stats) -> None:
    """workflow_versions needs special handling: Postgres requires version_number
    (no default) and a NOT NULL FK to workflow_definitions, neither of which the
    SQLite control-plane schema carries. Rather than fabricate values, report."""
    cur = sqlite_conn.execute("SELECT id FROM workflow_versions")
    rows = cur.fetchall()
    LOGGER.info("--- workflow_versions: %d row(s) in SQLite ---", len(rows))
    for (row_id,) in rows:
        LOGGER.warning(
            "SKIP  workflow_versions id=%s: SQLite schema lacks version_number and a "
            "workflow_definitions FK target that Postgres requires -- needs a manual "
            "decision, not migrated automatically",
            row_id,
        )
        stats.skipped_missing_fk += 1


async def run(apply: bool) -> int:
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        LOGGER.error("DATABASE_URL is not set -- nothing to migrate into. Aborting.")
        return 1

    if not LOCAL_CONTROL_PLANE_DB_FILE.exists():
        LOGGER.error("SQLite control-plane file not found at %s. Aborting.", LOCAL_CONTROL_PLANE_DB_FILE)
        return 1

    LOGGER.info("Mode: %s", "APPLY (writing to Postgres)" if apply else "DRY-RUN (no writes)")
    LOGGER.info("SQLite source: %s", LOCAL_CONTROL_PLANE_DB_FILE)
    LOGGER.info("Postgres target: %s", dsn.split("@")[-1] if "@" in dsn else dsn)

    sqlite_conn = sqlite3.connect(str(LOCAL_CONTROL_PLANE_DB_FILE))
    sqlite_conn.row_factory = None
    pg_conn = await asyncpg.connect(dsn=dsn)

    known_ids = KnownIds(pg_conn)
    stats_by_table: dict[str, Stats] = {}
    try:
        for table in MIGRATION_ORDER:
            spec = TABLE_SPECS[table]
            stats = Stats()
            stats_by_table[table] = stats
            await migrate_table(sqlite_conn, pg_conn, table, spec, apply=apply, stats=stats, known_ids=known_ids)

        workflow_stats = Stats()
        stats_by_table["workflow_versions"] = workflow_stats
        migrate_workflow_versions(sqlite_conn, workflow_stats)
    finally:
        sqlite_conn.close()
        await pg_conn.close()

    LOGGER.info("=" * 60)
    LOGGER.info("SUMMARY (%s)", "APPLY" if apply else "DRY-RUN")
    total_copied = total_would = total_exists = total_fk = total_error = 0
    for table in [*MIGRATION_ORDER, "workflow_versions"]:
        s = stats_by_table[table]
        total_copied += s.copied
        total_would += s.would_copy
        total_exists += s.skipped_exists
        total_fk += s.skipped_missing_fk
        total_error += s.skipped_error
        LOGGER.info(
            "  %-28s copied=%-3d would_copy=%-3d skipped_exists=%-3d skipped_missing_fk=%-3d skipped_error=%-3d",
            table, s.copied, s.would_copy, s.skipped_exists, s.skipped_missing_fk, s.skipped_error,
        )
    LOGGER.info(
        "  %-28s copied=%-3d would_copy=%-3d skipped_exists=%-3d skipped_missing_fk=%-3d skipped_error=%-3d",
        "TOTAL", total_copied, total_would, total_exists, total_fk, total_error,
    )
    LOGGER.info("=" * 60)
    if not apply and total_would > 0:
        LOGGER.info("Dry-run only -- re-run with --apply to write %d row(s).", total_would)
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

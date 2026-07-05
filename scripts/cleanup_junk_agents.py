#!/usr/bin/env python3
"""One-shot cleanup: remove 4 confirmed-junk agent installs (and the rows that
exist only to serve them) from BOTH Postgres and the control-plane SQLite file,
so they never resurface even if the app falls back to SQLite.

Owner-confirmed junk installs (Phase 1C):
    ainstall_ea8cb319ddbe484a  (sfv)
    ainstall_b0bacf554da94555  (jhb)
    ainstall_699e01f23ce1468d  (Provider Test)
    ainstall_47f0c1613c424938  (Fix-1 Playwright Agent)

What gets deleted:
  - The 4 workspace_agent_installs rows (in Postgres, ON DELETE CASCADE / SET
    NULL handles every dependent table automatically; in SQLite there are no FK
    constraints, so this script deletes the one dependent table that carries an
    agent_install_id -- agent_runtime_profiles -- explicitly first).
  - The shared agent_definition + its single version that exist ONLY to serve
    the 4 (agentdef_default_ws-1_fleet-specialist[_v1]). The script RE-VERIFIES
    at run time that no other install references them and refuses to delete if
    anything else does.
  - The 20 deployed_agents rows in SQLite whose backing_install_id is dangling
    (points to an install that does not exist) -- confirmed orphaned in Phase 1.
    Computed dynamically via LEFT JOIN so the set is always accurate.

NOT deleted:
  - runtime_profiles: the 4 junk installs reference none (runtime_profile_id is
    empty on all 4), so none are orphaned by this cleanup.

Safety rules (same family as the migrate_* scripts):
  - Dry-run by default: nothing is deleted unless --apply is passed.
  - Every affected row is logged individually.
  - Each store's deletes run inside a single transaction; any error rolls back
    that store.

Usage:
    python scripts/cleanup_junk_agents.py            # dry-run (default)
    python scripts/cleanup_junk_agents.py --dry-run   # explicit dry-run
    python scripts/cleanup_junk_agents.py --apply     # actually deletes
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sqlite3
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(str(Path(__file__).resolve().parent.parent / ".env"))
except Exception:
    pass

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from server_modules.control_plane_repository import LOCAL_CONTROL_PLANE_DB_FILE  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOGGER = logging.getLogger("cleanup_junk_agents")

JUNK_INSTALL_IDS = [
    "ainstall_ea8cb319ddbe484a",
    "ainstall_b0bacf554da94555",
    "ainstall_699e01f23ce1468d",
    "ainstall_47f0c1613c424938",
]
# The agent_definition + version that exist only to serve the junk installs.
SHARED_DEF_ID = "agentdef_default_ws-1_fleet-specialist"
SHARED_VERSION_ID = "agentdef_default_ws-1_fleet-specialist_v1"


def _sqlite_placeholders(n: int) -> str:
    return ", ".join("?" for _ in range(n))


def _pg_placeholders(n: int, start: int = 1) -> str:
    return ", ".join(f"${i}" for i in range(start, start + n))


# --------------------------------------------------------------------------- #
# Postgres
# --------------------------------------------------------------------------- #
async def cleanup_postgres(dsn: str, *, apply: bool) -> None:
    LOGGER.info("=== POSTGRES ===")
    conn = await asyncpg.connect(dsn=dsn)
    try:
        async with conn.transaction():
            # Which of the 4 installs actually exist here?
            existing = await conn.fetch(
                f"SELECT id, label FROM workspace_agent_installs WHERE id IN ({_pg_placeholders(len(JUNK_INSTALL_IDS))})",
                *JUNK_INSTALL_IDS,
            )
            if not existing:
                LOGGER.info("workspace_agent_installs: none of the 4 junk installs exist in Postgres (nothing to delete).")
            for row in existing:
                LOGGER.info("%s workspace_agent_installs id=%s (%s) [dependents cascade via FK]",
                            "DELETE" if apply else "WOULD DELETE", row["id"], row["label"])

            # Guard: only delete the shared def/version if nothing OTHER than the
            # 4 targets references them (the installs are deleted in this same
            # transaction, so we exclude the targets from the reference check).
            def_present = await conn.fetchval("SELECT 1 FROM agent_definitions WHERE id = $1", SHARED_DEF_ID)
            version_present = await conn.fetchval("SELECT 1 FROM agent_definition_versions WHERE id = $1", SHARED_VERSION_ID)

            if apply and existing:
                await conn.execute(
                    f"DELETE FROM workspace_agent_installs WHERE id IN ({_pg_placeholders(len(JUNK_INSTALL_IDS))})",
                    *JUNK_INSTALL_IDS,
                )

            if version_present:
                other_refs = await conn.fetchval(
                    f"SELECT count(*) FROM workspace_agent_installs "
                    f"WHERE agent_definition_version_id = $1 AND id NOT IN ({_pg_placeholders(len(JUNK_INSTALL_IDS), 2)})",
                    SHARED_VERSION_ID, *JUNK_INSTALL_IDS,
                )
                if other_refs:
                    LOGGER.warning("SKIP agent_definition_versions id=%s: still referenced by %d other install(s)", SHARED_VERSION_ID, other_refs)
                else:
                    LOGGER.info("%s agent_definition_versions id=%s", "DELETE" if apply else "WOULD DELETE", SHARED_VERSION_ID)
                    if apply:
                        await conn.execute("DELETE FROM agent_definition_versions WHERE id = $1", SHARED_VERSION_ID)
            else:
                LOGGER.info("agent_definition_versions id=%s: not present in Postgres (nothing to delete).", SHARED_VERSION_ID)

            if def_present:
                other_refs = await conn.fetchval(
                    f"SELECT count(*) FROM workspace_agent_installs "
                    f"WHERE agent_definition_id = $1 AND id NOT IN ({_pg_placeholders(len(JUNK_INSTALL_IDS), 2)})",
                    SHARED_DEF_ID, *JUNK_INSTALL_IDS,
                )
                if other_refs:
                    LOGGER.warning("SKIP agent_definitions id=%s: still referenced by %d other install(s)", SHARED_DEF_ID, other_refs)
                else:
                    LOGGER.info("%s agent_definitions id=%s", "DELETE" if apply else "WOULD DELETE", SHARED_DEF_ID)
                    if apply:
                        await conn.execute("DELETE FROM agent_definitions WHERE id = $1", SHARED_DEF_ID)
            else:
                LOGGER.info("agent_definitions id=%s: not present in Postgres (nothing to delete).", SHARED_DEF_ID)

            if not apply:
                # Roll back the (empty) transaction explicitly on dry-run.
                raise _DryRunRollback()
    except _DryRunRollback:
        LOGGER.info("Postgres dry-run: transaction rolled back, no changes written.")
    finally:
        await conn.close()


class _DryRunRollback(Exception):
    pass


# --------------------------------------------------------------------------- #
# SQLite control-plane
# --------------------------------------------------------------------------- #
def cleanup_sqlite(db_path: Path, *, apply: bool) -> None:
    LOGGER.info("=== SQLITE control-plane (%s) ===", db_path)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        ids = JUNK_INSTALL_IDS
        ph = _sqlite_placeholders(len(ids))

        # 1. agent_runtime_profiles rows for the 4 installs (explicit; no FK cascade in SQLite).
        arp = conn.execute(f"SELECT id, agent_install_id FROM agent_runtime_profiles WHERE agent_install_id IN ({ph})", ids).fetchall()
        for r in arp:
            LOGGER.info("%s agent_runtime_profiles id=%s (install=%s)", "DELETE" if apply else "WOULD DELETE", r["id"], r["agent_install_id"])
        if not arp:
            LOGGER.info("agent_runtime_profiles: 0 rows for the 4 installs.")

        # 2. The 4 installs.
        installs = conn.execute(f"SELECT id, label FROM workspace_agent_installs WHERE id IN ({ph})", ids).fetchall()
        for r in installs:
            LOGGER.info("%s workspace_agent_installs id=%s (%s)", "DELETE" if apply else "WOULD DELETE", r["id"], r["label"])

        # 3. Shared version + def, guarded by a re-check that only the 4 target it.
        version_other = conn.execute(
            f"SELECT count(*) AS c FROM workspace_agent_installs WHERE agent_definition_version_id = ? AND id NOT IN ({ph})",
            [SHARED_VERSION_ID, *ids],
        ).fetchone()["c"]
        version_present = conn.execute("SELECT 1 FROM agent_definition_versions WHERE id = ?", [SHARED_VERSION_ID]).fetchone()
        delete_version = bool(version_present) and version_other == 0
        if version_present and version_other:
            LOGGER.warning("SKIP agent_definition_versions id=%s: still referenced by %d other install(s)", SHARED_VERSION_ID, version_other)
        elif delete_version:
            LOGGER.info("%s agent_definition_versions id=%s", "DELETE" if apply else "WOULD DELETE", SHARED_VERSION_ID)

        def_other = conn.execute(
            f"SELECT count(*) AS c FROM workspace_agent_installs WHERE agent_definition_id = ? AND id NOT IN ({ph})",
            [SHARED_DEF_ID, *ids],
        ).fetchone()["c"]
        def_present = conn.execute("SELECT 1 FROM agent_definitions WHERE id = ?", [SHARED_DEF_ID]).fetchone()
        delete_def = bool(def_present) and def_other == 0
        if def_present and def_other:
            LOGGER.warning("SKIP agent_definitions id=%s: still referenced by %d other install(s)", SHARED_DEF_ID, def_other)
        elif delete_def:
            LOGGER.info("%s agent_definitions id=%s", "DELETE" if apply else "WOULD DELETE", SHARED_DEF_ID)

        # 4. Dangling deployed_agents (backing_install_id points to no install).
        dangling = conn.execute(
            """
            SELECT da.id, da.name, da.backing_install_id
            FROM deployed_agents da
            LEFT JOIN workspace_agent_installs wai ON wai.id = da.backing_install_id
            WHERE wai.id IS NULL
            """
        ).fetchall()
        for r in dangling:
            LOGGER.info("%s deployed_agents id=%s (%s) [dangling backing_install_id=%s]",
                        "DELETE" if apply else "WOULD DELETE", r["id"], r["name"], r["backing_install_id"])
        if not dangling:
            LOGGER.info("deployed_agents: 0 dangling rows.")

        if apply:
            conn.execute(f"DELETE FROM agent_runtime_profiles WHERE agent_install_id IN ({ph})", ids)
            conn.execute(f"DELETE FROM workspace_agent_installs WHERE id IN ({ph})", ids)
            if delete_version:
                conn.execute("DELETE FROM agent_definition_versions WHERE id = ?", [SHARED_VERSION_ID])
            if delete_def:
                conn.execute("DELETE FROM agent_definitions WHERE id = ?", [SHARED_DEF_ID])
            if dangling:
                conn.executemany("DELETE FROM deployed_agents WHERE id = ?", [(r["id"],) for r in dangling])
            conn.commit()
            LOGGER.info("SQLite: committed.")
        else:
            LOGGER.info("SQLite dry-run: no changes written.")

        # Summary counts.
        LOGGER.info(
            "SQLite summary: installs=%d, agent_runtime_profiles=%d, version=%d, def=%d, dangling_deployed_agents=%d",
            len(installs), len(arp), int(delete_version), int(delete_def), len(dangling),
        )
    finally:
        conn.close()


async def run(apply: bool) -> int:
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        LOGGER.error("DATABASE_URL is not set. Aborting (need Postgres to clean both stores).")
        return 1
    if not LOCAL_CONTROL_PLANE_DB_FILE.exists():
        LOGGER.error("SQLite control-plane file not found at %s. Aborting.", LOCAL_CONTROL_PLANE_DB_FILE)
        return 1

    LOGGER.info("Mode: %s", "APPLY (deleting)" if apply else "DRY-RUN (no deletes)")
    LOGGER.info("Junk installs: %s", ", ".join(JUNK_INSTALL_IDS))

    await cleanup_postgres(dsn, apply=apply)
    cleanup_sqlite(LOCAL_CONTROL_PLANE_DB_FILE, apply=apply)

    if not apply:
        LOGGER.info("Dry-run complete. Re-run with --apply to delete.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Actually delete rows.")
    parser.add_argument("--dry-run", action="store_true", help="Explicit dry-run (default behavior).")
    args = parser.parse_args()
    if args.apply and args.dry_run:
        parser.error("--dry-run and --apply are mutually exclusive.")
    return asyncio.run(run(apply=bool(args.apply)))


if __name__ == "__main__":
    raise SystemExit(main())

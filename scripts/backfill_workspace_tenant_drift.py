#!/usr/bin/env python3
"""Tenant-id drift backfill — realign child-table tenant_id to the workspace's
real, current tenant_id.

Root cause: workspaces created before per-workspace tenant_id resolution
existed had their child rows (agent installs, runtime profiles, ...) stamped
with a hardcoded tenant_id (historically 'default'). `workspaces.tenant_id`
was later backfilled/resolved to a real per-workspace tenant, but the child
rows were never realigned. Any query that scopes by the workspace's CURRENT
resolved tenant_id (e.g. fleet_list_agents -> list_workspace_agent_installs,
which filters `WHERE wai.tenant_id = $1 AND wai.workspace_id = $2`) then
silently returns zero rows for that workspace's real data — e.g. Sage
disappearing from the Fleet UI despite genuinely existing.

This script:
  1. Discovers every public table that has BOTH a tenant_id and a
     workspace_id column (not a fixed list — queried from
     information_schema, since the RLS-covered table list and the set of
     tables that actually carry a direct workspace_id FK are not the same).
  2. For each such table, reports rows where the row's tenant_id differs
     from its workspace's current `workspaces.tenant_id` ("drift"), and
     separately, rows whose workspace_id doesn't match any `workspaces` row
     at all ("orphaned" — informational only, never touched here).
  3. A small, explicit set of tables is EXCLUDED from the actual backfill
     (still reported, never written) because their tenant_id/workspace_id
     do not represent a simple "one owning workspace" child record — see
     EXCLUDED_TABLES for the reasoning per table. Realigning those needs its
     own, separate review.
  4. Under --apply, realigns every eligible table's drifted rows in one
     set-based UPDATE per table.

Must run with a connection that bypasses RLS (a normal RLS-scoped session
would filter to a single tenant and see none of the drift). This uses
control_plane_repository.ensure_control_plane_schema(), which connects with
DATABASE_URL directly (the same pool the app's own control-plane code
uses) — same convention as scripts/migrate_credential_project_scope.py.

Dry-run by default — prints the audit, changes nothing. Pass --apply to
perform the backfill on the eligible tables.

Usage:
    python scripts/backfill_workspace_tenant_drift.py                       # dry-run, all tables
    python scripts/backfill_workspace_tenant_drift.py --only runtime_profiles workspace_agent_installs
    python scripts/backfill_workspace_tenant_drift.py --apply               # perform it
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except Exception:
    pass

from server_modules import control_plane_repository as cpr  # noqa: E402

# Tables whose tenant_id/workspace_id pair does NOT represent a simple
# "single owning workspace" child record, so realigning them the same way
# would be wrong or premature. Still audited/reported below; never written.
EXCLUDED_TABLES: dict[str, str] = {
    "workspaces": "source of truth itself — self-referential, not a child record",
    "tenants": "tenant definition itself; workspace_id is the tenant's owning workspace, not a row scoped BY that workspace's tenant",
    "workspace_registry": "measured mostly disjoint from `workspaces` (legacy/parallel registry, not a live child table) — separate question, not this bug",
    "users": "identity row; a person can hold memberships in many workspaces (workspace_memberships is the many-to-many) — this single tenant/workspace column is a primary pointer, not an ownership relationship",
    "auth_identities": "identity row; same reasoning as users",
    "workspace_memberships": "inherently many-to-many between users and workspaces; needs its own review",
    "workspace_member_invites": "invite captured at send time; needs its own review",
}

_DISCOVER_TABLES_SQL = """
SELECT t1.table_name
FROM information_schema.columns t1
JOIN information_schema.columns t2
  ON t1.table_name = t2.table_name AND t1.table_schema = t2.table_schema
WHERE t1.table_schema = 'public'
  AND t1.column_name = 'tenant_id'
  AND t2.column_name = 'workspace_id'
ORDER BY t1.table_name
"""

_IDENT_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


async def _discover_tables(pool) -> list[str]:
    rows = await pool.fetch(_DISCOVER_TABLES_SQL)
    tables = [str(r["table_name"]) for r in rows]
    for t in tables:
        # Defensive: these come from information_schema (trusted), but this
        # script string-interpolates table names into SQL below, so refuse
        # to proceed if one is ever not a plain identifier.
        if not _IDENT_RE.match(t):
            raise ValueError(f"Unexpected table identifier from information_schema: {t!r}")
    return tables


async def _audit_table(pool, table: str, workspace_id: str | None = None) -> dict:
    ws_filter = "AND t.workspace_id = $1" if workspace_id else ""
    ws_args = [workspace_id] if workspace_id else []
    drift_rows = await pool.fetch(
        f"""
        SELECT t.workspace_id, t.tenant_id AS row_tenant_id, w.tenant_id AS real_tenant_id, count(*) AS n
        FROM "{table}" t
        JOIN workspaces w ON w.workspace_id = t.workspace_id
        WHERE t.workspace_id <> '' AND t.tenant_id <> w.tenant_id
          {ws_filter}
        GROUP BY t.workspace_id, t.tenant_id, w.tenant_id
        ORDER BY n DESC
        """,
        *ws_args,
    )
    orphan_rows = await pool.fetch(
        f"""
        SELECT t.workspace_id, count(*) AS n
        FROM "{table}" t
        LEFT JOIN workspaces w ON w.workspace_id = t.workspace_id
        WHERE t.workspace_id <> '' AND w.workspace_id IS NULL
          {ws_filter}
        GROUP BY t.workspace_id
        ORDER BY n DESC
        """,
        *ws_args,
    )
    return {
        "table": table,
        "drift_rows": drift_rows,
        "drift_total": sum(int(r["n"]) for r in drift_rows),
        "drift_workspaces": len({str(r["workspace_id"]) for r in drift_rows}),
        "orphan_rows": orphan_rows,
        "orphan_total": sum(int(r["n"]) for r in orphan_rows),
        "orphan_workspaces": len(orphan_rows),
    }


async def _apply_table(pool, table: str, workspace_id: str | None = None) -> int:
    # Scoping to a single workspace_id isn't just a convenience filter: some
    # tables enforce a same-workspace exclusion/uniqueness invariant (e.g.
    # workspace_agent_installs' `EXCLUDE USING gist (workspace_id WITH =,
    # tenant_id WITH <>)`), and Postgres checks that per-row *during* a
    # multi-row UPDATE — so realigning several sibling rows of one workspace
    # to the same new tenant_id in a single statement can transiently violate
    # it against a not-yet-updated sibling, even though the end state is
    # consistent. Scoping one workspace at a time doesn't fix a workspace
    # that itself has 2+ non-deferrable-conflicting rows (that needs its own
    # look), but it avoids batching unrelated workspaces' updates together in
    # ways that make an already-narrow, already-understood fix harder to
    # reason about.
    extra = ""
    args: list[Any] = []
    if workspace_id:
        extra = "AND t.workspace_id = $1"
        args.append(workspace_id)
    result = await pool.execute(
        f"""
        UPDATE "{table}" t
        SET tenant_id = w.tenant_id
        FROM workspaces w
        WHERE w.workspace_id = t.workspace_id
          AND t.workspace_id <> ''
          AND t.tenant_id <> w.tenant_id
          {extra}
        """,
        *args,
    )
    try:
        return int(str(result).split()[-1])
    except Exception:
        return -1


async def _main(apply: bool, only: list[str] | None, workspace: str | None) -> int:
    mode = "APPLY" if apply else "DRY-RUN"
    scope = f" (workspace={workspace})" if workspace else ""
    print(f"=== Tenant-id drift backfill [{mode}]{scope} ===\n")

    pool = await cpr.ensure_control_plane_schema()
    if pool is None:
        print("ABORT: DATABASE_URL not configured or Postgres unavailable.")
        return 1

    tables = await _discover_tables(pool)
    if only:
        wanted = set(only)
        tables = [t for t in tables if t in wanted]
        missing = wanted - set(tables)
        if missing:
            print(f"NOTE: --only name(s) not found (no tenant_id+workspace_id columns): {sorted(missing)}\n")

    print(f"Discovered {len(tables)} table(s) with both tenant_id + workspace_id columns.\n")

    excluded = [t for t in tables if t in EXCLUDED_TABLES]
    print(f"── Excluded from backfill ({len(excluded)}) — audited below, never written ──")
    for t in excluded:
        print(f"  - {t}: {EXCLUDED_TABLES[t]}")
    print()

    total_drift = 0
    total_orphan = 0
    drifted_eligible_tables: list[str] = []
    errored: list[tuple[str, str]] = []

    for t in tables:
        try:
            report = await _audit_table(pool, t, workspace_id=workspace)
        except Exception as exc:  # noqa: BLE001
            errored.append((t, str(exc)))
            print(f"[ERROR] {t}: {exc}\n")
            continue

        if report["drift_total"] == 0 and report["orphan_total"] == 0:
            continue

        tag = "EXCLUDED" if t in EXCLUDED_TABLES else "eligible"
        print(f"[{tag}] {t}")
        if report["drift_total"] > 0:
            print(f"    drift: {report['drift_total']} row(s) across {report['drift_workspaces']} workspace(s)")
            for r in report["drift_rows"][:10]:
                print(f"      ws={r['workspace_id']}  {r['row_tenant_id']!r} -> {r['real_tenant_id']!r}  x{r['n']}")
            if len(report["drift_rows"]) > 10:
                print(f"      ... and {len(report['drift_rows']) - 10} more workspace/tenant group(s)")
            total_drift += report["drift_total"]
            if t not in EXCLUDED_TABLES:
                drifted_eligible_tables.append(t)
        if report["orphan_total"] > 0:
            print(
                f"    orphaned (workspace_id not present in `workspaces`): "
                f"{report['orphan_total']} row(s) across {report['orphan_workspaces']} workspace_id value(s)"
            )
            total_orphan += report["orphan_total"]
        print()

    print("── Summary ──")
    print(f"  tables audited:                    {len(tables)}")
    print(f"  tables errored:                    {len(errored)} {[t for t, _ in errored] if errored else ''}")
    print(f"  total drifted rows (all tables):   {total_drift}")
    print(f"  total orphaned rows (all tables):  {total_orphan}")
    print(f"  eligible tables with drift to fix: {len(drifted_eligible_tables)} -> {drifted_eligible_tables}")

    if not apply:
        print("\nDRY-RUN complete. Re-run with --apply to realign the eligible tables listed above.")
        return 1 if errored else 0

    print("\n── Applying ──")
    applied_total = 0
    apply_failed: list[str] = []
    for t in drifted_eligible_tables:
        # Each table's UPDATE is its own implicit transaction — a failure
        # here (e.g. an unrelated unique-constraint collision) rolls back
        # only this table's statement, not previously-applied tables, and
        # must not abort the rest of the batch or hide their results.
        try:
            n = await _apply_table(pool, t, workspace_id=workspace)
            print(f"  {t}: {n} row(s) updated")
            if n > 0:
                applied_total += n
        except Exception as exc:  # noqa: BLE001
            apply_failed.append(t)
            print(f"  {t}: FAILED — {exc}")
    print(
        f"\nAPPLY complete — {applied_total} row(s) realigned across "
        f"{len(drifted_eligible_tables) - len(apply_failed)}/{len(drifted_eligible_tables)} table(s)."
    )
    if apply_failed:
        print(f"  FAILED tables (unchanged, needs its own look): {apply_failed}")
    return 1 if (errored or apply_failed) else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Tenant-id drift backfill (dry-run by default).")
    parser.add_argument("--apply", action="store_true", help="Perform the backfill (default is dry-run).")
    parser.add_argument("--only", nargs="*", default=None, metavar="TABLE", help="Restrict to these table names.")
    parser.add_argument(
        "--workspace", default=None, metavar="WORKSPACE_ID",
        help="Restrict to a single workspace_id — required when a workspace has 2+ rows in a table "
             "with a same-workspace exclusion/uniqueness constraint (batching them with other "
             "workspaces can trip a same-statement ordering conflict; see _apply_table).",
    )
    args = parser.parse_args()
    return asyncio.run(_main(apply=args.apply, only=args.only, workspace=args.workspace))


if __name__ == "__main__":
    raise SystemExit(main())

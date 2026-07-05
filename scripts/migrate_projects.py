"""Phase 2 — Projects backfill migration.

Creates a default ("General") project per existing workspace and backfills every
existing agent install into its workspace's default project. Also reports any
vault credentials that are NOT scoped to an agent (workspace/global-scoped
"unassigned legacy credentials") so the owner can decide their fate — these are
deliberately left workspace-scoped and are EXCLUDED from every agent's
"connected" status.

Dry-run by default (prints the plan, writes nothing). Pass --apply to execute.

Usage:
    python scripts/migrate_projects.py            # dry-run
    python scripts/migrate_projects.py --apply    # perform the migration
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except Exception:
    pass

from server_modules import control_plane_repository as cpr
from server_modules import projects_repository as projects


async def _distinct_workspaces(pool) -> list[tuple[str, str]]:
    """(tenant_id, workspace_id) pairs that need a default project — the union of
    every workspace that owns agent installs and every row in `workspaces`.
    Installs are the source of truth here: some live workspaces (e.g. ws-1) exist
    only via installs and never got a `workspaces` row."""
    rows = await pool.fetch(
        """
        SELECT DISTINCT tenant_id, workspace_id FROM workspace_agent_installs
        UNION
        SELECT DISTINCT tenant_id, id AS workspace_id FROM workspaces
        """
    )
    out = []
    for r in rows:
        t = str(r["tenant_id"] or "").strip()
        w = str(r["workspace_id"] or "").strip()
        if t and w:
            out.append((t, w))
    return sorted(set(out))


async def _legacy_unassigned_credentials() -> list[dict]:
    """Vault connector credentials with no agent scoping (agent_install_id /
    agent_id unset). These stay workspace-scoped and excluded from agent status."""
    try:
        from server_modules.vault_store import load_vault
        from server_modules.connection_catalog_service import CONNECTOR_CATALOG
    except Exception as exc:  # pragma: no cover
        print(f"  (could not load vault to inspect legacy credentials: {exc})")
        return []
    out = []
    for entry in load_vault().get("credentials", []):
        if not isinstance(entry, dict):
            continue
        provider = str(entry.get("provider") or "").strip()
        agent_scope = str(entry.get("agent_install_id") or entry.get("agent_id") or "").strip()
        if provider and provider in CONNECTOR_CATALOG and not agent_scope:
            out.append(
                {
                    "id": str(entry.get("id") or ""),
                    "provider": provider,
                    "label": str(entry.get("label") or ""),
                    "workspace_id": str(entry.get("workspace_id") or "") or "(global)",
                }
            )
    return out


async def _main(apply: bool) -> int:
    mode = "APPLY" if apply else "DRY-RUN"
    print(f"=== Phase 2 projects migration [{mode}] ===\n")

    pool = await cpr.ensure_control_plane_schema()
    if pool is None:
        print("ABORT: DATABASE_URL not configured or Postgres unavailable.")
        return 1
    print("Schema ready: projects table + workspace_agent_installs.project_id present.\n")

    workspaces = await _distinct_workspaces(pool)
    print(f"Workspaces to process: {len(workspaces)}\n")

    created_projects = 0
    reused_projects = 0
    backfilled_installs = 0

    for tenant_id, workspace_id in workspaces:
        # Does a default project already exist?
        existing_default = await pool.fetchrow(
            "SELECT id, name FROM projects WHERE tenant_id=$1 AND workspace_id=$2 AND is_default=TRUE LIMIT 1",
            tenant_id,
            workspace_id,
        )
        # How many installs still need a project?
        unassigned = await pool.fetchval(
            "SELECT COUNT(*) FROM workspace_agent_installs "
            "WHERE tenant_id=$1 AND workspace_id=$2 AND project_id IS NULL",
            tenant_id,
            workspace_id,
        )
        unassigned = int(unassigned or 0)

        if existing_default is not None:
            default_id = str(existing_default["id"])
            reused_projects += 1
            default_note = f"reuse default project {default_id}"
        else:
            default_id = None
            created_projects += 1
            default_note = f"CREATE default project '{projects.DEFAULT_PROJECT_NAME}'"

        if unassigned == 0 and existing_default is not None:
            continue  # nothing to do for this workspace

        print(f"  {workspace_id} (tenant {tenant_id}): {default_note}; backfill {unassigned} install(s)")

        if apply:
            default = await projects.ensure_default_project(tenant_id=tenant_id, workspace_id=workspace_id)
            default_id = default["id"]
            result = await pool.execute(
                "UPDATE workspace_agent_installs SET project_id=$3, updated_at=NOW() "
                "WHERE tenant_id=$1 AND workspace_id=$2 AND project_id IS NULL",
                tenant_id,
                workspace_id,
                default_id,
            )
            # asyncpg returns e.g. "UPDATE 2"
            try:
                backfilled_installs += int(str(result).split()[-1])
            except Exception:
                pass
        else:
            backfilled_installs += unassigned

    print()
    print("── Summary ──")
    print(f"  default projects created:  {created_projects}")
    print(f"  default projects reused:   {reused_projects}")
    print(f"  installs backfilled:       {backfilled_installs}")

    print("\n── Unassigned legacy vault credentials (left workspace-scoped, EXCLUDED from agent status) ──")
    legacy = await _legacy_unassigned_credentials()
    if not legacy:
        print("  (none)")
    for c in legacy:
        print(f"  ! {c['provider']} '{c['label']}' (id={c['id']}) — workspace={c['workspace_id']} — owner must decide its fate")

    if not apply:
        print("\nDRY-RUN complete. Re-run with --apply to perform these changes.")
    else:
        print("\nAPPLY complete.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Phase 2 projects backfill migration.")
    parser.add_argument("--apply", action="store_true", help="Perform the migration (default is dry-run).")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_main(apply=args.apply)))

"""UI Phase 1 — Credential project-scope backfill migration.

vault_credentials gained a project_id column (self-migrating ALTER in
control_plane_repository.ensure_control_plane_schema). This script backfills
project_id on every EXISTING agent-scoped credential (agent_install_id set)
from that agent's current project (workspace_agent_installs.project_id).

Credentials with no agent_install_id (workspace/global-scoped "unassigned
legacy" credentials — see migrate_projects.py) are left with project_id=NULL:
they were already excluded from every agent's connected status and stay
excluded from every project's connector list too.

Also repairs any agent-scoped credential that is missing its
agent_connector_bindings row (defensive — store_agent_connector_credential
always writes one, but this catches drift from manual DB edits or partial
writes before this fix).

Dry-run by default (prints the plan, writes nothing). Pass --apply to execute.

Usage:
    python scripts/migrate_credential_project_scope.py            # dry-run
    python scripts/migrate_credential_project_scope.py --apply    # perform it
"""

from __future__ import annotations

import argparse
import asyncio
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


async def _main(apply: bool) -> int:
    mode = "APPLY" if apply else "DRY-RUN"
    print(f"=== Credential project-scope backfill [{mode}] ===\n")

    pool = await cpr.ensure_control_plane_schema()
    if pool is None:
        print("ABORT: DATABASE_URL not configured or Postgres unavailable.")
        return 1
    print("Schema ready: vault_credentials.project_id present.\n")

    rows = await pool.fetch(
        """
        SELECT vc.id, vc.provider, vc.label, vc.agent_install_id,
               wai.project_id AS resolved_project_id, wai.tenant_id, wai.workspace_id
        FROM vault_credentials vc
        JOIN workspace_agent_installs wai ON wai.id = vc.agent_install_id
        WHERE vc.agent_install_id IS NOT NULL
          AND vc.project_id IS NULL
        """
    )
    print(f"Agent-scoped credentials needing project_id backfill: {len(rows)}\n")

    backfilled = 0
    skipped_no_project = 0
    bindings_repaired = 0

    for r in rows:
        cred_id = str(r["id"])
        resolved_project_id = str(r["resolved_project_id"] or "").strip()
        if not resolved_project_id:
            skipped_no_project += 1
            print(f"  ! {r['provider']} '{r['label']}' (id={cred_id}) — agent has no project; left unscoped")
            continue

        print(f"  {r['provider']} '{r['label']}' (id={cred_id}) -> project {resolved_project_id}")

        if apply:
            await pool.execute(
                "UPDATE vault_credentials SET project_id = $2, updated_at = NOW() WHERE id = $1",
                cred_id, resolved_project_id,
            )
            backfilled += 1

            # Defensive repair: ensure the binding row this credential should
            # already have (from store_agent_connector_credential) exists.
            existing_binding = await pool.fetchrow(
                "SELECT id, binding FROM agent_connector_bindings "
                "WHERE agent_install_id = $1 AND connector_key = $2",
                str(r["agent_install_id"]), str(r["provider"]),
            )
            if existing_binding is None:
                from server_modules import agent_bindings_repository as bindings
                await bindings.upsert_connector_binding(
                    tenant_id=str(r["tenant_id"]),
                    workspace_id=str(r["workspace_id"]),
                    agent_install_id=str(r["agent_install_id"]),
                    connector_key=str(r["provider"]),
                    enabled=True,
                    binding={"credential_id": cred_id},
                )
                bindings_repaired += 1
        else:
            backfilled += 1

    print()
    print("── Summary ──")
    print(f"  credentials backfilled:        {backfilled}")
    print(f"  skipped (agent has no project): {skipped_no_project}")
    print(f"  bindings repaired:              {bindings_repaired}")

    legacy = await pool.fetch(
        "SELECT id, provider, label, workspace_id FROM vault_credentials "
        "WHERE agent_install_id IS NULL AND project_id IS NULL"
    )
    print("\n── Unassigned legacy credentials (no agent, no project — left workspace-scoped) ──")
    if not legacy:
        print("  (none)")
    for c in legacy:
        print(f"  ! {c['provider']} '{c['label']}' (id={c['id']}) — workspace={c['workspace_id'] or '(global)'}")

    if not apply:
        print("\nDRY-RUN complete. Re-run with --apply to perform these changes.")
    else:
        print("\nAPPLY complete.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Credential project-scope backfill migration.")
    parser.add_argument("--apply", action="store_true", help="Perform the migration (default is dry-run).")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_main(apply=args.apply)))

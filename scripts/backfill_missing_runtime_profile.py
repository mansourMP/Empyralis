#!/usr/bin/env python3
"""Runtime-profile backfill — attach a resolved cloud placement to any
workspace_agent_installs row stuck with runtime_profile_id NULL.

Root cause (see agent_registry_repository.create_workspace_agent_install):
runtime_profile resolution reads list_runtime_profiles(), which seeds the
registry via ensure_workspace_agent_registry_seeded() first. That seed call
acquires its own scoped connection independently of the pool handle the
create path already checked — a transient hiccup there can make it silently
seed the LOCAL SQLite store instead of Postgres while the create path still
believes Postgres is available, leaving Postgres with zero runtime_profiles
rows for that workspace. The install then got created with
runtime_profile_id = NULL and no error — permanently "Not deployed" /
"Ready to configure" in the Fleet UI, with no repair path via the UI (the
Fleet PATCH endpoint's allowed-keys list doesn't include runtime_profile_id).
create_workspace_agent_install now verifies and retries this at create time
(fails loudly instead of proceeding with NULL) — this script is strictly for
agents created before that fix shipped.

Dry-run by default — prints what would be attached, changes nothing. Pass
--apply to perform the backfill.

Usage:
    python scripts/backfill_missing_runtime_profile.py                # dry-run
    python scripts/backfill_missing_runtime_profile.py --apply
    python scripts/backfill_missing_runtime_profile.py --workspace ws_abc123 --apply
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

from server_modules import agent_registry_repository as repo  # noqa: E402
from server_modules import control_plane_repository as cpr  # noqa: E402


async def _find_broken_installs(pool, *, workspace_id: str | None) -> list[dict]:
    ws_filter = "AND workspace_id = $1" if workspace_id else ""
    ws_args = [workspace_id] if workspace_id else []
    rows = await pool.fetch(
        f"""
        SELECT id, tenant_id, workspace_id, label
        FROM workspace_agent_installs
        WHERE runtime_profile_id IS NULL
          {ws_filter}
        ORDER BY tenant_id, workspace_id, created_at
        """,
        *ws_args,
    )
    return [dict(r) for r in rows]


async def _main(apply: bool, workspace: str | None) -> int:
    mode = "APPLY" if apply else "DRY-RUN"
    scope = f" (workspace={workspace})" if workspace else ""
    print(f"=== Missing runtime_profile backfill [{mode}]{scope} ===\n")

    pool = await cpr.ensure_control_plane_schema()
    if pool is None:
        print("ABORT: DATABASE_URL not configured or Postgres unavailable.")
        return 1

    broken = await _find_broken_installs(pool, workspace_id=workspace)
    if not broken:
        print("No installs with a NULL runtime_profile_id. Nothing to do.")
        return 0

    print(f"Found {len(broken)} install(s) with runtime_profile_id IS NULL:")
    for row in broken:
        print(f"  {row['id']}  ws={row['workspace_id']}  tenant={row['tenant_id']}  label={row['label']!r}")
    print()

    resolved_count = 0
    unresolved: list[dict] = []
    for row in broken:
        tenant_id = str(row["tenant_id"] or "").strip()
        workspace_id = str(row["workspace_id"] or "").strip()
        # Idempotent — re-seeding an already-seeded workspace is a no-op.
        await repo.ensure_workspace_agent_registry_seeded(tenant_id=tenant_id, workspace_id=workspace_id)
        profiles = await repo.list_runtime_profiles(
            tenant_id=tenant_id, workspace_id=workspace_id, seed_if_missing=False,
        )
        preferred = next((p for p in profiles if p.get("slug") == "empyralis-cloud"), None)
        profile = preferred or (profiles[0] if profiles else None)
        if profile is None:
            unresolved.append(row)
            print(f"  {row['id']}: could not resolve a runtime profile even after re-seeding — SKIPPED")
            continue
        profile_id = str(profile.get("id") or "").strip()
        print(f"  {row['id']}: -> {profile_id} ({profile.get('slug')})")
        if apply:
            await pool.execute(
                "UPDATE workspace_agent_installs SET runtime_profile_id = $1, updated_at = NOW() WHERE id = $2",
                profile_id,
                row["id"],
            )
        resolved_count += 1

    print(f"\n{'APPLY' if apply else 'DRY-RUN'} complete — {resolved_count}/{len(broken)} resolved"
          f"{'  (' + str(len(unresolved)) + ' unresolved, needs manual review)' if unresolved else ''}.")
    if not apply and resolved_count:
        print("Re-run with --apply to attach the resolved profiles above.")
    return 1 if unresolved else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill missing runtime_profile_id (dry-run by default).")
    parser.add_argument("--apply", action="store_true", help="Perform the backfill (default is dry-run).")
    parser.add_argument("--workspace", default=None, metavar="WORKSPACE_ID", help="Restrict to a single workspace_id.")
    args = parser.parse_args()
    return asyncio.run(_main(apply=args.apply, workspace=args.workspace))


if __name__ == "__main__":
    raise SystemExit(main())

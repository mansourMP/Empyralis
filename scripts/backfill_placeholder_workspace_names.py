#!/usr/bin/env python3
"""Repair for workspaces whose `name` column was seeded from their own
machine id (e.g. a workspace displayed on screen as `ws_b5c1fa225ae6`).

Root cause: `control_plane_repository.ensure_workspace_membership()` used to
pass `resolved_workspace_id` as the `name` of a brand-new workspace instead of
deriving a human name the way `create_local_password_account()` already does
(display name / email prefix + "'s Workspace"). Fixed at the write site so no
NEW workspace gets a placeholder name -- this script is the separate, explicit
repair for rows that were already written before that fix, per this
codebase's standing rule: a corrupted-row repair must be its own
separately-invocable function, never automatic, and never run as a side
effect of a read.

This script:
  1. Finds workspaces where `name` is literally equal to `workspace_id` (the
     exact signature of the bug -- never touches a workspace with any other
     name, including one a customer genuinely chose that happens to look
     machine-generated).
  2. For each, resolves a human name from its EARLIEST workspace member
     (preferring a member with role='owner', tying by created_at) using the
     same "display name or email prefix, + \"'s Workspace\"" pattern the
     fixed write site uses.
  3. A workspace with no resolvable member (should not happen -- every
     workspace is created alongside a membership row) is reported and
     skipped, never guessed.

Dry-run by default -- prints the audit, changes nothing. Pass --apply to
write the repaired names.

Usage:
    python scripts/backfill_placeholder_workspace_names.py                 # dry-run
    python scripts/backfill_placeholder_workspace_names.py --apply         # perform it
    python scripts/backfill_placeholder_workspace_names.py --workspace ws_b5c1fa225ae6 --apply
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

from server_modules import control_plane_repository as cpr  # noqa: E402

_FIND_PLACEHOLDER_WORKSPACES_SQL = """
SELECT workspace_id, tenant_id, name
FROM workspaces
WHERE name = workspace_id
ORDER BY created_at ASC
"""

_FIND_EARLIEST_MEMBER_SQL = """
SELECT u.email, u.display_name
FROM workspace_memberships wm
JOIN users u ON u.id = wm.user_id
WHERE wm.workspace_id = $1
ORDER BY (wm.role = 'owner') DESC, wm.created_at ASC
LIMIT 1
"""


def _derive_workspace_name(email: str, display_name: str | None) -> str:
    display_label = str(display_name or "").strip()
    email_prefix = str(email or "").strip().split("@", 1)[0]
    return f"{display_label or email_prefix}'s Workspace".strip()


async def _main(apply: bool, workspace: str | None) -> int:
    mode = "APPLY" if apply else "DRY-RUN"
    scope = f" (workspace={workspace})" if workspace else ""
    print(f"=== Placeholder workspace-name backfill [{mode}]{scope} ===\n")

    pool = await cpr.ensure_control_plane_schema()
    if pool is None:
        print("ABORT: DATABASE_URL not configured or Postgres unavailable.")
        return 1

    if workspace:
        rows = await pool.fetch(
            _FIND_PLACEHOLDER_WORKSPACES_SQL.replace(
                "WHERE name = workspace_id",
                "WHERE name = workspace_id AND workspace_id = $1",
            ),
            workspace,
        )
    else:
        rows = await pool.fetch(_FIND_PLACEHOLDER_WORKSPACES_SQL)

    print(f"Found {len(rows)} workspace(s) whose name is literally their own id.\n")
    if not rows:
        return 0

    repairs: list[tuple[str, str, str]] = []  # (workspace_id, old_name, new_name)
    unresolved: list[str] = []

    for row in rows:
        ws_id = str(row["workspace_id"])
        member = await pool.fetchrow(_FIND_EARLIEST_MEMBER_SQL, ws_id)
        if member is None:
            unresolved.append(ws_id)
            print(f"  [UNRESOLVED] {ws_id}: no workspace_memberships row -- skipped, not guessed")
            continue
        new_name = _derive_workspace_name(str(member["email"] or ""), member["display_name"])
        repairs.append((ws_id, str(row["name"]), new_name))
        print(f"  {ws_id}: {row['name']!r} -> {new_name!r}")

    print("\n── Summary ──")
    print(f"  placeholder workspaces found: {len(rows)}")
    print(f"  repairable:                   {len(repairs)}")
    print(f"  unresolved (no member row):   {len(unresolved)}")

    if not apply:
        print("\nDRY-RUN complete. Re-run with --apply to write the names listed above.")
        return 0

    print("\n── Applying ──")
    applied = 0
    for ws_id, _old_name, new_name in repairs:
        # Scope the UPDATE to the exact placeholder condition again, not just
        # the id -- if something else already renamed this workspace between
        # the audit read and this write, this is a silent no-op rather than a
        # clobber of a name a customer set in the meantime.
        result = await pool.execute(
            "UPDATE workspaces SET name = $2 WHERE workspace_id = $1 AND name = workspace_id",
            ws_id,
            new_name,
        )
        touched = 0
        try:
            touched = int(str(result).split()[-1])
        except Exception:
            pass
        if touched:
            applied += 1
        print(f"  {ws_id}: {'updated' if touched else 'skipped (no longer a placeholder)'}")

    print(f"\nAPPLY complete -- {applied}/{len(repairs)} workspace(s) renamed.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Repair workspaces whose name column was seeded from their own workspace_id (dry-run by default)."
    )
    parser.add_argument("--apply", action="store_true", help="Perform the repair (default is dry-run).")
    parser.add_argument("--workspace", default=None, metavar="WORKSPACE_ID", help="Restrict to a single workspace_id.")
    args = parser.parse_args()
    return asyncio.run(_main(apply=args.apply, workspace=args.workspace))


if __name__ == "__main__":
    raise SystemExit(main())

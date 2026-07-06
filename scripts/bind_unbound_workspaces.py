#!/usr/bin/env python3
"""Bind every currently-unbound workspace to a tenant.

An "unbound" workspace has a membership (a user belongs to it) but no
``workspaces`` row carrying a non-empty ``tenant_id`` — so
``tenant_id_for_workspace`` returns None and ``/auth/me`` 403s for that user.

For each unbound workspace this binds it to a tenant via the shared, fixed
``ensure_workspace_tenant_binding`` (which upserts the tenant + workspace rows
and honours one-tenant-per-workspace). Where the membership already references a
tenant, that tenant is reused; otherwise a fresh ``tenant_<uuid>`` is minted.

Dry-run by default — prints every workspace it WOULD bind and changes nothing.
Pass ``--apply`` to perform the binds. Run where the production DATABASE_URL is
set (the VPS):

    DATABASE_URL=postgres://... python3 scripts/bind_unbound_workspaces.py            # dry-run
    DATABASE_URL=postgres://... python3 scripts/bind_unbound_workspaces.py --apply     # perform
"""

import argparse
import asyncio
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_UNBOUND_QUERY = """
SELECT m.workspace_id, MAX(NULLIF(m.tenant_id, '')) AS tenant_hint
FROM workspace_memberships m
WHERE NOT EXISTS (
    SELECT 1 FROM workspaces w
    WHERE w.workspace_id = m.workspace_id
      AND w.tenant_id IS NOT NULL AND w.tenant_id <> ''
)
GROUP BY m.workspace_id
ORDER BY m.workspace_id
"""


async def _run(apply: bool) -> int:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        print("DATABASE_URL not set — run this where the production DB is configured (the VPS).")
        return 2
    try:
        import asyncpg  # noqa: PLC0415
    except ImportError:
        print("asyncpg is not installed — cannot enumerate unbound workspaces.")
        return 2

    from server_modules import control_plane_repository as cpr  # noqa: PLC0415

    conn = await asyncpg.connect(database_url, timeout=15)
    try:
        rows = await conn.fetch(_UNBOUND_QUERY)
    finally:
        await conn.close()

    if not rows:
        print("No unbound workspaces found — every workspace with a membership is bound to a tenant.")
        return 0

    mode = "APPLY" if apply else "DRY-RUN"
    print(f"[{mode}] {len(rows)} unbound workspace(s) found:\n")
    bound = 0
    failed = 0
    for row in rows:
        workspace_id = str(row["workspace_id"]).strip()
        tenant_hint = str(row["tenant_hint"] or "").strip()
        tenant_id = tenant_hint or f"tenant_{uuid.uuid4().hex[:12]}"
        source = "reuse membership tenant" if tenant_hint else "mint new tenant"
        if not apply:
            print(f"  [dry-run] would bind ws={workspace_id} → tenant={tenant_id} ({source})")
            continue
        try:
            result = await cpr.ensure_workspace_tenant_binding(
                workspace_id=workspace_id, tenant_id=tenant_id,
            )
            if isinstance(result, dict) and result.get("tenant_id"):
                bound += 1
                print(f"  [apply] bound ws={workspace_id} → tenant={result['tenant_id']} ({source})")
            else:
                failed += 1
                print(f"  [apply] FAILED ws={workspace_id} — binding returned {result!r}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  [apply] ERROR ws={workspace_id}: {exc}")

    print()
    if not apply:
        print(f"DRY-RUN complete — {len(rows)} workspace(s) would be bound. Re-run with --apply to perform.")
        return 0
    print(f"APPLY complete — bound {bound}, failed {failed}.")
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Bind unbound workspaces to tenants.")
    parser.add_argument("--apply", action="store_true", help="Perform the binds (default is dry-run).")
    args = parser.parse_args()
    return asyncio.run(_run(args.apply))


if __name__ == "__main__":
    raise SystemExit(main())

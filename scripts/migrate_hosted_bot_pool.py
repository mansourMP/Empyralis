"""Phase 3B — hosted_bot_pool schema migration.

Ensures the hosted_bot_pool table + its indexes exist and reports current pool
capacity. This is a new table with no data backfill, so the migration is
schema-only; the dry-run reports what is present without asserting any bots
(add those with scripts/manage_bot_pool.py).

Dry-run by default; pass --apply to (idempotently) ensure the schema.

    python scripts/migrate_hosted_bot_pool.py           # dry-run: report state
    python scripts/migrate_hosted_bot_pool.py --apply    # ensure schema
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
from server_modules import hosted_bot_pool_repository as pool_repo


async def _main(apply: bool) -> int:
    mode = "APPLY" if apply else "DRY-RUN"
    print(f"=== Phase 3B hosted_bot_pool migration [{mode}] ===\n")

    pool = await cpr.ensure_control_plane_schema()
    if pool is None:
        print("ABORT: DATABASE_URL not configured or Postgres unavailable.")
        return 1

    table = await pool.fetchval("SELECT to_regclass('public.hosted_bot_pool')")
    idx = await pool.fetchval(
        "SELECT COUNT(*) FROM pg_indexes WHERE indexname = 'uq_hosted_bot_pool_assignment'"
    )
    print(f"hosted_bot_pool table:            {'present' if table else 'MISSING'}")
    print(f"uq_hosted_bot_pool_assignment:    {'present' if int(idx or 0) else 'MISSING'}")

    cap = await pool_repo.pool_capacity()
    print(f"\nPool capacity: total={cap['total']} free={cap['free']} "
          f"assigned={cap['assigned']} quarantined={cap['quarantined']}")

    if not apply:
        print("\nDRY-RUN complete. Schema is ensured on app boot; run --apply to force it now.")
    else:
        print("\nAPPLY complete — schema ensured.")
    print("Add pool bots with:  python scripts/manage_bot_pool.py --add --token <BOT_TOKEN>")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Phase 3B hosted_bot_pool schema migration.")
    parser.add_argument("--apply", action="store_true", help="Ensure the schema now (default is dry-run).")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_main(apply=args.apply)))

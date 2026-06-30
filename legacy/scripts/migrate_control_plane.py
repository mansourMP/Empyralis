from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from server_modules.control_plane_repository import ensure_control_plane_schema


async def _main() -> int:
    pool = await ensure_control_plane_schema()
    if pool is None:
        print("Control-plane migration skipped: DATABASE_URL is not configured or Postgres is unavailable.")
        return 1
    print("Control-plane schema is ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))

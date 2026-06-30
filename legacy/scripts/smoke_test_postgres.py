#!/usr/bin/env python3
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
import sys
import uuid


async def _main() -> int:
    try:
        import asyncpg
    except Exception as exc:
        print(f"FAIL: asyncpg unavailable: {exc}")
        return 1

    database_url = str(os.getenv("DATABASE_URL") or "").strip()
    if not database_url:
        print("FAIL: DATABASE_URL is not set")
        return 1

    run_id = f"smoke-{uuid.uuid4().hex}"
    payload = {
        "run_id": run_id,
        "status": "queued",
        "workspace_id": "default",
        "tenant_id": "default",
        "context": {"workspace_id": "default", "tenant_id": "default"},
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    conn = None
    try:
        conn = await asyncpg.connect(dsn=database_url, command_timeout=10.0)
        await conn.execute(
            """
            INSERT INTO live_runs (run_id, workspace_id, tenant_id, state, payload, trace_id, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6, NOW(), NOW())
            ON CONFLICT (run_id) DO UPDATE SET
                workspace_id = EXCLUDED.workspace_id,
                tenant_id = EXCLUDED.tenant_id,
                state = EXCLUDED.state,
                payload = EXCLUDED.payload,
                trace_id = EXCLUDED.trace_id,
                updated_at = NOW()
            """,
            run_id,
            "default",
            "default",
            "queued",
            json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str),
            "smoke-test",
        )
        row = await conn.fetchrow(
            "SELECT run_id, state, payload FROM live_runs WHERE run_id = $1 LIMIT 1",
            run_id,
        )
        if row is None or str(row.get("run_id") or "") != run_id:
            print("FAIL: inserted row was not read back")
            return 1
        await conn.execute("DELETE FROM live_runs WHERE run_id = $1", run_id)
        print(f"PASS: inserted, read, and deleted live_runs row {run_id}")
        return 0
    except Exception as exc:
        print(f"FAIL: {exc}")
        return 1
    finally:
        if conn is not None:
            try:
                await conn.close()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))

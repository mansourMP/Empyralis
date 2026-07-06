#!/usr/bin/env python3
"""Report the live Row-Level Security state of every tenant-scoped table.

Run where the production DATABASE_URL is set (e.g. on the VPS):

    DATABASE_URL=postgres://... python3 scripts/rls_state_report.py

Prints a per-table matrix of (RLS enabled / FORCEd / policy count) and an exit
code: 0 = all tenant tables isolated, 1 = one or more gaps (isolation off),
2 = could not check (no DATABASE_URL / driver). Read-only; makes no changes.
"""

import asyncio
import os
import sys

# Allow running from the repo root without installation.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server_modules import preflight  # noqa: E402


async def _main() -> int:
    report = await preflight.report_rls_state()
    if not report["configured"]:
        print(f"RLS state: NOT CHECKED — {report['reason']}")
        return 2

    tables = report["tables"]
    width = max((len(t["table"]) for t in tables), default=10)
    print(f"{'table'.ljust(width)}  rls  force  policies  status")
    print("-" * (width + 30))
    for t in tables:
        status = "OK" if (t["present"] and t["rls_enabled"] and t["rls_forced"] and t["policy_count"] > 0) else "GAP"
        print(
            f"{t['table'].ljust(width)}  "
            f"{'Y' if t['rls_enabled'] else 'n'}    "
            f"{'Y' if t['rls_forced'] else 'n'}      "
            f"{str(t['policy_count']).rjust(3)}       {status}"
        )

    print()
    if report["ok"]:
        print(f"RESULT: OK — all {len(tables)} tenant-scoped tables have RLS + FORCE + a policy.")
        return 0
    print(f"RESULT: GAPS — {len(report['problems'])} problem(s); tenant isolation is NOT fully enforced:")
    for problem in report["problems"]:
        print(f"  - {problem}")
    print("\nApply: psql <DATABASE_URL> -f migrations/enable_rls.sql")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))

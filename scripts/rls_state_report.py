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

    excused = report.get("uncovered_but_excused") or []
    if excused:
        print()
        print(f"KNOWN UNCOVERED ({len(excused)}) — tenant-scoped tables deliberately outside")
        print("the migration today, each with a recorded reason. Remediation backlog:")
        for entry in excused:
            note = " [already has a policy — the exception can be deleted]" if entry.get("protected") else ""
            print(f"  - {entry['table']} ({', '.join(entry['scope_columns'])}){note}")
            print(f"      {entry['reason']}")

    print()
    if report["ok"]:
        print(f"RESULT: OK — all {len(tables)} tenant-scoped tables have RLS + FORCE + a policy,")
        print("and no tenant-scoped table is outside the migration without a recorded reason.")
        return 0

    problems = report["problems"]
    coverage_problems = report.get("coverage_problems") or []
    print(
        f"RESULT: GAPS — {len(problems) + len(coverage_problems)} problem(s); "
        "tenant isolation is NOT fully enforced:"
    )
    for problem in problems:
        print(f"  - [enforcement] {problem}")
    for problem in coverage_problems:
        print(f"  - [coverage]    {problem}")
    if problems:
        print("\nApply: psql <DATABASE_URL> -f migrations/enable_rls.sql")
    if coverage_problems:
        print(
            "\nA [coverage] entry is a table carrying tenant_id/workspace_id that the\n"
            "migration never mentions. Add it to migrations/enable_rls.sql once its\n"
            "queries are confirmed tenant-scoped, or record it in\n"
            "preflight._RLS_COVERAGE_EXCEPTIONS with the reason it is exempt."
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))

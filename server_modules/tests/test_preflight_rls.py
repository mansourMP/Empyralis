"""Proof for Fix 3: boot refuses to serve traffic with tenant-isolation RLS off.

Regression cover for the audit CRITICAL: nothing applied or verified Postgres
RLS, so a deploy that skipped migrations/enable_rls.sql booted "healthy" with
tenant isolation silently off and cross-tenant reads possible.
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from server_modules import preflight


class FakeConn:
    def __init__(self, rows):
        self._rows = rows
        self.closed = False

    async def fetch(self, _query, _tables):
        return self._rows

    async def close(self):
        self.closed = True


def _all_good_rows(tables):
    return [
        {"table_name": t, "rls_enabled": True, "rls_forced": True, "policy_count": 1}
        for t in tables
    ]


class MigrationParserTests(unittest.TestCase):
    def test_parses_tenant_tables_from_migration(self):
        """MAN-109 follow-up: this used to assert `len(tables) == 29`, which
        broke the moment migrations/enable_rls.sql grew to 35 tables (the six
        MAN-109 closed the RLS gap on: project_tasks, projects,
        project_memberships, workspace_labels, bug_reports,
        mcp_external_agent_roster). An exact count is also the wrong shape of
        test for this function on its own terms --
        _tenant_scoped_tables_from_migration's own docstring says it is
        "Parsed (not hardcoded) so the check tracks the migration
        automatically — a table added to the migration is verified without
        editing this file." A hardcoded count assertion fights that design:
        every future table added to the migration (which is the whole point
        of parsing it live) would force an unrelated edit here. Asserting
        membership of the specific tables that matter -- the original
        spot-check plus the six MAN-109 just added -- proves the same thing
        (the parser actually found them) without re-coupling this test to a
        number that is expected to keep changing. A generous lower bound
        stays as a sanity check that the parser did not regress to
        returning nothing/near-nothing.
        """
        tables = preflight._tenant_scoped_tables_from_migration()
        self.assertGreaterEqual(len(tables), 35)
        for expected in (
            "tenants", "workspaces", "users", "workspace_agent_installs",
            # MAN-109: the six tables this follow-up's RLS gap closure covers.
            "project_tasks", "projects", "project_memberships",
            "workspace_labels", "bug_reports", "mcp_external_agent_roster",
        ):
            self.assertIn(expected, tables)


class RlsProblemTests(unittest.TestCase):
    def test_flags_each_kind_of_gap(self):
        expected = ["a", "b", "c", "d"]
        state = {
            "a": {"rls_enabled": True, "rls_forced": True, "policy_count": 1},   # ok
            "b": {"rls_enabled": False, "rls_forced": False, "policy_count": 0},  # disabled
            "c": {"rls_enabled": True, "rls_forced": False, "policy_count": 1},   # not forced
            "d": {"rls_enabled": True, "rls_forced": True, "policy_count": 0},    # no policy
            # (a missing table is exercised below)
        }
        problems = preflight._rls_problems(expected, state)
        self.assertEqual(len(problems), 3)
        joined = " | ".join(problems)
        self.assertIn("b: RLS not enabled", joined)
        self.assertIn("c: RLS not FORCEd", joined)
        self.assertIn("d: no RLS policy", joined)

    def test_missing_table_is_a_gap(self):
        problems = preflight._rls_problems(["x"], {})
        self.assertEqual(problems, ["x: table not found"])

    def test_all_good_has_no_problems(self):
        state = {"a": {"rls_enabled": True, "rls_forced": True, "policy_count": 2}}
        self.assertEqual(preflight._rls_problems(["a"], state), [])


class CheckRlsTests(unittest.TestCase):
    def test_skipped_without_database_url_in_local_dev(self):
        with patch.dict("os.environ", {"DATABASE_URL": "", "EMPYRALIS_SKIP_RLS_CHECK": ""}, clear=False), \
             patch("server_modules.db.durable_runtime_required", return_value=False):
            self.assertIsNone(asyncio.run(preflight._check_rls()))

    def test_skip_env_bypasses_loudly(self):
        with patch.dict("os.environ", {"EMPYRALIS_SKIP_RLS_CHECK": "true"}, clear=False):
            self.assertIsNone(asyncio.run(preflight._check_rls()))

    def test_detects_missing_rls_and_refuses_boot(self):
        expected = preflight._tenant_scoped_tables_from_migration()
        rows = _all_good_rows(expected)
        rows[5]["rls_enabled"] = False  # one table missing RLS → isolation off
        broken_table = rows[5]["table_name"]
        with patch.dict("os.environ", {"DATABASE_URL": "postgres://x", "EMPYRALIS_SKIP_RLS_CHECK": ""}, clear=False), \
             patch("asyncpg.connect", new=AsyncMock(return_value=FakeConn(rows))):
            err = asyncio.run(preflight._check_rls())
        self.assertIsNotNone(err)
        self.assertIn(broken_table, err)
        self.assertIn("enable_rls.sql", err)

    def test_passes_when_all_tables_enforced(self):
        expected = preflight._tenant_scoped_tables_from_migration()
        rows = _all_good_rows(expected)
        with patch.dict("os.environ", {"DATABASE_URL": "postgres://x", "EMPYRALIS_SKIP_RLS_CHECK": ""}, clear=False), \
             patch("asyncpg.connect", new=AsyncMock(return_value=FakeConn(rows))):
            self.assertIsNone(asyncio.run(preflight._check_rls()))


if __name__ == "__main__":
    unittest.main()

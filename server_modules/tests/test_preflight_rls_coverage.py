"""Proof that preflight can see an RLS gap it was NOT already told about.

The pre-existing check (test_preflight_rls.py) verifies that every table
listed in ``migrations/enable_rls.sql`` really has RLS + FORCE + a policy on
the live database. That is a real check, and it passes — all 40 listed tables
are protected.

But it derived its own list of "tenant-scoped tables" by parsing that same
migration (``_tenant_scoped_tables_from_migration``). So it verified exactly
the tables the migration already knew about, and nothing else. A table
carrying ``tenant_id``/``workspace_id`` that nobody ever added to the
migration was simultaneously:

    unprotected  (no policy)          AND
    unverified   (not in the list)    ->  preflight: "all checks passed"

That circularity — not any individual missing table — is the structural
defect. 30+ tenant-scoped tables were sitting outside the migration on
2026-08-08 and the boot check could not have reported a single one of them.

The fix asks the LIVE database which tables carry a scope column and requires
each answer to be either covered by the migration or recorded in
``preflight._RLS_COVERAGE_EXCEPTIONS`` with a written reason. This file is
the regression guard for that, in three parts:

1. ``CoverageProblemTests`` — the pure comparison logic, no database.
2. ``ExceptionRegistryTests`` — hygiene on the exception list itself, so it
   decays toward empty instead of quietly becoming a dumping ground. These
   are the tests that fail when someone adds RLS to a table and forgets to
   remove its excuse, which is exactly the bookkeeping nobody does unaided.
3. ``CheckRlsCoverageTests`` / ``DiscoveryQueryTests`` — the wiring into
   ``_check_rls`` (boot really does fail) and the discovery SQL against a
   real Postgres (opt-in via DATABASE_URL, skipped otherwise, same posture as
   test_rls_six_tables_isolation_man109.py).
"""

import asyncio
import os
import re
import unittest
import uuid
from unittest.mock import AsyncMock, patch

from server_modules import preflight


def _database_url_available() -> bool:
    return bool(os.getenv("DATABASE_URL", "").strip())


_NO_PG_REASON = "No Postgres reachable (DATABASE_URL unset — export it to run this suite)"


class CoverageProblemTests(unittest.TestCase):
    """The comparison itself: live schema vs migration vs exceptions."""

    def test_flags_a_scoped_table_the_migration_never_heard_of(self):
        problems = preflight._rls_coverage_problems(
            discovered={"drifted": {"scope_columns": ["tenant_id", "workspace_id"]}},
            expected=["some_other_table"],
            exceptions={},
        )
        self.assertEqual(len(problems), 1)
        self.assertIn("drifted", problems[0])
        # The columns are named because a table with only ONE of the two
        # cannot use empyralis_rls_scope_match(tenant_id, workspace_id)
        # unchanged — whoever reads this error needs to know that up front.
        self.assertIn("tenant_id", problems[0])
        self.assertIn("workspace_id", problems[0])

    def test_a_table_with_only_workspace_id_is_still_flagged_and_says_so(self):
        problems = preflight._rls_coverage_problems(
            discovered={"half_scoped": {"scope_columns": ["workspace_id"]}},
            expected=[],
            exceptions={},
        )
        self.assertEqual(len(problems), 1)
        self.assertIn("half_scoped", problems[0])
        self.assertIn("workspace_id", problems[0])
        self.assertNotIn("tenant_id", problems[0])

    def test_covered_by_migration_is_not_flagged(self):
        problems = preflight._rls_coverage_problems(
            discovered={"covered": {"scope_columns": ["tenant_id", "workspace_id"]}},
            expected=["covered"],
            exceptions={},
        )
        self.assertEqual(problems, [])

    def test_a_table_already_protected_elsewhere_is_not_flagged(self):
        """The question is "is it protected", not "is it in that one file".

        `agent_computers` carries its own ENABLE + FORCE ROW LEVEL SECURITY in
        migrations/add_agent_computers.sql. It is genuinely protected, it is
        not in enable_rls.sql, and it is not excused — so a name-only
        comparison would report it as an unguarded tenant table. That is a
        false alarm, and false alarms are how a boot check gets switched off.
        """
        problems = preflight._rls_coverage_problems(
            discovered={
                "protected_elsewhere": {
                    "scope_columns": ["tenant_id", "workspace_id"],
                    "protected": True,
                },
            },
            expected=[],
            exceptions={},
        )
        self.assertEqual(problems, [])

    def test_partial_protection_does_not_count_as_protected(self):
        """RLS enabled but not FORCEd, or with zero policies, is not
        protection — the table owner bypasses an unFORCEd policy, and an
        enabled-but-policyless table is just a table. Discovery reports
        `protected` only when all three hold; assert the flag is honoured
        as a boolean and a false one still fails."""
        problems = preflight._rls_coverage_problems(
            discovered={
                "half_protected": {
                    "scope_columns": ["tenant_id", "workspace_id"],
                    "protected": False,
                },
            },
            expected=[],
            exceptions={},
        )
        self.assertEqual(len(problems), 1)
        self.assertIn("half_protected", problems[0])

    def test_recorded_exception_is_not_flagged(self):
        problems = preflight._rls_coverage_problems(
            discovered={"excused": {"scope_columns": ["workspace_id"]}},
            expected=[],
            exceptions={"excused": "deliberately global — reason here"},
        )
        self.assertEqual(problems, [])

    def test_unscoped_tables_are_ignored_entirely(self):
        """Discovery only ever hands us scoped tables, but be explicit: a
        table with no tenant_id/workspace_id has no tenant boundary to
        enforce and must never appear in this list."""
        problems = preflight._rls_coverage_problems(
            discovered={},
            expected=["covered"],
            exceptions={},
        )
        self.assertEqual(problems, [])

    def test_reports_every_gap_not_just_the_first(self):
        problems = preflight._rls_coverage_problems(
            discovered={
                "gap_a": {"scope_columns": ["tenant_id"]},
                "gap_b": {"scope_columns": ["workspace_id"]},
                "fine": {"scope_columns": ["tenant_id", "workspace_id"]},
            },
            expected=["fine"],
            exceptions={},
        )
        self.assertEqual(len(problems), 2)
        joined = " | ".join(problems)
        self.assertIn("gap_a", joined)
        self.assertIn("gap_b", joined)

    def test_defaults_to_the_real_exception_registry(self):
        """Called with no `exceptions` argument (as _check_rls calls it), the
        module-level registry is what applies — otherwise the seeded backlog
        would not actually suppress anything at boot."""
        registry = preflight._RLS_COVERAGE_EXCEPTIONS
        if not registry:
            self.skipTest("exception registry is empty — nothing to suppress")
        table = sorted(registry)[0]
        problems = preflight._rls_coverage_problems(
            discovered={table: {"scope_columns": ["tenant_id", "workspace_id"]}},
            expected=[],
        )
        self.assertEqual(problems, [])


class ExceptionRegistryTests(unittest.TestCase):
    """Hygiene on _RLS_COVERAGE_EXCEPTIONS itself.

    An allowlist with no upkeep rule becomes permanent. These assertions are
    the upkeep rule: they are cheap, they run in CI with no database, and
    they fail on precisely the two ways this list rots.
    """

    def test_every_exception_carries_a_written_reason(self):
        for table, reason in preflight._RLS_COVERAGE_EXCEPTIONS.items():
            with self.subTest(table=table):
                self.assertIsInstance(reason, str)
                # A one-word "TODO" is not a reason. The whole point of the
                # registry is that skipping a table is an argued decision.
                self.assertGreaterEqual(
                    len(reason.strip()), 30,
                    f"{table}: exception needs a real justification, not {reason!r}",
                )

    def test_no_exception_is_stale(self):
        """A table that has since been given a policy must lose its excuse.

        This is deliberately a TEST failure and not a boot failure. Leaving a
        stale entry is a bookkeeping miss, not a security hole — the table is
        protected either way — and taking production down over bookkeeping
        would be a worse outcome than the thing being prevented. CI is the
        right place to catch it.
        """
        covered = set(preflight._tenant_scoped_tables_from_migration())
        stale = sorted(set(preflight._RLS_COVERAGE_EXCEPTIONS) & covered)
        self.assertEqual(
            stale, [],
            "These tables now have RLS in migrations/enable_rls.sql, so their "
            "entries in preflight._RLS_COVERAGE_EXCEPTIONS are obsolete and "
            f"must be deleted: {stale}",
        )


_CREATE_TABLE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:public\.)?([a-zA-Z_][a-zA-Z0-9_]*)\s*\(",
    re.IGNORECASE,
)
_ADD_SCOPE_COLUMN = re.compile(
    r"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:public\.)?([a-zA-Z_][a-zA-Z0-9_]*)\s+"
    r"ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?(tenant_id|workspace_id)\b",
    re.IGNORECASE,
)
_SCOPE_COLUMN_DECL = re.compile(r"(^|,)\s*(tenant_id|workspace_id)\b", re.IGNORECASE | re.MULTILINE)
_ENABLE_RLS = re.compile(
    r"ALTER\s+TABLE\s+(?:public\.)?([a-zA-Z_][a-zA-Z0-9_]*)\s+ENABLE\s+ROW\s+LEVEL\s+SECURITY",
    re.IGNORECASE,
)
_SKIP_DIRS = {".git", "node_modules", ".next", "target", "venv", ".venv", "__pycache__"}


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _scoped_tables_declared_in_source() -> dict:
    """Every table this repo's own DDL gives a tenant_id/workspace_id column.

    Deliberately a TEST-side scraper and not production code. The runtime check
    asks the live database, which is the authority on what actually exists; this
    asks the source, which is the authority on what someone just WROTE. The
    runtime check cannot see a table until the boot after the one that creates
    it (server.py runs preflight before ensure_control_plane_schema), so without
    this the alarm would always be one restart late.

    Tables are declared in three different shapes here — .sql migration files,
    a big CONTROL_PLANE_SCHEMA_SQL string, and inline `CREATE TABLE IF NOT
    EXISTS` inside per-module `_ensure_*_tables()` helpers (the last kind has
    no trailing semicolon, which is exactly how a naive scraper misses them) —
    so this walks the paren depth rather than looking for a terminator.
    """
    root = _repo_root()
    found: dict = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for filename in filenames:
            if not filename.endswith((".py", ".sql", ".ts")):
                continue
            path = os.path.join(dirpath, filename)
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                    source = handle.read()
            except OSError:
                continue
            for match in _CREATE_TABLE.finditer(source):
                index, depth = match.end(), 1
                while index < len(source) and depth:
                    if source[index] == "(":
                        depth += 1
                    elif source[index] == ")":
                        depth -= 1
                    index += 1
                body = source[match.end():index - 1]
                columns = {m.group(2).lower() for m in _SCOPE_COLUMN_DECL.finditer(body)}
                if columns:
                    found.setdefault(match.group(1).lower(), set()).update(columns)
            for match in _ADD_SCOPE_COLUMN.finditer(source):
                found.setdefault(match.group(1).lower(), set()).add(match.group(2).lower())
    return found


def _tables_with_rls_declared_anywhere() -> set:
    """Tables any file in the repo turns RLS on for, not just enable_rls.sql.

    `agent_computers` carries its own ENABLE + FORCE ROW LEVEL SECURITY in
    migrations/add_agent_computers.sql (and re-asserts it in
    agent_computers_repository.py's self-provisioning path). It is protected;
    it is simply protected somewhere else. The runtime check learns this by
    reading the live policy state — the source-side twin has to learn it by
    reading the source, or it would demand an exception entry for a table
    that does not need one.
    """
    root = _repo_root()
    declared: set = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for filename in filenames:
            if not filename.endswith((".py", ".sql")):
                continue
            try:
                with open(os.path.join(dirpath, filename), "r", encoding="utf-8", errors="ignore") as handle:
                    source = handle.read()
            except OSError:
                continue
            declared.update(m.group(1).lower() for m in _ENABLE_RLS.finditer(source))
    return declared


class SourceDeclaredCoverageTests(unittest.TestCase):
    """Every scoped table in this repo's DDL is either covered or excused.

    This is the CI-time twin of the boot-time check, and it is the one that
    actually fires on the pull request that introduces the problem. If it goes
    red, a new table was given a tenant_id/workspace_id column without anyone
    deciding what protects it — which is precisely the state 60 tables were
    already in when this check was written.
    """

    def test_no_scoped_table_is_both_unprotected_and_unrecorded(self):
        declared = _scoped_tables_declared_in_source()
        self.assertGreater(
            len(declared), 50,
            "the source scraper found almost nothing — it has stopped working, "
            "which would make this test vacuously green",
        )
        protected = _tables_with_rls_declared_anywhere()
        self.assertIn(
            "agent_computers", protected,
            "the RLS-declaration scraper stopped finding policies outside "
            "enable_rls.sql, which would make this test demand exceptions for "
            "tables that are already protected",
        )
        excused = set(preflight._RLS_COVERAGE_EXCEPTIONS)
        orphans = sorted(set(declared) - protected - excused)
        self.assertEqual(
            orphans, [],
            "These tables carry tenant_id/workspace_id, no migration turns RLS "
            "on for them, and preflight._RLS_COVERAGE_EXCEPTIONS does not "
            "record why. Add RLS in migrations/enable_rls.sql (only once every "
            "query against the table is confirmed scoped, or its reads will "
            f"silently return zero rows), or record the exemption: {orphans}",
        )


class _FakeConn:
    """Stands in for asyncpg — returns state rows first, discovery rows next.

    _check_rls issues exactly two fetches in order (_fetch_rls_state, then
    _discover_tenant_scoped_tables), so dispatching on call order is enough
    and avoids matching on SQL prose.
    """

    def __init__(self, state_rows, discovery_rows):
        self._responses = [state_rows, discovery_rows]
        self.fetch_calls = 0
        self.closed = False

    async def fetch(self, _query, *_args):
        self.fetch_calls += 1
        if not self._responses:
            raise AssertionError("unexpected extra fetch")
        return self._responses.pop(0)

    async def close(self):
        self.closed = True


def _all_enforced(tables):
    return [
        {"table_name": t, "rls_enabled": True, "rls_forced": True, "policy_count": 1}
        for t in tables
    ]


def _discovery(*tables):
    return [
        {
            "table_name": t,
            "scope_columns": ["tenant_id", "workspace_id"],
            "rls_enabled": True,
            "rls_forced": True,
            "policy_count": 1,
        }
        for t in tables
    ]


def _discovery_unprotected(*tables):
    return [
        {
            "table_name": t,
            "scope_columns": ["tenant_id", "workspace_id"],
            "rls_enabled": False,
            "rls_forced": False,
            "policy_count": 0,
        }
        for t in tables
    ]


class CheckRlsCoverageTests(unittest.TestCase):
    """The wiring: boot really does fail on an uncovered tenant-scoped table."""

    def _run_check(self, conn, env=None):
        environment = {"DATABASE_URL": "postgres://x", "EMPYRALIS_SKIP_RLS_CHECK": "",
                       "EMPYRALIS_SKIP_RLS_COVERAGE_CHECK": ""}
        environment.update(env or {})
        with patch.dict("os.environ", environment, clear=False), \
             patch("asyncpg.connect", new=AsyncMock(return_value=conn)):
            return asyncio.run(preflight._check_rls())

    def test_boot_fails_on_an_uncovered_tenant_scoped_table(self):
        expected = preflight._tenant_scoped_tables_from_migration()
        drifted = f"brand_new_scoped_table_{uuid.uuid4().hex[:8]}"
        conn = _FakeConn(_all_enforced(expected), _discovery(*expected) + _discovery_unprotected(drifted))
        err = self._run_check(conn)
        self.assertIsNotNone(err, "an unprotected tenant-scoped table must fail boot")
        self.assertIn(drifted, err)
        # The message has to tell the operator both ways out, or they will
        # reach for EMPYRALIS_SKIP_RLS_CHECK and lose the other 40 tables.
        self.assertIn("enable_rls.sql", err)
        self.assertIn("_RLS_COVERAGE_EXCEPTIONS", err)
        self.assertTrue(conn.closed)

    def test_boot_passes_when_every_scoped_table_is_covered(self):
        expected = preflight._tenant_scoped_tables_from_migration()
        conn = _FakeConn(_all_enforced(expected), _discovery(*expected))
        self.assertIsNone(self._run_check(conn))

    def test_boot_passes_when_the_only_uncovered_table_is_excused(self):
        expected = preflight._tenant_scoped_tables_from_migration()
        excused = sorted(preflight._RLS_COVERAGE_EXCEPTIONS)
        if not excused:
            self.skipTest("exception registry is empty")
        conn = _FakeConn(_all_enforced(expected), _discovery(*expected) + _discovery_unprotected(excused[0]))
        self.assertIsNone(self._run_check(conn))

    def test_narrow_skip_suppresses_coverage_only(self):
        """EMPYRALIS_SKIP_RLS_COVERAGE_CHECK must not become a way to switch
        off the enforcement half that has been protecting 40 tables."""
        expected = preflight._tenant_scoped_tables_from_migration()
        rows = _all_enforced(expected)
        rows[0]["rls_enabled"] = False  # a REAL enforcement gap
        broken = rows[0]["table_name"]
        drifted = "some_uncovered_table"
        conn = _FakeConn(rows, _discovery(*expected) + _discovery_unprotected(drifted))
        err = self._run_check(conn, {"EMPYRALIS_SKIP_RLS_COVERAGE_CHECK": "true"})
        self.assertIsNotNone(err)
        self.assertIn(broken, err)

    def test_narrow_skip_does_not_query_discovery_at_all(self):
        expected = preflight._tenant_scoped_tables_from_migration()
        conn = _FakeConn(_all_enforced(expected), _discovery(*expected) + _discovery_unprotected("drifted"))
        err = self._run_check(conn, {"EMPYRALIS_SKIP_RLS_COVERAGE_CHECK": "1"})
        self.assertIsNone(err)
        # Asserting the absence of a complaint is not enough on its own — it
        # cannot tell "coverage was skipped" from "coverage ran and found
        # nothing". The call count is what distinguishes them.
        self.assertEqual(conn.fetch_calls, 1)

    def test_enforcement_failure_is_reported_before_coverage(self):
        """Both broken at once: an unenforced policy is the more urgent fact
        (data is live and unguarded right now), so it must not be buried."""
        expected = preflight._tenant_scoped_tables_from_migration()
        rows = _all_enforced(expected)
        rows[0]["rls_forced"] = False
        conn = _FakeConn(rows, _discovery(*expected) + _discovery_unprotected("drifted"))
        err = self._run_check(conn)
        self.assertIsNotNone(err)
        self.assertIn("RLS not FORCEd", err)


@unittest.skipUnless(_database_url_available(), _NO_PG_REASON)
class DiscoveryQueryTests(unittest.TestCase):
    """The discovery SQL against a real Postgres.

    Everything above mocks the database, which proves the comparison but not
    the query that feeds it — and the query is where this check can go blind
    (miss a table = miss a hole). Runs against whatever disposable database
    DATABASE_URL names (conftest.py already refuses a DSN whose database name
    does not contain 'test'), creating and dropping its own tables.
    """

    def setUp(self):
        self.prefix = f"rlscov_{uuid.uuid4().hex[:8]}"

    def _run(self, coro):
        return asyncio.run(coro)

    async def _exercise(self):
        import asyncpg

        dsn = os.environ["DATABASE_URL"].strip()
        conn = await asyncpg.connect(dsn, timeout=10)
        p = self.prefix
        try:
            await conn.execute(
                f"""
                CREATE TABLE {p}_both (id text PRIMARY KEY, tenant_id text, workspace_id text);
                CREATE TABLE {p}_ws_only (id text PRIMARY KEY, workspace_id text);
                CREATE TABLE {p}_tenant_only (id text PRIMARY KEY, tenant_id text);
                CREATE TABLE {p}_neither (id text PRIMARY KEY, note text);
                CREATE VIEW {p}_view AS SELECT id, tenant_id, workspace_id FROM {p}_both;
                ALTER TABLE {p}_both ADD COLUMN dropped_scope text;
                ALTER TABLE {p}_both DROP COLUMN dropped_scope;

                -- Protected the way agent_computers is: its own ENABLE +
                -- FORCE + policy, nowhere near enable_rls.sql.
                CREATE TABLE {p}_protected (id text PRIMARY KEY, tenant_id text, workspace_id text);
                ALTER TABLE {p}_protected ENABLE ROW LEVEL SECURITY;
                ALTER TABLE {p}_protected FORCE ROW LEVEL SECURITY;
                CREATE POLICY {p}_protected_scope ON {p}_protected FOR ALL USING (true);

                -- Enabled but NOT forced and with no policy: the two
                -- half-measures that must not read as protection.
                CREATE TABLE {p}_enabled_only (id text PRIMARY KEY, tenant_id text, workspace_id text);
                ALTER TABLE {p}_enabled_only ENABLE ROW LEVEL SECURITY;
                """
            )
            discovered = await preflight._discover_tenant_scoped_tables(conn)
        finally:
            await conn.execute(
                f"""
                DROP VIEW IF EXISTS {p}_view;
                DROP TABLE IF EXISTS {p}_both, {p}_ws_only, {p}_tenant_only,
                                     {p}_neither, {p}_protected, {p}_enabled_only;
                """
            )
            await conn.close()
        return discovered

    def test_discovers_scope_columns_and_ignores_everything_else(self):
        discovered = self._run(self._exercise())
        p = self.prefix

        self.assertEqual(discovered.get(f"{p}_both", {}).get("scope_columns"),
                         ["tenant_id", "workspace_id"])
        self.assertEqual(discovered.get(f"{p}_ws_only", {}).get("scope_columns"),
                         ["workspace_id"])
        self.assertEqual(discovered.get(f"{p}_tenant_only", {}).get("scope_columns"),
                         ["tenant_id"])

        # A table with no scope column has no tenant boundary to enforce.
        self.assertNotIn(f"{p}_neither", discovered)
        # A view cannot carry a policy of its own; flagging it would be noise
        # that trains people to ignore this check.
        self.assertNotIn(f"{p}_view", discovered)

    def test_reads_real_policy_state_not_just_column_names(self):
        """`protected` must come from the live catalog, or the check would
        flag every table protected outside enable_rls.sql — agent_computers
        being the one that actually exists today."""
        discovered = self._run(self._exercise())
        p = self.prefix

        self.assertTrue(discovered.get(f"{p}_protected", {}).get("protected"))
        # ENABLE without FORCE and without a policy is not protection: the
        # owner bypasses an unFORCEd policy, and there is no policy anyway.
        self.assertFalse(discovered.get(f"{p}_enabled_only", {}).get("protected"))
        self.assertFalse(discovered.get(f"{p}_both", {}).get("protected"))

    def test_finds_the_tables_the_migration_already_covers(self):
        """Sanity that discovery and the migration speak about the same
        schema — if this came back empty, the check would silently pass
        forever, which is the exact failure it exists to prevent."""
        import asyncpg

        async def go():
            conn = await asyncpg.connect(os.environ["DATABASE_URL"].strip(), timeout=10)
            try:
                return await preflight._discover_tenant_scoped_tables(conn)
            finally:
                await conn.close()

        discovered = self._run(go())
        covered = set(preflight._tenant_scoped_tables_from_migration())
        present = {t for t in covered if t in discovered}
        if not present:
            self.skipTest("this database has none of the app's tables (bare test DB)")
        for table in sorted(present):
            with self.subTest(table=table):
                self.assertTrue(
                    set(discovered[table].get("scope_columns") or []) & {"tenant_id", "workspace_id"},
                    f"{table} is in enable_rls.sql but discovery found no scope column",
                )


if __name__ == "__main__":
    unittest.main()

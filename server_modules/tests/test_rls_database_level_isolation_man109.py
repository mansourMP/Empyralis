"""MAN-109: does Postgres Row-Level Security actually isolate tenants at the
database level -- and, honestly, where does it not (yet)?

migrations/enable_rls.sql puts ENABLE/FORCE ROW LEVEL SECURITY plus the
`empyralis_rls_scope_match` policy on 29 tables. `project_tasks`, `projects`,
`project_memberships`, `workspace_labels`, `bug_reports`, and
`mcp_external_agent_roster` are deliberately excluded (see
migrations/add_project_tasks.sql:16-23 and the module docstrings of
project_tasks_service.py / projects_repository.py / bug_report_service.py /
mcp_external_agent_roster_service.py) -- "every query filters by (tenant_id,
workspace_id) explicitly instead."

Investigating whether those six tables could safely get the same RLS
treatment (this file's original assignment) found that they CANNOT, not
without a broader refactor first: project_tasks_service.py, projects_
repository.py, workspace_labels_service.py, bug_report_service.py, and
mcp_external_agent_roster_service.py issue 43 combined `pool.fetch/fetchrow/
execute(...)` calls against these six tables that never set the `app.
current_tenant_id` / `app.current_workspace_id` session GUCs
control_plane_repository._apply_connection_scope sets before every query
against an RLS-protected table (only 4 call sites in projects_repository.py
already route through the scoped `rls_fetch`/`rls_execute` helpers, and only
incidentally -- because those particular functions ALSO touch
workspace_agent_installs, which already has RLS). Turning on FORCE ROW LEVEL
SECURITY on these six tables today, without first converting all 43 call
sites, would make every one of those bare pool calls start silently
returning zero rows (reads) or raising a WITH CHECK violation (writes) --
exactly the "half-applied RLS policy that breaks reads in production"
outcome the assignment said to avoid. Worse, mcp_external_agent_roster_
service.get_external_agent_by_key_hash looks up a roster row BY BEARER KEY
HASH ALONE, with no tenant_id/workspace_id known yet (that IS the lookup
that resolves which tenant a request belongs to) -- structurally the same
"auth bootstrap, tenant unknown until this query returns" shape control_
plane_repository.py's own docstring already documents needing a deliberate
`bypass_rls=True` escape hatch for (self-hosted node auth). Getting that
one call site wrong either breaks all MCP auth or silently reintroduces a
bypass that defeats the whole point of adding RLS. This is real, multi-file,
call-site-by-call-site work -- not something to rush through inside this
same change. No migration was applied and preflight's verified-table list
(which is parsed live from migrations/enable_rls.sql, so it updates itself
--  see preflight._tenant_scoped_tables_from_migration) was intentionally
left untouched.

What THIS file proves instead, with real SQL that bypasses BOTH the Python
service layer AND the connecting role's own privilege level:

  1. The RLS MECHANISM genuinely isolates at the database level today, for
     an already-covered table (`agent_threads`) -- not a claim about code
     discipline, a Postgres policy actually filtering rows.
  2. Normal same-tenant reads are unaffected (the regression guard) --
     RLS filters OTHER tenants' rows, not the correct tenant's own.
  3. The reported gap is real, right now, for `projects`/`project_tasks`:
     a query scoped to tenant A can read tenant B's project/task rows with
     no explicit tenant filter in the WHERE clause at all. This test is
     EXPECTED TO START FAILING the moment a future change finishes the
     call-site refactor and lands the migration for these six tables --
     that is success, not a broken test; whoever lands that follow-up
     should replace test_projects_and_project_tasks_have_no_database_level_
     tenant_isolation_yet with an isolation proof shaped like class
     AgentThreadsRlsIsolationTests above it, not just delete it.

Real Postgres only, skipped when DATABASE_URL is not already exported --
mirrors test_project_tasks_human_assignee.py's own opt-in convention (no
.env reading, no os.environ mutation here). Crucially, every proof query
below does NOT use the DATABASE_URL connection to read data -- Postgres
superusers (and any role with the BYPASSRLS attribute) bypass row security
UNCONDITIONALLY, regardless of FORCE ROW LEVEL SECURITY; the local dev
DATABASE_URL role in .env.example (`postgres`) IS a superuser, so reading
through that connection would "prove" isolation whether or not the policy
even exists. Each test opens its own connection as a freshly minted,
ordinary (non-superuser, non-BYPASSRLS) throwaway Postgres role, granted
only the table privileges a real app connection would have, and drops it in
tearDown -- the DATABASE_URL connection is used only for admin setup/teardown
(seeding rows, creating/dropping the throwaway role), never for the read
that is actually being tested.
"""

from __future__ import annotations

import inspect
import os
import unittest
import uuid
from urllib.parse import urlsplit, urlunsplit


def _database_url_available() -> bool:
    """Opt-in, ONLY from an already-exported DATABASE_URL -- see
    test_project_tasks_human_assignee.py's own _database_url_available for
    the full reasoning (no .env reading, no os.environ mutation, so a plain
    `pytest` run leaves every later test's global state untouched)."""
    return bool(os.getenv("DATABASE_URL", "").strip())


def _run(coro):
    """Same persistent bridge loop test_project_tasks_human_assignee.py /
    test_task_subtasks_and_labels.py use -- see those files' _run docstring
    for why this is not a stylistic choice (asyncpg connections opened
    against one event loop cannot be used from another)."""
    from server_modules import sync_asyncio_bridge

    return sync_asyncio_bridge.run_coro_sync(coro)


class _BridgeAsyncTestCase(unittest.TestCase):
    def _maybe_await(self, value):
        return _run(value) if inspect.iscoroutine(value) else value

    def _callTestMethod(self, method):
        self._maybe_await(method())

    def setUp(self):
        try:
            self._maybe_await(self.async_setup())
        except Exception:
            # This is a shared local Postgres other work may be using
            # concurrently — a setup that fails partway through (e.g. a seed
            # INSERT tripping a CHECK constraint after the throwaway role
            # was already created) must not leave that role or any seed
            # rows behind just because unittest never calls tearDown() when
            # setUp() raises. Best-effort: clean up whatever got created so
            # far, then re-raise the original failure untouched.
            try:
                self._maybe_await(self.async_teardown())
            except Exception:
                pass
            raise

    def tearDown(self):
        self._maybe_await(self.async_teardown())

    async def async_setup(self):
        return None

    async def async_teardown(self):
        return None


def _probe_dsn(admin_dsn: str, *, user: str, password: str) -> str:
    """Same host/port/db as DATABASE_URL, swapped to throwaway credentials
    -- never the admin role, so RLS actually gets exercised."""
    parts = urlsplit(admin_dsn)
    netloc = f"{user}:{password}@{parts.hostname or 'localhost'}"
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


_NO_PG_REASON = "No Postgres reachable (DATABASE_URL unset — export it to run this suite)"


class _RlsProbeRoleFixture(_BridgeAsyncTestCase):
    """Shared plumbing: an admin (DATABASE_URL) connection for setup/teardown
    DDL and seed data, plus one throwaway non-superuser role -- created fresh
    per test, dropped in teardown -- used for every actual proof read."""

    #: tables this fixture's throwaway role needs privileges on; set by subclasses.
    GRANT_TABLES: tuple[str, ...] = ()

    async def async_setup(self):
        if not _database_url_available():
            self.skipTest(_NO_PG_REASON)
            return
        import asyncpg

        self.admin_dsn = os.environ["DATABASE_URL"].strip()
        try:
            self.admin_conn = await asyncpg.connect(self.admin_dsn, timeout=10)
        except Exception as exc:  # noqa: BLE001 — unreachable Postgres is a skip
            self.skipTest(f"{_NO_PG_REASON}: {exc}")
            return

        suffix = uuid.uuid4().hex[:10]
        self.tenant_a = f"t_rls_a_{suffix}"
        self.tenant_b = f"t_rls_b_{suffix}"
        self.ws_a = f"ws_rls_a_{suffix}"
        self.ws_b = f"ws_rls_b_{suffix}"
        self.role = f"rls_probe_{suffix}"
        # Ephemeral, hex-only (safe to inline — no quoting hazard), scoped to
        # this one throwaway role and dropped with it. Never written to disk,
        # never reused across a second test run.
        self.role_password = uuid.uuid4().hex

        await self.admin_conn.execute(
            f'CREATE ROLE "{self.role}" LOGIN PASSWORD \'{self.role_password}\''
        )
        if self.GRANT_TABLES:
            table_list = ", ".join(self.GRANT_TABLES)
            await self.admin_conn.execute(
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table_list} TO \"{self.role}\""
            )
        self.probe_dsn = _probe_dsn(self.admin_dsn, user=self.role, password=self.role_password)

    async def async_teardown(self):
        conn = getattr(self, "admin_conn", None)
        if conn is None:
            return
        try:
            await self._cleanup_rows(conn)
        finally:
            if self.GRANT_TABLES:
                table_list = ", ".join(self.GRANT_TABLES)
                await conn.execute(f'REVOKE ALL ON {table_list} FROM "{self.role}"')
            await conn.execute(f'DROP ROLE IF EXISTS "{self.role}"')
            await conn.close()

    async def _cleanup_rows(self, conn) -> None:
        """Subclasses delete exactly the rows they inserted."""
        return None

    async def _scoped_probe_read(self, tenant_id: str, workspace_id: str, query: str, *args):
        """Open a fresh connection AS THE THROWAWAY ROLE (never the admin
        connection), set the RLS session GUCs the exact way control_plane_
        repository._apply_connection_scope does (set_config(..., true) --
        local to the transaction — inside an explicit transaction), and run
        `query`. This is the service layer's own scoping mechanism, called
        directly with no service-layer code in between — the point is to
        prove the POLICY does the filtering, not application discipline."""
        import asyncpg

        conn = await asyncpg.connect(self.probe_dsn, timeout=10)
        try:
            async with conn.transaction():
                await conn.execute(
                    """
                    SELECT
                        set_config('app.current_tenant_id', $1, true),
                        set_config('app.current_workspace_id', $2, true),
                        set_config('app.rls_bypass', 'off', true)
                    """,
                    tenant_id,
                    workspace_id,
                )
                return await conn.fetch(query, *args)
        finally:
            await conn.close()


class AgentThreadsRlsIsolationTests(_RlsProbeRoleFixture):
    """Proof #1 + #2 (isolation, and the regression guard) against
    `agent_threads` -- one of the 29 tables migrations/enable_rls.sql
    already covers. This is the mechanism the six excluded tables would
    inherit once their call sites are converted; proving it holds here is
    what makes the "not safe to flip on yet" conclusion below meaningful
    rather than a guess."""

    GRANT_TABLES = ("agent_threads",)

    async def async_setup(self):
        await super().async_setup()
        if not hasattr(self, "admin_conn"):
            return
        await self.admin_conn.execute(
            """
            INSERT INTO agent_threads (id, tenant_id, workspace_id, title)
            VALUES ($1, $2, $3, 'Tenant A thread'), ($4, $5, $6, 'Tenant B thread')
            ON CONFLICT (id) DO NOTHING
            """,
            f"thread_a_{self.tenant_a}", self.tenant_a, self.ws_a,
            f"thread_b_{self.tenant_b}", self.tenant_b, self.ws_b,
        )
        self.thread_a_id = f"thread_a_{self.tenant_a}"
        self.thread_b_id = f"thread_b_{self.tenant_b}"

    async def _cleanup_rows(self, conn) -> None:
        await conn.execute(
            "DELETE FROM agent_threads WHERE tenant_id IN ($1, $2)",
            self.tenant_a, self.tenant_b,
        )

    async def test_rls_hides_the_other_tenants_row_with_no_where_clause_filter(self) -> None:
        """THE mandatory proof: a query with NO tenant_id/workspace_id filter
        anywhere in its WHERE clause, run by an ordinary (non-superuser) role
        scoped to tenant A, must not see tenant B's row. This bypasses
        project_tasks_service-style "the app remembers to filter" discipline
        entirely -- the query below asks for both ids by id alone."""
        rows = await self._scoped_probe_read(
            self.tenant_a, self.ws_a,
            "SELECT id, tenant_id FROM agent_threads WHERE id = ANY($1)",
            [self.thread_a_id, self.thread_b_id],
        )
        seen_ids = {row["id"] for row in rows}
        self.assertIn(self.thread_a_id, seen_ids)
        self.assertNotIn(self.thread_b_id, seen_ids, "RLS failed to hide tenant B's row from tenant A's session.")
        self.assertEqual(len(rows), 1)

    async def test_regression_guard_normal_reads_still_return_the_right_tenants_row(self) -> None:
        """Isolation is not "returns nothing for everyone" -- each tenant,
        scoped to itself, still gets its OWN row back correctly. Checked
        symmetrically (tenant A sees A but not B; tenant B sees B but not A)
        so a policy bug that happened to hide EVERYTHING wouldn't slip past
        the first test's assertions."""
        rows_a = await self._scoped_probe_read(
            self.tenant_a, self.ws_a,
            "SELECT id, tenant_id, title FROM agent_threads WHERE id = ANY($1)",
            [self.thread_a_id, self.thread_b_id],
        )
        self.assertEqual(len(rows_a), 1)
        self.assertEqual(rows_a[0]["id"], self.thread_a_id)
        self.assertEqual(rows_a[0]["title"], "Tenant A thread")

        rows_b = await self._scoped_probe_read(
            self.tenant_b, self.ws_b,
            "SELECT id, tenant_id, title FROM agent_threads WHERE id = ANY($1)",
            [self.thread_a_id, self.thread_b_id],
        )
        self.assertEqual(len(rows_b), 1)
        self.assertEqual(rows_b[0]["id"], self.thread_b_id)
        self.assertEqual(rows_b[0]["title"], "Tenant B thread")

    async def test_catalog_confirms_rls_enabled_and_forced(self) -> None:
        """Sanity check on the mechanism itself, straight from Postgres'
        catalog (same columns preflight._fetch_rls_state reads in
        production) -- agent_threads really is ENABLE + FORCE, which is
        exactly why the two tests above see real filtering."""
        row = await self.admin_conn.fetchrow(
            """
            SELECT c.relrowsecurity AS rls_enabled, c.relforcerowsecurity AS rls_forced
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relname = 'agent_threads'
            """
        )
        self.assertIsNotNone(row)
        self.assertTrue(row["rls_enabled"])
        self.assertTrue(row["rls_forced"])


class ProjectsAndProjectTasksRlsGapTests(_RlsProbeRoleFixture):
    """Proof #3: the honest characterization of the current gap. `projects`
    and `project_tasks` have NO RLS policy today, so a query scoped to
    tenant A can read tenant B's rows straight out of these tables with no
    WHERE-clause filter at all -- the exact structural risk MAN-109 flags
    ("one future query that forgets the WHERE leaks across tenants
    silently"), demonstrated rather than asserted.

    This test is SUPPOSED to start failing once a future change finishes
    converting project_tasks_service.py / projects_repository.py's 26
    combined unscoped call sites to the rls_fetch/rls_fetchrow/rls_execute
    helpers and lands the enable_rls.sql-style migration for these two
    tables -- at that point, replace this test with an isolation proof
    shaped like AgentThreadsRlsIsolationTests above, not just delete it.
    """

    GRANT_TABLES = ("projects", "project_tasks")

    async def async_setup(self):
        await super().async_setup()
        if not hasattr(self, "admin_conn"):
            return
        self.project_a_id = f"proj_a_{self.tenant_a}"
        self.project_b_id = f"proj_b_{self.tenant_b}"
        self.task_a_id = f"task_a_{self.tenant_a}"
        self.task_b_id = f"task_b_{self.tenant_b}"
        await self.admin_conn.execute(
            """
            INSERT INTO projects (id, tenant_id, workspace_id, name, slug)
            VALUES ($1, $2, $3, 'Tenant A project', $1), ($4, $5, $6, 'Tenant B project', $4)
            ON CONFLICT (id) DO NOTHING
            """,
            self.project_a_id, self.tenant_a, self.ws_a,
            self.project_b_id, self.tenant_b, self.ws_b,
        )
        await self.admin_conn.execute(
            """
            INSERT INTO project_tasks (id, tenant_id, workspace_id, project_id, title, status)
            VALUES ($1, $2, $3, $4, 'Tenant A task', 'todo'), ($5, $6, $7, $8, 'Tenant B task', 'todo')
            ON CONFLICT (id) DO NOTHING
            """,
            self.task_a_id, self.tenant_a, self.ws_a, self.project_a_id,
            self.task_b_id, self.tenant_b, self.ws_b, self.project_b_id,
        )

    async def _cleanup_rows(self, conn) -> None:
        await conn.execute(
            "DELETE FROM project_tasks WHERE tenant_id IN ($1, $2)",
            self.tenant_a, self.tenant_b,
        )
        await conn.execute(
            "DELETE FROM projects WHERE tenant_id IN ($1, $2)",
            self.tenant_a, self.tenant_b,
        )

    async def test_projects_and_project_tasks_have_no_database_level_tenant_isolation_yet(self) -> None:
        project_rows = await self._scoped_probe_read(
            self.tenant_a, self.ws_a,
            "SELECT id, tenant_id FROM projects WHERE id = ANY($1)",
            [self.project_a_id, self.project_b_id],
        )
        task_rows = await self._scoped_probe_read(
            self.tenant_a, self.ws_a,
            "SELECT id, tenant_id FROM project_tasks WHERE id = ANY($1)",
            [self.task_a_id, self.task_b_id],
        )
        project_ids_seen = {row["id"] for row in project_rows}
        task_ids_seen = {row["id"] for row in task_rows}
        # This is the GAP, demonstrated: tenant A's session can read tenant
        # B's row from both tables at the database level. If this assertion
        # ever fails, someone has fixed MAN-109 for these tables -- replace
        # this test with an isolation proof, don't just delete it.
        self.assertEqual(
            project_ids_seen, {self.project_a_id, self.project_b_id},
            "projects now isolates tenants at the database level — update this "
            "characterization test into an isolation proof (see AgentThreadsRlsIsolationTests).",
        )
        self.assertEqual(
            task_ids_seen, {self.task_a_id, self.task_b_id},
            "project_tasks now isolates tenants at the database level — update this "
            "characterization test into an isolation proof (see AgentThreadsRlsIsolationTests).",
        )

    async def test_catalog_confirms_no_rls_policy_on_either_table(self) -> None:
        rows = await self.admin_conn.fetch(
            """
            SELECT c.relname AS table_name, c.relrowsecurity AS rls_enabled,
                   c.relforcerowsecurity AS rls_forced
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relname IN ('projects', 'project_tasks')
            """
        )
        state = {row["table_name"]: row for row in rows}
        for table in ("projects", "project_tasks"):
            with self.subTest(table=table):
                self.assertFalse(state[table]["rls_enabled"])
                self.assertFalse(state[table]["rls_forced"])


if __name__ == "__main__":
    unittest.main()

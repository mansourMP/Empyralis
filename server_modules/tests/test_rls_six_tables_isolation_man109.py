"""MAN-109 follow-up: the replacement test_rls_database_level_isolation_
man109.py's own docstring asked for.

That file investigated turning on RLS for six tables -- `project_tasks`,
`projects`, `project_memberships`, `workspace_labels`, `bug_reports`,
`mcp_external_agent_roster` -- and found it unsafe to do YET: 43 combined
`pool.fetch/fetchrow/fetchval/execute(...)` call sites across project_tasks_
service.py / projects_repository.py / workspace_labels_service.py /
bug_report_service.py / mcp_external_agent_roster_service.py never set the
`app.current_tenant_id` / `app.current_workspace_id` session GUCs RLS reads,
so flipping FORCE ROW LEVEL SECURITY on before converting them would have
made every one of those call sites start silently returning zero rows on
reads and raising WITH CHECK violations on writes. Its own words: "This test
is EXPECTED TO START FAILING the moment a future change finishes the
call-site refactor and lands the migration for these six tables -- that is
success, not a broken test; whoever lands that follow-up should replace
test_projects_and_project_tasks_have_no_database_level_tenant_isolation_yet
with an isolation proof shaped like class AgentThreadsRlsIsolationTests
above it, not just delete it."

That conversion (all 43 call sites, plus the one genuine auth-bootstrap
exception documented on mcp_external_agent_roster_service.
get_external_agent_by_key_hash) and the migrations/enable_rls.sql extension
to all six tables landed in this same change. THIS is that replacement.

NOT editing test_rls_database_level_isolation_man109.py itself: MAN-109's
collision protocol assigns server_modules/tests/conftest.py and every
pre-existing test file to a different, concurrently-running agent -- new
tests go in new files, this is one. Landing this migration is EXPECTED to
flip that file's `ProjectsAndProjectTasksRlsGapTests` red (both
`test_projects_and_project_tasks_have_no_database_level_tenant_isolation_yet`
and `test_catalog_confirms_no_rls_policy_on_either_table` assert the GAP
exists; it no longer does). That is this change working as intended, per
that class's own docstring -- not a regression this file introduced. Whoever
owns that file next should retire those two characterization tests in favor
of the isolation proofs below.

THREE separate concerns, three test classes, because they are not the same
proof and conflating them would prove less than each alone:

1. ``SixTableRlsIsolationTests`` -- the database-level mechanism, exactly
   the methodology ``AgentThreadsRlsIsolationTests`` established: a
   throwaway, ORDINARY (non-superuser, non-BYPASSRLS) Postgres role, opened
   fresh, scoped via the same `set_config(...)` calls `_apply_connection_
   scope` uses, running a query with NO tenant_id/workspace_id filter
   anywhere in its WHERE clause. No service-layer code anywhere in this
   class. This is what proves the POLICY filters, not "the app remembered
   to filter" -- run once per table, across all six.

2. ``ServiceLayerRegressionTests`` -- the OTHER real risk this change
   carries: converting 43 call sites from `pool.fetch(query, *args)` to
   `rls_fetch(pool, query, *args, tenant_id=..., workspace_id=...)` is a
   mechanical-looking edit that is easy to get subtly wrong (a dropped
   positional arg, tenant_id and workspace_id swapped, a param that should
   have stayed unscoped). This class calls the REAL, PUBLIC functions in
   all five converted services end-to-end -- create/read/list/update
   through the actual code paths a route or an MCP tool would hit, never
   hand-rolled SQL -- for two different tenants, over the app's own
   connection pool. That pool is the superuser DATABASE_URL role locally
   (see class 1's own module for why that means this is NOT a second
   isolation proof: superusers bypass RLS unconditionally, FORCE or not).
   What it DOES prove: every converted call site still returns exactly the
   rows/behavior its caller expects, post-refactor.

3. ``AuthBootstrapBypassTests`` -- the one `bypass_rls=True` this change
   adds. Proves both halves: `get_external_agent_by_key_hash` still
   resolves a roster row by key_hash alone through the real service
   function (the bypass didn't break the happy path), AND -- the actual
   point of needing a bypass at all -- an ordinary role scoped to NO
   tenant/workspace (`app.rls_bypass` off, exactly resolve_workspace_from_
   api_key's state before it has learned anything) genuinely cannot read
   that row without it. If this second half failed, the bypass would be
   theater: the row would already have been reachable some other way and
   `bypass_rls=True` would be a hole opened for nothing.

Real Postgres only, opt-in from an already-exported DATABASE_URL -- same
convention as the rest of this suite (no .env reading, no os.environ
mutation). Every throwaway role this file creates is dropped in tearDown,
and every row it inserts is deleted, even when a test fails partway
(mirrors _RlsProbeRoleFixture's own setUp/tearDown contract).
"""

from __future__ import annotations

import inspect
import os
import unittest
import uuid
from urllib.parse import urlsplit, urlunsplit


def _database_url_available() -> bool:
    return bool(os.getenv("DATABASE_URL", "").strip())


def _run(coro):
    """Same persistent bridge loop the rest of this suite uses -- asyncpg
    connections (and this app's own `db.get_pool()` cache, keyed by
    `id(current_loop)`) cannot be used from a second event loop, so every
    async call in this file, including calls into the real service layer,
    must land on the SAME loop across setUp/test/tearDown."""
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
            # Shared local Postgres other agents may be using concurrently --
            # a setup that fails partway must not leave a throwaway role or
            # seed rows behind just because unittest never calls tearDown()
            # when setUp() raises.
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
    parts = urlsplit(admin_dsn)
    netloc = f"{user}:{password}@{parts.hostname or 'localhost'}"
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


_NO_PG_REASON = "No Postgres reachable (DATABASE_URL unset — export it to run this suite)"


class _RlsProbeRoleFixture(_BridgeAsyncTestCase):
    """Same shared plumbing as test_rls_database_level_isolation_man109.py's
    fixture of the same name, duplicated rather than imported: importing a
    class from a file a different concurrently-running agent owns (per
    MAN-109's collision protocol) would couple this file's correctness to
    edits happening there right now. This is intentionally a byte-for-byte
    port of the same approach the assignment said to reuse."""

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
        self.tenant_a = f"t_six_a_{suffix}"
        self.tenant_b = f"t_six_b_{suffix}"
        self.ws_a = f"ws_six_a_{suffix}"
        self.ws_b = f"ws_six_b_{suffix}"
        self.role = f"rls_six_probe_{suffix}"
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
        return None

    async def _scoped_probe_read(self, tenant_id: str, workspace_id: str, query: str, *args, bypass: bool = False):
        """Open a fresh connection AS THE THROWAWAY ROLE, set the RLS
        session GUCs exactly the way `_apply_connection_scope` does, run
        `query`. `bypass=True` sets `app.rls_bypass` to 'on' -- the same
        state `rls_fetchrow(..., bypass_rls=True)` produces -- for the
        auth-bootstrap proof below."""
        import asyncpg

        conn = await asyncpg.connect(self.probe_dsn, timeout=10)
        try:
            async with conn.transaction():
                await conn.execute(
                    """
                    SELECT
                        set_config('app.current_tenant_id', $1, true),
                        set_config('app.current_workspace_id', $2, true),
                        set_config('app.rls_bypass', $3, true)
                    """,
                    tenant_id,
                    workspace_id,
                    "on" if bypass else "off",
                )
                return await conn.fetch(query, *args)
        finally:
            await conn.close()


# ── 1. Database-level isolation, all six tables ─────────────────────────


class SixTableRlsIsolationTests(_RlsProbeRoleFixture):
    """One class, all six tables that migrations/enable_rls.sql just picked
    up: `project_tasks`, `projects`, `project_memberships`,
    `workspace_labels`, `bug_reports`, `mcp_external_agent_roster`. Seeds one
    row per tenant per table as the ADMIN (superuser) connection, then reads
    with NO tenant_id/workspace_id filter as the throwaway ordinary role
    scoped to tenant A only. Every read must see tenant A's row and MUST NOT
    see tenant B's -- the exact query shape (id-only WHERE clause) that
    "the app forgot to filter" would otherwise leak through."""

    GRANT_TABLES = (
        "project_tasks", "projects", "project_memberships",
        "workspace_labels", "bug_reports", "mcp_external_agent_roster",
    )

    async def async_setup(self):
        await super().async_setup()
        if not hasattr(self, "admin_conn"):
            return

        self.project_a_id = f"proj_a_{self.tenant_a}"
        self.project_b_id = f"proj_b_{self.tenant_b}"
        await self.admin_conn.execute(
            """
            INSERT INTO projects (id, tenant_id, workspace_id, name, slug)
            VALUES ($1, $2, $3, 'Tenant A project', $1), ($4, $5, $6, 'Tenant B project', $4)
            """,
            self.project_a_id, self.tenant_a, self.ws_a,
            self.project_b_id, self.tenant_b, self.ws_b,
        )

        self.task_a_id = f"task_a_{self.tenant_a}"
        self.task_b_id = f"task_b_{self.tenant_b}"
        await self.admin_conn.execute(
            """
            INSERT INTO project_tasks (id, tenant_id, workspace_id, project_id, title, status)
            VALUES ($1, $2, $3, $4, 'Tenant A task', 'todo'), ($5, $6, $7, $8, 'Tenant B task', 'todo')
            """,
            self.task_a_id, self.tenant_a, self.ws_a, self.project_a_id,
            self.task_b_id, self.tenant_b, self.ws_b, self.project_b_id,
        )

        self.user_a_id = f"user_a_{self.tenant_a}"
        self.user_b_id = f"user_b_{self.tenant_b}"
        await self.admin_conn.execute(
            """
            INSERT INTO users (id, tenant_id, workspace_id, email)
            VALUES ($1, $2, $3, $4), ($5, $6, $7, $8)
            """,
            self.user_a_id, self.tenant_a, self.ws_a, f"{self.user_a_id}@example.test",
            self.user_b_id, self.tenant_b, self.ws_b, f"{self.user_b_id}@example.test",
        )
        self.membership_a_id = f"pm_a_{self.tenant_a}"
        self.membership_b_id = f"pm_b_{self.tenant_b}"
        await self.admin_conn.execute(
            """
            INSERT INTO project_memberships (id, tenant_id, workspace_id, project_id, user_id, role)
            VALUES ($1, $2, $3, $4, $5, 'owner'), ($6, $7, $8, $9, $10, 'owner')
            """,
            self.membership_a_id, self.tenant_a, self.ws_a, self.project_a_id, self.user_a_id,
            self.membership_b_id, self.tenant_b, self.ws_b, self.project_b_id, self.user_b_id,
        )

        self.label_a_id = f"label_a_{self.tenant_a}"
        self.label_b_id = f"label_b_{self.tenant_b}"
        await self.admin_conn.execute(
            """
            INSERT INTO workspace_labels (id, tenant_id, workspace_id, name, color)
            VALUES ($1, $2, $3, 'bug', 'red'), ($4, $5, $6, 'bug', 'red')
            """,
            self.label_a_id, self.tenant_a, self.ws_a,
            self.label_b_id, self.tenant_b, self.ws_b,
        )

        self.bug_report_a_id = f"bugreport_a_{self.tenant_a}"
        self.bug_report_b_id = f"bugreport_b_{self.tenant_b}"
        await self.admin_conn.execute(
            """
            INSERT INTO bug_reports (id, tenant_id, workspace_id, title)
            VALUES ($1, $2, $3, 'Tenant A bug'), ($4, $5, $6, 'Tenant B bug')
            """,
            self.bug_report_a_id, self.tenant_a, self.ws_a,
            self.bug_report_b_id, self.tenant_b, self.ws_b,
        )

        self.roster_a_id = f"ext_agent_a_{self.tenant_a}"
        self.roster_b_id = f"ext_agent_b_{self.tenant_b}"
        await self.admin_conn.execute(
            """
            INSERT INTO mcp_external_agent_roster (id, tenant_id, workspace_id, display_name, key_hash)
            VALUES ($1, $2, $3, 'Agent A', $4), ($5, $6, $7, 'Agent B', $8)
            """,
            self.roster_a_id, self.tenant_a, self.ws_a, f"hash_a_{self.tenant_a}",
            self.roster_b_id, self.tenant_b, self.ws_b, f"hash_b_{self.tenant_b}",
        )

    async def _cleanup_rows(self, conn) -> None:
        # Children before parents (FK ordering), and IN ($1, $2) scoped to
        # exactly this test's two tenants -- never a broader DELETE against a
        # shared table other concurrently-running agents may also be using.
        await conn.execute("DELETE FROM mcp_external_agent_roster WHERE tenant_id IN ($1, $2)", self.tenant_a, self.tenant_b)
        await conn.execute("DELETE FROM bug_reports WHERE tenant_id IN ($1, $2)", self.tenant_a, self.tenant_b)
        await conn.execute("DELETE FROM workspace_labels WHERE tenant_id IN ($1, $2)", self.tenant_a, self.tenant_b)
        await conn.execute("DELETE FROM project_memberships WHERE tenant_id IN ($1, $2)", self.tenant_a, self.tenant_b)
        await conn.execute("DELETE FROM users WHERE tenant_id IN ($1, $2)", self.tenant_a, self.tenant_b)
        await conn.execute("DELETE FROM project_tasks WHERE tenant_id IN ($1, $2)", self.tenant_a, self.tenant_b)
        await conn.execute("DELETE FROM projects WHERE tenant_id IN ($1, $2)", self.tenant_a, self.tenant_b)

    def _assert_isolated(self, *, table: str, id_column: str, id_a: str, id_b: str, rows) -> None:
        seen = {row[id_column] for row in rows}
        self.assertIn(id_a, seen, f"{table}: tenant A's own row went missing under its own session scope.")
        self.assertNotIn(id_b, seen, f"{table}: RLS failed to hide tenant B's row from tenant A's session.")
        self.assertEqual(len(rows), 1, f"{table}: expected exactly tenant A's one row.")

    async def test_projects_isolated(self) -> None:
        rows = await self._scoped_probe_read(
            self.tenant_a, self.ws_a,
            "SELECT id, tenant_id FROM projects WHERE id = ANY($1)",
            [self.project_a_id, self.project_b_id],
        )
        self._assert_isolated(table="projects", id_column="id", id_a=self.project_a_id, id_b=self.project_b_id, rows=rows)

    async def test_project_tasks_isolated(self) -> None:
        rows = await self._scoped_probe_read(
            self.tenant_a, self.ws_a,
            "SELECT id, tenant_id FROM project_tasks WHERE id = ANY($1)",
            [self.task_a_id, self.task_b_id],
        )
        self._assert_isolated(table="project_tasks", id_column="id", id_a=self.task_a_id, id_b=self.task_b_id, rows=rows)

    async def test_project_memberships_isolated(self) -> None:
        rows = await self._scoped_probe_read(
            self.tenant_a, self.ws_a,
            "SELECT id, tenant_id FROM project_memberships WHERE id = ANY($1)",
            [self.membership_a_id, self.membership_b_id],
        )
        self._assert_isolated(table="project_memberships", id_column="id", id_a=self.membership_a_id, id_b=self.membership_b_id, rows=rows)

    async def test_workspace_labels_isolated(self) -> None:
        rows = await self._scoped_probe_read(
            self.tenant_a, self.ws_a,
            "SELECT id, tenant_id FROM workspace_labels WHERE id = ANY($1)",
            [self.label_a_id, self.label_b_id],
        )
        self._assert_isolated(table="workspace_labels", id_column="id", id_a=self.label_a_id, id_b=self.label_b_id, rows=rows)

    async def test_bug_reports_isolated(self) -> None:
        rows = await self._scoped_probe_read(
            self.tenant_a, self.ws_a,
            "SELECT id, tenant_id FROM bug_reports WHERE id = ANY($1)",
            [self.bug_report_a_id, self.bug_report_b_id],
        )
        self._assert_isolated(table="bug_reports", id_column="id", id_a=self.bug_report_a_id, id_b=self.bug_report_b_id, rows=rows)

    async def test_mcp_external_agent_roster_isolated(self) -> None:
        rows = await self._scoped_probe_read(
            self.tenant_a, self.ws_a,
            "SELECT id, tenant_id FROM mcp_external_agent_roster WHERE id = ANY($1)",
            [self.roster_a_id, self.roster_b_id],
        )
        self._assert_isolated(table="mcp_external_agent_roster", id_column="id", id_a=self.roster_a_id, id_b=self.roster_b_id, rows=rows)

    async def test_regression_guard_tenant_b_sees_its_own_rows_too(self) -> None:
        """Symmetric check (mirrors AgentThreadsRlsIsolationTests' own
        regression guard): isolation is not "nobody sees anything" -- scoped
        to tenant B, the SAME query sees tenant B's row instead."""
        rows = await self._scoped_probe_read(
            self.tenant_b, self.ws_b,
            "SELECT id, tenant_id FROM projects WHERE id = ANY($1)",
            [self.project_a_id, self.project_b_id],
        )
        self._assert_isolated(table="projects (tenant B session)", id_column="id", id_a=self.project_b_id, id_b=self.project_a_id, rows=rows)

    async def test_catalog_confirms_all_six_tables_enabled_and_forced(self) -> None:
        rows = await self.admin_conn.fetch(
            """
            SELECT c.relname AS table_name, c.relrowsecurity AS rls_enabled, c.relforcerowsecurity AS rls_forced,
                   COALESCE(p.policy_count, 0) AS policy_count
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            LEFT JOIN (
                SELECT schemaname, tablename, COUNT(*) AS policy_count
                FROM pg_policies GROUP BY schemaname, tablename
            ) p ON p.schemaname = n.nspname AND p.tablename = c.relname
            WHERE n.nspname = 'public' AND c.relname = ANY($1)
            """,
            list(self.GRANT_TABLES),
        )
        state = {row["table_name"]: row for row in rows}
        for table in self.GRANT_TABLES:
            with self.subTest(table=table):
                self.assertIn(table, state, f"{table}: not found in pg_class.")
                self.assertTrue(state[table]["rls_enabled"], f"{table}: RLS not enabled.")
                self.assertTrue(state[table]["rls_forced"], f"{table}: RLS not FORCEd.")
                self.assertGreaterEqual(state[table]["policy_count"], 1, f"{table}: no RLS policy.")


# ── 2. Regression guard: the real service functions still work ──────────


class ServiceLayerRegressionTests(_BridgeAsyncTestCase):
    """No throwaway role here -- this exercises the app's OWN connection
    pool (superuser locally, see module docstring for why that's the right
    tool for THIS question), calling the real, public functions in all five
    converted services. The question is not "does RLS isolate" (class 1
    already answers that with real SQL) -- it's "did converting 43 call
    sites to rls_fetch/rls_fetchrow/rls_fetchval/rls_execute break any of
    them." Two tenants throughout, so a tenant_id/workspace_id swap in the
    conversion would show up as a wrong-tenant row leaking through even
    over this pool's own explicit filters."""

    async def async_setup(self):
        if not _database_url_available():
            self.skipTest(_NO_PG_REASON)
            return
        from server_modules import control_plane_repository as cpr

        pool = await cpr.ensure_control_plane_schema()
        if pool is None:
            self.skipTest(_NO_PG_REASON)
            return
        self.pool = pool

        suffix = uuid.uuid4().hex[:10]
        self.tenant_a = f"t_svc_a_{suffix}"
        self.tenant_b = f"t_svc_b_{suffix}"
        self.ws_a = f"ws_svc_a_{suffix}"
        self.ws_b = f"ws_svc_b_{suffix}"

    async def async_teardown(self):
        pool = getattr(self, "pool", None)
        if pool is None:
            return
        # Best-effort, broad cleanup by the two tenant ids this test
        # generated -- every row any sub-test below could have written,
        # deleted even if the test failed partway. Children before parents.
        for tenant_id in (getattr(self, "tenant_a", None), getattr(self, "tenant_b", None)):
            if not tenant_id:
                continue
            try:
                await pool.execute("DELETE FROM project_task_labels WHERE tenant_id = $1", tenant_id)
                await pool.execute("DELETE FROM mcp_external_agent_roster WHERE tenant_id = $1", tenant_id)
                await pool.execute("DELETE FROM bug_reports WHERE tenant_id = $1", tenant_id)
                await pool.execute("DELETE FROM workspace_labels WHERE tenant_id = $1", tenant_id)
                await pool.execute("DELETE FROM project_memberships WHERE tenant_id = $1", tenant_id)
                await pool.execute("DELETE FROM project_tasks WHERE tenant_id = $1", tenant_id)
                await pool.execute("DELETE FROM projects WHERE tenant_id = $1", tenant_id)
                await pool.execute("DELETE FROM workspace_agent_installs WHERE tenant_id = $1", tenant_id)
                await pool.execute("DELETE FROM users WHERE tenant_id = $1", tenant_id)
            except Exception:
                pass

    async def test_project_lifecycle_through_projects_repository(self) -> None:
        from server_modules import projects_repository as repo

        project_a = await repo.create_project(tenant_id=self.tenant_a, workspace_id=self.ws_a, name="Alpha")
        project_b = await repo.create_project(tenant_id=self.tenant_b, workspace_id=self.ws_b, name="Beta")
        self.assertEqual(project_a["name"], "Alpha")
        self.assertEqual(project_b["name"], "Beta")

        fetched = await repo.get_project(tenant_id=self.tenant_a, workspace_id=self.ws_a, project_id=project_a["id"])
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["id"], project_a["id"])

        # Cross-tenant get: tenant B's own scope must not resolve tenant A's project id.
        cross = await repo.get_project(tenant_id=self.tenant_b, workspace_id=self.ws_b, project_id=project_a["id"])
        self.assertIsNone(cross, "get_project resolved a project id that belongs to a different tenant.")

        listed_a = await repo.list_projects(tenant_id=self.tenant_a, workspace_id=self.ws_a)
        listed_ids_a = {p["id"] for p in listed_a}
        self.assertIn(project_a["id"], listed_ids_a)
        self.assertNotIn(project_b["id"], listed_ids_a)

        renamed = await repo.rename_project(tenant_id=self.tenant_a, workspace_id=self.ws_a, project_id=project_a["id"], name="Alpha Renamed")
        self.assertEqual(renamed["name"], "Alpha Renamed")

        archived = await repo.set_project_archived(tenant_id=self.tenant_a, workspace_id=self.ws_a, project_id=project_a["id"], archived=True)
        self.assertTrue(archived["archived"])

        member = await repo.add_project_member(
            tenant_id=self.tenant_a, workspace_id=self.ws_a, project_id=project_a["id"], user_id=f"user_{self.tenant_a}", role="owner",
        )
        self.assertEqual(member["role"], "owner")
        self.assertTrue(await repo.is_project_member(tenant_id=self.tenant_a, workspace_id=self.ws_a, project_id=project_a["id"], user_id=f"user_{self.tenant_a}"))
        member_ids = await repo.list_member_project_ids(tenant_id=self.tenant_a, workspace_id=self.ws_a, user_id=f"user_{self.tenant_a}")
        self.assertIn(project_a["id"], member_ids)
        removed = await repo.remove_project_member(tenant_id=self.tenant_a, workspace_id=self.ws_a, project_id=project_a["id"], user_id=f"user_{self.tenant_a}")
        self.assertTrue(removed)

    async def test_task_lifecycle_through_project_tasks_service(self) -> None:
        from server_modules import projects_repository as repo
        from server_modules import project_tasks_service as tasks

        project_a = await repo.create_project(tenant_id=self.tenant_a, workspace_id=self.ws_a, name="Board A")
        project_b = await repo.create_project(tenant_id=self.tenant_b, workspace_id=self.ws_b, name="Board B")

        task_a = await tasks.create_task(tenant_id=self.tenant_a, workspace_id=self.ws_a, project_id=project_a["id"], title="Ship the thing")
        task_b = await tasks.create_task(tenant_id=self.tenant_b, workspace_id=self.ws_b, project_id=project_b["id"], title="Ship the other thing")
        self.assertEqual(task_a["status"], "todo")

        fetched = await tasks.get_task(tenant_id=self.tenant_a, workspace_id=self.ws_a, task_id=task_a["id"])
        self.assertIsNotNone(fetched)

        cross = await tasks.get_task(tenant_id=self.tenant_b, workspace_id=self.ws_b, task_id=task_a["id"])
        self.assertIsNone(cross, "get_task resolved a task id that belongs to a different tenant.")

        listed_a = await tasks.list_tasks(tenant_id=self.tenant_a, workspace_id=self.ws_a)
        listed_ids_a = {t["id"] for t in listed_a}
        self.assertIn(task_a["id"], listed_ids_a)
        self.assertNotIn(task_b["id"], listed_ids_a)

        updated = await tasks.update_task(tenant_id=self.tenant_a, workspace_id=self.ws_a, task_id=task_a["id"], status="in_review", priority=1)
        self.assertEqual(updated["status"], "in_review")
        self.assertEqual(updated["priority"], 1)

        commented = await tasks.add_task_comment(
            tenant_id=self.tenant_a, workspace_id=self.ws_a, task_id=task_a["id"],
            author_type="human", author_id="tester", body="Looks good.",
        )
        self.assertEqual(len(commented["metadata"]["comments"]), 1)

        subtask = await tasks.create_task(
            tenant_id=self.tenant_a, workspace_id=self.ws_a, project_id=project_a["id"],
            title="Sub-step", parent_task_id=task_a["id"],
        )
        self.assertEqual(subtask["parent_task_id"], task_a["id"])
        parent_after = await tasks.get_task(tenant_id=self.tenant_a, workspace_id=self.ws_a, task_id=task_a["id"])
        self.assertEqual(parent_after["subtask_count"], 1)

    async def test_label_lifecycle_through_workspace_labels_service(self) -> None:
        from server_modules import projects_repository as repo
        from server_modules import project_tasks_service as tasks
        from server_modules import workspace_labels_service as labels

        project_a = await repo.create_project(tenant_id=self.tenant_a, workspace_id=self.ws_a, name="Board A")
        task_a = await tasks.create_task(tenant_id=self.tenant_a, workspace_id=self.ws_a, project_id=project_a["id"], title="Needs a label")

        label_a = await labels.create_label(tenant_id=self.tenant_a, workspace_id=self.ws_a, name="bug", color="red")
        label_b = await labels.create_label(tenant_id=self.tenant_b, workspace_id=self.ws_b, name="bug", color="red")
        self.assertEqual(label_a["name"], "bug")

        listed_a = await labels.list_labels(tenant_id=self.tenant_a, workspace_id=self.ws_a)
        listed_ids_a = {l["id"] for l in listed_a}
        self.assertIn(label_a["id"], listed_ids_a)
        self.assertNotIn(label_b["id"], listed_ids_a)

        found = await labels.find_label_by_name(tenant_id=self.tenant_a, workspace_id=self.ws_a, name="Bug")
        self.assertEqual(found["id"], label_a["id"])

        cross = await labels.get_label(tenant_id=self.tenant_b, workspace_id=self.ws_b, label_id=label_a["id"])
        self.assertIsNone(cross, "get_label resolved a label id that belongs to a different tenant.")

        after_attach = await labels.attach_label(tenant_id=self.tenant_a, workspace_id=self.ws_a, task_id=task_a["id"], label="bug")
        self.assertEqual([l["id"] for l in after_attach], [label_a["id"]])

        task_labels = await labels.list_task_labels(tenant_id=self.tenant_a, workspace_id=self.ws_a, task_id=task_a["id"])
        self.assertEqual(len(task_labels), 1)

        renamed = await labels.update_label(tenant_id=self.tenant_a, workspace_id=self.ws_a, label_id=label_a["id"], color="blue")
        self.assertEqual(renamed["color"], "blue")

        after_detach = await labels.detach_label(tenant_id=self.tenant_a, workspace_id=self.ws_a, task_id=task_a["id"], label="bug")
        self.assertEqual(after_detach, [])

        deleted = await labels.delete_label(tenant_id=self.tenant_a, workspace_id=self.ws_a, label_id=label_a["id"])
        self.assertTrue(deleted)

    async def test_bug_report_lifecycle_through_bug_report_service(self) -> None:
        from server_modules import bug_report_service as reports

        report_a = await reports.create_report(tenant_id=self.tenant_a, workspace_id=self.ws_a, title="Button is broken")
        report_b = await reports.create_report(tenant_id=self.tenant_b, workspace_id=self.ws_b, title="Also broken, elsewhere")
        self.assertEqual(report_a["status"], "new")

        listed_a = await reports.list_reports(tenant_id=self.tenant_a, workspace_id=self.ws_a)
        listed_ids_a = {r["id"] for r in listed_a}
        self.assertIn(report_a["id"], listed_ids_a)
        self.assertNotIn(report_b["id"], listed_ids_a)

    async def test_external_agent_roster_lifecycle(self) -> None:
        from server_modules import mcp_external_agent_roster_service as roster

        key_hash_a = f"hash_{uuid.uuid4().hex}"
        result_a = await roster.register_external_agent(
            tenant_id=self.tenant_a, workspace_id=self.ws_a, key_hash=key_hash_a, display_name="Codex A",
        )
        self.assertTrue(result_a["ok"], result_a)

        # Idempotent re-registration of the SAME key_hash must return the
        # SAME identity, not a second row (ON CONFLICT DO NOTHING + fallback
        # read, both now scoped via rls_fetchrow rather than a bare pool call).
        result_a_again = await roster.register_external_agent(
            tenant_id=self.tenant_a, workspace_id=self.ws_a, key_hash=key_hash_a, display_name="Codex A",
        )
        self.assertTrue(result_a_again["ok"], result_a_again)
        self.assertEqual(result_a_again["id"], result_a["id"])

        listed_a = await roster.list_workspace_external_agents(tenant_id=self.tenant_a, workspace_id=self.ws_a)
        self.assertEqual([e["id"] for e in listed_a], [result_a["id"]])

        unified = await roster.list_unified_roster(tenant_id=self.tenant_a, workspace_id=self.ws_a)
        external_entries = [e for e in unified if e["kind"] == "external"]
        self.assertEqual([e["id"] for e in external_entries], [result_a["id"]])


# ── 3. The one bypass_rls=True this change adds ──────────────────────────


class AuthBootstrapBypassTests(_RlsProbeRoleFixture):
    """mcp_external_agent_roster_service.get_external_agent_by_key_hash is
    the sole `bypass_rls=True` this change introduces -- auth bootstrap,
    tenant genuinely unknown at call time. Two halves, because either one
    failing alone would be a bad outcome for a different reason: half 1
    failing means the bypass broke MCP auth for everyone; half 2 failing
    (i.e. the row is ALSO reachable without the bypass) would mean the
    bypass_rls=True on this call site is a hole opened for nothing -- the
    row was never actually protected."""

    GRANT_TABLES = ("mcp_external_agent_roster",)

    async def async_setup(self):
        await super().async_setup()
        if not hasattr(self, "admin_conn"):
            return
        self.key_hash = f"hash_bootstrap_{uuid.uuid4().hex}"
        self.roster_id = f"ext_agent_bootstrap_{self.tenant_a}"
        await self.admin_conn.execute(
            """
            INSERT INTO mcp_external_agent_roster (id, tenant_id, workspace_id, display_name, key_hash)
            VALUES ($1, $2, $3, 'Bootstrap Agent', $4)
            """,
            self.roster_id, self.tenant_a, self.ws_a, self.key_hash,
        )

    async def _cleanup_rows(self, conn) -> None:
        await conn.execute("DELETE FROM mcp_external_agent_roster WHERE tenant_id = $1", self.tenant_a)

    async def test_real_service_function_resolves_by_key_hash_alone(self) -> None:
        """Half 1: the real function, real pool (superuser locally, so this
        alone would pass even without the bypass -- see half 2 for the part
        that actually exercises the mechanism)."""
        from server_modules import mcp_external_agent_roster_service as roster

        entry = await roster.get_external_agent_by_key_hash(key_hash=self.key_hash)
        self.assertIsNotNone(entry)
        self.assertEqual(entry["id"], self.roster_id)
        self.assertEqual(entry["tenant_id"], self.tenant_a)

    async def test_ordinary_role_needs_the_bypass_to_see_the_row(self) -> None:
        """Half 2, the one that matters: as the throwaway ORDINARY role,
        scoped to NO tenant/workspace (empty strings — the actual state
        `_apply_connection_scope` puts a connection in when `tenant_id`/
        `workspace_id` are both None, mirroring `rls_fetchrow(bypass_rls=
        True)`'s own resolved_tenant_id/resolved_workspace_id = ""), the row
        is invisible WITHOUT the bypass GUC and visible WITH it. This is the
        actual mechanism get_external_agent_by_key_hash relies on."""
        without_bypass = await self._scoped_probe_read(
            "", "",
            "SELECT id, key_hash FROM mcp_external_agent_roster WHERE key_hash = $1",
            self.key_hash,
            bypass=False,
        )
        self.assertEqual(
            len(without_bypass), 0,
            "An ordinary role with no tenant scope and no bypass could read the roster row — "
            "the RLS policy is not actually protecting mcp_external_agent_roster.",
        )

        with_bypass = await self._scoped_probe_read(
            "", "",
            "SELECT id, key_hash FROM mcp_external_agent_roster WHERE key_hash = $1",
            self.key_hash,
            bypass=True,
        )
        self.assertEqual(len(with_bypass), 1)
        self.assertEqual(with_bypass[0]["id"], self.roster_id)


if __name__ == "__main__":
    unittest.main()

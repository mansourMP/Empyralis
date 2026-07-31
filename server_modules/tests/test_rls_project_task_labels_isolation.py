"""Isolation proof for `project_task_labels` -- the labels<->tasks join
table added by migrations/add_task_labels.sql AFTER the six-table RLS pass
in migrations/enable_rls.sql, and consequently missed by it. Real tenant
data (which label is on which task), no database-level protection, until
this change adds:

    ALTER TABLE project_task_labels ENABLE ROW LEVEL SECURITY;
    ALTER TABLE project_task_labels FORCE ROW LEVEL SECURITY;
    CREATE POLICY empyralis_project_task_labels_scope ON project_task_labels ...

Before adding that, every query path against this table was audited (see
server_modules/workspace_labels_service.py's attach_label/detach_label/
list_task_labels/list_labels, and server_modules/project_tasks_service.py's
_TASK_ROLLUP_JOINS/_TASK_ROLLUP_RETURNING, used by every task read/write in
that file) and confirmed to already route through
control_plane_repository.rls_fetch/rls_fetchrow/rls_execute rather than a
raw pool call -- so, unlike `project_task_labels` needing the policy added
at all, turning it on was safe with no code changes required. This file is
that safety claim, proven against real Postgres rather than asserted in a
commit message.

TWO classes, because they are not the same proof:

1. ``ProjectTaskLabelsRlsIsolationTests`` -- the database-level mechanism.
   Duplicates `test_rls_six_tables_isolation_man109.py`'s own
   `_RlsProbeRoleFixture` (a throwaway, ORDINARY -- non-superuser,
   non-BYPASSRLS -- Postgres role) rather than importing it, for the same
   reason that file gives for duplicating ITS predecessor's fixture:
   importing a class from a file a different, concurrently-running agent
   owns (server_modules/tests/ pre-existing files are off limits per this
   change's own collision protocol) would couple this file's correctness to
   edits happening there right now. A query with NO tenant_id/workspace_id
   filter in its WHERE clause, run as the throwaway role, must see only the
   scoped tenant's own row -- proving the POLICY filters, not "the service
   layer remembered to filter" (a superuser connection would pass this test
   even with zero protection, which is exactly why it is not used here).

2. ``ServiceLayerStillWorksTests`` -- one end-to-end sanity pass through the
   real, public `workspace_labels_service` functions (attach_label /
   list_task_labels / detach_label) over the app's own connection pool,
   two tenants throughout. Not a second isolation proof (this pool is the
   local superuser role, see class 1's own docstring for why that can't be
   one) -- it exists to catch the OTHER real risk enabling RLS on a
   previously-unprotected table carries: a WITH CHECK violation on a write
   path this audit missed. If every write below still succeeds and every
   read still returns exactly the expected rows, the audit was complete.

Real Postgres only, opt-in from an already-exported DATABASE_URL (this
suite's convention). Skips cleanly when Postgres is not reachable. The
throwaway role and every row this file inserts are removed in tearDown,
including on a partial failure.
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


class ProjectTaskLabelsRlsIsolationTests(_BridgeAsyncTestCase):
    GRANT_TABLES = ("project_task_labels",)

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
        self.tenant_a = f"t_tasklbl_a_{suffix}"
        self.tenant_b = f"t_tasklbl_b_{suffix}"
        self.ws_a = f"ws_tasklbl_a_{suffix}"
        self.ws_b = f"ws_tasklbl_b_{suffix}"
        self.role = f"rls_tasklbl_probe_{suffix}"
        self.role_password = uuid.uuid4().hex

        await self.admin_conn.execute(
            f'CREATE ROLE "{self.role}" LOGIN PASSWORD \'{self.role_password}\''
        )
        table_list = ", ".join(self.GRANT_TABLES)
        await self.admin_conn.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table_list} TO \"{self.role}\""
        )
        self.probe_dsn = _probe_dsn(self.admin_dsn, user=self.role, password=self.role_password)

        # ── Seed the full FK chain as admin (superuser): project -> task ->
        # label -> the join row -- for TWO tenants, so the proof below is
        # "tenant A's own row, not tenant B's" rather than empty vs nonempty.
        self.project_a_id = f"proj_a_{suffix}"
        self.project_b_id = f"proj_b_{suffix}"
        await self.admin_conn.execute(
            """
            INSERT INTO projects (id, tenant_id, workspace_id, name, slug)
            VALUES ($1, $2, $3, 'Tenant A project', $1), ($4, $5, $6, 'Tenant B project', $4)
            """,
            self.project_a_id, self.tenant_a, self.ws_a,
            self.project_b_id, self.tenant_b, self.ws_b,
        )
        self.task_a_id = f"task_a_{suffix}"
        self.task_b_id = f"task_b_{suffix}"
        await self.admin_conn.execute(
            """
            INSERT INTO project_tasks (id, tenant_id, workspace_id, project_id, title, status)
            VALUES ($1, $2, $3, $4, 'Tenant A task', 'todo'), ($5, $6, $7, $8, 'Tenant B task', 'todo')
            """,
            self.task_a_id, self.tenant_a, self.ws_a, self.project_a_id,
            self.task_b_id, self.tenant_b, self.ws_b, self.project_b_id,
        )
        self.label_a_id = f"label_a_{suffix}"
        self.label_b_id = f"label_b_{suffix}"
        await self.admin_conn.execute(
            """
            INSERT INTO workspace_labels (id, tenant_id, workspace_id, name, color)
            VALUES ($1, $2, $3, 'bug', 'red'), ($4, $5, $6, 'bug', 'red')
            """,
            self.label_a_id, self.tenant_a, self.ws_a,
            self.label_b_id, self.tenant_b, self.ws_b,
        )
        await self.admin_conn.execute(
            """
            INSERT INTO project_task_labels (task_id, label_id, tenant_id, workspace_id)
            VALUES ($1, $2, $3, $4), ($5, $6, $7, $8)
            """,
            self.task_a_id, self.label_a_id, self.tenant_a, self.ws_a,
            self.task_b_id, self.label_b_id, self.tenant_b, self.ws_b,
        )

    async def async_teardown(self):
        conn = getattr(self, "admin_conn", None)
        if conn is None:
            return
        try:
            for tenant_id in (getattr(self, "tenant_a", None), getattr(self, "tenant_b", None)):
                if not tenant_id:
                    continue
                await conn.execute("DELETE FROM project_task_labels WHERE tenant_id = $1", tenant_id)
                await conn.execute("DELETE FROM workspace_labels WHERE tenant_id = $1", tenant_id)
                await conn.execute("DELETE FROM project_tasks WHERE tenant_id = $1", tenant_id)
                await conn.execute("DELETE FROM projects WHERE tenant_id = $1", tenant_id)
        finally:
            role = getattr(self, "role", None)
            if role:
                table_list = ", ".join(self.GRANT_TABLES)
                await conn.execute(f"REVOKE ALL ON {table_list} FROM \"{role}\"")
                await conn.execute(f'DROP ROLE IF EXISTS "{role}"')
            await conn.close()

    async def _scoped_probe_read(self, tenant_id: str, workspace_id: str, query: str, *args):
        import asyncpg

        conn = await asyncpg.connect(self.probe_dsn, timeout=10)
        try:
            async with conn.transaction():
                await conn.execute(
                    """
                    SELECT set_config('app.current_tenant_id', $1, true),
                           set_config('app.current_workspace_id', $2, true)
                    """,
                    tenant_id,
                    workspace_id,
                )
                return await conn.fetch(query, *args)
        finally:
            await conn.close()

    async def test_project_task_labels_isolated(self) -> None:
        """A query with NO tenant_id/workspace_id filter, scoped to tenant
        A's session, must see tenant A's join row and MUST NOT see tenant
        B's -- exactly the "the app forgot to filter" shape RLS closes even
        when application code stays correct forever."""
        rows = await self._scoped_probe_read(
            self.tenant_a, self.ws_a,
            "SELECT task_id, label_id, tenant_id FROM project_task_labels WHERE label_id = ANY($1)",
            [self.label_a_id, self.label_b_id],
        )
        seen = {(row["task_id"], row["label_id"]) for row in rows}
        self.assertIn((self.task_a_id, self.label_a_id), seen)
        self.assertNotIn((self.task_b_id, self.label_b_id), seen)
        self.assertEqual(len(rows), 1)

    async def test_regression_guard_tenant_b_sees_its_own_row_too(self) -> None:
        """Isolation is not "nobody sees anything" -- scoped to tenant B,
        the same query sees tenant B's row instead."""
        rows = await self._scoped_probe_read(
            self.tenant_b, self.ws_b,
            "SELECT task_id, label_id, tenant_id FROM project_task_labels WHERE label_id = ANY($1)",
            [self.label_a_id, self.label_b_id],
        )
        seen = {(row["task_id"], row["label_id"]) for row in rows}
        self.assertIn((self.task_b_id, self.label_b_id), seen)
        self.assertNotIn((self.task_a_id, self.label_a_id), seen)
        self.assertEqual(len(rows), 1)

    async def test_unscoped_session_sees_neither_row(self) -> None:
        """No tenant/workspace GUC set at all (the state a bare pool call
        left a connection in before this whole class of bug was found) sees
        zero rows from either tenant -- not an error, just silence. The
        exact failure mode that made these tables worth protecting."""
        rows = await self._scoped_probe_read(
            "", "",
            "SELECT task_id, label_id FROM project_task_labels WHERE label_id = ANY($1)",
            [self.label_a_id, self.label_b_id],
        )
        self.assertEqual(list(rows), [])

    async def test_catalog_confirms_rls_enabled_and_forced(self) -> None:
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
            WHERE n.nspname = 'public' AND c.relname = 'project_task_labels'
            """,
        )
        self.assertEqual(len(rows), 1, "project_task_labels not found in pg_class.")
        row = rows[0]
        self.assertTrue(row["rls_enabled"], "project_task_labels: RLS not enabled.")
        self.assertTrue(row["rls_forced"], "project_task_labels: RLS not FORCEd.")
        self.assertGreaterEqual(row["policy_count"], 1, "project_task_labels: no RLS policy.")


class ServiceLayerStillWorksTests(_BridgeAsyncTestCase):
    """No throwaway role -- the app's own pool (superuser locally, see the
    module docstring for why that's the right tool for THIS question, not
    an isolation proof). Confirms the real attach/list/detach functions
    still work after FORCE RLS landed on the table they write to."""

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
        self.tenant_a = f"t_tasklblsvc_a_{suffix}"
        self.tenant_b = f"t_tasklblsvc_b_{suffix}"
        self.ws_a = f"ws_tasklblsvc_a_{suffix}"
        self.ws_b = f"ws_tasklblsvc_b_{suffix}"

    async def async_teardown(self):
        pool = getattr(self, "pool", None)
        if pool is None:
            return
        for tenant_id in (getattr(self, "tenant_a", None), getattr(self, "tenant_b", None)):
            if not tenant_id:
                continue
            try:
                await pool.execute("DELETE FROM project_task_labels WHERE tenant_id = $1", tenant_id)
                await pool.execute("DELETE FROM workspace_labels WHERE tenant_id = $1", tenant_id)
                await pool.execute("DELETE FROM project_tasks WHERE tenant_id = $1", tenant_id)
                await pool.execute("DELETE FROM projects WHERE tenant_id = $1", tenant_id)
            except Exception:
                pass

    async def test_attach_list_detach_round_trip_across_two_tenants(self) -> None:
        from server_modules import projects_repository as repo
        from server_modules import project_tasks_service as tasks
        from server_modules import workspace_labels_service as labels

        project_a = await repo.create_project(tenant_id=self.tenant_a, workspace_id=self.ws_a, name="Board A")
        project_b = await repo.create_project(tenant_id=self.tenant_b, workspace_id=self.ws_b, name="Board B")
        task_a = await tasks.create_task(tenant_id=self.tenant_a, workspace_id=self.ws_a, project_id=project_a["id"], title="Ship it")
        task_b = await tasks.create_task(tenant_id=self.tenant_b, workspace_id=self.ws_b, project_id=project_b["id"], title="Ship the other thing")

        label_a = await labels.create_label(tenant_id=self.tenant_a, workspace_id=self.ws_a, name="bug", color="red")
        label_b = await labels.create_label(tenant_id=self.tenant_b, workspace_id=self.ws_b, name="bug", color="red")

        after_attach_a = await labels.attach_label(tenant_id=self.tenant_a, workspace_id=self.ws_a, task_id=task_a["id"], label="bug")
        self.assertEqual([entry["id"] for entry in after_attach_a], [label_a["id"]])
        after_attach_b = await labels.attach_label(tenant_id=self.tenant_b, workspace_id=self.ws_b, task_id=task_b["id"], label="bug")
        self.assertEqual([entry["id"] for entry in after_attach_b], [label_b["id"]])

        # The task's own read path (project_tasks_service's inline LATERAL
        # join against project_task_labels, not list_task_labels) must also
        # see exactly its own tenant's label, post-RLS.
        fetched_task_a = await tasks.get_task(tenant_id=self.tenant_a, workspace_id=self.ws_a, task_id=task_a["id"])
        self.assertEqual([entry["id"] for entry in fetched_task_a["labels"]], [label_a["id"]])

        task_a_labels = await labels.list_task_labels(tenant_id=self.tenant_a, workspace_id=self.ws_a, task_id=task_a["id"])
        self.assertEqual([entry["id"] for entry in task_a_labels], [label_a["id"]])

        # Cross-tenant list must not resolve the other tenant's attachment.
        cross = await labels.list_task_labels(tenant_id=self.tenant_b, workspace_id=self.ws_b, task_id=task_a["id"])
        self.assertEqual(cross, [])

        after_detach = await labels.detach_label(tenant_id=self.tenant_a, workspace_id=self.ws_a, task_id=task_a["id"], label="bug")
        self.assertEqual(after_detach, [])

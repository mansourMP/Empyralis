"""Tests for project_documents_repository.py -- the storage layer for a
project's owned markdown documents (see that module's own docstring for the
full "no RAG, project-scoped, body-in-Postgres, no revisions yet" reasoning).

Real Postgres only, opt-in from an already-exported DATABASE_URL -- same
convention as test_rls_six_tables_isolation_man109.py (no .env reading, no
os.environ mutation, everything skips cleanly when DATABASE_URL is unset).

TWO separate concerns, two test classes, mirroring test_rls_six_tables_
isolation_man109.py's own split (that file's own docstring explains why
conflating them proves less than each alone):

1. ``ProjectDocumentsServiceLayerTests`` -- the real, public repository
   functions end-to-end (create/get/list/update/delete), over the app's own
   connection pool, for two different tenants. This is what proves the
   CRUD surface and the (tenant_id, workspace_id, project_id) scoping in
   every WHERE clause actually work -- it does NOT prove RLS, since the
   app's own pool is the superuser DATABASE_URL role locally and superusers
   bypass RLS unconditionally (FORCE or not) -- see class 2 for that.

2. ``ProjectDocumentsRlsIsolationTests`` -- the database-level mechanism, a
   throwaway ORDINARY (non-superuser) Postgres role, scoped via the same
   `set_config(...)` calls `_apply_connection_scope` uses, running a query
   with NO tenant_id/workspace_id filter anywhere in its WHERE clause. This
   is what proves the RLS POLICY filters, not "the app remembered to
   filter" -- the actual "a document in workspace A must not be readable
   from workspace B" proof this pass's brief asked for.

Fixture code is a deliberate, near-byte-for-byte port of test_rls_six_
tables_isolation_man109.py's own `_BridgeAsyncTestCase`/`_RlsProbeRoleFixture`
-- duplicated rather than imported, same reasoning that file gives for its
own duplication: importing a class from a different test file couples this
file's correctness to unrelated edits landing there concurrently.
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
    connections (and this app's own db.get_pool() cache, keyed by
    id(current_loop)) cannot be used from a second event loop, so every
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


_NO_PG_REASON = "No Postgres reachable (DATABASE_URL unset — export it to run this suite)"


# ── 1. Service-layer CRUD + scoping, over the real repository functions ──


class ProjectDocumentsServiceLayerTests(_BridgeAsyncTestCase):
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
        self.tenant_a = f"t_docs_a_{suffix}"
        self.tenant_b = f"t_docs_b_{suffix}"
        self.ws_a = f"ws_docs_a_{suffix}"
        self.ws_b = f"ws_docs_b_{suffix}"

    async def async_teardown(self):
        pool = getattr(self, "pool", None)
        if pool is None:
            return
        for tenant_id in (getattr(self, "tenant_a", None), getattr(self, "tenant_b", None)):
            if not tenant_id:
                continue
            try:
                await pool.execute("DELETE FROM project_documents WHERE tenant_id = $1", tenant_id)
                await pool.execute("DELETE FROM projects WHERE tenant_id = $1", tenant_id)
            except Exception:
                pass

    async def test_create_get_list_update_delete_lifecycle(self) -> None:
        from server_modules import projects_repository as repo
        from server_modules import project_documents_repository as documents

        project_a = await repo.create_project(tenant_id=self.tenant_a, workspace_id=self.ws_a, name="Board A")

        created = await documents.create_document(
            tenant_id=self.tenant_a,
            workspace_id=self.ws_a,
            project_id=project_a["id"],
            title="Runbook",
            body="# Deploy steps\n\n1. Build\n2. Ship",
            created_by="user_alpha",
        )
        self.assertEqual(created["title"], "Runbook")
        self.assertEqual(created["path"], "runbook.md")
        self.assertEqual(created["body"], "# Deploy steps\n\n1. Build\n2. Ship")
        self.assertEqual(created["created_by"], "user_alpha")
        # updated_by is seeded to the creator on insert -- "who last touched
        # this" is honest from row zero, not NULL until the first edit.
        self.assertEqual(created["updated_by"], "user_alpha")
        self.assertEqual(created["metadata"], {})

        fetched = await documents.get_document(
            tenant_id=self.tenant_a, workspace_id=self.ws_a, document_id=created["id"],
        )
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["id"], created["id"])
        self.assertEqual(fetched["body"], created["body"])

        by_path = await documents.get_document_by_path(
            tenant_id=self.tenant_a, workspace_id=self.ws_a, project_id=project_a["id"], path="runbook.md",
        )
        self.assertIsNotNone(by_path)
        self.assertEqual(by_path["id"], created["id"])

        # A second document with the same title gets a disambiguated path,
        # not a constraint violation -- mirrors projects_repository's own
        # _unique_slug suffixing, via this module's own _unique_path.
        second = await documents.create_document(
            tenant_id=self.tenant_a, workspace_id=self.ws_a, project_id=project_a["id"], title="Runbook",
        )
        self.assertEqual(second["path"], "runbook-2.md")

        listed = await documents.list_documents(
            tenant_id=self.tenant_a, workspace_id=self.ws_a, project_id=project_a["id"],
        )
        listed_ids = {d["id"] for d in listed}
        self.assertEqual(listed_ids, {created["id"], second["id"]})
        # List omits the body by default -- a table-of-contents read, not a
        # bulk content dump.
        self.assertNotIn("body", listed[0])

        listed_with_body = await documents.list_documents(
            tenant_id=self.tenant_a, workspace_id=self.ws_a, project_id=project_a["id"], include_body=True,
        )
        self.assertTrue(all("body" in d for d in listed_with_body))

        # Passing the REAL precondition token here rather than None keeps
        # this lifecycle test exercising the path the product actually uses
        # -- a conditional write that matches. The refusal path has its own
        # file (test_document_stale_write_precondition.py).
        updated = await documents.update_document(
            tenant_id=self.tenant_a,
            workspace_id=self.ws_a,
            document_id=created["id"],
            expected_sha256=created["state_sha256"],
            body="# Deploy steps\n\n1. Build\n2. Test\n3. Ship",
            updated_by="user_beta",
        )
        self.assertIsNotNone(updated)
        self.assertEqual(updated["body"], "# Deploy steps\n\n1. Build\n2. Test\n3. Ship")
        self.assertEqual(updated["updated_by"], "user_beta")
        # Title untouched by a body-only patch.
        self.assertEqual(updated["title"], "Runbook")
        # Path never moves on a rename/edit -- it is the document's stable
        # address.
        self.assertEqual(updated["path"], "runbook.md")

        renamed = await documents.update_document(
            tenant_id=self.tenant_a, workspace_id=self.ws_a, document_id=created["id"],
            expected_sha256=updated["state_sha256"], title="Deploy Runbook",
        )
        self.assertEqual(renamed["title"], "Deploy Runbook")
        self.assertEqual(renamed["path"], "runbook.md", "renaming a document must not re-path it")

        deleted = await documents.delete_document(
            tenant_id=self.tenant_a, workspace_id=self.ws_a, document_id=created["id"],
        )
        self.assertTrue(deleted)
        gone = await documents.get_document(
            tenant_id=self.tenant_a, workspace_id=self.ws_a, document_id=created["id"],
        )
        self.assertIsNone(gone)

        # Deleting an id that no longer exists is a clean False, not an error.
        deleted_again = await documents.delete_document(
            tenant_id=self.tenant_a, workspace_id=self.ws_a, document_id=created["id"],
        )
        self.assertFalse(deleted_again)

    async def test_cross_tenant_isolation_through_the_service_layer(self) -> None:
        """Every call site filters by (tenant_id, workspace_id) explicitly,
        on top of RLS -- this proves that application-level filter using the
        real functions (class 2 below proves the database-level RLS policy
        itself, with a non-superuser role)."""
        from server_modules import projects_repository as repo
        from server_modules import project_documents_repository as documents

        project_a = await repo.create_project(tenant_id=self.tenant_a, workspace_id=self.ws_a, name="Board A")
        project_b = await repo.create_project(tenant_id=self.tenant_b, workspace_id=self.ws_b, name="Board B")

        doc_a = await documents.create_document(
            tenant_id=self.tenant_a, workspace_id=self.ws_a, project_id=project_a["id"],
            title="Alpha secrets", body="tenant A only",
        )
        doc_b = await documents.create_document(
            tenant_id=self.tenant_b, workspace_id=self.ws_b, project_id=project_b["id"],
            title="Beta secrets", body="tenant B only",
        )

        # get_document: tenant B's scope must not resolve tenant A's document id.
        cross_get = await documents.get_document(
            tenant_id=self.tenant_b, workspace_id=self.ws_b, document_id=doc_a["id"],
        )
        self.assertIsNone(cross_get, "get_document resolved a document id that belongs to a different tenant.")

        # list_documents: tenant A's list must contain only tenant A's document,
        # even when queried against tenant A's OWN project id (no cross-tenant
        # project id guessing involved) -- this is the "workspace A cannot read
        # workspace B" proof scoped to the read path a UI/agent actually uses.
        listed_a = await documents.list_documents(
            tenant_id=self.tenant_a, workspace_id=self.ws_a, project_id=project_a["id"],
        )
        listed_ids_a = {d["id"] for d in listed_a}
        self.assertIn(doc_a["id"], listed_ids_a)
        self.assertNotIn(doc_b["id"], listed_ids_a)

        # update_document: tenant B cannot mutate tenant A's document by id.
        # expected_sha256=None deliberately: the tenant/workspace filter
        # must block this on its own, with no help from the precondition.
        cross_update = await documents.update_document(
            tenant_id=self.tenant_b, workspace_id=self.ws_b, document_id=doc_a["id"],
            expected_sha256=None, title="Hijacked",
        )
        self.assertIsNone(cross_update, "update_document mutated a document belonging to a different tenant.")
        unchanged = await documents.get_document(
            tenant_id=self.tenant_a, workspace_id=self.ws_a, document_id=doc_a["id"],
        )
        self.assertEqual(unchanged["title"], "Alpha secrets")

        # delete_document: tenant B cannot delete tenant A's document by id.
        cross_delete = await documents.delete_document(
            tenant_id=self.tenant_b, workspace_id=self.ws_b, document_id=doc_a["id"],
        )
        self.assertFalse(cross_delete, "delete_document deleted a document belonging to a different tenant.")
        still_there = await documents.get_document(
            tenant_id=self.tenant_a, workspace_id=self.ws_a, document_id=doc_a["id"],
        )
        self.assertIsNotNone(still_there)

    async def test_project_scoped_path_uniqueness_is_per_project_not_per_workspace(self) -> None:
        """Two different PROJECTS in the same workspace may each have a
        document titled/paths identically -- the uniqueness boundary is
        (project_id, path), not (workspace_id, path), matching the schema's
        UNIQUE (project_id, path) constraint."""
        from server_modules import projects_repository as repo
        from server_modules import project_documents_repository as documents

        project_1 = await repo.create_project(tenant_id=self.tenant_a, workspace_id=self.ws_a, name="Project One")
        project_2 = await repo.create_project(tenant_id=self.tenant_a, workspace_id=self.ws_a, name="Project Two")

        doc_1 = await documents.create_document(
            tenant_id=self.tenant_a, workspace_id=self.ws_a, project_id=project_1["id"], title="Overview",
        )
        doc_2 = await documents.create_document(
            tenant_id=self.tenant_a, workspace_id=self.ws_a, project_id=project_2["id"], title="Overview",
        )
        self.assertEqual(doc_1["path"], "overview.md")
        self.assertEqual(doc_2["path"], "overview.md")
        self.assertNotEqual(doc_1["id"], doc_2["id"])

    async def test_create_requires_title_and_project(self) -> None:
        from server_modules import projects_repository as repo
        from server_modules import project_documents_repository as documents

        project_a = await repo.create_project(tenant_id=self.tenant_a, workspace_id=self.ws_a, name="Board A")

        with self.assertRaises(ValueError):
            await documents.create_document(
                tenant_id=self.tenant_a, workspace_id=self.ws_a, project_id=project_a["id"], title="   ",
            )
        with self.assertRaises(ValueError):
            await documents.create_document(
                tenant_id=self.tenant_a, workspace_id=self.ws_a, project_id="", title="No project",
            )


# ── 2. Database-level RLS isolation, no application filter involved ──────


def _probe_dsn(admin_dsn: str, *, user: str, password: str) -> str:
    parts = urlsplit(admin_dsn)
    netloc = f"{user}:{password}@{parts.hostname or 'localhost'}"
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


class ProjectDocumentsRlsIsolationTests(_BridgeAsyncTestCase):
    """The actual "a document in workspace A must not be readable from
    workspace B" proof: a throwaway, ORDINARY (non-superuser, non-BYPASSRLS)
    Postgres role, scoped to tenant/workspace A via the same session GUCs
    `_apply_connection_scope` sets, running a query against
    `project_documents` with NO tenant_id/workspace_id filter in its own
    WHERE clause. If the RLS policy were missing or misconfigured, this
    query would see both tenants' rows; it must see only tenant A's."""

    GRANT_TABLES = ("projects", "project_documents")

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
        self.tenant_a = f"t_docs_rls_a_{suffix}"
        self.tenant_b = f"t_docs_rls_b_{suffix}"
        self.ws_a = f"ws_docs_rls_a_{suffix}"
        self.ws_b = f"ws_docs_rls_b_{suffix}"
        self.role = f"rls_docs_probe_{suffix}"
        self.role_password = uuid.uuid4().hex

        await self.admin_conn.execute(
            f'CREATE ROLE "{self.role}" LOGIN PASSWORD \'{self.role_password}\''
        )
        table_list = ", ".join(self.GRANT_TABLES)
        await self.admin_conn.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table_list} TO \"{self.role}\""
        )
        self.probe_dsn = _probe_dsn(self.admin_dsn, user=self.role, password=self.role_password)

        self.project_a_id = f"proj_docs_a_{self.tenant_a}"
        self.project_b_id = f"proj_docs_b_{self.tenant_b}"
        await self.admin_conn.execute(
            """
            INSERT INTO projects (id, tenant_id, workspace_id, name, slug)
            VALUES ($1, $2, $3, 'Tenant A project', $1), ($4, $5, $6, 'Tenant B project', $4)
            """,
            self.project_a_id, self.tenant_a, self.ws_a,
            self.project_b_id, self.tenant_b, self.ws_b,
        )

        self.doc_a_id = f"doc_a_{self.tenant_a}"
        self.doc_b_id = f"doc_b_{self.tenant_b}"
        await self.admin_conn.execute(
            """
            INSERT INTO project_documents (id, tenant_id, workspace_id, project_id, title, path, body)
            VALUES ($1, $2, $3, $4, 'Tenant A doc', 'tenant-a-doc', 'A secret'),
                   ($5, $6, $7, $8, 'Tenant B doc', 'tenant-b-doc', 'B secret')
            """,
            self.doc_a_id, self.tenant_a, self.ws_a, self.project_a_id,
            self.doc_b_id, self.tenant_b, self.ws_b, self.project_b_id,
        )

    async def async_teardown(self):
        conn = getattr(self, "admin_conn", None)
        if conn is None:
            return
        try:
            await conn.execute("DELETE FROM project_documents WHERE tenant_id IN ($1, $2)", self.tenant_a, self.tenant_b)
            await conn.execute("DELETE FROM projects WHERE tenant_id IN ($1, $2)", self.tenant_a, self.tenant_b)
        finally:
            table_list = ", ".join(self.GRANT_TABLES)
            await conn.execute(f'REVOKE ALL ON {table_list} FROM "{self.role}"')
            await conn.execute(f'DROP ROLE IF EXISTS "{self.role}"')
            await conn.close()

    async def _scoped_probe_read(self, tenant_id: str, workspace_id: str, query: str, *args):
        import asyncpg

        conn = await asyncpg.connect(self.probe_dsn, timeout=10)
        try:
            async with conn.transaction():
                await conn.execute(
                    """
                    SELECT set_config('app.current_tenant_id', $1, true),
                           set_config('app.current_workspace_id', $2, true),
                           set_config('app.rls_bypass', 'off', true)
                    """,
                    tenant_id,
                    workspace_id,
                )
                return await conn.fetch(query, *args)
        finally:
            await conn.close()

    async def test_workspace_a_cannot_read_workspace_bs_document(self) -> None:
        rows = await self._scoped_probe_read(
            self.tenant_a, self.ws_a,
            "SELECT id, tenant_id, body FROM project_documents WHERE id = ANY($1)",
            [self.doc_a_id, self.doc_b_id],
        )
        seen = {row["id"] for row in rows}
        self.assertIn(self.doc_a_id, seen, "tenant A's own document went missing under its own session scope.")
        self.assertNotIn(self.doc_b_id, seen, "RLS failed to hide tenant B's document from tenant A's session.")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["body"], "A secret")

    async def test_symmetric_workspace_b_sees_its_own_document_too(self) -> None:
        """Isolation is not "nobody sees anything" -- the same query, scoped
        to tenant B, sees tenant B's row instead."""
        rows = await self._scoped_probe_read(
            self.tenant_b, self.ws_b,
            "SELECT id, tenant_id, body FROM project_documents WHERE id = ANY($1)",
            [self.doc_a_id, self.doc_b_id],
        )
        seen = {row["id"] for row in rows}
        self.assertIn(self.doc_b_id, seen)
        self.assertNotIn(self.doc_a_id, seen)
        self.assertEqual(rows[0]["body"], "B secret")

    async def test_unscoped_read_with_no_tenant_set_sees_nothing(self) -> None:
        """A connection with NO tenant/workspace GUC set at all (empty
        strings -- the state a brand-new connection is in before any scope
        is applied) must see zero rows, not every row. This is the
        "forgetting to scope a connection fails closed" guarantee FORCE ROW
        LEVEL SECURITY exists for."""
        rows = await self._scoped_probe_read(
            "", "",
            "SELECT id FROM project_documents WHERE id = ANY($1)",
            [self.doc_a_id, self.doc_b_id],
        )
        self.assertEqual(rows, [])

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
            WHERE n.nspname = 'public' AND c.relname = 'project_documents'
            """,
        )
        self.assertEqual(len(rows), 1, "project_documents not found in pg_class.")
        row = rows[0]
        self.assertTrue(row["rls_enabled"], "project_documents: RLS not enabled.")
        self.assertTrue(row["rls_forced"], "project_documents: RLS not FORCEd.")
        self.assertGreaterEqual(row["policy_count"], 1, "project_documents: no RLS policy.")


if __name__ == "__main__":
    unittest.main()

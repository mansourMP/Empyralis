"""migrations/add_document_paths.sql shipped a `slug` -> `path` rename plus
a backfill that appended `.md` to every bare (extensionless, slash-less)
document path -- and the backfill half could never run.

    project_documents carries FORCE ROW LEVEL SECURITY (migrations/
    enable_rls.sql), policy empyralis_rls_scope_match(tenant_id,
    workspace_id). DEPLOY-RUNBOOK 3b applies both this migration and
    CONTROL_PLANE_SCHEMA_SQL as `empyralis_app` -- a NON-superuser, so
    FORCE binds it -- and neither psql nor a plain pool.execute() sets any
    of the app.* GUCs.

        ALTER TABLE ... RENAME COLUMN     DDL, RLS does not apply  -> APPLIED
        UPDATE project_documents SET ...  DML, policy is false     -> 0 rows

    Exit 0, no error anywhere. Every pre-existing document kept its bare
    name (`Platform`, never `Platform.md`) forever -- and forever is
    literal here, not hyperbole: the rename makes `path` exist, so the
    `NOT EXISTS (... 'path')` guard wrapping the whole block is false on
    every later boot. One shot, silently missed, no self-heal. Reproduced
    directly on a disposable NOSUPERUSER NOBYPASSRLS-owned replica of the
    table before this fix landed. Same shape as the GEN-12 task-identifier
    backfill (b8905fe1b, projects_repository.backfill_task_identifiers) on
    a different table; this file follows that one's own two-suite
    convention.

Two suites, answering different questions:

1. STRUCTURAL (DB-free, always runs). A behavioural test cannot catch the
   reintroduction of the original defect -- a DML backfill put back into
   the .sql file or the schema mirror, or the bypass dropped off the one
   cross-tenant enumeration read, would type-check, apply cleanly, exit 0
   and silently do nothing, which is precisely the failure being guarded.
   These assertions also pin the wiring (the boot repair is CALLED) and the
   reuse (the backfill goes through _unique_path rather than a second
   transcription of the same collision-suffixing logic).

2. REAL POSTGRES (opt-in on an already-exported DATABASE_URL; skips
   cleanly otherwise, same convention as test_document_stale_write_
   precondition.py). Mocks cannot prove this fix at all: the whole bug IS
   the database's own row-level security silently filtering a write that a
   mocked pool would happily apply. Seeds documents the way they looked
   BEFORE any of this shipped -- a raw, bypassed INSERT of a bare path,
   never through create_document (which already appends the extension for
   every NEW document) -- and asserts the backfill heals them, is
   idempotent, and never clobbers a collision.
"""

from __future__ import annotations

import ast
import inspect
import os
import uuid
from pathlib import Path
from typing import Any, Dict

import unittest

from server_modules import project_documents_repository as documents

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION = _REPO_ROOT / "migrations" / "add_document_paths.sql"


def _run(coro):
    """One shared bridge event loop, not a fresh `asyncio.run()` per call --
    see sync_asyncio_bridge's own module header. Each Postgres test method
    below calls this several times against the SAME pool the class's setUp
    opened; a fresh loop per call would hand that pool connections bound to
    an already-closed loop the moment the second call ran."""
    from server_modules import sync_asyncio_bridge

    return sync_asyncio_bridge.run_coro_sync(coro)


def _database_url_available() -> bool:
    return bool(os.getenv("DATABASE_URL", "").strip())


_NO_PG_REASON = "No Postgres reachable (DATABASE_URL unset — export it to run this suite)"


# ── 1. Structural: the shapes a behavioural test cannot see ────────────────


class DocumentPathBackfillStructureTests(unittest.TestCase):
    def test_the_migration_file_still_exists_and_this_test_reads_the_real_one(self) -> None:
        """Canary. Every source-scanning assertion below is vacuous if the
        path stops resolving -- the exact trap
        __tests__/exec-file-timeout-child-leak.test.ts fell into (it scanned
        a directory holding zero files of the extension it looked for and
        reported green forever)."""
        self.assertTrue(_MIGRATION.is_file(), f"expected a real migration at {_MIGRATION}")
        text = _MIGRATION.read_text(encoding="utf-8")
        self.assertIn("RENAME COLUMN slug TO path", text, "scanned a file that is not this migration")

    def test_the_migration_contains_no_dml_backfill_that_rls_would_silently_eat(self) -> None:
        """The original defect, banned by structure. An UPDATE against an
        RLS-forced table from a psql session touches zero rows and reports
        nothing -- so this cannot be a behavioural assertion; the broken
        version passes every behavioural test that does not run as the app
        role."""
        code_lines = [
            line.split("--", 1)[0]
            for line in _MIGRATION.read_text(encoding="utf-8").splitlines()
        ]
        code = "\n".join(code_lines).lower()
        self.assertNotIn(
            "update project_documents",
            code,
            "found DML against an RLS-forced table; it will silently touch zero "
            "rows when applied by psql as empyralis_app. The backfill belongs in "
            "project_documents_repository.backfill_document_paths().",
        )

    def test_the_schema_mirror_also_carries_no_dml_backfill(self) -> None:
        """The same trap, the same table, the OTHER place this DDL is
        mirrored (CONTROL_PLANE_SCHEMA_SQL, applied on every boot as the
        same non-superuser role)."""
        from server_modules import control_plane_repository

        source = inspect.getsource(control_plane_repository)
        # Scope to the project_documents rename block specifically, so this
        # assertion cannot be satisfied by accident just because some other,
        # unrelated part of a 4000+ line module happens to avoid the phrase.
        marker = "ALTER TABLE project_documents RENAME COLUMN slug TO path"
        self.assertIn(marker, source)
        block = source.split(marker, 1)[1].split("END $$", 1)[0]
        self.assertNotIn(
            "UPDATE project_documents",
            block,
            "the DO $$ rename block must be DDL only -- a DML UPDATE here is "
            "invisible to RLS-unaware review and silently touches zero rows",
        )

    def test_the_boot_repair_is_actually_called(self) -> None:
        """"Built, tested, and never wired" is this codebase's most common
        defect. The backfill existing is not the feature; being reached
        from ensure_control_plane_schema on every boot is."""
        from server_modules import control_plane_repository

        tree = ast.parse(inspect.getsource(control_plane_repository.ensure_control_plane_schema))
        called = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        self.assertIn("backfill_document_paths", called)

    def test_the_backfill_reuses_unique_path_rather_than_re_deriving_collision_logic(self) -> None:
        """One disambiguation rule, not two. A hand-rolled '-2' suffix loop
        here would agree with _unique_path today and drift the moment
        either side changes, and it would not dedupe against a document
        create_document is concurrently disambiguating."""
        tree = ast.parse(inspect.getsource(documents.backfill_document_paths))
        called = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertIn("_unique_path", called)

    def test_the_cross_tenant_enumeration_read_passes_the_rls_bypass(self) -> None:
        """The one enumeration SELECT spans every tenant, so it is the only
        statement here that needs the bypass -- and without it the read
        returns the empty set and the whole backfill becomes the no-op it
        is replacing."""
        source = inspect.getsource(documents.backfill_document_paths)
        self.assertEqual(
            source.count("bypass_rls=True"),
            1,
            "expected exactly the one cross-tenant enumeration read to bypass RLS",
        )
        self.assertIn("rls_execute", source, "the per-row write must stay scoped, never bypassed")

    def test_the_row_guard_matches_the_original_migrations_intent(self) -> None:
        """Scoped to a bare name (no dot, no slash) -- the same guard the
        original (RLS-defeated) migration used, so a path a person has
        already given a slash-shaped structure is never rewritten."""
        source = inspect.getsource(documents.backfill_document_paths)
        self.assertIn("path NOT LIKE '%.%'", source)
        self.assertIn("path NOT LIKE '%/%'", source)

    def test_the_write_guard_is_the_idempotency_not_a_has_run_flag(self) -> None:
        """The UPDATE's own WHERE clause must repeat the row-shape guard --
        once a row carries a dot it can never match again, which is what
        makes two processes booting at once, or the same box restarting
        twice, safe without a sentinel column."""
        source = inspect.getsource(documents.backfill_document_paths)
        update_stmt = source.split("UPDATE project_documents", 1)[1].split('"""', 1)[0]
        self.assertIn("path NOT LIKE '%.%'", update_stmt)
        self.assertIn("path NOT LIKE '%/%'", update_stmt)


# ── 2. Real Postgres: the only place this fix can actually be proven ───────


class DocumentPathBackfillPostgresTests(unittest.TestCase):
    def setUp(self) -> None:
        if not _database_url_available():
            self.skipTest(_NO_PG_REASON)
            return
        from server_modules import control_plane_repository as cpr

        pool = _run(cpr.ensure_control_plane_schema())
        if pool is None:
            self.skipTest(_NO_PG_REASON)
            return
        self.pool = pool
        self.cpr = cpr
        suffix = uuid.uuid4().hex[:10]
        self.tenant_id = f"t_docpath_{suffix}"
        self.workspace_id = f"ws_docpath_{suffix}"

    def tearDown(self) -> None:
        pool = getattr(self, "pool", None)
        if pool is None:
            return
        for table in ("project_document_revisions", "project_documents", "projects"):
            try:
                _run(pool.execute(f"DELETE FROM {table} WHERE tenant_id = $1", self.tenant_id))
            except Exception:
                pass

    # -- seeding, deliberately raw -------------------------------------------

    def _seed_project(self, *, name: str, tenant_id: str = None, workspace_id: str = None) -> str:
        """Raw INSERT, deliberately never through projects_repository.
        create_project -- that function also allocates a GEN-12 task_key
        (see CLAUDE.md's own entry on that backfill), which is an unrelated
        feature this file has no need to exercise or depend on. A document
        backfill test should not be coupled to a different table's
        allocator."""
        tenant_id = tenant_id or self.tenant_id
        workspace_id = workspace_id or self.workspace_id
        project_id = f"proj_{uuid.uuid4().hex[:12]}"
        slug = f"{name.lower().replace(' ', '-')}-{uuid.uuid4().hex[:6]}"
        _run(
            self.cpr.rls_execute(
                self.pool,
                """
                INSERT INTO projects (id, tenant_id, workspace_id, name, slug)
                VALUES ($1, $2, $3, $4, $5)
                """,
                project_id,
                tenant_id,
                workspace_id,
                name,
                slug,
                bypass_rls=True,
            )
        )
        return project_id

    def _seed_bare_document(self, *, project_id: str, title: str, path: str) -> str:
        """Insert a document the way one looked BEFORE this migration's
        backfill was ever supposed to run: a bare, extensionless path.
        Raw INSERT through the bypass, never through create_document --
        create_document already appends `.md` to every new document, so
        seeding through it could not reproduce the state production was
        actually stuck in."""
        doc_id = f"doc_{uuid.uuid4().hex[:16]}"
        _run(
            self.cpr.rls_execute(
                self.pool,
                """
                INSERT INTO project_documents
                    (id, tenant_id, workspace_id, project_id, title, path)
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                doc_id,
                self.tenant_id,
                self.workspace_id,
                project_id,
                title,
                path,
                bypass_rls=True,
            )
        )
        return doc_id

    def _path_for(self, document_id: str) -> str:
        rows = _run(
            self.cpr.rls_fetch(
                self.pool,
                "SELECT path FROM project_documents WHERE id = $1",
                document_id,
                bypass_rls=True,
            )
        )
        return str(rows[0]["path"])

    # -- the assertions -------------------------------------------------------

    def test_the_seeded_estate_starts_exactly_as_production_did(self) -> None:
        """Guards the seeder itself. If the fixture accidentally arrived
        already carrying an extension, every assertion below would pass
        while proving nothing."""
        project_id = self._seed_project(name="Docs Backfill")
        doc_id = self._seed_bare_document(project_id=project_id, title="Platform", path="Platform")
        self.assertEqual(self._path_for(doc_id), "Platform")

    def test_a_bare_path_is_healed_with_the_markdown_extension(self) -> None:
        project_id = self._seed_project(name="Docs Backfill")
        doc_id = self._seed_bare_document(project_id=project_id, title="Platform", path="Platform")

        stats = _run(documents.backfill_document_paths(self.pool))

        self.assertEqual(stats["documents_backfilled"], 1)
        self.assertEqual(self._path_for(doc_id), "Platform.md")

    def test_running_it_twice_backfills_nothing_the_second_time(self) -> None:
        """The property that matters most for a boot-time repair: it must
        be safe to run on every single restart forever, not just once."""
        project_id = self._seed_project(name="Docs Backfill")
        doc_id = self._seed_bare_document(project_id=project_id, title="onboarding", path="onboarding")

        first = _run(documents.backfill_document_paths(self.pool))
        self.assertEqual(first["documents_backfilled"], 1)

        second = _run(documents.backfill_document_paths(self.pool))
        self.assertEqual(second["documents_backfilled"], 0)
        self.assertEqual(self._path_for(doc_id), "onboarding.md")

    def test_a_collision_with_an_existing_md_path_is_disambiguated_not_clobbered(self) -> None:
        """UNIQUE(project_id, path) is a real constraint -- a naive append
        would raise a UniqueViolation the instant two documents in one
        project reduce to the same target name. This must disambiguate the
        same way create_document already does for a human typing a
        duplicate title, never raise and never overwrite the row that
        already holds `auth.md`."""
        project_id = self._seed_project(name="Docs Backfill")
        already_extended = self._seed_bare_document(
            project_id=project_id, title="auth", path="auth.md"
        )
        bare = self._seed_bare_document(project_id=project_id, title="auth (legacy)", path="auth")

        stats = _run(documents.backfill_document_paths(self.pool))

        self.assertEqual(stats["documents_backfilled"], 1)
        self.assertEqual(self._path_for(already_extended), "auth.md", "must not be touched")
        self.assertEqual(self._path_for(bare), "auth-2.md")

    def test_a_path_that_already_has_a_slash_is_left_alone(self) -> None:
        """Scoped to rows with no slash, deliberately -- the same guard the
        original migration's own header carried, so this can never rewrite
        a path a person or an agent has already given real structure."""
        project_id = self._seed_project(name="Docs Backfill")
        doc_id = self._seed_bare_document(
            project_id=project_id, title="Auth Notes", path="specs/auth-notes"
        )

        stats = _run(documents.backfill_document_paths(self.pool))

        self.assertEqual(stats["documents_backfilled"], 0)
        self.assertEqual(self._path_for(doc_id), "specs/auth-notes")

    def test_documents_across_two_tenants_are_each_scoped_to_their_own_write(self) -> None:
        """The cross-tenant READ is bypassed on purpose (see the structural
        test); this proves the WRITE stays scoped to each row's own tenant
        and workspace rather than smuggling a cross-tenant write through
        the same bypass."""
        other_suffix = uuid.uuid4().hex[:10]
        other_tenant = f"t_docpath_other_{other_suffix}"
        other_workspace = f"ws_docpath_other_{other_suffix}"
        try:
            other_project_id = self._seed_project(
                name="Other Tenant Docs", tenant_id=other_tenant, workspace_id=other_workspace,
            )
            other_doc_id = f"doc_{uuid.uuid4().hex[:16]}"
            _run(
                self.cpr.rls_execute(
                    self.pool,
                    """
                    INSERT INTO project_documents
                        (id, tenant_id, workspace_id, project_id, title, path)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    """,
                    other_doc_id,
                    other_tenant,
                    other_workspace,
                    other_project_id,
                    "Handbook",
                    "Handbook",
                    bypass_rls=True,
                )
            )

            project_id = self._seed_project(name="Docs Backfill")
            own_doc_id = self._seed_bare_document(
                project_id=project_id, title="Platform", path="Platform"
            )

            stats = _run(documents.backfill_document_paths(self.pool))

            self.assertGreaterEqual(stats["documents_backfilled"], 2)
            self.assertEqual(self._path_for(own_doc_id), "Platform.md")
            self.assertEqual(self._path_for(other_doc_id), "Handbook.md")
        finally:
            try:
                _run(
                    self.pool.execute(
                        "DELETE FROM project_documents WHERE tenant_id = $1", other_tenant
                    )
                )
                _run(self.pool.execute("DELETE FROM projects WHERE tenant_id = $1", other_tenant))
            except Exception:
                pass


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

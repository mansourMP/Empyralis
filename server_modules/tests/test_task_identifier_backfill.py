"""The `GEN-12` task identifier shipped its schema, shipped its allocation
code, and still rendered a hex slice of a uuid on every task on production --
because the BACKFILL in migrations/add_task_sequence_numbers.sql could never
run.

    projects / project_tasks carry FORCE ROW LEVEL SECURITY, policy
    empyralis_rls_scope_match(tenant_id, workspace_id). DEPLOY-RUNBOOK 3b
    applies migrations as `empyralis_app` -- a NON-superuser, so FORCE binds
    it -- and psql sets none of the app.* GUCs.

        ALTER TABLE / CREATE INDEX   DDL, RLS does not apply    -> APPLIED
        SELECT / UPDATE ... projects DML, policy is false       -> 0 rows

    Exit 0. Columns present. Unique index present. Every task_key NULL, every
    task_seq 0, every task number NULL. Measured on production 2026-08-18.

Two suites here, and they are answering different questions:

  1. STRUCTURAL (DB-free, always runs). A behavioural test cannot catch the
     reintroduction of the original defect -- a DML backfill put back into
     the .sql file, or the bypass dropped off the enumeration read, would
     type-check, apply cleanly, exit 0 and silently do nothing, which is
     precisely the failure being guarded. These assertions also pin the
     wiring (the boot repair is CALLED) and the reuse (the backfill goes
     through _unique_task_key rather than a second transcription of the same
     derivation, which is the "list copied into a second place" shape).

  2. REAL POSTGRES (opt-in on an already-exported DATABASE_URL; skips
     cleanly otherwise, same convention as
     test_agent_private_memory_repository.py). Mocks cannot prove this fix
     at all: the whole bug IS the database's own row-level security silently
     filtering a query that a mocked pool would happily answer. It seeds a
     production-SHAPED estate -- several projects, one with 23 tasks, some
     with none -- and asserts numbering, idempotency across a second run,
     and that a task created AFTERWARDS continues the sequence instead of
     colliding with a backfilled number.
"""

from __future__ import annotations

import ast
import datetime as _dt
import inspect
import os
import unittest
import uuid
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION = _REPO_ROOT / "migrations" / "add_task_sequence_numbers.sql"


def _database_url_available() -> bool:
    return bool(os.getenv("DATABASE_URL", "").strip())


def _run(coro):
    from server_modules import sync_asyncio_bridge

    return sync_asyncio_bridge.run_coro_sync(coro)



def _ts(day: int, minute: int = 0) -> "_dt.datetime":
    """A real timezone-aware datetime. asyncpg binds parameters by inferred
    type, so an ISO STRING is rejected outright even with a ::timestamptz cast
    in the SQL -- a harness detail, not a production one."""
    return _dt.datetime(2026, 7, day, 0, minute, 0, tzinfo=_dt.timezone.utc)


_NO_PG_REASON = "No Postgres reachable (DATABASE_URL unset — export it to run this suite)"


# ── 1. Structural: the shapes a behavioural test cannot see ────────────────


class TaskIdentifierBackfillStructureTests(unittest.TestCase):
    def test_the_migration_file_still_exists_and_this_test_reads_the_real_one(self) -> None:
        """Canary. Every source-scanning assertion below is vacuous if the path
        stops resolving -- the exact trap
        __tests__/exec-file-timeout-child-leak.test.ts fell into (it scanned a
        directory holding zero files of the extension it looked for and
        reported green forever)."""
        self.assertTrue(_MIGRATION.is_file(), f"expected a real migration at {_MIGRATION}")
        text = _MIGRATION.read_text(encoding="utf-8")
        self.assertIn("task_key", text, "scanned a file that is not this migration")
        self.assertIn("ADD COLUMN IF NOT EXISTS", text)

    def test_the_migration_carries_the_ddl_for_all_three_columns(self) -> None:
        text = _MIGRATION.read_text(encoding="utf-8").lower()
        self.assertIn("alter table projects add column if not exists task_key", text)
        self.assertIn("alter table projects add column if not exists task_seq", text)
        self.assertIn("alter table project_tasks add column if not exists number", text)

    def test_the_migration_contains_no_dml_backfill_that_rls_would_silently_eat(self) -> None:
        """The original defect, banned by structure. An UPDATE against either
        RLS-forced table from a psql session touches zero rows and reports
        nothing -- so this cannot be a behavioural assertion; the broken
        version passes every behavioural test that does not run as the app
        role."""
        # Strip comments: the header DESCRIBES the banned shape at length, and
        # a scan that read prose would trip on its own documentation.
        code_lines = [
            line.split("--", 1)[0]
            for line in _MIGRATION.read_text(encoding="utf-8").splitlines()
        ]
        code = "\n".join(code_lines).lower()
        for banned in ("update projects", "update project_tasks", "select id, tenant_id"):
            self.assertNotIn(
                banned,
                code,
                f"{banned!r} is DML against an RLS-forced table; it will silently "
                "touch zero rows when applied by psql as empyralis_app. The backfill "
                "belongs in projects_repository.backfill_task_identifiers().",
            )

    def test_the_migration_sets_the_rls_bypass_for_its_own_transaction(self) -> None:
        code = "\n".join(
            line.split("--", 1)[0]
            for line in _MIGRATION.read_text(encoding="utf-8").splitlines()
        ).lower()
        self.assertIn("set local app.rls_bypass", code)

    def test_the_schema_mirror_the_migration_claims_actually_exists(self) -> None:
        """The migration header asserted it was "Mirrored into
        control_plane_repository.py's ... ensure_control_plane_schema()". It
        was not -- that module did not contain the string `task_key` anywhere.
        A claim in a comment is not a mirror."""
        from server_modules import control_plane_repository

        source = inspect.getsource(control_plane_repository.ensure_control_plane_schema)
        self.assertIn("task_key", source)
        self.assertIn("task_seq", source)
        self.assertIn("uq_projects_task_key", source)

    def test_the_boot_repair_is_actually_called(self) -> None:
        """"Built, tested, and never wired" is this codebase's most common
        defect. The backfill existing is not the feature; being reached is."""
        from server_modules import control_plane_repository

        tree = ast.parse(inspect.getsource(control_plane_repository.ensure_control_plane_schema))
        called = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        self.assertIn("backfill_task_identifiers", called)

    def test_the_backfill_reuses_unique_task_key_rather_than_re_deriving_it(self) -> None:
        """One derivation, not two. A SQL transcription of the same slug->key
        rule would agree today and drift the day either side changes, and a
        project keyed by the backfill must dedupe against one keyed by
        create_project."""
        from server_modules import projects_repository

        tree = ast.parse(inspect.getsource(projects_repository.backfill_task_identifiers))
        called = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertIn("_unique_task_key", called)

    def test_the_cross_tenant_enumeration_reads_pass_the_rls_bypass(self) -> None:
        """The two enumeration SELECTs span every tenant, so they are the only
        statements here that need the bypass -- and without it they return the
        empty set and the whole backfill becomes the no-op it is replacing."""
        from server_modules import projects_repository

        source = inspect.getsource(projects_repository.backfill_task_identifiers)
        self.assertEqual(
            source.count("bypass_rls=True"),
            2,
            "expected exactly the two cross-tenant enumeration reads to bypass RLS",
        )

    def test_the_task_seq_seed_is_raise_only_and_not_guarded_on_zero(self) -> None:
        """Two failures, opposite directions, one predicate.

        LOWERING task_seq would reissue a number that already exists on a live
        task (a deleted task shrinks MAX(number)). Guarding on `task_seq = 0`
        -- what the original migration did -- SKIPS the seed on any project
        whose live allocator moved first, leaving the counter below the numbers
        the backfill just issued, so the next create_task collides.
        `task_seq < MAX(number)` is the one predicate that is both."""
        from server_modules import projects_repository

        source = inspect.getsource(projects_repository.backfill_task_identifiers)
        self.assertIn("p.task_seq < sub.mx", source)
        self.assertNotIn(
            "p.task_seq = 0",
            source,
            "a `task_seq = 0` guard skips projects the live allocator already touched",
        )

    def test_numbering_is_ordered_oldest_first_with_a_stable_tiebreak(self) -> None:
        """created_at alone is not stable -- two tasks created in the same
        instant could swap between runs, which would RENUMBER already-issued
        identifiers that appear in comments, links and agent memory."""
        from server_modules import projects_repository

        source = inspect.getsource(projects_repository.backfill_task_identifiers)
        self.assertIn("ORDER BY created_at ASC, id ASC", source)


# ── 2. Real Postgres: the only place this fix can actually be proven ───────


class TaskIdentifierBackfillPostgresTests(unittest.TestCase):
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
        self.tenant_id = f"t_taskseq_{suffix}"
        self.workspace_id = f"ws_taskseq_{suffix}"

    def tearDown(self) -> None:
        pool = getattr(self, "pool", None)
        if pool is None:
            return
        for table in ("project_tasks", "projects"):
            try:
                _run(pool.execute(f"DELETE FROM {table} WHERE tenant_id = $1", self.tenant_id))
            except Exception:
                pass

    # -- seeding, deliberately raw ------------------------------------------

    def _seed_project(self, *, slug: str, name: str, created_at: "_dt.datetime") -> str:
        """Insert a project the way one looked BEFORE any of this shipped: no
        task_key, task_seq at its 0 default. Written through the raw pool with
        the bypass, never through create_project -- create_project allocates a
        task_key, so seeding with it could not reproduce the state production
        is actually in."""
        project_id = f"proj_{uuid.uuid4().hex[:12]}"
        _run(
            self.cpr.rls_execute(
                self.pool,
                """
                INSERT INTO projects (id, tenant_id, workspace_id, name, slug, created_at)
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                project_id,
                self.tenant_id,
                self.workspace_id,
                name,
                slug,
                created_at,
                bypass_rls=True,
            )
        )
        return project_id

    def _seed_task(self, *, project_id: str, title: str, created_at: "_dt.datetime") -> str:
        task_id = f"task_{uuid.uuid4().hex[:16]}"
        _run(
            self.cpr.rls_execute(
                self.pool,
                """
                INSERT INTO project_tasks
                    (id, tenant_id, workspace_id, project_id, title, created_at)
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                task_id,
                self.tenant_id,
                self.workspace_id,
                project_id,
                title,
                created_at,
                bypass_rls=True,
            )
        )
        return task_id

    def _rows(self, query: str, *args):
        return _run(self.cpr.rls_fetch(self.pool, query, *args, bypass_rls=True))

    def _numbers_for(self, project_id: str) -> list[tuple[str, int]]:
        rows = self._rows(
            "SELECT id, number, created_at FROM project_tasks "
            "WHERE project_id = $1 ORDER BY created_at ASC, id ASC",
            project_id,
        )
        return [(str(r["id"]), r["number"]) for r in rows]

    def _project_row(self, project_id: str) -> dict:
        rows = self._rows(
            "SELECT task_key, task_seq FROM projects WHERE id = $1", project_id
        )
        return dict(rows[0])

    def _seed_production_shaped_estate(self) -> dict:
        """Mirrors what production actually looks like: several projects, one
        carrying the bulk of the tasks, several carrying none."""
        from server_modules import projects_repository

        general = self._seed_project(
            slug="general", name="General", created_at=_ts(1)
        )
        empyralis = self._seed_project(
            slug="empyralis", name="empyralis", created_at=_ts(2)
        )
        ember = self._seed_project(
            slug="ember", name="Ember", created_at=_ts(3)
        )
        kestrel = self._seed_project(
            slug="kestrel", name="Kestrel", created_at=_ts(4)
        )
        for i in range(4):
            self._seed_task(
                project_id=general,
                title=f"general task {i}",
                created_at=_ts(10, i),
            )
        for i in range(23):
            self._seed_task(
                project_id=empyralis,
                title=f"empyralis task {i}",
                created_at=_ts(11, i),
            )
        return {
            "repo": projects_repository,
            "general": general,
            "empyralis": empyralis,
            "ember": ember,
            "kestrel": kestrel,
        }

    # -- the assertions ------------------------------------------------------

    def test_the_seeded_estate_starts_exactly_as_production_did(self) -> None:
        """Guards the seeder itself. If the fixture accidentally arrived
        already-numbered, every assertion below would pass while proving
        nothing."""
        estate = self._seed_production_shaped_estate()
        for key in ("general", "empyralis", "ember", "kestrel"):
            row = self._project_row(estate[key])
            self.assertIsNone(row["task_key"])
            self.assertEqual(row["task_seq"], 0)
        unnumbered = self._rows(
            "SELECT count(*) AS c FROM project_tasks "
            "WHERE tenant_id = $1 AND number IS NULL",
            self.tenant_id,
        )
        self.assertEqual(unnumbered[0]["c"], 27)

    def test_every_project_gets_a_key_and_every_task_gets_a_number(self) -> None:
        estate = self._seed_production_shaped_estate()
        _run(estate["repo"].backfill_task_identifiers(self.pool))

        self.assertEqual(self._project_row(estate["general"])["task_key"], "GEN")
        self.assertEqual(self._project_row(estate["empyralis"])["task_key"], "EMP")
        self.assertEqual(self._project_row(estate["ember"])["task_key"], "EMB")
        self.assertEqual(self._project_row(estate["kestrel"])["task_key"], "KES")

        still_null = self._rows(
            "SELECT count(*) AS c FROM project_tasks "
            "WHERE tenant_id = $1 AND number IS NULL",
            self.tenant_id,
        )
        self.assertEqual(still_null[0]["c"], 0)

    def test_numbers_are_dense_from_one_and_oldest_first(self) -> None:
        """GEN-1 is the OLDEST task, which is what a person expects from an
        issue tracker."""
        estate = self._seed_production_shaped_estate()
        _run(estate["repo"].backfill_task_identifiers(self.pool))

        numbered = self._numbers_for(estate["empyralis"])
        self.assertEqual([n for _, n in numbered], list(range(1, 24)))
        # The seeder made "empyralis task 0" the oldest; it must hold EMP-1.
        oldest = self._rows(
            "SELECT title, number FROM project_tasks WHERE project_id = $1 "
            "ORDER BY created_at ASC LIMIT 1",
            estate["empyralis"],
        )[0]
        self.assertEqual(oldest["title"], "empyralis task 0")
        self.assertEqual(oldest["number"], 1)

    def test_task_seq_is_seeded_to_the_projects_own_high_water_mark(self) -> None:
        estate = self._seed_production_shaped_estate()
        _run(estate["repo"].backfill_task_identifiers(self.pool))

        self.assertEqual(self._project_row(estate["empyralis"])["task_seq"], 23)
        self.assertEqual(self._project_row(estate["general"])["task_seq"], 4)
        # A project with no tasks has nothing to seed from and stays at 0.
        self.assertEqual(self._project_row(estate["ember"])["task_seq"], 0)

    def test_a_project_with_zero_tasks_still_gets_a_key(self) -> None:
        """The key is a property of the PROJECT, not of its tasks -- an empty
        project must be ready to number its first task."""
        estate = self._seed_production_shaped_estate()
        _run(estate["repo"].backfill_task_identifiers(self.pool))
        self.assertEqual(self._project_row(estate["kestrel"])["task_key"], "KES")

    def test_running_it_twice_renumbers_nothing_and_creates_no_duplicate_key(self) -> None:
        """The property that matters most. A renumbering backfill would change
        every task's identity, and identities appear in comments, links and
        agent memory."""
        estate = self._seed_production_shaped_estate()
        _run(estate["repo"].backfill_task_identifiers(self.pool))

        before_numbers = {
            key: self._numbers_for(estate[key]) for key in ("general", "empyralis")
        }
        before_projects = {
            key: self._project_row(estate[key])
            for key in ("general", "empyralis", "ember", "kestrel")
        }

        second = _run(estate["repo"].backfill_task_identifiers(self.pool))

        self.assertEqual(second["projects_keyed"], 0)
        self.assertEqual(second["tasks_numbered"], 0)
        self.assertEqual(second["projects_seeded"], 0)
        for key, expected in before_numbers.items():
            self.assertEqual(self._numbers_for(estate[key]), expected)
        for key, expected in before_projects.items():
            self.assertEqual(self._project_row(estate[key]), expected)

    def test_two_projects_whose_slugs_reduce_to_the_same_key_do_not_collide(self) -> None:
        """uq_projects_task_key is a real unique index -- a colliding backfill
        would raise, not silently overwrite."""
        estate = self._seed_production_shaped_estate()
        general_ops = self._seed_project(
            slug="general-ops", name="General Ops", created_at=_ts(5)
        )
        _run(estate["repo"].backfill_task_identifiers(self.pool))

        self.assertEqual(self._project_row(estate["general"])["task_key"], "GEN")
        self.assertEqual(self._project_row(general_ops)["task_key"], "GEN2")

    def test_a_task_created_after_the_backfill_continues_the_sequence(self) -> None:
        """The end-to-end proof: the backfill and the live allocator must agree
        about where the sequence is. A new task landing on 1 (colliding with the
        oldest backfilled task) is the failure this guards."""
        from server_modules import project_tasks_service

        estate = self._seed_production_shaped_estate()
        _run(estate["repo"].backfill_task_identifiers(self.pool))

        created = _run(
            project_tasks_service.create_task(
                tenant_id=self.tenant_id,
                workspace_id=self.workspace_id,
                project_id=estate["empyralis"],
                title="the twenty-fourth task",
                created_by="user-test",
            )
        )
        self.assertEqual(created["number"], 24)
        self.assertEqual(created["project_task_key"], "EMP")
        self.assertEqual(self._project_row(estate["empyralis"])["task_seq"], 24)

        numbers = [n for _, n in self._numbers_for(estate["empyralis"])]
        self.assertEqual(len(numbers), len(set(numbers)), "a number was issued twice")

    def test_a_task_created_before_the_backfill_keeps_its_number(self) -> None:
        """A partially-numbered project (a run interrupted, or tasks created
        between the deploy and the boot) must RESUME, never restart -- the
        offset is that project's own MAX(number)."""
        from server_modules import project_tasks_service

        estate = self._seed_production_shaped_estate()
        # A live allocation happens first: task_seq 0 -> 1, so this task is #1.
        created = _run(
            project_tasks_service.create_task(
                tenant_id=self.tenant_id,
                workspace_id=self.workspace_id,
                project_id=estate["general"],
                title="allocated before the backfill",
                created_by="user-test",
            )
        )
        self.assertEqual(created["number"], 1)

        _run(estate["repo"].backfill_task_identifiers(self.pool))

        after = self._rows(
            "SELECT number FROM project_tasks WHERE id = $1", created["id"]
        )[0]
        self.assertEqual(after["number"], 1, "an already-numbered task was renumbered")

        numbers = sorted(n for _, n in self._numbers_for(estate["general"]))
        self.assertEqual(numbers, [1, 2, 3, 4, 5])
        self.assertEqual(self._project_row(estate["general"])["task_seq"], 5)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

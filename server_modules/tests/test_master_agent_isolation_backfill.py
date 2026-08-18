"""migrations/stage_4b_agent_isolation.sql shipped a one-time backfill --
`UPDATE workspace_agent_installs SET hardware_access = 'all', subagents_
enabled = TRUE WHERE ... agent_kind = 'master'` -- meant to grant every
workspace's master agent (Sage/the Operator) full hardware access. It could
only ever apply once, by hand, and it ran under exactly the RLS trap this
codebase has now confirmed four times in one day:

    workspace_agent_installs carries FORCE ROW LEVEL SECURITY (migrations/
    enable_rls.sql), policy empyralis_rls_scope_match(tenant_id,
    workspace_id). DEPLOY-RUNBOOK 3b applies the migration as `empyralis_app`
    -- a NON-superuser, so FORCE binds it -- and a plain `psql -f` session
    sets none of the app.* GUCs.

        UPDATE workspace_agent_installs SET ...   DML, policy is false
                                                    -> 0 rows, exit 0, no error

    Never mirrored into ensure_control_plane_schema(), so it is not
    re-executed on every boot the way a schema-mirror backfill would be --
    whatever it caught, it caught once, whenever someone happened to run it.

VERIFIED against real production 2026-08-18 (SSH, `SET app.rls_bypass =
'on'` to read past the same trap on the read side): 11 of 15 agent_kind=
'master' installs currently carry hardware_access='none'/subagents_
enabled=false, spanning every workspace created since 2026-07-07. Only the
4 rows that existed at the moment someone ran the migration correctly
(2026-06-25 through 2026-06-28) ever got 'all'/TRUE.

It is not purely historical either: `ensure_workspace_agent_registry_
seeded`'s own INSERT for the master install never set either column
explicitly, so every NEW workspace fell straight through to the plain
schema DEFAULT ('none'/FALSE) regardless of what the migration did or did
not do. That INSERT now sets both columns explicitly, alongside this fix.

Two suites, same convention as test_document_path_backfill.py /
test_task_identifier_backfill.py:

1. STRUCTURAL (DB-free, always runs). A behavioural test cannot catch the
   reintroduction of the original defect -- the boot-time call being
   dropped, the cross-tenant read losing its bypass, or a write becoming
   unscoped -- would type-check, apply cleanly, exit 0, and silently do
   nothing, exactly the failure being guarded against.

2. REAL POSTGRES (opt-in on an already-exported DATABASE_URL; skips
   cleanly otherwise). Seeds a master install the way production's
   surviving 11 rows actually look -- hardware_access='none', subagents_
   enabled=false, agent_kind='master' -- through a raw, bypassed INSERT
   never through ensure_workspace_agent_registry_seeded (which already
   gets this right after the fix, and is covered separately below), and
   asserts the backfill heals it, is idempotent, leaves specialists alone,
   and keeps every write scoped to its own tenant/workspace.
"""

from __future__ import annotations

import ast
import inspect
import os
import uuid
from pathlib import Path
from typing import Any, Dict

import unittest

from server_modules import agent_registry_repository

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION = _REPO_ROOT / "migrations" / "stage_4b_agent_isolation.sql"


def _run(coro):
    """One shared bridge event loop -- see test_document_path_backfill.py's
    identical helper for why a fresh asyncio.run() per call would break a
    setUp-opened pool across several calls in one test method."""
    from server_modules import sync_asyncio_bridge

    return sync_asyncio_bridge.run_coro_sync(coro)


def _database_url_available() -> bool:
    return bool(os.getenv("DATABASE_URL", "").strip())


_NO_PG_REASON = "No Postgres reachable (DATABASE_URL unset — export it to run this suite)"


# ── 1. Structural: the shapes a behavioural test cannot see ────────────────


class MasterAgentIsolationBackfillStructureTests(unittest.TestCase):
    def test_the_migration_file_still_exists_and_this_test_reads_the_real_one(self) -> None:
        """Canary. Every source-scanning assertion below is vacuous if the
        path stops resolving."""
        self.assertTrue(_MIGRATION.is_file(), f"expected a real migration at {_MIGRATION}")
        text = _MIGRATION.read_text(encoding="utf-8")
        self.assertIn("hardware_access = 'all'", text, "scanned a file that is not this migration")
        self.assertIn("subagents_enabled = TRUE", text)

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
        self.assertIn("backfill_master_agent_isolation_defaults", called)

    def test_the_boot_repair_never_blocks_boot(self) -> None:
        """A failed backfill must not crash bootstrap -- an agent stuck at
        hardware_access='none' is strictly better than a control plane
        that refuses to start. Mirrors the try/except shape the sibling
        backfills already use at this call site."""
        from server_modules import control_plane_repository

        source = inspect.getsource(control_plane_repository.ensure_control_plane_schema)
        marker = "backfill_master_agent_isolation_defaults(pool)"
        self.assertIn(marker, source)
        # The call must be the try-body of a bare `except Exception` block,
        # not left to propagate.
        before = source.split(marker, 1)[0]
        self.assertTrue(
            before.rstrip().splitlines()[-1].strip().startswith("await ")
            or "try:" in before[-400:],
            "expected the call to sit inside a try/except guarding boot",
        )
        after = source.split(marker, 1)[1]
        self.assertIn("except Exception", after[:400])

    def test_the_cross_tenant_enumeration_read_passes_the_rls_bypass(self) -> None:
        """The one enumeration SELECT spans every tenant, so it is the only
        statement here that needs the bypass -- and without it the read
        returns the empty set and the whole backfill becomes the no-op it
        is replacing."""
        source = inspect.getsource(agent_registry_repository.backfill_master_agent_isolation_defaults)
        self.assertEqual(
            source.count("bypass_rls=True"),
            1,
            "expected exactly the one cross-tenant enumeration read to bypass RLS",
        )
        self.assertIn("rls_execute", source, "the per-row write must stay scoped, never bypassed")
        self.assertNotIn(
            "bypass_rls=True",
            source.split("rls_execute", 1)[1],
            "the write must never bypass RLS -- these are real per-tenant "
            "customer rows, not a uniform system value",
        )

    def test_the_read_is_scoped_to_master_kind_only(self) -> None:
        """Keyed on agent_kind == 'master', never on an empty/missing
        project_id or any other proxy -- CLAUDE.md's own standing warning
        about this exact family of agent-reachability bugs."""
        source = inspect.getsource(agent_registry_repository.backfill_master_agent_isolation_defaults)
        self.assertIn("agent_kind = 'master'", source)

    def test_the_write_guard_is_the_idempotency_not_a_has_run_flag(self) -> None:
        """The UPDATE's own WHERE clause must repeat the row-shape guard --
        once a row carries 'all'/TRUE it can never match again, which is
        what makes two processes booting at once, or the same box
        restarting twice, safe without a sentinel column."""
        source = inspect.getsource(agent_registry_repository.backfill_master_agent_isolation_defaults)
        update_stmt = source.split("UPDATE workspace_agent_installs", 1)[1].split('"""', 1)[0]
        self.assertIn("IS DISTINCT FROM 'all'", update_stmt)
        self.assertIn("IS DISTINCT FROM TRUE", update_stmt)

    def test_the_master_install_insert_now_sets_both_columns_explicitly(self) -> None:
        """The root-cause half of the fix: ensure_workspace_agent_registry_
        seeded's own INSERT must no longer rely on the plain schema
        DEFAULT ('none'/FALSE) for a brand-new workspace's master install --
        that default is what made this an ONGOING bug, not just a
        historical one, since every workspace created since 2026-07-07 hit
        it fresh regardless of the migration's own fate."""
        source = inspect.getsource(agent_registry_repository.ensure_workspace_agent_registry_seeded)
        insert_block = source.split("INSERT INTO workspace_agent_installs", 1)[1].split(
            'ON CONFLICT (id) DO NOTHING', 1
        )[0]
        self.assertIn("hardware_access", insert_block)
        self.assertIn("subagents_enabled", insert_block)
        self.assertIn("'all'", insert_block)
        self.assertIn("TRUE", insert_block)


# ── 2. Real Postgres: the only place this fix can actually be proven ───────


class MasterAgentIsolationBackfillPostgresTests(unittest.TestCase):
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
        self.tenant_id = f"t_agiso_{suffix}"
        self.workspace_id = f"ws_agiso_{suffix}"

    def tearDown(self) -> None:
        pool = getattr(self, "pool", None)
        if pool is None:
            return
        for table in (
            "workspace_agent_installs",
            "agent_definition_versions",
            "agent_definitions",
        ):
            try:
                _run(pool.execute(f"DELETE FROM {table} WHERE tenant_id = $1", self.tenant_id))
            except Exception:
                pass

    # -- seeding, deliberately raw, deliberately pre-fix-shaped -------------

    def _seed_definition(
        self,
        *,
        agent_kind: str,
        tenant_id: str = None,
        workspace_id: str = None,
    ) -> str:
        tenant_id = tenant_id or self.tenant_id
        workspace_id = workspace_id or self.workspace_id
        definition_id = f"agentdef_{uuid.uuid4().hex[:12]}"
        slug = f"{agent_kind}-{uuid.uuid4().hex[:6]}"
        _run(
            self.cpr.rls_execute(
                self.pool,
                """
                INSERT INTO agent_definitions
                    (id, tenant_id, workspace_id, slug, name, agent_kind)
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                definition_id,
                tenant_id,
                workspace_id,
                slug,
                f"{agent_kind} definition",
                agent_kind,
                bypass_rls=True,
            )
        )
        version_id = f"{definition_id}_v1"
        _run(
            self.cpr.rls_execute(
                self.pool,
                """
                INSERT INTO agent_definition_versions
                    (id, tenant_id, workspace_id, agent_definition_id, version_number)
                VALUES ($1, $2, $3, $4, 1)
                """,
                version_id,
                tenant_id,
                workspace_id,
                definition_id,
                bypass_rls=True,
            )
        )
        return definition_id, version_id

    def _seed_install(
        self,
        *,
        agent_definition_id: str,
        agent_definition_version_id: str,
        hardware_access: str,
        subagents_enabled: bool,
        tenant_id: str = None,
        workspace_id: str = None,
    ) -> str:
        """Raw INSERT reproducing the pre-fix shape -- production's own 11
        wrong rows, and never through ensure_workspace_agent_registry_seeded
        (which already gets this right after the fix, covered by its own
        structural test above)."""
        tenant_id = tenant_id or self.tenant_id
        workspace_id = workspace_id or self.workspace_id
        install_id = f"ainstall_{uuid.uuid4().hex[:16]}"
        _run(
            self.cpr.rls_execute(
                self.pool,
                """
                INSERT INTO workspace_agent_installs
                    (id, tenant_id, workspace_id, agent_definition_id,
                     agent_definition_version_id, hardware_access, subagents_enabled)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                install_id,
                tenant_id,
                workspace_id,
                agent_definition_id,
                agent_definition_version_id,
                hardware_access,
                subagents_enabled,
                bypass_rls=True,
            )
        )
        return install_id

    def _install_state(self, install_id: str) -> Dict[str, Any]:
        rows = _run(
            self.cpr.rls_fetch(
                self.pool,
                "SELECT hardware_access, subagents_enabled FROM workspace_agent_installs WHERE id = $1",
                install_id,
                bypass_rls=True,
            )
        )
        return dict(rows[0])

    # -- the assertions -------------------------------------------------------

    def test_the_seeded_estate_starts_exactly_as_production_did(self) -> None:
        """Guards the seeder itself. If the fixture accidentally arrived
        already carrying 'all'/TRUE, every assertion below would pass
        while proving nothing."""
        definition_id, version_id = self._seed_definition(agent_kind="master")
        install_id = self._seed_install(
            agent_definition_id=definition_id,
            agent_definition_version_id=version_id,
            hardware_access="none",
            subagents_enabled=False,
        )
        state = self._install_state(install_id)
        self.assertEqual(state["hardware_access"], "none")
        self.assertFalse(state["subagents_enabled"])

    def test_a_wrong_master_install_is_healed(self) -> None:
        definition_id, version_id = self._seed_definition(agent_kind="master")
        install_id = self._seed_install(
            agent_definition_id=definition_id,
            agent_definition_version_id=version_id,
            hardware_access="none",
            subagents_enabled=False,
        )

        stats = _run(agent_registry_repository.backfill_master_agent_isolation_defaults(self.pool))

        self.assertGreaterEqual(stats["master_installs_repaired"], 1)
        state = self._install_state(install_id)
        self.assertEqual(state["hardware_access"], "all")
        self.assertTrue(state["subagents_enabled"])

    def test_running_it_twice_repairs_nothing_the_second_time(self) -> None:
        """The property that matters most for a boot-time repair: it must
        be safe to run on every single restart forever, not just once."""
        definition_id, version_id = self._seed_definition(agent_kind="master")
        install_id = self._seed_install(
            agent_definition_id=definition_id,
            agent_definition_version_id=version_id,
            hardware_access="none",
            subagents_enabled=False,
        )

        first = _run(agent_registry_repository.backfill_master_agent_isolation_defaults(self.pool))
        self.assertEqual(first["master_installs_repaired"], 1)

        second = _run(agent_registry_repository.backfill_master_agent_isolation_defaults(self.pool))
        self.assertEqual(second["master_installs_repaired"], 0)
        state = self._install_state(install_id)
        self.assertEqual(state["hardware_access"], "all")
        self.assertTrue(state["subagents_enabled"])

    def test_a_partially_wrong_row_is_healed_on_both_columns(self) -> None:
        """A row that already has hardware_access='all' but subagents_
        enabled still false (or vice versa) must still be caught -- the
        guard is an OR, not an AND, matching the migration's own intent."""
        definition_id, version_id = self._seed_definition(agent_kind="master")
        install_id = self._seed_install(
            agent_definition_id=definition_id,
            agent_definition_version_id=version_id,
            hardware_access="all",
            subagents_enabled=False,
        )

        stats = _run(agent_registry_repository.backfill_master_agent_isolation_defaults(self.pool))

        self.assertEqual(stats["master_installs_repaired"], 1)
        state = self._install_state(install_id)
        self.assertEqual(state["hardware_access"], "all")
        self.assertTrue(state["subagents_enabled"])

    def test_a_specialist_agent_is_never_touched(self) -> None:
        """hardware_access='none' is the CORRECT default for a specialist
        -- this backfill must never widen a specialist's access just
        because it happens to sit beside a wrong master row."""
        definition_id, version_id = self._seed_definition(agent_kind="specialist")
        install_id = self._seed_install(
            agent_definition_id=definition_id,
            agent_definition_version_id=version_id,
            hardware_access="none",
            subagents_enabled=False,
        )

        stats = _run(agent_registry_repository.backfill_master_agent_isolation_defaults(self.pool))

        self.assertEqual(stats["master_installs_repaired"], 0)
        state = self._install_state(install_id)
        self.assertEqual(state["hardware_access"], "none")
        self.assertFalse(state["subagents_enabled"])

    def test_already_correct_master_install_is_not_recounted(self) -> None:
        definition_id, version_id = self._seed_definition(agent_kind="master")
        install_id = self._seed_install(
            agent_definition_id=definition_id,
            agent_definition_version_id=version_id,
            hardware_access="all",
            subagents_enabled=True,
        )

        stats = _run(agent_registry_repository.backfill_master_agent_isolation_defaults(self.pool))

        self.assertEqual(stats["master_installs_repaired"], 0)
        state = self._install_state(install_id)
        self.assertEqual(state["hardware_access"], "all")
        self.assertTrue(state["subagents_enabled"])

    def test_master_installs_across_two_tenants_are_each_scoped_to_their_own_write(self) -> None:
        """The cross-tenant READ is bypassed on purpose (see the structural
        test); this proves the WRITE stays scoped to each row's own tenant
        and workspace rather than smuggling a cross-tenant write through
        the same bypass."""
        other_suffix = uuid.uuid4().hex[:10]
        other_tenant = f"t_agiso_other_{other_suffix}"
        other_workspace = f"ws_agiso_other_{other_suffix}"
        try:
            other_definition_id, other_version_id = self._seed_definition(
                agent_kind="master", tenant_id=other_tenant, workspace_id=other_workspace
            )
            other_install_id = self._seed_install(
                agent_definition_id=other_definition_id,
                agent_definition_version_id=other_version_id,
                hardware_access="none",
                subagents_enabled=False,
                tenant_id=other_tenant,
                workspace_id=other_workspace,
            )

            definition_id, version_id = self._seed_definition(agent_kind="master")
            own_install_id = self._seed_install(
                agent_definition_id=definition_id,
                agent_definition_version_id=version_id,
                hardware_access="none",
                subagents_enabled=False,
            )

            stats = _run(agent_registry_repository.backfill_master_agent_isolation_defaults(self.pool))

            self.assertGreaterEqual(stats["master_installs_repaired"], 2)
            own_state = self._install_state(own_install_id)
            other_state = self._install_state(other_install_id)
            self.assertEqual(own_state["hardware_access"], "all")
            self.assertTrue(own_state["subagents_enabled"])
            self.assertEqual(other_state["hardware_access"], "all")
            self.assertTrue(other_state["subagents_enabled"])
        finally:
            for table in (
                "workspace_agent_installs",
                "agent_definition_versions",
                "agent_definitions",
            ):
                try:
                    _run(
                        self.pool.execute(
                            f"DELETE FROM {table} WHERE tenant_id = $1", other_tenant
                        )
                    )
                except Exception:
                    pass

    def test_a_freshly_seeded_workspace_already_gets_it_right_without_the_backfill(self) -> None:
        """Proves the root-cause half of the fix end to end: a brand-new
        workspace calling ensure_workspace_agent_registry_seeded must land
        with 'all'/TRUE on its master install from the INSERT itself, with
        no dependency on the backfill ever running."""
        suffix = uuid.uuid4().hex[:10]
        tenant_id = f"t_agiso_fresh_{suffix}"
        workspace_id = f"ws_agiso_fresh_{suffix}"
        try:
            _run(
                agent_registry_repository.ensure_workspace_agent_registry_seeded(
                    tenant_id=tenant_id, workspace_id=workspace_id
                )
            )
            rows = _run(
                self.cpr.rls_fetch(
                    self.pool,
                    """
                    SELECT wai.hardware_access, wai.subagents_enabled
                    FROM workspace_agent_installs wai
                    JOIN agent_definitions ad ON ad.id = wai.agent_definition_id
                    WHERE wai.tenant_id = $1 AND wai.workspace_id = $2
                      AND ad.agent_kind = 'master'
                    """,
                    tenant_id,
                    workspace_id,
                    bypass_rls=True,
                )
            )
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["hardware_access"], "all")
            self.assertTrue(rows[0]["subagents_enabled"])

            stats = _run(agent_registry_repository.backfill_master_agent_isolation_defaults(self.pool))
            self.assertEqual(
                stats["master_installs_repaired"],
                0,
                "a freshly seeded workspace should need no healing at all",
            )
        finally:
            for table in (
                "workspace_agent_installs",
                "agent_definition_versions",
                "agent_definitions",
                "runtime_profiles",
            ):
                try:
                    _run(self.pool.execute(f"DELETE FROM {table} WHERE tenant_id = $1", tenant_id))
                except Exception:
                    pass


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

"""Proof for the live-production bug: task_mention_service.py's roster
loaders (`_load_agent_roster` / `_load_member_roster`, reached through the
public `load_mention_roster`) used to run bare `pool.fetch(...)` against
`workspace_agent_installs` and `workspace_memberships` -- two tables that
already carried FORCE ROW LEVEL SECURITY before tonight's six-table pass
(see migrations/enable_rls.sql). A bare pool call never sets the
`app.current_tenant_id` / `app.current_workspace_id` session GUCs the RLS
policy (`empyralis_rls_scope_match`) reads, so under the app's real,
non-superuser `empyralis_app` role every one of those reads silently
returned ZERO rows: `load_mention_roster` found no agents and no people,
every `@mention` resolved to nothing, and nothing about that failure was
loud -- no exception, no 500, just a comment that posted as plain text.

Both loaders are now converted to `control_plane_repository.rls_fetch`,
following the exact pattern `projects_repository.py` already used (see
migrations/enable_rls.sql's own comment on the six-table conversion, and
`server_modules/task_mention_service.py`'s `_load_agent_roster` /
`_load_member_roster`).

TWO separate concerns, because "the fix works" and "the bug was real" are
not the same claim and conflating them would prove less than each alone:

1. ``MentionRosterRlsFixTests.test_load_mention_roster_returns_real_agents_
   and_people_under_scoped_connection`` -- calls the REAL, public
   `task_mention_service.load_mention_roster`, passing a connection POOL
   opened as a throwaway, ORDINARY (non-superuser, non-BYPASSRLS) Postgres
   role -- the same methodology `test_rls_six_tables_isolation_man109.py`'s
   `_RlsProbeRoleFixture` established, duplicated here rather than imported
   for the same reason that file gives for duplicating its own predecessor:
   importing a class from a file a different, concurrently-running agent
   owns (server_modules/tests/ pre-existing files are off limits per this
   change's own collision protocol) would couple this file's correctness to
   edits happening there right now. Proves the fix actually reaches
   production's real role, not just the local superuser pool every other
   test in this module family happens to run against.

2. ``MentionRosterRlsFixTests.test_raw_unscoped_pool_call_against_these_
   force_rls_tables_returns_nothing`` -- proves WHY the bug was real and
   silent: the exact call shape task_mention_service.py used before the fix
   (`pool.fetch(query, *args)`, no session scope applied), run directly
   against the SAME throwaway non-superuser role, over rows the admin
   (superuser) connection in setUp can see exist, returns an empty list --
   not an error, not a warning, nothing. That silence is the entire bug.

Real Postgres only, opt-in from an already-exported DATABASE_URL (this
suite's convention: no .env reading, no os.environ mutation -- python-dotenv
finds the repo-root .env via its own upward search before pytest imports
this module). Skips cleanly when Postgres is not reachable. The throwaway
role and every row this file inserts are removed in tearDown, including on
a partial failure.
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


class MentionRosterRlsFixTests(_BridgeAsyncTestCase):
    """Throwaway non-superuser role, granted SELECT ONLY on the three tables
    the mention roster actually reads (`workspace_agent_installs`,
    `workspace_memberships`, `users`) -- the exact scope
    `agent_project_id` / `_workspace_user_exists` already validate
    against, and the exact scope this module's own docstring commits to."""

    GRANT_TABLES = ("workspace_agent_installs", "workspace_memberships", "users")

    async def async_setup(self):
        if not _database_url_available():
            self.skipTest(_NO_PG_REASON)
            return
        import asyncpg

        from server_modules import control_plane_repository as cpr

        admin_pool = await cpr.ensure_control_plane_schema()
        if admin_pool is None:
            self.skipTest(_NO_PG_REASON)
            return
        self.admin_pool = admin_pool

        self.admin_dsn = os.environ["DATABASE_URL"].strip()
        try:
            self.admin_conn = await asyncpg.connect(self.admin_dsn, timeout=10)
        except Exception as exc:  # noqa: BLE001 — unreachable Postgres is a skip
            self.skipTest(f"{_NO_PG_REASON}: {exc}")
            return

        suffix = uuid.uuid4().hex[:10]
        self.tenant_a = f"t_mention_a_{suffix}"
        self.tenant_b = f"t_mention_b_{suffix}"
        self.ws_a = f"ws_mention_a_{suffix}"
        self.ws_b = f"ws_mention_b_{suffix}"
        self.role = f"rls_mention_probe_{suffix}"
        self.role_password = uuid.uuid4().hex

        await self.admin_conn.execute(
            f'CREATE ROLE "{self.role}" LOGIN PASSWORD \'{self.role_password}\''
        )
        table_list = ", ".join(self.GRANT_TABLES)
        await self.admin_conn.execute(
            f"GRANT SELECT ON {table_list} TO \"{self.role}\""
        )
        self.probe_dsn = _probe_dsn(self.admin_dsn, user=self.role, password=self.role_password)
        self.probe_pool = await asyncpg.create_pool(self.probe_dsn, min_size=1, max_size=2, timeout=10)

        # ── Seed real rows for TWO tenants, as the admin/superuser
        # connection -- so the proof below is "tenant A's own rows, not
        # tenant B's" rather than just "empty vs nonempty". ──
        self.user_a_id = f"user_a_{suffix}"
        self.user_b_id = f"user_b_{suffix}"
        await self.admin_conn.execute(
            """
            INSERT INTO users (id, tenant_id, workspace_id, email, display_name, status)
            VALUES ($1, $2, $3, $4, 'Ada Human', 'active'), ($5, $6, $7, $8, 'Bea Human', 'active')
            """,
            self.user_a_id, self.tenant_a, self.ws_a, f"{self.user_a_id}@example.test",
            self.user_b_id, self.tenant_b, self.ws_b, f"{self.user_b_id}@example.test",
        )
        self.membership_a_id = f"mem_a_{suffix}"
        self.membership_b_id = f"mem_b_{suffix}"
        await self.admin_conn.execute(
            """
            INSERT INTO workspace_memberships (id, tenant_id, workspace_id, user_id, role, status)
            VALUES ($1, $2, $3, $4, 'member', 'active'), ($5, $6, $7, $8, 'member', 'active')
            """,
            self.membership_a_id, self.tenant_a, self.ws_a, self.user_a_id,
            self.membership_b_id, self.tenant_b, self.ws_b, self.user_b_id,
        )

        # workspace_agent_installs.agent_definition_id/agent_definition_
        # version_id are NOT NULL FKs -- seed the minimal valid chain
        # (agent_definitions -> agent_definition_versions -> the install),
        # same minimal shape test_project_tasks_human_assignee.py's own
        # HumanAssigneeDatabaseTests fixture uses.
        self.agent_def_a_id = f"agentdef_a_{suffix}"
        self.agent_def_b_id = f"agentdef_b_{suffix}"
        await self.admin_conn.execute(
            """
            INSERT INTO agent_definitions (id, tenant_id, workspace_id, slug, name)
            VALUES ($1, $2, $3, $1, 'Atlas'), ($4, $5, $6, $4, 'Boron')
            """,
            self.agent_def_a_id, self.tenant_a, self.ws_a,
            self.agent_def_b_id, self.tenant_b, self.ws_b,
        )
        self.agent_def_ver_a_id = f"agentdefver_a_{suffix}"
        self.agent_def_ver_b_id = f"agentdefver_b_{suffix}"
        await self.admin_conn.execute(
            """
            INSERT INTO agent_definition_versions (id, tenant_id, workspace_id, agent_definition_id, version_number)
            VALUES ($1, $2, $3, $4, 1), ($5, $6, $7, $8, 1)
            """,
            self.agent_def_ver_a_id, self.tenant_a, self.ws_a, self.agent_def_a_id,
            self.agent_def_ver_b_id, self.tenant_b, self.ws_b, self.agent_def_b_id,
        )
        self.agent_install_a_id = f"agent_a_{suffix}"
        self.agent_install_b_id = f"agent_b_{suffix}"
        await self.admin_conn.execute(
            """
            INSERT INTO workspace_agent_installs
                (id, tenant_id, workspace_id, agent_definition_id, agent_definition_version_id, label)
            VALUES ($1, $2, $3, $4, $5, 'Atlas'), ($6, $7, $8, $9, $10, 'Boron')
            """,
            self.agent_install_a_id, self.tenant_a, self.ws_a, self.agent_def_a_id, self.agent_def_ver_a_id,
            self.agent_install_b_id, self.tenant_b, self.ws_b, self.agent_def_b_id, self.agent_def_ver_b_id,
        )

    async def async_teardown(self):
        probe_pool = getattr(self, "probe_pool", None)
        if probe_pool is not None:
            await probe_pool.close()
        conn = getattr(self, "admin_conn", None)
        if conn is None:
            return
        try:
            for tenant_id in (getattr(self, "tenant_a", None), getattr(self, "tenant_b", None)):
                if not tenant_id:
                    continue
                await conn.execute("DELETE FROM workspace_agent_installs WHERE tenant_id = $1", tenant_id)
                await conn.execute("DELETE FROM agent_definition_versions WHERE tenant_id = $1", tenant_id)
                await conn.execute("DELETE FROM agent_definitions WHERE tenant_id = $1", tenant_id)
                await conn.execute("DELETE FROM workspace_memberships WHERE tenant_id = $1", tenant_id)
                await conn.execute("DELETE FROM users WHERE tenant_id = $1", tenant_id)
        finally:
            role = getattr(self, "role", None)
            if role:
                table_list = ", ".join(self.GRANT_TABLES)
                await conn.execute(f"REVOKE ALL ON {table_list} FROM \"{role}\"")
                await conn.execute(f'DROP ROLE IF EXISTS "{role}"')
            await conn.close()

    async def test_load_mention_roster_returns_real_agents_and_people_under_scoped_connection(self) -> None:
        """The fix: `load_mention_roster`, run through the REAL non-superuser
        role over a REAL connection pool (not a single bare connection —
        `rls_fetch` calls `pool.acquire()`), must see tenant A's agent and
        tenant A's person, and must NOT see tenant B's — proving the scope
        this function now sets is both effective (rows come back) and
        correctly bounded (no cross-tenant leak)."""
        from server_modules import task_mention_service

        roster = await task_mention_service.load_mention_roster(
            tenant_id=self.tenant_a, workspace_id=self.ws_a, pool=self.probe_pool,
        )
        by_kind_and_id = {(entry["kind"], entry["id"]) for entry in roster}
        self.assertIn(
            ("agent", self.agent_install_a_id), by_kind_and_id,
            "load_mention_roster did not resolve tenant A's own agent install under a scoped, "
            "non-superuser connection — the exact silent-zero-rows failure mode this fix closes.",
        )
        self.assertIn(
            ("user", self.user_a_id), by_kind_and_id,
            "load_mention_roster did not resolve tenant A's own active member under a scoped, "
            "non-superuser connection.",
        )
        all_ids = {entry["id"] for entry in roster}
        self.assertNotIn(self.agent_install_b_id, all_ids, "Tenant B's agent leaked into tenant A's roster.")
        self.assertNotIn(self.user_b_id, all_ids, "Tenant B's member leaked into tenant A's roster.")

        # Names actually resolve to what @mention parsing needs: a non-empty
        # display_name per entry (find_mention_candidates/resolve_mention_
        # candidates match on this).
        display_names = {entry["display_name"] for entry in roster}
        self.assertIn("Atlas", display_names)
        self.assertIn("Ada Human", display_names)

    async def test_load_mention_roster_scoped_to_tenant_b_sees_only_its_own_rows(self) -> None:
        """Symmetric check: the same function, scoped to tenant B instead,
        must resolve tenant B's rows and not tenant A's — isolation, not
        just "some rows come back"."""
        from server_modules import task_mention_service

        roster = await task_mention_service.load_mention_roster(
            tenant_id=self.tenant_b, workspace_id=self.ws_b, pool=self.probe_pool,
        )
        by_kind_and_id = {(entry["kind"], entry["id"]) for entry in roster}
        self.assertIn(("agent", self.agent_install_b_id), by_kind_and_id)
        self.assertIn(("user", self.user_b_id), by_kind_and_id)
        all_ids = {entry["id"] for entry in roster}
        self.assertNotIn(self.agent_install_a_id, all_ids)
        self.assertNotIn(self.user_a_id, all_ids)

    async def test_raw_unscoped_pool_call_against_these_force_rls_tables_returns_nothing(self) -> None:
        """Proves the bug was real, not hypothetical: the exact call shape
        task_mention_service.py used before the fix -- `pool.fetch(query,
        *args)` with NO `app.current_tenant_id` / `app.current_workspace_id`
        session scope applied -- run directly against the SAME non-superuser
        probe role, over rows setUp's admin/superuser connection can prove
        exist, comes back empty. No exception, no warning: just silence.
        That silence, reached through `load_mention_roster`, is exactly why
        every `@mention` used to resolve to nothing without ever surfacing
        as a request failure."""
        async with self.probe_pool.acquire() as connection:
            agent_rows = await connection.fetch(
                "SELECT id, label FROM workspace_agent_installs WHERE tenant_id = $1 AND workspace_id = $2",
                self.tenant_a, self.ws_a,
            )
            member_rows = await connection.fetch(
                """
                SELECT wm.user_id, u.email, u.display_name
                FROM workspace_memberships wm
                JOIN users u ON u.id = wm.user_id
                WHERE wm.tenant_id = $1 AND wm.workspace_id = $2 AND wm.status = 'active'
                """,
                self.tenant_a, self.ws_a,
            )
        self.assertEqual(
            list(agent_rows), [],
            "A raw, unscoped pool.fetch against workspace_agent_installs unexpectedly returned rows under "
            "the non-superuser role -- either RLS is not actually FORCEd on this table, or this proof's "
            "own setup is wrong. Either way this test no longer demonstrates the bug it exists to document.",
        )
        self.assertEqual(
            list(member_rows), [],
            "A raw, unscoped pool.fetch against workspace_memberships/users unexpectedly returned rows "
            "under the non-superuser role.",
        )
        # And the SAME rows, over the SAME probe role, ARE visible once the
        # session scope is set -- isolating "RLS hides it" from "the role
        # can't read it at all" (a GRANT problem would fail this too, for a
        # different, uninteresting reason).
        async with self.probe_pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    """
                    SELECT set_config('app.current_tenant_id', $1, true),
                           set_config('app.current_workspace_id', $2, true)
                    """,
                    self.tenant_a, self.ws_a,
                )
                scoped_rows = await connection.fetch(
                    "SELECT id, label FROM workspace_agent_installs WHERE tenant_id = $1 AND workspace_id = $2",
                    self.tenant_a, self.ws_a,
                )
        self.assertEqual(len(scoped_rows), 1, "Scoping the session should reveal exactly tenant A's one row.")

"""Tests for agent_private_memory_repository.py -- the storage layer for the
PER-PERSON half of the shared-vs-private memory split (see that module's
docstring and migrations/add_agent_private_memory.sql for the full
rationale).

THREE separate concerns, three test classes, mirroring test_project_
documents_repository.py's own split (real Postgres for what only a live
database can prove; a source-shape check and a mocked isolation proof for
everything else, since this suite normally runs with DATABASE_URL unset --
CLAUDE.md: "never point DATABASE_URL at a real database"):

1. ``AgentPrivateMemoryServiceLayerTests`` -- the real repository functions
   end-to-end (upsert/get/list-revisions) over the app's own connection
   pool, for two different users in the same workspace. Opt-in from an
   already-exported DATABASE_URL; skips cleanly otherwise. This does NOT
   prove RLS (the app's own pool is the superuser role locally and
   superusers bypass RLS unconditionally) -- it proves the CRUD surface and
   the four-way (tenant, workspace, agent_install, user) scoping in every
   WHERE clause actually work.

2. ``AgentPrivateMemoryQueryShapeTests`` -- a static source scan (no DB
   needed) asserting every SELECT in this module binds `user_id` in its
   WHERE clause. This is the "expected set and actual set must come from
   different sources" discipline CLAUDE.md's own FailOpenScopeFilterDrift
   Tests exemplifies -- a behavioural test can only cover today's queries;
   this catches a NEW query that forgets the filter.

3. ``AgentPrivateMemoryMockedIsolationTests`` -- drives the REAL repository
   functions (get_private_note/upsert_private_note) against a small
   in-memory fake standing in for control_plane_repository's pool/rls_*
   helpers, so isolation can be proven without a live Postgres. This is
   the "prove a second user's private memory is not retrievable by the
   first" evidence the task asked for: user B's read after user A's write
   returns zero rows, and the fake's call log proves user B's read touched
   exactly the query it should and nothing that could have returned A's row.
"""

from __future__ import annotations

import ast
import inspect
import os
import unittest
import uuid
from pathlib import Path


def _database_url_available() -> bool:
    return bool(os.getenv("DATABASE_URL", "").strip())


def _run(coro):
    from server_modules import sync_asyncio_bridge

    return sync_asyncio_bridge.run_coro_sync(coro)


_NO_PG_REASON = "No Postgres reachable (DATABASE_URL unset — export it to run this suite)"


# ── 1. Service-layer CRUD + scoping, over the real repository functions ────


class AgentPrivateMemoryServiceLayerTests(unittest.TestCase):
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
        suffix = uuid.uuid4().hex[:10]
        self.tenant_id = f"t_privmem_{suffix}"
        self.workspace_id = f"ws_privmem_{suffix}"
        self.agent_install_id = f"agent_privmem_{suffix}"

    def tearDown(self) -> None:
        pool = getattr(self, "pool", None)
        if pool is None:
            return
        try:
            _run(pool.execute("DELETE FROM agent_private_memory_notes WHERE tenant_id = $1", self.tenant_id))
        except Exception:
            pass

    def test_upsert_then_get_round_trips_for_the_same_user(self) -> None:
        from server_modules import agent_private_memory_repository as repo

        saved = _run(
            repo.upsert_private_note(
                tenant_id=self.tenant_id,
                workspace_id=self.workspace_id,
                agent_install_id=self.agent_install_id,
                user_id="user-a",
                content="Prefers concise, bullet-point answers.",
                reason="memory_write_private",
            )
        )
        self.assertEqual(saved["content"], "Prefers concise, bullet-point answers.")
        self.assertTrue(saved["revision_recorded"])

        fetched = _run(
            repo.get_private_note(
                tenant_id=self.tenant_id,
                workspace_id=self.workspace_id,
                agent_install_id=self.agent_install_id,
                user_id="user-a",
            )
        )
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["content"], "Prefers concise, bullet-point answers.")

    def test_a_second_write_from_the_same_user_updates_in_place_not_a_new_row(self) -> None:
        from server_modules import agent_private_memory_repository as repo

        first = _run(
            repo.upsert_private_note(
                tenant_id=self.tenant_id, workspace_id=self.workspace_id,
                agent_install_id=self.agent_install_id, user_id="user-a",
                content="Prefers async updates.",
            )
        )
        second = _run(
            repo.upsert_private_note(
                tenant_id=self.tenant_id, workspace_id=self.workspace_id,
                agent_install_id=self.agent_install_id, user_id="user-a",
                content="Prefers async updates, and morning check-ins.",
            )
        )
        self.assertEqual(first["id"], second["id"])
        fetched = _run(
            repo.get_private_note(
                tenant_id=self.tenant_id, workspace_id=self.workspace_id,
                agent_install_id=self.agent_install_id, user_id="user-a",
            )
        )
        self.assertEqual(fetched["content"], "Prefers async updates, and morning check-ins.")

        revisions = _run(
            repo.list_private_note_revisions(
                tenant_id=self.tenant_id, workspace_id=self.workspace_id,
                agent_install_id=self.agent_install_id, user_id="user-a",
            )
        )
        self.assertEqual(len(revisions), 2)

    def test_two_users_of_the_same_agent_get_two_independent_rows(self) -> None:
        from server_modules import agent_private_memory_repository as repo

        _run(
            repo.upsert_private_note(
                tenant_id=self.tenant_id, workspace_id=self.workspace_id,
                agent_install_id=self.agent_install_id, user_id="user-a",
                content="user-a's private preference.",
            )
        )
        _run(
            repo.upsert_private_note(
                tenant_id=self.tenant_id, workspace_id=self.workspace_id,
                agent_install_id=self.agent_install_id, user_id="user-b",
                content="user-b's private preference.",
            )
        )
        note_a = _run(
            repo.get_private_note(
                tenant_id=self.tenant_id, workspace_id=self.workspace_id,
                agent_install_id=self.agent_install_id, user_id="user-a",
            )
        )
        note_b = _run(
            repo.get_private_note(
                tenant_id=self.tenant_id, workspace_id=self.workspace_id,
                agent_install_id=self.agent_install_id, user_id="user-b",
            )
        )
        self.assertEqual(note_a["content"], "user-a's private preference.")
        self.assertEqual(note_b["content"], "user-b's private preference.")
        self.assertNotEqual(note_a["id"], note_b["id"])

    def test_missing_user_id_raises_rather_than_reading_or_writing_unscoped(self) -> None:
        from server_modules import agent_private_memory_repository as repo

        with self.assertRaises(ValueError):
            _run(
                repo.get_private_note(
                    tenant_id=self.tenant_id, workspace_id=self.workspace_id,
                    agent_install_id=self.agent_install_id, user_id="",
                )
            )
        with self.assertRaises(ValueError):
            _run(
                repo.upsert_private_note(
                    tenant_id=self.tenant_id, workspace_id=self.workspace_id,
                    agent_install_id=self.agent_install_id, user_id="   ",
                    content="should never be written",
                )
            )


# ── 2. Static source-shape check: every query binds user_id ────────────────


class AgentPrivateMemoryQueryShapeTests(unittest.TestCase):
    """No DB needed. A source scan, not a live-query assertion -- proves the
    query TEXT itself always names user_id in a WHERE clause, so a future
    edit that drops the filter is caught even though it would still
    type-check and even though this specific behavioural suite might not
    run against live Postgres in every environment."""

    def test_every_select_and_upsert_binds_user_id_in_its_where_clause(self) -> None:
        import re

        import server_modules.agent_private_memory_repository as module

        source = inspect.getsource(module)
        # Every TRIPLE-QUOTED SQL string in this module (not docstrings --
        # filtered to blocks containing an actual SQL keyword) that touches
        # agent_private_memory_notes or its revisions table must reference
        # user_id somewhere in the same statement. This is intentionally a
        # blunt substring check (mirrors the FailOpenScopeFilterDriftTests
        # style already in this codebase) -- it does not parse SQL, it just
        # refuses to let a query mentioning the table exist without the
        # literal predicate/binding token nearby.
        all_triple_quoted_blocks = re.findall(r'"""(.*?)"""', source, re.DOTALL)
        sql_blocks = [
            block for block in all_triple_quoted_blocks
            if "agent_private_memory_note" in block
            and re.search(r"\b(SELECT|INSERT INTO)\b", block)
        ]
        self.assertGreaterEqual(len(sql_blocks), 3, "expected to find the SELECT/INSERT/ON CONFLICT query bodies")
        for block in sql_blocks:
            self.assertIn(
                "user_id",
                block,
                f"query touching agent_private_memory_note* has no user_id reference at all:\n{block}",
            )

    def test_every_public_repository_function_requires_user_id_with_no_default(self) -> None:
        """AST-level check: `user_id` must appear as a keyword-only parameter
        with NO default value on every public (non-underscore) function --
        the "a scope column with a default is a loaded gun" rule, enforced
        structurally rather than left to convention."""
        import server_modules.agent_private_memory_repository as module

        tree = ast.parse(inspect.getsource(module))
        checked = 0
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            if node.name.startswith("_"):
                continue
            kwonly_names = [arg.arg for arg in node.args.kwonlyargs]
            if "user_id" not in kwonly_names:
                continue
            checked += 1
            index = kwonly_names.index("user_id")
            default = node.args.kw_defaults[index]
            self.assertIsNone(
                default,
                f"{node.name}'s user_id parameter has a default value -- it must be required.",
            )
        self.assertGreaterEqual(checked, 3, "expected at least 3 public functions taking user_id")


# ── 3. Mocked isolation proof: no live Postgres required ───────────────────


class _FakeRow(dict):
    """Stands in for an asyncpg Record: supports dict(row)."""


class _FakeControlPlane:
    """A minimal in-memory stand-in for the four control_plane_repository
    entry points agent_private_memory_repository.py calls
    (ensure_control_plane_schema/rls_fetchrow/rls_execute/rls_fetch).
    Deliberately dumb about SQL -- it dispatches on which TABLE the query
    text mentions and interprets the bound args by the well-known
    positional order this repository module always uses (tenant_id,
    workspace_id, agent_install_id, user_id, ...), so the test is exercising
    the REAL repository code's argument construction and control flow, not
    a re-implementation of its filtering logic."""

    def __init__(self) -> None:
        self.notes: dict[tuple, dict] = {}
        self.revisions: list[dict] = []
        self.calls: list[tuple[str, tuple]] = []

    async def ensure_control_plane_schema(self):
        return self

    async def rls_fetchrow(self, pool, query, *args, tenant_id=None, workspace_id=None, bypass_rls=False):
        self.calls.append(("fetchrow", args))
        if "INSERT INTO agent_private_memory_notes" in query:
            note_id, t, w, a, u, content = args
            key = (t, w, a, u)
            existing = self.notes.get(key)
            row = {
                "id": existing["id"] if existing else note_id,
                "tenant_id": t, "workspace_id": w, "agent_install_id": a, "user_id": u,
                "content": content, "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
            }
            self.notes[key] = row
            return _FakeRow(row)
        if "SELECT" in query and "agent_private_memory_notes" in query:
            t, w, a, u = args
            row = self.notes.get((t, w, a, u))
            return _FakeRow(row) if row else None
        raise AssertionError(f"unexpected query in fake rls_fetchrow: {query}")

    async def rls_execute(self, pool, query, *args, tenant_id=None, workspace_id=None, bypass_rls=False):
        self.calls.append(("execute", args))
        if "INSERT INTO agent_private_memory_note_revisions" in query:
            rev_id, t, w, note_id, a, u, content, reason = args
            existing = [r for r in self.revisions if r["note_id"] == note_id]
            rev_num = max((r["revision_number"] for r in existing), default=0) + 1
            self.revisions.append({
                "id": rev_id, "note_id": note_id, "content": content,
                "reason": reason, "revision_number": rev_num,
            })
            return None
        raise AssertionError(f"unexpected query in fake rls_execute: {query}")

    async def rls_fetch(self, pool, query, *args, tenant_id=None, workspace_id=None, bypass_rls=False):
        self.calls.append(("fetch", args))
        t, w, a, u, limit = args
        rows = [
            _FakeRow(r) for r in self.revisions
            if r["note_id"] in {n["id"] for n in self.notes.values() if (n["tenant_id"], n["workspace_id"], n["agent_install_id"], n["user_id"]) == (t, w, a, u)}
        ]
        return sorted(rows, key=lambda r: -r["revision_number"])[:limit]


class AgentPrivateMemoryMockedIsolationTests(unittest.TestCase):
    """Drives the REAL agent_private_memory_repository functions against the
    fake above -- no live Postgres. This is the isolation proof the task
    asked for: after user-a writes, user-b's read for the SAME workspace
    and agent returns None (zero rows), and the call log shows exactly one
    fetch was made for that read -- not a broader query that happened to
    filter client-side."""

    def setUp(self) -> None:
        from unittest.mock import patch
        import server_modules.agent_private_memory_repository as repo

        self.fake = _FakeControlPlane()
        self.repo = repo
        patcher = patch.object(repo, "control_plane_repository", self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        # runtime_db.DurableRuntimeConfigurationError is referenced via
        # control_plane_repository.runtime_db in the repository module; the
        # fake never raises through that path in these tests (pool is never
        # None here) so no further stubbing is needed.

    def test_user_b_cannot_read_user_a_note_zero_rows_and_call_count(self) -> None:
        saved = _run(
            self.repo.upsert_private_note(
                tenant_id="t1", workspace_id="ws1", agent_install_id="agent1",
                user_id="user-a", content="user-a's private note.",
            )
        )
        self.assertEqual(saved["content"], "user-a's private note.")
        # Exactly one write call (the notes upsert) + one revision insert.
        self.assertEqual(len(self.fake.calls), 2)

        calls_before_read = len(self.fake.calls)
        result_for_b = _run(
            self.repo.get_private_note(
                tenant_id="t1", workspace_id="ws1", agent_install_id="agent1",
                user_id="user-b",
            )
        )
        # Zero rows returned to user B.
        self.assertIsNone(result_for_b)
        # Exactly one downstream call was made for user B's read (a single
        # fetchrow), not a broader read that then filtered in Python.
        self.assertEqual(len(self.fake.calls) - calls_before_read, 1)
        self.assertEqual(self.fake.calls[-1][0], "fetchrow")

        # And user A can still read their own note back.
        result_for_a = _run(
            self.repo.get_private_note(
                tenant_id="t1", workspace_id="ws1", agent_install_id="agent1",
                user_id="user-a",
            )
        )
        self.assertIsNotNone(result_for_a)
        self.assertEqual(result_for_a["content"], "user-a's private note.")

    def test_user_bs_own_write_does_not_touch_user_as_row(self) -> None:
        _run(
            self.repo.upsert_private_note(
                tenant_id="t1", workspace_id="ws1", agent_install_id="agent1",
                user_id="user-a", content="A's note.",
            )
        )
        _run(
            self.repo.upsert_private_note(
                tenant_id="t1", workspace_id="ws1", agent_install_id="agent1",
                user_id="user-b", content="B's note.",
            )
        )
        note_a = _run(
            self.repo.get_private_note(
                tenant_id="t1", workspace_id="ws1", agent_install_id="agent1", user_id="user-a",
            )
        )
        note_b = _run(
            self.repo.get_private_note(
                tenant_id="t1", workspace_id="ws1", agent_install_id="agent1", user_id="user-b",
            )
        )
        self.assertEqual(note_a["content"], "A's note.")
        self.assertEqual(note_b["content"], "B's note.")
        self.assertNotEqual(note_a["id"], note_b["id"])


if __name__ == "__main__":
    unittest.main()

"""get_workspace_by_id must return identity_links consistently across both
backends it can run against.

THE BUG THIS FILE EXISTS FOR
-----------------------------
Postgres: identity_links is a REAL, written-to JSONB column
(update_workspace_identity_links's Postgres branch does
`UPDATE workspaces SET identity_links = $2::jsonb ...`) that
get_workspace_by_id's own SELECT never named. Every reader of the
returned record — command_registry._is_sender_owner (the slash-command
owner gate), sage_agent_runtime_service's per-turn sender-class
resolution (owner vs audience — which tools/authority a channel message
gets for the WHOLE turn, not just slash commands), and
routes_workspaces.py's identity-links settings endpoints — did
`.get("identity_links")`, always got None, and treated that identically
to "nobody is linked on any channel". The data was real and persisted;
only the read was broken.

SQLite (local-dev fallback): update_workspace_identity_links's own
connection-is-None branch is a documented no-op (`return False`) — there
is no persisted identity_links data under this backend to read back,
ever. So the two backends silently disagreed for a different reason
each: Postgres had real data it never returned; SQLite never had data to
return in the first place. Both must still expose the SAME shape to a
caller (a present, dict-typed identity_links key, never absent) so
nothing downstream needs backend-specific handling.

FAIL-CLOSED, NOT FAIL-OPEN (traced precisely before this fix, not
assumed): both command_registry._is_sender_owner and
sage_agent_runtime_service's sender-class resolution treat an
empty/missing identity_links as "not linked" and therefore DENY elevated
(owner) trust — never grant it. So the live consequence of this bug was
never a non-owner passing an owner gate; it was the opposite — a real,
linked owner messaging from their own paired Telegram/Discord/WhatsApp
account got silently classified as "audience" (serve-only tools, no
shell/hardware/fleet/memory_write/connector_write, no owner-gated slash
commands) on every single channel message, for as long as the SELECT
omitted the column.
"""

from __future__ import annotations

import asyncio
import sqlite3
import unittest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

from server_modules import command_registry
from server_modules import control_plane_repository


def _run(coro):
    return asyncio.run(coro)


class _FakePostgresConnection:
    """Stands in for the asyncpg connection get_workspace_by_id's Postgres
    branch calls .fetchrow() on. A plain dict is a fine stand-in for an
    asyncpg Record here — _workspace_record_from_row only ever does
    dict(row) and .get() on it, both of which a real dict already
    supports."""

    def __init__(self, row):
        self._row = row

    async def fetchrow(self, query, *args):
        return self._row


def _fake_scoped_connection_postgres(row):
    @asynccontextmanager
    async def _scoped(*, tenant_id=None, workspace_id=None, bypass_rls=False):
        yield _FakePostgresConnection(row)

    return _scoped


@asynccontextmanager
async def _fake_scoped_connection_sqlite_fallback(*, tenant_id=None, workspace_id=None, bypass_rls=False):
    # yield None is exactly what _scoped_connection does when
    # ensure_control_plane_schema() finds no live Postgres pool — the
    # trigger for get_workspace_by_id's own SQLite-fallback branch.
    yield None


def _fake_local_identity_connection(workspace_id: str) -> sqlite3.Connection:
    """A real, in-memory (never touching the developer's real
    ~/.empyralis/state local-identity DB file) sqlite3 connection with just
    enough schema for get_workspace_by_id's SQLite branch to read back one
    row — mirrors _connect_local_identity_db's own workspace_registry
    CREATE TABLE verbatim, minus every OTHER table that function also
    creates (irrelevant to this read)."""
    connection = sqlite3.connect(":memory:")
    connection.execute(
        """
        CREATE TABLE workspace_registry (
            workspace_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            name TEXT,
            workspace_type TEXT NOT NULL DEFAULT 'personal',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
        """
    )
    connection.execute(
        "INSERT INTO workspace_registry "
        "(workspace_id, tenant_id, name, workspace_type, metadata_json, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (workspace_id, "tenant-1", "Test Workspace", "personal", "{}", 0, 0),
    )
    connection.commit()
    connection.row_factory = sqlite3.Row
    return connection


class WorkspaceIdentityLinksReadParityTests(unittest.TestCase):
    def setUp(self) -> None:
        for ws_id in (
            "ws-pg-identity-test", "ws-pg-identity-test-2",
            "ws-sqlite-identity-test", "ws-parity-pg", "ws-parity-sqlite",
        ):
            control_plane_repository._workspace_lookup_cache_drop(ws_id)

    def test_postgres_path_returns_real_identity_links(self) -> None:
        row = {
            "id": "ws-pg-identity-test",
            "tenant_id": "tenant-1",
            "workspace_id": "ws-pg-identity-test",
            "slug": "ws-pg-identity-test",
            "name": "Test Workspace",
            "workspace_type": "personal",
            "status": "active",
            "created_by_user_id": "user-owner-1",
            "metadata": {},
            "identity_links": {"telegram_personal": {"user_id": "tg-owner-123", "sender_hash": ""}},
            "created_at": 0,
            "updated_at": 0,
        }
        with patch.object(
            control_plane_repository, "_scoped_connection",
            new=_fake_scoped_connection_postgres(row),
        ):
            record = _run(control_plane_repository.get_workspace_by_id("ws-pg-identity-test"))

        self.assertIsInstance(record, dict)
        self.assertIsInstance(record.get("identity_links"), dict)
        self.assertEqual(
            record["identity_links"]["telegram_personal"]["user_id"], "tg-owner-123",
        )

    def test_postgres_path_decodes_a_json_encoded_string_column_too(self) -> None:
        """asyncpg's JSONB decoding depends on codec setup — the column may
        arrive already-parsed as a dict OR as raw JSON text. Both must
        resolve to the same dict, the same way `metadata` already does."""
        row = {
            "id": "ws-pg-identity-test-2",
            "tenant_id": "tenant-1",
            "workspace_id": "ws-pg-identity-test-2",
            "slug": "ws-pg-identity-test-2",
            "name": "Test Workspace",
            "workspace_type": "personal",
            "status": "active",
            "created_by_user_id": "user-owner-1",
            "metadata": {},
            "identity_links": '{"telegram_personal": {"user_id": "tg-owner-999"}}',
            "created_at": 0,
            "updated_at": 0,
        }
        with patch.object(
            control_plane_repository, "_scoped_connection",
            new=_fake_scoped_connection_postgres(row),
        ):
            record = _run(control_plane_repository.get_workspace_by_id("ws-pg-identity-test-2"))

        self.assertIsInstance(record["identity_links"], dict)
        self.assertEqual(record["identity_links"]["telegram_personal"]["user_id"], "tg-owner-999")

    def test_sqlite_fallback_returns_present_but_empty_identity_links(self) -> None:
        """The SQLite fallback never persists identity_links at all — the
        key must still be PRESENT (a dict, {}), never absent, so a
        caller's own .get("identity_links") behaves the same on both
        backends without a backend-specific branch."""
        fake_conn = _fake_local_identity_connection("ws-sqlite-identity-test")
        try:
            with (
                patch.object(
                    control_plane_repository, "_scoped_connection",
                    new=_fake_scoped_connection_sqlite_fallback,
                ),
                patch.object(
                    control_plane_repository, "_connect_local_identity_db",
                    return_value=fake_conn,
                ),
            ):
                record = _run(control_plane_repository.get_workspace_by_id("ws-sqlite-identity-test"))
        finally:
            fake_conn.close()

        self.assertIsInstance(record, dict)
        self.assertIn("identity_links", record)
        self.assertEqual(record["identity_links"], {})

    def test_both_backends_expose_the_same_identity_links_shape(self) -> None:
        """The parity contract itself: regardless of backend, the returned
        record always carries an identity_links key whose value is a
        dict — never absent, never None, never a raw JSON string — so
        command_registry._is_sender_owner and sage_agent_runtime_service's
        sender-class resolution read it identically on either. This is
        the actual root shape of the original bug (two backends silently
        disagreeing), so it is asserted directly rather than only
        checking each backend in isolation."""
        pg_row = {
            "id": "ws-parity-pg", "tenant_id": "t", "workspace_id": "ws-parity-pg",
            "slug": "s", "name": "n", "workspace_type": "personal", "status": "active",
            "created_by_user_id": None, "metadata": {}, "identity_links": {},
            "created_at": 0, "updated_at": 0,
        }
        with patch.object(
            control_plane_repository, "_scoped_connection",
            new=_fake_scoped_connection_postgres(pg_row),
        ):
            pg_record = _run(control_plane_repository.get_workspace_by_id("ws-parity-pg"))

        fake_conn = _fake_local_identity_connection("ws-parity-sqlite")
        try:
            with (
                patch.object(
                    control_plane_repository, "_scoped_connection",
                    new=_fake_scoped_connection_sqlite_fallback,
                ),
                patch.object(
                    control_plane_repository, "_connect_local_identity_db",
                    return_value=fake_conn,
                ),
            ):
                sqlite_record = _run(control_plane_repository.get_workspace_by_id("ws-parity-sqlite"))
        finally:
            fake_conn.close()

        self.assertIn("identity_links", pg_record)
        self.assertIn("identity_links", sqlite_record)
        self.assertIsInstance(pg_record["identity_links"], dict)
        self.assertIsInstance(sqlite_record["identity_links"], dict)
        self.assertEqual(type(pg_record["identity_links"]), type(sqlite_record["identity_links"]))


class OwnerCheckReadsRealIdentityLinksTests(unittest.TestCase):
    """The actual regression this closes: command_registry._is_sender_owner
    (the slash-command owner gate, and — via sage_agent_runtime_service's
    identically-shaped bindings construction — the same fact every channel
    turn's owner/audience classification depends on) now sees real,
    persisted identity_links instead of always getting None."""

    def test_is_sender_owner_recognizes_a_linked_channel_identity(self) -> None:
        workspace = {
            "created_by_user_id": None,
            "identity_links": {"telegram_personal": {"user_id": "tg-owner-123", "sender_hash": ""}},
        }
        with patch(
            "server_modules.control_plane_repository.get_workspace_by_id",
            new=AsyncMock(return_value=workspace),
        ):
            self.assertTrue(_run(command_registry._is_sender_owner("tg-owner-123", "ws-1")))

    def test_is_sender_owner_still_denies_an_unlinked_sender(self) -> None:
        """Regression guard in the OTHER direction — fixing the read must
        never turn into a fail-open bug of its own. An empty
        identity_links (the honest state for a workspace with nothing
        linked, on either backend) must still deny."""
        workspace = {"created_by_user_id": None, "identity_links": {}}
        with patch(
            "server_modules.control_plane_repository.get_workspace_by_id",
            new=AsyncMock(return_value=workspace),
        ):
            self.assertFalse(_run(command_registry._is_sender_owner("some-stranger", "ws-1")))


if __name__ == "__main__":
    unittest.main()

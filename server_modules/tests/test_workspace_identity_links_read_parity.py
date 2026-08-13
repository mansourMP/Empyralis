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


class _StatefulWorkspaceIdentityLinksStore:
    """A REAL read-after-write round trip, not two independently-scripted
    mocks — this is what proves the clobber is actually fixed. Stands in
    for control_plane_repository.get_workspace_by_id /
    update_workspace_identity_links: the second call's read reflects the
    first call's write, exactly like a real database would (and exactly
    what get_workspace_by_id's own missing SELECT column defeated —
    before this fix, EVERY read from this store's real equivalent came
    back empty regardless of what had actually been written)."""

    def __init__(self) -> None:
        self.identity_links: dict = {}

    async def get_workspace_by_id(self, workspace_id):
        return {"identity_links": dict(self.identity_links)}

    async def update_workspace_identity_links(self, *, workspace_id, identity_links):
        self.identity_links = dict(identity_links)
        return True


class SecondLinkDoesNotClobberTheFirstTests(unittest.IsolatedAsyncioTestCase):
    """The bug found while fixing the read: routes_workspaces.
    upsert_workspace_identity_link does an explicit read-modify-write
    (current_links = dict(raw_links) if isinstance(raw_links, dict) else {};
    current_links[name] = channel_ids; update_workspace_identity_links(...))
    and update_workspace_identity_links's own Postgres SQL is a FULL
    column replacement (`UPDATE workspaces SET identity_links = $2::jsonb`,
    never a jsonb merge/jsonb_set) — so the write's correctness rests
    entirely on the read seeing the real, current set first. Before the
    SELECT fix, that read always came back empty, so every SECOND link
    added silently discarded the first.

    This drives the REAL route handler function (routes_workspaces.
    upsert_workspace_identity_link), not a hand-rolled reimplementation of
    its read-modify-write logic — a test that reimplements the logic under
    test proves nothing about a regression in the logic itself.
    """

    async def test_linking_a_second_channel_identity_preserves_the_first(self) -> None:
        from server_modules import routes_workspaces

        store = _StatefulWorkspaceIdentityLinksStore()

        with (
            patch.object(
                routes_workspaces.control_plane_repository, "get_workspace_by_id",
                new=store.get_workspace_by_id,
            ),
            patch.object(
                routes_workspaces.control_plane_repository, "update_workspace_identity_links",
                new=store.update_workspace_identity_links,
            ),
            patch.object(
                routes_workspaces.auth_module, "enforce_workspace_access",
                return_value="ws-clobber-test",
            ),
        ):
            first = await routes_workspaces.upsert_workspace_identity_link(
                workspace_id="ws-clobber-test",
                body=routes_workspaces.IdentityLinkEntry(name="telegram_personal", channel_ids=["tg-owner-123"]),
                current_user={"id": "user-owner-1"},
            )
            self.assertEqual(first.identity_links, {"telegram_personal": ["tg-owner-123"]})

            second = await routes_workspaces.upsert_workspace_identity_link(
                workspace_id="ws-clobber-test",
                body=routes_workspaces.IdentityLinkEntry(name="discord_personal", channel_ids=["dc-owner-456"]),
                current_user={"id": "user-owner-1"},
            )

        # THE assertion: both links present after the second write — the
        # first must survive, never be silently discarded by the second.
        self.assertEqual(
            second.identity_links,
            {
                "telegram_personal": ["tg-owner-123"],
                "discord_personal": ["dc-owner-456"],
            },
        )
        # And the persisted store itself — not just the response payload —
        # actually holds both, proving the write itself (not just what the
        # endpoint echoed back) preserved the first entry.
        self.assertEqual(
            store.identity_links,
            {
                "telegram_personal": ["tg-owner-123"],
                "discord_personal": ["dc-owner-456"],
            },
        )

    async def test_updating_an_existing_names_links_replaces_only_that_entry(self) -> None:
        """A same-name upsert (re-linking the same canonical name to
        different channel ids) must replace only ITS OWN entry, never the
        others — the read-modify-write's job either way."""
        from server_modules import routes_workspaces

        store = _StatefulWorkspaceIdentityLinksStore()
        store.identity_links = {
            "telegram_personal": ["tg-old-id"],
            "discord_personal": ["dc-owner-456"],
        }

        with (
            patch.object(
                routes_workspaces.control_plane_repository, "get_workspace_by_id",
                new=store.get_workspace_by_id,
            ),
            patch.object(
                routes_workspaces.control_plane_repository, "update_workspace_identity_links",
                new=store.update_workspace_identity_links,
            ),
            patch.object(
                routes_workspaces.auth_module, "enforce_workspace_access",
                return_value="ws-clobber-test-2",
            ),
        ):
            result = await routes_workspaces.upsert_workspace_identity_link(
                workspace_id="ws-clobber-test-2",
                body=routes_workspaces.IdentityLinkEntry(name="telegram_personal", channel_ids=["tg-new-id"]),
                current_user={"id": "user-owner-1"},
            )

        self.assertEqual(
            result.identity_links,
            {
                "telegram_personal": ["tg-new-id"],
                "discord_personal": ["dc-owner-456"],
            },
        )


class _StatefulFakePostgresIdentityLinksConnection:
    """ONE fake connection instance simulating a single workspace row's
    real identity_links column across multiple SELECT/UPDATE calls — the
    SAME instance is yielded by every _scoped_connection() call in this
    test, so state genuinely persists between the read inside
    get_workspace_by_id and the write inside
    update_workspace_identity_links, exactly like one real Postgres row
    would. This is what proves the FULL real chain (the fixed SELECT +
    the real UPDATE's full-column-replacement semantics) no longer
    clobbers — not just that upsert_workspace_identity_link's own
    read-modify-write logic is correct in isolation against a hand-rolled
    double."""

    def __init__(self, workspace_id: str) -> None:
        self.workspace_id = workspace_id
        self.identity_links: dict = {}

    async def fetchrow(self, query, *args):
        if "FROM workspaces" in query:
            full_row = {
                "id": self.workspace_id, "tenant_id": "t", "workspace_id": self.workspace_id,
                "slug": "s", "name": "n", "workspace_type": "personal", "status": "active",
                "created_by_user_id": None, "metadata": {},
                "identity_links": dict(self.identity_links),
                "created_at": 0, "updated_at": 0,
            }
            # A real Postgres connection returns EXACTLY the columns named
            # in the SELECT's own column list — never a column the query
            # text didn't ask for. Filtering here the same way is what
            # makes this test a genuine regression guard for the missing-
            # column bug: reverting get_workspace_by_id's SELECT to omit
            # "identity_links" must make THIS fake stop returning it too,
            # exactly like the real bug did, rather than the fake papering
            # over the query's own bug by always including it regardless.
            select_clause = query.split("FROM workspaces", 1)[0]
            return {key: value for key, value in full_row.items() if key in select_clause}
        return None

    async def execute(self, query, *args):
        if "UPDATE workspaces" in query and "identity_links" in query:
            # update_workspace_identity_links's own positional args:
            # (clean_workspace_id, _to_json(identity_links, default={})).
            raw = args[1] if len(args) > 1 else "{}"
            import json as _json
            self.identity_links = _json.loads(raw) if isinstance(raw, str) else dict(raw)
        return "UPDATE 1"


class RealSelectAndUpdateChainDoesNotClobberTests(unittest.IsolatedAsyncioTestCase):
    """The same proof as SecondLinkDoesNotClobberTheFirstTests above, but
    driven through the REAL get_workspace_by_id (the function this whole
    fix touched — its SELECT now names identity_links) and the REAL
    update_workspace_identity_links, not a hand-rolled store standing in
    for both. Only _scoped_connection is faked (the actual asyncpg
    network boundary), and it yields ONE stateful connection so the
    second call's SELECT genuinely reflects the first call's UPDATE."""

    async def test_real_get_and_update_chain_preserves_the_first_link_when_a_second_is_added(self) -> None:
        from server_modules import control_plane_repository, routes_workspaces

        control_plane_repository._workspace_lookup_cache_drop("ws-real-chain-test")
        fake_connection = _StatefulFakePostgresIdentityLinksConnection("ws-real-chain-test")

        @asynccontextmanager
        async def _scoped(*, tenant_id=None, workspace_id=None, bypass_rls=False):
            yield fake_connection

        with (
            patch.object(control_plane_repository, "_scoped_connection", new=_scoped),
            patch.object(routes_workspaces.auth_module, "enforce_workspace_access", return_value="ws-real-chain-test"),
        ):
            await routes_workspaces.upsert_workspace_identity_link(
                workspace_id="ws-real-chain-test",
                body=routes_workspaces.IdentityLinkEntry(name="telegram_personal", channel_ids=["tg-owner-123"]),
                current_user={"id": "user-owner-1"},
            )
            second = await routes_workspaces.upsert_workspace_identity_link(
                workspace_id="ws-real-chain-test",
                body=routes_workspaces.IdentityLinkEntry(name="discord_personal", channel_ids=["dc-owner-456"]),
                current_user={"id": "user-owner-1"},
            )

        self.assertEqual(
            second.identity_links,
            {
                "telegram_personal": ["tg-owner-123"],
                "discord_personal": ["dc-owner-456"],
            },
        )
        # And a completely FRESH read (a third call, GET this time, going
        # through get_workspace_by_id -> the real per-request cache too)
        # confirms the persisted row itself — not just the second POST's
        # own echoed response — really holds both.
        control_plane_repository._workspace_lookup_cache_drop("ws-real-chain-test")
        with (
            patch.object(control_plane_repository, "_scoped_connection", new=_scoped),
            patch.object(routes_workspaces.auth_module, "enforce_workspace_access", return_value="ws-real-chain-test"),
        ):
            fresh_read = await routes_workspaces.get_workspace_identity_links(
                workspace_id="ws-real-chain-test",
                current_user={"id": "user-owner-1"},
            )
        self.assertEqual(
            fresh_read.identity_links,
            {
                "telegram_personal": ["tg-owner-123"],
                "discord_personal": ["dc-owner-456"],
            },
        )


if __name__ == "__main__":
    unittest.main()

"""Tests for mcp_external_agent_roster_service.py -- Step 2 of "Mentions +
identity for platform AND external agents".

Covers:
(a) registering an external agent mints a row with an auto-assigned name
(b) registration is idempotent under ON CONFLICT (a race between mint-time
    and lazy-backfill-at-resolve-time must never create two identities)
(c) lookup by key_hash round-trips
(d) revoke mirrors into the roster row
(e) list_unified_roster merges platform (workspace_agent_installs) and
    external (this table) rows through ONE interface, each tagged `kind`
(f) no-Postgres degrades to an explicit ok=False, never raises
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from server_modules import mcp_external_agent_roster_service as roster


class _FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeConnection:
    """acquire() target the rls_* helpers open (mcp_external_agent_roster is
    now FORCE RLS -- MAN-109). Delegates fetchrow/fetch/execute straight back
    to the owning _RosterFakePool's own query-parsing methods, so the
    business logic that lives there (ON CONFLICT idempotency, revoke
    mutating rows_by_hash, execute_calls bookkeeping) keeps running exactly
    as before. The one exception is _apply_connection_scope's
    set_config(...) SET call issued at the top of every rls_* helper --
    swallowed here rather than routed into the pool's query parser, which
    has no case for it and would otherwise mis-record it as a real
    business execute() call."""

    def __init__(self, pool: "_RosterFakePool") -> None:
        self._pool = pool

    async def fetchrow(self, query, *args):
        return await self._pool.fetchrow(query, *args)

    async def fetch(self, query, *args):
        return await self._pool.fetch(query, *args)

    async def execute(self, query, *args):
        if "set_config(" in query:
            return "SELECT 1"
        return await self._pool.execute(query, *args)

    def transaction(self):
        return _FakeTransaction()


class _FakeAcquire:
    def __init__(self, connection: _FakeConnection) -> None:
        self._connection = connection

    async def __aenter__(self):
        return self._connection

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _RosterFakePool:
    """Minimal fake pool that actually enforces the UNIQUE(key_hash)
    constraint's ON CONFLICT DO NOTHING semantics, since that's exactly the
    idempotency behavior these tests need to prove."""

    def __init__(self):
        self.rows_by_hash: dict = {}
        self.insert_attempts = 0
        self.execute_calls: list = []

    def acquire(self):
        return _FakeAcquire(_FakeConnection(self))

    async def fetchrow(self, query, *args):
        q = " ".join(query.split())
        if q.startswith("INSERT INTO mcp_external_agent_roster"):
            self.insert_attempts += 1
            new_id, tenant_id, workspace_id, name, key_hash, mcp_key_id = args
            if key_hash in self.rows_by_hash:
                return None  # ON CONFLICT (key_hash) DO NOTHING -> no RETURNING row
            row = {
                "id": new_id, "tenant_id": tenant_id, "workspace_id": workspace_id,
                "display_name": name, "key_hash": key_hash, "mcp_key_id": mcp_key_id,
                "revoked": False, "created_at": "2026-07-24T00:00:00Z", "updated_at": "2026-07-24T00:00:00Z",
            }
            self.rows_by_hash[key_hash] = row
            return dict(row)
        if q.startswith("SELECT") and "WHERE key_hash = $1" in q:
            row = self.rows_by_hash.get(args[0])
            return dict(row) if row else None
        return None

    async def fetch(self, query, *args):
        tenant_id, workspace_id = args[0], args[1]
        only_active = "revoked = FALSE" in query
        return [
            dict(r) for r in self.rows_by_hash.values()
            if r["tenant_id"] == tenant_id and r["workspace_id"] == workspace_id
            and (not only_active or not r["revoked"])
        ]

    async def execute(self, query, *args):
        self.execute_calls.append((query, args))
        key_hash, revoked = args[0], args[1]
        if key_hash in self.rows_by_hash:
            self.rows_by_hash[key_hash]["revoked"] = revoked
        return "UPDATE 1"


def _patch_pool(pool):
    return patch(
        "server_modules.mcp_external_agent_roster_service.control_plane_repository.ensure_control_plane_schema",
        new=AsyncMock(return_value=pool),
    )


def _patch_no_platform_installs():
    return patch(
        "server_modules.agent_registry_repository.list_workspace_agent_installs",
        new=AsyncMock(return_value=[]),
    )


class RegisterExternalAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_mints_new_identity_with_autoname_from_pool(self):
        pool = _RosterFakePool()
        with _patch_pool(pool), _patch_no_platform_installs():
            result = await roster.register_external_agent(
                tenant_id="tenant-1", workspace_id="ws-1", key_hash="hash-a", mcp_key_id="mcp_key_1",
            )
        self.assertTrue(result["ok"])
        self.assertEqual(result["kind"], "external")
        self.assertTrue(result["display_name"])  # auto-named, non-empty
        from server_modules.agent_name_pool import NAME_POOL
        # Either a pool name or a "Name N" collision-fallback form.
        base = result["display_name"].split(" ")[0]
        self.assertIn(base, NAME_POOL)

    async def test_explicit_display_name_is_respected(self):
        pool = _RosterFakePool()
        with _patch_pool(pool), _patch_no_platform_installs():
            result = await roster.register_external_agent(
                tenant_id="tenant-1", workspace_id="ws-1", key_hash="hash-b",
                mcp_key_id="mcp_key_2", display_name="Codex Session",
            )
        self.assertTrue(result["ok"])
        self.assertEqual(result["display_name"], "Codex Session")

    async def test_registration_is_idempotent_on_conflict(self):
        """Mint-time registration and a later lazy backfill racing on the
        SAME key_hash must resolve to ONE identity, not two."""
        pool = _RosterFakePool()
        with _patch_pool(pool), _patch_no_platform_installs():
            first = await roster.register_external_agent(
                tenant_id="tenant-1", workspace_id="ws-1", key_hash="hash-c", mcp_key_id="mcp_key_3",
            )
            second = await roster.register_external_agent(
                tenant_id="tenant-1", workspace_id="ws-1", key_hash="hash-c", mcp_key_id="mcp_key_3",
            )
        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(pool.insert_attempts, 2)  # attempted twice
        self.assertEqual(len(pool.rows_by_hash), 1)  # but only one row exists

    async def test_requires_tenant_and_workspace(self):
        pool = _RosterFakePool()
        with _patch_pool(pool):
            result = await roster.register_external_agent(tenant_id="", workspace_id="ws-1", key_hash="hash-d")
        self.assertFalse(result["ok"])

    async def test_requires_key_hash(self):
        pool = _RosterFakePool()
        with _patch_pool(pool):
            result = await roster.register_external_agent(tenant_id="tenant-1", workspace_id="ws-1", key_hash="")
        self.assertFalse(result["ok"])

    async def test_no_postgres_returns_ok_false_not_raise(self):
        with patch(
            "server_modules.mcp_external_agent_roster_service.control_plane_repository.ensure_control_plane_schema",
            new=AsyncMock(return_value=None),
        ):
            result = await roster.register_external_agent(tenant_id="tenant-1", workspace_id="ws-1", key_hash="hash-e")
        self.assertFalse(result["ok"])
        self.assertIn("error", result)


class GetAndRevokeTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_by_key_hash_round_trips(self):
        pool = _RosterFakePool()
        with _patch_pool(pool), _patch_no_platform_installs():
            minted = await roster.register_external_agent(
                tenant_id="tenant-1", workspace_id="ws-1", key_hash="hash-f", display_name="Nova",
            )
            fetched = await roster.get_external_agent_by_key_hash(key_hash="hash-f")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["id"], minted["id"])
        self.assertEqual(fetched["display_name"], "Nova")

    async def test_get_by_key_hash_missing_returns_none(self):
        pool = _RosterFakePool()
        with _patch_pool(pool):
            fetched = await roster.get_external_agent_by_key_hash(key_hash="does-not-exist")
        self.assertIsNone(fetched)

    async def test_revoke_mirrors_into_roster_row(self):
        pool = _RosterFakePool()
        with _patch_pool(pool), _patch_no_platform_installs():
            await roster.register_external_agent(tenant_id="tenant-1", workspace_id="ws-1", key_hash="hash-g")
            await roster.set_external_agent_revoked(
                tenant_id="tenant-1", workspace_id="ws-1", key_hash="hash-g", revoked=True,
            )
            fetched = await roster.get_external_agent_by_key_hash(key_hash="hash-g")
        self.assertTrue(fetched["revoked"])

    async def test_revoke_never_raises_when_postgres_unavailable(self):
        with patch(
            "server_modules.mcp_external_agent_roster_service.control_plane_repository.ensure_control_plane_schema",
            new=AsyncMock(return_value=None),
        ):
            # tenant_id/workspace_id are required (RLS scoping, MAN-109) --
            # non-blank values so the call still reaches (and exercises) the
            # `pool is None` postgres-unavailable branch instead of
            # short-circuiting on the earlier blank-scope guard.
            await roster.set_external_agent_revoked(
                tenant_id="tenant-1", workspace_id="ws-1", key_hash="hash-h", revoked=True,
            )  # must not raise


class ListUnifiedRosterTests(unittest.IsolatedAsyncioTestCase):
    async def test_merges_platform_and_external_kinds(self):
        pool = _RosterFakePool()
        platform_install = {"id": "install-1", "label": "Atlas", "enabled": True, "status": "active"}
        with _patch_pool(pool), \
             patch("server_modules.agent_registry_repository.list_workspace_agent_installs",
                   new=AsyncMock(return_value=[platform_install])):
            await roster.register_external_agent(
                tenant_id="tenant-1", workspace_id="ws-1", key_hash="hash-i", display_name="Nimbus",
            )
            unified = await roster.list_unified_roster(tenant_id="tenant-1", workspace_id="ws-1")

        kinds = {(row["id"], row["kind"], row["display_name"]) for row in unified}
        self.assertIn(("install-1", "platform", "Atlas"), kinds)
        external_rows = [row for row in unified if row["kind"] == "external"]
        self.assertEqual(len(external_rows), 1)
        self.assertEqual(external_rows[0]["display_name"], "Nimbus")

    async def test_revoked_external_agents_excluded_from_unified_roster(self):
        pool = _RosterFakePool()
        with _patch_pool(pool), _patch_no_platform_installs():
            await roster.register_external_agent(tenant_id="tenant-1", workspace_id="ws-1", key_hash="hash-j")
            await roster.set_external_agent_revoked(
                tenant_id="tenant-1", workspace_id="ws-1", key_hash="hash-j", revoked=True,
            )
            unified = await roster.list_unified_roster(tenant_id="tenant-1", workspace_id="ws-1")
        self.assertEqual([row for row in unified if row["kind"] == "external"], [])

    async def test_scoped_to_workspace_no_cross_tenant_leak(self):
        pool = _RosterFakePool()
        with _patch_pool(pool), _patch_no_platform_installs():
            await roster.register_external_agent(tenant_id="tenant-A", workspace_id="ws-A", key_hash="hash-k")
            unified_a = await roster.list_unified_roster(tenant_id="tenant-A", workspace_id="ws-A")
            unified_b = await roster.list_unified_roster(tenant_id="tenant-B", workspace_id="ws-B")
        self.assertEqual(len([r for r in unified_a if r["kind"] == "external"]), 1)
        self.assertEqual([r for r in unified_b if r["kind"] == "external"], [])


if __name__ == "__main__":
    unittest.main()

"""MAN-130: provisioned agent computer (VPS) records move from a global JSON
state file to Postgres.

Three layers of coverage here, because the store is environment-dependent:

  * Pure unit tests (no store at all) — the row/record mapping, the status
    vocabulary the quota gate depends on, and the corrupt-state-file guard.
  * Service-level tests against an in-memory stand-in for
    agent_computers_repository — these exercise the real
    vps_provisioning_service wiring (one-time import, durable-vs-legacy
    routing, beacon annotation) without needing a database.
  * Real-Postgres tests marked `blackbox_db`, which run the ACTUAL SQL. They
    SKIP when DATABASE_URL is not set. Anything asserted about SQL semantics
    (workspace isolation in the count query, ON CONFLICT DO NOTHING) is proven
    there; the in-memory stand-in only ever proves the service delegates
    correctly, never that the SQL is right.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Mapping, Optional

import pytest

from server_modules import agent_computers_repository as repo
from server_modules import vps_provisioning_service as vps


# ---------------------------------------------------------------------------
# In-memory stand-in for the repository
# ---------------------------------------------------------------------------


class _FakeAgentComputersRepository:
    """Dict-backed mirror of agent_computers_repository's contract.

    Deliberately mirrors the SQL one statement at a time (upsert = whole-row
    replace, update = partial with `metadata ||` merge, import = ON CONFLICT DO
    NOTHING) so a service-level test can assert the service's behaviour without
    a database. It proves nothing about the SQL itself — that is what the
    blackbox_db tests at the bottom of this file are for.
    """

    ACTIVE_VPS_STATUSES = repo.ACTIVE_VPS_STATUSES
    TERMINAL_VPS_STATUSES = repo.TERMINAL_VPS_STATUSES
    VPS_STATUSES = repo.VPS_STATUSES

    def __init__(self) -> None:
        self.rows: Dict[str, Dict[str, Any]] = {}
        self.calls: List[tuple] = []
        self.import_calls = 0

    async def ensure_ready(self) -> Any:
        return self  # any non-None value means "Postgres is the store"

    async def import_legacy_records(self, records) -> int:
        self.import_calls += 1
        inserted = 0
        for record in records or []:
            vps_id = str(record.get("vps_id") or "").strip()
            if not vps_id or vps_id in self.rows:
                continue  # ON CONFLICT (vps_id) DO NOTHING
            self.rows[vps_id] = dict(record)
            inserted += 1
        return inserted

    async def upsert_vps_record(self, record: Mapping[str, Any]) -> Dict[str, Any]:
        stored = dict(record)
        self.rows[str(stored["vps_id"])] = stored
        self.calls.append(("upsert", stored["vps_id"]))
        return dict(stored)

    async def get_vps_record(self, vps_id: str) -> Optional[Dict[str, Any]]:
        row = self.rows.get(str(vps_id or "").strip())
        return dict(row) if row else None

    async def update_vps_record(
        self,
        vps_id: str,
        *,
        status: Optional[str] = None,
        credentials_ciphertext: Optional[str] = None,
        metadata_updates: Optional[Mapping[str, Any]] = None,
        updated_at: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        row = self.rows.get(str(vps_id or "").strip())
        if row is None:
            return None
        if status:
            row["status"] = status
        if credentials_ciphertext:
            row["credentials_ciphertext"] = credentials_ciphertext
        row.update(dict(metadata_updates or {}))  # metadata || $n::jsonb
        row["updated_at"] = updated_at or row.get("updated_at")
        self.calls.append(("update", vps_id))
        return dict(row)

    async def delete_vps_record(self, vps_id: str) -> bool:
        return self.rows.pop(str(vps_id or "").strip(), None) is not None

    async def list_workspace_vps(self, tenant_id: str, workspace_id: str) -> List[Dict[str, Any]]:
        self.calls.append(("list_workspace", tenant_id, workspace_id))
        rows = [
            dict(row)
            for row in self.rows.values()
            if str(row.get("tenant_id") or "") == tenant_id
            and str(row.get("workspace_id") or "") == workspace_id
        ]
        rows.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return rows

    async def count_active_workspace_vps(self, tenant_id: str, workspace_id: str) -> int:
        self.calls.append(("count_active", tenant_id, workspace_id))
        return sum(
            1
            for row in self.rows.values()
            if str(row.get("tenant_id") or "") == tenant_id
            and str(row.get("workspace_id") or "") == workspace_id
            and str(row.get("status") or "") in repo.ACTIVE_VPS_STATUSES
        )

    async def list_records_for_pairing_scan(self) -> List[Dict[str, Any]]:
        return [dict(row) for row in self.rows.values() if str(row.get("status") or "") != "deleted"]


@pytest.fixture
def durable_records(tmp_path, monkeypatch) -> _FakeAgentComputersRepository:
    """Point vps_provisioning_service at the in-memory store, with a fresh
    legacy state file and the one-time-import latch reset."""
    fake = _FakeAgentComputersRepository()
    monkeypatch.setattr(vps, "VPS_STATE_FILE", tmp_path / "vps.json")
    monkeypatch.setattr(vps.vault_store, "_openssl_encrypt", lambda text: f"enc:{text}")
    monkeypatch.setattr(vps.vault_store, "_openssl_decrypt", lambda text: text.removeprefix("enc:"))
    monkeypatch.setattr(vps, "agent_computers_repository", fake)
    monkeypatch.setattr(vps, "_LEGACY_RECORD_IMPORT_DONE", False)
    return fake


@pytest.fixture
def legacy_records_only(tmp_path, monkeypatch):
    """Force the no-Postgres path explicitly.

    Not merely "don't set DATABASE_URL" — the test suite does not isolate
    DATABASE_URL (MAN-139), so a developer with one exported would otherwise
    silently run these against a real database, proving nothing about the
    fallback and writing rows into it.
    """
    monkeypatch.setattr(vps, "VPS_STATE_FILE", tmp_path / "vps.json")
    monkeypatch.setattr(vps.vault_store, "_openssl_encrypt", lambda text: f"enc:{text}")
    monkeypatch.setattr(vps.vault_store, "_openssl_decrypt", lambda text: text.removeprefix("enc:"))
    monkeypatch.setattr(vps, "_LEGACY_RECORD_IMPORT_DONE", False)

    async def _no_pool() -> Any:
        return None

    monkeypatch.setattr(vps.agent_computers_repository, "ensure_ready", _no_pool)


def _write_legacy_state(path, records: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"v": 1, "vps": dict(records)}, indent=2), encoding="utf-8")


def _legacy_record(vps_id: str, *, workspace_id: str = "ws-1", status: str = "connected", **extra) -> Dict[str, Any]:
    record = {
        "vps_id": vps_id,
        "workspace_id": workspace_id,
        "tenant_id": "tenant-1",
        "user_id": "user-1",
        "provider": "digitalocean",
        "provider_resource_id": f"droplet-{vps_id}",
        "public_ip": "203.0.113.10",
        "region": "nyc3",
        "size": "s-1vcpu-2gb",
        "status": status,
        "pairing_id": "pairing-1",
        "pairing_token_ciphertext": f'enc:{{"pairing_token":"tok_{vps_id}"}}',
        "credentials_ciphertext": 'enc:{"access_token":"do_token"}',
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    record.update(extra)
    return record


# ---------------------------------------------------------------------------
# Pure unit tests — no store
# ---------------------------------------------------------------------------


def test_status_vocabulary_matches_the_services_normalizer():
    """ACTIVE/TERMINAL are the split MAN-132's quota gate counts by, and they
    are declared in the repository while _normalize_status owns the vocabulary.
    If someone adds a status to one and not the other, the quota silently
    miscounts — so pin them together."""
    assert set(repo.ACTIVE_VPS_STATUSES) | set(repo.TERMINAL_VPS_STATUSES) == set(repo.VPS_STATUSES)
    assert not set(repo.ACTIVE_VPS_STATUSES) & set(repo.TERMINAL_VPS_STATUSES)
    for status in repo.VPS_STATUSES:
        assert vps._normalize_status(status) == status
    # Anything outside the vocabulary is coerced, so no unknown status can slip
    # into the table and dodge both buckets.
    assert vps._normalize_status("banana") == "provisioning"
    assert set(repo.TERMINAL_VPS_STATUSES) == {"failed", "deleted"}


def test_record_row_round_trip_preserves_every_field_including_install_metadata():
    """The install-beacon/failure annotations have no column of their own; they
    ride in the JSONB metadata column. The record the service reads back must be
    indistinguishable from the one it would have read out of the JSON file."""
    record = _legacy_record(
        "vps_rt",
        status="failed",
        install_phase="gateway_download",
        install_error="could not download the gateway artifact (HTTP 404)",
        install_terminal=True,
        install_reported_at="2026-01-01T00:05:00Z",
        error="The agent computer failed during setup (gateway_download): HTTP 404",
        cleanup_error="droplet delete returned 500",
    )

    row = repo.record_to_row(record)
    assert set(row["metadata"]) == {
        "install_phase",
        "install_error",
        "install_terminal",
        "install_reported_at",
        "error",
        "cleanup_error",
    }
    # Ciphertext is carried through verbatim — nothing here re-encrypts.
    assert row["pairing_token_ciphertext"] == record["pairing_token_ciphertext"]
    assert row["credentials_ciphertext"] == record["credentials_ciphertext"]

    # Simulate what asyncpg hands back (metadata as a decoded dict, timestamps
    # as aware datetimes).
    restored = repo.row_to_record({**row, "metadata": row["metadata"]})
    for key, value in record.items():
        assert restored[key] == value, key
    assert vps._public_record(restored) == vps._public_record(record)


def test_row_to_record_accepts_metadata_as_a_raw_json_string():
    """Some pools hand JSONB back as a string rather than a dict."""
    row = repo.record_to_row(_legacy_record("vps_str", install_phase="apt"))
    restored = repo.row_to_record({**row, "metadata": json.dumps(row["metadata"])})
    assert restored["install_phase"] == "apt"


def test_corrupt_legacy_state_file_raises_instead_of_returning_an_empty_fleet(tmp_path, monkeypatch):
    """The old _load_state swallowed every parse error and returned
    {"v": 1, "vps": {}} — which erased the fleet, and the next _write_state made
    that permanent. It must fail loudly now."""
    state_file = tmp_path / "vps.json"
    monkeypatch.setattr(vps, "VPS_STATE_FILE", state_file)
    state_file.write_text('{"v": 1, "vps": {"vps_1": {"vps_id": "vps_1"', encoding="utf-8")

    with pytest.raises(vps.VPSStateCorruptError):
        vps._load_state()

    # And nothing rewrote the file behind our back.
    assert state_file.read_text(encoding="utf-8").startswith('{"v": 1, "vps"')


# ---------------------------------------------------------------------------
# Service wiring against the in-memory store
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_legacy_records_are_imported_once_and_the_import_is_idempotent(durable_records, monkeypatch):
    _write_legacy_state(vps.VPS_STATE_FILE, {"vps_a": _legacy_record("vps_a"), "vps_b": _legacy_record("vps_b")})

    first = await vps.load_vps_record("vps_a")
    assert first["vps_id"] == "vps_a"
    assert set(durable_records.rows) == {"vps_a", "vps_b"}
    assert durable_records.import_calls == 1

    # Every later call reuses the latch — the file is not re-read or re-imported.
    await vps.load_vps_record("vps_b")
    assert durable_records.import_calls == 1

    # Even with the latch cleared (a fresh worker process, say), re-running the
    # import must not duplicate or CLOBBER a record that has moved on since.
    await vps.record_vps_install_event(
        pairing_token="tok_vps_a", phase="apt", message="apt-get failed", terminal=True
    )
    assert durable_records.rows["vps_a"]["install_phase"] == "apt"

    monkeypatch.setattr(vps, "_LEGACY_RECORD_IMPORT_DONE", False)
    await vps.load_vps_record("vps_a")
    assert durable_records.import_calls == 2
    assert set(durable_records.rows) == {"vps_a", "vps_b"}  # no duplicates
    # The stale JSON copy did NOT overwrite the newer Postgres row.
    assert durable_records.rows["vps_a"]["install_phase"] == "apt"


@pytest.mark.asyncio
async def test_a_record_survives_a_corrupted_legacy_state_file(durable_records):
    """Before MAN-130 this exact sequence zeroed the fleet: one unreadable byte
    in the JSON file and _load_state returned an empty dict, which the next
    write persisted. With the records in Postgres, a corrupt legacy file is a
    non-event."""
    await vps.record_vps_provision(
        vps_id="vps_survivor",
        workspace_id="ws-1",
        tenant_id="tenant-1",
        user_id="user-1",
        provider="digitalocean",
        provider_resource_id="droplet-1",
        public_ip="203.0.113.11",
        region="nyc3",
        size="s-1vcpu-2gb",
        status="connected",
        pairing_token="pair_survivor",
        credentials={"access_token": "do_token"},
    )

    # Truncate the legacy file mid-object, the way a crashed write would.
    vps.VPS_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    vps.VPS_STATE_FILE.write_text('{"v": 1, "vps": {"vps_surv', encoding="utf-8")

    record = await vps.load_vps_record("vps_survivor")
    assert record["status"] == "connected"
    assert record["provider_resource_id"] == "droplet-1"
    assert await vps.count_active_workspace_vps(workspace_id="ws-1", tenant_id="tenant-1") == 1

    # A later write still lands, and still does not resurrect an empty fleet.
    await vps.mark_vps_provision_failed("vps_survivor", reason="boot never completed", attempt_cleanup=False)
    assert (await vps.load_vps_record("vps_survivor"))["status"] == "failed"
    assert await vps.count_active_workspace_vps(workspace_id="ws-1", tenant_id="tenant-1") == 0


@pytest.mark.asyncio
async def test_count_active_workspace_vps_is_scoped_to_one_workspace(durable_records):
    _write_legacy_state(
        vps.VPS_STATE_FILE,
        {
            "a1": _legacy_record("a1", workspace_id="ws-a", status="connected"),
            "a2": _legacy_record("a2", workspace_id="ws-a", status="provisioning"),
            "a3": _legacy_record("a3", workspace_id="ws-a", status="registering"),
            "a4": _legacy_record("a4", workspace_id="ws-a", status="failed"),
            "a5": _legacy_record("a5", workspace_id="ws-a", status="deleted"),
            "b1": _legacy_record("b1", workspace_id="ws-b", status="connected"),
            "b2": _legacy_record("b2", workspace_id="ws-b", status="connected"),
        },
    )

    assert await vps.count_active_workspace_vps(workspace_id="ws-a", tenant_id="tenant-1") == 3
    assert await vps.count_active_workspace_vps(workspace_id="ws-b", tenant_id="tenant-1") == 2
    # Workspace A cannot see B's machines and vice versa.
    assert {r["vps_id"] for r in await vps.list_workspace_vps(workspace_id="ws-a", tenant_id="tenant-1")} == {
        "a1", "a2", "a3", "a4", "a5",
    }
    assert {r["vps_id"] for r in await vps.list_workspace_vps(workspace_id="ws-b", tenant_id="tenant-1")} == {"b1", "b2"}
    # A different tenant with the same workspace id sees nothing.
    assert await vps.count_active_workspace_vps(workspace_id="ws-a", tenant_id="tenant-2") == 0
    # ...and the service delegated with the scope it was given, rather than
    # filtering a global fetch in Python.
    assert ("count_active", "tenant-1", "ws-a") in durable_records.calls
    assert ("count_active", "tenant-2", "ws-a") in durable_records.calls


@pytest.mark.asyncio
async def test_count_active_workspace_vps_is_scoped_on_the_legacy_fallback_too(legacy_records_only):
    """Same assertions with NO Postgres at all — this exercises the real
    filtering code in vps_provisioning_service rather than a stand-in, and pins
    the two backends to the same answer."""
    _write_legacy_state(
        vps.VPS_STATE_FILE,
        {
            "a1": _legacy_record("a1", workspace_id="ws-a", status="connected"),
            "a2": _legacy_record("a2", workspace_id="ws-a", status="provisioning"),
            "a3": _legacy_record("a3", workspace_id="ws-a", status="registering"),
            "a4": _legacy_record("a4", workspace_id="ws-a", status="failed"),
            "a5": _legacy_record("a5", workspace_id="ws-a", status="deleted"),
            "b1": _legacy_record("b1", workspace_id="ws-b", status="connected"),
            "b2": _legacy_record("b2", workspace_id="ws-b", status="connected"),
        },
    )

    assert await vps.count_active_workspace_vps(workspace_id="ws-a", tenant_id="tenant-1") == 3
    assert await vps.count_active_workspace_vps(workspace_id="ws-b", tenant_id="tenant-1") == 2
    assert await vps.count_active_workspace_vps(workspace_id="ws-a", tenant_id="tenant-2") == 0
    assert {r["vps_id"] for r in await vps.list_workspace_vps(workspace_id="ws-b", tenant_id="tenant-1")} == {"b1", "b2"}


@pytest.mark.asyncio
async def test_durable_backend_is_mandatory_when_durable_runtime_is_required(legacy_records_only, monkeypatch):
    """Production must never silently fall back to the per-process JSON file —
    that is the multi-worker lost-write defect this ticket exists to fix."""
    monkeypatch.setenv("ORION_REQUIRE_DURABLE_RUN_STATE", "1")

    with pytest.raises(vps.runtime_db.DurableRuntimeConfigurationError):
        await vps.load_vps_record("vps_anything")

    # ...and local dev, which does not require durable state, still works.
    monkeypatch.delenv("ORION_REQUIRE_DURABLE_RUN_STATE", raising=False)
    assert await vps.count_active_workspace_vps(workspace_id="ws-a") == 0


# ---------------------------------------------------------------------------
# Real Postgres — the actual SQL. Skipped without DATABASE_URL.
# ---------------------------------------------------------------------------

_DB_URL = str(os.getenv("DATABASE_URL") or "").strip()
requires_postgres = pytest.mark.skipif(
    not _DB_URL, reason="DATABASE_URL is not set; agent_computers SQL cannot be exercised (MAN-139)"
)


_PG_PREFIX = "vps_pgtest_"


async def _reset_postgres_rows() -> str:
    """Clear this file's rows and hand back the id prefix they all share.
    Deliberately a plain coroutine rather than an async fixture, so it does not
    depend on pytest-asyncio's fixture mode."""
    pool = await repo.ensure_ready()
    if pool is None:
        pytest.skip("Postgres is unreachable")
    async with repo._connection() as conn:
        await conn.execute("DELETE FROM agent_computers WHERE vps_id LIKE $1", f"{_PG_PREFIX}%")
    return _PG_PREFIX


@requires_postgres
@pytest.mark.blackbox_db
@pytest.mark.asyncio
async def test_pg_import_is_idempotent_and_never_clobbers_a_newer_row():
    prefix = await _reset_postgres_rows()
    legacy = _legacy_record(f"{prefix}1", workspace_id="ws-a", status="provisioning")

    assert await repo.import_legacy_records([legacy]) == 1
    # The record moves on in Postgres...
    await repo.update_vps_record(legacy["vps_id"], status="connected", updated_at="2026-02-02T00:00:00Z")
    # ...and re-running the import against the same (now stale) JSON copy is a
    # no-op rather than a downgrade.
    assert await repo.import_legacy_records([legacy]) == 0
    stored = await repo.get_vps_record(legacy["vps_id"])
    assert stored["status"] == "connected"
    assert len(await repo.list_workspace_vps("tenant-1", "ws-a")) == 1


@requires_postgres
@pytest.mark.blackbox_db
@pytest.mark.asyncio
async def test_pg_count_active_is_correct_and_workspace_scoped():
    prefix = await _reset_postgres_rows()
    rows = [
        _legacy_record(f"{prefix}a1", workspace_id="ws-a", status="connected"),
        _legacy_record(f"{prefix}a2", workspace_id="ws-a", status="provisioning"),
        _legacy_record(f"{prefix}a3", workspace_id="ws-a", status="registering"),
        _legacy_record(f"{prefix}a4", workspace_id="ws-a", status="failed"),
        _legacy_record(f"{prefix}a5", workspace_id="ws-a", status="deleted"),
        _legacy_record(f"{prefix}b1", workspace_id="ws-b", status="connected"),
    ]
    for row in rows:
        await repo.upsert_vps_record(row)

    assert await repo.count_active_workspace_vps("tenant-1", "ws-a") == 3
    assert await repo.count_active_workspace_vps("tenant-1", "ws-b") == 1
    assert await repo.count_active_workspace_vps("tenant-2", "ws-a") == 0
    listed_b = {r["vps_id"] for r in await repo.list_workspace_vps("tenant-1", "ws-b")}
    assert listed_b == {f"{prefix}b1"}


@requires_postgres
@pytest.mark.blackbox_db
@pytest.mark.asyncio
async def test_pg_update_merges_metadata_rather_than_replacing_it():
    prefix = await _reset_postgres_rows()
    await repo.upsert_vps_record(_legacy_record(f"{prefix}m1", status="provisioning"))

    await repo.update_vps_record(
        f"{prefix}m1",
        metadata_updates={"install_phase": "apt", "install_error": "apt-get failed"},
        updated_at="2026-02-02T00:00:00Z",
    )
    merged = await repo.update_vps_record(
        f"{prefix}m1", status="failed", metadata_updates={"error": "gave up"}, updated_at="2026-02-02T00:01:00Z"
    )

    assert merged["status"] == "failed"
    assert merged["install_phase"] == "apt"  # not erased by the second write
    assert merged["error"] == "gave up"
    assert merged["updated_at"] == "2026-02-02T00:01:00Z"


@requires_postgres
@pytest.mark.blackbox_db
@pytest.mark.asyncio
async def test_pg_round_trip_matches_the_json_records_public_shape():
    prefix = await _reset_postgres_rows()
    record = _legacy_record(
        f"{prefix}shape",
        status="failed",
        install_phase="gateway_download",
        install_error="HTTP 404",
        install_terminal=True,
        install_reported_at="2026-01-01T00:05:00Z",
        error="The agent computer failed during setup (gateway_download): HTTP 404",
    )
    stored = await repo.upsert_vps_record(record)
    assert vps._public_record(stored) == vps._public_record(record)
    assert (await repo.get_vps_record(record["vps_id"]))["credentials_ciphertext"] == record["credentials_ciphertext"]
    assert await repo.delete_vps_record(record["vps_id"]) is True
    assert await repo.get_vps_record(record["vps_id"]) is None

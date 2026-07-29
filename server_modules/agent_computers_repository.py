"""Durable storage for provisioned agent computers / VPS records (MAN-130).

Before this, every provisioned VPS lived in one global JSON blob
(``vps_provisioning_service.VPS_STATE_FILE``) guarded by an in-process
``threading.Lock``. Three concrete defects fell out of that:

  1. The lock is per-process, so two uvicorn workers writing the same file
     silently lost each other's records.
  2. ``_load_state`` swallowed every parse error and returned an empty
     ``{"v": 1, "vps": {}}`` — a truncated file erased the whole fleet, and the
     next write persisted that emptiness permanently.
  3. There was no list-or-count-by-workspace query at all, which is exactly
     what the per-workspace quota gate (MAN-132) needs.

This module is the Postgres home for those records. It follows
``usage_events_repository``'s shape: a ``*_SCHEMA_SQL`` blob plus the
``_ensure_schema`` / ``_SCHEMA_READY`` idiom, so a fresh dev database
self-provisions the table without waiting for anyone to run
``migrations/add_agent_computers.sql`` by hand (that migration is still the
authority for a real deployment, and is the only place RLS is unconditionally
applied — see the DO block below for why the self-provision path has to check
first).

Access is through ``control_plane_repository._scoped_connection`` on the same
asyncpg pool the rest of the control plane uses — no new driver, no second
connection strategy. Every query passes ``bypass_rls=True`` and carries its own
explicit ``tenant_id``/``workspace_id`` predicate, matching
``bug_report_service`` / ``project_tasks_service``: several of these lookups
(get-by-vps_id, the pairing-token scan) are reached before a workspace is even
known, so the row filter cannot be the session GUC.

Ciphertext (``pairing_token_ciphertext`` / ``credentials_ciphertext``) is
stored exactly as ``vps_provisioning_service`` already produced it. Nothing here
encrypts, decrypts, or re-keys anything.
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional

from server_modules import control_plane_repository as _cpr

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Status vocabulary
# ---------------------------------------------------------------------------
# Mirrors vps_provisioning_service._normalize_status exactly — that function is
# the single writer of this column, and it coerces anything unrecognised to
# 'provisioning'. Kept here (rather than imported) because the service imports
# this module, not the other way round; test_agent_computers_repository.py
# asserts the two vocabularies have not drifted apart.
VPS_STATUSES: tuple[str, ...] = ("provisioning", "registering", "connected", "failed", "deleted")

# A record in one of these is over: the box is gone (or was never created), it
# bills nothing, and it must not consume a workspace's quota slot.
TERMINAL_VPS_STATUSES: tuple[str, ...] = ("failed", "deleted")

# Everything else is a machine that exists (or is being created) on the user's
# provider account right now. This is the set count_active_workspace_vps counts.
ACTIVE_VPS_STATUSES: tuple[str, ...] = tuple(s for s in VPS_STATUSES if s not in TERMINAL_VPS_STATUSES)


AGENT_COMPUTERS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS agent_computers (
    vps_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    provider_resource_id TEXT NOT NULL DEFAULT '',
    public_ip TEXT NULL,
    region TEXT NOT NULL DEFAULT '',
    size TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'provisioning',
    pairing_id TEXT NULL,
    pairing_token_ciphertext TEXT NULL,
    credentials_ciphertext TEXT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_agent_computers_scope_status
    ON agent_computers(tenant_id, workspace_id, status);
CREATE INDEX IF NOT EXISTS idx_agent_computers_scope_created
    ON agent_computers(tenant_id, workspace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_computers_status
    ON agent_computers(status);
"""

# The migration applies RLS unconditionally; this self-provision path cannot,
# because a bare local dev database may never have run migrations/enable_rls.sql
# and CREATE POLICY against a missing empyralis_rls_scope_match() would fail
# every write on this table. Apply it when the function is there, skip quietly
# when it is not — the deployed database always has it.
AGENT_COMPUTERS_RLS_SQL = """
DO $$
BEGIN
    IF to_regprocedure('public.empyralis_rls_scope_match(text,text)') IS NOT NULL THEN
        EXECUTE 'ALTER TABLE agent_computers ENABLE ROW LEVEL SECURITY';
        EXECUTE 'ALTER TABLE agent_computers FORCE ROW LEVEL SECURITY';
        EXECUTE 'DROP POLICY IF EXISTS empyralis_agent_computers_scope ON agent_computers';
        EXECUTE 'CREATE POLICY empyralis_agent_computers_scope ON agent_computers '
                'FOR ALL '
                'USING (public.empyralis_rls_scope_match(tenant_id, workspace_id)) '
                'WITH CHECK (public.empyralis_rls_scope_match(tenant_id, workspace_id))';
    END IF;
END
$$;
"""

_SCHEMA_READY = False
_RLS_READY = False


async def _ensure_schema(conn: Any) -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    await conn.execute(AGENT_COMPUTERS_SCHEMA_SQL)
    _SCHEMA_READY = True


async def _ensure_rls() -> None:
    """Applied on its OWN connection/transaction on purpose: a failed statement
    aborts the whole enclosing transaction in Postgres, so folding this into
    _ensure_schema would mean a grants problem here takes the table creation —
    and then the caller's actual query — down with it."""
    global _RLS_READY
    if _RLS_READY:
        return
    try:
        async with _cpr._scoped_connection(bypass_rls=True) as conn:
            if conn is None:
                return
            await conn.execute(AGENT_COMPUTERS_RLS_SQL)
    except Exception:  # pragma: no cover - depends on the deployment's grants
        logger.warning(
            "agent_computers: could not apply row-level security in the self-provision path; "
            "migrations/add_agent_computers.sql remains the authority for a deployed database",
            exc_info=True,
        )
    _RLS_READY = True


async def ensure_ready() -> Any:
    """Return the control-plane pool with the agent_computers table present, or
    None when Postgres is not configured/reachable. Callers decide what a None
    means for them — see vps_provisioning_service._durable_records_backend,
    which turns it into "use the legacy JSON file" locally and into a hard
    DurableRuntimeConfigurationError wherever durable state is required."""
    pool = await _cpr.ensure_control_plane_schema()
    if pool is None:
        return None
    async with _cpr._scoped_connection(bypass_rls=True) as conn:
        if conn is None:
            return None
        await _ensure_schema(conn)
    await _ensure_rls()
    return pool


@asynccontextmanager
async def _connection():
    """A scoped connection with the schema guaranteed. Raises rather than
    yielding None: every caller here has already established (via ensure_ready)
    that Postgres is the store, so a pool that vanished mid-flight is a real
    error and must never be mistaken for "no such record"."""
    async with _cpr._scoped_connection(bypass_rls=True) as conn:
        if conn is None:
            raise _cpr.runtime_db.DurableRuntimeConfigurationError(
                "Postgres is required to read or write agent computer (VPS) records."
            )
        await _ensure_schema(conn)
        yield conn


# ---------------------------------------------------------------------------
# Row <-> record mapping
# ---------------------------------------------------------------------------
# The service's in-memory record is a flat dict. These are the keys that have
# their own column; every other key the service ever puts on a record (the
# install-beacon annotations record_vps_install_event writes, the error /
# cleanup_error strings mark_vps_provision_failed writes) round-trips through
# the JSONB `metadata` column, so the record dict handed back to the service is
# byte-for-byte what it would have read out of the JSON file.
_COLUMN_KEYS = (
    "vps_id",
    "workspace_id",
    "tenant_id",
    "user_id",
    "provider",
    "provider_resource_id",
    "public_ip",
    "region",
    "size",
    "status",
    "pairing_id",
    "pairing_token_ciphertext",
    "credentials_ciphertext",
    "created_at",
    "updated_at",
)


def _iso_z(value: Any) -> str:
    """Render a timestamp the way vps_provisioning_service._utc_now_iso does,
    so created_at/updated_at read identically whichever store they came from."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, datetime):
        moment = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return str(value).strip()


def _parse_ts(value: Any) -> datetime:
    """Parse the service's ISO-8601 'Z' strings into the aware datetime asyncpg
    needs for a TIMESTAMPTZ parameter. Falls back to now() for anything
    unreadable rather than dropping the row."""
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    token = str(value or "").strip()
    if token:
        try:
            parsed = datetime.fromisoformat(token.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            logger.warning("agent_computers: unparseable timestamp %r; substituting now()", token)
    return datetime.now(timezone.utc)


def _coerce_metadata(value: Any) -> Dict[str, Any]:
    """JSONB arrives already-decoded (dict) from some pools and as a raw string
    from others — same footgun bug_report_service._coerce_metadata documents."""
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def record_to_row(record: Mapping[str, Any]) -> Dict[str, Any]:
    """Split a service record dict into its columns plus a metadata blob."""
    metadata = {key: value for key, value in dict(record).items() if key not in _COLUMN_KEYS}
    return {
        "vps_id": str(record.get("vps_id") or "").strip(),
        "workspace_id": str(record.get("workspace_id") or "").strip() or "default",
        "tenant_id": str(record.get("tenant_id") or "").strip() or "default",
        "user_id": str(record.get("user_id") or "").strip() or "unknown-user",
        "provider": str(record.get("provider") or "").strip(),
        "provider_resource_id": str(record.get("provider_resource_id") or "").strip(),
        "public_ip": (str(record.get("public_ip")).strip() or None) if record.get("public_ip") else None,
        "region": str(record.get("region") or "").strip(),
        "size": str(record.get("size") or "").strip(),
        "status": str(record.get("status") or "provisioning").strip() or "provisioning",
        "pairing_id": (str(record.get("pairing_id")).strip() or None) if record.get("pairing_id") else None,
        "pairing_token_ciphertext": str(record.get("pairing_token_ciphertext") or "") or None,
        "credentials_ciphertext": str(record.get("credentials_ciphertext") or "") or None,
        "metadata": metadata,
        "created_at": _parse_ts(record.get("created_at")),
        "updated_at": _parse_ts(record.get("updated_at")),
    }


def row_to_record(row: Any) -> Dict[str, Any]:
    """Rebuild the flat service record dict from a database row."""
    if row is None:
        return {}
    data = dict(row)
    record: Dict[str, Any] = {
        "vps_id": str(data.get("vps_id") or "").strip(),
        "workspace_id": str(data.get("workspace_id") or "").strip(),
        "tenant_id": str(data.get("tenant_id") or "").strip(),
        "user_id": str(data.get("user_id") or "").strip(),
        "provider": str(data.get("provider") or "").strip(),
        "provider_resource_id": str(data.get("provider_resource_id") or "").strip(),
        "public_ip": str(data.get("public_ip") or "").strip() or None,
        "region": str(data.get("region") or "").strip(),
        "size": str(data.get("size") or "").strip(),
        "status": str(data.get("status") or "provisioning").strip() or "provisioning",
        "pairing_id": str(data.get("pairing_id") or "").strip() or None,
        "pairing_token_ciphertext": str(data.get("pairing_token_ciphertext") or ""),
        "credentials_ciphertext": str(data.get("credentials_ciphertext") or ""),
        "created_at": _iso_z(data.get("created_at")),
        "updated_at": _iso_z(data.get("updated_at")),
    }
    for key, value in _coerce_metadata(data.get("metadata")).items():
        if key not in _COLUMN_KEYS:
            record[key] = value
    return record


_SELECT_COLUMNS = """
    vps_id, workspace_id, tenant_id, user_id, provider, provider_resource_id,
    public_ip, region, size, status, pairing_id, pairing_token_ciphertext,
    credentials_ciphertext, metadata, created_at, updated_at
"""

_UPSERT_SQL = f"""
INSERT INTO agent_computers (
    vps_id, workspace_id, tenant_id, user_id, provider, provider_resource_id,
    public_ip, region, size, status, pairing_id, pairing_token_ciphertext,
    credentials_ciphertext, metadata, created_at, updated_at
) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14::jsonb,$15,$16)
ON CONFLICT (vps_id) DO UPDATE SET
    workspace_id = EXCLUDED.workspace_id,
    tenant_id = EXCLUDED.tenant_id,
    user_id = EXCLUDED.user_id,
    provider = EXCLUDED.provider,
    provider_resource_id = EXCLUDED.provider_resource_id,
    public_ip = EXCLUDED.public_ip,
    region = EXCLUDED.region,
    size = EXCLUDED.size,
    status = EXCLUDED.status,
    pairing_id = EXCLUDED.pairing_id,
    pairing_token_ciphertext = EXCLUDED.pairing_token_ciphertext,
    credentials_ciphertext = EXCLUDED.credentials_ciphertext,
    metadata = EXCLUDED.metadata,
    created_at = EXCLUDED.created_at,
    updated_at = EXCLUDED.updated_at
RETURNING {_SELECT_COLUMNS}
"""


def _upsert_args(row: Mapping[str, Any]) -> list:
    return [
        row["vps_id"],
        row["workspace_id"],
        row["tenant_id"],
        row["user_id"],
        row["provider"],
        row["provider_resource_id"],
        row["public_ip"],
        row["region"],
        row["size"],
        row["status"],
        row["pairing_id"],
        row["pairing_token_ciphertext"],
        row["credentials_ciphertext"],
        json.dumps(row["metadata"] or {}),
        row["created_at"],
        row["updated_at"],
    ]


async def upsert_vps_record(record: Mapping[str, Any]) -> Dict[str, Any]:
    """Write a record, replacing any existing row for the same vps_id whole.

    Whole-row replacement (including created_at) is deliberate: it is exactly
    what the JSON store did — `state["vps"][vps_id] = record` — and
    run_vps_provisioning_lifecycle depends on it to overwrite the placeholder
    written at request time with the real provider result.
    """
    row = record_to_row(record)
    if not row["vps_id"]:
        raise ValueError("vps_id is required.")
    async with _connection() as conn:
        written = await conn.fetchrow(_UPSERT_SQL, *_upsert_args(row))
    return row_to_record(written)


_UPDATE_SQL = f"""
UPDATE agent_computers SET
    status = COALESCE($2, status),
    credentials_ciphertext = COALESCE($3, credentials_ciphertext),
    metadata = metadata || $4::jsonb,
    updated_at = COALESCE($5, NOW())
WHERE vps_id = $1
RETURNING {_SELECT_COLUMNS}
"""


async def update_vps_record(
    vps_id: str,
    *,
    status: Optional[str] = None,
    credentials_ciphertext: Optional[str] = None,
    metadata_updates: Optional[Mapping[str, Any]] = None,
    updated_at: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Atomic partial update of one record; returns the new row, or None when
    no such vps_id exists.

    Single-statement on purpose. Status flips, install beacons and failure
    annotations all used to be read-modify-write over the whole JSON blob, which
    is exactly how two uvicorn workers silently dropped each other's writes.
    `metadata` is MERGED (``||``), never replaced, so an install beacon and a
    later failure reason accumulate on the same record instead of erasing one
    another.
    """
    clean = str(vps_id or "").strip()
    if not clean:
        return None
    async with _connection() as conn:
        row = await conn.fetchrow(
            _UPDATE_SQL,
            clean,
            (str(status).strip() or None) if status else None,
            credentials_ciphertext if credentials_ciphertext else None,
            json.dumps(dict(metadata_updates or {})),
            _parse_ts(updated_at) if updated_at else None,
        )
    return row_to_record(row) if row is not None else None


async def get_vps_record(vps_id: str) -> Optional[Dict[str, Any]]:
    clean = str(vps_id or "").strip()
    if not clean:
        return None
    async with _connection() as conn:
        row = await conn.fetchrow(
            f"SELECT {_SELECT_COLUMNS} FROM agent_computers WHERE vps_id = $1",
            clean,
        )
    return row_to_record(row) if row is not None else None


async def delete_vps_record(vps_id: str) -> bool:
    """Hard-delete a row. Note the ordinary user-facing delete path does NOT
    call this — delete_recorded_vps marks the record status 'deleted' and keeps
    it, exactly as the JSON store did. This exists for genuine row removal
    (tests, and any future retention sweep)."""
    clean = str(vps_id or "").strip()
    if not clean:
        return False
    async with _connection() as conn:
        result = await conn.execute("DELETE FROM agent_computers WHERE vps_id = $1", clean)
    return str(result or "").strip().upper() != "DELETE 0"


async def list_workspace_vps(tenant_id: str, workspace_id: str) -> List[Dict[str, Any]]:
    """Every agent computer ever recorded for one workspace, newest first —
    including terminal ones, so a caller can show history. Filter on
    ACTIVE_VPS_STATUSES (or use count_active_workspace_vps) for a live fleet."""
    tenant = str(tenant_id or "").strip() or "default"
    workspace = str(workspace_id or "").strip()
    if not workspace:
        return []
    async with _connection() as conn:
        rows = await conn.fetch(
            f"""
            SELECT {_SELECT_COLUMNS} FROM agent_computers
            WHERE tenant_id = $1 AND workspace_id = $2
            ORDER BY created_at DESC
            """,
            tenant,
            workspace,
        )
    return [row_to_record(row) for row in rows]


async def count_active_workspace_vps(tenant_id: str, workspace_id: str) -> int:
    """How many machines this workspace currently has standing (MAN-132's quota
    gate reads this). 'Active' is everything that is not terminal — see
    ACTIVE_VPS_STATUSES. Uses idx_agent_computers_scope_status."""
    tenant = str(tenant_id or "").strip() or "default"
    workspace = str(workspace_id or "").strip()
    if not workspace:
        return 0
    async with _connection() as conn:
        count = await conn.fetchval(
            """
            SELECT count(*) FROM agent_computers
            WHERE tenant_id = $1 AND workspace_id = $2 AND status = ANY($3::text[])
            """,
            tenant,
            workspace,
            list(ACTIVE_VPS_STATUSES),
        )
    return int(count or 0)


async def list_records_for_pairing_scan() -> List[Dict[str, Any]]:
    """Every non-deleted record, across all workspaces.

    record_vps_install_event has to find the record whose pairing token matches
    an inbound beacon, and the token is only ever stored encrypted — there is no
    predicate to push into SQL, so the candidate set is decrypted in Python
    exactly as the JSON scan did. Scoped to non-deleted rows for the same reason
    the JSON version was, and no more narrowly, so beacon matching keeps its
    current semantics.
    """
    async with _connection() as conn:
        rows = await conn.fetch(
            f"SELECT {_SELECT_COLUMNS} FROM agent_computers WHERE status <> 'deleted' ORDER BY created_at DESC"
        )
    return [row_to_record(row) for row in rows]


_IMPORT_SQL = f"""
INSERT INTO agent_computers (
    vps_id, workspace_id, tenant_id, user_id, provider, provider_resource_id,
    public_ip, region, size, status, pairing_id, pairing_token_ciphertext,
    credentials_ciphertext, metadata, created_at, updated_at
) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14::jsonb,$15,$16)
ON CONFLICT (vps_id) DO NOTHING
"""


async def import_legacy_records(records: Iterable[Mapping[str, Any]]) -> int:
    """One-time lift of the legacy JSON file's records into Postgres.

    Idempotent by construction: ON CONFLICT DO NOTHING, so a vps_id already in
    the table is neither duplicated nor overwritten with the JSON file's stale
    copy. Returns the number of rows actually inserted. Safe to run
    concurrently from several workers, and safe to run repeatedly.
    """
    rows = []
    for record in records or []:
        if not isinstance(record, Mapping):
            continue
        row = record_to_row(record)
        if not row["vps_id"]:
            continue
        rows.append(row)
    if not rows:
        return 0
    inserted = 0
    async with _connection() as conn:
        for row in rows:
            result = await conn.execute(_IMPORT_SQL, *_upsert_args(row))
            if str(result or "").strip().upper() != "INSERT 0 0":
                inserted += 1
    return inserted

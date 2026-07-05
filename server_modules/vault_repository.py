"""Phase 3C: Vault credential repository — row-level Postgres ops.

The encrypted secret store. Every operation touches a SINGLE row via
INSERT/UPDATE/DELETE — there is no whole-file (or whole-table) load-modify-save,
so concurrent writers can never clobber each other's rows (the failure mode that
lost a credential in Phase 3B).

NO FALLBACK for secrets: if DATABASE_URL is absent or Postgres is unreachable,
these functions raise VaultStorageUnavailable rather than silently landing
secrets in a file or SQLite.

Encryption is unchanged and lives in vault_store (_openssl_encrypt/_decrypt).
This module only moves the ciphertext's storage. `encrypted_secret` holds the
exact same string the JSON file held.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from server_modules import db as runtime_db


class VaultStorageUnavailable(RuntimeError):
    """Raised when the secret vault's Postgres backend is unavailable. Secrets
    are never written to or read from a file/SQLite fallback."""


_COLS = (
    "id, provider, workspace_id, agent_install_id, account_label, "
    "platform_scoped, label, mode, metadata, encrypted_secret, created_at, updated_at"
)


async def _pool() -> Any:
    pool = await runtime_db.get_pool()
    if pool is None:
        raise VaultStorageUnavailable(
            "Postgres is required for the secret vault; DATABASE_URL is not "
            "configured or Postgres is unreachable. Refusing to read or write "
            "secrets without durable storage (no file/SQLite fallback)."
        )
    return pool


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _row_to_entry(row: Any) -> Dict[str, Any]:
    """Reconstruct the JSON vault record shape from a row, so every existing
    reader (resolve_vault_credential, resolve_agent_credential, list_vault_*)
    keeps working byte-for-byte."""
    r = dict(row)
    meta = r.get("metadata")
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:
            meta = {}
    return {
        "id": r.get("id"),
        "provider": r.get("provider"),
        "workspace_id": r.get("workspace_id"),
        "agent_install_id": r.get("agent_install_id"),
        "account_label": r.get("account_label"),
        "platform_scoped": bool(r.get("platform_scoped")),
        "label": r.get("label"),
        "mode": r.get("mode"),
        "metadata": meta if isinstance(meta, dict) else {},
        "encrypted_secret": r.get("encrypted_secret"),
        "created_at": _iso(r.get("created_at")),
        "updated_at": _iso(r.get("updated_at")),
    }


def _derive_platform_scoped(entry: Dict[str, Any]) -> bool:
    if "platform_scoped" in entry and entry.get("platform_scoped") is not None:
        return bool(entry.get("platform_scoped"))
    ws = entry.get("workspace_id")
    return ws is None or str(ws).strip() == ""


def _norm_ws(entry: Dict[str, Any]) -> Optional[str]:
    ws = entry.get("workspace_id")
    if ws is None:
        return None
    ws = str(ws).strip()
    return ws or None


# ── Reads ───────────────────────────────────────────────────────────────────

async def list_all() -> List[Dict[str, Any]]:
    pool = await _pool()
    rows = await pool.fetch(f"SELECT {_COLS} FROM vault_credentials ORDER BY created_at ASC, id ASC")
    return [_row_to_entry(r) for r in rows]


async def get(credential_id: str) -> Optional[Dict[str, Any]]:
    pool = await _pool()
    row = await pool.fetchrow(f"SELECT {_COLS} FROM vault_credentials WHERE id = $1", str(credential_id or "").strip())
    return _row_to_entry(row) if row is not None else None


async def count() -> int:
    pool = await _pool()
    return int(await pool.fetchval("SELECT COUNT(*) FROM vault_credentials") or 0)


# ── Single-row writes ───────────────────────────────────────────────────────

async def upsert(entry: Dict[str, Any]) -> Dict[str, Any]:
    """INSERT one credential row, or UPDATE it in place on id conflict. Only this
    one row is touched — other rows are never read or rewritten."""
    pool = await _pool()
    cid = str(entry.get("id") or "").strip()
    if not cid:
        raise ValueError("credential entry requires an id.")
    enc = entry.get("encrypted_secret")
    if not isinstance(enc, str) or not enc:
        raise ValueError("credential entry requires a non-empty encrypted_secret.")
    row = await pool.fetchrow(
        f"""
        INSERT INTO vault_credentials
            (id, provider, workspace_id, agent_install_id, account_label,
             platform_scoped, label, mode, metadata, encrypted_secret, created_at, updated_at)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10, COALESCE($11::text::timestamptz, NOW()), NOW())
        ON CONFLICT (id) DO UPDATE SET
            provider = EXCLUDED.provider,
            workspace_id = EXCLUDED.workspace_id,
            agent_install_id = EXCLUDED.agent_install_id,
            account_label = EXCLUDED.account_label,
            platform_scoped = EXCLUDED.platform_scoped,
            label = EXCLUDED.label,
            mode = EXCLUDED.mode,
            metadata = EXCLUDED.metadata,
            encrypted_secret = EXCLUDED.encrypted_secret,
            updated_at = NOW()
        RETURNING {_COLS}
        """,
        cid,
        str(entry.get("provider") or "").strip(),
        _norm_ws(entry),
        (str(entry.get("agent_install_id")).strip() or None) if entry.get("agent_install_id") else None,
        (str(entry.get("account_label")).strip() or None) if entry.get("account_label") else None,
        _derive_platform_scoped(entry),
        entry.get("label"),
        entry.get("mode"),
        json.dumps(entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}),
        enc,
        entry.get("created_at"),
    )
    return _row_to_entry(row)


async def insert(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Insert a new credential. Errors if the id already exists."""
    existing = await get(str(entry.get("id") or ""))
    if existing is not None:
        raise ValueError(f"credential id already exists: {entry.get('id')}")
    return await upsert(entry)


async def update_metadata(credential_id: str, metadata: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    pool = await _pool()
    row = await pool.fetchrow(
        f"UPDATE vault_credentials SET metadata = $2::jsonb, updated_at = NOW() WHERE id = $1 RETURNING {_COLS}",
        str(credential_id or "").strip(),
        json.dumps(metadata if isinstance(metadata, dict) else {}),
    )
    return _row_to_entry(row) if row is not None else None


async def update_secret(
    credential_id: str,
    *,
    encrypted_secret: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Rotate one row's ciphertext (and optionally its metadata) in place — used
    by OAuth refresh and key rotation. Single-row UPDATE."""
    pool = await _pool()
    if metadata is None:
        row = await pool.fetchrow(
            f"UPDATE vault_credentials SET encrypted_secret = $2, updated_at = NOW() WHERE id = $1 RETURNING {_COLS}",
            str(credential_id or "").strip(),
            str(encrypted_secret or ""),
        )
    else:
        row = await pool.fetchrow(
            f"UPDATE vault_credentials SET encrypted_secret = $2, metadata = $3::jsonb, updated_at = NOW() "
            f"WHERE id = $1 RETURNING {_COLS}",
            str(credential_id or "").strip(),
            str(encrypted_secret or ""),
            json.dumps(metadata if isinstance(metadata, dict) else {}),
        )
    return _row_to_entry(row) if row is not None else None


async def delete(credential_id: str) -> bool:
    pool = await _pool()
    result = await pool.execute("DELETE FROM vault_credentials WHERE id = $1", str(credential_id or "").strip())
    return str(result or "").strip().endswith("1")


async def rotate_secrets(pairs: List[tuple]) -> int:
    """Atomically re-write many rows' ciphertext in ONE transaction. Used by
    vault-key rotation, where a partial update is unrecoverable — some secrets
    would be left under the old key and others under the new. All-or-nothing:
    if any row fails, the whole rotation rolls back."""
    clean = [(str(cid).strip(), str(enc)) for cid, enc in pairs if str(cid or "").strip() and str(enc or "")]
    if not clean:
        return 0
    pool = await _pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            updated = 0
            for cid, enc in clean:
                res = await conn.execute(
                    "UPDATE vault_credentials SET encrypted_secret = $2, updated_at = NOW() WHERE id = $1",
                    cid, enc,
                )
                if str(res).strip().endswith("1"):
                    updated += 1
            return updated

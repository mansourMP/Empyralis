"""OAuth 2.1 authorization server for the Empyralis MCP server.

This module lets Claude (web/desktop/mobile) add Empyralis as a one-click
**Connector** via OAuth, instead of the operator having to hand-copy a
per-workspace bearer API key (``server_modules/mcp_server_auth.py``, which
keeps working unchanged — see ``load_access_token`` below).

The installed ``mcp`` SDK (``mcp.server.auth.*``) already implements the
OAuth 2.1 mechanics — PKCE, RFC 7591 dynamic client registration, RFC 8414
authorization-server metadata, RFC 9728 protected-resource metadata, and the
401/``WWW-Authenticate`` challenge. This module supplies only the two things
the SDK deliberately leaves to the application:

1. ``EmpyralisOAuthProvider`` — the ``OAuthAuthorizationServerProvider``
   Protocol (``mcp/server/auth/provider.py``) implementation, backed by
   Empyralis's own Postgres control-plane database (with a local-SQLite
   fallback for dev, mirroring ``control_plane_repository.py``'s own
   dual-mode pattern) — never a flat JSON file.
2. The ``/mcp/consent`` screen — a plain FastAPI HTML page that ties the
   OAuth ``/authorize`` step to Empyralis's *existing* dashboard session
   (``server_modules/auth.py``): if nobody is logged in it bounces to the
   normal login page; once logged in it shows "<client> wants to access
   workspace <X> — Allow / Deny" and only mints an authorization code on an
   explicit, authenticated Allow.

Security invariants (see also the module-level tests):
  * Authorization codes are single-use (atomically consumed), short-lived,
    and PKCE-bound (S256 only — the SDK enforces the challenge/verifier
    match before ``exchange_authorization_code`` is ever called).
  * Access/refresh tokens are opaque, random (256 bits), stored only as
    SHA-256 hashes, and revocable. Refresh tokens rotate on every use.
  * Nothing here ever logs a raw code/token/secret — only hashes or
    ``_short_id()`` suffixes for correlation.
  * The workspace on an access token is fixed at consent time from the
    operator's *authenticated* dashboard session and membership list — it is
    never taken from a tool argument or an unauthenticated request field.
  * RFC 8707 resource indicators are validated against this server's own
    resource URL, both when an authorization is requested and again when a
    bearer token is loaded (defense in depth beyond what the SDK checks).
"""

from __future__ import annotations

import hashlib
import hmac
import html as _html
import json
import logging
import os
import secrets
import threading
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import AnyUrl

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    RegistrationError,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from server_modules import client_identity_service
from server_modules import db as runtime_db
from server_modules.jwt_secret import resolve_jwt_secret
from server_modules.sqlite_helpers import connect_sqlite_rw
from server_modules.state_paths import resolve_state_path

LOGGER = logging.getLogger(__name__)

# ── Scopes ───────────────────────────────────────────────────────────────

SCOPE_READ = "empyralis:read"
SCOPE_WRITE = "empyralis:write"
SCOPES = (SCOPE_READ, SCOPE_WRITE)
DEFAULT_SCOPES = (SCOPE_READ,)

_SCOPE_DESCRIPTIONS = {
    SCOPE_READ: "View your projects, agents, and activity",
    SCOPE_WRITE: "Create and configure agents, send messages, and connect channels on your behalf",
}

# Sentinel client_id for access tokens synthesized from a legacy per-workspace
# bearer API key (server_modules/mcp_server_auth.py). Never collides with a
# real DCR client_id (those are uuid4 strings).
LEGACY_CLIENT_ID = "legacy-api-key"

CONSENT_PATH = "/mcp/consent"

# ── Tunables (env-overridable) ──────────────────────────────────────────

_ACCESS_TOKEN_TTL_SECONDS = int(os.getenv("EMPYRALIS_MCP_OAUTH_ACCESS_TOKEN_TTL_SECONDS", "3600"))
_REFRESH_TOKEN_TTL_SECONDS = int(os.getenv("EMPYRALIS_MCP_OAUTH_REFRESH_TOKEN_TTL_SECONDS", str(60 * 60 * 24 * 180)))
_AUTHORIZATION_CODE_TTL_SECONDS = int(os.getenv("EMPYRALIS_MCP_OAUTH_AUTHZ_CODE_TTL_SECONDS", "120"))
_CONSENT_TICKET_TTL_SECONDS = int(os.getenv("EMPYRALIS_MCP_OAUTH_CONSENT_TICKET_TTL_SECONDS", "600"))
_REGISTER_RATE_LIMIT_PER_HOUR = int(os.getenv("EMPYRALIS_MCP_OAUTH_REGISTER_RATE_LIMIT_PER_HOUR", "20"))

_CONSENT_CSRF_COOKIE = "empyralis_mcp_oauth_csrf"


# ── Token model extensions ──────────────────────────────────────────────
#
# The SDK's docstring for these TypeVars explicitly sanctions this: "It's OK
# to add fields to subclasses which should not be exposed externally." We
# carry workspace_id/tenant_id/user_id alongside the SDK's own fields so
# `_resolve_workspace()` in mcp_server.py can derive the workspace straight
# from the verified token — never from a tool argument.


class EmpyralisAuthorizationCode(AuthorizationCode):
    workspace_id: str
    tenant_id: str = ""
    user_id: str = ""


class EmpyralisRefreshToken(RefreshToken):
    workspace_id: str
    tenant_id: str = ""
    user_id: str = ""


class EmpyralisAccessToken(AccessToken):
    workspace_id: str
    tenant_id: str = ""
    user_id: str = ""


# ── Schema ───────────────────────────────────────────────────────────────
#
# Same dual-mode pattern as control_plane_repository.py: Postgres via the
# shared pool (server_modules/db.py) when DATABASE_URL is configured, else a
# dedicated local SQLite file under EMPYRALIS_STATE_HOME. All "blob" columns
# are plain TEXT (JSON-encoded) in both dialects so the query/insert/update
# SQL stays structurally identical between the two — only placeholder syntax
# ($1 vs ?) differs.

_PG_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS mcp_oauth_clients (
    client_id TEXT PRIMARY KEY,
    client_secret_hash TEXT,
    client_info_json TEXT NOT NULL,
    created_at BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS mcp_oauth_authorization_codes (
    code_hash TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    code_challenge TEXT NOT NULL,
    redirect_uri TEXT NOT NULL,
    redirect_uri_explicit INTEGER NOT NULL DEFAULT 0,
    scopes_json TEXT NOT NULL DEFAULT '[]',
    resource TEXT,
    workspace_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL DEFAULT '',
    user_id TEXT NOT NULL DEFAULT '',
    expires_at DOUBLE PRECISION NOT NULL,
    created_at BIGINT NOT NULL,
    consumed_at BIGINT
);

CREATE TABLE IF NOT EXISTS mcp_oauth_access_tokens (
    token_hash TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    scopes_json TEXT NOT NULL DEFAULT '[]',
    resource TEXT,
    workspace_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL DEFAULT '',
    user_id TEXT NOT NULL DEFAULT '',
    expires_at BIGINT,
    created_at BIGINT NOT NULL,
    revoked_at BIGINT,
    refresh_token_hash TEXT
);

CREATE TABLE IF NOT EXISTS mcp_oauth_refresh_tokens (
    token_hash TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    scopes_json TEXT NOT NULL DEFAULT '[]',
    resource TEXT,
    workspace_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL DEFAULT '',
    user_id TEXT NOT NULL DEFAULT '',
    expires_at BIGINT,
    created_at BIGINT NOT NULL,
    revoked_at BIGINT,
    access_token_hash TEXT
);

CREATE INDEX IF NOT EXISTS idx_mcp_oauth_access_tokens_refresh_hash ON mcp_oauth_access_tokens(refresh_token_hash);
CREATE INDEX IF NOT EXISTS idx_mcp_oauth_refresh_tokens_access_hash ON mcp_oauth_refresh_tokens(access_token_hash);
CREATE INDEX IF NOT EXISTS idx_mcp_oauth_access_tokens_workspace ON mcp_oauth_access_tokens(workspace_id);
CREATE INDEX IF NOT EXISTS idx_mcp_oauth_refresh_tokens_workspace ON mcp_oauth_refresh_tokens(workspace_id);
"""

_SQLITE_SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS mcp_oauth_clients (
        client_id TEXT PRIMARY KEY,
        client_secret_hash TEXT,
        client_info_json TEXT NOT NULL,
        created_at INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS mcp_oauth_authorization_codes (
        code_hash TEXT PRIMARY KEY,
        client_id TEXT NOT NULL,
        code_challenge TEXT NOT NULL,
        redirect_uri TEXT NOT NULL,
        redirect_uri_explicit INTEGER NOT NULL DEFAULT 0,
        scopes_json TEXT NOT NULL DEFAULT '[]',
        resource TEXT,
        workspace_id TEXT NOT NULL,
        tenant_id TEXT NOT NULL DEFAULT '',
        user_id TEXT NOT NULL DEFAULT '',
        expires_at REAL NOT NULL,
        created_at INTEGER NOT NULL,
        consumed_at INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS mcp_oauth_access_tokens (
        token_hash TEXT PRIMARY KEY,
        client_id TEXT NOT NULL,
        scopes_json TEXT NOT NULL DEFAULT '[]',
        resource TEXT,
        workspace_id TEXT NOT NULL,
        tenant_id TEXT NOT NULL DEFAULT '',
        user_id TEXT NOT NULL DEFAULT '',
        expires_at INTEGER,
        created_at INTEGER NOT NULL,
        revoked_at INTEGER,
        refresh_token_hash TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS mcp_oauth_refresh_tokens (
        token_hash TEXT PRIMARY KEY,
        client_id TEXT NOT NULL,
        scopes_json TEXT NOT NULL DEFAULT '[]',
        resource TEXT,
        workspace_id TEXT NOT NULL,
        tenant_id TEXT NOT NULL DEFAULT '',
        user_id TEXT NOT NULL DEFAULT '',
        expires_at INTEGER,
        created_at INTEGER NOT NULL,
        revoked_at INTEGER,
        access_token_hash TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_mcp_oauth_access_tokens_refresh_hash ON mcp_oauth_access_tokens(refresh_token_hash)",
    "CREATE INDEX IF NOT EXISTS idx_mcp_oauth_refresh_tokens_access_hash ON mcp_oauth_refresh_tokens(access_token_hash)",
    "CREATE INDEX IF NOT EXISTS idx_mcp_oauth_access_tokens_workspace ON mcp_oauth_access_tokens(workspace_id)",
    "CREATE INDEX IF NOT EXISTS idx_mcp_oauth_refresh_tokens_workspace ON mcp_oauth_refresh_tokens(workspace_id)",
]

_PG_SCHEMA_READY = False
_PG_SCHEMA_LOCK = threading.Lock()
_LOCAL_SCHEMA_READY_PATHS: set[str] = set()
_LOCAL_LOCK = threading.Lock()


def _local_db_path():
    return resolve_state_path("EMPYRALIS_MCP_OAUTH_DB", "control-plane/mcp-oauth.sqlite3")


def _local_conn():
    path = _local_db_path()
    conn = connect_sqlite_rw(path, logger=LOGGER, label="mcp_oauth")
    key = str(path)
    if key not in _LOCAL_SCHEMA_READY_PATHS:
        for statement in _SQLITE_SCHEMA_STATEMENTS:
            conn.execute(statement)
        conn.commit()
        _LOCAL_SCHEMA_READY_PATHS.add(key)
    return conn


async def _pg_pool() -> Any:
    return await runtime_db.get_pool()


async def _ensure_pg_schema(pool: Any) -> None:
    global _PG_SCHEMA_READY
    if _PG_SCHEMA_READY:
        return
    # asyncpg pools are asyncio-native; this lock only needs to keep concurrent
    # coroutines on the SAME loop from racing the first-time migration.
    with _PG_SCHEMA_LOCK:
        if _PG_SCHEMA_READY:
            return
        await pool.execute(_PG_SCHEMA_SQL)
        _PG_SCHEMA_READY = True


async def _execute(pg_sql: str, sqlite_sql: str, params: tuple) -> None:
    pool = await _pg_pool()
    if pool is not None:
        await _ensure_pg_schema(pool)
        await pool.execute(pg_sql, *params)
        return
    with _LOCAL_LOCK:
        with _local_conn() as conn:
            conn.execute(sqlite_sql, params)
            conn.commit()


async def _fetchrow(pg_sql: str, sqlite_sql: str, params: tuple) -> Optional[Dict[str, Any]]:
    pool = await _pg_pool()
    if pool is not None:
        await _ensure_pg_schema(pool)
        row = await pool.fetchrow(pg_sql, *params)
        return dict(row) if row is not None else None
    with _LOCAL_LOCK:
        with _local_conn() as conn:
            cursor = conn.execute(sqlite_sql, params)
            row = cursor.fetchone()
            conn.commit()
            return dict(row) if row is not None else None


# ── Hashing / opaque token helpers ──────────────────────────────────────


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _new_opaque_token() -> str:
    # 32 bytes = 256 bits of entropy, well above RFC 6749 §10.10's 128-bit floor.
    return secrets.token_urlsafe(32)


def _short_id(value: str) -> str:
    """Last-6-chars correlation id for logs. Never logs the full secret."""
    v = str(value or "")
    return f"...{v[-6:]}" if len(v) > 6 else "***"


def _resource_matches(requested: Optional[str], expected: str) -> bool:
    a = str(requested or "").strip().rstrip("/")
    b = str(expected or "").strip().rstrip("/")
    return bool(a) and bool(b) and a == b


# ── Client persistence ───────────────────────────────────────────────────


async def _store_client(client_info: OAuthClientInformationFull) -> None:
    payload = client_info.model_dump_json()
    secret_hash = _hash_token(client_info.client_secret) if client_info.client_secret else None
    now = int(time.time())
    await _execute(
        """
        INSERT INTO mcp_oauth_clients (client_id, client_secret_hash, client_info_json, created_at)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (client_id) DO UPDATE SET
            client_secret_hash = EXCLUDED.client_secret_hash,
            client_info_json = EXCLUDED.client_info_json
        """,
        """
        INSERT INTO mcp_oauth_clients (client_id, client_secret_hash, client_info_json, created_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (client_id) DO UPDATE SET
            client_secret_hash = excluded.client_secret_hash,
            client_info_json = excluded.client_info_json
        """,
        (client_info.client_id, secret_hash, payload, now),
    )


async def _load_client_row(client_id: str) -> Optional[OAuthClientInformationFull]:
    row = await _fetchrow(
        "SELECT client_info_json FROM mcp_oauth_clients WHERE client_id = $1",
        "SELECT client_info_json FROM mcp_oauth_clients WHERE client_id = ?",
        (client_id,),
    )
    if row is None:
        return None
    return OAuthClientInformationFull.model_validate_json(row["client_info_json"])


# ── Authorization code persistence ──────────────────────────────────────


async def _store_authorization_code(
    code: str,
    *,
    client_id: str,
    code_challenge: str,
    redirect_uri: str,
    redirect_uri_explicit: bool,
    scopes: List[str],
    resource: Optional[str],
    workspace_id: str,
    tenant_id: str,
    user_id: str,
    expires_at: float,
) -> None:
    code_hash = _hash_token(code)
    scopes_json = json.dumps(list(scopes or []))
    now = int(time.time())
    await _execute(
        """
        INSERT INTO mcp_oauth_authorization_codes
            (code_hash, client_id, code_challenge, redirect_uri, redirect_uri_explicit,
             scopes_json, resource, workspace_id, tenant_id, user_id, expires_at, created_at)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
        ON CONFLICT (code_hash) DO NOTHING
        """,
        """
        INSERT OR IGNORE INTO mcp_oauth_authorization_codes
            (code_hash, client_id, code_challenge, redirect_uri, redirect_uri_explicit,
             scopes_json, resource, workspace_id, tenant_id, user_id, expires_at, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            code_hash, client_id, code_challenge, redirect_uri, 1 if redirect_uri_explicit else 0,
            scopes_json, resource, workspace_id, tenant_id, user_id, float(expires_at), now,
        ),
    )


async def _load_authorization_code_row(client_id: str, code: str) -> Optional[Dict[str, Any]]:
    code_hash = _hash_token(code)
    now = time.time()
    return await _fetchrow(
        """
        SELECT * FROM mcp_oauth_authorization_codes
        WHERE code_hash = $1 AND client_id = $2 AND consumed_at IS NULL AND expires_at > $3
        """,
        """
        SELECT * FROM mcp_oauth_authorization_codes
        WHERE code_hash = ? AND client_id = ? AND consumed_at IS NULL AND expires_at > ?
        """,
        (code_hash, client_id, now),
    )


async def _consume_authorization_code_row(client_id: str, code: str) -> Optional[Dict[str, Any]]:
    """Atomically mark the code used. Returns None if it was already used/unknown/expired."""
    code_hash = _hash_token(code)
    now = time.time()
    return await _fetchrow(
        """
        UPDATE mcp_oauth_authorization_codes SET consumed_at = $1
        WHERE code_hash = $2 AND client_id = $3 AND consumed_at IS NULL AND expires_at > $4
        RETURNING *
        """,
        """
        UPDATE mcp_oauth_authorization_codes SET consumed_at = ?
        WHERE code_hash = ? AND client_id = ? AND consumed_at IS NULL AND expires_at > ?
        RETURNING *
        """,
        (int(now), code_hash, client_id, now),
    )


def _authorization_code_from_row(code: str, row: Dict[str, Any]) -> EmpyralisAuthorizationCode:
    return EmpyralisAuthorizationCode(
        code=code,
        scopes=json.loads(row.get("scopes_json") or "[]"),
        expires_at=float(row["expires_at"]),
        client_id=row["client_id"],
        code_challenge=row["code_challenge"],
        redirect_uri=AnyUrl(row["redirect_uri"]),
        redirect_uri_provided_explicitly=bool(row.get("redirect_uri_explicit")),
        resource=row.get("resource"),
        workspace_id=row["workspace_id"],
        tenant_id=row.get("tenant_id") or "",
        user_id=row.get("user_id") or "",
    )


# ── Access / refresh token persistence ──────────────────────────────────


async def _store_access_token(
    token_hash: str,
    *,
    client_id: str,
    scopes_json: str,
    resource: Optional[str],
    workspace_id: str,
    tenant_id: str,
    user_id: str,
    expires_at: Optional[int],
    refresh_token_hash: Optional[str],
) -> None:
    now = int(time.time())
    await _execute(
        """
        INSERT INTO mcp_oauth_access_tokens
            (token_hash, client_id, scopes_json, resource, workspace_id, tenant_id, user_id,
             expires_at, created_at, refresh_token_hash)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
        """,
        """
        INSERT INTO mcp_oauth_access_tokens
            (token_hash, client_id, scopes_json, resource, workspace_id, tenant_id, user_id,
             expires_at, created_at, refresh_token_hash)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (token_hash, client_id, scopes_json, resource, workspace_id, tenant_id, user_id, expires_at, now, refresh_token_hash),
    )


async def _store_refresh_token(
    token_hash: str,
    *,
    client_id: str,
    scopes_json: str,
    resource: Optional[str],
    workspace_id: str,
    tenant_id: str,
    user_id: str,
    expires_at: Optional[int],
    access_token_hash: Optional[str],
) -> None:
    now = int(time.time())
    await _execute(
        """
        INSERT INTO mcp_oauth_refresh_tokens
            (token_hash, client_id, scopes_json, resource, workspace_id, tenant_id, user_id,
             expires_at, created_at, access_token_hash)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
        """,
        """
        INSERT INTO mcp_oauth_refresh_tokens
            (token_hash, client_id, scopes_json, resource, workspace_id, tenant_id, user_id,
             expires_at, created_at, access_token_hash)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (token_hash, client_id, scopes_json, resource, workspace_id, tenant_id, user_id, expires_at, now, access_token_hash),
    )


async def _mint_token_pair(
    *, client_id: str, scopes: List[str], resource: Optional[str], workspace_id: str, tenant_id: str, user_id: str,
) -> tuple[str, str, int]:
    access_token = _new_opaque_token()
    refresh_token = _new_opaque_token()
    now = int(time.time())
    access_expires_at = now + _ACCESS_TOKEN_TTL_SECONDS
    refresh_expires_at = now + _REFRESH_TOKEN_TTL_SECONDS
    scopes_json = json.dumps(list(scopes or []))
    access_hash = _hash_token(access_token)
    refresh_hash = _hash_token(refresh_token)
    await _store_access_token(
        access_hash, client_id=client_id, scopes_json=scopes_json, resource=resource,
        workspace_id=workspace_id, tenant_id=tenant_id, user_id=user_id,
        expires_at=access_expires_at, refresh_token_hash=refresh_hash,
    )
    await _store_refresh_token(
        refresh_hash, client_id=client_id, scopes_json=scopes_json, resource=resource,
        workspace_id=workspace_id, tenant_id=tenant_id, user_id=user_id,
        expires_at=refresh_expires_at, access_token_hash=access_hash,
    )
    return access_token, refresh_token, _ACCESS_TOKEN_TTL_SECONDS


async def _load_access_token_row(token: str) -> Optional[Dict[str, Any]]:
    token_hash = _hash_token(token)
    now = int(time.time())
    return await _fetchrow(
        """
        SELECT * FROM mcp_oauth_access_tokens
        WHERE token_hash = $1 AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at > $2)
        """,
        """
        SELECT * FROM mcp_oauth_access_tokens
        WHERE token_hash = ? AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at > ?)
        """,
        (token_hash, now),
    )


async def _load_refresh_token_row(client_id: str, token: str) -> Optional[Dict[str, Any]]:
    token_hash = _hash_token(token)
    now = int(time.time())
    return await _fetchrow(
        """
        SELECT * FROM mcp_oauth_refresh_tokens
        WHERE token_hash = $1 AND client_id = $2 AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at > $3)
        """,
        """
        SELECT * FROM mcp_oauth_refresh_tokens
        WHERE token_hash = ? AND client_id = ? AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at > ?)
        """,
        (token_hash, client_id, now),
    )


async def _consume_refresh_token_row(client_id: str, token: str) -> Optional[Dict[str, Any]]:
    """Atomically rotate: marks the refresh token revoked and returns its row, or
    None if it was already revoked/unknown/expired."""
    token_hash = _hash_token(token)
    now = int(time.time())
    return await _fetchrow(
        """
        UPDATE mcp_oauth_refresh_tokens SET revoked_at = $1
        WHERE token_hash = $2 AND client_id = $3 AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at > $4)
        RETURNING *
        """,
        """
        UPDATE mcp_oauth_refresh_tokens SET revoked_at = ?
        WHERE token_hash = ? AND client_id = ? AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at > ?)
        RETURNING *
        """,
        (now, token_hash, client_id, now),
    )


def _refresh_token_from_row(token: str, row: Dict[str, Any]) -> EmpyralisRefreshToken:
    return EmpyralisRefreshToken(
        token=token,
        client_id=row["client_id"],
        scopes=json.loads(row.get("scopes_json") or "[]"),
        expires_at=row.get("expires_at"),
        workspace_id=row["workspace_id"],
        tenant_id=row.get("tenant_id") or "",
        user_id=row.get("user_id") or "",
    )


async def _revoke_access_token(token: str) -> None:
    token_hash = _hash_token(token)
    now = int(time.time())
    row = await _fetchrow(
        """
        UPDATE mcp_oauth_access_tokens SET revoked_at = $1
        WHERE token_hash = $2 AND revoked_at IS NULL RETURNING refresh_token_hash
        """,
        """
        UPDATE mcp_oauth_access_tokens SET revoked_at = ?
        WHERE token_hash = ? AND revoked_at IS NULL RETURNING refresh_token_hash
        """,
        (now, token_hash),
    )
    if row and row.get("refresh_token_hash"):
        await _execute(
            "UPDATE mcp_oauth_refresh_tokens SET revoked_at = $1 WHERE token_hash = $2 AND revoked_at IS NULL",
            "UPDATE mcp_oauth_refresh_tokens SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL",
            (now, row["refresh_token_hash"]),
        )


async def _revoke_refresh_token(token: str) -> None:
    token_hash = _hash_token(token)
    now = int(time.time())
    row = await _fetchrow(
        """
        UPDATE mcp_oauth_refresh_tokens SET revoked_at = $1
        WHERE token_hash = $2 AND revoked_at IS NULL RETURNING access_token_hash
        """,
        """
        UPDATE mcp_oauth_refresh_tokens SET revoked_at = ?
        WHERE token_hash = ? AND revoked_at IS NULL RETURNING access_token_hash
        """,
        (now, token_hash),
    )
    if row and row.get("access_token_hash"):
        await _execute(
            "UPDATE mcp_oauth_access_tokens SET revoked_at = $1 WHERE token_hash = $2 AND revoked_at IS NULL",
            "UPDATE mcp_oauth_access_tokens SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL",
            (now, row["access_token_hash"]),
        )


# ── Audit ledger (best-effort, mirrors mcp_server.py's _ledger_mcp_call) ──


async def _ledger_oauth_event(*, workspace_id: str, tenant_id: str, action: str, summary: str) -> None:
    try:
        from server_modules import activity_ledger_service

        await activity_ledger_service.append_activity_event(
            tenant_id=tenant_id or "default",
            workspace_id=workspace_id,
            actor_type="external_mcp_client",
            actor_id="mcp_oauth",
            event_class="mcp_oauth",
            action=action,
            title=f"MCP OAuth: {action}",
            summary=summary,
        )
    except Exception:
        LOGGER.debug("Failed to ledger MCP OAuth event %s", action, exc_info=True)


# ── Consent ticket (stateless, HMAC-signed — carries the pending /authorize
# request across the "go log in, then come back" redirect without a DB write) ──


class _ConsentTicketError(Exception):
    pass


def _b64url(raw: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    import base64

    padding = "=" * ((4 - len(value) % 4) % 4)
    return base64.urlsafe_b64decode(f"{value}{padding}".encode("ascii"))


def _consent_signing_key() -> bytes:
    # Domain-separated from the dashboard session JWT secret so a leaked
    # consent ticket can never be mistaken for (or replayed as) a session token.
    return hmac.new(resolve_jwt_secret().encode("utf-8"), b"mcp-oauth-consent-ticket:v1", hashlib.sha256).digest()


def _sign_consent_ticket(payload: Dict[str, Any]) -> str:
    body = {**payload, "exp": time.time() + _CONSENT_TICKET_TTL_SECONDS}
    raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
    signature = hmac.new(_consent_signing_key(), raw, hashlib.sha256).digest()
    return f"{_b64url(raw)}.{_b64url(signature)}"


def _verify_consent_ticket(ticket: str) -> Dict[str, Any]:
    try:
        raw_b64, sig_b64 = str(ticket or "").split(".", 1)
        raw = _b64url_decode(raw_b64)
        signature = _b64url_decode(sig_b64)
    except Exception as exc:
        raise _ConsentTicketError("This connection request is invalid. Go back to Claude and try connecting again.") from exc
    expected_signature = hmac.new(_consent_signing_key(), raw, hashlib.sha256).digest()
    if not hmac.compare_digest(signature, expected_signature):
        raise _ConsentTicketError("This connection request could not be verified. Go back to Claude and try connecting again.")
    try:
        body = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise _ConsentTicketError("This connection request is invalid. Go back to Claude and try connecting again.") from exc
    if float(body.get("exp") or 0) < time.time():
        raise _ConsentTicketError("This connection request expired. Go back to Claude and try connecting again.")
    return body


# ── Frontend origin (for the login bounce) ──────────────────────────────
#
# Same "first configured origin, else FRONTEND_ORIGINS[0], else localhost"
# convention already used independently in channel_pairing_service.py,
# cloud_cutover_config.py, and connectors_actions.py.

_PUBLIC_FRONTEND_ORIGIN_ENV_KEYS = (
    "EMPYRALIS_PUBLIC_FRONTEND_ORIGIN",
    "FRONTEND_PUBLIC_ORIGIN",
    "NEXT_PUBLIC_APP_ORIGIN",
    "BACKEND_PUBLIC_ORIGIN",
)


def _frontend_origin() -> str:
    for key in _PUBLIC_FRONTEND_ORIGIN_ENV_KEYS:
        value = str(os.getenv(key) or "").strip()
        if value:
            return value.rstrip("/")
    origins = str(os.getenv("FRONTEND_ORIGINS") or "").strip()
    if origins:
        first = origins.split(",", 1)[0].strip()
        if first:
            return first.rstrip("/")
    return "http://127.0.0.1:3000"


def _cookie_secure(request: Request) -> bool:
    forwarded_proto = str(request.headers.get("x-forwarded-proto") or "").split(",", 1)[0].strip().lower()
    scheme = forwarded_proto or str(getattr(request.url, "scheme", "") or "").lower()
    if scheme == "https":
        return True
    return str(os.getenv("EMPYRALIS_DEPLOY_ENV") or os.getenv("ORION_ENV") or "").strip().lower() in {
        "production", "staging", "beta", "prod",
    }


def _current_dashboard_user(request: Request) -> Optional[Dict[str, Any]]:
    """Resolve the logged-in dashboard user from the existing session cookie,
    or None when nobody is logged in. Never raises."""
    from fastapi import HTTPException

    from server_modules import auth

    try:
        return auth.get_current_user(request=request, authorization=None, x_api_key=None)
    except HTTPException:
        return None
    except Exception:
        LOGGER.exception("Unexpected error resolving dashboard session for MCP OAuth consent.")
        return None


def _accessible_workspace_ids(current_user: Dict[str, Any]) -> List[str]:
    ids = [str(w or "").strip() for w in (current_user.get("workspace_ids") or [])]
    return [w for w in ids if w]


# ── Consent page rendering (plain HTML — no Next.js involved) ───────────

_CONSENT_CSS = """
:root { color-scheme: light dark; }
body {
  margin: 0; padding: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center;
  background: #0b0d12; color: #e7e9ee;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Inter, Roboto, sans-serif;
}
.card {
  width: 100%; max-width: 420px; margin: 32px; padding: 32px;
  background: #12151c; border: 1px solid #23273245; border-radius: 16px;
  box-shadow: 0 20px 60px rgba(0,0,0,0.35);
}
h1 { font-size: 1.15rem; line-height: 1.4; margin: 0 0 16px; font-weight: 600; }
p { line-height: 1.5; color: #a7adba; margin: 0 0 12px; }
ul.scopes { list-style: none; margin: 0 0 20px; padding: 0; }
ul.scopes li { padding: 10px 0; border-top: 1px solid #ffffff12; color: #d7dae1; font-size: 0.92rem; }
ul.scopes li:first-child { border-top: none; }
.workspace-line strong { color: #e7e9ee; }
.workspace-picker p { margin-bottom: 8px; }
.workspace-option { display: block; padding: 8px 0; color: #d7dae1; font-size: 0.92rem; }
.actions { display: flex; gap: 10px; margin-top: 24px; }
.btn { flex: 1; padding: 11px 16px; border-radius: 10px; border: 1px solid transparent; font-size: 0.95rem; font-weight: 600; cursor: pointer; }
.btn-primary { background: #6c5ce7; color: white; }
.btn-primary:hover { background: #7d6ef2; }
.btn-secondary { background: transparent; border-color: #ffffff28; color: #d7dae1; }
.btn-secondary:hover { background: #ffffff0c; }
.fine-print { margin-top: 18px; font-size: 0.78rem; color: #6c7280; }
a { color: #9b8cf2; }
"""


def _render_message_page(title: str, message: str, *, status_hint: str = "") -> str:
    extra = f'<p class="fine-print">{_html.escape(status_hint)}</p>' if status_hint else ""
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        f"<title>{_html.escape(title)} — Empyralis</title>"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<style>{_CONSENT_CSS}</style></head>"
        f"<body><main class=\"card\"><h1>{_html.escape(title)}</h1>"
        f"<p>{_html.escape(message)}</p>{extra}</main></body></html>"
    )


def _render_consent_form(
    *, client: OAuthClientInformationFull, scopes: List[str], workspace_ids: List[str], ticket: str, csrf_token: str,
) -> str:
    client_label = _html.escape(str(client.client_name or client.client_id or "This application"))
    scope_items = "".join(
        f"<li>{_html.escape(_SCOPE_DESCRIPTIONS.get(s, s))}</li>" for s in scopes
    ) or "<li>Basic access</li>"

    if len(workspace_ids) == 1:
        workspace_field = (
            f'<input type="hidden" name="workspace_id" value="{_html.escape(workspace_ids[0])}">'
            f'<p class="workspace-line">Workspace: <strong>{_html.escape(workspace_ids[0])}</strong></p>'
        )
    else:
        options = "".join(
            '<label class="workspace-option"><input type="radio" name="workspace_id" '
            f'value="{_html.escape(w)}"{" checked" if i == 0 else ""}> {_html.escape(w)}</label>'
            for i, w in enumerate(workspace_ids)
        )
        workspace_field = f'<div class="workspace-picker"><p>Choose a workspace:</p>{options}</div>'

    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        f"<title>Connect {client_label} — Empyralis</title>"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<style>{_CONSENT_CSS}</style></head>"
        "<body><main class=\"card\">"
        f"<h1>{client_label} wants to access your Empyralis workspace</h1>"
        f'<ul class="scopes">{scope_items}</ul>'
        f"{workspace_field}"
        f'<form method="post" action="{CONSENT_PATH}">'
        f'<input type="hidden" name="ticket" value="{_html.escape(ticket)}">'
        f'<input type="hidden" name="csrf_token" value="{_html.escape(csrf_token)}">'
        '<div class="actions">'
        '<button type="submit" name="decision" value="deny" class="btn btn-secondary">Deny</button>'
        '<button type="submit" name="decision" value="allow" class="btn btn-primary">Allow</button>'
        "</div></form>"
        '<p class="fine-print">You can revoke this access later from Empyralis workspace settings.</p>'
        "</main></body></html>"
    )


# ── The provider ─────────────────────────────────────────────────────────


class EmpyralisOAuthProvider(
    OAuthAuthorizationServerProvider[EmpyralisAuthorizationCode, EmpyralisRefreshToken, EmpyralisAccessToken]
):
    def __init__(self, *, issuer_url: str, resource_server_url: str):
        self._issuer_url = str(issuer_url).rstrip("/")
        self._resource_server_url = str(resource_server_url).rstrip("/")

    # -- Dynamic client registration (RFC 7591) --------------------------

    async def get_client(self, client_id: str) -> Optional[OAuthClientInformationFull]:
        clean = str(client_id or "").strip()
        if not clean:
            return None
        return await _load_client_row(clean)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if not client_info.client_id:
            raise RegistrationError(error="invalid_client_metadata", error_description="client_id is required.")
        await _store_client(client_info)
        LOGGER.info(
            "MCP OAuth client registered: client_id=%s client_name=%r redirect_uris=%d",
            client_info.client_id, client_info.client_name or "", len(client_info.redirect_uris or []),
        )

    # -- Authorization step: hand off to our own /mcp/consent page -------

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        if params.resource is not None and not _resource_matches(params.resource, self._resource_server_url):
            raise AuthorizeError(
                error="invalid_request",
                error_description="The requested resource is not served by this authorization server.",
            )
        scopes = list(params.scopes) if params.scopes else _default_scopes_for_client(client)
        ticket = _sign_consent_ticket(
            {
                "client_id": client.client_id,
                "redirect_uri": str(params.redirect_uri),
                "redirect_uri_explicit": bool(params.redirect_uri_provided_explicitly),
                "code_challenge": params.code_challenge,
                "scopes": scopes,
                "state": params.state,
                "resource": params.resource or self._resource_server_url,
            }
        )
        return f"{self._issuer_url}{CONSENT_PATH}?{urlencode({'ticket': ticket})}"

    # -- Authorization code lifecycle -------------------------------------

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> Optional[EmpyralisAuthorizationCode]:
        row = await _load_authorization_code_row(client.client_id, authorization_code)
        if row is None:
            return None
        return _authorization_code_from_row(authorization_code, row)

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: EmpyralisAuthorizationCode
    ) -> OAuthToken:
        row = await _consume_authorization_code_row(client.client_id, authorization_code.code)
        if row is None:
            # Either a concurrent request already redeemed it, or it expired
            # between load_authorization_code() and here — never re-issue.
            raise TokenError(error="invalid_grant", error_description="Authorization code already used or expired.")
        access_token, refresh_token, expires_in = await _mint_token_pair(
            client_id=client.client_id,
            scopes=authorization_code.scopes,
            resource=row.get("resource") or self._resource_server_url,
            workspace_id=row["workspace_id"],
            tenant_id=row.get("tenant_id") or "",
            user_id=row.get("user_id") or "",
        )
        LOGGER.info(
            "MCP OAuth token issued via authorization_code: client_id=%s workspace_id=%s token=%s",
            client.client_id, row["workspace_id"], _short_id(access_token),
        )
        await _ledger_oauth_event(
            workspace_id=row["workspace_id"], tenant_id=row.get("tenant_id") or "",
            action="mcp_oauth_token_issued", summary=f"client_id={client.client_id} grant=authorization_code",
        )
        return OAuthToken(
            access_token=access_token, token_type="Bearer", expires_in=expires_in,
            scope=" ".join(authorization_code.scopes), refresh_token=refresh_token,
        )

    # -- Refresh token lifecycle -------------------------------------------

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> Optional[EmpyralisRefreshToken]:
        row = await _load_refresh_token_row(client.client_id, refresh_token)
        if row is None:
            return None
        return _refresh_token_from_row(refresh_token, row)

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: EmpyralisRefreshToken, scopes: List[str],
    ) -> OAuthToken:
        row = await _consume_refresh_token_row(client.client_id, refresh_token.token)
        if row is None:
            raise TokenError(error="invalid_grant", error_description="Refresh token already used, revoked, or expired.")
        effective_scopes = list(scopes) if scopes else refresh_token.scopes
        access_token, new_refresh_token, expires_in = await _mint_token_pair(
            client_id=client.client_id,
            scopes=effective_scopes,
            resource=row.get("resource") or self._resource_server_url,
            workspace_id=row["workspace_id"],
            tenant_id=row.get("tenant_id") or "",
            user_id=row.get("user_id") or "",
        )
        LOGGER.info(
            "MCP OAuth token refreshed (rotated): client_id=%s workspace_id=%s token=%s",
            client.client_id, row["workspace_id"], _short_id(access_token),
        )
        await _ledger_oauth_event(
            workspace_id=row["workspace_id"], tenant_id=row.get("tenant_id") or "",
            action="mcp_oauth_token_refreshed", summary=f"client_id={client.client_id}",
        )
        return OAuthToken(
            access_token=access_token, token_type="Bearer", expires_in=expires_in,
            scope=" ".join(effective_scopes), refresh_token=new_refresh_token,
        )

    # -- Access token verification (called on every /mcp request) ---------

    async def load_access_token(self, token: str) -> Optional[EmpyralisAccessToken]:
        raw = str(token or "").strip()
        if not raw:
            return None

        row = await _load_access_token_row(raw)
        if row is not None:
            resource = row.get("resource")
            if resource and not _resource_matches(resource, self._resource_server_url):
                # Defense in depth: a token minted for a different resource must
                # never authenticate a call to THIS resource server (RFC 8707).
                # Cannot happen with a single-resource-server deployment via
                # normal minting, but a stored value that ever drifts must fail
                # closed rather than silently authenticate.
                LOGGER.warning("MCP OAuth access token resource mismatch — rejecting token=%s", _short_id(raw))
                return None
            return EmpyralisAccessToken(
                token=raw,
                client_id=row["client_id"],
                scopes=json.loads(row.get("scopes_json") or "[]"),
                expires_at=row.get("expires_at"),
                resource=resource,
                workspace_id=row["workspace_id"],
                tenant_id=row.get("tenant_id") or "",
                user_id=row.get("user_id") or "",
            )

        # Legacy fallback: the pre-existing per-workspace bearer API key path
        # (server_modules/mcp_server_auth.py). Keeps Claude Code CLI and any
        # already-issued keys working unchanged after OAuth is enabled.
        from server_modules.mcp_server_auth import resolve_workspace_from_api_key

        legacy = await resolve_workspace_from_api_key(raw)
        if legacy is None:
            return None
        scopes = list(SCOPES) if legacy.get("writes_enabled") else list(DEFAULT_SCOPES)
        return EmpyralisAccessToken(
            token=raw,
            client_id=LEGACY_CLIENT_ID,
            scopes=scopes,
            expires_at=None,
            resource=self._resource_server_url,
            workspace_id=str(legacy["workspace_id"]),
            tenant_id="",
            user_id="",
        )

    # -- Revocation (RFC 7009) ----------------------------------------------

    async def revoke_token(self, token: EmpyralisAccessToken | EmpyralisRefreshToken) -> None:
        if isinstance(token, EmpyralisAccessToken):
            if token.client_id == LEGACY_CLIENT_ID:
                # Legacy bearer keys are not stored in this provider's tables —
                # they have their own lifecycle at POST /api/connections/mcp-keys.
                return
            await _revoke_access_token(token.token)
            LOGGER.info("MCP OAuth access token revoked: client_id=%s token=%s", token.client_id, _short_id(token.token))
        elif isinstance(token, EmpyralisRefreshToken):
            await _revoke_refresh_token(token.token)
            LOGGER.info("MCP OAuth refresh token revoked: client_id=%s token=%s", token.client_id, _short_id(token.token))

    # -- Consent page (not part of the Protocol — called from the routes
    #    registered by register_consent_routes()) -------------------------

    async def render_consent_page(self, request: Request, ticket: str) -> Any:
        try:
            payload = _verify_consent_ticket(ticket)
        except _ConsentTicketError as exc:
            return HTMLResponse(_render_message_page("Connection request expired", str(exc)), status_code=400)

        client = await self.get_client(str(payload.get("client_id") or ""))
        if client is None:
            return HTMLResponse(
                _render_message_page("Unknown application", "This application is no longer registered with Empyralis."),
                status_code=400,
            )

        current_user = _current_dashboard_user(request)
        if current_user is None:
            next_path = f"{CONSENT_PATH}?{urlencode({'ticket': ticket})}"
            login_url = f"{_frontend_origin()}/login?{urlencode({'next': next_path})}"
            return RedirectResponse(login_url, status_code=302)

        workspace_ids = _accessible_workspace_ids(current_user)
        if not workspace_ids:
            return HTMLResponse(
                _render_message_page("No workspace", "Your Empyralis account is not a member of any workspace yet."),
                status_code=403,
            )

        csrf_token = secrets.token_urlsafe(24)
        page_html = _render_consent_form(
            client=client, scopes=list(payload.get("scopes") or []), workspace_ids=workspace_ids,
            ticket=ticket, csrf_token=csrf_token,
        )
        response = HTMLResponse(page_html)
        response.set_cookie(
            _CONSENT_CSRF_COOKIE, csrf_token, max_age=_CONSENT_TICKET_TTL_SECONDS,
            httponly=True, secure=_cookie_secure(request), samesite="lax", path=CONSENT_PATH,
        )
        return response

    async def decide_consent(self, request: Request) -> Any:
        form = await request.form()
        ticket = str(form.get("ticket") or "")
        try:
            payload = _verify_consent_ticket(ticket)
        except _ConsentTicketError as exc:
            return HTMLResponse(_render_message_page("Connection request expired", str(exc)), status_code=400)

        cookie_csrf = request.cookies.get(_CONSENT_CSRF_COOKIE) or ""
        form_csrf = str(form.get("csrf_token") or "")
        if not cookie_csrf or not form_csrf or not hmac.compare_digest(cookie_csrf, form_csrf):
            return HTMLResponse(
                _render_message_page(
                    "Session expired",
                    "Your consent session expired or this form was not submitted from the page you were shown.",
                    status_hint="Go back to Claude and try connecting again.",
                ),
                status_code=400,
            )

        redirect_uri = str(payload.get("redirect_uri") or "")
        state = payload.get("state")

        current_user = _current_dashboard_user(request)
        if current_user is None:
            return RedirectResponse(f"{_frontend_origin()}/login", status_code=302)

        decision = str(form.get("decision") or "").strip().lower()
        response: Any
        if decision != "allow":
            target = construct_redirect_uri(redirect_uri, error="access_denied", error_description="The user denied the request.", state=state)
            response = RedirectResponse(target, status_code=302)
            response.delete_cookie(_CONSENT_CSRF_COOKIE, path=CONSENT_PATH)
            return response

        workspace_id = str(form.get("workspace_id") or "").strip()
        if workspace_id not in _accessible_workspace_ids(current_user):
            return HTMLResponse(
                _render_message_page("Workspace not accessible", "That workspace is not accessible for this account."),
                status_code=403,
            )

        client = await self.get_client(str(payload.get("client_id") or ""))
        if client is None:
            return HTMLResponse(
                _render_message_page("Unknown application", "This application is no longer registered with Empyralis."),
                status_code=400,
            )

        from server_modules import control_plane_repository as cpr

        tenant_id = await cpr.resolve_tenant_id_for_workspace(workspace_id, default="default")
        user_id = str(current_user.get("user_id") or "")
        code = _new_opaque_token()
        await _store_authorization_code(
            code,
            client_id=client.client_id,
            code_challenge=str(payload.get("code_challenge") or ""),
            redirect_uri=redirect_uri,
            redirect_uri_explicit=bool(payload.get("redirect_uri_explicit")),
            scopes=list(payload.get("scopes") or []),
            resource=payload.get("resource"),
            workspace_id=workspace_id,
            tenant_id=tenant_id,
            user_id=user_id,
            expires_at=time.time() + _AUTHORIZATION_CODE_TTL_SECONDS,
        )
        LOGGER.info(
            "MCP OAuth consent granted: client_id=%s workspace_id=%s user_id=%s",
            client.client_id, workspace_id, user_id,
        )
        await _ledger_oauth_event(
            workspace_id=workspace_id, tenant_id=tenant_id,
            action="mcp_oauth_consent_granted", summary=f"client_id={client.client_id} user_id={user_id}",
        )
        target = construct_redirect_uri(redirect_uri, code=code, state=state)
        response = RedirectResponse(target, status_code=302)
        response.delete_cookie(_CONSENT_CSRF_COOKIE, path=CONSENT_PATH)
        return response


def _default_scopes_for_client(client: OAuthClientInformationFull) -> List[str]:
    if client.scope:
        parsed = [s for s in client.scope.split() if s]
        if parsed:
            return parsed
    return list(DEFAULT_SCOPES)


# ── HTTP wiring (called from mcp_server.py's mount_empyralist_mcp) ──────


def register_consent_routes(app: FastAPI, provider: EmpyralisOAuthProvider) -> None:
    """Register the plain-FastAPI /mcp/consent GET+POST pair.

    Deliberately NOT part of the mcp SDK's auth route bundle: the SDK's
    OAuthAuthorizationServerProvider.authorize() Protocol method only gets
    (client, params) — no Request — so it cannot read the dashboard session
    cookie itself. This route does, via the normal FastAPI Request.
    """

    @app.get(CONSENT_PATH, include_in_schema=False)
    async def _mcp_oauth_consent_get(request: Request, ticket: str = ""):  # noqa: ANN202
        return await provider.render_consent_page(request, ticket)

    @app.post(CONSENT_PATH, include_in_schema=False)
    async def _mcp_oauth_consent_post(request: Request):  # noqa: ANN202
        return await provider.decide_consent(request)


# ── POST /register rate limiting ─────────────────────────────────────────
#
# RFC 7591 dynamic client registration is unauthenticated by design (that's
# the whole point — Claude self-registers on first connect) which the spec
# itself flags as abusable. In-process sliding window is sufficient for this
# single-process deployment (see docs/DEPLOY-RUNBOOK.md); a future multi-
# instance deployment would need a shared store instead.

_register_rate_lock = threading.Lock()
_register_rate_buckets: Dict[str, List[float]] = {}


def _check_register_rate_limit(client_ip: str) -> bool:
    now = time.monotonic()
    window_start = now - 3600.0
    with _register_rate_lock:
        bucket = _register_rate_buckets.setdefault(client_ip or "unknown", [])
        while bucket and bucket[0] < window_start:
            bucket.pop(0)
        if len(bucket) >= _REGISTER_RATE_LIMIT_PER_HOUR:
            return False
        bucket.append(now)
        return True


def register_register_rate_limit_guard(app: FastAPI) -> None:
    @app.middleware("http")
    async def _mcp_oauth_register_rate_limit(request: Request, call_next):  # noqa: ANN202
        if request.method == "POST" and request.url.path == "/register":
            client_ip = client_identity_service.resolve_client_ip(request)
            if not _check_register_rate_limit(client_ip):
                LOGGER.warning("MCP OAuth client registration rate-limited: ip=%s", client_ip)
                return JSONResponse(
                    {"error": "rate_limited", "error_description": "Too many client registration attempts. Try again later."},
                    status_code=429,
                    headers={"Retry-After": "60"},
                )
        return await call_next(request)

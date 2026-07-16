"""Tests for server_modules/mcp_oauth_provider.py — the OAuth 2.1
authorization server backing Empyralis's Claude Connector support.

Required coverage (per the build task):
  (a) DCR client registration
  (b) PKCE-bound authorization code issue + exchange (single-use)
  (c) access/refresh token lifecycle + revocation
  (d) legacy per-workspace bearer API key fallback still works
  (e) read-vs-write scope enforcement
  (f) cross-workspace isolation

Plus a few more security-relevant behaviors this module owns: RFC 8707
resource-indicator validation, consent-ticket tamper/expiry detection, and
POST /register rate limiting.

Most tests are parametrized over `use_pg` (False = the real SQLite fallback
this dev/CI environment actually exercises since DATABASE_URL is unset;
True = the hand-written Postgres-dialect SQL, run against an in-memory
SQLite connection via a small placeholder-translating fake pool). SQLite
accepts arbitrary Postgres-ish column type names through its type-affinity
system and supports both `RETURNING` and `ON CONFLICT ... DO UPDATE`, so this
genuinely exercises the Postgres SQL strings for syntax/shape errors that
would otherwise stay invisible until first production use against a real
Postgres — a real gap, since nothing else in this environment runs the
Postgres branch.
"""

from __future__ import annotations

import asyncio
import re
import sqlite3
import time
import uuid
from typing import Any, Optional

import pytest
from pydantic import AnyUrl

from mcp.server.auth.provider import AuthorizationParams, AuthorizeError, TokenError
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata

from server_modules import mcp_oauth_provider as oauth

ISSUER = "https://mcp-test.example"
RESOURCE = f"{ISSUER}/mcp"


def _run(coro):
    return asyncio.run(coro)


# ── Fake Postgres pool (see module docstring) ────────────────────────────


class _FakePgPool:
    _PLACEHOLDER_RE = re.compile(r"\$(\d+)")

    def __init__(self) -> None:
        # check_same_thread=False: TestClient runs the ASGI app in a
        # background thread via anyio's from_thread portal; this fake is
        # single-connection test scaffolding with no real concurrent access,
        # so relaxing sqlite3's same-thread check is safe here.
        self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._conn.row_factory = sqlite3.Row

    async def execute(self, sql: str, *params: Any) -> None:
        if not params:
            self._conn.executescript(sql)
        else:
            self._conn.execute(self._PLACEHOLDER_RE.sub("?", sql), params)
        self._conn.commit()

    async def fetchrow(self, sql: str, *params: Any) -> Optional[sqlite3.Row]:
        cursor = self._conn.execute(self._PLACEHOLDER_RE.sub("?", sql), params)
        row = cursor.fetchone()
        self._conn.commit()
        return row


@pytest.fixture(autouse=True)
def _oauth_test_env(monkeypatch, tmp_path):
    """Isolate module-level caches + give resolve_jwt_secret() a fast, stable
    path (an explicit secret skips the Rust-kernel-gated file-persistence
    path, which the shared conftest's kernel mock doesn't shape correctly for
    — a pre-existing characteristic of jwt_secret.py, not something this
    module can or should work around)."""
    monkeypatch.setenv("ORION_JWT_SECRET", "test-jwt-secret-" + "x" * 32)
    monkeypatch.setattr(oauth, "_PG_SCHEMA_READY", False, raising=False)
    monkeypatch.setattr(oauth, "_register_rate_buckets", {}, raising=False)


@pytest.fixture(params=[False, True], ids=["sqlite", "postgres_fake"])
def use_pg(request, monkeypatch):
    if request.param:
        pool = _FakePgPool()
        monkeypatch.setattr(oauth, "_pg_pool", _mk_async_return(pool))
    else:
        monkeypatch.setattr(oauth, "_pg_pool", _mk_async_return(None))
    return request.param


def _mk_async_return(value):
    async def _inner():
        return value

    return _inner


@pytest.fixture
def provider() -> oauth.EmpyralisOAuthProvider:
    return oauth.EmpyralisOAuthProvider(issuer_url=ISSUER, resource_server_url=RESOURCE)


def _client_metadata(**overrides) -> OAuthClientMetadata:
    defaults: dict[str, Any] = dict(
        redirect_uris=[AnyUrl("https://claude.ai/api/mcp/auth_callback")],
        client_name="Claude",
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="client_secret_post",
    )
    defaults.update(overrides)
    return OAuthClientMetadata(**defaults)


async def _register_client(provider: oauth.EmpyralisOAuthProvider, **overrides) -> OAuthClientInformationFull:
    metadata = _client_metadata(**overrides)
    client_info = OAuthClientInformationFull(
        client_id=str(uuid.uuid4()),
        client_id_issued_at=int(time.time()),
        client_secret="test-client-secret",
        client_secret_expires_at=None,
        **metadata.model_dump(),
    )
    await provider.register_client(client_info)
    return client_info


def _pkce_pair() -> tuple[str, str]:
    """(code_verifier, code_challenge) — S256, matching RFC 7636."""
    import base64
    import hashlib as _hashlib

    verifier = "test-code-verifier-" + "a" * 40
    digest = _hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


async def _full_grant(
    provider: oauth.EmpyralisOAuthProvider, *, client: OAuthClientInformationFull, workspace_id: str = "ws-alpha",
    tenant_id: str = "tenant-alpha", user_id: str = "user-1", scopes: Optional[list[str]] = None,
):
    """Simulate an approved consent: store a code directly (bypassing the
    HTTP consent screen, which has its own dedicated tests below) and return
    (code, code_verifier)."""
    verifier, challenge = _pkce_pair()
    code = oauth._new_opaque_token()
    await oauth._store_authorization_code(
        code,
        client_id=client.client_id,
        code_challenge=challenge,
        redirect_uri=str(client.redirect_uris[0]),
        redirect_uri_explicit=True,
        scopes=list(scopes or [oauth.SCOPE_READ]),
        resource=RESOURCE,
        workspace_id=workspace_id,
        tenant_id=tenant_id,
        user_id=user_id,
        expires_at=time.time() + 120,
    )
    return code, verifier


# ── (a) Dynamic client registration ──────────────────────────────────────


def test_dynamic_client_registration_round_trip(provider, use_pg):
    async def _run_test():
        client_info = await _register_client(provider)
        loaded = await provider.get_client(client_info.client_id)
        assert loaded is not None
        assert loaded.client_id == client_info.client_id
        assert loaded.client_name == "Claude"
        assert loaded.redirect_uris == client_info.redirect_uris
        # client_secret must round-trip too — the token endpoint needs it for
        # client_secret_post auth — but only the hash is what's queryable
        # separately (never logged); confirm we don't need to log it to assert this.
        assert loaded.client_secret == "test-client-secret"

    _run(_run_test())


def test_get_client_unknown_returns_none(provider, use_pg):
    assert _run(provider.get_client("does-not-exist")) is None


def test_register_client_without_client_id_rejected(provider):
    from mcp.server.auth.provider import RegistrationError

    bad = OAuthClientInformationFull(
        client_id=None, redirect_uris=[AnyUrl("https://claude.ai/callback")],
    )
    with pytest.raises(RegistrationError):
        _run(provider.register_client(bad))


# ── Authorization step / resource validation / consent ticket ───────────


def test_authorize_rejects_mismatched_resource(provider, use_pg):
    async def _run_test():
        client = await _register_client(provider)
        params = AuthorizationParams(
            state="xyz", scopes=[oauth.SCOPE_READ], code_challenge="abc",
            redirect_uri=client.redirect_uris[0], redirect_uri_provided_explicitly=True,
            resource="https://not-this-server.example/mcp",
        )
        with pytest.raises(AuthorizeError):
            await provider.authorize(client, params)

    _run(_run_test())


def test_authorize_returns_consent_ticket_url(provider, use_pg):
    async def _run_test():
        client = await _register_client(provider)
        params = AuthorizationParams(
            state="xyz", scopes=[oauth.SCOPE_READ], code_challenge="challenge-value",
            redirect_uri=client.redirect_uris[0], redirect_uri_provided_explicitly=True, resource=RESOURCE,
        )
        redirect = await provider.authorize(client, params)
        assert redirect.startswith(f"{ISSUER}{oauth.CONSENT_PATH}?ticket=")
        ticket = redirect.split("ticket=", 1)[1]
        payload = oauth._verify_consent_ticket(ticket)
        assert payload["client_id"] == client.client_id
        assert payload["state"] == "xyz"
        assert payload["scopes"] == [oauth.SCOPE_READ]

    _run(_run_test())


def test_consent_ticket_tamper_detected():
    ticket = oauth._sign_consent_ticket({"client_id": "c1", "state": None})
    body_part, sig_part = ticket.split(".", 1)
    # Flip the signature — any single-char corruption must fail verification.
    corrupted = body_part + "." + ("A" if sig_part[0] != "A" else "B") + sig_part[1:]
    with pytest.raises(oauth._ConsentTicketError):
        oauth._verify_consent_ticket(corrupted)


def test_consent_ticket_expiry_detected(monkeypatch):
    monkeypatch.setattr(oauth, "_CONSENT_TICKET_TTL_SECONDS", -1)  # already expired the instant it's signed
    ticket = oauth._sign_consent_ticket({"client_id": "c1", "state": None})
    with pytest.raises(oauth._ConsentTicketError):
        oauth._verify_consent_ticket(ticket)


# ── (b) PKCE-bound authorization codes, single-use ───────────────────────


def test_authorization_code_round_trips_pkce_challenge(provider, use_pg):
    async def _run_test():
        client = await _register_client(provider)
        code, _verifier = await _full_grant(provider, client=client)
        loaded = await provider.load_authorization_code(client, code)
        assert loaded is not None
        assert loaded.client_id == client.client_id
        assert loaded.workspace_id == "ws-alpha"
        assert len(loaded.code_challenge) > 0

    _run(_run_test())


def test_authorization_code_wrong_client_rejected(provider, use_pg):
    async def _run_test():
        client_a = await _register_client(provider)
        client_b = await _register_client(provider)
        code, _verifier = await _full_grant(provider, client=client_a)
        assert await provider.load_authorization_code(client_b, code) is None

    _run(_run_test())


def test_authorization_code_expired_rejected(provider, use_pg):
    async def _run_test():
        client = await _register_client(provider)
        _verifier, challenge = _pkce_pair()
        code = oauth._new_opaque_token()
        await oauth._store_authorization_code(
            code, client_id=client.client_id, code_challenge=challenge,
            redirect_uri=str(client.redirect_uris[0]), redirect_uri_explicit=True,
            scopes=[oauth.SCOPE_READ], resource=RESOURCE, workspace_id="ws-1", tenant_id="t1", user_id="u1",
            expires_at=time.time() - 5,  # already expired
        )
        assert await provider.load_authorization_code(client, code) is None

    _run(_run_test())


def test_authorization_code_is_single_use(provider, use_pg):
    async def _run_test():
        client = await _register_client(provider)
        code, _verifier = await _full_grant(provider, client=client)
        loaded = await provider.load_authorization_code(client, code)
        tokens = await provider.exchange_authorization_code(client, loaded)
        assert tokens.access_token

        # Re-loading (let alone re-exchanging) the same code must fail now —
        # single-use, even though it hasn't hit its TTL yet.
        assert await provider.load_authorization_code(client, code) is None
        with pytest.raises(TokenError):
            await provider.exchange_authorization_code(client, loaded)

    _run(_run_test())


def test_authorization_code_concurrent_exchange_only_succeeds_once(provider, use_pg):
    """Two 'simultaneous' exchange attempts for the same code (e.g. a client
    double-submit / replay) — only one may mint tokens."""

    async def _run_test():
        client = await _register_client(provider)
        code, _verifier = await _full_grant(provider, client=client)
        loaded = await provider.load_authorization_code(client, code)

        results = await asyncio.gather(
            provider.exchange_authorization_code(client, loaded),
            provider.exchange_authorization_code(client, loaded),
            return_exceptions=True,
        )
        successes = [r for r in results if not isinstance(r, Exception)]
        failures = [r for r in results if isinstance(r, TokenError)]
        assert len(successes) == 1
        assert len(failures) == 1

    _run(_run_test())


# ── (c) Access / refresh token lifecycle + revocation ────────────────────


def test_access_token_lifecycle_and_scopes(provider, use_pg):
    async def _run_test():
        client = await _register_client(provider)
        code, _verifier = await _full_grant(provider, client=client, scopes=[oauth.SCOPE_READ, oauth.SCOPE_WRITE])
        auth_code = await provider.load_authorization_code(client, code)
        tokens = await provider.exchange_authorization_code(client, auth_code)

        loaded = await provider.load_access_token(tokens.access_token)
        assert loaded is not None
        assert loaded.workspace_id == "ws-alpha"
        assert set(loaded.scopes) == {oauth.SCOPE_READ, oauth.SCOPE_WRITE}
        assert loaded.client_id == client.client_id
        assert loaded.expires_at is not None and loaded.expires_at > time.time()

        # A bogus token must not resolve to anything.
        assert await provider.load_access_token("not-a-real-token") is None

    _run(_run_test())


def test_refresh_token_rotation(provider, use_pg):
    async def _run_test():
        client = await _register_client(provider)
        code, _verifier = await _full_grant(provider, client=client)
        auth_code = await provider.load_authorization_code(client, code)
        tokens = await provider.exchange_authorization_code(client, auth_code)

        refresh = await provider.load_refresh_token(client, tokens.refresh_token)
        assert refresh is not None

        rotated = await provider.exchange_refresh_token(client, refresh, [oauth.SCOPE_READ])
        assert rotated.access_token != tokens.access_token
        assert rotated.refresh_token != tokens.refresh_token

        # The OLD refresh token must be dead — rotation, not duplication.
        assert await provider.load_refresh_token(client, tokens.refresh_token) is None
        # The NEW refresh token works.
        assert await provider.load_refresh_token(client, rotated.refresh_token) is not None

        # Re-using the already-rotated (old) refresh token for another
        # exchange must fail (it's already consumed).
        with pytest.raises(TokenError):
            await provider.exchange_refresh_token(client, refresh, [oauth.SCOPE_READ])

    _run(_run_test())


def test_revoke_access_token_cross_revokes_refresh(provider, use_pg):
    async def _run_test():
        client = await _register_client(provider)
        code, _verifier = await _full_grant(provider, client=client)
        auth_code = await provider.load_authorization_code(client, code)
        tokens = await provider.exchange_authorization_code(client, auth_code)

        access = await provider.load_access_token(tokens.access_token)
        await provider.revoke_token(access)

        assert await provider.load_access_token(tokens.access_token) is None
        assert await provider.load_refresh_token(client, tokens.refresh_token) is None

    _run(_run_test())


def test_revoke_refresh_token_cross_revokes_access(provider, use_pg):
    async def _run_test():
        client = await _register_client(provider)
        code, _verifier = await _full_grant(provider, client=client)
        auth_code = await provider.load_authorization_code(client, code)
        tokens = await provider.exchange_authorization_code(client, auth_code)

        refresh = await provider.load_refresh_token(client, tokens.refresh_token)
        await provider.revoke_token(refresh)

        assert await provider.load_refresh_token(client, tokens.refresh_token) is None
        assert await provider.load_access_token(tokens.access_token) is None

    _run(_run_test())


def test_revoke_is_a_noop_for_unknown_token(provider, use_pg):
    """Per the Protocol docstring: revoking an invalid/already-revoked token
    should do nothing, not raise."""

    async def _run_test():
        fake = oauth.EmpyralisAccessToken(
            token="never-issued", client_id="whoever", scopes=[oauth.SCOPE_READ], workspace_id="ws-1",
        )
        await provider.revoke_token(fake)  # must not raise

    _run(_run_test())


# ── (d) Legacy per-workspace bearer API key fallback ─────────────────────


def test_legacy_bearer_key_fallback_still_works(provider, monkeypatch, tmp_path):
    async def _run_test():
        from server_modules import mcp_server_auth

        monkeypatch.setattr(mcp_server_auth, "_MCP_API_KEYS_FILE", tmp_path / "mcp_api_keys.json", raising=False)
        monkeypatch.setattr(mcp_server_auth, "_KEYS_CACHE", None, raising=False)

        created = await mcp_server_auth.create_workspace_mcp_api_key(
            workspace_id="ws-legacy", label="Claude Code CLI", writes_enabled=True,
        )
        assert created["ok"]

        loaded = await provider.load_access_token(created["key"])
        assert loaded is not None
        assert loaded.client_id == oauth.LEGACY_CLIENT_ID
        assert loaded.workspace_id == "ws-legacy"
        assert set(loaded.scopes) == {oauth.SCOPE_READ, oauth.SCOPE_WRITE}
        assert loaded.expires_at is None  # legacy keys don't expire on a TTL, only by revocation

        # Also accepts the raw key when it happens to be presented with a
        # "Bearer " prefix stripped upstream by the SDK's BearerAuthBackend
        # (which always strips it before calling load_access_token) — so a
        # plain key is exactly what this function should expect.
        revoke_result = await mcp_server_auth.revoke_workspace_mcp_api_key(created["key_id"])
        assert revoke_result["ok"]
        assert await provider.load_access_token(created["key"]) is None

    _run(_run_test())


def test_legacy_read_only_key_gets_read_scope_only(provider, monkeypatch, tmp_path):
    async def _run_test():
        from server_modules import mcp_server_auth

        monkeypatch.setattr(mcp_server_auth, "_MCP_API_KEYS_FILE", tmp_path / "mcp_api_keys2.json", raising=False)
        monkeypatch.setattr(mcp_server_auth, "_KEYS_CACHE", None, raising=False)

        created = await mcp_server_auth.create_workspace_mcp_api_key(workspace_id="ws-ro", label="read only")
        loaded = await provider.load_access_token(created["key"])
        assert loaded is not None
        assert list(loaded.scopes) == [oauth.SCOPE_READ]

    _run(_run_test())


def test_revoke_token_is_noop_for_legacy_client(provider):
    """Legacy bearer keys have their own revocation path
    (mcp_server_auth.revoke_workspace_mcp_api_key) — revoke_token() must not
    try (and fail) to find them in the OAuth tables."""

    async def _run_test():
        legacy_token = oauth.EmpyralisAccessToken(
            token="whatever", client_id=oauth.LEGACY_CLIENT_ID, scopes=[oauth.SCOPE_READ], workspace_id="ws-1",
        )
        await provider.revoke_token(legacy_token)  # must not raise

    _run(_run_test())


# ── (e) Read-vs-write scope enforcement (integration with mcp_server) ────


def test_resolve_workspace_write_scope_enables_writes(monkeypatch):
    _assert_resolve_workspace_scopes(
        monkeypatch, scopes=[oauth.SCOPE_READ, oauth.SCOPE_WRITE], expect_writes_enabled=True,
    )


def test_resolve_workspace_read_only_scope_blocks_writes(monkeypatch):
    _assert_resolve_workspace_scopes(monkeypatch, scopes=[oauth.SCOPE_READ], expect_writes_enabled=False)


def _assert_resolve_workspace_scopes(monkeypatch, *, scopes: list[str], expect_writes_enabled: bool) -> None:
    import mcp_server
    from mcp.server.auth.middleware.auth_context import auth_context_var
    from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser

    access_token = oauth.EmpyralisAccessToken(
        token="tok", client_id="claude", scopes=scopes, workspace_id="ws-scoped",
    )
    token_ctx = auth_context_var.set(AuthenticatedUser(access_token))
    try:
        resolved = _run(mcp_server._resolve_workspace(ctx=None))
    finally:
        auth_context_var.reset(token_ctx)

    assert resolved["workspace_id"] == "ws-scoped"
    assert resolved["writes_enabled"] is expect_writes_enabled

    # And _check_write() — the actual gate every write tool calls — agrees,
    # as long as the global off-switch is on (mirrors production config with
    # EMPYRALIS_MCP_WRITE_ENABLED=true; the off-switch itself is covered by
    # test_mcp_server.py's test_write_flag_defaults_false_no_env).
    monkeypatch.setattr(mcp_server, "_WRITE_ENABLED_GLOBAL", True)
    if expect_writes_enabled:
        mcp_server._check_write(resolved)  # must not raise
    else:
        with pytest.raises(RuntimeError):
            mcp_server._check_write(resolved)


def test_resolve_workspace_falls_back_to_header_when_no_access_token(monkeypatch):
    """When no auth_server_provider is wired in (EMPYRALIS_MCP_OAUTH_ENABLED
    unset), get_access_token() is always None and the original
    header-parsing path must still work unchanged."""
    import mcp_server

    class _FakeCtx:
        class request_context:
            class request:
                scope = {"headers": [(b"authorization", b"Bearer not-a-real-key")]}

    with pytest.raises(RuntimeError, match="Invalid or revoked"):
        _run(mcp_server._resolve_workspace(_FakeCtx()))


# ── (f) Cross-workspace isolation ─────────────────────────────────────────


def test_cross_workspace_isolation(provider, use_pg):
    async def _run_test():
        client = await _register_client(provider)
        code_a, _va = await _full_grant(provider, client=client, workspace_id="ws-A", tenant_id="tenant-A")
        code_b, _vb = await _full_grant(provider, client=client, workspace_id="ws-B", tenant_id="tenant-B")

        auth_a = await provider.load_authorization_code(client, code_a)
        auth_b = await provider.load_authorization_code(client, code_b)
        tokens_a = await provider.exchange_authorization_code(client, auth_a)
        tokens_b = await provider.exchange_authorization_code(client, auth_b)

        loaded_a = await provider.load_access_token(tokens_a.access_token)
        loaded_b = await provider.load_access_token(tokens_b.access_token)
        assert loaded_a.workspace_id == "ws-A"
        assert loaded_b.workspace_id == "ws-B"

        # Token A must never resolve to workspace B's data and vice versa —
        # the workspace is a property of the verified token row, not
        # something a caller can influence.
        assert loaded_a.workspace_id != loaded_b.workspace_id
        assert tokens_a.access_token != tokens_b.access_token

        # Revoking workspace A's token must not touch workspace B's.
        await provider.revoke_token(loaded_a)
        assert await provider.load_access_token(tokens_a.access_token) is None
        assert await provider.load_access_token(tokens_b.access_token) is not None

    _run(_run_test())


def test_cross_workspace_isolation_via_resolve_workspace(monkeypatch):
    """Same guarantee, exercised through the exact function every MCP tool
    call goes through."""
    import mcp_server
    from mcp.server.auth.middleware.auth_context import auth_context_var
    from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser

    for workspace_id in ("ws-alpha", "ws-beta"):
        token = oauth.EmpyralisAccessToken(
            token=f"tok-{workspace_id}", client_id="claude", scopes=[oauth.SCOPE_READ], workspace_id=workspace_id,
        )
        ctx_token = auth_context_var.set(AuthenticatedUser(token))
        try:
            resolved = _run(mcp_server._resolve_workspace(ctx=None))
        finally:
            auth_context_var.reset(ctx_token)
        assert resolved["workspace_id"] == workspace_id


# ── Rate limiting on POST /register ───────────────────────────────────────


def test_register_rate_limit_blocks_after_threshold(monkeypatch):
    monkeypatch.setattr(oauth, "_REGISTER_RATE_LIMIT_PER_HOUR", 3)
    ip = "203.0.113.9"
    assert oauth._check_register_rate_limit(ip) is True
    assert oauth._check_register_rate_limit(ip) is True
    assert oauth._check_register_rate_limit(ip) is True
    assert oauth._check_register_rate_limit(ip) is False  # 4th within the window is blocked


def test_register_rate_limit_is_per_ip(monkeypatch):
    monkeypatch.setattr(oauth, "_REGISTER_RATE_LIMIT_PER_HOUR", 1)
    assert oauth._check_register_rate_limit("203.0.113.10") is True
    assert oauth._check_register_rate_limit("203.0.113.10") is False
    # A different IP has its own budget.
    assert oauth._check_register_rate_limit("203.0.113.11") is True


# ── Consent HTTP flow (GET/POST /mcp/consent) ─────────────────────────────


def _consent_app(provider):
    from fastapi import FastAPI

    app = FastAPI()
    oauth.register_consent_routes(app, provider)
    return app


def test_consent_get_not_logged_in_redirects_to_login(provider, monkeypatch):
    from starlette.testclient import TestClient

    monkeypatch.setattr(oauth, "_current_dashboard_user", lambda request: None)
    client_info = _run(_register_client(provider))
    ticket = oauth._sign_consent_ticket(
        {
            "client_id": client_info.client_id, "redirect_uri": str(client_info.redirect_uris[0]),
            "redirect_uri_explicit": True, "code_challenge": "cc", "scopes": [oauth.SCOPE_READ],
            "state": None, "resource": RESOURCE,
        }
    )
    app = _consent_app(provider)
    with TestClient(app) as tc:
        response = tc.get(f"{oauth.CONSENT_PATH}?ticket={ticket}", follow_redirects=False)
    assert response.status_code == 302
    assert "/login" in response.headers["location"]
    assert "next=" in response.headers["location"]


def test_consent_get_logged_in_renders_allow_deny_form(provider, monkeypatch):
    from starlette.testclient import TestClient

    monkeypatch.setattr(
        oauth, "_current_dashboard_user", lambda request: {"user_id": "u1", "workspace_ids": ["ws-1"]},
    )
    client_info = _run(_register_client(provider))
    ticket = oauth._sign_consent_ticket(
        {
            "client_id": client_info.client_id, "redirect_uri": str(client_info.redirect_uris[0]),
            "redirect_uri_explicit": True, "code_challenge": "cc", "scopes": [oauth.SCOPE_READ],
            "state": None, "resource": RESOURCE,
        }
    )
    app = _consent_app(provider)
    with TestClient(app) as tc:
        response = tc.get(f"{oauth.CONSENT_PATH}?ticket={ticket}")
    assert response.status_code == 200
    assert "Allow" in response.text
    assert "Deny" in response.text
    assert "ws-1" in response.text
    assert oauth._CONSENT_CSRF_COOKIE in response.cookies


def test_consent_post_allow_issues_code_and_redirects(provider, monkeypatch, use_pg):
    from starlette.testclient import TestClient

    monkeypatch.setattr(
        oauth, "_current_dashboard_user", lambda request: {"user_id": "u1", "workspace_ids": ["ws-1"]},
    )
    client_info = _run(_register_client(provider))
    redirect_uri = str(client_info.redirect_uris[0])
    _verifier, challenge = _pkce_pair()
    ticket = oauth._sign_consent_ticket(
        {
            "client_id": client_info.client_id, "redirect_uri": redirect_uri, "redirect_uri_explicit": True,
            "code_challenge": challenge, "scopes": [oauth.SCOPE_READ], "state": "state123", "resource": RESOURCE,
        }
    )
    app = _consent_app(provider)
    with TestClient(app) as tc:
        get_response = tc.get(f"{oauth.CONSENT_PATH}?ticket={ticket}")
        csrf_cookie = get_response.cookies[oauth._CONSENT_CSRF_COOKIE]

        post_response = tc.post(
            oauth.CONSENT_PATH,
            data={"ticket": ticket, "csrf_token": csrf_cookie, "decision": "allow", "workspace_id": "ws-1"},
            follow_redirects=False,
        )
    assert post_response.status_code == 302
    location = post_response.headers["location"]
    assert location.startswith(redirect_uri)
    assert "code=" in location
    assert "state=state123" in location


def test_consent_post_deny_redirects_with_access_denied(provider, monkeypatch):
    from starlette.testclient import TestClient

    monkeypatch.setattr(
        oauth, "_current_dashboard_user", lambda request: {"user_id": "u1", "workspace_ids": ["ws-1"]},
    )
    client_info = _run(_register_client(provider))
    redirect_uri = str(client_info.redirect_uris[0])
    ticket = oauth._sign_consent_ticket(
        {
            "client_id": client_info.client_id, "redirect_uri": redirect_uri, "redirect_uri_explicit": True,
            "code_challenge": "cc", "scopes": [oauth.SCOPE_READ], "state": "s1", "resource": RESOURCE,
        }
    )
    app = _consent_app(provider)
    with TestClient(app) as tc:
        get_response = tc.get(f"{oauth.CONSENT_PATH}?ticket={ticket}")
        csrf_cookie = get_response.cookies[oauth._CONSENT_CSRF_COOKIE]
        post_response = tc.post(
            oauth.CONSENT_PATH,
            data={"ticket": ticket, "csrf_token": csrf_cookie, "decision": "deny", "workspace_id": "ws-1"},
            follow_redirects=False,
        )
    assert post_response.status_code == 302
    location = post_response.headers["location"]
    assert location.startswith(redirect_uri)
    assert "error=access_denied" in location


def test_consent_post_csrf_mismatch_rejected(provider, monkeypatch):
    from starlette.testclient import TestClient

    monkeypatch.setattr(
        oauth, "_current_dashboard_user", lambda request: {"user_id": "u1", "workspace_ids": ["ws-1"]},
    )
    client_info = _run(_register_client(provider))
    ticket = oauth._sign_consent_ticket(
        {
            "client_id": client_info.client_id, "redirect_uri": str(client_info.redirect_uris[0]),
            "redirect_uri_explicit": True, "code_challenge": "cc", "scopes": [oauth.SCOPE_READ],
            "state": None, "resource": RESOURCE,
        }
    )
    app = _consent_app(provider)
    with TestClient(app) as tc:
        tc.get(f"{oauth.CONSENT_PATH}?ticket={ticket}")
        # Deliberately send a csrf_token that does NOT match the cookie.
        response = tc.post(
            oauth.CONSENT_PATH,
            data={"ticket": ticket, "csrf_token": "wrong-value", "decision": "allow", "workspace_id": "ws-1"},
        )
    assert response.status_code == 400


def test_consent_post_workspace_not_in_membership_rejected(provider, monkeypatch):
    from starlette.testclient import TestClient

    monkeypatch.setattr(
        oauth, "_current_dashboard_user", lambda request: {"user_id": "u1", "workspace_ids": ["ws-1"]},
    )
    client_info = _run(_register_client(provider))
    ticket = oauth._sign_consent_ticket(
        {
            "client_id": client_info.client_id, "redirect_uri": str(client_info.redirect_uris[0]),
            "redirect_uri_explicit": True, "code_challenge": "cc", "scopes": [oauth.SCOPE_READ],
            "state": None, "resource": RESOURCE,
        }
    )
    app = _consent_app(provider)
    with TestClient(app) as tc:
        get_response = tc.get(f"{oauth.CONSENT_PATH}?ticket={ticket}")
        csrf_cookie = get_response.cookies[oauth._CONSENT_CSRF_COOKIE]
        # workspace-not-mine: user only belongs to ws-1, tries to grant ws-999.
        response = tc.post(
            oauth.CONSENT_PATH,
            data={"ticket": ticket, "csrf_token": csrf_cookie, "decision": "allow", "workspace_id": "ws-999"},
        )
    assert response.status_code == 403

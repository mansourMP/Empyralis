"""End-to-end tests for the MCP OAuth Connector flow, driven over the REAL
assembled app.

WHY THIS FILE EXISTS SEPARATELY FROM ``test_mcp_oauth_provider.py``
-------------------------------------------------------------------
That file has 33 passing tests over this code and caught NEITHER of the two
defects that made the Connector flow impossible to complete, for two
structural reasons this file is built to avoid:

1. Its consent tests build a bare ``FastAPI()`` and register only the consent
   routes on it. The production app also carries ``Mount("/mcp")``, registered
   BEFORE them — and a Starlette Mount matches by PREFIX, so ``/mcp/consent``
   was swallowed by the protocol sub-app and answered a plain-text 404. A test
   that never assembles the mount cannot see a mount-ordering bug at all.
   → every test here goes through ``mcp_server.mount_empyralist_mcp``.

2. They monkeypatch ``_current_dashboard_user``, so the real session
   resolution — and the CSRF check inside it — never runs. A browser cannot
   put an ``x-csrf-token`` HEADER on a plain ``<form method="post">``, so a
   genuinely logged-in operator clicking "Allow" was 403'd and bounced to
   /login.
   → nothing here patches the session resolver. A REAL signed session cookie
   is minted and the real ``auth.get_current_user`` runs, CSRF and all.

CLAUDE.md, "a mock protects a seam, not a path".
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import re
import sqlite3
import time
from typing import Any, Optional

import pytest
from pydantic import AnyUrl

from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata

from server_modules import mcp_oauth_provider as oauth

ISSUER = "https://mcp-test.example"
RESOURCE = f"{ISSUER}/mcp"


def _run(coro):
    return asyncio.run(coro)


# ── Storage doubles (same shape as test_mcp_oauth_provider.py) ────────────


class _FakePgPool:
    _PLACEHOLDER_RE = re.compile(r"\$(\d+)")

    def __init__(self) -> None:
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


@pytest.fixture
def pool(monkeypatch):
    """A single in-memory store shared by the app and the test body, so the
    test can read back exactly what the production code wrote."""
    fake = _FakePgPool()

    async def _get_pool():
        return fake

    monkeypatch.setattr(oauth, "_pg_pool", _get_pool)
    monkeypatch.setattr(oauth, "_PG_SCHEMA_READY", False, raising=False)
    return fake


@pytest.fixture(autouse=True)
def _oauth_env(monkeypatch):
    monkeypatch.setenv("ORION_JWT_SECRET", "test-jwt-secret-" + "x" * 32)
    monkeypatch.setenv("EMPYRALIS_PUBLIC_BASE_URL", ISSUER)
    monkeypatch.setattr(oauth, "_register_rate_buckets", {}, raising=False)


# ── The real assembled app ───────────────────────────────────────────────


@pytest.fixture
def assembled(monkeypatch):
    """Build the app the way production does: ``mount_empyralist_mcp`` on a
    FastAPI instance, with a real ``EmpyralisOAuthProvider`` wired in through
    ``_build_mcp_server`` — the same function that decides route order.

    Deliberately does NOT reload ``mcp_server``: reloading it would swap the
    module object other tests in the same process already hold references to
    (CLAUDE.md records that exact class of cross-test damage). Instead the two
    module globals ``mount_empyralist_mcp`` actually reads are monkeypatched,
    so the code under test is the shipped code.
    """
    from fastapi import FastAPI

    import mcp_server

    monkeypatch.setattr(mcp_server, "oauth_provider", None, raising=False)
    monkeypatch.setattr(mcp_server, "_MCP_OAUTH_ENABLED", True, raising=False)

    built = mcp_server._build_mcp_server()
    assert built is not None, "FastMCP unavailable — the MCP SDK is not installed."
    assert mcp_server.oauth_provider is not None, (
        "_build_mcp_server did not wire an OAuth provider; the rest of this "
        "file would be testing the legacy-only mount."
    )
    monkeypatch.setattr(mcp_server, "empyralist_mcp", built, raising=False)

    app = FastAPI()
    mcp_server.mount_empyralist_mcp(app)
    return app, mcp_server.oauth_provider


# ── Real dashboard session (NOT a monkeypatched resolver) ─────────────────


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _mint_real_session_token(user_id: str, workspace_ids: list[str]) -> str:
    """A genuinely signed access token the real ``auth._decode_token_payload``
    and ``auth._validated_bearer_context`` accept. No ``sid``, so no session
    row is needed; every other claim is validated for real."""
    from server_modules import auth

    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode("utf-8"))
    payload = _b64(
        json.dumps(
            {
                "sub": user_id,
                "email": f"{user_id}@example.test",
                "workspace_ids": workspace_ids,
                "exp": int(time.time()) + 3600,
            }
        ).encode("utf-8")
    )
    signature = hmac.new(
        auth._jwt_secret().encode("utf-8"), f"{header}.{payload}".encode("utf-8"), hashlib.sha256,
    ).digest()
    return f"{header}.{payload}.{_b64(signature)}"


def _login(test_client, user_id: str = "u-connector-1", workspace_ids=("ws-connector-1",)) -> None:
    from server_modules import auth

    test_client.cookies.set(auth.AUTH_ACCESS_COOKIE_NAME, _mint_real_session_token(user_id, list(workspace_ids)))
    # The dashboard's own CSRF cookie IS present, exactly as it is in a real
    # browser. What a browser cannot do is echo it back in a custom HEADER on
    # a plain form POST — which is the whole defect under test.
    test_client.cookies.set(auth.AUTH_CSRF_COOKIE_NAME, "dashboard-csrf-cookie-value")


# ── Fixtures for a registered client + a signed consent ticket ────────────


def _register_client(provider, *, client_id="client-connector-1", secret="s3cret-plaintext-value"):
    metadata = OAuthClientMetadata(
        redirect_uris=[AnyUrl("https://claude.ai/api/mcp/auth_callback")],
        client_name="Claude",
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="client_secret_post",
        scope=oauth.SCOPE_READ,
    )
    info = OAuthClientInformationFull(
        client_id=client_id,
        client_secret=secret,
        client_secret_expires_at=None,
        client_id_issued_at=int(time.time()),
        **metadata.model_dump(),
    )
    _run(provider.register_client(info))
    return info


def _ticket(client_info, *, scopes=None, code_challenge="cc", state=None):
    return oauth._sign_consent_ticket(
        {
            "client_id": client_info.client_id,
            "redirect_uri": str(client_info.redirect_uris[0]),
            "redirect_uri_explicit": True,
            "code_challenge": code_challenge,
            "scopes": list(scopes or [oauth.SCOPE_READ]),
            "state": state,
            "resource": RESOURCE,
        }
    )


# ═════════════════════════════════════════════════════════════════════════
# Blocker 1 — the consent page must be reachable on the assembled app
# ═════════════════════════════════════════════════════════════════════════


def test_consent_route_is_registered_before_the_mcp_mount(assembled):
    """Structural half. A Mount matches by PREFIX, so if /mcp is registered
    first it swallows /mcp/consent and no React/Starlette code anywhere says
    so — the request just 404s from the protocol sub-app."""
    app, _provider = assembled
    paths = [(getattr(r, "path", None), type(r).__name__) for r in app.router.routes]
    consent_idx = [i for i, (path, _kind) in enumerate(paths) if path == oauth.CONSENT_PATH]
    mount_idx = [i for i, (path, kind) in enumerate(paths) if path == "/mcp" and kind == "Mount"]
    assert consent_idx, f"{oauth.CONSENT_PATH} is not registered at all: {paths}"
    assert mount_idx, f"the /mcp Mount is missing: {paths}"
    assert max(consent_idx) < min(mount_idx), (
        "the /mcp Mount is registered before the consent routes, so it swallows "
        f"{oauth.CONSENT_PATH} by prefix: {paths}"
    )


def test_consent_page_answers_on_the_assembled_app(assembled, pool):
    """Behavioural half — the one an audit driving real HTTP would hit."""
    from starlette.testclient import TestClient

    app, provider = assembled
    client_info = _register_client(provider)
    with TestClient(app) as tc:
        _login(tc)
        response = tc.get(f"{oauth.CONSENT_PATH}?ticket={_ticket(client_info)}", follow_redirects=False)
    assert response.status_code == 200, (
        f"expected the consent screen, got {response.status_code}: {response.text[:200]!r}"
    )
    assert "Allow" in response.text and "Deny" in response.text


def test_authorize_redirect_target_is_actually_servable(assembled, pool):
    """/authorize's 302 target must be a page that exists. It pointed at a
    404 for the whole life of this feature."""
    from starlette.testclient import TestClient

    from mcp.server.auth.provider import AuthorizationParams

    app, provider = assembled
    client_info = _register_client(provider)
    target = _run(
        provider.authorize(
            client_info,
            AuthorizationParams(
                state="st",
                scopes=[oauth.SCOPE_READ],
                code_challenge="cc",
                redirect_uri=client_info.redirect_uris[0],
                redirect_uri_provided_explicitly=True,
                resource=RESOURCE,
            ),
        )
    )
    assert oauth.CONSENT_PATH in target
    with TestClient(app) as tc:
        _login(tc)
        response = tc.get(target.replace(ISSUER, ""), follow_redirects=False)
    assert response.status_code == 200, f"/authorize points at a dead page: {response.status_code}"


# ═════════════════════════════════════════════════════════════════════════
# Blocker 2 — "Allow" must work from a real browser form (no custom header)
# ═════════════════════════════════════════════════════════════════════════


def test_allow_from_a_plain_form_post_mints_a_code(assembled, pool):
    """No ``x-csrf-token`` header anywhere — a browser form cannot send one.
    The real ``auth.get_current_user`` runs; only the page's own double-submit
    token protects this POST, and it is present."""
    from starlette.testclient import TestClient

    app, provider = assembled
    client_info = _register_client(provider)
    ticket = _ticket(client_info, state="state123")
    redirect_uri = str(client_info.redirect_uris[0])

    with TestClient(app) as tc:
        _login(tc)
        page = tc.get(f"{oauth.CONSENT_PATH}?ticket={ticket}")
        assert page.status_code == 200
        form_csrf = page.cookies[oauth._CONSENT_CSRF_COOKIE]

        response = tc.post(
            oauth.CONSENT_PATH,
            data={
                "ticket": ticket,
                "csrf_token": form_csrf,
                "decision": "allow",
                "workspace_id": "ws-connector-1",
            },
            follow_redirects=False,
        )

    assert response.status_code == 302, (
        f"Allow did not mint a code: {response.status_code} {response.text[:300]!r}"
    )
    location = response.headers["location"]
    assert location.startswith(redirect_uri), location
    assert "code=" in location and "state=state123" in location
    assert "/login" not in location, "a logged-in operator was bounced to /login"


def test_allow_still_refuses_without_the_pages_own_csrf_token(assembled, pool):
    """The double-submit token is the protection that REPLACES the header
    check for this handler. It must still be enforced, or the fix above is a
    CSRF hole."""
    from starlette.testclient import TestClient

    app, provider = assembled
    client_info = _register_client(provider)
    ticket = _ticket(client_info)

    with TestClient(app) as tc:
        _login(tc)
        tc.get(f"{oauth.CONSENT_PATH}?ticket={ticket}")
        missing = tc.post(
            oauth.CONSENT_PATH,
            data={"ticket": ticket, "decision": "allow", "workspace_id": "ws-connector-1"},
            follow_redirects=False,
        )
        wrong = tc.post(
            oauth.CONSENT_PATH,
            data={
                "ticket": ticket, "csrf_token": "not-the-token",
                "decision": "allow", "workspace_id": "ws-connector-1",
            },
            follow_redirects=False,
        )
    assert missing.status_code == 400
    assert wrong.status_code == 400


def test_allow_refuses_a_workspace_the_session_does_not_hold(assembled, pool):
    """The workspace is fixed from the AUTHENTICATED session's membership, not
    from the form field. Now that the session resolves for real on POST, this
    is the assertion that proves the membership check runs against it."""
    from starlette.testclient import TestClient

    app, provider = assembled
    client_info = _register_client(provider)
    ticket = _ticket(client_info)

    with TestClient(app) as tc:
        _login(tc, workspace_ids=("ws-connector-1",))
        page = tc.get(f"{oauth.CONSENT_PATH}?ticket={ticket}")
        response = tc.post(
            oauth.CONSENT_PATH,
            data={
                "ticket": ticket,
                "csrf_token": page.cookies[oauth._CONSENT_CSRF_COOKIE],
                "decision": "allow",
                "workspace_id": "ws-somebody-elses",
            },
            follow_redirects=False,
        )
    assert response.status_code == 403


def test_deny_from_a_plain_form_post_reaches_the_client(assembled, pool):
    from starlette.testclient import TestClient

    app, provider = assembled
    client_info = _register_client(provider)
    ticket = _ticket(client_info)

    with TestClient(app) as tc:
        _login(tc)
        page = tc.get(f"{oauth.CONSENT_PATH}?ticket={ticket}")
        response = tc.post(
            oauth.CONSENT_PATH,
            data={
                "ticket": ticket,
                "csrf_token": page.cookies[oauth._CONSENT_CSRF_COOKIE],
                "decision": "deny",
                "workspace_id": "ws-connector-1",
            },
            follow_redirects=False,
        )
    assert response.status_code == 302
    assert "error=access_denied" in response.headers["location"]


def test_signed_out_post_can_get_back_to_the_same_connection(assembled, pool):
    """Not logged in on POST is a real state. It must say so in a way the
    person can act on — carrying the ticket through login — rather than
    stranding them on a bare /login with the connection lost."""
    from starlette.testclient import TestClient

    app, provider = assembled
    client_info = _register_client(provider)
    ticket = _ticket(client_info)

    with TestClient(app) as tc:
        # No _login(): mint only the page's own consent CSRF pair.
        csrf = "consent-csrf-value"
        tc.cookies.set(oauth._CONSENT_CSRF_COOKIE, csrf)
        response = tc.post(
            oauth.CONSENT_PATH,
            data={"ticket": ticket, "csrf_token": csrf, "decision": "allow", "workspace_id": "ws-connector-1"},
            follow_redirects=False,
        )
    assert response.status_code == 302
    location = response.headers["location"]
    assert "/login" in location
    assert "next=" in location and "ticket" in location, location


# ═════════════════════════════════════════════════════════════════════════
# Defect 4 — client secrets are not stored in cleartext
# ═════════════════════════════════════════════════════════════════════════


def _stored_client_row(pool, client_id: str) -> dict:
    cursor = pool._conn.execute(
        "SELECT client_secret_hash, client_info_json FROM mcp_oauth_clients WHERE client_id = ?",
        (client_id,),
    )
    row = cursor.fetchone()
    assert row is not None, f"no stored row for {client_id}"
    return dict(row)


def test_registered_client_secret_is_never_stored_in_cleartext(assembled, pool):
    app, provider = assembled
    secret = "plaintext-secret-that-must-not-be-persisted"
    _register_client(provider, secret=secret)

    row = _stored_client_row(pool, "client-connector-1")
    assert secret not in (row["client_info_json"] or ""), (
        "the plaintext client_secret is still inside client_info_json"
    )
    assert json.loads(row["client_info_json"]).get("client_secret") in (None, "")
    assert row["client_secret_hash"] == hashlib.sha256(secret.encode()).hexdigest()


def test_get_client_returns_the_hash_so_a_missing_authenticator_fails_closed(assembled, pool):
    """If ``install_hashed_client_secret_authenticator`` were ever not applied,
    the SDK's stock authenticator would compare this value against the raw
    presented secret and REFUSE. Returning None here would instead skip the
    secret check entirely."""
    app, provider = assembled
    secret = "another-plaintext-secret"
    _register_client(provider, client_id="client-hash-check", secret=secret)
    loaded = _run(provider.get_client("client-hash-check"))
    assert loaded is not None
    assert loaded.client_secret == hashlib.sha256(secret.encode()).hexdigest()
    assert loaded.client_secret != secret


def test_token_endpoint_accepts_the_real_secret_and_rejects_a_wrong_one(assembled, pool):
    """Drives the REAL /token route on the assembled app — the only thing that
    proves the hashed-secret authenticator is actually installed in the routes
    the SDK built."""
    from starlette.testclient import TestClient

    app, provider = assembled
    secret = "token-endpoint-secret"
    client_info = _register_client(provider, client_id="client-token", secret=secret)
    redirect_uri = str(client_info.redirect_uris[0])

    verifier = "v" * 64
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")

    def _mint_code() -> str:
        code = oauth._new_opaque_token()
        _run(
            oauth._store_authorization_code(
                code,
                client_id=client_info.client_id,
                code_challenge=challenge,
                redirect_uri=redirect_uri,
                redirect_uri_explicit=True,
                scopes=[oauth.SCOPE_READ],
                resource=RESOURCE,
                workspace_id="ws-connector-1",
                tenant_id="tenant-1",
                user_id="u-connector-1",
                expires_at=time.time() + 120,
            )
        )
        return code

    with TestClient(app) as tc:
        good = tc.post(
            "/token",
            data={
                "grant_type": "authorization_code", "code": _mint_code(),
                "client_id": client_info.client_id, "client_secret": secret,
                "redirect_uri": redirect_uri, "code_verifier": verifier, "resource": RESOURCE,
            },
        )
        bad = tc.post(
            "/token",
            data={
                "grant_type": "authorization_code", "code": _mint_code(),
                "client_id": client_info.client_id, "client_secret": "wrong-secret",
                "redirect_uri": redirect_uri, "code_verifier": verifier, "resource": RESOURCE,
            },
        )

    assert good.status_code == 200, f"the real secret was rejected: {good.status_code} {good.text[:300]}"
    assert good.json().get("access_token")
    assert bad.status_code != 200, "a WRONG client_secret was accepted"


def test_a_pre_hash_row_keeps_working_and_is_migrated_on_read(assembled, pool):
    """Rows written before this change carry the plaintext. They must keep
    authenticating, and must stop carrying it."""
    app, provider = assembled
    secret = "legacy-row-plaintext"
    info = _register_client(provider, client_id="client-legacy-row", secret=secret)

    # Rewrite the row into the OLD shape: full blob including the secret, and
    # no hash column — exactly what _store_client used to write.
    pool._conn.execute(
        "UPDATE mcp_oauth_clients SET client_secret_hash = NULL, client_info_json = ? WHERE client_id = ?",
        (info.model_dump_json(), "client-legacy-row"),
    )
    pool._conn.commit()
    assert secret in _stored_client_row(pool, "client-legacy-row")["client_info_json"]

    loaded = _run(provider.get_client("client-legacy-row"))
    assert loaded is not None
    assert loaded.client_secret == hashlib.sha256(secret.encode()).hexdigest()

    row = _stored_client_row(pool, "client-legacy-row")
    assert secret not in (row["client_info_json"] or ""), "the lazy migration did not scrub the cleartext"
    assert row["client_secret_hash"] == hashlib.sha256(secret.encode()).hexdigest()


# ═════════════════════════════════════════════════════════════════════════
# Defect 3 — empyralis:read must not permit writing
# ═════════════════════════════════════════════════════════════════════════
#
# Driven through the REAL registered tool (FastMCP's own tool manager, the
# same entry point a connected client hits), not by calling the gate function
# — a gate with no call site is this codebase's most-documented defect.


def _call_tool_with_scopes(tool_name: str, arguments: dict, *, scopes: list[str], client_id: str = "claude"):
    """Invoke a registered MCP tool as a caller holding exactly ``scopes``."""
    import mcp_server
    from mcp.server.auth.middleware.auth_context import auth_context_var
    from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser

    access_token = oauth.EmpyralisAccessToken(
        token="tok", client_id=client_id, scopes=list(scopes), workspace_id="ws-scope-test",
    )
    reset = auth_context_var.set(AuthenticatedUser(access_token))
    try:
        return _run(mcp_server.empyralist_mcp.call_tool(tool_name, arguments))
    finally:
        auth_context_var.reset(reset)


_READ_SCOPE_MUST_NOT_WRITE = [
    ("empyralis_create_task", {"project_id": "p1", "title": "t"}),
    ("empyralis_create_document", {"project_id": "p1", "title": "t", "body": "b"}),
    ("empyralis_edit_document", {"document_id": "d1", "old_string": "a", "new_string": "b"}),
    ("empyralis_update_document", {"document_id": "d1", "title": "t"}),
    ("empyralis_comment_on_task", {"task_id": "t1", "body": "hi"}),
    ("empyralis_update_task_status", {"task_id": "t1", "status": "done"}),
    ("empyralis_set_task_priority", {"task_id": "t1", "priority": 1}),
    ("empyralis_set_task_parent", {"task_id": "t1", "parent_task_id": "t2"}),
    ("empyralis_assign_task", {"task_id": "t1", "agent_id": "a1"}),
    ("empyralis_add_task_label", {"task_id": "t1", "label": "bug"}),
    ("empyralis_remove_task_label", {"task_id": "t1", "label": "bug"}),
    ("empyralis_chat", {"message": "hello"}),
]


@pytest.mark.parametrize("tool_name,arguments", _READ_SCOPE_MUST_NOT_WRITE, ids=[t for t, _ in _READ_SCOPE_MUST_NOT_WRITE])
def test_read_scope_cannot_mutate(tool_name, arguments):
    """A token granted only ``empyralis:read`` created documents, created
    tasks, commented, changed status and OVERWROTE a document body — all
    persisted — while the consent screen it came from said "View your
    projects, agents, and activity"."""
    with pytest.raises(Exception) as excinfo:
        _call_tool_with_scopes(tool_name, arguments, scopes=[oauth.SCOPE_READ])
    message = str(excinfo.value)
    assert "read-only" in message, f"{tool_name} failed for the wrong reason: {message}"
    assert oauth.SCOPE_WRITE in message, f"{tool_name} does not name the scope to grant: {message}"


@pytest.mark.parametrize("tool_name,arguments", _READ_SCOPE_MUST_NOT_WRITE, ids=[t for t, _ in _READ_SCOPE_MUST_NOT_WRITE])
def test_write_scope_is_not_blocked_by_the_gate(tool_name, arguments):
    """The control: with ``empyralis:write`` granted, the gate is not what
    stops the call. It will still fail further in (no such workspace/project
    in this test process) — but never with the read-only refusal, or the gate
    would be refusing everything and the test above would prove nothing."""
    try:
        _call_tool_with_scopes(tool_name, arguments, scopes=[oauth.SCOPE_READ, oauth.SCOPE_WRITE])
    except Exception as exc:  # noqa: BLE001
        assert "read-only" not in str(exc), f"{tool_name} was gated despite holding write scope: {exc}"


@pytest.mark.parametrize("tool_name,arguments", _READ_SCOPE_MUST_NOT_WRITE, ids=[t for t, _ in _READ_SCOPE_MUST_NOT_WRITE])
def test_legacy_bearer_key_path_is_deliberately_unchanged(tool_name, arguments):
    """The legacy per-workspace bearer key is minted by an operator for
    themselves and its ungated task/document writes are the founder's own
    core loop. Narrowing it as a side effect of an OAuth fix would change a
    LIVE path. ``load_access_token`` synthesizes a token under
    ``LEGACY_CLIENT_ID`` for such a key, with read-only scopes when the key
    is read-only — which must NOT be read as an OAuth consent."""
    try:
        _call_tool_with_scopes(
            tool_name, arguments, scopes=[oauth.SCOPE_READ], client_id=oauth.LEGACY_CLIENT_ID,
        )
    except Exception as exc:  # noqa: BLE001
        assert "read-only" not in str(exc), (
            f"{tool_name} newly refuses a legacy bearer key: {exc}"
        )


def test_consent_screen_read_line_does_not_promise_more_than_read(assembled, pool):
    """The screen is where a person decides. "View your projects, agents, and
    activity" over a grant that wrote was the dishonest half of this defect."""
    from starlette.testclient import TestClient

    app, provider = assembled
    client_info = _register_client(provider)
    with TestClient(app) as tc:
        _login(tc)
        page = tc.get(f"{oauth.CONSENT_PATH}?ticket={_ticket(client_info, scopes=[oauth.SCOPE_READ])}")
    assert page.status_code == 200
    read_line = oauth._SCOPE_DESCRIPTIONS[oauth.SCOPE_READ]
    assert read_line in page.text
    assert "read only" in read_line.lower(), f"the read scope line does not say it is read-only: {read_line!r}"
    for verb in ("create", "edit", "configure"):
        assert verb not in read_line.lower(), f"the read scope line promises {verb!r}: {read_line!r}"


def test_every_content_mutation_tool_calls_the_gate():
    """Structural. A behavioural test only covers the tools that exist today;
    the next mutation tool added is the one that forgets the gate, and it
    would type-check, run, and silently write under a read-only grant."""
    import ast
    import pathlib

    source = pathlib.Path("mcp_server.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    gated: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef) or not node.name.startswith("empyralis_"):
            continue
        for call in ast.walk(node):
            if isinstance(call, ast.Call) and getattr(call.func, "id", None) == "_check_content_write":
                gated.add(node.name)
    expected = {name for name, _args in _READ_SCOPE_MUST_NOT_WRITE}
    assert expected <= gated, f"ungated mutation tools: {sorted(expected - gated)}"


# ═════════════════════════════════════════════════════════════════════════
# Defect 5 — empyralis_create_task must not accept a foreign project_id
# ═════════════════════════════════════════════════════════════════════════


def test_create_task_refuses_a_project_outside_this_workspace(monkeypatch):
    """The row is stamped with the caller's own tenant/workspace, so this was
    never a cross-tenant WRITE — but the FK is on ``projects(id)``, which
    another tenant's real project id satisfies, so the task landed orphaned
    and invisible with ``ok: true``. Only a NONEXISTENT id failed, leaking raw
    Postgres constraint text."""
    import mcp_server
    from server_modules import projects_repository

    seen: dict = {}

    async def _get_project(*, tenant_id, workspace_id, project_id):
        seen["lookup"] = (tenant_id, workspace_id, project_id)
        return None  # a project that is not in THIS workspace

    async def _resolve_tenant(workspace_id, default="default"):
        return "tenant-scope-test"

    async def _create_task(**kwargs):
        raise AssertionError("create_task must not be reached for a foreign project_id")

    from server_modules import control_plane_repository, project_tasks_service

    monkeypatch.setattr(projects_repository, "get_project", _get_project)
    monkeypatch.setattr(control_plane_repository, "resolve_tenant_id_for_workspace", _resolve_tenant)
    monkeypatch.setattr(project_tasks_service, "create_task", _create_task)
    monkeypatch.setattr(mcp_server, "_ledger_mcp_call", _noop_ledger)

    result = _call_tool_with_scopes(
        "empyralis_create_task",
        {"project_id": "proj-from-another-workspace", "title": "orphan"},
        scopes=[oauth.SCOPE_READ, oauth.SCOPE_WRITE],
    )
    payload = _tool_payload(result)
    assert payload["ok"] is False
    assert "proj-from-another-workspace" in payload["error"]
    assert "empyralis_list_projects" in payload["error"]
    assert seen["lookup"] == ("tenant-scope-test", "ws-scope-test", "proj-from-another-workspace")


async def _noop_ledger(*args, **kwargs):
    return None


def _tool_payload(result):
    """FastMCP.call_tool returns (content_blocks, structured_result); a plain
    dict return is wrapped under "result"."""
    if isinstance(result, tuple):
        result = result[1]
    if isinstance(result, dict) and set(result) == {"result"}:
        result = result["result"]
    return result


# ═════════════════════════════════════════════════════════════════════════
# The whole flow, one pass, on the assembled app
# ═════════════════════════════════════════════════════════════════════════


def test_the_connector_flow_completes_end_to_end(assembled, pool):
    """/authorize → /mcp/consent → Allow → /token → a token that resolves a
    workspace. Every hop over real HTTP on the real assembled app, with a real
    session cookie and no custom CSRF header anywhere.

    The audit's verdict was NOT SAFE TO ENABLE because this could not run: the
    /authorize 302 landed on a 404, and clicking Allow was 403'd. This is the
    single test that fails if either comes back.
    """
    from starlette.testclient import TestClient
    from urllib.parse import parse_qs, urlparse

    app, provider = assembled
    secret = "end-to-end-secret"
    client_info = _register_client(provider, client_id="client-e2e", secret=secret)
    redirect_uri = str(client_info.redirect_uris[0])

    verifier = "e2e-verifier-" + "z" * 50
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")

    with TestClient(app) as tc:
        _login(tc)

        authorize = tc.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": client_info.client_id,
                "redirect_uri": redirect_uri,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "scope": oauth.SCOPE_READ,
                "state": "e2e-state",
                "resource": RESOURCE,
            },
            follow_redirects=False,
        )
        assert authorize.status_code in (302, 307), f"/authorize: {authorize.status_code} {authorize.text[:200]}"
        consent_url = authorize.headers["location"]
        assert oauth.CONSENT_PATH in consent_url

        page = tc.get(consent_url.replace(ISSUER, ""), follow_redirects=False)
        assert page.status_code == 200, f"the consent page 404'd: {page.status_code}"
        ticket = parse_qs(urlparse(consent_url).query)["ticket"][0]

        allow = tc.post(
            oauth.CONSENT_PATH,
            data={
                "ticket": ticket,
                "csrf_token": page.cookies[oauth._CONSENT_CSRF_COOKIE],
                "decision": "allow",
                "workspace_id": "ws-connector-1",
            },
            follow_redirects=False,
        )
        assert allow.status_code == 302, f"Allow: {allow.status_code} {allow.text[:300]}"
        callback = urlparse(allow.headers["location"])
        code = parse_qs(callback.query)["code"][0]
        assert parse_qs(callback.query)["state"] == ["e2e-state"]

        token_response = tc.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": client_info.client_id,
                "client_secret": secret,
                "redirect_uri": redirect_uri,
                "code_verifier": verifier,
                "resource": RESOURCE,
            },
        )

    assert token_response.status_code == 200, f"/token: {token_response.status_code} {token_response.text[:300]}"
    body = token_response.json()
    access_token = body["access_token"]

    loaded = _run(provider.load_access_token(access_token))
    assert loaded is not None
    assert loaded.workspace_id == "ws-connector-1"
    assert oauth.SCOPE_READ in loaded.scopes
    assert oauth.SCOPE_WRITE not in loaded.scopes, "a read-only consent minted a write token"

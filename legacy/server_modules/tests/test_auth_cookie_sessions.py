from __future__ import annotations

import httpx
import pytest
from fastapi import Depends, HTTPException
from fastapi import FastAPI
from starlette.responses import Response
from starlette.requests import Request

from server_modules import auth
from server_modules import routes_auth


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(routes_auth.router)
    return app


def _browser_payload(channel: str = "web") -> dict[str, object]:
    return {
        "ok": True,
        "token": "header.payload.signature",
        "auth_session": {
            "session_id": "session-1",
            "channel": channel,
        },
        "session_recovery": {
            "refresh_token": "refresh-token-1",
            "refresh_expires_at": 9999999999,
        },
        "user": {
            "id": "user-1",
            "email": "owner@example.com",
        },
    }


def _fake_set_auth_cookies(
    response: Response,
    payload: dict[str, object],
    *,
    request,
    channel: str = "web",
) -> None:
    response.set_cookie("empyralis_access_token", "access-cookie", httponly=True, path="/")
    response.set_cookie("empyralis_refresh_token", "refresh-cookie", httponly=True, path="/")
    response.set_cookie("empyralis_csrf_token", "csrf-cookie", httponly=False, path="/")


def _seed_browser_cookies(client: httpx.AsyncClient, *, refresh: bool = True, csrf: bool = True) -> None:
    client.cookies.set("empyralis_access_token", "access-cookie", path="/")
    if refresh:
        client.cookies.set("empyralis_refresh_token", "refresh-cookie", path="/")
    if csrf:
        client.cookies.set("empyralis_csrf_token", "csrf-cookie", path="/")


@pytest.mark.anyio
async def test_browser_login_sets_cookies_and_redacts_tokens(monkeypatch: pytest.MonkeyPatch):
    app = _build_app()
    monkeypatch.setattr(routes_auth, "login_user", lambda *args, **kwargs: _browser_payload())
    monkeypatch.setattr(routes_auth, "set_auth_cookies", _fake_set_auth_cookies)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/auth/login",
            json={"email": "owner@example.com", "password": "password-123", "channel": "web"},
        )

    assert response.status_code == 200
    assert "token" not in response.json()
    assert "session_recovery" not in response.json()
    set_cookie_headers = response.headers.get_list("set-cookie")
    assert any("empyralis_access_token=" in header for header in set_cookie_headers)
    assert any("empyralis_refresh_token=" in header for header in set_cookie_headers)
    assert any("empyralis_csrf_token=" in header for header in set_cookie_headers)


@pytest.mark.anyio
async def test_browser_refresh_requires_csrf_when_using_cookie_session():
    app = _build_app()
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        _seed_browser_cookies(client)
        response = await client.post(
            "/auth/refresh",
            json={"channel": "web"},
        )

    assert response.status_code == 403
    assert response.json()["detail"] == "CSRF validation failed."


@pytest.mark.anyio
async def test_cookie_authenticated_mutation_requires_csrf_before_user_resolution(
    monkeypatch: pytest.MonkeyPatch,
):
    app = _build_app()
    auth.CSRF_FAILURE_RATE_LIMIT_BUCKETS.clear()
    token_decoded = False

    def fake_decode_token_payload(token: str):
        nonlocal token_decoded
        token_decoded = True
        return {"sub": "user-1"}

    monkeypatch.setattr(auth, "_decode_token_payload", fake_decode_token_payload)

    @app.post("/protected-mutation")
    async def protected_mutation(current_user=Depends(auth.get_current_user)):
        return {"user_id": current_user["user_id"]}

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        _seed_browser_cookies(client, refresh=False, csrf=False)
        response = await client.post("/protected-mutation", json={"ok": True})

    assert response.status_code == 403
    assert response.json()["detail"] == "CSRF validation failed."
    assert token_decoded is False


@pytest.mark.anyio
async def test_cookie_authenticated_mutation_accepts_matching_csrf_header(
    monkeypatch: pytest.MonkeyPatch,
):
    app = _build_app()
    auth.CSRF_FAILURE_RATE_LIMIT_BUCKETS.clear()

    monkeypatch.setattr(
        auth,
        "_decode_token_payload",
        lambda token: {
            "sub": "user-1",
            "email": "owner@example.com",
            "role": "owner",
            "workspace_ids": ["ws-1"],
            "channel": "web",
        },
    )
    monkeypatch.setattr(
        auth,
        "_validated_bearer_context",
        lambda payload, *, touch_session=False: {
            "user_id": "user-1",
            "email": "owner@example.com",
            "workspace_ids": ["ws-1"],
            "identity_versions": {},
            "auth_session": {"session_id": "session-1", "channel": "web"},
        },
    )
    monkeypatch.setattr(auth, "_resolved_bearer_role", lambda user_id, email, role: "owner")
    monkeypatch.setattr(auth, "_has_auth_admin_identity", lambda user_id, email: False)
    monkeypatch.setattr(
        auth,
        "_effective_workspace_access",
        lambda **kwargs: {"ws-1": {"tenant_id": "tenant-1", "role": "owner"}},
    )

    @app.post("/protected-mutation")
    async def protected_mutation(current_user=Depends(auth.get_current_user)):
        return {"user_id": current_user["user_id"]}

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        _seed_browser_cookies(client, refresh=False, csrf=True)
        response = await client.post(
            "/protected-mutation",
            json={"ok": True},
            headers={"x-csrf-token": "csrf-cookie"},
        )

    assert response.status_code == 200
    assert response.json()["user_id"] == "user-1"


@pytest.mark.anyio
async def test_browser_refresh_csrf_failures_are_rate_limited(monkeypatch: pytest.MonkeyPatch):
    app = _build_app()
    auth.CSRF_FAILURE_RATE_LIMIT_BUCKETS.clear()
    monkeypatch.setattr(auth, "ORION_AUTH_CSRF_FAILURE_RATE_LIMIT_PER_MINUTE", 2)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        for _ in range(2):
            _seed_browser_cookies(client)
            failure = await client.post(
                "/auth/refresh",
                json={"channel": "web"},
            )
            assert failure.status_code == 403

        _seed_browser_cookies(client)
        limited = await client.post(
            "/auth/refresh",
            json={"channel": "web"},
        )

    assert limited.status_code == 429
    payload = limited.json()
    detail = payload.get("detail") if isinstance(payload, dict) else {}
    assert isinstance(detail, dict)
    assert detail.get("code") == "auth_csrf_rate_limited"


@pytest.mark.anyio
async def test_browser_refresh_uses_cookie_refresh_token_and_sets_new_cookies(
    monkeypatch: pytest.MonkeyPatch,
):
    app = _build_app()
    observed: dict[str, object] = {}

    def fake_refresh_authenticated_session(refresh_token: str, **kwargs):
        observed["refresh_token"] = refresh_token
        return _browser_payload()

    monkeypatch.setattr(routes_auth, "refresh_authenticated_session", fake_refresh_authenticated_session)
    monkeypatch.setattr(routes_auth, "set_auth_cookies", _fake_set_auth_cookies)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        _seed_browser_cookies(client)
        response = await client.post(
            "/auth/refresh",
            json={"channel": "web"},
            headers={"x-csrf-token": "csrf-cookie"},
        )

    assert response.status_code == 200
    assert observed["refresh_token"] == "refresh-cookie"
    assert "token" not in response.json()
    assert any(
        "empyralis_access_token=" in header
        for header in response.headers.get_list("set-cookie")
    )


@pytest.mark.anyio
async def test_logout_clears_browser_cookies(monkeypatch: pytest.MonkeyPatch):
    app = _build_app()
    app.dependency_overrides[routes_auth.get_current_user] = lambda: {
        "auth_type": "bearer",
        "user_id": "user-1",
        "session_id": "session-1",
    }
    monkeypatch.setattr(
        routes_auth,
        "logout_authenticated_session",
        lambda current_user: {"ok": True, "session_id": "session-1"},
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        _seed_browser_cookies(client)
        response = await client.post(
            "/auth/logout",
            headers={"x-csrf-token": "csrf-cookie"},
        )

    assert response.status_code == 200
    set_cookie_headers = response.headers.get_list("set-cookie")
    assert any("empyralis_access_token=" in header and "Max-Age=0" in header for header in set_cookie_headers)
    assert any("empyralis_refresh_token=" in header and "Max-Age=0" in header for header in set_cookie_headers)
    assert any("empyralis_csrf_token=" in header and "Max-Age=0" in header for header in set_cookie_headers)


@pytest.mark.anyio
async def test_auth_me_accepts_access_token_cookie(monkeypatch: pytest.MonkeyPatch):
    app = _build_app()
    monkeypatch.setattr(auth, "_orion_auth_required", lambda: True)
    monkeypatch.setattr(
        auth,
        "_decode_token_payload",
        lambda token: {
            "sub": "user-1",
            "email": "owner@example.com",
            "role": "member",
            "workspace_ids": ["ws-1"],
            "sid": "session-1",
            "channel": "web",
        },
    )
    monkeypatch.setattr(
        auth,
        "_validated_bearer_context",
        lambda payload, touch_session=False: {
            "user_id": "user-1",
            "email": "owner@example.com",
            "workspace_ids": ["ws-1"],
            "identity_versions": {"membership_version": 1},
            "auth_session": {"session_id": "session-1", "channel": "web"},
            "device_link": None,
        },
    )
    monkeypatch.setattr(auth, "_resolved_bearer_role", lambda user_id, email, claimed_role: "member")
    monkeypatch.setattr(
        auth,
        "_effective_workspace_access",
        lambda **kwargs: {
            "ws-1": {
                "workspace_id": "ws-1",
                "tenant_id": "tenant-1",
                "role": "member",
            }
        },
    )
    monkeypatch.setattr(
        auth,
        "tenant_access_map",
        lambda *_args, **_kwargs: {
            "tenant-1": {
                "tenant_id": "tenant-1",
                "role": "member",
            }
        },
    )
    monkeypatch.setattr(
        routes_auth,
        "get_authenticated_user_profile",
        lambda current_user: {"ok": True, "user": {"id": current_user["user_id"]}},
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        client.cookies.set("empyralis_access_token", "cookie-bearer-token", path="/")
        response = await client.get(
            "/auth/me",
        )

    assert response.status_code == 200
    assert response.json()["user"]["id"] == "user-1"


def test_set_auth_cookies_enforces_production_secure_defaults_and_bounded_expiry(
    monkeypatch: pytest.MonkeyPatch,
):
    now_ts = 1_700_000_000
    monkeypatch.setenv("ORION_ENV", "production")
    monkeypatch.delenv("EMPYRALIS_AUTH_COOKIE_SAMESITE", raising=False)
    monkeypatch.setattr(auth.time, "time", lambda: float(now_ts))
    monkeypatch.setattr(
        auth,
        "_decode_token_payload",
        lambda token: {"exp": now_ts + (auth.AUTH_ACCESS_COOKIE_MAX_AGE_SECONDS * 10)},
    )

    request = Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/auth/login",
            "raw_path": b"/auth/login",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
        }
    )
    response = Response()
    payload = {
        "token": "header.payload.signature",
        "session_recovery": {
            "refresh_token": "refresh-token-1",
            "refresh_expires_at": now_ts + (auth.AUTH_REFRESH_COOKIE_MAX_AGE_SECONDS * 10),
        },
    }
    auth.set_auth_cookies(response, payload, request=request, channel="web")
    cookie_headers = response.headers.getlist("set-cookie")
    access_header = next(item for item in cookie_headers if item.startswith("empyralis_access_token="))
    refresh_header = next(item for item in cookie_headers if item.startswith("empyralis_refresh_token="))
    csrf_header = next(item for item in cookie_headers if item.startswith("empyralis_csrf_token="))

    assert "Secure" in access_header
    assert "HttpOnly" in access_header
    assert "SameSite=strict" in access_header
    assert f"Max-Age={auth.AUTH_ACCESS_COOKIE_MAX_AGE_SECONDS}" in access_header

    assert "Secure" in refresh_header
    assert "HttpOnly" in refresh_header
    assert "SameSite=strict" in refresh_header
    assert f"Max-Age={auth.AUTH_REFRESH_COOKIE_MAX_AGE_SECONDS}" in refresh_header

    assert "Secure" in csrf_header
    assert "SameSite=strict" in csrf_header


@pytest.mark.anyio
async def test_browser_refresh_clears_cookies_when_refresh_token_is_stale(
    monkeypatch: pytest.MonkeyPatch,
):
    app = _build_app()

    def _stale_refresh(*args, **kwargs):
        raise HTTPException(status_code=401, detail="Refresh token is no longer active.")

    monkeypatch.setattr(routes_auth, "refresh_authenticated_session", _stale_refresh)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        _seed_browser_cookies(client)
        response = await client.post(
            "/auth/refresh",
            json={"channel": "web"},
            headers={"x-csrf-token": "csrf-cookie"},
        )

    assert response.status_code == 401
    set_cookie_headers = response.headers.get_list("set-cookie")
    assert any("empyralis_access_token=" in header and "Max-Age=0" in header for header in set_cookie_headers)
    assert any("empyralis_refresh_token=" in header and "Max-Age=0" in header for header in set_cookie_headers)
    assert any("empyralis_csrf_token=" in header and "Max-Age=0" in header for header in set_cookie_headers)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("method", "path", "json_body"),
    [
        ("post", "/auth/channel-pairing/intents", {"provider": "telegram"}),
        ("post", "/auth/channel-pairing/links/link-1/revoke", {"confirm": True}),
        ("patch", "/auth/me", {"name": "Updated Name"}),
        (
            "post",
            "/auth/admin/provision/users",
            {
                "email": "member@example.com",
                "tenant_id": "tenant-1",
                "workspace_roles": {"ws-1": "viewer"},
            },
        ),
    ],
)
async def test_state_changing_auth_routes_require_csrf_for_browser_session(
    method: str,
    path: str,
    json_body: dict[str, object],
):
    app = _build_app()
    app.dependency_overrides[routes_auth.get_current_user] = lambda: {
        "auth_type": "bearer",
        "user_id": "user-1",
        "role": "owner",
        "is_admin": True,
    }
    app.dependency_overrides[routes_auth.require_admin_access] = lambda: {
        "auth_type": "bearer",
        "user_id": "user-1",
        "role": "owner",
        "is_admin": True,
    }

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        _seed_browser_cookies(client, refresh=False)
        response = await getattr(client, method)(
            path,
            json=json_body,
        )

    assert response.status_code == 403
    assert response.json()["detail"] == "CSRF validation failed."
